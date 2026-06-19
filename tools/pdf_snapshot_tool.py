"""Tool wrapper for read_paper PDF snapshot script."""

import importlib
from skills.productivity.read_paper.scripts import pdf_snapshot as _pdf_snapshot_module
from tools.registry import registry

_pdf_snapshot_module = importlib.reload(_pdf_snapshot_module)
pdf_snapshot_tool = _pdf_snapshot_module.pdf_snapshot_tool


registry.register(
    name="pdf_snapshot",
    description=(
        "将 PDF 页面、指定区域或自动识别到的 Figure/Table caption 附近区域渲染为 PNG 截图，"
        "用于论文阅读笔记中插入图表截图。默认输出到 outputs/papers_output/assets/<pdf_stem>/。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "pdf_path": {"type": "string", "description": "PDF 路径，必须在工作区内。"},
            "output_dir": {"type": "string", "description": "截图输出目录；默认 outputs/papers_output/assets/<pdf_stem>/。", "default": ""},
            "pages": {"type": "array", "items": {"type": "integer"}, "description": "1-based 页码列表；为空时 auto/pages 模式处理所有页。", "default": None},
            "crops": {"type": "array", "items": {"type": "object"}, "description": "裁剪区域列表：{page,x0,y0,x1,y1,units:'points'|'normalized',label?}；mode='crops' 必填。", "default": None},
            "mode": {"type": "string", "description": "smart/auto=按 Figure/Table caption + 邻近文本/像素内容自动精裁；pages=整页截图；crops=指定 bbox 裁剪。", "default": "auto"},
            "dpi": {"type": "integer", "description": "渲染 DPI，72-600，默认 200。", "default": 200},
            "include_tables": {"type": "boolean", "description": "auto 模式是否同时识别 Table caption。", "default": True},
            "caption_above_ratio": {"type": "number", "description": "legacy auto 裁剪时 caption 上方保留的页面高度比例；smart 模式不依赖该值。", "default": 0.48},
            "caption_below_ratio": {"type": "number", "description": "legacy auto 裁剪时 caption 下方保留的页面高度比例；smart 模式不依赖该值。", "default": 0.16},
            "max_auto_per_page": {"type": "integer", "description": "auto/smart 模式每页最多截取 caption 数。", "default": 8},
            "smart_crop": {"type": "boolean", "description": "auto 模式是否启用更紧的智能裁剪：caption 锚点 + 行投影内容分段，避免卷入 abstract/正文。", "default": True},
            "content_refine": {"type": "boolean", "description": "保留兼容参数；smart 模式会使用内容投影做局部精裁。", "default": True},
            "crop_margin": {"type": "number", "description": "智能裁剪边距，单位 PDF points，默认 8。", "default": 8.0},
        },
        "required": ["pdf_path"],
    },
    handler=pdf_snapshot_tool,
)
