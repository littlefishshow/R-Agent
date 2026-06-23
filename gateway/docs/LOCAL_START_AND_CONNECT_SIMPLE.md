# R-Agent Gateway 本地启动与接入微信/飞书/QQ（简明版）

本文只写最短可操作流程。假设你已经在本机有这个 sandbox 项目：

```bash
cd sandbox/r_agent_gateway_work_20260622_173345
```

---

## 1. 本地启动 R-Agent Gateway

### 1.1 创建虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 1.2 配置模型环境变量

至少需要配置模型 Key：

```bash
export OPENAI_API_KEY="你的 OpenAI 或兼容接口 Key"
export LLM_MODEL="gpt-4o"
```

如果你使用自定义 OpenAI 兼容服务，也可以加：

```bash
export OPENAI_BASE_URL="https://你的模型服务地址/v1"
```

### 1.3 启动服务

```bash
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

看到类似输出即可：

```text
R-Agent gateway listening on http://0.0.0.0:8080
```

### 1.4 测试服务是否启动成功

新开一个终端：

```bash
curl http://127.0.0.1:8080/healthz
```

如果返回下面内容，说明服务正常：

```json
{"ok": true, "service": "r-agent-gateway"}
```

### 1.5 测试聊天接口

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test","message":"你好，介绍一下你自己"}'
```

如果能返回 `answer` 字段，说明 R-Agent 已经可以作为本地 HTTP 服务运行。

---

## 2. 让外网访问你的本地服务

微信、飞书和 QQ 平台回调都需要公网 HTTPS 地址，不能直接填 `127.0.0.1`。

本地调试最简单方式是用 ngrok 或 cloudflared。

### 方式 A：ngrok

```bash
ngrok http 8080
```

它会给你一个类似下面的 HTTPS 地址：

```text
https://xxxx.ngrok-free.app
```

### 方式 B：cloudflared

```bash
cloudflared tunnel --url http://localhost:8080
```

它会给你一个类似下面的 HTTPS 地址：

```text
https://xxxx.trycloudflare.com
```

后文假设你的公网地址是：

```text
https://你的公网域名
```

---

## 3. 接入飞书 Bot

飞书接入相对最简单，推荐优先测试飞书。

### 3.1 在飞书开放平台创建应用

1. 打开飞书开放平台。
2. 创建一个「企业自建应用」。
3. 添加「机器人」能力。
4. 进入「事件订阅」。

### 3.2 配置 R-Agent Gateway 环境变量

停止 gateway 后，重新用下面方式启动：

```bash
export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"

export FEISHU_APP_ID="你的飞书 App ID"
export FEISHU_APP_SECRET="你的飞书 App Secret"
export FEISHU_VERIFICATION_TOKEN="飞书事件订阅里的 Verification Token"

# 推荐开启：让飞书回调快速返回，后台再处理消息
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

### 3.3 在飞书后台填写回调地址

在「事件订阅」里填写请求地址：

```text
https://你的公网域名/webhook/feishu
```

保存时，飞书会发送一次 URL 验证请求。gateway 会自动返回 challenge。

### 3.4 订阅消息事件

在飞书事件订阅里添加事件：

```text
im.message.receive_v1
```

### 3.5 配置权限并发布应用

在权限管理里添加机器人收发消息相关权限。常见权限包括：

```text
接收消息
发送消息
```

不同飞书后台展示名称可能不同，以后台实际提示为准。

然后发布/安装应用到你的企业或测试群。

### 3.6 测试飞书 Bot

1. 把机器人拉进群，或直接私聊机器人。
2. 给机器人发消息，或在群里 @机器人。
3. gateway 收到事件后会调用 R-Agent，并通过飞书回复。

如果没有回复，优先检查：

- gateway 终端是否有报错；
- `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 是否正确；
- 事件订阅 URL 是否是 HTTPS；
- 是否订阅了 `im.message.receive_v1`；
- 应用是否已经发布/安装；
- 机器人是否有发送消息权限。

---

## 4. 接入微信

注意：个人微信没有官方 Bot webhook。推荐使用：

- 微信公众号；或
- 企业微信应用。

当前 sandbox 版本实现的是「微信公众号明文 XML 回调」的最小接入。

### 4.1 配置 R-Agent Gateway 环境变量

停止 gateway 后，重新启动：

