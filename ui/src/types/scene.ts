/** scene.json / Scene データクラスのフロントエンド型定義 */
export interface Scene {
  scene_id: number
  start_time: number
  end_time: number
  order: number
  enabled: boolean
  section: string
  lyrics: string
  plot: string
  image_prompt: string
  image_negative: string
  image_seed: number
  image_workflow: string | null
  active_image_version: string
  video_prompt: string
  video_negative: string
  video_seed: number
  video_workflow: string | null
  active_video_preview_version: string
  active_video_final_version: string
  video_instruction: string
  status: 'empty' | 'plot_done' | 'image_done' | 'video_done'
  notes: string
}

export const STATUS_ICONS: Record<Scene['status'], string> = {
  empty: '○',
  plot_done: '●',
  image_done: '🖼',
  video_done: '✅',
}
