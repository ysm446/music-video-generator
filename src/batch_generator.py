"""一括生成処理モジュール（バッチ、進捗管理、停止/再開）。"""

from __future__ import annotations

from datetime import datetime
import shutil
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
        target: str = "both",
        skip_video_done: bool = True,
        video_quality: str = "preview",
    ) -> None:
        """全シーンの画像・動画を順番に生成する。

        Args:
            on_progress: (scene_id, total, message) を受け取るコールバック
            on_error: (scene_id, error_message) を受け取るコールバック
            target: "image" / "video" / "both"
            skip_video_done: statusがvideo_doneのシーンをスキップするか（preview品質のみ有効）
            video_quality: "preview" または "final"
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

                scene_dir = proj.scene_dir(scene.scene_id)
                image_path = scene.image_path(scene_dir)

                if target == "image":
                    if not (scene.image_prompt or "").strip():
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（画像プロンプト未設定）")
                        continue
                    if image_path.exists() and image_path.stat().st_size > 0:
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（画像あり）")
                        continue
                    try:
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 画像生成中...")
                        self._generate_image(scene)
                        scene.status = "image_done"
                        proj.save_scene(scene)
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 完了")
                    except Exception as e:
                        if on_error:
                            on_error(scene.scene_id, f"画像生成エラー: {e}")
                    continue

                # スキップ判定
                if target == "video":
                    if not (image_path.exists() and image_path.stat().st_size > 0):
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（画像未生成）")
                        continue
                    if video_quality == "final":
                        existing_video = scene.video_final_path(scene_dir)
                        skip_label = "最終版動画あり"
                    else:
                        existing_video = scene.video_preview_path(scene_dir)
                        skip_label = "プレビュー動画あり"
                    if existing_video.exists() and existing_video.stat().st_size > 0:
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（{skip_label}）")
                        continue
                elif video_quality == "final":
                    # 最終版: video_final.mp4 が既に存在すればスキップ
                    if skip_video_done:
                        final_path = scene.video_final_path(proj.scene_dir(scene.scene_id))
                        if final_path.exists() and final_path.stat().st_size > 0:
                            if on_progress:
                                on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（最終版完了済み）")
                            continue
                    # 最終版生成には画像が必要
                    if not scene.is_image_done():
                        if on_progress:
                            on_progress(scene.scene_id, total, f"シーン {scene.scene_id} はスキップ（画像未生成）")
                        continue
                else:
                    # プレビュー: 既存の video_done スキップ
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
                quality_label = "最終版動画" if video_quality == "final" else "動画"
                try:
                    if on_progress:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: {quality_label}生成中...")
                    self._generate_video(scene, quality=video_quality)
                    if video_quality == "preview":
                        scene.status = "video_done"
                        proj.save_scene(scene)
                    if on_progress:
                        on_progress(scene.scene_id, total, f"シーン {scene.scene_id}/{total}: 完了")
                except Exception as e:
                    if on_error:
                        on_error(scene.scene_id, f"{quality_label}生成エラー: {e}")
        finally:
            self.is_running = False

    def run_async(
        self,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        on_error: Optional[Callable[[int, str], None]] = None,
        target: str = "both",
        video_quality: str = "preview",
    ) -> threading.Thread:
        """バックグラウンドスレッドで run() を実行する。"""
        thread = threading.Thread(
            target=self.run,
            kwargs={"on_progress": on_progress, "on_error": on_error, "target": target, "video_quality": video_quality},
            daemon=True,
        )
        thread.start()
        return thread

    # ---- 個別生成 ----

    def regenerate_scene(self, scene_id: int, target: str = "both", video_quality: str = "preview") -> None:
        """指定シーンのみ再生成する。

        Args:
            scene_id: 対象シーンID
            target: "image" / "video" / "both"
            video_quality: "preview" または "final"
        """
        proj = self.project
        scene = next(s for s in proj.scenes if s.scene_id == scene_id)

        if target in ("image", "both"):
            scene.status = "plot_done"
            self._generate_image(scene)
            scene.status = "image_done"
            proj.save_scene(scene)

        if target in ("video", "both"):
            self._generate_video(scene, quality=video_quality)
            if video_quality == "preview":
                scene.status = "video_done"
                proj.save_scene(scene)

    # ---- 内部生成処理 ----

    @staticmethod
    def _next_image_version_name() -> str:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"image_{stamp}.png"

    def _generate_image(self, scene: Scene) -> None:
        proj = self.project
        scene_dir = proj.scene_dir(scene.scene_id)
        versions_dir = scene.image_versions_dir(scene_dir)
        versions_dir.mkdir(parents=True, exist_ok=True)
        version_name = self._next_image_version_name()
        version_path = scene.image_version_path(scene_dir, version_name)
        active_path = scene.image_path(scene_dir)
        # シーン個別ワークフローが指定されていればそちらを優先
        workflow = scene.image_workflow or proj.image_workflow
        self.comfyui.generate_image(
            workflow_path=workflow,
            positive_prompt=scene.image_prompt,
            negative_prompt=scene.image_negative,
            seed=scene.image_seed,
            width=proj.image_resolution["width"],
            height=proj.image_resolution["height"],
            dest_path=version_path,
        )
        shutil.copy2(version_path, active_path)
        scene.active_image_version = version_name

    def _generate_video(self, scene: Scene, quality: str = "preview") -> None:
        proj = self.project
        scene_dir = proj.scene_dir(scene.scene_id)
        image_path = scene.image_path(scene_dir)

        if quality == "final":
            dest = scene.video_final_path(scene_dir)
            width = proj.video_final_resolution["width"]
            height = proj.video_final_resolution["height"]
        else:
            dest = scene.video_preview_path(scene_dir)
            width = proj.video_resolution["width"]
            height = proj.video_resolution["height"]

        # シーン個別ワークフローが指定されていればそちらを優先
        workflow = scene.video_workflow or proj.video_workflow
        self.comfyui.generate_video(
            workflow_path=workflow,
            input_image_path=image_path,
            positive_prompt=scene.video_prompt,
            negative_prompt=scene.video_negative,
            seed=scene.video_seed,
            width=width,
            height=height,
            fps=proj.video_fps,
            frame_count=proj.video_frame_count,
            dest_path=dest,
        )
