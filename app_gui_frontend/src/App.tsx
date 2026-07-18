import { useEffect, useMemo, useRef, useState } from 'react'
import MarkdownIt from 'markdown-it'
import { renderToString } from 'katex'
import { BookOpen, Check, Copy, Edit3, Ellipsis, Eye, File, FileText, Folder, Languages, Lightbulb, ListTree, Maximize2, MessageCircle, Minimize2, Plus, Search, Save, Send, Square, Trash2, Workflow, X, ZoomIn, ZoomOut } from 'lucide-react'
import 'katex/dist/katex.min.css'
import {
  copyWorkspaceItem,
  createLearningSession,
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
  const [input, setInput] = useState('')
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
  const [windowInputs, setWindowInputs] = useState<Record<string, string>>({})
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

  const chatItems = useMemo(() => buildChatItems(events), [events])
  const visibleSessions = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return accountRootIds
      .map(id => sessions[id])
      .filter(Boolean)
      .filter(item => item.node_kind !== 'file_root')
      .sort((a, b) => Number(isBlankRoot(a)) - Number(isBlankRoot(b)) || Number(!!b.running) - Number(!!a.running) || (b.event_count || 0) - (a.event_count || 0))
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
  const uploadInputRef = useRef<HTMLInputElement | null>(null)

  async function boot() {
    setError(null)
    try {
      const roots = await fetchLearningAccountRoots(accountId)
      const first = roots.nodes.find(item => item.node_kind !== 'file_root') || roots.nodes[0]
      const s = first || await createLearningSession({ title: '新的学习问题', account_id: accountId })
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
    if (!session) return
    const timer = window.setInterval(async () => {
      try {
        await refreshActive(session.session_id)
        await refreshOpenWindows()
      } catch { /* ignore */ }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [session?.session_id, Object.keys(windows).join('|')])

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

  async function refreshActive(sessionId = session?.session_id || '') {
    if (!sessionId) return
    try {
      const [state, ev] = await Promise.all([
        fetchLearningSession(sessionId),
        fetchLearningEvents(sessionId),
      ])
      setSession(state)
      setEvents(ev)
      setSessions(prev => ({ ...prev, [state.session_id]: state }))
    } catch (err: any) {
      await recoverActiveSession()
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
    const created = await createLearningSession({ title: '新的学习问题', account_id: accountId })
    setSessions({ [created.session_id]: created })
    setAccountRootIds([created.session_id])
    setSession(created)
    setEvents([])
  }

  async function refreshOpenWindows() {
    const entries = Object.values(windows).filter(win => !win.minimized)
    if (!entries.length) return
    const updates = await Promise.all(entries.map(async win => {
      try {
        const [state, ev] = await Promise.all([
          fetchLearningSession(win.sessionId),
          fetchLearningEvents(win.sessionId),
        ])
        return { win, state, ev, missing: false }
      } catch {
        return { win, state: null, ev: [], missing: true }
      }
    }))
    const missing = updates.filter(item => item?.missing).map(item => item!.win.id)
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
    const valid = updates.filter(item => item && !item.missing && item.state) as Array<{ win: FloatingWindowState, state: LearningSessionState, ev: ContextEvent[] }>
    if (!valid.length) return
    setSessions(prev => {
      const next = { ...prev }
      for (const item of valid) next[item.state.session_id] = item.state
      return next
    })
    setWindowEvents(prev => {
      const next = { ...prev }
      for (const item of valid) next[item.win.id] = item.ev
      return next
    })
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
    await refreshActive(sessionId)
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
      const s = await createLearningSession({ title: '新的学习问题', account_id: accountId })
      setSessions(prev => ({ ...prev, [s.session_id]: s }))
      setAccountRootIds(prev => [s.session_id, ...prev])
      await activateSession(s.session_id)
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function submit() {
    if (!session || !input.trim()) return
    const text = input
    setInput('')
    setError(null)
    try {
      await sendLearningMessage(session.session_id, text)
      await refreshActive(session.session_id)
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
    await interruptLearning(session.session_id)
    await refreshActive(session.session_id)
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
      const result = await forkLearningSessionFromMessage(sessionId, messageIndex)
      const branch = result.session
      setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
      setConversationChildren(prev => ({
        ...prev,
        [sessionId]: [branch.session_id, ...(prev[sessionId] || [])],
      }))
      setWindowInputs(prev => ({ ...prev, [`win_${branch.session_id}`]: result.draft }))
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
    if (!window.confirm('确认回退到这条用户消息之前？该消息及其之后的上下文会被删除。')) return
    try {
      const result = await setbackLearningSession(sessionId, messageIndex)
      setSessions(prev => ({ ...prev, [result.session.session_id]: result.session }))
      if (windowId) {
        setWindowInputs(prev => ({ ...prev, [windowId]: result.draft }))
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
    const result = readTextSelectionWithin(container, anchor, event)
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
      x: Math.min(result.rect.left + result.rect.width / 2, window.innerWidth - 220),
      y: Math.max(12, result.rect.top - 46),
    }, result.range)
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
  }) {
    const windowId = `win_${branch.session_id}`
    const nextZ = topZ + 1
    const color = options.color || branchColors[branch.session_id] || deriveChildColor(options.sourceSessionId, Object.values(sessions), branchColors)
    const placement = nextWindowPlacement(Object.values(windows), options.sourceSessionId)
    setTopZ(nextZ)
    setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
    setWindows(prev => ({
      ...prev,
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
    }))
    setWindowEvents(prev => ({ ...prev, [windowId]: [] }))
    const ev = await fetchLearningEvents(branch.session_id)
    setWindowEvents(prev => ({ ...prev, [windowId]: ev }))
  }

  async function createSelectionBranch(actionState: PendingSelectionAction, options: { customQuestion?: string, targetLanguage?: string, noteText?: string } = {}) {
    const menu = actionState
    try {
      const actionMeta = SELECTION_ACTIONS.find(item => item.action === menu.action)
      let sourceSessionId = menu.sourceSessionId
      const filePath = typeof menu.sourceContext?.path === 'string' ? menu.sourceContext.path : ''
      if (filePath) {
        const fileRoot = await getLearningFileRoot(accountId, filePath)
        sourceSessionId = fileRoot.session_id
        setSessions(prev => ({ ...prev, [fileRoot.session_id]: fileRoot }))
        setAccountRootIds(prev => prev.includes(fileRoot.session_id) ? prev : [...prev, fileRoot.session_id])
      }
      const branch = await selectionBranchLearningSession(sourceSessionId, {
        selected_text: menu.text,
        action: menu.action,
        custom_question: options.customQuestion,
        target_language: options.targetLanguage,
        note_text: options.noteText,
        source_context: menu.sourceContext,
      })
      const highlightId = `hl_${Date.now()}_${Math.random().toString(16).slice(2)}`
      const color = branchColors[branch.session_id] || deriveChildColor(menu.sourceSessionId, Object.values(sessions), branchColors)
      if (menu.pdfContext) {
        setPdfHighlights(prev => ({
          ...prev,
          [highlightId]: {
            id: highlightId,
            sessionId: branch.session_id,
            path: menu.pdfContext?.path || '',
            page: menu.pdfContext?.page || 1,
            text: menu.text,
            rects: menu.pdfContext?.rects || [],
            color,
          },
        }))
      }
      const highlight: HighlightRecord = {
        id: highlightId,
        sessionId: branch.session_id,
        sourceSessionId,
        chatId: menu.chatId,
        text: menu.text,
        action: menu.action,
        label: actionMeta?.label || '提问',
        color,
      }
      setHighlights(prev => ({ ...prev, [highlightId]: highlight }))
      setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
      setConversationChildren(prev => ({
        ...prev,
        [sourceSessionId]: [branch.session_id, ...(prev[sourceSessionId] || [])],
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
      })
    } catch (err: any) {
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
        selected_text: action.text,
        note_text: note,
        source_context: action.sourceContext,
      })
      const highlightId = `hl_${Date.now()}_${Math.random().toString(16).slice(2)}`
      const color = branchColors[branch.session_id] || deriveChildColor(sourceSessionId, Object.values(sessions), branchColors)
      if (action.pdfContext) {
        setPdfHighlights(prev => ({
          ...prev,
          [highlightId]: {
            id: highlightId,
            sessionId: branch.session_id,
            path: action.pdfContext?.path || '',
            page: action.pdfContext?.page || 1,
            text: action.text,
            rects: action.pdfContext?.rects || [],
            color,
          },
        }))
      }
      setHighlights(prev => ({
        ...prev,
        [highlightId]: {
          id: highlightId,
          sessionId: branch.session_id,
          sourceSessionId,
          chatId: action.chatId,
          text: action.text,
          action: 'note',
          label: '笔记',
          color,
        },
      }))
      setSessions(prev => ({ ...prev, [branch.session_id]: branch }))
      setConversationChildren(prev => ({
        ...prev,
        [sourceSessionId]: [branch.session_id, ...(prev[sourceSessionId] || [])],
      }))
      await openFloatingSession(branch, {
        sourceSessionId,
        title: branch.title || '笔记',
        highlightId,
        action: 'note',
        selectedText: action.text,
        noteText: note,
        color,
      })
      setWindows(prev => {
        const id = `win_${branch.session_id}`
        return prev[id] ? { ...prev, [id]: { ...prev[id], minimized: true } } : prev
      })
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function sendWindowMessage(windowId: string) {
    const win = windows[windowId]
    const text = (windowInputs[windowId] || '').trim()
    if (!win || !text) return
    setWindowInputs(prev => ({ ...prev, [windowId]: '' }))
    try {
      await sendLearningMessage(win.sessionId, text)
      const [state, ev] = await Promise.all([
        fetchLearningSession(win.sessionId),
        fetchLearningEvents(win.sessionId),
      ])
      setSessions(prev => ({ ...prev, [state.session_id]: state }))
      setWindowEvents(prev => ({ ...prev, [windowId]: ev }))
    } catch (err: any) {
      setError(err.message || String(err))
    }
  }

  async function stopWindow(windowId: string) {
    const win = windows[windowId]
    if (!win) return
    await interruptLearning(win.sessionId)
    const ev = await fetchLearningEvents(win.sessionId)
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
          const created = await createLearningSession({ title: '新的学习问题', account_id: accountId })
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
    setWindowEvents(prev => {
      const next = { ...prev }
      for (const [id, item] of Object.entries(windows)) {
        if (deleted.has(item.sessionId)) delete next[id]
      }
      return next
    })
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

  function openHighlight(highlightId: string) {
    const win = Object.values(windows).find(item => item.highlightId === highlightId)
    if (!win) return
    raiseWindow(win.id)
  }

  async function openWorkspaceItem(item: WorkspaceItem) {
    if (item.type === 'directory') {
      await refreshWorkspace(item.path)
      return
    }
    if (item.is_pdf) {
      const tab: OpenFileTab = { path: item.path, name: item.name, url: workspaceOpenUrl(item.path), type: 'pdf', loading: true }
      setOpenFiles(prev => ({ ...prev, [item.path]: tab }))
      setActiveFilePath(item.path)
      setActiveMode('files')
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
            onToggle={toggleConversationNode}
            onActivate={activateSession}
            onDelete={deleteRootSession}
          />)}
          {visibleFileRoots.length > 0 && <div className="account-tree-section">文件对话</div>}
          {visibleFileRoots.map(item => <ConversationTreeNode
            key={item.session_id}
            item={item}
            depth={0}
            activeSessionId={session?.session_id || ''}
            childrenById={conversationChildren}
            sessions={sessions}
            expanded={expandedConversationNodes}
            colors={branchColors}
            onToggle={toggleConversationNode}
            onActivate={activateSession}
            onDelete={deleteRootSession}
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
        onMarkdownSelection={(text, rect, path, range) => {
          const location = describeMarkdownSelectionLocation(openFiles[path]?.textContent || '', text)
          showSelectionMenu({
            sourceSessionId: session?.session_id || '',
            chatId: `markdown:${path}`,
            text,
            x: Math.min(rect.left + rect.width / 2, window.innerWidth - 220),
            y: Math.max(12, rect.top - 46),
            sourceContext: { kind: 'markdown', path, location },
          }, range)
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
            sourceContext: filePath ? { kind: 'pdf', path: filePath, location: `page ${page}` } : undefined,
          })
        }}
        onOpenHighlight={openHighlight}
      /> : <section className="conversation-panel">
          {!chatItems.length && <div className="learning-empty">
            <BookOpen size={28}/>
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
                  setWindowInputs(prev => ({ ...prev, [`win_${child.session_id}`]: child.root_question || child.last_question || '' }))
                }}
              />}
            </div>
            <MessageContent
              sessionId={session?.session_id || ''}
              item={item}
              collapsed={!!collapsedMessages[item.id]}
              highlights={highlights}
              onToggleCollapse={() => setCollapsedMessages(prev => ({ ...prev, [item.id]: !prev[item.id] }))}
              onTextSelection={(container, anchor, event) => handleTextSelection(session?.session_id || '', item.id, container, anchor, event)}
              onOpenHighlight={openHighlight}
            />
          </article>)}
          {session?.running && <div className="thinking-state main-thinking">{thinkingLabel(events, session.running)}</div>}
        </section>}

      {activeMode === 'chat' && <section className="learning-composer">
        <div className="composer-row">
          <textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => {
            if (shouldSubmitFromKey(e)) { e.preventDefault(); submit() }
          }}/>
          <button className="primary" onClick={submit}><Send size={18}/>发送</button>
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
      inputs={windowInputs}
      collapsedMessages={collapsedMessages}
      highlights={highlights}
      onInputChange={(windowId, value) => setWindowInputs(prev => ({ ...prev, [windowId]: value }))}
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
          setWindowInputs(prev => ({ ...prev, [`win_${child.session_id}`]: child.root_question || child.last_question || '' }))
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

function thinkingStats(events: ContextEvent[], running?: boolean): { elapsedSeconds: number, rounds: number } | null {
  if (!running) return null
  const lastInputIndex = Math.max(...events.map((event, index) => event.event_type === 'user_input_received' ? index : -1))
  if (lastInputIndex < 0) return { elapsedSeconds: 0, rounds: 0 }
  const start = events[lastInputIndex]?.created_at || Date.now() / 1000
  const afterInput = events.slice(lastInputIndex)
  return {
    elapsedSeconds: Math.max(0, Math.floor(Date.now() / 1000 - start)),
    rounds: afterInput.filter(event => event.event_type === 'llm_request_snapshot').length,
  }
}

function thinkingLabel(events: ContextEvent[], running?: boolean): string {
  const stats = thinkingStats(events, running)
  if (!stats) return ''
  return `思考过程中 · ${stats.elapsedSeconds}s · ${stats.rounds} 轮`
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

function ConversationTreeNode({ item, depth, activeSessionId, childrenById, sessions, expanded, colors, onToggle, onActivate, onDelete }: {
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
}

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

function MarkdownFileEditor({ tab, highlights, onUpdate, onToggleMode, onSave, onSelection, onOpenHighlight }: {
  tab: OpenFileTab
  highlights: Record<string, HighlightRecord>
  onUpdate: (content: string) => void
  onToggleMode: (mode: 'preview' | 'edit') => void
  onSave: () => void
  onSelection: (text: string, rect: DOMRect, range: Range) => void
  onOpenHighlight: (highlightId: string) => void
}) {
  const mode = tab.viewMode || 'preview'
  const selectionAnchorRef = useRef<TextSelectionAnchor | null>(null)
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
      ? <textarea className="markdown-editor" value={content} onChange={event => onUpdate(event.target.value)}/>
      : <div className="markdown-preview" onMouseDown={event => {
          if (!shouldManageMarkdownSelection(event)) return
          event.preventDefault()
          window.getSelection()?.removeAllRanges()
          selectionAnchorRef.current = makeTextSelectionAnchor(event.currentTarget, event)
        }} onMouseMove={event => {
          updateManagedTextSelection(event.currentTarget, selectionAnchorRef.current, event)
        }} onMouseLeave={() => {
          selectionAnchorRef.current = null
        }} onMouseUp={event => {
          const result = readTextSelectionWithin(event.currentTarget, selectionAnchorRef.current, event)
          selectionAnchorRef.current = null
          if (!result) return
          const selection = window.getSelection()
          selection?.removeAllRanges()
          selection?.addRange(result.range)
          onSelection(result.text, result.rect, result.range)
        }}>
          <MarkdownText text={content || '空 Markdown 文件。'} chatId={`markdown:${tab.path}`} highlights={highlights} onOpenHighlight={onOpenHighlight} basePath={tab.path}/>
        </div>}
  </section>
}

function FileWorkspacePanel({ openFiles, activeFile, activeFilePath, pdfHighlights, textHighlights, pdfZoom, onZoomOut, onZoomIn, onActivate, onClose, onUpdateMarkdown, onToggleMarkdownMode, onSaveMarkdown, onMarkdownSelection, onPdfSelection, onOpenHighlight }: {
  openFiles: Record<string, OpenFileTab>
  activeFile: OpenFileTab | null
  activeFilePath: string | null
  pdfHighlights: Record<string, PdfHighlightRecord>
  textHighlights: Record<string, HighlightRecord>
  pdfZoom: number
  onZoomOut: () => void
  onZoomIn: () => void
  onActivate: (path: string) => void
  onClose: (path: string) => void
  onUpdateMarkdown: (path: string, content: string) => void
  onToggleMarkdownMode: (path: string, mode: 'preview' | 'edit') => void
  onSaveMarkdown: (path: string) => void
  onMarkdownSelection: (text: string, rect: DOMRect, path: string, range: Range) => void
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
        ? <PdfTextReader tab={activeFile} highlights={pdfHighlights} zoom={pdfZoom} onSelection={onPdfSelection} onOpenHighlight={onOpenHighlight}/>
        : activeFile.type === 'markdown'
          ? <MarkdownFileEditor
              tab={activeFile}
              highlights={Object.fromEntries(Object.entries(textHighlights).filter(([, item]) => item.chatId === `markdown:${activeFile.path}`))}
              onUpdate={content => onUpdateMarkdown(activeFile.path, content)}
              onToggleMode={mode => onToggleMarkdownMode(activeFile.path, mode)}
              onSave={() => onSaveMarkdown(activeFile.path)}
              onSelection={(text, rect, range) => onMarkdownSelection(text, rect, activeFile.path, range)}
              onOpenHighlight={onOpenHighlight}
            />
        : <iframe className="pdf-frame" title={activeFile.name} src={activeFile.url}/>}
    </div> : <div className="file-empty">
      <Folder size={30}/>
      <span>从右侧文件系统打开 PDF 或文件。</span>
    </div>}
  </section>
}

function PdfTextReader({ tab, highlights, zoom, onSelection, onOpenHighlight }: {
  tab: OpenFileTab
  highlights: Record<string, PdfHighlightRecord>
  zoom: number
  onSelection: (text: string, page: number, rect: DOMRect, rects: PdfSelectionRect[]) => void
  onOpenHighlight: (highlightId: string) => void
}) {
  if (tab.loading) {
    return <div className="pdf-text-status">正在抽取 PDF 文本...</div>
  }
  if (tab.error) {
    return <div className="pdf-text-status error">PDF 文本抽取失败：{tab.error}</div>
  }
  if (!tab.pdfText?.pages.length) {
    return <div className="pdf-text-status">没有抽取到可选择文本。该 PDF 可能是扫描件，需要 OCR。</div>
  }
  return <div className="pdf-text-reader">
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
  const text = range.toString().trim()
  if (text.length < 2) return null
  const rect = range.getBoundingClientRect()
  if (!rect || (rect.width === 0 && rect.height === 0)) return null
  return { text, rect, range }
}

function makeTextSelectionAnchor(container: HTMLElement, event: any): TextSelectionAnchor | null {
  const target = event.target as HTMLElement
  if (target.closest('button, textarea, input, select, [data-highlight-id], .message-actions, .message-branch-menu')) return null
  return textSelectionPointFromEvent(container, event)
}

function shouldManageMarkdownSelection(event: any): boolean {
  const target = event.target as HTMLElement
  return !target.closest('button, textarea, input, select, [data-highlight-id], .message-actions, .message-branch-menu')
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
  const end = textSelectionPointFromEvent(container, event)
  if (!end) return null
  return rangeFromTextSelectionPoints(container, anchor, end)
}

function textSelectionPointFromEvent(container: HTMLElement, event: any): TextSelectionPoint | null {
  const target = event.target as HTMLElement
  const token = target.closest<HTMLElement>('[data-md-token]') || nearestMarkdownToken(container, event.clientX, event.clientY)
  if (!token || !container.contains(token)) return null
  if (token.dataset.mdSelectable !== '1') return null
  const textNode = firstTextNode(token)
  if (!textNode) return null
  const tokenIndex = Number(token.dataset.mdToken || -1)
  if (!Number.isFinite(tokenIndex) || tokenIndex < 0) return null
  return {
    tokenIndex,
    offset: offsetFromPointInTextNode(textNode, event.clientX, event.clientY),
  }
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

function nearestMarkdownToken(container: HTMLElement, x: number, y: number): HTMLElement | null {
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
  return bestScore <= 72 ? best : null
}

function firstTextNode(element: HTMLElement): Text | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
  return walker.nextNode() as Text | null
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
  inputs,
  collapsedMessages,
  highlights,
  onInputChange,
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
  inputs: Record<string, string>
  collapsedMessages: Record<string, boolean>
  highlights: Record<string, HighlightRecord>
  onInputChange: (windowId: string, value: string) => void
  onToggleCollapse: (messageId: string) => void
  onSend: (windowId: string) => void
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
              onMove(win.id, latestX, latestY)
            })
          }
          const handleUp = () => {
            if (frame) window.cancelAnimationFrame(frame)
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
              collapsed={!!collapsedMessages[item.id]}
              highlights={highlights}
              onToggleCollapse={() => onToggleCollapse(item.id)}
              onTextSelection={(container, anchor, event) => onTextSelection(win.sessionId, item.id, container, anchor, event)}
              onOpenHighlight={onOpenHighlight}
            />
          </article>)}
          {session?.running && <div className="thinking-state">{thinkingLabel(eventsByWindow[win.id] || [], session.running)}</div>}
        </div>
        <footer className="window-composer">
          <textarea
            value={inputs[win.id] || ''}
            onChange={e => onInputChange(win.id, e.target.value)}
            onKeyDown={e => {
              if (shouldSubmitFromKey(e)) {
                e.preventDefault()
                onSend(win.id)
              }
            }}
          />
          <button className="primary" onClick={() => onSend(win.id)}><Send size={16}/></button>
          <div className="composer-side-actions">
            <button className="secondary icon-only" onClick={() => onStop(win.id)}><Square size={16}/></button>
            <ToolsToggleButton
              enabled={session?.tools_enabled !== false}
              disabled={!session || !!session.running}
              onToggle={(enabled) => onToggleTools(win.sessionId, enabled)}
            />
          </div>
        </footer>
        {!win.fullscreen && <button className="window-resize-handle" title="拖动调整窗口大小" onMouseDown={event => {
          event.preventDefault()
          event.stopPropagation()
          onRaise(win.id)
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
              onResize(win.id, latestWidth, latestHeight)
            })
          }
          const handleUp = () => {
            if (frame) window.cancelAnimationFrame(frame)
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

function MessageContent({ sessionId, item, collapsed, highlights, onToggleCollapse, onTextSelection, onOpenHighlight }: {
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
      if (!shouldManageMarkdownSelection(event)) return
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
      onTextSelection(event.currentTarget, selectionAnchorRef.current, event)
      selectionAnchorRef.current = null
    }}
  >
    <MarkdownText text={visibleText} chatId={item.id} highlights={highlights} onOpenHighlight={onOpenHighlight}/>
    {canCollapse && <button className="collapse-toggle" onClick={onToggleCollapse}>{collapsed ? `展开全部 ${lines.length} 行` : '折叠到 10 行'}</button>}
  </div>
}

