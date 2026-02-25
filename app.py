"""MV Generator - Gradio メインアプリケーション。"""

from __future__ import annotations

import re
import json
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import gradio as gr
import yaml

from src.project import Project, list_projects
from src.scene import Scene
from src.llm_client import LLMClient
from src import model_manager
from src import settings_manager
from src.comfyui_client import ComfyUIClient
from src.batch_generator import BatchGenerator
from src.video_export import VideoExporter

# ---- 設定読込 ----

# __file__ 基準の絶対パスにすることで、Gradio コールバック実行時に
# CWD が一時ディレクトリに変わっても正しいパスを指せるようにする。
_APP_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = _APP_DIR / "config.yaml"
_WORKFLOWS_DIR = _APP_DIR / "workflows"


def _list_workflows(kind: str) -> list[str]:
    """workflows/{kind}/ フォルダ内の JSON ファイルをアプリルートからの相対パスで返す。"""
    folder = _WORKFLOWS_DIR / kind
    if not folder.exists():
        return []
    return sorted(
        str(p.relative_to(_APP_DIR)).replace("\\", "/")
        for p in folder.glob("*.json")
    )


def _list_image_workflows() -> list[str]:
    return _list_workflows("image")


def _list_video_workflows() -> list[str]:
    return _list_workflows("video")

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

_cfg = _load_config()
_base_dir_cfg = _cfg.get("project", {}).get("base_dir", "projects")
# 絶対パスが指定されていればそのまま使い、相対パスなら app.py のディレクトリを基準にする
BASE_DIR = (Path(_base_dir_cfg) if Path(_base_dir_cfg).is_absolute()
            else _APP_DIR / _base_dir_cfg)

# ---- グローバル状態 ----
# GradioのState経由で管理するが、バックグラウンドスレッドとの共有のため
# バッチジェネレータはモジュールレベルで保持する
_batch_gen: Optional[BatchGenerator] = None
_batch_log: list[str] = []
_batch_lock = threading.Lock()
_batch_started_at: Optional[float] = None
_batch_finished_at: Optional[float] = None
_batch_current_task: str = ""
_batch_mode_label: str = ""
_batch_run_id: int = 0
_batch_stop_requested: bool = False

# ---- ユーティリティ ----

def _get_audio_duration(path: str | Path) -> float:
    """mutagen で音楽ファイルの長さ（秒）を取得する。"""
    from mutagen import File as MutagenFile
    audio = MutagenFile(str(path))
    if audio is None or audio.info is None:
        raise ValueError("音楽ファイルの長さを取得できませんでした")
    return float(audio.info.length)

def _read_seed_from_png(img_path: str | Path) -> Optional[int]:
    """ComfyUI が生成した PNG のメタデータからシード値を読み取る。

    ComfyUI は PNG の tEXt チャンク "prompt" に API プロンプト JSON を埋め込む。
    そこから KSampler 等のノードの seed / noise_seed を探して返す。
    見つからない場合は None を返す。
    """
    try:
        import json as _json
        from PIL import Image as _PILImage
        img = _PILImage.open(str(img_path))
        prompt_str = img.info.get("prompt", "")
        if not prompt_str:
            return None
        prompt_data = _json.loads(prompt_str)
        for node in prompt_data.values():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs", {})
            for key in ("seed", "noise_seed"):
                val = inputs.get(key)
                if isinstance(val, (int, float)) and int(val) >= 0:
                    return int(val)
    except Exception:
        pass
    return None


def _read_seed_from_scene_json(scene_json_path: str | Path, key: str) -> Optional[int]:
    """scene.json ファイルから指定キー（image_seed / video_seed）を読み取る。"""
    try:
        import json as _json
        data = _json.loads(Path(scene_json_path).read_text(encoding="utf-8"))
        val = data.get(key)
        if isinstance(val, (int, float)):
            return int(val)
    except Exception:
        pass
    return None


def _project_from_state(state: dict | None) -> Optional[Project]:
    """State辞書からProjectオブジェクトを復元する。"""
    if not state:
        return None
    try:
        return Project.load(BASE_DIR / state["project_name"])
    except Exception:
        return None


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def _scene_status_label(scene: Scene) -> str:
    return f"{scene.status_icon()} {scene.scene_id:03d}"


def _list_scene_image_versions(scene: Scene, scene_dir: Path) -> list[Path]:
    versions_dir = scene.image_versions_dir(scene_dir)
    if not versions_dir.exists():
        return []
    return sorted(
        [p for p in versions_dir.glob("*.png") if p.is_file()],
        key=lambda p: p.name,
        reverse=True,
    )


def _files_identical(path_a: Path, path_b: Path) -> bool:
    if not (path_a.exists() and path_b.exists()):
        return False
    if path_a.stat().st_size != path_b.stat().st_size:
        return False
    return path_a.read_bytes() == path_b.read_bytes()


def _ensure_scene_image_history(scene: Scene, scene_dir: Path) -> bool:
    active_path = scene.image_path(scene_dir)
    if not active_path.exists():
        if scene.active_image_version:
            scene.active_image_version = ""
            return True
        return False

    versions_dir = scene.image_versions_dir(scene_dir)
    versions_dir.mkdir(parents=True, exist_ok=True)
    changed = False

    if scene.active_image_version:
        active_version_path = scene.image_version_path(scene_dir, scene.active_image_version)
        if active_version_path.exists():
            if not _files_identical(active_path, active_version_path):
                shutil.copy2(active_path, active_version_path)
            return changed

    for version_path in _list_scene_image_versions(scene, scene_dir):
        if _files_identical(active_path, version_path):
            if scene.active_image_version != version_path.name:
                scene.active_image_version = version_path.name
                changed = True
            return changed

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    imported_name = f"image_{stamp}_imported.png"
    imported_path = scene.image_version_path(scene_dir, imported_name)
    while imported_path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        imported_name = f"image_{stamp}_imported.png"
        imported_path = scene.image_version_path(scene_dir, imported_name)
    shutil.copy2(active_path, imported_path)
    scene.active_image_version = imported_name
    return True


def _selected_version_path(scene: Scene, scene_dir: Path, filename: str | None) -> Optional[Path]:
    if not filename:
        return None
    path = scene.image_version_path(scene_dir, Path(filename).name)
    return path if path.exists() else None


def _image_history_ui_updates(scene: Scene, scene_dir: Path, selected_name: str | None = None) -> tuple:
    versions = _list_scene_image_versions(scene, scene_dir)
    choices = [p.name for p in versions]
    if not choices:
        return gr.update(choices=[], value=None), None

    if selected_name not in choices:
        selected_name = scene.active_image_version if scene.active_image_version in choices else choices[0]
    selected_path = _selected_version_path(scene, scene_dir, selected_name)
    return gr.update(choices=choices, value=selected_name), (str(selected_path) if selected_path else None)


def _list_scene_video_versions(scene: Scene, scene_dir: Path, quality: str = "preview") -> list[Path]:
    """動画バージョン一覧を新しい順で返す。"""
    versions_dir = scene.video_versions_dir(scene_dir)
    if not versions_dir.exists():
        return []
    suffix = "_final" if quality == "final" else "_preview"
    return sorted(
        [p for p in versions_dir.glob(f"video{suffix}_*.mp4") if p.is_file()],
        key=lambda p: p.name,
        reverse=True,
    )


def _selected_video_version_path(scene: Scene, scene_dir: Path, filename: str | None) -> Optional[Path]:
    if not filename:
        return None
    path = scene.video_version_path(scene_dir, Path(filename).name)
    return path if path.exists() else None


def _video_history_ui_updates(scene: Scene, scene_dir: Path, quality: str = "preview", selected_name: str | None = None) -> tuple:
    versions = _list_scene_video_versions(scene, scene_dir, quality)
    choices = [p.name for p in versions]
    if not choices:
        return gr.update(choices=[], value=None), None

    active = scene.active_video_final_version if quality == "final" else scene.active_video_preview_version
    if selected_name not in choices:
        selected_name = active if active in choices else choices[0]
    selected_path = _selected_video_version_path(scene, scene_dir, selected_name)
    return gr.update(choices=choices, value=selected_name), (str(selected_path) if selected_path else None)


def _ensure_scene_video_history(scene: Scene, scene_dir: Path, quality: str = "preview") -> bool:
    """video_preview.mp4 / video_final.mp4 が存在する場合、video_versions/ に登録する。"""
    if quality == "final":
        active_path = scene.video_final_path(scene_dir)
        active_attr = "active_video_final_version"
    else:
        active_path = scene.video_preview_path(scene_dir)
        active_attr = "active_video_preview_version"

    if not active_path.exists():
        if getattr(scene, active_attr):
            setattr(scene, active_attr, "")
            return True
        return False

    versions_dir = scene.video_versions_dir(scene_dir)
    versions_dir.mkdir(parents=True, exist_ok=True)
    changed = False

    active_version = getattr(scene, active_attr)
    if active_version:
        active_version_path = scene.video_version_path(scene_dir, active_version)
        if active_version_path.exists():
            if not _files_identical(active_path, active_version_path):
                shutil.copy2(active_path, active_version_path)
            return changed

    for version_path in _list_scene_video_versions(scene, scene_dir, quality):
        if _files_identical(active_path, version_path):
            if active_version != version_path.name:
                setattr(scene, active_attr, version_path.name)
                changed = True
            return changed

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    suffix = "_final" if quality == "final" else "_preview"
    imported_name = f"video{suffix}_{stamp}_imported.mp4"
    imported_path = scene.video_version_path(scene_dir, imported_name)
    while imported_path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        imported_name = f"video{suffix}_{stamp}_imported.mp4"
        imported_path = scene.video_version_path(scene_dir, imported_name)
    shutil.copy2(active_path, imported_path)
    setattr(scene, active_attr, imported_name)
    return True


# ============================================================
# タブ1: プロジェクト
# ============================================================

def create_project_tab():
    """プロジェクトタブのUIと処理を定義する。"""

    with gr.Tab("プロジェクト"):
        gr.Markdown("## プロジェクト管理")

        with gr.Row():
            # --- 新規作成 ---
            with gr.Column():
                gr.Markdown("### 新規プロジェクト作成")
                new_name = gr.Textbox(label="プロジェクト名", placeholder="my_mv")
                new_music = gr.Audio(label="音楽ファイル", type="filepath")
                new_music_duration = gr.Textbox(label="音楽長(秒)", interactive=False)
                new_scene_dur = gr.Slider(label="シーン長さ（秒）", minimum=3, maximum=10, value=5, step=1)
                new_create_btn = gr.Button("作成", variant="primary")
                new_status = gr.Textbox(label="ステータス", interactive=False)

            # --- 既存読込 ---
            with gr.Column():
                gr.Markdown("### 既存プロジェクト読込")
                load_dropdown = gr.Dropdown(
                    label="プロジェクト一覧",
                    choices=list_projects(BASE_DIR),
                )
                load_refresh_btn = gr.Button("一覧更新")
                load_btn = gr.Button("読込", variant="primary")
                load_status = gr.Textbox(label="ステータス", interactive=False)

        gr.Markdown("---")
        gr.Markdown("### 基本設定")

        with gr.Row():
            cfg_comfyui_url = gr.Textbox(
                label="ComfyUI URL",
                value=_cfg.get("comfyui", {}).get("url", "http://localhost:8188"),
            )

        default_img_res = _cfg.get("defaults", {}).get(
            "image_resolution",
            _cfg.get("defaults", {}).get("resolution", {"width": 1280, "height": 720}),
        )
        default_vid_res = _cfg.get("defaults", {}).get(
            "video_resolution",
            _cfg.get("defaults", {}).get("resolution", {"width": 640, "height": 480}),
        )
        default_vid_final_res = _cfg.get("defaults", {}).get(
            "video_final_resolution", {"width": 1280, "height": 720}
        )
        default_vid_fps = _cfg.get("defaults", {}).get("video_fps", _cfg.get("defaults", {}).get("fps", 16))
        default_vid_frames = _cfg.get("defaults", {}).get("video_frame_count", 81)

        with gr.Tabs():
            with gr.Tab("画像設定"):
                with gr.Row():
                    cfg_img_res_w = gr.Number(label="画像解像度 幅", value=default_img_res.get("width", 1280), precision=0)
                    cfg_img_res_h = gr.Number(label="画像解像度 高さ", value=default_img_res.get("height", 720), precision=0)
                with gr.Row():
                    cfg_img_wf = gr.Dropdown(
                        label="デフォルト画像ワークフロー (workflows/image/)",
                        choices=_list_image_workflows(),
                        value=_cfg.get("comfyui", {}).get(
                            "image_workflow", "workflows/image/zimage_turbo.json"
                        ),
                        allow_custom_value=True,
                        scale=5,
                    )
                    cfg_img_wf_refresh = gr.Button("更新", scale=1, size="sm")

            with gr.Tab("動画設定"):
                with gr.Row():
                    cfg_vid_res_w = gr.Number(label="プレビュー解像度 幅", value=default_vid_res.get("width", 640), precision=0)
                    cfg_vid_res_h = gr.Number(label="プレビュー解像度 高さ", value=default_vid_res.get("height", 480), precision=0)
                with gr.Row():
                    cfg_vid_final_res_w = gr.Number(label="最終版解像度 幅", value=default_vid_final_res.get("width", 1280), precision=0)
                    cfg_vid_final_res_h = gr.Number(label="最終版解像度 高さ", value=default_vid_final_res.get("height", 720), precision=0)
                with gr.Row():
                    cfg_vid_fps = gr.Number(label="FPS", value=default_vid_fps, precision=0)
                    cfg_vid_frame_count = gr.Number(label="フレーム数", value=default_vid_frames, precision=0)
                with gr.Row():
                    cfg_vid_wf = gr.Dropdown(
                        label="デフォルト動画ワークフロー (workflows/video/)",
                        choices=_list_video_workflows(),
                        value=_cfg.get("comfyui", {}).get(
                            "video_workflow", "workflows/video/wan22_i2v.json"
                        ),
                        allow_custom_value=True,
                        scale=5,
                    )
                    cfg_vid_wf_refresh = gr.Button("更新", scale=1, size="sm")

        save_cfg_btn = gr.Button("設定を保存")
        save_cfg_status = gr.Textbox(label="", interactive=False)

    return (
        new_name, new_music, new_music_duration, new_scene_dur, new_create_btn, new_status,
        load_dropdown, load_refresh_btn, load_btn, load_status,
        cfg_comfyui_url, cfg_img_res_w, cfg_img_res_h, cfg_vid_res_w, cfg_vid_res_h,
        cfg_vid_final_res_w, cfg_vid_final_res_h,
        cfg_vid_fps, cfg_vid_frame_count,
        cfg_img_wf, cfg_vid_wf, cfg_img_wf_refresh, cfg_vid_wf_refresh,
        save_cfg_btn, save_cfg_status,
    )


# ============================================================
# タブ2: 計画
# ============================================================

