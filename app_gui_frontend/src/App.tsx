import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import MarkdownIt from 'markdown-it'
import { renderToString } from 'katex'
import { BookOpen, Check, Copy, Edit3, Ellipsis, Eye, File, FileText, Folder, Languages, Lightbulb, ListTree, Maximize2, MessageCircle, Minimize2, Plus, Search, Save, Send, Square, Trash2, Workflow, X, ZoomIn, ZoomOut } from 'lucide-react'
import 'katex/dist/katex.min.css'
import {
  copyWorkspaceItem,
  createLearningSession,
  continueLearningSession,
  createWorkspaceFolder,
  deleteLearningSession,
  deleteWorkspaceItem,
  fetchLearningAccountRoots,
  fetchLearningChildren,
  forkLearningSessionFromMessage,
  fetchWorkspacePdfText,
  fetchWorkspaceText,
  fetchWorkspaceTree,
  getLearningFileRoot,
  fetchLearningEvents,
  fetchLearningEventsSince,
  fetchLearningPayload,
  fetchLearningSession,
  interruptLearning,
  listWorkspaceFiles,
  listLearningSessions,
  setbackLearningSession,
  selectionBranchLearningSession,
  sendLearningMessage,
  saveWorkspaceText,
  saveSelectionNoteLearningSession,
  setLearningToolsEnabled,
  uploadWorkspaceFile,
  workspacePdfPageImageUrl,
  workspaceOpenUrl,
  type ContextEvent,
  type LearningSessionState,
  type TodoBoardState,
  type PdfTextDocument,
  type WorkspaceItem,
  type WorkspaceListing,
  type WorkspaceTreeNode,
} from './api'

type ChatItem = {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: any
  createdAt: number
  messageIndex?: number
}

type SelectionAction = 'question' | 'translate' | 'explain' | 'summarize' | 'note'

type SelectionMenuState = {
  sourceSessionId: string
  chatId: string
  text: string
  textOffset?: number
  occurrence?: number
  x: number
  y: number
  pdfContext?: {
    path: string
    page: number
    rects: PdfSelectionRect[]
  }
  sourceContext?: Record<string, any>
}

type PendingSelectionAction = SelectionMenuState & {
  action: SelectionAction
}

type HighlightRecord = {
  id: string
  sessionId: string
  sourceSessionId: string
  chatId: string
  text: string
  textOffset?: number
  occurrence?: number
  action: SelectionAction
  label: string
  color: BranchColor
}

type PdfHighlightRecord = {
  id: string
  sessionId: string
  path: string
  page: number
  text: string
  rects: PdfSelectionRect[]
  color: BranchColor
}

type PdfSelectionRect = {
  x0: number
  y0: number
  x1: number
  y1: number
}

type BranchColor = {
  hue: number
  depth: number
  bg: string
  border: string
  strong: string
  text: string
}

type FloatingWindowState = {
  id: string
  sessionId: string
  sourceSessionId: string
  title: string
  highlightId?: string
  color: BranchColor
  action?: SelectionAction
  selectedText?: string
  displayQuestion?: string
  targetLanguage?: string
  noteText?: string
  x: number
  y: number
  width: number
  height: number
  fullscreen: boolean
  minimized: boolean
  zIndex: number
}

type PdfViewerState = {
  path: string
  name: string
  url: string
}


