import json
import os
import glob
import importlib
import sys
import time
import multiprocessing
import threading
from typing import Callable, Dict, Any, List, Optional, Type

import cloudpickle


def _json_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _terminate_process(process: multiprocessing.Process, join_timeout: float = 1.0):
    """Best-effort terminate, then kill, a child process."""
    if not process.is_alive():
        process.join(timeout=0)
        return

    process.terminate()
    process.join(timeout=join_timeout)

    if process.is_alive():
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
            process.join(timeout=join_timeout)


def _tool_process_start_method(platform: Optional[str] = None) -> str:
    """Choose a safe multiprocessing start method for isolated tools."""
    return "spawn" if (platform or sys.platform) in ("darwin", "win32") else "fork"


def _execute_tool_from_mapping(tools: Dict[str, Dict[str, Any]], name: str, args_json: str) -> str:
    """Execute against a registry mapping and return the legacy JSON string."""
    if name not in tools:
        return json.dumps({"error": f"Tool '{name}' not found."})

    try:
        args = json.loads(args_json)
        handler = tools[name]["handler"]
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


def _tool_process_entry(conn, tools_payload, name: str, args_json: str):
    """Child-process entry point for isolated tool execution.

    The child always attempts to send a JSON string back to the parent.  When a
    registry snapshot is available (fork or pickleable handlers), use it so
    dynamically registered tools keep working.  Otherwise reload module-defined
    tools in the child as a compatibility fallback for spawn-based platforms.
    """
    try:
        tools_snapshot = cloudpickle.loads(tools_payload) if tools_payload is not None else None
        if tools_snapshot is not None:
            result = _execute_tool_from_mapping(tools_snapshot, name, args_json)
        else:
            registry.reload_all()
            result = registry.execute_tool(name, args_json)
        if not isinstance(result, str):
            result = json.dumps({"success": True, "result": result}, ensure_ascii=False)
    except Exception as exc:
        result = _json_error(str(exc))

    try:
        conn.send(result)
    except Exception as exc:
        # If the value unexpectedly cannot be pickled/sent, make one final
        # attempt with a plain JSON error string.
        try:
            conn.send(_json_error(f"Failed to return tool result: {exc}"))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


class ToolExecutionInterrupted(Exception):
    """Raised by isolated execution when the caller's cancel event is set."""


