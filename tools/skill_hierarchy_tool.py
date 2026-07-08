import os
import glob
import json
import shutil
from typing import Dict, List, Any
from tools.registry import registry
from core.skills import skill_manager


def _skill_records() -> List[Dict[str, Any]]:
    """扫描 skills/**/SKILL.md，返回结构化 skill 记录。"""
    records = []
    skills_dir = skill_manager.skills_dir
    search_pattern = os.path.join(skills_dir, "**", "SKILL.md")
    for skill_path in glob.glob(search_pattern, recursive=True):
        if not os.path.isfile(skill_path):
            continue
        rel_path = os.path.relpath(skill_path, skills_dir)
        parts = rel_path.split(os.sep)
        if len(parts) >= 3:
            category = parts[0]
            skill_name = parts[-2]
        else:
            category = "uncategorized"
            skill_name = parts[-2] if len(parts) >= 2 else "unknown"
        if category.startswith("."):
            continue
        desc = "无描述"
        content = ""
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()
            if content:
                desc = content.split("\n")[0].strip() or "无描述"
                if desc.startswith("---") or desc.startswith("name:"):
                    for line in content.split("\n"):
                        if line.startswith("description:"):
                            desc = line.replace("description:", "", 1).strip().strip('"\'')
                            break
            # 尝试提取 When to Use 小节的前几行，帮助类目内选择
            when_to_use = ""
            lines = content.split("\n") if content else []
            for i, line in enumerate(lines):
                if line.strip().lower() in ("## when to use", "## when to use:") or line.strip().startswith("## When to Use"):
                    snippet = []
                    for nxt in lines[i+1:i+8]:
                        if nxt.startswith("## "):
                            break
                        if nxt.strip():
                            snippet.append(nxt.strip())
                    when_to_use = " ".join(snippet)[:300]
                    break
        except Exception as e:
            desc = f"加载描述失败 ({e})"
            when_to_use = ""
        records.append({
            "name": skill_name,
            "category": category,
            "description": desc,
            "path": skill_path,
            "relative_path": rel_path,
            "when_to_use": when_to_use,
        })
    return sorted(records, key=lambda r: (r["category"], r["name"]))


def skill_categories(include_counts: bool = True):
    """列出所有 skill 类目。内部兼容函数；默认工具入口请使用 skill_search(action="categories")。"""
    records = _skill_records()
    cats: Dict[str, int] = {}
    for r in records:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    categories = []
    for cat in sorted(cats):
        item = {"category": cat}
        if include_counts:
            item["count"] = cats[cat]
        categories.append(item)
    return {"categories": categories, "total_categories": len(categories), "total_skills": len(records)}


def skills_by_category(categories: list = None, include_when_to_use: bool = False, limit_per_category: int = 100):
    """按一个或多个类目列出 skill 摘要。内部兼容函数；默认工具入口请使用 skill_search(action="by_category")。"""
    records = _skill_records()
    if categories is None or categories == []:
        selected = sorted({r["category"] for r in records})
    else:
        selected = categories
    selected_set = set(selected)
    grouped: Dict[str, List[Dict[str, Any]]] = {c: [] for c in selected}
    for r in records:
        if r["category"] in selected_set:
            item = {"name": r["name"], "description": r["description"], "relative_path": r["relative_path"]}
            if include_when_to_use and r.get("when_to_use"):
                item["when_to_use"] = r["when_to_use"]
            grouped.setdefault(r["category"], []).append(item)
    # 限制每个类目输出数量，防止 token 爆炸
    result = {}
    for cat, items in grouped.items():
        result[cat] = {
            "skills": items[:limit_per_category],
            "count": len(items),
            "truncated": len(items) > limit_per_category,
        }
    missing = [c for c in selected if c not in {r["category"] for r in records}]
    return {"categories": result, "missing_categories": missing}


