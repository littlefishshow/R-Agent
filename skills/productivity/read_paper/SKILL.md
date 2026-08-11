---
name: "read_paper"
description: "以研究者视角定位、精读、批判论文并沉淀中文研究笔记"
---

# Read Paper

## When to Use
- 用户想阅读 `outputs/papers/` 文件夹下的一篇论文，并要求快速抓住论文主线、方法、实验、图表、局限和可落地性。
- 用户指定论文 PDF/文本路径、日期、标题关键词、arXiv ID、作者、类别目录，或要求“帮我读这篇论文/总结这篇论文/精读论文”。
- 用户希望把论文阅读转化为后续研究判断：选题价值、核心假设、可复现性、baseline 公平性、可能的后续实验、可借鉴的工具/数据/写作方式。
- 用户需要将阅读结果沉淀到 `outputs/papers_output/` 文件夹，便于复查、对比、写综述或形成自己的研究记录。

## Researcher Mindset
读论文不是把论文压缩成摘要，而是训练研究品味与形成自己的问题判断。执行本技能时遵守以下原则：

1. **带着自己的问题读**：不要只吸收作者或热门方向给出的结论；先记录“我为什么读这篇、我希望它回答什么、它可能改变我的哪个判断”。
2. **先预测，再校正**：在阅读结果/实验前，基于标题、摘要、方法或图表先写下预测：作者会怎么做、结果可能强在哪里、可能失败在哪里。读完后回看预测，记录被纠正的地方。
3. **阅读原文而非二手总结**：优先读论文正文、图表、附录和局限性；不要用博客/帖子替代原文。附录、限制和实验细节常常包含真正关键的信息。
4. **重视旧资料和相邻领域**：把论文放回更长的知识谱系中，必要时指出它与经典思想、旧方法、相邻领域方法的关系，而不是只追逐最近热点。
5. **写下反证与不利证据**：像记录有利证据一样记录失败案例、负结果、异常现象、边界条件、作者没有解释的地方，避免只记住支持作者观点的内容。
6. **关注反馈循环和可验证性**：好论文应让人能快速复现实验或构造低成本 sanity check；阅读笔记要给出最小可验证实验、即用即抛版本或下一步复现建议。
7. **盯着输出而非只看指标**：下降的 loss 或更高的平均分不等于理解；要看原始样例、失败记录、错误类别、benchmark 文本和长尾现象。
8. **区分作者声称与我的判断**：所有批判性评价必须明确证据来源；不把自己的猜测写成论文结论，也不把作者声称当作已证事实。
9. **输出应服务研究复利**：阅读笔记不仅回答“论文讲了什么”，还要沉淀：可复用 idea、可借鉴实验设计、值得追踪的问题、未来 1-3 个可执行研究动作。

## Default Directories
- 默认论文目录：`outputs/papers/`
- 默认输出目录：`outputs/papers_output/`
- 用户可自行维护分类子目录；`read_paper` 必须把论文路径在 `outputs/papers/` 下的相对目录镜像到 `outputs/papers_output/`：
  - `outputs/papers/agent_RL/foo.pdf`
  - → `outputs/papers_output/agent_RL/foo_阅读笔记.md`
  - `outputs/papers/OPD/2604.13016.pdf`
  - → `outputs/papers_output/OPD/2604.13016_阅读笔记.md`
- 每个分类目录的图片资产放在该分类输出目录下：
  - `outputs/papers_output/agent_RL/assets/foo/<figure>.png`
  - Markdown 中引用为 `assets/foo/<figure>.png`。
- 中间/暂存文件（全文抽取、分块、OCR 临时文本、索引 JSON、调试日志等）不要放在 `outputs/papers_output/`；统一放在 `sandbox/read_paper/<paper_stem>/`，最终完成后可删除。`outputs/papers_output/` 只保留阅读笔记、用户明确需要的导出版和图片资产。

## Required Tool / Skill-local Scripts
- read_paper 专用能力默认采用 **skill-local scripts + `run_command`** 调用方式，避免把论文专用入口注册成全局 LLM tools，从而减少每轮 tool schema 干扰。
- 定位论文时通过 `run_command` 调用：
  ```bash
  python3 skills/productivity/read_paper/scripts/paper_locator.py "<query>" --category "<category>"
  ```
  该脚本默认递归搜索 `outputs/papers/`，默认输出到 `outputs/papers_output/`，并自动计算 Markdown 输出路径。匹配策略保持简单：明确路径/日期/文件名关键词/类别目录。
- 论文含关键 Figure/Table/Algorithm/案例图表时通过 `run_command` 调用：
  ```bash
  python3 skills/productivity/read_paper/scripts/pdf_snapshot.py <pdf_path> --mode smart
  ```
  默认根据 PDF 在 `outputs/papers/` 下的相对目录镜像输出到 `outputs/papers_output/<category>/assets/<pdf_stem>/`；**默认只截 Figure/Table/Algorithm/案例图表主体区域（含必要标题/图注），不得用整页截图冒充图表截图**。自动裁剪不理想或返回 suspicious/full-page 警告时，必须用 `--mode crops --crops-json '<json>'` 精裁；若暂时无法精裁，只能作为明确标注的降级资产使用，并在笔记中写明“降级整页/大区域截图，非最终图表主体裁剪”。
- `skills/productivity/read_paper/scripts/` 中的核心脚本：
  - `paper_locator.py`：论文定位与输出路径计算。
  - `pdf_snapshot.py`：PDF Figure/Table/Algorithm caption 定位、智能裁剪和渲染。
- 不再保留 `tools/paper_locator_tool.py` 与 `tools/pdf_snapshot_tool.py` 全局 wrapper；维护算法逻辑时只改 skill-local scripts。
- 每次确认要阅读某篇论文后，同时调用 `paper_research_scout` 的 skill-local read history 脚本登记该论文，避免后续论文调研重复推荐已经读过的文章：
  ```bash
  python3 skills/productivity/paper_research_scout/scripts/paper_read_history.py add --title "<paper title or inferred title>" --path "<outputs/papers/...pdf>" --arxiv-id "<arxiv-id-if-known>" --doi "<doi-if-known>" --url "<paper-url-if-known>" --category "<category>" --source read_paper
  ```
  该脚本写入 `skills/productivity/paper_research_scout/references/read_papers.json`，是 skill-local reference，不注册为全局工具。

## Inputs
- `query`：日期、标题关键词、文件名片段、arXiv ID 等，例如 `2025-02-20`、`STeCa`。
- `category`：可选类别目录，例如 `agent_RL`、`OPD`。
- 用户关注点：方法细节、公式、实验公平性、落地成本、某个图表、与某类 baseline 的差异、可复现性、后续研究方向等。
- 输出语言：默认中文。

## Paper Discovery Procedure
1. 若用户给出明确文件路径：直接使用该路径；输出路径仍尽量按 `outputs/papers` 到 `outputs/papers_output` 的相对路径镜像生成。
2. 否则通过 `run_command` 调用 `paper_locator.py`：
   - `query` 填用户给出的日期、标题关键词、arXiv ID 或文件名片段。
   - `category` 填用户给出的类别目录；没有则留空。
   - 默认 `papers_dir="outputs/papers"`，`output_dir="outputs/papers_output"`。
3. 处理 `paper_locator.py` 返回 JSON：
   - `status="unique"`：直接阅读 `selected.paper_path`，输出到 `selected.output_path`。
   - `status="ambiguous"`：如果上下文不能明显决定，列出候选路径让用户选择。
   - `status="no_match"` 或 `no_files`：告知没有找到，并列出使用的查询条件和默认目录。
4. 在 Markdown 基本信息中记录：论文路径、类别目录、输出路径、搜索线索。
5. 确认选定论文后，调用 `paper_research_scout/scripts/paper_read_history.py add` 登记已读历史；若只知道本地 PDF 路径，也必须至少记录 `--path`、推断标题和 `--category`。若知道 arXiv ID/DOI/URL，应一并记录，便于后续调研精确过滤。

