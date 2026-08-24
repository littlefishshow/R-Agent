"""Phase 1 · 抽取引擎测试（mock LLM，不消耗真实额度）。

覆盖：预处理（过滤/trivial/signal）、抽取落盘、trivial 跳过、watermark 去重、
抽取失败不打断、隐藏框架消息不进抽取输入。
"""

import json

from core.memory_extractor import (
    MemoryExtractor,
    detect_signals,
    filter_messages_for_memory,
    filter_trivial,
    parse_memory_update_response,
    prepare_update,
)
from core.memory_facts import FactStore
from core.memory_provider import DeerMemProvider


# --------------------------------------------------------------------------- #
# 预处理
# --------------------------------------------------------------------------- #
def test_filter_messages_keeps_user_and_final_ai():
    messages = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "我偏好中文回复"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "工具结果"},
        {"role": "assistant", "content": "好的，我会用中文"},
    ]
    filtered = filter_messages_for_memory(messages)
    roles = [m["role"] for m in filtered]
    assert roles == ["user", "assistant"]
    assert filtered[1]["content"] == "好的，我会用中文"


def test_filter_messages_drops_hidden_durable_context():
    messages = [
        {"role": "user", "content": "以下为系统保存的参考上下文（历史摘要...）"},
        {"role": "user", "content": "真实用户输入"},
        {"role": "assistant", "content": "回复"},
    ]
    filtered = filter_messages_for_memory(messages)
    assert [m["content"] for m in filtered if m["role"] == "user"] == ["真实用户输入"]


def test_filter_trivial_drops_acknowledgments():
    messages = [
        {"role": "user", "content": "好的"},
        {"role": "assistant", "content": "（对附和的回复）"},
        {"role": "user", "content": "我在用 macOS 系统开发"},
        {"role": "assistant", "content": "了解"},
    ]
    result = filter_trivial(messages)
    contents = [m["content"] for m in result]
    assert "好的" not in contents
    assert "我在用 macOS 系统开发" in contents


def test_detect_signals():
    messages = [{"role": "user", "content": "我偏好用 vim 编辑代码"}]
    assert "preference" in detect_signals(messages)
    messages2 = [{"role": "user", "content": "不对，应该用 tabs"}]
    assert "correction" in detect_signals(messages2)
    messages3 = [{"role": "user", "content": "已验证根因位于 session memory gate"}]
    assert "verified_result" in detect_signals(messages3)


def test_prepare_update_returns_none_for_trivial_only():
    messages = [
        {"role": "user", "content": "好的"},
        {"role": "assistant", "content": "回复"},
    ]
    assert prepare_update(messages) is None


def test_prepare_update_returns_none_without_assistant():
    messages = [{"role": "user", "content": "我偏好中文"}]
    assert prepare_update(messages) is None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def test_parse_extracts_json_from_noisy_text():
    raw = (
        "让我想想...\n```json\n"
        '{"user": {}, "history": {}, "newFacts": [{"content": "用户偏好中文", '
        '"category": "preference", "confidence": 0.9, "scope": "user", '
        '"durability": "durable", "authority": "descriptive"}], "factsToRemove": []}'
        "\n```\n以上就是结果。"
    )
    parsed = parse_memory_update_response(raw)
    assert len(parsed["newFacts"]) == 1
    assert parsed["newFacts"][0]["content"] == "用户偏好中文"
    assert parsed["newFacts"][0]["scope"] == "user"


def test_parse_rejects_missing_keys():
    import pytest

    with pytest.raises(json.JSONDecodeError):
        parse_memory_update_response('{"newFacts": []}')  # 缺 user/history


def test_parse_preserves_governance_fields():
    raw = json.dumps({
        "user": {},
        "history": {},
        "newFacts": [],
        "factsToRemove": [],
        "staleFactsToRemove": [{"id": "old-1", "reason": "过期"}],
        "staleFactsToExtend": [
            {"id": "old-2", "extend_by_days": 30, "reason": "仍有效"},
        ],
        "factsToConsolidate": [{
            "sourceIds": ["a", "b"],
            "consolidated": {
                "content": "合并事实",
                "category": "context",
                "confidence": 0.8,
                "scope": "user",
                "durability": "durable",
                "authority": "descriptive",
            },
        }],
    })
    parsed = parse_memory_update_response(raw)
    assert parsed["staleFactsToRemove"][0]["id"] == "old-1"
    assert parsed["staleFactsToExtend"][0]["extend_by_days"] == 30
    assert parsed["factsToConsolidate"][0]["sourceIds"] == ["a", "b"]


def test_governance_due_prompt_is_added():
    from core.memory_extractor import build_extraction_messages

    messages = build_extraction_messages(
        [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
        [],
        frozenset(),
        governance_due=True,
    )
    assert "<governance_due>" in messages[1]["content"]


def test_session_prompt_defines_scope_durability_and_authority_candidates():
    from core.memory_extractor import build_extraction_messages

    messages = build_extraction_messages(
        [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
        [],
        frozenset(),
        session_facts_enabled=True,
    )
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert "scope：user / project / task" in system
    assert "durability：durable / transient" in system
    assert "authority：descriptive / imperative" in system
    assert "scope=project" in user
    assert "scope=task" in user


# --------------------------------------------------------------------------- #
# 抽取器（mock LLM）
# --------------------------------------------------------------------------- #
class _MockLLM:
    def __init__(self, response_json: str):
        self.calls = 0
        self.last_kwargs = None
        payload = response_json

        class _Msg:
            content = payload

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        def create(**kwargs):
            self.calls += 1
            self.last_kwargs = kwargs
            return _Resp()

        import types

        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))


