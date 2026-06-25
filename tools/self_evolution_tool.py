import json
from pathlib import Path
from tools.registry import registry

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "outputs" / "self_evolution"


def self_evolution_review(messages_snapshot=None, mode: str = "heuristic", dry_run: bool = True) -> str:
    """Hermes 式后台复盘的确定性最小实现：只产出建议，不自动写 memory/skill。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    msgs = messages_snapshot if isinstance(messages_snapshot, list) else []
    text = "\n".join(str(m.get("content", "")) for m in msgs if isinstance(m, dict))
    suggestions = []
    lower = text.lower()
    if "用户偏好" in text or "prefers" in lower or "以后" in text:
        suggestions.append({"target": "memory", "reason": "可能包含长期偏好；需人工/模型确认后写入 memory。"})
    if "skill" in lower or "流程" in text or "工作流" in text or "踩坑" in text:
        suggestions.append({"target": "skill", "reason": "可能包含可复用工作流；优先 patch 现有 skill，其次创建 umbrella skill。"})
    if not suggestions:
        suggestions.append({"target": "none", "reason": "未发现明确可沉淀信息。"})
    result = {"success": True, "mode": mode, "dry_run": dry_run, "suggestions": suggestions}
    log_path = LOG_DIR / "latest_review.json"
    log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["log_path"] = str(log_path)
    return json.dumps(result, ensure_ascii=False)


registry.register(
    name="self_evolution_review",
    description="Hermes 式后台自演进复盘的最小安全实现：分析对话快照并输出 memory/skill 沉淀建议；默认 dry_run，不直接修改长期资产。",
    parameters={
        "type": "object",
        "properties": {
            "messages_snapshot": {"type": "array", "description": "对话消息快照，可省略"},
            "mode": {"type": "string", "description": "heuristic/background_review"},
            "dry_run": {"type": "boolean", "description": "默认 true；当前版本只建议不自动写入"},
        },
    },
    handler=self_evolution_review,
)