## Output Requirements
- 每篇论文生成一份 Markdown 总结，保存到 `paper_locator.py` 返回 JSON 中的 `output_path`。
- 总结必须优先回答用户明确要求的信息；若用户没有额外指定，则按本技能模板完整输出。模板章节职责必须保持一致：`## 1` 是“小白友好版论文解释”（合并旧“一句话结论”和旧“全局扫描”职责），`## 3` 是“论文主线串读”（原第 4 章职责前移），不得再单独设置独立的扫描式概览章节。
- `## 1 小白友好版论文解释` 必须包含 `### 1.1` 到 `### 1.6` 六个小节：研究问题、核心方法直观理解与关键公式/代码解释、方法细节、实验结果、相比已有方法的优势与原因、代价或局限；如果涉及关键图片、表格、公式或代码片段，必须在第一章对应解释处就地插入/列出，不能只放到后文索引。
- 不确定的信息必须标注“论文未明确说明”或“未在当前可读内容中找到”，不得猜测。
- 引用图表、公式、实验结果时尽量标注原文页码、章节号、图号或表号。
- 论文中的简称/缩写/术语首次出现时必须给出完整名称与中文解释，推荐格式：`完整英文名（ABBR，中文解释）`；后文再次出现可使用简称，但关键章节标题、图表解释和结论中仍应尽量保留“全称 + 简称”以降低阅读负担。
- 公式必须使用正常 Markdown 数学环境：复杂/块级公式用独立的 double-dollar display math block，不要把复杂公式塞进 Markdown 表格或行内代码。
- **Method 必须讲清所有关键细节与公式**：论文方法部分不能只做高层概括；凡是作者用于定义方法、训练目标、推理流程、数据构造、评分/选择/更新规则、标签/critique/proxy/reward 构造、oracle/ground truth/evaluator/readout 来源、模块交互或理论结论的关键步骤、公式、变量和超参数，都必须在笔记中完整解释。若篇幅限制导致无法展开，必须明确标注“此处尚未展开/原文未明确/需要回看附录”，不得用一句直观描述替代关键机制。对于依赖过程标签、偏好标签、方向性 critique、verifier、reward model、人工/规则标注或 benchmark-native signal 的论文，必须单独回答“这些监督信号从哪里来、如何计算、训练/评测时是否可用、迁移到开放场景的成本是什么”。
- 必须保留“研究者阅读记录”：阅读动机、预读预测、读后校正、反证/不利证据、后续可执行实验。
- **强制重检查与最终验收**：初版 Markdown 生成后，必须将总结文件和论文原文/抽取文本重新对照读一遍，查漏补缺，并把补充/修正整合进最终 Markdown；复杂任务还要执行最终验收，验证关键数值、公式、截图、Markdown 格式和重检查记录。
- **输出目录保持整洁**：最终版生成后，`outputs/papers_output/<category>/` 不应残留 `extracted*`、`*_fulltext.txt`、`chunk_*.txt`、临时索引 JSON、OCR/debug 中间文件；这些文件只允许留在 `sandbox/read_paper/<paper_stem>/`，除非用户明确要求保留。

## Reading Procedure

### 0. 长论文任务拆解与上下文拆解规范
当论文很长、结构复杂或用户要求精读时，必须先拆解任务和上下文，再进入逐段阅读；不要把整篇长论文一次性塞进主上下文后直接生成笔记。长论文流程采用三层结构：**章节任务 + 证据/图表任务 + 机制复原/审稿任务**，防止退化为章节摘要拼接，并显式解决长文精读中的六类失败模式：信息瓶颈、拆分粒度过粗、缺少证据矩阵、图表没有硬门槛、最终内容级二次对照不足、跨章节一致性缺失。落地时采用 A-D 策略：A 章节任务（Section Tasks）负责局部深读和证据矩阵，B 图表任务（Evidence & Figure/Table Tasks）负责关键图表硬门槛与 ledger，C 机制复原任务（Mechanism Reconstruction Tasks）负责跨章节复原机制链，D 最终审稿任务（Final Review Tasks）负责最终内容级二次对照与跨章节一致性验收。

触发条件包括但不限于：
- **PDF 页数大于 20 页（>20）即视为长论文**，必须进入本节的长论文拆解流程。
- 主文虽短，但 Appendix / Supplementary Material / supplementary PDF 很长，或正文外材料包含关键方法、实验、prompt、超参、证明、失败案例。
- 图表、公式、算法框、prompt、实验设置、附录细节很多，导致单次阅读难以完整覆盖。
- 用户明确要求精读、复现、公式推导、实验公平性、代码对应关系或长文细读。
- 抽取文本、OCR 文本、截图索引或候选证据接近单轮上下文预算。

发现长论文时的任务治理要求：
- 如果原始请求只是一个简单 `read_paper` 任务（例如“读这篇论文/总结这篇论文”），发现满足长论文触发条件后，**不要硬塞全文到当前任务里继续完成**；应由父进程重新规划 `todo_list`，把索引、章节/机制阅读、证据/图表、汇总写作、内容审稿/验收拆成可调度任务。
- 子进程不得自行批准或调度拆分；子进程只能通过 `todo_manage propose_split` 提出拆分建议，等待父进程批准/调度。只有父进程批准后，才并行或分批执行子任务。
- 若当前执行者就是父进程，也应先重规划 todo_list，再按下述索引→章节/机制文件→证据账本→机制复原→最终汇总→内容审稿流程执行。

执行策略：
1. **先用 PDF 阅读/抽取工具建索引**：先定位 PDF，使用 PyMuPDF、pdftotext、OCR 或可用 PDF 阅读/抽取工具抽取目录、页码、章节标题、图表/公式/算法/附录位置，建立 `section_index.md/json`、图表/公式/算法候选索引，并写入 `sandbox/read_paper/<paper_stem>/`；主上下文只保留索引摘要、当前阶段目标、关键证据锚点和待解决问题。注意：`figure_table_index.md/json` 只能作为候选索引和截图任务输入，不能替代后续经过截图、质量检查、解释和去向确认的 `figure_table_ledger.md/json`。
2. **按章节保存独立阅读文件**：根据章节索引把每个章节或页码范围保存为独立文件到 `sandbox/read_paper/<paper_stem>/sections/`，文件名建议包含顺序号、章节名和页码范围。每个章节文件开头必须记录来源 PDF、页码范围、章节标题、抽取方式和已知图表/公式锚点。
3. **拆分粒度必须足够细**：章节任务必须细到“少数机制问题/少数实验问题/少数附录问题”，不能让一个任务吞掉多个信息密集模块（如完整 Method+Theory+Training 或完整 Experiments+Ablation+Cost）。若单章内部包含多个密集机制、训练目标、实验族或附录细节，必须从“章节拆分”升级为“机制专题任务/实验专题任务”。拆分任务的输入应指向 `sections/` 下的局部文件和必要图表材料，而不是整篇 PDF 全文。
4. **第一层：章节任务（Section Tasks）**：父进程批准后，子进程并行或分批阅读不同章节/专题文件。每个章节任务除 `*_summary.md` 与 `*_details.md` 外，**强制**在 `sandbox/read_paper/<paper_stem>/notes/` 或等价位置输出：
   - `*_claims.md`：本章节主张与 Claim-Evidence Matrix。
   - `*_figures_tables.md`：涉及的 Figure/Table/Algorithm/案例图、截图状态、解释和去向。
   - `*_formulas_algorithms.md`：公式、算法、伪代码、变量、输入输出和机制角色。
   - `*_missing_details.md`：未说明、未读到、冲突点、跨章节依赖、需要附录/原文回查的问题。
   - Claim-Evidence Matrix 必须含字段：`Claim` / `原文位置` / `图表公式` / `机制步骤` / `证据强度` / `未说明` / `汇总去向` / `跨章节依赖`。证据强度可用 强/中/弱/仅作者声称/未验证，但必须说明原因。
