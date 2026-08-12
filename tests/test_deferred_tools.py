"""工具目录 + 延迟暴露测试（Improve_progress/06）。

覆盖：
1. registry 目录/检索：get_tool_catalog / search_catalog / get_schemas_for；metadata 兜底。
2. _apply_deferred_tool_filter：默认关=全量（零行为变化）；开启=仅 always-on+已提升。
3. tool_search 提升：执行 tool_search 后命中的工具进入 _promoted_tools。
4. 端到端：默认关闭时 _loop 行为不变。
"""

import json
from types import SimpleNamespace

import core.config as cfg
from core.agent import RAgent
from tools.registry import registry


# --- 假 LLM ---
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
    return SimpleNamespace(id=f"call_{name}", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _message(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message):
    return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=message)])


def _schema(name):
    return {"type": "function", "function": {"name": name, "description": f"{name} does things", "parameters": {"type": "object", "properties": {}}}}


# --------------------------------------------------------------------------- #
# 1. registry 目录
# --------------------------------------------------------------------------- #
def test_catalog_summary_and_metadata_fallback():
    registry.reload_all()
    registry.register("cat_tool_a", "Alpha capability line one.\nignored second.", {"type": "object", "properties": {}}, lambda: "a")
    registry.register("cat_tool_b", "Beta", {"type": "object", "properties": {}}, lambda: "b", metadata={"summary": "custom beta summary", "category": "x"})
    cat = {c["name"]: c for c in registry.get_tool_catalog()}
    assert cat["cat_tool_a"]["summary"] == "Alpha capability line one."   # description 首行兜底
    assert cat["cat_tool_b"]["summary"] == "custom beta summary"          # metadata 优先
    assert cat["cat_tool_b"]["category"] == "x"


def test_search_catalog_and_schemas_for():
    registry.reload_all()
    registry.register("web_fetch_x", "Fetch a web page.", {"type": "object", "properties": {}}, lambda: "", metadata={"category": "web"})
    res = registry.search_catalog("web fetch")
    assert any(r["name"] == "web_fetch_x" for r in res)
    sc = registry.get_schemas_for(["web_fetch_x"])
    assert len(sc) == 1 and sc[0]["function"]["name"] == "web_fetch_x"


# --------------------------------------------------------------------------- #
# 2. deferred filter
# --------------------------------------------------------------------------- #
def test_deferred_filter_off_is_passthrough(monkeypatch):
    monkeypatch.delenv("DEFERRED_TOOLS_ENABLED", raising=False)
    agent = RAgent(model="m", enable_self_review=False)
    tools = [_schema("a"), _schema("b"), _schema("tool_search")]
    assert agent._apply_deferred_tool_filter(tools) == tools  # 全量，零变化


def test_deferred_filter_on_hides_unpromoted(monkeypatch):
    monkeypatch.setenv("DEFERRED_TOOLS_ENABLED", "1")
    monkeypatch.setenv("DEFERRED_TOOLS_ALWAYS_ON", "tool_search,todo_manage")
    agent = RAgent(model="m", enable_self_review=False)
    tools = [_schema("web_fetch_x"), _schema("tool_search"), _schema("todo_manage")]
    visible = {s["function"]["name"] for s in agent._apply_deferred_tool_filter(tools)}
    assert visible == {"tool_search", "todo_manage"}  # web_fetch_x 隐藏
    # 提升后可见
    agent._promoted_tools.add("web_fetch_x")
    visible2 = {s["function"]["name"] for s in agent._apply_deferred_tool_filter(tools)}
    assert "web_fetch_x" in visible2


# --------------------------------------------------------------------------- #
# 3. tool_search 提升解析
# --------------------------------------------------------------------------- #
def test_promote_from_tool_search():
    agent = RAgent(model="m", enable_self_review=False)
    # 模拟 execute_tool 返回的外层 JSON（{"success":true,"result":"<inner json>"}）
    inner = {"success": True, "matches": [{"name": "web_fetch_x"}, {"name": "read_file"}]}
    outer = json.dumps({"success": True, "result": json.dumps(inner)})
    agent._maybe_promote_from_tool_search("tool_search", outer)
    assert "web_fetch_x" in agent._promoted_tools and "read_file" in agent._promoted_tools
    # 非 tool_search 不提升
    agent._maybe_promote_from_tool_search("something_else", outer)


# --------------------------------------------------------------------------- #
# 4. 端到端：默认关闭时行为不变
# --------------------------------------------------------------------------- #
def test_loop_unaffected_when_deferred_off(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEFERRED_TOOLS_ENABLED", raising=False)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [_schema("a")])

    agent = RAgent(model="m", max_iterations=2, enable_self_review=False)
    agent.client = _FakeClient([_response(_message(content="ok", tool_calls=None))])
    assert agent.run_conversation("hi") == "ok"


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("DEFERRED_TOOLS_ENABLED", raising=False)
    assert cfg.get_deferred_tools_enabled() is False
    assert "tool_search" in cfg.get_deferred_tools_always_on()
