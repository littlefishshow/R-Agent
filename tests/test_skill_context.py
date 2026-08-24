"""Skills 发现 + skill_context 持久化测试（Improve_progress/07）。

覆盖：
1. SkillManager.parse_skill_metadata：front-matter（--- 包裹 / 顶部 key:value）+ 无 metadata 兜底；
2. list_skills_structured：返回结构化目录，缺 metadata 用目录名兜底；
3. skill_view 后 skill_context 被写入，并出现在 durable context（03 章）里；
4. 去重：同一 skill 多次 view 只保留一条最新。
"""

import json
from types import SimpleNamespace

from core.agent import RAgent
from core.skills import SkillManager
from core.state import build_durable_context
from tools.registry import registry


# --------------------------------------------------------------------------- #
# 1. metadata 解析
# --------------------------------------------------------------------------- #
def test_parse_metadata_front_matter_wrapped():
    c = (
        '---\nname: "deploy"\ndescription: "Deploy to prod"\n'
        'triggers: "deploy, ship"\nallowed_tools: [read_file, write_file]\n---\n\n# Body'
    )
    md = SkillManager.parse_skill_metadata(c)
    assert md["name"] == "deploy"
    assert md["description"] == "Deploy to prod"
    assert md["triggers"] == "deploy, ship"
    assert md["allowed_tools"] == ["read_file", "write_file"]


def test_parse_metadata_top_keys_without_fence():
    c = "name: alpha\ndescription: Alpha skill\n\n# rest"
    md = SkillManager.parse_skill_metadata(c)
    assert md["description"] == "Alpha skill"


def test_parse_metadata_fallback_first_line():
    c = "# Title\n\nThis is the body first line."
    md = SkillManager.parse_skill_metadata(c)
    assert "body first line" in md["description"]


def test_parse_metadata_empty():
    assert SkillManager.parse_skill_metadata("")["description"] == ""


# --------------------------------------------------------------------------- #
# 2. list_skills_structured（用临时 skills 目录）
# --------------------------------------------------------------------------- #
def test_list_skills_structured(tmp_path):
    skills_dir = tmp_path / "skills"
    (skills_dir / "cat" / "with_meta").mkdir(parents=True)
    (skills_dir / "cat" / "with_meta" / "SKILL.md").write_text(
        '---\nname: nice\ndescription: A nice skill\n---\nbody', encoding="utf-8")
    (skills_dir / "cat" / "no_meta").mkdir(parents=True)
    (skills_dir / "cat" / "no_meta" / "SKILL.md").write_text("# just a heading\n\nplain body line", encoding="utf-8")

    mgr = SkillManager(skills_dir=str(skills_dir))
    cat = {c["dir_name"]: c for c in mgr.list_skills_structured()}
    assert cat["with_meta"]["name"] == "nice"
    assert cat["with_meta"]["description"] == "A nice skill"
    # 无 metadata -> name 兜底目录名，description 兜底正文首行
    assert cat["no_meta"]["name"] == "no_meta"
    assert "plain body line" in cat["no_meta"]["description"]


# --------------------------------------------------------------------------- #
# 3. skill_view -> skill_context -> durable context
# --------------------------------------------------------------------------- #
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


def _skill_view_call(skill_name):
    return SimpleNamespace(id="c1", function=SimpleNamespace(name="skill_view", arguments=json.dumps({"skill_name": skill_name})))


def _msg(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _resp(m):
    return SimpleNamespace(usage={"total_tokens": 1}, choices=[SimpleNamespace(message=m)])


def _install_fake_skill_view(monkeypatch, description="Does the X workflow"):
    def skv(skill_name=None, file_path=None):
        content = f"---\nname: {skill_name}\ndescription: {description}\n---\n# body"
        return json.dumps({"content": content, "file_path": "SKILL.md"}, ensure_ascii=False)

    registry.reload_all()
    registry.register("skill_view", "view a skill", {"type": "object", "properties": {}}, skv)
    monkeypatch.setattr(registry, "get_all_schemas", lambda: [registry._tools["skill_view"]["schema"]])
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda n, a, **k: registry.execute_tool(n, a))


