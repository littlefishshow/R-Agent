from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import os
from typing import Any, Callable, Dict, Optional

from app_gui.file_workspace import FileWorkspace
from app_gui.runtime import AgentRuntimeService, LearningRuntimeService

_runtime_service: Optional[AgentRuntimeService] = None
_learning_runtime_service: Optional[LearningRuntimeService] = None
_file_workspace: Optional[FileWorkspace] = None


class _LazyServiceProxy:
    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory

    def __getattr__(self, name: str) -> Any:
        return getattr(self._factory(), name)


def get_runtime_service() -> AgentRuntimeService:
    global _runtime_service
    if _runtime_service is None:
        _runtime_service = AgentRuntimeService()
    return _runtime_service


def get_learning_runtime_service() -> LearningRuntimeService:
    global _learning_runtime_service
    if _learning_runtime_service is None:
        _learning_runtime_service = LearningRuntimeService()
    return _learning_runtime_service


def get_file_workspace() -> FileWorkspace:
    global _file_workspace
    if _file_workspace is None:
        _file_workspace = FileWorkspace()
    return _file_workspace


def _workspace_for_session(default_workspace: FileWorkspace, session_id: str = "") -> FileWorkspace:
    """Return the shared GUI library rooted at ``outputs/``.

    The Cockpit file panel is a user-managed document library (papers, notes,
    generated reading outputs), not a disposable Agent execution workspace.
    Keep it shared and stable across chat sessions; ``session_id`` is accepted
    only for API compatibility with clients created during the sandbox rollout.
    """
    return default_workspace


