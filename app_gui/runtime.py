from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app_gui.event_bus import ContextEventBus
from app_gui.schemas import (
    EVENT_ERROR,
    EVENT_MEMORY_SNAPSHOT_LOADED,
    EVENT_SESSION_STARTED,
    EVENT_SYSTEM_PROMPT_BUILT,
    EVENT_USER_INPUT_RECEIVED,
    new_id,
)
from app_gui.snapshot_store import ContextSnapshotStore
from app_gui.normalizer import normalize_messages, normalize_tool_schemas
from core import config
from core.agent import AgentInterrupted, RAgent
from core.memory import memory_manager
from core.prompt_builder import build_system_prompt
from core.skills import skill_manager
from tools.registry import registry

SELF_EVOLUTION_PROMPT = (
    "\n\n【重要提示：自我进化能力】\n"
    "1. 更新技能(Skills)：你可以使用 `skill_manage` 工具维护技能包；默认优先 patch 现有技能。只有当用户明确要求或发现高度可复用且现有技能无法承载的稳定工作流时，才创建新技能，避免每轮任务都新增 skill。\n"
    "2. 更新工具(Tools)：你可以使用 `write_file` 工具直接在 `tools/` 目录下编写新的 Python 工具模块并调用 `registry.register`。在下一轮对话时，系统会自动热重载并为你注册新工具。\n"
    "请始终使用中文回复用户。"
)


