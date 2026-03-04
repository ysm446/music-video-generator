import { useState } from 'react'
import type { Scene } from '../../types/scene'
import type { SceneMedia } from '../../api/generation'
import { enqueueGenerate, useVersion, deleteVersion, getImageSeed, clearMedia } from '../../api/generation'
import { saveScene } from '../../api/scenes'
import SeedInput from '../../components/common/SeedInput'
import ChatPanel, { type ChatMessage } from '../../components/common/ChatPanel'

interface Props {
  projectName: string
  scene: Scene
  media: SceneMedia | null
  workflows: string[]
  onSceneChange: (partial: Partial<Scene>) => void
  onMediaRefresh: () => void
}

export default function ImageSubTab({ projectName, scene, media, workflows, onSceneChange, onMediaRefresh }: Props) {
  const [queueMsg, setQueueMsg] = useState('')
  const [showHistory, setShowHistory] = useState(false)

  async function handleReadSeedFromPng() {
    try {
      const seed = await getImageSeed(projectName, scene.scene_id)
      onSceneChange({ image_seed: seed })
    } catch {
      // 画像なし or メタデータなし → 何もしない
    }
  }

  async function handleClearMedia() {
    if (!confirm('アクティブ画像を未設定にしますか？（履歴は保持されます）')) return
    const m = await clearMedia(projectName, scene.scene_id, 'image')
    onMediaRefresh()
    // ステータス更新のため親にも通知
    if (m.status) onSceneChange({ status: m.status as Scene['status'] })
  }

  async function handleGenerate() {
    try {
      await saveScene(projectName, scene.scene_id, scene)
      const r = await enqueueGenerate(projectName, scene.scene_id, 'image')
      setQueueMsg(r.message)
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(err)
      setQueueMsg(`エラー: ${detail}`)
    }
  }

  async function handleUseVersion(v: string) {
    await useVersion(projectName, scene.scene_id, v, 'image')
    onMediaRefresh()
  }

  async function handleDeleteVersion(v: string) {
    if (!confirm(`バージョン "${v}" を削除しますか？`)) return
    await deleteVersion(projectName, scene.scene_id, v, 'image')
    onMediaRefresh()
  }

  function buildChatBody(msgs: ChatMessage[]) {
    return {
      messages: msgs.map(m => ({ role: m.role, content: m.content })),
      image_prompt: scene.image_prompt,
      image_negative: scene.image_negative,
    }
  }

  function handleChatEvent(data: unknown) {
    const d = data as Record<string, unknown>
    if (d?.prompt_update) {
      const upd = d.prompt_update as { positive?: string; negative?: string }
      if (upd.positive !== undefined) onSceneChange({ image_prompt: upd.positive })
      if (upd.negative !== undefined && upd.negative !== '') onSceneChange({ image_negative: upd.negative })
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {/* 上段: プロンプト・設定 ／ LLMチャット */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* 左カラム: プロンプト・設定 */}
        <div className="flex flex-col gap-3">
          <div className="form-group">
            <label>画像プロンプト</label>
            <textarea
              value={scene.image_prompt}
              onChange={e => onSceneChange({ image_prompt: e.target.value })}
              rows={6}
              placeholder="Positive prompt..."
            />
          </div>

          <div className="form-group">
            <label>ネガティブプロンプト</label>
            <textarea
              value={scene.image_negative}
              onChange={e => onSceneChange({ image_negative: e.target.value })}
              rows={3}
              placeholder="Negative prompt..."
            />
          </div>

          <div className="flex gap-4 items-start">
            <div className="form-group" style={{ flex: 1 }}>
              <label>シード</label>
              <SeedInput
                value={scene.image_seed}
                onChange={v => onSceneChange({ image_seed: v })}
                showReadFromPng={true}
                onReadFromPng={handleReadSeedFromPng}
              />
            </div>
            <div className="form-group" style={{ flex: 1 }}>
              <label>ワークフロー</label>
              <select
                value={scene.image_workflow ?? ''}
                onChange={e => onSceneChange({ image_workflow: e.target.value || null })}
              >
                <option value="">(デフォルト)</option>
                {workflows.map(wf => <option key={wf} value={wf}>{wf}</option>)}
              </select>
            </div>
          </div>

          <div className="flex gap-2 items-center">
            <button className="btn-primary" onClick={handleGenerate}>
              画像を生成
            </button>
            <button className="btn-secondary" style={{ fontSize: 12 }} onClick={handleClearMedia}>
              画像をクリア
            </button>
            {queueMsg && <span className="text-muted" style={{ fontSize: 12 }}>{queueMsg}</span>}
          </div>
        </div>

        {/* 右カラム: LLMチャット */}
        <div className="flex flex-col gap-2">
          <div style={{ fontSize: 13, fontWeight: 600 }}>LLMチャット（プロンプト編集）</div>
          <ChatPanel
            url={`/api/projects/${projectName}/scenes/${scene.scene_id}/llm/image-chat-stream`}
            buildBody={buildChatBody}
            height={240}
            placeholder="プロンプトの改善指示を入力（例: もっと幻想的に、青い色調で）..."
            onEvent={handleChatEvent}
          />
        </div>
      </div>

      {/* 下段: バージョン履歴（全幅） */}
      {(media?.image_versions?.length ?? 0) > 0 && (
        <div>
          <button
            className="btn-secondary"
            style={{ fontSize: 12 }}
            onClick={() => setShowHistory(h => !h)}
          >
            {showHistory ? '▼' : '▶'} 画像履歴（{media!.image_versions.length}件）
          </button>
          {showHistory && (
            <div className="flex flex-wrap gap-2" style={{ marginTop: 8, maxHeight: 320, overflowY: 'auto' }}>
              {media!.image_versions.map(v => {
                const sceneDir = `scene_${String(scene.scene_id).padStart(3, '0')}`
                const thumbUrl = `/api/files/${projectName}/scenes/${sceneDir}/image_versions/${v}`
                const isActive = v === media!.active_image_version
                return (
                  <div key={v} className="flex flex-col gap-1 items-center" style={{
                    padding: '6px',
                    borderRadius: 4,
                    border: `1px solid ${isActive ? 'var(--color-primary)' : 'var(--color-border)'}`,
                    background: isActive ? 'rgba(233,69,96,0.08)' : 'transparent',
                  }}>
                    <img
                      src={thumbUrl}
                      alt={v}
                      style={{ width: 160, height: 90, objectFit: 'cover', borderRadius: 3, background: 'var(--color-surface2)' }}
                    />
                    <span style={{
                      width: 160,
                      fontFamily: 'monospace',
                      fontSize: 10,
                      color: isActive ? 'var(--color-primary)' : 'var(--color-muted)',
                      wordBreak: 'break-all',
                    }}>
                      {isActive ? '★ ' : ''}{v}
                    </span>
                    {!isActive && (
                      <div className="flex gap-1">
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
