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
        loop_music: bool = False,
        video_quality: str = "preview",
        audio_fade_in: bool = False,
        audio_fade_in_seconds: float = 1.0,
        audio_fade_out: bool = False,
        audio_fade_out_seconds: float = 1.0,
        video_fade_out_black: bool = False,
        video_fade_out_seconds: float = 1.0,
    ) -> Path:
        """全シーンの動画を結合して最終 MP4 を生成する。

        Args:
            output_filename: 出力ファイル名
            with_music: 音楽ファイルを合成するか
            loop_music: 音楽をループして動画尺まで伸ばすか
            video_quality: "preview" または "final"（finalはプレビューにフォールバック）

        Returns:
            生成した最終動画のパス
        """
        proj = self.project
        output_path = proj.output_dir / output_filename

        # 動画ファイルリストを収集（無効シーン・存在しないファイル・空ファイルはスキップ）
        video_files: list[Path] = []
        for scene in proj.scenes:
            if not scene.enabled:
                continue
            scene_dir = proj.scene_dir(scene.scene_id)
            if video_quality == "final":
                vp = scene.video_final_path(scene_dir)
                if not (vp.exists() and vp.stat().st_size > 0):
                    # 最終版がなければプレビューにフォールバック
                    vp = scene.video_preview_path(scene_dir)
            else:
                vp = scene.video_preview_path(scene_dir)
            if vp.exists() and vp.stat().st_size > 0:
                video_files.append(vp)

        if not video_files:
            raise RuntimeError("結合できる動画ファイルが存在しません（video_done のシーンがありません）")

        # 出力ディレクトリを事前に作成
        proj.output_dir.mkdir(parents=True, exist_ok=True)

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

            total_duration = _probe_duration_seconds(merged_video)
            v_fade_sec = max(0.0, float(video_fade_out_seconds or 0.0))
            a_fade_in_sec = max(0.0, float(audio_fade_in_seconds or 0.0))
            a_fade_out_sec = max(0.0, float(audio_fade_out_seconds or 0.0))

            # 音楽合成
            music_path = proj.absolute_music_path()
            if with_music and music_path and music_path.exists():
                music_input_args = ["-stream_loop", "-1"] if loop_music else []
                audio_filters: list[str] = []
                if audio_fade_in and a_fade_in_sec > 0:
                    audio_filters.append(f"afade=t=in:st=0:d={a_fade_in_sec:.3f}")
                if audio_fade_out and a_fade_out_sec > 0:
                    a_fade_out_start = max(0.0, total_duration - a_fade_out_sec)
                    audio_filters.append(f"afade=t=out:st={a_fade_out_start:.3f}:d={a_fade_out_sec:.3f}")

                use_video_fade = video_fade_out_black and v_fade_sec > 0
                if use_video_fade and total_duration > 0:
                    v_fade_start = max(0.0, total_duration - v_fade_sec)
                    filter_parts = [f"[0:v]fade=t=out:st={v_fade_start:.3f}:d={v_fade_sec:.3f}[vout]"]
                    if audio_filters:
                        filter_parts.append(f"[1:a]{','.join(audio_filters)}[aout]")
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", str(merged_video),
                        *music_input_args,
                        "-i", str(music_path),
                        "-filter_complex", ";".join(filter_parts),
                        "-map", "[vout]",
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-preset", "medium",
                        "-crf", "18",
                    ]
                    if audio_filters:
                        cmd += ["-map", "[aout]"]
                    else:
                        cmd += ["-map", "1:a:0"]
                    cmd += ["-c:a", "aac", "-t", f"{total_duration:.3f}", str(output_path)]
                    _run_ffmpeg(cmd)
                elif audio_filters:
                    _run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", str(merged_video),
                        *music_input_args,
                        "-i", str(music_path),
                        "-filter_complex", f"[1:a]{','.join(audio_filters)}[aout]",
                        "-map", "0:v:0",
                        "-map", "[aout]",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-t", f"{total_duration:.3f}",
                        str(output_path),
                    ])
                else:
                    _run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", str(merged_video),
                        *music_input_args,
                        "-i", str(music_path),
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-t", f"{total_duration:.3f}",
                        str(output_path),
                    ])
                merged_video.unlink(missing_ok=True)
            else:
                # 音楽なしでもブラックフェードアウトのみ適用可能
                if video_fade_out_black and v_fade_sec > 0 and total_duration > 0:
                    v_fade_start = max(0.0, total_duration - v_fade_sec)
                    _run_ffmpeg([
                        "ffmpeg", "-y",
                        "-i", str(merged_video),
                        "-vf", f"fade=t=out:st={v_fade_start:.3f}:d={v_fade_sec:.3f}",
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-preset", "medium",
                        "-crf", "18",
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


def _probe_duration_seconds(path: Path) -> float:
    """ffprobeでメディア秒数を取得する。"""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe エラー:\n{result.stderr}")
    try:
        return max(0.0, float((result.stdout or "").strip()))
    except Exception as e:
        raise RuntimeError(f"メディア長の取得に失敗しました: {path}") from e
