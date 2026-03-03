import client from './client'
import type { Scene } from '../types/scene'

export interface BulkSaveRow {
  scene_id: number
  section: string
  plot: string
}

export async function getScenes(projectName: string): Promise<Scene[]> {
  const res = await client.get<{ scenes: Scene[] }>(`/api/projects/${encodeURIComponent(projectName)}/scenes`)
  return res.data.scenes
}

export async function saveScene(
  projectName: string,
  sceneId: number,
  data: Partial<Scene>,
): Promise<Scene> {
  const res = await client.put<Scene>(
    `/api/projects/${encodeURIComponent(projectName)}/scenes/${sceneId}`,
    data,
  )
  return res.data
}

export async function moveScene(
  projectName: string,
  sceneId: number,
  direction: 'up' | 'down',
): Promise<{ scenes: Scene[]; new_index: number }> {
  const res = await client.post(
    `/api/projects/${encodeURIComponent(projectName)}/scenes/${sceneId}/move`,
    { direction },
  )
  return res.data
}

export async function insertSceneAfter(
  projectName: string,
  sceneId: number,
): Promise<{ scenes: Scene[]; new_scene_id: number }> {
  const res = await client.post(
    `/api/projects/${encodeURIComponent(projectName)}/scenes/${sceneId}/insert-after`,
    {},
  )
  return res.data
}

export async function deleteScene(
  projectName: string,
  sceneId: number,
): Promise<{ scenes: Scene[] }> {
  const res = await client.delete(
    `/api/projects/${encodeURIComponent(projectName)}/scenes/${sceneId}`,
  )
  return res.data
}

export async function bulkSaveScenes(
  projectName: string,
  rows: BulkSaveRow[],
  concept?: string,
): Promise<{ updated: number; scenes: Scene[] }> {
  const res = await client.post(
    `/api/projects/${encodeURIComponent(projectName)}/scenes/bulk-save`,
    { rows: rows.map(r => [r.scene_id, r.section, r.plot]), concept },
  )
  return res.data
}
