"""子 Agent 委派契约测试（Improve_progress/05）。

覆盖：
1. _derive_stop_reason / _normalize_result_contract 的映射与只增不改；
2. 端到端：截断任务在 compact 返回里带 stop_reason=turn_capped，且不破坏既有 status；
3. LoopDetectionMiddleware 触发 loop_capped，并体现在 delegate 结果里；
4. started_at/completed_at 附加字段存在。
"""

import json

import core.agent
from core.state import ThreadState
from core.middleware import AgentContext, ToolCallView
from core.middleware.builtins import LoopDetectionMiddleware
from tools import delegate_tool
from tools import todo_tool


# --------------------------------------------------------------------------- #
# 1. stop_reason 纯函数
# --------------------------------------------------------------------------- #
def test_derive_stop_reason_mapping():
    d = delegate_tool._derive_stop_reason
    assert d({"status": "success"}) == "completed"
    assert d({"status": "timeout"}) == "timeout"
    assert d({"status": "truncated", "truncated": True}) == "turn_capped"
    assert d({"status": "error"}) == "error"
    # 显式 stop_reason 优先
    assert d({"status": "success", "stop_reason": "loop_capped"}) == "loop_capped"


def test_normalize_only_adds_fields():
    item = {"status": "success", "truncated": False, "task_id": "t"}
    out = delegate_tool._normalize_result_contract(item)
    assert out["stop_reason"] == "completed"
    # 既有字段不变
    assert out["status"] == "success" and out["truncated"] is False and out["task_id"] == "t"


# --------------------------------------------------------------------------- #
# 2. 端到端：截断 -> turn_capped
# --------------------------------------------------------------------------- #
class _TruncatingAgent:
    def __init__(self, max_iterations=None, **kwargs):  # 接受 middlewares/session_id 等新参数
        self.max_iterations = max_iterations

    def run_conversation(self, **kwargs):
        task_id = kwargs["user_message"].split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = kwargs["user_message"].split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage(
            "claim",
            json.dumps({"id": task_id, "worker_id": worker_id, "max_iterations": self.max_iterations}, ensure_ascii=False),
        )
        return "⚠️ **已达迭代上限，以下为强制收尾结果。**"

    def is_truncated(self):
        return True


def test_truncated_task_reports_turn_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(core.agent, "RAgent", _TruncatingAgent)

    todo_tool.todo_manage(
        "init",
        json.dumps({"tasks": [{"id": "t1", "description": "会被截断", "dependencies": []}]}, ensure_ascii=False),
    )

    result = delegate_tool.delegate_task(
        tasks=json.dumps([{"task_id": "t1", "goal": "触发截断", "max_iterations": 1}], ensure_ascii=False),
        max_workers=1,
        default_max_iterations=1,
    )
    task0 = json.loads(result)["tasks"][0]
    assert task0["status"] == "truncated"       # 既有语义不变
    assert task0["stop_reason"] == "turn_capped"  # 新增契约字段


# --------------------------------------------------------------------------- #
# 3. loop detection -> loop_capped
# --------------------------------------------------------------------------- #
class _LoopingAgent:
    """模拟一个真正跑循环检测中间件的子 Agent。"""

    def __init__(self, max_iterations=None, middlewares=None, **kwargs):
        self.max_iterations = max_iterations
        from core.middleware import MiddlewareChain, build_default_middlewares

        self.middleware = MiddlewareChain(middlewares if middlewares is not None else build_default_middlewares())
        self._loop_capped = False

    def run_conversation(self, **kwargs):
        task_id = kwargs["user_message"].split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = kwargs["user_message"].split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage(
            "claim",
            json.dumps({"id": task_id, "worker_id": worker_id, "max_iterations": self.max_iterations}, ensure_ascii=False),
        )
        # 模拟连续相同工具调用，直到循环检测否决
        ctx = AgentContext(agent=self)
        for _ in range(5):
            denial = self.middleware.run_before_tool(ctx, ToolCallView("spin", '{"same": 1}'))
            if denial:
                break
        return "已停止。"

    def is_truncated(self):
        return False


class _SteppingAgent:
    def __init__(self, max_iterations=None, **kwargs):
        self.max_iterations = max_iterations

    def run_conversation(self, **kwargs):
        task_id = kwargs["user_message"].split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = kwargs["user_message"].split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage(
            "claim",
            json.dumps({"id": task_id, "worker_id": worker_id, "max_iterations": self.max_iterations}, ensure_ascii=False),
        )
        kwargs["on_think"](0)
        kwargs["on_tool_start"]("read_file", '{"path":"README.md"}')
        kwargs["on_tool_end"]("read_file", "README preview")
        return "已完成。"

    def is_truncated(self):
        return False


def test_loop_detection_reports_loop_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(core.agent, "RAgent", _LoopingAgent)
    # 确保子 Agent 会装上 loop detection
    monkeypatch.setenv("LOOP_DETECTION_ENABLED", "1")
    monkeypatch.setenv("LOOP_DETECTION_THRESHOLD", "3")

    todo_tool.todo_manage(
        "init",
        json.dumps({"tasks": [{"id": "t1", "description": "会陷入循环", "dependencies": []}]}, ensure_ascii=False),
    )

    result = delegate_tool.delegate_task(
        tasks=json.dumps([{"task_id": "t1", "goal": "循环", "max_iterations": 10}], ensure_ascii=False),
        max_workers=1,
        default_max_iterations=10,
    )
    task0 = json.loads(result)["tasks"][0]
    assert task0["stop_reason"] == "loop_capped"


