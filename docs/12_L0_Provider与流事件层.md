# 12 · L0 Provider 与流事件层

> 本章解释 R-Agent 如何把「向模型发请求、把厂商流式响应转成统一事件」单独抽成一层，
> 让 Agent Loop 只依赖稳定的内部协议，而不认识任何厂商 SDK。实现快照：2026-10-19。

参考 Pi 的 L0 设计（`/Users/bytedance/myenv/dsh/pi/learning_note/L0.md`）。

## 1. 一句话理解

L0 位于 R-Agent 与外部模型服务之间，只做一件事：

> 向模型发送请求，并把不同厂商的（流式或非流式）响应，转换成 R-Agent 的统一事件。

```text
OpenAI / Azure / 未来的 Anthropic / Google / 本地模型
                      |
                      v
                 L0 协议层（core/llm）
                      |
                      v
         AssistantMessageEventStream
```

L0 不选择模型、不维护会话、不执行工具——这些属于更高层（Agent Loop / Session）。

## 2. 五个部分与源码入口

| 部分 | 作用 | 源码 |
| --- | --- | --- |
| 公共语言 | 统一的消息、工具调用、流事件类型（防腐层） | `core/llm/types.py` |
| 事件流容器 | 连接生产者/消费者的单消费者队列 | `core/llm/event_stream.py` |
| Provider 适配器 | 把厂商协议翻译成统一事件 | `core/llm/provider.py`、`core/llm/adapters/openai_chat.py` |
| 响应状态机 | 增量事件组装成完整 `AssistantMessage` | 在 adapter 内实现 |
| 运行保障 | 延迟加载、失败、取消、测试 | `core/llm/lazy.py`、`core/llm/adapters/faux.py` |

工厂与兼容层：`core/llm/__init__.py:build_provider`、`core/llm/compat.py`。

## 3. 公共语言（防腐层）

厂商协议只允许存在于 `core/llm/adapters/` 内部。Agent Loop、Session、GUI 只依赖：

```text
TextContent / ThinkingContent / ToolCall   内容块
Usage                                      token 与缓存计量
AssistantMessage                           最终状态（写历史、读 stop_reason、读 usage）
AssistantMessageEvent                      过程事件（实时渲染、观测成功/失败/取消）
```

两个视图是同一次模型调用的两面（Pi 第 4.3 节）：

```text
事件视图（for event in stream）：这次响应是怎样产生的
结果视图（stream.result()）    ：这次响应最终是什么
```

`ToolCall.arguments` 是**已解析**的 JSON 对象；解析失败时保留 `raw_arguments` 与
`arguments_error`，交由上层参数校验，绝不执行猜测出来的参数（Pi 第 6 节）。

## 4. 事件流容器

`AssistantMessageEventStream` 是**线程安全的单消费者队列**，不是广播。语义对齐 Pi：

- 终止事件（`done` / `error`）既作为过程最后一步交给迭代器，又用于解析 `result()`；
- 终止之后的 `push` 被忽略；
- 错误也是结构化终止（`error` 事件 + error `AssistantMessage`），调用方不必混合处理
  事件和异常。

与 Pi 的差异：R-Agent 主循环是同步 + 线程模型（`chat.completions` 同步、委派用线程），
所以这里是**同步**事件流（`for event in stream` / `stream.result(timeout)`），而非 async
迭代器。底层 `FifoQueue` 用 incoming/outgoing 双数组，均摊 O(1) 出队。

## 5. Provider 适配器（Ports and Adapters）

契约 `ModelProvider` 提供两个入口（Pi「stream() 与 streamSimple()」）：

```text
stream(request, **provider_options)   接受厂商专有配置
stream_simple(request, options)       接受 provider-neutral 的 StreamOptions
```

- `OpenAIChatProvider`（`adapters/openai_chat.py`）是首个真实 adapter，覆盖 OpenAI
  兼容端点与 Azure；
- 流式路径边到边 push `*_delta`，到流末尾组装 `*_end` 与完整 `ToolCall`；
- 非流式路径一次 `create()` 后**合成**同一套 start/delta/end/done 事件，消费者无差别；
- 归一化 `Usage` 的同时，把厂商原始 usage 对象放进 `Usage.raw`，让既有
  `core/cache_observability.provider_cache_tokens` 继续读取厂商缓存字段，不丢诊断。

