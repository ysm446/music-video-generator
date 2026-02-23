"""ffmpeg を使った動画結合・書き出しモジュール。"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .project import Project


class VideoExporter:
    """プロジェクトの全シーン動画を ffmpeg で結合して最終動画を書き出す。"""

    def __init__(self, project: Project) -> None:
        self.project = project

    def export(
        self,
        output_filename: str = "final.mp4",
        with_music: bool = True,
    ) -> Path:
        """全シーンの動画を結合して最終 MP4 を生成する。

        Args:
            output_filename: 出力ファイル名
            with_music: 音楽ファイルを合成するか

        Returns:
            生成した最終動画のパス
        """
        proj = self.project
        output_path = proj.output_dir / output_filename

        # 動画ファイルリストを収集（無効シーンはスキップ）
        video_files: list[Path] = []
        for scene in proj.scenes:
            if not scene.enabled:
                continue
            vp = scene.video_path(proj.scene_dir(scene.scene_id))
            if vp.exists():
                video_files.append(vp)

        if not video_files:
            raise RuntimeError("結合できる動画ファイルが存在しません")

        # concat リストファイルを一時ファイルに書き出し
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            for vf in video_files:
                tmp.write(f"file '{vf.as_posix()}'\n")
            concat_list = Path(tmp.name)

        try:
            # 動画のみ結合
            merged_video = proj.output_dir / "_merged.mp4"
            _run_ffmpeg([
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(merged_video),
            ])

            # 音楽合成
            music_path = proj.absolute_music_path()
            if with_music and music_path and music_path.exists():
                _run_ffmpeg([
                    "ffmpeg", "-y",
                    "-i", str(merged_video),
                    "-i", str(music_path),
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-shortest",
                    str(output_path),
                ])
                merged_video.unlink(missing_ok=True)
            else:
                merged_video.rename(output_path)

        finally:
            concat_list.unlink(missing_ok=True)

        return output_path

    def get_scene_thumbnails(self) -> list[tuple[int, Path | None]]:
        """各シーンの画像パスをリストで返す（サムネイル表示用）。

        Returns:
            [(scene_id, image_path_or_None), ...]
        """
        proj = self.project
        result = []
        for scene in proj.scenes:
            img = scene.image_path(proj.scene_dir(scene.scene_id))
            result.append((scene.scene_id, img if img.exists() else None))
        return result


def _run_ffmpeg(cmd: list[str]) -> None:
    """ffmpeg コマンドを実行する。失敗時は RuntimeError を送出する。"""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg エラー:\n{result.stderr}")
