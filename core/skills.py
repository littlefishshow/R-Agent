import os
import yaml

class SkillManager:
    """
    技能系统管理器 (对应 hermes-agent 的 skills_hub 和 curator)
    负责管理技能的渐进式加载：List (Tier 1) -> View (Tier 2) -> Load (Tier 3)
    """
    CATEGORY_DISPLAY_NAMES = {
        "creative": "创意创作",
        "github": "GitHub / 代码协作",
        "productivity": "生产力 / 办公自动化",
        "uncategorized": "未分类",
    }

    def __init__(self, skills_dir: str = "R-Agent/skills"):
        self.skills_dir = skills_dir
        os.makedirs(self.skills_dir, exist_ok=True)

    def _display_category_name(self, category: str) -> str:
        """返回用于展示的中文类目名称；未知类目保持首字母大写。"""
        return self.CATEGORY_DISPLAY_NAMES.get(category, category.capitalize())

    def list_skills(self) -> str:
        """获取所有可用技能的名称和简短描述 (Tier 1)，按目录进行分类"""
        import glob
        from collections import defaultdict
        
        # 使用字典按类别分组技能
        categorized_skills = defaultdict(list)
        
        # 递归搜索所有层级的 SKILL.md
        search_pattern = os.path.join(self.skills_dir, "**", "SKILL.md")
        for skill_path in glob.glob(search_pattern, recursive=True):
            if os.path.isfile(skill_path):
                # 获取相对路径以便确定分类
                rel_path = os.path.relpath(skill_path, self.skills_dir)
                parts = rel_path.split(os.sep)
                
                # 如果技能在顶级目录的子目录中（例如 skills/creative/skill_name/SKILL.md）
                if len(parts) >= 3:
                    category = parts[0]
                    skill_name = parts[-2]
                else:
                    category = "uncategorized"
                    skill_name = parts[-2] if len(parts) >= 2 else "unknown"

                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        # 尝试从第一行或 frontmatter 中提取描述
                        desc = content.split('\n')[0] if content else "无描述"
                        if desc.startswith("---") or desc.startswith("name:"):
                            # 简单提取 description
                            for line in content.split('\n'):
                                if line.startswith("description:"):
                                    desc = line.replace("description:", "").strip().strip('"\'')
                                    break
                        categorized_skills[category].append(f"  - **{skill_name}**: {desc}")
                except Exception as e:
                    categorized_skills[category].append(f"  - **{skill_name}**: 加载描述失败 ({str(e)})")
        
        if not categorized_skills:
            return "当前没有任何技能。"
            
        result = "可用的技能列表：\n"
        for category, skills in sorted(categorized_skills.items()):
            result += f"\n### {self._display_category_name(category)}\n"
            result += "\n".join(skills) + "\n"
            
        return result

    def view_skill(self) -> str:
        """查看指定技能的完整 SKILL.md (Tier 2)"""
        pass

    def view_skill(self, skill_name: str) -> str:
        skill_path = os.path.join(self.skills_dir, skill_name, "SKILL.md")
        if not os.path.exists(skill_path):
            return f"Error: 技能 '{skill_name}' 不存在。"
        
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()

    def create_skill(self, skill_name: str, description: str, content: str, category: str = "uncategorized") -> str:
        """创建一个新技能或更新已有技能"""
        # 如果提供了分类，则在对应的分类目录下创建
        if category and category != "uncategorized":
            skill_folder = os.path.join(self.skills_dir, category, skill_name)
        else:
            # 默认为了防止和已有分类冲突，放根目录或者 uncategorized 目录，这里放根目录便于被识别为未分类
            skill_folder = os.path.join(self.skills_dir, skill_name)
            
        os.makedirs(skill_folder, exist_ok=True)
        
        skill_path = os.path.join(skill_folder, "SKILL.md")
        with open(skill_path, "w", encoding="utf-8") as f:
            # 写入 YAML 前置元数据和主体
            f.write(f"---\nname: \"{skill_name}\"\ndescription: \"{description}\"\n---\n\n{content}")
            
        return f"Successfully created/updated skill: {skill_name} in category: {category}"

    def delete_skill(self, skill_name: str) -> str:
        """删除一个技能，并尝试清理空目录"""
        import shutil
        import glob
        
        # 查找该技能的真实路径 (因为它可能在某个分类目录下)
        search_pattern = os.path.join(self.skills_dir, "**", skill_name)
        skill_folders = [p for p in glob.glob(search_pattern, recursive=True) if os.path.isdir(p)]
        
        if not skill_folders:
            return f"Error: 技能 '{skill_name}' 不存在。"
            
        skill_folder = skill_folders[0]
        parent_dir = os.path.dirname(skill_folder)
        
        try:
            shutil.rmtree(skill_folder)
            
            # 如果父目录不是 skills 根目录，且删除技能后变为空，则顺便删除空分类目录
            if parent_dir != os.path.abspath(self.skills_dir) and not os.listdir(parent_dir):
                os.rmdir(parent_dir)
                
            return f"Successfully deleted skill: {skill_name}"
        except Exception as e:
            return f"Error deleting skill: {str(e)}"

skill_manager = SkillManager()