5. **第二层：证据/图表任务（Evidence & Figure/Table Tasks）**：图表任务独立设置且是硬门槛。必须提取关键 Figure/Table/Algorithm/案例图，调用 `pdf_snapshot.py` 或手工 crops 完成截图，检查裁剪质量（主体完整、非整页冒充、caption/必要标题清楚、路径可访问），逐项解释，并维护 `figure_table_ledger.md/json`。评测子任务必须截图主结果表、消融表、成本/效率表或等价证据；关键图表缺失时最终验收不得通过，除非在 ledger 和重检查记录中明确写明无法截取原因与替代证据（如原文表格转写、页码、数值回查）。`figure_table_index.md/json` 只是候选清单；只有 `figure_table_ledger.md/json` 才是最终合成和验收的图表账本。ledger 每项至少记录：图/表/算法编号、原文页码/章节、截图相对路径、裁剪质量状态、是否关键图表、解释摘要、最终 Markdown 插入章节/行文位置、缺失原因或替代证据。
6. **第三层：机制复原/审稿任务（Mechanism Reconstruction & Review Tasks）**：机制复原任务按问题横跨章节读取，而不是按章节摘要拼接。每个关键机制/实验结论必须复原：输入、输出、步骤、训练/推理差异、监督信号或 evaluator 来源、成本、失败模式、适用边界、依赖的图表/公式/附录证据；必要时生成 `mechanism_<topic>.md`。
7. **阶段性交接要可合并**：每个阶段应生成 `stage_summary.md`、`section_index.md/json`、`notes/*_summary.md`、`notes/*_details.md`、`notes/*_claims.md`、`figure_table_ledger.md/json`、`mechanism_*.md` 或等价摘要，记录已读范围、关键结论、证据锚点、未解决问题、章节间冲突、需要父任务统一口径的地方。长论文/多子任务交接必须额外生成固定机器可读的 `handoff_manifest.json`，放在 `sandbox/read_paper/<paper_stem>/` 或阶段输出根目录；它是汇总/机制/验收任务寻找材料的第一入口，不替代原始文件。manifest 至少包含：`final_note_path`、`asset_dir`、`figure_table_ledger_md`、`figure_table_ledger_json`、`section_index`、`claims_files`、`details_files`、`mechanism_files`、`ledger_item_count`、`image_count`、`missing_required_figures`、`validation_status`。`validation_status` 建议取 `draft` / `ready_for_synthesis` / `blocked` / `validated`，并在 `missing_required_figures` 非空或关键文件缺失时不得标为 `validated`。
8. **汇总子进程统一合成最终 Markdown**：汇总阶段**不得只读 `summary` 拼接**；必须先读取 `handoff_manifest.json`（若存在）定位 Claim-Evidence Matrix、figure/table ledger、关键 `details`、公式/算法记录和机制复原文件，并按 `claim → evidence → mechanism` 写最终笔记。若图表任务已完成，但汇总/机制阶段找不到 `figure_table_ledger.md/json`，必须依次回查 `handoff_manifest.json`、阶段 outputs、todo 摘要/result 中记录的路径；仍找不到则把任务标为 blocked/请求补交，不得静默跳过或用 `figure_table_index` 冒充 ledger。最终合成必须按 ledger 在正文对应论证位置就地插入关键图片（Markdown `![...](assets/<pdf_stem>/...)`），不能只写“见 Figure/Table x”或只保留文字引用；对于 ledger 中标为关键但未插图的项目，必须写明无法插入原因和替代证据。必须满足 `## 1 小白友好版论文解释` 的 1.1-1.6 要求，以及 `## 3 论文主线串读` 按行文顺序串联主文与附录的要求；同时统一术语、消除重复、检查章节结论冲突、补齐遗漏。
9. **最终审稿与内容级二次对照**：最终审稿任务必须逐条关键 claim 回查原文 section/页码/图表/公式/数值，检查作者声称 vs 事实证据、负结果、附录细节、空泛概括、数值/设置混淆和未说明处。父进程应抽查关键章节原文；若抽查发现关键证据缺失或 claim 与原文不符，必须退回补读/修正，不能最终验收。
10. **上下文预算优先**：当抽取文本或中间材料过长时，先写入 sandbox/artifact 并建立索引；当前对话只保留摘要、证据定位和下一步任务。禁止把整篇长论文全文、大段附录、整批 OCR 文本或所有子任务完整上下文复制到主对话。
11. **拆解不能降低质量门槛**：即使采用多阶段或多子任务阅读，也必须满足第 1 章小白解释、第 3 章主线串读、Appendix/Limitations 覆盖、图表/公式就地解释、强制重检查和最终验收要求。最终 Markdown 应保留“重检查记录/内容审稿记录”，必要时说明分块阅读范围、章节笔记来源与补漏情况。

### 1. 定位与抽取论文内容
1. 使用 `run_command` 调用 `paper_locator.py`，或根据明确路径选定论文文件，并确定输出 Markdown 路径。
2. 调用 `paper_research_scout/scripts/paper_read_history.py add` 登记该论文为已读/正在读，防止后续 `paper_research_scout` 重复推荐。登记失败不应阻塞阅读，但必须在最终说明中标注。
3. 抽取文本：PDF 优先使用 PyMuPDF、pdftotext 或 OCR/文档工具；若 PDF 是扫描件或图表文字缺失，使用 OCR/截图辅助理解。抽取全文、分块文本、索引 JSON 等中间文件必须写入 `sandbox/read_paper/<paper_stem>/`，不要写入 `outputs/papers_output/`。
4. 建立章节索引：记录 Abstract、Introduction、Related Work、Method、Experiments、Ablation、Analysis、Conclusion、Limitations、Appendix 等位置。
5. 建立术语/简称表：从标题、摘要、引言、方法和实验设置中抽取所有高频简称、方法名、数据集名、指标名、算法名和任务名；尽量回查原文首次定义，记录完整英文名、中文解释、所在章节/页码。若原文未展开，标注“原文未展开”。
6. 如果论文方法依赖任何中间监督或评测信号（如 step label、critique、proxy、reward、preference、verifier、judge、oracle、ground truth、confidence/belief readout、tool/evaluator signal），在章节索引阶段必须建立“监督信号来源表”：逐项记录信号名称、取值空间、由谁/什么产生、是否使用 ground truth 或 benchmark evaluator、训练时是否可见、公式/规则、所在主文与附录位置。不得只写“easy-to-obtain / rule-based / automatic”而不展开具体构造。
7. 如果全文过长，分阶段阅读：先做初步概览并草拟第 1 章，再按 Method/Experiments/Appendix/Limitations 深读。

### 1.5 术语、简称与符号消歧
论文阅读笔记必须主动降低缩写带来的理解成本：

- 对所有重要简称建立“术语与简称表”，至少包含：简称、完整英文名、中文解释、在本文中的具体含义、首次出现位置/证据。
- 首次出现时使用 `完整英文名（ABBR，中文解释）`，例如 `Multi-Agent Reinforcement Learning（MARL，多智能体强化学习）`。
- 对容易混淆的简称要额外说明边界，例如同一个缩写在不同领域可能有不同含义，本文具体指什么。
- 对方法名、数据集名、指标名、训练算法名、模型名、任务名都要尽量展开；如果原文没有展开，不要猜，写“原文未展开”。
- 图表解读和结论中不要只写一串简称；必要时重复全称，尤其是用户可能不熟悉的术语。

### 2. 预读记录：训练研究品味
在完整阅读实验结果之前，先写下：
- 我为什么读这篇论文？它关联到哪个研究问题、工程问题或知识缺口？
- 仅看标题/摘要/引言，我预测作者的核心假设是什么？
- 我预测方法会在哪些设置下有效/无效？
- 我预测实验最可能用哪些 baseline、数据集、指标和消融？
- 我最希望论文回答的 3 个问题是什么？

### 3. 初步扫描并写作输出第 1 章：小白友好版论文解释
重点阅读 Abstract、Introduction、Conclusion、Method 概览、主结果图表和 Limitations，目标不是生成旧版“全局扫描”独立章节，而是把旧版一句话结论与扫描式概览合并为输出 Markdown 的新 `## 1 小白友好版论文解释`。这一章要让没有读过原文、甚至不熟该方向的读者先建立正确直觉。

输出第 1 章必须按以下 1.1-1.6 写作，并在相关位置就地插入图片、公式、表格或代码解释：

1. `### 1.1 论文研究的问题`：说明领域背景、当前现状、论文具体要解决的问题、输入输出/成功标准、为什么这个问题重要且困难。不要只写一句话结论；要把“小白需要知道的前情”说清楚。
2. `### 1.2 核心方法及思想的直观理解和关键公式/代码解释`：用白话讲核心 idea、作者为什么会这么设计、它和人类直觉/简单例子的关系；若方法依赖关键公式、算法伪代码或代码逻辑，必须把公式/代码片段放在这里或对应位置，并逐项解释变量、输入输出和通俗含义。复杂公式使用 double-dollar display math block。
3. `### 1.3 方法细节仔细描述`：分步骤讲训练流程、推理流程、数据构造、模块交互、损失/奖励/选择/更新规则、超参数、监督信号来源、理论分析等。若论文有理论分析，必须说明定理/命题/假设/证明思路，并列出关键公式与每个符号的含义；不能用“作者证明了有效”带过。
4. `### 1.4 大体实验结果`：概括主实验、消融、分析和关键数值，说明数据集、指标、baseline、提升幅度和设置差异；若有关键表格/曲线，应在这里或对应说明处就地插入截图并解释。
5. `### 1.5 与已有方法相比好多少、为什么好、好在哪里`：回答相对谁提升、提升多少、在哪些场景最明显、为什么机制上可能更好；区分作者声称、实验支持和自己的判断，给出直观解释。
6. `### 1.6 代价或局限`：说明训练/推理/数据/标注/工具/外部依赖/理论假设/适用范围/失败案例/复现成本；若作者没有充分报告，要明确写“论文未明确说明”。

