"""
model_manager.py
Qwen3-VL ローカル推論クライアント（transformers ライブラリ経由）。
prompt-assistant の qwen_client.py をベースに MV Generator 用に調整。
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Generator, Optional

import torch
from transformers import AutoModelForImageTextToText, AutoProcessor, TextIteratorStreamer

try:
    from qwen_vl_utils import process_vision_info as _process_vision_info
    _HAS_QWEN_UTILS = True
except ImportError:
    _HAS_QWEN_UTILS = False

# ---- モデルプリセット（prompt-assistant と同一リスト） ----

MODEL_PRESETS: dict[str, str] = {
    "qwen3-vl-2b (軽量)": "Qwen/Qwen3-VL-2B-Instruct",
    "qwen3-vl-4b (推奨)": "Qwen/Qwen3-VL-4B-Instruct",
    "qwen3-vl-8b (高性能)": "Qwen/Qwen3-VL-8B-Instruct",
    "huihui-qwen3-vl-2b-abliterated": "huihui-ai/Huihui-Qwen3-VL-2B-Instruct-abliterated",
    "huihui-qwen3-vl-4b-abliterated": "huihui-ai/Huihui-Qwen3-VL-4B-Instruct-abliterated",
    "huihui-qwen3-vl-8b-abliterated": "huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated",
}

_model = None
_processor = None
_loaded_model_id: Optional[str] = None


# ============================================================
# モデル管理
# ============================================================

def load_model(model_id: str) -> str:
    """モデルをロードする。既に同じモデルがロード済みの場合はスキップ。

    Args:
        model_id: HuggingFace モデルID

    Returns:
        ステータスメッセージ
    """
    global _model, _processor, _loaded_model_id

    if _loaded_model_id == model_id and _model is not None:
        return f"モデル {model_id} は既にロード済みです。"

    try:
        _model = AutoModelForImageTextToText.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            device_map="auto",
        )
        _processor = AutoProcessor.from_pretrained(model_id)
        _loaded_model_id = model_id
        return f"モデル {model_id} のロードが完了しました。"
    except Exception as e:
        _model = None
        _processor = None
        _loaded_model_id = None
        raise RuntimeError(f"モデルのロードに失敗しました: {e}") from e


def unload_model() -> str:
    """モデルをアンロードして VRAM を解放する。"""
    global _model, _processor, _loaded_model_id
    if _model is None:
        return "モデルはロードされていません。"
    _model = None
    _processor = None
    _loaded_model_id = None
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return "Qwen3-VL モデルをアンロードしました。"


def is_loaded() -> bool:
    """モデルがロード済みかどうかを返す。"""
    return _model is not None and _processor is not None


def get_loaded_model_id() -> Optional[str]:
    """現在ロード中のモデルIDを返す。"""
    return _loaded_model_id


def get_vram_info() -> str:
    """GPU VRAM の使用状況を返す。"""
    if not torch.cuda.is_available():
        return "GPU なし（CPU モード）"
    used = torch.cuda.memory_allocated() / 1024 ** 3
    total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
    return f"{used:.1f} GB / {total:.1f} GB 使用中"


# ============================================================
# 内部ヘルパー
# ============================================================

def _apply_template(messages: list[dict]) -> str:
    """チャットテンプレートを適用してテキストを生成する。"""
    try:
        return _processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,  # Qwen3 系の thinking モードを無効化
        )
    except TypeError:
        return _processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )


def _build_inputs(messages: list[dict]) -> dict:
    """メッセージからモデル入力テンソルを組み立てる。"""
    text = _apply_template(messages)

    if _HAS_QWEN_UTILS:
        image_inputs, video_inputs = _process_vision_info(messages)
    else:
        image_inputs, video_inputs = None, None

    return _processor(
        text=[text],
        images=image_inputs if image_inputs else None,
        videos=video_inputs if video_inputs else None,
        padding=True,
        return_tensors="pt",
    ).to(_model.device)


def _stream_generate(inputs: dict, max_new_tokens: int = 512) -> Generator[str, None, None]:
    """ストリーミングでトークンを yield する。"""
    streamer = TextIteratorStreamer(_processor, skip_prompt=True, skip_special_tokens=True)

    def _gen():
        with torch.inference_mode():
            _model.generate(**inputs, max_new_tokens=max_new_tokens, streamer=streamer)

    thread = threading.Thread(target=_gen)
    thread.start()
    try:
        for token in streamer:
            yield token
    finally:
        thread.join()
        del inputs
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


# ============================================================
# MV Generator 用チャット関数
# ============================================================

_MV_SYSTEM_PROMPT = (
    "あなたはミュージックビデオのディレクターです。"
    "ユーザーの楽曲コンセプトをもとに、映像表現について提案・相談に応じてください。"
)


def chat_stream(
    messages: list[dict],
    max_new_tokens: int = 2048,
) -> Generator[str, None, None]:
    """汎用チャット（ストリーミング）。

    Args:
        messages: [{"role": "user"|"assistant", "content": str}, ...]
        max_new_tokens: 最大生成トークン数

    Yields:
        テキストチャンク
    """
    if not is_loaded():
        raise RuntimeError("モデルがロードされていません。モデル管理タブからロードしてください。")

    # 呼び出し元がシステムメッセージを渡した場合はそれを使用し、なければデフォルトを先頭に追加
    if messages and messages[0].get("role") == "system":
        full_messages = messages
    else:
        full_messages = [{"role": "system", "content": _MV_SYSTEM_PROMPT}] + messages
    inputs = _build_inputs(full_messages)
    yield from _stream_generate(inputs, max_new_tokens)


def chat(
    messages: list[dict],
    max_new_tokens: int = 2048,
) -> str:
    """汎用チャット（非ストリーミング）。"""
    return "".join(chat_stream(messages, max_new_tokens))


# ============================================================
# 全シーン一括プロンプト生成
# ============================================================

_BULK_SYSTEM = (
    "あなたはミュージックビデオのディレクターです。"
    "楽曲のコンセプトをもとに、各シーンの映像プロンプトを JSON 形式で生成してください。"
    "section, plot は日本語で記述してください。"
    "image_prompt と video_prompt は英語で記述してください。"
)

_BULK_USER_TEMPLATE = """\
コンセプト: {concept}

