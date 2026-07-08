import { useEffect, useMemo, useState } from 'react'
import { fetchPayload, type ContextEvent } from '../api'

export function Inspector({ sessionId, event }: { sessionId: string, event: ContextEvent | any | null }) {
  const [payloadText, setPayloadText] = useState<string | null>(null)
  const [expandedBlocks, setExpandedBlocks] = useState<Record<string, boolean>>({})
  const ref = findPayloadRef(event?.payload)
  const display = useMemo(() => buildDisplay(event?.payload), [event?.event_id, event?.payload])

  useEffect(() => {
    setPayloadText(null)
    setExpandedBlocks({})
  }, [event?.event_id])

  async function expandPayload() {
    if (!sessionId || !ref) return
    setPayloadText(await fetchPayload(sessionId, ref.id))
  }

  function toggleBlock(key: string) {
    setExpandedBlocks(prev => ({ ...prev, [key]: !prev[key] }))
  }

  return <aside className="right-panel glass-panel">
    <div className="panel-title">Inspector</div>
    {!event && <div className="empty-state">点击左侧任意上下文节点查看完整内容。</div>}
    {event && <>
      <div className="meta-grid"><span>模块</span><b>{display.title}</b><span>可见性</span><b>{display.visible}</b></div>
      {display.description && <div className="context-hint">{display.description}</div>}
      {display.blocks.length > 0 && <div className="block-list">{display.blocks.map((block, i) => {
        const key = `${event.event_id}_${i}`
        const expanded = !!expandedBlocks[key]
        return <section className="context-block" key={key}>
          <div className="context-block-title"><span>{block.title}</span><button className="tiny-toggle" onClick={() => toggleBlock(key)}>{expanded ? '折叠' : '展开'}</button></div>
          <pre className={expanded ? 'block-text expanded' : 'block-text collapsed'}>{block.text}</pre>
        </section>
      })}</div>}
      {!display.blocks.length && display.text && <CollapsibleText title="内容" text={display.text} defaultExpanded={false}/>} 
      {ref && <div className="payload-card"><div>完整 Payload：{ref.size} chars</div><pre className="block-text collapsed">{ref.preview}</pre><button className="primary wide" onClick={expandPayload}>载入完整 Payload</button></div>}
      {payloadText && <CollapsibleText title="完整 Payload" text={payloadText} defaultExpanded={true}/>} 
    </>}
  </aside>
}

function CollapsibleText({ title, text, defaultExpanded = false }: { title: string, text: string, defaultExpanded?: boolean }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  return <section className="context-block">
    <div className="context-block-title"><span>{title}</span><button className="tiny-toggle" onClick={() => setExpanded(!expanded)}>{expanded ? '折叠' : '展开'}</button></div>
    <pre className={expanded ? 'block-text expanded' : 'block-text collapsed'}>{text}</pre>
  </section>
}

function buildDisplay(payload: any): { title: string, visible: string, description: string, text: string, blocks: Array<{title: string, text: string}> } {
  if (!payload) return { title: '', visible: '', description: '', text: '', blocks: [] }
  const title = payload.label || payload.id || 'Context'
  const visible = payload.visible_to_model === true ? '会直接发给模型' : payload.visible_to_model === false ? '默认不直接发给模型' : '上下文事件'
  const description = payload.description || ''
  if (payload.kind === 'messages') {
    const items = payload.items || []
    return {
      title,
      visible,
      description,
      text: '',
      blocks: items.length ? items.map((m: any, i: number) => ({
        title: `#${i + 1} ${m.role}${m.name ? ` · ${m.name}` : ''}`,
        text: renderValue(m.content || (m.tool_calls?.length ? m.tool_calls : '(空消息)')),
      })) : [{ title: '当前对话历史', text: '当前还没有对话历史。' }],
    }
  }
  if (payload.kind === 'payload') {
    return {
      title,
      visible,
      description,
      text: payload.payload_ref?.preview || '点击下方按钮载入完整 Payload。',
      blocks: [],
    }
  }
  if (payload.kind === 'json') {
    const items = payload.items || []
    if (Array.isArray(items)) {
      return {
        title,
        visible,
        description,
        text: '',
        blocks: items.map((item: any, i: number) => ({
          title: item?.function?.name || item?.name || `item ${i + 1}`,
          text: JSON.stringify(item, null, 2),
        })),
      }
    }
    return { title, visible, description, text: JSON.stringify(payload.items || payload, null, 2), blocks: [] }
  }
  if (payload.kind === 'note') {
    return { title, visible, description, text: payload.content || '', blocks: [] }
  }
  return { title, visible, description, text: '', blocks: [] }
}

function renderValue(value: any): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (value?.payload_ref) return value.payload_ref.preview || `[payload ${value.payload_ref.id}]`
  try { return JSON.stringify(value, null, 2) } catch { return String(value) }
}

function findPayloadRef(obj: any): any | null {
  if (!obj || typeof obj !== 'object') return null
  if (obj.payload_ref?.id) return obj.payload_ref
  for (const value of Object.values(obj)) {
    const found = findPayloadRef(value)
    if (found) return found
  }
  return null
}