def create_plan_tab():
    """計画タブのUIを定義する。"""

    with gr.Tab("計画"):
        with gr.Row():
            # --- 左カラム: LLMチャット ---
            with gr.Column(scale=2):
                gr.Markdown("### LLMチャット（コンセプト相談）")
                plan_chatbot = gr.Chatbot(height=300)
                plan_chat_input = gr.Textbox(
                    label="メッセージ", placeholder="コンセプトについて質問...", lines=2
                )
                with gr.Row():
                    plan_chat_send = gr.Button("送信", variant="primary", scale=3)
                    plan_chat_clear = gr.Button("🗑 クリア", scale=1)
                plan_concept_input = gr.Textbox(label="全体コンセプト（保存用）", lines=2)
                plan_img_common_prompt = gr.Textbox(
                    label="画像プロンプト共通文（任意）",
                    lines=2,
                    placeholder="例: hyper-realistic miniature diorama, tilt-shift lens effect, depth of field",
                )
                with gr.Row():
                    plan_img_common_save_btn = gr.Button("共通文を保存", variant="secondary")
                    plan_img_common_status = gr.Textbox(label="", interactive=False, scale=3)
                plan_vid_common_instruction = gr.Textbox(
                    label="動画プロンプト共通追加指示（任意）",
                    lines=2,
                    placeholder="例: subtle camera movement, realistic physics, no abrupt motion",
                )
                with gr.Row():
                    plan_vid_common_save_btn = gr.Button("動画共通指示を保存", variant="secondary")
                    plan_vid_common_status = gr.Textbox(label="", interactive=False, scale=3)
                plan_bulk_btn = gr.Button("シーンの一括提案", variant="secondary")
                plan_bulk_status = gr.Textbox(label="一括提案ステータス", interactive=False, show_label=True)

            # --- 右カラム: シーン計画一覧 ---
            with gr.Column(scale=3):
                gr.Markdown("### シーン計画一覧")
                plan_scene_df = gr.Dataframe(
                    headers=["ID", "時間", "セクション", "プロット"],
                    datatype=["number", "str", "str", "str"],
                    column_count=(4, "fixed"),
                    interactive=True,
                    wrap=True,
                    value=[],
                    row_count=(1, "dynamic"),
                )
                with gr.Row():
                    plan_refresh_btn = gr.Button("🔄 更新", variant="secondary")
                    plan_save_all_btn = gr.Button("全て保存（コンセプト＋シーン計画）", variant="primary")
                plan_save_all_status = gr.Textbox(label="保存ステータス", interactive=False)

    return (
        plan_chatbot, plan_chat_input, plan_chat_send, plan_chat_clear,
        plan_concept_input,
        plan_img_common_prompt, plan_img_common_save_btn, plan_img_common_status,
        plan_vid_common_instruction, plan_vid_common_save_btn, plan_vid_common_status,
        plan_bulk_btn, plan_bulk_status,
        plan_scene_df, plan_refresh_btn, plan_save_all_btn, plan_save_all_status,
    )


# ============================================================
# タブ3: 生成・編集
# ============================================================

def create_generate_tab():
    """生成・編集タブのUIを定義する。"""

    with gr.Tab("生成・編集") as gen_tab:
        with gr.Row():
            # --- サイドバー ---
            with gr.Column(scale=1, min_width=140):
                gr.Markdown("### シーン一覧")
                gen_scene_btns = gr.Dataset(
                    label="",
                    components=[gr.Textbox(visible=False)],
                    samples=[],
                    type="index",
                    headers=["シーン"],
                )
                gen_prev_btn = gr.Button("◀ Prev")
                gen_next_btn = gr.Button("Next ▶")

                with gr.Accordion("一括生成", open=False):
                    gen_batch_img_prompt_btn = gr.Button("画像プロンプト")
                    gen_batch_img_btn = gr.Button("画像", variant="primary")
                    gen_batch_vid_prompt_btn = gr.Button("動画プロンプト")
                    gen_batch_preview_btn = gr.Button("プレビュー動画")
                    gen_batch_final_btn = gr.Button("最終版動画", variant="secondary")
                    gen_stop_btn = gr.Button("停止")
                    gen_progress = gr.Textbox(label="進捗", interactive=False, lines=4)

            # --- メインエリア ---
            with gr.Column(scale=4):
                with gr.Row():
                    # 左: シーン情報・有効フラグ
                    with gr.Column(scale=1):
                        gen_scene_id_disp = gr.Number(label="シーンID", value=1, interactive=False, precision=0)
                        gen_time_disp = gr.Textbox(label="時間", interactive=False)
                        gen_enabled = gr.Checkbox(label="このシーンを有効にする", value=True)
                    # 右: シーン説明・ステータス・保存
                    with gr.Column(scale=3):
                        gen_plot = gr.Textbox(
                            label="シーン説明（何を描くかの計画）",
                            lines=3,
                            placeholder="このシーンで描く内容を記入",
                        )
                        gen_status_disp = gr.Textbox(label="ステータス", interactive=False)
                        gen_save_btn = gr.Button("保存", variant="primary")

                with gr.Accordion("シーン管理", open=False):
                    with gr.Row():
                        gen_move_up_btn = gr.Button("↑ 上へ", size="sm")
                        gen_move_down_btn = gr.Button("↓ 下へ", size="sm")
                        gen_insert_btn = gr.Button("＋ 後に挿入", size="sm")
                        gen_delete_btn = gr.Button("🗑 削除", size="sm", variant="stop")

                # --- タブ: 画像 / プレビュー動画 / 最終版動画 ---
                with gr.Tabs():
                    # ■ 画像タブ
                    with gr.Tab("画像"):
                        gen_image_preview = gr.Image(label="生成画像", type="filepath")
                        with gr.Row():
                            with gr.Column(scale=3):
                                gen_img_prompt = gr.Textbox(label="画像プロンプト（英語）", lines=4)
                                gen_img_neg = gr.Textbox(label="画像ネガティブ（英語）", lines=2)
                                with gr.Row():
                                    gen_img_seed = gr.Number(label="画像シード(-1=ランダム)", value=-1, precision=0, scale=3)
                                    gen_img_seed_rand_btn = gr.Button("🎲", scale=1, size="sm", min_width=44)
                                    gen_img_seed_reload_btn = gr.Button("♻️", scale=1, size="sm", min_width=44)
                                    gen_img_wf = gr.Dropdown(
                                        label="画像ワークフロー（空=プロジェクトデフォルト）",
                                        choices=[""] + _list_image_workflows(),
                                        value="",
                                        allow_custom_value=True,
                                        scale=4,
                                    )
                            with gr.Column(scale=2):
                                gen_img_chatbot = gr.Chatbot(label="LLM相談", height=200)
                                gen_img_chat_input = gr.Textbox(
                                    label="",
                                    placeholder="画像プロンプトの修正指示を入力...",
                                )
                                with gr.Row():
                                    gen_img_chat_send = gr.Button("送信", variant="primary", scale=3)
                                    gen_img_chat_clear = gr.Button("🗑 クリア", scale=1)
                        with gr.Row():
                            gen_generate_img_prompt_btn = gr.Button("画像プロンプトの生成", variant="secondary")
                            gen_regen_img_btn = gr.Button("画像の生成", variant="primary")
                            gen_delete_image_btn = gr.Button("画像を削除", variant="stop")
                            gen_reset_scene_from_image_btn = gr.Button("このシーンをリセット", variant="stop")
                        with gr.Accordion("画像履歴", open=False):
                            with gr.Row():
                                gen_img_history = gr.Dropdown(
                                    label="保存済み画像",
                                    choices=[],
                                    value=None,
                                    allow_custom_value=False,
                                    scale=4,
                                )
                                gen_img_history_refresh_btn = gr.Button("更新", scale=1, size="sm")
                            with gr.Row():
                                gen_img_use_saved_btn = gr.Button("選択画像を本番に設定", variant="secondary")
                                gen_img_delete_saved_btn = gr.Button("選択画像を削除", variant="stop")
                            gen_img_history_preview = gr.Image(label="選択中の保存画像", type="filepath")

                    # ■ プレビュー動画タブ
                    with gr.Tab("プレビュー動画"):
                        gen_video_preview = gr.Video(label="プレビュー動画")
                        with gr.Row():
                            with gr.Column(scale=3):
                                gen_vid_prompt = gr.Textbox(label="動画プロンプト（英語）", lines=3)
                                gen_vid_neg = gr.Textbox(label="動画ネガティブ（英語）", lines=2)
                                with gr.Row():
                                    gen_vid_seed = gr.Number(label="動画シード(-1=ランダム)", value=-1, precision=0, scale=3)
                                    gen_vid_seed_rand_btn = gr.Button("🎲", scale=1, size="sm", min_width=44)
                                    gen_vid_seed_reload_btn = gr.Button("♻️", scale=1, size="sm", min_width=44)
                                    gen_vid_wf = gr.Dropdown(
                                        label="動画ワークフロー（空=プロジェクトデフォルト）",
                                        choices=[""] + _list_video_workflows(),
                                        value="",
                                        allow_custom_value=True,
                                        scale=4,
                                    )
                            with gr.Column(scale=2):
                                gen_vid_extra_input = gr.Textbox(
                                    label="追加指示",
                                    placeholder="動かしたい内容・雰囲気・カメラワーク等...",
                                    lines=4,
                                )
                                gen_vid_consult_btn = gr.Button("プロンプト生成", variant="secondary")
                        with gr.Row():
                            gen_regen_vid_btn = gr.Button("プレビュー動画を生成", variant="primary")
                            gen_delete_preview_btn = gr.Button("削除", variant="stop")
                            gen_reset_scene_from_preview_btn = gr.Button("このシーンをリセット", variant="stop")
                        with gr.Accordion("プレビュー動画履歴", open=False):
                            with gr.Row():
                                gen_vid_preview_history = gr.Dropdown(
                                    label="保存済みプレビュー動画",
                                    choices=[],
                                    value=None,
                                    allow_custom_value=False,
                                    scale=4,
                                )
                                gen_vid_preview_history_refresh_btn = gr.Button("更新", scale=1, size="sm")
                            with gr.Row():
                                gen_vid_preview_use_btn = gr.Button("選択動画を本番に設定", variant="secondary")
                                gen_vid_preview_delete_saved_btn = gr.Button("選択動画を削除", variant="stop")
                            gen_vid_preview_history_player = gr.Video(label="選択中のプレビュー動画")

                    # ■ 最終版動画タブ
                    with gr.Tab("最終版動画"):
                        gen_video_final_preview = gr.Video(label="最終版動画")
                        with gr.Row():
                            gen_regen_vid_final_btn = gr.Button("最終版動画を生成", variant="secondary")
                            gen_delete_final_btn = gr.Button("削除", variant="stop")
                            gen_reset_scene_from_final_btn = gr.Button("このシーンをリセット", variant="stop")
                        with gr.Accordion("最終版動画履歴", open=False):
                            with gr.Row():
                                gen_vid_final_history = gr.Dropdown(
                                    label="保存済み最終版動画",
                                    choices=[],
                                    value=None,
                                    allow_custom_value=False,
                                    scale=4,
                                )
                                gen_vid_final_history_refresh_btn = gr.Button("更新", scale=1, size="sm")
                            with gr.Row():
                                gen_vid_final_use_btn = gr.Button("選択動画を本番に設定", variant="secondary")
                                gen_vid_final_delete_saved_btn = gr.Button("選択動画を削除", variant="stop")
                            gen_vid_final_history_player = gr.Video(label="選択中の最終版動画")

    return (
        gen_tab,
        gen_scene_btns, gen_prev_btn, gen_next_btn,
        gen_batch_img_prompt_btn, gen_batch_img_btn, gen_batch_vid_prompt_btn, gen_batch_preview_btn, gen_batch_final_btn, gen_stop_btn, gen_progress,
        gen_scene_id_disp, gen_time_disp, gen_plot,
        gen_image_preview, gen_video_preview, gen_video_final_preview,
        gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
        gen_img_seed, gen_vid_seed,
        gen_img_wf, gen_vid_wf,
        gen_img_history, gen_img_history_preview, gen_img_use_saved_btn, gen_img_delete_saved_btn, gen_img_history_refresh_btn,
        gen_img_chatbot, gen_img_chat_input, gen_img_chat_send, gen_img_chat_clear,
        gen_img_seed_rand_btn, gen_img_seed_reload_btn,
        gen_vid_extra_input, gen_vid_consult_btn,
        gen_vid_seed_rand_btn, gen_vid_seed_reload_btn,
        gen_enabled,
        gen_move_up_btn, gen_move_down_btn, gen_insert_btn, gen_delete_btn,
        gen_delete_image_btn, gen_delete_preview_btn, gen_delete_final_btn,
        gen_reset_scene_from_image_btn, gen_reset_scene_from_preview_btn, gen_reset_scene_from_final_btn,
        gen_generate_img_prompt_btn, gen_regen_img_btn, gen_regen_vid_btn, gen_regen_vid_final_btn, gen_save_btn,
        gen_status_disp,
        gen_vid_preview_history, gen_vid_preview_history_player,
        gen_vid_preview_use_btn, gen_vid_preview_delete_saved_btn, gen_vid_preview_history_refresh_btn,
        gen_vid_final_history, gen_vid_final_history_player,
        gen_vid_final_use_btn, gen_vid_final_delete_saved_btn, gen_vid_final_history_refresh_btn,
    )


# ============================================================
# タブ4: 書き出し
# ============================================================

def create_export_tab():
    """書き出しタブのUIを定義する。"""

    with gr.Tab("書き出し"):
        gr.Markdown("## 書き出し")
        export_gallery = gr.Gallery(
            label="シーンサムネイル一覧",
            columns=8,
            height=300,
        )
        export_refresh_btn = gr.Button("サムネイル更新")
        with gr.Row():
            export_quality = gr.Radio(
                label="書き出し品質",
                choices=["プレビュー (640×360)", "最終版 (1280×720)"],
                value="プレビュー (640×360)",
            )
        with gr.Row():
            export_with_music = gr.Checkbox(label="音楽を合成する", value=True)
            export_btn = gr.Button("最終動画を書き出し（ffmpeg）", variant="primary")
        with gr.Accordion("書き出しオプション", open=False):
            with gr.Row():
                export_loop_music = gr.Checkbox(label="曲を繰り返す", value=False)
            with gr.Row():
                export_audio_fade_in = gr.Checkbox(label="冒頭を音声フェードイン", value=False)
                export_audio_fade_in_sec = gr.Number(label="フェードイン秒数", value=1.0, precision=2)
            with gr.Row():
                export_audio_fade_out = gr.Checkbox(label="末尾を音声フェードアウト", value=False)
                export_audio_fade_out_sec = gr.Number(label="フェードアウト秒数", value=1.0, precision=2)
            with gr.Row():
                export_video_fade_out_black = gr.Checkbox(label="末尾をブラックへフェードアウト", value=False)
                export_video_fade_out_sec = gr.Number(label="ブラックフェード秒数", value=1.0, precision=2)
        export_status = gr.Textbox(label="", interactive=False)
        export_video = gr.Video(label="最終動画プレビュー")

    return (
        export_gallery, export_refresh_btn,
        export_quality, export_with_music, export_loop_music,
        export_audio_fade_in, export_audio_fade_in_sec,
        export_audio_fade_out, export_audio_fade_out_sec,
        export_video_fade_out_black, export_video_fade_out_sec,
        export_btn, export_status, export_video,
    )


