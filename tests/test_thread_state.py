"""ThreadState 结构化状态测试（Improve_progress/02）。

覆盖三类验证点：
1. reducer 的合并/去重/容错行为；
2. RAgent 兼容属性：读、原地修改、整体重赋值都代理到 self.state，零行为变化；
3. 主循环运行时会把大工具产物写入 artifact_index channel。
"""

import json
from pathlib import Path
from types import SimpleNamespace

from core.agent import RAgent
from core.state import (
    ThreadState,
    merge_artifacts,
    merge_delegations,
    merge_skill_context,
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
# 1. reducers
# --------------------------------------------------------------------------- #
def test_merge_artifacts_dedupes_by_path():
    idx = []
    merge_artifacts(idx, {"path": "p", "summary": "a"})
    merge_artifacts(idx, {"path": "p", "summary": "b"})  # 同 path -> 覆盖
    merge_artifacts(idx, {"path": "q", "summary": "c"})  # 新 path -> 追加
    assert len(idx) == 2
    assert idx[0]["summary"] == "b"
    assert idx[1]["path"] == "q"


def test_merge_delegations_dedupes_by_task_id():
    led = []
    merge_delegations(led, {"task_id": "t1", "status": "running"})
    merge_delegations(led, {"task_id": "t1", "status": "completed"})  # 同 task -> 合并成最新
    merge_delegations(led, {"task_id": "t2", "status": "blocked"})
    assert len(led) == 2
    assert led[0]["status"] == "completed"


def test_merge_skill_context_dedupes_by_skill():
    sc = []
    merge_skill_context(sc, {"skill": "s", "summary": "1"})
    merge_skill_context(sc, {"skill": "s", "summary": "2"})
    assert len(sc) == 1 and sc[0]["summary"] == "2"


def test_reducers_tolerate_junk():
    idx, led, sc = [], [], []
    # 非 dict / None 直接跳过，绝不抛异常
    merge_artifacts(idx, None)
    merge_delegations(led, "bad")
    merge_skill_context(sc, 42)
    assert idx == [] and led == [] and sc == []


# --------------------------------------------------------------------------- #
# 2. RAgent 兼容属性
# --------------------------------------------------------------------------- #
def test_agent_state_backward_compat_properties():
    agent = RAgent(model="test-model", enable_self_review=False)
    assert isinstance(agent.state, ThreadState)

    # 读
    assert agent.messages == []
    assert agent.token_usage["total_tokens"] == 0

    # 原地修改（append / dict item 自增）应反映到 state
    agent.messages.append({"role": "user", "content": "hi"})
    assert agent.state.messages == [{"role": "user", "content": "hi"}]
    agent.token_usage["total_tokens"] += 7
    assert agent.state.token_usage["total_tokens"] == 7

    # 整体重赋值应写回 state（app_gui/runtime.py 大量这样用）
    agent.messages = [{"role": "system", "content": "x"}]
    assert agent.state.messages == [{"role": "system", "content": "x"}]

    # 计量字段重赋值也要生效
    agent.context_usage = {"estimated_tokens": 123}
    assert agent.state.context_usage["estimated_tokens"] == 123


# --------------------------------------------------------------------------- #
# 3. 主循环填充 artifact_index
# --------------------------------------------------------------------------- #
def test_loop_populates_artifact_index(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def huge_tool():
        return "A" * 90_000  # 超过默认阈值 -> 落盘为 artifact

    registry.register("huge_tool_for_state_test", "huge", {"type": "object", "properties": {}}, huge_tool)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["huge_tool_for_state_test"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda name, args, **kwargs: registry.execute_tool(name, args))

    agent = RAgent(model="test-model", max_iterations=3, enable_self_review=False)
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("huge_tool_for_state_test", {})])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("use huge") == "done"

    # artifact_index 应记录该产物，且 path 指向真实落盘文件
    assert len(agent.state.artifact_index) == 1
    entry = agent.state.artifact_index[0]
    assert entry["tool"] == "huge_tool_for_state_test"
    assert entry["path"]
    assert Path(entry["path"]).exists()
