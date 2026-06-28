from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

from app_gui.runtime import AgentRuntimeService

runtime = AgentRuntimeService()


def create_app(runtime_service: Optional[AgentRuntimeService] = None):
    try:
        from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
        # Avoid endpoint-local Pydantic models: with postponed annotations they can be
        # resolved as query parameters by FastAPI in some environments. Use Body dicts.
    except ImportError as exc:  # pragma: no cover - exercised only without optional deps
        raise RuntimeError("R-Agent Cockpit server requires optional dependencies: fastapi, uvicorn, pydantic") from exc

    service = runtime_service or runtime
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

    @app.post("/sessions/{session_id}/interrupt")
    def interrupt(session_id: str) -> Dict[str, Any]:
        try:
            return service.interrupt(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}/events")
    def get_events(session_id: str, event_type: Optional[str] = None) -> Dict[str, Any]:
        try:
            session = service.get_session(session_id)
            return {"session_id": session_id, "events": session.store.list_events(event_type=event_type)}
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
    uvicorn.run("app_gui.server:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
