import { useCallback, useRef, useState } from 'react'

/**
 * POST ベースの SSE (Server-Sent Events) フック。
 *
 * EventSource は GET のみ対応のため、fetch + ReadableStream を使用する。
 * 各 SSE イベントのデータは JSON として解析され、コールバックに渡される。
 */
export interface SSEOptions {
  /** SSE エンドポイント URL */
  url: string
  /** リクエストボディ */
  body: unknown
  /** チャンクを受け取るたびに呼ばれるコールバック */
  onChunk?: (chunk: string) => void
  /** {"type": "..."} 形式のイベントを受け取るコールバック */
  onEvent?: (data: Record<string, unknown>) => void
  /** 完了時コールバック */
  onDone?: () => void
  /** エラー時コールバック */
  onError?: (msg: string) => void
}

export function useSSE() {
  const [isStreaming, setIsStreaming] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)

  const start = useCallback((opts: SSEOptions) => {
    // 既存のストリームを中断
    if (controllerRef.current) {
      controllerRef.current.abort()
    }
    const controller = new AbortController()
    controllerRef.current = controller
    setIsStreaming(true)

    ;(async () => {
      try {
        const res = await fetch(opts.url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(opts.body),
          signal: controller.signal,
        })

        if (!res.ok) {
          const errText = await res.text()
          opts.onError?.(`HTTP ${res.status}: ${errText}`)
          return
        }

        const reader = res.body!.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          // SSE フォーマット: "data: {...}\n\n"
          const events = buffer.split('\n\n')
          buffer = events.pop() ?? ''

          for (const event of events) {
            const line = event.trim()
            if (!line.startsWith('data: ')) continue
            try {
              const data = JSON.parse(line.slice(6)) as Record<string, unknown>
              if (data.done) {
                opts.onDone?.()
                return
              }
              if (data.error) {
                opts.onError?.(String(data.error))
                return
              }
              if (typeof data.chunk === 'string') {
                opts.onChunk?.(data.chunk)
              }
              opts.onEvent?.(data)
            } catch {
              // JSON パース失敗は無視
            }
          }
        }
        opts.onDone?.()
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          opts.onError?.(String(err))
        }
      } finally {
        setIsStreaming(false)
      }
    })()
  }, [])

  const stop = useCallback(() => {
    controllerRef.current?.abort()
    controllerRef.current = null
    setIsStreaming(false)
  }, [])

  return { isStreaming, start, stop }
}
