import { useEffect, useRef, useState } from 'react'
import { useProject } from '../../context/ProjectContext'
import {
  getThumbnails,
  getOutputs,
  startExport,
  type ThumbnailItem,
  type OutputItem,
  type ExportRequest,
} from '../../api/export'

const DEFAULT_REQ: Omit<ExportRequest, 'output_kind'> = {
  with_music: true,
  loop_music: false,
  audio_fade_in: false,
  audio_fade_in_sec: 1.0,
  audio_fade_out: false,
  audio_fade_out_sec: 1.0,
  video_fade_out_black: false,
  video_fade_out_sec: 1.0,
}

export default function ExportTab() {
  const { projectName } = useProject()

  const [thumbnails, setThumbnails] = useState<ThumbnailItem[]>([])
  const [outputs, setOutputs] = useState<OutputItem[]>([])
  const [req, setReq] = useState(DEFAULT_REQ)
  const [status, setStatus] = useState('')
  const [isExporting, setIsExporting] = useState(false)
  const [outputUrl, setOutputUrl] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!projectName) return
    getThumbnails(projectName).then(setThumbnails).catch(() => {})
    getOutputs(projectName).then(setOutputs).catch(() => {})
  }, [projectName])

  function refreshOutputs() {
    if (!projectName) return
    getOutputs(projectName).then(setOutputs).catch(() => {})
  }

  function handleExport(kind: 'preview' | 'final') {
    if (!projectName || isExporting) return
    setIsExporting(true)
    setStatus('書き出し開始...')
    setOutputUrl(null)

    abortRef.current = startExport(
      projectName,
      { ...req, output_kind: kind },
      (data) => {
        if (data.done) {
          setIsExporting(false)
          refreshOutputs()
          return
        }
        if (data.message) setStatus(data.message)
        if (data.url) setOutputUrl(data.url)
        if (data.type === 'error') setIsExporting(false)
      },
      (err) => {
        setStatus(`エラー: ${err}`)
        setIsExporting(false)
      },
    )
  }

  function handleStop() {
    abortRef.current?.abort()
    setIsExporting(false)
    setStatus('キャンセルしました')
  }

  if (!projectName) {
    return <div className="card text-muted">プロジェクトを読み込んでください</div>
  }

  return (
    <div className="flex flex-col gap-4" style={{ height: 'calc(100vh - 100px)', overflowY: 'auto' }}>
      {/* サムネイルギャラリー */}
      <div className="card">
        <div className="flex items-center justify-between" style={{ marginBottom: 12 }}>
          <h3>シーン一覧（{thumbnails.length}シーン）</h3>
          <button className="btn-secondary" style={{ fontSize: 12 }} onClick={() => {
            getThumbnails(projectName).then(setThumbnails).catch(() => {})
          }}>
            🔄 更新
          </button>
        </div>
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
          gap: 8,
          maxHeight: 320,
          overflowY: 'auto',
        }}>
          {thumbnails.map(t => (
            <div key={t.scene_id} style={{ textAlign: 'center' }}>
              {t.url ? (
                <img
                  src={t.url}
                  alt={`Scene ${t.scene_id}`}
                  style={{ width: '100%', aspectRatio: '16/9', objectFit: 'cover', border: '1px solid var(--color-border)' }}
                />
              ) : (
                <div style={{
                  width: '100%',
                  aspectRatio: '16/9',
                  background: 'var(--color-surface)',
                  border: '1px solid var(--color-border)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 10,
                  color: 'var(--color-muted)',
                }}>
                  画像なし
                </div>
              )}
              <div style={{ fontSize: 10, color: 'var(--color-muted)', marginTop: 2 }}>#{t.scene_id}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 書き出し設定 */}
      <div className="card">
        <h3 style={{ marginBottom: 12 }}>書き出し設定</h3>

        <div className="flex gap-4 flex-wrap">
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={req.with_music} onChange={e => setReq(r => ({ ...r, with_music: e.target.checked }))} />
            音楽を合成
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={req.loop_music} onChange={e => setReq(r => ({ ...r, loop_music: e.target.checked }))} disabled={!req.with_music} />
            音楽をループ
          </label>
        </div>

        <div className="section-title mt-3">音声フェード</div>
        <div className="flex gap-4 flex-wrap" style={{ marginTop: 6 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={req.audio_fade_in} onChange={e => setReq(r => ({ ...r, audio_fade_in: e.target.checked }))} />
            フェードイン
          </label>
          {req.audio_fade_in && (
            <div className="flex items-center gap-2" style={{ fontSize: 13 }}>
              <span>時間:</span>
              <input type="number" value={req.audio_fade_in_sec} onChange={e => setReq(r => ({ ...r, audio_fade_in_sec: Number(e.target.value) }))} min={0.1} max={10} step={0.1} style={{ width: 70 }} />
              <span>秒</span>
            </div>
          )}
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={req.audio_fade_out} onChange={e => setReq(r => ({ ...r, audio_fade_out: e.target.checked }))} />
            フェードアウト
          </label>
          {req.audio_fade_out && (
            <div className="flex items-center gap-2" style={{ fontSize: 13 }}>
              <span>時間:</span>
              <input type="number" value={req.audio_fade_out_sec} onChange={e => setReq(r => ({ ...r, audio_fade_out_sec: Number(e.target.value) }))} min={0.1} max={10} step={0.1} style={{ width: 70 }} />
              <span>秒</span>
            </div>
          )}
        </div>

        <div className="section-title mt-3">映像エフェクト</div>
        <div className="flex gap-4 flex-wrap" style={{ marginTop: 6 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={req.video_fade_out_black} onChange={e => setReq(r => ({ ...r, video_fade_out_black: e.target.checked }))} />
            末尾をブラックフェードアウト
          </label>
          {req.video_fade_out_black && (
            <div className="flex items-center gap-2" style={{ fontSize: 13 }}>
              <span>時間:</span>
              <input type="number" value={req.video_fade_out_sec} onChange={e => setReq(r => ({ ...r, video_fade_out_sec: Number(e.target.value) }))} min={0.1} max={10} step={0.1} style={{ width: 70 }} />
              <span>秒</span>
            </div>
          )}
        </div>

        <div className="flex gap-3 items-center mt-4">
          <button className="btn-primary" onClick={() => handleExport('preview')} disabled={isExporting}>
            プレビュー版を書き出し
          </button>
          <button className="btn-secondary" onClick={() => handleExport('final')} disabled={isExporting}>
            最終版を書き出し
          </button>
          {isExporting && (
            <button className="btn-secondary" onClick={handleStop} style={{ color: 'var(--color-error)' }}>
              キャンセル
            </button>
          )}
        </div>

        {status && (
          <div className={`mt-2 ${status.includes('エラー') ? 'text-error' : 'text-success'}`} style={{ fontSize: 13 }}>
            {status}
          </div>
        )}

        {outputUrl && (
          <div className="mt-3">
            <video src={outputUrl} controls style={{ width: '100%', maxHeight: 360 }} />
          </div>
        )}
      </div>

      {/* 書き出し済みファイル一覧 */}
      {outputs.length > 0 && (
        <div className="card">
          <h3 style={{ marginBottom: 10 }}>書き出し済みファイル</h3>
          <div className="flex flex-col gap-2">
            {outputs.map(o => (
              <div key={o.filename} className="flex items-center gap-3" style={{ fontSize: 13 }}>
                <a href={o.url} download={o.filename} className="btn-secondary" style={{ fontSize: 12 }}>
                  ⬇ {o.filename}
                </a>
                <span className="text-muted" style={{ fontSize: 11 }}>
                  {(o.size / 1024 / 1024).toFixed(1)} MB
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
