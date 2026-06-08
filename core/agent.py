import time
import random
from core import config
from tools.registry import registry


# 标记：当一次 run 因迭代上限而被强制收尾时，agent 把这个标记记到自身
# 状态上，CLI 层可据此询问用户是否扩展预算继续推进。
_TRUNCATED_FLAG = "_truncated"
_PENDING_USER_MSG = "_pending_user_message"


def _is_transient_error(exc: Exception) -> bool:
    """
    判断异常是否属于"瞬时错误"，值得重试。
    覆盖：超时、连接错误、限流(429)、服务端错误(5xx)。
    内容策略/鉴权/参数错误(4xx，非 429) 一律不重试 —— 重试也不会成功，
    只会浪费 token 与时间。
    """
    # openai SDK 的特定异常类型（按需懒导入避免硬依赖）
    try:
        from openai import APITimeoutError, APIConnectionError, RateLimitError, InternalServerError  # type: ignore
        if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError)):
            return True
    except ImportError:
        pass

    # 兜底：通过 status_code 属性判断
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    if status is None:
        # 部分 SDK 把 response 挂在 .response.status_code 上
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
    if isinstance(status, int):
        if status == 429 or 500 <= status < 600:
            return True
        return False

    # 无法判断类型，但消息里出现典型瞬时关键字 —— 保守地重试一次
    msg = str(exc).lower()
    transient_markers = ("timeout", "timed out", "connection reset",
                         "connection aborted", "temporarily unavailable",
                         "bad gateway", "service unavailable")
    return any(m in msg for m in transient_markers)


def _format_llm_error(exc: Exception) -> str:
    """提取错误中最有用的信息，给用户一个可读的提示。"""
    # 尝试从结构化响应里挖出 message
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            data = resp.json()
            inner = data.get("error", {}).get("message")
            if inner:
                return inner
        except Exception:
            pass
    return str(exc)


