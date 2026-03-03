# Music Video Generator

音楽ファイルをもとにAIでミュージックビデオを自動生成するデスクトップアプリケーション。

LLMと対話してシーンのコンセプトを練り、ComfyUI で画像・動画を生成し、ffmpeg で1本の動画に書き出すところまでを一貫して行えます。

## 機能

- **プロジェクト管理**: 音楽ファイルをアップロードすると楽曲長に応じてシーンを自動分割
- **LLMチャット**: コンセプト相談・全シーンのプロンプト一括生成・個別シーンの改善提案
- **画像生成**: z-image Turbo（ComfyUI）で各シーンの画像を生成、生成履歴を管理
- **動画生成**: WAN2.2 img2video（ComfyUI）でプレビュー版・最終版の動画を生成
- **一括生成**: 画像プロンプト生成 / 動画プロンプト生成 / 画像 / 動画 をバックグラウンドで一括処理
- **書き出し**: 音楽合成・フェードイン/アウト・ブラックフェードアウトなどのオプション付きで最終動画を書き出し
- **ローカルLLM**: Qwen3-VL をローカル実行（VRAM 節約のためロード/アンロード可能）

## 技術スタック

| レイヤー | 技術 |
|---|---|
| デスクトップ | Electron |
| フロントエンド | React + TypeScript + Vite |
| バックエンド | FastAPI + Uvicorn (Python) |
| LLM | Qwen3-VL（OpenAI互換API or ローカルtransformers） |
| 画像生成 | ComfyUI (z-image Turbo ワークフロー) |
| 動画生成 | ComfyUI (WAN2.2 14B img2video ワークフロー) |
| 動画編集 | ffmpeg |

## 必要環境

- Python 3.10+（conda 推奨）
- Node.js 18+
- [ComfyUI](https://github.com/comfyanonymous/ComfyUI)（`--listen` オプションで起動）
- ffmpeg（PATH に通っていること）
- LLM サーバー（[Ollama](https://ollama.ai/) 等の OpenAI互換API）またはローカルGPU（VRAM 16GB+）

## セットアップ

### 1. Python 環境の構築

```bash
conda create -n main python=3.11
conda activate main
pip install -r requirements.txt
```

### 2. フロントエンドのビルド

```bash
cd ui
npm install
npm run build
cd ..
```

### 3. Electron 依存関係のインストール

```bash
cd electron
npm install
cd ..
```

## 起動方法

### Windows（推奨）

```bat
start.bat
```

`start.bat` は conda 環境を有効化して Electron を起動します。
ComfyUI は別途起動しておいてください。

### 手動起動

```bash
conda activate main
cd electron
npm run start
```

## 設定

### config.yaml

```yaml
comfyui:
  url: http://localhost:8188          # ComfyUI の URL
  image_workflow: workflows/image/image_z_image_turbo.json
  video_workflow: workflows/video/video_wan2_2_14B_i2v.json

llm:
  url: http://localhost:11434/v1      # Ollama 等の OpenAI互換 API
  model: qwen3-vl

defaults:
  scene_duration: 5                   # 1シーンの秒数
  image_resolution:
    width: 1280
    height: 720

project:
  base_dir: projects                  # プロジェクト保存先（絶対パス可）
```

### プロジェクト設定

アプリ内の「プロジェクト」タブから ComfyUI URL・LLM URL・解像度・ワークフローを変更できます。

## 使い方

### 1. プロジェクト作成

1. 「プロジェクト」タブを開く
2. プロジェクト名と音楽ファイル（MP3 等）を指定して「作成」
3. 楽曲長に応じてシーンが自動生成される

### 2. シーン計画（計画タブ）

1. LLMとチャットして全体コンセプトを決める
2. 「全シーンのプロンプトを一括生成」でプロット・プロンプトを自動作成
3. 各シーンを個別に編集・調整

### 3. 画像・動画生成（生成・編集タブ）

1. シーンを選択して「画像生成」
2. 気に入らない場合は再生成（バージョン履歴から選択可）
3. 「動画生成（プレビュー）」で短時間確認用の動画を生成
4. 一括生成パネルでまとめて処理することも可能

### 4. 書き出し（書き出しタブ）

1. サムネイルギャラリーで全シーンを確認
2. 音楽合成・フェード等のオプションを設定
3. 「プレビュー版」または「最終版」を書き出し

## プロジェクトデータ構造

```
projects/{project_name}/
├── project.json          # プロジェクト設定
├── settings.json         # UI 設定
├── music/song.mp3
├── scenes/
│   ├── scene_001/
│   │   ├── scene.json    # シーンデータ（プロンプト等）
│   │   ├── image.png
│   │   ├── video_preview.mp4
│   │   ├── video_final.mp4
│   │   ├── image_versions/   # 過去の生成画像
│   │   └── video_versions/   # 過去の生成動画
│   └── ...
└── output/final.mp4
```

## ローカル LLM について

「モデル管理」タブから Qwen3-VL をローカル実行できます。

- ロードには数分かかり、VRAM を大量消費します（目安: 16GB+）
- 画像・動画生成前にアンロードして VRAM を解放することを推奨
- ローカルモデルがロードされていない場合はプロジェクト設定の LLM URL が使用されます

## ライセンス

MIT
