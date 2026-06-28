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

    assert "ContextTree" in app
    assert "ChatPane" in app
    assert "Inspector" in app
    assert "Timeline" not in app
    assert "grid-template-columns: 310px minmax(420px, 1fr) 420px" in styles
    assert "/sessions" in api
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


def test_cockpit_frontend_current_context_nodes_present():
    tree = Path("app_gui_frontend/src/components/ContextTree.tsx").read_text(encoding="utf-8")
    app = Path("app_gui_frontend/src/App.tsx").read_text(encoding="utf-8")

    assert "Current Model Context" in tree
    assert "LLM Visible" in tree
    assert "Available by Tool" in tree
    assert "fetchCurrentContext" in app
