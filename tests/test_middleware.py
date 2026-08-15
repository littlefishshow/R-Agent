"""Agent 中间件框架与内核运行时链测试。

覆盖：
1. hook 触发顺序：before_iteration -> before_model -> after_model -> before_tool
   -> after_tool -> after_iteration；
2. before_tool 否决可拦下某个工具（工具不执行，否决串作为结果）；
3. 单个中间件抛异常不打断主循环；
4. 内核运行时链始终存在，自定义中间件插入稳定位置；
5. 新增单工具/整批工具 hook 的链式改写和异常隔离。
"""

import json
from types import SimpleNamespace

from core.agent import RAgent
from core.middleware import Middleware, MiddlewareChain, AgentContext, ToolCallView
from core.middleware.builtins import (
    ContextCompressionMiddleware,
    DeferredToolFilterMiddleware,
    SoftIterationBudgetMiddleware,
    ToolOutputBudgetMiddleware,
    ToolResultTrackingMiddleware,
    ToolRuntimeStateMiddleware,
)
from tools.registry import registry


# --- 复用仓库现有的假 LLM 客户端形态 ---
class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        if not self._responses:
            raise AssertionError("unexpected extra LLM call")
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


def _tool_call(name, arguments):
    return SimpleNamespace(
        id=f"call_{name}",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)),
    )


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message):
    return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=message)])


# --------------------------------------------------------------------------- #
# 1. hook 顺序
# --------------------------------------------------------------------------- #
class _RecordingMiddleware(Middleware):
    name = "recording"

    def __init__(self):
        self.log = []

    def before_iteration(self, ctx):
        self.log.append("before_iteration")

    def before_model(self, ctx):
        self.log.append("before_model")

    def after_model(self, ctx):
        self.log.append("after_model")

    def before_tool(self, ctx, call):
        self.log.append(f"before_tool:{call.name}")
        return None

    def after_tool(self, ctx, call, result):
        self.log.append(f"after_tool:{call.name}")

    def after_iteration(self, ctx):
        self.log.append("after_iteration")


def test_hook_order_across_a_tool_turn(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def echo():
        return "ok"

    registry.register("echo_for_mw_test", "echo", {"type": "object", "properties": {}}, echo)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["echo_for_mw_test"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    rec = _RecordingMiddleware()
    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False, middlewares=[rec])
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("echo_for_mw_test", {})])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("go") == "done"

    # 第一轮（有工具）: bi, bm, am, before_tool, after_tool, after_iteration
    # 第二轮（无工具，最终答复）: bi, bm, am, after_iteration
    assert rec.log == [
        "before_iteration", "before_model", "after_model",
        "before_tool:echo_for_mw_test", "after_tool:echo_for_mw_test", "after_iteration",
        "before_iteration", "before_model", "after_model", "after_iteration",
    ]


# --------------------------------------------------------------------------- #
# 2. before_tool 否决
# --------------------------------------------------------------------------- #
class _VetoMiddleware(Middleware):
    name = "veto"

    def before_tool(self, ctx, call):
        if call.name == "blocked_tool_for_mw_test":
            return "被中间件拦截：该工具不允许执行。"
        return None


def test_before_tool_veto_blocks_execution(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    executed = {"count": 0}

    def blocked_tool():
        executed["count"] += 1
        return "SHOULD_NOT_RUN"

    registry.register("blocked_tool_for_mw_test", "blocked", {"type": "object", "properties": {}}, blocked_tool)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["blocked_tool_for_mw_test"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False, middlewares=[_VetoMiddleware()])
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("blocked_tool_for_mw_test", {})])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("try blocked") == "done"
    # 工具未被真正执行
    assert executed["count"] == 0
    # 否决串作为工具结果进入对话
    tool_msgs = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "被中间件拦截" in tool_msgs[0]["content"]


# --------------------------------------------------------------------------- #
# 3. 中间件异常不打断主循环
# --------------------------------------------------------------------------- #
class _BoomMiddleware(Middleware):
    name = "boom"

    def before_model(self, ctx):
        raise RuntimeError("intentional boom")


def test_middleware_exception_does_not_break_loop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False, middlewares=[_BoomMiddleware()])
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    assert agent.run_conversation("hi") == "ok"
    # 异常被链捕获记录
    assert any(e["phase"] == "before_model" for e in agent.middleware.errors)