class ToolRegistry:
    """
    工具注册表，负责管理工具的 Schema 以及如何执行这些工具。
    """
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._tools_signature = None

    def register(self, name: str, description: str, parameters: Dict[str, Any], handler: Callable,
                 metadata: Optional[Dict[str, Any]] = None):
        """注册一个工具。

        ``metadata`` 是可选的轻量元信息（如 ``{"summary": "...", "category": "..."}``），
        供工具目录（deferred exposure）使用。不传时向后兼容，目录会用 description 的
        首行兜底。
        """
        with self._lock:
            self._tools[name] = {
                "schema": {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    }
                },
                "handler": handler,
                "metadata": dict(metadata or {}),
            }

    def _iter_tool_files(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        for file_path in glob.glob(os.path.join(current_dir, "*.py")):
            module_name = os.path.basename(file_path)[:-3]
            if module_name not in ["__init__", "registry"]:
                yield file_path, module_name

    def _compute_tools_signature(self):
        signature = []
        for file_path, module_name in self._iter_tool_files():
            try:
                stat = os.stat(file_path)
            except OSError:
                continue
            signature.append((module_name, stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(signature))

    def reload_all(self, *, force: bool = True):
        """重新扫描并加载所有工具模块"""
        with self._lock:
            signature = self._compute_tools_signature()
            if not force and self._tools and signature == self._tools_signature:
                return

            self._tools.clear()
            self._tools_signature = signature
            for _file_path, module_name in self._iter_tool_files():
                full_module_name = f"tools.{module_name}"
                try:
                    if full_module_name in sys.modules:
                        importlib.reload(sys.modules[full_module_name])
                    else:
                        importlib.import_module(full_module_name)
                except Exception as e:
                    print(f"⚠️ Warning: Failed to load tool module {module_name}: {e}")

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """获取所有已注册工具的 schema 列表，并在工具文件变化时热更新。

        按工具名固定排序返回：注册顺序或 dev 期热重载都可能打乱 dict 插入序，
        而工具 schema 位于模型请求前缀（通过 tools= 字段传递），顺序抖动会击穿
        KV cache。固定排序让稳态前缀逐字节稳定。
        """
        self.reload_all(force=False)
        with self._lock:
            schemas = [tool["schema"] for tool in self._tools.values()]
        schemas.sort(key=lambda s: s.get("function", {}).get("name", ""))
        return schemas

    @staticmethod
    def _summary_of(tool: Dict[str, Any]) -> str:
        """取工具的一句话摘要：优先 metadata.summary，否则用 description 首行。"""
        meta = tool.get("metadata") or {}
        summary = meta.get("summary")
        if summary:
            return str(summary)
        desc = tool.get("schema", {}).get("function", {}).get("description", "") or ""
        return desc.strip().splitlines()[0] if desc.strip() else ""

    def get_tool_catalog(self) -> List[Dict[str, Any]]:
        """返回精简工具目录（name + summary + category），供延迟暴露时给模型看。

        只含"是什么"，不含完整参数 schema，避免工具很多时把上下文撑爆。
        """
        self.reload_all(force=False)
        with self._lock:
            catalog = []
            for name, tool in self._tools.items():
                meta = tool.get("metadata") or {}
                catalog.append({
                    "name": name,
                    "summary": self._summary_of(tool),
                    "category": meta.get("category", ""),
                })
            return catalog

    def get_schemas_for(self, names) -> List[Dict[str, Any]]:
        """返回指定名字集合的完整 schema（供延迟暴露时提升已选工具）。"""
        self.reload_all(force=False)
        wanted = set(names or [])
        with self._lock:
            return [t["schema"] for n, t in self._tools.items() if n in wanted]

    def search_catalog(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """在工具目录里做轻量关键词匹配，返回 name+summary+category 列表。"""
        q = str(query or "").strip().lower()
        catalog = self.get_tool_catalog()
        if not q:
            return catalog[:limit]
        terms = [t for t in q.replace(",", " ").split() if t]
        scored = []
        for entry in catalog:
            hay = f"{entry['name']} {entry['summary']} {entry['category']}".lower()
            score = sum(hay.count(t) for t in terms)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda x: -x[0])
        return [e for _s, e in scored[:limit]]

    def execute_tool(self, name: str, args_json: str) -> str:
        """执行工具，返回结果的 JSON 字符串"""
        with self._lock:
            tools_snapshot = dict(self._tools)
        return _execute_tool_from_mapping(tools_snapshot, name, args_json)

    def execute_tool_isolated(
        self,
        name: str,
        args_json: str,
        cancel_event=None,
        timeout: Optional[float] = None,
        poll_interval: float = 0.05,
        interrupted_exception: Optional[Type[BaseException]] = None,
    ) -> str:
        """Execute a tool in a child process and return the usual JSON string.

        The parent process polls ``cancel_event`` while waiting.  If it is set,
        the child is terminated (and killed if needed) and ``interrupted_exception``
        is raised when provided; otherwise ToolExecutionInterrupted is raised.

        Timeout and child failures are reported as JSON error strings to match
        the forgiving behavior of ``execute_tool``.
        """
        with self._lock:
            if name not in self._tools:
                return json.dumps({"error": f"Tool '{name}' not found."})
            tools_snapshot = dict(self._tools)

        # macOS warns (and may deadlock) when a multi-threaded process forks.
        # cloudpickle preserves dynamically registered lambdas/closures across
        # spawn, so macOS and Windows can use the safe start method without
        # losing the registry snapshot. Linux keeps fork for lower overhead.
        start_method = _tool_process_start_method()
        try:
            ctx = multiprocessing.get_context(start_method)
        except ValueError:
            ctx = multiprocessing.get_context("spawn")
        try:
            tools_payload = cloudpickle.dumps(tools_snapshot)
        except Exception:
            tools_payload = None

        parent_conn, child_conn = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=_tool_process_entry,
            args=(child_conn, tools_payload, name, args_json),
            daemon=True,
        )

        try:
            process.start()
        except Exception as exc:
            # Retry without the snapshot; child reloads module-defined tools.
            start_error = exc
            try:
                parent_conn.close()
                child_conn.close()
            except Exception:
                pass
            parent_conn, child_conn = ctx.Pipe(duplex=False)
            process = ctx.Process(
                target=_tool_process_entry,
                args=(child_conn, None, name, args_json),
                daemon=True,
            )
            try:
                process.start()
            except Exception as exc2:
                try:
                    parent_conn.close()
                    child_conn.close()
                except Exception:
                    pass
                return _json_error(f"Failed to start tool process: {start_error}; fallback failed: {exc2}")
        finally:
            try:
                child_conn.close()
            except Exception:
                pass

        deadline = None if timeout is None or timeout <= 0 else time.monotonic() + timeout

        try:
            while True:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    _terminate_process(process)
                    exc_cls = interrupted_exception or ToolExecutionInterrupted
                    raise exc_cls()

                if parent_conn.poll(poll_interval):
                    try:
                        result = parent_conn.recv()
                    except EOFError:
                        result = _json_error("Tool process ended without returning a result.")
                    break

                if not process.is_alive():
                    process.join(timeout=0)
                    if parent_conn.poll():
                        try:
                            result = parent_conn.recv()
                        except EOFError:
                            result = _json_error("Tool process ended without returning a result.")
                    else:
                        code = process.exitcode
                        result = _json_error(f"Tool process exited without result (exitcode={code}).")
                    break

                if deadline is not None and time.monotonic() >= deadline:
                    _terminate_process(process)
                    return _json_error(f"Tool '{name}' timed out after {timeout:g}s.")

            process.join(timeout=1.0)
            if not isinstance(result, str):
                try:
                    return json.dumps({"success": True, "result": result}, ensure_ascii=False)
                except Exception as exc:
                    return _json_error(f"Tool returned a non-serializable result: {exc}")
            return result
        finally:
            try:
                parent_conn.close()
            except Exception:
                pass
            if process.is_alive():
                _terminate_process(process)

# 全局单例，便于其他模块引入并注册工具
registry = ToolRegistry()
