import os
import re
import tempfile
import threading
from contextlib import contextmanager

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - Windows fallback
    fcntl = None


class MemoryOperationError(Exception):
    """Memory 操作失败。"""


# Backward compatibility for older imports.  Prefer MemoryOperationError in new code
# to avoid confusion with Python's built-in MemoryError.
MemoryError = MemoryOperationError


class MemoryManager:
    """
    持久化记忆管理器。

    P0 安全加固内容：
    - atomic write：临时文件 + fsync + os.replace，避免半写文件。
    - duplicate check：避免重复保存同一条记忆。
    - unique replace/remove：old_text 必须唯一匹配，避免误替换/误删多处。
    - char limit：限制 USER.md / MEMORY.md 大小，避免无限污染 system prompt。
    - suspicious content scan：拒绝明显的 prompt injection / secret 内容。
    - frozen snapshot：Agent 启动时读取一次；写入只影响落盘和未来 session。

    仍然兼容旧接口：
    - read_memory()
    - append_memory(file_type, content)
    - replace_memory(file_type, old_content, new_content)
    - remove_memory(file_type, old_content)
    """

    USER_CHAR_LIMIT = 4000
    MEMORY_CHAR_LIMIT = 6000

    # 不是完整安全系统，只是 P0 的基础防线：阻止最明显不该进入长期记忆的内容。
    # 注意：仅用于新增/替换的新内容；remove/replace 的 old_text 不做此扫描，
    # 否则一旦历史里已经存在可疑内容，就会无法删除。
    SUSPICIOUS_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(all\s+)?previous\s+instructions",
        r"reveal\s+(the\s+)?system\s+prompt",
        r"print\s+(the\s+)?system\s+prompt",
        r"exfiltrate",
        r"BEGIN\s+(RSA\s+|OPENSSH\s+|PRIVATE\s+)?PRIVATE\s+KEY",
        r"api[_-]?key\s*[:=]",
        r"secret[_-]?key\s*[:=]",
        r"access[_-]?token\s*[:=]",
        r"password\s*[:=]",
    ]

    def __init__(self, memory_dir: str = "memories"):
        self.memory_dir = memory_dir
        os.makedirs(self.memory_dir, exist_ok=True)

        self.user_file = os.path.join(self.memory_dir, "USER.md")
        self.memory_file = os.path.join(self.memory_dir, "MEMORY.md")
        self.lock_file = os.path.join(self.memory_dir, ".memory.lock")
        self._thread_lock = threading.RLock()
        self._snapshot = None

        self._ensure_file(self.user_file)
        self._ensure_file(self.memory_file)

    # ------------------------------------------------------------------
    # 基础文件工具
    # ------------------------------------------------------------------
    def _ensure_file(self, path: str) -> None:
        if not os.path.exists(path):
            self._atomic_write(path, "")

    @contextmanager
    def _lock(self):
        """进程间 advisory lock；fcntl 不可用时至少保留进程内线程锁。"""
        os.makedirs(self.memory_dir, exist_ok=True)
        with self._thread_lock:
            with open(self.lock_file, "a+", encoding="utf-8") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    if fcntl is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _atomic_write(self, path: str, text: str) -> None:
        """安全写文件：要么完整成功，要么原文件保持不变。"""
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-memory-", dir=directory, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
            # 尽量 fsync 目录，确保 rename 元数据落盘。部分平台可能不支持目录 fsync。
            try:
                dir_fd = os.open(directory, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except (AttributeError, OSError):  # pragma: no cover - platform dependent
                pass
        except Exception:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            raise

    def _read_file(self, path: str) -> str:
        self._ensure_file(path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _target_file(self, file_type: str) -> tuple[str, str, int]:
        normalized = (file_type or "memory").strip().lower()
        if normalized == "user":
            return self.user_file, "USER", self.USER_CHAR_LIMIT
        if normalized == "memory":
            return self.memory_file, "MEMORY", self.MEMORY_CHAR_LIMIT
        # 兼容旧行为：非 USER 默认写入 MEMORY。
        return self.memory_file, "MEMORY", self.MEMORY_CHAR_LIMIT

    # ------------------------------------------------------------------
    # 校验与规范化
    # ------------------------------------------------------------------
    def _validate_non_empty(self, content: str, *, operation: str) -> str:
        if content is None:
            raise MemoryOperationError(f"content is required for {operation}.")
        text = str(content).strip()
        if not text:
            raise MemoryOperationError(f"content is empty for {operation}.")
        return text

    def _validate_new_content(self, content: str, *, operation: str) -> str:
        text = self._validate_non_empty(content, operation=operation)
        for pattern in self.SUSPICIOUS_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                raise MemoryOperationError(
                    "Refusing to store suspicious memory content. "
                    f"Matched safety pattern: {pattern}"
                )
        return text

    def _normalize_entry(self, text: str) -> str:
        # 兼容当前 bullet 格式：'- xxx'
        stripped = text.strip()
        stripped = re.sub(r"^[-*]\s+", "", stripped)
        return " ".join(stripped.lower().split())

    def _existing_entries(self, content: str) -> list[str]:
        # 当前文件基本是 bullet list；这里也兼容普通多行文本。
        entries = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(line)
        return entries

    def _is_duplicate(self, existing_content: str, new_entry: str) -> bool:
        normalized_new = self._normalize_entry(new_entry)
        return any(
            self._normalize_entry(entry) == normalized_new
            for entry in self._existing_entries(existing_content)
        )

    def _ensure_limit(self, text: str, limit: int, label: str) -> None:
        if len(text) > limit:
            raise MemoryOperationError(
                f"{label}.md would exceed char limit: {len(text)}/{limit}. "
                "Please remove or replace old memory first."
            )

    def _replace_once_unique(self, content: str, old: str, new: str) -> str:
        count = content.count(old)
        if count == 0:
            raise MemoryOperationError(f"old_text not found in memory: {old}")
        if count > 1:
            raise MemoryOperationError(
                "old_text is ambiguous and appears multiple times; "
                "please provide a longer exact substring."
            )
        return content.replace(old, new, 1)

    def _format_snapshot(self, user_content: str, memory_content: str) -> str:
        snapshot = ""
        if user_content:
            snapshot += f"\n<user_preferences>\n{user_content}\n</user_preferences>\n"
        if memory_content:
            snapshot += f"\n<environmental_memory>\n{memory_content}\n</environmental_memory>\n"
        return snapshot

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    def read_memory_live(self) -> str:
        """实时读取当前记忆文件。"""
        with self._lock():
            user_content = self._read_file(self.user_file).strip()
            memory_content = self._read_file(self.memory_file).strip()
        return self._format_snapshot(user_content, memory_content)

    def read_memory(self) -> str:
        """兼容旧接口：实时获取当前记忆快照。"""
        return self.read_memory_live()

    def load_snapshot(self) -> str:
        """在 Agent/session 启动时冻结一次记忆快照。"""
        self._snapshot = self.read_memory_live()
        return self._snapshot

    def read_memory_snapshot(self) -> str:
        """读取已冻结的快照；若尚未冻结，则先冻结。"""
        if self._snapshot is None:
            return self.load_snapshot()
        return self._snapshot

    def read_target(self, file_type: str) -> str:
        """读取单个目标文件，供 /mem 等本地命令使用。"""
        target_file, _label, _limit = self._target_file(file_type)
        with self._lock():
            return self._read_file(target_file)

    def append_memory(self, file_type: str, content: str) -> str:
        """追加记忆内容；重复内容会跳过。"""
        target_file, label, limit = self._target_file(file_type)
        entry = self._validate_new_content(content, operation="add")

        with self._lock():
            current = self._read_file(target_file)
            if self._is_duplicate(current, entry):
                return f"Skipped duplicate {label} memory; content already exists."

            prefix = "" if not current.strip() else "\n"
            new_text = f"{current.rstrip()}{prefix}- {entry}\n"
            self._ensure_limit(new_text, limit, label)
            self._atomic_write(target_file, new_text)

        return f"Successfully appended to {label} memory."

    def replace_memory(self, file_type: str, old_content: str, new_content: str) -> str:
        """替换记忆内容；old_content 必须唯一匹配。"""
        target_file, label, limit = self._target_file(file_type)
        # old_text 只检查非空，不做 suspicious scan；否则无法删除/修复已有污染内容。
        old = self._validate_non_empty(old_content, operation="replace(old_text)")
        new = self._validate_new_content(new_content, operation="replace(content)")

        with self._lock():
            current = self._read_file(target_file)
            new_text = self._replace_once_unique(current, old, new)
            self._ensure_limit(new_text, limit, label)
            self._atomic_write(target_file, new_text)

        return f"Successfully replaced memory in {label}."

    def remove_memory(self, file_type: str, old_content: str) -> str:
        """删除记忆内容；old_content 必须唯一匹配。"""
        target_file, label, _limit = self._target_file(file_type)
        # old_text 只检查非空，不做 suspicious scan；否则无法删除已有污染内容。
        old = self._validate_non_empty(old_content, operation="remove(old_text)")

        with self._lock():
            current = self._read_file(target_file)
            new_text = self._replace_once_unique(current, old, "")
            # 清理多余空行与空 bullet（例如删除 "- xxx" 后留下的 "-"）。
            cleaned_lines = []
            for line in new_text.splitlines():
                stripped = line.strip()
                if stripped in {"", "-", "*"}:
                    continue
                cleaned_lines.append(line.rstrip())
            new_text = "\n".join(cleaned_lines)
            if new_text:
                new_text += "\n"
            self._atomic_write(target_file, new_text)

        return f"Successfully removed memory from {label}."


    def _read_target_lines_unlocked(self, file_type: str) -> tuple[str, list[str]]:
        target_file, label, _limit = self._target_file(file_type)
        return label, self._read_file(target_file).splitlines()

    def _read_targets_for_query_unlocked(self, target: str) -> list[tuple[str, list[str]]]:
        normalized = (target or "all").strip().lower()
        if normalized == "all":
            return [
                self._read_target_lines_unlocked("user"),
                self._read_target_lines_unlocked("memory"),
            ]
        if normalized in {"user", "memory"}:
            return [self._read_target_lines_unlocked(normalized)]
        raise MemoryOperationError("target must be 'all', 'user', or 'memory'.")

    def search_memory(self, query: str, target: str = "all", max_results: int = 5) -> dict:
        """在 USER.md / MEMORY.md 中做轻量纯文本搜索。"""
        text_query = self._validate_non_empty(query, operation="search(query)")
        try:
            limit = int(max_results)
        except (TypeError, ValueError):
            raise MemoryOperationError("max_results must be an integer.")
        limit = max(1, min(limit, 50))

        terms = [term for term in re.split(r"\s+", text_query.lower()) if term]
        phrase = text_query.lower()
        results = []

        with self._lock():
            target_lines = self._read_targets_for_query_unlocked(target)
            for label, lines in target_lines:
                for line_no, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    haystack = stripped.lower()
                    score = sum(haystack.count(term) for term in terms)
                    if phrase and phrase in haystack and len(terms) > 1:
                        score += len(terms)
                    if score <= 0:
                        continue
                    results.append({
                        "target": label.lower(),
                        "line": line_no,
                        "score": score,
                        "snippet": stripped,
                    })

        results.sort(key=lambda item: (-item["score"], item["target"], item["line"]))
        return {
            "query": text_query,
            "target": (target or "all").strip().lower(),
            "max_results": limit,
            "count": min(len(results), limit),
            "results": results[:limit],
        }

    def get_memory(self, target: str, from_line: int = 1, lines: int = 50) -> dict:
        """按行读取单个 memory 目标，供 memory_get 工具分页查看。"""
        normalized = (target or "").strip().lower()
        if normalized not in {"user", "memory"}:
            raise MemoryOperationError("target must be 'user' or 'memory'.")

        try:
            start = int(from_line)
        except (TypeError, ValueError):
            raise MemoryOperationError("from_line must be an integer.")
        try:
            page_size = int(lines)
        except (TypeError, ValueError):
            raise MemoryOperationError("lines must be an integer.")

        start = max(1, start)
        page_size = max(1, min(page_size, 200))

        with self._lock():
            label, all_lines = self._read_target_lines_unlocked(normalized)

        total = len(all_lines)
        if start > total:
            selected = []
        else:
            selected = all_lines[start - 1:start - 1 + page_size]

        content = [
            {"line": start + idx, "text": line}
            for idx, line in enumerate(selected)
        ]
        end_line = start + len(selected) - 1 if selected else start - 1
        return {
            "target": label.lower(),
            "from_line": start,
            "lines": page_size,
            "total_lines": total,
            "end_line": end_line,
            "has_more": end_line < total,
            "content": content,
        }


memory_manager = MemoryManager()
