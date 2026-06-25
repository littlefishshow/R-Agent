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