第 1 章写作要求：
- 先白话、再符号；先整体直觉、再方法细节。读者应能只读第 1 章就知道论文要解决什么、怎么做、大概强多少、为什么强、代价是什么。
- 图片、公式、表格和代码解释必须“就地服务理解”：例如方法概览图放在 1.2 或 1.3，主结果表放在 1.4，理论公式放在 1.3，不要让第一章只引用“见后文”。
- 仍需保留证据锚点：图号、表号、公式号、章节号、页码；不确定或未读到的细节必须显式标注。

### 4. 写作输出第 3 章：按行文顺序的完整主线串读 / 近似翻译式串读
输出 Markdown 的新 `## 3 论文主线串读` 是阅读笔记主体，继承原第 4 章职责。目标是让读者像跟着论文作者的论证路线读原文，而不是读一组彼此割裂的清单。**必须按论文实际行文顺序推进，并尽量完整描述每一节在说什么；重要段落可以采用近似翻译式串读。附录也不能漏，至少要覆盖 Appendix 中与方法细节、prompt/超参、数据统计、额外实验、限制、失败案例相关的内容。**

强制写作原则：
1. **先给读者路线牌，再进入细节**：在输出第 3 章开头用 3-7 句话说明全文路线，例如 `Introduction → Problem Definition → Method → Training Objective → Experiments → Ablation/Analysis → Limitations → Appendix`。这不是目录复述，而是告诉读者“作者为什么要这样安排，每一站解决什么问题”。
2. **按原文顺序完整串读**：从 Abstract/Introduction 开始，按论文主文和附录顺序推进；不要只挑 Method/Experiments。对于 Related Work、Limitations、Appendix 等容易被省略的部分，也要说明其论证作用和关键信息。若某些章节确实信息量低，可以压缩，但不能漏记为未读。
3. **每个节点都要有目的句**：小节开头先写一句“这一节要帮读者理解什么”。借鉴 ARS 的 Structure Architect 思路，每个节点都要说明 `Purpose / Content / Transition`：本节点承担什么作用，讲哪些核心内容，如何接到下一节点。
4. **用 TEEL/CER 写自然段，而不是填表**：重要段落优先按 `Topic/Claim → Evidence → Explanation → Link` 写成连续解释：先说本段主张，再放图表/公式/实验证据，接着解释证据为什么支持主张，最后接到下一段。列表只用于补充符号、数值、边界条件，不要让列表替代叙事。
5. **以连续段落为主，列表为辅**：每个节点先用自然段说明“上一节留下什么问题 → 本节如何推进 → 图表/公式提供什么证据 → 下一节为什么需要继续”，再用少量 bullet 补充符号、数值或边界。不要只写“原文位置/图表说明/我的判断”的表单式清单。
6. **图表和公式必须就地服务论证**：如果一张图是方法概览，就放在方法第一次被解释的位置；如果一张表是主结果，就放在实验主结果段落；如果公式定义训练目标，就放在作者从问题定义过渡到优化方法的段落。禁止把“图表索引”“公式索引”“方法清单”分别堆在不同位置后再让读者自行拼接。
7. **每个叙事节点都要回答连接问题**：
   - 这一节点承接了前文哪个问题？
   - 作者在这里引入了什么概念/假设/机制？
   - 图表、公式或实验结果如何支撑这个机制？
   - 这个证据是否充分，有没有材料缺口或替代解释？
   - 下一步自然引出什么？
8. **显式标出材料缺口，不要用常识硬补**：如果当前原文没有给出某个关键细节、页码、实验设置、失败案例或公式解释，写“论文未明确说明 / 当前可读内容未找到”，不要靠模型记忆或领域常识补成作者结论。可以在节点末尾写“需要回看附录/代码/补充材料”。
9. **保留可扫读锚点，但不要牺牲可读性**：小节标题可包含 `Sec.x / p.x / Figure x / Eq.x`，但正文必须是顺畅解释；公式后要立即解释符号和通俗含义；图表后要说明它在当前论证中的作用。
10. **控制段落长度和阅读节奏**：复杂机制用短句拆开；每个自然段尽量只承载一个主张。遇到长方法链时，先给一句白话版，再展开正式符号和细节。
11. **避免重复割裂**：后续总结、索引和批判章节可以做提炼，但不得替代第 3 章主线串读。第 3 章已经讲过的图表/公式，后文只回指和评价，不要重新孤立复述。

推荐节点结构（不是机械模板，写作时应合并成通顺段落）：
1. **节点目的**：一句话说明这一节点要让读者理解什么，例如“这里作者把直觉问题改写成可训练目标”。
2. **过渡句**：承接上一个节点，说明为什么作者需要进入当前部分。
3. **原文位置锚点**：章节号、页码、图号/表号/公式号。
4. **作者论证 / 近似翻译式串读**：这一段在整篇论文中承担什么作用；必要时按原文段落顺序逐段解释作者说了什么。
5. **就地插入图表/公式**：截图和公式紧跟解释出现。
6. **解释与判断**：解释符号、图表趋势、实验设置，并说明证据强弱、反例、边界和材料缺口。
7. **下一步引出**：用一句话说明作者接下来为什么要做下一节/下一组实验。

图表与公式都要服务主线：如果一张图只是方法概览，就放在方法流程解释处；如果一张表是主结果，就放在实验主结果解释处；如果一个公式定义训练目标，就放在对应训练步骤处。若某张图/公式跨多个论点，可首次出现处详细解释，后文只回指，例如“见 Figure 2 的 shared-parameter 分支”。

### 4.1 图表与截图的就地插入规则（服务输出第 1 章和第 3 章）
对论文中的图、表、算法框、流程图、案例和错误样本进行系统梳理，但输出时要嵌入第 1 章或第 3 章对应论证位置：
- 先用 `run_command` 调用 `pdf_snapshot.py` 为关键 Figure/Table/Algorithm/案例图表生成 PNG 截图；优先使用 `--mode smart`，结合 caption、相邻文本和像素内容自动精裁。
- **默认截图粒度是图表主体区域**：只截对应 Figure/Table/Algorithm/案例图表本身及必要标题/图注，不得把整页截图当作图表截图交付，也不要把大片正文/其他图表混入截图。
- 自动裁剪不理想、明显过大、包含整页或脚本返回 `full_page_snapshot` / `is_suspicious_large_crop` 时，必须用 `--mode crops --crops-json ...` 指定 bbox 精裁；若一时无法精裁，必须在 Markdown 图片说明和重检查记录中显式标注“降级整页/大区域截图，非最终图表主体裁剪”，并说明待精裁原因。
- 将截图保存到阅读笔记所在输出目录的 `assets/<pdf_stem>/` 下，例如 `outputs/papers_output/agent_RL/assets/foo/foo_p001_figure_1.png`；图片链接必须写成相对当前 Markdown 文件的 `assets/<pdf_stem>/xxx.png`，不要写成 `outputs/papers_output/...` 绝对式路径，否则移动分类目录后 Markdown 预览可能找不到图片。
- 图：在讲到相关方法/实验节点时插入截图，说明图号、标题、作者想表达的信息、与当前论证的关系。
- 表：在讲到对应结果或消融时插入截图，说明比较对象、指标、最佳结果、相对提升，以及作者分析是否充分。
- 对关键架构图/流程图：截图之外，必要时用 Mermaid 或 ASCII 重画一个更易懂的抽象流程，但重画图要跟原图截图放在同一叙事节点。
- 对重要曲线图：说明横轴、纵轴、趋势、拐点、作者用它证明什么，以及它和前后实验的关系。
- 对案例图/错误分析图：说明成功案例、失败案例、错误类型和边界条件。
- 对 benchmark 或模型输出：尽量阅读原始样例/日志/生成结果，而不只看平均分；记录最有信息量的长尾现象。

