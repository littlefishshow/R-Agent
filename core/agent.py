import time
import random
import json
import threading
import re
from pathlib import Path
from core import config
from core.context_control import compress_messages, resolve_context_window, should_compress_context
from tools.registry import registry
from core.context.tool_result_storage import enforce_turn_budget, maybe_persist_tool_result
from core.sandbox_cleanup import maybe_cleanup_sandbox
from app_gui.normalizer import build_llm_request_snapshot, normalize_message
from app_gui.schemas import (
    EVENT_LLM_REQUEST_SNAPSHOT,
    EVENT_LLM_RESPONSE_RECEIVED,
    EVENT_MESSAGE_APPENDED,
    EVENT_TOOL_CALL_FINISHED,
    EVENT_TOOL_CALL_STARTED,
    EVENT_TOOL_RESULT_APPENDED,
    EVENT_TRUNCATION_FORCED,
)


# 标记：当一次 run 因迭代上限而被强制收尾时，agent 把这个标记记到自身
# 状态上，CLI 层可据此询问用户是否扩展预算继续推进。
_TRUNCATED_FLAG = "_truncated"
_PENDING_USER_MSG = "_pending_user_message"
TOKEN_USAGE_UNAVAILABLE = "unavailable"
LARGE_MESSAGE_COMPLETION_TOKEN_THRESHOLD = 50_000
LONG_CONTEXT_OUTPUT_DIR = Path("outputs") / "long_context"


class AgentInterrupted(Exception):
    """用户主动中断当前 Agent 运行。"""





def _safe_tool_session_id(session_id) -> str:
    """Normalize tool session ids for inheritance decisions.

    ``default`` and blank values are legacy aliases for the unscoped todo board,
    not an explicit GUI/CLI session.  Treat them as missing so Agent-level
    injection can inherit the current RAgent.session_id.
    """
    raw = str(session_id or "").strip()
    if not raw or raw == "default":
        return ""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe[:80] or ""


def _should_inherit_current_session(explicit_session_id, current_session_id) -> bool:
    return bool(_safe_tool_session_id(current_session_id)) and not _safe_tool_session_id(explicit_session_id)


def _inject_current_session(args, current_session_id) -> bool:
    if not isinstance(args, dict):
        return False
    if _should_inherit_current_session(args.get("session_id"), current_session_id):
        args["session_id"] = _safe_tool_session_id(current_session_id)
        return True
    return False

def _emit_event(event_sink, event_type: str, payload=None, **kwargs) -> None:
    if event_sink is None:
        return
    data = dict(payload or {})
    data.update(kwargs)
    try:
        if hasattr(event_sink, "emit"):
            event_sink.emit(event_type, data)
        else:
            event_sink(event_type, data)
    except TypeError:
        event_sink({"event_type": event_type, "payload": data})
    except Exception:
        # Observability must never break the core Agent loop.
        pass

def _is_cancelled(cancel_event=None) -> bool:
    """兼容 threading.Event 等带 is_set() 的取消信号。"""
    return bool(cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())


def _msg_get(message, key, default=None):
    """Read a field from a message that may be a dict or an SDK object."""
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _tool_call_ids(message) -> list:
    """Return the tool_call ids declared on an assistant message (if any)."""
    tool_calls = _msg_get(message, "tool_calls", None) or []
    ids = []
    for tc in tool_calls:
        tc_id = _msg_get(tc, "id", None)
        if tc_id:
            ids.append(tc_id)
    return ids


def sanitize_tool_call_messages(messages: list) -> list:
    """Drop dangling tool-call artifacts that make the chat API reject a request.

    The OpenAI-compatible API requires that every assistant message with
    ``tool_calls`` is immediately followed by one ``tool`` message per
    ``tool_call_id``. A run that dies mid tool-call (interrupt, crash, restart)
    can leave an assistant ``tool_calls`` message with no matching tool result,
    or an orphan ``tool`` message. Either shape makes every subsequent turn
    fail with "must be followed by tool messages responding to each
    tool_call_id". This repairs the list by:

    - dropping assistant messages whose declared tool_call_ids are not all
      answered by following tool messages before the next assistant/user turn;
    - dropping ``tool`` messages whose tool_call_id was never declared.
    """
    if not messages:
        return messages

    # Which tool_call_ids actually have a tool response somewhere later.
    answered = set()
    for message in messages:
        if _msg_get(message, "role") == "tool":
            tcid = _msg_get(message, "tool_call_id")
            if tcid:
                answered.add(tcid)

    cleaned = []
    valid_ids = set()
    for message in messages:
        role = _msg_get(message, "role")
        if role == "assistant" and _tool_call_ids(message):
            ids = _tool_call_ids(message)
            if all(tc_id in answered for tc_id in ids):
                cleaned.append(message)
                valid_ids.update(ids)
            # else: dangling assistant tool_calls -> drop it entirely.
            continue
        if role == "tool":
            tcid = _msg_get(message, "tool_call_id")
            if tcid and tcid not in valid_ids:
                # Orphan tool result (its assistant call was dropped/never existed).
                continue
        cleaned.append(message)
    return cleaned


