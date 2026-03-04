"""LLM連携 API ルーター（SSEストリーミング対応）。

エンドポイント:
  POST /api/llm/chat-stream                               - コンセプト相談チャット (SSE)
  POST /api/projects/{name}/llm/generate-all-prompts      - 全シーン一括提案 (SSE)
  POST /api/projects/{name}/scenes/{id}/llm/improve       - 個別シーン改善
  POST /api/projects/{name}/scenes/{id}/llm/image-prompt  - プロットから画像プロンプト生成
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from pathlib import Path
from typing import Generator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.project import Project
from src.scene import Scene
from src.llm_client import LLMClient
from src import model_manager
from src import settings_manager
from ._shared import BASE_DIR, _cfg

router = APIRouter()


# ---- ユーティリティ ----

def _load_proj(name: str) -> Project:
    proj_dir = BASE_DIR / name
    if not proj_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{name}' not found")
    return Project.load(proj_dir)


def _auto_load_model() -> None:
    """モデルが未ロードの場合、前回使用したモデルを自動ロードする。"""
    # 1) ルート設定に保存された前回のモデルラベル
    label = settings_manager.load_root().get("last_model_label", "")
    if label and label in model_manager.MODEL_PRESETS:
        model_id = model_manager.MODEL_PRESETS[label]
    else:
        # 2) フォールバック: "推奨" を含むプリセット、なければ先頭
        model_id = next(
            (v for k, v in model_manager.MODEL_PRESETS.items() if "推奨" in k),
            next(iter(model_manager.MODEL_PRESETS.values())),
        )
    model_manager.load_model(model_id)


def _llm_chat_stream(messages: list[dict], proj: Optional[Project]) -> Generator[str, None, None]:
    """ローカルモデルでストリーミングチャットを返すジェネレータ。未ロード時は自動ロードする。"""
    if not model_manager.is_loaded():
        _auto_load_model()
    yield from model_manager.chat_stream(messages)


def _llm_chat(messages: list[dict], proj: Optional[Project]) -> str:
    return "".join(_llm_chat_stream(messages, proj))


def _build_plan_chat_system(proj: Optional[Project]) -> str:
    """計画タブのLLMチャット用システムプロンプトを構築する（app.py から移植）。"""
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


async def _streaming_response(sync_gen: Generator[str, None, None]) -> StreamingResponse:
    """同期ジェネレータを SSE StreamingResponse に変換する。

    スレッドブリッジパターン: 同期ジェネレータをバックグラウンドスレッドで実行し、
    asyncio.Queue 経由でイベントループに渡す。
    """
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _producer():
        try:
            for chunk in sync_gen:
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as exc:
            loop.call_soon_threadsafe(
                queue.put_nowait, f"\x00ERROR\x00{exc}"  # エラーシグナル
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)  # 終端シグナル

    threading.Thread(target=_producer, daemon=True).start()

    async def _event_gen():
        while True:
            item = await queue.get()
            if item is None:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            if isinstance(item, str) and item.startswith("\x00ERROR\x00"):
                err_msg = item[7:]
                yield f"data: {json.dumps({'error': err_msg})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            yield f"data: {json.dumps({'chunk': item})}\n\n"

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# エンドポイント
# ============================================================

class ChatStreamBody(BaseModel):
    messages: list[dict]
    project_name: Optional[str] = None


@router.post("/llm/chat-stream")
async def chat_stream(body: ChatStreamBody) -> StreamingResponse:
    """コンセプト相談チャット（SSE）。

    messagesはOpenAI形式の会話履歴。システムプロンプトはサーバー側で付与する。
    """
    proj: Optional[Project] = None
    if body.project_name:
        try:
            proj = _load_proj(body.project_name)
        except Exception:
            pass

    system_prompt = _build_plan_chat_system(proj)
    messages = [{"role": "system", "content": system_prompt}] + list(body.messages)

    def _gen():
        yield from _llm_chat_stream(messages, proj)

    return await _streaming_response(_gen())


class GenerateAllPromptsBody(BaseModel):
    concept: str
    missing_only: bool = True  # Trueのとき plot が空のシーンのみ提案


@router.post("/projects/{name}/llm/generate-all-prompts")
async def generate_all_prompts(name: str, body: GenerateAllPromptsBody) -> StreamingResponse:
    """全シーン一括提案（SSE）。

    SSEイベント種別:
      {"type": "progress", "message": str}
      {"type": "scene_done", "scene_id": int, "section": str, "plot": str}
      {"done": true}
    """
    proj = _load_proj(name)
    if not body.concept:
        raise HTTPException(status_code=400, detail="コンセプトが空です")

    if body.missing_only:
        target_indices = [i for i, s in enumerate(proj.scenes) if not (s.plot or "").strip()]
    else:
        target_indices = list(range(len(proj.scenes)))

    if not target_indices:
        raise HTTPException(status_code=400, detail="未入力プロットのシーンはありません")

    # 提案結果キャッシュ（前後コンテキスト用）
    proposed: dict[int, tuple[str, str]] = {}
    total = len(target_indices)

    def _propose_one(proj_: Project, concept: str, scene_idx: int, proposed_: dict) -> tuple[str, str]:
        """1シーン分の section / plot を生成する（app.py の _llm_propose_missing_scene と同等）。"""
        scene = proj_.scenes[scene_idx]

        def _ctx_text() -> str:
            lines: list[str] = []
            for offset in (-2, -1, 1, 2):
                idx = scene_idx + offset
                if idx < 0 or idx >= len(proj_.scenes):
                    continue
                s = proj_.scenes[idx]
                sec = (proposed_.get(s.scene_id, ("", ""))[0] or s.section or "").strip()
                pl = (proposed_.get(s.scene_id, ("", ""))[1] or s.plot or "").strip()
                lines.append(
                    f"- scene_id={s.scene_id}, time={s.start_time:.1f}-{s.end_time:.1f}, "
                    f"section={sec or '(なし)'}, plot={pl or '(未入力)'}"
                )
            return "\n".join(lines) if lines else "- (前後シーンなし)"

        user_text = (
            "あなたはMVのシーン構成プランナーです。\n"
            "指定した1シーンについて、全体コンセプトを最優先に section と plot を提案してください。\n"
            "前後シーンとの関連性は保ちつつ、内容が似すぎないように差別化してください。\n"
            "出力は必ずJSONオブジェクトのみ。\n\n"
            f"全体コンセプト:\n{concept}\n\n"
            f"対象シーン:\n"
            f"- scene_id={scene.scene_id}\n"
            f"- time={scene.start_time:.1f}-{scene.end_time:.1f}\n"
            f"- 既存section={(scene.section or '(なし)').strip()}\n"
            "- 既存plot=(未入力)\n\n"
            f"前後シーン情報:\n{_ctx_text()}\n\n"
            '出力形式:\n{"section":"...","plot":"..."}'
        )
        raw = _llm_chat([{"role": "user", "content": user_text}], proj_)
        # JSON抽出
        stripped = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        m = re.search(r"```json\s*([\s\S]+?)\s*```", stripped, flags=re.IGNORECASE)
        raw_json = m.group(1) if m else stripped
        try:
            data = json.loads(raw_json)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        section = str(data.get("section", "") or "").strip()
        plot = str(data.get("plot", "") or "").strip()
        if not plot:
            plot = re.sub(r"\s+", " ", stripped).strip()[:200]
        return section, plot

    def _run_all():
        for pos, scene_idx in enumerate(target_indices, start=1):
            scene = proj.scenes[scene_idx]
            yield json.dumps({"type": "progress", "message": f"シーン {scene.scene_id} を処理中 ({pos}/{total})..."})
            try:
                section, plot = _propose_one(proj, body.concept, scene_idx, proposed)
                proposed[scene.scene_id] = (section, plot)
                yield json.dumps({"type": "scene_done", "scene_id": scene.scene_id, "section": section, "plot": plot})
            except Exception as exc:
                yield json.dumps({"type": "error", "scene_id": scene.scene_id, "message": str(exc)})

    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _producer():
        try:
            for payload in _run_all():
                loop.call_soon_threadsafe(queue.put_nowait, payload)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_producer, daemon=True).start()

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


class ImprovePromptBody(BaseModel):
    concept: str


@router.post("/projects/{name}/scenes/{scene_id}/llm/improve")
def improve_scene_prompt(name: str, scene_id: int, body: ImprovePromptBody) -> dict:
    """個別シーンのプロンプトを改善する（同期）。"""
    proj = _load_proj(name)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {scene_id} not found")

    scene_data = scene.to_dict()
    try:
        if not model_manager.is_loaded():
            _auto_load_model()
        result = model_manager.improve_scene_prompt(
            scene_data=scene_data,
            concept=body.concept,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return result or {}


# ============================================================
# モジュールレベルヘルパー（generation.py からもインポートされる）
# ============================================================

def _extract_image_prompt_text(raw: str) -> str:
    stripped = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    block = re.search(r"\[IMAGE_PROMPT\](.*?)\[/IMAGE_PROMPT\]", stripped, re.DOTALL | re.IGNORECASE)
    return block.group(1).strip() if block else stripped


def _parse_prompt_update(raw: str) -> tuple[str, str] | None:
    """[PROMPT_UPDATE] ブロックから (positive, negative) を抽出する。"""
    stripped = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    block = re.search(r"\[PROMPT_UPDATE\](.*?)\[/PROMPT_UPDATE\]", stripped, re.DOTALL | re.IGNORECASE)
    if not block:
        return None
    body = block.group(1)
    pos_m = re.search(r"^\s*Positive:\s*(.*?)(?=\n\s*Negative:|\Z)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    neg_m = re.search(r"^\s*Negative:\s*(.*)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    if not pos_m:
        return None
    pos = pos_m.group(1).strip()
    neg = neg_m.group(1).strip() if neg_m else ""
    neg = re.sub(r"\[/?PROMPT_UPDATE\].*", "", neg, flags=re.DOTALL).strip()
    return pos, neg


def _extract_video_prompt_update(raw: str) -> tuple[str, str] | None:
    """[VIDEO_PROMPT_UPDATE] ブロックから (prompt, negative) を抽出する。"""
    stripped = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    block = re.search(r"\[VIDEO_PROMPT_UPDATE\](.*?)\[/VIDEO_PROMPT_UPDATE\]", stripped, re.DOTALL | re.IGNORECASE)
    if not block:
        return None
    body = block.group(1)
    p_m = re.search(r"^\s*Prompt:\s*(.*?)(?=\n\s*Negative:|\Z)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    n_m = re.search(r"^\s*Negative:\s*(.*)", body, re.DOTALL | re.IGNORECASE | re.MULTILINE)
    if not p_m:
        return None
    prompt = p_m.group(1).strip()
    neg = n_m.group(1).strip() if n_m else ""
    neg = re.sub(r"\[/?VIDEO_PROMPT_UPDATE\].*", "", neg, flags=re.DOTALL).strip()
    return prompt, neg


def _generate_image_prompt_from_plot(plot: str, common_prompt: str, proj: Optional[Project]) -> str:
    """プロットから画像プロンプトを生成する（generation.py バッチからも使用）。"""
    common = (common_prompt or "").strip()
    user_text = (
        "Create one concise English image-generation positive prompt from the scene description.\n"
        "Do not output explanation.\n"
        "If common prompt exists, include it naturally.\n\n"
        f"Scene description:\n{plot or '(empty)'}\n\n"
        f"Common prompt:\n{common if common else '(none)'}\n\n"
        "Output format:\n"
        "[IMAGE_PROMPT]\n"
        "<one-line prompt>\n"
        "[/IMAGE_PROMPT]"
    )
    full = _llm_chat([{"role": "user", "content": user_text}], proj)
    prompt = _extract_image_prompt_text(full)
    return prompt or (common if common else "cinematic still")


def _generate_video_prompt_for_scene(
    scene: Scene,
    proj: Optional[Project],
    common_instruction: str = "",
) -> tuple[str, str]:
    """動画プロンプト / ネガティブを生成する（generation.py バッチからも使用）。"""
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
    full = _llm_chat([{"role": "user", "content": text_content}], proj)
    parsed = _extract_video_prompt_update(full)
    if parsed:
        return parsed
    cleaned = re.sub(r"<think>.*?</think>", "", full, flags=re.DOTALL).strip()
    return cleaned or "Scene: cinematic scene, Action: subtle natural motion, Camera: slow cinematic pan", ""


class GenImagePromptBody(BaseModel):
    plot: str
    common_prompt: str = ""


@router.post("/projects/{name}/scenes/{scene_id}/llm/image-prompt")
def generate_image_prompt(name: str, scene_id: int, body: GenImagePromptBody) -> dict:
    """プロットから画像プロンプトを生成する（同期）。"""
    proj = _load_proj(name)

    common = (body.common_prompt or "").strip()
    common_line = common if common else "(none)"
    user_text = (
        "Create one concise English image-generation positive prompt from the scene description.\n"
        "Do not output explanation.\n"
        "If common prompt exists, include it naturally.\n\n"
        f"Scene description:\n{body.plot or '(empty)'}\n\n"
        f"Common prompt:\n{common_line}\n\n"
        "Output format:\n"
        "[IMAGE_PROMPT]\n"
        "<one-line prompt>\n"
        "[/IMAGE_PROMPT]"
    )

    try:
        full = _llm_chat([{"role": "user", "content": user_text}], proj)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # [IMAGE_PROMPT] ブロックを抽出
    stripped = re.sub(r"<think>.*?</think>", "", full, flags=re.DOTALL).strip()
    block = re.search(r"\[IMAGE_PROMPT\](.*?)\[/IMAGE_PROMPT\]", stripped, re.DOTALL | re.IGNORECASE)
    prompt = block.group(1).strip() if block else stripped
    if not prompt:
        prompt = common if common else "cinematic still"

    return {"image_prompt": prompt}


class ImageChatStreamBody(BaseModel):
    messages: list[dict]
    image_prompt: str = ""
    image_negative: str = ""


@router.post("/projects/{name}/scenes/{scene_id}/llm/image-chat-stream")
async def image_chat_stream(name: str, scene_id: int, body: ImageChatStreamBody) -> StreamingResponse:
    """画像プロンプト編集チャット（SSE）。

    最後のユーザーメッセージをコンテキスト付きで LLM に渡し、
    [PROMPT_UPDATE] ブロックをパースして prompt_update イベントを送出する。
    """
    proj = _load_proj(name)
    user_msgs = [m for m in body.messages if m.get("role") == "user"]
    if not user_msgs:
        raise HTTPException(status_code=400, detail="ユーザーメッセージがありません")

    latest_user = user_msgs[-1]["content"]
    llm_user_content = (
        "これは画像生成プロンプトの編集タスクです。創作的な解釈は不要です。\n\n"
        "【現在のプロンプト】\n"
        f"Positive: {body.image_prompt or ''}\n"
        f"Negative: {body.image_negative or ''}\n\n"
        f"【編集指示】{latest_user}\n\n"
        "指示された部分だけを変更し、それ以外は一切変えないでください。\n"
        "以下のフォーマットのみで回答してください（説明・コメント禁止）:\n"
        "[PROMPT_UPDATE]\n"
        "Positive: <更新後のpositiveプロンプト>\n"
        "Negative: <更新後のnegativeプロンプト、または空>\n"
        "[/PROMPT_UPDATE]"
    )
    messages = [{"role": "user", "content": llm_user_content}]

    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    loop = asyncio.get_event_loop()
    parts: list[str] = []

    def _producer():
        try:
            for chunk in _llm_chat_stream(messages, proj):
                parts.append(chunk)
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, f"\x00ERROR\x00{exc}")
        finally:
            raw = "".join(parts)
            upd = _parse_prompt_update(raw)
            if upd:
                pos, neg = upd
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    f"\x00PROMPT_UPDATE\x00{json.dumps({'positive': pos, 'negative': neg})}",
                )
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_producer, daemon=True).start()

    async def _event_gen():
        while True:
            item = await queue.get()
            if item is None:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            if item.startswith("\x00ERROR\x00"):
                yield f"data: {json.dumps({'error': item[7:]})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            if item.startswith("\x00PROMPT_UPDATE\x00"):
                data = json.loads(item[len("\x00PROMPT_UPDATE\x00"):])
                yield f"data: {json.dumps({'prompt_update': data})}\n\n"
                continue
            yield f"data: {json.dumps({'chunk': item})}\n\n"

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class VideoPromptBody(BaseModel):
    video_instruction: str = ""
    common_instruction: str = ""


@router.post("/projects/{name}/scenes/{scene_id}/llm/video-prompt-stream")
async def video_prompt_stream(name: str, scene_id: int, body: VideoPromptBody) -> StreamingResponse:
    """動画プロンプト生成（SSE）。完了時に video_prompt_update イベントを送出する。"""
    proj = _load_proj(name)
    scene = next((s for s in proj.scenes if s.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {scene_id} not found")

    # video_instruction を一時的に上書き
    if body.video_instruction:
        scene.video_instruction = body.video_instruction

    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    loop = asyncio.get_event_loop()
    parts: list[str] = []

    def _producer():
        try:
            prompt, neg = _generate_video_prompt_for_scene(scene, proj, body.common_instruction)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                f"\x00VIDEO_PROMPT\x00{json.dumps({'video_prompt': prompt, 'video_negative': neg})}",
            )
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, f"\x00ERROR\x00{exc}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_producer, daemon=True).start()

    async def _event_gen():
        while True:
            item = await queue.get()
            if item is None:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            if item.startswith("\x00ERROR\x00"):
                yield f"data: {json.dumps({'error': item[7:]})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            if item.startswith("\x00VIDEO_PROMPT\x00"):
                data = json.loads(item[len("\x00VIDEO_PROMPT\x00"):])
                yield f"data: {json.dumps({'video_prompt_update': data})}\n\n"
                continue
            yield f"data: {json.dumps({'chunk': item})}\n\n"

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
