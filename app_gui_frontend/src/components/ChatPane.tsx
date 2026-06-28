import { useEffect, useState } from 'react'
import { fetchPayload, type ContextEvent } from '../api'

export function ChatPane({ events, sessionId }: { events: ContextEvent[], sessionId: string }) {
  const last = events.length ? events[events.length - 1] : null
  const [fullPayload, setFullPayload] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      setFullPayload(null)
      const content = last?.payload?.message?.content
      const ref = content?.payload_ref
      if (sessionId && ref?.id) {
        try {
          const text = await fetchPayload(sessionId, ref.id)
          if (!cancelled) setFullPayload(text)
        } catch {
          if (!cancelled) setFullPayload(renderContent(content))
        }
      }
    }
    load()
    return () => { cancelled = true }
  }, [last?.event_id, sessionId])

  const content = fullPayload ?? (last ? renderContent(last.payload?.message?.content) : '')
  return <div className="chat-pane">
    {!last && <div className="empty-state">这里仅展示 Assistant 的最后回复。完整上下文请看左侧 Current Model Context。</div>}
    {last && <div className="bubble assistant last-answer">
      <div className="bubble-role">assistant 最后回复</div>
      <pre>{content || '(assistant 暂无文本结果，可能仍在工具调用中)'}</pre>
    </div>}
  </div>
}

function renderContent(value: any): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value?.payload_ref) {
    const ref = value.payload_ref
    return ref.preview || `[内容较长，正在载入完整回答：${ref.size ?? '?'} chars]`
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
