import { useEffect, useMemo, useState } from 'react'
import { fetchPayload, type ContextEvent } from '../api'

type ChatItem = {
  id: string
  role: 'user' | 'assistant'
  content: any
}

export function ChatPane({ events, sessionId }: { events: ContextEvent[], sessionId: string }) {
  const chatItems = useMemo(() => buildChatItems(events), [events])
  const [loadedPayloads, setLoadedPayloads] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    async function loadLongAssistantPayloads() {
      const refs = chatItems
        .filter(item => item.role === 'assistant')
        .map(item => ({ id: item.id, ref: item.content?.payload_ref }))
        .filter(item => item.ref?.id && !loadedPayloads[item.id])
      for (const item of refs) {
        if (!sessionId) continue
        try {
          const text = await fetchPayload(sessionId, item.ref.id)
          if (!cancelled) setLoadedPayloads(prev => ({ ...prev, [item.id]: text }))
        } catch {
          if (!cancelled) setLoadedPayloads(prev => ({ ...prev, [item.id]: renderContent({ payload_ref: item.ref }) }))
        }
      }
    }
    loadLongAssistantPayloads()
    return () => { cancelled = true }
  }, [chatItems, sessionId])

  return <div className="chat-pane">
    {!chatItems.length && <div className="empty-state">这里会显示本轮对话的 user 输入和 assistant 回复。完整上下文请看左侧 Current Model Context。</div>}
    {chatItems.map(item => <div key={item.id} className={`bubble ${item.role}${item.role === 'assistant' ? ' last-answer' : ''}`}>
      <div className="bubble-role">{item.role === 'user' ? 'user 输入' : 'assistant 回复'}</div>
      <pre>{(loadedPayloads[item.id] ?? renderContent(item.content)) || (item.role === 'assistant' ? '(assistant 暂无文本结果，可能仍在工具调用中)' : '(空输入)')}</pre>
    </div>)}
  </div>
}

function buildChatItems(events: ContextEvent[]): ChatItem[] {
  const items: ChatItem[] = []
  for (const event of events) {
    if (event.event_type === 'user_input_received') {
      items.push({ id: event.event_id, role: 'user', content: event.payload?.content || '' })
      continue
    }
    if (event.event_type === 'message_appended' && event.payload?.message?.role === 'assistant') {
      items.push({ id: event.event_id, role: 'assistant', content: event.payload?.message?.content })
    }
  }
  return items
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
