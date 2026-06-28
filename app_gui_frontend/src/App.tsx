import { useEffect, useMemo, useState } from 'react'
import { Activity, BrainCircuit, Radio, Send, Square, TerminalSquare } from 'lucide-react'
import { createSession, fetchCurrentContext, fetchEvents, interrupt, sendMessage, type ContextEvent, type SessionState } from './api'
import { ChatPane } from './components/ChatPane'
import { ContextTree } from './components/ContextTree'
import { Inspector } from './components/Inspector'

export function App() {
  const [session, setSession] = useState<SessionState | null>(null)
  const [events, setEvents] = useState<ContextEvent[]>([])
  const [selected, setSelected] = useState<ContextEvent | any | null>(null)
  const [currentContext, setCurrentContext] = useState<Record<string, any> | null>(null)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function boot() {
    setError(null)
    try {
      const s = await createSession()
      setSession(s)
      setEvents(await fetchEvents(s.session_id))
      setCurrentContext(await fetchCurrentContext(s.session_id))
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

  const assistantEvents = useMemo(() => events.filter(e => e.event_type === 'message_appended' && e.payload?.message?.role === 'assistant'), [events])

  async function submit() {
    if (!session || !input.trim()) return
    const text = input
    setInput('')
    setError(null)
    try {
      await sendMessage(session.session_id, text)
      setError(null)
      setEvents(await fetchEvents(session.session_id))
      setCurrentContext(await fetchCurrentContext(session.session_id))
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
    </header>
    {error && <div className="error-banner">{error}<button className="error-close" onClick={() => setError(null)}>×</button></div>}
    <main className="grid-layout">
      <ContextTree currentContext={currentContext} selected={selected} onSelect={setSelected}/>
      <section className="center-column glass-panel">
        <ChatPane events={assistantEvents} sessionId={session?.session_id || ''}/>
        <div className="composer">
          <textarea value={input} onChange={e => setInput(e.target.value)} placeholder="向 R-Agent 发送任务... Shift+Enter 换行" onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
          }}/>
          <button className="primary" onClick={submit}><Send size={18}/> Send</button>
          <button className="ghost" onClick={stop}><Square size={18}/> Stop</button>
        </div>
      </section>
      <Inspector sessionId={session?.session_id || ''} event={selected}/>
    </main>
  </div>
}