# ============================================================
# シーン表示ヘルパー
# ============================================================

def _scene_to_gen_values(scene: Scene, proj: Project) -> tuple:
    """SceneオブジェクトをGenerateタブの各コンポーネント値に変換する。"""
    scene_dir = proj.scene_dir(scene.scene_id)
    changed = _ensure_scene_image_history(scene, scene_dir)
    changed |= _ensure_scene_video_history(scene, scene_dir, quality="preview")
    changed |= _ensure_scene_video_history(scene, scene_dir, quality="final")
    if changed:
        proj.save_scene(scene)
    img_path = scene.image_path(scene_dir)
    vid_path = scene.video_path(scene_dir)
    vid_final_path = scene.video_final_path(scene_dir)
    img_history_update, img_history_preview = _image_history_ui_updates(scene, scene_dir)
    vid_preview_history_update, vid_preview_history_player = _video_history_ui_updates(scene, scene_dir, quality="preview")
    vid_final_history_update, vid_final_history_player = _video_history_ui_updates(scene, scene_dir, quality="final")
    return (
        scene.scene_id,
        f"{scene.start_time:.1f}s - {scene.end_time:.1f}s",
        scene.plot,
        str(img_path) if img_path.exists() else None,
        str(vid_path) if vid_path.exists() else None,
        str(vid_final_path) if vid_final_path.exists() else None,
        scene.image_prompt,
        scene.image_negative,
        scene.video_prompt,
        scene.video_negative,
        scene.image_seed,
        scene.video_seed,
        scene.image_workflow or "",
        scene.video_workflow or "",
        img_history_update,
        img_history_preview,
        scene.video_instruction,
        scene.status,
        scene.enabled,
        vid_preview_history_update,
        vid_preview_history_player,
        vid_final_history_update,
        vid_final_history_player,
    )

def _build_scene_samples(scenes: list[Scene]) -> list[list[str]]:
    """Dataset用のサンプルリストを生成する。"""
    return [[_scene_status_label(s)] for s in scenes]


def _build_plan_df(scenes: list[Scene]) -> list[list]:
    """Dataframe用のシーン計画リストを生成する。"""
    return [
        [s.scene_id, f"{s.start_time:.1f}s-{s.end_time:.1f}s", s.section or "", s.plot or ""]
        for s in scenes
    ]


# ============================================================
# タブ5: モデル管理
# ============================================================

def create_model_tab():
    """モデル管理タブのUIを定義する。"""

    with gr.Tab("モデル管理"):
        gr.Markdown("## ローカル LLM 管理（Qwen3-VL）")
        gr.Markdown(
            "モデルは HuggingFace Hub から自動ダウンロードされ、`./models/` フォルダにキャッシュされます。"
            "初回は数GB のダウンロードが発生します。ロード中はUIが応答しません。"
        )

        with gr.Row():
            model_dropdown = gr.Dropdown(
                label="モデル選択",
                choices=list(model_manager.MODEL_PRESETS.keys()),
                value=list(model_manager.MODEL_PRESETS.keys())[0],
                scale=3,
            )
            model_load_btn = gr.Button("ロード", variant="primary", scale=1)
            model_unload_btn = gr.Button("アンロード", scale=1)

        model_status = gr.Textbox(
            label="モデルステータス",
            value="未ロード",
            interactive=False,
        )
        model_vram = gr.Textbox(
            label="VRAM 使用状況",
            value="",
            interactive=False,
        )

        gr.Markdown("---")
        gr.Markdown("### モデル一覧")
        gr.Dataframe(
            value=[
                ["qwen3-vl-2b (軽量)", "Qwen/Qwen3-VL-2B-Instruct", "~4.5GB", "~4GB VRAM", "公式・最軽量"],
                ["qwen3-vl-4b (推奨)", "Qwen/Qwen3-VL-4B-Instruct", "~8.3GB", "~6GB VRAM", "公式・軽量・推奨"],
                ["qwen3-vl-8b (高性能)", "Qwen/Qwen3-VL-8B-Instruct", "~16GB", "~10GB VRAM", "公式・高性能"],
                ["huihui-qwen3-vl-2b-abliterated", "huihui-ai/Huihui-Qwen3-VL-2B-Instruct-abliterated", "~4.5GB", "~4GB VRAM", "検閲除去版・2B"],
                ["huihui-qwen3-vl-4b-abliterated", "huihui-ai/Huihui-Qwen3-VL-4B-Instruct-abliterated", "~8.3GB", "~6GB VRAM", "検閲除去版・4B"],
                ["huihui-qwen3-vl-8b-abliterated", "huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated", "~16GB", "~10GB VRAM", "検閲除去版・8B"],
            ],
            headers=["表示名", "HuggingFace ID", "ディスク容量", "必要VRAM", "説明"],
            interactive=False,
            wrap=True,
        )

        gr.Markdown(
            "> **注意**: ローカルモデルをロードすると、計画タブの LLM 機能はローカルモデルを使用します。"
            " アンロード後は config.yaml の LLM URL 設定（API モード）にフォールバックします。"
        )

    return model_dropdown, model_load_btn, model_unload_btn, model_status, model_vram


# ============================================================
# LLM 呼び出しヘルパー（ローカル優先、API フォールバック）
# ============================================================

def _llm_chat(messages: list[dict], proj: Optional[Project]) -> str:
    """ローカルモデルが loaded なら使用し、そうでなければ API を呼ぶ。"""
    return "".join(_llm_chat_stream(messages, proj))


def _llm_chat_stream(messages: list[dict], proj: Optional[Project]):
    """ローカルモデル優先でストリーミングチャットを返すジェネレータ。"""
    if model_manager.is_loaded():
        yield from model_manager.chat_stream(messages)
        return
    # API フォールバック
    llm_url = proj.llm_url if proj else _cfg.get("llm", {}).get("url", "http://localhost:11434/v1")
    llm_model = _cfg.get("llm", {}).get("model", "qwen3-vl")
    client = LLMClient(base_url=llm_url, model=llm_model)
    try:
        yield from client.chat_stream(messages)
        return
    except Exception as e:
        err = str(e).lower()
        is_model_not_found = ("model" in err) and ("not found" in err or "does not exist" in err or "404" in err)
        if not is_model_not_found:
            raise

        model_ids = client.list_model_ids()
        fallback = next((m for m in model_ids if m != llm_model), None)
        if not fallback:
            avail = ", ".join(model_ids) if model_ids else "(取得できませんでした)"
            raise RuntimeError(
                f"LLMモデル '{llm_model}' が見つかりません。config.yaml の llm.model を修正してください。利用可能モデル: {avail}"
            ) from e

        # 自動フォールバック（同一起動中）
        _cfg.setdefault("llm", {})["model"] = fallback
        retry_client = LLMClient(base_url=llm_url, model=fallback)
        yield from retry_client.chat_stream(messages)


def _llm_bulk(concept, scene_count, scene_duration, refs, proj,
              start_scene_id: int = 1) -> list[dict]:
    """全シーン一括提案をローカル or API で実行する。"""
    if model_manager.is_loaded():
        return model_manager.generate_all_scene_prompts(
            concept=concept,
            scene_count=scene_count,
            scene_duration=scene_duration,
            start_scene_id=start_scene_id,
            reference_images=refs if refs else None,
        )
    llm_url = proj.llm_url if proj else _cfg.get("llm", {}).get("url", "http://localhost:11434/v1")
    llm_model = _cfg.get("llm", {}).get("model", "qwen3-vl")
    client = LLMClient(base_url=llm_url, model=llm_model)
    return client.generate_all_scene_prompts(
        concept=concept,
        scene_count=scene_count,
        scene_duration=scene_duration,
        start_scene_id=start_scene_id,
        reference_images=refs if refs else None,
    )


def _extract_json_dict_from_text(text: str) -> dict:
    """Extract JSON object from plain text or ```json fenced block."""
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    m = re.search(r"```json\s*([\s\S]+?)\s*```", stripped, flags=re.IGNORECASE)
    raw = m.group(1) if m else stripped
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _llm_propose_missing_scene(
    proj: Project,
    concept: str,
    scene_idx: int,
    scene: Scene,
    proposed: dict[int, tuple[str, str]],
) -> tuple[str, str]:
    """Generate section/plot for a single scene with neighboring context."""

    def _ctx_text(target_idx: int) -> str:
        lines: list[str] = []
        for offset in (-2, -1, 1, 2):
            idx = target_idx + offset
            if idx < 0 or idx >= len(proj.scenes):
                continue
            s = proj.scenes[idx]
            sec = (proposed.get(s.scene_id, ("", ""))[0] or s.section or "").strip()
            pl = (proposed.get(s.scene_id, ("", ""))[1] or s.plot or "").strip()
            lines.append(
                f"- scene_id={s.scene_id}, time={s.start_time:.1f}-{s.end_time:.1f}, "
                f"section={sec or '(なし)'}, plot={pl or '(未入力)'}"
            )
        return "\n".join(lines) if lines else "- (前後シーンなし)"

    context_text = _ctx_text(scene_idx)
    section_hint = (scene.section or "").strip()
    user_text = (
        "あなたはMVのシーン構成プランナーです。\n"
        "指定した1シーンについて、全体コンセプトを最優先に section と plot を提案してください。\n"
        "前後シーンとの関連性は保ちつつ、内容が似すぎないように差別化してください。\n"
        "出力は必ずJSONオブジェクトのみ。\n\n"
        f"全体コンセプト:\n{concept}\n\n"
        f"対象シーン:\n"
        f"- scene_id={scene.scene_id}\n"
        f"- time={scene.start_time:.1f}-{scene.end_time:.1f}\n"
        f"- 既存section={section_hint or '(なし)'}\n"
        "- 既存plot=(未入力)\n\n"
        f"前後シーン情報:\n{context_text}\n\n"
        "制約:\n"
        "- 最優先は全体コンセプトへの一致\n"
        "- section, plot は日本語\n"
        "- plot は1〜2文で簡潔に\n"
        "- 前後の流れが自然になるようにする\n"
        "- 前後シーンと同じ描写・同じ展開を避ける（舞台/被写体/動き/視点のうち1つ以上を変える）\n"
        "- ただし完全に断絶させず、物語や雰囲気の連続性は保つ\n\n"
        "出力形式:\n"
        "{\"section\":\"...\",\"plot\":\"...\"}"
    )
    raw = _llm_chat([{"role": "user", "content": user_text}], proj)
    data = _extract_json_dict_from_text(raw)
    section = str(data.get("section", "") or "").strip()
    plot = str(data.get("plot", "") or "").strip()
    if not plot:
        # フォーマット不一致時の最小フォールバック
        plot = re.sub(r"\s+", " ", re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)).strip()[:200]
    return section, plot


def _llm_improve(scene_data, concept, refs, proj) -> dict:
    """個別シーン改善をローカル or API で実行する。"""
    if model_manager.is_loaded():
        return model_manager.improve_scene_prompt(
            scene_data=scene_data,
            concept=concept,
            reference_images=refs if refs else None,
        )
    llm_url = proj.llm_url if proj else _cfg.get("llm", {}).get("url", "http://localhost:11434/v1")
    llm_model = _cfg.get("llm", {}).get("model", "qwen3-vl")
    client = LLMClient(base_url=llm_url, model=llm_model)
    return client.improve_scene_prompt(
        scene_data=scene_data,
        concept=concept,
        reference_images=refs if refs else None,
    )


def _extract_image_prompt_text(raw_text: str) -> str:
    """Extract image prompt text from model output."""
    stripped = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    block = re.search(r"\[IMAGE_PROMPT\](.*?)\[/IMAGE_PROMPT\]", stripped, re.DOTALL | re.IGNORECASE)
    if block:
        text = block.group(1).strip()
        if text:
            return text
    return stripped


def _generate_image_prompt_from_plot(plot: str, common_prompt: str, proj: Optional[Project]) -> str:
    """Generate image prompt text from scene plot with optional common prompt."""
    common = (common_prompt or "").strip()
    common_line = common if common else "(none)"
    user_text = (
        "Create one concise English image-generation positive prompt from the scene description.\n"
        "Do not output explanation.\n"
        "If common prompt exists, include it naturally.\n\n"
        f"Scene description:\n{plot or '(empty)'}\n\n"
        f"Common prompt:\n{common_line}\n\n"
        "Output format:\n"
        "[IMAGE_PROMPT]\n"
        "<one-line prompt>\n"
        "[/IMAGE_PROMPT]"
    )
    full = "".join(_llm_chat_stream([{"role": "user", "content": user_text}], proj))
    prompt = _extract_image_prompt_text(full)
    if not prompt:
        prompt = (common_line if common else "") or "cinematic still"
    return prompt


