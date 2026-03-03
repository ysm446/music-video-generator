import { useState } from 'react'
import { useSSE } from '../../hooks/useSSE'

interface Props {
  projectName: string
  concept: string
  /** シーンが更新されたとき（scene_id, section, plotを受け取る） */
  onSceneProposed: (sceneId: number, section: string, plot: string) => void
}

export default function BulkPlanPanel({ projectName, concept, onSceneProposed }: Props) {
  const [log, setLog] = useState<string[]>([])
  const { isStreaming, start, stop } = useSSE()

  function handleStart() {
    if (!concept.trim()) {
      setLog(['コンセプトを入力してください'])
      return
    }
    setLog(['一括提案を開始します...'])

    start({
      url: `/api/projects/${encodeURIComponent(projectName)}/llm/generate-all-prompts`,
      body: { concept, missing_only: true },
      onEvent: (data) => {
        if (data.type === 'progress') {
          setLog(prev => [...prev.slice(-19), String(data.message)])
        } else if (data.type === 'scene_done') {
          const sid = Number(data.scene_id)
          const section = String(data.section ?? '')
          const plot = String(data.plot ?? '')
          onSceneProposed(sid, section, plot)
          setLog(prev => [...prev.slice(-19), `✓ シーン ${sid} 完了: ${plot.slice(0, 40)}...`])
        } else if (data.type === 'error') {
          setLog(prev => [...prev.slice(-19), `⚠ シーン ${data.scene_id}: ${data.message}`])
        }
      },
      onDone: () => {
        setLog(prev => [...prev, '一括提案が完了しました。内容を確認後「全シーンを保存」してください。'])
      },
      onError: (msg) => {
        setLog(prev => [...prev, `エラー: ${msg}`])
      },
    })
  }

  return (
    <div>
      <div className="flex gap-2 mb-2">
        <button
          className="btn-secondary"
          onClick={isStreaming ? stop : handleStart}
          disabled={false}
        >
          {isStreaming ? '⏹ 停止' : '🤖 シーンを一括提案'}
        </button>
        {!isStreaming && log.length > 0 && (
          <button className="btn-secondary" onClick={() => setLog([])} style={{ fontSize: 12 }}>
            ログクリア
          </button>
        )}
      </div>

      {log.length > 0 && (
        <div
          style={{
            background: 'var(--color-input-bg)',
            border: '1px solid var(--color-border)',
            borderRadius: 'var(--radius)',
            padding: '8px 10px',
            fontSize: 12,
            lineHeight: 1.8,
            maxHeight: 160,
            overflowY: 'auto',
          }}
        >
          {log.map((line, i) => <div key={i}>{line}</div>)}
        </div>
      )}
    </div>
  )
}
