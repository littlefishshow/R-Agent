# Project_progress

该目录用于保存大型功能开发或长期项目维护的续接上下文。

## 用途

当某个功能尚未完成、上下文即将丢失、会话即将结束或需要下次继续时，在这里保存：

- 项目主体和用户目标；
- 当前完成进展；
- 未完成项和下一步；
- 关键代码和文件位置；
- 设计决策；
- 验证结果和待验证项。

## 文件命名建议

```text
YYYY-MM-DD_<project>_context.md
YYYY-MM-DD_<project>_handoff.txt
YYYY-MM-DD_<project>_debug.log
```

## 注意

- 不保存密钥、token、密码、私钥。
- 不保存无筛选的大段终端输出。
- 不把这里当作长期 Memory；这里只服务于项目续接。
- 后续继续开发时，应先读取这里的上下文，再检查真实文件内容和 git diff。

## 上下文合并与清理

- `project_progress.py save` 默认会在同日同项目文件存在时读取旧 entry，将旧摘要、状态、关键文件、决策、验证和下一步压缩进 `Prior Context Considered`，再写入当前 entry；这样下次保存不会反复复制旧上下文。
- 只有需要完整保留历史流水时才使用 `--append`。
- CLI `/project_list` 中可输入普通编号载入上下文；输入 `1,2 del` 可删除选中的上下文文件，用于清理已经合并或不再需要的旧文件。