### 4.2 公式与数学定义的就地解释规则（服务输出第 1 章和第 3 章）
公式不要集中堆在“公式表”里，而要放在第 1 章小白解释或第 3 章主线串读中首次需要它的位置：
- 问题定义公式：放在任务定义/Preliminaries 对应段落。
- 方法概率分解：放在方法整体流程对应段落，并和架构图相互解释。
- 训练目标/损失函数：放在训练算法步骤处，并说明该目标如何驱动 agent/model 行为。
- 奖励函数：放在实验设置或奖励消融处，并解释可能诱发的 shortcut、角色反转或偏差。
- 理论分析公式：放在 Appendix/理论解释处，明确它是严格保证、直觉解释还是附录假设；若理论是论文关键贡献，也要在第 1.3 节给出详细公式与通俗解释。

每个公式至少包含：
1. 原文位置和公式号。
2. 块级公式。
3. 符号定义，包括简称/算法名/指标名的完整名称。
4. 公式在当前主线中的作用。
5. 一句话通俗理解。
6. 不确定或原文未定义清楚的地方。
7. **方法细节解释**：如果该公式参与论文方法，必须解释它在方法中的具体角色、所需输入、产生的中间量/输出、与前后步骤的关系，以及原文是否给出实现细节；不能只写“该公式用于优化/加权/对齐”等泛化表述。

### 5. 第 3 章主线串读必须覆盖的问题
在输出第 3 章串读中自然覆盖以下内容，而不是另起割裂清单：
- 问题定义：任务、输入、输出、训练/推理阶段分别需要的信息、成功标准/指标、为什么难、与相近任务或前人设定的区别。
- 核心假设：作者相信什么、证据来自哪里、为什么会想到这样做、与现有方法的思路差异、是否被实验充分验证、是否有反例或适用边界。
- 方法流程：方法分几步；每一步输入/输出是什么；各自做什么；哪一步是创新点；训练流程和推理流程是否不同。
- 最小可运行版本：如果要复现/验证这篇论文，最小数据、最小模型、最小脚本和最小 sanity check 是什么。
- 附录补充：Appendix 中的 prompt、超参、数据构造细节、证明、额外实验、失败案例、限制和任务特化设置。

### 6. 实验阅读：看它是否真的合理

#### 6.1 实验到底验证了什么
检查实验是否回答：方法有效吗；相比谁更强；为什么更强；哪些模块真的有用；在哪些场景有效/失效；代价是什么。

#### 6.2 Baseline 是否公平
检查：对比对象是不是当前强 baseline；是否和最接近的方法比较；参数规模、数据量、训练时长、资源、调参强度是否可比；是否偷偷用了更多数据、更大模型、更多算力、额外知识库或额外标注。若提升可能来自模型更大、数据更多、训练更久或挑选有利设定，要明确指出。

#### 6.3 Ablation 消融实验
检查：去掉模块后性能是否明显下降；每个新设计是否都贡献增益；多个模块叠加是否只是堆料；关键超参数是否敏感；若多个创新点中只有少数有效，要重新评估真实贡献。

#### 6.4 误差分析和案例分析
检查：模型在哪些样本上失败；错误类型；方法对哪类问题最有效；是否存在明显偏差；作者是否诚实讨论失败案例。若论文只展示漂亮案例、不展示失败样本，要保持警惕并写入总结。

#### 6.5 效率、成本和可扩展性
关注：训练成本、训练时长、硬件资源、推理延迟、吞吐、显存占用、数据需求、标注成本、是否依赖外部知识库/检索库/工具调用/私有数据、是否容易扩展到大规模场景、收益是否配得上成本。

#### 6.6 统计可信度与复现性
关注：随机种子、置信区间/方差、样本量、显著性、数据泄漏、评测污染、开源代码/数据、超参披露、prompt 披露、失败运行是否报告。若论文只有单次结果或缺少复现细节，要明确记录。

### 7. 附录、局限与旧思想连接
1. 读 Appendix/Limitations：补充正文省略的实验设置、prompt、超参、数据统计、额外消融、失败案例和限制。
2. 找知识谱系：记录它与经典方法、旧论文、相邻领域或通用研究原则的关系；必要时指出“新包装旧思想”或“旧思想在新条件下复活”。
3. 记录未被消化的问题：论文提出但没有解决、实验暗示但没有展开、值得后续追踪的想法。

### 8. 初版 Markdown 写入
1. 按推荐模板生成初版阅读笔记。
2. 写入 `paper_locator.py` 返回或明确路径推导出的输出路径。
3. 记录初版文件路径、行数和主要章节。


### 8.5 Markdown 数学公式与 KaTeX 安全规则
论文阅读笔记中经常需要重写公式。为了保证 GitHub/Obsidian/KaTeX/Markdown 渲染稳定，必须遵守：

1. **复杂公式使用块级数学环境**：

```markdown
$$
J(\theta)=\mathbb{E}_{(x,y^*)\sim\mathcal{D},\,y\sim\pi_\theta}[R(y,y^*)]
$$
```

2. **不要把复杂公式放进 Markdown 表格或行内代码**：表格里的 `|`、换行、反斜杠和尖括号容易破坏渲染。推荐用“小节标题 + double-dollar display math block + bullet 解释”的结构。
3. **避免尖括号历史下标**：某些渲染链会把 `<l>` / `<t>` 当成 HTML 标签片段，导致 KaTeX 公式被截断，并出现 `ParseError: Expected '\right', got 'EOF'`。
   - 不稳写法：`y_{<l}`、`\{m,y\}_{<t}`、`y_{i,t,<j}`。
   - 稳定写法：`y_{1:l-1}`、`\{m,y\}_{1:t-1}`、`y_{i,t,1:j-1}`。
4. **谨慎使用 `\left` / `\right`**：能用普通括号 `(...)`、`[...]`、`\{...\}` 时优先用普通括号；若使用 `\left`，必须有配对的 `\right`。
5. **用程序写入 LaTeX 时防止反斜杠转义**：Python 普通字符串会把某些 LaTeX 命令误当转义字符，导致 KaTeX 报错。典型灾难：`\theta` 变成 TAB + `heta`，`\text` 变成 TAB + `ext`，`\frac` 变成 FORM FEED + `rac`，`\arg` 变成 BEL + `rg`，`\right` 可能被写坏成 `ight`，最终出现 `Unexpected character: '\f'` 或 `Expected '\right', got 'EOF'`。生成含 LaTeX 的 Markdown 必须优先使用 raw string（如 `r'''...'''`）或对反斜杠写成 `\\`，写入后必须检查控制字符。
6. **公式修复流程**：若用户报告 KaTeX parse error，先搜索报错附近的 `<`、`>`、`\left`、`\right`、`\arg`、`\frac`、`\theta`、`\text` 和控制字符；然后把公式改成块级 double-dollar display math block，替换尖括号下标，修复被 Python 转义损坏的命令，检查 double-dollar 分隔符、花括号、`\left/\right` 配对。
   - 常见修复映射：TAB+`heta` → `\theta`；TAB+`ext` → `\text`；FORM FEED+`rac` → `\frac`；BEL+`rg` → `\arg`。
   - 注意：检查控制字符时不能把 TAB 当作允许字符，因为公式中的 TAB 往往就是 `\theta` / `\text` 被错误转义后的结果。

推荐验证命令：

```bash
python3 - <<'PY'
from pathlib import Path
import re
p = Path('outputs/papers_output/xxx_阅读笔记.md')
text = p.read_text()
delim = '$' * 2
print('display_math_delimiters', text.count(delim), 'unpaired?', text.count(delim) % 2 != 0)
print('unclosed_fences?', text.count('```') % 2 != 0, 'fence_count', text.count('```'))
blocks = text.split(delim)
errs = []
for idx in range(1, len(blocks), 2):
    block = blocks[idx]
    bal = 0
    for ch in block:
        if ch == '{': bal += 1
        elif ch == '}': bal -= 1
        if bal < 0: break
    if bal != 0:
        errs.append((idx // 2 + 1, 'brace_balance', bal, block.strip()[:120]))
    if '<' in block or '>' in block:
        errs.append((idx // 2 + 1, 'angle_bracket', block.strip()[:120]))
    lefts = len(re.findall(r'\\left\b', block))
    rights = len(re.findall(r'\\right\b', block))
    if lefts != rights:
        errs.append((idx // 2 + 1, 'left_right', lefts, rights, block.strip()[:120]))
ctrl = [(i, ord(c), repr(c)) for i, c in enumerate(text) if ord(c) < 32 and c not in '\n\r']
print('math_errors', errs)
print('control_chars', ctrl[:10])
print('tab_count', text.count('\t'), 'formfeed_count', text.count('\f'), 'bell_count', text.count('\a'))
PY
```

