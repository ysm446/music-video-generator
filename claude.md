# MV Generator - CLAUDE.md

## プロジェクト概要

音楽ファイルをもとにミュージックビデオを生成するデスクトップアプリケーション。
LLM(Qwen3-VL)と対話しながらシーンを計画し、ComfyUIバックエンドで画像(z-image Turbo)と動画(WAN2.2)を自動生成する。

## 技術スタック

- **デスクトップ**: Electron（Chromiumシェル）
- **フロントエンド**: React + TypeScript + Vite
- **バックエンド**: FastAPI + Uvicorn (Python)
- **LLM**: Qwen3-VL（OpenAI互換APIまたはローカルtransformers実行）
- **画像生成**: z-image Turbo（ComfyUI経由）
- **動画生成**: WAN2.2 img2video（ComfyUI経由）
- **動画結合**: ffmpeg
- **データ保存**: JSON + ローカルファイル

## アーキテクチャ

```
Electron → HTTP(8000) → FastAPI (api.py)
                         ├── 静的ファイル配信 (ui/dist/)
                         ├── REST API / SSE (/api/*)
                         ├── ComfyUI API (localhost:8188)
                         ├── Qwen3-VL API (OpenAI互換 or transformers)
                         └── ffmpeg (動画結合)
```

- `electron/main.js` が FastAPI (`api.py`) をサブプロセスとして起動し、起動完了を待って BrowserWindow を開く
- React UI は `ui/dist/` にビルドし、FastAPI の `StaticFiles` で配信する
- すべての API は `/api/` プレフィックスで提供される
- `src/` モジュール（ビジネスロジック）は変更せず、`api_routes/` から呼び出す

## ディレクトリ構造

```
music-video-generator/
├── api.py                      # FastAPI エントリポイント
├── api_routes/                 # ルーター群
│   ├── _shared.py              # BASE_DIR 等の共有変数
│   ├── projects.py             # プロジェクト CRUD
│   ├── scenes.py               # シーン CRUD + 並び替え
│   ├── generation.py           # 画像/動画生成 + バッチ
│   ├── llm.py                  # LLM チャット・プロンプト生成 (SSE)
│   ├── files.py                # ファイルサービング
│   ├── export.py               # 動画書き出し (SSE)
│   └── model.py                # ローカルモデル管理
├── src/                        # コアビジネスロジック（変更禁止）
│   ├── project.py              # プロジェクト管理
│   ├── scene.py                # Scene データクラス
│   ├── llm_client.py           # OpenAI互換API連携
│   ├── model_manager.py        # ローカルtransformers実行
│   ├── settings_manager.py     # プロジェクト設定管理
│   ├── comfyui_client.py       # ComfyUI API連携
│   ├── batch_generator.py      # 一括生成処理
│   └── video_export.py         # ffmpeg書き出し
├── ui/                         # React フロントエンド
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/                # API クライアント (axios)
│   │   │   ├── client.ts       # axios インスタンス (baseURL: '')
│   │   │   ├── projects.ts
│   │   │   ├── scenes.ts
│   │   │   ├── generation.ts
│   │   │   ├── export.ts
│   │   │   └── model.ts
│   │   ├── context/ProjectContext.tsx
│   │   ├── hooks/
│   │   │   ├── useSSE.ts       # POST SSE (fetch + ReadableStream)
│   │   │   └── usePoll.ts      # setInterval ラッパー
│   │   ├── components/common/
│   │   │   ├── ChatPanel.tsx
│   │   │   └── SeedInput.tsx
│   │   └── tabs/
│   │       ├── ProjectTab/
│   │       ├── PlanTab/
│   │       ├── GenerateTab/
│   │       ├── ExportTab/
│   │       └── ModelTab/
│   └── dist/                   # ビルド成果物（要 npm run build）
├── electron/
│   ├── main.js                 # Electron メインプロセス
│   └── preload.js
├── workflows/
│   ├── image/                  # ComfyUI 画像ワークフロー JSON
│   └── video/                  # ComfyUI 動画ワークフロー JSON
├── config.yaml                 # デフォルト設定
├── requirements.txt
└── start.bat                   # 起動スクリプト (Windows)
```

### プロジェクトデータ（実行時に生成）

デフォルトは `{アプリルート}/projects/` に作成される。
`config.yaml` の `project.base_dir` で変更可（相対パスはアプリルート基準、絶対パスはそのまま使用）。

