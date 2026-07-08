# R-Agent Gateway 接入微信/飞书/QQ 教程

本文档对应 sandbox 副本中的 `gateway/` 服务层。它把原本 CLI 内部的 `RAgent.run_conversation()` 包成 HTTP 服务，并提供微信、飞书 webhook 适配；QQ 当前建议通过机器人中间层调用 `/v1/chat`。

## 1. 本地启动

```bash
cd sandbox/r_agent_gateway_work_20260622_173345
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 配置你的模型环境变量（示例）
export OPENAI_API_KEY="..."
export LLM_MODEL="gpt-4o"

# 可选：保护通用 HTTP API
export RAGENT_GATEWAY_AUTH_TOKEN="change-me"

python3 -m gateway.server --host 0.0.0.0 --port 8080
```

健康检查：

```bash
curl http://127.0.0.1:8080/healthz
```

通用聊天 API：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer change-me' \
  -d '{"session_id":"demo","message":"你好"}'
```

## 2. 对外暴露 HTTPS

微信、飞书和 QQ 平台回调都需要公网 HTTPS 地址。开发测试可用 ngrok/cloudflared：

```bash
ngrok http 8080
# 或 cloudflared tunnel --url http://localhost:8080
```

生产建议部署到有固定域名和 TLS 证书的服务器，并放在反向代理（Nginx/Caddy）后面。

## 3. 接入微信公众号/企业微信

> 说明：个人微信没有官方开放的 bot webhook。推荐使用「微信公众号」或「企业微信应用」。本 gateway 当前实现的是微信公众号/兼容 XML 回调风格；企业微信还需要根据你使用的应用回调格式补充 AES 解密/加密回复。

### 3.1 配置环境变量

```bash
export WECHAT_TOKEN="你在微信后台填写的 Token"
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

### 3.2 微信后台配置

1. 进入微信公众平台 → 开发 → 基本配置 → 服务器配置。
2. URL 填：`https://你的域名/webhook/wechat`
3. Token 填：与 `WECHAT_TOKEN` 完全一致。
4. EncodingAESKey：开发初期可选明文模式；如果启用安全模式，需要补充消息解密/加密。
5. 提交时微信会 GET 该 URL，本 gateway 会校验 `signature/timestamp/nonce` 并回显 `echostr`。
6. 开启服务器配置后，用户发送文本消息时，微信会 POST XML 到 `/webhook/wechat`，gateway 用 `FromUserName` 作为会话 ID 调用 R-Agent，并返回 XML 被动回复。

### 3.3 注意事项

- 微信被动回复通常要求 5 秒内返回；R-Agent 复杂工具调用可能超时。生产建议改成“立即返回 success + 客服消息异步回复”。
- 安全模式（AES）未在最小实现中启用，需要增加 `WECHAT_ENCODING_AES_KEY` 和加解密逻辑。
- 个人微信机器人通常依赖非官方协议，风险较高，不建议作为主方案。

## 4. 接入飞书 Bot

### 4.1 创建应用

1. 打开飞书开放平台，创建企业自建应用。
2. 添加机器人能力，并开通事件订阅。
3. 权限建议至少包含：接收消息事件、发送消息。常见权限：`im:message`、`im:message:send_as_bot`（以飞书后台实际名称为准）。
4. 发布/安装应用到目标企业或测试群。

### 4.2 配置环境变量

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_VERIFICATION_TOKEN="事件订阅 Verification Token"
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

### 4.3 飞书事件订阅

1. 在飞书开放平台 → 事件订阅，请求地址填：`https://你的域名/webhook/feishu`
2. 先不要启用 Encrypt Key（本最小实现会明确拒绝 encrypted payload）；如果必须启用，需要在 gateway 中补充解密。
3. 保存时飞书会发送 `url_verification`，gateway 返回 `challenge`。
4. 订阅 `im.message.receive_v1`。
5. 用户在群里 @机器人或私聊机器人时，飞书会 POST 事件；gateway 会调用 R-Agent，并使用飞书发送消息 API 回复到 `chat_id`。

### 4.4 注意事项

- 如果没有配置 `FEISHU_APP_ID/FEISHU_APP_SECRET`，gateway 仍会处理事件并返回 `sent=false`，便于本地验收解析逻辑，但不会真正发飞书消息。
- 飞书回调可能重复投递；生产建议基于 `event_id` 做幂等去重。
- 复杂任务可能超过飞书回调期望耗时；生产建议改成队列异步处理。


## 5. 接入 QQ 官方机器人

### 5.1 当前实现

