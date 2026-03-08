"""プロジェクト管理 API ルーター。

エンドポイント:
  GET  /api/projects                     - プロジェクト一覧
  POST /api/projects                     - プロジェクト新規作成（multipart）
  GET  /api/projects/{name}              - プロジェクト読込
  PUT  /api/projects/{name}/settings     - 設定保存
  GET  /api/projects/{name}/workflows    - ワークフロー一覧
  GET  /api/config                       - アプリデフォルト設定
  GET  /api/projects/{name}/last         - 最後に開いたプロジェクトを記録・取得
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from src.project import Project, list_projects
from src import settings_manager

from ._shared import BASE_DIR, _APP_DIR, _cfg, list_image_workflows, list_video_workflows

router = APIRouter()


# ---- ユーティリティ ----

def _load_proj(name: str) -> Project:
    """プロジェクトをディスクから読み込む。存在しなければ 404。"""
    proj_dir = BASE_DIR / name
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
    try:
        return Project.load(proj_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_audio_duration(path: str | Path) -> float:
    """mutagen で音楽ファイルの長さ（秒）を取得する。"""
    from mutagen import File as MutagenFile
    audio = MutagenFile(str(path))
    if audio is None or audio.info is None:
        raise ValueError("音楽ファイルの長さを取得できませんでした")
    return float(audio.info.length)


# ---- エンドポイント ----

@router.get("/projects")
def get_projects() -> dict:
    """プロジェクト名の一覧を返す。"""
    return {"projects": list_projects(BASE_DIR)}


@router.post("/projects")
async def create_project(
    name: str = Form(...),
    scene_duration: int = Form(5),
    comfyui_url: str = Form("http://localhost:8188"),
    image_resolution_w: int = Form(1280),
    image_resolution_h: int = Form(720),
    video_resolution_w: int = Form(640),
    video_resolution_h: int = Form(480),
    video_final_resolution_w: int = Form(1280),
    video_final_resolution_h: int = Form(720),
    video_fps: int = Form(16),
    video_frame_count: int = Form(81),
    image_workflow: str = Form("workflows/image/image_z_image_turbo.json"),
    video_workflow: str = Form("workflows/video/video_wan2_2_14B_i2v.json"),
    model: str = Form("qwen3-vl-4b (推奨)"),
    music: UploadFile = File(...),
) -> dict:
    """新しいプロジェクトを作成する。"""
    # プロジェクト名の検証
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="プロジェクト名が空です")
    proj_dir = BASE_DIR / name
    if proj_dir.exists():
        raise HTTPException(status_code=400, detail=f"プロジェクト '{name}' は既に存在します")

    # 音楽ファイルを一時ファイルに書き出して長さを取得
    suffix = Path(music.filename or "music.mp3").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        content = await music.read()
        tmp.write(content)

    try:
        duration = _get_audio_duration(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"音楽ファイルエラー: {e}")

    # プロジェクト作成
    try:
        proj = Project(
            project_name=name,
            base_dir=BASE_DIR,
            duration=duration,
            scene_duration=scene_duration,
            comfyui_url=comfyui_url,
            image_resolution={"width": image_resolution_w, "height": image_resolution_h},
            video_resolution={"width": video_resolution_w, "height": video_resolution_h},
            video_final_resolution={"width": video_final_resolution_w, "height": video_final_resolution_h},
            video_fps=video_fps,
            video_frame_count=video_frame_count,
            image_workflow=image_workflow,
            video_workflow=video_workflow,
        )
        proj.initialize_dirs()
        proj.setup_scenes()

        # 音楽ファイルをプロジェクトにコピー
        music_dest = proj.music_dir / (music.filename or f"music{suffix}")
        import shutil
        shutil.copy2(tmp_path, music_dest)
        proj.music_file = str(music_dest.relative_to(proj.project_dir))

        proj.save()

        # 設定を保存
        settings_manager.save(proj.project_dir, {
            "comfyui_url": comfyui_url,
            "llm_url": _cfg.get("llm", {}).get("url", "http://localhost:11434/v1"),
            "image_resolution_w": image_resolution_w,
            "image_resolution_h": image_resolution_h,
            "video_resolution_w": video_resolution_w,
            "video_resolution_h": video_resolution_h,
            "video_final_resolution_w": video_final_resolution_w,
            "video_final_resolution_h": video_final_resolution_h,
            "video_fps": video_fps,
            "video_frame_count": video_frame_count,
            "scene_duration": scene_duration,
            "image_workflow": image_workflow,
            "video_workflow": video_workflow,
            "model": model,
        })
        settings_manager.save_last_project(name)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "project_name": name,
        "scene_count": len(proj.scenes),
        "duration": duration,
    }


@router.get("/projects/last")
def get_last_project() -> dict:
    """最後に開いたプロジェクト名を返す。"""
    return {"last_project": settings_manager.get_last_project()}


@router.get("/projects/{name}")
def load_project(name: str) -> dict:
    """プロジェクトデータを返す（project.json + settings.json + 全シーン）。"""
    proj = _load_proj(name)
    s = settings_manager.load(proj.project_dir)
    settings_manager.save_last_project(name)

    # 音楽ファイルの URL を生成
    music_url: Optional[str] = None
    music_abs = proj.absolute_music_path()
    if music_abs and music_abs.exists():
        rel = music_abs.relative_to(BASE_DIR)
        music_url = f"/api/files/{rel.as_posix()}"

    proj_dict = {
        "project_name": proj.project_name,
        "duration": proj.duration,
        "scene_duration": proj.scene_duration,
        "scene_count": len(proj.scenes),
        "concept": proj.concept,
        "image_resolution": proj.image_resolution,
        "video_resolution": proj.video_resolution,
        "video_final_resolution": proj.video_final_resolution,
        "video_fps": proj.video_fps,
        "video_frame_count": proj.video_frame_count,
        "comfyui_url": proj.comfyui_url,
        "image_workflow": proj.image_workflow,
        "video_workflow": proj.video_workflow,
        "music_url": music_url,
        "created_at": proj.created_at,
        "updated_at": proj.updated_at,
    }

    return {
        "project": proj_dict,
        "settings": s,
        "scenes": [sc.to_dict() for sc in proj.scenes],
    }


class ProjectSettingsBody(BaseModel):
    comfyui_url: Optional[str] = None
    image_resolution_w: Optional[int] = None
    image_resolution_h: Optional[int] = None
    video_resolution_w: Optional[int] = None
    video_resolution_h: Optional[int] = None
    video_final_resolution_w: Optional[int] = None
    video_final_resolution_h: Optional[int] = None
    video_fps: Optional[int] = None
    video_frame_count: Optional[int] = None
    scene_duration: Optional[int] = None
    image_workflow: Optional[str] = None
    video_workflow: Optional[str] = None
    model: Optional[str] = None
    concept: Optional[str] = None
    # 書き出し設定
    export_with_music: Optional[bool] = None
    export_loop_music: Optional[bool] = None
    export_audio_fade_in: Optional[bool] = None
    export_audio_fade_in_sec: Optional[float] = None
    export_audio_fade_out: Optional[bool] = None
    export_audio_fade_out_sec: Optional[float] = None
    export_video_fade_out_black: Optional[bool] = None
    export_video_fade_out_sec: Optional[float] = None


@router.put("/projects/{name}/settings")
def save_project_settings(name: str, body: ProjectSettingsBody) -> dict:
    """プロジェクト設定を保存する。"""
    proj = _load_proj(name)

    # settings.json に保存するキー（None は除外）
    settings_data = {k: v for k, v in body.model_dump().items() if v is not None and k != "concept"}
    if settings_data:
        settings_manager.save(proj.project_dir, settings_data)

    # project.json に反映すべきフィールドを更新
    changed = False
    if body.concept is not None:
        proj.concept = body.concept
        changed = True
    if body.comfyui_url is not None:
        proj.comfyui_url = body.comfyui_url
        changed = True
    if body.image_resolution_w is not None and body.image_resolution_h is not None:
        proj.image_resolution = {"width": body.image_resolution_w, "height": body.image_resolution_h}
        changed = True
    if body.video_resolution_w is not None and body.video_resolution_h is not None:
        proj.video_resolution = {"width": body.video_resolution_w, "height": body.video_resolution_h}
        changed = True
    if body.video_final_resolution_w is not None and body.video_final_resolution_h is not None:
        proj.video_final_resolution = {"width": body.video_final_resolution_w, "height": body.video_final_resolution_h}
        changed = True
    if body.video_fps is not None:
        proj.video_fps = body.video_fps
        changed = True
    if body.video_frame_count is not None:
        proj.video_frame_count = body.video_frame_count
        changed = True
    if body.image_workflow is not None:
        proj.image_workflow = body.image_workflow
        changed = True
    if body.video_workflow is not None:
        proj.video_workflow = body.video_workflow
        changed = True
    if changed:
        proj.save()

    return {"ok": True}


@router.put("/projects/{name}/music")
async def replace_music(name: str, music: UploadFile = File(...)) -> dict:
    """プロジェクトの音楽ファイルを差し替える。"""
    import shutil

    proj = _load_proj(name)

    suffix = Path(music.filename or "music.mp3").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(await music.read())

    try:
        duration = _get_audio_duration(tmp_path)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"音楽ファイルエラー: {e}")

    try:
        # 旧ファイル削除
        old_abs = proj.absolute_music_path()
        if old_abs and old_abs.exists():
            old_abs.unlink(missing_ok=True)

        music_dest = proj.music_dir / (music.filename or f"music{suffix}")
        shutil.copy2(tmp_path, music_dest)
        proj.music_file = str(music_dest.relative_to(proj.project_dir))
        proj.duration = duration
        proj.save()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"duration": duration, "music_file": proj.music_file}


@router.get("/projects/{name}/workflows")
def get_project_workflows(name: str) -> dict:
    """利用可能なワークフロー一覧を返す。"""
    return {
        "image": list_image_workflows(),
        "video": list_video_workflows(),
    }


@router.get("/config")
def get_config() -> dict:
    """アプリのデフォルト設定（config.yaml + DEFAULT_SETTINGS）を返す。"""
    return {
        "defaults": settings_manager.DEFAULT_SETTINGS,
        "image_workflows": list_image_workflows(),
        "video_workflows": list_video_workflows(),
        "last_project": settings_manager.get_last_project(),
    }
