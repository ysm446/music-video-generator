"""プロジェクト管理モジュール（作成・保存・読込）。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .scene import Scene, create_scenes


class Project:
    """プロジェクト全体を管理するクラス。"""

    def __init__(
        self,
        project_name: str,
        base_dir: Path,
        duration: float = 0.0,
        scene_duration: int = 5,
        concept: str = "",
        lyrics: str = "",
        image_resolution: Optional[dict] = None,
        video_resolution: Optional[dict] = None,
        resolution: Optional[dict] = None,
        video_fps: int = 16,
        video_frame_count: int = 81,
        fps: int = 16,
        music_file: str = "",
        comfyui_url: str = "http://localhost:8188",
        llm_url: str = "http://localhost:11434/v1",
        image_workflow: str = "workflows/zimage_turbo.json",
        video_workflow: str = "workflows/wan22_i2v.json",
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        self.project_name = project_name
        self.base_dir = Path(base_dir)
        self.duration = duration
        self.scene_duration = scene_duration
        self.concept = concept
        self.lyrics = lyrics
        base_resolution = resolution or {"width": 1280, "height": 720}
        self.image_resolution = image_resolution or dict(base_resolution)
        self.video_resolution = video_resolution or dict(base_resolution)
        # Backward-compatibility alias for legacy code/JSON
        self.resolution = self.image_resolution
        self.video_fps = int(video_fps)
        self.video_frame_count = int(video_frame_count)
        self.fps = fps
        self.music_file = music_file
        self.comfyui_url = comfyui_url
        self.llm_url = llm_url
        self.image_workflow = image_workflow
        self.video_workflow = video_workflow
        now = datetime.now().isoformat(timespec="seconds")
        self.created_at = created_at or now
        self.updated_at = updated_at or now
        self.scenes: list[Scene] = []

    # ---- パス ----

    @property
    def project_dir(self) -> Path:
        return self.base_dir / self.project_name

    @property
    def music_dir(self) -> Path:
        return self.project_dir / "music"

    @property
    def scenes_dir(self) -> Path:
        return self.project_dir / "scenes"

    @property
    def references_dir(self) -> Path:
        return self.project_dir / "references"

    @property
    def output_dir(self) -> Path:
        return self.project_dir / "output"

    def scene_dir(self, scene_id: int) -> Path:
        return self.scenes_dir / f"scene_{scene_id:03d}"

    # ---- プロジェクト作成 ----

    def initialize_dirs(self) -> None:
        """プロジェクトに必要なディレクトリを作成する。"""
        for d in [self.music_dir, self.scenes_dir, self.references_dir, self.output_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def setup_scenes(self) -> None:
        """durationとscene_durationからシーン一覧を初期化し、各scene.jsonを保存する。"""
        self.scenes = create_scenes(self.duration, self.scene_duration)
        for scene in self.scenes:
            scene.save(self.scene_dir(scene.scene_id))

    # ---- 保存 ----

    def save(self) -> None:
        """project.jsonを保存する。シーンは個別に保存済みのためここでは保存しない。"""
        self.updated_at = datetime.now().isoformat(timespec="seconds")
        data = {
            "project_name": self.project_name,
            "music_file": self.music_file,
            "duration": self.duration,
            "scene_duration": self.scene_duration,
            "scene_count": len(self.scenes),
            "concept": self.concept,
            "lyrics": self.lyrics,
            "resolution": self.image_resolution,
            "image_resolution": self.image_resolution,
            "video_resolution": self.video_resolution,
            "video_fps": self.video_fps,
            "video_frame_count": self.video_frame_count,
            "fps": self.fps,
            "comfyui_url": self.comfyui_url,
            "llm_url": self.llm_url,
            "image_workflow": self.image_workflow,
            "video_workflow": self.video_workflow,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        path = self.project_dir / "project.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_scene(self, scene: Scene) -> None:
        """指定シーンをディスクに保存し、scenes リストも更新する。"""
        scene.save(self.scene_dir(scene.scene_id))
        for i, s in enumerate(self.scenes):
            if s.scene_id == scene.scene_id:
                self.scenes[i] = scene
                break

    # ---- シーン順序・挿入・削除 ----

    def _renormalize_order(self) -> None:
        """self.scenes の現在のリスト順を元に order を 1, 2, 3... と正規化して保存する。"""
        for i, scene in enumerate(self.scenes, 1):
            scene.order = i
            scene.save(self.scene_dir(scene.scene_id))

    def move_scene_up(self, scene_idx: int) -> bool:
        """scenes[scene_idx] を1つ前に移動する（order値を交換）。先頭の場合は False を返す。"""
        if scene_idx <= 0 or scene_idx >= len(self.scenes):
            return False
        a, b = self.scenes[scene_idx], self.scenes[scene_idx - 1]
        a.order, b.order = b.order, a.order
        a.save(self.scene_dir(a.scene_id))
        b.save(self.scene_dir(b.scene_id))
        self.scenes.sort(key=lambda s: s.order)
        return True

    def move_scene_down(self, scene_idx: int) -> bool:
        """scenes[scene_idx] を1つ後ろに移動する（order値を交換）。末尾の場合は False を返す。"""
        if scene_idx < 0 or scene_idx >= len(self.scenes) - 1:
            return False
        a, b = self.scenes[scene_idx], self.scenes[scene_idx + 1]
        a.order, b.order = b.order, a.order
        a.save(self.scene_dir(a.scene_id))
        b.save(self.scene_dir(b.scene_id))
        self.scenes.sort(key=lambda s: s.order)
        return True

    def insert_scene_after(self, scene_idx: int) -> Scene:
        """scenes[scene_idx] の直後に空シーンを挿入して返す。"""
        new_id = max((s.scene_id for s in self.scenes), default=0) + 1
        cur = self.scenes[scene_idx]
        new_start = cur.end_time
        new_end = round(new_start + self.scene_duration, 2)
        new_scene = Scene(
            scene_id=new_id,
            start_time=new_start,
            end_time=new_end,
            order=cur.order + 1,  # _renormalize_order で上書きされる
        )
        new_scene.save(self.scene_dir(new_id))
        self.scenes.insert(scene_idx + 1, new_scene)
        self._renormalize_order()
        return new_scene

    def delete_scene(self, scene_idx: int) -> None:
        """scenes[scene_idx] をディレクトリごと削除し、order を正規化する。"""
        import shutil as _shutil
        scene = self.scenes[scene_idx]
        sd = self.scene_dir(scene.scene_id)
        if sd.exists():
            _shutil.rmtree(sd)
        self.scenes.pop(scene_idx)
        self._renormalize_order()

    # ---- 読込 ----

    @classmethod
    def load(cls, project_dir: Path) -> "Project":
        """project.json とシーン一覧を読み込む。

        Args:
            project_dir: プロジェクトディレクトリのパス

        Returns:
            Projectオブジェクト
        """
        path = project_dir / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        proj = cls(
            project_name=data["project_name"],
            base_dir=project_dir.parent,
            duration=data.get("duration", 0.0),
            scene_duration=data.get("scene_duration", 5),
            concept=data.get("concept", ""),
            lyrics=data.get("lyrics", ""),
            image_resolution=data.get(
                "image_resolution",
                data.get("resolution", {"width": 1280, "height": 720}),
            ),
            video_resolution=data.get(
                "video_resolution",
                data.get("resolution", {"width": 1280, "height": 720}),
            ),
            video_fps=data.get("video_fps", data.get("fps", 16)),
            video_frame_count=data.get("video_frame_count", 81),
            resolution=data.get("resolution", {"width": 1280, "height": 720}),
            fps=data.get("fps", 16),
            music_file=data.get("music_file", ""),
            comfyui_url=data.get("comfyui_url", "http://localhost:8188"),
            llm_url=data.get("llm_url", "http://localhost:11434/v1"),
            image_workflow=data.get("image_workflow", "workflows/zimage_turbo.json"),
            video_workflow=data.get("video_workflow", "workflows/wan22_i2v.json"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )
        proj.initialize_dirs()

        # シーン読込（order フィールドで並び替え）
        scenes_dir = proj.scenes_dir
        scene_dirs = sorted(scenes_dir.glob("scene_*"))
        for sd in scene_dirs:
            try:
                scene = Scene.load(sd)
                proj.scenes.append(scene)
            except Exception:
                pass
        proj.scenes.sort(key=lambda s: s.order)

        return proj

    # ---- 音楽ファイル ----

    def copy_music(self, src: str | Path) -> str:
        """音楽ファイルをプロジェクトの music/ ディレクトリにコピーする。

        Returns:
            プロジェクトルートからの相対パス文字列
        """
        src = Path(src)
        dest = self.music_dir / src.name
        shutil.copy2(src, dest)
        rel = dest.relative_to(self.project_dir)
        self.music_file = str(rel)
        return self.music_file

    def absolute_music_path(self) -> Optional[Path]:
        """音楽ファイルの絶対パスを返す。存在しない場合はNone。"""
        if not self.music_file:
            return None
        p = self.project_dir / self.music_file
        return p if p.exists() else None


def list_projects(base_dir: Path) -> list[str]:
    """base_dir 内のプロジェクト名一覧を返す。"""
    base_dir = Path(base_dir)
    if not base_dir.exists():
        return []
    return [
        d.name
        for d in sorted(base_dir.iterdir())
        if d.is_dir() and (d / "project.json").exists()
    ]
