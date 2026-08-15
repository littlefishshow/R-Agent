"""R-Agent 的轻量运行时中间件框架。

``RAgent._loop`` 保留模型/工具控制流，横切逻辑通过职责单一、可编排的 hook 运行。
内核运行时中间件始终安装；配置中间件和调用方中间件插入稳定扩展位置。

hook 阶段：

* ``before_iteration(ctx)``  每轮循环开始
* ``before_model(ctx)``      调用模型前（此时 tools 已组装、上下文已压缩）
* ``after_model(ctx)``       拿到模型回复后
* ``before_tool(ctx, call)`` 执行某个工具前——可返回字符串**否决**该工具（作为工具结果）
* ``after_tool_execution(ctx, call, result)`` handler 返回后的单结果处理
* ``after_tool_batch(ctx, calls, tool_messages)`` 同一轮全部工具结果的聚合处理
* ``after_tool(ctx, call, result)`` 最终 tool message 写入历史前
* ``after_iteration(ctx)``   每轮循环结束
* ``after_context_compression(ctx)`` 上下文压缩成功后

安全原则：单个中间件抛异常**绝不打断主循环**（与 events/durable-context 一致）。
``MiddlewareChain`` 捕获每个 hook 的异常并记录，然后继续。
"""

from __future__ import annotations

from typing import Any, Optional


class AgentContext:
    """贯穿一轮/一步的运行上下文，作为中间件读写状态的统一入口。

    刻意保持薄：持有对 agent、当前 iteration、tools、当前消息与 event_sink 的引用。
    中间件通过 ``ctx.agent.state``（ThreadState）读写结构化 channel。
    """

    __slots__ = ("agent", "iteration", "tools", "message", "event_sink", "extra")

    def __init__(self, agent, iteration: int = 0, tools=None, message=None, event_sink=None):
        self.agent = agent
        self.iteration = iteration
        self.tools = tools if tools is not None else []
        self.message = message
        self.event_sink = event_sink
        self.extra: dict = {}


class ToolCallView:
    """传给 before_tool/after_tool 的工具调用视图（只读关键信息）。"""

    __slots__ = ("name", "arguments", "call_id")

    def __init__(self, name: str, arguments: Any, call_id: Optional[str] = None):
        self.name = name
        self.arguments = arguments
        self.call_id = call_id


class Middleware:
    """中间件基类：所有 hook 默认 no-op，子类只覆盖关心的阶段。"""

    #: 便于日志/调试识别
    name: str = "middleware"

    def before_iteration(self, ctx: AgentContext) -> None:  # noqa: D401
        return None

    def before_model(self, ctx: AgentContext) -> None:
        return None

    def after_model(self, ctx: AgentContext) -> None:
        return None

    def before_tool(self, ctx: AgentContext, call: ToolCallView) -> Optional[str]:
        """执行工具前调用。返回非空字符串表示**否决**该工具，该字符串会作为工具结果。"""
        return None

    def after_tool_execution(self, ctx: AgentContext, call: ToolCallView, result: Any) -> Optional[str]:
        """工具 handler 返回后调用；可在整轮预算前改写单个结果。"""
        return None

    def after_tool_batch(self, ctx: AgentContext, calls: list[ToolCallView], tool_messages: list[dict]) -> Optional[list[dict]]:
        """同一 assistant turn 的全部工具执行完后调用；可改写整批 tool messages。"""
        return None

    def before_tool_message(self, ctx: AgentContext, call: ToolCallView, result: Any) -> None:
        """预算处理完成后、外部回调和最终文本清洗前调用。"""
        return None

    def after_tool(self, ctx: AgentContext, call: ToolCallView, result: Any) -> Optional[str]:
        """最终 tool message 写入历史前调用；返回非空字符串表示改写结果。"""
        return None

    def after_iteration(self, ctx: AgentContext) -> None:
        return None

    def after_context_compression(self, ctx: AgentContext) -> None:
        """上下文实际压缩成功后调用。"""
        return None


