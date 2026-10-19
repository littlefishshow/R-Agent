from types import SimpleNamespace

from core.agent import RAgent
from core.cache_observability import build_request_fingerprint, provider_cache_tokens
from main import _format_token_usage_label


def _response(usage):
    return SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
    )


def test_provider_cache_tokens_distinguishes_zero_from_unavailable():
    assert provider_cache_tokens({"prompt_tokens": 10}) == (None, None)
    assert provider_cache_tokens({
        "prompt_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 0},
    }) == (0, None)
    assert provider_cache_tokens(SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cached_tokens=7),
    )) == (7, None)


def test_provider_cache_ratio_remains_secondary_diagnostic():
    agent = RAgent(model="test", max_iterations=1, enable_self_review=False)
    observation = {
        "prefix_reuse_expected": True,
        "change_reasons": ["tail_append_only"],
    }
    agent._record_token_usage(_response({
        "prompt_tokens": 100,
        "completion_tokens": 1,
        "total_tokens": 101,
        "prompt_tokens_details": {"cached_tokens": 80},
    }))
    agent._record_cache_usage(_response({
        "prompt_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 80},
    }), observation=observation, latency_seconds=0.01)
    agent._record_token_usage(_response({
        "prompt_tokens": 300,
        "completion_tokens": 1,
        "total_tokens": 301,
        "prompt_tokens_details": {"cached_tokens": 120},
    }))
    agent._record_cache_usage(_response({
        "prompt_tokens": 300,
        "prompt_tokens_details": {"cached_tokens": 120},
    }), observation=observation, latency_seconds=0.02)

    cache = agent.get_cache_usage()
    assert cache["provider_cached_prompt_tokens"] == 200
    assert cache["provider_prompt_tokens"] == 400
    assert cache["provider_cache_hit_ratio"] == 0.5
    assert cache["last_provider_cache_hit_ratio"] == 0.4
    assert cache["last_provider_uncached_prompt_tokens"] == 180
    assert cache["total_request_latency_ms"] == 30.0
    label = _format_token_usage_label(agent)
    assert "KV L/O —/—" in label
    assert label.endswith("· tail")


def test_local_kv_reuse_ratio_is_token_weighted_and_drives_cli(monkeypatch):
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "system")
    agent = RAgent(model="test", max_iterations=1, enable_self_review=False)
    agent.messages = [
        {"role": "system", "content": "stable system"},
        {"role": "user", "content": "first"},
    ]
    first = agent._observe_request_prefix(catalog_note="catalog", request_tools=[])
    agent.messages.extend([
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "second"},
    ])
    second = agent._observe_request_prefix(catalog_note="catalog", request_tools=[])

    assert first["local_reusable_tokens"] == 0
    assert first["local_reuse_ratio"] == 0.0
    assert 0 < second["local_reusable_tokens"] < second["local_prompt_tokens"]
    expected = (
        first["local_reusable_tokens"] + second["local_reusable_tokens"]
    ) / (
        first["local_prompt_tokens"] + second["local_prompt_tokens"]
    )
    cache = agent.get_cache_usage()
    assert cache["kv_reuse_ratio"] == expected
    assert cache["cache_hit_ratio"] == expected
    assert cache["last_kv_reuse_ratio"] == second["local_reuse_ratio"]
    assert cache["last_cache_hit_ratio"] == second["local_reuse_ratio"]
    label = _format_token_usage_label(agent)
    assert "KV L/O" in label
    assert label.endswith("· tail")


def test_request_fingerprint_reports_tail_append_and_component_changes():
    first_public, first_private = build_request_fingerprint(
        base_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "one"},
        ],
        catalog_note="catalog",
        durable_message={"role": "user", "content": "durable"},
        tools=[{"function": {"name": "base"}}],
    )
    assert first_public["change_reasons"] == ["first_request"]
    assert first_public["local_reusable_tokens"] == 0
    assert first_public["local_reuse_ratio"] == 0.0

    second_public, second_private = build_request_fingerprint(
        base_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ],
        catalog_note="catalog",
        durable_message={"role": "user", "content": "durable"},
        tools=[{"function": {"name": "base"}}],
        previous=first_private,
    )
    assert second_public["prefix_reuse_expected"] is True
    assert second_public["change_reasons"] == ["tail_append_only"]
    assert 0 < second_public["local_reusable_tokens"] < second_public["local_prompt_tokens"]
    assert 0 < second_public["local_reuse_ratio"] < 1

    identical_public, _ = build_request_fingerprint(
        base_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ],
        catalog_note="catalog",
        durable_message={"role": "user", "content": "durable"},
        tools=[{"function": {"name": "base"}}],
        previous=second_private,
    )
    assert identical_public["local_reuse_ratio"] == 1.0
    assert identical_public["local_new_tokens"] == 0

    rewritten_public, _ = build_request_fingerprint(
        base_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "replacement"},
        ],
        catalog_note="catalog",
        durable_message={"role": "user", "content": "durable"},
        tools=[{"function": {"name": "base"}}],
        previous=second_private,
    )
    assert "history_rewritten" in rewritten_public["change_reasons"]
    assert 0 < rewritten_public["local_reuse_ratio"] < 1

    changed_public, _ = build_request_fingerprint(
        base_messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ],
        catalog_note="catalog",
        durable_message={"role": "user", "content": "durable"},
        tools=[{"function": {"name": "base"}}, {"function": {"name": "dynamic"}}],
        previous=second_private,
        change_hints=["tool_promoted"],
    )
    assert changed_public["prefix_reuse_expected"] is False
    assert "tool_schema_changed" in changed_public["change_reasons"]
    assert "tool_promoted" in changed_public["change_reasons"]
    assert changed_public["local_reusable_tokens"] == 0
    assert changed_public["local_reuse_ratio"] == 0.0


def test_parent_merges_child_local_reuse_into_overall_harness_score(monkeypatch):
    monkeypatch.setenv("MEMORY_INJECTION_MODE", "system")
    agent = RAgent(model="test", enable_self_review=False)
    agent.cache_usage.update({
        "local_prompt_tokens": 300,
        "local_reusable_tokens": 240,
        "local_new_tokens": 60,
        "kv_reuse_ratio": 0.8,
        "cache_hit_ratio": 0.8,
        "last_kv_reuse_ratio": 0.9,
        "last_cache_hit_ratio": 0.9,
    })
    assert agent.merge_delegated_cache_usage({
        "request_count": 4,
        "observed_agents": 2,
        "local_prompt_tokens": 200,
        "local_reusable_tokens": 100,
        "local_new_tokens": 100,
    }) is True

    cache = agent.get_cache_usage()
    assert cache["delegated_kv_reuse_ratio"] == 0.5
    assert cache["overall_local_prompt_tokens"] == 500
    assert cache["overall_local_reusable_tokens"] == 340
    assert cache["overall_kv_reuse_ratio"] == 0.68
    label = _format_token_usage_label(agent)
    assert "KV L/O 90.0%/68.0%" in label
