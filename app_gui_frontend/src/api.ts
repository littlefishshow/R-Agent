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

export type LearningSelectionState = {
  source_session_id?: string
  selected_text?: string
  action?: 'question' | 'translate' | 'explain' | 'summarize' | 'note'
  action_label?: string
  custom_question?: string
  target_language?: string
  note_text?: string
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

export async function createLearningSession(payload: {
  title?: string
  root_question?: string
  initial_question?: string
  parent_session_id?: string | null
  account_id?: string
  background?: boolean
  tools_enabled?: boolean
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

export async function setLearningToolsEnabled(sessionId: string, enabled: boolean): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/tools`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function branchLearningSession(sessionId: string, question: string): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/branch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, background: true }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function setbackLearningSession(sessionId: string, messageIndex: number): Promise<{ session: LearningSessionState, draft: string }> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/setback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_index: messageIndex }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function forkLearningSessionFromMessage(sessionId: string, messageIndex: number): Promise<{ session: LearningSessionState, draft: string }> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/fork-from-message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_index: messageIndex }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function selectionBranchLearningSession(sessionId: string, payload: {
  selected_text: string
  action: 'question' | 'translate' | 'explain' | 'summarize' | 'note'
  custom_question?: string
  target_language?: string
  note_text?: string
  title?: string
  source_context?: Record<string, any>
  background?: boolean
}): Promise<LearningSessionState> {
  const res = await fetch(`${API_BASE}/learning/sessions/${sessionId}/selection-branch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ background: true, ...payload }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function saveSelectionNoteLearningSession(sessionId: string, payload: {
  selected_text: string
  note_text: string
  title?: string
  source_context?: Record<string, any>
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

export async function listWorkspaceFiles(path = ''): Promise<WorkspaceListing> {
  const res = await fetch(`${API_BASE}/workspace/files?path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchWorkspaceTree(expanded: string[] = ['']): Promise<{ root: WorkspaceTreeNode }> {
  const query = new URLSearchParams({ expanded: expanded.join(',') })
  const res = await fetch(`${API_BASE}/workspace/tree?${query.toString()}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function createWorkspaceFolder(path: string, name: string): Promise<WorkspaceItem> {
  const res = await fetch(`${API_BASE}/workspace/folders`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, name }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function uploadWorkspaceFile(path: string, file: File): Promise<WorkspaceItem> {
  const content_base64 = await fileToBase64(file)
  const res = await fetch(`${API_BASE}/workspace/files`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, name: file.name, content_base64 }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function copyWorkspaceItem(source: string, targetDir: string, name?: string): Promise<WorkspaceItem> {
  const res = await fetch(`${API_BASE}/workspace/copy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, target_dir: targetDir, name: name || undefined }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function deleteWorkspaceItem(path: string): Promise<{ deleted: string }> {
  const res = await fetch(`${API_BASE}/workspace/files`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function workspaceOpenUrl(path: string, download = false): string {
  const query = new URLSearchParams({ path })
  if (download) query.set('download', 'true')
  return `${API_BASE}/workspace/open?${query.toString()}`
}

export async function fetchWorkspaceText(path: string): Promise<{ path: string, name: string, content: string, item: WorkspaceItem }> {
  const res = await fetch(`${API_BASE}/workspace/text?path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function saveWorkspaceText(path: string, content: string): Promise<WorkspaceItem> {
  const res = await fetch(`${API_BASE}/workspace/text`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, content }),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function fetchWorkspacePdfText(path: string): Promise<PdfTextDocument> {
  const res = await fetch(`${API_BASE}/workspace/pdf-text?path=${encodeURIComponent(path)}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function workspacePdfPageImageUrl(path: string, page: number, zoom = 1.6): string {
  const query = new URLSearchParams({ path, page: String(page), zoom: String(zoom) })
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
