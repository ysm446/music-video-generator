# MV Generator - CLAUDE.md

## プロジェクト概要

音楽ファイルをもとにミュージックビデオを生成するGradioアプリケーション。
LLM(Qwen3-VL)と対話しながらシーンを計画し、ComfyUIバックエンドで画像(z-image Turbo)と動画(WAN2.2)を自動生成する。

## 技術スタック

- **UI**: Gradio (Python)
- **LLM**: Qwen3-VL（OpenAI互換APIまたはローカルtransformers実行）
- **画像生成**: z-image Turbo（ComfyUI経由）
- **動画生成**: WAN2.2 img2video（ComfyUI経由）
- **動画結合**: ffmpeg
- **データ保存**: JSON + ローカルファイル

## アーキテクチャ

```
Gradio UI → Python Backend → ComfyUI API (localhost:8188)
                           → Qwen3-VL API (OpenAI互換 or transformers)
                           → ffmpeg (動画結合)
```

## ディレクトリ構造

### アプリケーション

```
music-video-generator/
├── CLAUDE.md
├── app.py                      # Gradioメインアプリ（薄いUI層）
├── config.yaml                 # 設定ファイル（API URL、デフォルトパラメータ等）
├── requirements.txt
├── src/
│   ├── project.py              # プロジェクト管理（作成・保存・読込）
│   ├── scene.py                # シーンデータ管理（Sceneデータクラス）
│   ├── llm_client.py           # Qwen3-VL OpenAI互換API連携
│   ├── model_manager.py        # Qwen3-VL ローカルtransformers実行
│   ├── settings_manager.py     # プロジェクト設定の保存・読込
│   ├── comfyui_client.py       # ComfyUI API連携（画像・動画生成）
│   ├── batch_generator.py      # 一括生成処理
│   └── video_export.py         # ffmpeg結合・書き出し
└── workflows/
    ├── image/
    │   └── image_z_image_turbo.json   # z-image Turbo用ワークフロー
    └── video/
        └── video_wan2_2_14B_i2v.json  # WAN2.2 img2video用ワークフロー
```

### プロジェクトデータ（実行時に生成）

デフォルトは `{アプリルート}/projects/` に作成される。
`config.yaml` の `project.base_dir` で変更可（相対パスはアプリルート基準、絶対パスはそのまま使用）。

```
projects/
└── {project_name}/
    ├── project.json          # プロジェクト全体メタデータ
    ├── settings.json         # UIパラメータ（URL、ワークフロー等）
    ├── music/
    │   └── song.mp3
    ├── scenes/
    │   ├── scene_001/
    │   │   ├── scene.json    # プロット、プロンプト、ステータス
    │   │   ├── image.png
    │   │   └── video.mp4
    │   └── scene_NNN/
    │       └── ...
    ├── references/           # スタイル参照画像
    └── output/
        └── final.mp4
```

## データモデル

### project.json

```json
{
  "project_name": "my_mv",
  "music_file": "music/song.mp3",
  "duration": 210,
  "scene_duration": 5,
  "scene_count": 42,
  "concept": "全体コンセプト",
  "resolution": {"width": 1280, "height": 720},
  "image_resolution": {"width": 1280, "height": 720},
  "video_resolution": {"width": 640, "height": 360},
  "video_fps": 16,
  "video_frame_count": 81,
  "fps": 16,
  "comfyui_url": "http://localhost:8188",
  "llm_url": "http://localhost:11434/v1",
  "image_workflow": "workflows/image/image_z_image_turbo.json",
  "video_workflow": "workflows/video/video_wan2_2_14B_i2v.json",
  "created_at": "2025-01-01T00:00:00",
  "updated_at": "2025-01-01T00:00:00"
}
```

### scene.json

```json
{
  "scene_id": 1,
  "start_time": 0.0,
  "end_time": 5.0,
  "order": 1,
  "enabled": true,
  "section": "intro",
  "lyrics": "該当部分の歌詞",
  "plot": "シーンの内容説明（日本語）",
  "image_prompt": "英語プロンプト",
  "image_negative": "英語ネガティブプロンプト",
  "image_seed": -1,
  "image_workflow": null,
  "video_prompt": "英語モーション指示",
  "video_negative": "英語ネガティブプロンプト",
  "video_seed": -1,
  "video_workflow": null,
  "video_instruction": "LLM動画プロンプト生成時の追加指示",
  "status": "empty",
  "notes": ""
}
```

