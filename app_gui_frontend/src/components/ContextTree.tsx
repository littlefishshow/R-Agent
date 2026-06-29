import type { ContextEvent } from '../api'

type Selectable = ContextEvent | { event_id: string, event_type: string, source: string, created_at: number, payload: Record<string, any> }

export function ContextTree({ currentContext, resources, selected, onSelect }: { currentContext: Record<string, any> | null, resources?: Record<string, any> | null, selected: Selectable | null, onSelect: (e: Selectable) => void }) {
  const modules = currentContext?.modules || []
  const resourceModules = buildResourceModules(resources || null)
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
    <details open className="tree-group resource-group">
      <summary>Resource Library <span>{resourceModules.length}</span></summary>
      <div className="context-hint compact">这里展示工具可读取或 GUI 可检查的资源快照：tools、skills、memory、self-evolution。</div>
      {resourceModules.map((module: any) => {
        const item = makeItem(module)
        return <button key={item.event_id} className={selected?.event_id === item.event_id ? 'tree-item active' : 'tree-item'} onClick={() => onSelect(item)}>
          <span>◆</span> {module.label}
        </button>
      })}
    </details>
  </aside>
}


function buildResourceModules(resources: Record<string, any> | null): any[] {
  if (!resources) return []
  const modules: any[] = []
  if (resources.tools) {
    modules.push({
      id: 'resource_tools',
      label: `Tool Registry（${resources.tools.count ?? 0}）`,
      kind: 'json',
      items: resources.tools.schemas || [],
      visible_to_model: false,
      description: '资源库：当前后端注册的完整工具 schema。',
    })
  }
  if (resources.skills?.list_ref) {
    modules.push({
      id: 'resource_skills',
      label: 'Skills Catalog',
      kind: 'payload',
      payload_ref: resources.skills.list_ref,
      visible_to_model: false,
      description: '资源库：skill 列表快照，点击右侧载入完整 payload。',
    })
  }
  if (resources.memory?.frozen_ref) {
    modules.push({
      id: 'resource_memory_frozen',
      label: 'Frozen Memory Snapshot',
      kind: 'payload',
      payload_ref: resources.memory.frozen_ref,
      visible_to_model: true,
      description: '资源库：session 启动时注入 system prompt 的 frozen memory snapshot。',
    })
  }
  if (resources.memory?.live_ref) {
    modules.push({
      id: 'resource_memory_live',
      label: 'Live Memory Files',
      kind: 'payload',
      payload_ref: resources.memory.live_ref,
      visible_to_model: false,
      description: '资源库：磁盘 memory 文件实时读取结果。',
    })
  }
  if (resources.self_evolution) {
    modules.push({
      id: 'resource_self_evolution',
      label: 'Self-Evolution Review',
      kind: resources.self_evolution.latest_ref ? 'payload' : 'json',
      payload_ref: resources.self_evolution.latest_ref,
      items: resources.self_evolution.latest || resources.self_evolution,
      visible_to_model: false,
      description: '资源库：最近一次 self-evolution review 产物（如果存在）。',
    })
  }
  return modules
}
