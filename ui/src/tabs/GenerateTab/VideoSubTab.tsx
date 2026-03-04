import { useState } from 'react'
import type { Scene } from '../../types/scene'
import type { SceneMedia } from '../../api/generation'
import { enqueueGenerate, useVersion, deleteVersion, getVideoSeed, clearMedia } from '../../api/generation'
import { saveScene } from '../../api/scenes'
import { useSSE } from '../../hooks/useSSE'
import SeedInput from '../../components/common/SeedInput'

interface Props {
  quality: 'preview' | 'final'
  projectName: string
  scene: Scene
  media: SceneMedia | null
  workflows: string[]
  commonInstruction?: string
  onSceneChange: (partial: Partial<Scene>) => void
  onMediaRefresh: () => void
}

export default function VideoSubTab({
  quality,
  projectName,
  scene,
  media,
  workflows,
  commonInstruction = '',
  onSceneChange,
  onMediaRefresh,
}: Props) {
  const [queueMsg, setQueueMsg] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const [llmStatus, setLlmStatus] = useState('')
  const { isStreaming, start, stop } = useSSE()

  const mediaType = quality === 'preview' ? 'video_preview' : 'video_final'
  const versions = quality === 'preview' ? (media?.video_versions_preview ?? []) : (media?.video_versions_final ?? [])
  const activeVersion = quality === 'preview' ? media?.active_video_preview_version : media?.active_video_final_version
  const label = quality === 'preview' ? 'プレビュー動画' : '最終版動画'

  async function handleReadSeedFromVideo() {
    try {
      const seed = await getVideoSeed(projectName, scene.scene_id, quality)
      onSceneChange({ video_seed: seed })
    } catch {
      // 動画なし or メタデータなし → 何もしない
    }
  }

  async function handleClearMedia() {
    const mediaType = quality === 'final' ? 'video_final' : 'video_preview'
    if (!confirm(`アクティブな${label}を未設定にしますか？（履歴は保持されます）`)) return
    await clearMedia(projectName, scene.scene_id, mediaType)
    onMediaRefresh()
  }

  async function handleGenerate() {
    try {
      await saveScene(projectName, scene.scene_id, scene)
      const r = await enqueueGenerate(projectName, scene.scene_id, 'video', quality)
      setQueueMsg(r.message)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(err)
      setQueueMsg(`エラー: ${detail}`)
    }
  }

  async function handleUseVersion(v: string) {
    await useVersion(projectName, scene.scene_id, v, mediaType)
    onMediaRefresh()
  }

  async function handleDeleteVersion(v: string) {
    if (!confirm(`バージョン "${v}" を削除しますか？`)) return
    await deleteVersion(projectName, scene.scene_id, v, mediaType)
    onMediaRefresh()
  }

  function handleGenerateVideoPrompt() {
    setLlmStatus('生成中...')
    start({
      url: `/api/projects/${projectName}/scenes/${scene.scene_id}/llm/video-prompt-stream`,
      body: {
        video_instruction: scene.video_instruction,
        common_instruction: commonInstruction,
      },
      onEvent: (data) => {
        const d = data as Record<string, unknown>
        if (d?.video_prompt_update) {
          const upd = d.video_prompt_update as { video_prompt?: string; video_negative?: string }
          if (upd.video_prompt !== undefined) onSceneChange({ video_prompt: upd.video_prompt })
          if (upd.video_negative !== undefined) onSceneChange({ video_negative: upd.video_negative })
          setLlmStatus('動画プロンプトを更新しました')
        }
        if ((d as { error?: string })?.error) {
          setLlmStatus(`LLMエラー: ${(d as { error: string }).error}`)
        }
      },
      onDone: () => setLlmStatus(prev => prev === '生成中...' ? '完了' : prev),
    })
  }

  return (
    <div className="flex flex-col gap-3">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {/* 左: プロンプト・シード・ワークフロー */}
        <div className="flex flex-col gap-3">
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>動画プロンプト</label>
            <textarea
              value={scene.video_prompt}
              onChange={e => onSceneChange({ video_prompt: e.target.value })}
              rows={3}
              placeholder="Scene: ..., Action: ..., Camera: ..."
            />
          </div>

          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>ネガティブプロンプト</label>
            <textarea
              value={scene.video_negative}
              onChange={e => onSceneChange({ video_negative: e.target.value })}
              rows={2}
              placeholder="Negative prompt..."
            />
          </div>

          <div className="flex gap-4 items-start">
            <div className="form-group" style={{ flex: 1, marginBottom: 0 }}>
              <label>シード</label>
              <SeedInput
                value={scene.video_seed}
                onChange={v => onSceneChange({ video_seed: v })}
                showReadFromPng={true}
                onReadFromPng={handleReadSeedFromVideo}
              />
            </div>
            <div className="form-group" style={{ flex: 1, marginBottom: 0 }}>
              <label>ワークフロー</label>
              <select
                value={scene.video_workflow ?? ''}
                onChange={e => onSceneChange({ video_workflow: e.target.value || null })}
              >
                <option value="">(デフォルト)</option>
                {workflows.map(wf => <option key={wf} value={wf}>{wf}</option>)}
              </select>
            </div>
          </div>
        </div>

        {/* 右: 追加指示 + プロンプト生成 */}
        <div className="flex flex-col gap-3">
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label>追加指示（LLM動画プロンプト生成用）</label>
            <textarea
              value={scene.video_instruction}
              onChange={e => onSceneChange({ video_instruction: e.target.value })}
              rows={8}
              placeholder="subtle camera movement..."
            />
          </div>
          <div className="flex gap-2 items-center">
            <button
              className="btn-secondary"
              onClick={handleGenerateVideoPrompt}
              disabled={isStreaming}
            >
              {isStreaming ? 'LLM生成中...' : 'LLMで動画プロンプト生成'}
            </button>
            {isStreaming && (
              <button className="btn-secondary" style={{ fontSize: 12 }} onClick={stop}>停止</button>
            )}
            {llmStatus && <span className="text-muted" style={{ fontSize: 12 }}>{llmStatus}</span>}
          </div>
        </div>
      </div>

      <div className="flex gap-2 items-center">
        <button className="btn-primary" onClick={handleGenerate}>
          {label}を生成
        </button>
        <button className="btn-secondary" style={{ fontSize: 12 }} onClick={handleClearMedia}>
          {label}をクリア
        </button>
        {queueMsg && <span className="text-muted" style={{ fontSize: 12 }}>{queueMsg}</span>}
      </div>

      {/* バージョン履歴 */}
      {versions.length > 0 && (
        <div>
          <button
            className="btn-secondary"
            style={{ fontSize: 12 }}
            onClick={() => setShowHistory(h => !h)}
          >
            {showHistory ? '▼' : '▶'} {label}履歴（{versions.length}件）
          </button>
          {showHistory && (
            <div className="flex flex-col gap-2" style={{ marginTop: 8, maxHeight: 400, overflowY: 'auto' }}>
              {versions.map(v => {
                const sceneDir = `scene_${String(scene.scene_id).padStart(3, '0')}`
                const videoUrl = `/api/files/${projectName}/scenes/${sceneDir}/video_versions/${v}`
                const isActive = v === activeVersion
                return (
                  <div key={v} className="flex gap-2 items-center" style={{
                    padding: '4px 6px',
                    borderRadius: 4,
                    border: `1px solid ${isActive ? 'var(--color-primary)' : 'var(--color-border)'}`,
                    background: isActive ? 'rgba(233,69,96,0.08)' : 'transparent',
                  }}>
                    <video
                      src={videoUrl}
                      preload="metadata"
                      muted
                      loop
                      playsInline
                      style={{ width: 160, height: 90, objectFit: 'cover', borderRadius: 3, flexShrink: 0, background: '#000', cursor: 'pointer' }}
                      onMouseEnter={e => (e.currentTarget as HTMLVideoElement).play()}
                      onMouseLeave={e => { const el = e.currentTarget as HTMLVideoElement; el.pause(); el.currentTime = 0 }}
                    />
                    <span style={{
                      flex: 1,
                      fontFamily: 'monospace',
                      fontSize: 10,
                      color: isActive ? 'var(--color-primary)' : 'var(--color-muted)',
                      wordBreak: 'break-all',
                    }}>
                      {isActive ? '★ ' : ''}{v}
                    </span>
                    {!isActive && (
                      <div className="flex flex-col gap-1">
                        <button className="btn-secondary" style={{ fontSize: 11, padding: '2px 6px' }} onClick={() => handleUseVersion(v)}>
                          使用
                        </button>
                        <button className="btn-secondary" style={{ fontSize: 11, padding: '2px 6px', color: 'var(--color-error)' }} onClick={() => handleDeleteVersion(v)}>
                          削除
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
