"""L0 Provider 与流事件层（R-Agent 的模型防腐层）。

参考 Pi 的 L0 设计（见 ``/Users/bytedance/myenv/dsh/pi/learning_note/L0.md``）：
把"向模型发请求、把厂商流式响应转成统一事件"这件事单独成层。上层（Agent Loop、
Session、GUI）只依赖本包导出的统一类型与 ``ModelProvider`` 契约，不认识任何厂商 SDK。

公开接口::

    from core.llm import (
        AssistantMessage, AssistantMessageEvent, ToolCall, Usage,
        AssistantMessageEventStream,
        ModelProvider, ModelRequest, StreamOptions,
        build_provider,
    )

``build_provider`` 根据 ``core.config`` 创建默认 Provider（当前是 OpenAI/Azure 兼容
adapter），并用 ``lazy_stream`` 语义保证统一的同步返回与错误事件。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from core.llm.event_stream import AssistantMessageEventStream, FifoQueue
from core.llm.lazy import lazy_stream
from core.llm.provider import ModelProvider, ModelRequest, StreamOptions
from core.llm.types import (
    AssistantMessage,
    AssistantMessageEvent,
    ContentBlock,
    StopReason,
    TextContent,
    ThinkingContent,
    ToolCall,
    Usage,
    make_error_message,
)

__all__ = [
    "AssistantMessage",
    "AssistantMessageEvent",
    "AssistantMessageEventStream",
    "ContentBlock",
    "FifoQueue",
    "ModelProvider",
    "ModelRequest",
    "StopReason",
    "StreamOptions",
    "TextContent",
    "ThinkingContent",
    "ToolCall",
    "Usage",
    "build_provider",
    "lazy_stream",
    "make_error_message",
]


def build_provider(
    client: Any = None,
    *,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> ModelProvider:
    """根据配置构造默认 Provider。

    * ``client``：已配置的 LLM SDK 客户端；为空时用 ``config.create_llm_client()``；
    * ``cancel_check``：可选取消检查回调（Esc 中断等），adapter 会据此发 ``aborted``。

    目前所有端点都是 OpenAI 兼容（含 Azure），统一返回 ``OpenAIChatProvider``。将来接
    Anthropic/Google 时，这里按 ``config.get_client_type()`` 分派不同 adapter 即可，
    Agent Loop 无需改动。
    """
    from core import config
    from core.llm.adapters.openai_chat import OpenAIChatProvider

    if client is None:
        client = config.create_llm_client()
    return OpenAIChatProvider(
        client,
        max_retries=config.get_llm_max_retries(),
        base_delay=config.get_llm_retry_base_delay(),
        cancel_check=cancel_check,
    )