### ステータス遷移

```
empty → plot_done → image_done → video_done
```

- `empty`: 未着手
- `plot_done`: プロット・プロンプト記入済み
- `image_done`: 画像生成完了
- `video_done`: 動画生成完了

### シードの扱い

- `image_seed` / `video_seed` が `-1` のときはランダム生成（ComfyUI側でも毎回異なるシードを注入）
- `image_seed` は生成済みPNGのtEXtメタデータ（`"prompt"` チャンク）から読み取り可能
- `video_seed` はscene.jsonから読み取る（MP4にはシード埋め込みなし）

## UI設計

### タブ構成

5タブ: **プロジェクト** / **計画** / **生成・編集** / **書き出し** / **モデル管理**

### 共通パターン: サイドバー + メインエリア

計画タブと生成・編集タブは左にサイドバー（シーン一覧ナビ）、右にメインエリア。

### ページ切り替え方式

`gr.State` で現在のシーン番号を管理。サイドバーの `gr.Dataset` クリック、または Prev/Next ボタンで `load_gen_scene()` / `load_plan_scene()` を呼び出し、メインエリアの全コンポーネント値をPython側で差し替える。

### 保存方式

**明示保存**。各シーン編集画面に「保存」ボタンを配置。保存ボタン押下時にscene.json（ディスク）に書き込む。`project_state` は `{"project_name": str}` のみ保持し、都度 `Project.load()` で復元する。

### プロジェクトタブ

- 新規プロジェクト作成（プロジェクト名、音楽アップロード → 長さ自動検出 → シーン数算出 → ディレクトリ生成）
- 既存プロジェクト読込（プロジェクト一覧から選択）
- 基本設定（ComfyUI URL、画像解像度、動画解像度、FPS、フレーム数、ワークフロー選択）
- 設定は `config.yaml` とプロジェクトの `project.json` / `settings.json` 両方に保存

### 計画タブ

- 上部: LLMとのチャットUI（コンセプト相談、全シーン一括提案ボタン）
- 下部: 選択中シーンの編集（セクション、歌詞、プロット、画像/動画プロンプト、ワークフロー指定、メモ）
- サイドバー: シーン番号 + ステータスアイコン一覧
- 「このシーンをQwenに相談」ボタンで個別プロンプト改善

### 生成・編集タブ

- サイドバー: シーン一覧 + 一括生成ボタン（開始/停止）+ 進捗表示
- メインエリア: 画像プレビュー + 動画プレビュー + サブタブ
  - **画像タブ**: プロンプト・ネガティブ・シード・ワークフロー + LLM相談チャット（プロンプト編集専用）
    - 🎲 シードをランダム(-1)に、♻️ 生成済みPNGからシード読み取り
    - LLMチャットは「編集タスク」として指示を受け付け、`[PROMPT_UPDATE]` ブロックで返答させて自動反映
  - **動画タブ**: プロンプト・ネガティブ・シード・ワークフロー + LLMによる動画プロンプト生成
    - 🎲/♻️ シードボタン（動画はscene.jsonからシード読み取り）
    - 追加指示テキストボックス（`video_instruction` としてscene.jsonに保存）+ 生成ボタン
    - 「生成」押下で現在の画像・画像プロンプト・追加指示をLLMに渡し、Scene/Action/Camera形式の動画プロンプトを生成
- 「有効にする」チェックボックス（無効シーンはバッチ生成・書き出しでスキップ）
- 個別再生成ボタン（画像のみ・動画のみ・両方）
- 保存ボタン（プロンプト・シード・ワークフロー・追加指示・有効フラグをscene.jsonに書き込み）

### 書き出しタブ

