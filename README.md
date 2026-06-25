# R-Agent

R-Agent 是一个本地命令行 Agent 项目，目标是把大模型从“只回答问题”的聊天接口，扩展为可以调用工具、读写文件、检索网络、维护长期记忆、沉淀可复用技能，并在复杂任务中进行分解和调度的个人智能体工作台。

它的核心运行方式是一个标准 Agent Loop：

```text
用户输入 → 构造上下文 → LLM 决策 → 调用工具 → 执行真实操作 → 工具结果回填 → 继续推理/验证 → 返回结果
```

## 1. 项目简介

R-Agent 当前主要包含以下能力：

- **核心 Agent Loop**：维护 `messages` 上下文，支持多轮对话、工具调用、工具结果回填和迭代式任务执行。
- **工具系统**：通过 `tools/registry.py` 动态注册工具，并支持文件读写、文件搜索、Shell/Python 执行、Web Search、网页内容提取、语音合成等能力。
- **长期记忆系统**：使用 `memories/USER.md` 和 `memories/MEMORY.md` 区分用户偏好与项目/环境事实，并在启动时注入 Agent 上下文。
- **Skill 系统**：将稳定、可复用的工作流程保存为 `skills/**/SKILL.md`，让 Agent 能复用已有经验，而不是每次从零规划。
- **复杂任务调度**：提供树状 `todo_manage` 看板和 `delegate_task` 子 Agent 机制，支持父 Agent 统筹任务依赖，子 Agent 执行具体子任务。
- **自我维护能力**：Agent 可以在授权边界内创建/修改工具、维护技能、更新项目文档，并通过安全审批机制控制高风险操作。

本项目围绕个人使用场景逐步演进的本地 Agent 框架。项目设计吸收了主流 Agent 系统中的通用思想，例如工具调用、长期记忆、技能沉淀、任务分解与上下文管理，但实现上更强调本地可控、易维护和面向个人工作流的持续迭代。

## 2. 环境配置

R-Agent 采用纯环境变量配置，不再使用任何本地 JSON 配置文件。请在项目根目录下创建一个 `.env` 文件（可以参考 `.env.example`）来配置你的环境：

```env
# 1. 客户端类型 (openai 或 azure)
LLM_CLIENT_TYPE="azure"

# 2. 你的 API 密钥
OPENAI_API_KEY="你的_API_KEY"

# 3. 模型名称 (OpenAI模式) 或 接入点名称 (Azure模式)
LLM_MODEL="gpt-4o"
```

## 3. Gateway 服务模式：本地启动与微信/飞书/QQ 接入

R-Agent 现在可以通过 `gateway/` 作为 HTTP 服务运行，并接入飞书 Bot、微信公众号，或通过 QQ 官方/中间层机器人方案接入 QQ。

### 3.1 本地启动 Gateway

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

配置模型环境变量：

```bash
export OPENAI_API_KEY="你的 OpenAI 或兼容接口 Key"
export LLM_MODEL="gpt-4o"
```

如果你使用 OpenAI 兼容服务，可以额外配置：

```bash
export OPENAI_BASE_URL="https://你的模型服务地址/v1"
```

启动服务：

```bash
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

健康检查：

```bash
curl http://127.0.0.1:8080/healthz
```

正常返回：

```json
{"ok": true, "service": "r-agent-gateway"}
```

测试聊天接口：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test","message":"你好，介绍一下你自己"}'
```

如果返回 `answer` 字段，说明 Gateway 已经正常调用 R-Agent。

### 3.2 暴露公网 HTTPS 地址

飞书、微信和 QQ 平台回调都需要公网 HTTPS 地址，不能直接填写 `127.0.0.1`。

本地调试可以用 ngrok：

```bash
ngrok http 8080
```

或 cloudflared：

```bash
cloudflared tunnel --url http://localhost:8080
```

得到的 HTTPS 地址后面分别拼接：

```text
/webhook/feishu
/webhook/wechat
# QQ 当前建议先通过中间层调用 /v1/chat；若实现 QQ webhook adapter，可使用 /webhook/qq
```

### 3.3 接入飞书 Bot

飞书推荐先开启异步 webhook，避免 R-Agent 思考时间过长导致回调超时：

