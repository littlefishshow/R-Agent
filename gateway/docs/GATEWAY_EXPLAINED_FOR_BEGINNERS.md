# R-Agent Gateway 小学生也能看懂的解释

> 这篇文档不假设你懂后端、网络、Webhook。目标是讲清楚：
>
> 1. Gateway 到底是什么；
> 2. 为什么本地启动后还要 cloudflared/ngrok；
> 3. QQ / 飞书 / 微信里的消息，最后是怎么跑到 R-Agent，再怎么回复回去的。

---

## 0. 先用一句话解释

**Gateway 就像 R-Agent 家门口的“收发室”。**

- QQ、飞书、微信来的消息，先送到 Gateway；
- Gateway 把消息翻译成 R-Agent 能看懂的话；
- R-Agent 想好答案；
- Gateway 再把答案送回 QQ、飞书、微信。

可以想象成：

```text
用户在 QQ 发消息
    ↓
QQ 平台把消息送到 Gateway
    ↓
Gateway 把消息交给 R-Agent
    ↓
R-Agent 生成回复
    ↓
Gateway 调 QQ 接口发回去
    ↓
用户在 QQ 看到回复
```

---

## 1. 先讲一点计算机网络基础

### 1.1 什么是“服务”

你平时打开网站，比如：

```text
https://www.baidu.com
```

其实是你的电脑在对百度服务器说：

```text
你好，我想看首页。
```

百度服务器收到后，返回网页。

所以“服务”就是：

```text
一个一直开着、等待别人来请求的程序。
```

R-Agent Gateway 也是一个服务。

你启动它：

```bash
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

它就会在你的电脑上开一个“窗口”，等别人来访问。

这个窗口的地址是：

```text
http://127.0.0.1:8080
```

---

### 1.2 什么是 IP 和端口

你可以把电脑想象成一栋楼。

- **IP 地址**：这栋楼的位置；
- **端口 port**：楼里面的某个房间号。

例如：

```text
127.0.0.1:8080
```

意思是：

```text
127.0.0.1 这台电脑上的 8080 房间
```

`127.0.0.1` 很特殊，它表示：

```text
我自己这台电脑
```

所以：

```text
http://127.0.0.1:8080
```

只能你自己电脑访问，外面的 QQ、飞书、微信访问不到。

---

### 1.3 什么是 HTTP

HTTP 可以理解成一种“网络说话格式”。

比如你用浏览器打开：

```text
http://127.0.0.1:8080/healthz
```

浏览器是在说：

```text
GET /healthz
```

意思是：

```text
我要看看这个服务还活着吗？
```

Gateway 会回复：

```json
{"ok": true, "service": "r-agent-gateway"}
```

意思是：

```text
我活着，我是 r-agent-gateway。
```

---

### 1.4 什么是路由

路由就是服务里的“不同门牌号”。

R-Agent Gateway 现在有这些门牌号：

| 路由 | 用途 |
|---|---|
| `/healthz` | 检查服务是否活着 |
| `/v1/chat` | 直接和 R-Agent 聊天 |
| `/webhook/feishu` | 接收飞书消息 |
| `/webhook/wechat` | 接收微信消息 |
| `/webhook/qq` | 接收 QQ 官方机器人消息 |

所以你打开根路径：

```text
http://127.0.0.1:8080/
```

看到：

```json
{"error":"not found"}
```

不是服务坏了，而是因为 Gateway 没有给 `/` 这个门牌号安排工作。

正确测试方式是打开：

```text
http://127.0.0.1:8080/healthz
```

---

## 2. Gateway 到底干什么

R-Agent 原本更像一个“命令行聊天程序”。

你在终端里输入：

```text
帮我总结一下这个文件
```

R-Agent 在终端里回答。

但 QQ、飞书、微信不会打开你的终端。

它们只会用网络请求把消息发过来。

所以我们加了 Gateway。

Gateway 的工作是：

```text
把“外部平台的消息格式”转换成“R-Agent 能处理的普通文字”
```

再把：

```text
R-Agent 的回答
```

转换成：

```text
QQ / 飞书 / 微信 能发送的回复格式
```

---

## 3. 本地服务为什么外网访问不到

你启动 Gateway 后：

```bash
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

你的本机可以访问：

```text
http://127.0.0.1:8080/healthz
```

但 QQ、飞书、微信在公网，它们不在你的电脑里。

它们不能访问：

```text
127.0.0.1
```

因为对 QQ 来说，`127.0.0.1` 指的是 QQ 自己的服务器，不是你的电脑。

这就是为什么你需要：

```bash
cloudflared tunnel --url http://localhost:8080
```

或者：

```bash
ngrok http 8080
```

---

## 4. cloudflared / ngrok 是干什么的

它们像一个“临时公网传送门”。

你的电脑在家里，QQ 服务器在公网。

