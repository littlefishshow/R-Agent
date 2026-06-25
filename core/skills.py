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

    def _validate_skill_name(self, skill_name: str) -> str:
        if not isinstance(skill_name, str) or not skill_name.strip():
            raise ValueError("skill_name is required.")
        name = skill_name.strip()
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or any(sep in name for sep in ("/", "\\")):
            raise ValueError("skill_name must be a simple relative directory name without path traversal.")
        return name

    def _skill_dirs(self, skill_name: str):
        name = self._validate_skill_name(skill_name)
        pattern = os.path.join(self.skills_dir, "**", name, "SKILL.md")
        return [Path(p).parent for p in glob.glob(pattern, recursive=True) if os.path.isfile(p)]

    def resolve_skill_dir(self, skill_name: str) -> Path:
        matches = self._skill_dirs(skill_name)
        if not matches:
            raise FileNotFoundError(f"技能 '{skill_name}' 不存在。")
        resolved = sorted(matches, key=lambda p: str(p))[0].resolve()
        root = Path(self.skills_dir).resolve()
        resolved.relative_to(root)
        return resolved

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
                desc = content.split('\n')[0] if content else "无描述"
                if desc.startswith("---") or desc.startswith("name:"):
                    for line in content.split('\n'):
                        if line.startswith("description:"):
                            desc = line.replace("description:", "").strip().strip('"\''); break
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

    def create_skill(self, skill_name: str, description: str, content: str, category: str = "uncategorized") -> str:
        name = self._validate_skill_name(skill_name); cat = (category or "uncategorized").strip()
        if Path(cat).is_absolute() or ".." in Path(cat).parts: raise ValueError("category must be a relative directory name.")
        skill_folder = Path(self.skills_dir) / (cat if cat != "uncategorized" else "") / name
        skill_folder.mkdir(parents=True, exist_ok=True)
        body = content if content.lstrip().startswith("---") else f"---\nname: \"{name}\"\ndescription: \"{description}\"\n---\n\n{content}"
        (skill_folder / "SKILL.md").write_text(body, encoding="utf-8")
        return f"Successfully created/updated skill: {name} in category: {cat}"

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
