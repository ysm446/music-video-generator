import { useEffect, useRef, useState } from 'react'
import { useSSE } from '../../hooks/useSSE'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  /** SSEエンドポイントURL */
  url: string
  /** リクエストボディ生成関数（messagesを受け取り、bodyを返す） */
  buildBody: (messages: ChatMessage[]) => unknown
  /** 初期メッセージ履歴 */
  initialMessages?: ChatMessage[]
  /** 高さ(px) */
  height?: number
  /** プレースホルダー */
  placeholder?: string
  /** エラー時コールバック */
  onError?: (msg: string) => void
  /** カスタムSSEイベント受信コールバック（chunk以外のイベント） */
  onEvent?: (data: unknown) => void
}

export default function ChatPanel({
  url,
  buildBody,
  initialMessages = [],
  height = 320,
  placeholder = 'メッセージを入力...',
  onError,
  onEvent,
}: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [input, setInput] = useState('')
  const { isStreaming, start, stop } = useSSE()
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // 新しいメッセージが追加されたら自動スクロール
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function handleSend() {
    const text = input.trim()
    if (!text || isStreaming) return

    const newMessages: ChatMessage[] = [
      ...messages,
      { role: 'user', content: text },
      { role: 'assistant', content: '' },
    ]
    setMessages(newMessages)
    setInput('')

    start({
      url,
      body: buildBody(newMessages.slice(0, -1)), // assistantの空メッセージを除く
      onEvent: (data) => {
        onEvent?.(data)
      },
      onChunk: (chunk) => {
        setMessages(prev => {
          const updated = [...prev]
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content: updated[updated.length - 1].content + chunk,
          }
          return updated
        })
      },
      onError: (msg) => {
        setMessages(prev => {
          const updated = [...prev]
          updated[updated.length - 1] = {
            ...updated[updated.length - 1],
            content: `エラー: ${msg}`,
          }
          return updated
        })
        onError?.(msg)
      },
    })
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function handleClear() {
    stop()
    setMessages([])
  }

  return (
    <div className="flex flex-col gap-2">
      {/* メッセージ表示エリア */}
      <div
        style={{
          height,
          overflowY: 'auto',
          background: 'var(--color-input-bg)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: '8px 12px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {messages.length === 0 && (
          <div className="text-muted" style={{ fontSize: 12, margin: 'auto' }}>
            チャット履歴はありません
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            style={{
              alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '85%',
              background: msg.role === 'user' ? 'var(--color-surface2)' : 'var(--color-surface)',
              border: '1px solid var(--color-border)',
              borderRadius: 'var(--radius)',
              padding: '6px 10px',
              fontSize: 13,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {msg.role === 'assistant' && msg.content === '' && isStreaming && (
              <span className="text-muted" style={{ fontSize: 11 }}>▌</span>
            )}
            {msg.content}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* 入力エリア */}
      <div className="flex gap-2">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={2}
          disabled={isStreaming}
          style={{ flex: 1, resize: 'none', minHeight: 'unset' }}
        />
        <div className="flex flex-col gap-2">
          <button
            className="btn-primary"
            onClick={handleSend}
            disabled={isStreaming || !input.trim()}
            style={{ whiteSpace: 'nowrap' }}
          >
            {isStreaming ? '送信中...' : '送信'}
          </button>
          <button
            className="btn-secondary"
            onClick={isStreaming ? stop : handleClear}
            style={{ whiteSpace: 'nowrap', fontSize: 12 }}
          >
            {isStreaming ? '停止' : 'クリア'}
          </button>
        </div>
      </div>
    </div>
  )
}
