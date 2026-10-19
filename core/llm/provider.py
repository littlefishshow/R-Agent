"""L0 Provider 端口（Ports and Adapters）。

对齐 Pi 的第三部分「Provider 适配器」（learning_note/L0.md 第 5 节）：

* Agent Loop 只依赖 ``ModelProvider`` 契约，不根据 ``model.provider`` 写 if/else；
* 每个厂商实现一个 adapter，把 Pi 请求翻译成厂商协议，再把厂商响应翻译回统一的
  ``AssistantMessageEvent`` 流；
* 契约提供两个入口（Pi「stream() 与 streamSimple()」）：
  - ``stream(request, **provider_options)`` —— 接受厂商专有配置；
  - ``stream_simple(request, options)``     —— 接受 provider-neutral 的
    ``StreamOptions``，由 adapter 映射到厂商专有配置后调用 ``stream()``；
* 所有 ``stream*`` 方法都**同步返回** ``AssistantMessageEventStream``（即使底层 setup
  是异步/惰性的，也通过 lazy wrapper 包成同步返回，见 ``core/llm/lazy.py``）。

``ModelRequest`` 是 provider-neutral 的请求负载：消息历史、工具 schema、模型名、
温度等通用项。R-Agent 现阶段的消息/工具仍是 OpenAI 风格 dict，这里原样承载——
adapter 负责在内部做任何必要转换。这样可以**零成本接入既有调用点**，又为将来接
Anthropic/Google 预留了 adapter 内部翻译空间。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.llm.event_stream import AssistantMessageEventStream


# ---------------------------------------------------------------------------
# 请求负载
# ---------------------------------------------------------------------------
@dataclass
class ModelRequest:
    """provider-neutral 的一次模型请求。

    ``messages`` / ``tools`` 使用 OpenAI chat 风格结构（R-Agent 当前的内部表示）。
    adapter 可在内部把它们翻译成任意厂商协议。``extra`` 承载额外的、会原样透传给
    厂商 ``create()`` 的参数（例如 ``response_format``），便于高级用户不绕开契约。
    """

    model: str
    messages: List[Any]
    tools: Optional[List[Any]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_openai_kwargs(self) -> Dict[str, Any]:
        """构造 OpenAI chat.completions.create 的 kwargs（不含 stream 开关）。"""
        kwargs: Dict[str, Any] = {"model": self.model, "messages": self.messages}
        if self.tools:
            kwargs["tools"] = self.tools
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        for key, value in (self.extra or {}).items():
            if value is not None:
                kwargs[key] = value
        return kwargs


# ---------------------------------------------------------------------------
# provider-neutral 流选项
# ---------------------------------------------------------------------------
@dataclass
class StreamOptions:
    """跨 Provider 通用的流选项（Pi 的 SimpleStreamOptions 简化版）。

    这里只放真正 provider-neutral 的项；厂商专有能力（OpenAI serviceTier、
    reasoningSummary 等）应通过各 adapter 的 ``stream()`` 专有参数传入，不污染公共
    接口。``stream`` 为 False 时 adapter 走非流式 ``create()``，但对外仍产出同一套
    事件序列（一次性 push start/delta/end/done），保证消费者无差别。
    """

    stream: bool = True
    # reasoning / thinking 粗粒度等级，adapter 自行 clamp/映射到厂商参数。
    reasoning: Optional[str] = None
    # 透传给底层调用的其它关键字（如重试回调所需的内部标记）。
    passthrough: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider 契约
# ---------------------------------------------------------------------------
class ModelProvider(ABC):
    """所有 Provider adapter 必须实现的端口。

    不变量（Pi 第 5 / 7 节）：

    1. ``stream*`` 同步返回 ``AssistantMessageEventStream``；
    2. 每条流必须由终止事件（``done`` / ``error``）或 ``end(result)`` 提供唯一最终
       结果；
    3. 返回 stream 之前只能同步抛错（如明确缺少认证）；返回 stream 之后，任何错误
       都必须进入流，形成 ``error`` AssistantMessage，不得变成后台 unhandled 异常。
    """

    #: 人类可读的 provider 名称，用于日志/诊断。
    name: str = "provider"

    @abstractmethod
    def stream(self, request: ModelRequest, **provider_options: Any) -> AssistantMessageEventStream:
        """用厂商专有选项发起一次流式请求。"""
        raise NotImplementedError

    def stream_simple(
        self,
        request: ModelRequest,
        options: Optional[StreamOptions] = None,
    ) -> AssistantMessageEventStream:
        """用 provider-neutral 选项发起请求；默认把通用项透传给 ``stream()``。

        具体 adapter 可覆盖本方法，把 ``options.reasoning`` 等映射到厂商专有参数。
        """
        options = options or StreamOptions()
        return self.stream(request, stream=options.stream, **(options.passthrough or {}))
