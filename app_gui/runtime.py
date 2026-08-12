from __future__ import annotations

import json
import os
import re
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _memory_for_system_prompt() -> str:
    """返回要拼进 system prompt 的 memory 快照。

    默认 'system' 模式：返回冻结快照（行为不变）。'hidden_user' 模式：返回空串，
    memory 改由 durable context 以隐藏 user 段注入（权限降级，见 03/04 文档）。
    """
    if config.get_memory_injection_mode() == "hidden_user":
        return ""
    return memory_manager.load_snapshot()

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


def _safe_todo_session_id(session_id: str) -> str:
    raw = str(session_id or "").strip()
    if not raw or raw == "default":
        return ""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe[:80] or ""


def _coerce_gui_session_id(session_id: Optional[str], *, prefix: str) -> str:
    """Return a stable, non-legacy GUI session id.

    GUI sessions must never use the legacy ``default`` todo scope: every browser
    window / learning branch owns an explicit session id so Agent tool injection,
    delegate_task and TodoBoardPreview all point at the same per-session todo
    file.  Caller-provided ids are preserved after the same sanitization used by
    todo_manage; blank/default ids are replaced with a new window-scoped id.
    """
    sid = _safe_todo_session_id(str(session_id or ""))
    return sid or new_id(prefix)


def _apply_gui_iteration_budget(agent: RAgent) -> RAgent:
    max_iterations = config.get_gui_max_iterations()
    agent.max_iterations = max_iterations
    agent._default_max_iterations = max_iterations
    return agent


def _session_time_bounds(store: ContextSnapshotStore) -> tuple[float, float]:
    """Return (created_at, last_activity_at) for a persisted GUI session.

    Prefer event timestamps because they reflect conversation activity and survive
    process restarts. Fall back to context.json mtime for old/empty sessions.
    """
    event_times = []
    for event in getattr(store, "events", []) or []:
        try:
            event_times.append(float(event.get("created_at") or 0))
        except (TypeError, ValueError):
            continue
    try:
        mtime = float(store.context_path.stat().st_mtime) if store.context_path.exists() else 0.0
    except OSError:
        mtime = 0.0
    created_at = min(event_times) if event_times else mtime
    last_activity_at = max(event_times) if event_times else mtime
    return created_at, last_activity_at