### 9. 重检查机制：总结文件 × 论文原文二次对照
初版 Markdown 生成后，必须执行一次重检查，目标是发现遗漏、误读、证据不足和结构缺口。

#### 9.1 重读对象
- 重新读取已生成的 Markdown 总结文件。
- 重新读取论文原文或抽取文本，至少覆盖：Abstract、Introduction、Conclusion、Method 全章、Experiments 主文、图表标题/图注/表格/算法框、Limitations、Appendix 中与实验设置/prompt/超参/额外消融/案例分析/限制相关的部分。

#### 9.2 对照检查清单
逐项检查 Markdown 是否遗漏或需要修正；长论文必须同时读取章节 Claim-Evidence Matrix、figure/table ledger、关键 `details` 和机制复原文件，不能只读 summary：
- 标题、作者、年份/会议、arXiv 版本、文件路径、类别目录、输出路径是否准确。
- 是否建立术语与简称表；重要简称首次出现是否给出完整英文名和中文解释；原文未展开的简称是否明确标注“原文未展开”。
- 阅读动机、预读预测、读后校正是否存在，是否能训练自己的研究判断。
- 问题定义、输入输出、成功标准是否与论文原文一致。
- 现有方法不足是否完整，是否误把自己的判断写成作者结论。
- 核心假设是否明确，是否有原文证据支撑。
- 方法步骤是否完整：数据构造、训练流程、推理流程、损失函数/奖励、算法细节是否缺失。
- 方法中的标签、critique、proxy、reward、verifier、oracle、judge、ground truth、readout 或 evaluator signal 是否逐项追踪来源、计算规则、可用阶段和迁移成本；是否查过附录中的 task-specific instantiation。
- 公式是否漏掉关键符号、符号定义、约束条件、超参数含义。
- 第 1 章是否包含 1.1-1.6，且把旧“一句话结论”和旧“全局扫描”职责合并为小白友好解释；是否在相关位置就地插入/列出关键图片、公式、表格或代码解释。
- 第 3 章主线、图表和公式是否按论文行文顺序完整串联（含附录），而不是分裂成“主线清单 / 图表清单 / 公式清单”三块，或残留独立扫描式概览章节。
- 每条关键 claim 是否能在 Claim-Evidence Matrix 中找到原文位置、图表/公式、机制步骤、证据强度、未说明项、汇总去向和跨章节依赖。
- 图表是否覆盖主文所有关键 Figure/Table/Algorithm/案例；表格数值是否抄错；提升幅度是否算错。长论文必须核对 `handoff_manifest.json` 中的 `ledger_item_count`、`image_count`、`missing_required_figures`、`validation_status` 与实际 ledger/final Markdown 是否一致。
- 关键 Figure/Table/Algorithm 是否已通过 `pdf_snapshot.py` 截图并就地插入到对应解释附近；截图路径是否存在，Markdown 链接是否相对当前笔记可访问（通常应为 `assets/<pdf_stem>/xxx.png`）；自动裁剪是否截到主体，必要时是否用 crops 精裁。评测类论文的主结果表、消融表、成本/效率表缺失时不得通过，除非 ledger 记录无法截取原因与替代证据。
- 关键公式是否就地出现在相关方法/实验/理论段落，而不是集中堆在公式表里；公式是否解释了它如何服务当前论证。
- Appendix 中是否有重要细节未纳入：数据统计、prompt 模板、训练超参、额外实验、更多消融、案例、限制；尤其是主文只抽象描述的标签/critique/proxy/reward 构造细节。
- Baseline 公平性判断是否有证据；是否遗漏计算预算、数据预算、模型规模、训练轮数。
- 消融是否足以支持每个创新点；是否遗漏负结果或弱结果。
- 误差分析是否只是漂亮案例；有没有失败案例、边界条件和偏差讨论。
- 是否看过原始输出/样例/benchmark 文本，而不是只复述平均指标。
- 效率、成本、可扩展性是否具体到训练/推理/数据/标注/外部依赖。
- 统计可信度和复现性是否被检查：种子、方差、显著性、数据泄漏、代码数据开源。
- 批判性总结是否区分“论文声称”和“我的判断”。
- 后续行动是否足够可执行：最小复现实验、低成本 sanity check、下一篇应读论文或下一组实验。
- Markdown 是否有空章节、重复章节、格式损坏、未闭合代码块、表格错位。
- 数学公式是否可被 KaTeX/Markdown 正常渲染：double-dollar 分隔符是否成对，公式块内是否残留 `<`/`>` 历史下标，`\left/\right` 是否配对，是否存在 Python 转义产生的控制字符。

#### 9.3 重检查输出方式
- 长论文重检查必须列出读取过的 evidence matrix、figure/table ledger、关键 details 和机制复原文件；若只读 summary，则重检查无效。
- 如果发现遗漏或错误：直接修改原 Markdown，总结文件应成为“重检查后最终版”。
- 在最终 Markdown 末尾追加 `## 重检查记录`，包含：重检查日期；对照范围；发现并补充/修正的要点；仍不确定或论文未明确说明的信息。
- 如果没有发现实质问题，也要追加记录：`未发现需要修改的关键遗漏；仅做格式/措辞检查`。
- 最终答复用户前，验证文件存在、行数合理，并说明已完成重检查。

### 9.4 最终验收与父进程二次对照
当阅读任务由多步/子任务完成，或已经生成初版/重检查版 Markdown 后，必须把最终验收并入 `read_paper` 流程，而不是另建独立论文检查技能：

1. 定位最终 Markdown、sandbox 中的抽取文本/索引和原 PDF。
2. 读取最终 Markdown 全文或关键章节，确认包含 `## 重检查记录`，并检查是否存在空章节、重复章节、未闭合代码块、表格错位、图片链接缺失、数学公式渲染风险。
3. 针对论文类型抽查关键证据，并做内容级二次对照：
   - 逐条关键 claim 回查原文 section/页码/图表/公式/数值，确认 Claim-Evidence Matrix 中证据强度与最终表述一致。
   - 主结果表格与附录表格的数值、设置差异，例如 strict/loose、ID/OOD、不同模型/数据集。
   - 方法公式、机制步骤、训练/推理差异、监督信号来源、硬件/推理成本、消融、局限、失败案例。
   - 作者声称 vs 事实证据是否区分；是否遗漏负结果、附录限制或把空泛概括写成已证事实。
4. 父进程应抽查关键章节原文；若发现关键 claim 无法定位到原文/图表/公式/数值，或关键图表 ledger 缺失且无替代证据，最终验收不得通过。若图表任务完成但验收阶段找不到 ledger，必须回查 `handoff_manifest.json`、outputs 和 todo 摘要；仍找不到则标记 blocked，不得静默继续。
5. 若发现遗漏、误读或可加强处，直接修改最终 Markdown；不要只在对话中说明。
6. 最终验证至少包括：文件存在、行数合理、字符数、Markdown 代码围栏闭合、double-dollar 数学分隔符成对、关键截图路径存在、figure/table ledger 完整、`image_count` 与 ledger coverage 硬门槛、内容审稿记录存在。只要 ledger 中存在截图路径或关键图表，`final_note_image_count` 不得为 0；关键图表覆盖率（已在正文就地插入或有明确替代证据的关键项 / ledger 关键项）必须达到 100%，否则不得通过。
7. 在最终答复中给出：最终文件路径、验证结果、最关键的二次确认点。

推荐最终验收命令：

