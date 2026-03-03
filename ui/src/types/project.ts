/** プロジェクトメタデータ */
export interface ProjectMeta {
  project_name: string
  duration: number
  scene_duration: number
  scene_count: number
  concept: string
  image_resolution: { width: number; height: number }
  video_resolution: { width: number; height: number }
  video_final_resolution: { width: number; height: number }
  video_fps: number
  video_frame_count: number
  comfyui_url: string
  llm_url: string
  image_workflow: string
  video_workflow: string
  music_url: string | null
  created_at: string
  updated_at: string
}

/** settings.json の内容 */
export interface ProjectSettings {
  comfyui_url: string
  llm_url: string
  image_workflow: string
  video_workflow: string
  image_resolution_w: number
  image_resolution_h: number
  video_resolution_w: number
  video_resolution_h: number
  video_final_resolution_w: number
  video_final_resolution_h: number
  video_fps: number
  video_frame_count: number
  scene_duration: number
  model: string
  export_quality: string
  export_with_music: boolean
  export_loop_music: boolean
  export_audio_fade_in: boolean
  export_audio_fade_in_sec: number
  export_audio_fade_out: boolean
  export_audio_fade_out_sec: number
  export_video_fade_out_black: boolean
  export_video_fade_out_sec: number
}

/** GET /api/config のレスポンス */
export interface AppConfig {
  defaults: ProjectSettings
  image_workflows: string[]
  video_workflows: string[]
  last_project: string | null
}