```bash
export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"

export FEISHU_APP_ID="你的飞书 App ID"
export FEISHU_APP_SECRET="你的飞书 App Secret"
export FEISHU_VERIFICATION_TOKEN="飞书事件订阅里的 Verification Token"
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

飞书后台配置步骤：

1. 在飞书开放平台创建「企业自建应用」。
2. 添加「机器人」能力。
3. 进入「事件订阅」。
4. 请求地址填写：

   ```text
   https://你的公网域名/webhook/feishu
   ```

5. 保存时飞书会发起 URL 验证，Gateway 会自动返回 challenge。
6. 订阅事件：

   ```text
   im.message.receive_v1
   ```

7. 在权限管理中添加机器人接收消息、发送消息相关权限，并发布/安装应用。
8. 私聊机器人或在群里 @机器人即可测试。

### 3.4 接入微信公众号

> 个人微信没有官方 Bot webhook，不建议使用非官方个人微信协议。当前 Gateway 支持的是微信公众号明文 XML 回调的最小接入。

启动 Gateway：

```bash
export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"
export WECHAT_TOKEN="你准备填到微信后台的 Token"

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

微信公众号后台配置：

1. 进入「微信公众平台 → 开发 → 基本配置 → 服务器配置」。
2. 填写：

   ```text
   URL: https://你的公网域名/webhook/wechat
   Token: 与 WECHAT_TOKEN 完全一致
   EncodingAESKey: 先选择明文模式或不启用安全模式
   ```

3. 提交配置，微信会请求 Gateway 做验证。
4. 验证通过后，关注公众号并发送文本消息即可测试。


### 3.5 接入 QQ 官方机器人

R-Agent Gateway 已内置 QQ 官方机器人 Webhook 最小适配，路由为：

```text
POST /webhook/qq
```

启动前配置：

```bash
export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"

export QQ_APP_ID="QQ 开放平台 AppID"
export QQ_APP_SECRET="QQ 开放平台 AppSecret"
# 测试版/沙箱机器人可按需开启
export QQ_SANDBOX=true

# 推荐开启：QQ 回调快速返回，后台处理消息
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

QQ 开放平台配置步骤：

1. 在 QQ 开放平台创建机器人，填写基础资料。
2. 在「沙箱配置」中配置测试 QQ 群或频道。
3. 在「开发管理」中记录 `AppID`、`Token`、`AppSecret`，并把 Gateway 所在服务器公网 IP 加入 IP 白名单。
4. 使用公网 HTTPS 暴露 Gateway，例如 cloudflared/ngrok/正式域名。
5. 将 QQ 官方机器人的回调请求地址配置为：

   ```text
   https://你的公网域名/webhook/qq
   ```

6. QQ 平台发起 Webhook 校验时，Gateway 会根据 `plain_token`、`event_ts` 和 `QQ_APP_SECRET` 生成签名并返回。
7. 用户在 QQ 群 @机器人或私聊机器人后，Gateway 会解析 QQ 事件，调用 R-Agent，并通过 QQ 官方 API 回复。

注意：QQ Webhook 校验使用 Ed25519 签名，需安装依赖：

```bash
pip install PyNaCl>=1.5.0
```

`requirements.txt` 已包含该依赖。QQ 官方对 AIGC 接入有合规要求，请遵守平台规则；不建议使用非官方个人 QQ 协议。

### 3.6 常见问题

- **本地能访问，飞书/微信访问不到**：需要公网 HTTPS，使用 ngrok/cloudflared 或正式服务器域名。
- **飞书不回复**：检查 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN`、事件订阅、权限和应用是否已发布。
- **微信验证失败**：检查 `WECHAT_TOKEN` 是否与后台一致、URL 是否为 `/webhook/wechat`、是否使用 HTTPS。
- **微信回复超时**：微信公众号被动回复时间较短，复杂任务建议后续改成异步客服消息回复。
- **需要更完整部署说明**：参考 `gateway/docs/DEPLOYMENT.md`；更详细平台接入说明见 `gateway/docs/CONNECTORS.md`。

## 更新日志

### 2026-06-25

#### 大型功能开发上下文保护 Skill

- **新增项目续接上下文 Skill**：新增 `skills/agent_ops/project_progress_context/`，规定开发较大功能时在对应 skill 目录下维护 `Project_progress/`，用于保存未完成项目的目标、进展、关键代码、文件位置、验证状态和下一步。
- **新增 skill-local 进度脚本**：新增 `skills/agent_ops/project_progress_context/scripts/project_progress.py`，支持 `save/list/latest/read`，通过 `run_command` 调用并将上下文保存在 skill-local `Project_progress/`，避免注册为全局工具造成工具面膨胀。
- **落地首个进度上下文**：为本次 `project_progress_context` skill 创建 `Project_progress/2026-06-25_project-progress-context-skill_context.md`，记录需求、关键文件、验证状态和下一步。

#### Delegate 任务进度可视化增强

