import { useEffect, useRef, useState } from 'react'
import type { AppConfig } from '../../types/project'
import { createProject } from '../../api/projects'
import { useProject } from '../../context/ProjectContext'

interface Props {
  config: AppConfig | null
}

export default function NewProjectForm({ config }: Props) {
  const { switchProject } = useProject()

  const [name, setName] = useState('')
  const [musicFile, setMusicFile] = useState<File | null>(null)
  const [duration, setDuration] = useState<number | null>(null)
  const [sceneDuration, setSceneDuration] = useState(5)
  const [comfyuiUrl, setComfyuiUrl] = useState('http://localhost:8188')
  const [imageWf, setImageWf] = useState('')
  const [videoWf, setVideoWf] = useState('')
  const [imgW, setImgW] = useState(1280)
  const [imgH, setImgH] = useState(720)
  const [vidW, setVidW] = useState(640)
  const [vidH, setVidH] = useState(480)
  const [vidFinalW, setVidFinalW] = useState(1280)
  const [vidFinalH, setVidFinalH] = useState(720)
  const [vidFps, setVidFps] = useState(16)
  const [vidFrames, setVidFrames] = useState(81)
  const [model, setModel] = useState('qwen3-vl-4b (推奨)')

  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  const audioRef = useRef<HTMLAudioElement>(null)

  // config が読み込まれたらデフォルト値を反映
  useEffect(() => {
    if (!config) return
    const d = config.defaults
    setComfyuiUrl(d.comfyui_url)
    setImgW(d.image_resolution_w)
    setImgH(d.image_resolution_h)
    setVidW(d.video_resolution_w)
    setVidH(d.video_resolution_h)
    setVidFinalW(d.video_final_resolution_w)
    setVidFinalH(d.video_final_resolution_h)
    setVidFps(d.video_fps)
    setVidFrames(d.video_frame_count)
    setSceneDuration(d.scene_duration)
    setModel(d.model)
    if (config.image_workflows.length > 0) setImageWf(config.image_workflows[0])
    if (config.video_workflows.length > 0) setVideoWf(config.video_workflows[0])
  }, [config])

  function onMusicChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null
    setMusicFile(file)
    setDuration(null)
    if (file) {
      // ブラウザの Audio API で長さを検出
      const url = URL.createObjectURL(file)
      const audio = new Audio(url)
      audio.addEventListener('loadedmetadata', () => {
        setDuration(Math.round(audio.duration))
        URL.revokeObjectURL(url)
      })
    }
  }

  const sceneCount = duration != null ? Math.ceil(duration / sceneDuration) : null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!musicFile) { setStatus({ type: 'error', msg: '音楽ファイルを選択してください' }); return }
    if (!name.trim()) { setStatus({ type: 'error', msg: 'プロジェクト名を入力してください' }); return }

    setLoading(true)
    setStatus(null)
    try {
      const result = await createProject({
        name: name.trim(),
        music: musicFile,
        scene_duration: sceneDuration,
        comfyui_url: comfyuiUrl,
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
        model,
      })
      setStatus({ type: 'success', msg: `プロジェクト "${result.project_name}" を作成しました（${result.scene_count} シーン）` })
      await switchProject(result.project_name)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message :
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '不明なエラー'
      setStatus({ type: 'error', msg })
    } finally {
      setLoading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <div className="form-group">
        <label>プロジェクト名</label>
        <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="my_music_video" />
      </div>

      <div className="form-group">
        <label>音楽ファイル</label>
        <input type="file" accept="audio/*" onChange={onMusicChange} style={{ color: 'var(--color-text)' }} />
        {duration != null && (
          <div className="mt-2 text-muted" style={{ fontSize: 12 }}>
            長さ: {Math.floor(duration / 60)}:{String(duration % 60).padStart(2, '0')}
            {sceneCount != null && ` → ${sceneCount} シーン`}
          </div>
        )}
      </div>

      <div className="form-group">
        <label>1シーンの長さ (秒)</label>
        <div className="flex gap-2 items-center">
          <input type="range" min={3} max={10} value={sceneDuration}
            onChange={e => setSceneDuration(Number(e.target.value))}
            style={{ flex: 1 }} />
          <span style={{ minWidth: 30 }}>{sceneDuration}s</span>
        </div>
      </div>

      <div className="form-group">
        <label>ComfyUI URL</label>
        <input type="url" value={comfyuiUrl} onChange={e => setComfyuiUrl(e.target.value)} />
      </div>

      <div className="section-title mt-4">解像度設定</div>
      <div className="form-row">
        <div className="form-group">
          <label>画像 幅×高さ</label>
          <div className="flex gap-2">
            <input type="number" value={imgW} onChange={e => setImgW(Number(e.target.value))} min={64} step={1} />
            <input type="number" value={imgH} onChange={e => setImgH(Number(e.target.value))} min={64} step={1} />
          </div>
        </div>
        <div className="form-group">
          <label>プレビュー動画 幅×高さ</label>
          <div className="flex gap-2">
            <input type="number" value={vidW} onChange={e => setVidW(Number(e.target.value))} min={64} step={1} />
            <input type="number" value={vidH} onChange={e => setVidH(Number(e.target.value))} min={64} step={1} />
          </div>
        </div>
        <div className="form-group">
          <label>最終動画 幅×高さ</label>
          <div className="flex gap-2">
            <input type="number" value={vidFinalW} onChange={e => setVidFinalW(Number(e.target.value))} min={64} step={1} />
            <input type="number" value={vidFinalH} onChange={e => setVidFinalH(Number(e.target.value))} min={64} step={1} />
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
            {(config?.image_workflows ?? []).map(wf => (
              <option key={wf} value={wf}>{wf}</option>
            ))}
          </select>
        </div>
        <div className="form-group">
          <label>動画ワークフロー</label>
          <select value={videoWf} onChange={e => setVideoWf(e.target.value)}>
            {(config?.video_workflows ?? []).map(wf => (
              <option key={wf} value={wf}>{wf}</option>
            ))}
          </select>
        </div>
      </div>

      {status && (
        <div className={`alert alert-${status.type === 'success' ? 'success' : 'error'}`}>
          {status.msg}
        </div>
      )}

      <button type="submit" className="btn-primary w-full" disabled={loading}>
        {loading ? <><span className="spinner" style={{ verticalAlign: 'middle' }} /> 作成中...</> : 'プロジェクトを作成'}
      </button>
      <audio ref={audioRef} style={{ display: 'none' }} />
    </form>
  )
}
