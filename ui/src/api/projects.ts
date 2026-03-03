import client from './client'
import type { AppConfig, ProjectMeta, ProjectSettings } from '../types/project'
import type { Scene } from '../types/scene'

export interface ProjectLoadResponse {
  project: ProjectMeta
  settings: ProjectSettings
  scenes: Scene[]
}

export interface CreateProjectParams {
  name: string
  music: File
  scene_duration?: number
  comfyui_url?: string
  llm_url?: string
  image_resolution_w?: number
  image_resolution_h?: number
  video_resolution_w?: number
  video_resolution_h?: number
  video_final_resolution_w?: number
  video_final_resolution_h?: number
  video_fps?: number
  video_frame_count?: number
  image_workflow?: string
  video_workflow?: string
  model?: string
}

/** プロジェクト名一覧を取得する */
export async function listProjects(): Promise<string[]> {
  const res = await client.get<{ projects: string[] }>('/api/projects')
  return res.data.projects
}

/** 新しいプロジェクトを作成する */
export async function createProject(params: CreateProjectParams): Promise<{
  project_name: string
  scene_count: number
  duration: number
}> {
  const form = new FormData()
  form.append('name', params.name)
  form.append('music', params.music)
  if (params.scene_duration != null) form.append('scene_duration', String(params.scene_duration))
  if (params.comfyui_url) form.append('comfyui_url', params.comfyui_url)
  if (params.llm_url) form.append('llm_url', params.llm_url)
  if (params.image_resolution_w != null) form.append('image_resolution_w', String(params.image_resolution_w))
  if (params.image_resolution_h != null) form.append('image_resolution_h', String(params.image_resolution_h))
  if (params.video_resolution_w != null) form.append('video_resolution_w', String(params.video_resolution_w))
  if (params.video_resolution_h != null) form.append('video_resolution_h', String(params.video_resolution_h))
  if (params.video_final_resolution_w != null) form.append('video_final_resolution_w', String(params.video_final_resolution_w))
  if (params.video_final_resolution_h != null) form.append('video_final_resolution_h', String(params.video_final_resolution_h))
  if (params.video_fps != null) form.append('video_fps', String(params.video_fps))
  if (params.video_frame_count != null) form.append('video_frame_count', String(params.video_frame_count))
  if (params.image_workflow) form.append('image_workflow', params.image_workflow)
  if (params.video_workflow) form.append('video_workflow', params.video_workflow)
  if (params.model) form.append('model', params.model)

  const res = await client.post('/api/projects', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return res.data
}

/** プロジェクトを読み込む */
export async function loadProject(name: string): Promise<ProjectLoadResponse> {
  const res = await client.get<ProjectLoadResponse>(`/api/projects/${encodeURIComponent(name)}`)
  return res.data
}

/** プロジェクト設定を保存する */
export async function saveProjectSettings(
  name: string,
  settings: Partial<ProjectSettings & { concept: string }>,
): Promise<void> {
  await client.put(`/api/projects/${encodeURIComponent(name)}/settings`, settings)
}

/** ワークフロー一覧を取得する */
export async function getWorkflows(name: string): Promise<{
  image: string[]
  video: string[]
}> {
  const res = await client.get(`/api/projects/${encodeURIComponent(name)}/workflows`)
  return res.data
}

/** アプリのデフォルト設定を取得する */
export async function getAppConfig(): Promise<AppConfig> {
  const res = await client.get<AppConfig>('/api/config')
  return res.data
}

/** 最後に開いたプロジェクト名を取得する */
export async function getLastProject(): Promise<string | null> {
  const res = await client.get<{ last_project: string | null }>('/api/projects/last')
  return res.data.last_project
}
