import { useEffect, useState } from 'react'
import { ProjectProvider, useProject } from './context/ProjectContext'
import ProjectTab from './tabs/ProjectTab'
import PlanTab from './tabs/PlanTab'
import GenerateTab from './tabs/GenerateTab'
import ExportTab from './tabs/ExportTab'
import ModelTab from './tabs/ModelTab'
import { getAppConfig } from './api/projects'

const TABS = [
  { id: 'project', label: 'プロジェクト' },
  { id: 'plan', label: '計画' },
  { id: 'generate', label: '生成・編集' },
  { id: 'export', label: '書き出し' },
  { id: 'model', label: 'モデル管理' },
] as const

type TabId = (typeof TABS)[number]['id']

function AppInner() {
  const [activeTab, setActiveTab] = useState<TabId>('project')
  const { projectName, switchProject } = useProject()

  // 起動時に最後に開いたプロジェクトを自動読込
  useEffect(() => {
    getAppConfig().then(cfg => {
      if (cfg.last_project) {
        switchProject(cfg.last_project).catch(() => {/* 存在しない場合は無視 */})
      }
    }).catch(() => {/* サーバー未起動時は無視 */})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const projectRequired = (tab: TabId) =>
    tab !== 'project' && !projectName

  return (
    <div className="app-layout">
      {/* タブバー */}
      <div className="tab-bar">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={`tab-bar-btn${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
            disabled={projectRequired(tab.id)}
            title={projectRequired(tab.id) ? 'プロジェクトを先に読み込んでください' : undefined}
          >
            {tab.label}
          </button>
        ))}
        {projectName && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center' }}>
            <span className="text-muted" style={{ fontSize: 12 }}>
              📁 {projectName}
            </span>
          </div>
        )}
      </div>

      {/* タブコンテンツ */}
      <div className="tab-content">
        {activeTab === 'project' && <ProjectTab />}
        {activeTab === 'plan' && <PlanTab />}
        {activeTab === 'generate' && <GenerateTab />}
        {activeTab === 'export' && <ExportTab />}
        {activeTab === 'model' && <ModelTab />}
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ProjectProvider>
      <AppInner />
    </ProjectProvider>
  )
}