シーン数: {scene_count}（1シーン {scene_duration} 秒）
scene_id は {start_id} 〜 {end_id} を使用すること。

以下の JSON 配列形式で指定した scene_id 範囲のプロンプトを出力してください。
各要素は scene_id, section, plot,
image_prompt, image_negative, video_prompt, video_negative を含めること。
```json
[ ... ]
```"""


def generate_all_scene_prompts(
    concept: str,
    scene_count: int,
    scene_duration: int,
    start_scene_id: int = 1,
    reference_images: Optional[list[Path]] = None,
) -> list[dict]:
    """全シーンのプロンプトを一括生成する。

    Returns:
        [{"scene_id": 1, "section": ..., "plot": ..., ...}, ...]
    """
    if not is_loaded():
        raise RuntimeError("モデルがロードされていません。モデル管理タブからロードしてください。")

    user_content: list[dict] = []

    # 参照画像（最大4枚）
    if reference_images:
        for img_path in reference_images[:4]:
            try:
                from PIL import Image as PILImage
                img = PILImage.open(str(img_path)).convert("RGB")
                user_content.append({"type": "image", "image": img})
            except Exception:
                pass

    user_content.append({
        "type": "text",
        "text": _BULK_USER_TEMPLATE.format(
            concept=concept,
            scene_count=scene_count,
            scene_duration=scene_duration,
            start_id=start_scene_id,
            end_id=start_scene_id + scene_count - 1,
        ),
    })

    messages = [
        {"role": "system", "content": _BULK_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    inputs = _build_inputs(messages)
    response = "".join(_stream_generate(inputs, max_new_tokens=3000))
    return _extract_json_list(response)


# ============================================================
# 個別シーンのプロンプト改善
# ============================================================

_IMPROVE_SYSTEM = (
    "あなたはミュージックビデオのプロンプトエンジニアです。"
    "シーン情報をもとに、より良い画像／動画プロンプトを提案してください。"
    "image_prompt と video_prompt は英語で記述してください。"
)


def improve_scene_prompt(
    scene_data: dict,
    concept: str,
    reference_images: Optional[list[Path]] = None,
) -> dict:
    """単一シーンのプロンプトを改善する。

    Returns:
        改善後のフィールドを含む辞書
    """
    if not is_loaded():
        raise RuntimeError("モデルがロードされていません。モデル管理タブからロードしてください。")

    user_content: list[dict] = []

    if reference_images:
        for img_path in reference_images[:2]:
            try:
                from PIL import Image as PILImage
                img = PILImage.open(str(img_path)).convert("RGB")
                user_content.append({"type": "image", "image": img})
            except Exception:
                pass

    user_content.append({
        "type": "text",
        "text": (
            f"全体コンセプト: {concept}\n\n"
            f"シーン情報:\n{json.dumps(scene_data, ensure_ascii=False, indent=2)}\n\n"
            "改善したプロンプトを JSON 形式で出力してください。"
            "plot, image_prompt, image_negative, video_prompt, video_negative を含めること。\n"
            "```json\n{ ... }\n```"
        ),
    })

    messages = [
        {"role": "system", "content": _IMPROVE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    inputs = _build_inputs(messages)
    response = "".join(_stream_generate(inputs, max_new_tokens=1024))
    return _extract_json_dict(response)


# ============================================================
# JSON 抽出ユーティリティ
# ============================================================

def _extract_json_list(text: str) -> list[dict]:
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    raw = match.group(1) if match else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _extract_json_dict(text: str) -> dict:
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    raw = match.group(1) if match else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
