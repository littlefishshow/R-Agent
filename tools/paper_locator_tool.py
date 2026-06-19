"""Tool wrapper for read_paper paper locator script."""

import importlib
from skills.productivity.read_paper.scripts import paper_locator as _paper_locator_module
from tools.registry import registry

_paper_locator_module = importlib.reload(_paper_locator_module)
DEFAULT_OUTPUT_DIR = _paper_locator_module.DEFAULT_OUTPUT_DIR
DEFAULT_PAPERS_DIR = _paper_locator_module.DEFAULT_PAPERS_DIR
locate_paper_tool = _paper_locator_module.locate_paper_tool


registry.register(
    name="locate_paper",
    description=(
        "为 read_paper skill 定位论文并计算输出 Markdown 路径。默认递归搜索 outputs/papers，"
        "默认输出到 outputs/papers_output，并镜像论文相对目录结构。匹配策略保持简单：明确路径/日期/文件名关键词/类别目录。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "论文线索：日期、标题关键词、文件名片段或 arXiv ID。可为空。", "default": ""},
            "category": {"type": "string", "description": "可选类别目录名，如 agentic_rl；只做简单目录/路径匹配。", "default": ""},
            "papers_dir": {"type": "string", "description": "论文根目录，默认 outputs/papers。必须是工作区内相对路径。", "default": DEFAULT_PAPERS_DIR},
            "output_dir": {"type": "string", "description": "输出根目录，默认 outputs/papers_output。必须是工作区内相对路径。", "default": DEFAULT_OUTPUT_DIR},
            "limit": {"type": "integer", "description": "最多返回候选数量，默认 10。", "default": 10},
        },
        "required": [],
    },
    handler=locate_paper_tool,
)
