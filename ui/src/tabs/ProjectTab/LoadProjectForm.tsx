import { useEffect, useState } from 'react'
import { listProjects } from '../../api/projects'
import { useProject } from '../../context/ProjectContext'

export default function LoadProjectForm() {
  const { switchProject, projectName } = useProject()

  const [projects, setProjects] = useState<string[]>([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<{ type: 'success' | 'error'; msg: string } | null>(null)

  async function refresh() {
    try {
      const list = await listProjects()
      setProjects(list)
      if (list.length > 0 && !selected) setSelected(list[0])
    } catch {
      // サーバー未起動時は無視
    }
  }

  useEffect(() => { refresh() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function handleLoad() {
    if (!selected) return
    setLoading(true)
    setStatus(null)
    try {
      await switchProject(selected)
      setStatus({ type: 'success', msg: `"${selected}" を読み込みました` })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message :
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? '読み込みエラー'
      setStatus({ type: 'error', msg })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="form-group">
        <label>プロジェクト</label>
        <div className="flex gap-2">
          <select
            value={selected}
            onChange={e => setSelected(e.target.value)}
            style={{ flex: 1 }}
          >
            {projects.length === 0
              ? <option value="">（プロジェクトなし）</option>
              : projects.map(p => (
                <option key={p} value={p}>{p}{p === projectName ? ' ✓' : ''}</option>
              ))
            }
          </select>
          <button type="button" className="btn-secondary" onClick={refresh} title="一覧を更新">
            🔄
          </button>
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
        onClick={handleLoad}
        disabled={loading || !selected || projects.length === 0}
      >
        {loading ? <><span className="spinner" style={{ verticalAlign: 'middle' }} /> 読込中...</> : 'プロジェクトを読み込む'}
      </button>
    </div>
  )
}
