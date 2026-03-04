"""ファイルサービングルーター。

/api/files/{path} でプロジェクトディレクトリ内の任意ファイルを配信する。
パストラバーサル攻撃を防ぐため、BASE_DIR 外のパスはすべて 403 を返す。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ._shared import BASE_DIR

router = APIRouter()


@router.get("/files/{path:path}")
def serve_file(path: str) -> FileResponse:
    """BASE_DIR 内のファイルを返す。"""
    try:
        full = (BASE_DIR / path).resolve()
        # パストラバーサル保護: BASE_DIR 配下であることを確認
        full.relative_to(BASE_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not full.exists() or not full.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    mime, _ = mimetypes.guess_type(str(full))
    return FileResponse(
        str(full),
        media_type=mime or "application/octet-stream",
        headers={"Cache-Control": "no-cache"},
    )