QQ 找不到你的电脑，于是 cloudflared 帮你开一个公网地址：

```text
https://xxxx.trycloudflare.com
```

然后它负责转发：

```text
QQ / 飞书 / 微信
    ↓
https://xxxx.trycloudflare.com
    ↓
cloudflared
    ↓
http://localhost:8080
    ↓
R-Agent Gateway
```

所以你访问：

```text
https://xxxx.trycloudflare.com/healthz
```

其实最后到了你本地的：

```text
http://localhost:8080/healthz
```

---

## 5. 一条普通消息是怎么流动的

假设你在 QQ 群里 @机器人：

```text
@R-Agent 你好，帮我写一个 Python hello world
```

整体流程是：

```text
你在 QQ 发消息
    ↓
QQ 官方平台收到消息
    ↓
QQ 官方平台向你的公网地址发 HTTP 请求
    ↓
cloudflared 把请求转到你本地 8080 端口
    ↓
Gateway 的 /webhook/qq 收到消息
    ↓
Gateway 提取文本：你好，帮我写一个 Python hello world
    ↓
Gateway 调用 R-Agent
    ↓
R-Agent 生成回答
    ↓
Gateway 调 QQ 官方发消息 API
    ↓
QQ 群里出现机器人回复
```

画成图：

```text
┌────────┐
│  你    │
└───┬────┘
    │ 在 QQ 发消息
    ▼
┌──────────────┐
│ QQ 官方平台  │
└───┬──────────┘
    │ POST https://公网域名/webhook/qq
    ▼
┌──────────────┐
│ cloudflared  │
└───┬──────────┘
    │ 转发到 http://localhost:8080/webhook/qq
    ▼
┌──────────────┐
│ Gateway      │
└───┬──────────┘
    │ 调用 R-Agent
    ▼
┌──────────────┐
│ R-Agent      │
└───┬──────────┘
    │ 生成回答
    ▼
┌──────────────┐
│ Gateway      │
└───┬──────────┘
    │ 调 QQ 发消息接口
    ▼
┌──────────────┐
│ QQ 官方平台  │
└───┬──────────┘
    │ 展示消息
    ▼
┌────────┐
│  你    │
└────────┘
```

---

## 6. `/v1/chat` 是什么

`/v1/chat` 是 Gateway 提供的“通用聊天入口”。

你可以不用 QQ、飞书、微信，直接用 curl 调它：

```bash
curl -X POST http://127.0.0.1:8080/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"test","message":"你好"}'
```

意思是：

```text
我有一个叫 test 的会话，我发了一句话：你好。
```

Gateway 会返回：

```json
{
  "session_id": "test",
  "answer": "你好！我是 R-Agent..."
}
```

这里的 `session_id` 很重要。

它代表“这是哪一段聊天”。

比如：

```text
qq:group:123       QQ 群 123 的聊天
qq:user:abc        QQ 用户 abc 的私聊
feishu:oc_xxx      飞书群聊
wechat:openid_xxx  微信用户
```

这样不同群、不同用户的上下文不会混在一起。

---

## 7. `/webhook/qq` 是什么

`/webhook/qq` 是专门给 QQ 官方平台调用的入口。

你不要手动像 `/v1/chat` 那样直接发普通文本给它。

QQ 官方平台会发一种特定格式的 JSON。

Gateway 收到后，会做几件事：

1. 判断是不是 QQ 的验证请求；
2. 如果是验证请求，就返回 QQ 需要的签名；
3. 如果是消息事件，就取出消息文字；
4. 调 R-Agent；
5. 把 R-Agent 的答案发回 QQ。

---

## 8. QQ 为什么要验证

当你在 QQ 开放平台填：

```text
https://你的公网域名/webhook/qq
```

QQ 会先问 Gateway：

```text
你真的是这个机器人服务吗？
```

这叫“回调地址校验”。

QQ 会发来类似：

```json
{
  "op": 13,
  "d": {
    "plain_token": "一串随机文字",
    "event_ts": "时间戳"
  }
}
```

Gateway 要用你的：

```text
QQ_APP_SECRET
```

生成一个签名，然后返回：

```json
{
  "plain_token": "一串随机文字",
  "signature": "签名"
}
```

QQ 验证通过后，才会正式把消息发给你的 Gateway。

---

## 9. QQ Bot 本地启动流程

### 9.1 安装依赖

```bash
pip install -r requirements.txt
```

里面包含：

```text
PyNaCl
```

它用于 QQ 回调校验签名。

### 9.2 配置环境变量

```bash
export OPENAI_API_KEY="你的模型 Key"
export LLM_MODEL="gpt-4o"

export QQ_APP_ID="QQ 开放平台 AppID"
export QQ_APP_SECRET="QQ 开放平台 AppSecret"
export QQ_SANDBOX=true

export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true
```

### 9.3 启动 Gateway

