---
name: "paper_note_targeted_correction"
description: "按原论文定点修正论文阅读笔记"
---

# Paper Note Targeted Correction

## When to Use
当用户指出某篇论文阅读笔记缺少/误写某个关键公式、算法步骤、实验细节，并要求“根据原论文回答并更新文档”时使用。

## Procedure
1. 定位笔记与原论文 PDF：优先用 `search_files` 查找指定笔记名和 PDF。
2. 读取现有笔记相关段落，确认缺口与已有表述，避免重复或覆盖无关内容。
3. 若已有全文抽取不可用，用 `PyMuPDF/fitz` 从 PDF 抽取全文到 `sandbox/read_paper/<paper>/fulltext_pymupdf.txt`；所有临时文本放在 `sandbox`。
4. 围绕用户问题检索原文关键词，如方法名、公式名、变量名、section heading、appendix heading；必要时打印相邻上下文而不是只看命中行。
5. 对照原文形成修正：
   - 明确原论文公式/定义；
   - 标注相对旧笔记的更正点；
   - 区分“原文确定内容”和“实现上可理解的组织方式”；
   - 避免把直观解释写成论文公式。
6. 更新 Markdown：将补充内容插入对应章节，同时更新公式索引/重检查记录；如果修正了旧错误，要显式说明“此前表述已修正”。
7. 验证：用 `read_file`/`grep` 检查新增内容、关键公式、表格索引和 Markdown 转义，特别注意 LaTeX 中 `\nabla`、`\right` 等不要被字符串转义破坏。

## Notes
- 对公式密集更新，优先用脚本进行块替换；替换后必须读取更新片段验证。
- 若用户问题涉及“如何计算/如何加权”，不要只给直觉，要定位论文中的 objective、系数定义和最终 actor/critic 影响。