**新增 Provider 时 Agent Loop 不用改**：只要 adapter 满足「翻译请求 + 产出统一事件 +
统一终止/错误语义」契约即可。将来接 Anthropic/Google 时，在 `build_provider` 里按
`config.get_client_type()` 分派不同 adapter。

## 6. 运行保障

- `lazy.py:lazy_stream`：立即返回外层 stream，后台做 setup（如延迟 import adapter、
  刷新认证）。setup 失败不会变成后台未捕获异常，而是转成外层 `error` 终止事件。
- `adapters/faux.py:FauxProvider`：确定性测试 Provider，真实产出完整事件序列
  （start → delta → done/error），可脚本化 text/thinking/tool call/error/abort，验证
  harness 时序而不仅是最终值。
- 取消：`build_provider(..., cancel_check=...)` 把 Esc 中断桥接进 adapter，取消时流
  产出 `aborted` 终止事件，Agent 层转成 `AgentInterrupted`。

错误边界（Pi 第 7 节）：返回 stream 之前只能同步抛错；返回 stream 之后，任何错误都
必须进入流形成 `error` 消息。

## 7. 如何接进既有 Agent 循环

`core/llm/compat.py` 把统一的 `AssistantMessage` 适配回「看起来像 OpenAI 响应」的对象
（`.choices[0].message` + `.usage`），让 `core/agent.py` 的 token/cache 记账与主循环结构
零改动复用：

```text
_complete()  ──┬── provider 层开启 → _chat_completion_via_provider()（经 ModelProvider）
               └── 关闭           → _chat_completion_with_retry()（旧直连 SDK 路径）
```

- 助手回复经 `ProviderMessage.to_dict()` 以**可序列化 dict** 入 `messages` 历史，保证
  下一轮请求能被厂商 SDK 正常序列化；
- 流式开启时，`text_delta` / `thinking_delta` 转发给 `on_think(iteration,
  stream_delta=..., stream_kind=...)`，供 CLI/GUI 实时渲染；旧版 `on_think` 不支持该
  kwargs 时自动忽略增量，最终消息仍完整呈现。

## 8. 配置开关

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `LLM_PROVIDER_LAYER_ENABLED` | `1` | 主模型请求是否经 L0 Provider 层；`0` 回退旧直连 SDK |
| `LLM_STREAMING_ENABLED` | `0` | 是否使用流式响应（仅 Provider 层开启时生效） |

两个开关都可随时回退，隔离排查风险。

## 9. 如何验证

```bash
PYTHONPATH=. python3 -m pytest tests/test_llm_provider_layer.py -q
PYTHONPATH=. python3 -m pytest tests/test_agent_provider_layer.py -q
# 回归（上下文/压缩/durable/事件流/中断）
PYTHONPATH=. python3 -m pytest \
  tests/test_context_control.py tests/test_durable_versions.py \
  tests/test_gui_runtime.py tests/test_run_event_stream.py \
  tests/test_agent_interrupt.py -q
```

重点测试：

- `test_event_stream_iterates_then_result` / `test_event_stream_error_is_terminal_result`
- `test_faux_tool_call_turn` / `test_faux_error_turn_keeps_partial_text`
- `test_openai_adapter_streaming_incremental_tool_call`
- `test_openai_adapter_create_error_becomes_error_event`
- `test_lazy_stream_setup_failure_is_error_event`
- `test_agent_loop_runs_through_provider_layer`
- `test_provider_layer_streaming_forwards_deltas`

> 在本机跑 pytest 建议设 `PYTHONPYCACHEPREFIX=/private/tmp/r-agent-pycache`，避免受限的
> 本地字节码写入。

## 10. 当前边界

- 目前只有一个真实 adapter（OpenAI 兼容 / Azure）；Anthropic/Google 为预留；
- 事件流是单消费者队列，不是广播；需要多个过程观察者时应在上层消费一次再分发；
- 非流式路径为合成事件，`*_delta` 的粒度等于一整块内容，不代表真实到达节奏；
- 流式路径依赖端点支持 `stream_options.include_usage` 才能拿到 usage，否则 usage 为空；
- compat 层只覆盖 chat.completions 形状；若将来 adapter 走 responses API，需要扩展
  兼容响应对象。
