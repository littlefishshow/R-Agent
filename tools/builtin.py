from tools.registry import registry

def add_numbers(a: float, b: float) -> float:
    """执行简单的加法"""
    return a + b

# 将该函数注册为 LLM 工具
registry.register(
    name="add_numbers",
    description="计算两个数字的和。当用户需要加法计算时调用此工具。",
    parameters={
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "第一个数字"},
            "b": {"type": "number", "description": "第二个数字"}
        },
        "required": ["a", "b"]
    },
    handler=add_numbers
)
