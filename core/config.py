import json
import os
from dotenv import load_dotenv

# Ensure absolute path based on this file's location
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 自动加载根目录下的 .env 文件
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)

def get_api_key():
    # 从环境变量读取。防范空字符串 ""
    val = os.environ.get("OPENAI_API_KEY")
    return val if val else ""

def get_model():
    # Azure 模式下，通常使用特定的部署名，如 gpt-4o 或其他模型名。
    # 支持多种常见的环境变量名，防范空字符串 "" 覆盖默认值
    val = os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or os.environ.get("MODEL")
    if not val:
        return "gpt-4o"
    return val

def get_client_type():
    """获取客户端类型：'openai' 或 'azure'"""
    return os.environ.get("LLM_CLIENT_TYPE", "openai")

def get_azure_endpoint():
    return os.environ.get("AZURE_OPENAI_ENDPOINT", "https://aidp.bytedance.net/api/modelhub/online/v2/crawl")

def get_azure_api_version():
    return os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")

def get_openai_base_url():
    return os.environ.get("OPENAI_BASE_URL", "")

def get_display_mode():
    return os.environ.get("DISPLAY_MODE", "detailed")

def get_max_iterations():
    """单次对话允许的最大思考轮数。"""
    try:
        return int(os.environ.get("MAX_ITERATIONS", "30"))
    except ValueError:
        return 50


def get_gui_max_iterations():
    """GUI/Cockpit 单次对话允许的最大思考轮数。

    GUI 是长时交互界面，不应继承 CLI/子任务默认的 30 轮安全预算。
    环境变量按更具体到更通用的顺序读取：
    R_AGENT_GUI_MAX_ITERATIONS、GUI_MAX_ITERATIONS、COCKPIT_MAX_ITERATIONS。
    """
    for name in ("R_AGENT_GUI_MAX_ITERATIONS", "GUI_MAX_ITERATIONS", "COCKPIT_MAX_ITERATIONS"):
        raw = os.environ.get(name)
        if raw in (None, ""):
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        return max(1, value)
    return 200

def get_soft_warn_ratio():
    """软提醒阈值占 max_iterations 的比例（达到此比例后注入提醒）。"""
    try:
        ratio = float(os.environ.get("SOFT_WARN_RATIO", "0.7"))
    except ValueError:
        ratio = 0.7
    if ratio <= 0 or ratio >= 1:
        ratio = 0.7
    return ratio

def get_llm_max_retries():
    """瞬时错误（超时/限流/5xx）的最大重试次数（不含首次请求）。"""
    try:
        n = int(os.environ.get("LLM_MAX_RETRIES", "3"))
    except ValueError:
        n = 3
    return max(0, n)

def get_llm_retry_base_delay():
    """重试初始等待秒数，按 2^attempt 指数退避。"""
    try:
        d = float(os.environ.get("LLM_RETRY_BASE_DELAY", "1.0"))
    except ValueError:
        d = 1.0
    return max(0.0, d)



def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def get_llm_request_timeout():
    """LLM 单次请求超时时间（秒）；防止子 Agent 卡在 provider 请求上。"""
    return _env_float("LLM_REQUEST_TIMEOUT", 300.0, minimum=1.0)


def get_tool_execution_timeout():
    """隔离工具单次执行超时时间（秒）；<=0 时禁用超时。"""
    value = _env_float("TOOL_EXECUTION_TIMEOUT", 300.0, minimum=0.0)
    return None if value <= 0 else value


def get_delegate_task_wall_timeout():
    """单个 delegate 子任务默认墙钟超时时间（秒）；<=0 时禁用。"""
    value = _env_float("DELEGATE_TASK_WALL_TIMEOUT", 300.0, minimum=0.0)
    return None if value <= 0 else value


def get_delegate_step_events_limit():
    """每个委派子任务最多内嵌多少条采样 step event；0 表示不内嵌。"""
    try:
        value = int(os.environ.get("DELEGATE_STEP_EVENTS_LIMIT", "32"))
    except ValueError:
        value = 32
    return max(0, min(value, 200))


