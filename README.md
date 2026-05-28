# R-Agent 架构与构建计划

## 1. Hermes-agent 架构分析

通过分析 `hermes-agent` 的项目结构和开发文档，我们可以看出一个成熟的 AI Agent 包含以下几个核心模块：

1. **核心对话循环 (Core Agent Loop - `run_agent.py`)**：
   这是 Agent 的“大脑”。它负责维护一个完整的对话循环（`while` 循环），将用户的输入发送给 LLM（如 OpenAI、Anthropic），检查 LLM 的返回是否包含工具调用（Tool Calls）。如果有，则暂停对话，执行工具，将结果拼接到上下文中并再次请求 LLM，直到 LLM 给出最终的文本回复。

2. **工具注册与调度系统 (Tool Orchestration - `model_tools.py`, `tools/registry.py`)**：
   Agent 需要“手和脚”来操作环境。该系统负责将普通的 Python 函数转换成 LLM 能理解的 JSON Schema，并在 LLM 要求调用时，通过路由找到对应的函数并执行。

3. **用户交互入口 (CLI / Gateway - `cli.py`, `gateway/`)**：
   Agent 的“嘴巴和耳朵”。负责接收用户的指令，并以友好的方式（如终端流式输出、进度条）将 Agent 的思考和操作过程展示给用户。

4. **状态与记忆管理 (State & Memory - `hermes_state.py`)**：
   用于持久化存储对话历史、工具执行结果和系统配置，保证多轮对话的连贯性。

## 2. 构建 R-Agent：第一步需要完成什么？

要从零构建属于我们自己的 `R-Agent`，**最先需要完成的是“核心对话循环”和“工具调度系统”**。
没有交互界面，我们可以用简单的 `input()` 替代；没有持久化记忆，我们可以先在内存中维护一个 `messages` 列表。但如果没有对话循环和工具调用机制，它就只是一个普通的 LLM 包装器，而不是 Agent。

### R-Agent 逐步演进计划 (从简单到复杂)

- **Phase 1: 基础骨架 (MVP)** ✅
  - 实现最简工具注册表（将 Python 函数包装为 Tool Schema）。
  - 实现核心的 `AIAgent` 类和 `while` 循环逻辑。
  - 实现一个极其简单的命令行交互脚本。
- **Phase 2: 基础工具接入**
  - 添加文件读取、文件写入工具。
  - 添加简单的终端命令执行工具。
- **Phase 3: 状态与记忆**
  - 实现多轮对话的历史记录管理。
- **Phase 4: 完善体验** ✅
  - 添加流式输出、终端 UI 美化（对标 hermes-agent 的 CLI，已通过 `rich` 库实现）。

---

我们已经完成了基础骨架和终端 UI 美化（Phase 1 & Phase 4）。
请通过 `pip install -r requirements.txt` 安装依赖，然后运行 `python main.py` 体验美观的 CLI 交互！
# R-Agent
# R-Agent
