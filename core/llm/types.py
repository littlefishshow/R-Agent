"""L0 公共语言：R-Agent 内部统一的模型消息、工具调用与流事件表示。

对齐 Pi 的 L0 设计（见 learning_note/L0.md 第 3 节「公共语言」）：这是一层
**防腐层（anti-corruption layer）**。厂商协议（OpenAI chat / Azure / 未来的
Anthropic / 本地模型）只允许存在于 ``core/llm/adapters/`` 内部；Agent Loop、
Session、GUI 只依赖本文件里的稳定类型。

两个核心视图（Pi 第 4.3 节）：

* ``AssistantMessage``       —— 一次模型调用完成后的**最终状态**（写历史、读 stop_reason、
  统计 usage、读取完整 tool call）。
* ``AssistantMessageEvent``  —— 回复**如何逐步产生**的过程事件（实时渲染文字、thinking、
  工具参数增量、成功/失败/取消）。

设计约束（Pi 第 6 节「响应状态机」）：

* 普通事件不能早于 ``start``；
* 一条流只能终止一次（``done`` 或 ``error``）；
* ``done`` 携带完整回复，``error`` 携带结构化错误回复；
* tool call 必须到 ``toolcall_end`` 才算完整，中途 delta 只是未闭合 JSON 文本。

本文件只定义数据类型，不做任何 IO、不认识任何厂商 SDK。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Union


# ---------------------------------------------------------------------------
# 结束原因（stop reason）
# ---------------------------------------------------------------------------
# 跨厂商归一化的结束原因。adapter 负责把厂商的 finish_reason 翻译到这里。
StopReason = Literal[
    "stop",          # 模型正常结束
    "tool_use",      # 模型要求调用工具
    "length",        # 触达 max tokens / 长度限制
    "content_filter",  # 内容策略拦截
    "aborted",       # 用户/调用方取消
    "error",         # 请求或生成过程出错
]


# ---------------------------------------------------------------------------
# 内容块（content blocks）
# ---------------------------------------------------------------------------
@dataclass
class TextContent:
    """一段最终文本内容。"""

    text: str = ""
    type: Literal["text"] = "text"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "text", "text": self.text}


@dataclass
class ThinkingContent:
    """一段思考（reasoning）内容。

    ``signature`` 预留给需要回传推理签名的厂商（如 Anthropic extended thinking），
    默认为空，普通 OpenAI chat 不使用。
    """

    text: str = ""
    signature: str = ""
    type: Literal["thinking"] = "thinking"

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"type": "thinking", "text": self.text}
        if self.signature:
            data["signature"] = self.signature
        return data


@dataclass
class ToolCall:
    """一次工具调用。

    ``arguments`` 为**已解析**的 JSON 对象；若最终 JSON 无法解析，``arguments`` 为
    ``{}`` 且 ``arguments_error`` 保留原始文本与错误，交由上层参数校验处理（对齐 Pi
    第 6 节检查题：绝不执行猜测出来的参数）。``raw_arguments`` 始终保留原始字符串，
    便于回传给需要原样 arguments 的厂商协议。
    """

    id: str = ""
    name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    arguments_error: Optional[str] = None
    type: Literal["tool_call"] = "tool_call"

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "type": "tool_call",
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
        }
        if self.arguments_error:
            data["arguments_error"] = self.arguments_error
        return data

    def to_openai_tool_call(self) -> Dict[str, Any]:
        """转回 OpenAI chat 协议的 assistant.tool_calls 条目。"""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments or "{}",
            },
        }


ContentBlock = Union[TextContent, ThinkingContent, ToolCall]


# ---------------------------------------------------------------------------
# Usage（token 与缓存计量）
# ---------------------------------------------------------------------------
@dataclass
class Usage:
    """归一化的 token 计量。

    ``provider_cached_tokens`` / ``provider_cache_creation_tokens`` 为厂商上报的缓存
    诊断值（可为 None 表示该端点不暴露）。``raw`` 保留厂商原始 usage 对象，供
    cache_observability 等既有诊断逻辑继续读取厂商专有字段。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    provider_cached_tokens: Optional[int] = None
    provider_cache_creation_tokens: Optional[int] = None
    available: bool = False
    raw: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "provider_cached_tokens": self.provider_cached_tokens,
            "provider_cache_creation_tokens": self.provider_cache_creation_tokens,
            "available": self.available,
        }


