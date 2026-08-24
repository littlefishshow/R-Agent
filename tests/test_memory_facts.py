"""Phase 0 · FactStore（JSONL 事实库）测试。

覆盖：原子写、内容去重、按 confidence trim、并发锁、坏行容错、id 唯一、迁移脚本幂等。
"""

import json
import threading

from core.memory_facts import (
    FactStore,
    coerce_confidence,
    content_key,
    generate_fact_id,
    trim_facts_to_max,
)


def test_append_and_load_roundtrip(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    assert store.append_fact(store.make_fact("用户偏好中文回复", category="preference", confidence=0.9)) is True
    facts = store.load_facts()
    assert len(facts) == 1
    assert facts[0]["content"] == "用户偏好中文回复"
    assert facts[0]["category"] == "preference"
    assert facts[0]["id"].startswith("fact_")
    # 重新构造 store（读磁盘），内容一致。
    store2 = FactStore(memory_dir=str(tmp_path))
    assert store2.load_facts()[0]["content"] == "用户偏好中文回复"


def test_content_dedup_casefold(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    assert store.append_fact(store.make_fact("Prefers Dark Mode")) is True
    # 大小写不同的同内容被去重。
    assert store.append_fact(store.make_fact("prefers dark mode")) is False
    assert len(store.load_facts()) == 1


def test_trim_to_max_keeps_highest_confidence(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    for i, conf in enumerate([0.2, 0.9, 0.5, 0.95, 0.1]):
        store.append_fact(store.make_fact(f"fact {i}", confidence=conf))
    removed = store.trim_to_max(2)
    assert removed == 3
    remaining = store.load_facts()
    assert len(remaining) == 2
    confs = sorted(coerce_confidence(f) for f in remaining)
    assert confs == [0.9, 0.95]


def test_trim_handles_null_and_nonnumeric_confidence(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    # 直接写入含坏 confidence 的 fact（模拟手改/导入）。
    facts = [
        {"id": "a", "content": "null conf", "confidence": None},
        {"id": "b", "content": "str conf", "confidence": "high"},
        {"id": "c", "content": "good conf", "confidence": 0.99},
    ]
    store.write_all(facts)
    # 不应因 None/str confidence crash。
    removed = store.trim_to_max(1)
    assert removed == 2
    remaining = store.load_facts()
    assert len(remaining) == 1
    assert remaining[0]["id"] == "c"


def test_remove_and_replace(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    store.append_fact(store.make_fact("fact A", fact_id="fa"))
    store.append_fact(store.make_fact("fact B", fact_id="fb"))
    assert store.remove_facts(["fa"]) == 1
    assert {f["id"] for f in store.load_facts()} == {"fb"}

    # replace：删旧加新
    changed = store.replace_fact("fb", store.make_fact("fact C", fact_id="fc"))
    assert changed is True
    contents = {f["content"] for f in store.load_facts()}
    assert contents == {"fact C"}


def test_bad_lines_are_skipped(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    # 手写一个含坏行的文件。
    with open(store.facts_file, "w", encoding="utf-8") as f:
        f.write('{"id":"1","content":"good"}\n')
        f.write("this is not json\n")
        f.write("\n")
        f.write('{"id":"2","content":"also good"}\n')
        f.write('{"id":"3"}\n')  # 无 content，被跳过
    facts = store.load_facts()
    assert {f["content"] for f in facts} == {"good", "also good"}


def test_concurrent_appends_do_not_lose_data(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    n = 40

    def worker(i):
        store.append_fact(store.make_fact(f"concurrent fact {i}", confidence=0.8))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    facts = store.load_facts()
    assert len(facts) == n
    # 每行都是合法 JSON（无半写）。
    with open(store.facts_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                json.loads(line)


def test_make_fact_normalizes_classification(tmp_path):
    store = FactStore(memory_dir=str(tmp_path))
    fact = store.make_fact(
        "  用户在用 macOS  ",
        category="identity",
        confidence=1.5,  # 超界 -> 钳到 1.0
        scope="USER",
        durability="Durable",
        authority="Descriptive",
        expected_valid_days=3650,
        source="thread-1",
    )
    assert fact["content"] == "用户在用 macOS"
    assert fact["confidence"] == 1.0
    assert fact["scope"] == "user"
    assert fact["durability"] == "durable"
    assert fact["authority"] == "descriptive"
    assert fact["expected_valid_days"] == 3650


def test_helpers():
    assert content_key("  Hello ") == "hello"
    assert content_key("") is None
    assert content_key(123) is None
    assert generate_fact_id().startswith("fact_")
    assert coerce_confidence({"confidence": True}) == 0.5
    assert coerce_confidence({"confidence": 0.42}) == 0.42
    assert trim_facts_to_max([{"content": "x", "confidence": 0.1}], 5) == [{"content": "x", "confidence": 0.1}]


def test_migration_script_idempotent(tmp_path):
    # 造 USER.md / MEMORY.md
    (tmp_path / "USER.md").write_text("- 用户偏好中文回复\n- 用户在用 macOS\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("- 项目使用 frozen snapshot\n", encoding="utf-8")

    import scripts.migrate_memory_to_jsonl as mig

    dry = mig.migrate(str(tmp_path), dry_run=True)
    assert dry["to_add"] == 3
    # dry-run 不写文件
    assert FactStore(memory_dir=str(tmp_path)).count() == 0

    applied = mig.migrate(str(tmp_path), dry_run=False)
    assert applied["to_add"] == 3
    store = FactStore(memory_dir=str(tmp_path))
    facts = store.load_facts()
    assert len(facts) == 3
    # 迁移的 fact 带准入分类
    for f in facts:
        assert f["scope"] == "user"
        assert f["durability"] == "durable"
        assert f["authority"] == "descriptive"

    # 再次运行：幂等，不新增
    again = mig.migrate(str(tmp_path), dry_run=False)
    assert again["to_add"] == 0
    assert FactStore(memory_dir=str(tmp_path)).count() == 3
