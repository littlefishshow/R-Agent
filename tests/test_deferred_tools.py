"""工具目录 + 延迟暴露测试（Improve_progress/06）。

覆盖：
1. registry 目录/检索：get_tool_catalog / search_catalog / get_schemas_for；metadata 兜底。
2. _apply_deferred_tool_filter：默认关=全量；开启=固定 always-on/control schemas。
3. tool_search 发现：完整契约进入消息/durable next，不改变顶层 tools=。
4. 端到端：默认关闭时 _loop 行为不变。
"""

import json
from types import SimpleNamespace

import core.config as cfg
from core.agent import RAgent
from tools.registry import registry
from tools.tool_search_tool import tool_search


# --- 假 LLM ---
class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
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


def test_tool_search_returns_full_contract_in_tool_message(monkeypatch):
    monkeypatch.setattr(registry, "search_catalog", lambda query, limit=8: [{
        "name": "dynamic_x",
        "summary": "dynamic summary",
        "category": "demo",
    }])
    monkeypatch.setattr(registry, "get_schemas_for", lambda names: [{
        "type": "function",
        "function": {
            "name": "dynamic_x",
            "description": "full description",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }])

    payload = json.loads(tool_search("dynamic"))
    contract = payload["matches"][0]
    assert contract["name"] == "dynamic_x"
    assert contract["description"] == "full description"
    assert contract["parameters"]["required"] == ["query"]
    assert payload["returned_contracts"] == 1
    assert "invoke_dynamic_tool" in payload["hint"]


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
    # 发现后仍不进入顶层 tools=，只允许经固定 dispatcher 调用。
    agent._register_dynamic_tool_contracts([{
        "name": "web_fetch_x",
        "description": "Fetch a page",
        "parameters": {"type": "object", "properties": {}},
    }])
    visible2 = {s["function"]["name"] for s in agent._apply_deferred_tool_filter(tools)}
    assert "web_fetch_x" not in visible2
    assert agent._dynamic_tool_contract("web_fetch_x") is not None


# --------------------------------------------------------------------------- #
# 3. tool_search 动态契约解析
# --------------------------------------------------------------------------- #
def test_promote_from_tool_search():
    agent = RAgent(model="m", enable_self_review=False)
    # 模拟 execute_tool 返回的外层 JSON（{"success":true,"result":"<inner json>"}）
    inner = {"success": True, "matches": [
        {"name": "web_fetch_x", "description": "fetch", "parameters": {"type": "object"}},
        {"name": "read_file", "description": "read", "parameters": {"type": "object"}},
    ]}
    outer = json.dumps({"success": True, "result": json.dumps(inner)})
    agent._maybe_promote_from_tool_search("tool_search", outer)
    assert agent._promoted_tools == set()
    assert agent._dynamic_tool_contract("web_fetch_x")["description"] == "fetch"
    assert agent._dynamic_tool_contract("read_file")["description"] == "read"
    # 非 tool_search 不记录契约
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
    assert "invoke_dynamic_tool" in cfg.get_deferred_tools_always_on()


def test_dynamic_tool_discovery_keeps_top_level_schema_stable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEFERRED_TOOLS_ENABLED", "1")
    monkeypatch.setenv("DEFERRED_TOOLS_ALWAYS_ON", "tool_search,invoke_dynamic_tool")
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "system")

    search_schema = _schema("tool_search")
    invoke_schema = _schema("invoke_dynamic_tool")
    hidden_schema = _schema("hidden_demo")
    all_schemas = [search_schema, invoke_schema, hidden_schema]
    monkeypatch.setattr(registry, "get_all_schemas", lambda: list(all_schemas))
    monkeypatch.setattr(
        registry,
        "get_tool_catalog",
        lambda: [{"name": "hidden_demo", "summary": "hidden_demo does things", "category": "demo"}],
    )
    monkeypatch.setattr(
        registry,
        "get_schemas_for",
        lambda names: [schema for schema in all_schemas if schema["function"]["name"] in set(names)],
    )

    search_inner = json.dumps({
        "success": True,
        "matches": [{
            "name": "hidden_demo",
            "description": "hidden_demo does things",
            "parameters": {"type": "object", "properties": {}},
        }],
    })

    def execute(name, args, **kwargs):
        if name == "tool_search":
            return json.dumps({"success": True, "result": search_inner})
        if name == "hidden_demo":
            return json.dumps({"success": True, "result": "dynamic-ok"})
        raise AssertionError(name)

    monkeypatch.setattr(registry, "execute_tool_isolated", execute)

    agent = RAgent(model="m", max_iterations=4, enable_self_review=False)
    monkeypatch.setattr(agent, "_collect_unfinished_todos", lambda: [])
    agent.client = _FakeClient([
        _response(_message(tool_calls=[_tool_call("tool_search", {"query": "demo"})])),
        _response(_message(tool_calls=[_tool_call("invoke_dynamic_tool", {
            "tool_name": "hidden_demo",
            "arguments": {},
        })])),
        _response(_message(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("use hidden tool", system_message="system") == "done"
    requests = agent.client.chat.completions.requests
    tool_name_sets = [
        [schema["function"]["name"] for schema in request.get("tools") or []]
        for request in requests
    ]
    assert tool_name_sets == [
        ["tool_search", "invoke_dynamic_tool"],
        ["tool_search", "invoke_dynamic_tool"],
        ["tool_search", "invoke_dynamic_tool"],
    ]
    assert agent._dynamic_tool_contract("hidden_demo") is not None
    dynamic_results = [
        message for message in agent.messages
        if isinstance(message, dict)
        and message.get("role") == "tool"
        and "dynamic-ok" in str(message.get("content"))
    ]
    assert len(dynamic_results) == 1


def test_dynamic_tool_dispatch_revalidates_schema_and_arguments(monkeypatch):
    monkeypatch.setenv("DEFERRED_TOOLS_ENABLED", "1")
    agent = RAgent(model="m", enable_self_review=False)
    monkeypatch.setattr(agent, "_collect_unfinished_todos", lambda: [])
    contract = {
        "name": "strict_demo",
        "description": "strict",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }
    live_schema = {
        "type": "function",
        "function": {
            "name": contract["name"],
            "description": contract["description"],
            "parameters": contract["parameters"],
        },
    }
    monkeypatch.setattr(registry, "get_schemas_for", lambda names: [live_schema] if "strict_demo" in names else [])
    agent._register_dynamic_tool_contracts([contract])

    missing = json.loads(agent._execute_dynamic_tool_call(json.dumps({
        "tool_name": "strict_demo",
        "arguments": {},
    })))
    assert missing["success"] is False
    assert "missing required fields" in missing["error"]

    wrong_type = json.loads(agent._execute_dynamic_tool_call(json.dumps({
        "tool_name": "strict_demo",
        "arguments": {"query": 3},
    })))
    assert wrong_type["success"] is False
    assert "must be string" in wrong_type["error"]

    stale_schema = _schema("strict_demo")
    monkeypatch.setattr(registry, "get_schemas_for", lambda names: [stale_schema])
    stale = json.loads(agent._execute_dynamic_tool_call(json.dumps({
        "tool_name": "strict_demo",
        "arguments": {"query": "x"},
    })))
    assert stale["success"] is False
    assert "schema changed" in stale["error"]
