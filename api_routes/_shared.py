"""api_routes 共通ユーティリティ。

api.py で設定した BASE_DIR / _APP_DIR / _WORKFLOWS_DIR を各ルーターから
参照するためのシングルトン。循環インポートを避けるため、api.py からではなく
このモジュールを経由して参照する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

_APP_DIR = Path(__file__).parent.parent.resolve()
CONFIG_PATH = _APP_DIR / "config.yaml"
_WORKFLOWS_DIR = _APP_DIR / "workflows"


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_cfg = _load_config()
_base_dir_cfg = _cfg.get("project", {}).get("base_dir", "projects")
BASE_DIR: Path = (
    Path(_base_dir_cfg)
    if Path(_base_dir_cfg).is_absolute()
    else _APP_DIR / _base_dir_cfg
)


def list_image_workflows() -> list[str]:
    """workflows/image/ 内の JSON ファイル一覧をアプリルートからの相対パスで返す。"""
    folder = _WORKFLOWS_DIR / "image"
    if not folder.exists():
        return []
    return sorted(
        str(p.relative_to(_APP_DIR)).replace("\\", "/")
        for p in folder.glob("*.json")
    )


def list_video_workflows() -> list[str]:
    """workflows/video/ 内の JSON ファイル一覧をアプリルートからの相対パスで返す。"""
    folder = _WORKFLOWS_DIR / "video"
    if not folder.exists():
        return []
    return sorted(
        str(p.relative_to(_APP_DIR)).replace("\\", "/")
        for p in folder.glob("*.json")
    )