def test_step_events_are_bounded_and_included(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(core.agent, "RAgent", _SteppingAgent)
    monkeypatch.setenv("DELEGATE_STEP_EVENTS_LIMIT", "3")

    todo_tool.todo_manage(
        "init",
        json.dumps({"tasks": [{"id": "t1", "description": "记录步骤", "dependencies": []}]}, ensure_ascii=False),
    )

    result = delegate_tool.delegate_task(
        tasks=json.dumps([{"task_id": "t1", "goal": "记录步骤"}], ensure_ascii=False),
        max_workers=1,
    )
    task0 = json.loads(result)["tasks"][0]
    assert [event["event_type"] for event in task0["step_events"]] == [
        "llm.step",
        "tool.start",
        "tool.end",
    ]
    assert task0["step_events"][1]["arguments_preview"] == '{"path":"README.md"}'
    assert task0["step_events"][2]["result_preview"] == "README preview"


# --------------------------------------------------------------------------- #
# 4. 时间戳（full 模式保留原始字段）
# --------------------------------------------------------------------------- #
def test_timestamps_present_full_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(core.agent, "RAgent", _TruncatingAgent)

    todo_tool.todo_manage(
        "init",
        json.dumps({"tasks": [{"id": "t1", "description": "x", "dependencies": []}]}, ensure_ascii=False),
    )

    result = delegate_tool.delegate_task(
        tasks=json.dumps([{"task_id": "t1", "goal": "g", "max_iterations": 1}], ensure_ascii=False),
        max_workers=1,
        default_max_iterations=1,
        return_mode="full",
    )
    task0 = json.loads(result)["tasks"][0]
    assert "started_at" in task0 and "completed_at" in task0
    assert task0["completed_at"] >= task0["started_at"]
    assert task0["stop_reason"] == "turn_capped"


class _PrefixCaptureAgent:
    instances = []

    def __init__(self, max_iterations=None, session_id="", **kwargs):
        self.max_iterations = max_iterations
        self.session_id = session_id
        self.state = ThreadState()
        self.messages = []
        self._durable_snapshot = None
        self.system_message = ""
        self.user_message = ""
        self.current_content = ""
        self.__class__.instances.append(self)

    def run_conversation(self, **kwargs):
        self.system_message = kwargs["system_message"]
        self.user_message = kwargs["user_message"]
        self.current_content = self.state.durable_current.get("content", "")
        task_id = self.user_message.split("task_id: ", 1)[1].split("\n", 1)[0]
        worker_id = self.user_message.split("worker_id: ", 1)[1].split("\n", 1)[0]
        todo_tool.todo_manage(
            "claim",
            json.dumps({"id": task_id, "worker_id": worker_id}, ensure_ascii=False),
        )
        todo_tool.todo_manage(
            "update",
            json.dumps({"id": task_id, "status": "completed", "result": "done"}, ensure_ascii=False),
        )
        return "done"

    def get_token_usage_summary(self, include_children=False):
        return {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "available": True}

    def get_cache_usage(self):
        return {
            "request_count": 2,
            "local_prompt_tokens": 100,
            "local_reusable_tokens": 70,
            "local_new_tokens": 30,
            "kv_reuse_ratio": 0.7,
        }

    def is_truncated(self):
        return False


def test_sibling_agents_share_static_system_and_frozen_durable_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_tool, "TODO_FILE", str(tmp_path / "todo_list.json"))
    monkeypatch.setattr(core.agent, "RAgent", _PrefixCaptureAgent)
    _PrefixCaptureAgent.instances = []
    todo_tool.todo_manage(
        "init",
        json.dumps({
            "tasks": [
                {"id": "a", "description": "task A"},
                {"id": "b", "description": "task B"},
            ]
        }, ensure_ascii=False),
    )

    payload = json.loads(delegate_tool.delegate_task(
        tasks=json.dumps([
            {"task_id": "a", "goal": "goal-A", "context_summary": "ctx-A"},
            {"task_id": "b", "goal": "goal-B", "context_summary": "ctx-B"},
        ], ensure_ascii=False),
        max_workers=2,
    ))

    agents = _PrefixCaptureAgent.instances
    assert len(agents) == 2
    assert {agent.system_message for agent in agents} == {delegate_tool.SUBAGENT_SYSTEM_PROMPT}
    assert all("goal-A" not in agent.system_message and "goal-B" not in agent.system_message for agent in agents)
    assert len({agent.current_content for agent in agents}) == 1
    assert all("<durable_unfinished_todos>" in agent.current_content for agent in agents)
    assert any("goal: goal-A" in agent.user_message for agent in agents)
    assert any("goal: goal-B" in agent.user_message for agent in agents)
    prefix_hashes = {task["delegation_prefix_hash"] for task in payload["tasks"]}
    assert len(prefix_hashes) == 1
    assert payload["delegated_cache_usage"]["observed_agents"] == 2
    assert payload["delegated_cache_usage"]["kv_reuse_ratio"] == 0.7
