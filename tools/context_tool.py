import json
from tools.registry import registry

def archive_subtask(summary: str, next_steps: str = "") -> str:
    """
    当完成一个相对独立的子任务，或者发现上下文太长/接近迭代上限时调用。
    将之前的执行过程压缩成摘要保存，并清空中间的对话历史。
    """
    # 实际的清理逻辑在 core/agent.py 中拦截执行
    # 工具本身只返回一个确认信息
    res = {
        "success": True,
        "message": "Subtask archived successfully. Conversation history has been compressed.",
        "recorded_summary": summary,
        "next_steps": next_steps
    }
    return json.dumps(res, ensure_ascii=False)

registry.register(
    name="archive_subtask",
    description=(
        "上下文压缩工具。当一个复杂的子任务完成时，或者执行步骤太多即将到达最大迭代次数限制时调用此工具。"
        "此工具会将你传入的摘要(summary)保存为临时上下文记忆，并清空之前繁杂的对话历史记录（从而防止上下文超长或超过最大迭代次数）。"
        "如果在此子任务中发现了重要的客观事实或用户偏好，请在调用此工具前，先调用 memory 工具将其持久化。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "当前已完成子任务的详细摘要（包括取得了什么成果，得到了什么关键信息）。"
            },
            "next_steps": {
                "type": "string",
                "description": "接下来还需要执行什么任务（备忘提示）。"
            }
        },
        "required": ["summary"]
    },
    handler=archive_subtask
)
