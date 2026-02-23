"""ComfyUI API 連携モジュール（画像・動画生成）。"""

from __future__ import annotations

import copy
import json
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

import requests


class ComfyUIClient:
    """ComfyUI の REST API を介して画像・動画を生成するクライアント。"""

    def __init__(self, base_url: str = "http://localhost:8188") -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.client_id = str(uuid.uuid4())
        self._local_output_dir: Optional[Path] = None

    # ---- 接続確認 ----

    def is_available(self) -> bool:
        """ComfyUI サーバーへの接続を確認する。"""
        for endpoint in ("/system_stats", "/queue"):
            try:
                resp = requests.get(f"{self.base_url}{endpoint}", timeout=5)
                if resp.status_code == 200:
                    return True
            except Exception:
                continue
        return False

    # ---- ワークフロー読込 ----

    def load_workflow(self, workflow_path: str | Path) -> dict:
        """ワークフローJSONを読み込む。"""
        path = Path(workflow_path)
        return json.loads(path.read_text(encoding="utf-8"))

    # ---- プロンプト投入 ----

    def queue_prompt(self, workflow: dict) -> str:
        """ワークフローをキューに投入し、prompt_id を返す。"""
        payload = {"prompt": workflow, "client_id": self.client_id}
        resp = requests.post(
            f"{self.base_url}/prompt",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["prompt_id"]

    # ---- 完了ポーリング ----

    def wait_for_prompt(
        self, prompt_id: str, poll_interval: float = 2.0, timeout: float = 300.0
    ) -> dict:
        """prompt_id の完了をポーリングで待ち、outputs を返す。

        Returns:
            ComfyUI の history エントリ (outputs 含む)
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            history = self._get_history(prompt_id)
            if prompt_id in history:
                return history[prompt_id]
            time.sleep(poll_interval)
        raise TimeoutError(f"ComfyUI タイムアウト: prompt_id={prompt_id}")

    def _get_history(self, prompt_id: str) -> dict:
        resp = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    # ---- 生成ファイル取得 ----

    def download_output(self, filename: str, subfolder: str = "", dest: Path = None) -> Path:
        """ComfyUI の output から生成ファイルをダウンロードして dest に保存する。"""
        params = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": "output"})
        url = f"{self.base_url}/view?{params}"
        resp = requests.get(url, timeout=60, stream=True)
        if resp.status_code == 200:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return dest

        local_src = self._resolve_local_output_file(filename, subfolder)
        if local_src and local_src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_src, dest)
            return dest

        resp.raise_for_status()
        return dest

    # ---- 画像生成 ----

    def _resolve_local_output_file(self, filename: str, subfolder: str) -> Optional[Path]:
        output_dir = self._get_local_output_dir()
        if output_dir is None:
            return None

        candidates = [
            output_dir / subfolder / filename if subfolder else output_dir / filename,
            output_dir / filename,
        ]
        for p in candidates:
            try:
                if p.exists():
                    return p
            except Exception:
                continue
        return None

    def _get_local_output_dir(self) -> Optional[Path]:
        if self._local_output_dir is not None:
            return self._local_output_dir
        try:
            resp = requests.get(f"{self.base_url}/system_stats", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            argv = data.get("system", {}).get("argv", [])
            if not argv:
                return None
            main_py = Path(argv[0])
            self._local_output_dir = main_py.parent / "output"
            return self._local_output_dir
        except Exception:
            return None

    def generate_image(
        self,
        workflow_path: str | Path,
        positive_prompt: str,
        negative_prompt: str,
        seed: int,
        width: int,
        height: int,
        dest_path: Path,
        poll_interval: float = 2.0,
        timeout: float = 300.0,
    ) -> Path:
        """z-image Turbo ワークフローで画像を生成し dest_path に保存する。

        ワークフローJSON内のノードへのパラメータ注入はワークフロー依存。
        ここでは汎用的なキー名で探索して設定する。
        """
        workflow = self.load_workflow(workflow_path)
        workflow = _inject_image_params(workflow, positive_prompt, negative_prompt, seed, width, height)

        prompt_id = self.queue_prompt(workflow)
        result = self.wait_for_prompt(prompt_id, poll_interval, timeout)

        # outputs から最初の画像ファイルを取得
        image_info = _extract_first_image(result)
        if image_info is None:
            raise RuntimeError("ComfyUI から画像が返されませんでした")

        return self.download_output(image_info["filename"], image_info.get("subfolder", ""), dest_path)

    # ---- 動画生成 ----

    def generate_video(
        self,
        workflow_path: str | Path,
        input_image_path: Path,
        positive_prompt: str,
        negative_prompt: str,
        seed: int,
        width: int,
        height: int,
        fps: int,
        frame_count: int,
        dest_path: Path,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
    ) -> Path:
        """WAN2.2 img2video ワークフローで動画を生成し dest_path に保存する。

        入力画像は ComfyUI の input フォルダへアップロードする。
        """
        # 入力画像をComfyUIにアップロード
        upload_name = self._upload_image(input_image_path)

        workflow = self.load_workflow(workflow_path)
        workflow = _inject_video_params(
            workflow, upload_name, positive_prompt, negative_prompt, seed, width, height, fps, frame_count
        )

        prompt_id = self.queue_prompt(workflow)
        result = self.wait_for_prompt(prompt_id, poll_interval, timeout)

        video_info = _extract_first_video_any(result)
        if video_info is None:
            raise RuntimeError("ComfyUI から動画が返されませんでした")

        return self.download_output(video_info["filename"], video_info.get("subfolder", ""), dest_path)

    def _upload_image(self, image_path: Path) -> str:
        """画像を ComfyUI の /upload/image エンドポイントにアップロードし、ファイル名を返す。"""
        with open(image_path, "rb") as f:
            files = {"image": (image_path.name, f, "image/png")}
            resp = requests.post(f"{self.base_url}/upload/image", files=files, timeout=30)
        resp.raise_for_status()
        return resp.json()["name"]


# ---- ワークフロー パラメータ注入ヘルパー ----

def _inject_image_params(
    workflow: dict,
    positive: str,
    negative: str,
    seed: int,
    width: int,
    height: int,
) -> dict:
    """ワークフローの各ノードに画像生成パラメータを注入する。

    ノードの class_type を元に汎用的に設定を行う。
    実際のワークフローに合わせて必要なら拡張すること。
    """
    wf = copy.deepcopy(workflow)
    for node in wf.values():
        inputs = node.get("inputs", {})
        ct = node.get("class_type", "")

        if ct in ("CLIPTextEncode",):
            # positive / negative の判定はノード名やtitleで行う
            title = node.get("_meta", {}).get("title", "").lower()
            if "negative" in title or "neg" in title:
                inputs["text"] = negative
            else:
                inputs["text"] = positive

        if ct in ("KSampler", "KSamplerAdvanced", "SamplerCustom"):
            if seed >= 0:
                inputs["seed"] = seed
                inputs["noise_seed"] = seed

        if ct in ("EmptyLatentImage", "EmptySD3LatentImage"):
            inputs["width"] = width
            inputs["height"] = height

    return wf


def _inject_video_params(
    workflow: dict,
    image_name: str,
    positive: str,
    negative: str,
    seed: int,
    width: int,
    height: int,
    fps: int,
    frame_count: int,
) -> dict:
    """ワークフローの各ノードに動画生成パラメータを注入する。"""
    wf = copy.deepcopy(workflow)
    for node in wf.values():
        inputs = node.get("inputs", {})
        ct = node.get("class_type", "")

        if ct == "LoadImage":
            inputs["image"] = image_name

        if ct in ("CLIPTextEncode",):
            title = node.get("_meta", {}).get("title", "").lower()
            if "negative" in title or "neg" in title:
                inputs["text"] = negative
            else:
                inputs["text"] = positive

        if ct in ("KSampler", "KSamplerAdvanced", "SamplerCustom"):
            if seed >= 0:
                inputs["seed"] = seed
                inputs["noise_seed"] = seed

        # Override resolution for nodes that expose width/height directly.
        if "width" in inputs and "height" in inputs:
            inputs["width"] = width
            inputs["height"] = height

        # Override fps/frame-count for common video nodes.
        if "frame_rate" in inputs:
            inputs["frame_rate"] = fps
        if "fps" in inputs:
            inputs["fps"] = fps
        if "num_frames" in inputs:
            inputs["num_frames"] = frame_count
        if "length" in inputs:
            inputs["length"] = frame_count

    return wf


def _extract_first_image(history_entry: dict) -> Optional[dict]:
    """history エントリから最初の画像情報を返す。"""
    outputs = history_entry.get("outputs", {})
    for node_output in outputs.values():
        images = node_output.get("images", [])
        if images:
            return images[0]
    return None


def _extract_first_video(history_entry: dict) -> Optional[dict]:
    """history エントリから最初の動画情報を返す。"""
    outputs = history_entry.get("outputs", {})
    for node_output in outputs.values():
        videos = node_output.get("videos", [])
        if videos:
            return videos[0]
        gifs = node_output.get("gifs", [])
        if gifs:
            return gifs[0]
    return None


def _extract_first_video_any(history_entry: dict) -> Optional[dict]:
    """ComfyUIの複数出力形式に対応して最初の動画情報を返す。"""
    outputs = history_entry.get("outputs", {})
    video_exts = (".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif")
    for node_output in outputs.values():
        videos = node_output.get("videos", [])
        if videos and isinstance(videos[0], dict):
            return videos[0]

        gifs = node_output.get("gifs", [])
        if gifs and isinstance(gifs[0], dict):
            return gifs[0]

        animated = node_output.get("animated", [])
        if animated and isinstance(animated[0], dict):
            return animated[0]

        images = node_output.get("images", [])
        for item in images:
            name = str(item.get("filename", "")).lower()
            if name.endswith(video_exts):
                return item
    return None