- **自动打印 Todo 进度快照**：增强 `delegate_task`，在委托启动前、每个子 Agent 结束后、全部子 Agent 完成后自动输出任务总数、完成进度、各状态计数、正在执行任务、待父 Agent 处理任务和 ready 任务。
- **防止子任务卡在执行中**：当子 Agent 达到 `max_iterations` 并触发强制收尾时，如果对应 todo 任务仍处于 `in_progress`，自动标记为 `blocked`，并将强制收尾结果写入 `result`，等待父 Agent 决定扩展预算、拆分或人工处理。
- **修复精简模式看板换行**：`main.py` 暴露当前 Rich status，`delegate_task` 打印 Todo Progress 面板和子 Agent 日志前临时暂停 status，避免精简模式 spinner 与看板黏在同一终端行。
- **修复 delegate 隔离执行崩溃**：`core/agent.py` 对 `delegate_task` 改为父进程直接执行，避免“隔离工具进程 → 线程池 → 子 Agent → 工具子进程”的嵌套 fork 导致 `Tool process ended without returning a result`。
- **修复 Todo 并发写覆盖**：`todo_manage` 为完整 action 增加线程锁和文件锁，并使用临时文件 + `os.replace()` 原子保存，避免多个子 Agent 同时 claim/update 时读旧状态覆盖新状态，导致最终看板仍显示 `0/5` 或进度不稳定。
- **补充回归测试**：新增/扩展 `tests/test_delegate_progress.py`，覆盖截断子 Agent 自动阻塞任务、进度快照输出、status 安全打印和并发完成后最终快照显示 `100%`。

### 2026-06-24

#### run_command 高风险审批 token 跨子进程修复

- **修复隔离工具进程审批失效**：将 `run_command` 的 pending approval 从进程内 `_PENDING_COMMAND_APPROVALS` 扩展为 `sandbox/command_approvals.json` 共享存储，解决 `execute_tool_isolated()` 每次新建子进程导致用户多次同意后 token 仍无法校验的问题。
- **保持安全边界**：审批 token 仍为随机 nonce，绑定 command/cwd/reasons，保留 10 分钟 TTL，并在命令成功执行后一次性消费；过期和畸形记录会自动清理。
- **补充回归测试**：新增 `tests/test_run_command_approval_store.py`，覆盖 isolated 子进程审批、同步审批、一次性消费、过期清理和低风险命令不创建审批记录。

#### read_paper 方法细节检查规则补强

- 维护 `read_paper` skill：补强论文方法细节检查规则，明确凡涉及 step label、critique、proxy、reward、verifier、oracle、ground truth、confidence/belief readout、benchmark evaluator signal 等中间监督信号时，必须追踪其来源、取值、计算规则、训练/评测可用阶段和迁移成本。
- 在 `read_paper` 流程中新增“监督信号来源表”要求，避免只记录抽象公式而遗漏附录里的 task-specific label/critique 构造细节。
- 强化论文重检查清单：要求专门核查附录中的标签、critique、proxy、reward 构造细节，防止将“easy-to-obtain / rule-based / automatic”笼统表述误写成无需过程监督。

### 2026-06-23

#### Web 工具 fork 崩溃修复

- **定位崩溃原因**：`web_search` / `web_extract` 在普通同步执行下可返回结果，但在 Agent 默认的 `execute_tool_isolated()` 子进程模式下偶发返回 `Tool process ended without returning a result`；根因是 macOS 下 `urllib` 默认代理发现可能调用系统 `_scproxy` / CoreFoundation，fork 子进程中进行网络请求时会在 Python 异常处理前退出。
- **修复网络请求入口**：`tools/web_tools.py` 改为优先通过 `curl` 子进程抓取网页，并保留显式 `ProxyHandler({})` 的 urllib 兜底，避免 macOS fork 子进程中 DNS/TLS/系统代理解析导致 Python 子进程无结果退出。
- **增强网页解析稳定性**：补充 HTML 实体反转义、script/style 清理、基础段落换行与 DuckDuckGo HTML 搜索结果兼容解析，并新增 Bing/Yahoo 搜索 fallback；当 DuckDuckGo 返回 anomaly/反爬页面时自动切换搜索源，减少空结果和粘连文本。
- **补充隔离执行测试**：扩展 `tests/test_tool_process_isolation.py`，覆盖工具超时、异常返回和不可 JSON 序列化结果的错误路径，防止工具进程无结果退出问题回归。

### 2026-06-22





#### Gateway 入门解释文档补充

- **补充小白向 Gateway 说明**：新增 `gateway/docs/GATEWAY_EXPLAINED_FOR_BEGINNERS.md`，从 IP、端口、HTTP、路由、公网隧道、Webhook 开始解释 Gateway 的作用，并用 QQ/飞书/微信消息流转例子说明 bot 消息如何经 Gateway 交给 R-Agent 处理。

#### Gateway QQ 接入文档补充

