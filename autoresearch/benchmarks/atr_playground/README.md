# atr_playground

`atr_playground` 是 R-Agent 内置的一组 AutoResearch 示例 benchmark，当前位置：

```text
autoresearch/benchmarks/atr_playground/
```

它原本是一个独立测试仓库；迁入 R-Agent 后已移除内部 `.git`，现在作为 `autoresearch/` 子系统的示例与本地回归素材维护。

## 设计目标

这些项目都很小、确定性强、CPU-only、无需网络，适合验证 AutoResearch 是否能完成最基本的研究闭环：

1. 读取 `program.md` 理解目标、边界与 Completion Criteria；
2. 不修改固定评测文件；
3. 修改允许范围内的 `solution.py`、`train/` 或 `submission/`；
4. 运行 `prepare.py`、`train/train.sh`、`eval.sh`；
5. 读取 `metrics.json` 判断指标是否变好；
6. 记录结果、保留改进、总结失败经验。

## 通用协议

大多数项目遵循类似结构：

```text
<project>/
  README.md
  program.md
  prepare.py
  train/train.sh
  eval.py
  eval.sh
  metrics.json
  solution.py 或 submission/
```

典型手动运行方式：

```bash
cd autoresearch/benchmarks/atr_playground/json_repair_micro
python3 prepare.py
bash train/train.sh
bash eval.sh
cat metrics.json
```

通过 R-Agent CLI 启动 AutoResearch：

```text
/autoresearch run autoresearch/benchmarks/atr_playground/json_repair_micro
```

查看进度：

```text
/autoresearch show autoresearch/benchmarks/atr_playground/json_repair_micro
```

停止运行：

```text
/autoresearch kill
```

## Benchmark 列表

| 项目 | 大致任务 | Baseline 弱点 | 主要指标 / 目标 |
|---|---|---|---|
| `byte_codec_detector` | 修复 mojibake、HTML entity、escape sequence，把乱码小文本解码为干净 Unicode | 只做简单 html unescape / unicode escape | `decoded_exact_accuracy`，目标全对 |
| `coin_change_dp` | 最小硬币兑换 | 递归 memoized solver 有函数调用/递归开销 | `score`，正确性优先、速度其次 |
| `csv_cleaner` | 清洗脏 CSV，规范姓名、年龄、邮箱、州名 | 规则不完整，只做 lower/strip 等基础清洗 | `score`，综合 row exact 与 cell F1/accuracy |
| `json_repair_micro` | 修复小型损坏 JSON | 只处理少量简单替换 | `repair_exact_accuracy`，目标全对 |
| `knapsack_solver` | 0/1 背包最优值 | value/weight 贪心不保证最优 | `score`，精确最优为主 |
| `log_anomaly_f1` | 判断合成服务日志是否异常 | 只查严重关键词，漏掉 latency/5xx/retry/resource 等模式 | `positive_f1`，目标无误报/漏报 |
| `mini_ir_ranker` | 微型信息检索排序 | raw token overlap，缺少 IDF/短语/同义词等信号 | `mean_reciprocal_rank`，目标相关文档排第一 |
| `route_heuristic_optimizer` | 小型欧氏路径/TSP-like heuristic | 从 node 0 nearest-neighbor 容易陷入局部差解 | `route_quality_score`，接近已知最优路径 |
| `string_matcher` | 多 pattern 子串匹配，统计 overlapping occurrences | naive 双重/多重扫描效率低 | `score`，精确计数优先、速度其次 |
| `text_normalizer_editrules` | 噪声产品/类别短字符串规范化 | 只做 lower+strip，缺少拼写/标点/缩写/罗马数字规则 | `exact_match_accuracy`，目标规范化全对 |

## 维护注意事项

- `eval.py`、`eval.sh` 和固定测试数据通常代表官方评测，不应在 AutoResearch 实验中修改。
- `.auto/`、`.autoresearch/`、`__pycache__/`、日志等运行产物不应提交。
- 如果需要重置某个 benchmark，可优先用 Git 查看 R-Agent 仓库中的 diff，而不是在 benchmark 内部另建 git 仓库。
- 新增 benchmark 时，应至少提供 `README.md`、`program.md`、`prepare.py`、`train/train.sh`、`eval.sh` 和 `metrics.json`。
