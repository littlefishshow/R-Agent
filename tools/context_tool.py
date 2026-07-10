import json
from tools.registry import registry
from core.context_control import compress_messages


def _prepend_manual_summary(result: dict, summary: str = "", next_steps: str = "") -> dict:
    """把用户/模型显式给出的阶段摘要合并进自动压缩摘要。"""
    if not result.get("success"):
        return result
    manual_parts = []
    if summary:
        manual_parts.append("【手动归档摘要】\n" + str(summary))
    if next_steps:
        manual_parts.append("【下一步】\n" + str(next_steps))
    if not manual_parts:
        return result

    manual_text = "\n\n".join(manual_parts)
    result["recorded_summary"] = summary
    result["next_steps"] = next_steps
    result["summary"] = manual_text + ("\n\n" + result.get("summary", "") if result.get("summary") else "")

    compressed = result.get("compressed_messages") or []
    if compressed:
        # compress_messages 的第 2 条通常是 system 自动摘要；若不存在则插入。
        insert_at = 1 if len(compressed) > 1 and compressed[0].get("role") == "system" else 0
        if insert_at < len(compressed) and compressed[insert_at].get("role") == "system":
            compressed[insert_at] = {
                **compressed[insert_at],
                "content": manual_text + "\n\n" + str(compressed[insert_at].get("content", "")),
            }
        else:
            compressed.insert(insert_at, {"role": "system", "content": manual_text})
        result["compressed_messages"] = compressed
    return result


def archive_subtask(summary: str = "", next_steps: str = "", messages=None, tools=None,
                    model: str = "", max_context_tokens: int = 0,
                    trigger_ratio: float = 0.8, target_ratio: float = 0.55,
                    preserve_recent_messages: int = 16, force: bool = True) -> str:
    """统一上下文压缩/归档工具。

    兼容旧用法：只传 summary/next_steps 时，由 Agent 主循环拦截并压缩当前 self.messages。
    新用法：传入完整 messages/tools 时，本工具直接返回 compressed_messages 与统计。
    """
    try:
        if messages:
            result = compress_messages(
                messages or [],
                tools or [],
                model=model or None,
                max_context_tokens=max_context_tokens or None,
                trigger_ratio=trigger_ratio,
                target_ratio=target_ratio,
                preserve_recent_messages=preserve_recent_messages,
                force=force,
            )
            result = _prepend_manual_summary(result, summary, next_steps)
            result.setdefault("message", "Context archived and compressed from provided messages.")
            return json.dumps(result, ensure_ascii=False)

        # 没有 messages 时，工具进程无法直接访问 Agent 的 self.messages；实际压缩由
        # core/agent.py 在看到 archive_subtask 成功后完成。
        res = {
            "success": True,
            "compressed": False,
            "agent_managed": True,
            "message": "Archive request recorded. The Agent loop will compress current conversation history.",
            "recorded_summary": summary,
            "next_steps": next_steps,
        }
        return json.dumps(res, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


registry.register(
    name="archive_subtask",
    description=(
        "统一上下文压缩/归档工具：可在阶段性任务完成时归档，也可接收完整 messages/tools 做智能压缩。"
        "旧用法只传 summary/next_steps，Agent 主流程会压缩当前 self.messages；"
        "新用法传 messages 时会按完整 message 与 assistant+tool 组压缩，保留用户重点、助手决策、tool call/result 要点，"
        "不会从保留的单条 message 中间截断。默认也作为主流程接近上下文窗口时的统一压缩语义。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "阶段性手动摘要；旧用法必填，新用法可选，会并入自动摘要。"},
            "next_steps": {"type": "string", "description": "接下来还需要执行什么任务。"},
            "messages": {"type": "array", "description": "可选：完整 OpenAI chat messages 列表；每条 message 应作为整体传入。"},
            "tools": {"type": "array", "description": "可选 tools schema 列表，用于估算请求总上下文。"},
            "model": {"type": "string", "description": "模型名，用于本地上下文窗口映射。"},
            "max_context_tokens": {"type": "integer", "description": "显式最大上下文窗口；优先于模型映射。"},
            "trigger_ratio": {"type": "number", "description": "触发压缩比例，默认 0.8。"},
            "target_ratio": {"type": "number", "description": "压缩后目标比例，默认 0.55。"},
            "preserve_recent_messages": {"type": "integer", "description": "至少尝试保留的最近完整 message 数，默认 16。"},
            "force": {"type": "boolean", "description": "是否强制压缩；false 时低于阈值仅返回原 messages 和统计。"}
        },
        "required": []
    },
    handler=archive_subtask
)
