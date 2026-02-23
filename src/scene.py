"""シーンデータ管理モジュール。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class Scene:
    """1シーン分のデータを保持するデータクラス。"""

    scene_id: int
    start_time: float
    end_time: float
    section: str = ""
    lyrics: str = ""
    plot: str = ""
    image_prompt: str = ""
    image_negative: str = ""
    image_seed: int = -1
    video_prompt: str = ""
    video_negative: str = ""
    video_seed: int = -1
    status: str = "empty"   # empty / plot_done / image_done / video_done
    notes: str = ""

    # ---- ステータス判定 ----

    def is_empty(self) -> bool:
        return self.status == "empty"

    def is_plot_done(self) -> bool:
        return self.status in ("plot_done", "image_done", "video_done")

    def is_image_done(self) -> bool:
        return self.status in ("image_done", "video_done")

    def is_video_done(self) -> bool:
        return self.status == "video_done"

    # ---- シリアライズ ----

    def to_dict(self) -> dict:
        """辞書形式に変換する。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Scene":
        """辞書からSceneを生成する。"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    # ---- ファイル I/O ----

    def save(self, scene_dir: Path) -> None:
        """scene.json をシーンディレクトリに保存する。"""
        scene_dir.mkdir(parents=True, exist_ok=True)
        path = scene_dir / "scene.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, scene_dir: Path) -> "Scene":
        """scene_dir/scene.json からSceneを読み込む。"""
        path = scene_dir / "scene.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    # ---- ファイルパス取得 ----

    def image_path(self, scene_dir: Path) -> Path:
        return scene_dir / "image.png"

    def video_path(self, scene_dir: Path) -> Path:
        return scene_dir / "video.mp4"

    # ---- ステータスアイコン ----

    def status_icon(self) -> str:
        """UIで表示するステータスアイコン文字を返す。"""
        icons = {
            "empty": "○",
            "plot_done": "●",
            "image_done": "🖼",
            "video_done": "✅",
        }
        return icons.get(self.status, "?")


def create_scenes(duration: float, scene_duration: int = 5) -> list[Scene]:
    """楽曲長さからシーン一覧を生成する。

    Args:
        duration: 楽曲の長さ（秒）
        scene_duration: 1シーンの長さ（秒）

    Returns:
        Sceneオブジェクトのリスト
    """
    scenes: list[Scene] = []
    t = 0.0
    scene_id = 1
    while t < duration:
        end = min(t + scene_duration, duration)
        scenes.append(Scene(scene_id=scene_id, start_time=round(t, 2), end_time=round(end, 2)))
        t = end
        scene_id += 1
    return scenes
