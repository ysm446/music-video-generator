import client from './client'

export interface ThumbnailItem {
  scene_id: number
  url: string | null
}

export interface OutputItem {
  filename: string
  url: string
  size: number
}

export interface ExportRequest {
  output_kind: 'preview' | 'final'
  with_music: boolean
  loop_music: boolean
  audio_fade_in: boolean
  audio_fade_in_sec: number
  audio_fade_out: boolean
  audio_fade_out_sec: number
  video_fade_out_black: boolean
  video_fade_out_sec: number
}

export async function getThumbnails(projectName: string): Promise<ThumbnailItem[]> {
  const r = await client.get(`/api/projects/${projectName}/export/thumbnails`)
  return r.data.thumbnails
}

export async function getOutputs(projectName: string): Promise<OutputItem[]> {
  const r = await client.get(`/api/projects/${projectName}/export/outputs`)
  return r.data.outputs
}

/** SSEで書き出し進捗を受け取るため、fetchを使って直接呼ぶ。
 *  返り値はAbortController（キャンセル用）。*/
export function startExport(
  projectName: string,
  req: ExportRequest,
  onMessage: (data: { type?: string; message?: string; url?: string; done?: boolean }) => void,
  onError?: (err: string) => void,
): AbortController {
  const controller = new AbortController()
  ;(async () => {
    try {
      const res = await fetch(`/api/projects/${projectName}/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        onError?.(`HTTP ${res.status}`)
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop() ?? ''
        for (const event of events) {
          if (!event.startsWith('data: ')) continue
          try {
            const data = JSON.parse(event.slice(6))
            onMessage(data)
          } catch { /* ignore */ }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        onError?.(String(e))
      }
    }
  })()
  return controller
}