```bash
export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"

export WECHAT_TOKEN="你准备填到微信后台的 Token"

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

`WECHAT_TOKEN` 可以自己设置一串字符串，例如：

```bash
export WECHAT_TOKEN="ragent-wechat-token-123"
```

### 4.2 在微信公众号后台配置服务器

进入：

```text
微信公众平台 → 开发 → 基本配置 → 服务器配置
```

填写：

```text
URL: https://你的公网域名/webhook/wechat
Token: 与 WECHAT_TOKEN 完全一致
EncodingAESKey: 先选择明文模式或不启用安全模式
```

提交时，微信会请求 gateway 做验证。验证通过后即可启用服务器配置。

### 4.3 测试微信公众号

1. 关注你的公众号。
2. 给公众号发送文本消息。
3. gateway 会收到微信 XML 消息，调用 R-Agent，然后返回文本回复。

如果验证失败或无回复，优先检查：

- `WECHAT_TOKEN` 是否和微信后台 Token 完全一致；
- URL 是否是 HTTPS；
- URL 是否填写 `/webhook/wechat`；
- 是否选择了明文模式；
- gateway 终端是否有报错。


---

## 5. 接入 QQ 官方机器人

当前 Gateway 已支持 QQ 官方机器人 Webhook，回调地址是：

```text
https://你的公网域名/webhook/qq
```

### 5.1 启动 Gateway

```bash
export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"

export QQ_APP_ID="QQ 开放平台 AppID"
export QQ_APP_SECRET="QQ 开放平台 AppSecret"
export QQ_SANDBOX=true
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

> QQ Webhook 校验需要 Ed25519 签名，依赖 `PyNaCl`；`requirements.txt` 已包含。若本地未安装，执行 `pip install -r requirements.txt`。

### 5.2 QQ 开放平台配置

1. 创建 QQ 官方机器人。
2. 在「沙箱配置」中配置测试 QQ 群或频道。
3. 在「开发管理」中记录 `AppID`、`Token`、`AppSecret`。
4. 把 Gateway 所在服务器公网 IP 加入 IP 白名单。
5. 用 cloudflared/ngrok/正式服务器暴露 HTTPS。
6. 回调请求地址填写：

   ```text
   https://你的公网域名/webhook/qq
   ```

7. 保存时 QQ 平台会发起校验，Gateway 会自动返回 `plain_token` 和 `signature`。
8. 在 QQ 群 @机器人或私聊机器人测试。

### 5.3 工作流程

```text
QQ 官方平台 → /webhook/qq → R-Agent Gateway → QQ 官方发消息 API → QQ
```

注意：QQ 官方对 AIGC 接入有合规要求，请遵守平台规则；不建议使用非官方个人 QQ 协议。

---

## 6. 常见问题

### 5.1 为什么本地能访问，飞书/微信/QQ 访问不到？

因为飞书/微信/QQ 平台在公网，它们不能访问你的 `127.0.0.1`。必须使用 ngrok、cloudflared 或正式服务器 HTTPS 域名。

### 5.2 飞书为什么建议开异步？

R-Agent 有时会调用工具，耗时较长。开启：

```bash
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true
```

gateway 会先快速回复飞书「已接收」，再后台处理，降低超时概率。

### 5.3 微信为什么可能超时？

微信公众号被动回复通常要求很快返回。如果 R-Agent 思考太久，微信可能认为超时。当前版本适合简单测试；生产建议改成异步客服消息回复。

### 5.4 能直接接个人微信吗？

不建议。个人微信没有官方 webhook，非官方协议有封号和稳定性风险。

---

## 7. 最简单推荐路径

如果你只是想先跑通：

1. 本地启动 gateway。
2. 用 ngrok 暴露 `8080`。
3. 先接飞书 Bot。
4. 飞书跑通后，再接微信公众号。
5. 如果要接 QQ，配置 QQ 官方机器人回调到 `/webhook/qq`。

飞书推荐启动命令：

```bash
cd sandbox/r_agent_gateway_work_20260622_173345
source .venv/bin/activate

export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"
export FEISHU_APP_ID="你的飞书 App ID"
export FEISHU_APP_SECRET="你的飞书 App Secret"
export FEISHU_VERIFICATION_TOKEN="飞书 Verification Token"
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

飞书回调地址填：

```text
https://你的公网域名/webhook/feishu
```

微信公众号推荐启动命令：

```bash
cd sandbox/r_agent_gateway_work_20260622_173345
source .venv/bin/activate

export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"
export WECHAT_TOKEN="你自定义的微信 Token"

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

微信公众号 URL 填：

```text
https://你的公网域名/webhook/wechat
```
