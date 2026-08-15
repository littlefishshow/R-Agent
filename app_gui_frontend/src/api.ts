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
  truncated?: boolean
  max_iterations?: number
  event_count: number
  created_at?: number
  updated_at?: number
  last_activity_at?: number
  last_response?: string | null
  last_error?: string | null
  token_usage?: string | number
}


export type TodoBoardTask = {
  id: string
  description: string
  parent_id?: string | null
  dependencies?: string[]
  status: 'pending' | 'in_progress' | 'needs_split' | 'blocked' | 'completed' | 'failed' | 'cancelled' | string
  assigned_to?: string
  deliverable?: string
  updated_at?: number
}

export type TodoBoardState = {
  exists?: boolean
  session_id?: string
  path?: string
  total?: number
  completed?: number
  progress?: number
  status_counts?: Record<string, number>
  ready_to_execute?: string[]
  tasks?: TodoBoardTask[]
  truncated?: boolean
  updated_at?: number
  error?: string
}

export type LearningSelectionState = {
  source_session_id?: string
  selected_text?: string
  action?: 'question' | 'translate' | 'explain' | 'summarize' | 'note' | 'modify'
  action_label?: string
  custom_question?: string
  target_language?: string
  note_text?: string
  modification_instruction?: string
  accepted?: boolean
  replacement_text?: string
  source_context?: Record<string, any>
}

export type LearningSessionState = SessionState & {
  mode?: 'learning'
  title?: string
  root_question?: string
  last_question?: string
  parent_session_id?: string | null
  account_id?: string
  node_kind?: string
  file_path?: string
  child_count?: number
  source_message_index?: number | null
  allowed_tools?: string[]
  tools_enabled?: boolean
  selection?: LearningSelectionState
  send?: any
  todo_board?: TodoBoardState | null
  parent_todo_board?: TodoBoardState | null
}

export type WorkspaceItem = {
  name: string
  path: string
  type: 'file' | 'directory'
  size: number
  mtime: number
  content_type: string
  is_pdf: boolean
  is_markdown?: boolean
}

export type WorkspaceTreeNode = WorkspaceItem & {
  children?: WorkspaceTreeNode[]
}

export type WorkspaceListing = {
  cwd: string
  parent: string
  root_name: string
  items: WorkspaceItem[]
}

export type PdfWord = {
  x0: number
  y0: number
  x1: number
  y1: number
  text: string
}

export type PdfLine = PdfWord & {
  font_size?: number
}

export type PdfTextPage = {
  page: number
  text: string
  width: number
  height: number
  words?: PdfWord[]
  lines?: PdfLine[]
}

export type PdfTextDocument = {
  path: string
  name: string
  page_count: number
  pages: PdfTextPage[]
}

const API_BASE = import.meta.env.VITE_R_AGENT_API_BASE || ''

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

export async function continueSession(sessionId: string, extraIterations?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/continue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ background: true, extra_iterations: extraIterations }),
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

export async function fetchEventsSince(sessionId: string, since = 0): Promise<{ events: ContextEvent[], event_count: number }> {
  const res = await fetch(`${API_BASE}/sessions/${sessionId}/events?since=${Math.max(0, since)}`)
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json()
  return { events: data.events || [], event_count: data.event_count ?? since }
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
  const wsBase = API_BASE
    ? API_BASE.replace(/^http/, 'ws')
    : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  return new WebSocket(`${wsBase}/sessions/${sessionId}/ws`)
}

export async function createLearningSession(payload: {
  title?: string
  root_question?: string
  initial_question?: string
  parent_session_id?: string | null
  account_id?: string
  background?: boolean
  tools_enabled?: boolean
  session_id?: string
} = {}): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function listLearningSessions(accountId = ''): Promise<Record<string, LearningSessionState>> {
  const query = accountId ? `?account_id=${encodeURIComponent(accountId)}` : ''
  const res = await fetch(`${API_BASE}/learning/sessions${query}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getLearningFileRoot(accountId: string, filePath: string): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/file-root`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account_id: accountId || 'default', file_path: filePath }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchLearningAccountRoots(accountId: string): Promise<{ account_id: string, nodes: LearningSessionState[] }> {
  const res = await fetch(`${API_BASE}/learning/accounts/${encodeURIComponent(accountId || 'default')}/roots`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchLearningChildren(sessionId: string): Promise<{ session_id: string, account_id: string, nodes: LearningSessionState[] }> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/children`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchLearningSession(sessionId: string): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function sendLearningMessage(sessionId: string, text: string): Promise<any> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, background: true }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function continueLearningSession(sessionId: string, extraIterations?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/continue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ background: true, extra_iterations: extraIterations }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function setLearningToolsEnabled(sessionId: string, enabled: boolean): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/tools`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function branchLearningSession(sessionId: string, question: string, childSessionId?: string): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/branch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, background: true, session_id: childSessionId || undefined }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function setbackLearningSession(sessionId: string, messageIndex: number): Promise<{ session: LearningSessionState, draft: string, deleted?: string[] }> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/setback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_index: messageIndex }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function forkLearningSessionFromMessage(sessionId: string, messageIndex: number, childSessionId?: string): Promise<{ session: LearningSessionState, draft: string }> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/fork-from-message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_index: messageIndex, session_id: childSessionId || undefined }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function selectionBranchLearningSession(sessionId: string, payload: {
  selected_text: string
  action: 'question' | 'translate' | 'explain' | 'summarize' | 'note' | 'modify'
  custom_question?: string
  target_language?: string
  note_text?: string
  modification_instruction?: string
  title?: string
  source_context?: Record<string, any>
  background?: boolean
  session_id?: string
}): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/selection-branch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ background: true, ...payload }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function acceptSelectionModification(sessionId: string): Promise<{
  success: boolean
  session_id: string
  path: string
  replacement_text: string
  content: string
  deleted: string[]
}> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/accept-modification`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function saveSelectionNoteLearningSession(sessionId: string, payload: {
  selected_text: string
  note_text: string
  title?: string
  source_context?: Record<string, any>
  session_id?: string
}): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/selection-note`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function deleteLearningSession(sessionId: string): Promise<{ deleted: string[] }> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function interruptLearning(sessionId: string): Promise<any> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/interrupt`, { method: 'POST' })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchLearningEvents(sessionId: string): Promise<ContextEvent[]> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/events`)
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json()
  return data.events || []
}