```bash
python3 - <<'PY'
from pathlib import Path
import re
p = Path('outputs/papers_output/xxx_阅读笔记.md')
ledger_json = Path('sandbox/read_paper/xxx/figure_table_ledger.json')  # 按 handoff_manifest.json 实际路径替换
text = p.read_text()
delim = '$' * 2
image_links = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)
missing_images = []
for link in image_links:
    if '://' in link or link.startswith('#'):
        continue
    img = (p.parent / link).resolve()
    if not img.exists():
        missing_images.append(link)
final_note_image_count = len(image_links)
ledger_item_count = 0
critical_total = 0
critical_covered = 0
ledger_has_screenshot = False
if ledger_json.exists():
    import json
    data = json.loads(ledger_json.read_text())
    items = data.get('items', data if isinstance(data, list) else [])
    ledger_item_count = len(items)
    for it in items:
        screenshot = it.get('screenshot_path') or it.get('image_path') or it.get('path')
        is_critical = bool(it.get('is_critical') or it.get('required') or it.get('关键图表'))
        inserted = bool(it.get('final_note_inserted') or it.get('final_markdown_position') or (screenshot and Path(screenshot).name in text))
        alternative = bool(it.get('alternative_evidence') or it.get('missing_reason'))
        ledger_has_screenshot = ledger_has_screenshot or bool(screenshot)
        critical_total += int(is_critical)
        critical_covered += int(is_critical and (inserted or alternative))
coverage_ok = (critical_total == 0 or critical_covered == critical_total)
image_count_ok = (not ledger_has_screenshot) or final_note_image_count > 0
print('exists', p.exists())
print('lines', len(text.splitlines()))
print('chars', len(text))
print('unclosed_fences?', text.count('```') % 2 != 0, 'fence_count', text.count('```'))
print('unpaired_display_math?', text.count(delim) % 2 != 0, 'display_math_delimiters', text.count(delim))
print('final_note_image_count', final_note_image_count, 'missing_images', missing_images, 'image_count_ok', image_count_ok)
print('ledger_item_count', ledger_item_count, 'critical_coverage', f'{critical_covered}/{critical_total}', 'coverage_ok', coverage_ok)
print('has_recheck_record', '## 重检查记录' in text or '重检查记录' in text)
PY
```

质量门槛：
- 不把附录/不同实验设置的数值误写成主表结论。
- 对“作者声称”与“我的判断”保持明确区分。
- 长论文最终笔记必须可从 `claim → evidence → mechanism` 追溯到原文；关键 claim 没有证据矩阵或审稿回查记录时不得通过。
- 关键图表/主结果表/消融表/成本表缺失时不得通过，除非明确记录无法截取与替代证据。
- `image_count` 与 ledger coverage 是硬门槛：ledger 有截图时 `final_note_image_count` 不得为 0；ledger 标为关键/required 的图表必须 100% 在正文就地插入，或逐项记录无法插入原因与替代证据。
- 最终 Markdown 应包含二次验收/内容审稿记录，便于以后复查。

### 10. 批判性总结与研究行动
在输出末尾给出独立判断：真正贡献、最值得学习的思想/技巧、证据最强的结论、证据不足或可能被夸大的结论、主要局限和适用边界、对用户后续研究/工程实现/选型的启发。

同时给出：
- **最小复现/验证实验**：用最低成本验证论文关键 claim 的方案。
- **下一步可做研究问题**：1-3 个具体、可实验检验的问题，而不是泛泛方向。
- **下一篇该读什么**：经典前作、最接近 baseline、相邻领域启发或作者引用中最关键的一篇。
- **长期预测**：这篇论文中哪些想法可能在 1-2 年后仍重要，哪些可能只是短期包装。

## Recommended Markdown Template

````markdown
# 《论文标题》阅读笔记

## 0. 基本信息
- 论文：
- 作者/机构：
- 年份/会议：
- arXiv/DOI：
- 输入类别目录：
- 文件路径：
- 输出路径：
- 阅读日期：
- 用户线索/关注点：

## 0.5 术语与简称表
| 简称/术语 | 完整英文名 | 中文解释 | 本文中的具体含义 | 首次出现/证据 |
|---|---|---|---|---|

## 0.6 长论文证据交接记录（短论文可省略）
- 章节/机制任务来源：`section_index.md`、`notes/*_summary.md`、`notes/*_details.md`、`notes/*_claims.md`
- 图表账本：`figure_table_ledger.md/json`（`figure_table_index` 仅为候选索引，不能替代 ledger）
- 机制复原文件：`mechanism_*.md`
- 机器可读交接：`handoff_manifest.json`（含 final_note_path、asset_dir、ledger 路径、section_index、claims/details/mechanism 文件、ledger_item_count、image_count、missing_required_figures、validation_status）
- 汇总原则：本笔记按 `claim → evidence → mechanism` 合成，不只拼接 summary；关键图片按 ledger 在正文就地插入。

## 1. 小白友好版论文解释
> 本章合并旧版一句话结论与扫描式概览的职责。目标是让不熟悉该方向的读者先知道：论文研究什么问题、核心方法是什么、细节如何运作、结果大概怎样、为什么比已有方法好、代价和局限是什么。关键图片、表格、公式、代码片段必须在本章对应位置就地插入/列出并解释。

### 1.1 论文研究的问题
说明背景、现状、具体问题、输入输出、成功标准、为什么难、为什么值得研究。

### 1.2 核心方法及思想的直观理解和关键公式/代码解释
先用白话和例子解释核心 idea；再就地插入关键方法图、公式或伪代码/代码片段，并解释变量、输入输出和通俗含义。

![方法/直觉相关图表](assets/<pdf_stem>/xxx.png)

$$
关键公式写在这里；没有公式则删除本公式块
$$

### 1.3 方法细节仔细描述
分步骤描述数据构造、训练流程、推理流程、模块交互、损失/奖励/选择/更新规则、超参数、监督信号来源。若有理论分析，列出定理/命题/假设、关键公式、证明思路和限制；不能只写“论文证明了有效”。

### 1.4 大体实验结果
概括主结果、消融、分析、关键数值、数据集、指标、baseline、提升幅度和设置差异；关键结果表/曲线就地插入。

### 1.5 与已有方法相比好多少、为什么好、好在哪里
回答相对谁提升、提升多少、在哪些场景最明显、机制上为什么更好；区分作者声称、实验支持和我的判断，并给出直观解释。

### 1.6 代价或局限
说明训练/推理/数据/标注/工具/外部依赖/理论假设/适用范围/失败案例/复现成本；未报告处明确写“论文未明确说明”。

## 2. 研究者阅读记录
### 2.1 阅读动机：我为什么读这篇
### 2.2 预读预测：在看完整结果前我以为会怎样
### 2.3 读后校正：哪些预测被证实/推翻
### 2.4 我最想追问的 3 个问题

## 3. 论文主线串读：按行文顺序的完整描述 / 近似翻译式串读
> 本章是阅读笔记主体，继承旧第 4 章职责。先给出“叙事地图”，再按论文实际行文顺序完整串读 Abstract、Introduction、Related Work、Problem/Formulation、Method、Experiments、Analysis、Limitations、Conclusion 和 Appendix。附录不能漏；图表、公式、算法框和实验结果必须嵌入它们支撑的论证附近。

### 3.0 叙事地图：作者如何一步步推进全文
用 3-7 句话说明论文路线：从背景问题出发，如何进入任务定义/方法，如何设计训练或实验，最后如何用结果、消融、局限和附录回扣核心假设。叙事地图必须是“读者路线牌”，不要只列章节名；每一站都要写出这一站解决的问题和为什么下一站自然出现。

### 3.x 原文节点标题（Sec.x / p.x / Figure x / Table x / Eq.x）
**节点目的**：一句话说明这一小节让读者搞懂什么。

先用一段自然语言承接上一节点：作者为什么要进入这里？这一节点在全文论证链中解决什么问题？这一段按 `主张 → 证据 → 解释 → 连接` 写，不要只列原文位置。对于信息密集段落，可以按原文段落顺序做近似翻译式串读。

然后在需要处就地插入图表或公式，而不是先列图表再解释：

![相关图表](assets/<pdf_stem>/xxx.png)

$$
相关公式写在这里；没有公式则删除本公式块
$$

接着用连续段落解释：公式里的符号分别是什么，图表展示了什么趋势或结构，它如何支撑作者当前论点；如果证据不足、原文没给细节、或存在反例，也在这里直接写出。最后用一句话自然引出下一节点。

**材料缺口**：如果本节点依赖的实验设置、公式推导、数据来源、失败案例或附录细节没有在当前可读材料中出现，明确写“论文未明确说明 / 需要回看附录或代码”，不要用常识补齐。

### 3.y Appendix / Supplementary Material 串读
按附录实际顺序覆盖：证明、额外方法细节、prompt/超参、数据统计、额外实验、更多消融、案例、失败样本、限制和任务特化设置。若附录无相关内容，也要写“附录未提供/当前版本未包含”。

