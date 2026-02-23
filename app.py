"""MV Generator - Gradio メインアプリケーション。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import gradio as gr
import yaml

from src.project import Project, list_projects
from src.scene import Scene
from src.llm_client import LLMClient
from src import model_manager
from src import settings_manager
from src.comfyui_client import ComfyUIClient
from src.batch_generator import BatchGenerator
from src.video_export import VideoExporter

# ---- 設定読込 ----

CONFIG_PATH = Path("config.yaml")
_WORKFLOWS_DIR = Path("workflows")


def _list_workflows(kind: str) -> list[str]:
    """workflows/{kind}/ フォルダ内の JSON ファイルパスをリストで返す。"""
    folder = _WORKFLOWS_DIR / kind
    if not folder.exists():
        return []
    return sorted(str(p).replace("\\", "/") for p in folder.glob("*.json"))


def _list_image_workflows() -> list[str]:
    return _list_workflows("image")


def _list_video_workflows() -> list[str]:
    return _list_workflows("video")

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

_cfg = _load_config()
BASE_DIR = Path(_cfg.get("project", {}).get("base_dir", "projects"))

# ---- グローバル状態 ----
# GradioのState経由で管理するが、バックグラウンドスレッドとの共有のため
# バッチジェネレータはモジュールレベルで保持する
_batch_gen: Optional[BatchGenerator] = None
_batch_log: list[str] = []
_batch_lock = threading.Lock()

# ---- ユーティリティ ----

def _get_audio_duration(path: str | Path) -> float:
    """mutagen で音楽ファイルの長さ（秒）を取得する。"""
    from mutagen import File as MutagenFile
    audio = MutagenFile(str(path))
    if audio is None or audio.info is None:
        raise ValueError("音楽ファイルの長さを取得できませんでした")
    return float(audio.info.length)

def _project_from_state(state: dict | None) -> Optional[Project]:
    """State辞書からProjectオブジェクトを復元する。"""
    if not state:
        return None
    try:
        return Project.load(BASE_DIR / state["project_name"])
    except Exception:
        return None

def _scene_status_label(scene: Scene) -> str:
    return f"{scene.status_icon()} {scene.scene_id:03d}"


# ============================================================
# タブ1: プロジェクト
# ============================================================

def create_project_tab():
    """プロジェクトタブのUIと処理を定義する。"""

    with gr.Tab("プロジェクト"):
        gr.Markdown("## プロジェクト管理")

        with gr.Row():
            # --- 新規作成 ---
            with gr.Column():
                gr.Markdown("### 新規プロジェクト作成")
                new_name = gr.Textbox(label="プロジェクト名", placeholder="my_mv")
                new_music = gr.Audio(label="音楽ファイル", type="filepath")
                new_scene_dur = gr.Slider(label="シーン長さ（秒）", minimum=3, maximum=10, value=5, step=1)
                new_create_btn = gr.Button("作成", variant="primary")
                new_status = gr.Textbox(label="ステータス", interactive=False)

            # --- 既存読込 ---
            with gr.Column():
                gr.Markdown("### 既存プロジェクト読込")
                load_dropdown = gr.Dropdown(
                    label="プロジェクト一覧",
                    choices=list_projects(BASE_DIR),
                )
                load_refresh_btn = gr.Button("一覧更新")
                load_btn = gr.Button("読込", variant="primary")
                load_status = gr.Textbox(label="ステータス", interactive=False)

        gr.Markdown("---")
        gr.Markdown("### 基本設定")

        with gr.Row():
            cfg_comfyui_url = gr.Textbox(
                label="ComfyUI URL",
                value=_cfg.get("comfyui", {}).get("url", "http://localhost:8188"),
            )

        with gr.Row():
            cfg_res_w = gr.Number(label="解像度 幅", value=1280, precision=0)
            cfg_res_h = gr.Number(label="解像度 高さ", value=720, precision=0)
            cfg_fps = gr.Number(label="FPS", value=16, precision=0)

        gr.Markdown("#### ワークフロー選択")
        with gr.Tabs():
            with gr.Tab("画像ワークフロー"):
                with gr.Row():
                    cfg_img_wf = gr.Dropdown(
                        label="ワークフローファイル (workflows/image/)",
                        choices=_list_image_workflows(),
                        value=_cfg.get("comfyui", {}).get(
                            "image_workflow", "workflows/image/zimage_turbo.json"
                        ),
                        allow_custom_value=True,
                        scale=5,
                    )
                    cfg_img_wf_refresh = gr.Button("更新", scale=1, size="sm")
            with gr.Tab("動画ワークフロー"):
                with gr.Row():
                    cfg_vid_wf = gr.Dropdown(
                        label="ワークフローファイル (workflows/video/)",
                        choices=_list_video_workflows(),
                        value=_cfg.get("comfyui", {}).get(
                            "video_workflow", "workflows/video/wan22_i2v.json"
                        ),
                        allow_custom_value=True,
                        scale=5,
                    )
                    cfg_vid_wf_refresh = gr.Button("更新", scale=1, size="sm")

        save_cfg_btn = gr.Button("設定を保存")
        save_cfg_status = gr.Textbox(label="", interactive=False)

    return (
        new_name, new_music, new_scene_dur, new_create_btn, new_status,
        load_dropdown, load_refresh_btn, load_btn, load_status,
        cfg_comfyui_url, cfg_res_w, cfg_res_h, cfg_fps,
        cfg_img_wf, cfg_vid_wf, cfg_img_wf_refresh, cfg_vid_wf_refresh,
        save_cfg_btn, save_cfg_status,
    )


# ============================================================
# タブ2: 計画
# ============================================================

def create_plan_tab():
    """計画タブのUIを定義する。"""

    with gr.Tab("計画"):
        with gr.Row():
            # --- サイドバー（シーン一覧） ---
            with gr.Column(scale=1, min_width=140):
                gr.Markdown("### シーン一覧")
                plan_scene_btns = gr.Dataset(
                    label="",
                    components=[gr.Textbox(visible=False)],
                    samples=[],
                    type="index",
                    headers=["シーン"],
                )
                plan_prev_btn = gr.Button("◀ Prev")
                plan_next_btn = gr.Button("Next ▶")

            # --- メインエリア ---
            with gr.Column(scale=4):
                gr.Markdown("### LLMチャット（コンセプト相談）")
                plan_chatbot = gr.Chatbot(height=260)
                with gr.Row():
                    plan_chat_input = gr.Textbox(
                        label="", placeholder="コンセプトや歌詞について質問...", scale=4
                    )
                    plan_chat_send = gr.Button("送信", scale=1)
                plan_concept_input = gr.Textbox(label="全体コンセプト（保存用）", lines=2)
                plan_lyrics_input = gr.Textbox(label="歌詞（任意）", lines=4)
                plan_bulk_btn = gr.Button("全シーンを一括提案（Qwen）", variant="secondary")
                plan_bulk_status = gr.Textbox(label="", interactive=False)

                gr.Markdown("---")
                gr.Markdown("### シーン編集")
                plan_scene_id_disp = gr.Number(label="シーンID", interactive=False, precision=0)
                plan_time_disp = gr.Textbox(label="時間", interactive=False)
                plan_section = gr.Textbox(label="セクション")
                plan_lyrics = gr.Textbox(label="歌詞")
                plan_plot = gr.Textbox(label="プロット（日本語）", lines=3)

                with gr.Row():
                    plan_img_prompt = gr.Textbox(label="画像プロンプト（英語）", lines=2)
                    plan_img_neg = gr.Textbox(label="画像ネガティブ（英語）", lines=2)

                with gr.Row():
                    plan_vid_prompt = gr.Textbox(label="動画プロンプト（英語）", lines=2)
                    plan_vid_neg = gr.Textbox(label="動画ネガティブ（英語）", lines=2)

                with gr.Row():
                    plan_img_wf = gr.Dropdown(
                        label="画像ワークフロー（空=プロジェクトデフォルト）",
                        choices=[""] + _list_image_workflows(),
                        value="",
                        allow_custom_value=True,
                    )
                    plan_vid_wf = gr.Dropdown(
                        label="動画ワークフロー（空=プロジェクトデフォルト）",
                        choices=[""] + _list_video_workflows(),
                        value="",
                        allow_custom_value=True,
                    )

                plan_notes = gr.Textbox(label="メモ", lines=1)

                with gr.Row():
                    plan_save_btn = gr.Button("保存", variant="primary")
                    plan_consult_btn = gr.Button("このシーンをQwenに相談")

                plan_save_status = gr.Textbox(label="", interactive=False)

    return (
        plan_scene_btns, plan_prev_btn, plan_next_btn,
        plan_chatbot, plan_chat_input, plan_chat_send,
        plan_concept_input, plan_lyrics_input, plan_bulk_btn, plan_bulk_status,
        plan_scene_id_disp, plan_time_disp, plan_section, plan_lyrics,
        plan_plot, plan_img_prompt, plan_img_neg, plan_vid_prompt, plan_vid_neg,
        plan_img_wf, plan_vid_wf,
        plan_notes, plan_save_btn, plan_consult_btn, plan_save_status,
    )


# ============================================================
# タブ3: 生成・編集
# ============================================================

def create_generate_tab():
    """生成・編集タブのUIを定義する。"""

    with gr.Tab("生成・編集"):
        with gr.Row():
            # --- サイドバー ---
            with gr.Column(scale=1, min_width=140):
                gr.Markdown("### シーン一覧")
                gen_scene_btns = gr.Dataset(
                    label="",
                    components=[gr.Textbox(visible=False)],
                    samples=[],
                    type="index",
                    headers=["シーン"],
                )
                gen_prev_btn = gr.Button("◀ Prev")
                gen_next_btn = gr.Button("Next ▶")

                gr.Markdown("---")
                gen_batch_btn = gr.Button("一括生成 開始", variant="primary")
                gen_stop_btn = gr.Button("停止")
                gen_progress = gr.Textbox(label="進捗", interactive=False, lines=4)

            # --- メインエリア ---
            with gr.Column(scale=4):
                gen_scene_id_disp = gr.Number(label="シーンID", interactive=False, precision=0)
                gen_time_disp = gr.Textbox(label="時間", interactive=False)

                with gr.Row():
                    gen_image_preview = gr.Image(label="生成画像", type="filepath")
                    gen_video_preview = gr.Video(label="生成動画")

                gen_img_prompt = gr.Textbox(label="画像プロンプト（英語）", lines=2)
                gen_img_neg = gr.Textbox(label="画像ネガティブ（英語）", lines=2)
                gen_vid_prompt = gr.Textbox(label="動画プロンプト（英語）", lines=2)
                gen_vid_neg = gr.Textbox(label="動画ネガティブ（英語）", lines=2)

                with gr.Row():
                    gen_img_seed = gr.Number(label="画像シード(-1=ランダム)", value=-1, precision=0)
                    gen_vid_seed = gr.Number(label="動画シード(-1=ランダム)", value=-1, precision=0)

                with gr.Row():
                    gen_img_wf = gr.Dropdown(
                        label="画像ワークフロー（空=プロジェクトデフォルト）",
                        choices=[""] + _list_image_workflows(),
                        value="",
                        allow_custom_value=True,
                    )
                    gen_vid_wf = gr.Dropdown(
                        label="動画ワークフロー（空=プロジェクトデフォルト）",
                        choices=[""] + _list_video_workflows(),
                        value="",
                        allow_custom_value=True,
                    )

                with gr.Row():
                    gen_regen_img_btn = gr.Button("画像だけ再生成")
                    gen_regen_vid_btn = gr.Button("動画だけ再生成")
                    gen_regen_both_btn = gr.Button("両方再生成", variant="secondary")
                    gen_save_btn = gr.Button("保存", variant="primary")

                gen_status_disp = gr.Textbox(label="ステータス", interactive=False)

    return (
        gen_scene_btns, gen_prev_btn, gen_next_btn,
        gen_batch_btn, gen_stop_btn, gen_progress,
        gen_scene_id_disp, gen_time_disp,
        gen_image_preview, gen_video_preview,
        gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
        gen_img_seed, gen_vid_seed,
        gen_img_wf, gen_vid_wf,
        gen_regen_img_btn, gen_regen_vid_btn, gen_regen_both_btn, gen_save_btn,
        gen_status_disp,
    )


# ============================================================
# タブ4: 書き出し
# ============================================================

def create_export_tab():
    """書き出しタブのUIを定義する。"""

    with gr.Tab("書き出し"):
        gr.Markdown("## 書き出し")
        export_gallery = gr.Gallery(
            label="シーンサムネイル一覧",
            columns=8,
            height=300,
        )
        export_refresh_btn = gr.Button("サムネイル更新")
        with gr.Row():
            export_with_music = gr.Checkbox(label="音楽を合成する", value=True)
            export_btn = gr.Button("最終動画を書き出し（ffmpeg）", variant="primary")
        export_status = gr.Textbox(label="", interactive=False)
        export_video = gr.Video(label="最終動画プレビュー")

    return (
        export_gallery, export_refresh_btn,
        export_with_music, export_btn, export_status, export_video,
    )


# ============================================================
# シーン表示ヘルパー
# ============================================================

def _scene_to_plan_values(scene: Scene) -> tuple:
    """SceneオブジェクトをPlanタブの各コンポーネント値に変換する。"""
    return (
        scene.scene_id,
        f"{scene.start_time:.1f}s - {scene.end_time:.1f}s",
        scene.section,
        scene.lyrics,
        scene.plot,
        scene.image_prompt,
        scene.image_negative,
        scene.video_prompt,
        scene.video_negative,
        scene.image_workflow or "",
        scene.video_workflow or "",
        scene.notes,
    )

def _scene_to_gen_values(scene: Scene, proj: Project) -> tuple:
    """SceneオブジェクトをGenerateタブの各コンポーネント値に変換する。"""
    scene_dir = proj.scene_dir(scene.scene_id)
    img_path = scene.image_path(scene_dir)
    vid_path = scene.video_path(scene_dir)
    return (
        scene.scene_id,
        f"{scene.start_time:.1f}s - {scene.end_time:.1f}s",
        str(img_path) if img_path.exists() else None,
        str(vid_path) if vid_path.exists() else None,
        scene.image_prompt,
        scene.image_negative,
        scene.video_prompt,
        scene.video_negative,
        scene.image_seed,
        scene.video_seed,
        scene.image_workflow or "",
        scene.video_workflow or "",
        scene.status,
    )

def _build_scene_samples(scenes: list[Scene]) -> list[list[str]]:
    """Dataset用のサンプルリストを生成する。"""
    return [[_scene_status_label(s)] for s in scenes]


# ============================================================
# タブ5: モデル管理
# ============================================================

def create_model_tab():
    """モデル管理タブのUIを定義する。"""

    with gr.Tab("モデル管理"):
        gr.Markdown("## ローカル LLM 管理（Qwen3-VL）")
        gr.Markdown(
            "モデルは HuggingFace Hub から自動ダウンロードされ、`./models/` フォルダにキャッシュされます。"
            "初回は数GB のダウンロードが発生します。ロード中はUIが応答しません。"
        )

        with gr.Row():
            model_dropdown = gr.Dropdown(
                label="モデル選択",
                choices=list(model_manager.MODEL_PRESETS.keys()),
                value=list(model_manager.MODEL_PRESETS.keys())[0],
                scale=3,
            )
            model_load_btn = gr.Button("ロード", variant="primary", scale=1)
            model_unload_btn = gr.Button("アンロード", scale=1)

        model_status = gr.Textbox(
            label="モデルステータス",
            value="未ロード",
            interactive=False,
        )
        model_vram = gr.Textbox(
            label="VRAM 使用状況",
            value="",
            interactive=False,
        )

        gr.Markdown("---")
        gr.Markdown("### モデル一覧")
        gr.Dataframe(
            value=[
                ["qwen3-vl-4b (推奨)", "Qwen/Qwen3-VL-4B-Instruct", "~8.3GB", "~6GB VRAM", "公式・軽量・推奨"],
                ["qwen3-vl-8b (高性能)", "Qwen/Qwen3-VL-8B-Instruct", "~16GB", "~10GB VRAM", "公式・高性能"],
                ["huihui-qwen3-vl-4b-abliterated", "huihui-ai/Huihui-Qwen3-VL-4B-Instruct-abliterated", "~8.3GB", "~6GB VRAM", "検閲除去版・4B"],
                ["huihui-qwen3-vl-8b-abliterated", "huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated", "~16GB", "~10GB VRAM", "検閲除去版・8B"],
            ],
            headers=["表示名", "HuggingFace ID", "ディスク容量", "必要VRAM", "説明"],
            interactive=False,
            wrap=True,
        )

        gr.Markdown(
            "> **注意**: ローカルモデルをロードすると、計画タブの LLM 機能はローカルモデルを使用します。"
            " アンロード後は config.yaml の LLM URL 設定（API モード）にフォールバックします。"
        )

    return model_dropdown, model_load_btn, model_unload_btn, model_status, model_vram


# ============================================================
# LLM 呼び出しヘルパー（ローカル優先、API フォールバック）
# ============================================================

def _llm_chat(messages: list[dict], proj: Optional[Project]) -> str:
    """ローカルモデルが loaded なら使用し、そうでなければ API を呼ぶ。"""
    return "".join(_llm_chat_stream(messages, proj))


def _llm_chat_stream(messages: list[dict], proj: Optional[Project]):
    """ローカルモデル優先でストリーミングチャットを返すジェネレータ。"""
    if model_manager.is_loaded():
        yield from model_manager.chat_stream(messages)
        return
    # API フォールバック
    llm_url = proj.llm_url if proj else _cfg.get("llm", {}).get("url", "http://localhost:11434/v1")
    llm_model = _cfg.get("llm", {}).get("model", "qwen3-vl")
    client = LLMClient(base_url=llm_url, model=llm_model)
    yield from client.chat_stream(messages)


def _llm_bulk(concept, lyrics, scene_count, scene_duration, refs, proj) -> list[dict]:
    """全シーン一括提案をローカル or API で実行する。"""
    if model_manager.is_loaded():
        return model_manager.generate_all_scene_prompts(
            concept=concept,
            lyrics=lyrics,
            scene_count=scene_count,
            scene_duration=scene_duration,
            reference_images=refs if refs else None,
        )
    llm_url = proj.llm_url if proj else _cfg.get("llm", {}).get("url", "http://localhost:11434/v1")
    llm_model = _cfg.get("llm", {}).get("model", "qwen3-vl")
    client = LLMClient(base_url=llm_url, model=llm_model)
    return client.generate_all_scene_prompts(
        concept=concept,
        lyrics=lyrics,
        scene_count=scene_count,
        scene_duration=scene_duration,
        reference_images=refs if refs else None,
    )


def _llm_improve(scene_data, concept, refs, proj) -> dict:
    """個別シーン改善をローカル or API で実行する。"""
    if model_manager.is_loaded():
        return model_manager.improve_scene_prompt(
            scene_data=scene_data,
            concept=concept,
            reference_images=refs if refs else None,
        )
    llm_url = proj.llm_url if proj else _cfg.get("llm", {}).get("url", "http://localhost:11434/v1")
    llm_model = _cfg.get("llm", {}).get("model", "qwen3-vl")
    client = LLMClient(base_url=llm_url, model=llm_model)
    return client.improve_scene_prompt(
        scene_data=scene_data,
        concept=concept,
        reference_images=refs if refs else None,
    )


# ============================================================
# メインアプリ構築
# ============================================================

def _settings_to_cfg_values(s: dict) -> tuple:
    """settings 辞書をプロジェクトタブの cfg_* コンポーネント値のタプルに変換する。"""
    return (
        s.get("comfyui_url", settings_manager.DEFAULT_SETTINGS["comfyui_url"]),
        s.get("resolution_w", settings_manager.DEFAULT_SETTINGS["resolution_w"]),
        s.get("resolution_h", settings_manager.DEFAULT_SETTINGS["resolution_h"]),
        s.get("fps", settings_manager.DEFAULT_SETTINGS["fps"]),
        s.get("image_workflow", settings_manager.DEFAULT_SETTINGS["image_workflow"]),
        s.get("video_workflow", settings_manager.DEFAULT_SETTINGS["video_workflow"]),
        s.get("model", settings_manager.DEFAULT_SETTINGS["model"]),
    )


def build_app() -> gr.Blocks:
    """Gradioアプリを構築して返す。"""

    with gr.Blocks(title="Music Video Generator") as demo:
        # グローバルState
        project_state = gr.State(None)   # {"project_name": str}
        current_scene_idx = gr.State(0)  # 0-based index

        gr.Markdown("# Music Video Generator")

        # ---- タブUI構築 ----
        (
            new_name, new_music, new_scene_dur, new_create_btn, new_status,
            load_dropdown, load_refresh_btn, load_btn, load_status,
            cfg_comfyui_url, cfg_res_w, cfg_res_h, cfg_fps,
            cfg_img_wf, cfg_vid_wf, cfg_img_wf_refresh, cfg_vid_wf_refresh,
            save_cfg_btn, save_cfg_status,
        ) = create_project_tab()

        (
            plan_scene_btns, plan_prev_btn, plan_next_btn,
            plan_chatbot, plan_chat_input, plan_chat_send,
            plan_concept_input, plan_lyrics_input, plan_bulk_btn, plan_bulk_status,
            plan_scene_id_disp, plan_time_disp, plan_section, plan_lyrics,
            plan_plot, plan_img_prompt, plan_img_neg, plan_vid_prompt, plan_vid_neg,
            plan_img_wf, plan_vid_wf,
            plan_notes, plan_save_btn, plan_consult_btn, plan_save_status,
        ) = create_plan_tab()

        (
            gen_scene_btns, gen_prev_btn, gen_next_btn,
            gen_batch_btn, gen_stop_btn, gen_progress,
            gen_scene_id_disp, gen_time_disp,
            gen_image_preview, gen_video_preview,
            gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
            gen_img_seed, gen_vid_seed,
            gen_img_wf, gen_vid_wf,
            gen_regen_img_btn, gen_regen_vid_btn, gen_regen_both_btn, gen_save_btn,
            gen_status_disp,
        ) = create_generate_tab()

        (
            export_gallery, export_refresh_btn,
            export_with_music, export_btn, export_status, export_video,
        ) = create_export_tab()

        (
            model_dropdown, model_load_btn, model_unload_btn, model_status, model_vram,
        ) = create_model_tab()


        # ============================================================
        # 起動時: 最後に開いたプロジェクトをドロップダウンに反映
        # ============================================================

        def on_app_load():
            """アプリ起動時に最後のプロジェクト選択を復元し、保存済みモデルを自動ロードする。"""
            last = settings_manager.get_last_project()
            projects = list_projects(BASE_DIR)
            dropdown_update = (
                gr.update(choices=projects, value=last)
                if last and last in projects
                else gr.update(choices=projects)
            )

            # 前回のプロジェクトから保存済みモデルラベルを取得
            model_label = None
            if last and last in projects:
                s = settings_manager.load(BASE_DIR / last)
                model_label = s.get("model")

            if not model_label or model_label not in model_manager.MODEL_PRESETS:
                return dropdown_update, gr.update(), "未ロード"

            model_id = model_manager.MODEL_PRESETS[model_label]

            def _auto_load():
                try:
                    model_manager.load_model(model_id)
                except Exception:
                    pass

            threading.Thread(target=_auto_load, daemon=True).start()
            return dropdown_update, gr.update(value=model_label), f"自動ロード中: {model_label} ..."

        demo.load(fn=on_app_load, outputs=[load_dropdown, model_dropdown, model_status])

        # ワークフロー一覧更新ボタン
        cfg_img_wf_refresh.click(
            fn=lambda: gr.update(choices=_list_image_workflows()),
            outputs=[cfg_img_wf],
        )
        cfg_vid_wf_refresh.click(
            fn=lambda: gr.update(choices=_list_video_workflows()),
            outputs=[cfg_vid_wf],
        )

        # ============================================================
        # イベントハンドラ: プロジェクトタブ
        # ============================================================

        # settings 読み書きで共通する cfg 出力コンポーネントリスト
        _cfg_outputs = [
            cfg_comfyui_url, cfg_res_w, cfg_res_h, cfg_fps,
            cfg_img_wf, cfg_vid_wf, model_dropdown,
        ]

        def on_create_project(name, music_path, scene_dur, comfyui_url, res_w, res_h, fps, img_wf, vid_wf, model):
            """新規プロジェクトを作成する。"""
            _no_cfg = (gr.update(),) * 7
            if not name:
                return gr.update(), gr.update(), "プロジェクト名を入力してください", None, 0, *_no_cfg
            if not music_path:
                return gr.update(), gr.update(), "音楽ファイルをアップロードしてください", None, 0, *_no_cfg

            try:
                duration = _get_audio_duration(music_path)
            except Exception as e:
                return gr.update(), gr.update(), f"音楽ファイルエラー: {e}", None, 0, *_no_cfg

            BASE_DIR.mkdir(parents=True, exist_ok=True)
            proj = Project(
                project_name=name,
                base_dir=BASE_DIR,
                duration=duration,
                scene_duration=int(scene_dur),
                resolution={"width": int(res_w), "height": int(res_h)},
                fps=int(fps),
                comfyui_url=comfyui_url,
                image_workflow=img_wf,
                video_workflow=vid_wf,
            )
            proj.initialize_dirs()
            proj.copy_music(music_path)
            proj.setup_scenes()
            proj.save()

            # settings.json を保存
            settings_manager.save(proj.project_dir, {
                "comfyui_url": comfyui_url,
                "image_workflow": img_wf,
                "video_workflow": vid_wf,
                "resolution_w": int(res_w),
                "resolution_h": int(res_h),
                "fps": int(fps),
                "scene_duration": int(scene_dur),
                "model": model,
            })
            settings_manager.save_last_project(name)

            state = {"project_name": name}
            samples = _build_scene_samples(proj.scenes)
            msg = f"プロジェクト '{name}' を作成しました（{len(proj.scenes)}シーン, {duration:.1f}秒）"
            s = settings_manager.load(proj.project_dir)
            return samples, samples, msg, state, 0, *_settings_to_cfg_values(s)

        new_create_btn.click(
            fn=on_create_project,
            inputs=[
                new_name, new_music, new_scene_dur,
                cfg_comfyui_url, cfg_res_w, cfg_res_h, cfg_fps,
                cfg_img_wf, cfg_vid_wf, model_dropdown,
            ],
            outputs=[plan_scene_btns, gen_scene_btns, new_status, project_state, current_scene_idx,
                     *_cfg_outputs],
        )

        def on_load_refresh():
            return gr.update(choices=list_projects(BASE_DIR))

        load_refresh_btn.click(fn=on_load_refresh, outputs=[load_dropdown])

        def on_load_project(name):
            """既存プロジェクトを読み込む。settings.json から UI パラメータを復元する。"""
            _no_cfg = (gr.update(),) * 7
            if not name:
                return gr.update(), gr.update(), "プロジェクトを選択してください", None, 0, *_no_cfg
            try:
                proj = Project.load(BASE_DIR / name)
            except Exception as e:
                return gr.update(), gr.update(), f"読込エラー: {e}", None, 0, *_no_cfg

            # settings.json 読み込み
            s = settings_manager.load(proj.project_dir)
            settings_manager.save_last_project(name)

            samples = _build_scene_samples(proj.scenes)
            state = {"project_name": name}
            msg = f"プロジェクト '{name}' を読み込みました（{len(proj.scenes)}シーン）"
            return samples, samples, msg, state, 0, *_settings_to_cfg_values(s)

        load_btn.click(
            fn=on_load_project,
            inputs=[load_dropdown],
            outputs=[plan_scene_btns, gen_scene_btns, load_status, project_state, current_scene_idx,
                     *_cfg_outputs],
        )

        def on_save_config(comfyui_url, res_w, res_h, fps, img_wf, vid_wf, state):
            """設定を config.yaml とプロジェクトの settings.json に保存する。"""
            try:
                # config.yaml（グローバルデフォルト）
                cfg = _load_config()
                cfg.setdefault("comfyui", {})["url"] = comfyui_url
                cfg.setdefault("comfyui", {})["image_workflow"] = img_wf
                cfg.setdefault("comfyui", {})["video_workflow"] = vid_wf
                cfg.setdefault("defaults", {})["resolution"] = {"width": int(res_w), "height": int(res_h)}
                cfg.setdefault("defaults", {})["fps"] = int(fps)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, allow_unicode=True)

                # プロジェクトの settings.json（プロジェクトが読み込まれている場合）
                proj = _project_from_state(state)
                if proj:
                    settings_manager.save(proj.project_dir, {
                        "comfyui_url": comfyui_url,
                        "image_workflow": img_wf,
                        "video_workflow": vid_wf,
                        "resolution_w": int(res_w),
                        "resolution_h": int(res_h),
                        "fps": int(fps),
                    })
                    return "設定を保存しました（config.yaml + settings.json）"
                return "設定を保存しました（config.yaml）"
            except Exception as e:
                return f"保存エラー: {e}"

        save_cfg_btn.click(
            fn=on_save_config,
            inputs=[cfg_comfyui_url, cfg_res_w, cfg_res_h, cfg_fps, cfg_img_wf, cfg_vid_wf,
                    project_state],
            outputs=[save_cfg_status],
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - シーン切替
        # ============================================================

        plan_scene_outputs = [
            plan_scene_id_disp, plan_time_disp, plan_section, plan_lyrics,
            plan_plot, plan_img_prompt, plan_img_neg, plan_vid_prompt, plan_vid_neg,
            plan_img_wf, plan_vid_wf,
            plan_notes, current_scene_idx,
        ]

        def load_plan_scene(idx: int, state: dict) -> tuple:
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return (None, "", "", "", "", "", "", "", "", "", "", "", idx)
            idx = max(0, min(idx, len(proj.scenes) - 1))
            scene = proj.scenes[idx]
            return _scene_to_plan_values(scene) + (idx,)

        plan_scene_btns.click(
            fn=lambda evt, state: load_plan_scene(evt, state),
            inputs=[plan_scene_btns, project_state],
            outputs=plan_scene_outputs,
        )

        plan_prev_btn.click(
            fn=lambda idx, state: load_plan_scene(idx - 1, state),
            inputs=[current_scene_idx, project_state],
            outputs=plan_scene_outputs,
        )

        plan_next_btn.click(
            fn=lambda idx, state: load_plan_scene(idx + 1, state),
            inputs=[current_scene_idx, project_state],
            outputs=plan_scene_outputs,
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - 保存
        # ============================================================

        def on_plan_save(idx, state, scene_id, section, lyrics, plot,
                         img_p, img_n, vid_p, vid_n, img_wf, vid_wf, notes):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません", gr.update()
            scene = proj.scenes[idx]
            scene.section = section
            scene.lyrics = lyrics
            scene.plot = plot
            scene.image_prompt = img_p
            scene.image_negative = img_n
            scene.video_prompt = vid_p
            scene.video_negative = vid_n
            scene.image_workflow = img_wf or None
            scene.video_workflow = vid_wf or None
            scene.notes = notes
            if scene.status == "empty" and plot:
                scene.status = "plot_done"
            proj.save_scene(scene)
            samples = _build_scene_samples(proj.scenes)
            return f"シーン {scene.scene_id} を保存しました", gr.update(samples=samples)

        plan_save_btn.click(
            fn=on_plan_save,
            inputs=[
                current_scene_idx, project_state,
                plan_scene_id_disp, plan_section, plan_lyrics, plan_plot,
                plan_img_prompt, plan_img_neg, plan_vid_prompt, plan_vid_neg,
                plan_img_wf, plan_vid_wf, plan_notes,
            ],
            outputs=[plan_save_status, plan_scene_btns],
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - LLMチャット
        # ============================================================

        def on_chat_send(user_msg: str, history: list, state: dict):
            """ストリーミングでチャット応答を返すジェネレータ。"""
            if not user_msg.strip():
                yield history or [], ""
                return
            proj = _project_from_state(state)
            history = list(history or [])
            history.append({"role": "user", "content": user_msg})
            history.append({"role": "assistant", "content": ""})
            # ユーザーメッセージを即座に反映し、入力欄をクリア
            yield history, ""
            try:
                messages = [{"role": m["role"], "content": m["content"]} for m in history[:-1]]
                for chunk in _llm_chat_stream(messages, proj):
                    history[-1]["content"] += chunk
                    yield history, ""
            except Exception as e:
                history[-1]["content"] = f"LLM接続エラー: {e}"
                yield history, ""

        plan_chat_send.click(
            fn=on_chat_send,
            inputs=[plan_chat_input, plan_chatbot, project_state],
            outputs=[plan_chatbot, plan_chat_input],
        )
        plan_chat_input.submit(
            fn=on_chat_send,
            inputs=[plan_chat_input, plan_chatbot, project_state],
            outputs=[plan_chatbot, plan_chat_input],
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - 全シーン一括提案
        # ============================================================

        def on_bulk_generate(concept: str, lyrics: str, state: dict):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません", gr.update()
            if not concept:
                return "コンセプトを入力してください", gr.update()

            try:
                refs = list(proj.references_dir.glob("*.png"))[:4]
                results = _llm_bulk(
                    concept=concept,
                    lyrics=lyrics,
                    scene_count=len(proj.scenes),
                    scene_duration=proj.scene_duration,
                    refs=refs,
                    proj=proj,
                )
            except Exception as e:
                return f"LLMエラー: {e}", gr.update()

            # 結果をシーンに反映して保存
            updated = 0
            for item in results:
                sid = item.get("scene_id")
                if sid and 1 <= sid <= len(proj.scenes):
                    scene = proj.scenes[sid - 1]
                    scene.section = item.get("section", scene.section)
                    scene.lyrics = item.get("lyrics", scene.lyrics)
                    scene.plot = item.get("plot", scene.plot)
                    scene.image_prompt = item.get("image_prompt", scene.image_prompt)
                    scene.image_negative = item.get("image_negative", scene.image_negative)
                    scene.video_prompt = item.get("video_prompt", scene.video_prompt)
                    scene.video_negative = item.get("video_negative", scene.video_negative)
                    if scene.status == "empty" and scene.plot:
                        scene.status = "plot_done"
                    proj.save_scene(scene)
                    updated += 1

            # コンセプト保存
            proj.concept = concept
            proj.save()

            samples = _build_scene_samples(proj.scenes)
            return f"{updated}シーンのプロンプトを更新しました", gr.update(samples=samples)

        plan_bulk_btn.click(
            fn=on_bulk_generate,
            inputs=[plan_concept_input, plan_lyrics_input, project_state],
            outputs=[plan_bulk_status, plan_scene_btns],
        )


        # ============================================================
        # イベントハンドラ: 計画タブ - 個別シーン相談
        # ============================================================

        def on_consult_scene(idx, state, concept, plot, img_p, img_n, vid_p, vid_n, history):
            proj = _project_from_state(state)
            if proj is None:
                return history, "プロジェクトが読み込まれていません"
            scene = proj.scenes[idx]
            scene_data = {
                "scene_id": scene.scene_id,
                "section": scene.section,
                "lyrics": scene.lyrics,
                "plot": plot,
                "image_prompt": img_p,
                "image_negative": img_n,
                "video_prompt": vid_p,
                "video_negative": vid_n,
            }
            try:
                refs = list(proj.references_dir.glob("*.png"))[:2]
                improved = _llm_improve(
                    scene_data=scene_data,
                    concept=concept or proj.concept,
                    refs=refs,
                    proj=proj,
                )
            except Exception as e:
                return history, f"LLMエラー: {e}"

            msg = f"シーン {scene.scene_id} の改善案:\n```json\n{improved}\n```"
            history = history or []
            history.append({"role": "assistant", "content": msg})
            return history, "改善案をチャットに表示しました"

        plan_consult_btn.click(
            fn=on_consult_scene,
            inputs=[
                current_scene_idx, project_state, plan_concept_input,
                plan_plot, plan_img_prompt, plan_img_neg, plan_vid_prompt, plan_vid_neg,
                plan_chatbot,
            ],
            outputs=[plan_chatbot, plan_save_status],
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - シーン切替
        # ============================================================

        gen_scene_outputs = [
            gen_scene_id_disp, gen_time_disp,
            gen_image_preview, gen_video_preview,
            gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
            gen_img_seed, gen_vid_seed,
            gen_img_wf, gen_vid_wf,
            gen_status_disp, current_scene_idx,
        ]

        def load_gen_scene(idx: int, state: dict) -> tuple:
            proj = _project_from_state(state)
            if proj is None or not proj.scenes:
                return (None, "", None, None, "", "", "", "", -1, -1, "", "", "", idx)
            idx = max(0, min(idx, len(proj.scenes) - 1))
            scene = proj.scenes[idx]
            return _scene_to_gen_values(scene, proj) + (idx,)

        gen_scene_btns.click(
            fn=lambda evt, state: load_gen_scene(evt, state),
            inputs=[gen_scene_btns, project_state],
            outputs=gen_scene_outputs,
        )
        gen_prev_btn.click(
            fn=lambda idx, state: load_gen_scene(idx - 1, state),
            inputs=[current_scene_idx, project_state],
            outputs=gen_scene_outputs,
        )
        gen_next_btn.click(
            fn=lambda idx, state: load_gen_scene(idx + 1, state),
            inputs=[current_scene_idx, project_state],
            outputs=gen_scene_outputs,
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 保存
        # ============================================================

        def on_gen_save(idx, state, img_p, img_n, vid_p, vid_n, img_seed, vid_seed, img_wf, vid_wf):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません", gr.update()
            scene = proj.scenes[idx]
            scene.image_prompt = img_p
            scene.image_negative = img_n
            scene.video_prompt = vid_p
            scene.video_negative = vid_n
            scene.image_seed = int(img_seed)
            scene.video_seed = int(vid_seed)
            scene.image_workflow = img_wf or None
            scene.video_workflow = vid_wf or None
            proj.save_scene(scene)
            samples = _build_scene_samples(proj.scenes)
            return f"シーン {scene.scene_id} を保存しました", gr.update(samples=samples)

        gen_save_btn.click(
            fn=on_gen_save,
            inputs=[
                current_scene_idx, project_state,
                gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
                gen_img_seed, gen_vid_seed, gen_img_wf, gen_vid_wf,
            ],
            outputs=[gen_status_disp, gen_scene_btns],
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 個別再生成
        # ============================================================

        def _get_comfyui(proj: Project) -> ComfyUIClient:
            return ComfyUIClient(base_url=proj.comfyui_url)

        def on_regen(idx, state, target, img_p, img_n, vid_p, vid_n, img_seed, vid_seed, img_wf, vid_wf):
            proj = _project_from_state(state)
            if proj is None:
                return None, None, "プロジェクトが読み込まれていません", gr.update()
            comfyui = _get_comfyui(proj)
            if not comfyui.is_available():
                return None, None, "ComfyUIに接続できません", gr.update()

            scene = proj.scenes[idx]
            scene.image_prompt = img_p
            scene.image_negative = img_n
            scene.video_prompt = vid_p
            scene.video_negative = vid_n
            scene.image_seed = int(img_seed)
            scene.video_seed = int(vid_seed)
            scene.image_workflow = img_wf or None
            scene.video_workflow = vid_wf or None
            proj.save_scene(scene)

            gen = BatchGenerator(proj, comfyui)
            try:
                gen.regenerate_scene(scene.scene_id, target=target)
            except Exception as e:
                return None, None, f"生成エラー: {e}", gr.update()

            updated = proj.scenes[idx]
            scene_dir = proj.scene_dir(updated.scene_id)
            img = updated.image_path(scene_dir)
            vid = updated.video_path(scene_dir)
            samples = _build_scene_samples(proj.scenes)
            return (
                str(img) if img.exists() else None,
                str(vid) if vid.exists() else None,
                updated.status,
                gr.update(samples=samples),
            )

        _regen_inputs = [
            current_scene_idx, project_state,
            gen_img_prompt, gen_img_neg, gen_vid_prompt, gen_vid_neg,
            gen_img_seed, gen_vid_seed, gen_img_wf, gen_vid_wf,
        ]
        gen_regen_img_btn.click(
            fn=lambda *a: on_regen(*a, target="image"),
            inputs=_regen_inputs,
            outputs=[gen_image_preview, gen_video_preview, gen_status_disp, gen_scene_btns],
        )
        gen_regen_vid_btn.click(
            fn=lambda *a: on_regen(*a, target="video"),
            inputs=_regen_inputs,
            outputs=[gen_image_preview, gen_video_preview, gen_status_disp, gen_scene_btns],
        )
        gen_regen_both_btn.click(
            fn=lambda *a: on_regen(*a, target="both"),
            inputs=_regen_inputs,
            outputs=[gen_image_preview, gen_video_preview, gen_status_disp, gen_scene_btns],
        )


        # ============================================================
        # イベントハンドラ: 生成・編集タブ - 一括生成
        # ============================================================

        def on_batch_start(state):
            global _batch_gen, _batch_log
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません"
            comfyui = _get_comfyui(proj)
            if not comfyui.is_available():
                return "ComfyUIに接続できません"

            with _batch_lock:
                _batch_log = []
                _batch_gen = BatchGenerator(proj, comfyui)

            def on_progress(sid, total, msg):
                with _batch_lock:
                    _batch_log.append(msg)
                    if len(_batch_log) > 20:
                        _batch_log.pop(0)

            def on_error(sid, msg):
                with _batch_lock:
                    _batch_log.append(f"[ERROR] {msg}")

            _batch_gen.run_async(on_progress=on_progress, on_error=on_error)
            return "一括生成を開始しました"

        def on_batch_stop():
            global _batch_gen
            if _batch_gen:
                _batch_gen.stop()
                return "停止リクエストを送信しました"
            return "実行中の生成はありません"

        def on_batch_progress_poll():
            with _batch_lock:
                return "\n".join(_batch_log[-10:]) if _batch_log else "待機中..."

        gen_batch_btn.click(fn=on_batch_start, inputs=[project_state], outputs=[gen_progress])
        gen_stop_btn.click(fn=on_batch_stop, outputs=[gen_progress])


        # ============================================================
        # イベントハンドラ: 書き出しタブ
        # ============================================================

        def on_export_refresh(state):
            proj = _project_from_state(state)
            if proj is None:
                return []
            exporter = VideoExporter(proj)
            thumbs = exporter.get_scene_thumbnails()
            return [str(p) for _, p in thumbs if p is not None]

        export_refresh_btn.click(
            fn=on_export_refresh,
            inputs=[project_state],
            outputs=[export_gallery],
        )

        def on_export(state, with_music):
            proj = _project_from_state(state)
            if proj is None:
                return "プロジェクトが読み込まれていません", None
            try:
                exporter = VideoExporter(proj)
                out_path = exporter.export(with_music=with_music)
                return f"書き出し完了: {out_path}", str(out_path)
            except Exception as e:
                return f"書き出しエラー: {e}", None

        export_btn.click(
            fn=on_export,
            inputs=[project_state, export_with_music],
            outputs=[export_status, export_video],
        )


        # ============================================================
        # イベントハンドラ: モデル管理タブ
        # ============================================================

        def on_model_load(model_label: str, state: dict):
            """選択したモデルをロードし、settings.json にモデル選択を保存する。"""
            model_id = model_manager.MODEL_PRESETS.get(model_label, model_label)
            try:
                msg = model_manager.load_model(model_id)
            except RuntimeError as e:
                return str(e), ""
            # プロジェクトが開いていれば settings.json にモデル選択を保存
            proj = _project_from_state(state)
            if proj:
                settings_manager.save(proj.project_dir, {"model": model_label})
            vram = model_manager.get_vram_info()
            return msg, vram

        def on_model_unload():
            """モデルをアンロードして VRAM を解放する。"""
            msg = model_manager.unload_model()
            vram = model_manager.get_vram_info()
            return msg, vram

        def on_model_vram_refresh():
            """VRAM 情報を更新する。"""
            loaded = model_manager.get_loaded_model_id()
            status = f"ロード済み: {loaded}" if loaded else "未ロード"
            return status, model_manager.get_vram_info()

        model_load_btn.click(
            fn=on_model_load,
            inputs=[model_dropdown, project_state],
            outputs=[model_status, model_vram],
        )
        model_unload_btn.click(
            fn=on_model_unload,
            outputs=[model_status, model_vram],
        )

        # 起動時の自動ロード完了を検知するポーリングタイマー（2秒ごと）
        gr.Timer(value=2.0, active=True).tick(
            fn=on_model_vram_refresh,
            outputs=[model_status, model_vram],
        )

    return demo


# ============================================================
# エントリポイント
# ============================================================

if __name__ == "__main__":
    app = build_app()
    app.queue()
    app.launch(share=False, server_name="0.0.0.0", inbrowser=True)
