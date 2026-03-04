"""MV Generator - FastAPI メインアプリケーション。

Gradio の app.py を置き換える薄い API 層。
ビジネスロジックはすべて src/ モジュールに委譲する。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Windows でシステムエラーメッセージの文字化けを防ぐ
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---- パス設定 ----
# __file__ 基準の絶対パスにすることで、uvicorn 実行時に
# CWD が変わっても正しいパスを指せるようにする。
_APP_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = _APP_DIR / "config.yaml"
_WORKFLOWS_DIR = _APP_DIR / "workflows"


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_cfg = _load_config()
_base_dir_cfg = _cfg.get("project", {}).get("base_dir", "projects")
BASE_DIR = (
    Path(_base_dir_cfg)
    if Path(_base_dir_cfg).is_absolute()
    else _APP_DIR / _base_dir_cfg
)

# ---- settings_manager の相対パス問題を修正 ----
# settings_manager._ROOT_SETTINGS_PATH はデフォルトで相対パス "settings.json" を
# 使用するため、uvicorn 実行時に CWD が変わると壊れる。アプリルート基準に上書き。
import src.settings_manager as _sm  # noqa: E402

_sm._ROOT_SETTINGS_PATH = _APP_DIR / "settings.json"

# ---- asyncio 例外ハンドラ（クライアント切断の WinError 10054 等を抑制）----
def _asyncio_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    exc = context.get("exception")
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return
    loop.default_exception_handler(context)

asyncio.get_event_loop().set_exception_handler(_asyncio_exception_handler)

# ---- FastAPI アプリ ----
app = FastAPI(title="MV Generator API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- ルーターを登録 ----
from api_routes.projects import router as projects_router  # noqa: E402
from api_routes.scenes import router as scenes_router  # noqa: E402
from api_routes.llm import router as llm_router  # noqa: E402
from api_routes.generation import router as generation_router  # noqa: E402
from api_routes.export import router as export_router  # noqa: E402
from api_routes.model import router as model_router  # noqa: E402
from api_routes.files import router as files_router  # noqa: E402

app.include_router(projects_router, prefix="/api")
app.include_router(scenes_router, prefix="/api")
app.include_router(llm_router, prefix="/api")
app.include_router(generation_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(model_router, prefix="/api")
app.include_router(files_router, prefix="/api")

# ---- React ビルド成果物を配信 ----
# フェーズ2以降のルーターが登録された後でマウントする必要がある。
# /api/* は上記ルーターが処理するので、残りのパスを React の index.html で受ける。
_UI_DIST = _APP_DIR / "ui" / "dist"
if _UI_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_UI_DIST), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="MV Generator API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    uvicorn.run(
        "api:app",
        host=args.host,
        port=args.port,
        reload=False,
    )