## 4. 论文主线总结
### 4.1 问题定义：任务、输入、输出、成功标准
### 4.2 核心假设与证据链
### 4.3 方法流程总览
### 4.3.1 监督信号/标签/critique/proxy 来源表（如适用）
| 信号 | 用途 | 取值/公式 | 由谁产生 | 是否依赖 ground truth/oracle/evaluator | 训练/评测可用阶段 | 附录证据 | 迁移成本 |
|---|---|---|---|---|---|---|---|

### 4.4 最小可运行版本 / sanity check

## 5. 关键公式索引（可选）
> 这里只做索引，详细解释必须已经在第 1 章或第 3 章主线串读中就地出现。
| 公式号 | 对应第 1/3 章位置 | 作用 | 详解位置 |
|---|---|---|---|

## 6. 实验是否合理
### 6.1 实验验证的问题
### 6.2 Baseline 公平性
### 6.3 主结果分析
### 6.4 消融实验
### 6.5 误差/案例/原始输出分析
### 6.6 效率、成本、可扩展性
### 6.7 统计可信度与复现性

## 7. 附录、局限与反证记录
### 7.1 Appendix 关键细节（若已在第 3 章串读，这里做索引和批判性总结）
### 7.2 作者承认的局限
### 7.3 我记录的不利证据/反例/边界条件
### 7.4 与旧论文/经典思想/相邻领域的关系

## 8. 我的判断与可借鉴点
- 真正贡献：
- 最强证据：
- 可能夸大：
- 适用场景：
- 不适用场景：
- 最值得借鉴的研究技巧/工程技巧：

## 9. 后续研究行动
- 最小复现/验证实验：
- 低成本 sanity check：
- 下一步可做的 1-3 个研究问题：
- 下一篇该读的论文/资料：
- 对 1-2 年后影响力的预测：

## 重检查记录
- 重检查日期：
- 对照范围：原文 section/页码/图表/公式、章节 details、Claim-Evidence Matrix、figure/table ledger、机制复原文件（短论文按实际情况填写）
- 补充/修正：
- 仍不确定：

## 内容审稿记录（长论文必填）
| 关键 claim | 回查原文位置 | 图表/公式/数值 | 作者声称 vs 事实 | 负结果/附录/未说明 | 处理结果 |
|---|---|---|---|---|---|

- 父进程抽查关键章节原文：已完成 / 未完成 / 不适用；抽查结论：
````

## Quality Checklist
- 是否通过 `run_command` 调用 `paper_locator.py` 定位论文并确定输出路径？
- 输出是否镜像 `outputs/papers/` 下的相对目录到 `outputs/papers_output/`？
- 若 PDF 页数大于 20 页（>20），或主文短但 Appendix / Supplementary Material 很长，是否已按长论文流程处理，而不是沿用旧的“约 30 页”阈值？
- 发现长论文且原任务只是简单 `read_paper` 时，是否由父进程重新规划 `todo_list`；子进程是否只提出 `propose_split`，没有自行批准/调度拆分？
- 长论文是否先用 PDF 阅读/抽取工具建立章节/页码/图表/公式/算法索引，并把每个章节或页码范围保存为 `sandbox/read_paper/<paper_stem>/sections/` 下的独立文件？
- 拆分粒度是否细到少数机制/实验问题；是否避免一个任务吞掉多个密集 Method/Experiment/Appendix 模块，必要时是否升级为机制专题任务？
- 章节阅读子任务是否只读取负责的章节/专题文件，并为每个章节强制产出 `summary`、`details`、`claims.md`、`figures_tables.md`、`formulas_algorithms.md`、`missing_details.md`？
- `claims.md` 是否包含 Claim-Evidence Matrix 字段：Claim / 原文位置 / 图表公式 / 机制步骤 / 证据强度 / 未说明 / 汇总去向 / 跨章节依赖？
- 是否设置独立证据/图表任务，并维护 `figure_table_ledger.md/json`，逐项记录截图、裁剪质量检查、解释和汇总去向？
- 评测子任务是否截图主结果表、消融表、成本/效率表；关键图表缺失时是否已明确记录无法截取原因与替代证据？
- 是否设置跨章节机制复原任务，复原输入、输出、步骤、训练/推理差异、监督信号、成本、失败模式和依赖证据？
- 最终汇总是否先读取 `handoff_manifest.json` 定位 evidence matrix、figure/table ledger、关键 details 和机制复原文件，并按 `claim → evidence → mechanism` 写作，而不是只拼接 summary？
- 是否写下阅读动机、预读预测和读后校正，而不是只总结作者结论？
- 是否避免残留独立扫描式概览章节；新 `## 3` 必须是论文主线串读？
- 第 1 章是否完整包含 `### 1.1`-`### 1.6`，并明确问题、现有不足、作者方案、实验结果、相比已有方法的提升幅度/原因和代价局限？
- 是否给出重要简称/术语的完整名称和中文解释，避免只堆缩写让用户困惑？
- 是否在第 1 章和第 3 章就地覆盖关键图表、公式、代码/算法框、案例、原始输出，并解释作者分析？
- 是否已对关键 Figure/Table/Algorithm/案例图表生成并按 ledger 在正文就地插入截图，且 Markdown 图片路径可用？若图表任务已完成但找不到 ledger，是否已回查 manifest/outputs/todo 摘要，仍找不到则 blocked？
- 截图是否默认只覆盖图表主体区域（含必要标题/图注），没有用整页截图冒充；若自动裁剪失败，是否已用 crops 精裁，或在 Markdown 与重检查记录中明确标注降级整页/大区域截图？
- 是否讲清任务输入、输出、成功标准和难点？
- 是否找到了核心假设，而不是只复述方法？
- 第 3 章是否按论文行文顺序完成完整主线串读/近似翻译式串读，把问题、方法、图表、公式、实验、局限和附录连接成一个统一叙事？
- 第 3 章是否先给出“读者路线牌”式叙事地图，而不是只列章节名？
- 每个 3.x 节点是否有节点目的、过渡句、原文锚点、证据解释和下一步引出？
- 关键段落是否按 `主张/主题句 → 证据 → 解释 → 连接` 写成人能顺着读的段落，而不是表格或清单堆砌？
- 图表和公式是否都出现在第 1 章或第 3 章中它们实际支撑的论证附近，并解释它们如何服务当前主线？
- 遇到论文未明确说明的关键细节时，是否显式标注材料缺口，而不是用常识或模型记忆补齐？
- 第 1.2/1.3 节是否给出方法整体流程图或清晰架构描述，并对关键公式/代码做小白友好的直观解释？
- Method 章节是否已经讲清所有关键步骤、公式、变量、超参数、训练/推理差异和模块连接；是否存在只用高层直觉概括、但遗漏论文具体机制的地方？
- 是否在第 1 章或第 3 章相关节点就地解释关键公式、符号和通俗含义，而不是集中堆公式？
- 公式是否使用正常 Markdown 数学 display block，并通过 KaTeX 安全检查（无尖括号下标、`\left/\right` 配对、无控制字符）？
- 是否检查 baseline 公平性、消融、误差分析、效率成本？
- 是否检查统计可信度与复现性：种子、方差、显著性、数据泄漏、开源、超参/prompt 披露？
- 第 3 章是否串读 Appendix 与 Limitations，且第 7 章是否记录不利证据、边界条件和作者未解释的问题？
- 是否区分了论文原文结论与自己的批判性判断？
- 是否给出最小复现实验、低成本 sanity check、下一步研究问题和下一篇应读资料？
- 是否在初版生成后执行“总结文件 × 论文原文”的重检查；长论文是否同时读取 evidence matrix、figure/table ledger、关键 details 和机制复原文件？
- 是否把重检查发现的遗漏/错误整合进最终 Markdown？
- 是否追加 `## 重检查记录`；长论文是否追加 `## 内容审稿记录`？
- 最终审稿是否逐条关键 claim 回查原文 section/图表/公式/数值，检查作者声称 vs 事实、负结果、附录、空泛概括和未说明项？
- 父进程是否抽查关键章节原文；抽查发现关键证据缺失时是否退回补读/修正？
- 是否完成最终验收：文件存在、行数/字符数合理、代码围栏闭合、数学分隔符成对、关键截图存在、figure/table ledger 完整、`image_count` 与 ledger coverage 达到硬门槛、主表/附录设置没有混淆？ledger 有截图时 `final_note_image_count` 是否不为 0，关键图表覆盖率是否为 100%（或逐项有替代证据）？
