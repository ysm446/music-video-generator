import client from './client'

export interface ModelStatus {
  is_loaded: boolean
  loaded_model_id: string | null
  vram_info: string
}

export async function getModelStatus(): Promise<ModelStatus> {
  const r = await client.get('/api/model/status')
  return r.data
}

export async function getModelPresets(): Promise<Record<string, string>> {
  const r = await client.get('/api/model/presets')
  return r.data.presets
}

export async function loadModel(modelLabel: string): Promise<{ message: string; vram_info: string }> {
  const r = await client.post('/api/model/load', { model_label: modelLabel })
  return r.data
}

export async function unloadModel(): Promise<{ message: string; vram_info: string }> {
  const r = await client.delete('/api/model')
  return r.data
}