# --------------------------------------------------------------------------- #
# 4. 默认 Agent 安装内核运行时链
# --------------------------------------------------------------------------- #
def test_agent_always_installs_runtime_middlewares(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    # 显式空列表只关闭可选/自定义链，不会移除运行时不变量。
    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    assert [type(mw) for mw in agent.middleware.middlewares[:5]] == [
        DeferredToolFilterMiddleware,
        ContextCompressionMiddleware,
        ToolRuntimeStateMiddleware,
        ToolResultTrackingMiddleware,
        ToolOutputBudgetMiddleware,
    ]
    assert isinstance(agent.middleware.middlewares[-1], SoftIterationBudgetMiddleware)
    agent.client = _FakeClient([_response(_message(content="answer", tool_calls=None))])
    assert agent.run_conversation("q") == "answer"


def test_explicit_middlewares_append_after_runtime_chain(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])

    custom = _RecordingMiddleware()
    agent = RAgent(
        model="test-model",
        max_iterations=2,
        enable_self_review=False,
        middlewares=[custom],
    )
    assert isinstance(agent.middleware.middlewares[0], DeferredToolFilterMiddleware)
    assert isinstance(agent.middleware.middlewares[1], ContextCompressionMiddleware)
    assert agent.middleware.middlewares[-3] is custom
    assert isinstance(agent.middleware.middlewares[-2], ToolOutputBudgetMiddleware)
    assert isinstance(agent.middleware.middlewares[-1], SoftIterationBudgetMiddleware)


def test_runtime_compression_runs_before_custom_before_model(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])
    observed = []

    class _Custom(Middleware):
        def before_model(self, ctx):
            observed.append(ctx.agent.context_usage.get("compressed_count"))

    agent = RAgent(
        model="test-model",
        max_iterations=2,
        enable_self_review=False,
        middlewares=[_Custom()],
    )
    monkeypatch.setattr(
        agent,
        "_maybe_compress_context",
        lambda tools, mw_ctx=None: agent.context_usage.update({"compressed_count": 7}),
    )
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    assert agent.run_conversation("hi") == "ok"
    assert observed == [7]


def test_deferred_filter_runs_before_context_compression(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    visible_schema = {"type": "function", "function": {"name": "visible"}}
    hidden_schema = {"type": "function", "function": {"name": "hidden"}}
    monkeypatch.setattr(
        registry,
        "get_all_schemas",
        lambda: [visible_schema, hidden_schema],
    )

    agent = RAgent(
        model="test-model",
        max_iterations=2,
        enable_self_review=False,
        middlewares=[],
    )
    monkeypatch.setattr(
        agent,
        "_apply_deferred_tool_filter",
        lambda tools: [schema for schema in tools if schema["function"]["name"] == "visible"],
    )
    observed = []
    monkeypatch.setattr(
        agent,
        "_maybe_compress_context",
        lambda tools, mw_ctx=None: observed.append(
            [schema["function"]["name"] for schema in tools]
        ),
    )
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])

    assert agent.run_conversation("hi") == "ok"
    assert observed == [["visible"]]


def test_after_tool_batch_chains_replacements():
    class _Append(Middleware):
        def __init__(self, suffix):
            self.suffix = suffix

        def after_tool_batch(self, ctx, calls, tool_messages):
            return [
                {**message, "content": message["content"] + self.suffix}
                for message in tool_messages
            ]

    chain = MiddlewareChain([_Append("-a"), _Append("-b")])
    messages = [{"role": "tool", "content": "value"}]
    result = chain.run_after_tool_batch(
        AgentContext(agent=None),
        [ToolCallView("demo", "{}", "call-demo")],
        messages,
    )
    assert result[0]["content"] == "value-a-b"


def test_new_tool_hooks_isolate_exceptions():
    class _Boom(Middleware):
        name = "boom-new-hooks"

        def after_tool_execution(self, ctx, call, result):
            raise RuntimeError("execution hook failed")

        def after_tool_batch(self, ctx, calls, tool_messages):
            raise RuntimeError("batch hook failed")

        def before_tool_message(self, ctx, call, result):
            raise RuntimeError("message hook failed")

    chain = MiddlewareChain([_Boom()])
    ctx = AgentContext(agent=None)
    call = ToolCallView("demo", "{}", "call-demo")

    assert chain.run_after_tool_execution(ctx, call, "value") is None
    assert chain.run_after_tool_batch(
        ctx,
        [call],
        [{"role": "tool", "content": "value"}],
    ) is None
    chain.run_before_tool_message(ctx, call, "value")
    assert [error["phase"] for error in chain.errors] == [
        "after_tool_execution",
        "after_tool_batch",
        "before_tool_message",
    ]