function MarkdownText({ text, chatId, highlights, onOpenHighlight, basePath = '' }: {
  text: string
  chatId: string
  highlights: Record<string, HighlightRecord>
  onOpenHighlight: (highlightId: string) => void
  basePath?: string
}) {
  const html = useMemo(() => renderMarkdownToHtml(text || '', { basePath, chatId, highlights }), [text, basePath, chatId, highlights])
  return <div
    className="markdown-body"
    onClick={event => {
      const target = event.target as HTMLElement
      const highlight = target.closest<HTMLElement>('[data-highlight-id]')
      if (highlight?.dataset.highlightId) {
        onOpenHighlight(highlight.dataset.highlightId)
      }
    }}
    dangerouslySetInnerHTML={{ __html: html }}
  />
}

function renderMarkdownToHtml(text: string, options: {
  basePath?: string
  chatId: string
  highlights: Record<string, HighlightRecord>
}): string {
  const mathHtml: string[] = []
  const withMath = text
    .replace(/\\\[\s*([\s\S]*?)\s*\\\]/g, (_, body) => stashMath(mathHtml, body, true))
    .replace(/\$\$\s*([\s\S]*?)\s*\$\$/g, (_, body) => stashMath(mathHtml, body, true))
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, body) => stashMath(mathHtml, body, false))
    .replace(/\$([^$\n]+)\$/g, (_, body) => stashMath(mathHtml, body, false))
  let html = markdownRenderer.render(withMath)
  mathHtml.forEach((value, index) => {
    html = html.split(`@@RAGENT_MATH_${index}@@`).join(value)
  })
  html = rewriteMarkdownAssetUrls(html, options.basePath || '')
  html = wrapMarkdownTextTokens(html)
  return applyMarkdownHighlights(html, options.chatId, options.highlights)
}

