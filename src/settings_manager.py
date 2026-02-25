"""
settings_manager.py
プロジェクトごとの UI パラメータを settings.json に保存・読み込みするモジュール。
ルートの settings.json には最後に開いたプロジェクト名も保存する。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# ---- デフォルト値 ----

DEFAULT_SETTINGS: dict = {
    "comfyui_url": "http://localhost:8188",
    "llm_url": "http://localhost:11434/v1",
    "image_workflow": "workflows/image/zimage_turbo.json",
    "video_workflow": "workflows/video/wan22_i2v.json",
    "image_resolution_w": 1280,
    "image_resolution_h": 720,
    "video_resolution_w": 640,
    "video_resolution_h": 480,
    "video_final_resolution_w": 1280,
    "video_final_resolution_h": 720,
    "video_fps": 16,
    "video_frame_count": 81,
    "scene_duration": 5,
    "model": "qwen3-vl-4b (推奨)",
    "export_quality": "プレビュー (640×360)",
    "export_with_music": True,
    "export_audio_fade_in": False,
    "export_audio_fade_in_sec": 1.0,
    "export_audio_fade_out": False,
    "export_audio_fade_out_sec": 1.0,
    "export_video_fade_out_black": False,
    "export_video_fade_out_sec": 1.0,
}

# アプリルートのグローバル設定ファイル（最後に開いたプロジェクトを記憶）
_ROOT_SETTINGS_PATH = Path("settings.json")


# ============================================================
# プロジェクトごとの設定
# ============================================================

def load(project_dir: Path) -> dict:
    """project_dir/settings.json を読み込み、デフォルト値とマージして返す。

    ファイルが存在しない場合はデフォルト値を返す。
    """
    path = project_dir / "settings.json"
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {**DEFAULT_SETTINGS, **data}


def save(project_dir: Path, data: dict) -> None:
    """project_dir/settings.json に設定を保存する。

    既存ファイルがある場合はマージして上書きする。
    """
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "settings.json"

    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    merged = {**existing, **data}
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# ルート設定（最後に開いたプロジェクト）
# ============================================================

def load_root() -> dict:
    """ルートの settings.json を読み込む。"""
    if _ROOT_SETTINGS_PATH.exists():
        try:
            return json.loads(_ROOT_SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_last_project(project_name: str) -> None:
    """最後に開いたプロジェクト名をルートの settings.json に保存する。"""
    data = load_root()
    data["last_project"] = project_name
    _ROOT_SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_last_project() -> Optional[str]:
    """最後に開いたプロジェクト名を返す。なければ None。"""
    return load_root().get("last_project")