- シーンサムネイルギャラリー（画像一覧）
- 音楽合成チェックボックス
- 「最終動画を書き出し」ボタン（ffmpeg concat → 音楽合成）
- 最終動画プレビュー

### モデル管理タブ

- Qwen3-VL ローカルモデルのロード/アンロード（HuggingFaceから自動ダウンロード）
- モデルプリセット一覧（`model_manager.MODEL_PRESETS`）
- VRAM使用状況表示

## LLM連携の2モード

### OpenAI互換API（`llm_client.py`）

- `LLMClient(base_url, model)` で接続
- メッセージはOpenAI形式 `{"role": "user", "content": [{"type": "text"/"image_url", ...}]}`
- 参照画像はbase64エンコードして `image_url` 形式で渡す

### ローカルtransformers（`model_manager.py`）

- グローバルシングルトンとしてモデルを保持（`_model`, `_processor`）
- `is_loaded()` でロード状態確認
- メッセージ内の画像は `{"type": "image", "image": PIL.Image}` 形式
- `qwen_vl_utils.process_vision_info` が利用可能な場合はそちらで処理

### LLM使用箇所

| 用途 | 関数 | 備考 |
|------|------|------|
| コンセプト相談（チャット） | `chat_stream()` / `LLMClient.chat_stream()` | システムプロンプト: MVディレクター |
| 全シーン一括プロンプト生成 | `generate_all_scene_prompts()` | JSON配列を返す |
| 個別シーンのプロンプト改善 | `improve_scene_prompt()` | JSON辞書を返す |
| 画像プロンプト編集（生成・編集タブ） | `on_gen_img_chat_send()` | `[PROMPT_UPDATE]`ブロック形式 |
| 動画プロンプト生成（生成・編集タブ） | `on_gen_vid_consult()` | `[VIDEO_PROMPT_UPDATE]`ブロック形式 |

## ComfyUI連携（`comfyui_client.py`）

- ComfyUI APIモード（`--listen`）で起動前提
- ワークフローJSONは `workflows/image/` または `workflows/video/` に配置
- 相対パスはアプリルート（`Path(__file__).parent.parent`）基準で解決
- パラメータ注入: `_inject_image_params()` / `_inject_video_params()` でノードのclass_typeを見て自動注入
- シードが `-1` の場合は `random.randint(0, 2**32-1)` で乱数を生成して注入（ワークフロー内の固定シードを上書き）
- 生成完了はポーリング（`/history/{prompt_id}`）で確認
- 出力ファイルは `/view` APIでダウンロード、失敗時はローカルoutputディレクトリからコピー

## 一括生成（`batch_generator.py`）

- シーンを順番に処理: 画像生成 → 動画生成 → 次のシーンへ
- `status` が `video_done` のシーンはスキップ（再開対応）
- 途中停止フラグで中断可能
- 個別再生成: `target="image"/"video"/"both"` で指定

## 書き出し（`video_export.py`）

- 有効シーンかつ `video.mp4` が存在しサイズ > 0 のものだけ連結
- 動画が一部しかなくても書き出し可能（存在するシーンのみ連結）
- 音楽は `-shortest` で動画長さに合わせてトリム
- concat用一時ファイルは `tempfile.NamedTemporaryFile(delete=False)` で作成し処理後削除

## パス管理の注意点

- `app.py` では `_APP_DIR = Path(__file__).parent.resolve()` でアプリルートを取得
- `CONFIG_PATH`, `_WORKFLOWS_DIR`, `BASE_DIR` はすべて `_APP_DIR` 基準の絶対パスで管理
- これはGradioのコールバック実行時にCWDが一時ディレクトリに変わる問題への対策
- `settings_manager.py` の `_ROOT_SETTINGS_PATH` は相対パス（要注意）

## コーディング規約

- Python 3.10+
- 型ヒント使用
- docstring必須（日本語可）
- Gradioのイベントハンドラは関数を分離し、app.pyは薄く保つ
- エラーハンドリング: ComfyUI/LLM接続失敗時はUI上にエラーメッセージ表示
- コメントは日本語OK
- `gr.Markdown` は `scale` パラメータ非対応（`gr.Column(scale=N)` でラップする）