Gateway 已内置 QQ 官方机器人 Webhook 最小适配：

```text
POST /webhook/qq
```

支持：

- QQ Webhook URL 校验：处理 `plain_token` / `event_ts`，返回 `signature`。
- 解析常见消息事件：`GROUP_AT_MESSAGE_CREATE`、`C2C_MESSAGE_CREATE`、`AT_MESSAGE_CREATE`、`DIRECT_MESSAGE_CREATE`、`MESSAGE_CREATE`。
- 群聊会话 ID：`qq:group:{group_openid}`。
- 私聊会话 ID：`qq:user:{user_openid}`。
- 调用 R-Agent 后通过 QQ 官方 OpenAPI 回复文本消息。
- 可复用 `RAGENT_GATEWAY_ASYNC_WEBHOOKS=true` 做异步处理，复用 `EventDeduplicator` 做事件去重。

### 5.2 配置环境变量

```bash
export QQ_APP_ID="QQ 开放平台 AppID"
export QQ_APP_SECRET="QQ 开放平台 AppSecret"
export QQ_SANDBOX=true
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true
```

QQ Webhook 校验需要 Ed25519 签名，依赖：

```bash
pip install PyNaCl>=1.5.0
```

项目 `requirements.txt` 已包含 `PyNaCl>=1.5.0`。

### 5.3 QQ 开放平台配置

1. 前往 QQ 开放平台，进入应用管理，创建机器人并填写基本信息。
2. 测试版机器人通常先配置「资料」和「沙箱配置」。
3. 在「沙箱配置」中：
   - 如果部署到 QQ 群，按 QQ 群 ID 要求选择群聊，并在消息列表配置中添加有私聊权限的用户；
   - 如果部署到 QQ 频道，按频道 ID 要求选择频道，机器人类型为 0。
4. 在「开发管理」中记录 `AppID`、`Token`、`AppSecret`。
5. 将 Gateway 所在服务器公网 IP 填入 IP 白名单。
6. 用 HTTPS 暴露 Gateway，回调请求地址填写：

   ```text
   https://你的域名/webhook/qq
   ```

7. 保存时 QQ 平台会发起 Webhook 校验，Gateway 自动返回签名。
8. 用户在 QQ 群 @机器人或私聊机器人后，Gateway 会调用 R-Agent 并回复。

### 5.4 注意事项

- 当前实现是 QQ 官方 Webhook 的最小文本消息适配；图片、文件、富文本等消息暂未支持。
- QQ 官方对违规 AIGC 接入有限制，请遵守平台规则。
- 测试版机器人和正式上线机器人流程不同，正式上线需按 QQ 开放平台发布流程进行。
- 不建议使用非官方个人 QQ 协议，存在稳定性和账号风险。

## 6. API 路由清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 健康检查 |
| POST | `/v1/chat` | 通用聊天 API，JSON 入参 `session_id/message/exclude_tools` |
| GET | `/v1/sessions` | 查看内存中的会话列表 |
| POST | `/v1/sessions/{id}/reset` | 清空某个会话 |
| GET/POST | `/webhook/wechat` | 微信服务器校验与消息回调 |
| POST | `/webhook/feishu` | 飞书事件回调 |
| POST | `/webhook/qq` | QQ 官方机器人 Webhook：URL 校验、消息回调与文本回复 |

## 7. 生产化建议

- 把 `AgentSessionManager` 的会话状态落盘或接 Redis，避免进程重启丢上下文。
- 将 webhook 处理改成异步队列，避免微信/飞书回调超时。
- 增加 webhook IP 白名单、签名校验、重放防护和 event_id 幂等。
- 为工具执行增加 per-session 并发限制和超时策略。
- 在反向代理层添加 HTTPS、访问日志和限流。

## 8. 异步 webhook 与重复投递

本 sandbox 版本继续补充了轻量生产化能力：

```bash
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true
export RAGENT_GATEWAY_EVENT_DEDUPE_TTL_SECONDS=3600
export RAGENT_GATEWAY_ASYNC_QUEUE_SIZE=100
export RAGENT_GATEWAY_ASYNC_WORKERS=1
```

- 飞书 webhook 在异步模式下会快速返回 `202 Accepted`，后台 worker 再调用 R-Agent 并发送消息。
- gateway 使用飞书 `event_id` 做内存 TTL 去重，降低重复投递导致重复回复的概率。
- 当前为无外部依赖的最小实现；多进程/多机器生产环境应改成 Redis/RQ/Celery 或其他可靠队列。

详见 `gateway/docs/DEPLOYMENT.md`。
