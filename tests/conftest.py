"""Pytest 共享夹具：让测试与部署用的 .env 开关解耦。

背景：``core/config.py`` 在 import 时会 ``load_dotenv(.env)``，把部署环境的开关
（如 ``DEFERRED_TOOLS_ENABLED=1``）带进 ``os.environ``。而绝大多数测试是针对**代码
默认行为**编写的（默认所有行为改变型开关都关）。若不隔离，改一次 .env 就可能让一批
无关测试变红。

这里用一个 autouse 夹具，在每个测试开始前把"会改变对话行为"的部署开关清空，使测试
统一跑在代码默认之上。需要验证某开关的测试自行用 ``monkeypatch.setenv`` 显式打开
（会覆盖本夹具），因此不受影响。
"""

import pytest

# 只清"会改变对话行为"的开关；不动 RUN_EVENTS/LOOP_DETECTION 等纯增强项，
# 也不动 API key、模型名等部署必需配置。
_BEHAVIOR_TOGGLES = [
    "DEFERRED_TOOLS_ENABLED",
    "DEFERRED_TOOLS_ALWAYS_ON",
    "DURABLE_CONTEXT_ENABLED",
    "MEMORY_INJECTION_MODE",
    "TOOL_SANITIZATION_ENABLED",
    "TOOL_SANITIZATION_MODE",
    "MEMORY_PROVIDER",
    "MEMORY_WRITE_MIDDLEWARE_ENABLED",
    "CONTEXT_SUMMARIZATION_MODE",
    "CONTEXT_SUMMARIZATION_MODEL",
    "CONTEXT_SUMMARIZATION_INPUT_TOKENS",
    "CONTEXT_COMPRESSION_TRIGGERS",
    "CONTEXT_COMPRESSION_KEEP",
    "DELEGATE_STEP_EVENTS_LIMIT",
    "SESSION_SANDBOX_ENABLED",
    "SESSION_SANDBOX_ROOT",
    "R_AGENT_TOOL_OUTPUTS_DIR",
    "R_AGENT_DELEGATE_CONTEXTS_DIR",
]


@pytest.fixture(autouse=True)
def _neutralize_behavior_toggles(monkeypatch):
    """默认把行为改变型开关清空，让测试跑在代码默认之上（可被 setenv 覆盖）。"""
    for key in _BEHAVIOR_TOGGLES:
        monkeypatch.delenv(key, raising=False)
    yield
