import os
from core import config
from tools.registry import registry

class RAgent:
    """
    R-Agent 的核心控制器，对应 hermes-agent 中的 run_agent.py (AIAgent)。
    维护多轮对话状态和工具调用的生命周期循环。
    """
    def __init__(self, model=None, max_iterations=10):
        self.model = model or config.get_model()
        self.max_iterations = max_iterations
        # 统一使用 config 模块创建配置好的客户端 (支持 Azure 等)
        self.client = config.create_llm_client()
        self.messages = []

    def run_conversation(self, user_message: str, system_message: str = None,
                         on_think=None, on_tool_start=None, on_tool_end=None) -> str:
        """
        核心对话循环 (The Agent Loop)
        """
        if system_message and not any(m.get("role") == "system" for m in self.messages):
            self.messages.append({"role": "system", "content": system_message})
            
        self.messages.append({"role": "user", "content": user_message})
        
        iteration = 0
        while iteration < self.max_iterations:
            tools = registry.get_all_schemas()
            
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
                iteration += 1
                # 工具执行完毕，继续进入下一次 while 循环，模型会基于工具的返回继续思考
            else:
                # 如果没有 tool_calls，说明模型得出了最终结论，跳出循环
                return message.content
                
        return "Agent 运行达到最大迭代次数，已强制终止。"
