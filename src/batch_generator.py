"""一括生成処理モジュール（バッチ、進捗管理、停止/再開）。"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from .project import Project
from .scene import Scene
from .comfyui_client import ComfyUIClient


class BatchGenerator:
    """全シーンを順番に 画像→動画 と処理するバッチジェネレータ。

    スレッドセーフな停止フラグを持ち、途中停止・再開が可能。
    """

    def __init__(self, project: Project, comfyui: ComfyUIClient) -> None:
        self.project = project
        self.comfyui = comfyui
        self._stop_event = threading.Event()
        self.is_running = False

    # ---- 制御 ----

    def stop(self) -> None:
        """一括生成を停止するフラグを立てる。"""
        self._stop_event.set()

    def reset_stop(self) -> None:
        """停止フラグをリセットする。"""
        self._stop_event.clear()

    # ---- バッチ実行 ----

    def run(
        self,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        on_error: Optional[Callable[[int, str], None]] = None,
        skip_video_done: bool = True,
    ) -> None:
        """全シーンの画像・動画を順番に生成する。

        Args:
            on_progress: (scene_id, total, message) を受け取るコールバック
            on_error: (scene_id, error_message) を受け取るコールバック
            skip_video_done: statusがvideo_doneのシーンをスキップするか
        """
        self.reset_stop()
        self.is_running = True
        proj = self.project
        total = len(proj.scenes)

        try:
            for i, scene in enumerate(proj.scenes):
                if self._stop_event.is_set():
                    break

                if not scene.enabled:
                    if on_progress:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（無効）")
                    continue

                if skip_video_done and scene.is_video_done():
                    if on_progress:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（完了済み）")
                    continue

                # 画像生成
                if not scene.is_image_done():
                    try:
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 画像生成中...")
                        self._generate_image(scene)
                        scene.status = "image_done"
                        proj.save_scene(scene)
                    except Exception as e:
                        if on_error:
                            on_error(scene.scene_id, f"画像生成エラー: {e}")
                        continue

                if self._stop_event.is_set():
                    break

                # 動画生成
                try:
                    if on_progress:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 動画生成中...")
                    self._generate_video(scene)
                    scene.status = "video_done"
                    proj.save_scene(scene)
                    if on_progress:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 完了")
                except Exception as e:
                    if on_error:
                        on_error(scene.scene_id, f"動画生成エラー: {e}")
        finally:
            self.is_running = False

    def run_async(
        self,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        on_error: Optional[Callable[[int, str], None]] = None,
    ) -> threading.Thread:
        """バックグラウンドスレッドで run() を実行する。"""
        thread = threading.Thread(
            target=self.run,
            kwargs={"on_progress": on_progress, "on_error": on_error},
            daemon=True,
        )
        thread.start()
        return thread

    # ---- 個別生成 ----

    def regenerate_scene(self, scene_id: int, target: str = "both") -> None:
        """指定シーンのみ再生成する。

        Args:
            scene_id: 対象シーンID
            target: "image" / "video" / "both"
        """
        proj = self.project
        scene = next(s for s in proj.scenes if s.scene_id == scene_id)

        if target in ("image", "both"):
            scene.status = "plot_done"
            self._generate_image(scene)
            scene.status = "image_done"
            proj.save_scene(scene)

        if target in ("video", "both"):
            self._generate_video(scene)
            scene.status = "video_done"
            proj.save_scene(scene)

    # ---- 内部生成処理 ----

    def _generate_image(self, scene: Scene) -> None:
        proj = self.project
        dest = scene.image_path(proj.scene_dir(scene.scene_id))
        # シーン個別ワークフローが指定されていればそちらを優先
        workflow = scene.image_workflow or proj.image_workflow
        self.comfyui.generate_image(
            workflow_path=workflow,
            positive_prompt=scene.image_prompt,
            negative_prompt=scene.image_negative,
            seed=scene.image_seed,
            width=proj.resolution["width"],
            height=proj.resolution["height"],
            dest_path=dest,
        )

    def _generate_video(self, scene: Scene) -> None:
        proj = self.project
        scene_dir = proj.scene_dir(scene.scene_id)
        image_path = scene.image_path(scene_dir)
        dest = scene.video_path(scene_dir)
        # シーン個別ワークフローが指定されていればそちらを優先
        workflow = scene.video_workflow or proj.video_workflow
        self.comfyui.generate_video(
            workflow_path=workflow,
            input_image_path=image_path,
            positive_prompt=scene.video_prompt,
            negative_prompt=scene.video_negative,
            seed=scene.video_seed,
            fps=proj.fps,
            dest_path=dest,
        )