_GOOD_RESPONSE = json.dumps({
    "user": {}, "history": {},
    "newFacts": [{
        "content": "用户偏好中文回复", "category": "preference", "confidence": 0.9,
        "scope": "user", "durability": "durable", "authority": "descriptive",
        "expected_valid_days": 3650,
    }],
    "factsToRemove": [],
})


def test_extractor_writes_fact(tmp_path):
    client = _MockLLM(_GOOD_RESPONSE)
    extractor = MemoryExtractor(client=client, model="test-model")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(store=store, extractor=extractor, async_extract=False)

    messages = [
        {"role": "user", "content": "我偏好中文回复，请一直用中文"},
        {"role": "assistant", "content": "好的，我会一直用中文回复"},
    ]
    provider.add(thread_id="t1", messages=messages)

    facts = store.load_facts()
    assert len(facts) == 1
    assert facts[0]["content"] == "用户偏好中文回复"
    assert client.calls == 1


def test_extractor_skips_trivial_turns(tmp_path):
    client = _MockLLM(_GOOD_RESPONSE)
    extractor = MemoryExtractor(client=client, model="test-model")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(store=store, extractor=extractor, async_extract=False)

    provider.add(thread_id="t1", messages=[
        {"role": "user", "content": "好的"},
        {"role": "assistant", "content": "嗯"},
    ])
    assert client.calls == 0
    assert store.count() == 0


def test_watermark_only_extracts_increment(tmp_path):
    client = _MockLLM(_GOOD_RESPONSE)
    extractor = MemoryExtractor(client=client, model="test-model")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(store=store, extractor=extractor, async_extract=False)

    messages = [
        {"role": "user", "content": "我偏好中文回复"},
        {"role": "assistant", "content": "好的"},
    ]
    provider.add(thread_id="t1", messages=messages)
    assert client.calls == 1

    # 同一 thread 再次 add 但没有新增有意义交换 -> 不再抽取。
    provider.add(thread_id="t1", messages=messages)
    assert client.calls == 1


def test_extraction_failure_does_not_raise(tmp_path):
    class _BoomLLM:
        def __init__(self):
            import types

            def create(**kwargs):
                raise RuntimeError("LLM down")

            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    extractor = MemoryExtractor(client=_BoomLLM(), model="test-model")
    store = FactStore(memory_dir=str(tmp_path))
    provider = DeerMemProvider(store=store, extractor=extractor, async_extract=False)

    # 不应抛异常，facts 保持空。
    provider.add(thread_id="t1", messages=[
        {"role": "user", "content": "我偏好中文回复"},
        {"role": "assistant", "content": "好的"},
    ])
    assert store.count() == 0


# --------------------------------------------------------------------------- #
# 修复 1：temperature 可配置（默认不传）
# --------------------------------------------------------------------------- #
_CONV = [
    {"role": "user", "content": "我偏好中文回复"},
    {"role": "assistant", "content": "好的"},
]


def test_temperature_omitted_by_default():
    client = _MockLLM(_GOOD_RESPONSE)
    extractor = MemoryExtractor(client=client, model="m")
    extractor.extract(_CONV, [])
    # 默认不传 temperature（最大兼容：部分模型/网关拒绝 temperature=0）。
    assert "temperature" not in client.last_kwargs
    assert client.last_kwargs["stream"] is False


def test_temperature_passed_when_set():
    client = _MockLLM(_GOOD_RESPONSE)
    extractor = MemoryExtractor(client=client, model="m", temperature=0)
    extractor.extract(_CONV, [])
    assert client.last_kwargs["temperature"] == 0


# --------------------------------------------------------------------------- #
# 保真：抽取 prompt 要求保留日期/人名/地点等具体细节
# --------------------------------------------------------------------------- #
def test_extraction_prompt_requires_detail_preservation():
    from core.memory_extractor import build_extraction_messages

    conv = [
        {"role": "user", "content": "我 2023 年 5 月 7 日参加了 LGBTQ 支持团体"},
        {"role": "assistant", "content": "记下了"},
    ]
    messages = build_extraction_messages(conv, [], frozenset())
    system = messages[0]["content"]
    user = messages[1]["content"]
    # 系统 prompt 明确要求不要压缩掉日期/人名/地点。
    assert "日期" in system
    assert "不要过度压缩" in system or "保真" in system
    # 具体细节（日期）被原样送入抽取输入，供模型保留。
    assert "2023 年 5 月 7 日" in user


# --------------------------------------------------------------------------- #
# 修复 2：抽取失败可观测性（不再静默）
# --------------------------------------------------------------------------- #
def test_extraction_failure_is_logged(caplog):
    import logging

    class _BoomLLM:
        def __init__(self):
            import types

            def create(**kwargs):
                raise RuntimeError("BadRequest: temperature not supported")

            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    extractor = MemoryExtractor(client=_BoomLLM(), model="m")
    with caplog.at_level(logging.WARNING, logger="core.memory_extractor"):
        result = extractor.extract(_CONV, [])
    assert result is None
    assert any("memory extraction failed" in r.message for r in caplog.records)
    # exc_info=True -> 日志里带 traceback（BadRequest 可见）。
    assert any(r.exc_info for r in caplog.records)
