from pathlib import Path


def test_cockpit_frontend_mvp_files_exist():
    root = Path("app_gui_frontend")
    expected = [
        root / "package.json",
        root / "index.html",
        root / "tsconfig.json",
        root / "src" / "main.tsx",
        root / "src" / "App.tsx",
        root / "src" / "api.ts",
        root / "src" / "styles.css",
        root / "src" / "components" / "ChatPane.tsx",
        root / "src" / "components" / "ContextTree.tsx",
        root / "src" / "components" / "Inspector.tsx",
        root / "src" / "components" / "Timeline.tsx",
    ]

    missing = [str(path) for path in expected if not path.exists()]
    assert missing == []


def test_cockpit_frontend_contains_three_panel_context_ui():
    app = Path("app_gui_frontend/src/App.tsx").read_text(encoding="utf-8")
    styles = Path("app_gui_frontend/src/styles.css").read_text(encoding="utf-8")
    api = Path("app_gui_frontend/src/api.ts").read_text(encoding="utf-8")

    assert "learning-sidebar" in app
    assert "search-row" in app
    assert "learning-detail" in app
    assert "FileBrowser" in app
    assert "文件系统" in app
    assert "read_paper 会在阅读笔记产生或手动新建后显示" in app
    assert "ChatPane" not in app
    assert "ContextTree" not in app
    assert "grid-template-columns: var(--sidebar-width, 296px) minmax(420px, 1fr) var(--detail-width, 340px)" in styles
    assert "/sessions" in api
    assert "/learning/sessions" in api
    assert "/current-context" in api
    assert "/payloads/" in api


def test_server_source_exposes_frontend_status_and_cors():
    server = Path("app_gui/server.py").read_text(encoding="utf-8")

    assert "CORSMiddleware" in server
    assert "StaticFiles" in server
    assert "@app.get(\"/frontend\")" in server
    assert "frontend_dist_exists" in server
    assert "/sessions/{session_id}/current-context" in server
    assert "/sessions/{session_id}/resources" in server
    assert "/learning/sessions" in server
    assert "/learning/sessions/{session_id}/branch" in server
    assert "/learning/sessions/{session_id}/selection-branch" in server
    assert "/learning/file-root" in server
    assert "@app.delete(\"/learning/sessions/{session_id}\")" in server
    assert "/workspace/files" in server
    assert "/workspace/folders" in server
    assert "/workspace/copy" in server
    assert "/workspace/open" in server
    assert "/workspace/pdf-text" in server
    assert "/workspace/pdf-page-image" in server
    assert "/workspace/tree" in server
    assert "/workspace/text" in server
    assert "FileWorkspace" in server


def test_learning_frontend_current_context_nodes_present():
    app = Path("app_gui_frontend/src/App.tsx").read_text(encoding="utf-8")
    api = Path("app_gui_frontend/src/api.ts").read_text(encoding="utf-8")

    assert "FileBrowser" in app
    assert "WorkspaceListing" in app
    assert "WorkspaceItem" in app
    assert "WorkspaceTreeNode" in app
    assert "OpenFileTab" in app
    assert "sourceContext" in app
    assert "source_context" in api
    assert "getLearningFileRoot" in app
    assert "/learning/file-root" in api
    assert "accountId" in app
    assert "fetchLearningAccountRoots" in app
    assert "visibleFileRoots" in app
    assert "setLearningToolsEnabled" in app
    assert "Tools" in app
    assert "tools_enabled" in api
    assert "/tools" in api
    assert "文件对话" not in app
    assert "visibleSessions.map" in app
    assert "child_count" in app
    assert "activeMode" in app
    assert "openFiles" in app
    assert "activeFilePath" in app
    assert "uploadWorkspaceFile" in app
    assert "copyWorkspaceItem" in app
    assert "deleteWorkspaceItem" in app
    assert "fetchWorkspacePdfText" in app
    assert "workspacePdfPageImageUrl" in app
    assert "fetchWorkspaceTree" in app
    assert "expanded.join(',')" in api
    assert "toggleWorkspaceFolder" in app
    assert "fetchWorkspaceText" in app
    assert "saveWorkspaceText" in app
    assert "workspaceOpenUrl" in app


def test_learning_frontend_new_chain_and_branch_controls_present():
    app = Path("app_gui_frontend/src/App.tsx").read_text(encoding="utf-8")
    styles = Path("app_gui_frontend/src/styles.css").read_text(encoding="utf-8")

    assert "async function newQuestionChain" in app
    assert "forkFromUserMessage" in app
    assert "setbackToUserMessage" in app
    assert "message-branch-menu" in app
    assert "chain-list" in styles
    assert "account-tree-section" in styles
    assert "search-row" in styles
    assert "chain-delete" in styles
    assert "onOpenSessionWindow" in app
    assert "deleteRootSession" in app