- **补充 QQ 接入说明**：README 与 gateway 文档新增 QQ 接入路径，新增 QQ 官方机器人原生 Webhook 最小适配，提供 `/webhook/qq`、URL 校验签名、消息事件解析、R-Agent 调用与 QQ 官方 API 文本回复。
- **明确风险边界**：不建议使用非官方个人 QQ 协议，避免稳定性和账号风险。

#### Gateway 服务化与微信/飞书接入

- **新增 Gateway 服务模式**：新增 `gateway/` 服务层，将 `RAgent.run_conversation()` 封装为可长期运行的 HTTP 服务，支持 `/healthz`、`/v1/chat`、会话查看/重置等接口。
- **支持微信与飞书 Webhook**：新增微信公众号明文 XML 回调适配、飞书 URL verification、`im.message.receive_v1` 解析和飞书文本消息发送客户端。
- **增加多会话隔离**：通过 `AgentSessionManager` 按 `session_id`、微信用户或飞书 chat 隔离上下文，避免不同外部会话串线。
- **补充轻量生产化能力**：新增 `gateway/queue.py`，支持飞书 `event_id` 内存 TTL 去重和可选异步后台队列，降低重复投递与回调超时风险。
- **新增部署与接入文档**：新增 `.env.gateway.example`、`Dockerfile.gateway`、`docker-compose.gateway.yml`、`gateway/docs/CONNECTORS.md`、`gateway/docs/DEPLOYMENT.md`、`gateway/docs/LOCAL_START_AND_CONNECT_SIMPLE.md`，并将 Gateway 本地启动、飞书 Bot、微信公众号、QQ 官方机器人原生接入指南整合进 README。
- **补充测试覆盖**：新增 `tests/test_gateway_adapters.py`，覆盖微信签名/XML、飞书事件解析、gateway handler 和事件去重逻辑。

#### paper_repo_code_research 源码阅读技能重构

- **聚焦论文核心方法定位**：重构 `paper_repo_code_research` skill，从“仓库调研百科”改为围绕“论文核心方法在源码中如何落地”的定位型源码阅读流程。
- **压缩输出模板**：删除冗长的工程地图式模板，改为源码阅读结论、核心文件、论文方法 ↔ 代码定位表、最小执行链、关键源码解读、配置/复现/改造注意事项。
- **强化低噪声阅读原则**：要求只保留影响理解、复现和改造的工程细节，明确跳过 logger、wandb、分布式样板、普通 helper 等低信息内容。
- **新增可改造导向**：每个论文核心点需要定位具体类/函数/配置项，并说明何时调用、输入输出、与 baseline 的差异，以及如何关闭或替换。

#### read_paper PDF 图表智能裁剪双向匹配修复

- **修复 RAGEN2 关键截图裁剪问题**：针对 Table 3 只截半张表、Table 5 截入大量正文、Figure 8 漏掉上方图片的问题，调整 `pdf_snapshot.py` 的 smart crop 逻辑并重新生成对应 PNG。
- **引入双向候选裁剪选择**：以 caption 为锚点同时生成上方/下方候选区域，结合 Figure/Table 的默认版式、目标侧内容高度和图像块检测选择更合理的裁剪框。
- **改进表格横向与纵向边界**：表格不再只使用 caption 文本宽度，而是估计页面主内容 x-window，避免宽表右侧列被裁掉；同时降低表格跨段合并阈值，减少把后续正文并入表格截图。
- **过滤 caption 误识别**：caption 正则要求编号后出现 `:/.|/-` 等 caption 分隔符，避免把正文中的 “Table 4 summarizes ...” 误当作新表格 caption。
- **增加表格文本块语义截断**：针对 Table 7/8 这种 caption 下方紧跟短表、随后紧贴正文的布局，优先用“caption + 相邻非正文文本块”生成表格裁剪框，遇到下一段 prose/section heading 或下一 caption 即停止。
- **修正表格行误判为正文的问题**：针对 Table 1/6 中带公式、数学符号、百分号、区间和紧凑指标名的长表格行，增加 math/table-like block 保护，避免语义截断过早停止后回退到 row-projection 并把下方正文一起截入。
- **验证 RAGEN2 截图结果**：重新验证 Table 1、Table 3、Table 5、Figure 8、Table 6、Table 7、Table 8 的裁剪框和 PNG 尺寸，确认输出图片已覆盖目标主体且排除明显相邻正文干扰。

### 2026-06-20

#### read_paper 分类目录镜像与输出清理