```
projects/
└── {project_name}/
    ├── project.json            # プロジェクト全体メタデータ
    ├── settings.json           # UIパラメータ（URL、ワークフロー等）
    ├── music/
    │   └── song.mp3
    ├── scenes/
    │   ├── scene_001/
    │   │   ├── scene.json      # プロット、プロンプト、ステータス
    │   │   ├── image.png       # アクティブ画像
    │   │   ├── video_preview.mp4
    │   │   ├── video_final.mp4
    │   │   ├── image_versions/ # 生成履歴
    │   │   └── video_versions/
    │   └── scene_NNN/
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
  "plot": "シーンの内容説明（日本語）",
  "image_prompt": "英語プロンプト",
  "image_negative": "英語ネガティブプロンプト",
  "image_seed": -1,
  "image_workflow": null,
  "active_image_version": "",
  "active_video_preview_version": "",
  "active_video_final_version": "",
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
- `image_seed` は生成済みPNGのtEXtメタデータから読み取り可能
- `video_seed` はscene.jsonから読み取る（MP4にはシード埋め込みなし）

## API エンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/config` | アプリ設定・最終プロジェクト名 |
| GET | `/api/projects` | プロジェクト一覧 |
| POST | `/api/projects` | プロジェクト新規作成（multipart） |
| GET | `/api/projects/{name}` | プロジェクト詳細（シーン一覧含む） |
| PUT | `/api/projects/{name}/settings` | プロジェクト設定保存 |
| GET | `/api/projects/{name}/workflows` | ワークフロー一覧 |
| GET | `/api/projects/{name}/scenes` | シーン一覧 |
| PUT | `/api/projects/{name}/scenes/{id}` | シーン保存 |
| POST | `/api/projects/{name}/scenes/{id}/move` | シーン順序変更 |
| POST | `/api/projects/{name}/scenes/{id}/insert-after` | シーン挿入 |
| DELETE | `/api/projects/{name}/scenes/{id}` | シーン削除 |
| POST | `/api/projects/{name}/scenes/bulk-save` | シーン一括保存 |
| POST | `/api/llm/chat-stream` | LLMチャット SSE |
| POST | `/api/projects/{name}/llm/generate-all-prompts` | 全シーンプロンプト生成 SSE |
| POST | `/api/projects/{name}/scenes/{id}/llm/improve-prompt` | シーンプロンプト改善 |
| POST | `/api/projects/{name}/scenes/{id}/llm/image-chat-stream` | 画像プロンプト編集 SSE |
| POST | `/api/projects/{name}/scenes/{id}/llm/video-prompt-stream` | 動画プロンプト生成 SSE |
| GET | `/api/projects/{name}/scenes/{id}/media` | メディアURL・バージョン情報 |
| POST | `/api/projects/{name}/scenes/{id}/generate` | 個別生成キュー追加 |
| POST | `/api/projects/{name}/scenes/{id}/use-version` | バージョン切り替え |
| DELETE | `/api/projects/{name}/scenes/{id}/version` | バージョン削除 |
| GET | `/api/queue/status` | 個別生成キュー状態 |
| POST | `/api/batch/start` | 一括生成開始 |
| POST | `/api/batch/stop` | 一括生成停止 |
| GET | `/api/batch/status` | 一括生成状態 |
| GET | `/api/projects/{name}/export/thumbnails` | サムネイル一覧 |
| GET | `/api/projects/{name}/export/outputs` | 書き出し済みファイル一覧 |
| POST | `/api/projects/{name}/export` | 動画書き出し SSE |
| GET | `/api/model/status` | モデルロード状態 |
| GET | `/api/model/presets` | モデルプリセット一覧 |
| POST | `/api/model/load` | ローカルモデルロード |
| DELETE | `/api/model` | ローカルモデルアンロード |
| GET | `/api/files/{path:path}` | プロジェクトファイル配信 |

## SSEパターン（Python）

ブロッキング処理を asyncio.Queue + threading でブリッジする共通パターン:

```python
async def event_generator():
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()
    def _producer():
        for chunk in _some_blocking_generator():
            loop.call_soon_threadsafe(queue.put_nowait, chunk)
        loop.call_soon_threadsafe(queue.put_nowait, None)
    threading.Thread(target=_producer, daemon=True).start()
    while True:
        item = await queue.get()
        if item is None: break
        yield f'data: {json.dumps({"chunk": item})}\n\n'
    yield 'data: {"done": true}\n\n'
return EventSourceResponse(event_generator())
```

## LLM連携の2モード

### OpenAI互換API（`llm_client.py`）

- `LLMClient(base_url, model)` で接続
- メッセージはOpenAI形式 `{"role": "user", "content": [{"type": "text"/"image_url", ...}]}`

### ローカルtransformers（`model_manager.py`）

- グローバルシングルトンとしてモデルを保持（`_model`, `_processor`）
- `is_loaded()` でロード状態確認
- `asyncio.to_thread()` でブロッキング処理をラップ

## フロントエンド実装パターン

### APIクライアント

`ui/src/api/client.ts` の axios インスタンスは `baseURL: ''`。
すべてのパスは **`/api/` で始める**（プレフィックス省略禁止）。

```typescript
// 正しい
client.get('/api/projects/Foo/scenes')
// 誤り（StaticFiles が受け取ってしまう）
client.get('/projects/Foo/scenes')
```

### SSEフック（useSSE）

```typescript
const { isStreaming, start, stop } = useSSE()
start({
  url: '/api/projects/Foo/llm/chat-stream',
  body: { messages },
  onChunk: (chunk) => { /* ... */ },
  onDone: () => {},
})
```

### ポーリング（usePoll）

```typescript
const status = usePoll(getQueueStatus, 1500, isActive)
```

## パス管理の注意点

- `api.py` / `api_routes/_shared.py` どちらも `Path(__file__).parent.resolve()` でアプリルートを取得
- `settings_manager.py` の `_ROOT_SETTINGS_PATH` は相対パスのため、`api.py` 起動時に上書き:
  ```python
  _sm._ROOT_SETTINGS_PATH = _APP_DIR / "settings.json"
  ```
- `api_routes/files.py` はパストラバーサル保護のため `full.relative_to(BASE_DIR.resolve())` を必ず確認

## コーディング規約

- Python 3.10+、型ヒント使用、docstring 必須（日本語可）
- TypeScript strict モード、関数コンポーネント + フック
- コメントは日本語 OK
- `src/` モジュールは変更しない（`api_routes/` から呼び出すのみ）
- エラーハンドリング: ComfyUI/LLM 接続失敗時は HTTPException でフロントに伝える