```bash
python3 -m gateway.server --host 0.0.0.0 --port 8080
```

### 9.4 开公网隧道

```bash
cloudflared tunnel --url http://localhost:8080
```

得到：

```text
https://xxxx.trycloudflare.com
```

### 9.5 测试健康检查

打开：

```text
https://xxxx.trycloudflare.com/healthz
```

看到：

```json
{"ok": true, "service": "r-agent-gateway"}
```

说明公网访问通了。

### 9.6 QQ 开放平台填回调地址

填：

```text
https://xxxx.trycloudflare.com/webhook/qq
```

不要填：

```text
https://xxxx.trycloudflare.com/
```

也不要填：

```text
https://xxxx.trycloudflare.com/v1/chat
```

---

## 10. 飞书消息怎么走

飞书和 QQ 很像。

飞书后台填：

```text
https://你的公网域名/webhook/feishu
```

流程：

```text
用户给飞书机器人发消息
    ↓
飞书平台 POST /webhook/feishu
    ↓
Gateway 解析飞书消息
    ↓
Gateway 调 R-Agent
    ↓
Gateway 调飞书 API 发回答案
```

---

## 11. 微信消息怎么走

微信公众号后台填：

```text
https://你的公网域名/webhook/wechat
```

流程：

```text
用户给公众号发消息
    ↓
微信服务器 POST /webhook/wechat
    ↓
Gateway 解析 XML
    ↓
Gateway 调 R-Agent
    ↓
Gateway 返回 XML 回复
    ↓
用户在微信看到回复
```

微信和 QQ/飞书有一个区别：

- QQ/飞书通常是 Gateway 再主动调用平台 API 发消息；
- 微信公众号明文模式下，经常是 Gateway 直接在 HTTP 响应里返回 XML。

---

## 12. 异步处理是什么意思

有时 R-Agent 回答很慢，比如它要读文件、查资料、调用工具。

但 QQ、飞书这些平台不喜欢等太久。

所以我们有一个开关：

```bash
export RAGENT_GATEWAY_ASYNC_WEBHOOKS=true
```

意思是：

```text
平台消息来了以后，Gateway 先说“我收到了”，然后后台慢慢让 R-Agent 处理。
```

就像餐厅：

```text
服务员先拿到订单 → 告诉顾客已下单 → 厨房慢慢做菜 → 做好再上菜
```

---

## 13. 你看到的日志是什么意思

例如：

```text
[gateway] "GET / HTTP/1.1" 404 -
[gateway] "GET /favicon.ico HTTP/1.1" 404 -
```

意思是：

```text
浏览器访问了 / 和 /favicon.ico，但是 Gateway 没有这两个路由。
```

这不是错误。

正确看服务是否正常，要访问：

```text
/healthz
```

---

## 14. 最小完整例子：QQ 用户发一句话

假设：

```text
用户在 QQ 群里说：@R-Agent 写一个 Python hello world
```

QQ 发给 Gateway 的消息大概会包含：

```json
{
  "t": "GROUP_AT_MESSAGE_CREATE",
  "d": {
    "id": "msg-001",
    "group_openid": "group-abc",
    "content": "@R-Agent 写一个 Python hello world"
  }
}
```

Gateway 会提取出：

```text
写一个 Python hello world
```

构造会话 ID：

```text
qq:group:group-abc
```

然后内部相当于调用：

```text
R-Agent.run_conversation("写一个 Python hello world")
```

R-Agent 可能返回：

```text
可以这样写：

print("Hello, world!")
```

Gateway 再调用 QQ 发消息 API，把这段话发回群里。

---

## 15. 现在这个 Gateway 已经支持什么

| 平台 | 入口 | 当前状态 |
|---|---|---|
| 通用聊天 | `/v1/chat` | 已支持 |
| 飞书 | `/webhook/feishu` | 已支持文本消息 |
| 微信公众号 | `/webhook/wechat` | 已支持明文 XML 文本消息 |
| QQ 官方机器人 | `/webhook/qq` | 已支持文本消息最小适配 |

---

## 16. 现在还不支持什么

QQ 当前还只是最小文本适配，不支持：

- 图片；
- 文件；
- 富文本；
- 所有 QQ 官方事件类型；
- 更复杂的错误码处理；
- 正式上线前的完整合规流程自动化。

微信当前还不支持：

- 安全模式 AES 加解密；
- 企业微信完整适配。

飞书当前还不支持：

- encrypted callback 解密。

---

## 17. 你应该记住的 4 句话

1. **Gateway 是 R-Agent 的收发室。**
2. **本地的 `127.0.0.1` 外网访问不到，所以要 cloudflared/ngrok。**
3. **QQ/飞书/微信要填各自的 webhook 路由，不要填 `/`。**
4. **平台消息先进 Gateway，Gateway 再交给 R-Agent，最后 Gateway 把答案发回平台。**
