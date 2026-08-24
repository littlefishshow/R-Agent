#!/usr/bin/env python3
"""把现有 USER.md / MEMORY.md 的 bullet 迁移成结构化 facts.jsonl。

对齐 memory_progress/01_Phase0_数据层FactStore.md：
- USER.md 的 bullet  -> category=preference, scope=user, durability=durable,
                        authority=descriptive, confidence=0.7
- MEMORY.md 的 bullet -> category=context, 同上 scope 分类, confidence=0.7

特性：
- 幂等：按内容去重键（casefold）跳过已存在的 fact，重复运行不产生重复。
- --dry-run：只打印将写入的 fact 数量与样例，不改文件。
- --memory-dir：指定目录（默认 memories/）。

用法：
    python3 scripts/migrate_memory_to_jsonl.py --dry-run
    python3 scripts/migrate_memory_to_jsonl.py
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# 让脚本可从仓库根目录直接运行。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.memory_facts import FactStore, content_key  # noqa: E402


_BULLET_RE = re.compile(r"^\s*[-*]\s+")


def _extract_bullets(path: str) -> list[str]:
    """读取一个 markdown 文件，返回所有非空 bullet 行的正文（去掉 '- '/'* ' 前缀）。"""
    if not os.path.exists(path):
        return []
    bullets: list[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            text = _BULLET_RE.sub("", stripped).strip()
            if text:
                bullets.append(text)
    return bullets


def build_facts_from_markdown(memory_dir: str) -> list[dict]:
    """把 USER.md / MEMORY.md 的 bullet 构造成 fact 列表（未去重、未落盘）。"""
    store = FactStore(memory_dir=memory_dir)
    facts: list[dict] = []
    plans = [
        ("USER.md", "preference"),
        ("MEMORY.md", "context"),
    ]
    for filename, category in plans:
        for text in _extract_bullets(os.path.join(memory_dir, filename)):
            facts.append(
                store.make_fact(
                    text,
                    category=category,
                    confidence=0.7,
                    scope="user",
                    durability="durable",
                    authority="descriptive",
                    source=f"migrate:{filename}",
                )
            )
    return facts


def migrate(memory_dir: str, dry_run: bool) -> dict:
    store = FactStore(memory_dir=memory_dir)
    candidate_facts = build_facts_from_markdown(memory_dir)

    existing = store.load_facts()
    existing_keys = {content_key(f.get("content")) for f in existing}

    to_add: list[dict] = []
    seen_keys = set(existing_keys)
    for fact in candidate_facts:
        key = content_key(fact.get("content"))
        if key is None or key in seen_keys:
            continue
        seen_keys.add(key)
        to_add.append(fact)

    if not dry_run and to_add:
        for fact in to_add:
            store.append_fact(fact)

    return {
        "memory_dir": memory_dir,
        "dry_run": dry_run,
        "existing_facts": len(existing),
        "candidates": len(candidate_facts),
        "to_add": len(to_add),
        "samples": [f["content"][:80] for f in to_add[:5]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate USER.md/MEMORY.md bullets into facts.jsonl")
    parser.add_argument("--memory-dir", default="memories", help="记忆目录（默认 memories/）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不改文件")
    args = parser.parse_args()

    result = migrate(args.memory_dir, args.dry_run)

    mode = "DRY-RUN（未写入）" if result["dry_run"] else "已写入"
    print(f"[{mode}] memory_dir={result['memory_dir']}")
    print(f"  现有 facts: {result['existing_facts']}")
    print(f"  markdown bullet 候选: {result['candidates']}")
    print(f"  本次新增: {result['to_add']}")
    if result["samples"]:
        print("  样例:")
        for s in result["samples"]:
            print(f"    - {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