def _extract_video_prompt_update(raw_text: str) -> tuple[str, str] | None:
    """Extract prompt/negative from [VIDEO_PROMPT_UPDATE] block."""
    stripped = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL)
    block = re.search(r"\[VIDEO_PROMPT_UPDATE\](.*?)\[/VIDEO_PROMPT_UPDATE\]", stripped, re.DOTALL | re.IGNORECASE)
    if not block:
        return None
    body = block.group(1)
    prompt_m = re.search(r"^\s*Prompt:\s*(.*?)(?=\n\s*Negative:|\Z)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    neg_m = re.search(r"^\s*Negative:\s*(.*)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    if not prompt_m:
        return None
    prompt = prompt_m.group(1).strip()
    neg = neg_m.group(1).strip() if neg_m else ""
    neg = re.sub(r"\[/?VIDEO_PROMPT_UPDATE\].*", "", neg, flags=re.DOTALL).strip()
    return prompt, neg


def _generate_video_prompt_for_scene(
    scene: Scene,
    proj: Optional[Project],
    common_instruction: str = "",
) -> tuple[str, str]:
    """Generate video prompt/negative using available scene fields."""
    instruction_parts: list[str] = []
    if (common_instruction or "").strip():
        instruction_parts.append(f"共通追加指示: {(common_instruction or '').strip()}")
    if (scene.video_instruction or "").strip():
        instruction_parts.append(f"シーン追加指示: {(scene.video_instruction or '').strip()}")
    if not instruction_parts:
        instruction_parts.append("追加指示なし。画像やプロンプトから自然で一貫性のある動画プロンプトを提案してください。")
    instruction_text = "\n".join(instruction_parts)
    text_content = (
        "これはWAN2.2 img2video向け動画プロンプトの生成タスクです。\n\n"
        f"【シーン説明】\n{(scene.plot or '(なし)').strip()}\n\n"
        f"【画像プロンプト（生成済み画像の内容）】\n{(scene.image_prompt or '(なし)').strip()}\n\n"
        f"【追加指示】{instruction_text}\n\n"
        "上記情報をもとに、WAN2.2 img2video向けの動画プロンプトを英語で生成してください。\n"
        "以下の3要素を含めてください:\n"
        "- Scene: 場面・背景・雰囲気の描写\n"
        "- Action: 被写体・人物の動き\n"
        "- Camera: カメラワーク（zoom in/out, pan left/right, tracking shot 等）\n\n"
        "以下のフォーマットのみで回答してください（説明不要）:\n"
        "[VIDEO_PROMPT_UPDATE]\n"
        "Prompt: <Scene: ..., Action: ..., Camera: ...>\n"
        "Negative: <ネガティブプロンプト、または空>\n"
        "[/VIDEO_PROMPT_UPDATE]"
    )
    full = "".join(_llm_chat_stream([{"role": "user", "content": text_content}], proj))
    parsed = _extract_video_prompt_update(full)
    if parsed:
        return parsed
    cleaned = re.sub(r"<think>.*?</think>", "", full, flags=re.DOTALL).strip()
    return cleaned or "Scene: cinematic scene, Action: subtle natural motion, Camera: slow cinematic pan", ""


def _build_plan_chat_system(proj: Optional[Project]) -> str:
    """計画タブのLLMチャット用システムプロンプトを構築する。
    プロジェクトのコンセプトと既存シーン計画をコンテキストとして含める。
    """
    lines = [
        "あなたはミュージックビデオのディレクターです。",
        "ユーザーの楽曲コンセプトをもとに、映像表現について提案・相談に応じてください。",
    ]
    if proj:
        if proj.concept:
            lines.append(f"\n【全体コンセプト】\n{proj.concept}")
        if proj.scenes:
            lines.append("\n【現在のシーン計画】")
            for s in proj.scenes:
                info = f"  シーン{s.scene_id}（{s.start_time:.1f}s-{s.end_time:.1f}s）"
                if s.section:
                    info += f" [{s.section}]"
                if s.plot:
                    info += f": {s.plot}"
                lines.append(info)
    return "\n".join(lines)


def _build_image_prompt_consult_system_prompt(positive_prompt: str, negative_prompt: str) -> str:
    return (
        "あなたは画像生成のプロンプトエンジニアリングの専門家です。\n"
        "ユーザーの意図を理解し、Stable Diffusion（Illustrious チェックポイント）向けの\n"
        "高品質なプロンプトを提案してください。\n\n"
        "現在のプロンプト:\n"
        f"Positive: {positive_prompt}\n"
        f"Negative: {negative_prompt}\n\n"
        "【修正する場合の方針】\n"
        "プロンプトの構成をなるべく変更せず、単語だけ置き換えること。\n"
        "【ネガティブプロンプトの方針】\n"
        "- ネガティブプロンプトは原則として空のままにすること。\n"
        "- ユーザーが「〜を除外したい」「〜を出したくない」と明示的に求めた場合のみ追加すること。\n"
        "- 追加する場合も 10 タグ以内に抑えること。\n\n"
        "プロンプトを更新する場合は、返答の中に以下のフォーマットで含めてください:\n"
        "[PROMPT_UPDATE]\n"
        "Positive: <新しい positive プロンプト>\n"
        "Negative: <新しい negative プロンプト>\n"
        "最後に一言報告を添えてください。\n"
        "[/PROMPT_UPDATE]"
    )


def _parse_prompt_update(text: str) -> Optional[tuple[str, str]]:
    block = re.search(r"\[PROMPT_UPDATE\](.*?)\[/PROMPT_UPDATE\]", text, re.DOTALL)
    if not block:
        return None
    body = block.group(1)
    pos = re.search(r"Positive:\s*(.*)", body)
    neg = re.search(r"Negative:\s*(.*)", body)
    if not pos:
        return None
    positive = pos.group(1).strip()
    negative = neg.group(1).strip() if neg else ""
    return positive, negative


def _build_image_prompt_consult_system_prompt_v2(positive_prompt: str, negative_prompt: str) -> str:
    return (
        "You are an expert at Stable Diffusion / Z-Image Turbo prompt engineering.\n"
        "The user will give you a modification instruction (possibly in Japanese).\n"
        "Your ONLY job is to modify the current prompt according to the instruction and output the result.\n\n"
        "Current prompt:\n"
        f"Positive: {positive_prompt or '(empty)'}\n"
        f"Negative: {negative_prompt or '(empty)'}\n\n"
        "Rules:\n"
        "- Only change what the user explicitly requests. Keep everything else exactly as-is.\n"
        "- Negative prompt: keep it empty unless the user explicitly asks to exclude something.\n"
        "- Do NOT add explanation, commentary, or any other text.\n\n"
        "You MUST respond with ONLY the following block and nothing else:\n"
        "[PROMPT_UPDATE]\n"
        "Positive: <updated positive prompt>\n"
        "Negative: <updated negative prompt, or empty>\n"
        "[/PROMPT_UPDATE]"
    )


def _parse_prompt_update_v2(text: str) -> Optional[tuple[str, str]]:
    """LLM応答から [PROMPT_UPDATE] ブロックを解析してプロンプトを取り出す。"""
    # <think>...</think> ブロックを除去してから探す
    stripped = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    block = re.search(r"\[PROMPT_UPDATE\](.*?)\[/PROMPT_UPDATE\]", stripped, re.DOTALL)
    if not block:
        # フォールバック: テキスト全体から "Positive:" を探す（厳密な行頭マッチのみ）
        block = re.search(r"\[PROMPT_UPDATE\](.*?)\[/PROMPT_UPDATE\]", text, re.DOTALL)
        if not block:
            body = stripped
        else:
            body = block.group(1)
    else:
        body = block.group(1)
    pos = re.search(r"^\s*Positive:\s*(.*?)(?=\n\s*Negative:|\Z)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    neg = re.search(r"^\s*Negative:\s*(.*)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    if pos:
        positive = pos.group(1).strip()
        negative = neg.group(1).strip() if neg else ""
        # ネガティブに "[/PROMPT_UPDATE]" が混入していれば除去
        negative = re.sub(r"\[/?PROMPT_UPDATE\].*", "", negative, flags=re.DOTALL).strip()
        return positive, negative
    return None


def _clean_llm_response_for_display(text: str) -> str:
    """LLM応答の生テキストから表示用テキストを生成する。
    <think> ブロックと [PROMPT_UPDATE] ブロックを除去する。
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"\[PROMPT_UPDATE\].*?\[/PROMPT_UPDATE\]", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


# ============================================================
# メインアプリ構築
# ============================================================

def _settings_to_cfg_values(s: dict) -> tuple:
    """settings 辞書をプロジェクトタブの cfg_* コンポーネント値のタプルに変換する。"""
    return (
        s.get("comfyui_url", settings_manager.DEFAULT_SETTINGS["comfyui_url"]),
        s.get(
            "image_resolution_w",
            s.get("resolution_w", settings_manager.DEFAULT_SETTINGS["image_resolution_w"]),
        ),
        s.get(
            "image_resolution_h",
            s.get("resolution_h", settings_manager.DEFAULT_SETTINGS["image_resolution_h"]),
        ),
        s.get(
            "video_resolution_w",
            s.get("resolution_w", settings_manager.DEFAULT_SETTINGS["video_resolution_w"]),
        ),
        s.get(
            "video_resolution_h",
            s.get("resolution_h", settings_manager.DEFAULT_SETTINGS["video_resolution_h"]),
        ),
        s.get("video_final_resolution_w", settings_manager.DEFAULT_SETTINGS["video_final_resolution_w"]),
        s.get("video_final_resolution_h", settings_manager.DEFAULT_SETTINGS["video_final_resolution_h"]),
        s.get("video_fps", s.get("fps", settings_manager.DEFAULT_SETTINGS["video_fps"])),
        s.get("video_frame_count", settings_manager.DEFAULT_SETTINGS["video_frame_count"]),
        s.get("image_workflow", settings_manager.DEFAULT_SETTINGS["image_workflow"]),
        s.get("video_workflow", settings_manager.DEFAULT_SETTINGS["video_workflow"]),
        s.get("model", settings_manager.DEFAULT_SETTINGS["model"]),
    )


def _settings_to_export_values(s: dict) -> tuple:
    """settings 辞書を書き出しタブの export_* コンポーネント値へ変換する。"""
    d = settings_manager.DEFAULT_SETTINGS
    return (
        s.get("export_quality", d["export_quality"]),
        bool(s.get("export_with_music", d["export_with_music"])),
        bool(s.get("export_loop_music", d["export_loop_music"])),
        bool(s.get("export_audio_fade_in", d["export_audio_fade_in"])),
        float(s.get("export_audio_fade_in_sec", d["export_audio_fade_in_sec"])),
        bool(s.get("export_audio_fade_out", d["export_audio_fade_out"])),
        float(s.get("export_audio_fade_out_sec", d["export_audio_fade_out_sec"])),
        bool(s.get("export_video_fade_out_black", d["export_video_fade_out_black"])),
        float(s.get("export_video_fade_out_sec", d["export_video_fade_out_sec"])),
    )


def build_app() -> gr.Blocks:
    """Gradioアプリを構築して返す。"""

    with gr.Blocks(title="Music Video Generator") as demo:
        # グローバルState
        project_state = gr.State(None)   # {"project_name": str}
        current_scene_idx = gr.State(0)  # 0-based index

        gr.Markdown("# Music Video Generator")

        # ---- タブUI構築 ----
        (
            new_name, new_music, new_music_duration, new_scene_dur, new_create_btn, new_status,
            load_dropdown, load_refresh_btn, load_btn, load_status,
            cfg_comfyui_url, cfg_img_res_w, cfg_img_res_h, cfg_vid_res_w, cfg_vid_res_h,
            cfg_vid_final_res_w, cfg_vid_final_res_h,
            cfg_vid_fps, cfg_vid_frame_count,
            cfg_img_wf, cfg_vid_wf, cfg_img_wf_refresh, cfg_vid_wf_refresh,
            save_cfg_btn, save_cfg_status,
        ) = create_project_tab()

        (
            plan_chatbot, plan_chat_input, plan_chat_send, plan_chat_clear,
            plan_concept_input,
            plan_img_common_prompt, plan_img_common_save_btn, plan_img_common_status,
            plan_vid_common_instruction, plan_vid_common_save_btn, plan_vid_common_status,
            plan_bulk_btn, plan_bulk_status,
            plan_scene_df, plan_refresh_btn, plan_save_all_btn, plan_save_all_status,
        ) = create_plan_tab()

        (
            gen_tab,
            gen_scene_btns, gen_prev_btn, gen_next_btn,
            gen_batch_img_prompt_btn, gen_batch_img_btn, gen_batch_vid_prompt_btn, gen_batch_preview_btn, gen_batch_final_btn, gen_stop_btn, gen_progress,
            gen_scene_id_disp, gen_time_disp, gen_plot,
            gen_image_preview, gen_video_preview, gen_video_final_preview,
            gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
            gen_img_seed, gen_vid_seed,
            gen_img_wf, gen_vid_wf,
            gen_img_history, gen_img_history_preview, gen_img_use_saved_btn, gen_img_delete_saved_btn, gen_img_history_refresh_btn,
            gen_img_chatbot, gen_img_chat_input, gen_img_chat_send, gen_img_chat_clear,
            gen_img_seed_rand_btn, gen_img_seed_reload_btn,
            gen_vid_extra_input, gen_vid_consult_btn,
            gen_vid_seed_rand_btn, gen_vid_seed_reload_btn,
            gen_enabled,
            gen_move_up_btn, gen_move_down_btn, gen_insert_btn, gen_delete_btn,
            gen_delete_image_btn, gen_delete_preview_btn, gen_delete_final_btn,
            gen_reset_scene_from_image_btn, gen_reset_scene_from_preview_btn, gen_reset_scene_from_final_btn,
            gen_generate_img_prompt_btn, gen_regen_img_btn, gen_regen_vid_btn, gen_regen_vid_final_btn, gen_save_btn,
            gen_status_disp,
            gen_vid_preview_history, gen_vid_preview_history_player,
            gen_vid_preview_use_btn, gen_vid_preview_delete_saved_btn, gen_vid_preview_history_refresh_btn,
            gen_vid_final_history, gen_vid_final_history_player,
            gen_vid_final_use_btn, gen_vid_final_delete_saved_btn, gen_vid_final_history_refresh_btn,
        ) = create_generate_tab()

        (
            export_gallery, export_refresh_btn,
            export_quality, export_with_music, export_loop_music,
            export_audio_fade_in, export_audio_fade_in_sec,
            export_audio_fade_out, export_audio_fade_out_sec,
            export_video_fade_out_black, export_video_fade_out_sec,
            export_btn, export_status, export_video,
        ) = create_export_tab()

        (
            model_dropdown, model_load_btn, model_unload_btn, model_status, model_vram,
        ) = create_model_tab()


        # ============================================================
        # 起動時: 最後に開いたプロジェクトをドロップダウンに反映
        # ============================================================

        def on_app_load():
            """アプリ起動時に最後のプロジェクト選択を復元し、保存済みモデルを自動ロードする。"""
            last = settings_manager.get_last_project()
            projects = list_projects(BASE_DIR)
            dropdown_update = (
                gr.update(choices=projects, value=last)
                if last and last in projects
                else gr.update(choices=projects)
            )

            # 前回のプロジェクトから保存済みモデルラベルを取得
            model_label = None
            if last and last in projects:
                s = settings_manager.load(BASE_DIR / last)
                model_label = s.get("model")

            if not model_label or model_label not in model_manager.MODEL_PRESETS:
                return dropdown_update, gr.update(), "未ロード"

            model_id = model_manager.MODEL_PRESETS[model_label]

            def _auto_load():
                try:
                    model_manager.load_model(model_id)
                except Exception:
                    pass

            threading.Thread(target=_auto_load, daemon=True).start()
            return dropdown_update, gr.update(value=model_label), f"自動ロード中: {model_label} ..."

        demo.load(fn=on_app_load, outputs=[load_dropdown, model_dropdown, model_status])

        # ワークフロー一覧更新ボタン
        cfg_img_wf_refresh.click(
            fn=lambda: gr.update(choices=_list_image_workflows()),
            outputs=[cfg_img_wf],
        )
        cfg_vid_wf_refresh.click(
            fn=lambda: gr.update(choices=_list_video_workflows()),
            outputs=[cfg_vid_wf],
        )

        # ============================================================
        # イベントハンドラ: プロジェクトタブ
        # ============================================================

        # settings 読み書きで共通する cfg 出力コンポーネントリスト
        _cfg_outputs = [
            cfg_comfyui_url, cfg_img_res_w, cfg_img_res_h, cfg_vid_res_w, cfg_vid_res_h,
            cfg_vid_final_res_w, cfg_vid_final_res_h,
            cfg_vid_fps, cfg_vid_frame_count,
            cfg_img_wf, cfg_vid_wf, model_dropdown,
        ]
        _export_outputs = [
            export_quality, export_with_music, export_loop_music,
            export_audio_fade_in, export_audio_fade_in_sec,
            export_audio_fade_out, export_audio_fade_out_sec,
            export_video_fade_out_black, export_video_fade_out_sec,
        ]

        def on_create_project(
            name,
            music_path,
            scene_dur,
            comfyui_url,
            img_res_w,
            img_res_h,
            vid_res_w,
            vid_res_h,
            vid_final_res_w,
            vid_final_res_h,
            vid_fps,
            vid_frame_count,
            img_wf,
            vid_wf,
            model,
        ):
            """新規プロジェクトを作成する。"""
            _no_cfg = (gr.update(),) * 12
            _no_export = (gr.update(),) * 9
            if not name:
                return gr.update(), gr.update(), "プロジェクト名を入力してください", None, 0, *_no_cfg, gr.update(), gr.update(), *_no_export, gr.update()
            if not music_path:
                return gr.update(), gr.update(), "音楽ファイルをアップロードしてください", None, 0, *_no_cfg, gr.update(), gr.update(), *_no_export, gr.update()

            try:
                duration = _get_audio_duration(music_path)
            except Exception as e:
                return gr.update(), gr.update(), f"音楽ファイルエラー: {e}", None, 0, *_no_cfg, gr.update(), gr.update(), *_no_export, gr.update()

            BASE_DIR.mkdir(parents=True, exist_ok=True)
            proj = Project(
                project_name=name,
                base_dir=BASE_DIR,
                duration=duration,
                scene_duration=int(scene_dur),
                image_resolution={"width": int(img_res_w), "height": int(img_res_h)},
                video_resolution={"width": int(vid_res_w), "height": int(vid_res_h)},
                video_final_resolution={"width": int(vid_final_res_w), "height": int(vid_final_res_h)},
                video_fps=int(vid_fps),
                video_frame_count=int(vid_frame_count),
                comfyui_url=comfyui_url,
                image_workflow=img_wf,
                video_workflow=vid_wf,
            )
            proj.initialize_dirs()
            proj.copy_music(music_path)
            proj.setup_scenes()
            proj.save()

            # settings.json を保存
            settings_manager.save(proj.project_dir, {
                "comfyui_url": comfyui_url,
                "image_workflow": img_wf,
                "video_workflow": vid_wf,
                "image_resolution_w": int(img_res_w),
                "image_resolution_h": int(img_res_h),
                "video_resolution_w": int(vid_res_w),
                "video_resolution_h": int(vid_res_h),
                "video_final_resolution_w": int(vid_final_res_w),
                "video_final_resolution_h": int(vid_final_res_h),
                "video_fps": int(vid_fps),
                "video_frame_count": int(vid_frame_count),
                "scene_duration": int(scene_dur),
                "model": model,
                "batch_image_prompt_common": "",
                "batch_video_prompt_common_instruction": "",
                "export_quality": settings_manager.DEFAULT_SETTINGS["export_quality"],
                "export_with_music": settings_manager.DEFAULT_SETTINGS["export_with_music"],
                "export_loop_music": settings_manager.DEFAULT_SETTINGS["export_loop_music"],
                "export_audio_fade_in": settings_manager.DEFAULT_SETTINGS["export_audio_fade_in"],
                "export_audio_fade_in_sec": settings_manager.DEFAULT_SETTINGS["export_audio_fade_in_sec"],
                "export_audio_fade_out": settings_manager.DEFAULT_SETTINGS["export_audio_fade_out"],
                "export_audio_fade_out_sec": settings_manager.DEFAULT_SETTINGS["export_audio_fade_out_sec"],
                "export_video_fade_out_black": settings_manager.DEFAULT_SETTINGS["export_video_fade_out_black"],
                "export_video_fade_out_sec": settings_manager.DEFAULT_SETTINGS["export_video_fade_out_sec"],
            })
            settings_manager.save_last_project(name)

            state = {"project_name": name}
            samples = _build_scene_samples(proj.scenes)
            msg = f"プロジェクト '{name}' を作成しました（{len(proj.scenes)}シーン, {duration:.1f}秒）"
            s = settings_manager.load(proj.project_dir)
            return (
                _build_plan_df(proj.scenes),
                gr.Dataset(samples=samples),
                msg,
                state,
                0,
                *_settings_to_cfg_values(s),
                s.get("batch_image_prompt_common", ""),
                s.get("batch_video_prompt_common_instruction", ""),
                *_settings_to_export_values(s),
                f"{duration:.1f}",
            )

        new_create_btn.click(
            fn=on_create_project,
            inputs=[
                new_name, new_music, new_scene_dur,
                cfg_comfyui_url, cfg_img_res_w, cfg_img_res_h, cfg_vid_res_w, cfg_vid_res_h,
                cfg_vid_final_res_w, cfg_vid_final_res_h,
                cfg_vid_fps, cfg_vid_frame_count,
                cfg_img_wf, cfg_vid_wf, model_dropdown,
            ],
            outputs=[plan_scene_df, gen_scene_btns, new_status, project_state, current_scene_idx,
                     *_cfg_outputs, plan_img_common_prompt, plan_vid_common_instruction, *_export_outputs,
                     new_music_duration],
        )

        def on_load_refresh():
            return gr.update(choices=list_projects(BASE_DIR))

        load_refresh_btn.click(fn=on_load_refresh, outputs=[load_dropdown])

        def on_load_project(name):
            """既存プロジェクトを読み込む。settings.json から UI パラメータを復元する。"""
            _no_cfg = (gr.update(),) * 12
            _no_export = (gr.update(),) * 9
            if not name:
                return gr.update(), gr.update(), "プロジェクトを選択してください", None, 0, *_no_cfg, None, gr.update(), gr.update(), gr.update(), gr.update(), *_no_export, gr.update()
            try:
                proj = Project.load(BASE_DIR / name)
            except Exception as e:
                return gr.update(), gr.update(), f"読込エラー: {e}", None, 0, *_no_cfg, None, gr.update(), gr.update(), gr.update(), gr.update(), *_no_export, gr.update()

            # settings.json 読み込み
            s = settings_manager.load(proj.project_dir)
            settings_manager.save_last_project(name)

            samples = _build_scene_samples(proj.scenes)
            state = {"project_name": name}
            msg = f"プロジェクト '{name}' を読み込みました（{len(proj.scenes)}シーン）"
            music_path = proj.absolute_music_path()
            music_val = str(music_path) if music_path else None
            duration_text = f"{float(proj.duration):.1f}" if proj.duration else ""
            return (_build_plan_df(proj.scenes), gr.Dataset(samples=samples), msg, state, 0,
                    *_settings_to_cfg_values(s), music_val, name,
                    proj.concept, s.get("batch_image_prompt_common", ""),
                    s.get("batch_video_prompt_common_instruction", ""),
                    *_settings_to_export_values(s), duration_text)

        load_btn.click(
            fn=on_load_project,
            inputs=[load_dropdown],
            outputs=[plan_scene_df, gen_scene_btns, load_status, project_state, current_scene_idx,
                     *_cfg_outputs, new_music, new_name,
                     plan_concept_input, plan_img_common_prompt, plan_vid_common_instruction, *_export_outputs,
                     new_music_duration],
        )

        def on_save_config(
            comfyui_url,
            img_res_w,
            img_res_h,
            vid_res_w,
            vid_res_h,
            vid_final_res_w,
            vid_final_res_h,
            vid_fps,
            vid_frame_count,
            img_wf,
            vid_wf,
            state,
        ):
            """設定を config.yaml とプロジェクトの settings.json に保存する。"""
            try:
                # config.yaml（グローバルデフォルト）
                cfg = _load_config()
                cfg.setdefault("comfyui", {})["url"] = comfyui_url
                cfg.setdefault("comfyui", {})["image_workflow"] = img_wf
                cfg.setdefault("comfyui", {})["video_workflow"] = vid_wf
                cfg.setdefault("defaults", {})["image_resolution"] = {
                    "width": int(img_res_w),
                    "height": int(img_res_h),
                }
                cfg.setdefault("defaults", {})["video_resolution"] = {
                    "width": int(vid_res_w),
                    "height": int(vid_res_h),
                }
                cfg.setdefault("defaults", {})["video_final_resolution"] = {
                    "width": int(vid_final_res_w),
                    "height": int(vid_final_res_h),
                }
                cfg.setdefault("defaults", {})["video_fps"] = int(vid_fps)
                cfg.setdefault("defaults", {})["video_frame_count"] = int(vid_frame_count)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, allow_unicode=True)

                # プロジェクトの settings.json（プロジェクトが読み込まれている場合）
                proj = _project_from_state(state)
                if proj:
                    proj.comfyui_url = comfyui_url
                    proj.image_workflow = img_wf
                    proj.video_workflow = vid_wf
                    proj.image_resolution = {"width": int(img_res_w), "height": int(img_res_h)}
                    proj.video_resolution = {"width": int(vid_res_w), "height": int(vid_res_h)}
                    proj.video_final_resolution = {"width": int(vid_final_res_w), "height": int(vid_final_res_h)}
                    proj.video_fps = int(vid_fps)
                    proj.video_frame_count = int(vid_frame_count)
                    proj.resolution = proj.image_resolution
                    proj.save()

                    settings_manager.save(proj.project_dir, {
                        "comfyui_url": comfyui_url,
                        "image_workflow": img_wf,
                        "video_workflow": vid_wf,
                        "image_resolution_w": int(img_res_w),
                        "image_resolution_h": int(img_res_h),
                        "video_resolution_w": int(vid_res_w),
                        "video_resolution_h": int(vid_res_h),
                        "video_final_resolution_w": int(vid_final_res_w),
                        "video_final_resolution_h": int(vid_final_res_h),
                        "video_fps": int(vid_fps),
                        "video_frame_count": int(vid_frame_count),
                    })
                    return "設定を保存しました（config.yaml + settings.json）"
                return "設定を保存しました（config.yaml）"
            except Exception as e:
                return f"保存エラー: {e}"

        save_cfg_btn.click(
            fn=on_save_config,
            inputs=[cfg_comfyui_url, cfg_img_res_w, cfg_img_res_h, cfg_vid_res_w, cfg_vid_res_h,
                    cfg_vid_final_res_w, cfg_vid_final_res_h,
                    cfg_vid_fps, cfg_vid_frame_count, cfg_img_wf, cfg_vid_wf,
                    project_state],
            outputs=[save_cfg_status],
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - LLMチャット
        # ============================================================

        def on_chat_send(user_msg: str, history: list, state: dict):
            """ストリーミングでチャット応答を返すジェネレータ。"""
            if not user_msg.strip():
                yield history or [], ""
                return
            proj = _project_from_state(state)
            history = list(history or [])
            history.append({"role": "user", "content": user_msg})
            history.append({"role": "assistant", "content": ""})
            # ユーザーメッセージを即座に反映し、入力欄をクリア
            yield history, ""
            try:
                system_prompt = _build_plan_chat_system(proj)
                chat_history = [{"role": m["role"], "content": m["content"]} for m in history[:-1]]
                messages = [{"role": "system", "content": system_prompt}] + chat_history
                for chunk in _llm_chat_stream(messages, proj):
                    history[-1]["content"] += chunk
                    yield history, ""
            except Exception as e:
                history[-1]["content"] = f"LLM接続エラー: {e}"
                yield history, ""

        plan_chat_send.click(
            fn=on_chat_send,
            inputs=[plan_chat_input, plan_chatbot, project_state],
            outputs=[plan_chatbot, plan_chat_input],
        )
        plan_chat_input.submit(
            fn=on_chat_send,
            inputs=[plan_chat_input, plan_chatbot, project_state],
            outputs=[plan_chatbot, plan_chat_input],
        )
        plan_chat_clear.click(fn=lambda: ([], ""), outputs=[plan_chatbot, plan_chat_input])

        def on_save_common_image_prompt(state: dict, common_prompt: str):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません"
            try:
                settings_manager.save(proj.project_dir, {
                    "batch_image_prompt_common": (common_prompt or "").strip(),
                })
                return "共通画像プロンプトを保存しました"
            except Exception as e:
                return f"保存エラー: {e}"

        plan_img_common_save_btn.click(
            fn=on_save_common_image_prompt,
            inputs=[project_state, plan_img_common_prompt],
            outputs=[plan_img_common_status],
        )

        def on_save_common_video_instruction(state: dict, common_instruction: str):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません"
            try:
                settings_manager.save(proj.project_dir, {
                    "batch_video_prompt_common_instruction": (common_instruction or "").strip(),
                })
                return "共通動画追加指示を保存しました"
            except Exception as e:
                return f"保存エラー: {e}"

        plan_vid_common_save_btn.click(
            fn=on_save_common_video_instruction,
            inputs=[project_state, plan_vid_common_instruction],
            outputs=[plan_vid_common_status],
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - 全シーン一括提案
        # ============================================================

        def on_bulk_generate(concept: str, state: dict):
            """プロット未入力シーンのみを前後コンテキスト付きで提案し、Dataframeに表示する。"""
            proj = _project_from_state(state)
            if proj is None:
                yield "プロジェクトが読み込まれていません", gr.update()
                return
            if not concept:
                yield "コンセプトを入力してください", gr.update()
                return

            missing_indices = [i for i, s in enumerate(proj.scenes) if not (s.plot or "").strip()]
            missing_total = len(missing_indices)
            if missing_total == 0:
                yield "未入力プロットのシーンはありません", gr.update()
                return

            # 提案結果を一時的に保持（scene_id → (section, plot)）
            proposed: dict[int, tuple[str, str]] = {}

            for pos, scene_idx in enumerate(missing_indices, start=1):
                scene = proj.scenes[scene_idx]
                yield f"処理中: シーン {scene.scene_id} ({pos}/{missing_total})...", gr.update()
                try:
                    section, plot = _llm_propose_missing_scene(
                        proj=proj,
                        concept=concept,
                        scene_idx=scene_idx,
                        scene=scene,
                        proposed=proposed,
                    )
                    if plot:
                        proposed[scene.scene_id] = (section, plot)
                except Exception as e:
                    yield f"LLMエラー (シーン {scene.scene_id}): {e}", gr.update()
                    continue

                # 既存データは保持し、提案対象シーンのみ逐次上書き表示
                df_rows = [
                    [s.scene_id,
                     f"{s.start_time:.1f}s-{s.end_time:.1f}s",
                     proposed[s.scene_id][0] if s.scene_id in proposed else (s.section or ""),
                     proposed[s.scene_id][1] if s.scene_id in proposed else (s.plot or "")]
                    for s in proj.scenes
                ]
                yield (f"シーン {scene.scene_id} 提案完了（{len(proposed)}/{missing_total}件）",
                       gr.update(value=df_rows))

            yield (f"提案完了: {len(proposed)}/{missing_total}シーン。内容を確認・編集後「全て保存」を押してください。",
                   gr.update())

        plan_bulk_btn.click(
            fn=on_bulk_generate,
            inputs=[plan_concept_input, project_state],
            outputs=[plan_bulk_status, plan_scene_df],
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - 全シーン保存
        # ============================================================

        def on_plan_save_all(df_data, concept, state):
            """コンセプト・全シーン計画をまとめて保存する。"""
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません"
            # コンセプトを保存
            proj.concept = concept or ""
            # df_data は pandas DataFrame または list[list]
            if hasattr(df_data, "values"):
                rows = df_data.values.tolist()
            else:
                rows = df_data if df_data else []
            updated = 0
            for i, row in enumerate(rows):
                if i >= len(proj.scenes):
                    break
                scene = proj.scenes[i]
                section = str(row[2]) if row[2] is not None else ""
                plot = str(row[3]) if row[3] is not None else ""
                scene.section = section
                scene.plot = plot
                if scene.status == "empty" and plot:
                    scene.status = "plot_done"
                proj.save_scene(scene)
                updated += 1
            proj.save()
            return f"コンセプト・{updated}シーンを保存しました"

        plan_save_all_btn.click(
            fn=on_plan_save_all,
            inputs=[plan_scene_df, plan_concept_input, project_state],
            outputs=[plan_save_all_status],
        )

        def on_plan_refresh(state: dict):
            """シーン計画一覧をディスクから最新状態に更新する。"""
            proj = _project_from_state(state)
            if proj is None:
                return gr.update()
            return _build_plan_df(proj.scenes)

        plan_refresh_btn.click(
            fn=on_plan_refresh,
            inputs=[project_state],
            outputs=[plan_scene_df],
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - シーン切替
        # ============================================================

        gen_scene_outputs = [
            gen_scene_id_disp, gen_time_disp, gen_plot,
            gen_image_preview, gen_video_preview, gen_video_final_preview,
            gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
            gen_img_seed, gen_vid_seed,
            gen_img_wf, gen_vid_wf,
            gen_img_history, gen_img_history_preview,
            gen_vid_extra_input,
            gen_status_disp, gen_enabled, current_scene_idx, gen_img_chatbot,
            gen_vid_preview_history, gen_vid_preview_history_player,
            gen_vid_final_history, gen_vid_final_history_player,
        ]

        def load_gen_scene(idx: int, state: dict) -> tuple:
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                _no_vid_hist = gr.update(choices=[], value=None)
                return (None, "", "", None, None, None, "", "", "", "", -1, -1, "", "", gr.update(choices=[], value=None), None, "", "", True, idx, [], _no_vid_hist, None, _no_vid_hist, None)
            idx = max(0, min(idx, len(proj.scenes) - 1))
            scene = proj.scenes[idx]
            base = _scene_to_gen_values(scene, proj)
            # base[0:19] = scene値、base[19:] = 動画履歴4値
            # gen_scene_outputs[19]=current_scene_idx, [20]=gen_img_chatbot が先に来る
            return base[:19] + (idx, []) + base[19:]

        gen_tab.select(
            fn=load_gen_scene,
            inputs=[current_scene_idx, project_state],
            outputs=gen_scene_outputs,
        )
        gen_scene_btns.click(
            fn=lambda evt, state: load_gen_scene(evt, state),
            inputs=[gen_scene_btns, project_state],
            outputs=gen_scene_outputs,
        )
        gen_prev_btn.click(
            fn=lambda idx, state: load_gen_scene(idx - 1, state),
            inputs=[current_scene_idx, project_state],
            outputs=gen_scene_outputs,
        )
        gen_next_btn.click(
            fn=lambda idx, state: load_gen_scene(idx + 1, state),
            inputs=[current_scene_idx, project_state],
            outputs=gen_scene_outputs,
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 保存
        # ============================================================

        def on_gen_save(idx, state, plot, img_p, img_n, vid_p, vid_n, img_seed, vid_seed, img_wf, vid_wf, vid_instr, enabled):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません", gr.update()
            scene = proj.scenes[idx]
            scene.plot = plot
            scene.image_prompt = img_p
            scene.image_negative = img_n
            scene.video_prompt = vid_p
            scene.video_negative = vid_n
            scene.image_seed = int(img_seed)
            scene.video_seed = int(vid_seed)
            scene.image_workflow = img_wf or None
            scene.video_workflow = vid_wf or None
            scene.video_instruction = vid_instr or ""
            scene.enabled = enabled
            proj.save_scene(scene)
            samples = _build_scene_samples(proj.scenes)
            return f"シーン {scene.scene_id} を保存しました", gr.Dataset(samples=samples)

        gen_save_btn.click(
            fn=on_gen_save,
            inputs=[
                current_scene_idx, project_state,
                gen_plot,
                gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
                gen_img_seed, gen_vid_seed, gen_img_wf, gen_vid_wf,
                gen_vid_extra_input, gen_enabled,
            ],
            outputs=[gen_status_disp, gen_scene_btns],
        )

        def on_gen_image_prompt_generate(idx, state, plot):
            proj = _project_from_state(state)
            if proj is None:
                return gr.update(), "プロジェクトが読み込まれていません"
            if not (plot or "").strip():
                return gr.update(), "シーン説明が空のため生成できません"
            try:
                settings = settings_manager.load(proj.project_dir)
                common_prompt = settings.get("batch_image_prompt_common", "")
                generated = _generate_image_prompt_from_plot(plot, common_prompt, proj)
                return generated, "画像プロンプトを生成しました"
            except Exception as e:
                return gr.update(), f"画像プロンプト生成エラー: {e}"

        gen_generate_img_prompt_btn.click(
            fn=on_gen_image_prompt_generate,
            inputs=[current_scene_idx, project_state, gen_plot],
            outputs=[gen_img_prompt, gen_status_disp],
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 個別再生成
        # ============================================================

        def on_gen_img_chat_send(user_msg: str, history: list, state: dict, img_p: str, img_n: str):
            if not user_msg.strip():
                yield history or [], "", gr.update(), gr.update(), ""
                return

            proj = _project_from_state(state)
            history = list(history or [])

            # 現在のプロンプト＋指示をひとつのユーザーメッセージにまとめてLLMへ渡す。
            # system promptを使わずユーザーメッセージに文脈を埋め込むことで、
            # LLMが創作的な応答をせず編集タスクとして解釈しやすくなる。
            llm_user_content = (
                "これは画像生成プロンプトの編集タスクです。創作的な解釈は不要です。\n\n"
                "【現在のプロンプト】\n"
                f"Positive: {img_p or ''}\n"
                f"Negative: {img_n or ''}\n\n"
                f"【編集指示】{user_msg}\n\n"
                "指示された部分だけを変更し、それ以外は一切変えないでください。\n"
                "以下のフォーマットのみで回答してください（説明・コメント禁止）:\n"
                "[PROMPT_UPDATE]\n"
                "Positive: <更新後のpositiveプロンプト>\n"
                "Negative: <更新後のnegativeプロンプト、または空>\n"
                "[/PROMPT_UPDATE]"
            )
            messages = [
                {"role": "user", "content": llm_user_content},
            ]

            history.append({"role": "user", "content": user_msg})
            history.append({"role": "assistant", "content": ""})
            yield history, "", gr.update(), gr.update(), "LLM応答中..."

            try:
                for chunk in _llm_chat_stream(messages, proj):
                    history[-1]["content"] += chunk
                    yield history, "", gr.update(), gr.update(), "LLM応答中..."
            except Exception as e:
                history[-1]["content"] = f"LLMエラー: {e}"
                yield history, "", gr.update(), gr.update(), "LLMエラー"
                return

            raw_response = history[-1]["content"]
            upd = _parse_prompt_update_v2(raw_response)

            # チャット表示をクリーンにする（<think>ブロックと[PROMPT_UPDATE]タグを除去）
            display_text = _clean_llm_response_for_display(raw_response)

            if upd:
                new_pos, new_neg = upd
                # "(空)" "(empty)" 等のプレースホルダーを空文字に正規化
                _placeholders = {"(空)", "（空）", "(empty)", "empty", "none", "(none)"}
                if not new_neg.strip() or new_neg.strip().lower() in _placeholders:
                    new_neg = img_n or ""
                # 表示: 思考テキスト(あれば) + 更新後プロンプト
                summary = "プロンプトを更新しました。"
                if display_text:
                    summary = display_text
                history[-1]["content"] = f"{summary}\n\n**Positive:** {new_pos}"
                yield history, "", new_pos, new_neg, "画像プロンプトをLLM提案で更新しました"
            else:
                # 更新ブロックなし: クリーンなテキストを表示
                history[-1]["content"] = display_text or raw_response
                yield history, "", gr.update(), gr.update(), "回答を受信しました（プロンプト更新はなし）"

        gen_img_chat_send.click(
            fn=on_gen_img_chat_send,
            inputs=[gen_img_chat_input, gen_img_chatbot, project_state, gen_img_prompt, gen_img_neg],
            outputs=[gen_img_chatbot, gen_img_chat_input, gen_img_prompt, gen_img_neg, gen_status_disp],
        )
        gen_img_chat_input.submit(
            fn=on_gen_img_chat_send,
            inputs=[gen_img_chat_input, gen_img_chatbot, project_state, gen_img_prompt, gen_img_neg],
            outputs=[gen_img_chatbot, gen_img_chat_input, gen_img_prompt, gen_img_neg, gen_status_disp],
        )
        gen_img_chat_clear.click(
            fn=lambda: [],
            outputs=[gen_img_chatbot],
        )

        # シードボタン
        gen_img_seed_rand_btn.click(fn=lambda: -1, outputs=[gen_img_seed])
        gen_vid_seed_rand_btn.click(fn=lambda: -1, outputs=[gen_vid_seed])

        def _reload_img_seed(idx: int, state: dict):
            """生成済みPNGのメタデータからシード値を読み取る。失敗時はscene.jsonにフォールバック。"""
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return gr.update()
            scene = proj.scenes[max(0, min(idx, len(proj.scenes) - 1))]
            scene_dir = proj.scene_dir(scene.scene_id)
            img_path = scene.image_path(scene_dir)
            if img_path.exists():
                seed = _read_seed_from_png(img_path)
                if seed is not None:
                    return seed
            # フォールバック: scene.json から読む
            scene_json = scene_dir / "scene.json"
            seed = _read_seed_from_scene_json(scene_json, "image_seed")
            return seed if seed is not None else gr.update()

        def _reload_vid_seed(idx: int, state: dict):
            """scene.json からビデオシード値を読み取る（動画ファイルにはメタデータなし）。"""
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return gr.update()
            scene = proj.scenes[max(0, min(idx, len(proj.scenes) - 1))]
            scene_dir = proj.scene_dir(scene.scene_id)
            scene_json = scene_dir / "scene.json"
            seed = _read_seed_from_scene_json(scene_json, "video_seed")
            return seed if seed is not None else gr.update()

        gen_img_seed_reload_btn.click(
            fn=_reload_img_seed,
            inputs=[current_scene_idx, project_state],
            outputs=[gen_img_seed],
        )
        gen_vid_seed_reload_btn.click(
            fn=_reload_vid_seed,
            inputs=[current_scene_idx, project_state],
            outputs=[gen_vid_seed],
        )

        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 動画プロンプトLLM生成
        # ============================================================

        def on_gen_vid_consult(instruction: str, state: dict, img_path: str, img_p: str, vid_p: str, vid_n: str):
            """画像・画像プロンプト・追加指示を元にLLMが動画プロンプトを生成する。"""
            instruction_text = (instruction or "").strip()
            if not instruction_text:
                instruction_text = "追加指示なし。画像と画像プロンプトから自然で一貫性のある動画プロンプトを提案してください。"

            proj = _project_from_state(state)
            text_content = (
                "これはWAN2.2 img2video向け動画プロンプトの生成タスクです。\n\n"
                "【画像プロンプト（生成済み画像の内容）】\n"
                f"{img_p or '(なし)'}\n\n"
                f"【追加指示】{instruction_text}\n\n"
                "添付画像と上記の情報をもとに、WAN2.2 img2video向けの動画プロンプトを英語で生成してください。\n"
                "以下の3要素を含めてください:\n"
                "- Scene: 場面・背景・雰囲気の描写\n"
                "- Action: 被写体・人物の動き\n"
                "- Camera: カメラワーク（zoom in/out, pan left/right, tracking shot 等）\n\n"
                "以下のフォーマットのみで回答してください（説明不要）:\n"
                "[VIDEO_PROMPT_UPDATE]\n"
                "Prompt: <Scene: ..., Action: ..., Camera: ...>\n"
                "Negative: <ネガティブプロンプト、または空>\n"
                "[/VIDEO_PROMPT_UPDATE]"
            )

            # 画像がある場合は Vision で渡す
            # ローカルモデル (transformers) は PIL 形式、API は base64 URL 形式
            import base64 as _b64
            if img_path and Path(img_path).exists():
                try:
                    if model_manager.is_loaded():
                        from PIL import Image as _PILImage
                        pil_img = _PILImage.open(img_path).convert("RGB")
                        messages = [{"role": "user", "content": [
                            {"type": "image", "image": pil_img},
                            {"type": "text", "text": text_content},
                        ]}]
                    else:
                        with open(img_path, "rb") as f:
                            img_b64 = _b64.b64encode(f.read()).decode()
                        ext = Path(img_path).suffix.lower().lstrip(".")
                        mime = f"image/{ext}" if ext in ("png", "jpg", "jpeg", "webp") else "image/jpeg"
                        messages = [{"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                            {"type": "text", "text": text_content},
                        ]}]
                except Exception:
                    messages = [{"role": "user", "content": text_content}]
            else:
                messages = [{"role": "user", "content": text_content}]

            yield gr.update(), gr.update(), "LLM応答中..."
            full_response = ""
            try:
                for chunk in _llm_chat_stream(messages, proj):
                    full_response += chunk
            except Exception as e:
                yield gr.update(), gr.update(), f"LLMエラー: {e}"
                return

            parsed = _extract_video_prompt_update(full_response)
            if parsed:
                new_prompt, new_neg = parsed
                _placeholders = {"(空)", "（空）", "(empty)", "empty", "none", "(none)"}
                if not new_neg or new_neg.lower() in _placeholders:
                    new_neg = vid_n or ""
                yield new_prompt, new_neg, "動画プロンプトを更新しました"
                return

            yield gr.update(), gr.update(), "動画プロンプトを更新できませんでした（フォーマット不一致）"

        gen_vid_consult_btn.click(
            fn=on_gen_vid_consult,
            inputs=[gen_vid_extra_input, project_state, gen_image_preview, gen_img_prompt, gen_vid_prompt, gen_vid_neg],
            outputs=[gen_vid_prompt, gen_vid_neg, gen_status_disp],
        )

        def _get_comfyui(proj: Project) -> ComfyUIClient:
            return ComfyUIClient(base_url=proj.comfyui_url)

        def on_regen(idx, state, plot, img_p, img_n, vid_p, vid_n, img_seed, vid_seed, img_wf, vid_wf, target="both", video_quality="preview"):
            proj = _project_from_state(state)
            if proj is None:
                return None, None, None, "プロジェクトが読み込まれていません", gr.update(), gr.update(choices=[], value=None), None
            comfyui = _get_comfyui(proj)
            if not comfyui.is_available():
                return None, None, None, f"ComfyUIに接続できません: {proj.comfyui_url}", gr.update(), gr.update(), gr.update()

            scene = proj.scenes[idx]
            scene.plot = plot
            scene.image_prompt = img_p
            scene.image_negative = img_n
            scene.video_prompt = vid_p
            scene.video_negative = vid_n
            scene.image_seed = int(img_seed)
            scene.video_seed = int(vid_seed)
            scene.image_workflow = img_wf or None
            scene.video_workflow = vid_wf or None
            proj.save_scene(scene)

            gen = BatchGenerator(proj, comfyui)
            try:
                gen.regenerate_scene(scene.scene_id, target=target, video_quality=video_quality)
            except Exception as e:
                return None, None, None, f"生成エラー: {e}", gr.update(), gr.update(), gr.update()

            updated = proj.scenes[idx]
            scene_dir = proj.scene_dir(updated.scene_id)
            img = updated.image_path(scene_dir)
            preview_vid = updated.video_preview_path(scene_dir)
            final_vid = updated.video_final_path(scene_dir)
            img_hist_update, img_hist_preview = _image_history_ui_updates(updated, scene_dir, updated.active_image_version)
            vid_preview_hist_update, vid_preview_hist_player = _video_history_ui_updates(updated, scene_dir, "preview", updated.active_video_preview_version)
            vid_final_hist_update, vid_final_hist_player = _video_history_ui_updates(updated, scene_dir, "final", updated.active_video_final_version)
            samples = _build_scene_samples(proj.scenes)
            status_msg = updated.status
            if video_quality == "final":
                if final_vid.exists():
                    status_msg += " (最終版あり)"
            return (
                str(img) if img.exists() else None,
                str(preview_vid) if preview_vid.exists() else None,
                str(final_vid) if final_vid.exists() else None,
                status_msg,
                gr.Dataset(samples=samples),
                img_hist_update,
                img_hist_preview,
                vid_preview_hist_update,
                vid_preview_hist_player,
                vid_final_hist_update,
                vid_final_hist_player,
            )

        _regen_inputs = [
            current_scene_idx, project_state,
            gen_plot,
            gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
            gen_img_seed, gen_vid_seed, gen_img_wf, gen_vid_wf,
        ]
        _regen_outputs = [
            gen_image_preview, gen_video_preview, gen_video_final_preview, gen_status_disp, gen_scene_btns,
            gen_img_history, gen_img_history_preview,
            gen_vid_preview_history, gen_vid_preview_history_player,
            gen_vid_final_history, gen_vid_final_history_player,
        ]
        gen_regen_img_btn.click(
            fn=lambda *a: on_regen(*a, target="image"),
            inputs=_regen_inputs,
            outputs=_regen_outputs,
        )
        gen_regen_vid_btn.click(
            fn=lambda *a: on_regen(*a, target="video", video_quality="preview"),
            inputs=_regen_inputs,
            outputs=_regen_outputs,
        )
        gen_regen_vid_final_btn.click(
            fn=lambda *a: on_regen(*a, target="video", video_quality="final"),
            inputs=_regen_inputs,
            outputs=_regen_outputs,
        )

        def _refresh_scene_status_from_files(scene: Scene, scene_dir: Path) -> None:
            """ファイル実体に合わせて scene.status を更新する。"""
            if scene.video_preview_path(scene_dir).exists():
                scene.status = "video_done"
            elif scene.image_path(scene_dir).exists():
                scene.status = "image_done"
            elif (scene.plot or "").strip():
                scene.status = "plot_done"
            else:
                scene.status = "empty"

        def on_delete_media(idx, state, media_type):
            proj = _project_from_state(state)
            if proj is None:
                return None, None, None, "プロジェクトが読み込まれていません", gr.update(), gr.update(choices=[], value=None), None

            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)

            media_map = {
                "image": scene.image_path(scene_dir),
                "preview": scene.video_preview_path(scene_dir),
                "final": scene.video_final_path(scene_dir),
            }
            target_path = media_map[media_type]

            if target_path.exists():
                target_path.unlink()
                deleted_msg = f"{target_path.name} を削除しました"
            else:
                deleted_msg = f"{target_path.name} は存在しません"
            if media_type == "image":
                scene.active_image_version = ""
            elif media_type == "preview":
                scene.active_video_preview_version = ""
            elif media_type == "final":
                scene.active_video_final_version = ""

            _refresh_scene_status_from_files(scene, scene_dir)
            proj.save_scene(scene)
            img_hist_update, img_hist_preview = _image_history_ui_updates(scene, scene_dir, scene.active_image_version)
            vid_preview_hist_update, vid_preview_hist_player = _video_history_ui_updates(scene, scene_dir, "preview", scene.active_video_preview_version)
            vid_final_hist_update, vid_final_hist_player = _video_history_ui_updates(scene, scene_dir, "final", scene.active_video_final_version)
            samples = _build_scene_samples(proj.scenes)
            return (
                str(scene.image_path(scene_dir)) if scene.image_path(scene_dir).exists() else None,
                str(scene.video_preview_path(scene_dir)) if scene.video_preview_path(scene_dir).exists() else None,
                str(scene.video_final_path(scene_dir)) if scene.video_final_path(scene_dir).exists() else None,
                deleted_msg,
                gr.Dataset(samples=samples),
                img_hist_update,
                img_hist_preview,
                vid_preview_hist_update,
                vid_preview_hist_player,
                vid_final_hist_update,
                vid_final_hist_player,
            )

        _delete_media_outputs = [
            gen_image_preview, gen_video_preview, gen_video_final_preview, gen_status_disp, gen_scene_btns,
            gen_img_history, gen_img_history_preview,
            gen_vid_preview_history, gen_vid_preview_history_player,
            gen_vid_final_history, gen_vid_final_history_player,
        ]
        gen_delete_image_btn.click(
            fn=lambda idx, st: on_delete_media(idx, st, "image"),
            inputs=[current_scene_idx, project_state],
            outputs=_delete_media_outputs,
        )
        gen_delete_preview_btn.click(
            fn=lambda idx, st: on_delete_media(idx, st, "preview"),
            inputs=[current_scene_idx, project_state],
            outputs=_delete_media_outputs,
        )
        gen_delete_final_btn.click(
            fn=lambda idx, st: on_delete_media(idx, st, "final"),
            inputs=[current_scene_idx, project_state],
            outputs=_delete_media_outputs,
        )

        def on_scene_reset(idx, state):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return tuple([gr.update()] * len(gen_scene_outputs)) + (gr.update(),)

            idx = max(0, min(idx, len(proj.scenes) - 1))
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            if scene_dir.exists():
                shutil.rmtree(scene_dir)

            # Scene identity/timing/order は維持し、編集・生成データを初期化
            scene.enabled = True
            scene.section = ""
            scene.plot = ""
            scene.image_prompt = ""
            scene.image_negative = ""
            scene.image_seed = -1
            scene.image_workflow = None
            scene.active_image_version = ""
            scene.active_video_preview_version = ""
            scene.active_video_final_version = ""
            scene.video_prompt = ""
            scene.video_negative = ""
            scene.video_seed = -1
            scene.video_workflow = None
            scene.video_instruction = ""
            scene.status = "empty"
            scene.notes = ""
            proj.save_scene(scene)

            refreshed = list(load_gen_scene(idx, state))
            refreshed[17] = "シーンを初期化しました（関連ファイルも削除）"
            return tuple(refreshed) + (gr.Dataset(samples=_build_scene_samples(proj.scenes)),)

        _scene_reset_outputs = gen_scene_outputs + [gen_scene_btns]
        gen_reset_scene_from_image_btn.click(
            fn=on_scene_reset,
            inputs=[current_scene_idx, project_state],
            outputs=_scene_reset_outputs,
        )
        gen_reset_scene_from_preview_btn.click(
            fn=on_scene_reset,
            inputs=[current_scene_idx, project_state],
            outputs=_scene_reset_outputs,
        )
        gen_reset_scene_from_final_btn.click(
            fn=on_scene_reset,
            inputs=[current_scene_idx, project_state],
            outputs=_scene_reset_outputs,
        )

        def on_saved_image_select(idx, state, selected_name):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            selected_path = _selected_version_path(scene, scene_dir, selected_name)
            return str(selected_path) if selected_path else None

        def on_saved_image_refresh(idx, state):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return gr.update(choices=[], value=None), None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            if _ensure_scene_image_history(scene, scene_dir):
                proj.save_scene(scene)
            return _image_history_ui_updates(scene, scene_dir, scene.active_image_version)

        def on_saved_image_use(idx, state, selected_name):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return None, "プロジェクトが読み込まれていません", gr.update(), gr.update(choices=[], value=None), None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            selected_path = _selected_version_path(scene, scene_dir, selected_name)
            if selected_path is None:
                return gr.update(), "選択中の画像が見つかりません", gr.update(), gr.update(), gr.update()

            shutil.copy2(selected_path, scene.image_path(scene_dir))
            scene.active_image_version = selected_path.name
            _refresh_scene_status_from_files(scene, scene_dir)
            proj.save_scene(scene)
            samples = _build_scene_samples(proj.scenes)
            history_update, history_preview = _image_history_ui_updates(scene, scene_dir, scene.active_image_version)
            return (
                str(scene.image_path(scene_dir)),
                f"{selected_path.name} を本番画像に設定しました",
                gr.Dataset(samples=samples),
                history_update,
                history_preview,
            )

        def on_saved_image_delete(idx, state, selected_name):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return None, "プロジェクトが読み込まれていません", gr.update(), gr.update(choices=[], value=None), None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            selected_path = _selected_version_path(scene, scene_dir, selected_name)
            if selected_path is None:
                return gr.update(), "削除対象の画像が見つかりません", gr.update(), gr.update(), gr.update()

            was_active = selected_path.name == scene.active_image_version
            selected_path.unlink()

            remaining = _list_scene_image_versions(scene, scene_dir)
            if was_active:
                if remaining:
                    scene.active_image_version = remaining[0].name
                    shutil.copy2(remaining[0], scene.image_path(scene_dir))
                else:
                    scene.active_image_version = ""
                    active_path = scene.image_path(scene_dir)
                    if active_path.exists():
                        active_path.unlink()

            _refresh_scene_status_from_files(scene, scene_dir)
            proj.save_scene(scene)
            samples = _build_scene_samples(proj.scenes)
            history_update, history_preview = _image_history_ui_updates(scene, scene_dir, scene.active_image_version)
            active_img = scene.image_path(scene_dir)
            return (
                str(active_img) if active_img.exists() else None,
                f"{selected_path.name} を削除しました",
                gr.Dataset(samples=samples),
                history_update,
                history_preview,
            )

        gen_img_history.change(
            fn=on_saved_image_select,
            inputs=[current_scene_idx, project_state, gen_img_history],
            outputs=[gen_img_history_preview],
        )
        gen_img_history_refresh_btn.click(
            fn=on_saved_image_refresh,
            inputs=[current_scene_idx, project_state],
            outputs=[gen_img_history, gen_img_history_preview],
        )
        gen_img_use_saved_btn.click(
            fn=on_saved_image_use,
            inputs=[current_scene_idx, project_state, gen_img_history],
            outputs=[gen_image_preview, gen_status_disp, gen_scene_btns, gen_img_history, gen_img_history_preview],
        )
        gen_img_delete_saved_btn.click(
            fn=on_saved_image_delete,
            inputs=[current_scene_idx, project_state, gen_img_history],
            outputs=[gen_image_preview, gen_status_disp, gen_scene_btns, gen_img_history, gen_img_history_preview],
        )

        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 動画履歴操作
        # ============================================================

        def on_saved_video_select(idx, state, selected_name):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            selected_path = _selected_video_version_path(scene, scene_dir, selected_name)
            return str(selected_path) if selected_path else None

        def on_saved_video_refresh(idx, state, quality):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return gr.update(choices=[], value=None), None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            if _ensure_scene_video_history(scene, scene_dir, quality):
                proj.save_scene(scene)
            active = scene.active_video_final_version if quality == "final" else scene.active_video_preview_version
            return _video_history_ui_updates(scene, scene_dir, quality, active)

        def on_saved_video_use(idx, state, selected_name, quality):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return None, "プロジェクトが読み込まれていません", gr.update(), gr.update(choices=[], value=None), None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            selected_path = _selected_video_version_path(scene, scene_dir, selected_name)
            if selected_path is None:
                return gr.update(), "選択中の動画が見つかりません", gr.update(), gr.update(), gr.update()
            dest = scene.video_final_path(scene_dir) if quality == "final" else scene.video_preview_path(scene_dir)
            shutil.copy2(selected_path, dest)
            if quality == "final":
                scene.active_video_final_version = selected_path.name
            else:
                scene.active_video_preview_version = selected_path.name
            _refresh_scene_status_from_files(scene, scene_dir)
            proj.save_scene(scene)
            samples = _build_scene_samples(proj.scenes)
            active = scene.active_video_final_version if quality == "final" else scene.active_video_preview_version
            hist_update, hist_player = _video_history_ui_updates(scene, scene_dir, quality, active)
            vid_path = dest
            return (
                str(vid_path) if vid_path.exists() else None,
                f"{selected_path.name} を本番動画に設定しました",
                gr.Dataset(samples=samples),
                hist_update,
                hist_player,
            )

        def on_saved_video_delete(idx, state, selected_name, quality):
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return None, "プロジェクトが読み込まれていません", gr.update(), gr.update(choices=[], value=None), None
            scene = proj.scenes[idx]
            scene_dir = proj.scene_dir(scene.scene_id)
            selected_path = _selected_video_version_path(scene, scene_dir, selected_name)
            if selected_path is None:
                return gr.update(), "削除対象の動画が見つかりません", gr.update(), gr.update(), gr.update()
            active_attr = "active_video_final_version" if quality == "final" else "active_video_preview_version"
            dest = scene.video_final_path(scene_dir) if quality == "final" else scene.video_preview_path(scene_dir)
            was_active = selected_path.name == getattr(scene, active_attr)
            selected_path.unlink()
            remaining = _list_scene_video_versions(scene, scene_dir, quality)
            if was_active:
                if remaining:
                    setattr(scene, active_attr, remaining[0].name)
                    shutil.copy2(remaining[0], dest)
                else:
                    setattr(scene, active_attr, "")
                    if dest.exists():
                        dest.unlink()
            _refresh_scene_status_from_files(scene, scene_dir)
            proj.save_scene(scene)
            samples = _build_scene_samples(proj.scenes)
            active = getattr(scene, active_attr)
            hist_update, hist_player = _video_history_ui_updates(scene, scene_dir, quality, active)
            return (
                str(dest) if dest.exists() else None,
                f"{selected_path.name} を削除しました",
                gr.Dataset(samples=samples),
                hist_update,
                hist_player,
            )

        # プレビュー動画履歴
        gen_vid_preview_history.change(
            fn=on_saved_video_select,
            inputs=[current_scene_idx, project_state, gen_vid_preview_history],
            outputs=[gen_vid_preview_history_player],
        )
        gen_vid_preview_history_refresh_btn.click(
            fn=lambda idx, st: on_saved_video_refresh(idx, st, "preview"),
            inputs=[current_scene_idx, project_state],
            outputs=[gen_vid_preview_history, gen_vid_preview_history_player],
        )
        gen_vid_preview_use_btn.click(
            fn=lambda idx, st, name: on_saved_video_use(idx, st, name, "preview"),
            inputs=[current_scene_idx, project_state, gen_vid_preview_history],
            outputs=[gen_video_preview, gen_status_disp, gen_scene_btns, gen_vid_preview_history, gen_vid_preview_history_player],
        )
        gen_vid_preview_delete_saved_btn.click(
            fn=lambda idx, st, name: on_saved_video_delete(idx, st, name, "preview"),
            inputs=[current_scene_idx, project_state, gen_vid_preview_history],
            outputs=[gen_video_preview, gen_status_disp, gen_scene_btns, gen_vid_preview_history, gen_vid_preview_history_player],
        )

        # 最終版動画履歴
        gen_vid_final_history.change(
            fn=on_saved_video_select,
            inputs=[current_scene_idx, project_state, gen_vid_final_history],
            outputs=[gen_vid_final_history_player],
        )
        gen_vid_final_history_refresh_btn.click(
            fn=lambda idx, st: on_saved_video_refresh(idx, st, "final"),
            inputs=[current_scene_idx, project_state],
            outputs=[gen_vid_final_history, gen_vid_final_history_player],
        )
        gen_vid_final_use_btn.click(
            fn=lambda idx, st, name: on_saved_video_use(idx, st, name, "final"),
            inputs=[current_scene_idx, project_state, gen_vid_final_history],
            outputs=[gen_video_final_preview, gen_status_disp, gen_scene_btns, gen_vid_final_history, gen_vid_final_history_player],
        )
        gen_vid_final_delete_saved_btn.click(
            fn=lambda idx, st, name: on_saved_video_delete(idx, st, name, "final"),
            inputs=[current_scene_idx, project_state, gen_vid_final_history],
            outputs=[gen_video_final_preview, gen_status_disp, gen_scene_btns, gen_vid_final_history, gen_vid_final_history_player],
        )

        # ============================================================
        # イベントハンドラ: 生成・編集タブ - シーン管理（移動・挿入・削除）
        # ============================================================

        delete_confirm_state = gr.State(False)

        def _scene_manage_result(proj, new_idx, status_msg):
            """Build common outputs for scene management handlers."""
            if proj.scenes:
                new_idx = max(0, min(new_idx, len(proj.scenes) - 1))
                base = _scene_to_gen_values(proj.scenes[new_idx], proj)
                # Keep the same order as load_gen_scene()
                vals = base[:19] + (new_idx, []) + base[19:]
                # index 17 is gen_status_disp
                vals = vals[:17] + (status_msg,) + vals[18:]
            else:
                _no_vid_hist = gr.update(choices=[], value=None)
                vals = (
                    None, "", "", None, None, None, "", "", "", "",
                    -1, -1, "", "",
                    gr.update(choices=[], value=None), None,
                    "", status_msg, True, 0, [],
                    _no_vid_hist, None, _no_vid_hist, None,
                )
            samples = _build_scene_samples(proj.scenes)
            return vals + (gr.Dataset(samples=samples), False)

        def on_scene_move_up(idx, state):
            proj = _project_from_state(state)
            if proj is None:
                return (gr.update(),) * len(gen_scene_outputs) + (gr.update(), False)
            moved = proj.move_scene_up(idx)
            new_idx = idx - 1 if moved else idx
            msg = "上に移動しました" if moved else "先頭のため移動できません"
            return _scene_manage_result(proj, new_idx, msg)

        def on_scene_move_down(idx, state):
            proj = _project_from_state(state)
            if proj is None:
                return (gr.update(),) * len(gen_scene_outputs) + (gr.update(), False)
            moved = proj.move_scene_down(idx)
            new_idx = idx + 1 if moved else idx
            msg = "下に移動しました" if moved else "末尾のため移動できません"
            return _scene_manage_result(proj, new_idx, msg)

        def on_scene_insert(idx, state):
            proj = _project_from_state(state)
            if proj is None:
                return (gr.update(),) * len(gen_scene_outputs) + (gr.update(), False)
            proj.insert_scene_after(idx)
            return _scene_manage_result(proj, idx + 1, "新しいシーンを挿入しました")

        def on_scene_delete(idx, state, confirm):
            proj = _project_from_state(state)
            if proj is None:
                return (gr.update(),) * len(gen_scene_outputs) + (gr.update(), False)
            if not confirm:
                # 1回目クリック: 警告表示のみ、確認待ち状態へ
                no_op = [gr.update()] * len(gen_scene_outputs)
                no_op[17] = "⚠️ 削除確認: もう一度「削除」を押すと削除します"
                return tuple(no_op) + (gr.update(), True)
            # 2回目クリック: 実際に削除
            proj.delete_scene(idx)
            new_idx = min(idx, len(proj.scenes) - 1) if proj.scenes else 0
            return _scene_manage_result(proj, new_idx, "シーンを削除しました")

        _scene_manage_outputs = gen_scene_outputs + [gen_scene_btns, delete_confirm_state]

        gen_move_up_btn.click(
            fn=on_scene_move_up,
            inputs=[current_scene_idx, project_state],
            outputs=_scene_manage_outputs,
        )
        gen_move_down_btn.click(
            fn=on_scene_move_down,
            inputs=[current_scene_idx, project_state],
            outputs=_scene_manage_outputs,
        )
        gen_insert_btn.click(
            fn=on_scene_insert,
            inputs=[current_scene_idx, project_state],
            outputs=_scene_manage_outputs,
        )
        gen_delete_btn.click(
            fn=on_scene_delete,
            inputs=[current_scene_idx, project_state, delete_confirm_state],
            outputs=_scene_manage_outputs,
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 一括生成
        # ============================================================

        def _start_batch(state, target: str, video_quality: str = "preview"):
            global _batch_gen, _batch_log, _batch_started_at, _batch_finished_at
            global _batch_current_task, _batch_mode_label, _batch_run_id, _batch_stop_requested
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません"

            is_prompt_mode = target in ("image_prompt", "video_prompt")

            # 画像/動画の実生成前はローカルLLMを開放してVRAMを節約
            if not is_prompt_mode:
                try:
                    if model_manager.is_loaded():
                        model_manager.unload_model()
                except Exception:
                    pass

            comfyui = None
            if not is_prompt_mode:
                comfyui = _get_comfyui(proj)
                if not comfyui.is_available():
                    return f"ComfyUIに接続できません: {proj.comfyui_url}"

            with _batch_lock:
                if _batch_started_at is not None and _batch_finished_at is None:
                    return "一括生成がすでに実行中です。完了または停止後に再実行してください"

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

                _batch_gen = None if is_prompt_mode else BatchGenerator(proj, comfyui)

            with _batch_lock:
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
                def _run_prompt_batch():
                    total = len(proj.scenes)
                    _s = settings_manager.load(proj.project_dir)
                    common_prompt = _s.get("batch_image_prompt_common", "")
                    common_video_instruction = _s.get("batch_video_prompt_common_instruction", "")
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
                                new_prompt, new_neg = _generate_video_prompt_for_scene(
                                    scene,
                                    proj,
                                    common_instruction=common_video_instruction,
                                )
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
            return f"{_batch_mode_label}を開始しました"

        def on_batch_image_start(state):
            return _start_batch(state, target="image")

        def on_batch_image_prompt_start(state):
            return _start_batch(state, target="image_prompt")

        def on_batch_video_prompt_start(state):
            return _start_batch(state, target="video_prompt")

        def on_batch_preview_start(state):
            return _start_batch(state, target="video", video_quality="preview")

        def on_batch_stop():
            global _batch_current_task, _batch_stop_requested
            with _batch_lock:
                is_running = _batch_started_at is not None and _batch_finished_at is None
            if not is_running:
                return "実行中の一括生成はありません"
            if _batch_gen:
                _batch_gen.stop()
            with _batch_lock:
                _batch_stop_requested = True
                _batch_current_task = "停止要求を送信しました"
            return "停止要求を送信しました"

        def on_batch_progress_poll():
            with _batch_lock:
                if _batch_started_at is None:
                    return "待機中..."
                now = time.monotonic()
                elapsed = _format_elapsed(now - _batch_started_at)
                if _batch_finished_at is not None:
                    total = _format_elapsed(_batch_finished_at - _batch_started_at)
                    state_label = "完了"
                elif _batch_started_at is not None and _batch_finished_at is None:
                    total = "-"
                    state_label = "実行中"
                else:
                    total = "-"
                    state_label = "停止"

                lines = [
                    f"状態: {state_label}",
                    f"モード: {_batch_mode_label or '-'}",
                    f"現在処理: {_batch_current_task or '-'}",
                    f"経過時間: {elapsed}",
                    f"最終合計時間: {total}",
                    "",
                    "---- ログ ----",
                ]
                logs = _batch_log[-8:] if _batch_log else ["(ログなし)"]
                lines.extend(logs)
                return "\n".join(lines)

        def on_batch_final_start(state):
            return _start_batch(state, target="video", video_quality="final")

        gen_batch_img_prompt_btn.click(fn=on_batch_image_prompt_start, inputs=[project_state], outputs=[gen_progress])
        gen_batch_img_btn.click(fn=on_batch_image_start, inputs=[project_state], outputs=[gen_progress])
        gen_batch_vid_prompt_btn.click(fn=on_batch_video_prompt_start, inputs=[project_state], outputs=[gen_progress])
        gen_batch_preview_btn.click(fn=on_batch_preview_start, inputs=[project_state], outputs=[gen_progress])
        gen_stop_btn.click(fn=on_batch_stop, outputs=[gen_progress])
        gen_batch_final_btn.click(fn=on_batch_final_start, inputs=[project_state], outputs=[gen_progress])
        gr.Timer(value=1.0, active=True).tick(
            fn=on_batch_progress_poll,
            outputs=[gen_progress],
        )


        # ============================================================
        # イベントハンドラ: 書き出しタブ
        # ============================================================

        def on_export_refresh(state):
            proj = _project_from_state(state)
            if proj is None:
                return []
            exporter = VideoExporter(proj)
            thumbs = exporter.get_scene_thumbnails()
            return [str(p) for _, p in thumbs if p is not None]

        export_refresh_btn.click(
            fn=on_export_refresh,
            inputs=[project_state],
            outputs=[export_gallery],
        )

        def on_export(
            state,
            quality_label,
            with_music,
            loop_music,
            audio_fade_in,
            audio_fade_in_sec,
            audio_fade_out,
            audio_fade_out_sec,
            video_fade_out_black,
            video_fade_out_sec,
        ):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません", None
            try:
                video_quality = "final" if "最終版" in quality_label else "preview"
                settings_manager.save(proj.project_dir, {
                    "export_quality": quality_label,
                    "export_with_music": bool(with_music),
                    "export_loop_music": bool(loop_music),
                    "export_audio_fade_in": bool(audio_fade_in),
                    "export_audio_fade_in_sec": float(audio_fade_in_sec or 0),
                    "export_audio_fade_out": bool(audio_fade_out),
                    "export_audio_fade_out_sec": float(audio_fade_out_sec or 0),
                    "export_video_fade_out_black": bool(video_fade_out_black),
                    "export_video_fade_out_sec": float(video_fade_out_sec or 0),
                })
                exporter = VideoExporter(proj)
                out_path = exporter.export(
                    with_music=with_music,
                    loop_music=bool(loop_music),
                    video_quality=video_quality,
                    audio_fade_in=bool(audio_fade_in),
                    audio_fade_in_seconds=float(audio_fade_in_sec or 0),
                    audio_fade_out=bool(audio_fade_out),
                    audio_fade_out_seconds=float(audio_fade_out_sec or 0),
                    video_fade_out_black=bool(video_fade_out_black),
                    video_fade_out_seconds=float(video_fade_out_sec or 0),
                )
                return f"書き出し完了: {out_path}", str(out_path)
            except Exception as e:
                return f"書き出しエラー: {e}", None

        export_btn.click(
            fn=on_export,
            inputs=[
                project_state, export_quality, export_with_music, export_loop_music,
                export_audio_fade_in, export_audio_fade_in_sec,
                export_audio_fade_out, export_audio_fade_out_sec,
                export_video_fade_out_black, export_video_fade_out_sec,
            ],
            outputs=[export_status, export_video],
        )


        # ============================================================
        # イベントハンドラ: モデル管理タブ
        # ============================================================

        def on_model_load(model_label: str, state: dict):
            """選択したモデルをロードし、settings.json にモデル選択を保存する。"""
            model_id = model_manager.MODEL_PRESETS.get(model_label, model_label)
            try:
                msg = model_manager.load_model(model_id)
            except RuntimeError as e:
                return str(e), ""
            # プロジェクトが開いていれば settings.json にモデル選択を保存
            proj = _project_from_state(state)
            if proj:
                settings_manager.save(proj.project_dir, {"model": model_label})
            vram = model_manager.get_vram_info()
            return msg, vram

        def on_model_unload():
            """モデルをアンロードして VRAM を解放する。"""
            msg = model_manager.unload_model()
            vram = model_manager.get_vram_info()
            return msg, vram

        def on_model_vram_refresh():
            """VRAM 情報を更新する。"""
            loaded = model_manager.get_loaded_model_id()
            status = f"ロード済み: {loaded}" if loaded else "未ロード"
            return status, model_manager.get_vram_info()

        model_load_btn.click(
            fn=on_model_load,
            inputs=[model_dropdown, project_state],
            outputs=[model_status, model_vram],
        )
        model_unload_btn.click(
            fn=on_model_unload,
            outputs=[model_status, model_vram],
        )

        # 起動時の自動ロード完了を検知するポーリングタイマー（2秒ごと）
        gr.Timer(value=2.0, active=True).tick(
            fn=on_model_vram_refresh,
            outputs=[model_status, model_vram],
        )

    return demo


# ============================================================
# エントリポイント
# ============================================================

if __name__ == "__main__":
    app = build_app()
    app.queue()
    app.launch(share=False, server_name="0.0.0.0", inbrowser=True)