class RAgent:
    """
    R-Agent 的核心控制器，对应 hermes-agent 中的 run_agent.py (AIAgent)。
    维护多轮对话状态和工具调用的生命周期循环。

    迭代预算策略：
      - 软提醒：达到 max_iterations * SOFT_WARN_RATIO 时，向 messages 注入一条
        system 提示，让模型主动收敛、避免发散式工具调用。
      - 强制收尾：到达最后一轮时，禁用 tools 再请求一次，强制模型输出文本
        总结 + 未完成清单。
      - 截断标记：把截断状态记录在 self 上，并保留完整 messages 历史。CLI 层
        可调用 continue_after_truncation(extra_iterations) 直接续跑，无需让
        用户重发问题，也不会丢失上下文。
    """

    def __init__(self, model=None, max_iterations=None):
        self.model = model or config.get_model()
        self.max_iterations = max_iterations or config.get_max_iterations()
        # 记录默认预算；续跑可以临时扩展，但下一次新对话会恢复，避免预算永久膨胀。
        self._default_max_iterations = self.max_iterations
        self._active_exclude_tools = set()
        # 统一使用 config 模块创建配置好的客户端 (支持 Azure 等)
        self.client = config.create_llm_client()
        self.messages = []
        # 截断状态：bool。被强制收尾后置 True，下次正常 run 前会自动复位。
        setattr(self, _TRUNCATED_FLAG, False)
        # 软提醒幂等标记：避免在同一段 run 里重复注入 system 提示。
        self._soft_warned = False

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _chat_completion_with_retry(self, on_think=None, iteration=None, **kwargs):
        """
        包装 client.chat.completions.create，对瞬时错误自动指数退避重试。
        非瞬时错误（如内容策略 cyber_policy / 鉴权 / 参数错误）直接抛出。
        """
        max_retries = config.get_llm_max_retries()
        base_delay = config.get_llm_retry_base_delay()

        last_exc = None
        for attempt in range(max_retries + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as e:
                last_exc = e
                if attempt >= max_retries or not _is_transient_error(e):
                    raise
                # 指数退避 + 抖动，避免与同伴请求形成同步重试风暴
                delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
                if on_think and iteration is not None:
                    try:
                        on_think(iteration, retry_attempt=attempt + 1,
                                 retry_max=max_retries,
                                 retry_delay=delay,
                                 retry_reason=_format_llm_error(e))
                    except TypeError:
                        # 旧版 on_think 不支持额外 kwargs，退化到普通调用
                        on_think(iteration)
                else:
                    print(f"[retry {attempt + 1}/{max_retries}] 模型瞬时错误: "
                          f"{_format_llm_error(e)}，{delay:.1f}s 后重试...")
                time.sleep(delay)
        # 不应到达这里，但出于完整性
        raise last_exc  # type: ignore[misc]

    def _inject_soft_warning(self, used: int, total: int):
        """注入一条软提醒，让模型主动收敛。"""
        warn = (
            f"【系统提醒】你已使用 {used}/{total} 轮思考预算。"
            "请评估当前任务进度，优先合并/收敛工具调用，避免发散式探索。"
            "如果剩余信息已足够，请尽早给出最终答复。"
        )
        self.messages.append({"role": "system", "content": warn})

    def _force_finalize(self, used: int, total: int, on_think=None) -> str:
        """
        最后一次请求：禁用 tools，要求模型输出文本总结与未完成清单。
        即使模型仍想调用工具，由于没有 tools 字段，它只能输出文本。
        """
        finalize_hint = (
            f"【系统强制收尾】你已用尽 {used}/{total} 轮思考预算，本轮不再"
            "提供任何工具。请：\n"
            "1) 用一段简短自然语言给出当前能给出的最佳答复；\n"
            "2) 在「未完成事项」小节中列出仍需继续的子任务（如果有）；\n"
            "3) 在「建议下一步」小节给出用户可以做的明确选择"
            "（例如：扩展预算继续 / 缩小问题范围 / 提供更多信息）。"
        )
        self.messages.append({"role": "system", "content": finalize_hint})

        if on_think:
            on_think(used)

        try:
            response = self._chat_completion_with_retry(
                on_think=on_think,
                iteration=used,
                model=self.model,
                messages=self.messages,
            )
        except Exception as e:
            return f"模型强制收尾失败: {_format_llm_error(e)}"

        message = response.choices[0].message
        self.messages.append(message)
        return message.content or "(模型未返回文本)"

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run_conversation(self, user_message: str, system_message: str = None,
                         on_think=None, on_tool_start=None, on_tool_end=None,
                         exclude_tools=None) -> str:
        """核心对话循环 (The Agent Loop)"""
        if system_message and not any(m.get("role") == "system" for m in self.messages):
            self.messages.append({"role": "system", "content": system_message})

        self.messages.append({"role": "user", "content": user_message})

        # 新一轮 run，复位截断/软提醒标记，并恢复默认预算。
        # continue_after_truncation 只应影响当前被截断任务，不应永久抬高后续对话预算。
        self.max_iterations = self._default_max_iterations
        setattr(self, _TRUNCATED_FLAG, False)
        self._soft_warned = False
        self._active_exclude_tools = set(exclude_tools or [])

        return self._loop(start_iteration=0, on_think=on_think,
                          on_tool_start=on_tool_start, on_tool_end=on_tool_end,
                          exclude_tools=self._active_exclude_tools)

    def continue_after_truncation(self, extra_iterations: int,
                                  on_think=None, on_tool_start=None,
                                  on_tool_end=None, exclude_tools=None) -> str:
        """
        在被强制截断后，由 CLI 询问用户并扩展预算后调用，直接续跑。
        不会让用户重新输入问题，messages 历史完整保留。
        """
        if extra_iterations <= 0:
            return "未扩展迭代预算，已保留当前结果。"

        # 在历史中追加一条 user 风格的指令：让模型继续推进未完成事项
        self.messages.append({
            "role": "user",
            "content": (
                f"【用户决定扩展 {extra_iterations} 轮思考预算】"
                "请基于上面的「未完成事项」继续推进；如已无可推进事项，请直接给出最终答复。"
            ),
        })
        self.max_iterations += extra_iterations
        setattr(self, _TRUNCATED_FLAG, False)
        self._soft_warned = False

        # 从「之前已用满的轮数」继续计数
        used_before = self.max_iterations - extra_iterations
        active_exclude_tools = self._active_exclude_tools if exclude_tools is None else set(exclude_tools or [])
        self._active_exclude_tools = set(active_exclude_tools)
        return self._loop(start_iteration=used_before, on_think=on_think,
                          on_tool_start=on_tool_start, on_tool_end=on_tool_end,
                          exclude_tools=active_exclude_tools)

    def is_truncated(self) -> bool:
        return bool(getattr(self, _TRUNCATED_FLAG, False))

    # ------------------------------------------------------------------
    # 真实循环
    # ------------------------------------------------------------------
    def _loop(self, start_iteration: int, on_think=None,
              on_tool_start=None, on_tool_end=None, exclude_tools=None) -> str:
        soft_threshold = max(1, int(self.max_iterations * config.get_soft_warn_ratio()))
        iteration = start_iteration
        excluded = set(exclude_tools or [])

        while iteration < self.max_iterations:
            # 软提醒（一次性）
            if not self._soft_warned and iteration >= soft_threshold:
                self._inject_soft_warning(iteration, self.max_iterations)
                self._soft_warned = True

            tools = registry.get_all_schemas()
            if excluded:
                tools = [
                    schema for schema in tools
                    if schema.get("function", {}).get("name") not in excluded
                ]
            kwargs = {"model": self.model, "messages": self.messages}
            if tools:
                kwargs["tools"] = tools

            if on_think:
                on_think(iteration)
            else:
                print(f"[{iteration}] 正在思考 (请求大模型)...")

            try:
                response = self._chat_completion_with_retry(
                    on_think=on_think,
                    iteration=iteration,
                    **kwargs,
                )
            except Exception as e:
                return f"模型请求失败: {_format_llm_error(e)}"

            message = response.choices[0].message
            self.messages.append(message)

            if message.tool_calls:
                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = tool_call.function.arguments

                    if on_tool_start:
                        on_tool_start(func_name, func_args)
                    else:
                        print(f"  [Tool Call] {func_name}({func_args})")

                    if func_name in excluded:
                        result = f'工具 {func_name} 已在当前上下文中被禁用，未执行。'
                    else:
                        result = registry.execute_tool(func_name, func_args)

                    if on_tool_end:
                        on_tool_end(func_name, result)
                    else:
                        print(f"  [Tool Result] {result}")

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": result,
                    })
                iteration += 1
                # 进入下一轮
            else:
                # 模型已给出最终答复
                return message.content

        # 达到上限：强制收尾
        finalized = self._force_finalize(iteration, self.max_iterations, on_think=on_think)
        setattr(self, _TRUNCATED_FLAG, True)

        prefix = (
            f"⚠️ **已达迭代上限 ({self.max_iterations} 轮)，以下为强制收尾结果。"
            "上下文已完整保留，可在下方选择是否扩展预算继续。**\n\n"
        )
        return prefix + finalized
