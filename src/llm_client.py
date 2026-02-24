"""Qwen3-VL (OpenAI互換API) 連携モジュール。"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Generator, Optional

from openai import OpenAI


class LLMClient:
    """Qwen3-VL などの OpenAI互換ローカルLLMクライアント。"""

    def __init__(self, base_url: str, model: str, api_key: str = "dummy") -> None:
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=api_key)

    # ---- チャット（ストリーム） ----

    def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """ストリーミングでチャット応答を返すジェネレータ。

        Yields:
            テキストチャンク
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

    def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """チャット応答を文字列で返す（非ストリーム）。"""
        return "".join(self.chat_stream(messages, temperature, max_tokens))

    # ---- 全シーン一括プロンプト生成 ----

    def generate_all_scene_prompts(
        self,
        concept: str,
        scene_count: int,
        scene_duration: int,
        start_scene_id: int = 1,
        reference_images: Optional[list[Path]] = None,
        temperature: float = 0.7,
    ) -> list[dict]:
        """楽曲情報とコンセプトから全シーンのプロンプトを一括生成する。

        Returns:
            scene_idをキーとした辞書のリスト
            [{"scene_id": 1, "section": "...", "plot": "...",
              "image_prompt": "...", "image_negative": "...",
              "video_prompt": "...", "video_negative": "..."}, ...]
        """
        system = (
            "あなたはミュージックビデオのディレクターです。"
            "楽曲のコンセプトをもとに、各シーンの映像プロンプトをJSON形式で生成してください。"
            "section, plot は日本語で記述してください。"
            "image_promptとvideo_promptは英語で記述してください。"
        )

        content: list[dict] = []

        # 参照画像をVisionで渡す
        if reference_images:
            for img_path in reference_images[:4]:  # 最大4枚
                try:
                    b64 = base64.b64encode(img_path.read_bytes()).decode()
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    })
                except Exception:
                    pass

        end_scene_id = start_scene_id + scene_count - 1
        content.append({
            "type": "text",
            "text": (
                f"コンセプト: {concept}\n\n"
                f"シーン数: {scene_count}（1シーン{scene_duration}秒）\n"
                f"scene_id は {start_scene_id} 〜 {end_scene_id} を使用すること。\n\n"
                "以下のJSON配列形式で指定した scene_id 範囲のプロンプトを出力してください。"
                "各要素は scene_id, section, plot, "
                "image_prompt, image_negative, video_prompt, video_negative を含めること。\n"
                "```json\n[ ... ]\n```"
            ),
        })

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        response = self.chat(messages, temperature=temperature, max_tokens=3000)
        return _extract_json_list(response)

    # ---- 個別シーンのプロンプト改善 ----

    def improve_scene_prompt(
        self,
        scene_data: dict,
        concept: str,
        reference_images: Optional[list[Path]] = None,
        temperature: float = 0.7,
    ) -> dict:
        """単一シーンのプロンプトを改善する。

        Returns:
            改善後のフィールドを含む辞書
        """
        system = (
            "あなたはミュージックビデオのプロンプトエンジニアです。"
            "シーン情報をもとに、より良い画像/動画プロンプトを提案してください。"
            "image_promptとvideo_promptは英語で記述してください。"
        )

        content: list[dict] = []

        if reference_images:
            for img_path in reference_images[:2]:
                try:
                    b64 = base64.b64encode(img_path.read_bytes()).decode()
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    })
                except Exception:
                    pass

        content.append({
            "type": "text",
            "text": (
                f"全体コンセプト: {concept}\n\n"
                f"シーン情報:\n{json.dumps(scene_data, ensure_ascii=False, indent=2)}\n\n"
                "改善したプロンプトをJSON形式で出力してください。"
                "plot, image_prompt, image_negative, video_prompt, video_negative を含めること。\n"
                "```json\n{ ... }\n```"
            ),
        })

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        response = self.chat(messages, temperature=temperature)
        result = _extract_json_dict(response)
        return result


# ---- ユーティリティ ----

def _extract_json_list(text: str) -> list[dict]:
    """テキストからJSONリストを抽出する。失敗時は空リストを返す。"""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    raw = match.group(1) if match else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _extract_json_dict(text: str) -> dict:
    """テキストからJSON辞書を抽出する。失敗時は空辞書を返す。"""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    raw = match.group(1) if match else text
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
