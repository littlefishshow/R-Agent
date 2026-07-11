import os
import glob
import importlib

# 获取当前工具目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 动态加载所有的 python 文件。
#
# `tools/autoresearch_tool.py` is a registry-discovery shim that reloads the
# real implementation in `autoresearch.autoresearch_tool`.  Importing it while
# the `tools` package itself is still initializing can recurse back into a
# partially initialized autoresearch module, so leave it to ToolRegistry.reload_all().
for file_path in glob.glob(os.path.join(current_dir, "*.py")):
    module_name = os.path.basename(file_path)[:-3]
    if module_name not in ["__init__", "registry", "autoresearch_tool"]:
        try:
            importlib.import_module(f"tools.{module_name}")
        except Exception as e:
            print(f"⚠️ Warning: Failed to load tool module {module_name}: {e}")
