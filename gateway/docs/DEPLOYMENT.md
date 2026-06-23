# R-Agent Gateway 部署说明

## 本地进程部署

```bash
cp .env.gateway.example .env.gateway
set -a
source .env.gateway
set +a
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

## Docker Compose 部署

```bash
cp .env.gateway.example .env.gateway
# 编辑 .env.gateway，填入模型、微信、飞书配置
docker compose -f docker-compose.gateway.yml up -d --build
```

## Nginx 反向代理示例

```nginx
server {
    listen 443 ssl http2;
    server_name bot.example.com;

    ssl_certificate /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

## 异步 webhook 与幂等

- `RAGENT_GATEWAY_ASYNC_WEBHOOKS=true` 时，飞书消息会进入内存队列，HTTP 回调快速返回 `202`。
- `RAGENT_GATEWAY_EVENT_DEDUPE_TTL_SECONDS` 控制飞书 `event_id` 去重窗口，默认 3600 秒。
- 当前队列和去重表都是进程内内存实现。生产多副本部署时建议替换为 Redis/Celery/RQ，保证跨进程幂等和任务持久化。

## 生产 checklist

- 使用 HTTPS 域名，不要直接暴露裸 HTTP。
- 设置 `RAGENT_GATEWAY_AUTH_TOKEN` 保护 `/v1/*` 通用 API。
- webhook 平台侧保留签名/Token 校验。
- 根据实际负载设置 `RAGENT_GATEWAY_MAX_SESSIONS`、异步队列大小和 worker 数。
- 对微信安全模式、企业微信、飞书 Encrypt Key、QQ 平台签名校验按需补充适配。
- 增加日志采集、监控、限流和异常告警。


## QQ 接入部署说明

当前 Gateway 已支持原生 QQ 官方机器人 Webhook：

```text
https://你的域名/webhook/qq
```

部署时需要：

1. R-Agent Gateway 正常运行。
2. 安装 `PyNaCl`，用于 QQ Webhook Ed25519 校验签名。
3. 设置 `QQ_APP_ID`、`QQ_APP_SECRET`、`QQ_SANDBOX`。
4. 通过 Caddy/Nginx/cloudflared/ngrok 暴露 HTTPS。
5. 在 QQ 开放平台将请求地址配置为 `/webhook/qq`。
6. 将服务器公网 IP 加入 QQ 开放平台 IP 白名单。

生产环境中，QQ 消息建议开启异步处理：

```bash
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true
```

当前实现为文本消息最小适配；更复杂的 QQ 消息类型需要继续扩展 `gateway/adapters/qq.py`。