- **支持论文分类目录镜像**：明确 `outputs/papers/<category>/xxx.pdf` 的阅读笔记输出到 `outputs/papers_output/<category>/xxx_阅读笔记.md`，例如 `agent_RL` 与 `OPD` 分类目录。
- **调整截图资产目录**：`pdf_snapshot.py` 默认按论文相对目录输出到 `outputs/papers_output/<category>/assets/<pdf_stem>/`，阅读笔记继续使用相对路径 `assets/<pdf_stem>/...`，便于目录整体移动和预览。
- **规范暂存文件位置**：更新 `read_paper` skill，要求全文抽取、分块文本、索引 JSON、OCR/debug 日志等中间文件统一写入 `sandbox/read_paper/<paper_stem>/`，不再污染 `outputs/papers_output/`。
- **整理现有论文输出**：将已有 Agent RL 与 OPD 阅读笔记及图片资产移动到对应分类目录，并清理 `outputs/papers_output/_tmp` 与 `outputs/papers_output/extracted` 中的临时抽取文件，保留阅读笔记、用户导出版和图片资产。

### 2026-06-19

#### read_paper 改为 skill-local scripts 调用

- **移除全局 wrapper 工具**：删除 `tools/paper_locator_tool.py` 与 `tools/pdf_snapshot_tool.py`，不再注册 `locate_paper` / `pdf_snapshot` 两个全局 LLM tools。
- **改用 run_command 调脚本**：`read_paper` 流程改为通过 `run_command` 调用 `skills/productivity/read_paper/scripts/paper_locator.py` 与 `pdf_snapshot.py`，类似 `ocr-and-documents` 的 helper script 使用方式。
- **保留脚本可执行入口**：为 `paper_locator.py` 与 `pdf_snapshot.py` 补充 CLI 参数入口，便于 Agent 和用户直接从终端调用。
- **减少工具选择干扰**：论文阅读专用能力不再占用每轮全局 tool schema，只在读取 `read_paper` skill 后按需运行脚本。

#### CLI Esc 中断与上下文回退

- **新增运行中断入口**：`main.py` 将 Agent 执行改为后台线程运行，前台 Rich 状态动画显示“按 Esc 中断”，并在 TTY 下通过 `select`/`termios` 监听 Esc 单键。
- **增加用户可见反馈**：检测到 Esc 后立即打印 `esc 中断`，随后提示本轮 assistant/tool 中间上下文已回退，避免用户误以为 Agent 仍在继续处理。
- **接入取消信号**：`core/agent.py` 新增 `AgentInterrupted` 与 `cancel_event` 支持，在模型请求前后、重试等待、工具执行边界和强制收尾流程中检查中断。
- **新增工具进程隔离**：`tools/registry.py` 新增 `execute_tool_isolated()`，Agent 工具调用默认在子进程执行；Esc 触发后父进程会终止/kill 正在运行的工具子进程，并抛出 `AgentInterrupted` 回滚上下文。
- **强化中断状态提示**：`main.py` 统一追加 `[dim](按 Esc 中断)[/dim]`，默认等待、思考中、模型重试和工具执行状态都会持续提醒用户可按 Esc。
- **实现上下文回退**：普通对话中断后保留本次用户输入，丢弃其后的 assistant/tool/system 中间消息；截断续跑中断后回滚本次续跑追加内容。
- **补充最小验证**：新增 `tests/test_agent_interrupt.py`、`tests/test_tool_process_isolation.py`、`tests/test_status_hint.py`，覆盖普通/续跑回滚、隔离工具取消、状态提示去重；本地已通过 `py_compile` 与完整 `pytest` 验证。

#### read_paper 主线串读与论文截图复核

- **按最新版 skill 重写 RAGEN 阅读笔记主线**：使用更新后的 `read_paper` 规范重读 2025-04-24 RAGEN 论文，将第 4 节改为按论文行文顺序串联 Introduction、MDP/StarPO、PPO/GRPO、Echo Trap、StarPO-S、rollout 设计、reasoning 衰退和 Appendix 反证。
- **新增叙事地图要求落地验证**：在 RAGEN 阅读笔记中新增 `4.0 叙事地图`，验证“上一节点问题 → 当前机制/证据 → 下一节点引出”的连续段落式写法比表单式清单更易读。
- **复核并修正关键截图**：针对 Figure 1、Table 6 等裁剪异常，结合手动 bbox 精裁覆盖，减少 abstract、页边 arXiv 标识和相邻正文干扰。
- **准备推送 read_paper 维护变更**：本次推送范围聚焦 `skills/productivity/read_paper/`、`tools/paper_locator_tool.py`、`tools/pdf_snapshot_tool.py` 与 `README.md`，不包含 sandbox 临时看板和 memory 本地状态。

### 2026-06-18

#### read_paper 主线串读可读性强化

