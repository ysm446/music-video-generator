import { useEffect, useState } from 'react'
import {
  getModelStatus,
  getModelPresets,
  loadModel,
  unloadModel,
  type ModelStatus,
} from '../../api/model'
import { usePoll } from '../../hooks/usePoll'

export default function ModelTab() {
  const [presets, setPresets] = useState<Record<string, string>>({})
  const [selectedLabel, setSelectedLabel] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(false)

  // 2秒ごとにステータスをポーリング
  const status = usePoll<ModelStatus>(getModelStatus, 2000, true)

  useEffect(() => {
    getModelPresets().then(p => {
      setPresets(p)
      const labels = Object.keys(p)
      if (labels.length > 0) setSelectedLabel(labels[0])
    }).catch(() => {})
  }, [])

  async function handleLoad() {
    if (!selectedLabel || loading) return
    setLoading(true)
    setMsg('ロード中（数分かかる場合があります）...')
    try {
      const r = await loadModel(selectedLabel)
      setMsg(r.message)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(err)
      setMsg(`エラー: ${detail}`)
    } finally {
      setLoading(false)
    }
  }

  async function handleUnload() {
    if (loading) return
    setLoading(true)
    setMsg('アンロード中...')
    try {
      const r = await unloadModel()
      setMsg(r.message)
    } catch (err: unknown) {
      setMsg(`エラー: ${String(err)}`)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-4" style={{ maxWidth: 600 }}>
      {/* 現在の状態 */}
      <div className="card">
        <h3 style={{ marginBottom: 10 }}>モデル状態</h3>
        <div className="flex flex-col gap-2" style={{ fontSize: 13 }}>
          <div>
            <span className="text-muted">状態: </span>
            <strong>{status?.is_loaded ? 'ロード済み' : '未ロード'}</strong>
            {status?.is_loaded && status.loaded_model_id && (
              <span className="text-muted" style={{ marginLeft: 8 }}>({status.loaded_model_id})</span>
            )}
          </div>
          <div>
            <span className="text-muted">VRAM: </span>
            <span style={{ fontFamily: 'monospace', fontSize: 12 }}>
              {status?.vram_info || '情報なし'}
            </span>
          </div>
        </div>
      </div>

      {/* モデル選択・操作 */}
      <div className="card">
        <h3 style={{ marginBottom: 10 }}>ローカルモデル管理（Qwen3-VL）</h3>
        <p className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
          ローカルtransformersモデルをロードするとOpenAI互換APIを使わずにLLM機能が使えます。
          ロードには数分かかりVRAMを大量消費します。使い終わったらアンロードしてください。
        </p>

        <div className="form-group">
          <label>モデルプリセット</label>
          <select
            value={selectedLabel}
            onChange={e => setSelectedLabel(e.target.value)}
            disabled={loading}
          >
            {Object.keys(presets).map(label => (
              <option key={label} value={label}>{label}</option>
            ))}
          </select>
          {selectedLabel && presets[selectedLabel] && (
            <div className="text-muted" style={{ fontSize: 11, marginTop: 4 }}>
              モデルID: {presets[selectedLabel]}
            </div>
          )}
        </div>

        <div className="flex gap-3 items-center mt-3">
          <button
            className="btn-primary"
            onClick={handleLoad}
            disabled={loading || !selectedLabel}
          >
            {loading ? (
              <><span className="spinner" style={{ verticalAlign: 'middle' }} /> ロード中...</>
            ) : 'ロード'}
          </button>
          <button
            className="btn-secondary"
            onClick={handleUnload}
            disabled={loading || !status?.is_loaded}
          >
            アンロード
          </button>
        </div>

        {msg && (
          <div
            className={msg.includes('エラー') ? 'text-error' : 'text-success'}
            style={{ fontSize: 13, marginTop: 10 }}
          >
            {msg}
          </div>
        )}
      </div>

      {/* 使い方のヒント */}
      <div className="card" style={{ background: 'var(--color-input-bg)' }}>
        <h3 style={{ marginBottom: 8, fontSize: 13 }}>ヒント</h3>
        <ul style={{ fontSize: 12, color: 'var(--color-muted)', lineHeight: 1.8, paddingLeft: 16 }}>
          <li>ローカルモデルがロードされていない場合は、プロジェクト設定の <strong>LLM URL</strong> が使われます</li>
          <li>LLM URLには Ollama（<code>http://localhost:11434/v1</code>）等のOpenAI互換APIを設定してください</li>
          <li>ローカルモデルとAPI URLを両方設定した場合、ローカルモデルが優先されます</li>
          <li>画像・動画生成前にアンロードするとVRAMを節約できます</li>
        </ul>
      </div>
    </div>
  )
}