def _search_records(query: str = "", categories: list = None, include_when_to_use: bool = True, limit: int = 50):
    records = _skill_records()
    selected_set = set(categories or [])
    q = (query or "").strip().lower()
    matches = []
    for r in records:
        if selected_set and r["category"] not in selected_set:
            continue
        haystack = " ".join([r.get("name", ""), r.get("category", ""), r.get("description", ""), r.get("when_to_use", "")]).lower()
        if q and q not in haystack:
            continue
        item = {
            "name": r["name"],
            "category": r["category"],
            "description": r["description"],
            "relative_path": r["relative_path"],
        }
        if include_when_to_use and r.get("when_to_use"):
            item["when_to_use"] = r["when_to_use"]
        matches.append(item)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))
    return {"query": query or "", "matches": matches[:limit], "count": len(matches), "truncated": len(matches) > limit}


def skill_search(action: str = "search", query: str = "", categories: list = None,
                 include_counts: bool = True, include_when_to_use: bool = False,
                 limit_per_category: int = 100, limit: int = 50):
    """统一 skill 查询入口：categories/by_category/search。"""
    action = (action or "search").strip().lower()
    if action == "categories":
        return skill_categories(include_counts=include_counts)
    if action in {"by_category", "category"}:
        return skills_by_category(
            categories=categories,
            include_when_to_use=include_when_to_use,
            limit_per_category=limit_per_category,
        )
    if action == "search":
        return _search_records(
            query=query,
            categories=categories,
            include_when_to_use=include_when_to_use,
            limit=limit,
        )
    return {"success": False, "error": "Unsupported action. Use categories, by_category, or search."}


def skill_relocate(skill_name: str, new_category: str):
    """将 skill 移动到新类目，用于动态维护分类。"""
    if not skill_name or not new_category:
        raise ValueError("skill_name 和 new_category 不能为空")
    skills_dir = skill_manager.skills_dir
    pattern = os.path.join(skills_dir, "**", skill_name)
    matches = [p for p in glob.glob(pattern, recursive=True) if os.path.isdir(p) and os.path.exists(os.path.join(p, "SKILL.md"))]
    if not matches:
        return {"success": False, "error": f"技能 '{skill_name}' 不存在"}
    if len(matches) > 1:
        return {"success": False, "error": "存在多个同名技能，请先手动消歧", "matches": matches}
    src = matches[0]
    dst_parent = os.path.join(skills_dir, new_category) if new_category != "uncategorized" else skills_dir
    dst = os.path.join(dst_parent, skill_name)
    if os.path.abspath(src) == os.path.abspath(dst):
        return {"success": True, "message": "技能已经在目标类目中", "path": dst}
    if os.path.exists(dst):
        return {"success": False, "error": f"目标位置已存在: {dst}"}
    os.makedirs(dst_parent, exist_ok=True)
    shutil.move(src, dst)
    # 清理空父类目目录
    src_parent = os.path.dirname(src)
    try:
        if os.path.abspath(src_parent) != os.path.abspath(skills_dir) and not os.listdir(src_parent):
            os.rmdir(src_parent)
    except OSError:
        pass
    return {"success": True, "skill_name": skill_name, "new_category": new_category, "path": dst}


registry.register(
    name="skill_search",
    description="统一查询 skill：action=categories 列类目，by_category 按类目列摘要，search 按关键词检索名称/描述/When to Use。",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "categories | by_category | search；默认 search"},
            "query": {"type": "string", "description": "search 时的关键词；为空则按过滤条件列出技能摘要"},
            "categories": {"type": "array", "items": {"type": "string"}, "description": "by_category/search 时限定的类目列表"},
            "include_counts": {"type": "boolean", "description": "categories 时是否包含每个类目的技能数量", "default": True},
            "include_when_to_use": {"type": "boolean", "description": "是否包含 When to Use 摘要", "default": False},
            "limit_per_category": {"type": "integer", "description": "by_category 时每个类目最多返回多少个技能", "default": 100},
            "limit": {"type": "integer", "description": "search 时最多返回多少个技能", "default": 50},
        }
    },
    handler=skill_search,
)

registry.register(
    name="skill_relocate",
    description="动态维护 skill 类目：将指定 skill 移动到新的类目目录。",
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "要移动的技能名称"},
            "new_category": {"type": "string", "description": "目标类目，例如 agent_ops、creative、github、productivity"}
        },
        "required": ["skill_name", "new_category"]
    },
    handler=skill_relocate,
)
