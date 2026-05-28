from core.agent import RAgent

def main():
    # 调大 max_iterations，因为复杂的依赖图需要多轮工具调用
    agent = RAgent(max_iterations=20)
    system_prompt = (
        "你是一个强大的 AI Agent，负责全局调度。\n"
        "【重要】面对特别复杂、包含多个步骤或依赖关系的任务时，请务必启用“父子协同”模式：\n"
        "1. 首先，使用 `todo_manage` 工具(action='init')创建包含拓扑依赖的 Todo List 计划。\n"
        "2. 然后，使用 `todo_manage` (action='view') 查看当前 ready_to_execute (可执行) 的任务。\n"
        "3. 【父进程的职责】：你绝对不要亲自执行具体的文件读写或代码编写！你只关注全局任务。请将 ready 的任务通过 `delegate_task` 工具委托给子智能体执行。\n"
        "4. 【跟进与调整】：子任务返回结果后，父进程必须根据执行结果使用 `todo_manage` (action='update') 标记其状态为 completed 或 failed，并记录 result 摘要。若子任务失败，父进程负责分析原因，并决定是重试该任务还是修改 Todo List。\n"
        "5. 不断循环执行“查看 ready 任务 -> 委托执行 -> 更新状态”，直到所有任务完成。\n"
        "请始终使用中文回复用户。"
    )
    
    user_message = "请帮我完成一个复杂任务：第一步，创建一个 data.txt 文件，里面写入 '10, 20, 30'。第二步，读取 data.txt 计算总和，将结果写入 sum.txt。第三步，读取 sum.txt 并根据结果写一句话的总结到 summary.txt。这三步有严格的依赖关系，请务必启用父子协同模式，使用 todo_manage 维护依赖拓扑，并在适当时候用 delegate_task 将就绪的任务分发给子智能体。"
    
    print("Sending user message:", user_message)
    response = agent.run_conversation(user_message, system_message=system_prompt)
    print("\nFinal Response:\n", response)

if __name__ == "__main__":
    main()