def _is_transient_error(exc: Exception) -> bool:
    """
    判断异常是否属于"瞬时错误"，值得重试。
    覆盖：超时、连接错误、限流(429)、服务端错误(5xx)。
    内容策略/鉴权/参数错误(4xx，非 429) 一律不重试 —— 重试也不会成功，
    只会浪费 token 与时间。
    """
    # openai SDK 的特定异常类型（按需懒导入避免硬依赖）
    try:
        from openai import APITimeoutError, APIConnectionError, RateLimitError, InternalServerError  # type: ignore
        if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
            return True
    except ImportError:
        pass

    # 兜底：通过 status_code 属性判断
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status is None:
        # 部分 SDK 把 response 挂在 .response.status_code 上
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        if status == 429 or 500 <= status < 600:
            return True
        return False

    # 无法判断类型，但消息里出现典型瞬时关键字 —— 保守地重试一次
    msg = str(exc).lower()
    transient_markers = ("timeout", "timed out", "connection reset",
                         "connection aborted", "temporarily unavailable",
                         "bad gateway", "service unavailable")
    return any(m in msg for m in transient_markers)


def _format_llm_error(exc: Exception) -> str:
    """提取错误中最有用的信息，给用户一个可读的提示。"""
    # 尝试从结构化响应里挖出 message
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            inner = data.get("error", {}).get("message")
            if inner:
                return inner
        except Exception:
            pass
    return str(exc)


def _is_context_length_error(exc: Exception) -> bool:
    """Best-effort detect LLM context/token-limit failures across providers."""
    text = _format_llm_error(exc).lower()
    code = str(getattr(exc, "code", "") or getattr(exc, "error_code", "")).lower()
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            err = data.get("error", {}) if isinstance(data, dict) else {}
            if isinstance(err, dict):
                code = " ".join([code, str(err.get("code", "")), str(err.get("type", ""))]).lower()
        except Exception:
            pass
    surface = f"{code} {text}"
    explicit_markers = (
        "context_length_exceeded",
        "context length exceeded",
        "input tokens exceed",
        "messages resulted in",
        "please reduce the length of the messages",
        "maximum context length",
        "too many tokens",
        "prompt is too long",
        "input is too long",
    )
    if any(marker in surface for marker in explicit_markers):
        return True
    context_markers = ("context", "上下文", "prompt", "messages", "input")
    token_markers = ("token", "tokens", "长度", "too long", "exceed", "exceeds", "exceeded", "超过")
    return any(marker in surface for marker in context_markers) and any(marker in surface for marker in token_markers)


def _maybe_save_long_context_diagnostics(messages, exc: Exception) -> str:
    """Save diagnostics once per exception object and return the summary path."""
    existing = getattr(exc, "_long_context_diagnostics_path", None)
    if existing:
        return str(existing)
    path = _save_long_context_diagnostics(messages, exc)
    try:
        setattr(exc, "_long_context_diagnostics_path", path)
    except Exception:
        pass
    return path


def _plain_message(message):
    if message is None or isinstance(message, (str, int, float, bool)):
        return message
    if isinstance(message, dict):
        return {str(k): _plain_message(v) for k, v in message.items()}
    if isinstance(message, (list, tuple)):
        return [_plain_message(v) for v in message]
    if hasattr(message, "model_dump"):
        try:
            return _plain_message(message.model_dump())
        except Exception:
            pass
    if hasattr(message, "to_dict"):
        try:
            return _plain_message(message.to_dict())
        except Exception:
            pass
    if hasattr(message, "__dict__"):
        public = {k: v for k, v in vars(message).items() if not k.startswith("_")}
        if public:
            return _plain_message(public)
    return str(message)


def _message_diagnostic_record(index: int, message) -> dict:
    plain = _plain_message(message)
    try:
        serialized = json.dumps(plain, ensure_ascii=False, default=str)
    except Exception:
        serialized = str(plain)
    role = plain.get("role") if isinstance(plain, dict) else getattr(message, "role", None)
    name = plain.get("name") if isinstance(plain, dict) else getattr(message, "name", None)
    content = plain.get("content") if isinstance(plain, dict) else getattr(message, "content", "")
    tool_calls = plain.get("tool_calls") if isinstance(plain, dict) else getattr(message, "tool_calls", None)
    try:
        tool_calls_text = json.dumps(tool_calls or [], ensure_ascii=False, default=str)
    except Exception:
        tool_calls_text = str(tool_calls or "")
    return {
        "index": index,
        "role": role,
        "name": name,
        "content_chars": len(str(content or "")),
        "tool_calls_chars": len(tool_calls_text),
        "serialized_chars": len(serialized),
        "message": plain,
    }


def _save_long_context_diagnostics(messages, exc: Exception, *, top_n: int = 3) -> str:
    """Persist the largest messages when an LLM request fails due to context length."""
    out_dir = LONG_CONTEXT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    records = [_message_diagnostic_record(i, m) for i, m in enumerate(messages or [])]
    records.sort(key=lambda r: r.get("serialized_chars", 0), reverse=True)
    selected = records[: max(1, int(top_n or 3))]
    summary = {
        "error": _format_llm_error(exc),
        "message_count": len(messages or []),
        "saved_count": len(selected),
        "output_dir": str(out_dir),
        "records": [
            {k: v for k, v in rec.items() if k != "message"}
            for rec in selected
        ],
    }
    summary_path = out_dir / f"{stamp}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    for rank, rec in enumerate(selected, start=1):
        role = str(rec.get("role") or "unknown").replace("/", "_")[:40]
        size = rec.get("serialized_chars", 0)
        msg_path = out_dir / f"{stamp}_rank{rank}_idx{rec.get('index')}_{role}_{size}chars.json"
        msg_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return str(summary_path)


