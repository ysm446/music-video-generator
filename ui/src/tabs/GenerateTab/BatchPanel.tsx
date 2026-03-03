import { useEffect, useState } from 'react'
import { startBatch, stopBatch, getBatchStatus, type BatchStatus } from '../../api/generation'
import { usePoll } from '../../hooks/usePoll'

interface Props {
  projectName: string
  onDone?: () => void
}

export default function BatchPanel({ projectName, onDone }: Props) {
  const [msg, setMsg] = useState('')
  const [polling, setPolling] = useState(false)

  const batchStatus = usePoll<BatchStatus>(getBatchStatus, 1000, polling)

  // 完了したらポーリング停止
  useEffect(() => {
    if (batchStatus?.state === 'done') {
      setPolling(false)
      onDone?.()
    }
  }, [batchStatus?.state, onDone])

  async function handleStart(target: string, videoQuality: 'preview' | 'final' = 'preview') {
    try {
      const r = await startBatch(projectName, target, videoQuality)
      setMsg(r.message)
      setPolling(true)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(err)
      setMsg(`エラー: ${detail}`)
    }
  }

  async function handleStop() {
    try {
      const r = await stopBatch()
      setMsg(r.message)
      setPolling(false)
    } catch (err: unknown) {
      setMsg(`停止エラー: ${String(err)}`)
    }
  }

  const isRunning = batchStatus?.state === 'running'

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-1 flex-wrap">
        <button className="btn-secondary" style={{ fontSize: 11 }} onClick={() => handleStart('image_prompt')} disabled={isRunning}>
          画像プロンプト生成
        </button>
        <button className="btn-secondary" style={{ fontSize: 11 }} onClick={() => handleStart('video_prompt')} disabled={isRunning}>
          動画プロンプト生成
        </button>
        <button className="btn-primary" style={{ fontSize: 11 }} onClick={() => handleStart('image')} disabled={isRunning}>
          一括画像生成
        </button>
        <button className="btn-secondary" style={{ fontSize: 11 }} onClick={() => handleStart('video', 'preview')} disabled={isRunning}>
          一括プレビュー動画
        </button>
        <button className="btn-secondary" style={{ fontSize: 11 }} onClick={() => handleStart('video', 'final')} disabled={isRunning}>
          一括最終版動画
        </button>
        <button className="btn-secondary" style={{ fontSize: 11, color: 'var(--color-error)' }} onClick={handleStop} disabled={!isRunning}>
          停止
        </button>
      </div>

      {msg && <div style={{ fontSize: 11, color: 'var(--color-muted)' }}>{msg}</div>}

      {batchStatus && batchStatus.state !== 'idle' && (
        <div style={{
          background: 'var(--color-input-bg)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: '6px 10px',
          fontSize: 11,
          fontFamily: 'monospace',
        }}>
          <div>状態: <strong>{batchStatus.state === 'running' ? '実行中' : '完了'}</strong> | モード: {batchStatus.mode}</div>
          <div>現在: {batchStatus.current_task}</div>
          <div>経過: {batchStatus.elapsed}</div>
          {batchStatus.logs.length > 0 && (
            <div style={{ marginTop: 4, color: 'var(--color-muted)' }}>
              {batchStatus.logs.slice(-4).map((l, i) => <div key={i}>{l}</div>)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
