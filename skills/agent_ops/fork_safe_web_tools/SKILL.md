---
name: "fork_safe_web_tools"
description: "修复 macOS fork 子进程 Web 工具崩溃"
---

---
name: "fork_safe_web_tools"
description: "修复 macOS fork 子进程 Web 工具崩溃"
---

# Fork-safe Web Tools on macOS

## When to Use
- Agent 工具在 `execute_tool_isolated()` 子进程模式下返回 `Tool process ended without returning a result`。
- 同一个 Web 工具直接 `execute_tool()` 可用，但真实 LLM tool call 或隔离执行失败。
- macOS 环境下 Python `urllib`/网络请求在 fork 后子进程中崩溃、无 traceback、无 JSON 返回。
- DuckDuckGo HTML 搜索出现 anomaly/反爬页，导致 `web_search` 返回空结果。

## Diagnosis
1. 先分别验证同步与隔离执行：
   ```bash
   python3 - <<'PY'
   from tools.registry import registry
   registry.reload_all()
   print(registry.execute_tool('web_search', '{"query":"example domain","limit":1}'))
   print(registry.execute_tool_isolated('web_search', '{"query":"example domain","limit":1}', timeout=20))
   print(registry.execute_tool_isolated('web_extract', '{"urls":["https://example.com"]}', timeout=20))
   PY
   ```
2. 若同步可用、隔离失败，优先怀疑 fork 子进程中网络/DNS/TLS/系统代理解析问题，而不是工具 schema 或 handler 参数问题。
3. 在 macOS 上，`urllib` 默认 opener 可能触发系统代理发现（`_scproxy` / CoreFoundation），fork 子进程中可能直接退出，父进程只看到 EOF/无结果。

## Fix Pattern
1. Web 抓取入口优先调用 `curl` 子进程：
   - 使用 `subprocess.run([...], stdout=PIPE, stderr=PIPE, check=False)`；
   - 设置 `--max-time`、`-L`、明确 User-Agent；
   - 子进程返回非零时，将 stderr 纳入 Python 异常消息。
2. 保留 `urllib` 兜底时使用显式空代理：
   ```python
   _OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
   ```
   避免隐式系统代理发现。
3. 所有失败必须返回 JSON 字符串，不让异常穿透工具进程入口。
4. 对 `web_search` 使用多 provider fallback：
   - DuckDuckGo HTML 作为第一选择；
   - 检测 `anomaly` 且无 `result__` 时视为空结果；
   - 继续尝试 Bing / Yahoo HTML 解析；
   - 全部失败时返回 `{success: true, results: [], warnings: [...]}` 或明确 `{success:false,error:...}`，不要崩溃。

## Verification
- 运行语法检查：
  ```bash
  python3 -m py_compile tools/web_tools.py tools/registry.py tests/test_tool_process_isolation.py
  ```
- 运行隔离执行测试：
  ```bash
  python3 -m pytest tests/test_tool_process_isolation.py
  ```
- 用真实工具入口验证：
  - `web_extract(urls=["https://example.com"])` 应返回 Example Domain 文本；
  - `web_search(query="example domain", limit=2)` 应返回 JSON，不应再出现 `Tool process ended without returning a result`。

## Documentation
维护 R-Agent 项目时，按用户偏好在 `README.md` 的更新日志中记录日期、根因、修改内容和验证结果。