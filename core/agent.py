import os
from tools.registry import registry
from core.memory import memory_manager
from core.config import get_api_key, get_model, create_llm_client
from core.prompt_builder import build_system_prompt

class RAgent:
    """
    R-Agent 的核心控制器，对应 hermes-agent 中的 run_agent.py (AIAgent)。
    维护多轮对话状态和工具调用的生命周期循环。
    """
    def __init__(self, model=None, max_iterations=10):
        self.model = model or get_model()
        self.max_iterations = max_iterations
        
        # 从配置读取 API Key
        api_key = get_api_key()
        if not api_key:
            # Fallback for the case where config might not be fully initialized yet
            api_key = ""
            
        # 根据配置统一初始化客户端 (支持 OpenAI / AzureOpenAI)
        self.client = create_llm_client(api_key)
        self.messages = []
        self.context_memory = []  # 新增：用于存放子任务上下文压缩摘要

    def run_conversation(self, user_message: str, system_message: str = None,
                         on_think=None, on_tool_start=None, on_tool_end=None,
                         exclude_tools=None) -> str:
        """
        核心对话循环 (The Agent Loop)
        """
        # 构建 System Prompt
        base_system_prompt = build_system_prompt()
        if system_message:
            base_system_prompt += f"\n\n{system_message}"
            
        # 在 System Prompt 中注入冻结的记忆快照 (Frozen Snapshot)
        memory_snapshot = memory_manager.read_memory()
        if memory_snapshot:
            base_system_prompt += f"\n\n{memory_snapshot}"

        if base_system_prompt and not any(m.get("role") == "system" for m in self.messages):
            self.messages.append({"role": "system", "content": base_system_prompt})
            
        self.messages.append({"role": "user", "content": user_message})
        
        iteration = 0
        while iteration < self.max_iterations:
            tools = registry.get_all_schemas()
            if exclude_tools:
                tools = [t for t in tools if t["function"]["name"] not in exclude_tools]
            
            kwargs = {
                "model": self.model,
                "messages": self.messages,
            }
            if tools:
                kwargs["tools"] = tools
            
            if on_think:
                on_think(iteration)
            else:
                print(f"[{iteration}] 正在思考 (请求大模型)...")
                
            try:
                response = self.client.chat.completions.create(**kwargs)
            except Exception as e:
                return f"模型请求失败: {e}"
                
            message = response.choices[0].message
            
            # 将模型的原始响应添加到历史中
            self.messages.append(message)
            
            # 检查 LLM 是否发起了工具调用
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    func_name = tool_call.function.name
                    func_args = tool_call.function.arguments
                    
                    if on_tool_start:
                        on_tool_start(func_name, func_args)
                    else:
                        print(f"  [Tool Call] {func_name}({func_args})")
                    
                    # 调度执行对应的函数
                    result = registry.execute_tool(func_name, func_args)
                    
                    if on_tool_end:
                        on_tool_end(func_name, result)
                    else:
                        print(f"  [Tool Result] {result}")
                    
                    # 将工具执行结果作为 tool role 追加到对话历史，供下一轮 LLM 读取
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": func_name,
                        "content": result
                    })

                    # ======== 新增：子任务上下文压缩与迭代重置 ========
                    if func_name == "archive_subtask":
                        import json
                        try:
                            args_dict = json.loads(func_args)
                            summary = args_dict.get("summary", "")
                            if summary:
                                self.context_memory.append(summary)
                                print(f"  [Context Compressed] 已保存子任务摘要: {summary[:50]}...")
                        except:
                            pass
                        
                        # 保留最初的 System Prompt 和所有的 User Message，以及新生成的 context_memory
                        def get_role(m):
                            if isinstance(m, dict): return m.get("role")
                            return getattr(m, "role", None)

                        sys_msgs = [m for m in self.messages if get_role(m) == "system"]
                        user_msgs = [m for m in self.messages if get_role(m) == "user"]
                        
                        # 重新组装消息，丢弃中间所有的 tool/assistant 交互
                        self.messages = sys_msgs + user_msgs
                        
                        # 将累积的子任务摘要注入到新的 System Message 中
                        context_str = "\n".join([f"- {s}" for s in self.context_memory])
                        self.messages.append({
                            "role": "system",
                            "content": f"【系统提示：之前子任务的执行摘要，供参考】\n{context_str}"
                        })
                        
                        # 将迭代次数重置为 0，允许 Agent 继续无限制地执行后续子任务
                        iteration = 0
                        print(f"  [Iteration Reset] 当前会话已压缩，迭代计数重置为 0。")
                    # ===============================================

                # 若没有触发 archive_subtask，则正常累加迭代次数
                if func_name != "archive_subtask":
                    iteration += 1
                # 工具执行完毕，继续进入下一次 while 循环，模型会基于工具的返回继续思考
            else:
                # 如果没有 tool_calls，说明模型得出了最终结论，跳出循环
                return message.content
                
        return "Agent 运行达到最大迭代次数，已强制终止。"
