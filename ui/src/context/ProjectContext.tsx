import React, { createContext, useCallback, useContext, useState } from 'react'
import type { ProjectMeta, ProjectSettings } from '../types/project'
import type { Scene } from '../types/scene'
import { loadProject } from '../api/projects'

interface ProjectState {
  projectName: string | null
  project: ProjectMeta | null
  settings: ProjectSettings | null
  scenes: Scene[]
}

interface ProjectContextValue extends ProjectState {
  /** プロジェクトをディスクから再読み込みする */
  reloadProject: () => Promise<void>
  /** プロジェクトを切り替える（名前だけセットして reloadProject を呼ぶ） */
  switchProject: (name: string) => Promise<void>
  /** ローカルのシーンリストを更新する（サーバー通信なし） */
  updateScenes: (scenes: Scene[]) => void
  /** ローカルの1シーンを更新する */
  updateScene: (scene: Scene) => void
}

const ProjectContext = createContext<ProjectContextValue | null>(null)

export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<ProjectState>({
    projectName: null,
    project: null,
    settings: null,
    scenes: [],
  })

  const reloadProject = useCallback(async () => {
    if (!state.projectName) return
    const data = await loadProject(state.projectName)
    setState(prev => ({
      ...prev,
      project: data.project,
      settings: data.settings,
      scenes: data.scenes,
    }))
  }, [state.projectName])

  const switchProject = useCallback(async (name: string) => {
    const data = await loadProject(name)
    setState({
      projectName: name,
      project: data.project,
      settings: data.settings,
      scenes: data.scenes,
    })
  }, [])

  const updateScenes = useCallback((scenes: Scene[]) => {
    setState(prev => ({ ...prev, scenes }))
  }, [])

  const updateScene = useCallback((scene: Scene) => {
    setState(prev => ({
      ...prev,
      scenes: prev.scenes.map(s => (s.scene_id === scene.scene_id ? scene : s)),
    }))
  }, [])

  return (
    <ProjectContext.Provider
      value={{ ...state, reloadProject, switchProject, updateScenes, updateScene }}
    >
      {children}
    </ProjectContext.Provider>
  )
}

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext)
  if (!ctx) throw new Error('useProject must be used within ProjectProvider')
  return ctx
}
