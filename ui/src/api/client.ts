import axios from 'axios'

/**
 * axios インスタンス。
 * - Electron (file://) から呼ぶ場合も含め、baseURL は空文字にしておく。
 * - Vite の proxy 設定により開発時は /api → http://127.0.0.1:8000 に転送される。
 * - 本番（Electron + FastAPI）では FastAPI が同一オリジンで /api を提供する。
 */
const client = axios.create({
  baseURL: '',
  headers: {
    'Content-Type': 'application/json',
  },
})

export default client

/** プロジェクトディレクトリ内のファイルを参照する URL を生成する */
export function fileUrl(projectRelPath: string): string {
  return `/api/files/${projectRelPath}`
}

/** scene_id から scene ディレクトリ名を生成する */
export function sceneDirName(sceneId: number): string {
  return `scene_${String(sceneId).padStart(3, '0')}`
}

/** シーンの画像 URL を生成する */
export function sceneImageUrl(projectName: string, sceneId: number): string {
  return fileUrl(`${projectName}/scenes/${sceneDirName(sceneId)}/image.png`)
}

/** シーンのプレビュー動画 URL を生成する */
export function sceneVideoPreviewUrl(projectName: string, sceneId: number): string {
  return fileUrl(`${projectName}/scenes/${sceneDirName(sceneId)}/video_preview.mp4`)
}

/** シーンの最終動画 URL を生成する */
export function sceneVideoFinalUrl(projectName: string, sceneId: number): string {
  return fileUrl(`${projectName}/scenes/${sceneDirName(sceneId)}/video_final.mp4`)
}