export async function fetchLearningEventsSince(sessionId: string, since = 0): Promise<{ events: ContextEvent[], event_count: number }> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/events?since=${Math.max(0, since)}`)
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json()
  return { events: data.events || [], event_count: data.event_count ?? since }
}

export async function fetchLearningPayload(sessionId: string, payloadId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/payloads/${payloadId}`)
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json()
  return data.content || ''
}

export async function fetchLearningContext(sessionId: string): Promise<Record<string, any>> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/current-context`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchLearningResources(sessionId: string): Promise<Record<string, any>> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/resources`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function listWorkspaceFiles(path = '', sessionId = ''): Promise<WorkspaceListing> {
  const query = new URLSearchParams({ path })
  if (sessionId) query.set('session_id', sessionId)
  const res = await fetch(`${API_BASE}/workspace/files?${query.toString()}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchWorkspaceTree(expanded: string[] = [''], sessionId = ''): Promise<{ root: WorkspaceTreeNode }> {
  const query = new URLSearchParams({ expanded: expanded.join(',') })
  if (sessionId) query.set('session_id', sessionId)
  const res = await fetch(`${API_BASE}/workspace/tree?${query.toString()}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function createWorkspaceFolder(path: string, name: string, sessionId = ''): Promise<WorkspaceItem> {
  const res = await fetch(`${API_BASE}/workspace/folders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, name, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function uploadWorkspaceFile(path: string, file: File, sessionId = ''): Promise<WorkspaceItem> {
  const content_base64 = await fileToBase64(file)
  const res = await fetch(`${API_BASE}/workspace/files`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, name: file.name, content_base64, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function copyWorkspaceItem(source: string, targetDir: string, name?: string, sessionId = ''): Promise<WorkspaceItem> {
  const res = await fetch(`${API_BASE}/workspace/copy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, target_dir: targetDir, name: name || undefined, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function deleteWorkspaceItem(path: string, sessionId = ''): Promise<{ deleted: string }> {
  const res = await fetch(`${API_BASE}/workspace/files`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function workspaceOpenUrl(path: string, download = false, sessionId = ''): string {
  const query = new URLSearchParams({ path })
  if (download) query.set('download', 'true')
  if (sessionId) query.set('session_id', sessionId)
  return `${API_BASE}/workspace/open?${query.toString()}`
}

export async function fetchWorkspaceText(path: string, sessionId = ''): Promise<{ path: string, name: string, content: string, item: WorkspaceItem }> {
  const query = new URLSearchParams({ path })
  if (sessionId) query.set('session_id', sessionId)
  const res = await fetch(`${API_BASE}/workspace/text?${query.toString()}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function saveWorkspaceText(path: string, content: string, sessionId = ''): Promise<WorkspaceItem> {
  const res = await fetch(`${API_BASE}/workspace/text`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, content, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchWorkspacePdfText(path: string, sessionId = ''): Promise<PdfTextDocument> {
  const query = new URLSearchParams({ path })
  if (sessionId) query.set('session_id', sessionId)
  const res = await fetch(`${API_BASE}/workspace/pdf-text?${query.toString()}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function workspacePdfPageImageUrl(path: string, page: number, zoom = 1.6, sessionId = ''): string {
  const query = new URLSearchParams({ path, page: String(page), zoom: String(zoom) })
  if (sessionId) query.set('session_id', sessionId)
  return `${API_BASE}/workspace/pdf-page-image?${query.toString()}`
}

async function fileToBase64(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  let binary = ''
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.slice(i, i + chunkSize))
  }
  return window.btoa(binary)
}
