import os

class MemoryManager:
    """
    持久化记忆管理器 (对应 hermes-agent 的 memory_manager.py 和 memory_tool.py 的底层逻辑)
    负责读取和写入 USER.md (用户偏好) 和 MEMORY.md (环境/项目事实)
    """
    def __init__(self, memory_dir: str = "R-Agent/memories"):
        self.memory_dir = memory_dir
        os.makedirs(self.memory_dir, exist_ok=True)
        
        self.user_file = os.path.join(self.memory_dir, "USER.md")
        self.memory_file = os.path.join(self.memory_dir, "MEMORY.md")
        
        # 初始化空文件
        if not os.path.exists(self.user_file):
            with open(self.user_file, "w", encoding="utf-8") as f:
                f.write("")
        if not os.path.exists(self.memory_file):
            with open(self.memory_file, "w", encoding="utf-8") as f:
                f.write("")

    def read_memory(self) -> str:
        """获取当前记忆快照，用于注入到 System Prompt"""
        with open(self.user_file, "r", encoding="utf-8") as f:
            user_content = f.read().strip()
        with open(self.memory_file, "r", encoding="utf-8") as f:
            memory_content = f.read().strip()
            
        snapshot = ""
        if user_content:
            snapshot += f"\n<user_preferences>\n{user_content}\n</user_preferences>\n"
        if memory_content:
            snapshot += f"\n<environmental_memory>\n{memory_content}\n</environmental_memory>\n"
            
        return snapshot

    def append_memory(self, file_type: str, content: str) -> str:
        """追加记忆内容"""
        target_file = self.user_file if file_type.upper() == "USER" else self.memory_file
        with open(target_file, "a", encoding="utf-8") as f:
            f.write(f"\n- {content}")
        return f"Successfully appended to {file_type.upper()} memory."

    def replace_memory(self, file_type: str, old_content: str, new_content: str) -> str:
        """替换记忆内容，用于修正或更新旧事实"""
        target_file = self.user_file if file_type.upper() == "USER" else self.memory_file
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        if old_content not in content:
            return f"Error: '{old_content}' not found in {file_type.upper()} memory."
            
        new_text = content.replace(old_content, new_content)
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(new_text)
        return f"Successfully replaced memory in {file_type.upper()}."

    def remove_memory(self, file_type: str, old_content: str) -> str:
        """删除记忆内容"""
        target_file = self.user_file if file_type.upper() == "USER" else self.memory_file
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        if old_content not in content:
            return f"Error: '{old_content}' not found in {file_type.upper()} memory."
            
        new_text = content.replace(old_content, "")
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(new_text)
        return f"Successfully removed memory from {file_type.upper()}."

memory_manager = MemoryManager()
