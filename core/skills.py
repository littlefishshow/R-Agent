from __future__ import annotations
import glob
import os
import shutil
from pathlib import Path

class SkillManager:
    """技能系统管理器：渐进式加载 List -> View，并支持 Hermes 式技能包文件。"""
    CATEGORY_DISPLAY_NAMES = {
        "agent_ops": "Agent 运维 / 自进化",
        "creative": "创意创作",
        "github": "GitHub / 代码协作",
        "productivity": "生产力 / 办公自动化",
        "uncategorized": "未分类",
    }
    ALLOWED_SUPPORT_DIRS = {"references", "templates", "scripts", "assets", "Project_progress"}

    def __init__(self, skills_dir: str = None):
        if skills_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.skills_dir = os.path.join(base_dir, "skills")
        else:
            self.skills_dir = skills_dir
        os.makedirs(self.skills_dir, exist_ok=True)

    def _display_category_name(self, category: str) -> str:
        return self.CATEGORY_DISPLAY_NAMES.get(category, category.capitalize())

    @staticmethod
    def parse_skill_metadata(content: str) -> dict:
        """从 SKILL.md 内容里解析轻量 metadata：name / description / triggers。

        兼容两种写法：
        1. YAML 风格 front-matter（``---`` 包裹或顶部若干 ``key: value`` 行）；
        2. 无 front-matter：description 兜底取首个非空行。

        绝不抛异常——解析失败就返回尽力而为的兜底。
        """
        meta = {"name": "", "description": "", "triggers": "", "allowed_tools": []}
        if not content:
            return meta
        try:
            lines = content.splitlines()
            # 收集 front-matter 区域：可能被 --- 包裹，也可能只是顶部的 key: value 行。
            scan = []
            if lines and lines[0].strip() == "---":
                for line in lines[1:]:
                    if line.strip() == "---":
                        break
                    scan.append(line)
            else:
                for line in lines[:8]:
                    if ":" in line and not line.strip().startswith("#"):
                        scan.append(line)
                    elif line.strip() == "":
                        continue
                    else:
                        break
            for line in scan:
                if ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key = key.strip().lower()
                val = val.strip().strip('"\'')
                if key in ("name", "description", "triggers") and val:
                    meta[key] = val
                elif key in ("allowed_tools", "allowed-tools") and val:
                    raw_tools = val.strip("[]")
                    meta["allowed_tools"] = [
                        item.strip().strip('"\'')
                        for item in raw_tools.split(",")
                        if item.strip()
                    ]
            # description 兜底：首个非空、非 front-matter、非标题行。
            if not meta["description"]:
                for line in lines:
                    s = line.strip()
                    if not s or s == "---" or s.startswith("#") or ":" in s.split(" ")[0]:
                        continue
                    meta["description"] = s
                    break
                else:
                    first = next((l.strip() for l in lines if l.strip()), "")
                    meta["description"] = first
        except Exception:
            pass
        return meta

    def list_skills_structured(self) -> list:
        """返回结构化技能目录：[{name, category, description, triggers}, ...]。

        供延迟加载/skill_context 使用；metadata 缺失时用目录名 + 首行兜底。
        """
        catalog = []
        for skill_path in glob.glob(os.path.join(self.skills_dir, "**", "SKILL.md"), recursive=True):
            if not os.path.isfile(skill_path):
                continue
            rel_path = os.path.relpath(skill_path, self.skills_dir)
            parts = rel_path.split(os.sep)
            if len(parts) >= 3:
                category, skill_name = parts[0], parts[-2]
            else:
                category, skill_name = "uncategorized", parts[-2] if len(parts) >= 2 else "unknown"
            if category.startswith("."):
                continue
            try:
                content = Path(skill_path).read_text(encoding="utf-8")
                meta = self.parse_skill_metadata(content)
            except Exception:
                meta = {"name": "", "description": "无描述", "triggers": "", "allowed_tools": []}
            catalog.append({
                "name": meta.get("name") or skill_name,
                "dir_name": skill_name,
                "category": category,
                "description": meta.get("description") or "无描述",
                "triggers": meta.get("triggers", ""),
                "allowed_tools": list(meta.get("allowed_tools") or []),
            })
        return catalog

    def _validate_simple_name(self, value: str, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required.")
        name = value.strip()
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or any(sep in name for sep in ("/", "\\")):
            raise ValueError(f"{field_name} must be a simple relative directory name without path traversal.")
        if name.startswith("."):
            raise ValueError(f"{field_name} must not start with '.'.")
        return name

    def _validate_skill_name(self, skill_name: str) -> str:
        return self._validate_simple_name(skill_name, "skill_name")

    def _validate_category(self, category: str | None) -> str:
        cat = (category or "uncategorized").strip() or "uncategorized"
        if cat == "uncategorized":
            return cat
        return self._validate_simple_name(cat, "category")

    def _skill_dirs(self, skill_name: str):
        name = self._validate_skill_name(skill_name)
        pattern = os.path.join(self.skills_dir, "**", name, "SKILL.md")
        return [Path(p).parent for p in glob.glob(pattern, recursive=True) if os.path.isfile(p)]

    def resolve_skill_dir(self, skill_name: str) -> Path:
        matches = sorted(self._skill_dirs(skill_name), key=lambda p: str(p))
        if not matches:
            raise FileNotFoundError(f"技能 '{skill_name}' 不存在。")
        root = Path(self.skills_dir).resolve()
        resolved_matches = []
        for match in matches:
            resolved = match.resolve()
            resolved.relative_to(root)
            resolved_matches.append(resolved)
        if len(resolved_matches) > 1:
            rels = [str(p.relative_to(root)) for p in resolved_matches]
            raise ValueError(f"技能 '{skill_name}' 存在多个匹配，请先按类目消歧: {rels}")
        return resolved_matches[0]

    def _safe_file_path(self, skill_dir: Path, file_path: str | None) -> Path:
        if not file_path:
            return skill_dir / "SKILL.md"
        raw = str(file_path).strip().replace("\\", "/")
        rel = Path(raw)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("file_path must stay inside the skill directory and cannot contain '..'.")
        parts = [p for p in rel.parts if p not in ("", ".")]
        if not parts:
            raise ValueError("file_path is empty.")
        if parts[0] == "SKILL.md" and len(parts) != 1:
            raise ValueError("SKILL.md cannot be used as a directory in file_path.")
        if parts[0] != "SKILL.md" and parts[0] not in self.ALLOWED_SUPPORT_DIRS:
            raise ValueError("file_path must be SKILL.md or under one of: " + ", ".join(sorted(self.ALLOWED_SUPPORT_DIRS)))
        target = (skill_dir / rel).resolve()
        target.relative_to(skill_dir.resolve())
        return target

    def list_skills(self) -> str:
        from collections import defaultdict
        categorized_skills = defaultdict(list)
        for skill_path in glob.glob(os.path.join(self.skills_dir, "**", "SKILL.md"), recursive=True):
            if not os.path.isfile(skill_path): continue
            rel_path = os.path.relpath(skill_path, self.skills_dir); parts = rel_path.split(os.sep)
            if len(parts) >= 3: category, skill_name = parts[0], parts[-2]
            else: category, skill_name = "uncategorized", parts[-2] if len(parts) >= 2 else "unknown"
            if category.startswith("."): continue
            try:
                content = Path(skill_path).read_text(encoding="utf-8")
                desc = self.parse_skill_metadata(content).get("description") or "无描述"
                categorized_skills[category].append(f"  - **{skill_name}**: {desc}")
            except Exception as e:
                categorized_skills[category].append(f"  - **{skill_name}**: 加载描述失败 ({str(e)})")
        if not categorized_skills: return "当前没有任何技能。"
        result = "可用的技能列表：\n"
        for category, skills in sorted(categorized_skills.items()):
            result += f"\n### {self._display_category_name(category)}\n" + "\n".join(sorted(skills)) + "\n"
        return result

    def view_skill(self, skill_name: str, file_path: str = None) -> str:
        skill_dir = self.resolve_skill_dir(skill_name); target = self._safe_file_path(skill_dir, file_path)
        if not target.exists() or not target.is_file(): raise FileNotFoundError(f"技能文件不存在: {file_path or 'SKILL.md'}")
        return target.read_text(encoding="utf-8")

    def create_skill(self, skill_name: str, description: str, content: str, category: str = "uncategorized", overwrite: bool = False) -> str:
        name = self._validate_skill_name(skill_name); cat = self._validate_category(category)
        skill_folder = Path(self.skills_dir) / (cat if cat != "uncategorized" else "") / name
        target_resolved = skill_folder.resolve()
        existing = sorted(self._skill_dirs(name), key=lambda p: str(p))
        if existing:
            root = Path(self.skills_dir).resolve()
            rels = [str(p.resolve().relative_to(root)) for p in existing]
            target_exists = any(p.resolve() == target_resolved for p in existing)
            if not overwrite:
                raise FileExistsError(f"技能 '{name}' 已存在；如需覆盖请使用 edit/patch 或显式 overwrite。匹配: {rels}")
            if not target_exists:
                raise FileExistsError(f"技能 '{name}' 已存在于其他类目；拒绝通过 create 生成同名副本。匹配: {rels}")
        skill_folder.mkdir(parents=True, exist_ok=True)
        body = content if content.lstrip().startswith("---") else f"---\nname: \"{name}\"\ndescription: \"{description}\"\n---\n\n{content}"
        (skill_folder / "SKILL.md").write_text(body, encoding="utf-8")
        verb = "updated" if existing else "created"
        return f"Successfully {verb} skill: {name} in category: {cat}"

    def edit_skill_file(self, skill_name: str, content: str, file_path: str = None) -> str:
        skill_dir = self.resolve_skill_dir(skill_name); target = self._safe_file_path(skill_dir, file_path)
        target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content, encoding="utf-8")
        return f"Successfully wrote {skill_name}:{file_path or 'SKILL.md'}"

    def patch_skill_file(self, skill_name: str, old_string: str, new_string: str, file_path: str = None) -> str:
        if old_string is None or new_string is None: raise ValueError("old_string and new_string are required for patch.")
        skill_dir = self.resolve_skill_dir(skill_name); target = self._safe_file_path(skill_dir, file_path)
        if not target.exists(): raise FileNotFoundError(f"技能文件不存在: {file_path or 'SKILL.md'}")
        content = target.read_text(encoding="utf-8"); count = content.count(old_string)
        if count == 0: raise ValueError("old_string not found.")
        if count > 1: raise ValueError("old_string ambiguous; appears multiple times.")
        target.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
        return f"Successfully patched {skill_name}:{file_path or 'SKILL.md'}"

    def remove_skill_file(self, skill_name: str, file_path: str) -> str:
        if not file_path: raise ValueError("file_path is required for remove_file; use delete to remove a whole skill.")
        skill_dir = self.resolve_skill_dir(skill_name); target = self._safe_file_path(skill_dir, file_path)
        if target.name == "SKILL.md": raise ValueError("Refusing to remove SKILL.md via remove_file; use delete instead.")
        if not target.exists(): raise FileNotFoundError(f"技能文件不存在: {file_path}")
        target.unlink(); return f"Successfully removed {skill_name}:{file_path}"

    def delete_skill(self, skill_name: str) -> str:
        skill_folder = self.resolve_skill_dir(skill_name); parent_dir = skill_folder.parent
        shutil.rmtree(skill_folder); root = Path(self.skills_dir).resolve()
        try:
            if parent_dir.resolve() != root and not any(parent_dir.iterdir()): parent_dir.rmdir()
        except Exception: pass
        return f"Successfully deleted skill: {skill_name}"

skill_manager = SkillManager()