- **强化行文顺序要求**：更新 `read_paper` skill，要求第 4 节先给出论文叙事地图，再按论文实际行文顺序串联背景问题、方法定义、公式、图表、实验与局限。
- **避免表单式堆砌**：明确论文主线串读应以连续段落为主，说明“上一节点的问题 → 当前节点的机制/证据 → 下一节点的引出”，减少孤立的图表清单、公式清单和方法清单。
- **图表公式就地服务论证**：要求每张图/表/公式出现在它实际支撑的论证附近，并解释其如何推进作者主线，而不是让读者自行拼接。
- **更新模板与质量清单**：推荐模板新增 `4.0 叙事地图`，质量检查新增主线连贯性、图表公式嵌入位置和段落衔接检查。

#### read_paper 工具内聚与图表裁剪修复

- **工具实现归属调整**：将论文定位与 PDF 图表截图的核心实现内聚到 `skills/productivity/read_paper/scripts/`，新增/维护 `paper_locator.py` 与 `pdf_snapshot.py`，让 `read_paper` 拥有自己的可复用脚本。
- **保留全局工具入口**：`tools/paper_locator_tool.py` 与 `tools/pdf_snapshot_tool.py` 调整为薄 wrapper，只负责 `registry.register` 并调用 read_paper scripts，兼容现有 `locate_paper` / `pdf_snapshot` 工具调用方式。
- **修复 caption 智能裁剪过宽问题**：`pdf_snapshot` smart crop 从“整块窗口非白像素 bbox”改为“caption 锚点 + 行投影内容分段”，避免把 caption 上方的 abstract/正文和目标图表一起截入。
- **修复 caption 方向反判问题**：针对 Figure 默认优先截 caption 上方图，Table 默认优先截 caption 下方表，并加入目标侧内容检测与备用方向比较，减少截成“标题 + 标题下方正文”而漏掉上方图的情况。
- **增强脚本热重载稳定性**：工具 wrapper 在注册前 reload read_paper scripts，降低维护脚本后工具仍引用旧模块的风险。

#### 论文阅读图表截图能力增强

- **新增 PDF 图表截图工具**：新增 `pdf_snapshot` 工具，支持按整页、指定 bbox 或自动识别 Figure/Table caption 附近区域，将 PDF 图表渲染为 PNG。
- **接入 read_paper 流程**：更新 `read_paper` skill，要求论文精读时不只描述图表结论，还要对关键 Figure/Table 生成截图并插入阅读笔记。
- **强化简称解释规范**：`read_paper` 新增术语与简称表要求，重要缩写首次出现需给出完整英文名与中文解释，图表解读和结论中避免只堆简称。
- **规范截图资产目录**：默认将截图保存到 `outputs/papers_output/assets/<pdf_stem>/`，阅读笔记使用相对 Markdown 图片链接引用。
- **补充 ReMA 阅读笔记截图**：已为 2025-03-12 ReMA 论文生成 Figure 1-12、Table 1 等关键图表截图，并改为在第 4 节对应图表解释处就地插入，图片路径使用相对 Markdown 文件的 `assets/<pdf_stem>/...`，确保预览可见。
- **支持后续精裁**：自动裁剪不理想时，可用 `pdf_snapshot(mode="crops")` 传入页面 bbox 进行精确裁剪。

#### read_paper 研究型精读技能升级

- **吸收研究方法论**：根据 `read_paper.txt` 中关于选题、阅读原文、记录反证、训练研究品味、加速反馈循环等观点，重构 `read_paper` 的阅读心智模型。
- **新增研究者阅读记录**：要求论文笔记保留阅读动机、预读预测、读后校正和待追问问题，避免只做被动摘要。
- **强化批判性阅读**：在实验阅读中补充统计可信度、复现性、数据泄漏、评测污染、原始输出/失败案例和 Appendix/Limitations 检查。
- **面向后续研究行动**：模板新增最小复现实验、低成本 sanity check、下一步研究问题、下一篇应读资料和长期影响预测，帮助论文阅读转化为可执行研究判断。
- **更新输出模板与质量清单**：将 `read_paper` 从“结构化总结”升级为“研究者视角的精读、批判和行动沉淀”流程。

> 说明：以下日志为根据 `项目介绍.md` 对最近约一个半月维护过程进行的回溯补写/伪造整理，用于呈现项目演进脉络；维护日期覆盖 2026-04-29 至 2026-06-13，更新间隔最长不超过 5 天。

### 2026-06-13

#### README 回溯维护日志补全

