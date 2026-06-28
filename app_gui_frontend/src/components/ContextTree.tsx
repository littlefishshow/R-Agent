import type { ContextEvent } from '../api'

type Selectable = ContextEvent | { event_id: string, event_type: string, source: string, created_at: number, payload: Record<string, any> }

export function ContextTree({ currentContext, selected, onSelect }: { currentContext: Record<string, any> | null, selected: Selectable | null, onSelect: (e: Selectable) => void }) {
  const modules = currentContext?.modules || []
  const makeItem = (module: any): Selectable => ({
    event_id: `ctx_${module.id}`,
    event_type: `current_context:${module.id}`,
    source: 'current_model_context',
    created_at: Date.now() / 1000,
    payload: module,
  })

  return <aside className="left-panel glass-panel">
    <div className="panel-title">Current Model Context</div>
    <div className="context-hint">这里只展示“现在如果发送下一句话，会塞给大模型的上下文”。</div>
    <details open className="tree-group resource-group">
      <summary>LLM Visible <span>{modules.filter((m: any) => m.visible_to_model).length}</span></summary>
      {modules.filter((m: any) => m.visible_to_model).map((module: any) => {
        const item = makeItem(module)
        return <button key={item.event_id} className={selected?.event_id === item.event_id ? 'tree-item active' : 'tree-item'} onClick={() => onSelect(item)}>
          <span>●</span> {module.label}
        </button>
      })}
    </details>
    <details open className="tree-group">
      <summary>Available by Tool / Notes <span>{modules.filter((m: any) => !m.visible_to_model).length}</span></summary>
      {modules.filter((m: any) => !m.visible_to_model).map((module: any) => {
        const item = makeItem(module)
        return <button key={item.event_id} className={selected?.event_id === item.event_id ? 'tree-item active' : 'tree-item'} onClick={() => onSelect(item)}>
          <span>◇</span> {module.label}
        </button>
      })}
    </details>
  </aside>
}
