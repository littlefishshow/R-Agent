---
name: "isolated_tool_approval_token_diagnosis"
description: "诊断隔离工具进程导致审批 token 永远无效的问题"
---

# isolated_tool_approval_token_diagnosis

## When to Use
- 用户反复明确同意高风险 `run_command`，并且调用时已传入 `allow_high_privilege=true` 与最新 `approval_token`，但工具仍持续返回新的 `permission_required`。
- 高风险命令如 `npm install`、`pip install`、删除命令等在 Agent Loop 中无法通过审批门禁。
- 项目使用 `registry.execute_tool_isolated()` 在子进程中执行工具。

## Root Cause Pattern
`run_command` 的审批 token 若只保存在模块级内存字典中，例如：

```python
_PENDING_COMMAND_APPROVALS = {}
```

而 Agent Loop 使用隔离子进程执行工具：

```python
registry.execute_tool_isolated(...)
```

则首次高风险调用在**子进程 A**中生成 token 并写入子进程 A 的 `_PENDING_COMMAND_APPROVALS`。结果返回父进程后，子进程 A 退出，token 记录随进程内存消失。第二次带 token 的调用会在**子进程 B**中校验，但子进程 B 从父进程 fork/spawn 而来，父进程从未拥有该 token 记录，因此校验失败并生成新 token，表现为“用户再三同意仍无法执行”。

## How to Verify
1. 读取关键代码：
   - `core/agent.py` 是否使用 `registry.execute_tool_isolated()` 执行工具。
   - `tools/sys_tools.py` 是否用模块全局 `_PENDING_COMMAND_APPROVALS` 保存 token。
2. 用最小高风险但无副作用命令复现，例如：
   ```python
   import json
   from tools.registry import registry
   registry.reload_all()
   cmd = 'npm install --help >/dev/null'
   r1 = json.loads(registry.execute_tool_isolated('run_command', json.dumps({'command': cmd, 'timeout': 10})))
   token = json.loads(r1['result'])['approval_token']
   r2 = json.loads(registry.execute_tool_isolated('run_command', json.dumps({'command': cmd, 'timeout': 10, 'allow_high_privilege': True, 'approval_token': token})))
   print(json.loads(r2['result']))
   ```
   若第二次仍返回 `permission_required` 且生成新 token，即命中该问题。
3. 对照同步执行：
   ```python
   r1 = json.loads(registry.execute_tool('run_command', json.dumps({'command': cmd, 'timeout': 10})))
   token = json.loads(r1['result'])['approval_token']
   r2 = json.loads(registry.execute_tool('run_command', json.dumps({'command': cmd, 'timeout': 10, 'allow_high_privilege': True, 'approval_token': token})))
   print(json.loads(r2['result']))
   ```
   若同步执行可通过审批，则进一步确认问题来自隔离子进程内存状态不共享。

## Fix Options
- 推荐：把 pending approvals 存到父进程可见的持久/共享介质，例如 `sandbox/command_approvals.json`，使用原子写和短 TTL；子进程校验后一次性删除。
- 或：对需要审批的工具改为父进程预审批，再把“已审批”的执行请求交给子进程。
- 或：让 `run_command` 高风险审批流程不使用随机内存 token，改用用户显式二次确认 + 命令哈希/时间窗，但要避免伪造与重放风险。
- 不推荐：关闭 `execute_tool_isolated()`，会削弱中断/超时/工具崩溃隔离能力。

## Notes
- `npm install` 被识别为高风险通常是预期行为，因为它会修改依赖或运行环境。
- 如果用户声称已经安装依赖但 `require('pptxgenjs')` 失败，应同时检查实际执行目录、`package.json` dependencies、`node_modules` 是否存在，以及全局 npm root。