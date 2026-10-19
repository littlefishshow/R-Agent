"""L0 <-> 既有 Agent 循环的兼容层。

Agent 主循环（``core/agent.py:_loop``）历史上直接消费 OpenAI SDK 的响应对象：

    message = response.choices[0].message
    message.content / message.tool_calls / tool_call.function.name ...
    self.messages.append(message)
    response.usage  # 供 token / cache 记账

为了在不改写主循环结构的前提下接入 L0 ``ModelProvider``，这里把统一的
``AssistantMessage`` 适配回一个"看起来像 OpenAI 响应"的对象：

* ``ProviderMessage``   —— 支持属性访问（role/content/tool_calls），且可通过
  ``to_dict()`` / ``model_dump()`` 转回标准 OpenAI assistant dict（写回历史用）；
* ``ProviderResponse``  —— 具备 ``.choices[0].message`` 与 ``.usage``，让既有的
  ``_record_token_usage`` / ``_record_cache_usage`` 原样工作。

写回历史时应使用 ``ProviderMessage.to_dict()`` 的**纯 dict**，避免把自定义对象塞进
``messages`` 后，下一轮请求无法被厂商 SDK 序列化。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.llm.types import AssistantMessage


class ProviderFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}


class ProviderToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.type = "function"
        self.function = ProviderFunction(name, arguments)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": "function", "function": self.function.to_dict()}


class ProviderMessage:
    """属性可访问、又能转回 OpenAI dict 的 assistant 消息。"""

    def __init__(self, assistant: AssistantMessage) -> None:
        self.role = "assistant"
        self.content = assistant.text
        self.reasoning_content = assistant.thinking
        self._tool_calls: List[ProviderToolCall] = [
            ProviderToolCall(tc.id, tc.name, tc.raw_arguments or "{}")
            for tc in assistant.tool_calls
        ]

    @property
    def tool_calls(self):
        # 与 OpenAI SDK 一致：无工具调用时为 None（主循环用 ``if message.tool_calls``）。
        return self._tool_calls or None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self._tool_calls:
            data["tool_calls"] = [tc.to_dict() for tc in self._tool_calls]
        return data

    # normalizer / cache_observability 的对象路径会优先尝试 model_dump / to_dict。
    def model_dump(self) -> Dict[str, Any]:
        return self.to_dict()


class ProviderChoice:
    def __init__(self, message: ProviderMessage, finish_reason: Optional[str]) -> None:
        self.message = message
        self.finish_reason = finish_reason


class ProviderResponse:
    """``.choices[0].message`` + ``.usage``，兼容既有 token/cache 记账。"""

    _STOP_TO_FINISH = {
        "stop": "stop",
        "tool_use": "tool_calls",
        "length": "length",
        "content_filter": "content_filter",
        "aborted": "stop",
        "error": "stop",
    }

    def __init__(self, assistant: AssistantMessage) -> None:
        finish = self._STOP_TO_FINISH.get(assistant.stop_reason, "stop")
        self.choices = [ProviderChoice(ProviderMessage(assistant), finish)]
        # 优先用厂商原始 usage 对象（含 cached_tokens 等诊断字段）；否则退回归一化 dict。
        self.usage = assistant.usage.raw if assistant.usage.raw is not None else assistant.usage.to_dict()


def to_openai_response(assistant: AssistantMessage) -> ProviderResponse:
    return ProviderResponse(assistant)
