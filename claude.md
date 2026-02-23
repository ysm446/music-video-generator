# MV Generator - CLAUDE.md

## プロジェクト概要

音楽ファイルをもとに3〜4分のミュージックビデオを生成するGradioアプリケーション。
LLM(Qwen3-VL)と対話しながらシーンを計画し、ComfyUIバックエンドで画像(z-image Turbo)と動画(WAN2.2)を自動生成する。

## 技術スタック

- **UI**: Gradio (Python)
- **LLM**: Qwen3-VL（ローカル実行、OpenAI互換API）
- **画像生成**: z-image Turbo（ComfyUI経由）
- **動画生成**: WAN2.2 img2video（ComfyUI経由）
- **動画結合**: ffmpeg
- **データ保存**: JSON + ローカルファイル

細かい仕様は以前作成したここを参考にし、これを拡張する形にしたい
https://github.com/ysm446/prompt-assistant

## アーキテクチャ

```
Gradio UI → Python Backend → ComfyUI API (localhost:8188)
                           → Qwen3-VL API (OpenAI互換)
                           → ffmpeg (動画結合)
```

## ディレクトリ構造

### アプリケーション

```
mv-generator/
├── CLAUDE.md
├── app.py                  # Gradioメインアプリ
├── requirements.txt
├── src/
│   ├── project.py          # プロジェクト管理（作成・保存・読込）
│   ├── scene.py            # シーンデータ管理
│   ├── llm_client.py       # Qwen3-VL API連携
│   ├── comfyui_client.py   # ComfyUI API連携（画像・動画生成）
│   ├── batch_generator.py  # 一括生成処理
│   └── video_export.py     # ffmpeg結合・書き出し
├── workflows/
│   ├── zimage_turbo.json   # z-image Turbo用ComfyUIワークフロー
│   └── wan22_i2v.json      # WAN2.2 img2video用ComfyUIワークフロー
└── config.yaml             # 設定ファイル（API URL、デフォルトパラメータ等）
```

### プロジェクトデータ（実行時に生成）

```
projects/
└── {project_name}/
    ├── project.json          # プロジェクト全体メタデータ
    ├── music/
    │   └── song.mp3
    ├── scenes/
    │   ├── scene_001/
    │   │   ├── scene.json    # プロット、プロンプト、ステータス
    │   │   ├── image.png
    │   │   └── video.mp4
    │   ├── scene_002/
    │   │   └── ...
    │   └── scene_NNN/
    │       └── ...
    ├── references/            # スタイル参照画像
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
  "fps": 16,
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
  "section": "intro",
  "lyrics": "該当部分の歌詞",
  "plot": "シーンの内容説明（日本語）",
  "image_prompt": "英語プロンプト",
  "image_negative": "英語ネガティブプロンプト",
  "image_seed": -1,
  "video_prompt": "英語モーション指示",
  "video_negative": "英語ネガティブプロンプト",
  "video_seed": -1,
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

## UI設計

### タブ構成

4タブ: **プロジェクト** / **計画** / **生成・編集** / **書き出し**

### 共通パターン: サイドバー + メインエリア

計画タブと生成・編集タブは左にサイドバー（シーン一覧ナビ）、右にメインエリア。

### ページ切り替え方式

`gr.State`で現在のシーン番号を管理。サイドバーのボタンまたはPrev/Nextボタンのクリックで、メインエリアの全コンポーネントの値をPython側で差し替える。36〜48シーンを1ページずつ表示する。

### 保存方式

**明示保存**。各シーン編集画面に「保存」ボタンを配置。保存ボタン押下時にproject_data(gr.State)とscene.json(ディスク)の両方に書き込む。

### プロジェクトタブ

- 新規プロジェクト作成（プロジェクト名入力、音楽アップロード → 長さ自動検出 → シーン数算出 → ディレクトリ生成）
- 既存プロジェクト読込（プロジェクトフォルダ一覧から選択）
- プロジェクト保存
- 基本設定（解像度、FPS、ComfyUI URL、LLM URL、comfyUI用の画像workflow.json、動画workflow.json）

### 計画タブ

- 上部: Qwen3-VLとのチャットUI（コンセプト相談、全シーン一括提案ボタン）
- 下部: 選択中シーンの編集（プロット、画像プロンプト、動画プロンプト）
- サイドバー: シーン番号ボタン一覧（ステータスアイコン付き）
- 「このシーンをQwenに相談」ボタンで個別プロンプト改善

### 生成・編集タブ

- サイドバー: シーン一覧 + 一括生成ボタン（開始/停止）+ 進捗表示
- メインエリア: 画像プレビュー + 動画プレビュー + プロンプト編集 + 個別再生成ボタン
- 一括生成: 全シーンを順番に画像→動画と処理。途中停止・再開可能（statusで管理）

### 書き出しタブ

- 全シーンサムネイル一覧（完了/未完了表示）
- ffmpeg結合実行ボタン
- 最終動画プレビュー

## 一括生成の仕様

- シーンを順番に処理: 画像生成 → 動画生成 → 次のシーンへ
- `status`が`video_done`のシーンはスキップ
- 途中停止フラグで中断可能。再開時はstatusを見て続きから
- 個別再生成: statusを戻して該当シーンだけ再実行
- ComfyUI APIのqueue_promptでジョブ投入、ポーリングで完了待ち

## ComfyUI連携

- ComfyUI APIモード（`--listen`）で起動前提
- ワークフローJSONをテンプレートとして保持
- プロンプト・シード等のパラメータをノードに注入してqueue_prompt
- 画像生成: z-image Turboワークフロー
- 動画生成: WAN2.2 img2videoワークフロー（生成済み画像を入力）
- 生成結果はComfyUIのoutputから取得し、プロジェクトフォルダにコピー

## Qwen3-VL連携

- OpenAI互換APIで接続（ローカルサーバー）
- 用途1: コンセプト相談（チャット形式）
- 用途2: 全シーン一括プロンプト生成（楽曲情報+歌詞+コンセプトから一括でJSON出力）
- 用途3: 個別シーンのプロンプト改善提案
- 参照画像がある場合はVision機能で画風読み取り

## コーディング規約

- Python 3.10+
- 型ヒント使用
- docstring必須（日本語可）
- Gradioのイベントハンドラは関数を分離し、app.pyは薄く保つ
- エラーハンドリング: ComfyUI/LLM接続失敗時はUI上にエラーメッセージ表示
- コメントは日本語OK

## 開発順序

1. **Phase 1**: UI骨組み + プロジェクト管理（JSON保存/読込）— ComfyUI/LLM連携なし
2. **Phase 2**: Qwen3-VL連携（チャット、一括提案、個別相談）
3. **Phase 3**: ComfyUI連携（画像生成、動画生成）
4. **Phase 4**: 一括生成処理（バッチ、進捗表示、停止/再開）
5. **Phase 5**: ffmpeg書き出し + 最終調整