# ---------------------------------------------------------------------------
# AssistantMessage（最终状态）
# ---------------------------------------------------------------------------
@dataclass
class AssistantMessage:
    """一次模型调用完成后的完整回复（最终状态视图）。"""

    content: List[ContentBlock] = field(default_factory=list)
    stop_reason: StopReason = "stop"
    usage: Usage = field(default_factory=Usage)
    error_message: str = ""
    # adapter 可把厂商原始 message 对象塞这里；上层一般不读，仅用于兜底/调试。
    raw: Any = None

    # --- 便捷访问器：让上层无需理解 content block 列表结构 ---
    @property
    def text(self) -> str:
        """拼接所有文本块（不含 thinking）。"""
        return "".join(b.text for b in self.content if isinstance(b, TextContent))

    @property
    def thinking(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, ThinkingContent))

    @property
    def tool_calls(self) -> List[ToolCall]:
        return [b for b in self.content if isinstance(b, ToolCall)]

    @property
    def is_error(self) -> bool:
        return self.stop_reason in ("error", "aborted")

    def to_openai_message(self) -> Dict[str, Any]:
        """转成可直接 append 进 ``messages`` 历史的 OpenAI chat assistant 消息。

        R-Agent 历史目前存 OpenAI 风格 dict / SDK 对象；这里产出 dict，字段与
        既有 ``normalize_message`` 兼容：``content`` 为纯文本，``tool_calls`` 为
        function 条目列表（无工具调用时省略）。
        """
        message: Dict[str, Any] = {"role": "assistant", "content": self.text}
        calls = self.tool_calls
        if calls:
            message["tool_calls"] = [tc.to_openai_tool_call() for tc in calls]
        return message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": [b.to_dict() for b in self.content],
            "stop_reason": self.stop_reason,
            "usage": self.usage.to_dict(),
            "error_message": self.error_message,
        }


# ---------------------------------------------------------------------------
# AssistantMessageEvent（过程事件）
# ---------------------------------------------------------------------------
# 12 种事件类型，对齐 Pi 第 4.1 节。
EventType = Literal[
    "start",
    "text_start",
    "text_delta",
    "text_end",
    "thinking_start",
    "thinking_delta",
    "thinking_end",
    "toolcall_start",
    "toolcall_delta",
    "toolcall_end",
    "done",
    "error",
]

_TERMINAL_EVENTS = ("done", "error")


@dataclass
class AssistantMessageEvent:
    """生成过程中的一个事件。

    字段语义（Pi 第 4.1 节）：

    * ``content_index`` —— 本事件对应 ``AssistantMessage.content`` 的哪个 block；
    * ``delta``         —— 这一次新增的内容（文本 / thinking / tool 参数 JSON 文本）；
    * ``content``       —— 一个 block 结束后的权威完整内容；
    * ``tool_call``     —— ``toolcall_end`` 时的完整 ToolCall；
    * ``reason``        —— ``done`` / ``error`` 的结束原因；
    * ``message``       —— 终止事件携带的最终 AssistantMessage；
    * ``error``         —— ``error`` 事件携带的错误消息（也是一个 AssistantMessage）。
    """

    type: EventType
    content_index: Optional[int] = None
    delta: str = ""
    content: str = ""
    tool_call: Optional[ToolCall] = None
    reason: Optional[StopReason] = None
    message: Optional[AssistantMessage] = None
    error: Optional[AssistantMessage] = None

    @property
    def is_terminal(self) -> bool:
        return self.type in _TERMINAL_EVENTS

    def result_message(self) -> Optional[AssistantMessage]:
        """从终止事件提取最终 AssistantMessage（done -> message / error -> error）。"""
        if self.type == "done":
            return self.message
        if self.type == "error":
            return self.error
        return None


# ---------------------------------------------------------------------------
# 事件构造便捷函数（adapter 用）
# ---------------------------------------------------------------------------
def event_start() -> AssistantMessageEvent:
    return AssistantMessageEvent(type="start")


def event_text_start(index: int) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="text_start", content_index=index)


def event_text_delta(index: int, delta: str) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="text_delta", content_index=index, delta=delta)


def event_text_end(index: int, content: str) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="text_end", content_index=index, content=content)


def event_thinking_start(index: int) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="thinking_start", content_index=index)


def event_thinking_delta(index: int, delta: str) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="thinking_delta", content_index=index, delta=delta)


def event_thinking_end(index: int, content: str) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="thinking_end", content_index=index, content=content)


def event_toolcall_start(index: int) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="toolcall_start", content_index=index)


def event_toolcall_delta(index: int, delta: str) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="toolcall_delta", content_index=index, delta=delta)


def event_toolcall_end(index: int, tool_call: ToolCall) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="toolcall_end", content_index=index, tool_call=tool_call)


def event_done(reason: StopReason, message: AssistantMessage) -> AssistantMessageEvent:
    return AssistantMessageEvent(type="done", reason=reason, message=message)


def event_error(message: AssistantMessage, reason: StopReason = "error") -> AssistantMessageEvent:
    return AssistantMessageEvent(type="error", reason=reason, error=message)


def make_error_message(error_text: str, *, reason: StopReason = "error") -> AssistantMessage:
    """构造一个"错误即最终消息"的 AssistantMessage（Pi：错误也是结构化终止）。"""
    return AssistantMessage(content=[], stop_reason=reason, error_message=str(error_text))