def get_self_evolution_review_interval():
    """每多少轮用户对话触发一次后台自演进复盘；<=0 表示关闭。"""
    try:
        n = int(os.environ.get("SELF_EVOLUTION_REVIEW_INTERVAL", "3"))
    except ValueError:
        n = 3
    return max(0, n)


def get_llm_context_window():
    """显式模型最大上下文窗口；0 表示使用本地模型映射。"""
    raw = (
        os.environ.get("LLM_CONTEXT_WINDOW")
        or os.environ.get("MODEL_CONTEXT_WINDOW")
        or os.environ.get("CONTEXT_WINDOW_TOKENS")
        or "0"
    )
    try:
        value = int(raw)
    except ValueError:
        return 0
    return max(0, value)


def get_context_compression_trigger_ratio():
    """上下文压缩触发比例；默认达到最大窗口 80% 触发。"""
    try:
        ratio = float(os.environ.get("CONTEXT_COMPRESSION_TRIGGER_RATIO", "0.8"))
    except ValueError:
        ratio = 0.8
    if ratio <= 0 or ratio >= 1:
        ratio = 0.8
    return ratio


def get_context_compression_target_ratio():
    """上下文压缩后的目标比例。"""
    try:
        ratio = float(os.environ.get("CONTEXT_COMPRESSION_TARGET_RATIO", "0.55"))
    except ValueError:
        ratio = 0.55
    if ratio <= 0 or ratio >= 1:
        ratio = 0.55
    return ratio


def get_context_compression_preserve_recent_messages():
    """自动压缩时至少尝试保留的最近完整 message 数。"""
    try:
        n = int(os.environ.get("CONTEXT_COMPRESSION_PRESERVE_RECENT_MESSAGES", "16"))
    except ValueError:
        n = 16
    return max(4, n)


def _parse_context_size(value, *, allow_list=False):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    items = parsed if allow_list and isinstance(parsed, list) else [parsed]
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            return None
        kind = str(item.get("type") or "").strip().lower()
        raw_value = item.get("value")
        if kind not in ("tokens", "messages", "fraction"):
            return None
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            return None
        if number <= 0 or (kind == "fraction" and number > 1):
            return None
        normalized.append((kind, int(number) if kind in ("tokens", "messages") else number))
    return normalized if allow_list else normalized[0]


def get_context_compression_triggers():
    """上下文压缩触发条件；列表内任一 tokens/messages/fraction 条件满足即触发。"""
    raw = os.environ.get("CONTEXT_COMPRESSION_TRIGGERS", "").strip()
    parsed = _parse_context_size(raw, allow_list=True) if raw else None
    if parsed:
        return parsed
    return [("fraction", get_context_compression_trigger_ratio())]


def get_context_compression_keep():
    """压缩后保留策略；支持 messages、tokens 或模型窗口 fraction。"""
    raw = os.environ.get("CONTEXT_COMPRESSION_KEEP", "").strip()
    parsed = _parse_context_size(raw) if raw else None
    if parsed:
        return parsed
    return ("messages", get_context_compression_preserve_recent_messages())


def get_context_summarization_mode():
    """上下文摘要策略：llm（默认、复用当前模型）或 heuristic（零额外调用）。"""
    raw = str(os.environ.get("CONTEXT_SUMMARIZATION_MODE", "llm")).strip().lower()
    return raw if raw in ("heuristic", "llm") else "llm"


def get_context_summarization_model():
    """可选的专用摘要模型；为空时复用当前 run model。"""
    return os.environ.get("CONTEXT_SUMMARIZATION_MODEL", "").strip()


def get_context_summarization_input_tokens():
    """摘要模型可接收的旧摘要 + 新历史预算，不含固定 prompt 文本。"""
    try:
        value = int(os.environ.get("CONTEXT_SUMMARIZATION_INPUT_TOKENS", "15564"))
    except ValueError:
        value = 15564
    return max(256, value)


