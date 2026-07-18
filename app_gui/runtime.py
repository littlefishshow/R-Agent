from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app_gui.event_bus import ContextEventBus
from app_gui.schemas import (
    EVENT_ERROR,
    EVENT_MEMORY_SNAPSHOT_LOADED,
    EVENT_MESSAGE_APPENDED,
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

LEARNING_AGENT_PROMPT = (
    "\n\n【学习模式：发散式问答工作台】\n"
    "你正在一个专门用于学习的交互界面中回答问题。用户的学习方式是发散式的："
    "一个问题可能继续深挖，也可能从某个回答旁开出完全不同的新问题链。\n"
    "请把当前会话只视为当前问题链的上下文，不要假设其他问题链内容已经可见。"
    "如果问题适合拆成多个方向，先给出清晰的分支地图，再沿用户选择的方向深入。"
    "回答应服务学习：解释关键概念、给出例子、指出常见误区、必要时提供可验证的小实验。"
    "可以使用保留的学习工具做网页检索、资料抽取、只读文件查看、记忆/技能检索、轻量计算或委派子任务；"
    "不要修改项目文件，不要做无关工程执行。"
)

LEARNING_ALLOWED_TOOLS = {
    "web_search",
    "web_extract",
    "delegate_task",
    "read_file",
    "search_files",
    "memory_search",
    "memory_get",
    "skill_view",
    "skill_search",
    "artifact_inspect",
    "artifact_search",
    "artifact_slice",
    "run_python",
    "todo_manage",
    "archive_subtask",
    "speak_text",
    "text_to_speech",
}


def _make_learning_title(text: str, *, limit: int = 34) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return "新的学习问题"
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "..."


SELECTION_ACTIONS = {
    "question": ("提问", "请基于选中文本回答用户提出的问题。"),
    "translate": ("翻译", "只翻译选中文本，不要引入父对话上下文，不要解释无关内容。"),
    "explain": ("解释", "请解释选中文本本身的含义、背景、关键概念和常见误区。"),
    "summarize": ("总结", "请总结选中文本本身的核心要点。"),
    "note": ("笔记", "请结合选中文本和用户手写笔记回答，优先围绕笔记中的想法、疑问或判断展开。"),
}


class GuiSession:
    def __init__(self, session_id: str, *, store_root: str | Path = "outputs/gui_context", agent: Optional[RAgent] = None, restore: bool = False):
        self.session_id = session_id
        self.store = ContextSnapshotStore(Path(store_root) / session_id)
        self.event_bus = ContextEventBus(store=self.store, session_id=session_id)
        if restore:
            self.event_bus.events = list(self.store.events)
        self.agent = agent or RAgent(session_id=session_id)
        self.agent.session_id = session_id
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._active_run_id = ""
        self._active_run_baseline = []
        self.last_response: Optional[str] = None
        self.last_error: Optional[str] = None
        if restore:
            self.system_prompt = self._restore_system_prompt()
            self.agent.messages = self._restore_agent_messages()
        else:
            self.system_prompt = self._build_and_emit_system_prompt()
            self.event_bus.emit(EVENT_SESSION_STARTED, {"session_id": session_id, "model": self.agent.model})

    def _restore_system_prompt(self) -> str:
        for event in reversed(self.store.events):
            if event.get("event_type") != EVENT_SYSTEM_PROMPT_BUILT:
                continue
            payload = event.get("payload") or {}
            ref = payload.get("payload_ref") or {}
            payload_id = ref.get("id") if isinstance(ref, dict) else None
            if payload_id:
                try:
                    return self.store.get_payload(payload_id)
                except Exception:
                    break
        return self._build_system_prompt_text()

    def _build_system_prompt_text(self) -> str:
        return build_system_prompt() + SELF_EVOLUTION_PROMPT + memory_manager.load_snapshot()

    def _restore_agent_messages(self) -> list:
        messages = []
        indexed = []
        for event in self.store.events:
            if event.get("event_type") != EVENT_MESSAGE_APPENDED:
                continue
            payload = event.get("payload") or {}
            message = self._expand_restored_message(payload.get("message") or {})
            indexed.append((payload.get("message_index"), len(indexed), message))
        indexed.sort(key=lambda item: (item[0] if isinstance(item[0], int) else item[1], item[1]))
        for _, _, message in indexed:
            if message.get("role"):
                messages.append(message)
        return messages

    def _expand_restored_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        restored = dict(message or {})
        content = restored.get("content")
        if isinstance(content, dict):
            ref = content.get("payload_ref") or {}
            payload_id = ref.get("id") if isinstance(ref, dict) else None
            if payload_id:
                try:
                    restored["content"] = self.store.get_payload(payload_id)
                except Exception:
                    restored["content"] = ref.get("preview", "")
        return restored

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

    def _is_current_run(self, run_id: str) -> bool:
        return bool(run_id and self._active_run_id == run_id)

    def _scoped_event_sink(self, run_id: str):
        session = self

        class ScopedEventSink:
            def emit(self, event_type: str, payload=None, **kwargs):
                if not session._is_current_run(run_id):
                    return {}
                return session.event_bus.emit(event_type, payload, **kwargs)

            def __call__(self, event_type: str, payload=None, **kwargs):
                if not session._is_current_run(run_id):
                    return {}
                return session.event_bus(event_type, payload, **kwargs)

        return ScopedEventSink()

    def _new_agent_from_baseline(self, baseline_messages) -> RAgent:
        agent = RAgent(session_id=self.session_id)
        agent.session_id = self.session_id
        agent.messages = list(baseline_messages or [])
        return agent

    def send_message(self, text: str, *, background: bool = True) -> Dict[str, Any]:
        text = str(text or "")
        if not text.strip():
            raise ValueError("message text is empty")
        with self._lock:
            if self.running:
                raise RuntimeError("session is already running")
            run_id = new_id("run")
            self._active_run_id = run_id
            self._active_run_baseline = list(self.agent.messages)
            self.cancel_event = threading.Event()
            cancel_event = self.cancel_event
            self.last_response = None
            self.last_error = None
            self.event_bus.emit(EVENT_USER_INPUT_RECEIVED, {"content": text})
            run_agent = self.agent
            event_sink = self._scoped_event_sink(run_id)
            if background:
                self._thread = threading.Thread(
                    target=self._run_message,
                    args=(text, run_id, run_agent, event_sink, cancel_event),
                    name=f"gui-session-{self.session_id}",
                    daemon=True,
                )
                self._thread.start()
                return {"session_id": self.session_id, "status": "running"}
        # Run outside the lock for synchronous mode.
        response = self._run_message(text, run_id, run_agent, event_sink, cancel_event)
        return {"session_id": self.session_id, "status": "completed", "response": response}

    def _run_message(self, text: str, run_id: str = "", agent: Optional[RAgent] = None, event_sink=None, cancel_event=None) -> str:
        active_agent = agent or self.agent
        sink = event_sink or self.event_bus
        try:
            active_agent.model = config.get_model()
            active_agent.session_id = self.session_id
            active_agent.client = config.create_llm_client()
            response = active_agent.run_conversation(
                text,
                system_message=self.system_prompt,
                cancel_event=cancel_event or self.cancel_event,
                event_sink=sink,
            )
            if not run_id or self._is_current_run(run_id):
                self.last_response = response
            return response
        except AgentInterrupted:
            if not run_id or self._is_current_run(run_id):
                self.last_error = "interrupted"
                sink.emit(EVENT_ERROR, {"error": "interrupted"})
            return "interrupted"
        except Exception as exc:
            if not run_id or self._is_current_run(run_id):
                self.last_error = str(exc)
                sink.emit(EVENT_ERROR, {"error": str(exc)})
            raise
        finally:
            if run_id and self._is_current_run(run_id) and threading.current_thread() is self._thread:
                self._thread = None

    def interrupt(self, *, join_timeout: float = 0.0) -> Dict[str, Any]:
        self.cancel_event.set()
        with self._lock:
            thread = self._thread
            baseline = list(self._active_run_baseline)
            self._active_run_id = new_id("interrupted")
            if thread is not None and thread.is_alive() and join_timeout > 0:
                thread.join(timeout=max(0.0, float(join_timeout)))
            alive = bool(thread is not None and thread.is_alive())
            self._thread = None
            if alive:
                self.agent = self._new_agent_from_baseline(baseline)
            self.last_error = "interrupted"
            self.event_bus.emit(EVENT_ERROR, {"error": "interrupted"})
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


class LearningSession(GuiSession):
    def __init__(
        self,
        session_id: str,
        *,
        store_root: str | Path = "outputs/gui_context",
        agent: Optional[RAgent] = None,
        title: str = "",
        root_question: str = "",
        parent_session_id: Optional[str] = None,
        account_id: str = "default",
        node_kind: str = "chat",
        file_path: str = "",
        source_message_index: Optional[int] = None,
        tools_enabled: bool = True,
        restore: bool = False,
    ):
        self.title = title or _make_learning_title(root_question)
        self.root_question = root_question
        self.parent_session_id = parent_session_id
        self.last_question = root_question
        self.account_id = str(account_id or "default")
        self.node_kind = str(node_kind or "chat")
        self.file_path = str(file_path or "")
        self.source_message_index = source_message_index
        self.tools_enabled = bool(tools_enabled)
        super().__init__(session_id, store_root=store_root, agent=agent, restore=restore)
        self.store.update_metadata({
            "title": self.title,
            "root_question": self.root_question,
            "parent_session_id": self.parent_session_id,
            "account_id": self.account_id,
            "node_kind": self.node_kind,
            "file_path": self.file_path,
            "source_message_index": self.source_message_index,
            "tools_enabled": self.tools_enabled,
        })

    def _build_and_emit_system_prompt(self) -> str:
        base_prompt = build_system_prompt() + LEARNING_AGENT_PROMPT
        memory_snapshot = memory_manager.load_snapshot()
        self.event_bus.emit(EVENT_MEMORY_SNAPSHOT_LOADED, {"payload_ref": self.store.put_payload(memory_snapshot).to_dict()})
        system_prompt = base_prompt + memory_snapshot
        self.event_bus.emit(EVENT_SYSTEM_PROMPT_BUILT, {"payload_ref": self.store.put_payload(system_prompt).to_dict()})
        return system_prompt

    def _build_system_prompt_text(self) -> str:
        return build_system_prompt() + LEARNING_AGENT_PROMPT + memory_manager.load_snapshot()

    def send_message(self, text: str, *, background: bool = True) -> Dict[str, Any]:
        cleaned = str(text or "").strip()
        if not cleaned:
            raise ValueError("message text is empty")
        if not self.root_question:
            self.root_question = cleaned
        self.last_question = cleaned
        if not self.title or self.title == "新的学习问题":
            self.title = _make_learning_title(self.root_question or self.last_question)
        return super().send_message(text, background=background)

    def _run_message(self, text: str, run_id: str = "", agent: Optional[RAgent] = None, event_sink=None, cancel_event=None) -> str:
        active_agent = agent or self.agent
        sink = event_sink or self.event_bus
        try:
            active_agent.model = config.get_model()
            active_agent.session_id = self.session_id
            active_agent.client = config.create_llm_client()
            response = active_agent.run_conversation(
                text,
                system_message=self.system_prompt,
                cancel_event=cancel_event or self.cancel_event,
                event_sink=sink,
                allowed_tools=LEARNING_ALLOWED_TOOLS if self.tools_enabled else set(),
            )
            if not run_id or self._is_current_run(run_id):
                self.last_response = response
            return response
        except AgentInterrupted:
            if not run_id or self._is_current_run(run_id):
                self.last_error = "interrupted"
                sink.emit(EVENT_ERROR, {"error": "interrupted"})
            return "interrupted"
        except Exception as exc:
            if not run_id or self._is_current_run(run_id):
                self.last_error = str(exc)
                sink.emit(EVENT_ERROR, {"error": str(exc)})
            raise
        finally:
            if run_id and self._is_current_run(run_id) and threading.current_thread() is self._thread:
                self._thread = None

    def current_model_context(self) -> Dict[str, Any]:
        context = super().current_model_context()
        allowed = sorted(LEARNING_ALLOWED_TOOLS) if self.tools_enabled else []
        for module in context.get("modules", []):
            if module.get("id") == "tool_schemas":
                raw_items = module.get("items") or []
                module["label"] = f"Learning Tool Schemas（{len(allowed)} allowed）"
                module["items"] = [
                    item for item in raw_items
                    if item.get("function", {}).get("name") in set(allowed)
                ]
                module["visible_to_model"] = bool(self.tools_enabled)
                module["description"] = (
                    "学习模式白名单工具：网页检索、资料抽取、只读查看、记忆/技能检索、轻量计算、委派子任务。"
                    if self.tools_enabled
                    else "Agent 工具上下文已关闭：下一次请求不会携带 tools schema，模型不能调用工具。"
                )
            if module.get("id") == "system_prompt":
                module["label"] = "Learning System Prompt（学习模式 persona / frozen memory）"
        context["learning"] = {
            "title": self.title,
            "root_question": self.root_question,
            "last_question": self.last_question,
            "parent_session_id": self.parent_session_id,
            "allowed_tools": allowed,
            "tools_enabled": self.tools_enabled,
        }
        return context

    def resources(self) -> Dict[str, Any]:
        resources = super().resources()
        schemas = resources.get("tools", {}).get("schemas", [])
        allowed_schemas = [
            schema for schema in schemas
            if self.tools_enabled and schema.get("function", {}).get("name") in LEARNING_ALLOWED_TOOLS
        ]
        resources["tools"] = {
            "count": len(allowed_schemas),
            "schemas": allowed_schemas,
            "allowed_names": sorted(LEARNING_ALLOWED_TOOLS) if self.tools_enabled else [],
            "enabled": self.tools_enabled,
        }
        resources["learning_mode"] = {
            "title": self.title,
            "root_question": self.root_question,
            "last_question": self.last_question,
            "parent_session_id": self.parent_session_id,
            "tool_policy": "restricted" if self.tools_enabled else "disabled",
            "tools_enabled": self.tools_enabled,
        }
        return resources

    def set_tools_enabled(self, enabled: bool) -> None:
        self.tools_enabled = bool(enabled)
        self.store.update_metadata({"tools_enabled": self.tools_enabled})

    def state(self) -> Dict[str, Any]:
        state = super().state()
        state.update({
            "mode": "learning",
            "title": self.title,
            "root_question": self.root_question,
            "last_question": self.last_question,
            "parent_session_id": self.parent_session_id,
            "account_id": self.account_id,
            "node_kind": self.node_kind,
            "file_path": self.file_path,
            "source_message_index": self.source_message_index,
            "context_path": str(self.store.context_path),
            "allowed_tools": sorted(LEARNING_ALLOWED_TOOLS) if self.tools_enabled else [],
            "tools_enabled": self.tools_enabled,
        })
        return state


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


class LearningRuntimeService(AgentRuntimeService):
    def __init__(self, *, store_root: str | Path = "outputs/learning_context", max_saved_sessions: int = 200, max_session_age_hours: float = 0.0):
        super().__init__(store_root=store_root)
        self.max_saved_sessions = max(1, int(max_saved_sessions))
        self.max_session_age_hours = max(0.0, float(max_session_age_hours))
        self.cleanup_saved_sessions()
        self.restore_saved_sessions()

    def restore_saved_sessions(self) -> Dict[str, Any]:
        root = Path(self.store_root)
        if not root.exists():
            return {"restored": []}
        restored = []
        errors = []
        for context_path in sorted(root.glob("*/context.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0):
            session_dir = context_path.parent
            sid = session_dir.name
            if sid in self.sessions:
                continue
            try:
                store = ContextSnapshotStore(session_dir)
                metadata = dict(store.metadata or {})
                session = LearningSession(
                    sid,
                    store_root=self.store_root,
                    title=str(metadata.get("title") or ""),
                    root_question=str(metadata.get("root_question") or ""),
                    parent_session_id=metadata.get("parent_session_id"),
                    account_id=str(metadata.get("account_id") or "default"),
                    node_kind=str(metadata.get("node_kind") or "chat"),
                    file_path=str(metadata.get("file_path") or ""),
                    source_message_index=metadata.get("source_message_index"),
                    tools_enabled=bool(metadata.get("tools_enabled", True)),
                    restore=True,
                )
                self._hydrate_learning_session_from_events(session)
                self.sessions[sid] = session
                restored.append(sid)
            except Exception as exc:
                errors.append({"session_id": sid, "error": str(exc)})
                continue
        return {"restored": restored, "errors": errors}

    def _hydrate_learning_session_from_events(self, session: LearningSession) -> None:
        user_inputs = [
            str((event.get("payload") or {}).get("content") or "").strip()
            for event in session.store.events
            if event.get("event_type") == EVENT_USER_INPUT_RECEIVED
        ]
        meaningful_inputs = [item for item in user_inputs if item]
        if meaningful_inputs:
            if not session.root_question:
                session.root_question = meaningful_inputs[0]
            session.last_question = meaningful_inputs[-1]
        if not session.title or session.title == "新的学习问题":
            session.title = _make_learning_title(session.root_question or session.last_question)
        session.store.update_metadata({
            "title": session.title,
            "root_question": session.root_question,
            "last_question": session.last_question,
            "parent_session_id": session.parent_session_id,
            "account_id": session.account_id,
            "node_kind": session.node_kind,
            "file_path": session.file_path,
            "source_message_index": session.source_message_index,
            "tools_enabled": session.tools_enabled,
        })

    def cleanup_saved_sessions(self) -> Dict[str, Any]:
        root = Path(self.store_root)
        if not root.exists():
            return {"deleted": []}
        now = time.time()
        dirs = [path for path in root.iterdir() if path.is_dir()]
        deleted = []
        retained = []
        for path in dirs:
            try:
                age_hours = (now - path.stat().st_mtime) / 3600.0
            except OSError:
                continue
            if self.max_session_age_hours and age_hours > self.max_session_age_hours:
                shutil.rmtree(path, ignore_errors=True)
                deleted.append(path.name)
            else:
                retained.append(path)
        retained.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
        for path in retained[self.max_saved_sessions:]:
            shutil.rmtree(path, ignore_errors=True)
            deleted.append(path.name)
        return {"deleted": deleted}

    def create_session(
        self,
        *,
        session_id: Optional[str] = None,
        agent: Optional[RAgent] = None,
        title: str = "",
        root_question: str = "",
        parent_session_id: Optional[str] = None,
        account_id: str = "default",
        node_kind: str = "chat",
        file_path: str = "",
        source_message_index: Optional[int] = None,
        tools_enabled: bool = True,
        restore: bool = False,
    ) -> LearningSession:
        sid = session_id or new_id("learn")
        with self._lock:
            if sid in self.sessions:
                raise ValueError(f"session already exists: {sid}")
            session = LearningSession(
                sid,
                store_root=self.store_root,
                agent=agent,
                title=title,
                root_question=root_question,
                parent_session_id=parent_session_id,
                account_id=account_id,
                node_kind=node_kind,
                file_path=file_path,
                source_message_index=source_message_index,
                tools_enabled=tools_enabled,
                restore=restore,
            )
            self.sessions[sid] = session
            return session

    def list_sessions(self, account_id: Optional[str] = None) -> Dict[str, Any]:
        if account_id is None:
            return super().list_sessions()
        account = str(account_id or "default")
        return {
            sid: session.state()
            for sid, session in self.sessions.items()
            if getattr(session, "account_id", "default") == account
        }

    def get_or_create_file_root(self, *, account_id: str = "default", file_path: str) -> LearningSession:
        path = str(file_path or "").strip()
        if not path:
            raise ValueError("file_path is required")
        account = str(account_id or "default")
        with self._lock:
            for session in self.sessions.values():
                if (
                    getattr(session, "account_id", "default") == account
                    and getattr(session, "node_kind", "") == "file_root"
                    and getattr(session, "file_path", "") == path
                ):
                    return session
        title = f"文件: {Path(path).name}"
        return self.create_session(
            title=title,
            root_question=path,
            account_id=account,
            node_kind="file_root",
            file_path=path,
        )

    def account_roots(self, account_id: str = "default") -> Dict[str, Any]:
        account = str(account_id or "default")
        roots = [
            self._tree_node_state(session)
            for session in self.sessions.values()
            if getattr(session, "account_id", "default") == account and not getattr(session, "parent_session_id", None)
        ]
        roots.sort(key=lambda item: (item.get("node_kind") != "chat", item.get("title") or item.get("session_id")))
        return {"account_id": account, "nodes": roots}

    def child_nodes(self, session_id: str) -> Dict[str, Any]:
        parent = self.get_session(session_id)
        children = [
            self._tree_node_state(session)
            for session in self.sessions.values()
            if getattr(session, "parent_session_id", None) == session_id
        ]
        children.sort(key=lambda item: item.get("event_count", 0), reverse=True)
        return {
            "session_id": session_id,
            "account_id": getattr(parent, "account_id", "default"),
            "nodes": children,
        }

    def _tree_node_state(self, session: LearningSession) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "model": session.agent.model,
            "running": session.running,
            "event_count": len(session.event_bus.events),
            "mode": "learning",
            "title": session.title,
            "root_question": session.root_question,
            "last_question": session.last_question,
            "parent_session_id": session.parent_session_id,
            "account_id": session.account_id,
            "node_kind": session.node_kind,
            "file_path": session.file_path,
            "source_message_index": session.source_message_index,
            "context_path": str(session.store.context_path),
            "tools_enabled": session.tools_enabled,
            "child_count": sum(
                1 for item in self.sessions.values()
                if getattr(item, "parent_session_id", None) == session.session_id
            ),
        }

    def _state_with_child_count(self, session: LearningSession) -> Dict[str, Any]:
        state = session.state()
        state["child_count"] = sum(
            1 for item in self.sessions.values()
            if getattr(item, "parent_session_id", None) == session.session_id
        )
        return state

    def setback_to_message(self, session_id: str, message_index: int) -> Dict[str, Any]:
        session = self.get_session(session_id)
        index = int(message_index)
        if index < 0 or index >= len(session.agent.messages):
            raise ValueError("message_index is out of range")
        normalized = normalize_messages([session.agent.messages[index]])[0]
        if normalized.get("role") != "user":
            raise ValueError("setback target must be a user message")
        draft = normalized.get("content") or ""
        session.agent.messages = list(session.agent.messages[:index])
        self._truncate_store_from_message_index(session, index)
        session.store.replace_message_events(normalize_messages(session.agent.messages), session_id=session.session_id)
        session.event_bus.events = list(session.store.events)
        session.last_response = None
        session.last_question = ""
        return {"session": session.state(), "draft": draft}

    def fork_from_message(self, session_id: str, message_index: int) -> Dict[str, Any]:
        parent = self.get_session(session_id)
        index = int(message_index)
        if index < 0 or index >= len(parent.agent.messages):
            raise ValueError("message_index is out of range")
        normalized = normalize_messages([parent.agent.messages[index]])[0]
        if normalized.get("role") != "user":
            raise ValueError("fork target must be a user message")
        draft = normalized.get("content") or ""
        child = self.create_session(
            title=_make_learning_title(draft, limit=10),
            root_question=draft,
            parent_session_id=session_id,
            account_id=getattr(parent, "account_id", "default"),
            node_kind="chat",
            file_path=getattr(parent, "file_path", ""),
            source_message_index=index,
            tools_enabled=getattr(parent, "tools_enabled", True),
        )
        child.agent.messages = list(parent.agent.messages[:index])
        child.store.replace_message_events(normalize_messages(child.agent.messages), session_id=child.session_id)
        child.event_bus.events = list(child.store.events)
        return {"session": child.state(), "draft": draft}

    @staticmethod
    def _truncate_store_from_message_index(session: LearningSession, message_index: int) -> None:
        cutoff = None
        kept = []
        for event in session.store.events:
            payload = event.get("payload") or {}
            if event.get("event_type") == "message_appended" and payload.get("message_index") == message_index:
                cutoff = event.get("created_at")
                break
        for event in session.store.events:
            payload = event.get("payload") or {}
            event_index = payload.get("message_index")
            if isinstance(event_index, int) and event_index >= message_index:
                continue
            if cutoff is not None and event.get("created_at", 0) >= cutoff:
                continue
            kept.append(event)
        session.store.events = kept
        session.event_bus.events = list(kept)
        if hasattr(session.store, "_save"):
            session.store._save()

    def branch_session(
        self,
        source_session_id: Optional[str],
        *,
        question: str,
        title: str = "",
        background: bool = True,
    ) -> Dict[str, Any]:
        question = str(question or "").strip()
        if not question:
            raise ValueError("question is empty")
        source = None
        if source_session_id:
            source = self.get_session(source_session_id)
        session = self.create_session(
            title=title or _make_learning_title(question),
            root_question=question,
            parent_session_id=source_session_id,
            account_id=getattr(source, "account_id", "default") if source else "default",
            node_kind="chat",
            file_path=getattr(source, "file_path", "") if source and getattr(source, "node_kind", "") == "file_root" else "",
            tools_enabled=getattr(source, "tools_enabled", True) if source else True,
        )
        if source_session_id:
            session.agent.messages = self._copy_parent_context(source_session_id, system_prompt=session.system_prompt)
        send_result = session.send_message(question, background=background)
        state = session.state()
        state["send"] = send_result
        return state

    def _copy_parent_context(self, source_session_id: str, *, system_prompt: str = "") -> list:
        source = self.get_session(source_session_id)
        copied = [{"role": "system", "content": system_prompt}] if system_prompt else []
        for message in source.agent.messages:
            normalized = normalize_messages([message])[0]
            role = normalized.get("role") or ""
            if role == "tool":
                continue
            content = normalized.get("content") or ""
            if role == "system":
                continue
            copied.append({"role": role, "content": content})
        return copied

    def branch_from_selection(
        self,
        source_session_id: str,
        *,
        selected_text: str,
        action: str,
        custom_question: str = "",
        target_language: str = "",
        note_text: str = "",
        title: str = "",
        source_context: Optional[Dict[str, Any]] = None,
        background: bool = True,
    ) -> Dict[str, Any]:
        selected_text = str(selected_text or "").strip()
        if not selected_text:
            raise ValueError("selected_text is empty")
        action_key = str(action or "question").strip().lower()
        action_label, action_instruction = SELECTION_ACTIONS.get(action_key, SELECTION_ACTIONS["question"])
        question = str(custom_question or "").strip()
        target_language = str(target_language or "").strip()
        note_text = str(note_text or "").strip()
        if action_key == "question" and not question:
            raise ValueError("custom_question is required for question action")
        if action_key == "translate" and not target_language:
            raise ValueError("target_language is required for translate action")
        if action_key == "note" and not note_text:
            raise ValueError("note_text is required for note action")
        source = self.get_session(source_session_id)
        source_context = source_context or {}
        context_lines = []
        if source_context:
            source_kind = str(source_context.get("kind") or "").strip()
            source_path = str(source_context.get("path") or "").strip()
            source_location = str(source_context.get("location") or "").strip()
            if source_kind:
                context_lines.append(f"【来源类型】{source_kind}")
            if source_path:
                context_lines.append(f"【来源文件】{source_path}")
            if source_location:
                context_lines.append(f"【来源位置】{source_location}")
        context_block = ("\n".join(context_lines) + "\n\n") if context_lines else ""
        if action_key == "translate":
            initial_question = (
                f"{action_instruction}\n\n"
                f"{context_block}"
                f"【目标语言】{target_language}\n\n"
                f"【选中文本】\n{selected_text}"
            )
        elif action_key == "question":
            initial_question = (
                f"{action_instruction}\n\n"
                f"{context_block}"
                f"【选中文本】\n{selected_text}\n\n"
                f"【我的问题】\n{question}"
            )
        elif action_key == "note":
            initial_question = (
                f"{action_instruction}\n\n"
                f"{context_block}"
                f"【选中文本】\n{selected_text}\n\n"
                f"【我的手写笔记】\n{note_text}"
            )
        else:
            initial_question = (
                f"{action_instruction}\n\n"
                f"{context_block}"
                f"【选中文本】\n{selected_text}"
            )
        display_title = title or f"{action_label}: {_make_learning_title(selected_text, limit=24)}"
        session = self.create_session(
            title=display_title,
            root_question=selected_text,
            parent_session_id=source_session_id,
            account_id=getattr(source, "account_id", "default"),
            node_kind="selection",
            file_path=getattr(source, "file_path", ""),
            tools_enabled=getattr(source, "tools_enabled", True),
        )
        if action_key in {"question", "explain", "summarize", "note"}:
            session.agent.messages = self._copy_parent_context(source_session_id, system_prompt=session.system_prompt)
        send_result = session.send_message(initial_question, background=background)
        state = session.state()
        state["send"] = send_result
        state["selection"] = {
            "source_session_id": source_session_id,
            "selected_text": selected_text,
            "action": action_key,
            "action_label": action_label,
            "custom_question": question,
            "target_language": target_language,
            "note_text": note_text,
            "source_context": source_context,
        }
        return state

    def save_selection_note(
        self,
        source_session_id: str,
        *,
        selected_text: str,
        note_text: str,
        title: str = "",
        source_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selected_text = str(selected_text or "").strip()
        note_text = str(note_text or "").strip()
        if not selected_text:
            raise ValueError("selected_text is empty")
        if not note_text:
            raise ValueError("note_text is empty")
        source = self.get_session(source_session_id)
        session = self.create_session(
            title=title or f"笔记: {_make_learning_title(note_text, limit=18)}",
            root_question=note_text,
            parent_session_id=source_session_id,
            account_id=getattr(source, "account_id", "default"),
            node_kind="note",
            file_path=getattr(source, "file_path", ""),
            tools_enabled=getattr(source, "tools_enabled", True),
        )
        note_message = (
            "【手写笔记】\n"
            f"{note_text}\n\n"
            "【关联选中文本】\n"
            f"{selected_text}"
        )
        source_context = source_context or {}
        if source_context:
            note_message += "\n\n【来源】\n" + "\n".join(
                f"{key}: {value}" for key, value in source_context.items() if value
            )
        session.agent.messages = [{"role": "user", "content": note_message}]
        session.store.replace_message_events(normalize_messages(session.agent.messages), session_id=session.session_id)
        session.event_bus.events = list(session.store.events)
        state = session.state()
        state["selection"] = {
            "source_session_id": source_session_id,
            "selected_text": selected_text,
            "action": "note",
            "action_label": "笔记",
            "note_text": note_text,
            "source_context": source_context,
        }
        return state

    def set_tools_enabled(self, session_id: str, enabled: bool) -> Dict[str, Any]:
        session = self.get_session(session_id)
        session.set_tools_enabled(enabled)
        return session.state()

    def descendant_session_ids(self, session_id: str) -> list[str]:
        descendants = []
        stack = [session_id]
        while stack:
            current = stack.pop()
            children = [
                sid for sid, session in self.sessions.items()
                if getattr(session, "parent_session_id", None) == current
            ]
            descendants.extend(children)
            stack.extend(children)
        return descendants

    def delete_subtree(self, session_id: str) -> Dict[str, Any]:
        ids = [session_id] + self.descendant_session_ids(session_id)
        deleted = []
        with self._lock:
            for sid in ids:
                session = self.sessions.pop(sid, None)
                if session is None:
                    continue
                try:
                    session.shutdown(join_timeout=0.2)
                except Exception:
                    pass
                try:
                    shutil.rmtree(session.store.base_dir, ignore_errors=True)
                except Exception:
                    pass
                deleted.append(sid)
        if not deleted:
            raise KeyError(f"session not found: {session_id}")
        return {"deleted": deleted}