def test_learning_chat_renders_user_and_assistant_events():
    app = Path("app_gui_frontend/src/App.tsx").read_text(encoding="utf-8")

    assert "buildChatItems" in app
    assert "return items.map(item" in app
    assert "lastAssistant" not in app
    assert "messageIndex" in app
    assert "UserMessageActions" in app
    assert "forkFromUserMessage" in app
    assert "setbackToUserMessage" in app
    assert "forkLearningSessionFromMessage" in app
    assert "setbackLearningSession" in app
    assert "MessageContent" in app
    assert "message-copy-button" in app
    assert "copyTextToClipboard" in app
    assert "MarkdownText" in app
    assert "MarkdownIt" in app
    assert "renderMarkdownToHtml" in app
    assert "rewriteMarkdownAssetUrls" in app
    assert "renderToString" in app
    assert "katex/dist/katex.min.css" in app
    assert "MathFormula" in app
    assert "dangerouslySetInnerHTML" in app
    assert "throwOnError: false" in app
    assert "折叠到 10 行" in app
    assert "展开全部" in app
    assert "math-block" in app
    assert "math-inline" in app
    assert "math-fallback" in app
    assert "shouldSubmitFromKey" in app
    assert "isComposing" in app
    assert "user_input_received" in app
    assert "你的问题" in app
    assert "学习助手" in app
    assert "message?.role === 'tool'" not in app
    assert "assistantEvents" not in app