class RAgent:
    """
    R-Agent 的核心控制器，对应 hermes-agent 中的 run_agent.py (AIAgent)。
    维护多轮对话状态和工具调用的生命周期循环。

    迭代预算策略：
      - 软提醒：达到 max_iterations * SOFT_WARN_RATIO 时，向 messages 注入一条
        system 提示，让模型主动收敛、避免发散式工具调用。
      - 强制收尾：到达最后一轮时，禁用 tools 再请求一次，强制模型输出文本
        总结 + 未完成清单。
      - 截断标记：把截断状态记录在 self 上，并保留完整 messages 历史。CLI 层
        可调用 continue_after_truncation(extra_iterations) 直接续跑，无需让
        用户重发问题，也不会丢失上下文。
    """

    def __init__(self, model=None, max_iterations=None, enable_self_review=True, session_id=None):
        maybe_cleanup_sandbox()
        self.model = model or config.get_model()
        self.session_id = session_id or ""
        self.max_iterations = max_iterations or config.get_max_iterations()
        # 记录默认预算；续跑可以临时扩展，但下一次新对话会恢复，避免预算永久膨胀。
        self._default_max_iterations = self.max_iterations
        self._active_exclude_tools = set()
        # 统一使用 config 模块创建配置好的客户端 (支持 Azure 等)
        self.client = config.create_llm_client()
        self.messages = []
        # 从本次 Agent 启动开始累计 LLM API 返回的 token usage。
        # 部分兼容 OpenAI 接口不返回 usage，此时保持 unavailable，用于 UI 优雅降级。
        self.token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "last_prompt_tokens": 0,
            "last_completion_tokens": 0,
            "last_total_tokens": 0,
            "available": False,
        }
        # 子 Agent / delegate_task 汇总回来的 token usage。与 self.token_usage
        # 分开记录，避免把父 Agent 本轮会话用量和被委托子会话用量混在一起。
        self.delegated_token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "available": False,
        }
        self.context_usage = {
            "estimated_tokens": 0,
            "max_context_tokens": 0,
            "usage_ratio": None,
            "compressed_count": 0,
        }
        # 截断状态：bool。被强制收尾后置 True，下次正常 run 前会自动复位。
        setattr(self, _TRUNCATED_FLAG, False)
        # 软提醒幂等标记：避免在同一段 run 里重复注入 system 提示。
        self._soft_warned = False
        self._turns_since_self_review = 0
        self._enable_self_review = bool(enable_self_review)
        self._background_threads = []
        self._background_lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._background_errors = []

    def _compress_after_archive(self, summary: str, next_steps: str = ""):
        """兼容 archive_subtask 旧入口，同时复用统一上下文压缩语义。"""
        manual_parts = []
        if summary:
            manual_parts.append("【archive_subtask 手动归档摘要】\n" + str(summary))
        if next_steps:
            manual_parts.append("【下一步】\n" + str(next_steps))
        manual_text = "\n\n".join(manual_parts)
        try:
            result = compress_messages(
                self.messages,
                [],
                model=self.model,
                max_context_tokens=resolve_context_window(self.model, config.get_llm_context_window()),
                trigger_ratio=config.get_context_compression_trigger_ratio(),
                target_ratio=config.get_context_compression_target_ratio(),
                preserve_recent_messages=min(
                    config.get_context_compression_preserve_recent_messages(),
                    max(1, len(self.messages) // 2),
                ),
                force=True,
            )
            if result.get("success") and result.get("compressed"):
                compressed = result.get("compressed_messages") or []
                if manual_text:
                    insert_at = 1 if compressed and isinstance(compressed[0], dict) and compressed[0].get("role") == "system" else 0
                    if insert_at < len(compressed) and isinstance(compressed[insert_at], dict) and compressed[insert_at].get("role") == "system":
                        compressed[insert_at] = {
                            **compressed[insert_at],
                            "content": manual_text + "\n\n" + str(compressed[insert_at].get("content", "")),
                        }
                    else:
                        compressed.insert(insert_at, {"role": "system", "content": manual_text})
                self.messages = compressed
                return
        except Exception:
            pass

        # 最小安全兜底：保持旧行为，避免归档失败导致上下文不收敛。
        system_msgs = [m for m in self.messages if isinstance(m, dict) and m.get("role") == "system"][:1]
        recent_user = [m for m in self.messages if isinstance(m, dict) and m.get("role") == "user"][-1:]
        archive_msg = {
            "role": "system",
            "content": "【archive_subtask 压缩摘要】\n" + str(summary) + ("\n下一步：" + str(next_steps) if next_steps else ""),
        }
        self.messages = system_msgs + [archive_msg] + recent_user


    def _maybe_compress_context(self, tools=None) -> None:
        """在每次 LLM 请求前做上下文窗口判别，必要时自动压缩。

        Chat completion response 的 usage 只告诉本次请求用量，不告诉模型最大
        context window；R-Agent 使用 config 显式覆盖或本地模型映射。保留的
        message 总是按完整条目保留，较早历史被合并为一条 system 摘要。
        """
        max_context = resolve_context_window(self.model, config.get_llm_context_window())
        trigger_ratio = config.get_context_compression_trigger_ratio()
        check = should_compress_context(
            self.messages,
            tools or [],
            max_context_tokens=max_context,
            trigger_ratio=trigger_ratio,
        )
        self.context_usage.update({
            "estimated_tokens": check.get("estimated_tokens", 0),
            "max_context_tokens": max_context,
            "usage_ratio": check.get("usage_ratio"),
        })
        if not check.get("should_compress"):
            return

        result = compress_messages(
            self.messages,
            tools or [],
            model=self.model,
            max_context_tokens=max_context,
            trigger_ratio=trigger_ratio,
            target_ratio=config.get_context_compression_target_ratio(),
            preserve_recent_messages=config.get_context_compression_preserve_recent_messages(),
            force=True,
        )
        if result.get("success") and result.get("compressed"):
            self.messages = result.get("compressed_messages", self.messages)
            stats = result.get("stats") or {}
            self.context_usage.update({
                "estimated_tokens": stats.get("compressed_estimated_tokens", check.get("estimated_tokens", 0)),
                "max_context_tokens": max_context,
                "usage_ratio": stats.get("usage_ratio_after"),
                "compressed_count": int(self.context_usage.get("compressed_count") or 0) + 1,
                "last_compression": stats,
            })

    def get_context_usage(self):
        """返回下一次请求的估算上下文窗口占用信息。"""
        return dict(self.context_usage)

    def _run_self_evolution_review(self, snapshot):
        from tools.self_evolution_tool import self_evolution_review

        # CLI 自动后台复盘默认使用 heuristic，避免在后台线程中再启动
        # review Agent / 隔离工具子进程，降低 exit 与 macOS fork 卡死风险。
        return self_evolution_review(
            messages_snapshot=snapshot,
            mode="heuristic",
            dry_run=True,
            use_forked_agent=False,
        )

    def _schedule_self_evolution_review(self):
        if self._shutdown_event.is_set():
            return
        try:
            snapshot = [m for m in self.messages[-20:] if isinstance(m, dict)]

            def _worker():
                try:
                    if self._shutdown_event.is_set():
                        return
                    self._run_self_evolution_review(snapshot)
                except Exception as exc:
                    self._background_errors.append(str(exc))
                finally:
                    current = threading.current_thread()
                    with self._background_lock:
                        self._background_threads = [t for t in self._background_threads if t is not current]

            thread = threading.Thread(
                target=_worker,
                name="r-agent-self-evolution-review",
                daemon=True,
            )
            with self._background_lock:
                self._background_threads.append(thread)
            thread.start()
        except Exception as exc:
            self._background_errors.append(str(exc))

    def shutdown_background_tasks(self, timeout: float = 1.0) -> int:
        """请求后台任务停止，并短暂等待；返回仍存活的后台线程数。"""
        self._shutdown_event.set()
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        with self._background_lock:
            threads = list(self._background_threads)
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
        with self._background_lock:
            self._background_threads = [t for t in self._background_threads if t.is_alive()]
            return len(self._background_threads)

    # ------------------------------------------------------------------
    # Token usage helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _usage_value(usage, name: str) -> int:
        if usage is None:
            return 0
        if isinstance(usage, dict):
            value = usage.get(name, 0)
        else:
            value = getattr(usage, name, 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _record_token_usage(self, response) -> None:
        usage = getattr(response, "usage", None)
        if not usage:
            return
        prompt_tokens = self._usage_value(usage, "prompt_tokens")
        completion_tokens = self._usage_value(usage, "completion_tokens")
        total_tokens = self._usage_value(usage, "total_tokens")
        if not total_tokens:
            total_tokens = prompt_tokens + completion_tokens
        if not any((prompt_tokens, completion_tokens, total_tokens)):
            return
        self.token_usage["prompt_tokens"] += prompt_tokens
        self.token_usage["completion_tokens"] += completion_tokens
        self.token_usage["total_tokens"] += total_tokens
        self.token_usage["last_prompt_tokens"] = prompt_tokens
        self.token_usage["last_completion_tokens"] = completion_tokens
        self.token_usage["last_total_tokens"] = total_tokens
        self.token_usage["available"] = True
        if completion_tokens > LARGE_MESSAGE_COMPLETION_TOKEN_THRESHOLD:
            print(
                "⚠️ 单次模型返回 message token 数过大："
                f"completion_tokens={completion_tokens} "
                f"(阈值 {LARGE_MESSAGE_COMPLETION_TOKEN_THRESHOLD})；"
                f"prompt_tokens={prompt_tokens}, total_tokens={total_tokens}"
            )

    def get_token_usage_total(self):
        """返回本次父 Agent 启动以来自身累计 token；无 usage 信息时返回 'unavailable'。"""
        if not self.token_usage.get("available"):
            return TOKEN_USAGE_UNAVAILABLE
        return self.token_usage.get("total_tokens", 0)

    def get_last_token_usage_total(self):
        """返回最近一次父 Agent LLM 响应 token；无 usage 信息时返回 'unavailable'。"""
        if not self.token_usage.get("available"):
            return TOKEN_USAGE_UNAVAILABLE
        return self.token_usage.get("last_total_tokens", 0)

    def get_delegated_token_usage_total(self):
        """返回已合并的子 Agent token；没有子 usage 时返回 'unavailable'。"""
        if not self.delegated_token_usage.get("available"):
            return TOKEN_USAGE_UNAVAILABLE
        return self.delegated_token_usage.get("total_tokens", 0)

    def get_total_token_usage_including_children(self):
        """返回父 Agent 自身 + 子 Agent 累计 token；两者都无 usage 时返回 'unavailable'。"""
        parent_available = bool(self.token_usage.get("available"))
        child_available = bool(self.delegated_token_usage.get("available"))
        if not parent_available and not child_available:
            return TOKEN_USAGE_UNAVAILABLE
        return (self.token_usage.get("total_tokens", 0) if parent_available else 0) + (
            self.delegated_token_usage.get("total_tokens", 0) if child_available else 0
        )

    def get_token_usage_summary(self, include_children: bool = False) -> dict:
        """返回可 JSON 化的 token usage 摘要；默认只包含当前 Agent 自身会话。"""
        summary = {
            "prompt_tokens": self.token_usage.get("prompt_tokens", 0),
            "completion_tokens": self.token_usage.get("completion_tokens", 0),
            "total_tokens": self.token_usage.get("total_tokens", 0),
            "last_prompt_tokens": self.token_usage.get("last_prompt_tokens", 0),
            "last_completion_tokens": self.token_usage.get("last_completion_tokens", 0),
            "last_total_tokens": self.token_usage.get("last_total_tokens", 0),
            "available": bool(self.token_usage.get("available")),
        }
        if include_children:
            delegated = {
                "prompt_tokens": self.delegated_token_usage.get("prompt_tokens", 0),
                "completion_tokens": self.delegated_token_usage.get("completion_tokens", 0),
                "total_tokens": self.delegated_token_usage.get("total_tokens", 0),
                "available": bool(self.delegated_token_usage.get("available")),
            }
            summary["delegated_token_usage"] = delegated
            summary["total_including_children"] = self.get_total_token_usage_including_children()
        return summary

    def merge_delegated_token_usage(self, usage) -> bool:
        """把 delegate_task 返回的 delegated_token_usage 合并到当前父 Agent。

        返回 True 表示成功合并了至少一个非零 usage；无 usage/不可解析时保持
        unavailable 语义并返回 False。
        """
        if not isinstance(usage, dict) or not usage.get("available"):
            return False
        prompt_tokens = self._usage_value(usage, "prompt_tokens")
        completion_tokens = self._usage_value(usage, "completion_tokens")
        total_tokens = self._usage_value(usage, "total_tokens")
        if not total_tokens:
            total_tokens = prompt_tokens + completion_tokens
        if not any((prompt_tokens, completion_tokens, total_tokens)):
            return False
        self.delegated_token_usage["prompt_tokens"] += prompt_tokens
        self.delegated_token_usage["completion_tokens"] += completion_tokens
        self.delegated_token_usage["total_tokens"] += total_tokens
        self.delegated_token_usage["available"] = True
        return True

    def _merge_delegated_token_usage_from_tool_result(self, result: str) -> bool:
        """解析 delegate_task 的工具返回，并合并其中的 delegated_token_usage。

        兼容两种既有返回形态：直接调用 delegate_task 得到的 JSON，以及
        registry.execute_tool 包装后的 {success,result} JSON。若工具结果因过大
        被持久化替换，则调用方应在持久化前调用此 helper。
        """
        try:
            payload = json.loads(result) if isinstance(result, str) else result
        except Exception:
            return False
        if isinstance(payload, dict) and "result" in payload:
            inner = payload.get("result")
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except Exception:
                    inner = None
            if isinstance(inner, dict):
                payload = inner
        if not isinstance(payload, dict):
            return False
        return self.merge_delegated_token_usage(payload.get("delegated_token_usage"))

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _chat_completion_with_retry(self, on_think=None, iteration=None, cancel_event=None, **kwargs):
        """
        包装 client.chat.completions.create，对瞬时错误自动指数退避重试。
        非瞬时错误（如内容策略 cyber_policy / 鉴权 / 参数错误）直接抛出。
        """
        max_retries = config.get_llm_max_retries()
        base_delay = config.get_llm_retry_base_delay()

        last_exc = None
        for attempt in range(max_retries + 1):
            if _is_cancelled(cancel_event):
                raise AgentInterrupted()
            try:
                response = self.client.chat.completions.create(**kwargs)
                if _is_cancelled(cancel_event):
                    raise AgentInterrupted()
                self._record_token_usage(response)
                return response
            except Exception as e:
                last_exc = e
                if attempt >= max_retries or not _is_transient_error(e):
                    if _is_context_length_error(e):
                        try:
                            path = _maybe_save_long_context_diagnostics(kwargs.get("messages", []), e)
                            print(f"⚠️ 模型输入上下文过长，已保存最长的 3 条 message 到: {path}")
                        except Exception as diag_exc:
                            print(f"⚠️ 模型输入上下文过长，但保存 long_context 诊断失败: {diag_exc}")
                    raise
                # 指数退避 + 抖动，避免与同伴请求形成同步重试风暴
                delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                if on_think and iteration is not None:
                    try:
                        on_think(iteration, retry_attempt=attempt + 1,
                                 retry_max=max_retries,
                                 retry_delay=delay,
                                 retry_reason=_format_llm_error(e))
                    except TypeError:
                        # 旧版 on_think 不支持额外 kwargs，退化到普通调用
                        on_think(iteration)

                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    if _is_cancelled(cancel_event):
                        raise AgentInterrupted()
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        # 不应到达这里，但出于完整性
        raise last_exc  # type: ignore[misc]

    def _inject_soft_warning(self, used: int, total: int):
        """注入一条软提醒，让模型主动收敛。"""
        warn = (
            f"【系统提醒】你已使用 {used}/{total} 轮思考预算。"
            "请评估当前任务进度，优先合并/收敛工具调用，避免发散式探索。"
            "如果剩余信息已足够，请尽早给出最终答复。"
        )
        self.messages.append({"role": "system", "content": warn})

    def _force_finalize(self, used: int, total: int, on_think=None, cancel_event=None) -> str:
        """
        最后一次请求：禁用 tools，要求模型输出文本总结与未完成清单。
        即使模型仍想调用工具，由于没有 tools 字段，它只能输出文本。
        """
        finalize_hint = (
            f"【系统强制收尾】你已用尽 {used}/{total} 轮思考预算，本轮不再"
            "提供任何工具。请：\n"
            "1) 用一段简短自然语言给出当前能给出的最佳答复；\n"
            "2) 在「未完成事项」小节中列出仍需继续的子任务（如果有）；\n"
            "3) 在「建议下一步」小节给出用户可以做的明确选择"
            "（例如：扩展预算继续 / 缩小问题范围 / 提供更多信息）。"
        )
        if _is_cancelled(cancel_event):
            raise AgentInterrupted()
        self.messages.append({"role": "system", "content": finalize_hint})

        if on_think:
            on_think(used)

        try:
            response = self._chat_completion_with_retry(
                on_think=on_think,
                iteration=used,
                cancel_event=cancel_event,
                model=self.model,
                messages=self.messages,
            )
        except AgentInterrupted:
            raise
        except Exception as e:
            return f"模型强制收尾失败: {_format_llm_error(e)}"

        if _is_cancelled(cancel_event):
            raise AgentInterrupted()
        message = response.choices[0].message
        self.messages.append(message)
        return message.content or "(模型未返回文本)"

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run_conversation(self, user_message: str, system_message: str = None,
                         on_think=None, on_tool_start=None, on_tool_end=None,
                         exclude_tools=None, cancel_event=None, tool_call_guard=None, allowed_tools=None,
                         event_sink=None) -> str:
        """核心对话循环 (The Agent Loop)。

        cancel_event 用于 CLI 的 Esc 中断：一旦置位，当前轮会回滚到本次
        用户输入后的快照（保留用户输入，丢弃 assistant/tool 中间消息）并
        抛出 AgentInterrupted。
        """
        if system_message and not any(m.get("role") == "system" for m in self.messages):
            system_msg = {"role": "system", "content": system_message}
            self.messages.append(system_msg)
            _emit_event(event_sink, EVENT_MESSAGE_APPENDED, {"message": normalize_message(system_msg), "message_index": len(self.messages) - 1})

        user_msg = {"role": "user", "content": user_message}
        self.messages.append(user_msg)
        _emit_event(event_sink, EVENT_MESSAGE_APPENDED, {"message": normalize_message(user_msg), "message_index": len(self.messages) - 1})
        # Repair any dangling tool-call state left by a previous run that died
        # mid tool-call (interrupt/crash/restart). Otherwise the chat API rejects
        # every turn with "assistant message with 'tool_calls' must be followed
        # by tool messages...". Done before rollback_index so a later interrupt
        # rolls back to the repaired baseline.
        self.messages = sanitize_tool_call_messages(self.messages)
        rollback_index = len(self.messages)

        # 新一轮 run，复位截断/软提醒标记，并恢复默认预算。
        # continue_after_truncation 只应影响当前被截断任务，不应永久抬高后续对话预算。
        self.max_iterations = self._default_max_iterations
        setattr(self, _TRUNCATED_FLAG, False)
        self._soft_warned = False
        self._active_exclude_tools = set(exclude_tools or [])

        try:
            result = self._loop(start_iteration=0, on_think=on_think,
                                on_tool_start=on_tool_start, on_tool_end=on_tool_end,
                                exclude_tools=self._active_exclude_tools,
                                cancel_event=cancel_event,
                                tool_call_guard=tool_call_guard,
                                allowed_tools=allowed_tools,
                                event_sink=event_sink)
            self._turns_since_self_review += 1
            review_interval = config.get_self_evolution_review_interval()
            if (
                self._enable_self_review
                and review_interval > 0
                and self._turns_since_self_review >= review_interval
                and not self._shutdown_event.is_set()
            ):
                self._turns_since_self_review = 0
                self._schedule_self_evolution_review()
            return result
        except AgentInterrupted:
            self.messages = self.messages[:rollback_index]
            setattr(self, _TRUNCATED_FLAG, False)
            self._soft_warned = False
            raise

    def continue_after_truncation(self, extra_iterations: int,
                                  on_think=None, on_tool_start=None,
                                  on_tool_end=None, exclude_tools=None,
                                  cancel_event=None, event_sink=None) -> str:
        """
        在被强制截断后，由 CLI 询问用户并扩展预算后调用，直接续跑。
        不会让用户重新输入问题，messages 历史完整保留。
        """
        if extra_iterations <= 0:
            return "未扩展迭代预算，已保留当前结果。"

        rollback_index = len(self.messages)

        # 在历史中追加一条 user 风格的指令：让模型继续推进未完成事项
        resume_msg = {
            "role": "user",
            "content": (
                f"【用户决定扩展 {extra_iterations} 轮思考预算】"
                "请基于上面的「未完成事项」继续推进；如已无可推进事项，请直接给出最终答复。"
            ),
        }
        self.messages.append(resume_msg)
        _emit_event(event_sink, EVENT_MESSAGE_APPENDED, {"message": normalize_message(resume_msg), "message_index": len(self.messages) - 1})
        self.max_iterations += extra_iterations
        setattr(self, _TRUNCATED_FLAG, False)
        self._soft_warned = False

        # 从「之前已用满的轮数」继续计数
        used_before = self.max_iterations - extra_iterations
        active_exclude_tools = self._active_exclude_tools if exclude_tools is None else set(exclude_tools or [])
        self._active_exclude_tools = set(active_exclude_tools)
        try:
            return self._loop(start_iteration=used_before, on_think=on_think,
                              on_tool_start=on_tool_start, on_tool_end=on_tool_end,
                              exclude_tools=active_exclude_tools,
                              cancel_event=cancel_event,
                              event_sink=event_sink)
        except AgentInterrupted:
            self.messages = self.messages[:rollback_index]
            setattr(self, _TRUNCATED_FLAG, False)
            self._soft_warned = False
            raise

    def is_truncated(self) -> bool:
        return bool(getattr(self, _TRUNCATED_FLAG, False))

    # ------------------------------------------------------------------
    # 真实循环
    # ------------------------------------------------------------------
    def _loop(self, start_iteration: int, on_think=None,
              on_tool_start=None, on_tool_end=None, exclude_tools=None,
              cancel_event=None, tool_call_guard=None, allowed_tools=None, event_sink=None) -> str:
        soft_threshold = max(1, int(self.max_iterations * config.get_soft_warn_ratio()))
        iteration = start_iteration
        excluded = set(exclude_tools or [])
        allowed = set(allowed_tools or []) if allowed_tools is not None else None

        while iteration < self.max_iterations:
            if _is_cancelled(cancel_event):
                raise AgentInterrupted()

            # 软提醒（一次性）
            if not self._soft_warned and iteration >= soft_threshold:
                self._inject_soft_warning(iteration, self.max_iterations)
                self._soft_warned = True

            tools = registry.get_all_schemas()
            if allowed is not None:
                tools = [
                    schema for schema in tools
                    if schema.get("function", {}).get("name") in allowed
                ]
            if excluded:
                tools = [
                    schema for schema in tools
                    if schema.get("function", {}).get("name") not in excluded
                ]
            self._maybe_compress_context(tools)

            kwargs = {"model": self.model, "messages": self.messages}
            if tools:
                kwargs["tools"] = tools

            _emit_event(event_sink, EVENT_LLM_REQUEST_SNAPSHOT, build_llm_request_snapshot(
                model=self.model,
                messages=self.messages,
                tools=tools,
                iteration=iteration,
            ))

            if on_think:
                on_think(iteration)

            try:
                response = self._chat_completion_with_retry(
                    on_think=on_think,
                    iteration=iteration,
                    cancel_event=cancel_event,
                    **kwargs,
                )
            except AgentInterrupted:
                raise
            except Exception as e:
                if _is_context_length_error(e):
                    try:
                        path = _maybe_save_long_context_diagnostics(self.messages, e)
                        return f"模型请求失败: {_format_llm_error(e)}\n已保存最长的 3 条 message 到: {path}"
                    except Exception as diag_exc:
                        return f"模型请求失败: {_format_llm_error(e)}\n保存 long_context 诊断失败: {diag_exc}"
                return f"模型请求失败: {_format_llm_error(e)}"

            if _is_cancelled(cancel_event):
                raise AgentInterrupted()
            message = response.choices[0].message
            _emit_event(event_sink, EVENT_LLM_RESPONSE_RECEIVED, {"message": normalize_message(message)})
            self.messages.append(message)
            _emit_event(event_sink, EVENT_MESSAGE_APPENDED, {"message": normalize_message(message), "message_index": len(self.messages) - 1})

            if message.tool_calls:
                pending_tool_events = []
                pending_tool_messages = []
                for tool_call in message.tool_calls:
                    if _is_cancelled(cancel_event):
                        raise AgentInterrupted()
                    func_name = tool_call.function.name
                    func_args = tool_call.function.arguments

                    _emit_event(event_sink, EVENT_TOOL_CALL_STARTED, {
                        "call_id": getattr(tool_call, "id", None),
                        "name": func_name,
                        "arguments": func_args,
                    })
                    if on_tool_start:
                        on_tool_start(func_name, func_args)

                    guard_denial = None
                    if tool_call_guard is not None:
                        try:
                            guard_denial = tool_call_guard(func_name, func_args)
                        except Exception as exc:
                            guard_denial = f"工具 {func_name} 被安全策略拒绝：{exc}"

                    if func_name == "todo_manage" and _safe_tool_session_id(self.session_id):
                        try:
                            todo_args_for_session = json.loads(func_args or "{}")
                            if _inject_current_session(todo_args_for_session, self.session_id):
                                func_args = json.dumps(todo_args_for_session, ensure_ascii=False)
                        except Exception:
                            pass

                    if guard_denial:
                        result = guard_denial
                    elif allowed is not None and func_name not in allowed:
                        result = f'工具 {func_name} 未在当前上下文中启用，未执行。'
                    elif func_name in excluded:
                        result = f'工具 {func_name} 已在当前上下文中被禁用，未执行。'
                    elif func_name == "delegate_task":
                        # delegate_task 是父进程调度器：内部会启动线程/子 Agent，并需要
                        # 直接向当前终端打印 Rich 看板。若再放进隔离工具进程，容易形成
                        # “隔离进程 -> 线程池 -> 工具子进程”的嵌套 fork，macOS 下会
                        # 无返回崩溃，也会让 CLI status 无法正确让出终端行。GUI 模式下
                        # 额外注入 event_sink，用于捕获子 Agent 上下文。
                        if event_sink is not None:
                            try:
                                delegate_args = json.loads(func_args or "{}")
                                if isinstance(delegate_args, dict):
                                    delegate_args["event_sink"] = event_sink
                                    if allowed:
                                        delegate_args["allowed_tools"] = sorted(allowed)
                                    _inject_current_session(delegate_args, self.session_id)
                                    result = registry._tools[func_name]["handler"](**delegate_args)
                                else:
                                    result = registry.execute_tool(func_name, func_args)
                            except Exception as exc:
                                result = json.dumps({"error": str(exc)}, ensure_ascii=False)
                        else:
                            try:
                                delegate_args = json.loads(func_args or "{}")
                                if isinstance(delegate_args, dict):
                                    _inject_current_session(delegate_args, self.session_id)
                                    if allowed:
                                        delegate_args["allowed_tools"] = sorted(allowed)
                                    func_args = json.dumps(delegate_args, ensure_ascii=False)
                            except Exception:
                                pass
                            result = registry.execute_tool(func_name, func_args)
                    else:
                        result = registry.execute_tool_isolated(
                            func_name,
                            func_args,
                            cancel_event=cancel_event,
                            timeout=config.get_tool_execution_timeout(),
                            interrupted_exception=AgentInterrupted,
                        )

                    if _is_cancelled(cancel_event):
                        raise AgentInterrupted()

                    if func_name == "delegate_task":
                        self._merge_delegated_token_usage_from_tool_result(result)

                    result = maybe_persist_tool_result(
                        content=result,
                        tool_name=func_name,
                        tool_use_id=getattr(tool_call, "id", None),
                    )

                    pending_tool_events.append({
                        "call_id": getattr(tool_call, "id", None),
                        "name": func_name,
                        "arguments": func_args,
                    })
                    pending_tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": result,
                    })

                pending_tool_messages = enforce_turn_budget(pending_tool_messages)

                for tool_event, tool_msg in zip(pending_tool_events, pending_tool_messages):
                    result = tool_msg.get("content", "")
                    _emit_event(event_sink, EVENT_TOOL_CALL_FINISHED, {
                        **tool_event,
                        "result": result,
                    })
                    if on_tool_end:
                        on_tool_end(tool_event["name"], result)

                    self.messages.append(tool_msg)
                    normalized_tool_msg = normalize_message(tool_msg)
                    _emit_event(event_sink, EVENT_TOOL_RESULT_APPENDED, {"message": normalized_tool_msg})
                    _emit_event(event_sink, EVENT_MESSAGE_APPENDED, {"message": normalized_tool_msg, "message_index": len(self.messages) - 1})
                    if tool_event["name"] == "archive_subtask":
                        try:
                            outer = json.loads(result)
                            inner = outer.get("result", outer) if isinstance(outer, dict) else {}
                            if isinstance(inner, str):
                                inner = json.loads(inner)
                            if isinstance(inner, dict) and inner.get("success"):
                                self._compress_after_archive(inner.get("recorded_summary", ""), inner.get("next_steps", ""))
                        except Exception:
                            pass
                iteration += 1
                # 进入下一轮
            else:
                # 模型已给出最终答复
                return message.content

        # 达到上限：强制收尾
        finalized = self._force_finalize(iteration, self.max_iterations, on_think=on_think,
                                         cancel_event=cancel_event)
        setattr(self, _TRUNCATED_FLAG, True)
        _emit_event(event_sink, EVENT_TRUNCATION_FORCED, {"used": iteration, "max_iterations": self.max_iterations})

        prefix = (
            f"⚠️ **已达迭代上限 ({self.max_iterations} 轮)，以下为强制收尾结果。"
            "上下文已完整保留，可在下方选择是否扩展预算继续。**\n\n"
        )
        return prefix + finalized
