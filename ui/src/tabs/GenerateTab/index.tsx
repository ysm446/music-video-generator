import { useCallback, useEffect, useState } from 'react'
import { useProject } from '../../context/ProjectContext'
import type { Scene } from '../../types/scene'
import type { SceneMedia } from '../../api/generation'
import { getSceneMedia, getQueueStatus } from '../../api/generation'
import { saveScene } from '../../api/scenes'
import { getWorkflows } from '../../api/projects'
import { usePoll } from '../../hooks/usePoll'
import SceneSidebar from './SceneSidebar'
import BatchPanel from './BatchPanel'
import ImageSubTab from './ImageSubTab'
import VideoSubTab from './VideoSubTab'

type SubTab = 'image' | 'video_preview' | 'video_final'

export default function GenerateTab() {
  const { projectName, scenes, settings, updateScene } = useProject()

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [editScene, setEditScene] = useState<Scene | null>(null)
  const [media, setMedia] = useState<SceneMedia | null>(null)
  const [activeSubTab, setActiveSubTab] = useState<SubTab>('image')
  const [saveStatus, setSaveStatus] = useState('')
  const [imageWorkflows, setImageWorkflows] = useState<string[]>([])
  const [videoWorkflows, setVideoWorkflows] = useState<string[]>([])

  // キュー監視: dirty=trueになったらメディアをリフレッシュ
  const queueStatus = usePoll(getQueueStatus, 1500, !!selectedId)

  useEffect(() => {
    if (queueStatus?.dirty && selectedId && projectName) {
      getSceneMedia(projectName, selectedId).then(m => {
        setMedia(m)
        // 更新後のステータスをContextにも反映
        if (m.status) {
          const updated = scenes.find(s => s.scene_id === selectedId)
          if (updated && updated.status !== m.status) {
            updateScene({ ...updated, status: m.status as Scene['status'] })
          }
        }
      }).catch(() => {})
    }
  }, [queueStatus?.dirty]) // eslint-disable-line react-hooks/exhaustive-deps

  // ワークフロー一覧
  useEffect(() => {
    if (!projectName) return
    getWorkflows(projectName).then(wf => {
      setImageWorkflows(wf.image)
      setVideoWorkflows(wf.video)
    }).catch(() => {})
  }, [projectName])

  const refreshMedia = useCallback(async () => {
    if (!projectName || !selectedId) return
    const m = await getSceneMedia(projectName, selectedId)
    setMedia(m)
  }, [projectName, selectedId])

  function handleSelectScene(scene: Scene) {
    setSelectedId(scene.scene_id)
    setEditScene({ ...scene })
    setSaveStatus('')
    if (projectName) {
      getSceneMedia(projectName, scene.scene_id).then(setMedia).catch(() => setMedia(null))
    }
  }

  function handleSceneChange(partial: Partial<Scene>) {
    setEditScene(prev => prev ? { ...prev, ...partial } : null)
  }

  async function handleSave() {
    if (!projectName || !editScene) return
    setSaveStatus('保存中...')
    try {
      const updated = await saveScene(projectName, editScene.scene_id, editScene)
      updateScene(updated)
      setEditScene(updated)
      setSaveStatus('保存しました')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(err)
      setSaveStatus(`エラー: ${msg}`)
    }
  }

  if (!projectName) {
    return <div className="card text-muted">プロジェクトを読み込んでください</div>
  }

  const commonInstruction = (settings as Record<string, unknown> | null)?.batch_video_prompt_common_instruction as string ?? ''

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 12, height: 'calc(100vh - 100px)' }}>
      {/* 左サイドバー */}
      <div className="card flex flex-col gap-2" style={{ minWidth: 0, overflow: 'hidden', padding: '8px 0' }}>
        <div style={{ padding: '0 10px 8px', borderBottom: '1px solid var(--color-border)', flexShrink: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>一括生成</div>
          <BatchPanel projectName={projectName} onDone={refreshMedia} />
        </div>
        <SceneSidebar
          scenes={scenes}
          selectedId={selectedId}
          onSelect={handleSelectScene}
        />
      </div>

      {/* 右メインエリア */}
      <div className="flex flex-col gap-3" style={{ minWidth: 0, overflow: 'hidden' }}>
        {!editScene ? (
          <div className="card text-muted" style={{ flex: 1 }}>
            左のサイドバーからシーンを選択してください
          </div>
        ) : (
          <>
            {/* メディアプレビュー */}
            <div className="card" style={{ flexShrink: 0 }}>
              <div className="flex gap-2 items-start" style={{ flexWrap: 'wrap' }}>
                <div>
                  {media?.image_url ? (
                    <img src={media.image_url} alt="scene" style={{ maxHeight: 160, maxWidth: 280, objectFit: 'contain', border: '1px solid var(--color-border)' }} />
                  ) : (
                    <div style={{ width: 200, height: 120, background: 'var(--color-surface)', border: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: 'var(--color-muted)' }}>
                      画像なし
                    </div>
                  )}
                </div>
                <div>
                  {media?.video_preview_url ? (
                    <video
                      src={media.video_preview_url}
                      controls
                      style={{ maxHeight: 160, maxWidth: 280 }}
                    />
                  ) : (
                    <div style={{ width: 200, height: 120, background: 'var(--color-surface)', border: '1px solid var(--color-border)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: 'var(--color-muted)' }}>
                      動画なし
                    </div>
                  )}
                </div>
                <div className="flex flex-col gap-1" style={{ fontSize: 12 }}>
                  <div>シーン #{editScene.scene_id} | ステータス: <strong>{media?.status ?? editScene.status}</strong></div>
                  <div className="flex gap-2">
                    <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <input
                        type="checkbox"
                        checked={editScene.enabled}
                        onChange={e => handleSceneChange({ enabled: e.target.checked })}
                      />
                      有効
                    </label>
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label>プロット</label>
                    <textarea
                      value={editScene.plot}
                      onChange={e => handleSceneChange({ plot: e.target.value })}
                      rows={2}
                      style={{ fontSize: 11 }}
                    />
                  </div>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label>メモ</label>
                    <textarea
                      value={editScene.notes}
                      onChange={e => handleSceneChange({ notes: e.target.value })}
                      rows={1}
                      style={{ fontSize: 11 }}
                    />
                  </div>
                  <div className="flex gap-2 items-center">
                    <button className="btn-primary" style={{ fontSize: 12 }} onClick={handleSave}>
                      保存
                    </button>
                    {saveStatus && (
                      <span className={saveStatus.includes('エラー') ? 'text-error' : 'text-success'} style={{ fontSize: 11 }}>
                        {saveStatus}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* サブタブ */}
            <div className="card flex flex-col gap-0" style={{ flex: 1, overflow: 'hidden' }}>
              <div className="flex gap-0" style={{ borderBottom: '1px solid var(--color-border)', flexShrink: 0 }}>
                {(['image', 'video_preview', 'video_final'] as SubTab[]).map(tab => (
                  <button
                    key={tab}
                    className={`tab-bar-btn${activeSubTab === tab ? ' active' : ''}`}
                    onClick={() => setActiveSubTab(tab)}
                    style={{ fontSize: 12, padding: '6px 12px' }}
                  >
                    {tab === 'image' ? '画像' : tab === 'video_preview' ? 'プレビュー動画' : '最終版動画'}
                  </button>
                ))}
              </div>
              <div style={{ overflow: 'auto', flex: 1, padding: 12 }}>
                {activeSubTab === 'image' && (
                  <ImageSubTab
                    projectName={projectName}
                    scene={editScene}
                    media={media}
                    workflows={imageWorkflows}
                    onSceneChange={handleSceneChange}
                    onMediaRefresh={refreshMedia}
                  />
                )}
                {activeSubTab === 'video_preview' && (
                  <VideoSubTab
                    quality="preview"
                    projectName={projectName}
                    scene={editScene}
                    media={media}
                    workflows={videoWorkflows}
                    commonInstruction={commonInstruction}
                    onSceneChange={handleSceneChange}
                    onMediaRefresh={refreshMedia}
                  />
                )}
                {activeSubTab === 'video_final' && (
                  <VideoSubTab
                    quality="final"
                    projectName={projectName}
                    scene={editScene}
                    media={media}
                    workflows={videoWorkflows}
                    commonInstruction={commonInstruction}
                    onSceneChange={handleSceneChange}
                    onMediaRefresh={refreshMedia}
                  />
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
