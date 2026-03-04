"""書き出しタブ用ルーター。

エンドポイント:
  POST /api/projects/{name}/export         - 動画書き出し（SSE進捗）
  GET  /api/projects/{name}/export/thumbnails - シーンサムネイル一覧
  GET  /api/projects/{name}/export/output  - 書き出し済み動画一覧
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.project import Project
from src.video_export import VideoExporter
from src import settings_manager

from ._shared import BASE_DIR

router = APIRouter()


def _load_project(name: str) -> Project:
    proj_dir = BASE_DIR / name
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail=f"プロジェクトが見つかりません: {name}")
    return Project.load(proj_dir)


# ---- サムネイル ----

@router.get("/projects/{name}/export/thumbnails")
def get_thumbnails(name: str):
    """書き出しタブのシーンサムネイル（画像URL）を返す。"""
    proj = _load_project(name)
    exporter = VideoExporter(proj)
    thumbs = exporter.get_scene_thumbnails()
    result = []
    for scene_id, path in thumbs:
        url = None
        if path is not None and path.exists() and path.stat().st_size > 0:
            rel = path.relative_to(BASE_DIR)
            mtime = int(path.stat().st_mtime)
            url = f"/api/files/{rel.as_posix()}?v={mtime}"
        result.append({"scene_id": scene_id, "url": url})
    return {"thumbnails": result}


# ---- 書き出し済み動画一覧 ----

@router.get("/projects/{name}/export/outputs")
def get_outputs(name: str):
    """書き出し済みの動画ファイルURLを返す。"""
    proj = _load_project(name)
    output_dir = proj.output_dir
    outputs = []
    if output_dir.exists():
        for f in sorted(output_dir.glob("*.mp4"), reverse=True):
            rel = f.relative_to(BASE_DIR)
            st = f.stat()
            outputs.append({
                "filename": f.name,
                "url": f"/api/files/{rel.as_posix()}?v={int(st.st_mtime)}",
                "size": st.st_size,
            })
    return {"outputs": outputs}


# ---- 書き出し（SSE） ----

class ExportRequest(BaseModel):
    output_kind: str = "preview"          # "preview" / "final"
    with_music: bool = True
    loop_music: bool = False
    audio_fade_in: bool = False
    audio_fade_in_sec: float = 1.0
    audio_fade_out: bool = False
    audio_fade_out_sec: float = 1.0
    video_fade_out_black: bool = False
    video_fade_out_sec: float = 1.0


@router.post("/projects/{name}/export")
async def export_video(name: str, body: ExportRequest) -> StreamingResponse:
    """動画書き出しを実行し、SSEで進捗を返す。"""
    proj = _load_project(name)

    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _run():
        try:
            # 設定を保存
            settings_manager.save(proj.project_dir, {
                "export_with_music": body.with_music,
                "export_loop_music": body.loop_music,
                "export_audio_fade_in": body.audio_fade_in,
                "export_audio_fade_in_sec": body.audio_fade_in_sec,
                "export_audio_fade_out": body.audio_fade_out,
                "export_audio_fade_out_sec": body.audio_fade_out_sec,
                "export_video_fade_out_black": body.video_fade_out_black,
                "export_video_fade_out_sec": body.video_fade_out_sec,
            })

            is_final = body.output_kind == "final"
            output_filename = "final.mp4" if is_final else "preview.mp4"
            label = "最終版" if is_final else "プレビュー版"

            loop.call_soon_threadsafe(
                queue.put_nowait,
                json.dumps({"type": "progress", "message": f"{label}書き出しを開始..."}),
            )

            exporter = VideoExporter(proj)
            out_path = exporter.export(
                output_filename=output_filename,
                with_music=body.with_music,
                loop_music=body.loop_music,
                video_quality="final" if is_final else "preview",
                audio_fade_in=body.audio_fade_in,
                audio_fade_in_seconds=body.audio_fade_in_sec,
                audio_fade_out=body.audio_fade_out,
                audio_fade_out_seconds=body.audio_fade_out_sec,
                video_fade_out_black=body.video_fade_out_black,
                video_fade_out_seconds=body.video_fade_out_sec,
            )
            rel = out_path.relative_to(BASE_DIR)
            mtime = int(out_path.stat().st_mtime)
            url = f"/api/files/{rel.as_posix()}?v={mtime}"
            loop.call_soon_threadsafe(
                queue.put_nowait,
                json.dumps({"type": "done", "message": f"{label}書き出し完了: {out_path.name}", "url": url}),
            )
        except Exception as e:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                json.dumps({"type": "error", "message": f"書き出しエラー: {e}"}),
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_run, daemon=True).start()

    async def _event_gen():
        while True:
            item = await queue.get()
            if item is None:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            yield f"data: {item}\n\n"

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
