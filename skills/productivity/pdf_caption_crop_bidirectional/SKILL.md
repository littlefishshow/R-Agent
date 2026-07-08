---
name: "pdf_caption_crop_bidirectional"
description: "PDF caption 图表双向裁剪与误裁修复流程"
---

# PDF Caption Crop Bidirectional Repair

## When to Use
- `read_paper` 或 `pdf_snapshot.py --mode smart` 生成的 Figure/Table 截图出现：表格只截半边、表格包含后续正文、Figure 漏掉上方图片却截入下方文字。
- 需要维护 `skills/productivity/read_paper/scripts/pdf_snapshot.py` 的 caption-based 自动裁剪逻辑。

## Procedure
1. 用 PyMuPDF 检查目标页的 block 结构：caption block、text block、image block、bbox。重点打印 `page.get_text("dict")` 中每个 block 的 `type`、`bbox` 和文本前缀。
2. 对每个 caption 同时生成 `above` 与 `below` 候选框，而不是只按 Figure/Table 默认方向裁剪。
3. Figure 优先检测 caption 目标侧的 raster image blocks：如果 image block 与 caption 在 x 方向有足够重叠、且垂直距离在合理阈值内，直接 union 图片 block 与 caption；这能修复“图片和 caption 间隔较大导致只截 caption+下方正文”的情况。
4. Table 的横向窗口不要只用 caption 文本宽度；应估计页面主内容 x-window，避免宽表右侧列被 caption 宽度裁掉。
5. Table 的纵向处理优先走文本块语义裁剪：用 `caption + 相邻非正文 text blocks` 组成裁剪框，遇到下一段 prose、section heading、下一 caption 或明显大间隙即停止。这样能处理 Table 7/8 这种短表格后面紧贴正文的布局。
6. 表格文本块语义判断必须保护 table-like blocks：带公式/数学符号、百分号、区间、`arg max`、`top-k`、紧凑指标名、缩进明显的数字行，不应被误判为正文。否则 Table 1/6 这类公式表会过早停止，随后 fallback 到 row-projection 又把下方正文并入。
7. 如果文本块语义裁剪失败，再 fallback 到 row-projection；Table 的 row-projection 合并阈值要比 Figure 小，减少把后续正文并入。
8. Caption 识别正则应要求编号后出现 caption-like delimiter（如 `:`, `.`, `|`, `—`, `-`），避免正文里的 “Table 4 summarizes ...” 被误识别为 caption。
9. 修改后用 `python3 -m py_compile` 验证脚本语法，再对出错页运行：
   ```bash
   python3 skills/productivity/read_paper/scripts/pdf_snapshot.py <pdf> --mode smart --pages <pages> --dpi 120 --output-dir sandbox/<test_dir>
   ```
10. 做回归验证：不仅检查新出错表格，还要同时跑之前修过的 Figure/Table，避免修 Table 7/8 时又破坏 Table 1/3/5/6 或 Figure 8。
11. 对最终阅读笔记中已有图片文件名，可重新输出到对应 assets 目录并覆盖/复制为原 Markdown 引用的文件名，避免额外改 Markdown。
12. 用 PIL 或其他图片工具验证输出 PNG 存在、尺寸合理；必要时仍可退回 `--mode crops --crops-json` 手动 bbox 精裁。

## Notes
- 对 RAGEN2 的验证样例：Table 3 宽表被 caption 宽度裁半，Table 5 被后续正文并入，Figure 8 因图片和 caption 间距较大而漏截图片，Table 7/8 因短表和下方 prose 间距过小而被 row-projection 一起合并，Table 1/6 因公式/符号型表格行被误判为正文导致 fallback 后截入下文。双向候选 + image block + 表格文本块语义截断 + table-like block 保护可覆盖这些情况。
- 输出到 `outputs/papers_output/.../assets/...` 时，Markdown 通常使用相对链接 `assets/<pdf_stem>/...`；不要把绝对式 `outputs/papers_output/...` 写入笔记。