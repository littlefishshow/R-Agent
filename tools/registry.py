import json
import os
import glob
import importlib
import sys
from typing import Callable, Dict, Any, List

class ToolRegistry:
    """
    工具注册表，负责管理工具的 Schema 以及如何执行这些工具。
    """
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(self, name: str, description: str, parameters: Dict[str, Any], handler: Callable):
        """注册一个工具"""
        self._tools[name] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                }
            },
            "handler": handler
        }

    def reload_all(self):
        """重新扫描并加载所有工具模块"""
        self._tools.clear()
        current_dir = os.path.dirname(os.path.abspath(__file__))
        for file_path in glob.glob(os.path.join(current_dir, "*.py")):
            module_name = os.path.basename(file_path)[:-3]
            if module_name not in ["__init__", "registry"]:
                full_module_name = f"tools.{module_name}"
                try:
                    if full_module_name in sys.modules:
                        importlib.reload(sys.modules[full_module_name])
                    else:
                        importlib.import_module(full_module_name)
                except Exception as e:
                    print(f"⚠️ Warning: Failed to load tool module {module_name}: {e}")

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """获取所有已注册工具的 schema 列表，每次获取前自动热更新"""
        self.reload_all()
        return [tool["schema"] for tool in self._tools.values()]

    def execute_tool(self, name: str, args_json: str) -> str:
        """执行工具，返回结果的 JSON 字符串"""
        if name not in self._tools:
            return json.dumps({"error": f"Tool '{name}' not found."})
        
        try:
            # 解析 LLM 返回的 JSON 参数
            args = json.loads(args_json)
            handler = self._tools[name]["handler"]
            # 将参数解包传递给函数
            if isinstance(args, dict):
                result = handler(**args)
            elif isinstance(args, list):
                result = handler(*args)
            else:
                result = handler(args)
            return json.dumps({"success": True, "result": result}, ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid JSON arguments."})
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

# 全局单例，便于其他模块引入并注册工具
registry = ToolRegistry()
