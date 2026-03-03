import { useEffect, useRef, useState } from 'react'

/**
 * 定期ポーリングフック。
 * `enabled` が true の間、`intervalMs` ごとに `fetcher` を呼び出す。
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  enabled: boolean,
): T | null {
  const [data, setData] = useState<T | null>(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    if (!enabled) return
    let cancelled = false

    const run = async () => {
      try {
        const result = await fetcherRef.current()
        if (!cancelled) setData(result)
      } catch {
        // エラーは無視（接続中断等）
      }
    }

    run() // 初回即時実行
    const id = setInterval(run, intervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [enabled, intervalMs])

  return data
}