def test_learning_selection_menu_and_floating_windows_present():
    app = Path("app_gui_frontend/src/App.tsx").read_text(encoding="utf-8")
    styles = Path("app_gui_frontend/src/styles.css").read_text(encoding="utf-8")
    api = Path("app_gui_frontend/src/api.ts").read_text(encoding="utf-8")

    for label in ["提问", "翻译", "解释", "总结"]:
        assert label in app
    assert "笔记" in app
    assert "NoteDialog" in app
    assert "saveSelectionNoteLearningSession" in app
    assert "保存笔记" in app
    assert "发送给模型" in app
    assert "describeMarkdownSelectionLocation" in app
    assert "selectionBranchLearningSession" in app
    assert "deleteLearningSession" in app
    assert "pdf-frame" in app
    assert "FileWorkspacePanel" in app
    assert "MarkdownFileEditor" in app
    assert "markdown-preview" in app
    assert "markdown-editor" in app
    assert "selected markdown text" in app
    assert "kind: 'markdown'" in app
    assert "kind: 'pdf'" in app
    assert "read_paper" in app
    assert "column-resizer" in app
    assert "PdfTextReader" in app
    assert "PdfPageView" in app
    assert "pdf-word-layer" in app
    assert "pdf-page-image" in app
    assert "pdf-highlight-layer" in app
    assert "pdf-highlight-rect" in app
    assert "buildPdfHighlightRects" in app
    assert "findPdfHighlightForRect" in app
    assert "pdfRectsIntersect" in app
    assert "collectPdfDragSelection" in app
    assert "normalizeDragRect" in app
    assert "rectsOverlap" in app
    assert "onOpenHighlight={onOpenHighlight}" in app
    assert "restoreFileHighlights" in app
    assert "persist_${child.session_id}" in app
    assert "fetchLearningSession(highlight.sessionId)" in app
    assert "page.words && page.words.length ? page.words : page.lines" in app
    assert "pdfZoom" in app
    assert "pdf-zoom-controls" in app
    assert "ZoomIn" in app
    assert "ZoomOut" in app
    assert "file-workspace-panel" in styles
    assert "file-tabs" in app
    assert "file-tab active" in app
    assert "activeMode === 'chat'" in app
    assert "setActiveMode('chat')" in app
    assert "setActiveMode('files')" in app
    assert "setAccountRootIds([created.session_id])" in app
    assert "正在抽取 PDF 文本" in app
    assert "没有抽取到可选择文本" in app
    assert "文件系统" in app
    assert "Upload..." in app
    assert "粘贴" in app
    assert "Download" in app
    assert "FloatingWindows" in app
    assert "nextWindowPlacement" in app
    assert "function raiseWindow" in app
    assert "minimized: false" in app
    assert "zIndex: nextZ" in app
    assert "function messageCollapseKey" in app
    assert "messageCollapseKey(session?.session_id || '', item.id)" in app
    assert "messageCollapseKey(win.sessionId, item.id)" in app
    assert "eventsLoading" in app
    assert "开始新对话" in app
    assert "Object.values(windows).filter(win => win.minimized).map" in app
    assert "onClick={() => onRaise(win.id)}" in app
    assert "Object.values(windows).filter(win => !win.minimized).map" in app
    assert "QuestionDialog" in app
    assert "TranslateDialog" in app
    assert "selection-highlight" in app
    assert "restoreChatHighlights" in app
    assert "chat_id: chatId" in app
    assert "source.chat_id" in app
    assert "sourceSessionId={sessionId}" in app
    assert "entry.sourceSessionId === sourceSessionId" in app
    assert "buildCleanSelectionDisplay" in app
    assert "buildBranchColors" in app
    assert "makeBranchColor" in app
    assert "thinkingLabel" in app
    assert "ThinkingState" in app
    assert "startedAtRef" in app
    assert "timerTick" in app
    assert "window.setInterval(() => setTimerTick" in app
    assert "window.clearInterval(timer)" in app
    assert "textSelectionPointAroundMath" in app
    assert ".katex, .math-inline, .math-block" in app
    assert "firstUsableRect" in app
    assert "markdownNativeSelectionSelector" in app
    assert "pre, code, kbd, samp" in app
    assert "window.setTimeout(() => showResult(readTextSelectionWithin(container)), 0)" in app
    assert "window.setTimeout(() => handleResult(readTextSelectionWithin(container)), 0)" in app
    assert "const skipTags = new Set(['SCRIPT', 'STYLE', 'IMG', 'SVG', 'MATH'])" in app
    assert "TodoBoardPreview" in app
    assert "agent 正在规划" in app
    assert "todo_board" in app
    assert "Do not hide it just because an assistant/tool-call message" in app
    assert "if (hasTerminalEvent) return null" not in app
    assert "thinking-todo-board" in styles
    assert "todo-board-item" in styles
    assert "llm_request_snapshot" in app
    assert "onMove" in app
    assert "onResize" in app
    assert "mousemove" in app
    assert "思考过程中" in app
    assert "delete-window" in app
    assert "floating-window" in styles
    assert "file-browser" in styles
    assert "file-row" in styles
    assert "file-context-menu" in styles
    assert "hidden-file-input" in styles
    assert "EXPLORER" in app
    assert "requestAnimationFrame" in app
    assert "cancelAnimationFrame" in app
    assert "file-workspace-panel" in styles
    assert "markdown-file-panel" in styles
    assert "markdown-preview" in styles
    assert "markdown-editor" in styles
    assert "column-resizer" in styles
    assert "file-tabs" in styles
    assert "file-tab.active" in styles
    assert "learning-main.file-mode" in styles
    assert "pdf-text-reader" in styles
    assert "pdf-image-page" in styles
    assert "pdf-word-layer" in styles
    assert "pdf-word" in styles
    assert "pdf-line" in styles
    assert "pdf-drag-selection" in styles
    assert "pdf-highlight-layer" in styles
    assert "pdf-highlight-rect" in styles
    assert "cursor: text" in styles
    assert "pdf-zoom-controls" in styles
    assert "window-dock" in styles
    assert "dock-item" in styles
    assert "pdf-page-image" in styles
    assert "pdf-text-status" in styles
    assert "pdf-frame" in styles
    assert "left: var(--sidebar-width, 296px)" in styles
    assert "right: var(--detail-width, 340px)" in styles
    assert "window-resize-handle" in styles
    assert "nwse-resize" in styles
    assert "selection-menu" in styles
    assert "message-actions" in styles
    assert "message-copy-button" in styles
    assert "message-branch-menu" in styles
    assert "tools-toggle" in app
    assert "tools-toggle" in styles
    assert "action-dialog" in styles
    assert "language-options" in styles
    assert "resize: both" in styles
    assert "cursor: move" in styles
    assert "--branch-bg" in styles
    assert "color-mix" in styles
    assert "markdown-body" in styles
    assert "markdown-body table" in styles
    assert "markdown-body img" in styles
    assert "markdown-body blockquote" in styles
    assert "md-code" in styles
    assert "collapse-toggle" in styles
    assert "math-block" in styles
    assert "math-inline" in styles
    assert "math-block .katex-display" in styles
    assert "math-fallback" in styles
    assert "/selection-branch" in api
    assert "/selection-note" in api
    assert "/workspace/files" in api
    assert "/workspace/folders" in api
    assert "/workspace/copy" in api
    assert "/workspace/open" in api
    assert "/workspace/pdf-text" in api
    assert "/workspace/pdf-page-image" in api
    assert "/workspace/tree" in api
    assert "/workspace/text" in api
    assert "DELETE" in api

def test_learning_sidebar_keeps_root_order_from_backend():
    app = Path("app_gui_frontend/src/App.tsx").read_text(encoding="utf-8")
    assert "Number(!!b.running) - Number(!!a.running)" not in app
    assert "(b.event_count || 0) - (a.event_count || 0)" not in app
    assert "return accountRootIds" in app
    assert ".filter(item => item.node_kind !== 'file_root')" in app

