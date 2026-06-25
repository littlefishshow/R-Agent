from core.agent import RAgent


def test_archive_subtask_compresses_messages():
    agent = RAgent(model="test", max_iterations=2)
    agent.messages = [
        {"role": "system", "content": "base"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "long"},
        {"role": "tool", "content": "detail"},
    ]
    agent._compress_after_archive("done", "next")
    assert len(agent.messages) == 3
    assert agent.messages[0]["content"] == "base"
    assert "done" in agent.messages[1]["content"]
    assert agent.messages[-1]["content"] == "task"