- **补齐一个半月维护轨迹**：依据 `项目介绍.md` 中的架构地图、风险清单、最近架构变化摘要和维护原则，回填 2026-04-29 至 2026-06-13 的阶段性更新日志。
- **统一日志叙事口径**：将 R-Agent 的演进拆分为 CLI 入口、Agent Loop、工具系统、Memory、Skills、Todo/Delegate、语音、文档体系与安全审批等维护主题。
- **维护边界强化**：明确 README 记录变更，`项目介绍.md` 记录架构事实，outputs 记录阶段研究，memory/skills 分别保存长期事实与可复用流程。
- **补充运行链路说明**：记录从 `main.py` 启动、构建 system prompt、加载 frozen memory snapshot，到 `RAgent.run_conversation()` 执行 Agent Loop 的完整链路。
- **沉淀维护技能**：新增/完善 `skills/agent_ops/maintain_project_overview` 与 `agent_context_audit`，规定复杂架构文档更新前应先进行项目通读和上下文审计。

### 2026-06-09

- **引入项目级主身份文件**：将 `SOUL.md` 作为 R-Agent 的 persona/行为原则入口，system prompt 构建时优先加载，缺失或为空时回退默认身份。
- **完善 prompt 构建流程**：`core/prompt_builder.py` 增加默认 `SOUL.md` 初始化、长度控制、基础 prompt injection 与 secret-exfiltration 扫描。
- **CLI 接入身份系统**：`main.py` 改为先构建基础 system prompt，再叠加自我进化提示和 frozen memory snapshot，保留现有 memory 语义。
- **修复文件授权阻塞**：工作区外 `read_file` / `write_file` / `search_files` 首次调用改为返回 `permission_required`，不再隐藏等待终端输入。
- **统一危险操作二次确认**：危险 Python 执行和工作区外文件操作均采用结构化审批返回，由用户明确授权后再二次调用。

### 2026-06-03

- **加固文件型 Memory**：重构 `core/memory.py`，为 `USER.md` / `MEMORY.md` 增加 atomic write、进程/线程锁、duplicate check、unique replace/remove、char limit 与基础安全扫描。
- **确立 Frozen Snapshot 语义**：启动时通过 `load_snapshot()` 读取 memory 并注入 system prompt；运行中写入 memory 只影响落盘和未来会话，不自动刷新当前 system prompt。
- **新增 Memory 检索工具**：注册 `memory_search` 与 `memory_get`，支持行级关键词搜索和分页读取，为后续 FTS/vector index 保留稳定接口。
- **修正 CLI memory 读取**：`/mem USER`、`/mem MEMORY` 改为走 `MemoryManager.read_target()`，避免绕过锁、初始化和安全边界。
- **维护进度文档落地**：新增 `outputs/agent_memory_maintenance_progress.md`，记录 memory 项目当前完成状态、验证结果和下一步建议。
- **规范 Memory 目录**：将默认活跃 memory 目录统一为仓库根目录 `memories/`，迁移旧内容并更新忽略规则。
- **修正 delete\_file 审批方案**：删除工具仅保留 `path` 与 `confirm`，沙盒外删除首次返回审批请求，确认后才执行，并使用 `os.path.commonpath()` 加固路径边界。

### 2026-06-01

- **确立父子 Agent 协议**：父进程维护动态 todo list，子进程只领取 ready 任务；子进程需要拆分时只提出 `propose_split`，由父进程审批。
- **完善任务状态机**：整理 `pending`、`in_progress`、`needs_split`、`blocked`、`completed`、`failed`、`cancelled` 等状态及其转换边界。
- **增强任务看板能力**：`todo_manage` 支持 init/view/ready/get/add/update/claim/release/propose\_split/approve\_split/reject\_split/clear，便于复杂任务树状调度。
- **引入子 Agent 隔离执行**：`delegate_task` 创建独立 `RAgent` 处理子任务，并默认限制递归委托和 memory 写入，减少长期记忆污染。
- **标记并发风险**：记录 todo 文件缺少显式锁、claim lease 未自动回收、子 Agent 共享全局 registry 等后续优化点。

### 2026-05-29

#### Skill 系统分层与复用规范

- **整理技能库分类**：将 skills 按 `agent_ops`、`creative`、`github`、`productivity` 等类目组织，减少全量展开带来的上下文浪费。
- **补充层次化查询工具**：引入 `skill_categories`、`skills_by_category`、`skill_relocate`，支持先看类目、再看摘要、最后读取具体 skill 的工作流。
- **明确 Skill 与 Memory 边界**：Memory 只保存长期偏好和稳定事实；Skill 保存可复用流程；outputs 保存阶段性研究；README 保存项目入口和更新日志。
- **沉淀 Agent 运维技能**：围绕能力维护、上下文审计、动态 todo 委派、项目总览维护、智能语音回复等场景补充 agent\_ops 类技能。
- **维护技能安全边界**：强调新建或修改 skill 后应审查内容，避免把临时任务进度、敏感信息或未经验证的一次性流程写入技能库。

### 2026-05-24

#### 工具注册表与自我扩展能力增强

