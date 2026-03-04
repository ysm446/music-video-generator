"""生成・編集タブ用ルーター。

個別生成キュー、一括バッチ生成、履歴管理を提供する。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.batch_generator import BatchGenerator
from src.comfyui_client import ComfyUIClient
from src.project import Project

from ._shared import BASE_DIR, _APP_DIR

router = APIRouter()


# ===========================================================
# 共通ユーティリティ
# ===========================================================

def _load_project(name: str) -> Project:
    proj_dir = BASE_DIR / name
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail=f"プロジェクトが見つかりません: {name}")
    return Project.load(proj_dir)


def _get_comfyui(proj: Project) -> ComfyUIClient:
    return ComfyUIClient(base_url=proj.comfyui_url)


def _format_elapsed(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _scene_media_urls(proj: Project, scene_id: int) -> dict:
    """シーンのメディアURLを返す（ファイル存在チェック込み）。"""
    from src.scene import Scene as SceneModel
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        return {}
    scene_dir = proj.scene_dir(scene_id)
    img = scene.image_path(scene_dir)
    vid_preview = scene.video_preview_path(scene_dir)
    vid_final = scene.video_final_path(scene_dir)

    def to_url(path) -> Optional[str]:
        if path.exists() and path.stat().st_size > 0:
            rel = path.relative_to(BASE_DIR)
            mtime = int(path.stat().st_mtime)
            return f"/api/files/{rel.as_posix()}?v={mtime}"
        return None

    # バージョン一覧
    img_versions = []
    versions_dir = scene.image_versions_dir(scene_dir)
    if versions_dir.exists():
        img_versions = sorted(
            [f.name for f in versions_dir.glob("*.png")],
            reverse=True,
        )

    vid_versions: dict[str, list[str]] = {"preview": [], "final": []}
    vid_versions_dir = scene.video_versions_dir(scene_dir)
    if vid_versions_dir.exists():
        for f in vid_versions_dir.glob("*.mp4"):
            if "_final_" in f.name:
                vid_versions["final"].append(f.name)
            else:
                vid_versions["preview"].append(f.name)
        vid_versions["preview"].sort(reverse=True)
        vid_versions["final"].sort(reverse=True)

    return {
        "image_url": to_url(img),
        "video_preview_url": to_url(vid_preview),
        "video_final_url": to_url(vid_final),
        "active_image_version": scene.active_image_version,
        "active_video_preview_version": scene.active_video_preview_version,
        "active_video_final_version": scene.active_video_final_version,
        "image_versions": img_versions,
        "video_versions_preview": vid_versions["preview"],
        "video_versions_final": vid_versions["final"],
        "status": scene.status,
    }


# ===========================================================
# 個別シーン生成キュー（単体ボタン用）
# ===========================================================

_regen_queue: list[dict] = []
_regen_running: Optional[dict] = None
_regen_worker_running = False
_regen_next_id = 1
_regen_logs: list[str] = []
_regen_dirty = False
_regen_lock = threading.Lock()


def _append_regen_log(msg: str) -> None:
    _regen_logs.append(msg)
    if len(_regen_logs) > 40:
        del _regen_logs[:-40]


def _format_regen_debug() -> dict:
    running = _regen_running
    pending = len(_regen_queue)
    return {
        "running": running["label"] if running else None,
        "pending": pending,
        "logs": list(_regen_logs[-12:]),
    }


def _regen_worker() -> None:
    global _regen_running, _regen_worker_running, _regen_dirty
    while True:
        with _regen_lock:
            if not _regen_queue:
                _regen_running = None
                _regen_worker_running = False
                return
            task = _regen_queue.pop(0)
            _regen_running = task
            _append_regen_log(f"開始: {task['label']}")

        try:
            proj = _load_project(task["project_name"])
            comfyui = _get_comfyui(proj)
            gen = BatchGenerator(proj, comfyui)
            gen.regenerate_scene(
                task["scene_id"],
                target=task["target"],
                video_quality=task["video_quality"],
            )
            _append_regen_log(f"完了: {task['label']}")
        except Exception as e:
            _append_regen_log(f"エラー: {task['label']} -> {e}")

        with _regen_lock:
            _regen_running = None
            _regen_dirty = True


def _enqueue_regen(project_name: str, scene_id: int, target: str, video_quality: str) -> dict:
    global _regen_next_id, _regen_worker_running
    mode_label = "画像" if target == "image" else ("最終版動画" if video_quality == "final" else "プレビュー動画")

    with _regen_lock:
        task_id = _regen_next_id
        _regen_next_id += 1
        label = f"#{task_id} シーン{scene_id} {mode_label}"
        task = {
            "id": task_id,
            "label": label,
            "project_name": project_name,
            "scene_id": scene_id,
            "target": target,
            "video_quality": video_quality,
        }
        # 画像タスクを動画タスクより先に処理
        if target == "image":
            insert_at = len(_regen_queue)
            for i, q in enumerate(_regen_queue):
                if q.get("target") != "image":
                    insert_at = i
                    break
            _regen_queue.insert(insert_at, task)
        else:
            _regen_queue.append(task)
        _append_regen_log(f"追加: {label}")
        if not _regen_worker_running:
            _regen_worker_running = True
            threading.Thread(target=_regen_worker, daemon=True).start()

    return {"message": f"生成キューに追加: {label}", "queue": _format_regen_debug()}


# ===========================================================
# 個別生成エンドポイント
# ===========================================================

class GenerateRequest(BaseModel):
    target: str = "image"          # "image" / "video" / "both"
    video_quality: str = "preview"  # "preview" / "final"


@router.post("/projects/{name}/scenes/{scene_id}/generate")
def generate_scene(name: str, scene_id: int, body: GenerateRequest):
    """シングルシーンの生成をキューに追加する。"""
    # プロジェクト存在確認
    _load_project(name)
    return _enqueue_regen(name, scene_id, body.target, body.video_quality)


@router.get("/projects/{name}/scenes/{scene_id}/media")
def get_scene_media(name: str, scene_id: int):
    """シーンのメディアURL・バージョン情報を返す。"""
    proj = _load_project(name)
    return _scene_media_urls(proj, scene_id)


@router.get("/projects/{name}/scenes/{scene_id}/image-seed")
def get_image_seed(name: str, scene_id: int):
    """アクティブ画像のPNGメタデータからシード値を読み取る。"""
    import json
    from PIL import Image

    proj = _load_project(name)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="シーンが見つかりません")
    scene_dir = proj.scene_dir(scene_id)
    img_path = scene.image_path(scene_dir)
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="画像が見つかりません")

    try:
        img = Image.open(img_path)
        text_data = img.info
        if "prompt" in text_data:
            prompt_data = json.loads(text_data["prompt"])
            for node in prompt_data.values():
                inputs = node.get("inputs", {})
                for key in ("seed", "noise_seed"):
                    if key in inputs and isinstance(inputs[key], int):
                        return {"seed": inputs[key]}
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="PNG内にシード値が見つかりません")


@router.get("/projects/{name}/scenes/{scene_id}/video-seed")
def get_video_seed(name: str, scene_id: int, quality: str = "preview"):
    """アクティブ動画のメタデータからシード値を読み取る（ffprobe使用）。"""
    import subprocess

    proj = _load_project(name)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="シーンが見つかりません")
    scene_dir = proj.scene_dir(scene_id)
    video_path = scene.video_final_path(scene_dir) if quality == "final" else scene.video_preview_path(scene_dir)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="動画が見つかりません")

    try:
        import json as _json
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        data = _json.loads(result.stdout)
        tags = data.get("format", {}).get("tags", {})
        if "prompt" in tags:
            prompt_data = _json.loads(tags["prompt"])
            for node in prompt_data.values():
                inputs = node.get("inputs", {})
                for key in ("seed", "noise_seed"):
                    if key in inputs and isinstance(inputs[key], int):
                        return {"seed": inputs[key]}
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="動画内にシード値が見つかりません")


@router.get("/queue/status")
def get_queue_status():
    """個別生成キューの状態を返す。"""
    global _regen_dirty
    with _regen_lock:
        status = _format_regen_debug()
        dirty = _regen_dirty
        _regen_dirty = False
    return {**status, "dirty": dirty}


# ===========================================================
# 履歴バージョン操作
# ===========================================================

class UseVersionRequest(BaseModel):
    version_name: str
    media_type: str  # "image" / "video_preview" / "video_final"


@router.post("/projects/{name}/scenes/{scene_id}/use-version")
def use_version(name: str, scene_id: int, body: UseVersionRequest):
    """指定バージョンをアクティブに切り替える。"""
    import shutil
    proj = _load_project(name)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="シーンが見つかりません")

    scene_dir = proj.scene_dir(scene_id)

    if body.media_type == "image":
        src = scene.image_version_path(scene_dir, body.version_name)
        if not src.exists():
            raise HTTPException(status_code=404, detail="バージョンファイルが見つかりません")
        dst = scene.image_path(scene_dir)
        shutil.copy2(src, dst)
        scene.active_image_version = body.version_name
    elif body.media_type == "video_preview":
        src = scene.video_version_path(scene_dir, body.version_name)
        if not src.exists():
            raise HTTPException(status_code=404, detail="バージョンファイルが見つかりません")
        dst = scene.video_preview_path(scene_dir)
        shutil.copy2(src, dst)
        scene.active_video_preview_version = body.version_name
    elif body.media_type == "video_final":
        src = scene.video_version_path(scene_dir, body.version_name)
        if not src.exists():
            raise HTTPException(status_code=404, detail="バージョンファイルが見つかりません")
        dst = scene.video_final_path(scene_dir)
        shutil.copy2(src, dst)
        scene.active_video_final_version = body.version_name
    else:
        raise HTTPException(status_code=400, detail="不正な media_type")

    proj.save_scene(scene)
    return _scene_media_urls(proj, scene_id)


class DeleteVersionRequest(BaseModel):
    version_name: str
    media_type: str  # "image" / "video_preview" / "video_final"


@router.delete("/projects/{name}/scenes/{scene_id}/version")
def delete_version(name: str, scene_id: int, body: DeleteVersionRequest):
    """バージョンファイルを削除する（アクティブバージョンは削除不可）。"""
    proj = _load_project(name)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail="シーンが見つかりません")

    scene_dir = proj.scene_dir(scene_id)

    if body.media_type == "image":
        if scene.active_image_version == body.version_name:
            raise HTTPException(status_code=400, detail="アクティブバージョンは削除できません")
        target = scene.image_version_path(scene_dir, body.version_name)
    elif body.media_type in ("video_preview", "video_final"):
        active = (scene.active_video_preview_version if body.media_type == "video_preview"
                  else scene.active_video_final_version)
        if active == body.version_name:
            raise HTTPException(status_code=400, detail="アクティブバージョンは削除できません")
        target = scene.video_version_path(scene_dir, body.version_name)
    else:
        raise HTTPException(status_code=400, detail="不正な media_type")

    if not target.exists():
        raise HTTPException(status_code=404, detail="バージョンファイルが見つかりません")
    target.unlink()
    return _scene_media_urls(proj, scene_id)


# ===========================================================
# 一括バッチ生成
# ===========================================================

_batch_gen: Optional[BatchGenerator] = None
_batch_log: list[str] = []
_batch_lock = threading.Lock()
_batch_started_at: Optional[float] = None
_batch_finished_at: Optional[float] = None
_batch_current_task: str = ""
_batch_mode_label: str = ""
_batch_run_id: int = 0
_batch_stop_requested: bool = False


class BatchStartRequest(BaseModel):
    project_name: str
    target: str = "both"          # "image_prompt" / "video_prompt" / "image" / "video" / "both"
    video_quality: str = "preview" # "preview" / "final"


@router.post("/batch/start")
def batch_start(body: BatchStartRequest):
    """一括バッチ生成を開始する。"""
    global _batch_gen, _batch_log, _batch_started_at, _batch_finished_at
    global _batch_current_task, _batch_mode_label, _batch_run_id, _batch_stop_requested

    proj = _load_project(body.project_name)
    target = body.target
    video_quality = body.video_quality
    is_prompt_mode = target in ("image_prompt", "video_prompt")

    if not is_prompt_mode:
        comfyui = _get_comfyui(proj)
        if not comfyui.is_available():
            raise HTTPException(status_code=503, detail=f"ComfyUIに接続できません: {proj.comfyui_url}")

    with _batch_lock:
        if _batch_started_at is not None and _batch_finished_at is None:
            raise HTTPException(status_code=409, detail="一括生成がすでに実行中です")

        _batch_run_id += 1
        run_id = _batch_run_id
        _batch_log = []
        _batch_started_at = time.monotonic()
        _batch_finished_at = None
        _batch_stop_requested = False
        _batch_current_task = "開始準備中..."

        if is_prompt_mode:
            _batch_mode_label = "画像プロンプト生成" if target == "image_prompt" else "動画プロンプト生成"
        elif target == "image":
            _batch_mode_label = "画像生成"
        elif video_quality == "final":
            _batch_mode_label = "最終版動画生成"
        else:
            _batch_mode_label = "プレビュー動画生成"

        _batch_gen = None if is_prompt_mode else BatchGenerator(proj, _get_comfyui(proj))

    _batch_log.append(f"{_batch_mode_label} を開始")

    def on_progress(sid, total, msg):
        global _batch_current_task
        with _batch_lock:
            _batch_current_task = msg
            _batch_log.append(msg)
            if len(_batch_log) > 20:
                _batch_log.pop(0)

    def on_error(sid, msg):
        global _batch_current_task
        with _batch_lock:
            _batch_current_task = f"[ERROR] {msg}"
            _batch_log.append(f"[ERROR] {msg}")

    if is_prompt_mode:
        from src import llm_client as _llm_mod, settings_manager

        def _run_prompt_batch():
            total = len(proj.scenes)
            s = settings_manager.load(proj.project_dir)
            common_prompt = s.get("batch_image_prompt_common", "")
            common_video_instruction = s.get("batch_video_prompt_common_instruction", "")

            # llm_client の関数を直接呼び出せるよう import
            from api_routes.llm import _generate_image_prompt_from_plot, _generate_video_prompt_for_scene

            for scene in proj.scenes:
                if _batch_stop_requested:
                    break
                if not scene.enabled:
                    on_progress(scene.scene_id, total, f"シーン {scene.scene_id} は無効化されているためスキップ")
                    continue
                if target == "image_prompt":
                    if (scene.image_prompt or "").strip():
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id} は画像プロンプト入力済みのためスキップ")
                        continue
                    if not (scene.plot or "").strip():
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はシーン説明が空のためスキップ")
                        continue
                    try:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 画像プロンプト生成中...")
                        scene.image_prompt = _generate_image_prompt_from_plot(scene.plot, common_prompt, proj)
                        if scene.status == "empty":
                            scene.status = "plot_done"
                        proj.save_scene(scene)
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 完了")
                    except Exception as e:
                        on_error(scene.scene_id, f"画像プロンプト生成失敗: {e}")
                else:
                    if (scene.video_prompt or "").strip():
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id} は動画プロンプト入力済みのためスキップ")
                        continue
                    if not (scene.plot or "").strip() and not (scene.image_prompt or "").strip():
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id} は入力情報不足のためスキップ")
                        continue
                    try:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 動画プロンプト生成中...")
                        new_prompt, new_neg = _generate_video_prompt_for_scene(scene, proj, common_instruction=common_video_instruction)
                        scene.video_prompt = new_prompt
                        if not (scene.video_negative or "").strip() and (new_neg or "").strip():
                            scene.video_negative = new_neg
                        proj.save_scene(scene)
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 完了")
                    except Exception as e:
                        on_error(scene.scene_id, f"動画プロンプト生成失敗: {e}")

        worker = threading.Thread(target=_run_prompt_batch, daemon=True)
        worker.start()
    else:
        worker = _batch_gen.run_async(
            on_progress=on_progress,
            on_error=on_error,
            target=target,
            video_quality=video_quality,
        )

    def _watch_batch(this_run_id: int):
        global _batch_finished_at, _batch_current_task
        worker.join()
        with _batch_lock:
            if this_run_id != _batch_run_id:
                return
            now = time.monotonic()
            _batch_finished_at = now
            elapsed = _format_elapsed(now - (_batch_started_at or now))
            if _batch_gen and _batch_gen.is_running:
                return
            if _batch_stop_requested:
                _batch_current_task = "停止"
                _batch_log.append(f"停止: 経過 {elapsed}")
            else:
                _batch_current_task = "完了"
                _batch_log.append(f"完了: 合計 {elapsed}")
            if len(_batch_log) > 20:
                _batch_log[:] = _batch_log[-20:]

    threading.Thread(target=_watch_batch, args=(run_id,), daemon=True).start()
    return {"message": f"{_batch_mode_label}を開始しました"}


@router.post("/batch/stop")
def batch_stop():
    """一括バッチ生成を停止する。"""
    global _batch_current_task, _batch_stop_requested
    with _batch_lock:
        is_running = _batch_started_at is not None and _batch_finished_at is None

    # 個別キューもクリア
    with _regen_lock:
        cleared = len(_regen_queue)
        _regen_queue.clear()
        if cleared > 0:
            _append_regen_log(f"停止: 待機中 {cleared} 件をクリア")

    if not is_running:
        return {"message": "実行中の一括生成はありません（メディア生成キューはクリアしました）"}

    if _batch_gen:
        _batch_gen.stop()
    with _batch_lock:
        _batch_stop_requested = True
        _batch_current_task = "停止要求を送信しました"
    return {"message": "停止要求を送信しました"}


@router.get("/batch/status")
def batch_status():
    """一括バッチ生成の進捗を返す。"""
    with _batch_lock:
        if _batch_started_at is None:
            return {"state": "idle", "mode": "", "current_task": "", "elapsed": "", "logs": []}
        now = time.monotonic()
        elapsed = _format_elapsed(now - _batch_started_at)
        if _batch_finished_at is not None:
            total_time = _format_elapsed(_batch_finished_at - _batch_started_at)
            state = "done"
        else:
            total_time = "-"
            state = "running"
        logs = list(_batch_log[-8:])

    return {
        "state": state,
        "mode": _batch_mode_label,
        "current_task": _batch_current_task,
        "elapsed": elapsed,
        "total_time": total_time,
        "logs": logs,
    }
