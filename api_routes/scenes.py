"""シーン管理 API ルーター。

エンドポイント:
  GET    /api/projects/{name}/scenes              - シーン一覧
  GET    /api/projects/{name}/scenes/{id}         - シーン取得
  PUT    /api/projects/{name}/scenes/{id}         - シーン保存
  POST   /api/projects/{name}/scenes/{id}/move    - シーン並び替え
  POST   /api/projects/{name}/scenes/{id}/insert-after - シーン挿入
  DELETE /api/projects/{name}/scenes/{id}         - シーン削除
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.project import Project
from ._shared import BASE_DIR

router = APIRouter()


# ---- ユーティリティ ----

def _load_proj(name: str) -> Project:
    proj_dir = BASE_DIR / name
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
    try:
        return Project.load(proj_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _find_scene_idx(proj: Project, scene_id: int) -> int:
    for i, s in enumerate(proj.scenes):
        if s.scene_id == scene_id:
            return i
    raise HTTPException(status_code=404, detail=f"Scene {scene_id} not found")


# ---- エンドポイント ----

@router.get("/projects/{name}/scenes")
def get_scenes(name: str) -> dict:
    """プロジェクトのシーン一覧を返す。"""
    proj = _load_proj(name)
    return {"scenes": [s.to_dict() for s in proj.scenes]}


@router.get("/projects/{name}/scenes/{scene_id}")
def get_scene(name: str, scene_id: int) -> dict:
    """指定シーンのデータを返す。"""
    proj = _load_proj(name)
    _find_scene_idx(proj, scene_id)
    scene = proj.scenes[_find_scene_idx(proj, scene_id)]
    return scene.to_dict()


class SceneSaveBody(BaseModel):
    plot: Optional[str] = None
    section: Optional[str] = None
    lyrics: Optional[str] = None
    enabled: Optional[bool] = None
    image_prompt: Optional[str] = None
    image_negative: Optional[str] = None
    image_seed: Optional[int] = None
    image_workflow: Optional[str] = None
    video_prompt: Optional[str] = None
    video_negative: Optional[str] = None
    video_seed: Optional[int] = None
    video_workflow: Optional[str] = None
    video_instruction: Optional[str] = None
    notes: Optional[str] = None


@router.put("/projects/{name}/scenes/{scene_id}")
def save_scene(name: str, scene_id: int, body: SceneSaveBody) -> dict:
    """シーンデータを保存する。"""
    proj = _load_proj(name)
    idx = _find_scene_idx(proj, scene_id)
    scene = proj.scenes[idx]

    if body.plot is not None:
        scene.plot = body.plot
        if scene.status == "empty" and body.plot.strip():
            scene.status = "plot_done"
    if body.section is not None:
        scene.section = body.section
    if body.lyrics is not None:
        scene.lyrics = body.lyrics
    if body.enabled is not None:
        scene.enabled = body.enabled
    if body.image_prompt is not None:
        scene.image_prompt = body.image_prompt
    if body.image_negative is not None:
        scene.image_negative = body.image_negative
    if body.image_seed is not None:
        scene.image_seed = body.image_seed
    if body.image_workflow is not None:
        scene.image_workflow = body.image_workflow if body.image_workflow else None
    if body.video_prompt is not None:
        scene.video_prompt = body.video_prompt
    if body.video_negative is not None:
        scene.video_negative = body.video_negative
    if body.video_seed is not None:
        scene.video_seed = body.video_seed
    if body.video_workflow is not None:
        scene.video_workflow = body.video_workflow if body.video_workflow else None
    if body.video_instruction is not None:
        scene.video_instruction = body.video_instruction
    if body.notes is not None:
        scene.notes = body.notes

    proj.save_scene(scene)
    return scene.to_dict()


class SceneMoveBody(BaseModel):
    direction: str  # "up" | "down"


@router.post("/projects/{name}/scenes/{scene_id}/move")
def move_scene(name: str, scene_id: int, body: SceneMoveBody) -> dict:
    """シーンを上または下に移動する。"""
    proj = _load_proj(name)
    idx = _find_scene_idx(proj, scene_id)

    if body.direction == "up":
        ok = proj.move_scene_up(idx)
    elif body.direction == "down":
        ok = proj.move_scene_down(idx)
    else:
        raise HTTPException(status_code=400, detail="direction must be 'up' or 'down'")

    if not ok:
        raise HTTPException(status_code=400, detail="Cannot move scene in that direction")

    proj.save()
    # 移動後の新しいインデックスを返す
    new_idx = _find_scene_idx(proj, scene_id)
    return {
        "scenes": [s.to_dict() for s in proj.scenes],
        "new_index": new_idx,
    }


@router.post("/projects/{name}/scenes/{scene_id}/insert-after")
def insert_scene_after(name: str, scene_id: int) -> dict:
    """指定シーンの後に新しいシーンを挿入する。"""
    proj = _load_proj(name)
    idx = _find_scene_idx(proj, scene_id)

    new_scene = proj.insert_scene_after(idx)
    proj.save()
    proj.save_scene(new_scene)

    return {
        "scenes": [s.to_dict() for s in proj.scenes],
        "new_scene_id": new_scene.scene_id,
    }


@router.delete("/projects/{name}/scenes/{scene_id}")
def delete_scene(name: str, scene_id: int) -> dict:
    """シーンを削除する（シーンディレクトリも削除）。"""
    proj = _load_proj(name)
    idx = _find_scene_idx(proj, scene_id)

    # シーンディレクトリを削除
    import shutil
    scene_dir = proj.scene_dir(scene_id)
    if scene_dir.exists():
        shutil.rmtree(scene_dir)

    proj.delete_scene(idx)
    proj.save()

    return {"scenes": [s.to_dict() for s in proj.scenes]}


class SceneMoveToBody(BaseModel):
    target_index: int  # 0始まりのインデックス


@router.post("/projects/{name}/scenes/{scene_id}/move-to")
def move_scene_to(name: str, scene_id: int, body: SceneMoveToBody) -> dict:
    """シーンを指定インデックスへ移動する（up/downを繰り返して実現）。"""
    proj = _load_proj(name)
    current_idx = _find_scene_idx(proj, scene_id)
    target_idx = max(0, min(body.target_index, len(proj.scenes) - 1))

    while current_idx < target_idx:
        proj.move_scene_down(current_idx)
        current_idx += 1
    while current_idx > target_idx:
        proj.move_scene_up(current_idx)
        current_idx -= 1

    proj.save()
    return {"scenes": [s.to_dict() for s in proj.scenes], "new_index": current_idx}


class SceneBulkSaveBody(BaseModel):
    """一括保存: [(scene_id, section, plot), ...]"""
    rows: list[list]  # [[scene_id, section, plot], ...]
    concept: Optional[str] = None


@router.post("/projects/{name}/scenes/bulk-save")
def bulk_save_scenes(name: str, body: SceneBulkSaveBody) -> dict:
    """計画タブのDataframe一括保存。section / plot のみ更新する。"""
    proj = _load_proj(name)

    if body.concept is not None:
        proj.concept = body.concept

    updated = 0
    for row in body.rows:
        if len(row) < 3:
            continue
        try:
            sid = int(row[0])
        except (ValueError, TypeError):
            continue
        section = str(row[1]) if row[1] is not None else ""
        plot = str(row[2]) if row[2] is not None else ""
        for scene in proj.scenes:
            if scene.scene_id == sid:
                scene.section = section
                scene.plot = plot
                if scene.status == "empty" and plot.strip():
                    scene.status = "plot_done"
                proj.save_scene(scene)
                updated += 1
                break

    proj.save()
    return {"updated": updated, "scenes": [s.to_dict() for s in proj.scenes]}