def _session_recent_sort_key(item: Dict[str, Any]) -> tuple[float, float, str]:
    def _num(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    return (
        _num(item.get("last_activity_at")),
        _num(item.get("updated_at")),
        str(item.get("session_id") or item.get("title") or ""),
    )


def _session_todo_board(session_id: str) -> Optional[Dict[str, Any]]:
    """Return a compact, GUI-friendly todo board snapshot for this session.

    The todo_manage tool stores per-session boards in sandbox/todo_lists. Reading
    it from session.state() lets the Cockpit polling loop refresh progress even
    when the agent is still inside a long tool/delegate call and no final chat
    message has been appended yet.
    """
    sid = _safe_todo_session_id(session_id)
    if not sid:
        return None
    path = PROJECT_ROOT / "sandbox" / "todo_lists" / f"todo_list_{sid}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"exists": True, "path": str(path), "error": "todo board is not readable"}
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    if not isinstance(tasks, list):
        tasks = []
    statuses = ["pending", "in_progress", "needs_split", "blocked", "completed", "failed", "cancelled"]
    counts = {status: 0 for status in statuses}
    compact_tasks = []
    max_updated_at = 0
    task_by_id = {}
    for raw in tasks:
        if not isinstance(raw, dict):
            continue
        task = dict(raw)
        tid = str(task.get("id") or "")
        if tid:
            task_by_id[tid] = task
        status = str(task.get("status") or "pending")
        if status not in counts:
            counts[status] = 0
        counts[status] += 1
        try:
            max_updated_at = max(max_updated_at, int(float(task.get("updated_at") or 0)))
        except Exception:
            pass
        compact_tasks.append({
            "id": tid,
            "description": str(task.get("description") or ""),
            "parent_id": task.get("parent_id"),
            "dependencies": task.get("dependencies") if isinstance(task.get("dependencies"), list) else [],
            "status": status,
            "assigned_to": str(task.get("assigned_to") or (task.get("claim") or {}).get("worker_id") or ""),
            "deliverable": str(task.get("deliverable") or ""),
            "updated_at": task.get("updated_at"),
        })

    def deps_met(task: Dict[str, Any]) -> bool:
        for dep in task.get("dependencies") or []:
            if (task_by_id.get(str(dep)) or {}).get("status") != "completed":
                return False
        return True

    parent_ids = {str(task.get("parent_id")) for task in compact_tasks if task.get("parent_id")}
    ready = [
        task.get("id")
        for task in compact_tasks
        if task.get("id") and task.get("status") == "pending" and task.get("id") not in parent_ids and deps_met(task)
    ]
    total = len(compact_tasks)
    completed = counts.get("completed", 0)
    return {
        "exists": True,
        "session_id": sid,
        "path": str(path),
        "version": data.get("version", 2) if isinstance(data, dict) else 2,
        "total": total,
        "completed": completed,
        "progress": (completed / total) if total else 0,
        "status_counts": counts,
        "ready_to_execute": ready,
        "tasks": compact_tasks[:40],
        "truncated": len(compact_tasks) > 40,
        "updated_at": max_updated_at,
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
    def __init__(self, session_id: str, *, store_root: str | Path = "outputs/gui_context", agent: Optional[RAgent] = None, restore: bool = False, base_dir: Optional[str | Path] = None):
        self.session_id = session_id
        self.store = ContextSnapshotStore(Path(base_dir) if base_dir is not None else Path(store_root) / session_id)
        self.event_bus = ContextEventBus(store=self.store, session_id=session_id)
        if restore:
            self.event_bus.events = list(self.store.events)
        self.agent = _apply_gui_iteration_budget(agent or RAgent(session_id=session_id, max_iterations=config.get_gui_max_iterations()))
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
        return build_system_prompt() + SELF_EVOLUTION_PROMPT + _memory_for_system_prompt()

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
        restored = self._expand_payload_refs(dict(message or {}))
        for tool_call in restored.get("tool_calls") or []:
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                function["arguments"] = json.dumps(arguments, ensure_ascii=False, default=str)
        return restored

    def _expand_payload_refs(self, value: Any) -> Any:
        if isinstance(value, dict):
            ref = value.get("payload_ref")
            payload_id = ref.get("id") if isinstance(ref, dict) else None
            if payload_id:
                try:
                    return self.store.get_payload(payload_id)
                except Exception:
                    return ref.get("preview", "")
            return {key: self._expand_payload_refs(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._expand_payload_refs(item) for item in value]
        return value

    def _build_and_emit_system_prompt(self) -> str:
        base_prompt = build_system_prompt() + SELF_EVOLUTION_PROMPT
        memory_snapshot = memory_manager.load_snapshot()
        self.event_bus.emit(EVENT_MEMORY_SNAPSHOT_LOADED, {"payload_ref": self.store.put_payload(memory_snapshot).to_dict()})
        system_prompt = base_prompt + (memory_snapshot if config.get_memory_injection_mode() != "hidden_user" else "")
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
        agent = RAgent(session_id=self.session_id, max_iterations=config.get_gui_max_iterations())
        agent.session_id = self.session_id
        agent.messages = list(baseline_messages or [])
        return _apply_gui_iteration_budget(agent)

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

    def continue_after_truncation(self, *, extra_iterations: Optional[int] = None, background: bool = True) -> Dict[str, Any]:
        if extra_iterations is None:
            extra_iterations = config.get_gui_max_iterations()
        extra_iterations = int(extra_iterations)
        with self._lock:
            if self.running:
                raise RuntimeError("session is already running")
            if not self.agent.is_truncated():
                raise RuntimeError("session is not truncated")
            run_id = new_id("continue")
            self._active_run_id = run_id
            self._active_run_baseline = list(self.agent.messages)
            self.cancel_event = threading.Event()
            cancel_event = self.cancel_event
            self.last_response = None
            self.last_error = None
            run_agent = self.agent
            event_sink = self._scoped_event_sink(run_id)
            if background:
                self._thread = threading.Thread(
                    target=self._run_continue_after_truncation,
                    args=(extra_iterations, run_id, run_agent, event_sink, cancel_event),
                    name=f"gui-session-{self.session_id}-continue",
                    daemon=True,
                )
                self._thread.start()
                return {"session_id": self.session_id, "status": "running", "extra_iterations": extra_iterations}
        response = self._run_continue_after_truncation(extra_iterations, run_id, run_agent, event_sink, cancel_event)
        return {"session_id": self.session_id, "status": "completed", "response": response, "extra_iterations": extra_iterations}

    def _run_continue_after_truncation(self, extra_iterations: int, run_id: str = "", agent: Optional[RAgent] = None, event_sink=None, cancel_event=None) -> str:
        active_agent = agent or self.agent
        sink = event_sink or self.event_bus
        try:
            active_agent.model = config.get_model()
            active_agent.session_id = self.session_id
            active_agent.client = config.create_llm_client()
            response = active_agent.continue_after_truncation(
                extra_iterations,
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
        created_at, last_activity_at = _session_time_bounds(self.store)
        return {
            "session_id": self.session_id,
            "model": self.agent.model,
            "running": self.running,
            "truncated": self.agent.is_truncated(),
            "max_iterations": self.agent.max_iterations,
            "event_count": self.store.event_count(),
            "created_at": created_at,
            "updated_at": last_activity_at,
            "last_activity_at": last_activity_at,
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
            "todo_board": _session_todo_board(self.session_id),
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
        selection: Optional[Dict[str, Any]] = None,
        restore: bool = False,
        base_dir: Optional[str | Path] = None,
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
        self.selection = dict(selection or {})
        super().__init__(session_id, store_root=store_root, agent=agent, restore=restore, base_dir=base_dir)
        if not self.selection and isinstance(self.store.metadata.get("selection"), dict):
            self.selection = dict(self.store.metadata.get("selection") or {})
        self.store.update_metadata({
            "title": self.title,
            "root_question": self.root_question,
            "parent_session_id": self.parent_session_id,
            "account_id": self.account_id,
            "node_kind": self.node_kind,
            "file_path": self.file_path,
            "source_message_index": self.source_message_index,
            "tools_enabled": self.tools_enabled,
            "selection": self.selection,
        })

    def _build_and_emit_system_prompt(self) -> str:
        base_prompt = build_system_prompt() + LEARNING_AGENT_PROMPT
        memory_snapshot = memory_manager.load_snapshot()
        self.event_bus.emit(EVENT_MEMORY_SNAPSHOT_LOADED, {"payload_ref": self.store.put_payload(memory_snapshot).to_dict()})
        system_prompt = base_prompt + (memory_snapshot if config.get_memory_injection_mode() != "hidden_user" else "")
        self.event_bus.emit(EVENT_SYSTEM_PROMPT_BUILT, {"payload_ref": self.store.put_payload(system_prompt).to_dict()})
        return system_prompt

    def _build_system_prompt_text(self) -> str:
        return build_system_prompt() + LEARNING_AGENT_PROMPT + _memory_for_system_prompt()

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

    def _run_continue_after_truncation(self, extra_iterations: int, run_id: str = "", agent: Optional[RAgent] = None, event_sink=None, cancel_event=None) -> str:
        active_agent = agent or self.agent
        sink = event_sink or self.event_bus
        try:
            active_agent.model = config.get_model()
            active_agent.session_id = self.session_id
            active_agent.client = config.create_llm_client()
            response = active_agent.continue_after_truncation(
                extra_iterations,
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
        parent_board = _session_todo_board(self.parent_session_id) if self.parent_session_id else None
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
            "selection": self.selection,
            "parent_todo_board": parent_board,
        })
        return state


class AgentRuntimeService:
    def __init__(self, *, store_root: str | Path = "outputs/gui_context"):
        self.store_root = Path(store_root)
        self.sessions: Dict[str, GuiSession] = {}
        self._lock = threading.Lock()

    def create_session(self, *, session_id: Optional[str] = None, agent: Optional[RAgent] = None) -> GuiSession:
        sid = _coerce_gui_session_id(session_id, prefix="gui")
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

    def continue_after_truncation(self, session_id: str, *, extra_iterations: Optional[int] = None, background: bool = True) -> Dict[str, Any]:
        return self.get_session(session_id).continue_after_truncation(extra_iterations=extra_iterations, background=background)

    def interrupt(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).interrupt()

    def shutdown_session(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).shutdown()

    def resources(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).resources()

    def current_model_context(self, session_id: str) -> Dict[str, Any]:
        return self.get_session(session_id).current_model_context()


class LearningRuntimeService(AgentRuntimeService):
    # Children live physically under their parent so on-disk layout mirrors the
    # conversation tree: <root>/<id>/children/<child_id>/children/<grandchild>/...
    CHILDREN_DIRNAME = "children"

    def __init__(
        self,
        *,
        store_root: str | Path = "outputs/learning_context",
        max_saved_sessions: int = 200,
        max_session_age_hours: float = 0.0,
        max_context_bytes: Optional[int] = None,
    ):
        super().__init__(store_root=store_root)
        self.max_saved_sessions = max(1, int(max_saved_sessions))
        self.max_session_age_hours = max(0.0, float(max_session_age_hours))
        if max_context_bytes is None:
            try:
                max_context_bytes = int(os.environ.get("R_AGENT_COCKPIT_RESTORE_MAX_CONTEXT_MB", "50")) * 1024 * 1024
            except ValueError:
                max_context_bytes = 50 * 1024 * 1024
        self.max_context_bytes = max(1, int(max_context_bytes))
        # session_id -> on-disk directory (Path). Authoritative location map so
        # children can be created/deleted relative to their parent's folder.
        self._dirs: Dict[str, Path] = {}
        self._archive_legacy_flat_store()
        self.cleanup_saved_sessions()
        self.restore_saved_sessions()

    def _archive_legacy_flat_store(self) -> None:
        """One-time: move a pre-nesting flat store aside so we start fresh.

        Legacy layout kept every session as a flat sibling under store_root with
        parent/child only in metadata. The nested layout is incompatible, so we
        rename the old tree to a timestamped ``.flat.bak-*`` sibling instead of
        deleting it (data is preserved, just not shown).
        """
        root = Path(self.store_root)
        if not root.exists():
            return
        session_dirs = [p for p in root.iterdir() if p.is_dir() and (p / "context.json").exists()]
        if not session_dirs:
            return
        # A dir is a "child" in the new layout only if it sits under a
        # ``children/`` folder. If none do but parent_session_id links exist,
        # this is the legacy flat store.
        looks_nested = any(p.parent.name == self.CHILDREN_DIRNAME for p in root.rglob("context.json"))
        if looks_nested:
            return
        has_parent_links = False
        for p in session_dirs:
            try:
                data = json.loads((p / "context.json").read_text(encoding="utf-8"))
                if (data.get("metadata") or {}).get("parent_session_id"):
                    has_parent_links = True
                    break
            except Exception:
                continue
        if not has_parent_links:
            return
        backup = root.with_name(f"{root.name}.flat.bak-{int(time.time())}")
        try:
            root.rename(backup)
        except OSError:
            return

    def _session_dir(self, session_id: str, parent_session_id: Optional[str]) -> Path:
        """Resolve the on-disk directory for a session given its parent."""
        if parent_session_id and parent_session_id in self._dirs:
            return self._dirs[parent_session_id] / self.CHILDREN_DIRNAME / session_id
        return Path(self.store_root) / session_id

    def restore_saved_sessions(self) -> Dict[str, Any]:
        root = Path(self.store_root)
        if not root.exists():
            return {"restored": []}
        restored = []
        errors = []
        # Read every context.json (at any depth), then instantiate parents
        # before children so nested base_dirs resolve through self._dirs.
        discovered = []
        for context_path in root.rglob("context.json"):
            session_dir = context_path.parent
            if session_dir.name == self.CHILDREN_DIRNAME:
                continue
            sid = session_dir.name
            try:
                size = context_path.stat().st_size
                if size > self.max_context_bytes:
                    errors.append({
                        "session_id": sid,
                        "error": f"skipped oversized context.json ({size} bytes > {self.max_context_bytes} bytes)",
                    })
                    continue
                data = json.loads(context_path.read_text(encoding="utf-8"))
                metadata = dict(data.get("metadata") or {})
            except Exception as exc:
                errors.append({"session_id": sid, "error": str(exc)})
                continue
            discovered.append((sid, session_dir, metadata))

        pending = {sid: (session_dir, metadata) for sid, session_dir, metadata in discovered}

        def _restore_one(sid: str) -> None:
            if sid in self.sessions or sid not in pending:
                return
            session_dir, metadata = pending[sid]
            parent_id = metadata.get("parent_session_id")
            if parent_id and parent_id in pending and parent_id not in self.sessions:
                _restore_one(parent_id)
            try:
                session = LearningSession(
                    sid,
                    store_root=self.store_root,
                    base_dir=session_dir,
                    title=str(metadata.get("title") or ""),
                    root_question=str(metadata.get("root_question") or ""),
                    parent_session_id=parent_id,
                    account_id=str(metadata.get("account_id") or "default"),
                    node_kind=str(metadata.get("node_kind") or "chat"),
                    file_path=str(metadata.get("file_path") or ""),
                    source_message_index=metadata.get("source_message_index"),
                    tools_enabled=bool(metadata.get("tools_enabled", True)),
                    selection=metadata.get("selection") if isinstance(metadata.get("selection"), dict) else None,
                    restore=True,
                )
                self._hydrate_learning_session_from_events(session)
                self.sessions[sid] = session
                self._dirs[sid] = Path(session_dir)
                restored.append(sid)
            except Exception as exc:
                errors.append({"session_id": sid, "error": str(exc)})

        for sid, _dir, _meta in sorted(
            discovered,
            key=lambda item: item[1].stat().st_mtime if item[1].exists() else 0,
        ):
            _restore_one(sid)
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
            "selection": session.selection,
        })

    def cleanup_saved_sessions(self) -> Dict[str, Any]:
        """Age/count-based cleanup that only ever removes whole root subtrees.

        Operating on top-level root dirs keeps nested children with their parent
        (removing a root removes its whole subtree folder in one rmtree).
        """
        root = Path(self.store_root)
        if not root.exists():
            return {"deleted": []}
        now = time.time()
        root_dirs = [
            path for path in root.iterdir()
            if path.is_dir() and path.name != self.CHILDREN_DIRNAME and (path / "context.json").exists()
        ]
        deleted = []
        retained = []
        for path in root_dirs:
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
        selection: Optional[Dict[str, Any]] = None,
        restore: bool = False,
    ) -> LearningSession:
        sid = _coerce_gui_session_id(session_id, prefix="learn")
        with self._lock:
            if sid in self.sessions:
                raise ValueError(f"session already exists: {sid}")
            base_dir = self._session_dir(sid, parent_session_id)
            session = LearningSession(
                sid,
                store_root=self.store_root,
                base_dir=base_dir,
                agent=agent,
                title=title,
                root_question=root_question,
                parent_session_id=parent_session_id,
                account_id=account_id,
                node_kind=node_kind,
                file_path=file_path,
                source_message_index=source_message_index,
                tools_enabled=tools_enabled,
                selection=selection,
                restore=restore,
            )
            self.sessions[sid] = session
            self._dirs[sid] = Path(base_dir)
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
        roots.sort(key=_session_recent_sort_key, reverse=True)
        return {"account_id": account, "nodes": roots}

    def child_nodes(self, session_id: str) -> Dict[str, Any]:
        parent = self.get_session(session_id)
        children = [
            self._tree_node_state(session)
            for session in self.sessions.values()
            if getattr(session, "parent_session_id", None) == session_id
        ]
        children.sort(key=_session_recent_sort_key, reverse=True)
        return {
            "session_id": session_id,
            "account_id": getattr(parent, "account_id", "default"),
            "nodes": children,
        }

    def _tree_node_state(self, session: LearningSession) -> Dict[str, Any]:
        created_at, last_activity_at = _session_time_bounds(session.store)
        return {
            "session_id": session.session_id,
            "model": session.agent.model,
            "running": session.running,
            "event_count": session.store.event_count(),
            "created_at": created_at,
            "updated_at": last_activity_at,
            "last_activity_at": last_activity_at,
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
            "selection": session.selection,
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
        # Any branch/fork/selection/note child anchored at or after the
        # truncation point loses its anchor; delete those subtrees so the tree
        # and on-disk folders stay consistent with the shortened context.
        orphaned = [
            child.session_id
            for child in list(self.sessions.values())
            if getattr(child, "parent_session_id", None) == session_id
            and isinstance(getattr(child, "source_message_index", None), int)
            and child.source_message_index >= index
        ]
        deleted: list[str] = []
        for child_id in orphaned:
            if child_id in self.sessions:
                try:
                    deleted.extend(self.delete_subtree(child_id).get("deleted", []))
                except KeyError:
                    pass
        session.agent.messages = list(session.agent.messages[:index])
        self._truncate_store_from_message_index(session, index)
        session.store.replace_message_events(normalize_messages(session.agent.messages), session_id=session.session_id)
        session.event_bus.events = list(session.store.events)
        session.last_response = None
        session.last_question = ""
        return {"session": session.state(), "draft": draft, "deleted": deleted}

    def fork_from_message(self, session_id: str, message_index: int, *, child_session_id: Optional[str] = None) -> Dict[str, Any]:
        parent = self.get_session(session_id)
        index = int(message_index)
        if index < 0 or index >= len(parent.agent.messages):
            raise ValueError("message_index is out of range")
        normalized = normalize_messages([parent.agent.messages[index]])[0]
        if normalized.get("role") != "user":
            raise ValueError("fork target must be a user message")
        draft = normalized.get("content") or ""
        child = self.create_session(
            session_id=child_session_id,
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
        child_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        question = str(question or "").strip()
        if not question:
            raise ValueError("question is empty")
        source = None
        if source_session_id:
            source = self.get_session(source_session_id)
        session = self.create_session(
            session_id=child_session_id,
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
        child_session_id: Optional[str] = None,
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
        selection_meta = {
            "source_session_id": source_session_id,
            "selected_text": selected_text,
            "action": action_key,
            "action_label": action_label,
            "custom_question": question,
            "target_language": target_language,
            "note_text": note_text,
            "source_context": source_context,
        }
        display_title = title or f"{action_label}: {_make_learning_title(selected_text, limit=24)}"
        session = self.create_session(
            session_id=child_session_id,
            title=display_title,
            root_question=selected_text,
            parent_session_id=source_session_id,
            account_id=getattr(source, "account_id", "default"),
            node_kind="selection",
            file_path=getattr(source, "file_path", ""),
            source_message_index=len(source.agent.messages),
            tools_enabled=getattr(source, "tools_enabled", True),
            selection=selection_meta,
        )
        if action_key in {"question", "explain", "summarize", "note"}:
            session.agent.messages = self._copy_parent_context(source_session_id, system_prompt=session.system_prompt)
        send_result = session.send_message(initial_question, background=background)
        state = session.state()
        state["send"] = send_result
        state["selection"] = selection_meta
        return state

    def save_selection_note(
        self,
        source_session_id: str,
        *,
        selected_text: str,
        note_text: str,
        title: str = "",
        source_context: Optional[Dict[str, Any]] = None,
        child_session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        selected_text = str(selected_text or "").strip()
        note_text = str(note_text or "").strip()
        if not selected_text:
            raise ValueError("selected_text is empty")
        if not note_text:
            raise ValueError("note_text is empty")
        source = self.get_session(source_session_id)
        source_context = source_context or {}
        selection_meta = {
            "source_session_id": source_session_id,
            "selected_text": selected_text,
            "action": "note",
            "action_label": "笔记",
            "note_text": note_text,
            "source_context": source_context,
        }
        session = self.create_session(
            session_id=child_session_id,
            title=title or f"笔记: {_make_learning_title(note_text, limit=18)}",
            root_question=note_text,
            parent_session_id=source_session_id,
            account_id=getattr(source, "account_id", "default"),
            node_kind="note",
            file_path=getattr(source, "file_path", ""),
            source_message_index=len(source.agent.messages),
            tools_enabled=getattr(source, "tools_enabled", True),
            selection=selection_meta,
        )
        note_message = (
            "【手写笔记】\n"
            f"{note_text}\n\n"
            "【关联选中文本】\n"
            f"{selected_text}"
        )
        if source_context:
            note_message += "\n\n【来源】\n" + "\n".join(
                f"{key}: {value}" for key, value in source_context.items() if value
            )
        session.agent.messages = [{"role": "user", "content": note_message}]
        session.store.replace_message_events(normalize_messages(session.agent.messages), session_id=session.session_id)
        session.event_bus.events = list(session.store.events)
        state = session.state()
        state["selection"] = selection_meta
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

    @staticmethod
    def _normalize_workspace_path(path: str) -> str:
        normalized = str(path or "").strip().replace("\\", "/")
        while normalized.startswith("/"):
            normalized = normalized[1:]
        parts = [part for part in normalized.split("/") if part not in {"", "."}]
        return "/".join(parts)

    @staticmethod
    def _selection_source_path(session: LearningSession) -> str:
        selection = getattr(session, "selection", None)
        if not isinstance(selection, dict):
            return ""
        source_context = selection.get("source_context")
        if not isinstance(source_context, dict):
            return ""
        return str(source_context.get("path") or "")

    @staticmethod
    def _workspace_path_matches(candidate: str, deleted_path: str, *, is_directory: bool) -> bool:
        candidate_path = LearningRuntimeService._normalize_workspace_path(candidate)
        target_path = LearningRuntimeService._normalize_workspace_path(deleted_path)
        if not candidate_path or not target_path:
            return False
        if candidate_path == target_path:
            return True
        if is_directory:
            return candidate_path.startswith(target_path.rstrip("/") + "/")
        return False

    def _session_dir_is_managed(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        session_dir = Path(session.store.base_dir) if session is not None else self._dirs.get(session_id)
        if session_dir is None:
            return False
        try:
            root = Path(self.store_root).resolve()
            candidate = Path(session_dir).resolve()
        except Exception:
            return False
        return candidate == root or root in candidate.parents

    def delete_sessions_for_workspace_path(self, workspace_path: str, *, is_directory: bool = False) -> Dict[str, Any]:
        """Delete learning session subtrees associated with a workspace file/path.

        Matching is intentionally metadata-only: a session is associated when its
        ``file_path`` or ``selection.source_context.path`` equals the deleted
        workspace path.  For directory deletions, descendants under
        ``path + '/'`` also match.  Only roots whose on-disk context directory is
        inside this service's managed ``store_root`` are passed to
        :meth:`delete_subtree`; the workspace deletion path is never used as a
        filesystem deletion target.
        """
        target_path = self._normalize_workspace_path(workspace_path)
        if not target_path:
            return {"deleted_learning_sessions": []}

        with self._lock:
            matched = []
            for sid, session in list(self.sessions.items()):
                paths = [
                    getattr(session, "file_path", ""),
                    self._selection_source_path(session),
                ]
                if any(self._workspace_path_matches(path, target_path, is_directory=is_directory) for path in paths):
                    matched.append(sid)

            matched_set = set(matched)
            delete_roots = []
            for sid in matched:
                parent_id = getattr(self.sessions.get(sid), "parent_session_id", None)
                has_matched_ancestor = False
                while parent_id:
                    if parent_id in matched_set:
                        has_matched_ancestor = True
                        break
                    parent = self.sessions.get(parent_id)
                    parent_id = getattr(parent, "parent_session_id", None) if parent is not None else None
                if not has_matched_ancestor and self._session_dir_is_managed(sid):
                    delete_roots.append(sid)

        deleted: list[str] = []
        for sid in delete_roots:
            if sid not in self.sessions:
                continue
            try:
                deleted.extend(self.delete_subtree(sid).get("deleted", []))
            except KeyError:
                continue
        return {"deleted_learning_sessions": deleted}

    def delete_subtree(self, session_id: str) -> Dict[str, Any]:
        ids = [session_id] + self.descendant_session_ids(session_id)
        deleted = []
        # Children are nested under the root's folder, so removing the root
        # directory removes the whole subtree in one shot; capture it first.
        root_session = self.sessions.get(session_id)
        root_dir = root_session.store.base_dir if root_session is not None else self._dirs.get(session_id)
        with self._lock:
            for sid in ids:
                session = self.sessions.pop(sid, None)
                self._dirs.pop(sid, None)
                if session is None:
                    continue
                try:
                    session.shutdown(join_timeout=0.2)
                except Exception:
                    pass
                deleted.append(sid)
            if root_dir is not None:
                try:
                    shutil.rmtree(root_dir, ignore_errors=True)
                except Exception:
                    pass
        if not deleted:
            raise KeyError(f"session not found: {session_id}")
        return {"deleted": deleted}