class GuiSession:
    def __init__(self, session_id: str, *, store_root: str | Path = "outputs/gui_context", agent: Optional[RAgent] = None):
        self.session_id = session_id
        self.store = ContextSnapshotStore(Path(store_root) / session_id)
        self.event_bus = ContextEventBus(store=self.store, session_id=session_id)
        self.agent = agent or RAgent(session_id=session_id)
        self.agent.session_id = session_id
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.last_response: Optional[str] = None
        self.last_error: Optional[str] = None
        self.system_prompt = self._build_and_emit_system_prompt()
        self.event_bus.emit(EVENT_SESSION_STARTED, {"session_id": session_id, "model": self.agent.model})

    def _build_and_emit_system_prompt(self) -> str:
        base_prompt = build_system_prompt() + SELF_EVOLUTION_PROMPT
        memory_snapshot = memory_manager.load_snapshot()
        self.event_bus.emit(EVENT_MEMORY_SNAPSHOT_LOADED, {"payload_ref": self.store.put_payload(memory_snapshot).to_dict()})
        system_prompt = base_prompt + memory_snapshot
        self.event_bus.emit(EVENT_SYSTEM_PROMPT_BUILT, {"payload_ref": self.store.put_payload(system_prompt).to_dict()})
        return system_prompt

    @property
    def running(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    def send_message(self, text: str, *, background: bool = True) -> Dict[str, Any]:
        text = str(text or "")
        if not text.strip():
            raise ValueError("message text is empty")
        with self._lock:
            if self.running:
                raise RuntimeError("session is already running")
            self.cancel_event = threading.Event()
            self.last_response = None
            self.last_error = None
            self.event_bus.emit(EVENT_USER_INPUT_RECEIVED, {"content": text})
            if background:
                self._thread = threading.Thread(target=self._run_message, args=(text,), name=f"gui-session-{self.session_id}", daemon=True)
                self._thread.start()
                return {"session_id": self.session_id, "status": "running"}
        # Run outside the lock for synchronous mode.
        response = self._run_message(text)
        return {"session_id": self.session_id, "status": "completed", "response": response}

    def _run_message(self, text: str) -> str:
        try:
            self.agent.model = config.get_model()
            self.agent.session_id = self.session_id
            self.agent.client = config.create_llm_client()
            response = self.agent.run_conversation(
                text,
                system_message=self.system_prompt,
                cancel_event=self.cancel_event,
                event_sink=self.event_bus,
            )
            self.last_response = response
            return response
        except AgentInterrupted:
            self.last_error = "interrupted"
            self.event_bus.emit(EVENT_ERROR, {"error": "interrupted"})
            raise
        except Exception as exc:
            self.last_error = str(exc)
            self.event_bus.emit(EVENT_ERROR, {"error": str(exc)})
            raise

    def interrupt(self, *, join_timeout: float = 1.0) -> Dict[str, Any]:
        self.cancel_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(join_timeout)))
        alive = bool(thread is not None and thread.is_alive())
        return {"session_id": self.session_id, "interrupted": True, "still_running": alive}

    def shutdown(self, *, join_timeout: float = 1.0) -> Dict[str, Any]:
        interrupt_result = self.interrupt(join_timeout=join_timeout)
        alive_background = self.agent.shutdown_background_tasks(timeout=join_timeout)
        interrupt_result["alive_background_tasks"] = alive_background
        return interrupt_result



    def current_model_context(self) -> Dict[str, Any]:
        """Return the current model-visible context for the next user turn.

        This is intentionally not a historical event timeline. It answers: if the
        user sends a message now, which context modules will be sent to the LLM?
        """
        try:
            tool_schemas = registry.get_all_schemas()
        except Exception as exc:
            tool_schemas = [{"error": str(exc)}]
        normalized_messages = normalize_messages(self.agent.messages)
        return {
            "session_id": self.session_id,
            "model": self.agent.model,
            "modules": [
                {
                    "id": "system_prompt",
                    "label": "System Prompt（含 SOUL / 规则 / frozen memory）",
                    "kind": "payload",
                    "payload_ref": self.store.put_payload(self.system_prompt).to_dict(),
                    "visible_to_model": True,
                    "description": "下一次请求中作为 system message 注入；其中包含启动时冻结的 memory snapshot。",
                },
                {
                    "id": "messages",
                    "label": "Conversation Messages（当前对话历史）",
                    "kind": "messages",
                    "items": normalized_messages,
                    "visible_to_model": True,
                    "description": "下一次请求会携带的当前 RAgent.messages。",
                },
                {
                    "id": "tool_schemas",
                    "label": "Tool Schemas（可调用工具定义）",
                    "kind": "json",
                    "items": normalize_tool_schemas(tool_schemas),
                    "visible_to_model": True,
                    "description": "下一次请求会携带的 tools schema；注意 tool 结果只有被调用后才会进入 messages。",
                },
                {
                    "id": "skills_note",
                    "label": "Skills（默认不全文塞入）",
                    "kind": "note",
                    "content": "Skill 全文默认不会直接塞给大模型；模型只能看到 skill 查询/读取工具的 schema，调用 skill_view 后相关内容才会作为 tool result 进入 messages。",
                    "visible_to_model": False,
                },
                {
                    "id": "live_memory_note",
                    "label": "Live Memory（默认不自动刷新）",
                    "kind": "note",
                    "content": "当前会话启动后 system prompt 中使用的是 frozen memory snapshot；运行中 memory 文件变化不会自动重新塞入，除非新会话或显式工具读取。",
                    "visible_to_model": False,
                },
            ],
        }

    def resources(self) -> Dict[str, Any]:
        """Return GUI-readable resource snapshots beyond the live event stream."""
        try:
            tool_schemas = registry.get_all_schemas()
        except Exception as exc:
            tool_schemas = [{"error": str(exc)}]

        try:
            skills_text = skill_manager.list_skills()
        except Exception as exc:
            skills_text = f"技能列表读取失败: {exc}"

        try:
            live_memory = memory_manager.read_memory_live()
        except Exception as exc:
            live_memory = f"实时 memory 读取失败: {exc}"

        review_path = Path("outputs/self_evolution/latest_review.json")
        review_payload: Any = None
        review_ref = None
        if review_path.exists():
            try:
                review_text = review_path.read_text(encoding="utf-8")
                review_ref = self.store.put_payload(review_text, content_type="application/json").to_dict()
                review_payload = json.loads(review_text)
            except Exception as exc:
                review_payload = {"error": str(exc), "path": str(review_path)}

        return {
            "session_id": self.session_id,
            "tools": {"count": len(tool_schemas), "schemas": tool_schemas},
            "skills": {"list_ref": self.store.put_payload(skills_text, content_type="text/markdown").to_dict()},
            "memory": {
                "frozen_ref": self.store.put_payload(memory_manager.read_memory_snapshot()).to_dict(),
                "live_ref": self.store.put_payload(live_memory).to_dict(),
            },
            "self_evolution": {
                "latest_path": str(review_path),
                "latest_exists": review_path.exists(),
                "latest": review_payload,
                "latest_ref": review_ref,
            },
        }

    def state(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "model": self.agent.model,
            "running": self.running,
            "event_count": len(self.event_bus.events),
            "last_response": self.last_response,
            "last_error": self.last_error,
            # Backward compatible fields: token_usage remains the parent Agent session total.
            "token_usage": self.agent.get_token_usage_total(),
            "last_token_usage": self.agent.get_last_token_usage_total(),
            "parent_session_token_usage": self.agent.get_token_usage_total(),
            "children_token_usage": self.agent.get_delegated_token_usage_total(),
            "total_token_usage_including_children": self.agent.get_total_token_usage_including_children(),
            "token_usage_breakdown": self.agent.get_token_usage_summary(include_children=True),
            "context_usage": self.agent.get_context_usage(),
        }


class AgentRuntimeService:
    def __init__(self, *, store_root: str | Path = "outputs/gui_context"):
        self.store_root = Path(store_root)
        self.sessions: Dict[str, GuiSession] = {}
        self._lock = threading.Lock()

    def create_session(self, *, session_id: Optional[str] = None, agent: Optional[RAgent] = None) -> GuiSession:
        sid = session_id or new_id("session")
        with self._lock:
            if sid in self.sessions:
                raise ValueError(f"session already exists: {sid}")
            session = GuiSession(sid, store_root=self.store_root, agent=agent)
            self.sessions[sid] = session
            return session

    def get_session(self, session_id: str) -> GuiSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"session not found: {session_id}") from exc

    def list_sessions(self) -> Dict[str, Any]:
        return {sid: session.state() for sid, session in self.sessions.items()}

    def send_message(self, session_id: str, text: str, *, background: bool = True) -> Dict[str, Any]:
        return self.get_session(session_id).send_message(text, background=background)

    def interrupt(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).interrupt()

    def shutdown_session(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).shutdown()

    def resources(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).resources()

    def current_model_context(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).current_model_context()