function stashMath(items: string[], tex: string, displayMode: boolean): string {
  const index = items.length
  try {
    items.push(renderToString(String(tex || '').trim(), {
      displayMode,
      throwOnError: false,
      strict: 'ignore',
      trust: false,
    }))
  } catch {
    items.push(markdownRenderer.utils.escapeHtml(String(tex || '')))
  }
  return `@@RAGENT_MATH_${index}@@`
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
      if (skipTags.has(element.tagName) || element.classList.contains('katex') || element.closest('.selection-highlight')) return
      Array.from(element.childNodes).forEach(visit)
      return
    }
    if (node.nodeType !== Node.TEXT_NODE) return
    const value = node.textContent || ''
    if (!value.trim()) return
    const parent = node.parentElement
    if (!parent || parent.closest('pre, code, .katex, .selection-highlight')) return
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

function applyMarkdownHighlights(html: string, chatId: string, highlights: Record<string, HighlightRecord>): string {
  let output = html
  for (const item of Object.values(highlights).filter(entry => entry.chatId === chatId)) {
    const escapedText = markdownRenderer.utils.escapeHtml(item.text)
    if (!escapedText || !output.includes(escapedText)) continue
    const style = [
      `--branch-bg:${item.color.bg}`,
      `--branch-border:${item.color.border}`,
      `--branch-strong:${item.color.strong}`,
      `--branch-text:${item.color.text}`,
    ].join(';')
    output = output.replace(
      escapedText,
      `<span class="selection-highlight" data-highlight-id="${markdownRenderer.utils.escapeHtml(item.id)}" style="${style}">${escapedText}</span>`,
    )
  }
  return output
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