def create_app(
    runtime_service: Optional[AgentRuntimeService] = None,
    learning_service: Optional[LearningRuntimeService] = None,
    workspace_service: Optional[FileWorkspace] = None,
):
    try:
        from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse
        from fastapi.responses import StreamingResponse
        from fastapi.staticfiles import StaticFiles
        # Avoid endpoint-local Pydantic models: with postponed annotations they can be
        # resolved as query parameters by FastAPI in some environments. Use Body dicts.
    except ImportError as exc:  # pragma: no cover - exercised only without optional deps
        raise RuntimeError("R-Agent Cockpit server requires optional dependencies: fastapi, uvicorn, pydantic") from exc

    service = runtime_service or _LazyServiceProxy(get_runtime_service)
    learning = learning_service or _LazyServiceProxy(get_learning_runtime_service)
    workspace = workspace_service or _LazyServiceProxy(get_file_workspace)
    app = FastAPI(title="R-Agent Cockpit API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8765"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    frontend_dist = Path(__file__).resolve().parents[1] / "app_gui_frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/app", StaticFiles(directory=str(frontend_dist), html=True), name="cockpit_frontend")

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "service": "r-agent-cockpit", "frontend_dist_exists": frontend_dist.exists()}

    @app.get("/frontend")
    def frontend_status() -> Dict[str, Any]:
        return {
            "mounted": frontend_dist.exists(),
            "path": str(frontend_dist),
            "url": "/app" if frontend_dist.exists() else None,
            "dev_url": "http://127.0.0.1:5173",
        }

    @app.post("/sessions")
    def create_session(request: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        try:
            session = service.create_session(session_id=request.get("session_id"))
            return session.state()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/sessions")
    def list_sessions() -> Dict[str, Any]:
        return service.list_sessions()

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> Dict[str, Any]:
        try:
            return service.get_session(session_id).state()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/send")
    def send_message(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            text = str(request.get("text") or "")
            background = bool(request.get("background", True))
            return service.send_message(session_id, text, background=background)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/continue")
    def continue_session(session_id: str, request: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        try:
            extra = request.get("extra_iterations")
            extra_iterations = int(extra) if extra not in (None, "") else None
            background = bool(request.get("background", True))
            return service.continue_after_truncation(session_id, extra_iterations=extra_iterations, background=background)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/sessions/{session_id}/interrupt")
    def interrupt(session_id: str) -> Dict[str, Any]:
        try:
            return service.interrupt(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}/events")
    def get_events(session_id: str, event_type: Optional[str] = None, since: int = 0) -> Dict[str, Any]:
        try:
            session = service.get_session(session_id)
            return {
                "session_id": session_id,
                "events": session.store.list_events(event_type=event_type, since=since),
                "event_count": session.store.event_count(),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc



    @app.get("/sessions/{session_id}/current-context")
    def get_current_context(session_id: str) -> Dict[str, Any]:
        try:
            return service.current_model_context(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}/resources")
    def get_resources(session_id: str) -> Dict[str, Any]:
        try:
            return service.resources(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/learning/sessions")
    def create_learning_session(request: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        try:
            session = learning.create_session(
                session_id=request.get("session_id"),
                title=str(request.get("title") or ""),
                root_question=str(request.get("root_question") or ""),
                parent_session_id=request.get("parent_session_id"),
                account_id=str(request.get("account_id") or "default"),
                tools_enabled=bool(request.get("tools_enabled", True)),
            )
            initial_question = str(request.get("initial_question") or request.get("root_question") or "")
            if initial_question.strip():
                send_result = session.send_message(initial_question, background=bool(request.get("background", True)))
                state = session.state()
                state["send"] = send_result
                return state
            return session.state()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/learning/sessions")
    def list_learning_sessions(account_id: Optional[str] = None) -> Dict[str, Any]:
        return learning.list_sessions(account_id=account_id)

    @app.post("/learning/file-root")
    def get_learning_file_root(request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            session = learning.get_or_create_file_root(
                account_id=str(request.get("account_id") or "default"),
                file_path=str(request.get("file_path") or ""),
            )
            return session.state()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/learning/accounts/{account_id}/roots")
    def get_learning_account_roots(account_id: str) -> Dict[str, Any]:
        try:
            return learning.account_roots(account_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/learning/sessions/{session_id}/children")
    def get_learning_session_children(session_id: str) -> Dict[str, Any]:
        try:
            return learning.child_nodes(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/learning/sessions/{session_id}")
    def get_learning_session(session_id: str) -> Dict[str, Any]:
        try:
            return learning.get_session(session_id).state()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/send")
    def send_learning_message(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            text = str(request.get("text") or "")
            background = bool(request.get("background", True))
            return learning.send_message(session_id, text, background=background)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/continue")
    def continue_learning_session(session_id: str, request: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        try:
            extra = request.get("extra_iterations")
            extra_iterations = int(extra) if extra not in (None, "") else None
            background = bool(request.get("background", True))
            return learning.continue_after_truncation(session_id, extra_iterations=extra_iterations, background=background)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/tools")
    def set_learning_tools_enabled(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return learning.set_tools_enabled(session_id, bool(request.get("enabled")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/branch")
    def branch_learning_session(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            question = str(request.get("question") or request.get("text") or "")
            return learning.branch_session(
                session_id,
                question=question,
                title=str(request.get("title") or ""),
                background=bool(request.get("background", True)),
                child_session_id=request.get("session_id"),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/setback")
    def setback_learning_session(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return learning.setback_to_message(session_id, int(request.get("message_index")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/fork-from-message")
    def fork_learning_session_from_message(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return learning.fork_from_message(session_id, int(request.get("message_index")), child_session_id=request.get("session_id"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/selection-branch")
    def branch_learning_selection(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return learning.branch_from_selection(
                session_id,
                selected_text=str(request.get("selected_text") or ""),
                action=str(request.get("action") or "question"),
                custom_question=str(request.get("custom_question") or ""),
                target_language=str(request.get("target_language") or ""),
                note_text=str(request.get("note_text") or ""),
                modification_instruction=str(request.get("modification_instruction") or ""),
                title=str(request.get("title") or ""),
                source_context=request.get("source_context") if isinstance(request.get("source_context"), dict) else None,
                background=bool(request.get("background", True)),
                child_session_id=request.get("session_id"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/selection-note")
    def save_learning_selection_note(session_id: str, request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            return learning.save_selection_note(
                session_id,
                selected_text=str(request.get("selected_text") or ""),
                note_text=str(request.get("note_text") or ""),
                title=str(request.get("title") or ""),
                source_context=request.get("source_context") if isinstance(request.get("source_context"), dict) else None,
                child_session_id=request.get("session_id"),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/accept-modification")
    def accept_learning_selection_modification(session_id: str) -> Dict[str, Any]:
        try:
            return learning.accept_selection_modification(
                session_id,
                workspace=_workspace_for_session(workspace, session_id),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/learning/sessions/{session_id}")
    def delete_learning_session(session_id: str) -> Dict[str, Any]:
        try:
            return learning.delete_subtree(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/learning/sessions/{session_id}/interrupt")
    def interrupt_learning(session_id: str) -> Dict[str, Any]:
        try:
            return learning.interrupt(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/learning/sessions/{session_id}/events")
    def get_learning_events(session_id: str, event_type: Optional[str] = None, since: int = 0) -> Dict[str, Any]:
        try:
            session = learning.get_session(session_id)
            return {
                "session_id": session_id,
                "events": session.store.list_events(event_type=event_type, since=since),
                "event_count": session.store.event_count(),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/learning/sessions/{session_id}/current-context")
    def get_learning_current_context(session_id: str) -> Dict[str, Any]:
        try:
            return learning.current_model_context(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/learning/sessions/{session_id}/resources")
    def get_learning_resources(session_id: str) -> Dict[str, Any]:
        try:
            return learning.resources(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/learning/sessions/{session_id}/payloads/{payload_id}")
    def get_learning_payload(session_id: str, payload_id: str) -> Dict[str, Any]:
        try:
            session = learning.get_session(session_id)
            return {"session_id": session_id, "payload_id": payload_id, "content": session.store.get_payload(payload_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"payload not found: {payload_id}") from exc

    @app.get("/workspace/files")
    def list_workspace_files(path: str = "", session_id: str = "") -> Dict[str, Any]:
        try:
            return _workspace_for_session(workspace, session_id).list_dir(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except NotADirectoryError as exc:
            raise HTTPException(status_code=400, detail=f"not a directory: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/workspace/tree")
    def get_workspace_tree(expanded: str = "", session_id: str = "") -> Dict[str, Any]:
        try:
            expanded_paths = [item for item in str(expanded or "").split(",") if item or item == ""]
            return _workspace_for_session(workspace, session_id).tree(expanded_paths)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/workspace/folders")
    def create_workspace_folder(request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            scoped_workspace = _workspace_for_session(workspace, str(request.get("session_id") or ""))
            return scoped_workspace.create_folder(str(request.get("path") or ""), str(request.get("name") or ""))
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"folder exists: {exc}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/workspace/files")
    def upload_workspace_file(request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            scoped_workspace = _workspace_for_session(workspace, str(request.get("session_id") or ""))
            return scoped_workspace.write_base64_file(
                str(request.get("path") or ""),
                str(request.get("name") or ""),
                str(request.get("content_base64") or ""),
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"file exists: {exc}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/workspace/text")
    def read_workspace_text(path: str, session_id: str = "") -> Dict[str, Any]:
        try:
            return _workspace_for_session(workspace, session_id).read_text_file(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=f"not a file: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/workspace/text")
    def write_workspace_text(request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            scoped_workspace = _workspace_for_session(workspace, str(request.get("session_id") or ""))
            return scoped_workspace.write_text_file(str(request.get("path") or ""), str(request.get("content") or ""))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=f"not a file: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/workspace/copy")
    def copy_workspace_item(request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            scoped_workspace = _workspace_for_session(workspace, str(request.get("session_id") or ""))
            return scoped_workspace.copy(
                str(request.get("source") or ""),
                str(request.get("target_dir") or ""),
                str(request.get("name") or "") or None,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/workspace/files")
    def delete_workspace_item(request: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            scoped_workspace = _workspace_for_session(workspace, str(request.get("session_id") or ""))
            requested_path = str(request.get("path") or "")
            resolved_path = scoped_workspace._resolve(requested_path)
            if resolved_path == scoped_workspace.root:
                raise ValueError("cannot delete workspace root")
            if not resolved_path.exists():
                raise FileNotFoundError(scoped_workspace._rel(resolved_path))
            is_directory = resolved_path.is_dir()
            normalized_path = scoped_workspace._rel(resolved_path)
            result = scoped_workspace.delete(requested_path)
            cleanup = learning.delete_sessions_for_workspace_path(
                normalized_path,
                is_directory=is_directory,
            )
            result["deleted_learning_sessions"] = cleanup.get("deleted_learning_sessions", [])
            return result
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/workspace/open")
    def open_workspace_file(path: str, download: bool = False, session_id: str = ""):
        try:
            file_path = _workspace_for_session(workspace, session_id).get_file(path)
            media_type = "application/pdf" if file_path.suffix.lower() == ".pdf" else None
            return FileResponse(
                str(file_path),
                media_type=media_type,
                filename=file_path.name if download else None,
                headers={"Content-Disposition": f"{'attachment' if download else 'inline'}; filename=\"{file_path.name}\""},
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=f"not a file: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/workspace/pdf-text")
    def get_workspace_pdf_text(path: str, session_id: str = "") -> Dict[str, Any]:
        try:
            return _workspace_for_session(workspace, session_id).extract_pdf_text(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=f"not a file: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/workspace/pdf-page-image")
    def get_workspace_pdf_page_image(path: str, page: int, zoom: float = 1.6, session_id: str = ""):
        try:
            png = _workspace_for_session(workspace, session_id).render_pdf_page_png(path, page, zoom=zoom)
            return StreamingResponse(
                BytesIO(png),
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"path not found: {exc}") from exc
        except IsADirectoryError as exc:
            raise HTTPException(status_code=400, detail=f"not a file: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}/payloads/{payload_id}")
    def get_payload(session_id: str, payload_id: str) -> Dict[str, Any]:
        try:
            session = service.get_session(session_id)
            return {"session_id": session_id, "payload_id": payload_id, "content": session.store.get_payload(payload_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"payload not found: {payload_id}") from exc

    @app.websocket("/sessions/{session_id}/ws")
    async def session_events(websocket: WebSocket, session_id: str):
        await websocket.accept()
        try:
            session = service.get_session(session_id)
        except KeyError:
            await websocket.send_json({"event_type": "error", "payload": {"error": f"session not found: {session_id}"}})
            await websocket.close(code=1008)
            return

        sent = 0
        try:
            while True:
                events = session.store.list_events()
                for event in events[sent:]:
                    await websocket.send_json(event)
                sent = len(events)
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
        except WebSocketDisconnect:
            return
        finally:
            return

    return app


try:
    app = create_app()
except RuntimeError:
    # Keep module importable in environments that have not installed optional GUI server deps yet.
    app = None


def main() -> None:  # pragma: no cover - manual server entrypoint
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Starting R-Agent Cockpit requires uvicorn. Install fastapi and uvicorn first.") from exc
    if app is None:
        raise RuntimeError("R-Agent Cockpit FastAPI app is unavailable. Install fastapi, uvicorn and pydantic first.")
    host = os.environ.get("R_AGENT_COCKPIT_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("R_AGENT_COCKPIT_PORT", "8765"))
    except ValueError:
        port = 8765
    uvicorn.run("app_gui.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