function makeGuiSessionId(prefix = 'gui'): string {
  const randomPart = (globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}_${Math.random().toString(16).slice(2)}`)
    .replace(/[^A-Za-z0-9_.-]+/g, '_')
    .slice(0, 32)
  return `${prefix}_${randomPart}`
}

function makeChildSessionId(parentSessionId: string, prefix = 'learn'): string {
  const parent = (parentSessionId || 'root').replace(/[^A-Za-z0-9_.-]+/g, '_').slice(0, 40)
  return `${prefix}_${parent}_${makeGuiSessionId('win').slice(4)}`
}

type OpenFileTab = PdfViewerState & {
  type: 'pdf' | 'markdown' | 'file'
  pdfText?: PdfTextDocument
  textContent?: string
  dirty?: boolean
  viewMode?: 'preview' | 'edit'
  loading?: boolean
  error?: string
}

type FileContextMenuState = {
  x: number
  y: number
  item: WorkspaceTreeNode | null
}

type TextSelectionResult = {
  text: string
  textOffset?: number
  occurrence?: number
  rect: DOMRect
  range: Range
}

type TextSelectionAnchor = {
  tokenIndex: number
  offset: number
}

type TextSelectionPoint = TextSelectionAnchor

const SELECTION_ACTIONS: Array<{ action: SelectionAction, label: string, icon: any }> = [
  { action: 'question', label: '提问', icon: MessageCircle },
  { action: 'translate', label: '翻译', icon: Languages },
  { action: 'explain', label: '解释', icon: Lightbulb },
  { action: 'summarize', label: '总结', icon: FileText },
  { action: 'note', label: '笔记', icon: Edit3 },
]

const ROOT_BRANCH_COLOR = makeBranchColor(212, 0)
const markdownRenderer = new MarkdownIt({
  html: false,
  linkify: true,
  typographer: true,
  breaks: false,
})

export function App() {
  const [sessions, setSessions] = useState<Record<string, LearningSessionState>>({})
  const [accountRootIds, setAccountRootIds] = useState<string[]>([])
  const [conversationChildren, setConversationChildren] = useState<Record<string, string[]>>({})
  const [expandedConversationNodes, setExpandedConversationNodes] = useState<Record<string, boolean>>({})
  const [session, setSession] = useState<LearningSessionState | null>(null)
  const [accountId] = useState('default')
  const [events, setEvents] = useState<ContextEvent[]>([])
  const [eventsLoading, setEventsLoading] = useState(false)
  const eventsRef = useRef<ContextEvent[]>([])
  const [input, setInput] = useState('')
  const inputDraftRef = useRef('')
  const [inputResetToken, setInputResetToken] = useState(0)
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [selectionMenu, setSelectionMenu] = useState<SelectionMenuState | null>(null)
  const [pendingAction, setPendingAction] = useState<PendingSelectionAction | null>(null)
  const [questionDraft, setQuestionDraft] = useState('')
  const [noteDraft, setNoteDraft] = useState('')
  const [translationChoice, setTranslationChoice] = useState<'英语' | '中文' | '其他'>('英语')
  const [customLanguage, setCustomLanguage] = useState('')
  const [highlights, setHighlights] = useState<Record<string, HighlightRecord>>({})
  const [pdfHighlights, setPdfHighlights] = useState<Record<string, PdfHighlightRecord>>({})
  const [windows, setWindows] = useState<Record<string, FloatingWindowState>>({})
  const [windowEvents, setWindowEvents] = useState<Record<string, ContextEvent[]>>({})
  const windowEventsRef = useRef<Record<string, ContextEvent[]>>({})
  const [windowInputs, setWindowInputs] = useState<Record<string, string>>({})
  const windowInputDraftsRef = useRef<Record<string, string>>({})
  const [windowInputResetTokens, setWindowInputResetTokens] = useState<Record<string, number>>({})
  const [windowSending, setWindowSending] = useState<Record<string, boolean>>({})
  const windowSendingRef = useRef<Record<string, boolean>>({})
  const [messageBranchMenus, setMessageBranchMenus] = useState<Record<string, boolean>>({})
  const [collapsedMessages, setCollapsedMessages] = useState<Record<string, boolean>>({})
  const [workspace, setWorkspace] = useState<WorkspaceListing | null>(null)
  const [workspaceTree, setWorkspaceTree] = useState<WorkspaceTreeNode | null>(null)
  const [expandedFolders, setExpandedFolders] = useState<Record<string, boolean>>({ '': true, papers: true })
  const [clipboardPath, setClipboardPath] = useState<string | null>(null)
  const [fileContextMenu, setFileContextMenu] = useState<FileContextMenuState | null>(null)
  const [uploadTargetPath, setUploadTargetPath] = useState('')
  const [activeMode, setActiveMode] = useState<'chat' | 'files'>('chat')
  const [openFiles, setOpenFiles] = useState<Record<string, OpenFileTab>>({})
  const [activeFilePath, setActiveFilePath] = useState<string | null>(null)
  const [pdfZoom, setPdfZoom] = useState(1.85)
  const [sidebarWidth, setSidebarWidth] = useState(296)
  const [detailWidth, setDetailWidth] = useState(340)
  const [topZ, setTopZ] = useState(30)
  const activeTextSelectionRangeRef = useRef<Range | null>(null)
  // Sessions with a just-sent message whose background run may not yet report
  // running=true. The poller keeps reconciling these so a reply can't get stuck.
  const pendingRunsRef = useRef<Record<string, number>>({})
  // Latest-closure refs so the sidebar tree can receive stable callback
  // identities (memoized ConversationTreeNode skips re-render on poll ticks).
  const activateSessionRef = useRef<(id: string) => void>(() => {})
  const toggleConversationNodeRef = useRef<(id: string) => void>(() => {})
  const deleteRootSessionRef = useRef<(id: string) => void>(() => {})
  const stableActivateSession = useCallback((id: string) => activateSessionRef.current(id), [])
  const stableToggleNode = useCallback((id: string) => toggleConversationNodeRef.current(id), [])
  const stableDeleteRoot = useCallback((id: string) => deleteRootSessionRef.current(id), [])

  const chatItems = useMemo(() => buildChatItems(events), [events])
  const visibleSessions = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return accountRootIds
      .map(id => sessions[id])
      .filter(Boolean)
      .filter(item => item.node_kind !== 'file_root')
      .filter(item => !q || `${item.title || ''} ${item.root_question || ''} ${item.last_question || ''}`.toLowerCase().includes(q))
  }, [sessions, accountRootIds, filter])
  const visibleFileRoots = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return accountRootIds
      .map(id => sessions[id])
      .filter(Boolean)
      .filter(item => item.node_kind === 'file_root')
      .sort((a, b) => (a.file_path || '').localeCompare(b.file_path || ''))
      .filter(item => !q || `${item.title || ''} ${item.file_path || ''}`.toLowerCase().includes(q))
  }, [sessions, accountRootIds, filter])
  const sessionTree = useMemo(() => buildSessionTree(Object.values(sessions)), [sessions])
  const branchColors = useMemo(() => buildBranchColors(Object.values(sessions)), [sessions])
  const activeFile = activeFilePath ? openFiles[activeFilePath] || null : null
  const conversationPanelRef = useRef<HTMLElement | null>(null)
  const chatScrollPositionsRef = useRef<Record<string, number>>({})
  const fileScrollPositionsRef = useRef<Record<string, number>>({})
  const openFilesRef = useRef<Record<string, OpenFileTab>>({})
  activateSessionRef.current = activateSession
  toggleConversationNodeRef.current = toggleConversationNode
  deleteRootSessionRef.current = deleteRootSession
  const uploadInputRef = useRef<HTMLInputElement | null>(null)
  const openWindowPollKey = useMemo(() => Object.values(windows)
    .map(win => `${win.id}:${win.sessionId}:${win.minimized ? 'min' : 'open'}`)
    .sort()
    .join('|'), [windows])
  const runningSessionPollKey = useMemo(() => Object.values(sessions)
    .filter(item => item.running)
    .map(item => item.session_id)
    .sort()
    .join('|'), [sessions])

  async function boot() {
    setError(null)
    try {
      const roots = await fetchLearningAccountRoots(accountId)
      const first = roots.nodes.find(item => item.node_kind !== 'file_root') || roots.nodes[0]
      const s = first || await createLearningSession({ session_id: makeGuiSessionId(), title: '新的学习问题', account_id: accountId })
      setSessions(Object.fromEntries((first ? roots.nodes : [s]).map(item => [item.session_id, item])))
      setAccountRootIds(first ? roots.nodes.map(item => item.session_id) : [s.session_id])
      await activateSession(s.session_id)
      await refreshWorkspace('')
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  useEffect(() => { boot() }, [])

  useEffect(() => {
    eventsRef.current = events
  }, [events])

  useEffect(() => {
    windowEventsRef.current = windowEvents
  }, [windowEvents])

  useEffect(() => {
    openFilesRef.current = openFiles
  }, [openFiles])

  useEffect(() => {
    if (activeMode !== 'chat' || !session?.session_id) return
    const panel = conversationPanelRef.current
    if (!panel) return
    const sessionId = session.session_id
    const scrollTop = chatScrollPositionsRef.current[sessionId] || 0
    window.requestAnimationFrame(() => {
      if (conversationPanelRef.current === panel && session?.session_id === sessionId) {
        panel.scrollTop = scrollTop
      }
    })
  }, [activeMode, session?.session_id, chatItems.length])

  useEffect(() => {
    if (!session) return
    const timer = window.setInterval(async () => {
      try {
        const activePending = isPendingRun(session.session_id)
        if (session.running || activePending) await refreshActive(session.session_id)
        if (Object.values(windows).some(win => !win.minimized && (sessions[win.sessionId]?.running || isPendingRun(win.sessionId)))) {
          await refreshOpenWindows()
        }
      } catch { /* ignore */ }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [session?.session_id, session?.running, openWindowPollKey, runningSessionPollKey])

  useEffect(() => {
    function clearMenu(event: PointerEvent) {
      const target = event.target as HTMLElement
      if (target.closest('.selection-menu, .file-context-menu, .message-actions, .message-branch-menu')) return
      if (selectionMenu) {
        activeTextSelectionRangeRef.current = null
        window.getSelection()?.removeAllRanges()
        setSelectionMenu(null)
      }
      if (fileContextMenu) setFileContextMenu(null)
      if (Object.keys(messageBranchMenus).length) setMessageBranchMenus({})
    }
    window.addEventListener('pointerdown', clearMenu, true)
    return () => window.removeEventListener('pointerdown', clearMenu, true)
  }, [selectionMenu, fileContextMenu, messageBranchMenus])

  function showSelectionMenu(menu: SelectionMenuState, range?: Range | null) {
    if (range) {
      activeTextSelectionRangeRef.current = range.cloneRange()
      const selection = window.getSelection()
      selection?.removeAllRanges()
      selection?.addRange(range)
    } else {
      activeTextSelectionRangeRef.current = null
    }
    setSelectionMenu(menu)
  }

  function markPendingRun(sessionId: string) {
    if (!sessionId) return
    // Keep polling this session for up to 90s even if running never flips true.
    pendingRunsRef.current[sessionId] = Date.now() + 90_000
  }

  function isPendingRun(sessionId: string): boolean {
    const deadline = pendingRunsRef.current[sessionId]
    if (!deadline) return false
    if (Date.now() > deadline) {
      delete pendingRunsRef.current[sessionId]
      return false
    }
    return true
  }

  function resolvePendingRun(sessionId: string, state?: LearningSessionState | null) {
    if (!sessionId || !pendingRunsRef.current[sessionId]) return
    // Once the backend has actually started (running) we no longer need the
    // pending flag — the normal running-based poll takes over. If it already
    // finished with a response/error, clear it too.
    if (state?.running || state?.last_response || state?.last_error) {
      delete pendingRunsRef.current[sessionId]
    }
  }

  function setWindowSendingFlag(windowId: string, sending: boolean) {
    windowSendingRef.current = { ...windowSendingRef.current, [windowId]: sending }
    setWindowSending(prev => prev[windowId] === sending ? prev : ({ ...prev, [windowId]: sending }))
  }

  function seedWindowInput(windowId: string, value: string) {
    if (!value) return
    setWindowInputs(prev => {
      const existingDraft = windowInputDraftsRef.current[windowId]
      const existingValue = existingDraft ?? prev[windowId] ?? ''
      if (existingValue) return prev
      windowInputDraftsRef.current[windowId] = value
      return { ...prev, [windowId]: value }
    })
  }

  function restoreWindowInputAfterFailedSend(windowId: string, originalText: string) {
    if (!originalText) return
    setWindowInputs(prev => {
      const currentDraft = windowInputDraftsRef.current[windowId] ?? prev[windowId] ?? ''
      const restored = currentDraft
        ? (currentDraft === originalText || currentDraft.includes(originalText) ? currentDraft : `${originalText}\n\n${currentDraft}`)
        : originalText
      windowInputDraftsRef.current[windowId] = restored
      return prev[windowId] === restored ? prev : { ...prev, [windowId]: restored }
    })
  }


  function makeOptimisticBranch(sessionId: string, sourceSessionId: string, data: {
    title: string
    action?: SelectionAction
    selectedText?: string
    customQuestion?: string
    targetLanguage?: string
    noteText?: string
    sourceContext?: Record<string, any>
  }): LearningSessionState {
    return {
      session_id: sessionId,
      mode: 'learning',
      model: '',
      running: true,
      event_count: 0,
      title: data.title,
      root_question: data.customQuestion || data.noteText || data.selectedText || data.title,
      last_question: data.customQuestion || data.noteText || data.selectedText || data.title,
      parent_session_id: sourceSessionId,
      account_id: accountId,
      selection: data.selectedText ? {
        source_session_id: sourceSessionId,
        selected_text: data.selectedText,
        action: data.action,
        custom_question: data.customQuestion,
        target_language: data.targetLanguage,
        note_text: data.noteText,
        source_context: data.sourceContext,
      } : undefined,
    }
  }

  async function refreshActive(sessionId = session?.session_id || '') {
    if (!sessionId) return
    try {
      const forceFullEvents = session?.session_id !== sessionId
      const [state, ev] = await Promise.all([
        fetchLearningSession(sessionId),
        forceFullEvents ? fetchLearningEvents(sessionId) : Promise.resolve(null),
      ])
      resolvePendingRun(sessionId, state)
      setSession(prev => prev && sameLearningSession(prev, state) ? prev : state)
      setSessions(prev => sameLearningSession(prev[state.session_id], state) ? prev : ({ ...prev, [state.session_id]: state }))
      restoreChatHighlights(state.session_id).catch(() => {})
      if (ev) {
        setEvents(prev => sameEventList(prev, ev) ? prev : ev)
      } else {
        await syncActiveEvents(sessionId, state, false)
      }
    } catch (err: any) {
      await recoverActiveSession()
    }
  }

  async function syncActiveEvents(sessionId: string, state: LearningSessionState, forceFull = false) {
    const expected = state.event_count || 0
    const current = eventsRef.current
    if (forceFull || expected < current.length) {
      const ev = await fetchLearningEvents(sessionId)
      setEvents(prev => sameEventList(prev, ev) ? prev : ev)
      return
    }
    // Nothing new and not mid-run: safe to skip. While running we keep
    // reconciling so a raced/dropped batch cannot leave the UI stuck.
    if (expected === current.length && !state.running && !isPendingRun(sessionId)) return
    const since = current.length
    const result = await fetchLearningEventsSince(sessionId, since)
    if (!result.events.length) return
    let applied = false
    setEvents(prev => {
      if (prev.length !== since) return prev
      applied = true
      return [...prev, ...result.events]
    })
    if (!applied) {
      // Raced with another sync (eventsRef lags a render): the incremental
      // batch no longer lines up. Self-heal with a full fetch instead of
      // silently dropping events (root cause of "no reply until setback").
      const ev = await fetchLearningEvents(sessionId)
      setEvents(prev => sameEventList(prev, ev) ? prev : ev)
    }
  }

  async function recoverActiveSession() {
    const roots = await fetchLearningAccountRoots(accountId)
    const next = roots.nodes.find(item => item.node_kind !== 'file_root') || roots.nodes[0]
    if (next) {
      setSessions(Object.fromEntries(roots.nodes.map(item => [item.session_id, item])))
      setAccountRootIds(roots.nodes.map(item => item.session_id))
      setSession(next)
      setEvents(await fetchLearningEvents(next.session_id))
      return
    }
    const created = await createLearningSession({ session_id: makeGuiSessionId(), title: '新的学习问题', account_id: accountId })
    setSessions({ [created.session_id]: created })
    setAccountRootIds([created.session_id])
    setSession(created)
    setEvents([])
  }

  async function refreshOpenWindows() {
    const entries = Object.values(windows).filter(win => !win.minimized && (sessions[win.sessionId]?.running || isPendingRun(win.sessionId)))
    if (!entries.length) return
    const updates = await Promise.all(entries.map(async win => {
      try {
        const state = await fetchLearningSession(win.sessionId)
        return { win, state, missing: false }
      } catch {
        return { win, state: null, missing: true }
      }
    }))
    const missing = updates
      .filter(item => item?.missing && !isPendingRun(item.win.sessionId))
      .map(item => item!.win.id)
    if (missing.length) {
      const missingSet = new Set(missing)
      setWindows(prev => {
        const next = { ...prev }
        for (const id of missingSet) delete next[id]
        return next
      })
      setWindowEvents(prev => {
        const next = { ...prev }
        for (const id of missingSet) delete next[id]
        return next
      })
    }
    const valid = updates.filter(item => item && !item.missing && item.state) as Array<{ win: FloatingWindowState, state: LearningSessionState }>
    if (!valid.length) return
    setSessions(prev => {
      let changed = false
      const next = { ...prev }
      for (const item of valid) {
        resolvePendingRun(item.state.session_id, item.state)
        const pendingActive = isPendingRun(item.state.session_id)
        const effectiveState = pendingActive && !item.state.running && !item.state.last_response && !item.state.last_error
          ? { ...item.state, running: true }
          : item.state
        if (!sameLearningSession(prev[effectiveState.session_id], effectiveState)) {
          next[effectiveState.session_id] = effectiveState
          changed = true
        }
      }
      return changed ? next : prev
    })
    await Promise.all(valid.map(item => syncWindowEvents(item.win, item.state)))
  }

  async function syncWindowEvents(win: FloatingWindowState, state: LearningSessionState) {
    const expected = state.event_count || 0
    const current = windowEventsRef.current[win.id] || []
    if (expected < current.length) {
      const ev = await fetchLearningEvents(win.sessionId)
      setWindowEvents(prev => sameEventList(prev[win.id] || [], ev) ? prev : ({ ...prev, [win.id]: ev }))
      return
    }
    if (expected === current.length && !state.running && !isPendingRun(win.sessionId)) return
    const since = current.length
    const result = await fetchLearningEventsSince(win.sessionId, since)
    if (!result.events.length) return
    let applied = false
    setWindowEvents(prev => {
      const existing = prev[win.id] || []
      if (existing.length !== since) return prev
      applied = true
      return { ...prev, [win.id]: [...existing, ...result.events] }
    })
    if (!applied) {
      const ev = await fetchLearningEvents(win.sessionId)
      setWindowEvents(prev => sameEventList(prev[win.id] || [], ev) ? prev : ({ ...prev, [win.id]: ev }))
    }
  }

  async function refreshWorkspace(path = workspace?.cwd || '') {
    const expandedPaths = Object.entries(expandedFolders)
      .filter(([, value]) => value)
      .map(([key]) => key)
    const [listing, tree] = await Promise.all([
      listWorkspaceFiles(path),
      fetchWorkspaceTree(expandedPaths),
    ])
    setWorkspace(listing)
    setWorkspaceTree(tree.root)
  }

  function toggleWorkspaceFolder(path: string) {
    setExpandedFolders(prev => {
      const next = { ...prev, [path]: !prev[path] }
      const expandedPaths = Object.entries(next).filter(([, value]) => value).map(([key]) => key)
      fetchWorkspaceTree(expandedPaths)
        .then(tree => setWorkspaceTree(tree.root))
        .catch((err: any) => setError(err.message || String(err)))
      return next
    })
  }

  function beginColumnResize(side: 'left' | 'right', event: any) {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = side === 'left' ? sidebarWidth : detailWidth
    const handleMove = (moveEvent: MouseEvent) => {
      const delta = moveEvent.clientX - startX
      if (side === 'left') {
        setSidebarWidth(Math.max(220, Math.min(440, startWidth + delta)))
      } else {
        setDetailWidth(Math.max(260, Math.min(560, startWidth - delta)))
      }
    }
    const handleUp = () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
    }
    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
  }

  async function toggleConversationNode(sessionId: string) {
    const nextOpen = !expandedConversationNodes[sessionId]
    setExpandedConversationNodes(prev => ({ ...prev, [sessionId]: nextOpen }))
    if (!nextOpen) return
    // If children are already cached, the subtree renders immediately; the
    // fetch below just refreshes counts in the background.
    try {
      const result = await fetchLearningChildren(sessionId)
      setSessions(prev => ({
        ...prev,
        ...Object.fromEntries(result.nodes.map(item => [item.session_id, item])),
      }))
      setConversationChildren(prev => ({
        ...prev,
        [sessionId]: result.nodes.map(item => item.session_id),
      }))
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function activateSession(sessionId: string) {
    setError(null)
    setActiveMode('chat')
    // Optimistic switch: show the target conversation immediately from the
    // sidebar cache and clear stale bubbles so switching feels instant, then
    // reconcile against the server. The explicit loading flag is cleared after
    // events are fetched, so sessions whose stored events do not produce chat
    // bubbles can still show the normal new-conversation empty state.
    const known = sessions[sessionId]
    const switchingSession = known && known.session_id !== session?.session_id
    if (switchingSession) {
      setEventsLoading(true)
      setSession(known)
      setEvents([])
    }
    try {
      await refreshActive(sessionId)
    } finally {
      if (switchingSession) setEventsLoading(false)
    }
  }

  async function newQuestionChain() {
    setError(null)
    setInput('')
    try {
      const existingBlank = Object.values(sessions).find(item => isBlankRoot(item))
      if (existingBlank) {
        await activateSession(existingBlank.session_id)
        return
      }
      const s = await createLearningSession({ session_id: makeGuiSessionId(), title: '新的学习问题', account_id: accountId })
      setSessions(prev => ({ ...prev, [s.session_id]: s }))
      setAccountRootIds(prev => [s.session_id, ...prev])
      await activateSession(s.session_id)
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function submit(textOverride?: string) {
    const draft = textOverride ?? inputDraftRef.current ?? input
    if (!session || !draft.trim()) return
    const text = draft
    const sessionId = session.session_id
    inputDraftRef.current = ''
    setInput('')
    setInputResetToken(prev => prev + 1)
    setError(null)
    // Keep the poller reconciling immediately; send can return before the
    // backend run flips running=true, and failures clear the optimistic state.
    markPendingRun(sessionId)
    setSession(prev => prev && prev.session_id === sessionId ? { ...prev, running: true } : prev)
    setSessions(prev => prev[sessionId] ? { ...prev, [sessionId]: { ...prev[sessionId], running: true } } : prev)
    try {
      await sendLearningMessage(sessionId, text)
      await refreshActive(sessionId)
    } catch (err: any) {
      delete pendingRunsRef.current[sessionId]
      setSession(prev => prev && prev.session_id === sessionId ? { ...prev, running: false } : prev)
      setSessions(prev => prev[sessionId] ? { ...prev, [sessionId]: { ...prev[sessionId], running: false } } : prev)
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
    await interruptLearning(session.session_id)
    await refreshActive(session.session_id)
  }

  async function continueActiveAfterTruncation() {
    if (!session || session.running || !session.truncated) return
    const sessionId = session.session_id
    setError(null)
    markPendingRun(sessionId)
    setSession(prev => prev && prev.session_id === sessionId ? { ...prev, running: true } : prev)
    setSessions(prev => prev[sessionId] ? { ...prev, [sessionId]: { ...prev[sessionId], running: true } } : prev)
    try {
      await continueLearningSession(sessionId)
      await refreshActive(sessionId)
    } catch (err: any) {
      delete pendingRunsRef.current[sessionId]
      setSession(prev => prev && prev.session_id === sessionId ? { ...prev, running: false } : prev)
      setSessions(prev => prev[sessionId] ? { ...prev, [sessionId]: { ...prev[sessionId], running: false } } : prev)
      setError(err.message || String(err))
    }
  }

  async function toggleActiveSessionTools(enabled: boolean) {
    if (!session) return
    try {
      const updated = await setLearningToolsEnabled(session.session_id, enabled)
      setSession(updated)
      setSessions(prev => ({ ...prev, [updated.session_id]: updated }))
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function forkFromUserMessage(sessionId: string, messageIndex?: number, windowId?: string) {
    if (messageIndex == null) return
    try {
      const result = await forkLearningSessionFromMessage(sessionId, messageIndex, makeChildSessionId(sessionId, 'fork'))
      const branch = result.session
      setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
      setConversationChildren(prev => ({
        ...prev,
        [sessionId]: [branch.session_id, ...(prev[sessionId] || [])],
      }))
      seedWindowInput(`win_${branch.session_id}`, result.draft)
      await openFloatingSession(branch, {
        sourceSessionId: sessionId,
        title: branch.title || result.draft.slice(0, 10) || 'Fork',
        displayQuestion: result.draft,
        fullscreen: !windowId,
      })
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function setbackToUserMessage(sessionId: string, messageIndex?: number, windowId?: string) {
    if (messageIndex == null) return
    if (!window.confirm('确认回退到这条用户消息之前？该消息及其之后的上下文会被删除，其后创建的分支/高亮子对话也会一并删除。')) return
    try {
      const result = await setbackLearningSession(sessionId, messageIndex)
      setSessions(prev => ({ ...prev, [result.session.session_id]: result.session }))
      if (result.deleted?.length) {
        removeDeletedSessions(new Set(result.deleted))
      }
      if (windowId) {
        windowInputDraftsRef.current[windowId] = result.draft
        setWindowInputs(prev => prev[windowId] === result.draft ? prev : ({ ...prev, [windowId]: result.draft }))
        setWindowInputResetTokens(prev => ({ ...prev, [windowId]: (prev[windowId] || 0) + 1 }))
        const ev = await fetchLearningEvents(sessionId)
        setWindowEvents(prev => ({ ...prev, [windowId]: ev }))
      } else {
        setInput(result.draft)
        await refreshActive(sessionId)
      }
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  function raiseWindow(windowId: string) {
    setTopZ(prev => {
      const nextZ = prev + 1
      setWindows(current => current[windowId] ? {
        ...current,
        [windowId]: { ...current[windowId], zIndex: nextZ, minimized: false },
      } : current)
      return nextZ
    })
  }

  function handleTextSelection(sourceSessionId: string, chatId: string, container: HTMLElement, anchor: TextSelectionAnchor | null, event: any) {
    const showResult = (result: TextSelectionResult | null) => {
      if (result?.range) {
        const selection = window.getSelection()
        selection?.removeAllRanges()
        selection?.addRange(result.range)
      }
      if (!result) return
      showSelectionMenu({
        sourceSessionId,
        chatId,
        text: result.text,
        textOffset: result.textOffset,
        occurrence: result.occurrence,
        x: Math.min(result.rect.left + result.rect.width / 2, window.innerWidth - 220),
        y: Math.max(12, result.rect.top - 46),
        // Unify with file/PDF selections: always describe where the text came
        // from so the backend prompt gets kind/location/selected text.
        sourceContext: {
          kind: 'chat',
          session_id: sourceSessionId,
          location: `对话消息 ${chatId}`,
          chat_id: chatId,
          text_offset: result.textOffset,
          occurrence: result.occurrence,
        },
      }, result.range)
    }
    const result = readTextSelectionWithin(container, anchor, event)
    if (result || anchor) {
      showResult(result)
      return
    }
    window.setTimeout(() => showResult(readTextSelectionWithin(container)), 0)
  }

  function handleSelectionAction(action: SelectionAction) {
    if (!selectionMenu) return
    const menu = selectionMenu
    setSelectionMenu(null)
    window.getSelection()?.removeAllRanges()
    if (action === 'question') {
      setQuestionDraft('')
      setPendingAction({ ...menu, action })
      return
    }
    if (action === 'translate') {
      setTranslationChoice('英语')
      setCustomLanguage('')
      setPendingAction({ ...menu, action })
      return
    }
    if (action === 'note') {
      setNoteDraft('')
      setPendingAction({ ...menu, action })
      return
    }
    createSelectionBranch({ ...menu, action })
  }

  async function openFloatingSession(branch: LearningSessionState, options: {
    sourceSessionId: string
    title?: string
    highlightId?: string
    action?: SelectionAction
    selectedText?: string
    displayQuestion?: string
    targetLanguage?: string
    noteText?: string
    color?: BranchColor
    fullscreen?: boolean
    initialEvents?: ContextEvent[]
    skipInitialFetch?: boolean
  }) {
    const windowId = `win_${branch.session_id}`
    const color = options.color || branchColors[branch.session_id] || deriveChildColor(options.sourceSessionId, Object.values(sessions), branchColors)
    setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
    setTopZ(prevZ => {
      const nextZ = prevZ + 1
      setWindows(current => {
        const existing = current[windowId]
        if (existing) {
          return {
            ...current,
            [windowId]: {
              ...existing,
              sessionId: branch.session_id,
              sourceSessionId: options.sourceSessionId || existing.sourceSessionId,
              title: options.title || branch.title || existing.title || '新分支',
              highlightId: options.highlightId ?? existing.highlightId,
              color,
              action: options.action ?? existing.action,
              selectedText: options.selectedText ?? existing.selectedText,
              displayQuestion: options.displayQuestion ?? existing.displayQuestion,
              targetLanguage: options.targetLanguage ?? existing.targetLanguage,
              noteText: options.noteText ?? existing.noteText,
              minimized: false,
              zIndex: nextZ,
            },
          }
        }
        const placement = nextWindowPlacement(Object.values(current), options.sourceSessionId)
        return {
          ...current,
          [windowId]: {
            id: windowId,
            sessionId: branch.session_id,
            sourceSessionId: options.sourceSessionId,
            title: options.title || branch.title || '新分支',
            highlightId: options.highlightId,
            color,
            action: options.action,
            selectedText: options.selectedText,
            displayQuestion: options.displayQuestion,
            targetLanguage: options.targetLanguage,
            noteText: options.noteText,
            x: placement.x,
            y: placement.y,
            width: placement.width,
            height: placement.height,
            fullscreen: !!options.fullscreen,
            minimized: false,
            zIndex: nextZ,
          },
        }
      })
      return nextZ
    })
    if (options.initialEvents) {
      setWindowEvents(prev => {
        const existing = prev[windowId] || []
        const incoming = options.initialEvents || []
        return incoming.length >= existing.length ? { ...prev, [windowId]: incoming } : prev
      })
    } else if (!options.skipInitialFetch) {
      setWindowEvents(prev => ({ ...prev, [windowId]: prev[windowId] || [] }))
      try {
        const ev = await fetchLearningEvents(branch.session_id)
        setWindowEvents(prev => {
          const existing = prev[windowId] || []
          return ev.length >= existing.length ? { ...prev, [windowId]: ev } : prev
        })
      } catch {
        // Optimistic windows may be visible before the backend creates the
        // session. The poller will reconcile events as soon as the session exists.
      }
    } else {
      setWindowEvents(prev => ({ ...prev, [windowId]: prev[windowId] || [] }))
    }
  }

  async function createSelectionBranch(actionState: PendingSelectionAction, options: { customQuestion?: string, targetLanguage?: string, noteText?: string } = {}) {
    const menu = actionState
    const actionMeta = SELECTION_ACTIONS.find(item => item.action === menu.action)
    const requestedSourceSessionId = menu.sourceSessionId
    const provisionalSessionId = makeChildSessionId(requestedSourceSessionId, menu.action === 'note' ? 'note' : 'learn')
    const highlightId = `hl_${Date.now()}_${Math.random().toString(16).slice(2)}`
    const provisionalColor = branchColors[provisionalSessionId] || deriveChildColor(requestedSourceSessionId, Object.values(sessions), branchColors)
    const highlight: HighlightRecord = {
      id: highlightId,
      sessionId: provisionalSessionId,
      sourceSessionId: requestedSourceSessionId,
      chatId: menu.chatId,
      text: menu.text,
      textOffset: menu.textOffset,
      occurrence: menu.occurrence,
      action: menu.action,
      label: actionMeta?.label || '提问',
      color: provisionalColor,
    }
    if (menu.pdfContext) {
      setPdfHighlights(prev => ({
        ...prev,
        [highlightId]: {
          id: highlightId,
          sessionId: provisionalSessionId,
          path: menu.pdfContext?.path || '',
          page: menu.pdfContext?.page || 1,
          text: menu.text,
          rects: menu.pdfContext?.rects || [],
          color: provisionalColor,
        },
      }))
    }
    setHighlights(prev => ({ ...prev, [highlightId]: highlight }))
    markPendingRun(provisionalSessionId)
    const optimisticBranch = makeOptimisticBranch(provisionalSessionId, requestedSourceSessionId, {
      title: options.customQuestion || options.noteText || actionMeta?.label || '新分支',
      action: menu.action,
      selectedText: menu.text,
      customQuestion: options.customQuestion,
      targetLanguage: options.targetLanguage,
      noteText: options.noteText,
      sourceContext: menu.sourceContext,
    })
    await openFloatingSession(optimisticBranch, {
      sourceSessionId: requestedSourceSessionId,
      title: optimisticBranch.title || highlight.label,
      highlightId,
      action: menu.action,
      selectedText: menu.text,
      displayQuestion: options.customQuestion,
      targetLanguage: options.targetLanguage,
      noteText: options.noteText,
      color: provisionalColor,
      skipInitialFetch: true,
    })
    try {
      let sourceSessionId = requestedSourceSessionId
      const filePath = typeof menu.sourceContext?.path === 'string' ? menu.sourceContext.path : ''
      if (filePath) {
        const fileRoot = await getLearningFileRoot(accountId, filePath)
        sourceSessionId = fileRoot.session_id
        setSessions(prev => ({ ...prev, [fileRoot.session_id]: fileRoot }))
        setAccountRootIds(prev => prev.includes(fileRoot.session_id) ? prev : [...prev, fileRoot.session_id])
      }
      if (sourceSessionId !== requestedSourceSessionId) {
        setHighlights(prev => prev[highlightId] ? ({
          ...prev,
          [highlightId]: { ...prev[highlightId], sourceSessionId },
        }) : prev)
      }
      const branch = await selectionBranchLearningSession(sourceSessionId, {
        session_id: provisionalSessionId,
        selected_text: menu.text,
        action: menu.action,
        custom_question: options.customQuestion,
        target_language: options.targetLanguage,
        note_text: options.noteText,
        source_context: menu.sourceContext,
      })
      const color = branchColors[branch.session_id] || provisionalColor
      if (menu.pdfContext) {
        setPdfHighlights(prev => prev[highlightId] ? ({
          ...prev,
          [highlightId]: { ...prev[highlightId], sessionId: branch.session_id, color },
        }) : prev)
      }
      setHighlights(prev => prev[highlightId] ? ({
        ...prev,
        [highlightId]: { ...prev[highlightId], sessionId: branch.session_id, sourceSessionId, color },
      }) : prev)
      setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
      setConversationChildren(prev => ({
        ...prev,
        [sourceSessionId]: prev[sourceSessionId]?.includes(branch.session_id)
          ? prev[sourceSessionId]
          : [branch.session_id, ...(prev[sourceSessionId] || [])],
      }))
      await openFloatingSession(branch, {
        sourceSessionId,
        title: branch.title || highlight.label,
        highlightId,
        action: menu.action,
        selectedText: menu.text,
        displayQuestion: options.customQuestion,
        targetLanguage: options.targetLanguage,
        noteText: options.noteText,
        color,
        skipInitialFetch: true,
      })
      await refreshOpenWindows()
    } catch (err: any) {
      delete pendingRunsRef.current[provisionalSessionId]
      setWindows(prev => {
        const next = { ...prev }
        delete next[`win_${provisionalSessionId}`]
        return next
      })
      setWindowEvents(prev => {
        const next = { ...prev }
        delete next[`win_${provisionalSessionId}`]
        return next
      })
      setHighlights(prev => {
        const next = { ...prev }
        delete next[highlightId]
        return next
      })
      setPdfHighlights(prev => {
        const next = { ...prev }
        delete next[highlightId]
        return next
      })
      setError(err.message || String(err))
    }
  }

  function submitQuestionDialog() {
    if (!pendingAction || !questionDraft.trim()) return
    const action = pendingAction
    setPendingAction(null)
    createSelectionBranch(action, { customQuestion: questionDraft.trim() })
  }

  function submitTranslationDialog() {
    if (!pendingAction) return
    const language = translationChoice === '其他' ? customLanguage.trim() : translationChoice
    if (!language) return
    const action = pendingAction
    setPendingAction(null)
    createSelectionBranch(action, { targetLanguage: language })
  }

  async function saveNoteDialog(sendToModel: boolean) {
    if (!pendingAction || !noteDraft.trim()) return
    const action = pendingAction
    const note = noteDraft.trim()
    setPendingAction(null)
    setNoteDraft('')
    if (sendToModel) {
      createSelectionBranch(action, { noteText: note })
      return
    }
    const provisionalSessionId = makeChildSessionId(action.sourceSessionId, 'note')
    const highlightId = `hl_${Date.now()}_${Math.random().toString(16).slice(2)}`
    const provisionalColor = branchColors[provisionalSessionId] || deriveChildColor(action.sourceSessionId, Object.values(sessions), branchColors)
    if (action.pdfContext) {
      setPdfHighlights(prev => ({
        ...prev,
        [highlightId]: {
          id: highlightId,
          sessionId: provisionalSessionId,
          path: action.pdfContext?.path || '',
          page: action.pdfContext?.page || 1,
          text: action.text,
          rects: action.pdfContext?.rects || [],
          color: provisionalColor,
        },
      }))
    }
    setHighlights(prev => ({
      ...prev,
      [highlightId]: {
        id: highlightId,
        sessionId: provisionalSessionId,
        sourceSessionId: action.sourceSessionId,
        chatId: action.chatId,
        text: action.text,
        textOffset: action.textOffset,
        occurrence: action.occurrence,
        action: 'note',
        label: '笔记',
        color: provisionalColor,
      },
    }))
    const optimisticBranch = makeOptimisticBranch(provisionalSessionId, action.sourceSessionId, {
      title: '笔记',
      action: 'note',
      selectedText: action.text,
      noteText: note,
      sourceContext: action.sourceContext,
    })
    await openFloatingSession(optimisticBranch, {
      sourceSessionId: action.sourceSessionId,
      title: '笔记',
      highlightId,
      action: 'note',
      selectedText: action.text,
      noteText: note,
      color: provisionalColor,
      skipInitialFetch: true,
    })
    setWindows(prev => {
      const id = `win_${provisionalSessionId}`
      return prev[id] ? { ...prev, [id]: { ...prev[id], minimized: true } } : prev
    })
    try {
      let sourceSessionId = action.sourceSessionId
      const filePath = typeof action.sourceContext?.path === 'string' ? action.sourceContext.path : ''
      if (filePath) {
        const fileRoot = await getLearningFileRoot(accountId, filePath)
        sourceSessionId = fileRoot.session_id
        setSessions(prev => ({ ...prev, [fileRoot.session_id]: fileRoot }))
        setAccountRootIds(prev => prev.includes(fileRoot.session_id) ? prev : [...prev, fileRoot.session_id])
      }
      const branch = await saveSelectionNoteLearningSession(sourceSessionId, {
        session_id: provisionalSessionId,
        selected_text: action.text,
        note_text: note,
        source_context: action.sourceContext,
      })
      const color = branchColors[branch.session_id] || provisionalColor
      if (action.pdfContext) {
        setPdfHighlights(prev => prev[highlightId] ? ({
          ...prev,
          [highlightId]: { ...prev[highlightId], sessionId: branch.session_id, color },
        }) : prev)
      }
      setHighlights(prev => prev[highlightId] ? ({
        ...prev,
        [highlightId]: { ...prev[highlightId], sessionId: branch.session_id, sourceSessionId, color },
      }) : prev)
      setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
      setConversationChildren(prev => ({
        ...prev,
        [sourceSessionId]: prev[sourceSessionId]?.includes(branch.session_id)
          ? prev[sourceSessionId]
          : [branch.session_id, ...(prev[sourceSessionId] || [])],
      }))
      await openFloatingSession(branch, {
        sourceSessionId,
        title: branch.title || '笔记',
        highlightId,
        action: 'note',
        selectedText: action.text,
        noteText: note,
        color,
        skipInitialFetch: true,
      })
      setWindows(prev => {
        const id = `win_${branch.session_id}`
        return prev[id] ? { ...prev, [id]: { ...prev[id], minimized: true } } : prev
      })
    } catch (err: any) {
      setWindows(prev => {
        const next = { ...prev }
        delete next[`win_${provisionalSessionId}`]
        return next
      })
      setWindowEvents(prev => {
        const next = { ...prev }
        delete next[`win_${provisionalSessionId}`]
        return next
      })
      setHighlights(prev => {
        const next = { ...prev }
        delete next[highlightId]
        return next
      })
      setPdfHighlights(prev => {
        const next = { ...prev }
        delete next[highlightId]
        return next
      })
      setError(err.message || String(err))
    }
  }

  async function sendWindowMessage(windowId: string, textOverride?: string) {
    const win = windows[windowId]
    const rawText = textOverride ?? windowInputDraftsRef.current[windowId] ?? windowInputs[windowId] ?? ''
    const text = rawText.trim()
    if (!win || !text) return
    if (windowSendingRef.current[windowId] || sessions[win.sessionId]?.running || isPendingRun(win.sessionId)) {
      setError('Agent 正在运行，请稍候或点击 Stop。')
      return
    }
    windowInputDraftsRef.current[windowId] = ''
    setWindowInputs(prev => ({ ...prev, [windowId]: '' }))
    setWindowInputResetTokens(prev => ({ ...prev, [windowId]: (prev[windowId] || 0) + 1 }))
    setWindowSendingFlag(windowId, true)
    markPendingRun(win.sessionId)
    setSessions(prev => prev[win.sessionId] ? { ...prev, [win.sessionId]: { ...prev[win.sessionId], running: true } } : prev)
    try {
      await sendLearningMessage(win.sessionId, text)
      const [state, ev] = await Promise.all([
        fetchLearningSession(win.sessionId),
        fetchLearningEvents(win.sessionId),
      ])
      resolvePendingRun(win.sessionId, state)
      const pendingActive = isPendingRun(win.sessionId)
      const effectiveState = pendingActive && !state.running && !state.last_response && !state.last_error
        ? { ...state, running: true }
        : state
      setSessions(prev => ({ ...prev, [effectiveState.session_id]: effectiveState }))
      setWindowEvents(prev => ({ ...prev, [windowId]: ev }))
    } catch (err: any) {
      delete pendingRunsRef.current[win.sessionId]
      setSessions(prev => prev[win.sessionId] ? { ...prev, [win.sessionId]: { ...prev[win.sessionId], running: false } } : prev)
      restoreWindowInputAfterFailedSend(windowId, rawText)
      const message = err.message || String(err)
      if (message.includes('session is already running')) {
        setError('Agent 正在运行，请稍候或点击 Stop。')
      } else {
        setError(message)
      }
    } finally {
      setWindowSendingFlag(windowId, false)
    }
  }

  async function stopWindow(windowId: string) {
    const win = windows[windowId]
    if (!win) return
    await interruptLearning(win.sessionId)
    const [state, ev] = await Promise.all([
      fetchLearningSession(win.sessionId),
      fetchLearningEvents(win.sessionId),
    ])
    setSessions(prev => ({ ...prev, [state.session_id]: state }))
    setWindowEvents(prev => ({ ...prev, [windowId]: ev }))
  }

  function minimizeWindow(windowId: string) {
    setWindows(prev => {
      const win = prev[windowId]
      if (!win) return prev
      return {
        ...prev,
        [windowId]: win.fullscreen
          ? { ...win, fullscreen: false }
          : { ...win, minimized: true },
      }
    })
  }

  function maximizeWindow(windowId: string) {
    setTopZ(prev => {
      const nextZ = prev + 1
      setWindows(current => current[windowId] ? {
        ...current,
        [windowId]: { ...current[windowId], fullscreen: true, minimized: false, zIndex: nextZ },
      } : current)
      return nextZ
    })
  }

  function moveWindow(windowId: string, x: number, y: number) {
    setWindows(prev => {
      const win = prev[windowId]
      if (!win || win.fullscreen) return prev
      return {
        ...prev,
        [windowId]: {
          ...win,
          x: Math.max(8, Math.min(x, window.innerWidth - 120)),
          y: Math.max(8, Math.min(y, window.innerHeight - 80)),
        },
      }
    })
  }

  function resizeWindow(windowId: string, width: number, height: number) {
    setWindows(prev => {
      const win = prev[windowId]
      if (!win || win.fullscreen) return prev
      return {
        ...prev,
        [windowId]: {
          ...win,
          width: Math.max(360, Math.min(width, window.innerWidth - win.x - 12)),
          height: Math.max(330, Math.min(height, window.innerHeight - win.y - 12)),
        },
      }
    })
  }

  async function closeWindowAndDeleteSubtree(windowId: string) {
    const win = windows[windowId]
    if (!win) return
    try {
      let result: { deleted: string[] }
      try {
        result = await deleteLearningSession(win.sessionId)
      } catch (err: any) {
        result = { deleted: [win.sessionId] }
      }
      const deleted = new Set(result.deleted || [win.sessionId])
      removeDeletedSessions(deleted)
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function deleteRootSession(sessionId: string) {
    try {
      let result: { deleted: string[] }
      try {
        result = await deleteLearningSession(sessionId)
      } catch (err: any) {
        result = { deleted: [sessionId, ...collectLocalDescendants(sessionId, sessions)] }
      }
      const deleted = new Set(result.deleted || [sessionId])
      removeDeletedSessions(deleted)
      const remaining = await listLearningSessions(accountId)
      const nextRoot = Object.values(remaining).find(item => !item.parent_session_id)
      setSessions(remaining)
      if (session?.session_id === sessionId || deleted.has(session?.session_id || '')) {
        if (nextRoot) {
          await activateSession(nextRoot.session_id)
        } else {
          const created = await createLearningSession({ session_id: makeGuiSessionId(), title: '新的学习问题', account_id: accountId })
          setSessions({ [created.session_id]: created })
          setAccountRootIds([created.session_id])
          await activateSession(created.session_id)
        }
      }
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  function removeDeletedSessions(deleted: Set<string>) {
    setAccountRootIds(prev => prev.filter(id => !deleted.has(id)))
    setConversationChildren(prev => {
      const next = { ...prev }
      for (const id of deleted) delete next[id]
      for (const [id, children] of Object.entries(next)) {
        next[id] = children.filter(child => !deleted.has(child))
      }
      return next
    })
    setExpandedConversationNodes(prev => {
      const next = { ...prev }
      for (const id of deleted) delete next[id]
      return next
    })
    setWindows(prev => {
      const next = { ...prev }
      for (const [id, item] of Object.entries(next)) {
        if (deleted.has(item.sessionId)) delete next[id]
      }
      return next
    })
    const deletedWindowIds = Object.entries(windows)
      .filter(([, item]) => deleted.has(item.sessionId))
      .map(([id]) => id)
    setWindowEvents(prev => {
      const next = { ...prev }
      for (const id of deletedWindowIds) delete next[id]
      return next
    })
    if (deletedWindowIds.length) {
      windowInputDraftsRef.current = { ...windowInputDraftsRef.current }
      for (const id of deletedWindowIds) delete windowInputDraftsRef.current[id]
      windowSendingRef.current = { ...windowSendingRef.current }
      for (const id of deletedWindowIds) delete windowSendingRef.current[id]
      setWindowInputs(prev => {
        const next = { ...prev }
        for (const id of deletedWindowIds) delete next[id]
        return next
      })
      setWindowInputResetTokens(prev => {
        const next = { ...prev }
        for (const id of deletedWindowIds) delete next[id]
        return next
      })
      setWindowSending(prev => {
        const next = { ...prev }
        for (const id of deletedWindowIds) delete next[id]
        return next
      })
    }
    setHighlights(prev => {
      const next = { ...prev }
      for (const [id, item] of Object.entries(next)) {
        if (deleted.has(item.sessionId)) delete next[id]
      }
      return next
    })
    setPdfHighlights(prev => {
      const next = { ...prev }
      for (const [id, item] of Object.entries(next)) {
        if (deleted.has(item.sessionId)) delete next[id]
      }
      return next
    })
    setSessions(prev => {
      const next = { ...prev }
      for (const id of deleted) delete next[id]
      return next
    })
  }

  async function openHighlight(highlightId: string) {
    const win = Object.values(windows).find(item => item.highlightId === highlightId)
    if (win) {
      raiseWindow(win.id)
      return
    }
    const highlight = highlights[highlightId] || pdfHighlights[highlightId]
    if (!highlight?.sessionId) return
    try {
      const branch = await fetchLearningSession(highlight.sessionId)
      await openFloatingSession(branch, {
        sourceSessionId: (highlight as any).sourceSessionId || branch.parent_session_id || '',
        title: branch.title || (highlight as any).label || '子问题',
        highlightId,
        action: (branch.selection?.action as SelectionAction) || (highlight as any).action,
        selectedText: branch.selection?.selected_text || highlight.text,
        displayQuestion: branch.selection?.custom_question,
        targetLanguage: branch.selection?.target_language,
        noteText: branch.selection?.note_text,
      })
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function restoreChatHighlights(sessionId: string) {
    if (!sessionId) return
    try {
      const children = await fetchLearningChildren(sessionId)
      const nextTextHighlights: Record<string, HighlightRecord> = {}
      for (const child of children.nodes) {
        const selection = child.selection || {}
        const source = selection.source_context || {}
        if (source.kind !== 'chat' || !selection.selected_text) continue
        const chatId = typeof source.chat_id === 'string'
          ? source.chat_id
          : String(source.location || '').replace(/^对话消息\s*/, '')
        if (!chatId) continue
        const highlightId = `persist_${child.session_id}`
        const color = branchColors[child.session_id] || deriveChildColor(sessionId, [...Object.values(sessions), ...children.nodes], branchColors)
        nextTextHighlights[highlightId] = {
          id: highlightId,
          sessionId: child.session_id,
          sourceSessionId: sessionId,
          chatId,
          text: selection.selected_text || '',
          textOffset: typeof source.text_offset === 'number' ? source.text_offset : undefined,
          occurrence: typeof source.occurrence === 'number' ? source.occurrence : undefined,
          action: (selection.action as SelectionAction) || 'question',
          label: selection.action_label || '提问',
          color,
        }
      }
      if (Object.keys(nextTextHighlights).length) {
        setHighlights(prev => ({ ...prev, ...nextTextHighlights }))
      }
    } catch {
      // Chat highlights are opportunistic; the conversation should still render.
    }
  }

  async function restoreFileHighlights(path: string) {
    try {
      const fileRoot = await getLearningFileRoot(accountId, path)
      const children = await fetchLearningChildren(fileRoot.session_id)
      setSessions(prev => ({
        ...prev,
        [fileRoot.session_id]: fileRoot,
        ...Object.fromEntries(children.nodes.map(item => [item.session_id, item])),
      }))
      setAccountRootIds(prev => prev.includes(fileRoot.session_id) ? prev : [...prev, fileRoot.session_id])
      setConversationChildren(prev => ({ ...prev, [fileRoot.session_id]: children.nodes.map(item => item.session_id) }))
      const nextTextHighlights: Record<string, HighlightRecord> = {}
      const nextPdfHighlights: Record<string, PdfHighlightRecord> = {}
      for (const child of children.nodes) {
        const selection = child.selection || {}
        const source = selection.source_context || {}
        if (source.path !== path || !selection.selected_text) continue
        const highlightId = `persist_${child.session_id}`
        const color = branchColors[child.session_id] || deriveChildColor(fileRoot.session_id, [...Object.values(sessions), fileRoot, ...children.nodes], branchColors)
        nextTextHighlights[highlightId] = {
          id: highlightId,
          sessionId: child.session_id,
          sourceSessionId: fileRoot.session_id,
          chatId: source.kind === 'markdown' ? `markdown:${path}` : `pdf:${path}:p${source.page || 1}`,
          text: selection.selected_text || '',
          textOffset: typeof source.text_offset === 'number' ? source.text_offset : undefined,
          occurrence: typeof source.occurrence === 'number' ? source.occurrence : undefined,
          action: (selection.action as SelectionAction) || 'question',
          label: selection.action_label || '提问',
          color,
        }
        if (source.kind === 'pdf' && Array.isArray(source.rects)) {
          nextPdfHighlights[highlightId] = {
            id: highlightId,
            sessionId: child.session_id,
            path,
            page: Number(source.page || String(source.location || '').match(/page (\d+)/)?.[1] || 1),
            text: selection.selected_text || '',
            rects: source.rects,
            color,
          }
        }
      }
      setHighlights(prev => ({ ...prev, ...nextTextHighlights }))
      setPdfHighlights(prev => ({ ...prev, ...nextPdfHighlights }))
    } catch {
      // File highlights are opportunistic; opening the file should still work.
    }
  }

  async function openWorkspaceItem(item: WorkspaceItem) {
    if (item.type === 'directory') {
      await refreshWorkspace(item.path)
      return
    }
    if (openFilesRef.current[item.path]) {
      setActiveFilePath(item.path)
      setActiveMode('files')
      return
    }
    if (item.is_pdf) {
      const tab: OpenFileTab = { path: item.path, name: item.name, url: workspaceOpenUrl(item.path), type: 'pdf', loading: true }
      setOpenFiles(prev => ({ ...prev, [item.path]: tab }))
      setActiveFilePath(item.path)
      setActiveMode('files')
      await restoreFileHighlights(item.path)
      try {
        const pdfText = await fetchWorkspacePdfText(item.path)
        setOpenFiles(prev => ({
          ...prev,
          [item.path]: {
            ...(prev[item.path] || tab),
            pdfText,
            loading: false,
            error: undefined,
          },
        }))
      } catch (err: any) {
        setOpenFiles(prev => ({
          ...prev,
          [item.path]: {
            ...(prev[item.path] || tab),
            loading: false,
            error: err.message || String(err),
          },
        }))
      }
      return
    }
    if (item.is_markdown) {
      const markdownTab: OpenFileTab = { path: item.path, name: item.name, url: workspaceOpenUrl(item.path), type: 'markdown', loading: true, viewMode: 'preview' }
      setOpenFiles(prev => ({ ...prev, [item.path]: markdownTab }))
      setActiveFilePath(item.path)
      setActiveMode('files')
      await restoreFileHighlights(item.path)
      try {
        const text = await fetchWorkspaceText(item.path)
        setOpenFiles(prev => ({
          ...prev,
          [item.path]: {
            ...(prev[item.path] || markdownTab),
            textContent: text.content,
            loading: false,
            dirty: false,
          },
        }))
      } catch (err: any) {
        setOpenFiles(prev => ({
          ...prev,
          [item.path]: {
            ...(prev[item.path] || markdownTab),
            loading: false,
            error: err.message || String(err),
          },
        }))
      }
      return
    }
    const tab: OpenFileTab = { path: item.path, name: item.name, url: workspaceOpenUrl(item.path), type: 'file' }
    setOpenFiles(prev => ({ ...prev, [item.path]: tab }))
    setActiveFilePath(item.path)
    setActiveMode('files')
  }

  async function uploadFiles(files: FileList | null, targetPath = workspace?.cwd || '') {
    if (!files || !workspace) return
    try {
      for (const file of Array.from(files)) {
        await uploadWorkspaceFile(targetPath, file)
      }
      await refreshWorkspace(targetPath)
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function createFolderFromPrompt(targetPath = workspace?.cwd || '') {
    if (!workspace) return
    const name = window.prompt('新文件夹名称')
    if (!name) return
    try {
      await createWorkspaceFolder(targetPath, name)
      await refreshWorkspace(targetPath)
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function pasteClipboard() {
    if (!workspace || !clipboardPath) return
    try {
      await copyWorkspaceItem(clipboardPath, workspace.cwd)
      setClipboardPath(null)
      await refreshWorkspace(workspace.cwd)
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function removeWorkspaceItem(item: WorkspaceItem) {
    if (!window.confirm(`删除 ${item.name}？`)) return
    try {
      await deleteWorkspaceItem(item.path)
      setOpenFiles(prev => {
        const next = { ...prev }
        delete next[item.path]
        return next
      })
      if (activeFilePath === item.path) {
        const remaining = Object.keys(openFiles).filter(path => path !== item.path)
        setActiveFilePath(remaining[0] || null)
        if (!remaining.length) setActiveMode('chat')
      }
      if (clipboardPath === item.path) setClipboardPath(null)
      await refreshWorkspace(workspace?.cwd || '')
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  function contextTargetDir(item: WorkspaceTreeNode | null): string {
    if (!item) return workspace?.cwd || ''
    return item.type === 'directory' ? item.path : parentPath(item.path)
  }

  function openUploadDialog(targetPath: string) {
    setUploadTargetPath(targetPath)
    uploadInputRef.current?.click()
  }

  function handleFileContextAction(action: 'upload' | 'new-folder' | 'copy' | 'paste' | 'delete' | 'download' | 'open') {
    const item = fileContextMenu?.item || null
    setFileContextMenu(null)
    if (action === 'upload') {
      openUploadDialog(contextTargetDir(item))
      return
    }
    if (action === 'new-folder') {
      createFolderFromPrompt(contextTargetDir(item))
      return
    }
    if (action === 'copy' && item) {
      setClipboardPath(item.path)
      return
    }
    if (action === 'paste') {
      const target = contextTargetDir(item)
      if (!clipboardPath) return
      copyWorkspaceItem(clipboardPath, target)
        .then(() => {
          setClipboardPath(null)
          return refreshWorkspace(target)
        })
        .catch((err: any) => setError(err.message || String(err)))
      return
    }
    if (action === 'delete' && item) {
      removeWorkspaceItem(item)
      return
    }
    if (action === 'download' && item?.type === 'file') {
      window.open(workspaceOpenUrl(item.path, true), '_blank', 'noreferrer')
      return
    }
    if (action === 'open' && item) {
      openWorkspaceItem(item)
    }
  }

  return <div className="learning-shell" style={{
    '--sidebar-width': `${sidebarWidth}px`,
    '--detail-width': `${detailWidth}px`,
  } as any}>
    {error && <div className="error-banner">{error}<button className="error-close" onClick={() => setError(null)}>x</button></div>}
    <aside className="learning-sidebar">
      <div className="search-row">
        <div className="search-box"><Search size={16}/><input value={filter} onChange={e => setFilter(e.target.value)} placeholder="搜索问题链"/></div>
        <button className="icon-button" onClick={newQuestionChain} title="新建问题链"><Plus size={18}/></button>
      </div>
      <div className="chain-list">
        <div className="account-tree-root">
          {visibleSessions.map(item => <ConversationTreeNode
            key={item.session_id}
            item={item}
            depth={0}
            activeSessionId={session?.session_id || ''}
            childrenById={conversationChildren}
            sessions={sessions}
            expanded={expandedConversationNodes}
            colors={branchColors}
            onToggle={stableToggleNode}
            onActivate={stableActivateSession}
            onDelete={stableDeleteRoot}
          />)}
        </div>
      </div>
    </aside>
    <button className="column-resizer left-resizer" title="调整左侧栏宽度" onMouseDown={event => beginColumnResize('left', event)}/>

    <main className={activeMode === 'files' ? 'learning-main file-mode' : 'learning-main'}>
      {activeMode === 'files' ? <FileWorkspacePanel
        openFiles={openFiles}
        activeFile={activeFile}
        activeFilePath={activeFilePath}
        pdfHighlights={pdfHighlights}
        textHighlights={highlights}
        pdfZoom={pdfZoom}
        scrollPositions={fileScrollPositionsRef.current}
        onFileScroll={(path, scrollTop) => { fileScrollPositionsRef.current[path] = scrollTop }}
        onZoomOut={() => setPdfZoom(prev => Math.max(1.15, Math.round((prev - 0.15) * 100) / 100))}
        onZoomIn={() => setPdfZoom(prev => Math.min(2.6, Math.round((prev + 0.15) * 100) / 100))}
        onActivate={(path) => {
          setActiveFilePath(path)
          setActiveMode('files')
        }}
        onClose={(path) => {
          setOpenFiles(prev => {
            const next = { ...prev }
            delete next[path]
            return next
          })
          if (activeFilePath === path) {
            const remaining = Object.keys(openFiles).filter(item => item !== path)
            setActiveFilePath(remaining[0] || null)
            if (!remaining.length) setActiveMode('chat')
          }
        }}
        onUpdateMarkdown={(path, content) => {
          setOpenFiles(prev => ({
            ...prev,
            [path]: {
              ...prev[path],
              textContent: content,
              dirty: true,
            },
          }))
        }}
        onToggleMarkdownMode={(path, mode) => {
          setOpenFiles(prev => ({
            ...prev,
            [path]: {
              ...prev[path],
              viewMode: mode,
            },
          }))
        }}
        onSaveMarkdown={async (path) => {
          const tab = openFiles[path]
          if (!tab) return
          try {
            await saveWorkspaceText(path, tab.textContent || '')
            setOpenFiles(prev => ({
              ...prev,
              [path]: {
                ...prev[path],
                dirty: false,
              },
            }))
            await refreshWorkspace(workspace?.cwd || '')
          } catch (err: any) {
            setError(err.message || String(err))
          }
        }}
        onMarkdownSelection={(result, path) => {
          const location = describeMarkdownSelectionLocation(openFiles[path]?.textContent || '', result.text)
          showSelectionMenu({
            sourceSessionId: session?.session_id || '',
            chatId: `markdown:${path}`,
            text: result.text,
            textOffset: result.textOffset,
            occurrence: result.occurrence,
            x: Math.min(result.rect.left + result.rect.width / 2, window.innerWidth - 220),
            y: Math.max(12, result.rect.top - 46),
            sourceContext: { kind: 'markdown', path, location, text_offset: result.textOffset, occurrence: result.occurrence },
          }, result.range)
        }}
        onPdfSelection={(text, page, rect, rects) => {
          const filePath = activeFile?.path || ''
          showSelectionMenu({
            sourceSessionId: session?.session_id || '',
            chatId: `pdf:${filePath}:p${page}`,
            text,
            x: Math.min(rect.left + rect.width / 2, window.innerWidth - 220),
            y: Math.max(12, rect.top - 46),
            pdfContext: filePath ? { path: filePath, page, rects } : undefined,
            sourceContext: filePath ? { kind: 'pdf', path: filePath, location: `page ${page}`, page, rects } : undefined,
          })
        }}
        onOpenHighlight={openHighlight}
      /> : <section
        ref={conversationPanelRef}
        className="conversation-panel"
        onScroll={event => {
          if (session?.session_id) chatScrollPositionsRef.current[session.session_id] = event.currentTarget.scrollTop
        }}
      >
          {!chatItems.length && eventsLoading && <div className="learning-empty">
            <BookOpen size={28}/>
            <div>正在载入对话上下文...</div>
          </div>}
          {!chatItems.length && !eventsLoading && <div className="learning-empty">
            <BookOpen size={28}/>
            <div>开始新对话</div>
            <div>输入一个问题开始学习。当前问题链会独立保存上下文；遇到旁支问题时切换到“新分支”发送。</div>
          </div>}
          {chatItems.map(item => <article key={item.id} className={`learn-bubble ${item.role}`} style={colorVars(branchColors[session?.session_id || ''] || ROOT_BRANCH_COLOR)}>
            <div className="bubble-head">
              <span>{roleLabel(item.role)}</span>
              {item.role === 'user' && <UserMessageActions
                sessionId={session?.session_id || ''}
                item={item}
                sessions={sessions}
                menuKey={`main:${item.id}`}
                openMenus={messageBranchMenus}
                onToggleMenu={(key) => setMessageBranchMenus(prev => ({ ...prev, [key]: !prev[key] }))}
                onFork={forkFromUserMessage}
                onSetback={setbackToUserMessage}
                onOpenBranch={(child) => {
                  openFloatingSession(child, {
                    sourceSessionId: session?.session_id || '',
                    title: child.title || (child.root_question || '').slice(0, 10) || '分支',
                    displayQuestion: child.root_question || child.last_question,
                  })
                  seedWindowInput(`win_${child.session_id}`, child.root_question || child.last_question || '')
                }}
              />}
            </div>
            <MessageContent
              sessionId={session?.session_id || ''}
              item={item}
              collapsed={!!collapsedMessages[messageCollapseKey(session?.session_id || '', item.id)]}
              highlights={highlights}
              onToggleCollapse={() => {
                const key = messageCollapseKey(session?.session_id || '', item.id)
                setCollapsedMessages(prev => ({ ...prev, [key]: !prev[key] }))
              }}
              onTextSelection={(container, anchor, event) => handleTextSelection(session?.session_id || '', item.id, container, anchor, event)}
              onOpenHighlight={openHighlight}
            />
          </article>)}
          {session?.running && <ThinkingState events={events} running={session.running} todoBoard={session.todo_board}/>}
        </section>}

      {activeMode === 'chat' && <section className="learning-composer">
        {session?.truncated && !session.running && <div className="truncation-continue">
          <span>本次回答已达到思考轮数上限（{session.max_iterations || '当前预算'}）。</span>
          <button className="secondary" onClick={continueActiveAfterTruncation}>继续思考</button>
        </div>}
        <div className="composer-row">
          <ComposerInput
            value={input}
            onChange={setInput}
            onDraftChange={value => { inputDraftRef.current = value }}
            resetSignal={inputResetToken}
            onSubmit={submit}
          />
          <button className="primary" onClick={() => submit()}><Send size={18}/>发送</button>
          <div className="composer-side-actions">
            <button className="secondary icon-only" onClick={stop} title="停止当前回答"><Square size={16}/></button>
            <ToolsToggleButton
              enabled={session?.tools_enabled !== false}
              disabled={!session || !!session.running}
              onToggle={toggleActiveSessionTools}
            />
          </div>
        </div>
      </section>}
    </main>
    <button className="column-resizer right-resizer" title="调整右侧栏宽度" onMouseDown={event => beginColumnResize('right', event)}/>

    <aside className="learning-detail">
      <input
        ref={uploadInputRef}
        className="hidden-file-input"
        type="file"
        multiple
        onChange={event => {
          uploadFiles(event.target.files, uploadTargetPath)
          event.currentTarget.value = ''
        }}
      />
      <FileBrowser
        listing={workspace}
        tree={workspaceTree}
        expanded={expandedFolders}
        clipboardPath={clipboardPath}
        onRefresh={() => refreshWorkspace()}
        onToggleFolder={toggleWorkspaceFolder}
        onOpen={openWorkspaceItem}
        onContextMenu={(event, item) => {
          event.preventDefault()
          event.stopPropagation()
          setFileContextMenu({ x: event.clientX, y: event.clientY, item })
        }}
      />
    </aside>
    {fileContextMenu && <FileContextMenu
      menu={fileContextMenu}
      clipboardPath={clipboardPath}
      onAction={handleFileContextAction}
    />}
    {selectionMenu && <SelectionMenu menu={selectionMenu} onAction={handleSelectionAction}/>}
    {pendingAction?.action === 'question' && <QuestionDialog
      selectedText={pendingAction.text}
      value={questionDraft}
      onChange={setQuestionDraft}
      onCancel={() => setPendingAction(null)}
      onSubmit={submitQuestionDialog}
    />}
    {pendingAction?.action === 'translate' && <TranslateDialog
      selectedText={pendingAction.text}
      choice={translationChoice}
      customLanguage={customLanguage}
      onChoice={setTranslationChoice}
      onCustomLanguage={setCustomLanguage}
      onCancel={() => setPendingAction(null)}
      onSubmit={submitTranslationDialog}
    />}
    {pendingAction?.action === 'note' && <NoteDialog
      selectedText={pendingAction.text}
      value={noteDraft}
      onChange={setNoteDraft}
      onCancel={() => setPendingAction(null)}
      onSave={() => saveNoteDialog(false)}
      onSend={() => saveNoteDialog(true)}
    />}
    <FloatingWindows
      windows={windows}
      sessions={sessions}
      eventsByWindow={windowEvents}
      inputSeeds={windowInputs}
      inputResetTokens={windowInputResetTokens}
      sendingByWindow={windowSending}
      collapsedMessages={collapsedMessages}
      highlights={highlights}
      onInputDraftChange={(windowId, value) => { windowInputDraftsRef.current[windowId] = value }}
      onToggleCollapse={(messageId) => setCollapsedMessages(prev => ({ ...prev, [messageId]: !prev[messageId] }))}
      onSend={sendWindowMessage}
      onStop={stopWindow}
      onMinimize={minimizeWindow}
      onMaximize={maximizeWindow}
      onClose={closeWindowAndDeleteSubtree}
      onRaise={raiseWindow}
      onMove={moveWindow}
      onResize={resizeWindow}
      onOpenSessionWindow={(child, parentWindowId) => {
        const existing = Object.values(windows).find(win => win.sessionId === child.session_id)
        if (existing) {
          raiseWindow(existing.id)
        } else {
          const parent = windows[parentWindowId]
          openFloatingSession(child, {
            sourceSessionId: parent?.sessionId || child.parent_session_id || '',
            title: child.title || child.root_question || '新分支',
            displayQuestion: child.root_question || child.last_question,
          })
          seedWindowInput(`win_${child.session_id}`, child.root_question || child.last_question || '')
        }
      }}
      onTextSelection={handleTextSelection}
      onOpenHighlight={openHighlight}
      messageBranchMenus={messageBranchMenus}
      onToggleMessageBranchMenu={(key) => setMessageBranchMenus(prev => ({ ...prev, [key]: !prev[key] }))}
      onForkMessage={forkFromUserMessage}
      onSetbackMessage={setbackToUserMessage}
      onToggleTools={async (sessionId, enabled) => {
        try {
          const updated = await setLearningToolsEnabled(sessionId, enabled)
          setSessions(prev => ({ ...prev, [updated.session_id]: updated }))
          if (session?.session_id === updated.session_id) setSession(updated)
        } catch (err: any) {
          setError(err.message || String(err))
        }
      }}
    />
  </div>
}

function sameLearningSession(a?: LearningSessionState, b?: LearningSessionState): boolean {
  if (!a || !b) return false
  return a.session_id === b.session_id &&
    a.model === b.model &&
    a.running === b.running &&
    a.event_count === b.event_count &&
    a.last_error === b.last_error &&
    a.last_response === b.last_response &&
    a.token_usage === b.token_usage &&
    (a as any).last_token_usage === (b as any).last_token_usage &&
    (a as any).parent_session_token_usage === (b as any).parent_session_token_usage &&
    (a as any).children_token_usage === (b as any).children_token_usage &&
    (a as any).total_token_usage_including_children === (b as any).total_token_usage_including_children &&
    a.title === b.title &&
    a.root_question === b.root_question &&
    a.last_question === b.last_question &&
    a.parent_session_id === b.parent_session_id &&
    a.account_id === b.account_id &&
    a.node_kind === b.node_kind &&
    a.file_path === b.file_path &&
    a.child_count === b.child_count &&
    a.source_message_index === b.source_message_index &&
    a.tools_enabled === b.tools_enabled &&
    sameStringList(a.allowed_tools, b.allowed_tools) &&
    sameJsonValue(a.selection, b.selection) &&
    sameJsonValue((a as any).token_usage_breakdown, (b as any).token_usage_breakdown) &&
    sameJsonValue(a.todo_board, b.todo_board)
}

function sameEventList(a: ContextEvent[] = [], b: ContextEvent[] = []): boolean {
  if (a === b) return true
  if (a.length !== b.length) return false
  for (let index = 0; index < a.length; index += 1) {
    const left = a[index]
    const right = b[index]
    if (left.event_id !== right.event_id ||
      left.event_type !== right.event_type ||
      left.created_at !== right.created_at ||
      left.session_id !== right.session_id) {
      return false
    }
  }
  return true
}

function sameStringList(a?: string[], b?: string[]): boolean {
  const left = a || []
  const right = b || []
  if (left.length !== right.length) return false
  return left.every((value, index) => value === right[index])
}

function sameJsonValue(a: any, b: any): boolean {
  if (a === b) return true
  try {
    return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
  } catch {
    return false
  }
}

function messageCollapseKey(sessionId: string, messageId: string): string {
  return `${sessionId || 'unknown'}:${messageId}`
}

function buildChatItems(events: ContextEvent[]): ChatItem[] {
  const items: ChatItem[] = []
  const hasAppendedUsers = events.some(event => event.event_type === 'message_appended' && event.payload?.message?.role === 'user')
  for (const event of events) {
    if (!hasAppendedUsers && event.event_type === 'user_input_received') {
      items.push({ id: event.event_id, role: 'user', content: event.payload?.content || '', createdAt: event.created_at })
      continue
    }
    if (event.event_type === 'message_appended') {
      const message = event.payload?.message
      if (message?.role === 'assistant' && isMeaningfulContent(message.content)) {
        items.push({ id: event.event_id, role: 'assistant', content: message.content, createdAt: event.created_at, messageIndex: event.payload?.message_index })
      }
      if (message?.role === 'user' && isMeaningfulContent(message.content)) {
        items.push({ id: event.event_id, role: 'user', content: message.content, createdAt: event.created_at, messageIndex: event.payload?.message_index })
      }
    }
  }
  return items
}

function isMeaningfulContent(value: any): boolean {
  if (value?.payload_ref) return true
  return renderContent(value).trim().length > 0
}

function visibleWindowItems(items: ChatItem[], win: FloatingWindowState): ChatItem[] {
  let firstUserRewritten = false
  return items.map(item => {
    if (item.role !== 'user' || firstUserRewritten || (!win.action && !win.displayQuestion)) return item
    firstUserRewritten = true
    return { ...item, content: buildCleanSelectionDisplay(win) }
  })
}

function thinkingStats(events: ContextEvent[], running?: boolean, nowSeconds = Date.now() / 1000, fallbackStartSeconds = nowSeconds): { elapsedSeconds: number, rounds: number } | null {
  if (!running) return null
  const lastInputIndex = Math.max(...events.map((event, index) => event.event_type === 'user_input_received' ? index : -1))
  const afterInput = lastInputIndex >= 0 ? events.slice(lastInputIndex) : []
  // ThinkingState is only rendered while the backend still reports running=true.
  // Do not hide it just because an assistant/tool-call message has already been
  // appended: during long tool calls the assistant may emit a partial message
  // before todo_manage updates the board, and hiding here makes the GUI lose the
  // live todo progress panel.
  const start = lastInputIndex >= 0 ? (events[lastInputIndex]?.created_at || fallbackStartSeconds) : fallbackStartSeconds
  return {
    elapsedSeconds: Math.max(0, Math.floor(nowSeconds - start)),
    rounds: afterInput.filter(event => event.event_type === 'llm_request_snapshot').length,
  }
}

function thinkingLabel(events: ContextEvent[], running?: boolean, nowSeconds = Date.now() / 1000, fallbackStartSeconds = nowSeconds): string {
  const stats = thinkingStats(events, running, nowSeconds, fallbackStartSeconds)
  if (!stats) return ''
  return `思考过程中 · ${stats.elapsedSeconds}s · ${stats.rounds} 轮`
}

function ThinkingState({ events, running, todoBoard }: { events: ContextEvent[], running?: boolean, todoBoard?: TodoBoardState | null }) {
  const startedAtRef = useRef(Date.now() / 1000)
  const [timerTick, setTimerTick] = useState(0)
  useEffect(() => {
    if (!running) return
    startedAtRef.current = Date.now() / 1000
    setTimerTick(tick => tick + 1)
    const timer = window.setInterval(() => setTimerTick(tick => tick + 1), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  const nowSeconds = Date.now() / 1000
  const label = thinkingLabel(events, running, nowSeconds, startedAtRef.current)
  void timerTick
  if (!label) return null
  return <div className="thinking-state">
    <div className="thinking-state-header">{label}</div>
    <TodoBoardPreview board={todoBoard}/>
  </div>
}

function TodoBoardPreview({ board }: { board?: TodoBoardState | null }) {
  if (!board?.exists || !Array.isArray(board.tasks) || board.tasks.length === 0) {
    return <div className="thinking-planning">agent 正在规划，todo 看板生成后会自动显示。</div>
  }
  const counts = board.status_counts || {}
  const total = board.total ?? board.tasks.length
  const completed = board.completed ?? counts.completed ?? 0
  const progress = total ? Math.round(completed / total * 100) : 0
  const visibleTasks = board.tasks.slice(0, 8)
  return <div className="thinking-todo-board">
    <div className="todo-board-summary">
      <span>Todo 看板{board.session_id ? ` · ${board.session_id}` : ''}</span>
      <strong>{completed}/{total}</strong>
      <span>{progress}%</span>
    </div>
    <div className="todo-board-counts">
      <span>🚧 {counts.in_progress || 0}</span>
      <span>🕓 {counts.pending || 0}</span>
      <span>🧩 {counts.needs_split || 0}</span>
      <span>🧱 {counts.blocked || 0}</span>
      <span>✅ {counts.completed || 0}</span>
      <span>❌ {counts.failed || 0}</span>
    </div>
    <ul className="todo-board-list">
      {visibleTasks.map(task => <li key={task.id} className={`todo-board-item ${task.status}`}>
        <span className="todo-board-status">{todoStatusIcon(task.status)}</span>
        <span className="todo-board-text"><b>{task.id}</b> {task.description}</span>
        {task.assigned_to && <span className="todo-board-owner">{task.assigned_to}</span>}
      </li>)}
    </ul>
    {(board.truncated || board.tasks.length > visibleTasks.length) && <div className="todo-board-more">还有更多任务，已折叠显示。</div>}
    {!!board.ready_to_execute?.length && <div className="todo-board-ready">Ready: {board.ready_to_execute.slice(0, 6).join(', ')}</div>}
  </div>
}

function todoStatusIcon(status: string): string {
  if (status === 'completed') return '✅'
  if (status === 'in_progress') return '🚧'
  if (status === 'needs_split') return '🧩'
  if (status === 'blocked') return '🧱'
  if (status === 'failed') return '❌'
  if (status === 'cancelled') return '🚫'
  return '🕓'
}

function buildCleanSelectionDisplay(win: FloatingWindowState): string {
  const selected = win.selectedText || ''
  if (win.action === 'note') {
    return `笔记：\n${win.noteText || ''}\n\n关联文本：\n${selected}`
  }
  if (win.action === 'question') {
    return win.displayQuestion || ''
  }
  if (win.action === 'translate') {
    return `翻译为：${win.targetLanguage || ''}\n\n${selected}`
  }
  if (win.action === 'explain') {
    return `解释：\n${selected}`
  }
  if (win.action === 'summarize') {
    return `总结：\n${selected}`
  }
  if (win.displayQuestion) {
    return win.displayQuestion
  }
  return selected
}

function roleLabel(role: ChatItem['role']): string {
  if (role === 'user') return '你的问题'
  if (role === 'tool') return '学习工具'
  return '学习助手'
}

function formatFileSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = size
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

function describeMarkdownSelectionLocation(content: string, selectedText: string): string {
  const source = String(content || '')
  const selected = String(selectedText || '').trim()
  if (!source || !selected) return 'selected markdown text'
  const exactIndex = source.indexOf(selected)
  const normalizedIndex = exactIndex >= 0 ? exactIndex : source.replace(/\s+/g, ' ').indexOf(selected.replace(/\s+/g, ' '))
  if (exactIndex < 0 && normalizedIndex < 0) return 'selected markdown text'
  const index = Math.max(0, exactIndex >= 0 ? exactIndex : normalizedIndex)
  const before = source.slice(0, index)
  const startLine = before.split('\n').length
  const lineCount = Math.max(1, selected.split('\n').length)
  const endLine = startLine + lineCount - 1
  return startLine === endLine ? `line ${startLine}` : `lines ${startLine}-${endLine}`
}

function parentPath(path: string): string {
  const parts = String(path || '').split('/').filter(Boolean)
  parts.pop()
  return parts.join('/')
}

function shouldSubmitFromKey(event: any): boolean {
  const nativeEvent = event.nativeEvent || {}
  return event.key === 'Enter' && !event.shiftKey && !event.isComposing && !nativeEvent.isComposing && nativeEvent.keyCode !== 229
}

function SelectionMenu({ menu, onAction }: { menu: SelectionMenuState, onAction: (action: SelectionAction) => void }) {
  return <div className="selection-menu" style={{ left: menu.x, top: menu.y }} onPointerDown={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()}>
    {SELECTION_ACTIONS.map(item => {
      const Icon = item.icon
      return <button key={item.action} onClick={() => onAction(item.action)}><Icon size={14}/>{item.label}</button>
    })}
  </div>
}

function ToolsToggleButton({ enabled, disabled, onToggle }: {
  enabled: boolean
  disabled?: boolean
  onToggle: (enabled: boolean) => void
}) {
  return <button
    type="button"
    className={enabled ? 'tools-toggle active' : 'tools-toggle'}
    disabled={disabled}
    title="控制下一次请求是否携带 tools schema"
    onClick={() => onToggle(!enabled)}
  >
    <span>Tools</span>
    <strong>{enabled ? 'On' : 'Off'}</strong>
  </button>
}

function ComposerInput({ value, onChange, onDraftChange, onSubmit, className, resetSignal = 0, disabled = false }: {
  value: string
  onChange?: (value: string) => void
  onDraftChange?: (value: string) => void
  onSubmit: (value: string) => void
  className?: string
  resetSignal?: number
  disabled?: boolean
}) {
  const [draft, setDraft] = useState(value)
  const resetSignalRef = useRef(resetSignal)
  useEffect(() => {
    setDraft(value)
    onDraftChange?.(value)
  }, [value])
  useEffect(() => {
    if (resetSignalRef.current === resetSignal) return
    resetSignalRef.current = resetSignal
    setDraft(value)
    onDraftChange?.(value)
    onChange?.(value)
  }, [resetSignal])
  return <textarea
    className={className}
    disabled={disabled}
    value={draft}
    onChange={event => {
      const next = event.target.value
      setDraft(next)
      onDraftChange?.(next)
    }}
    onBlur={() => onChange?.(draft)}
    onKeyDown={event => {
      if (shouldSubmitFromKey(event)) {
        event.preventDefault()
        const text = draft
        onChange?.(text)
        if (!disabled) onSubmit(text)
      }
    }}
  />
}

function QuestionDialog({ selectedText, value, onChange, onCancel, onSubmit }: {
  selectedText: string
  value: string
  onChange: (value: string) => void
  onCancel: () => void
  onSubmit: () => void
}) {
  return <div className="modal-backdrop">
    <section className="action-dialog">
      <header>围绕选中文本提问</header>
      <div className="selected-preview">{selectedText}</div>
      <textarea value={value} onChange={e => onChange(e.target.value)} placeholder="输入你想追问的问题..."/>
      <footer>
        <button className="secondary" onClick={onCancel}>取消</button>
        <button className="primary" onClick={onSubmit}>提问</button>
      </footer>
    </section>
  </div>
}

function TranslateDialog({ selectedText, choice, customLanguage, onChoice, onCustomLanguage, onCancel, onSubmit }: {
  selectedText: string
  choice: '英语' | '中文' | '其他'
  customLanguage: string
  onChoice: (value: '英语' | '中文' | '其他') => void
  onCustomLanguage: (value: string) => void
  onCancel: () => void
  onSubmit: () => void
}) {
  return <div className="modal-backdrop">
    <section className="action-dialog">
      <header>选择翻译语言</header>
      <div className="selected-preview">{selectedText}</div>
      <div className="language-options">
        {(['英语', '中文', '其他'] as const).map(item => <button key={item} className={choice === item ? 'selected' : ''} onClick={() => onChoice(item)}>{item}</button>)}
      </div>
      {choice === '其他' && <input className="language-input" value={customLanguage} onChange={e => onCustomLanguage(e.target.value)} placeholder="输入目标语言，例如日语、德语、法语"/>}
      <footer>
        <button className="secondary" onClick={onCancel}>取消</button>
        <button className="primary" onClick={onSubmit}>翻译</button>
      </footer>
    </section>
  </div>
}

function NoteDialog({ selectedText, value, onChange, onCancel, onSave, onSend }: {
  selectedText: string
  value: string
  onChange: (value: string) => void
  onCancel: () => void
  onSave: () => void
  onSend: () => void
}) {
  return <div className="modal-backdrop">
    <section className="action-dialog">
      <header>写手写笔记</header>
      <div className="selected-preview">{selectedText}</div>
      <textarea value={value} onChange={event => onChange(event.target.value)} placeholder="写下你的理解、疑问或备注..."/>
      <footer>
        <button className="secondary" onClick={onCancel}>取消</button>
        <button className="secondary" disabled={!value.trim()} onClick={onSave}>保存笔记</button>
        <button className="primary" disabled={!value.trim()} onClick={onSend}>发送给模型</button>
      </footer>
    </section>
  </div>
}

function UserMessageActions({ sessionId, item, sessions, menuKey, openMenus, onToggleMenu, onFork, onSetback, onOpenBranch }: {
  sessionId: string
  item: ChatItem
  sessions: Record<string, LearningSessionState>
  menuKey: string
  openMenus: Record<string, boolean>
  onToggleMenu: (key: string) => void
  onFork: (sessionId: string, messageIndex?: number, windowId?: string) => void
  onSetback: (sessionId: string, messageIndex?: number, windowId?: string) => void
  onOpenBranch: (child: LearningSessionState) => void
}) {
  const branches = Object.values(sessions).filter(
    session => session.parent_session_id === sessionId && session.source_message_index === item.messageIndex,
  )
  return <div className="message-actions">
    <button disabled={item.messageIndex == null} onClick={() => onFork(sessionId, item.messageIndex)}>fork</button>
    <button disabled={item.messageIndex == null} onClick={() => onSetback(sessionId, item.messageIndex)}>setback</button>
    <button onClick={() => onToggleMenu(menuKey)}>...</button>
    {openMenus[menuKey] && <div className="message-branch-menu">
      {!branches.length && <div className="muted">暂无分支</div>}
      {branches.map(child => <button key={child.session_id} onClick={() => onOpenBranch(child)}>{child.title || child.root_question || child.session_id}</button>)}
    </div>}
  </div>
}

const ConversationTreeNode = memo(function ConversationTreeNode({ item, depth, activeSessionId, childrenById, sessions, expanded, colors, onToggle, onActivate, onDelete }: {
  item: LearningSessionState
  depth: number
  activeSessionId: string
  childrenById: Record<string, string[]>
  sessions: Record<string, LearningSessionState>
  expanded: Record<string, boolean>
  colors: Record<string, BranchColor>
  onToggle: (sessionId: string) => void
  onActivate: (sessionId: string) => void
  onDelete: (sessionId: string) => void
}) {
  const childIds = childrenById[item.session_id] || []
  const hasLoadedChildren = childIds.length > 0
  const hasChildren = hasLoadedChildren || Number(item.child_count || 0) > 0
  const isOpen = !!expanded[item.session_id]
  return <div className="conversation-tree-node">
    <div
      className={item.session_id === activeSessionId ? 'conversation-row active' : 'conversation-row'}
      style={{ ...colorVars(colors[item.session_id] || ROOT_BRANCH_COLOR), '--tree-depth': depth } as any}
    >
      <button className="tree-toggle" onClick={() => hasChildren && onToggle(item.session_id)}>{hasChildren ? (isOpen ? 'v' : '>') : ''}</button>
      <button className="conversation-open" onClick={() => onActivate(item.session_id)}>
        <span className="chain-title">{item.title || item.root_question || item.file_path || '新的学习问题'}</span>
        <span className="chain-meta">{item.node_kind === 'file_root' ? 'file root' : item.running ? '运行中' : `${item.event_count || 0} events`}</span>
      </button>
      <button className="chain-delete" title="删除节点及其子树" onClick={() => onDelete(item.session_id)}><Trash2 size={13}/></button>
    </div>
    {isOpen && hasLoadedChildren && childIds.map(childId => sessions[childId] ? <ConversationTreeNode
      key={childId}
      item={sessions[childId]}
      depth={depth + 1}
      activeSessionId={activeSessionId}
      childrenById={childrenById}
      sessions={sessions}
      expanded={expanded}
      colors={colors}
      onToggle={onToggle}
      onActivate={onActivate}
      onDelete={onDelete}
    /> : null)}
  </div>
})

function FileBrowser({
  listing,
  tree,
  expanded,
  clipboardPath,
  onRefresh,
  onToggleFolder,
  onOpen,
  onContextMenu,
}: {
  listing: WorkspaceListing | null
  tree: WorkspaceTreeNode | null
  expanded: Record<string, boolean>
  clipboardPath: string | null
  onRefresh: () => void
  onToggleFolder: (path: string) => void
  onOpen: (item: WorkspaceItem) => void
  onContextMenu: (event: any, item: WorkspaceTreeNode | null) => void
}) {
  const currentPath = listing?.cwd || ''
  const crumbs = currentPath ? currentPath.split('/') : []
  return <section className="file-browser" onContextMenu={event => onContextMenu(event, null)}>
    <header className="file-head">
      <div>
        <div className="detail-title"><Folder size={14}/>EXPLORER</div>
        <div className="file-path">{crumbs.length ? crumbs.join(' / ') : 'outputs'}</div>
      </div>
      <button className="icon-button" title="刷新文件列表" onClick={onRefresh}><Workflow size={14}/></button>
    </header>
    {clipboardPath && <div className="clipboard-note"><Copy size={13}/>待粘贴：{clipboardPath}</div>}
    <div className="file-list">
      {!tree && <div className="muted">正在读取文件系统...</div>}
      {tree?.children?.map(item => <FileTreeNode
        key={item.path || item.name}
        node={item}
        depth={0}
        expanded={expanded}
        onToggleFolder={onToggleFolder}
        onOpen={onOpen}
        onContextMenu={onContextMenu}
      />)}
      {tree && !tree.children?.length && <div className="muted">暂无文件。outputs/papers 会在初始化时自动创建；read_paper 会在阅读笔记产生或手动新建后显示。</div>}
    </div>
  </section>
}

function FileTreeNode({ node, depth, expanded, onToggleFolder, onOpen, onContextMenu }: {
  node: WorkspaceTreeNode
  depth: number
  expanded: Record<string, boolean>
  onToggleFolder: (path: string) => void
  onOpen: (item: WorkspaceItem) => void
  onContextMenu: (event: any, item: WorkspaceTreeNode) => void
}) {
  const isOpen = expanded[node.path || '']
  const Icon = node.type === 'directory' ? Folder : node.is_pdf ? FileText : File
  return <div className="file-tree-node">
    <div className={node.is_pdf ? 'file-row pdf' : 'file-row'} style={{ '--tree-depth': depth } as any} onContextMenu={event => onContextMenu(event, node)}>
      <button className="tree-toggle" onClick={() => node.type === 'directory' && onToggleFolder(node.path || '')}>
        {node.type === 'directory' ? (isOpen ? 'v' : '>') : <span/>}
      </button>
      <button className="file-open" onClick={() => onOpen(node)} title={node.path || 'workspace root'}>
        <Icon size={14}/>
        <span className="file-name">{node.name}</span>
        {node.type === 'file' && <span className="file-size">{formatFileSize(node.size)}</span>}
      </button>
    </div>
    {node.type === 'directory' && isOpen && node.children?.map(child => <FileTreeNode
      key={child.path || child.name}
      node={child}
      depth={depth + 1}
      expanded={expanded}
      onToggleFolder={onToggleFolder}
      onOpen={onOpen}
      onContextMenu={onContextMenu}
    />)}
  </div>
}

function FileContextMenu({ menu, clipboardPath, onAction }: {
  menu: FileContextMenuState
  clipboardPath: string | null
  onAction: (action: 'upload' | 'new-folder' | 'copy' | 'paste' | 'delete' | 'download' | 'open') => void
}) {
  const item = menu.item
  return <div className="file-context-menu" style={{ left: menu.x, top: menu.y }} onMouseDown={event => event.stopPropagation()}>
    {item && <button onClick={() => onAction('open')}>Open</button>}
    <button onClick={() => onAction('upload')}>Upload...</button>
    <button onClick={() => onAction('new-folder')}>New Folder</button>
    {item && <button onClick={() => onAction('copy')}>Copy</button>}
    <button disabled={!clipboardPath} onClick={() => onAction('paste')}>Paste</button>
    {item?.type === 'file' && <button onClick={() => onAction('download')}>Download</button>}
    {item && <button className="danger" onClick={() => onAction('delete')}>Delete</button>}
  </div>
}

function MarkdownFileEditor({ tab, highlights, scrollTop, onScroll, onUpdate, onToggleMode, onSave, onSelection, onOpenHighlight }: {
  tab: OpenFileTab
  highlights: Record<string, HighlightRecord>
  scrollTop: number
  onScroll: (scrollTop: number) => void
  onUpdate: (content: string) => void
  onToggleMode: (mode: 'preview' | 'edit') => void
  onSave: () => void
  onSelection: (result: TextSelectionResult) => void
  onOpenHighlight: (highlightId: string) => void
}) {
  const mode = tab.viewMode || 'preview'
  const selectionAnchorRef = useRef<TextSelectionAnchor | null>(null)
  const editorScrollRef = useRef<HTMLTextAreaElement | null>(null)
  const previewScrollRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const target = mode === 'edit' ? editorScrollRef.current : previewScrollRef.current
    if (!target) return
    window.requestAnimationFrame(() => { target.scrollTop = scrollTop || 0 })
  }, [tab.path, tab.loading, mode])
  if (tab.loading) return <div className="pdf-text-status">正在读取 Markdown...</div>
  if (tab.error) return <div className="pdf-text-status error">Markdown 读取失败：{tab.error}</div>
  const content = tab.textContent || ''
  return <section className="markdown-file-panel">
    <div className="markdown-toolbar">
      <div className="markdown-mode-switch">
        <button className={mode === 'preview' ? 'selected' : ''} onClick={() => onToggleMode('preview')}><Eye size={14}/>预览</button>
        <button className={mode === 'edit' ? 'selected' : ''} onClick={() => onToggleMode('edit')}><Edit3 size={14}/>编辑</button>
      </div>
      <button className="secondary" disabled={!tab.dirty} onClick={onSave}>{tab.dirty ? <Save size={14}/> : <Check size={14}/>}保存</button>
    </div>
    {mode === 'edit'
      ? <textarea
          ref={editorScrollRef}
          className="markdown-editor"
          value={content}
          onScroll={event => onScroll(event.currentTarget.scrollTop)}
          onChange={event => onUpdate(event.target.value)}
        />
      : <div ref={previewScrollRef} className="markdown-preview" onScroll={event => onScroll(event.currentTarget.scrollTop)} onMouseDown={event => {
          if (!shouldManageMarkdownSelection(event)) {
            selectionAnchorRef.current = null
            return
          }
          event.preventDefault()
          window.getSelection()?.removeAllRanges()
          selectionAnchorRef.current = makeTextSelectionAnchor(event.currentTarget, event)
        }} onMouseMove={event => {
          updateManagedTextSelection(event.currentTarget, selectionAnchorRef.current, event)
        }} onMouseLeave={() => {
          selectionAnchorRef.current = null
        }} onMouseUp={event => {
          const container = event.currentTarget
          const anchor = selectionAnchorRef.current
          const handleResult = (result: TextSelectionResult | null) => {
            if (!result) return
            const selection = window.getSelection()
            selection?.removeAllRanges()
            selection?.addRange(result.range)
            onSelection(result)
          }
          const result = readTextSelectionWithin(container, anchor, anchor ? event : undefined)
          selectionAnchorRef.current = null
          if (result || anchor) {
            handleResult(result)
            return
          }
          window.setTimeout(() => handleResult(readTextSelectionWithin(container)), 0)
        }}>
          <MarkdownText text={content || '空 Markdown 文件。'} chatId={`markdown:${tab.path}`} highlights={highlights} onOpenHighlight={onOpenHighlight} basePath={tab.path}/>
        </div>}
  </section>
}

function FileWorkspacePanel({ openFiles, activeFile, activeFilePath, pdfHighlights, textHighlights, pdfZoom, scrollPositions, onFileScroll, onZoomOut, onZoomIn, onActivate, onClose, onUpdateMarkdown, onToggleMarkdownMode, onSaveMarkdown, onMarkdownSelection, onPdfSelection, onOpenHighlight }: {
  openFiles: Record<string, OpenFileTab>
  activeFile: OpenFileTab | null
  activeFilePath: string | null
  pdfHighlights: Record<string, PdfHighlightRecord>
  textHighlights: Record<string, HighlightRecord>
  pdfZoom: number
  scrollPositions: Record<string, number>
  onFileScroll: (path: string, scrollTop: number) => void
  onZoomOut: () => void
  onZoomIn: () => void
  onActivate: (path: string) => void
  onClose: (path: string) => void
  onUpdateMarkdown: (path: string, content: string) => void
  onToggleMarkdownMode: (path: string, mode: 'preview' | 'edit') => void
  onSaveMarkdown: (path: string) => void
  onMarkdownSelection: (result: TextSelectionResult, path: string) => void
  onPdfSelection: (text: string, page: number, rect: DOMRect, rects: PdfSelectionRect[]) => void
  onOpenHighlight: (highlightId: string) => void
}) {
  const tabs = Object.values(openFiles)
  return <section className="file-workspace-panel">
    <div className="file-tabs">
      {tabs.map(tab => <button
        key={tab.path}
        className={tab.path === activeFilePath ? 'file-tab active' : 'file-tab'}
        onClick={() => onActivate(tab.path)}
        title={tab.path}
      >
        {tab.type === 'pdf' ? <FileText size={14}/> : <File size={14}/>}
        <span>{tab.name}</span>
        <span className="tab-close" onClick={event => {
          event.stopPropagation()
          onClose(tab.path)
        }}><X size={13}/></span>
      </button>)}
      {activeFile?.type === 'pdf' && <div className="pdf-zoom-controls">
        <button title="缩小 PDF" onClick={onZoomOut}><ZoomOut size={14}/></button>
        <span>{Math.round(pdfZoom * 100)}%</span>
        <button title="放大 PDF" onClick={onZoomIn}><ZoomIn size={14}/></button>
      </div>}
    </div>
    {activeFile ? <div className="file-viewer clean">
      {activeFile.type === 'pdf'
        ? <PdfTextReader
            tab={activeFile}
            highlights={pdfHighlights}
            zoom={pdfZoom}
            scrollTop={scrollPositions[activeFile.path] || 0}
            onScroll={scrollTop => onFileScroll(activeFile.path, scrollTop)}
            onSelection={onPdfSelection}
            onOpenHighlight={onOpenHighlight}
          />
        : activeFile.type === 'markdown'
          ? <MarkdownFileEditor
              tab={activeFile}
              highlights={Object.fromEntries(Object.entries(textHighlights).filter(([, item]) => item.chatId === `markdown:${activeFile.path}`))}
              scrollTop={scrollPositions[activeFile.path] || 0}
              onScroll={scrollTop => onFileScroll(activeFile.path, scrollTop)}
              onUpdate={content => onUpdateMarkdown(activeFile.path, content)}
              onToggleMode={mode => onToggleMarkdownMode(activeFile.path, mode)}
              onSave={() => onSaveMarkdown(activeFile.path)}
              onSelection={result => onMarkdownSelection(result, activeFile.path)}
              onOpenHighlight={onOpenHighlight}
            />
        : <WorkspaceFileFrame
            tab={activeFile}
            scrollTop={scrollPositions[activeFile.path] || 0}
            onScroll={scrollTop => onFileScroll(activeFile.path, scrollTop)}
          />}
    </div> : <div className="file-empty">
      <Folder size={30}/>
      <span>从右侧文件系统打开 PDF 或文件。</span>
    </div>}
  </section>
}

function WorkspaceFileFrame({ tab, scrollTop, onScroll }: {
  tab: OpenFileTab
  scrollTop: number
  onScroll: (scrollTop: number) => void
}) {
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  const restoreAndBind = useCallback(() => {
    cleanupRef.current?.()
    cleanupRef.current = null
    const frame = frameRef.current
    if (!frame) return
    try {
      const win = frame.contentWindow
      if (!win) return
      window.requestAnimationFrame(() => win.scrollTo(0, scrollTop || 0))
      const handleScroll = () => onScroll(win.scrollY || win.document?.documentElement?.scrollTop || win.document?.body?.scrollTop || 0)
      win.addEventListener('scroll', handleScroll, { passive: true })
      cleanupRef.current = () => win.removeEventListener('scroll', handleScroll)
    } catch {
      cleanupRef.current = null
    }
  }, [scrollTop, onScroll])

  useEffect(() => {
    restoreAndBind()
    return () => {
      cleanupRef.current?.()
      cleanupRef.current = null
    }
  }, [tab.path, restoreAndBind])

  return <iframe ref={frameRef} className="pdf-frame" title={tab.name} src={tab.url} onLoad={restoreAndBind}/>
}

function PdfTextReader({ tab, highlights, zoom, scrollTop, onScroll, onSelection, onOpenHighlight }: {
  tab: OpenFileTab
  highlights: Record<string, PdfHighlightRecord>
  zoom: number
  scrollTop: number
  onScroll: (scrollTop: number) => void
  onSelection: (text: string, page: number, rect: DOMRect, rects: PdfSelectionRect[]) => void
  onOpenHighlight: (highlightId: string) => void
}) {
  const readerRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (tab.loading || tab.error || !tab.pdfText?.pages.length) return
    const reader = readerRef.current
    if (!reader) return
    window.requestAnimationFrame(() => { reader.scrollTop = scrollTop || 0 })
  }, [tab.path, tab.loading, tab.error, tab.pdfText?.pages.length, zoom])
  if (tab.loading) {
    return <div className="pdf-text-status">正在抽取 PDF 文本...</div>
  }
  if (tab.error) {
    return <div className="pdf-text-status error">PDF 文本抽取失败：{tab.error}</div>
  }
  if (!tab.pdfText?.pages.length) {
    return <div className="pdf-text-status">没有抽取到可选择文本。该 PDF 可能是扫描件，需要 OCR。</div>
  }
  return <div ref={readerRef} className="pdf-text-reader" onScroll={event => onScroll(event.currentTarget.scrollTop)}>
    {tab.pdfText.pages.map(page => <PdfPageView
      key={page.page}
      pdfPath={tab.path}
      page={page}
      highlights={Object.values(highlights).filter(item => item.path === tab.path && item.page === page.page)}
      zoom={zoom}
      onSelection={onSelection}
      onOpenHighlight={onOpenHighlight}
    />)}
  </div>
}

function PdfPageView({ pdfPath, page, highlights, zoom, onSelection, onOpenHighlight }: {
  pdfPath: string
  page: NonNullable<OpenFileTab['pdfText']>['pages'][number]
  highlights: PdfHighlightRecord[]
  zoom: number
  onSelection: (text: string, page: number, rect: DOMRect, rects: PdfSelectionRect[]) => void
  onOpenHighlight: (highlightId: string) => void
}) {
  const [dragStart, setDragStart] = useState<{ x: number, y: number } | null>(null)
  const [dragEnd, setDragEnd] = useState<{ x: number, y: number } | null>(null)
  const dragStartRef = useRef<{ x: number, y: number } | null>(null)
  const imageWidth = Math.max(1, page.width * zoom)
  const imageHeight = Math.max(1, page.height * zoom)
  const textItems = (page.words && page.words.length ? page.words : page.lines || [])
  const highlightRects = buildPdfHighlightRects(highlights, zoom)
  const dragRect = dragStart && dragEnd ? normalizeDragRect(dragStart, dragEnd) : null
  function pointFromEvent(event: any): { x: number, y: number } {
    const bounds = event.currentTarget.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(imageWidth, event.clientX - bounds.left)),
      y: Math.max(0, Math.min(imageHeight, event.clientY - bounds.top)),
    }
  }
  return <article
    className="pdf-image-page"
    style={{ width: imageWidth, height: imageHeight }}
    onMouseDown={event => {
      if ((event.target as HTMLElement).closest('.pdf-word, .pdf-line')) {
        const highlight = findPdfHighlightForRect({
          x0: Number((event.target as HTMLElement).dataset.x0 || 0),
          y0: Number((event.target as HTMLElement).dataset.y0 || 0),
          x1: Number((event.target as HTMLElement).dataset.x1 || 0),
          y1: Number((event.target as HTMLElement).dataset.y1 || 0),
        }, highlights)
        if (highlight) return
      }
      event.preventDefault()
      const point = pointFromEvent(event)
      dragStartRef.current = point
      setDragStart(point)
      setDragEnd(point)
    }}
    onMouseMove={event => {
      if (!dragStart) return
      event.preventDefault()
      setDragEnd(pointFromEvent(event))
    }}
    onMouseLeave={() => {
      if (!dragStart) return
      dragStartRef.current = null
      setDragStart(null)
      setDragEnd(null)
    }}
    onMouseUp={event => {
      const start = dragStartRef.current || dragStart
      if (!start) return
      event.preventDefault()
      event.stopPropagation()
      const end = pointFromEvent(event)
      const distance = Math.hypot(end.x - start.x, end.y - start.y)
      const selected = collectPdfRangeSelection(textItems, start, end, zoom) ||
        collectPdfDragSelection(textItems, normalizeDragRect(start, end), zoom)
      dragStartRef.current = null
      setDragStart(null)
      setDragEnd(null)
      if (distance < 3) return
      if (!selected.text || selected.text.length < 2 || !selected.rects.length) return
      const rect = selectionMenuRectFromPdfRects(selected.rects, event.currentTarget.getBoundingClientRect(), zoom)
      onSelection(selected.text, page.page, rect, selected.rects)
    }}
  >
    <img className="pdf-page-image" src={workspacePdfPageImageUrl(pdfPath, page.page, zoom)} alt={`Page ${page.page}`}/>
    <div className="pdf-highlight-layer" aria-hidden="true">
      {highlightRects.map(rect => <span
        key={rect.id}
        className="pdf-highlight-rect"
        style={{
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
          background: rect.color.bg,
          borderColor: rect.color.border,
        }}
      />)}
    </div>
    <div className="pdf-word-layer" aria-label={`Page ${page.page} selectable text`}>
      {textItems.map((item, index) => <span
        key={`${page.page}_${index}_${item.x0}_${item.y0}`}
        className={page.words && page.words.length ? 'pdf-word' : 'pdf-line'}
        data-pdf-text={item.text}
        data-page={page.page}
        data-x0={item.x0}
        data-y0={item.y0}
        data-x1={item.x1}
        data-y1={item.y1}
        onClick={(event) => {
          const highlight = findPdfHighlightForRect({
            x0: item.x0,
            y0: item.y0,
            x1: item.x1,
            y1: item.y1,
          }, highlights)
          if (!highlight) return
          event.stopPropagation()
          onOpenHighlight(highlight.id)
        }}
        style={{
          left: item.x0 * zoom - 1,
          top: item.y0 * zoom - 1,
          width: Math.max(1, (item.x1 - item.x0) * zoom + 2),
          height: Math.max(1, (item.y1 - item.y0) * zoom + 2),
          fontSize: Math.max(6, ((item as any).font_size || (item.y1 - item.y0)) * zoom * 0.86),
        }}
      >{item.text}</span>)}
    </div>
    {dragRect && <div
      className="pdf-drag-selection"
      style={{ left: dragRect.left, top: dragRect.top, width: dragRect.width, height: dragRect.height }}
    />}
    <div className="pdf-page-label">Page {page.page}</div>
  </article>
}

function buildPdfHighlightRects(
  highlights: PdfHighlightRecord[],
  zoom: number,
) {
  const rects: Array<{ id: string, left: number, top: number, width: number, height: number, color: BranchColor }> = []
  for (const highlight of highlights) {
    highlight.rects.forEach((item, index) => {
      rects.push({
        id: `${highlight.id}_${index}`,
        left: item.x0 * zoom,
        top: item.y0 * zoom,
        width: Math.max(1, (item.x1 - item.x0) * zoom),
        height: Math.max(1, (item.y1 - item.y0) * zoom),
        color: highlight.color,
      })
    })
  }
  return rects
}

function collectPdfDragSelection(
  items: Array<{ x0: number, y0: number, x1: number, y1: number, text: string }>,
  rect: { left: number, top: number, width: number, height: number },
  zoom: number,
): { text: string, rects: PdfSelectionRect[] } {
  const pdfRect = {
    x0: rect.left / zoom,
    y0: rect.top / zoom,
    x1: (rect.left + rect.width) / zoom,
    y1: (rect.top + rect.height) / zoom,
  }
  const selected = items.filter(item => pdfRectsIntersect(item, pdfRect, 0))
  const ordered = selected.sort((a, b) => {
    const ay = Number(a.y0 || 0)
    const by = Number(b.y0 || 0)
    if (Math.abs(ay - by) > 3) return ay - by
    return Number(a.x0 || 0) - Number(b.x0 || 0)
  })
  const words = ordered.map(item => item.text).filter(Boolean)
  const rects = ordered.map(item => ({
    x0: item.x0,
    y0: item.y0,
    x1: item.x1,
    y1: item.y1,
  }))
  return { text: words.join(' ').replace(/\s+/g, ' ').trim(), rects }
}

function collectPdfRangeSelection(
  items: Array<{ x0: number, y0: number, x1: number, y1: number, text: string }>,
  start: { x: number, y: number },
  end: { x: number, y: number },
  zoom: number,
): { text: string, rects: PdfSelectionRect[] } | null {
  const ordered = orderPdfTextItems(items)
  if (!ordered.length) return null
  const startIndex = closestPdfTextIndex(ordered, { x: start.x / zoom, y: start.y / zoom })
  const endIndex = closestPdfTextIndex(ordered, { x: end.x / zoom, y: end.y / zoom })
  if (startIndex < 0 || endIndex < 0) return null
  const from = Math.min(startIndex, endIndex)
  const to = Math.max(startIndex, endIndex)
  const selected = ordered.slice(from, to + 1).filter(item => item.text)
  if (!selected.length) return null
  return {
    text: selected.map(item => item.text).join(' ').replace(/\s+/g, ' ').trim(),
    rects: selected.map(item => ({
      x0: item.x0,
      y0: item.y0,
      x1: item.x1,
      y1: item.y1,
    })),
  }
}

function orderPdfTextItems<T extends { x0: number, y0: number, x1: number, y1: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    const ay = Number(a.y0 || 0)
    const by = Number(b.y0 || 0)
    const lineTolerance = Math.max(3, Math.min(a.y1 - a.y0, b.y1 - b.y0) * 0.65)
    if (Math.abs(ay - by) > lineTolerance) return ay - by
    return Number(a.x0 || 0) - Number(b.x0 || 0)
  })
}

function closestPdfTextIndex(
  items: Array<{ x0: number, y0: number, x1: number, y1: number }>,
  point: { x: number, y: number },
): number {
  let bestIndex = -1
  let bestScore = Infinity
  items.forEach((item, index) => {
    const insideX = point.x >= item.x0 && point.x <= item.x1
    const insideY = point.y >= item.y0 && point.y <= item.y1
    const dx = insideX ? 0 : Math.min(Math.abs(point.x - item.x0), Math.abs(point.x - item.x1))
    const dy = insideY ? 0 : Math.min(Math.abs(point.y - item.y0), Math.abs(point.y - item.y1))
    const score = dx * dx + dy * dy * 9
    if (score < bestScore) {
      bestScore = score
      bestIndex = index
    }
  })
  return bestIndex
}

function selectionMenuRectFromPdfRects(rects: PdfSelectionRect[], pageRect: DOMRect, zoom: number): DOMRect {
  const left = Math.min(...rects.map(item => item.x0)) * zoom + pageRect.left
  const top = Math.min(...rects.map(item => item.y0)) * zoom + pageRect.top
  const right = Math.max(...rects.map(item => item.x1)) * zoom + pageRect.left
  const bottom = Math.max(...rects.map(item => item.y1)) * zoom + pageRect.top
  return new DOMRect(left, top, Math.max(1, right - left), Math.max(1, bottom - top))
}

function normalizeDragRect(a: { x: number, y: number }, b: { x: number, y: number }) {
  const left = Math.min(a.x, b.x)
  const top = Math.min(a.y, b.y)
  return {
    left,
    top,
    width: Math.abs(a.x - b.x),
    height: Math.abs(a.y - b.y),
  }
}

function rectsOverlap(a: PdfSelectionRect, b: PdfSelectionRect, minRatio: number): boolean {
  const left = Math.max(a.x0, b.x0)
  const right = Math.min(a.x1, b.x1)
  const top = Math.max(a.y0, b.y0)
  const bottom = Math.min(a.y1, b.y1)
  const width = Math.max(0, right - left)
  const height = Math.max(0, bottom - top)
  if (!width || !height) return false
  const area = width * height
  const base = Math.max(1, (a.x1 - a.x0) * (a.y1 - a.y0))
  return area / base >= minRatio
}

function findPdfHighlightForRect(
  rect: PdfSelectionRect,
  highlights: PdfHighlightRecord[],
): PdfHighlightRecord | null {
  return highlights.find(highlight => highlight.rects.some(item => pdfRectsIntersect(rect, item, 2))) || null
}

function pdfRectsIntersect(a: PdfSelectionRect, b: PdfSelectionRect, tolerance = 0): boolean {
  return !(
    a.x1 < b.x0 - tolerance ||
    a.x0 > b.x1 + tolerance ||
    a.y1 < b.y0 - tolerance ||
    a.y0 > b.y1 + tolerance
  )
}

function shouldRaiseFromMouseDown(event: any): boolean {
  const target = event.target as HTMLElement
  return !target.closest('button, textarea, input, .message-content, .markdown-body, .pdf-word-layer, .pdf-text-reader')
}

function readTextSelectionWithin(container: HTMLElement, anchor?: TextSelectionAnchor | null, event?: any): TextSelectionResult | null {
  const anchoredRange = rangeFromAnchorToEvent(container, anchor || null, event)
  if (anchoredRange) {
    const result = textSelectionResultFromRange(container, anchoredRange)
    if (result) return result
  }
  if (event) return null
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null
  const range = selection.getRangeAt(0).cloneRange()
  return textSelectionResultFromRange(container, range)
}

function textSelectionResultFromRange(container: HTMLElement, range: Range): TextSelectionResult | null {
  if (!rangeInsideContainer(container, range)) return null
  const extracted = selectableTextFromRange(container, range)
  const rawText = extracted?.text || range.toString()
  const leadingTrim = extracted?.leadingTrim ?? (rawText.length - rawText.trimStart().length)
  const text = rawText.trim()
  if (text.length < 2) return null
  const rect = range.getBoundingClientRect()
  if (!rect || (rect.width === 0 && rect.height === 0)) return null
  return { text, rect, range, ...locateSelectionInContainer(container, range, text, leadingTrim) }
}

function makeTextSelectionAnchor(container: HTMLElement, event: any): TextSelectionAnchor | null {
  const target = event.target as HTMLElement
  if (target.closest(markdownNativeSelectionSelector())) return null
  return textSelectionPointFromEvent(container, event)
}

function shouldManageMarkdownSelection(event: any): boolean {
  const target = event.target as HTMLElement
  return !target.closest(markdownNativeSelectionSelector())
}

function markdownNativeSelectionSelector(): string {
  return 'button, textarea, input, select, [data-highlight-id], .message-actions, .message-branch-menu, pre, code, kbd, samp'
}

function updateManagedTextSelection(container: HTMLElement, anchor: TextSelectionAnchor | null, event: any): void {
  const range = rangeFromAnchorToEvent(container, anchor, event)
  if (!range) return
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
}

function rangeFromAnchorToEvent(container: HTMLElement, anchor: TextSelectionAnchor | null, event: any): Range | null {
  if (!anchor) return null
  const end = textSelectionPointFromEvent(container, event, anchor)
  if (!end) return null
  return rangeFromTextSelectionPoints(container, anchor, end)
}

function textSelectionPointFromEvent(container: HTMLElement, event: any, anchor?: TextSelectionAnchor | null): TextSelectionPoint | null {
  const target = event.target as HTMLElement
  const token = target.closest<HTMLElement>('[data-md-token]')
  if (token && container.contains(token)) return textSelectionPointFromToken(token, event.clientX, event.clientY)
  const mathBoundary = target.closest<HTMLElement>('.katex, .math-inline, .math-block')
  if (mathBoundary && container.contains(mathBoundary)) {
    const mathPoint = textSelectionPointAroundMath(container, mathBoundary, anchor || null)
    if (mathPoint) return mathPoint
  }
  const nearest = nearestMarkdownToken(container, event.clientX, event.clientY)
  return nearest ? textSelectionPointFromToken(nearest, event.clientX, event.clientY) : null
}

function textSelectionPointFromToken(token: HTMLElement, x: number, y: number): TextSelectionPoint | null {
  if (token.dataset.mdSelectable !== '1') return null
  const textNode = firstTextNode(token)
  if (!textNode) return null
  const tokenIndex = Number(token.dataset.mdToken || -1)
  if (!Number.isFinite(tokenIndex) || tokenIndex < 0) return null
  return {
    tokenIndex,
    offset: token.dataset.mdMath === '1'
      ? offsetFromPointInMathToken(token, textNode, x)
      : offsetFromPointInTextNode(textNode, x, y),
  }
}

function textSelectionPointAroundMath(container: HTMLElement, mathElement: HTMLElement, anchor: TextSelectionAnchor | null): TextSelectionPoint | null {
  const mathRect = mathElement.getBoundingClientRect()
  const tokens = markdownTokens(container).filter(token => token.dataset.mdSelectable === '1')
  const before: HTMLElement[] = []
  const after: HTMLElement[] = []
  for (const token of tokens) {
    const rect = firstUsableRect(token)
    if (!rect) continue
    const sameLine = rect.bottom >= mathRect.top - 10 && rect.top <= mathRect.bottom + 10
    if (!sameLine) continue
    const tokenIndex = Number(token.dataset.mdToken || -1)
    if (!Number.isFinite(tokenIndex) || tokenIndex < 0) continue
    if (rect.right <= mathRect.left + 2) before.push(token)
    if (rect.left >= mathRect.right - 2) after.push(token)
  }
  before.sort((a, b) => Number(a.dataset.mdToken || 0) - Number(b.dataset.mdToken || 0))
  after.sort((a, b) => Number(a.dataset.mdToken || 0) - Number(b.dataset.mdToken || 0))
  const previous = before[before.length - 1]
  const next = after[0]
  const anchorIndex = anchor?.tokenIndex ?? -1
  if (next && (anchorIndex < 0 || anchorIndex <= Number(next.dataset.mdToken || 0))) {
    return { tokenIndex: Number(next.dataset.mdToken || 0), offset: 0 }
  }
  if (previous) {
    const textNode = firstTextNode(previous)
    return { tokenIndex: Number(previous.dataset.mdToken || 0), offset: textNode?.textContent?.length || 0 }
  }
  if (next) return { tokenIndex: Number(next.dataset.mdToken || 0), offset: 0 }
  const nearest = nearestMarkdownToken(container, mathRect.left + mathRect.width / 2, mathRect.top + mathRect.height / 2, 220)
  return nearest ? textSelectionPointFromToken(nearest, mathRect.left + mathRect.width / 2, mathRect.top + mathRect.height / 2) : null
}

function rangeFromTextSelectionPoints(container: HTMLElement, a: TextSelectionPoint, b: TextSelectionPoint): Range | null {
  const tokens = markdownTokens(container)
  const startPoint = compareTextSelectionPoints(a, b) <= 0 ? a : b
  const endPoint = compareTextSelectionPoints(a, b) <= 0 ? b : a
  const startToken = tokens[startPoint.tokenIndex]
  const endToken = tokens[endPoint.tokenIndex]
  const startText = startToken ? firstTextNode(startToken) : null
  const endText = endToken ? firstTextNode(endToken) : null
  if (!startText || !endText) return null
  const range = document.createRange()
  range.setStart(startText, clampOffset(startPoint.offset, startText))
  range.setEnd(endText, clampOffset(endPoint.offset, endText))
  if (!rangeInsideContainer(container, range)) return null
  return range
}

function compareTextSelectionPoints(a: TextSelectionPoint, b: TextSelectionPoint): number {
  if (a.tokenIndex !== b.tokenIndex) return a.tokenIndex - b.tokenIndex
  return a.offset - b.offset
}

function markdownTokens(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>('[data-md-token]'))
}

function nearestMarkdownToken(container: HTMLElement, x: number, y: number, maxScore = 72): HTMLElement | null {
  let best: HTMLElement | null = null
  let bestScore = Infinity
  for (const token of markdownTokens(container)) {
    if (token.dataset.mdSelectable !== '1') continue
    const rects = Array.from(token.getClientRects())
    for (const rect of rects) {
      if (!rect.width && !rect.height) continue
      const insideY = y >= rect.top - 4 && y <= rect.bottom + 4
      const dx = x < rect.left ? rect.left - x : x > rect.right ? x - rect.right : 0
      const dy = insideY ? 0 : Math.min(Math.abs(y - rect.top), Math.abs(y - rect.bottom))
      const score = dx + dy * 8
      if (score < bestScore) {
        bestScore = score
        best = token
      }
    }
  }
  return bestScore <= maxScore ? best : null
}

function firstUsableRect(element: HTMLElement): DOMRect | null {
  return Array.from(element.getClientRects()).find(rect => rect.width || rect.height) || null
}

function firstTextNode(element: HTMLElement): Text | null {
  const mathSource = element.dataset.mdMath === '1'
    ? element.querySelector<HTMLElement>('[data-md-math-source]')
    : null
  if (mathSource) {
    const mathWalker = document.createTreeWalker(mathSource, NodeFilter.SHOW_TEXT)
    const mathText = mathWalker.nextNode() as Text | null
    if (mathText) return mathText
  }
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  return walker.nextNode() as Text | null
}

function offsetFromPointInMathToken(token: HTMLElement, node: Text, x: number): number {
  const textLength = (node.textContent || '').length
  if (!textLength) return 0
  const rect = token.getBoundingClientRect()
  if (!rect || (!rect.width && !rect.height)) return textLength
  return x <= rect.left + rect.width / 2 ? 0 : textLength
}

function offsetFromPointInTextNode(node: Text, x: number, y: number): number {
  const text = node.textContent || ''
  if (!text) return 0
  const range = document.createRange()
  let bestOffset = 0
  let bestDistance = Infinity
  for (let offset = 0; offset <= text.length; offset += 1) {
    range.setStart(node, Math.max(0, Math.min(offset, text.length)))
    range.setEnd(node, Math.max(0, Math.min(offset + 1, text.length)))
    const rect = range.getBoundingClientRect()
    if (!rect || (rect.width === 0 && rect.height === 0)) continue
    const center = offset >= text.length ? rect.right : rect.left + rect.width / 2
    const middleY = rect.top + rect.height / 2
    const distance = Math.abs(x - center) + Math.abs(y - middleY) * 6
    if (distance < bestDistance) {
      bestDistance = distance
      bestOffset = offset
    }
  }
  return Math.max(0, Math.min(bestOffset, text.length))
}

function clampOffset(offset: number, node: Text): number {
  return Math.max(0, Math.min(offset, (node.textContent || '').length))
}

function rangeInsideContainer(container: HTMLElement, range: Range): boolean {
  return nodeInsideContainer(container, range.startContainer) && nodeInsideContainer(container, range.endContainer)
}

function nodeInsideContainer(container: HTMLElement, node: Node | null): boolean {
  if (!node) return false
  const element = node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement
  return !!element && container.contains(element)
}

function FloatingWindows({
  windows,
  sessions,
  eventsByWindow,
  inputSeeds,
  inputResetTokens,
  sendingByWindow,
  collapsedMessages,
  highlights,
  onInputDraftChange,
  onToggleCollapse,
  onSend,
  onStop,
  onMinimize,
  onMaximize,
  onClose,
  onRaise,
  onMove,
  onResize,
  onOpenSessionWindow,
  onTextSelection,
  onOpenHighlight,
  messageBranchMenus,
  onToggleMessageBranchMenu,
  onForkMessage,
  onSetbackMessage,
  onToggleTools,
}: {
  windows: Record<string, FloatingWindowState>
  sessions: Record<string, LearningSessionState>
  eventsByWindow: Record<string, ContextEvent[]>
  inputSeeds: Record<string, string>
  inputResetTokens: Record<string, number>
  sendingByWindow: Record<string, boolean>
  collapsedMessages: Record<string, boolean>
  highlights: Record<string, HighlightRecord>
  onInputDraftChange: (windowId: string, value: string) => void
  onToggleCollapse: (messageId: string) => void
  onSend: (windowId: string, text?: string) => void
  onStop: (windowId: string) => void
  onMinimize: (windowId: string) => void
  onMaximize: (windowId: string) => void
  onClose: (windowId: string) => void
  onRaise: (windowId: string) => void
  onMove: (windowId: string, x: number, y: number) => void
  onResize: (windowId: string, width: number, height: number) => void
  onOpenSessionWindow: (session: LearningSessionState, parentWindowId: string) => void
  onTextSelection: (sourceSessionId: string, chatId: string, container: HTMLElement, anchor: TextSelectionAnchor | null, event: any) => void
  onOpenHighlight: (highlightId: string) => void
  messageBranchMenus: Record<string, boolean>
  onToggleMessageBranchMenu: (key: string) => void
  onForkMessage: (sessionId: string, messageIndex?: number, windowId?: string) => void
  onSetbackMessage: (sessionId: string, messageIndex?: number, windowId?: string) => void
  onToggleTools: (sessionId: string, enabled: boolean) => void
}) {
  return <>
    <div className="window-dock">
      {Object.values(windows).filter(win => win.minimized).map(win => {
        const session = sessions[win.sessionId]
        return <button
          key={win.id}
          className="dock-item"
          style={colorVars(win.color || ROOT_BRANCH_COLOR)}
          onClick={() => onRaise(win.id)}
          title={win.title || session?.title || '子问题'}
        >
          <ListTree size={13}/>
          <span>{win.title || session?.title || '子问题'}</span>
        </button>
      })}
    </div>
    {Object.values(windows).filter(win => !win.minimized).map(win => {
      const session = sessions[win.sessionId]
      const sending = !!sendingByWindow[win.id]
      const busy = sending || !!session?.running
      const chatItems = buildChatItems(eventsByWindow[win.id] || [])
      const style = win.fullscreen
        ? { zIndex: win.zIndex }
        : { left: win.x, top: win.y, width: win.width, height: win.height, zIndex: win.zIndex }
      return <section
        key={win.id}
        className={win.fullscreen ? 'floating-window fullscreen' : 'floating-window'}
        style={{ ...style, ...colorVars(win.color || ROOT_BRANCH_COLOR) }}
        onMouseDown={event => {
          if (shouldRaiseFromMouseDown(event)) onRaise(win.id)
        }}
      >
        <header className="window-titlebar" onMouseDown={event => {
          if (win.fullscreen) return
          const target = event.target as HTMLElement
          if (target.closest('button')) return
          event.preventDefault()
          onRaise(win.id)
          const windowElement = target.closest<HTMLElement>('.floating-window')
          const startX = event.clientX
          const startY = event.clientY
          const originX = win.x
          const originY = win.y
          let frame = 0
          let latestX = originX
          let latestY = originY
          const handleMove = (moveEvent: MouseEvent) => {
            latestX = originX + moveEvent.clientX - startX
            latestY = originY + moveEvent.clientY - startY
            if (frame) return
            frame = window.requestAnimationFrame(() => {
              frame = 0
              if (windowElement) {
                windowElement.style.transform = `translate3d(${latestX - originX}px, ${latestY - originY}px, 0)`
              }
            })
          }
          const handleUp = () => {
            if (frame) window.cancelAnimationFrame(frame)
            if (windowElement) windowElement.style.transform = ''
            onMove(win.id, latestX, latestY)
            window.removeEventListener('mousemove', handleMove)
            window.removeEventListener('mouseup', handleUp)
          }
          window.addEventListener('mousemove', handleMove)
          window.addEventListener('mouseup', handleUp)
        }}>
          <div className="window-title"><ListTree size={15}/><span>{win.title || session?.title || '子问题'}</span></div>
          <div className="window-controls">
            <button title="缩小" onClick={() => onMinimize(win.id)}><Minimize2 size={15}/></button>
            <button title="全屏" onClick={() => onMaximize(win.id)}><Maximize2 size={15}/></button>
            <button className="delete-window" title="删除并删除子树" onClick={() => onClose(win.id)}><Trash2 size={14}/>删除</button>
          </div>
        </header>
        <div className="window-body">
          {!session?.running && !visibleWindowItems(chatItems, win).length && <div className="window-empty">子窗口上下文已独立创建。</div>}
          {visibleWindowItems(chatItems, win).map(item => <article key={item.id} className={`window-bubble ${item.role}`}>
            <div className="bubble-head">
              <span>{roleLabel(item.role)}</span>
              {item.role === 'user' && <UserMessageActions
                sessionId={win.sessionId}
                item={item}
                sessions={sessions}
                menuKey={`${win.id}:${item.id}`}
                openMenus={messageBranchMenus}
                onToggleMenu={onToggleMessageBranchMenu}
                onFork={(sessionId, messageIndex) => onForkMessage(sessionId, messageIndex, win.id)}
                onSetback={(sessionId, messageIndex) => onSetbackMessage(sessionId, messageIndex, win.id)}
                onOpenBranch={(child) => onOpenSessionWindow(child, win.id)}
              />}
            </div>
            <MessageContent
              sessionId={win.sessionId}
              item={item}
              collapsed={!!collapsedMessages[messageCollapseKey(win.sessionId, item.id)]}
              highlights={highlights}
              onToggleCollapse={() => onToggleCollapse(messageCollapseKey(win.sessionId, item.id))}
              onTextSelection={(container, anchor, event) => onTextSelection(win.sessionId, item.id, container, anchor, event)}
              onOpenHighlight={onOpenHighlight}
            />
          </article>)}
          {busy && <ThinkingState events={eventsByWindow[win.id] || []} running={busy} todoBoard={session?.todo_board}/>}
        </div>
        <footer className="window-composer">
          <ComposerInput
            value={inputSeeds[win.id] || ''}
            onDraftChange={value => onInputDraftChange(win.id, value)}
            resetSignal={inputResetTokens[win.id] || 0}
            disabled={busy}
            onSubmit={value => onSend(win.id, value)}
          />
          <button className="primary" onClick={() => onSend(win.id)} disabled={busy}><Send size={16}/>{sending ? '发送中' : '发送'}</button>
          <div className="composer-side-actions">
            <button className="secondary icon-only" onClick={() => onStop(win.id)} disabled={!busy}><Square size={16}/></button>
            <ToolsToggleButton
              enabled={session?.tools_enabled !== false}
              disabled={!session || busy}
              onToggle={(enabled) => onToggleTools(win.sessionId, enabled)}
            />
          </div>
        </footer>
        {!win.fullscreen && <button className="window-resize-handle" title="拖动调整窗口大小" onMouseDown={event => {
          event.preventDefault()
          event.stopPropagation()
          onRaise(win.id)
          const windowElement = (event.currentTarget as HTMLElement).closest<HTMLElement>('.floating-window')
          const startX = event.clientX
          const startY = event.clientY
          const startWidth = win.width
          const startHeight = win.height
          let frame = 0
          let latestWidth = startWidth
          let latestHeight = startHeight
          const handleMove = (moveEvent: MouseEvent) => {
            latestWidth = startWidth + moveEvent.clientX - startX
            latestHeight = startHeight + moveEvent.clientY - startY
            if (frame) return
            frame = window.requestAnimationFrame(() => {
              frame = 0
              if (windowElement) {
                windowElement.style.width = `${Math.max(360, latestWidth)}px`
                windowElement.style.height = `${Math.max(330, latestHeight)}px`
              }
            })
          }
          const handleUp = () => {
            if (frame) window.cancelAnimationFrame(frame)
            if (windowElement) {
              windowElement.style.width = ''
              windowElement.style.height = ''
            }
            onResize(win.id, latestWidth, latestHeight)
            window.removeEventListener('mousemove', handleMove)
            window.removeEventListener('mouseup', handleUp)
          }
          window.addEventListener('mousemove', handleMove)
          window.addEventListener('mouseup', handleUp)
        }}/>}
      </section>
    })}
  </>
}

function copyTextToClipboard(text: string): void {
  const value = String(text || '')
  if (!value) return
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(value).catch(() => fallbackCopyText(value))
    return
  }
  fallbackCopyText(value)
}

function fallbackCopyText(text: string): void {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  try { document.execCommand('copy') } catch { /* ignore */ }
  textarea.remove()
}

function renderContent(value: any): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (value?.payload_ref) {
    const ref = value.payload_ref
    return ref.preview || `[内容较长，点击载入完整回答：${ref.size ?? '?'} chars]`
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const MessageContent = memo(function MessageContent({ sessionId, item, collapsed, highlights, onToggleCollapse, onTextSelection, onOpenHighlight }: {
  sessionId: string
  item: ChatItem
  collapsed: boolean
  highlights: Record<string, HighlightRecord>
  onToggleCollapse: () => void
  onTextSelection: (container: HTMLElement, anchor: TextSelectionAnchor | null, event: any) => void
  onOpenHighlight: (highlightId: string) => void
}) {
  const [fullText, setFullText] = useState<string | null>(null)
  const selectionAnchorRef = useRef<TextSelectionAnchor | null>(null)
  const ref = item.content?.payload_ref

  useEffect(() => {
    let cancelled = false
    async function load() {
      if (!ref?.id || !sessionId) return
      try {
        const text = await fetchLearningPayload(sessionId, ref.id)
        if (!cancelled) setFullText(text)
      } catch {
        if (!cancelled) setFullText(renderContent(item.content))
      }
    }
    load()
    return () => { cancelled = true }
  }, [sessionId, ref?.id])

  const text = fullText ?? renderContent(item.content)
  const lines = text.split('\n')
  const canCollapse = lines.length > 10
  const visibleText = collapsed && canCollapse ? lines.slice(0, 10).join('\n') : text
  return <div
    className={collapsed && canCollapse ? 'message-content collapsed' : 'message-content'}
    data-chat-id={item.id}
    onMouseDown={event => {
      if (!shouldManageMarkdownSelection(event)) {
        selectionAnchorRef.current = null
        return
      }
      event.preventDefault()
      window.getSelection()?.removeAllRanges()
      selectionAnchorRef.current = makeTextSelectionAnchor(event.currentTarget, event)
    }}
    onMouseMove={event => {
      updateManagedTextSelection(event.currentTarget, selectionAnchorRef.current, event)
    }}
    onMouseLeave={() => {
      selectionAnchorRef.current = null
    }}
    onMouseUp={event => {
      const anchor = selectionAnchorRef.current
      onTextSelection(event.currentTarget, anchor, anchor ? event : undefined)
      selectionAnchorRef.current = null
    }}
  >
    {(item.role === 'user' || item.role === 'assistant') && <button
      className="message-copy-button"
      title="复制这条消息"
      onClick={event => {
        event.stopPropagation()
        copyTextToClipboard(text)
      }}
    ><Copy size={13}/></button>}
    {canCollapse && <button className="collapse-toggle" onClick={onToggleCollapse}>{collapsed ? `展开全部 ${lines.length} 行` : '折叠到 10 行'}</button>}
    <MarkdownText text={visibleText} chatId={item.id} sourceSessionId={sessionId} highlights={highlights} onOpenHighlight={onOpenHighlight}/>
  </div>
})

const MarkdownText = memo(function MarkdownText({ text, chatId, sourceSessionId, highlights, onOpenHighlight, basePath = '' }: {
  text: string
  chatId: string
  sourceSessionId?: string
  highlights: Record<string, HighlightRecord>
  onOpenHighlight: (highlightId: string) => void
  basePath?: string
}) {
  const html = useMemo(() => renderMarkdownToHtml(text || '', { basePath, chatId, sourceSessionId, highlights }), [text, basePath, chatId, sourceSessionId, highlights])
  return <div
    className="markdown-body"
    onMouseDown={event => {
      const target = event.target as HTMLElement
      if (target.closest('[data-highlight-id]')) event.stopPropagation()
    }}
    onMouseUp={event => {
      const target = event.target as HTMLElement
      if (target.closest('[data-highlight-id]')) event.stopPropagation()
    }}
    onClick={event => {
      const target = event.target as HTMLElement
      const highlight = target.closest<HTMLElement>('[data-highlight-id]')
      if (highlight?.dataset.highlightId) {
        event.preventDefault()
        event.stopPropagation()
        onOpenHighlight(highlight.dataset.highlightId)
      }
    }}
    dangerouslySetInnerHTML={{ __html: html }}
  />
})

type MathRenderPlaceholder = {
  html: string
  source: string
  displayMode: boolean
}

function renderMarkdownToHtml(text: string, options: {
  basePath?: string
  chatId: string
  sourceSessionId?: string
  highlights: Record<string, HighlightRecord>
}): string {
  const mathItems: MathRenderPlaceholder[] = []
  const withMath = text
    .replace(/\\\[\s*([\s\S]*?)\s*\\\]/g, (_, body) => stashMath(mathItems, body, true))
    .replace(/\$\$\s*([\s\S]*?)\s*\$\$/g, (_, body) => stashMath(mathItems, body, true))
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, body) => stashMath(mathItems, body, false))
    .replace(/\$([^$\n]+)\$/g, (_, body) => stashMath(mathItems, body, false))
  let html = markdownRenderer.render(withMath)
  mathItems.forEach((item, index) => {
    html = html.split(`@@RAGENT_MATH_${index}@@`).join(renderMathPlaceholder(item))
  })
  html = rewriteMarkdownAssetUrls(html, options.basePath || '')
  html = applyMarkdownHighlights(html, options.chatId, options.sourceSessionId, options.highlights)
  html = wrapMarkdownTextTokens(html)
  return html
}

function locateSelectionInContainer(container: HTMLElement, range: Range, selectedText: string, trimOffset = 0): { textOffset?: number, occurrence?: number } {
  const textNodes = selectableTextNodes(container)
  const selected = String(selectedText || '').trim()
  let offset = 0
  for (const node of textNodes) {
    if (node === range.startContainer) {
      const startOffset = Math.max(0, Math.min(range.startOffset, node.textContent?.length || 0))
      const textOffset = offset + startOffset + trimOffset
      return { textOffset, occurrence: occurrenceBeforeOffset(textNodes.map(item => item.textContent || '').join(''), selected, textOffset) }
    }
    offset += node.textContent?.length || 0
  }
  return {}
}

function stashMath(items: MathRenderPlaceholder[], tex: string, displayMode: boolean): string {
  const index = items.length
  const source = String(tex || '').trim()
  let html = ''
  try {
    html = renderToString(source, {
      displayMode,
      throwOnError: false,
      strict: 'ignore',
      trust: false,
    })
  } catch {
    html = markdownRenderer.utils.escapeHtml(source)
  }
  items.push({ html, source, displayMode })
  return `@@RAGENT_MATH_${index}@@`
}

function renderMathPlaceholder(item: MathRenderPlaceholder): string {
  const className = item.displayMode ? 'math-block' : 'math-inline'
  const tag = item.displayMode ? 'div' : 'span'
  const escapedSource = markdownRenderer.utils.escapeHtml(item.source)
  const source = `<span class="md-math-source" data-md-math-source="1" aria-hidden="true">${escapedSource}</span>`
  return `<${tag} class="${className}" data-md-math="1">${item.html}${source}</${tag}>`
}

function rewriteMarkdownAssetUrls(html: string, basePath: string): string {
  if (!basePath) return html
  return html.replace(/\s(src|href)="([^"]+)"/g, (match, attr, rawUrl) => {
    const url = String(rawUrl || '')
    if (/^(https?:|data:|blob:|mailto:|#)/i.test(url)) return match
    return ` ${attr}="${markdownRenderer.utils.escapeHtml(resolveWorkspaceRelativeUrl(basePath, url))}"`
  })
}

function resolveWorkspaceRelativeUrl(basePath: string, url: string): string {
  const cleanUrl = url.replace(/^\.\//, '')
  const baseParts = String(basePath || '').split('/').filter(Boolean)
  baseParts.pop()
  for (const part of cleanUrl.split('/')) {
    if (!part || part === '.') continue
    if (part === '..') {
      baseParts.pop()
    } else {
      baseParts.push(part)
    }
  }
  return workspaceOpenUrl(baseParts.join('/'))
}

function wrapMarkdownTextTokens(html: string): string {
  const parser = new DOMParser()
  const doc = parser.parseFromString(`<div>${html}</div>`, 'text/html')
  const root = doc.body.firstElementChild as HTMLElement | null
  if (!root) return html
  let index = 0
  const skipTags = new Set(['SCRIPT', 'STYLE', 'CODE', 'PRE', 'KBD', 'SAMP', 'IMG', 'SVG', 'MATH'])
  function visit(node: Node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const element = node as HTMLElement
      if (skipTags.has(element.tagName)) return
      if (element.dataset.mdMath === '1') {
        index = makeMathToken(doc, element, index)
        return
      }
      if (element.classList.contains('katex')) return
      Array.from(element.childNodes).forEach(visit)
      return
    }
    if (node.nodeType !== Node.TEXT_NODE) return
    const value = node.textContent || ''
    if (!value.trim()) return
    const parent = node.parentElement
    if (!parent || parent.closest('pre, code, .katex, .selection-highlight, [data-md-math]')) return
    const fragment = doc.createDocumentFragment()
    const parts = value.match(/\S+|\s+/g) || [value]
    for (const part of parts) {
      if (!part) continue
      const span = doc.createElement('span')
      span.className = 'md-token'
      span.dataset.mdToken = String(index++)
      span.dataset.mdSelectable = part.trim() ? '1' : '0'
      span.textContent = part
      fragment.appendChild(span)
    }
    parent.replaceChild(fragment, node)
  }
  Array.from(root.childNodes).forEach(visit)
  return root.innerHTML
}

function makeMathToken(doc: Document, element: HTMLElement, index: number): number {
  element.classList.add('md-token', 'md-math-token')
  element.dataset.mdToken = String(index)
  element.dataset.mdSelectable = '1'
  const source = element.querySelector<HTMLElement>('[data-md-math-source]')
  if (!source) {
    const fallback = doc.createElement('span')
    fallback.className = 'md-math-source'
    fallback.dataset.mdMathSource = '1'
    fallback.setAttribute('aria-hidden', 'true')
    fallback.textContent = element.textContent || ''
    element.appendChild(fallback)
  }
  return index + 1
}

type ResolvedHighlightRange = {
  item: HighlightRecord
  start: number
  end: number
  order: number
}

function applyMarkdownHighlights(html: string, chatId: string, sourceSessionId: string | undefined, highlights: Record<string, HighlightRecord>): string {
  const items = Object.values(highlights).filter(entry =>
    entry.chatId === chatId &&
    (!sourceSessionId || !entry.sourceSessionId || entry.sourceSessionId === sourceSessionId) &&
    String(entry.text || '').trim()
  )
  if (!items.length) return html
  const parser = new DOMParser()
  const doc = parser.parseFromString(`<div>${html}</div>`, 'text/html')
  const root = doc.body.firstElementChild as HTMLElement | null
  if (!root) return html
  applyMathOnlyHighlights(doc, root, items)
  const textNodes = selectableTextNodes(root)
  const fullText = textNodes.map(node => node.textContent || '').join('')
  const ranges = items
    .map((item, order) => {
      const range = resolveHighlightRange(fullText, item)
      return range ? { ...range, item, order } : null
    })
    .filter((range): range is ResolvedHighlightRange => !!range)
  applyHighlightRangesToTextNodes(doc, textNodes, ranges)
  return root.innerHTML
}


function applyMathOnlyHighlights(doc: Document, root: HTMLElement, items: HighlightRecord[]): void {
  const mathElements = Array.from(root.querySelectorAll<HTMLElement>('[data-md-math]'))
  if (!mathElements.length) return
  for (const element of mathElements) {
    if (element.closest('[data-highlight-id]')) continue
    const source = element.querySelector<HTMLElement>('[data-md-math-source]')?.textContent || ''
    if (!source) continue
    const match = items.find(item => normalizeComparableText(item.text) === normalizeComparableText(source))
    if (!match) continue
    highlightMathElement(doc, element, match)
  }
}

function normalizeComparableText(value: string): string {
  return normalizeTextWithMap(String(value || '').replace(/^\s*(?:\$\$|\\\[|\\\()\s*/, '').replace(/\s*(?:\$\$|\\\]|\\\))\s*$/, '')).text
}

function selectableTextNodes(root: Node): Text[] {
  const nodes: Text[] = []
  const skipTags = new Set(['SCRIPT', 'STYLE', 'IMG', 'SVG', 'MATH'])
  function visit(node: Node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const element = node as HTMLElement
      if (skipTags.has(element.tagName)) return
      if (element.dataset.mdMath === '1') {
        const source = element.querySelector<HTMLElement>('[data-md-math-source]')
        const text = source ? firstTextNode(source) : null
        if (text?.textContent) nodes.push(text)
        return
      }
      if (element.classList.contains('katex') || element.closest('[data-md-math]')) return
      Array.from(element.childNodes).forEach(visit)
      return
    }
    if (node.nodeType !== Node.TEXT_NODE) return
    if (node.textContent) nodes.push(node as Text)
  }
  visit(root)
  return nodes
}

function selectableTextFromRange(container: HTMLElement, range: Range): { text: string, leadingTrim: number } | null {
  const textNodes = selectableTextNodes(container)
  if (!textNodes.length) return null
  let text = ''
  let started = false
  for (const node of textNodes) {
    const value = node.textContent || ''
    if (node === range.startContainer && node === range.endContainer) {
      const start = clampOffset(range.startOffset, node)
      const end = clampOffset(range.endOffset, node)
      text += value.slice(Math.min(start, end), Math.max(start, end))
      break
    }
    if (node === range.startContainer) {
      started = true
      text += value.slice(clampOffset(range.startOffset, node))
      continue
    }
    if (node === range.endContainer) {
      if (started) text += value.slice(0, clampOffset(range.endOffset, node))
      break
    }
    if (started) text += value
  }
  if (!text && range.intersectsNode) {
    for (const node of textNodes) {
      const parent = node.parentElement
      if (!parent || !range.intersectsNode(parent)) continue
      text += node.textContent || ''
    }
  }
  return text ? { text, leadingTrim: text.length - text.trimStart().length } : null
}

function applyHighlightToTextNodes(doc: Document, textNodes: Text[], item: HighlightRecord): void {
  const fullText = textNodes.map(node => node.textContent || '').join('')
  const range = resolveHighlightRange(fullText, item)
  if (!range) return
  applyHighlightRangesToTextNodes(doc, textNodes, [{ ...range, item, order: 0 }])
}

function resolveHighlightRange(fullText: string, item: HighlightRecord): { start: number, end: number } | null {
  const text = String(item.text || '').trim()
  if (!text) return null
  let start = typeof item.textOffset === 'number' && item.textOffset >= 0 ? item.textOffset : -1
  let end = start >= 0 ? start + text.length : -1
  if (start < 0 || fullText.slice(start, end) !== text) {
    start = nthIndexOf(fullText, text, item.occurrence || 0)
    end = start >= 0 ? start + text.length : -1
  }
  if (start < 0) {
    start = fullText.indexOf(text)
    end = start >= 0 ? start + text.length : -1
  }
  if (start < 0) {
    const normalized = findNormalizedTextRange(fullText, text, item.occurrence || 0)
    if (!normalized) return null
    start = normalized.start
    end = normalized.end
  }
  if (start < 0 || end <= start) return null
  return { start, end }
}

function findNormalizedTextRange(source: string, needle: string, occurrence: number): { start: number, end: number } | null {
  const sourceMap = normalizeTextWithMap(source)
  const needleText = normalizeTextWithMap(needle).text
  if (!sourceMap.text || !needleText) return null
  const normalizedStart = nthIndexOf(sourceMap.text, needleText, occurrence)
  if (normalizedStart < 0) return null
  const normalizedEnd = normalizedStart + needleText.length
  const start = sourceMap.map[normalizedStart]
  const end = (sourceMap.map[normalizedEnd - 1] ?? start) + 1
  return { start, end }
}

function normalizeTextWithMap(value: string): { text: string, map: number[] } {
  let text = ''
  const map: number[] = []
  let pendingSpace = false
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index]
    if (/\s/.test(char)) {
      pendingSpace = text.length > 0
      continue
    }
    if (pendingSpace) {
      text += ' '
      map.push(index)
      pendingSpace = false
    }
    text += char
    map.push(index)
  }
  return { text, map }
}

function applyHighlightRangesToTextNodes(doc: Document, textNodes: Text[], ranges: ResolvedHighlightRange[]): void {
  const normalizedRanges = normalizeHighlightRanges(ranges)
  if (!normalizedRanges.length) return
  let cursor = 0
  let rangeIndex = 0
  const nodes = [...textNodes]
  for (const node of nodes) {
    const value = node.textContent || ''
    const nodeStart = cursor
    const nodeEnd = cursor + value.length
    cursor = nodeEnd
    if (!node.parentElement || !value) continue
    while (rangeIndex < normalizedRanges.length && normalizedRanges[rangeIndex].end <= nodeStart) rangeIndex += 1
    const nodeRanges: ResolvedHighlightRange[] = []
    for (let index = rangeIndex; index < normalizedRanges.length && normalizedRanges[index].start < nodeEnd; index += 1) {
      const range = normalizedRanges[index]
      if (range.end > nodeStart) nodeRanges.push(range)
    }
    if (!nodeRanges.length) continue
    const mathElement = node.parentElement.closest<HTMLElement>('[data-md-math]')
    if (mathElement) {
      highlightMathElement(doc, mathElement, nodeRanges[0].item)
      continue
    }
    const fragment = doc.createDocumentFragment()
    let localOffset = 0
    for (const range of nodeRanges) {
      const from = Math.max(localOffset, Math.max(0, range.start - nodeStart))
      const to = Math.min(value.length, range.end - nodeStart)
      if (from > localOffset) fragment.appendChild(doc.createTextNode(value.slice(localOffset, from)))
      if (from < to) {
        fragment.appendChild(createHighlightSpan(doc, range.item, value.slice(from, to)))
        localOffset = to
      }
    }
    if (localOffset < value.length) fragment.appendChild(doc.createTextNode(value.slice(localOffset)))
    node.parentElement.replaceChild(fragment, node)
  }
}


function highlightMathElement(doc: Document, element: HTMLElement, item: HighlightRecord): void {
  const existingWrapper = element.parentElement?.closest<HTMLElement>('[data-highlight-id]')
  if (existingWrapper) return
  const wrapper = createHighlightSpan(doc, item, '')
  wrapper.classList.add('math-selection-highlight')
  element.parentNode?.insertBefore(wrapper, element)
  wrapper.appendChild(element)
}

function normalizeHighlightRanges(ranges: ResolvedHighlightRange[]): ResolvedHighlightRange[] {
  const sorted = [...ranges]
    .filter(range => range.end > range.start)
    .sort((a, b) => a.start - b.start || a.end - b.end || a.order - b.order)
  const result: ResolvedHighlightRange[] = []
  let coveredUntil = -1
  for (const range of sorted) {
    const start = Math.max(range.start, coveredUntil)
    if (start >= range.end) continue
    result.push({ ...range, start })
    coveredUntil = range.end
  }
  return result
}

function createHighlightSpan(doc: Document, item: HighlightRecord, text: string): HTMLElement {
  const span = doc.createElement('span')
  span.className = 'selection-highlight'
  span.dataset.highlightId = item.id
  span.setAttribute('style', highlightStyle(item))
  span.textContent = text
  return span
}

function wrapTextNodeRange(doc: Document, textNodes: Text[], start: number, end: number, item: HighlightRecord): void {
  applyHighlightRangesToTextNodes(doc, textNodes, [{ item, start, end, order: 0 }])
}

function highlightStyle(item: HighlightRecord): string {
  return [
    `--branch-bg:${item.color.bg}`,
    `--branch-border:${item.color.border}`,
    `--branch-strong:${item.color.strong}`,
    `--branch-text:${item.color.text}`,
  ].join(';')
}

function nthIndexOf(source: string, needle: string, occurrence: number): number {
  let from = 0
  let index = -1
  for (let count = 0; count <= Math.max(0, occurrence); count += 1) {
    index = source.indexOf(needle, from)
    if (index < 0) return -1
    from = index + needle.length
  }
  return index
}

function occurrenceBeforeOffset(source: string, needle: string, offset: number): number {
  if (!needle || offset <= 0) return 0
  let count = 0
  let from = 0
  while (from < offset) {
    const index = source.indexOf(needle, from)
    if (index < 0 || index >= offset) break
    count += 1
    from = index + needle.length
  }
  return count
}

function MathFormula({ tex, displayMode = false }: { tex: string, displayMode?: boolean }) {
  const normalized = tex.trim()
  const html = useMemo(() => {
    if (!normalized) return ''
    try {
      return renderToString(normalized, {
        displayMode,
        throwOnError: false,
        strict: 'ignore',
        trust: false,
      })
    } catch {
      return ''
    }
  }, [normalized, displayMode])
  const className = displayMode ? 'math-block' : 'math-inline'
  if (!html) {
    return displayMode
      ? <pre className={`${className} math-fallback`}>{normalized}</pre>
      : <code className={`${className} math-fallback`}>{normalized}</code>
  }
  const Tag = displayMode ? 'div' : 'span'
  return <Tag className={className} dangerouslySetInnerHTML={{ __html: html }} />
}

function buildSessionTree(items: LearningSessionState[]) {
  const byParent: Record<string, LearningSessionState[]> = {}
  for (const item of items) {
    const parent = item.parent_session_id || 'root'
    byParent[parent] = [...(byParent[parent] || []), item]
  }
  return byParent
}

function isBlankRoot(item: LearningSessionState): boolean {
  return !item.parent_session_id && !item.root_question && !item.last_question && (item.title || '') === '新的学习问题'
}

function collectLocalDescendants(sessionId: string, sessions: Record<string, LearningSessionState>): string[] {
  const result: string[] = []
  const stack = [sessionId]
  while (stack.length) {
    const current = stack.pop() || ''
    for (const session of Object.values(sessions)) {
      if (session.parent_session_id === current) {
        result.push(session.session_id)
        stack.push(session.session_id)
      }
    }
  }
  return result
}

function makeBranchColor(hue: number, depth: number): BranchColor {
  const normalized = ((hue % 360) + 360) % 360
  const light = Math.max(48, 96 - depth * 9)
  const bg = `hsl(${normalized} 78% ${light}%)`
  const border = `hsl(${normalized} 58% ${Math.max(40, light - 22)}%)`
  const strong = `hsl(${normalized} 70% ${Math.max(32, light - 36)}%)`
  return {
    hue: normalized,
    depth,
    bg,
    border,
    strong,
    text: depth >= 4 ? '#ffffff' : '#172033',
  }
}

function buildBranchColors(items: LearningSessionState[]): Record<string, BranchColor> {
  const children = buildSessionTree(items)
  const colors: Record<string, BranchColor> = {}
  const roots = children.root || []
  roots.forEach((root, index) => {
    const rootHue = 212 + index * 28
    colors[root.session_id] = makeBranchColor(rootHue, 0)
    assignChildColors(root.session_id, rootHue, 1, children, colors)
  })
  for (const item of items) {
    if (!colors[item.session_id]) {
      const parentColor = item.parent_session_id ? colors[item.parent_session_id] : ROOT_BRANCH_COLOR
      colors[item.session_id] = makeBranchColor(parentColor.hue, (parentColor.depth || 0) + 1)
    }
  }
  return colors
}

function assignChildColors(parentId: string, parentHue: number, depth: number, children: Record<string, LearningSessionState[]>, colors: Record<string, BranchColor>) {
  const siblings = children[parentId] || []
  const spread = Math.min(32, 10 + siblings.length * 4)
  siblings.forEach((child, index) => {
    const midpoint = (siblings.length - 1) / 2
    const hue = parentHue + (index - midpoint) * (spread / Math.max(1, siblings.length))
    colors[child.session_id] = makeBranchColor(hue, depth)
    assignChildColors(child.session_id, hue, depth + 1, children, colors)
  })
}

function deriveChildColor(parentId: string, items: LearningSessionState[], colors: Record<string, BranchColor>): BranchColor {
  const parentColor = colors[parentId] || ROOT_BRANCH_COLOR
  const siblingCount = items.filter(item => item.parent_session_id === parentId).length
  const hue = parentColor.hue + Math.min(18, 8 + siblingCount * 3)
  return makeBranchColor(hue, parentColor.depth + 1)
}

function nextWindowPlacement(windows: FloatingWindowState[], sourceSessionId: string): { x: number, y: number, width: number, height: number } {
  const visible = windows.filter(win => !win.minimized)
  const related = [...visible].reverse().find(win => win.sourceSessionId === sourceSessionId || win.sessionId === sourceSessionId)
  const base = related || visible.reduce((best, win) => (win.zIndex > (best?.zIndex || 0) ? win : best), null as FloatingWindowState | null)
  const width = base?.width || 520
  const height = base?.height || 520
  const step = 28
  const rawX = (base?.x ?? 340) + step
  const rawY = (base?.y ?? 96) + step
  const maxX = Math.max(8, window.innerWidth - width - 24)
  const maxY = Math.max(8, window.innerHeight - height - 24)
  const wrappedX = rawX > maxX ? 320 + (visible.length % 4) * step : rawX
  const wrappedY = rawY > maxY ? 88 + (visible.length % 4) * step : rawY
  return {
    x: Math.max(8, Math.min(wrappedX, maxX)),
    y: Math.max(8, Math.min(wrappedY, maxY)),
    width,
    height,
  }
}

function colorVars(color: BranchColor): Record<string, string> {
  return {
    '--branch-bg': color.bg,
    '--branch-border': color.border,
    '--branch-strong': color.strong,
    '--branch-text': color.text,
  } as Record<string, string>
}
