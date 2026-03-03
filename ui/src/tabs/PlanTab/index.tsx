import { useEffect, useState } from 'react'
import { useProject } from '../../context/ProjectContext'
import { saveProjectSettings } from '../../api/projects'
import { bulkSaveScenes } from '../../api/scenes'
import ChatPanel, { type ChatMessage } from '../../components/common/ChatPanel'
import ScenePlanTable, { type SceneRow } from './ScenePlanTable'
import BulkPlanPanel from './BulkPlanPanel'

export default function PlanTab() {
  const { project, settings, scenes, projectName, reloadProject, updateScenes } = useProject()

  const [concept, setConcept] = useState('')
  const [imgCommonPrompt, setImgCommonPrompt] = useState('')
  const [vidCommonInstruction, setVidCommonInstruction] = useState('')
  const [pendingRows, setPendingRows] = useState<SceneRow[]>([])
  const [saveStatus, setSaveStatus] = useState('')
  const [commonSaveStatus, setCommonSaveStatus] = useState('')

  useEffect(() => {
    if (!project) return
    setConcept(project.concept || '')
    const s = (settings ?? {}) as Record<string, unknown>
    setImgCommonPrompt(typeof s?.batch_image_prompt_common === 'string' ? s.batch_image_prompt_common : '')
    setVidCommonInstruction(typeof s?.batch_video_prompt_common_instruction === 'string' ? s.batch_video_prompt_common_instruction : '')
    setPendingRows([])
    setSaveStatus('')
  }, [project, settings])

  if (!projectName || !project) {
    return <div className="card text-muted">プロジェクトを読み込んでください</div>
  }

  function handleRowsChange(rows: SceneRow[]) {
    setPendingRows(rows)
  }

  function handleSceneProposed(sceneId: number, section: string, plot: string) {
    setPendingRows(prev => {
      const base: SceneRow[] = prev.length > 0
        ? prev
        : scenes.map(s => ({
            scene_id: s.scene_id,
            time: `${s.start_time.toFixed(1)}s-${s.end_time.toFixed(1)}s`,
            section: s.section,
            plot: s.plot,
            status: s.status,
          }))
      return base.map(r =>
        r.scene_id === sceneId ? { ...r, section, plot } : r
      )
    })
  }

  async function handleSaveAll() {
    if (!projectName) return
    setSaveStatus('保存中...')
    try {
      const rows = pendingRows.length > 0
        ? pendingRows
        : scenes.map(s => ({ scene_id: s.scene_id, section: s.section, plot: s.plot }))
      const result = await bulkSaveScenes(projectName, rows, concept)
      updateScenes(result.scenes)
      setPendingRows([])
      setSaveStatus(`${result.updated}シーンを保存しました`)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(err)
      setSaveStatus(`エラー: ${msg}`)
    }
  }

  async function handleSaveCommon() {
    if (!projectName) return
    setCommonSaveStatus('保存中...')
    try {
      await saveProjectSettings(projectName, {
        batch_image_prompt_common: imgCommonPrompt,
        batch_video_prompt_common_instruction: vidCommonInstruction,
      } as never)
      setCommonSaveStatus('保存しました')
    } catch {
      setCommonSaveStatus('保存エラー')
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '380px 1fr', gap: 16, height: 'calc(100vh - 100px)' }}>
      {/* 左カラム: LLMチャット + 設定 */}
      <div className="flex flex-col gap-3" style={{ minWidth: 0, overflowY: 'auto' }}>
        <div className="card">
          <h3 style={{ marginBottom: 12 }}>LLMチャット（コンセプト相談）</h3>
          <ChatPanel
            url="/api/llm/chat-stream"
            buildBody={(msgs: ChatMessage[]) => ({
              messages: msgs.map(m => ({ role: m.role, content: m.content })),
              project_name: projectName,
            })}
            height={260}
            placeholder="コンセプトについて相談..."
          />
        </div>

        <div className="card">
          <div className="form-group">
            <label>全体コンセプト</label>
            <textarea
              value={concept}
              onChange={e => setConcept(e.target.value)}
              rows={3}
              placeholder="MVの全体コンセプト..."
            />
          </div>

          <div className="form-group">
            <label>画像プロンプト共通文</label>
            <textarea
              value={imgCommonPrompt}
              onChange={e => setImgCommonPrompt(e.target.value)}
              rows={2}
              placeholder="hyper-realistic, tilt-shift lens..."
            />
          </div>

          <div className="form-group">
            <label>動画プロンプト共通追加指示</label>
            <textarea
              value={vidCommonInstruction}
              onChange={e => setVidCommonInstruction(e.target.value)}
              rows={2}
              placeholder="subtle camera movement..."
            />
          </div>

          <div className="flex gap-2 items-center">
            <button className="btn-secondary" onClick={handleSaveCommon}>
              共通設定を保存
            </button>
            {commonSaveStatus && (
              <span className={commonSaveStatus.includes('エラー') ? 'text-error' : 'text-success'} style={{ fontSize: 12 }}>
                {commonSaveStatus}
              </span>
            )}
          </div>
        </div>

        <div className="card">
          <h3 style={{ marginBottom: 8 }}>一括提案</h3>
          <BulkPlanPanel
            projectName={projectName}
            concept={concept}
            onSceneProposed={handleSceneProposed}
          />
        </div>
      </div>

      {/* 右カラム: シーン計画テーブル */}
      <div className="card flex flex-col gap-3" style={{ minWidth: 0, overflow: 'hidden' }}>
        <div className="flex items-center justify-between" style={{ flexShrink: 0 }}>
          <h3>シーン計画一覧（{scenes.length}シーン）</h3>
          <div className="flex gap-2 items-center">
            {saveStatus && (
              <span className={saveStatus.includes('エラー') ? 'text-error' : 'text-success'} style={{ fontSize: 12 }}>
                {saveStatus}
              </span>
            )}
            <button className="btn-secondary" onClick={() => reloadProject()} style={{ fontSize: 12 }}>
              🔄 更新
            </button>
            <button className="btn-primary" onClick={handleSaveAll}>
              全シーンを保存
            </button>
          </div>
        </div>

        <div style={{ overflow: 'auto', flex: 1 }}>
          <ScenePlanTable
            scenes={scenes}
            onChange={handleRowsChange}
          />
        </div>
      </div>
    </div>
  )
}
