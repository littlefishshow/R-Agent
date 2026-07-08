export type ContextEvent = {
  event_id: string
  event_type: string
  session_id: string
  source: string
  created_at: number
  payload: Record<string, any>
}

export type SessionState = {
  session_id: string
  model: string
  running: boolean
  event_count: number
  last_response?: string | null
  last_error?: string | null
  token_usage?: string | number
}

const API_BASE = import.meta.env.VITE_R_AGENT_API_BASE || 'http://127.0.0.1:8765'

export async function createSession(sessionId?: string): Promise<SessionState> {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId || null }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function sendMessage(sessionId: string, text: string): Promise<any> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, background: true }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function interrupt(sessionId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/interrupt`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchEvents(sessionId: string): Promise<ContextEvent[]> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/events`)
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json()
  return data.events || []
}

export async function fetchPayload(sessionId: string, payloadId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/payloads/${payloadId}`)
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json()
  return data.content || ''
}

export async function fetchCurrentContext(sessionId: string): Promise<Record<string, any>> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/current-context`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchResources(sessionId: string): Promise<Record<string, any>> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/resources`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function openEventSocket(sessionId: string): WebSocket {
  const wsBase = API_BASE.replace(/^http/, 'ws')
  return new WebSocket(`${wsBase}/sessions/${sessionId}/ws`)
}