class MiddlewareChain:
    """按注册顺序运行中间件的调度器；单个中间件异常被吞掉，绝不打断主循环。"""

    def __init__(self, middlewares=None):
        self._middlewares = list(middlewares or [])
        self.errors: list = []

    def __len__(self) -> int:
        return len(self._middlewares)

    def __bool__(self) -> bool:
        return bool(self._middlewares)

    @property
    def middlewares(self) -> list:
        return list(self._middlewares)

    def _record_error(self, phase: str, mw, exc: Exception) -> None:
        self.errors.append({"phase": phase, "middleware": getattr(mw, "name", type(mw).__name__), "error": str(exc)})

    def run_before_iteration(self, ctx: AgentContext) -> None:
        for mw in self._middlewares:
            try:
                mw.before_iteration(ctx)
            except Exception as exc:  # noqa: BLE001
                self._record_error("before_iteration", mw, exc)

    def run_before_model(self, ctx: AgentContext) -> None:
        for mw in self._middlewares:
            try:
                mw.before_model(ctx)
            except Exception as exc:  # noqa: BLE001
                self._record_error("before_model", mw, exc)

    def run_after_model(self, ctx: AgentContext) -> None:
        for mw in self._middlewares:
            try:
                mw.after_model(ctx)
            except Exception as exc:  # noqa: BLE001
                self._record_error("after_model", mw, exc)

    def run_before_tool(self, ctx: AgentContext, call: ToolCallView) -> Optional[str]:
        """返回第一个非空否决字符串；否则 None。"""
        for mw in self._middlewares:
            try:
                denial = mw.before_tool(ctx, call)
            except Exception as exc:  # noqa: BLE001
                self._record_error("before_tool", mw, exc)
                continue
            if denial:
                return str(denial)
        return None

    def run_after_tool_execution(self, ctx: AgentContext, call: ToolCallView, result: Any) -> Optional[str]:
        """链式运行单工具后处理；后一个中间件读取前一个的改写结果。"""
        replaced: Optional[str] = None
        current = result
        for mw in self._middlewares:
            try:
                out = mw.after_tool_execution(ctx, call, current)
            except Exception as exc:  # noqa: BLE001
                self._record_error("after_tool_execution", mw, exc)
                continue
            if out is not None:
                replaced = str(out)
                current = replaced
        return replaced

    def run_after_tool_batch(
        self,
        ctx: AgentContext,
        calls: list[ToolCallView],
        tool_messages: list[dict],
    ) -> Optional[list[dict]]:
        """链式运行整批工具后处理；返回最终（可能替换的）消息列表。"""
        replaced: Optional[list[dict]] = None
        current = tool_messages
        for mw in self._middlewares:
            try:
                out = mw.after_tool_batch(ctx, calls, current)
            except Exception as exc:  # noqa: BLE001
                self._record_error("after_tool_batch", mw, exc)
                continue
            if out is not None:
                replaced = list(out)
                current = replaced
        return replaced

    def run_before_tool_message(self, ctx: AgentContext, call: ToolCallView, result: Any) -> None:
        for mw in self._middlewares:
            try:
                mw.before_tool_message(ctx, call, result)
            except Exception as exc:  # noqa: BLE001
                self._record_error("before_tool_message", mw, exc)

    def run_after_tool(self, ctx: AgentContext, call: ToolCallView, result: Any) -> Optional[str]:
        """依次调用 after_tool；若某中间件返回改写串，则用它作为后续结果并继续。

        返回最终（可能被改写的）结果字符串；若无任何中间件改写则返回 None。
        """
        replaced: Optional[str] = None
        current = result
        for mw in self._middlewares:
            try:
                out = mw.after_tool(ctx, call, current)
            except Exception as exc:  # noqa: BLE001
                self._record_error("after_tool", mw, exc)
                continue
            if out is not None:
                replaced = str(out)
                current = replaced
        return replaced

    def run_after_iteration(self, ctx: AgentContext) -> None:
        for mw in self._middlewares:
            try:
                mw.after_iteration(ctx)
            except Exception as exc:  # noqa: BLE001
                self._record_error("after_iteration", mw, exc)

    def run_after_context_compression(self, ctx: AgentContext) -> None:
        for mw in self._middlewares:
            try:
                mw.after_context_compression(ctx)
            except Exception as exc:  # noqa: BLE001
                self._record_error("after_context_compression", mw, exc)


def build_runtime_middlewares(optional_middlewares=None) -> list:
    """构造完整运行时链，并把调用方中间件放到稳定的扩展位置。"""
    from core.middleware.builtins import (
        ContextCompressionMiddleware,
        DeferredToolFilterMiddleware,
        SoftIterationBudgetMiddleware,
        ToolOutputBudgetMiddleware,
        ToolResultTrackingMiddleware,
        ToolRuntimeStateMiddleware,
    )

    return [
        DeferredToolFilterMiddleware(),
        ContextCompressionMiddleware(),
        ToolRuntimeStateMiddleware(),
        ToolResultTrackingMiddleware(),
        *list(optional_middlewares or []),
        # 调用方可以先规范化结果，但输出预算必须最后兜底，避免重新放大上下文。
        ToolOutputBudgetMiddleware(),
        # 保持旧顺序：调用方 before_iteration 先运行，随后才注入软预算提醒。
        SoftIterationBudgetMiddleware(),
    ]


def build_default_middlewares() -> list:
    """按 config 开关构造可选中间件。

    内核运行时中间件由 :func:`build_runtime_middlewares` 单独构造；本函数只返回：
      * TOOL_SANITIZATION_ENABLED=1        -> ToolResultSanitizationMiddleware
      * MEMORY_WRITE_MIDDLEWARE_ENABLED=1  -> MemoryWriteMiddleware
    """
    chain: list = []
    try:
        from core import config
        from core.middleware.builtins import (
            MemoryWriteMiddleware,
            ToolResultSanitizationMiddleware,
        )

        if config.get_tool_sanitization_enabled():
            chain.append(ToolResultSanitizationMiddleware(mode=config.get_tool_sanitization_mode()))
        if config.get_memory_write_middleware_enabled():
            chain.append(MemoryWriteMiddleware())
    except Exception:
        # 组装失败绝不能影响 Agent 启动；退回空链。
        return []
    return chain
