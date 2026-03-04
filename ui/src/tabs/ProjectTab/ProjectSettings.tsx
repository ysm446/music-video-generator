import { useEffect, useState } from 'react'
import { useProject } from '../../context/ProjectContext'
import { saveProjectSettings, getWorkflows, replaceMusic } from '../../api/projects'

export default function ProjectSettings() {
  const { project, settings, projectName, reloadProject } = useProject()

  const [comfyuiUrl, setComfyuiUrl] = useState('')
  const [concept, setConcept] = useState('')
  const [imgW, setImgW] = useState(1280)
  const [imgH, setImgH] = useState(720)
  const [vidW, setVidW] = useState(640)
  const [vidH, setVidH] = useState(480)
  const [vidFinalW, setVidFinalW] = useState(1280)
  const [vidFinalH, setVidFinalH] = useState(720)
  const [vidFps, setVidFps] = useState(16)
  const [vidFrames, setVidFrames] = useState(81)
  const [imageWf, setImageWf] = useState('')
  const [videoWf, setVideoWf] = useState('')
  const [imageWorkflows, setImageWorkflows] = useState<string[]>([])
  const [videoWorkflows, setVideoWorkflows] = useState<string[]>([])

  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)
  const [musicUploading, setMusicUploading] = useState(false)
  const [musicStatus, setMusicStatus] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  // プロジェクトが変わったら設定を反映
  useEffect(() => {
    if (!project || !settings) return
    setComfyuiUrl(settings.comfyui_url)
    setConcept(project.concept)
    setImgW(settings.image_resolution_w)
    setImgH(settings.image_resolution_h)
    setVidW(settings.video_resolution_w)
    setVidH(settings.video_resolution_h)
    setVidFinalW(settings.video_final_resolution_w)
    setVidFinalH(settings.video_final_resolution_h)
    setVidFps(settings.video_fps)
    setVidFrames(settings.video_frame_count)
    setImageWf(settings.image_workflow)
    setVideoWf(settings.video_workflow)
  }, [project, settings])

  // ワークフロー一覧を取得
  useEffect(() => {
    if (!projectName) return
    getWorkflows(projectName).then(wf => {
      setImageWorkflows(wf.image)
      setVideoWorkflows(wf.video)
    }).catch(() => {})
  }, [projectName])

  async function handleSave() {
    if (!projectName) return
    setSaving(true)
    setStatus(null)
    try {
      await saveProjectSettings(projectName, {
        comfyui_url: comfyuiUrl,
        concept,
        image_resolution_w: imgW,
        image_resolution_h: imgH,
        video_resolution_w: vidW,
        video_resolution_h: vidH,
        video_final_resolution_w: vidFinalW,
        video_final_resolution_h: vidFinalH,
        video_fps: vidFps,
        video_frame_count: vidFrames,
        image_workflow: imageWf,
        video_workflow: videoWf,
      })
      await reloadProject()
      setStatus({ type: 'success', msg: '設定を保存しました' })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message :
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '保存エラー'
      setStatus({ type: 'error', msg })
    } finally {
      setSaving(false)
    }
  }

  if (!project) return null

  return (
    <div>
      {/* プロジェクト情報（読み取り専用） */}
      <div className="card mb-4" style={{ background: 'var(--color-input-bg)', fontSize: 12 }}>
        <div className="flex gap-4">
          <span><span className="text-muted">長さ: </span>{Math.floor(project.duration / 60)}:{String(Math.round(project.duration % 60)).padStart(2, '0')}</span>
          <span><span className="text-muted">シーン数: </span>{project.scene_count}</span>
          <span><span className="text-muted">作成: </span>{project.created_at.slice(0, 10)}</span>
        </div>
        {project.music_url && (
          <div className="mt-2">
            <audio controls src={project.music_url} style={{ width: '100%', height: 32 }} />
          </div>
        )}
        <div className="flex gap-2 items-center mt-2">
          <input
            type="file"
            accept="audio/*"
            disabled={musicUploading}
            style={{ fontSize: 12, flex: 1 }}
            onChange={async (e) => {
              const file = e.target.files?.[0]
              if (!file || !projectName) return
              setMusicUploading(true)
              setMusicStatus(null)
              try {
                const r = await replaceMusic(projectName, file)
                await reloadProject()
                setMusicStatus({ type: 'success', msg: `差し替え完了（${Math.floor(r.duration / 60)}:${String(Math.round(r.duration % 60)).padStart(2, '0')}）` })
              } catch (err: unknown) {
                const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '差し替えエラー'
                setMusicStatus({ type: 'error', msg })
              } finally {
                setMusicUploading(false)
                e.target.value = ''
              }
            }}
          />
          {musicUploading && <span className="spinner" style={{ verticalAlign: 'middle' }} />}
          {musicStatus && (
            <span className={musicStatus.type === 'success' ? 'text-success' : 'text-error'} style={{ fontSize: 12 }}>
              {musicStatus.msg}
            </span>
          )}
        </div>
      </div>

      <div className="form-group">
        <label>コンセプト</label>
        <textarea
          value={concept}
          onChange={e => setConcept(e.target.value)}
          placeholder="MVの全体コンセプトを記述..."
          rows={3}
        />
      </div>

      <div className="form-group">
        <label>ComfyUI URL</label>
        <input type="url" value={comfyuiUrl} onChange={e => setComfyuiUrl(e.target.value)} />
      </div>

      <div className="section-title mt-4">解像度</div>
      <div className="form-row">
        <div className="form-group">
          <label>画像 幅×高さ</label>
          <div className="flex gap-2">
            <input type="number" value={imgW} onChange={e => setImgW(Number(e.target.value))} min={64} step={64} />
            <input type="number" value={imgH} onChange={e => setImgH(Number(e.target.value))} min={64} step={64} />
          </div>
        </div>
        <div className="form-group">
          <label>プレビュー動画 幅×高さ</label>
          <div className="flex gap-2">
            <input type="number" value={vidW} onChange={e => setVidW(Number(e.target.value))} min={64} step={64} />
            <input type="number" value={vidH} onChange={e => setVidH(Number(e.target.value))} min={64} step={64} />
          </div>
        </div>
        <div className="form-group">
          <label>最終動画 幅×高さ</label>
          <div className="flex gap-2">
            <input type="number" value={vidFinalW} onChange={e => setVidFinalW(Number(e.target.value))} min={64} step={64} />
            <input type="number" value={vidFinalH} onChange={e => setVidFinalH(Number(e.target.value))} min={64} step={64} />
          </div>
        </div>
      </div>

      <div className="form-row">
        <div className="form-group">
          <label>FPS</label>
          <input type="number" value={vidFps} onChange={e => setVidFps(Number(e.target.value))} min={8} max={30} />
        </div>
        <div className="form-group">
          <label>フレーム数</label>
          <input type="number" value={vidFrames} onChange={e => setVidFrames(Number(e.target.value))} min={9} step={4} />
        </div>
      </div>

      <div className="section-title mt-4">ワークフロー</div>
      <div className="form-row">
        <div className="form-group">
          <label>画像ワークフロー</label>
          <select value={imageWf} onChange={e => setImageWf(e.target.value)}>
            <option value="">(デフォルト)</option>
            {imageWorkflows.map(wf => <option key={wf} value={wf}>{wf}</option>)}
          </select>
        </div>
        <div className="form-group">
          <label>動画ワークフロー</label>
          <select value={videoWf} onChange={e => setVideoWf(e.target.value)}>
            <option value="">(デフォルト)</option>
            {videoWorkflows.map(wf => <option key={wf} value={wf}>{wf}</option>)}
          </select>
        </div>
      </div>

      {status && (
        <div className={`alert alert-${status.type === 'success' ? 'success' : 'error'}`}>
          {status.msg}
        </div>
      )}

      <button
        type="button"
        className="btn-primary w-full"
        onClick={handleSave}
        disabled={saving}
      >
        {saving ? <><span className="spinner" style={{ verticalAlign: 'middle' }} /> 保存中...</> : '設定を保存'}
      </button>
    </div>
  )
}
