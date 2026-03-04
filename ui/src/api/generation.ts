import client from './client'

export interface SceneMedia {
  image_url: string | null
  video_preview_url: string | null
  video_final_url: string | null
  active_image_version: string
  active_video_preview_version: string
  active_video_final_version: string
  image_versions: string[]
  video_versions_preview: string[]
  video_versions_final: string[]
  status: string
}

export interface QueueStatus {
  running: string | null
  pending: number
  logs: string[]
  dirty: boolean
}

export interface BatchStatus {
  state: 'idle' | 'running' | 'done'
  mode: string
  current_task: string
  elapsed: string
  total_time: string
  logs: string[]
}

export async function enqueueGenerate(
  projectName: string,
  sceneId: number,
  target: 'image' | 'video' | 'both',
  videoQuality: 'preview' | 'final' = 'preview',
): Promise<{ message: string; queue: QueueStatus }> {
  const r = await client.post(`/api/projects/${projectName}/scenes/${sceneId}/generate`, {
    target,
    video_quality: videoQuality,
  })
  return r.data
}

export async function getSceneMedia(projectName: string, sceneId: number): Promise<SceneMedia> {
  const r = await client.get(`/api/projects/${projectName}/scenes/${sceneId}/media`)
  return r.data
}

export async function getImageSeed(projectName: string, sceneId: number): Promise<number> {
  const r = await client.get(`/api/projects/${projectName}/scenes/${sceneId}/image-seed`)
  return r.data.seed
}

export async function getVideoSeed(projectName: string, sceneId: number, quality: 'preview' | 'final'): Promise<number> {
  const r = await client.get(`/api/projects/${projectName}/scenes/${sceneId}/video-seed?quality=${quality}`)
  return r.data.seed
}

export async function getQueueStatus(): Promise<QueueStatus> {
  const r = await client.get('/api/queue/status')
  return r.data
}

export async function useVersion(
  projectName: string,
  sceneId: number,
  versionName: string,
  mediaType: 'image' | 'video_preview' | 'video_final',
): Promise<SceneMedia> {
  const r = await client.post(`/api/projects/${projectName}/scenes/${sceneId}/use-version`, {
    version_name: versionName,
    media_type: mediaType,
  })
  return r.data
}

export async function deleteVersion(
  projectName: string,
  sceneId: number,
  versionName: string,
  mediaType: 'image' | 'video_preview' | 'video_final',
): Promise<SceneMedia> {
  const r = await client.delete(`/api/projects/${projectName}/scenes/${sceneId}/version`, {
    data: { version_name: versionName, media_type: mediaType },
  })
  return r.data
}

export async function startBatch(
  projectName: string,
  target: string,
  videoQuality: 'preview' | 'final' = 'preview',
): Promise<{ message: string }> {
  const r = await client.post('/api/batch/start', {
    project_name: projectName,
    target,
    video_quality: videoQuality,
  })
  return r.data
}

export async function stopBatch(): Promise<{ message: string }> {
  const r = await client.post('/api/batch/stop')
  return r.data
}

export async function getBatchStatus(): Promise<BatchStatus> {
  const r = await client.get('/api/batch/status')
  return r.data
}