def get_run_events_enabled():
    """是否把主循环运行事件写入 append-only 事件流（JSONL）。

    默认开启：开销极小，且是后续升级的验证地基。设为 0/false/no 可关闭。
    """
    raw = str(os.environ.get("RUN_EVENTS_ENABLED", "1")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_run_events_dir():
    """运行事件流落盘目录；默认 sandbox/run_events。"""
    return os.environ.get("RUN_EVENTS_DIR", "").strip() or os.path.join("sandbox", "run_events")


def get_memory_provider_name():
    """长期记忆 backend 名称；默认 file（零配置文件型）。"""
    return os.environ.get("MEMORY_PROVIDER", "").strip().lower() or "file"


def get_memory_injection_mode():
    """记忆注入方式：'system'（现状，拼进 system prompt）或 'hidden_user'（降权为隐藏 user 段）。

    默认保持 'system' 以确保零行为变化；设为 'hidden_user' 可启用 deer-flow 风格的
    权限隔离（memory 作为数据而非最高指令）。
    """
    raw = str(os.environ.get("MEMORY_INJECTION_MODE", "system")).strip().lower()
    return raw if raw in ("system", "hidden_user") else "system"


def get_durable_context_enabled():
    """是否每轮注入 durable context（summary_text + delegation_ledger + skill_context + memory）。

    默认关闭，保持现状；开启后按 deer-flow 风格以隐藏低权限 user 段回注结构化上下文。
    当 MEMORY_INJECTION_MODE=hidden_user 时强制开启，避免 memory 已退出 system prompt，
    却因为 durable 通道关闭而在本轮完全不可见。
    """
    if get_memory_injection_mode() == "hidden_user":
        return True
    raw = str(os.environ.get("DURABLE_CONTEXT_ENABLED", "0")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_tool_sanitization_enabled():
    """兼容旧布尔开关：audit/enforce 都视为“已启用中间件”."""
    return get_tool_sanitization_mode() != "off"


def get_tool_sanitization_mode():
    """工具结果注入防护：off（关闭）/ audit（只上报）/ enforce（上报并中和）。"""
    raw = str(os.environ.get("TOOL_SANITIZATION_MODE", "")).strip().lower()
    if raw in ("off", "audit", "enforce"):
        return raw
    legacy = str(os.environ.get("TOOL_SANITIZATION_ENABLED", "0")).strip().lower()
    return "enforce" if legacy not in ("0", "false", "no", "off", "") else "off"


def get_memory_write_middleware_enabled():
    """是否启用“上下文压缩成功后”自动更新 memory 的 hook。默认关闭。

    注意：即使开启，默认文件型 provider 的 add() 仍是 no-op（只提供 hook 点），
    不会自动改写记忆文件；需要自定义 provider 才会真正萃取写入。
    """
    raw = str(os.environ.get("MEMORY_WRITE_MIDDLEWARE_ENABLED", "0")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


# ---------------------------------------------------------------------------
# deermem backend 私有旋钮（仅在 MEMORY_PROVIDER=deermem 时生效）。默认值对齐
# deer-flow DeerMemConfig，按 R-Agent 场景微调（见 memory_progress/）。
# ---------------------------------------------------------------------------
def get_memory_max_facts():
    """事实库容量上限；超出按 confidence 淘汰最低的。默认 200。"""
    try:
        n = int(os.environ.get("MEMORY_MAX_FACTS", "200"))
    except ValueError:
        n = 200
    return max(10, n)


def get_memory_fact_confidence_threshold():
    """新 fact 落盘的最小 confidence。默认 0.5（比 deer-flow 的 0.7 更宽松，首轮少漏写）。"""
    try:
        v = float(os.environ.get("MEMORY_FACT_CONFIDENCE_THRESHOLD", "0.5"))
    except ValueError:
        v = 0.5
    return max(0.0, min(v, 1.0))


def get_memory_max_injection_tokens():
    """记忆注入的 token/字符预算上限。默认 2000。"""
    try:
        n = int(os.environ.get("MEMORY_MAX_INJECTION_TOKENS", "2000"))
    except ValueError:
        n = 2000
    return max(100, n)


def get_memory_guaranteed_categories():
    """无论预算如何都优先注入的保底类别（逗号分隔）。默认 correction。"""
    raw = os.environ.get("MEMORY_GUARANTEED_CATEGORIES", "").strip()
    if raw:
        return [t.strip() for t in raw.split(",") if t.strip()]
    return ["correction"]


def get_memory_guaranteed_token_budget():
    """保底类别 fact 的 token/字符预算上限。默认 500。"""
    try:
        n = int(os.environ.get("MEMORY_GUARANTEED_TOKEN_BUDGET", "500"))
    except ValueError:
        n = 500
    return max(50, n)


def get_memory_staleness_enabled():
    """是否启用 staleness 自动复查（过期 fact 删除/续期）。默认关闭。"""
    raw = str(os.environ.get("MEMORY_STALENESS_ENABLED", "0")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_memory_consolidation_enabled():
    """是否启用 consolidation 自动合并（碎片 fact 合并）。默认关闭。"""
    raw = str(os.environ.get("MEMORY_CONSOLIDATION_ENABLED", "0")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_memory_governance_interval_days():
    """自动整理 memory 的最小间隔天数。默认 3 天。"""
    try:
        value = float(os.environ.get("MEMORY_GOVERNANCE_INTERVAL_DAYS", "3"))
    except ValueError:
        value = 3.0
    return max(0.0, value)


def get_memory_session_facts_enabled():
    """是否启用 session 级工作记忆：保存 user/project/task transient descriptive facts，
    可检索、session 结束即消失。默认开启；不写入 durable 库。设 0 关闭。
    """
    raw = str(os.environ.get("MEMORY_SESSION_FACTS_ENABLED", "1")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_memory_session_fact_confidence_threshold():
    """Session fact 最低置信度。默认 0.3，低于 durable 阈值以减少工作事实漏写。"""
    try:
        value = float(os.environ.get("MEMORY_SESSION_FACT_CONFIDENCE_THRESHOLD", "0.3"))
    except ValueError:
        value = 0.3
    return max(0.0, min(value, 1.0))


def get_memory_session_max_facts():
    """单个 session 最多保留的事实数。默认 100。"""
    try:
        value = int(os.environ.get("MEMORY_SESSION_MAX_FACTS", "100"))
    except ValueError:
        value = 100
    return max(10, value)


def get_memory_staleness_age_days():
    """fact 超过多少天成为 staleness 候选。默认 90。"""
    try:
        n = int(os.environ.get("MEMORY_STALENESS_AGE_DAYS", "90"))
    except ValueError:
        n = 90
    return max(1, n)


def get_memory_staleness_max_removals_per_cycle():
    """单个 staleness 周期最多删除多少 fact。默认 10。"""
    try:
        n = int(os.environ.get("MEMORY_STALENESS_MAX_REMOVALS_PER_CYCLE", "10"))
    except ValueError:
        n = 10
    return max(1, n)


def get_memory_staleness_protected_categories():
    """staleness/consolidation 免疫的保护类别（逗号分隔）。默认 correction。"""
    raw = os.environ.get("MEMORY_STALENESS_PROTECTED_CATEGORIES", "").strip()
    if raw:
        return [t.strip() for t in raw.split(",") if t.strip()]
    return ["correction"]


def get_memory_staleness_max_extension_days():
    """续期后 expected_valid_days 的绝对上限。默认 3650。"""
    try:
        n = int(os.environ.get("MEMORY_STALENESS_MAX_EXTENSION_DAYS", "3650"))
    except ValueError:
        n = 3650
    return max(1, n)


def get_memory_staleness_max_lifetime_multiplier():
    """创建时 expected_valid_days 的钳制倍数（staleness_age_days * multiplier）。默认 20。"""
    try:
        v = float(os.environ.get("MEMORY_STALENESS_MAX_LIFETIME_MULTIPLIER", "20.0"))
    except ValueError:
        v = 20.0
    return max(1.0, v)


def get_memory_consolidation_min_facts():
    """单类别 fact 数达到多少才触发合并复查。默认 8。"""
    try:
        n = int(os.environ.get("MEMORY_CONSOLIDATION_MIN_FACTS", "8"))
    except ValueError:
        n = 8
    return max(3, n)


def get_memory_consolidation_max_groups_per_cycle():
    """单周期最多合并多少组。默认 3。"""
    try:
        n = int(os.environ.get("MEMORY_CONSOLIDATION_MAX_GROUPS_PER_CYCLE", "3"))
    except ValueError:
        n = 3
    return max(1, n)


def get_memory_consolidation_max_sources():
    """单组最多合并多少源 fact。默认 8。"""
    try:
        n = int(os.environ.get("MEMORY_CONSOLIDATION_MAX_SOURCES", "8"))
    except ValueError:
        n = 8
    return max(2, n)


def get_loop_detection_enabled():
    """委派子 Agent 是否启用循环保护（连续相同工具调用检测）。

    默认**开启**：子任务自动执行、无人盯着，死循环风险高，循环保护是安全增强。
    设为 0/false 可关闭。
    """
    raw = str(os.environ.get("LOOP_DETECTION_ENABLED", "1")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_loop_detection_threshold():
    """连续多少次相同工具调用（同名+同参）判定为循环并阻止；最小 2，默认 3。"""
    try:
        n = int(os.environ.get("LOOP_DETECTION_THRESHOLD", "3"))
    except ValueError:
        n = 3
    return max(2, n)


def get_deferred_tools_enabled():
    """是否启用延迟工具暴露（deferred tools）：prompt 先给工具目录，模型用 tool_search 提升。

    默认**关闭**：R-Agent 默认工具数不多，全量暴露更省事、零行为变化。工具很多或接入
    大量 MCP 工具时开启，可显著降低上下文占用。
    """
    raw = str(os.environ.get("DEFERRED_TOOLS_ENABLED", "0")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_deferred_tools_always_on():
    """延迟暴露模式下始终可见的工具名（逗号分隔）。

    默认保留"每轮都可能立刻要用"的日常工具（文件/命令/网页/调度/技能发现/记忆），
    延迟暴露只藏起"体量大且专用"的工具（AutoResearch 套件、语音、skill 管理、artifact
    切片、self-evolution 等）——它们经 tool_search 按需提升即可。可用环境变量覆盖。
    """
    raw = os.environ.get("DEFERRED_TOOLS_ALWAYS_ON", "").strip()
    if raw:
        return [t.strip() for t in raw.split(",") if t.strip()]
    return [
        # 检索入口（必需，否则无法发现被延迟的工具）
        "tool_search",
        # 调度骨架
        "todo_manage", "delegate_task",
        # 技能发现（小体量，用于找到并阅读技能）
        "skill_search", "skill_view", "skill_activate",
        # 核心文件 / 命令 / 代码执行
        "read_file", "write_file", "search_files", "run_command", "run_python",
        # 核心网页
        "web_search", "web_extract",
        # 核心记忆
        "memory", "memory_search", "memory_review",
    ]


def get_session_sandbox_enabled():
    """Whether the per-session sandbox compatibility layer is enabled."""
    raw = str(os.environ.get("SESSION_SANDBOX_ENABLED", "0")).strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def get_session_sandbox_root():
    """Root for opt-in per-session sandboxes."""
    return os.environ.get("SESSION_SANDBOX_ROOT", "").strip() or os.path.join("sandbox", "sessions")


def create_llm_client(api_key=None):
    """
    根据环境变量统一创建 LLM 客户端。
    支持 openai 和 azure 两种客户端。
    """
    key = api_key or get_api_key()
    client_type = get_client_type().lower()
    
    if client_type == "azure":
        from openai import AzureOpenAI
        import uuid
        return AzureOpenAI(
            api_key=key,
            api_version=get_azure_api_version(),
            azure_endpoint=get_azure_endpoint(),
            default_headers={"X-TT-LOGID": uuid.uuid4().hex},
            timeout=get_llm_request_timeout(),
        )
    else:
        from openai import OpenAI
        base_url = get_openai_base_url()
        kwargs = {"api_key": key, "timeout": get_llm_request_timeout()}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)
