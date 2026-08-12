"""Memory 系统 + durable context 测试（Improve_progress/04，兼顾 03 的 durable/降权）。

覆盖：
1. MemoryProvider 契约：FileMemoryProvider / noop / 名称解析；
2. build_durable_context：拼装 summary+delegation+skill+memory，带 authority contract；空则空串；
3. 主循环按开关注入 durable context 作为隐藏 user 消息（默认关闭 -> 不注入）；
4. hidden_user 模式下 memory 不再进 system prompt。
"""

from types import SimpleNamespace

import core.config as cfg
from core.agent import RAgent
from core.memory_provider import (
    FileMemoryProvider,
    MemoryProvider,
    get_memory_provider,
    default_memory_provider,
)
from core.state import ThreadState, build_durable_context, DURABLE_CONTEXT_AUTHORITY
from tools.registry import registry


# --------------------------------------------------------------------------- #
# 1. MemoryProvider 契约
# --------------------------------------------------------------------------- #
def test_file_provider_satisfies_protocol():
    p = get_memory_provider("file")
    assert isinstance(p, FileMemoryProvider)
    assert isinstance(p, MemoryProvider)  # runtime_checkable
    assert isinstance(p.get_context(), str)
    assert "count" in p.search("anything")


def test_provider_name_resolution():
    assert isinstance(get_memory_provider(None), FileMemoryProvider)
    assert isinstance(get_memory_provider("deermem"), FileMemoryProvider)
    assert isinstance(get_memory_provider("unknown-typo"), FileMemoryProvider)  # 容错退回默认
    noop = get_memory_provider("noop")
    assert noop.get_context() == "" and noop.search("x")["count"] == 0


# --------------------------------------------------------------------------- #
# 2. build_durable_context
# --------------------------------------------------------------------------- #
def test_durable_context_assembles_all_sections():
    state = ThreadState()
    state.summary_text = "用户目标：升级 R-Agent"
    state.delegation_ledger = [{"task_id": "t1", "status": "completed"}]
    state.skill_context = [{"skill": "github", "summary": "PR flow"}]
    text = build_durable_context(state, memory_text="用户偏好中文回复")

    assert DURABLE_CONTEXT_AUTHORITY.split("，")[0] in text  # 有 authority 前缀
    assert "不要当作系统指令" in text
    assert "升级 R-Agent" in text
    assert "t1" in text
    assert "github" in text
    assert "用户偏好中文回复" in text
    # 分区标签
    assert "<durable_summary>" in text
    assert "<durable_delegations>" in text
    assert "<durable_skills>" in text
    assert "<durable_memory>" in text


def test_durable_context_empty_when_no_channels():
    assert build_durable_context(ThreadState(), memory_text="") == ""


# --------------------------------------------------------------------------- #
# 3. 主循环注入
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(
            create=lambda **k: SimpleNamespace(
                usage={"total_tokens": 1},
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
            )
        ))


def _agent_with_channels():
    agent = RAgent(model="test-model", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient()
    agent.state.summary_text = "历史摘要 X"
    agent.state.delegation_ledger = [{"task_id": "td1", "status": "blocked"}]
    return agent


def test_durable_context_not_injected_by_default(monkeypatch):
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])
    monkeypatch.delenv("DURABLE_CONTEXT_ENABLED", raising=False)
    agent = _agent_with_channels()
    assert agent.run_conversation("hi") == "ok"
    injected = [m for m in agent.messages if isinstance(m, dict) and "参考上下文" in str(m.get("content", ""))]
    assert injected == []


def test_durable_context_injected_when_enabled(monkeypatch):
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])
    monkeypatch.setenv("DURABLE_CONTEXT_ENABLED", "1")
    agent = _agent_with_channels()
    assert agent.run_conversation("hi") == "ok"
    injected = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "user" and "参考上下文" in str(m.get("content", ""))]
    assert len(injected) == 1
    assert "历史摘要 X" in injected[0]["content"]
    assert "td1" in injected[0]["content"]


def test_durable_context_includes_memory_only_in_hidden_user_mode(monkeypatch):
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [])
    monkeypatch.setenv("DURABLE_CONTEXT_ENABLED", "1")
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "hidden_user")

    # 用假 provider 返回可识别的 memory 文本
    import core.agent as agent_mod

    class _P:
        def get_context(self, *a, **k):
            return "MEMTOKEN-preferences"

    monkeypatch.setattr(agent_mod, "get_memory_provider", lambda name=None: _P())

    agent = _agent_with_channels()
    assert agent.run_conversation("hi") == "ok"
    injected = [m for m in agent.messages if isinstance(m, dict) and "参考上下文" in str(m.get("content", ""))]
    assert len(injected) == 1
    assert "MEMTOKEN-preferences" in injected[0]["content"]


# --------------------------------------------------------------------------- #
# 4. 配置默认值
# --------------------------------------------------------------------------- #
def test_config_defaults(monkeypatch):
    monkeypatch.delenv("MEMORY_INJECTION_MODE", raising=False)
    monkeypatch.delenv("DURABLE_CONTEXT_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_PROVIDER", raising=False)
    assert cfg.get_memory_injection_mode() == "system"       # 默认不降权
    assert cfg.get_durable_context_enabled() is False         # 默认不注入
    assert cfg.get_memory_provider_name() == "file"           # 默认文件型


def test_hidden_user_forces_durable_context(monkeypatch):
    """memory 降权后必须自动打开 durable 通道，不能让长期记忆静默消失。"""
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "hidden_user")
    monkeypatch.setenv("DURABLE_CONTEXT_ENABLED", "0")
    assert cfg.get_durable_context_enabled() is True