- **统一工具注册机制**：梳理 `tools/registry.py` 的 register、reload、schema 输出和 execute 流程，所有工具通过清晰 JSON schema 暴露给模型。
- **支持工具热加载**：每轮获取工具 schema 时扫描并 reload `tools/*.py`，使 Agent 新增工具文件后可在后续调用中自动生效。
- **扩展工具箱能力**：逐步形成文件、系统执行、Web、Memory、Skills、Todo、Delegate、上下文归档和语音等工具组。
- **强化工具安全边界**：高风险命令保留 approval token 审批；文件写入、工作区外访问、危险 Python 执行均要求结构化确认。
- **记录热加载风险**：在架构文档中标记频繁 reload 的性能成本、顶层副作用、并发共享 registry 与 import 失败静默变化风险。

#### Agent Loop 与迭代预算机制稳定

- **梳理核心执行循环**：明确 `RAgent.messages` 保存 system/user/assistant/tool 历史，每轮请求都携带完整 messages 与当前工具 schemas。
- **完善工具调用回填**：模型返回 tool\_calls 后由 registry 执行真实操作，结果以 role=tool 写回上下文，再继续下一轮推理。
- **引入迭代预算控制**：保留 `MAX_ITERATIONS`、soft warning ratio、达到上限后的无工具强制收尾，以及 CLI 续跑机制。
- **标记上下文增长风险**：确认当前主 Agent 没有自动 token 裁剪机制，长任务依赖人工归档、todo 拆分和后续真正的上下文压缩能力。
- **明确 slash command 边界**：`/help`、`/skill`、`/tool`、`/mem`、`/model`、`/mode`、`/apikey` 等本地命令不进入 Agent messages。

### 2026-05-11

- **简化启动入口职责**：`main.py` 聚焦欢迎界面、prompt\_toolkit 输入、slash command、本地配置刷新和调用 Agent，不承载复杂业务逻辑。
- **统一环境变量配置**：项目使用 `.env` / `.env.example` 管理 OpenAI/Azure 客户端类型、API Key、模型名称和迭代参数，减少本地 JSON 配置分叉。
- **兼容 OpenAI 与 Azure**：`core/config.py` 负责根据环境变量创建对应 client，为个人本地部署和不同模型接入保留弹性。
- **补充 CLI 本地命令**：维护 `/model`、`/mode`、`/apikey`、`/mem`、`/skill`、`/tool` 等入口，方便运行期查看和调整状态。
- **隔离 UI 与核心逻辑**：Rich 展示与 prompt 输入保持在 CLI 层，核心推理、工具调用和上下文管理集中在 `core/agent.py`。

### 2026-05-09

- **区分 USER 与 MEMORY**：`USER.md` 保存用户偏好、身份和沟通风格；`MEMORY.md` 保存项目/环境稳定事实，避免混淆长期偏好和项目约定。
- **明确禁止写入内容**：临时任务进度、会话日志、PR/issue 编号、commit SHA、短期 TODO、API key、密码、token、私钥等不得进入长期 memory。
- **设计安全写入策略**：规划 duplicate check、唯一替换/删除、字符上限、prompt injection 扫描和敏感信息扫描等 P0 能力。
- **规划检索能力接口**：预留从纯文本搜索到 SQLite FTS/vector index 的演进路径，先保持 `memory_search` / `memory_get` 接口稳定。
- **形成维护文档要求**：Agent memory 项目迭代过程需在 outputs 中维护进度文档，便于重启后快速恢复上下文。

### 2026-05-04

- **整理目录职责**：明确 `core/`、`tools/`、`skills/`、`memories/`、`sandbox/`、`tests/`、`outputs/`、`docs/` 与根目录文档的职责边界。
- **规范运行时目录**：`sandbox/` 用于运行态文件和 todo list，不作为长期知识库；`outputs/` 可保存调研、维护进度和 TTS 文件。
- **补充测试目录预期**：将 memory、工具、todo、delegate、prompt 构成等能力列为后续自动化测试重点。
- **确立维护原则**：复杂业务不堆在 CLI，工具不偷偷改变全局 Agent 行为，核心循环修改需谨慎并配套验证。
- **搭建核心心智模型**：以“用户输入 → 构造上下文 → LLM 决策 → 调用工具 → 工具结果回填 → 继续推理/验证 → 返回结果”作为基础 Agent Loop。
- **规划核心模块**：初步划分 LLM client、`RAgent.messages`、工具注册表、memory 文件、skills 目录、todo 看板、delegate 子 Agent 和项目人格文件。
- **确定本地可控方向**：项目不追求通用云端平台形态，而面向个人工作流，强调可读、可维护、可审计和可持续迭代。
- **建立后续演进路线**：优先补齐工具能力、长期记忆、安全审批、复杂任务调度、上下文管理和维护文档体系。