def test_skill_view_populates_skill_context(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_skill_view(monkeypatch)

    agent = RAgent(model="m", max_iterations=3, enable_self_review=False)
    agent.client = _FakeClient([
        _resp(_msg(tool_calls=[_skill_view_call("myskill")])),
        _resp(_msg(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("use skill") == "done"
    sc = agent.state.skill_context
    assert any(s.get("skill") == "myskill" and "X workflow" in s.get("summary", "") for s in sc)

    # durable context 包含 skill 引用（03 章会据此回注）
    dc = build_durable_context(agent.state)
    assert "myskill" in dc and "durable_skills" in dc


def test_skill_context_dedupes_repeat_views(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _install_fake_skill_view(monkeypatch, description="latest summary")

    agent = RAgent(model="m", max_iterations=5, enable_self_review=False)
    agent.client = _FakeClient([
        _resp(_msg(tool_calls=[_skill_view_call("dup")])),
        _resp(_msg(tool_calls=[_skill_view_call("dup")])),
        _resp(_msg(content="done", tool_calls=None)),
    ])

    assert agent.run_conversation("view twice") == "done"
    dups = [s for s in agent.state.skill_context if s.get("skill") == "dup"]
    assert len(dups) == 1  # 去重：同一 skill 只留一条
    assert dups[0]["summary"] == "latest summary"


def test_skill_policy_only_applies_after_explicit_activation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def activate(action="", skill_name=""):
        if action == "deactivate":
            return json.dumps({
                "success": True,
                "action": "deactivate",
                "skill_name": skill_name,
                "allowed_tools": [],
            })
        return json.dumps({
            "success": True,
            "action": "activate",
            "skill_name": skill_name,
            "allowed_tools": ["read_file", "write_file"],
            "description": "File editing policy",
        })

    registry.reload_all()
    registry.register(
        "skill_activate",
        "activate",
        {"type": "object", "properties": {}},
        activate,
    )
    schemas = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in (
            "skill_activate",
            "skill_view",
            "skill_search",
            "tool_search",
            "read_file",
            "write_file",
            "run_command",
        )
    ]
    monkeypatch.setattr(registry, "get_all_schemas", lambda: schemas)
    monkeypatch.setattr(registry, "execute_tool_isolated", lambda n, a, **k: registry.execute_tool(n, a))

    seen_tools = []

    class _CaptureCompletions:
        def __init__(self):
            self.turn = 0

        def create(self, **kwargs):
            self.turn += 1
            seen_tools.append([tool["function"]["name"] for tool in kwargs.get("tools", [])])
            if self.turn == 1:
                return _resp(_msg(tool_calls=[
                    SimpleNamespace(
                        id="activate",
                        function=SimpleNamespace(
                            name="skill_activate",
                            arguments=json.dumps({"action": "activate", "skill_name": "files"}),
                        ),
                    )
                ]))
            return _resp(_msg(content="done", tool_calls=None))

    agent = RAgent(model="m", max_iterations=3, enable_self_review=False)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=_CaptureCompletions()))

    assert agent.run_conversation("activate files") == "done"
    assert "run_command" in seen_tools[0]  # 激活前不收窄
    # 收缩延迟（KV 稳定）：技能激活是「收窄」而非「增长」，schema 前缀仍保留激活前
    # 的工具超集，run_command 依旧出现在 schema 里，等下次压缩才随 KV 重建收窄。
    assert "run_command" in seen_tools[1]
    assert set(seen_tools[1]) >= {
        "skill_activate",
        "skill_view",
        "skill_search",
        "tool_search",
        "read_file",
        "write_file",
    }
    assert agent.state.active_skill_policy["skill"] == "files"
    # 但执行层保底闸门已生效：白名单不含 run_command，真正调用会被拒。
    allowed = agent._effective_skill_allowed_tools()
    assert allowed is not None and "run_command" not in allowed


def test_skill_policy_deactivate_restores_unrestricted_state():
    agent = RAgent(model="m", enable_self_review=False)
    activate_result = json.dumps({
        "success": True,
        "result": json.dumps({
            "success": True,
            "action": "activate",
            "skill_name": "files",
            "allowed_tools": ["read_file"],
        }),
    })
    agent._maybe_apply_skill_policy("skill_activate", activate_result)
    assert agent.state.active_skill_policy["skill"] == "files"

    deactivate_result = json.dumps({
        "success": True,
        "result": json.dumps({
            "success": True,
            "action": "deactivate",
            "skill_name": "files",
            "allowed_tools": [],
        }),
    })
    agent._maybe_apply_skill_policy("skill_activate", deactivate_result)
    assert agent.state.active_skill_policy == {}
