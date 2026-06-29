import { useEffect, useState } from 'react'
import { Activity, BrainCircuit, Plus, Radio, Send, Square, TerminalSquare } from 'lucide-react'
import { createSession, fetchCurrentContext, fetchEvents, fetchResources, interrupt, sendMessage, type ContextEvent, type SessionState } from './api'
import { ChatPane } from './components/ChatPane'
import { ContextTree } from './components/ContextTree'
import { Inspector } from './components/Inspector'

export function App() {
  const [session, setSession] = useState<SessionState | null>(null)
  const [events, setEvents] = useState<ContextEvent[]>([])
  const [selected, setSelected] = useState<ContextEvent | any | null>(null)
  const [currentContext, setCurrentContext] = useState<Record<string, any> | null>(null)
  const [resources, setResources] = useState<Record<string, any> | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  async function boot() {
    setError(null)
    try {
      const s = await createSession()
      setSession(s)
      setEvents(await fetchEvents(s.session_id))
      setCurrentContext(await fetchCurrentContext(s.session_id))
      setResources(await fetchResources(s.session_id))
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  useEffect(() => { boot() }, [])

  useEffect(() => {
    if (!session) return
    const timer = window.setInterval(async () => {
      try {
        setEvents(await fetchEvents(session.session_id))
        setCurrentContext(await fetchCurrentContext(session.session_id))
      } catch { /* ignore */ }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [session?.session_id])

  async function newChat() {
    setError(null)
    setNotice(null)
    setInput('')
    setSelected(null)
    try {
      const s = await createSession()
      setSession(s)
      setEvents(await fetchEvents(s.session_id))
      setCurrentContext(await fetchCurrentContext(s.session_id))
      setResources(await fetchResources(s.session_id))
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  function selectContextModule(id: string) {
    const module = [...(currentContext?.modules || []), ...buildResourceModules(resources)].find((m: any) => m.id === id)
    if (!module) {
      setNotice(`当前上下文中没有找到 ${id}`)
      return
    }
    setSelected({
      event_id: `ctx_${module.id}`,
      event_type: `current_context:${module.id}`,
      source: 'slash_command',
      created_at: Date.now() / 1000,
      payload: module,
    })
  }

  async function handleSlashCommand(command: string): Promise<boolean> {
    const cmd = command.trim().split(/\s+/)[0].toLowerCase()
    if (!cmd.startsWith('/')) return false
    if (cmd === '/new') {
      await newChat()
      return true
    }
    if (cmd === '/help') {
      setNotice('可用命令：/new 新话题；/context 查看 system prompt；/messages 查看当前对话 messages；/tools 查看工具 schema；/skills 查看 skill 说明；/memory 查看 memory 说明；/resources 查看资源库；/frozen 查看冻结 memory；/reviews 查看 self-evolution 说明。终端版 /bbb 语音输入暂未接入 Cockpit。')
      return true
    }
    if (cmd === '/context') { selectContextModule('system_prompt'); return true }
    if (cmd === '/messages') { selectContextModule('messages'); return true }
    if (cmd === '/tools') { selectContextModule('tool_schemas'); return true }
    if (cmd === '/skills') { selectContextModule('skills_note'); return true }
    if (cmd === '/memory') { selectContextModule('live_memory_note'); return true }
    if (cmd === '/resources') { selectContextModule('resource_tools'); return true }
    if (cmd === '/frozen') { selectContextModule('resource_memory_frozen'); return true }
    if (cmd === '/reviews') {
      setNotice('Self-evolution review 当前不默认塞给模型；后续会作为后台/资源上下文接入独立面板。')
      return true
    }
    if (cmd === '/bbb') {
      setNotice('Cockpit 暂未接入 /bbb 语音输入；请先在终端 CLI 使用 /bbb。')
      return true
    }
    setNotice(`未知命令：${cmd}。输入 /help 查看 Cockpit 支持的命令。`)
    return true
  }

  async function submit() {
    if (!session || !input.trim()) return
    const text = input
    setInput('')
    setError(null)
    setNotice(null)
    try {
      if (await handleSlashCommand(text)) return
      await sendMessage(session.session_id, text)
      setError(null)
      setEvents(await fetchEvents(session.session_id))
      setCurrentContext(await fetchCurrentContext(session.session_id))
      setResources(await fetchResources(session.session_id))
    } catch (err: any) {
      const message = err.message || String(err)
      if (message.includes('session is already running')) {
        setError('Agent 正在运行，请稍候或点击 Stop。')
      } else {
        setError(message)
      }
    }
  }

  async function stop() {
    if (!session) return
    await interrupt(session.session_id)
  }

  return <div className="cockpit-shell">
    <header className="topbar glass-panel">
      <div className="brand"><BrainCircuit size={24}/><span>R-Agent Cockpit</span></div>
      <div className="status-chip"><Radio size={16}/> {session ? session.session_id : 'connecting...'}</div>
      <div className="status-chip"><Activity size={16}/> events {events.length}</div>
      <div className="status-chip"><TerminalSquare size={16}/> {session?.model || 'model unknown'}</div>
      <button className="ghost top-action" onClick={newChat}><Plus size={16}/> New Chat</button>
    </header>
    {error && <div className="error-banner">{error}<button className="error-close" onClick={() => setError(null)}>×</button></div>}
    {notice && <div className="notice-banner">{notice}<button className="error-close" onClick={() => setNotice(null)}>×</button></div>}
    <main className="grid-layout">
      <ContextTree currentContext={currentContext} resources={resources} selected={selected} onSelect={setSelected}/>
      <section className="center-column glass-panel">
        <ChatPane events={events} sessionId={session?.session_id || ''}/>
        <div className="composer-wrap">
          {input.trim().startsWith('/') && <div className="slash-menu">
            <button onClick={() => setInput('/new')}>/new 新话题</button>
            <button onClick={() => setInput('/help')}>/help 帮助</button>
            <button onClick={() => setInput('/context')}>/context System Prompt</button>
            <button onClick={() => setInput('/messages')}>/messages 对话历史</button>
            <button onClick={() => setInput('/tools')}>/tools 工具 Schema</button>
            <button onClick={() => setInput('/skills')}>/skills Skill 说明</button>
            <button onClick={() => setInput('/memory')}>/memory Memory 说明</button>
            <button onClick={() => setInput('/resources')}>/resources 资源库</button>
            <button onClick={() => setInput('/frozen')}>/frozen Frozen Memory</button>
          </div>}
          <div className="composer">
          <textarea value={input} onChange={e => setInput(e.target.value)} placeholder="向 R-Agent 发送任务，或输入 / 打开本地命令... Shift+Enter 换行" onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
          }}/>
          <button className="primary" onClick={submit}><Send size={18}/> Send</button>
          <button className="ghost" onClick={stop}><Square size={18}/> Stop</button>
          </div>
        </div>
      </section>
      <Inspector sessionId={session?.session_id || ''} event={selected}/>
    </main>
  </div>
}


function buildResourceModules(resources: Record<string, any> | null): any[] {
  if (!resources) return []
  const modules: any[] = []
  if (resources.tools) {
    modules.push({
      id: 'resource_tools',
      label: `Resource: Tool Registry（${resources.tools.count ?? 0}）`,
      kind: 'json',
      items: resources.tools.schemas || [],
      visible_to_model: false,
      description: '资源库视图：当前后端注册的完整工具 schema。不会自动塞给模型，但下一轮请求会携带 tool schema。',
    })
  }
  if (resources.skills?.list_ref) {
    modules.push({
      id: 'resource_skills',
      label: 'Resource: Skills Catalog',
      kind: 'payload',
      payload_ref: resources.skills.list_ref,
      visible_to_model: false,
      description: '资源库视图：当前 skill 列表快照。Skill 全文需要通过 skill_view 等工具进入对话。',
    })
  }
  if (resources.memory?.frozen_ref) {
    modules.push({
      id: 'resource_memory_frozen',
      label: 'Resource: Frozen Memory Snapshot',
      kind: 'payload',
      payload_ref: resources.memory.frozen_ref,
      visible_to_model: true,
      description: '资源库视图：当前 session 构造 system prompt 时注入的 frozen memory snapshot。',
    })
  }
  if (resources.memory?.live_ref) {
    modules.push({
      id: 'resource_memory_live',
      label: 'Resource: Live Memory Files',
      kind: 'payload',
      payload_ref: resources.memory.live_ref,
      visible_to_model: false,
      description: '资源库视图：磁盘上 memory 文件的实时读取结果；不会自动刷新进当前 system prompt。',
    })
  }
  if (resources.self_evolution) {
    modules.push({
      id: 'resource_self_evolution',
      label: 'Resource: Self-Evolution Review',
      kind: resources.self_evolution.latest_ref ? 'payload' : 'json',
      payload_ref: resources.self_evolution.latest_ref,
      items: resources.self_evolution.latest || resources.self_evolution,
      visible_to_model: false,
      description: '资源库视图：最近一次 self-evolution review 产物（如果存在）。默认不塞给模型。',
    })
  }
  return modules
}
