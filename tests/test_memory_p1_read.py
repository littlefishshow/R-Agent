import json

from core.memory import MemoryManager
from tools import memory_read_tool


def test_search_memory_all_and_target_filter(tmp_path):
    store = MemoryManager(memory_dir=str(tmp_path))
    store.append_memory("user", "用户喜欢中文回复")
    store.append_memory("memory", "项目使用 frozen snapshot")

    all_result = store.search_memory("中文 snapshot", target="all", max_results=10)
    assert all_result["count"] == 2
    assert {item["target"] for item in all_result["results"]} == {"user", "memory"}

    user_result = store.search_memory("中文 snapshot", target="user", max_results=10)
    assert user_result["count"] == 1
    assert user_result["results"][0]["target"] == "user"


def test_get_memory_pagination_and_bounds(tmp_path):
    store = MemoryManager(memory_dir=str(tmp_path))
    store.append_memory("memory", "第一条")
    store.append_memory("memory", "第二条")
    store.append_memory("memory", "第三条")

    page = store.get_memory("memory", from_line=2, lines=1)
    assert page["total_lines"] == 3
    assert page["content"] == [{"line": 2, "text": "- 第二条"}]
    assert page["has_more"] is True

    out_of_range = store.get_memory("memory", from_line=99, lines=10)
    assert out_of_range["content"] == []
    assert out_of_range["has_more"] is False


def test_memory_read_tools(monkeypatch, tmp_path):
    store = MemoryManager(memory_dir=str(tmp_path))
    store.append_memory("user", "用户喜欢简洁回答")
    monkeypatch.setattr(memory_read_tool, "memory_manager", store)

    search_result = json.loads(memory_read_tool.memory_search(query="简洁", target="all"))
    assert search_result["success"] is True
    assert search_result["count"] == 1

    get_result = json.loads(memory_read_tool.memory_get(target="user", from_line=1, lines=20))
    assert get_result["success"] is True
    assert get_result["content"][0]["text"] == "- 用户喜欢简洁回答"
