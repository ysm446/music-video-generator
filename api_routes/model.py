"""モデル管理タブ用ルーター。

エンドポイント:
  GET    /api/model/status  - ロード状態 + VRAM情報
  GET    /api/model/presets - モデルプリセット一覧
  POST   /api/model/load    - モデルロード（ブロッキング処理を asyncio.to_thread でラップ）
  DELETE /api/model         - モデルアンロード
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import model_manager

router = APIRouter()


@router.get("/model/status")
def get_model_status():
    """現在のモデルロード状態と VRAM 情報を返す。"""
    loaded_id = model_manager.get_loaded_model_id()
    vram = model_manager.get_vram_info()
    return {
        "is_loaded": model_manager.is_loaded(),
        "loaded_model_id": loaded_id,
        "vram_info": vram,
    }


@router.get("/model/presets")
def get_model_presets():
    """利用可能なモデルプリセット一覧を返す。"""
    return {"presets": model_manager.MODEL_PRESETS}


class LoadModelRequest(BaseModel):
    model_label: str  # MODEL_PRESETS のキー


@router.post("/model/load")
async def load_model(body: LoadModelRequest):
    """指定モデルをロードする。数分かかるため asyncio.to_thread でブロッキングを回避。"""
    model_id = model_manager.MODEL_PRESETS.get(body.model_label)
    if model_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"不明なモデルラベル: {body.model_label}。有効なラベル: {list(model_manager.MODEL_PRESETS.keys())}",
        )
    try:
        msg = await asyncio.to_thread(model_manager.load_model, model_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    vram = model_manager.get_vram_info()
    return {"message": msg, "vram_info": vram}


@router.delete("/model")
def unload_model():
    """ロード済みモデルをアンロードして VRAM を解放する。"""
    msg = model_manager.unload_model()
    vram = model_manager.get_vram_info()
    return {"message": msg, "vram_info": vram}
