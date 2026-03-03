import { useEffect, useState } from 'react'
import { getAppConfig } from '../../api/projects'
import { useProject } from '../../context/ProjectContext'
import type { AppConfig } from '../../types/project'
import NewProjectForm from './NewProjectForm'
import LoadProjectForm from './LoadProjectForm'
import ProjectSettings from './ProjectSettings'

type SubTab = 'new' | 'load' | 'settings'

export default function ProjectTab() {
  const { projectName } = useProject()
  const [subTab, setSubTab] = useState<SubTab>('load')
  const [config, setConfig] = useState<AppConfig | null>(null)

  useEffect(() => {
    getAppConfig().then(setConfig).catch(() => {})
  }, [])

  // プロジェクトが読み込まれたら設定タブをアクティブに
  useEffect(() => {
    if (projectName) setSubTab('settings')
  }, [projectName])

  const SUB_TABS: { id: SubTab; label: string }[] = [
    { id: 'load', label: '読込' },
    { id: 'new', label: '新規作成' },
    { id: 'settings', label: '基本設定' },
  ]

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <h2 style={{ marginBottom: 16 }}>プロジェクト</h2>

      {/* サブタブ */}
      <div className="flex gap-2 mb-4">
        {SUB_TABS.map(t => (
          <button
            key={t.id}
            className={t.id === subTab ? 'btn-primary' : 'btn-secondary'}
            onClick={() => setSubTab(t.id)}
            disabled={t.id === 'settings' && !projectName}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="card">
        {subTab === 'new' && <NewProjectForm config={config} />}
        {subTab === 'load' && <LoadProjectForm />}
        {subTab === 'settings' && <ProjectSettings />}
      </div>
    </div>
  )
}
