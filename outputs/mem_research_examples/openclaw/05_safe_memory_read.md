# 安全读取 memory 文件

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/packages/memory-host-sdk/src/host/read-file.ts#L66-L182
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `packages/memory-host-sdk/src/host/read-file.ts`

## 说明

限制 workspace/extraPaths、只读 .md、防 symlink escape、缺失返回空、按预算截断。

```ts
    66|/** Read a validated memory markdown file from workspace or configured extra paths. */
    67|export async function readMemoryFile(params: {
    68|  workspaceDir: string;
    69|  extraPaths?: string[];
    70|  relPath: string;
    71|  from?: number;
    72|  lines?: number;
    73|  defaultLines?: number;
    74|  maxChars?: number;
    75|}): Promise<MemoryReadResult> {
    76|  const rawPath = params.relPath.trim();
    77|  if (!rawPath) {
    78|    throw new Error("path required");
    79|  }
    80|  const absPath = path.isAbsolute(rawPath)
    81|    ? path.resolve(rawPath)
    82|    : path.resolve(params.workspaceDir, rawPath);
    83|  const relPath = path.relative(params.workspaceDir, absPath).replace(/\\/g, "/");
    84|  const inWorkspace = relPath.length > 0 && !relPath.startsWith("..") && !path.isAbsolute(relPath);
    85|  const allowedWorkspace = inWorkspace && isMemoryPath(relPath);
    86|  let allowedAdditional = false;
    87|  if (!allowedWorkspace && (params.extraPaths?.length ?? 0) > 0) {
    88|    const additionalPaths = normalizeExtraMemoryPaths(params.workspaceDir, params.extraPaths);
    89|    for (const additionalPath of additionalPaths) {
    90|      try {
    91|        const stat = await fs.lstat(additionalPath);
    92|        if (stat.isSymbolicLink()) {
    93|          continue;
    94|        }
    95|        if (stat.isDirectory()) {
    96|          if (await isAllowedAdditionalDirectoryPath(additionalPath, absPath)) {
    97|            const candidateStat = await fs.lstat(absPath).catch(() => null);
    98|            if (candidateStat?.isSymbolicLink()) {
    99|              continue;
   100|            }
   101|            allowedAdditional = true;
   102|            break;
   103|          }
   104|          continue;
   105|        }
   106|        if (stat.isFile() && absPath === additionalPath && absPath.endsWith(".md")) {
   107|          allowedAdditional = true;
   108|          break;
   109|        }
   110|      } catch {}
   111|    }
   112|  }
   113|  if (!allowedWorkspace && !allowedAdditional) {
   114|    throw new Error("path required");
   115|  }
   116|  if (!absPath.endsWith(".md")) {
   117|    throw new Error("path required");
   118|  }
   119|  if (allowedWorkspace) {
   120|    try {
   121|      // Workspace reads use the safe fs root so symlink escapes are rejected before file IO.
   122|      const workspaceRoot = await root(params.workspaceDir);
   123|      await workspaceRoot.resolve(relPath);
   124|    } catch (err) {
   125|      if (isFileMissingError(err)) {
   126|        return { text: "", path: relPath };
   127|      }
   128|      throw err;
   129|    }
   130|  }
   131|  const statResult = await statRegularFile(absPath);
   132|  if (statResult.missing) {
   133|    return { text: "", path: relPath };
   134|  }
   135|  let content: string;
   136|  try {
   137|    content = (
   138|      await retryTransientMemoryRead(
   139|        () => readRegularFile({ filePath: absPath }),
   140|        `read memory file ${absPath}`,
   141|      )
   142|    ).buffer.toString("utf-8");
   143|  } catch (err) {
   144|    if (isFileDisappearedDuringReadError(err)) {
   145|      return { text: "", path: relPath };
   146|    }
   147|    throw err;
   148|  }
   149|  return buildMemoryReadResult({
   150|    content,
   151|    relPath,
   152|    from: params.from,
   153|    lines: params.lines,
   154|    defaultLines: params.defaultLines ?? DEFAULT_MEMORY_READ_LINES,
   155|    maxChars: params.maxChars,
   156|    suggestReadFallback: allowedWorkspace,
   157|  });
   158|}
   159|
   160|/** Resolve agent memory config and read one memory file for that agent. */
   161|export async function readAgentMemoryFile(params: {
   162|  cfg: OpenClawConfig;
   163|  agentId: string;
   164|  relPath: string;
   165|  from?: number;
   166|  lines?: number;
   167|}): Promise<MemoryReadResult> {
   168|  const settings = resolveMemorySearchConfig(params.cfg, params.agentId);
   169|  if (!settings) {
   170|    throw new Error("memory search disabled");
   171|  }
   172|  const contextLimits = resolveAgentContextLimits(params.cfg, params.agentId);
   173|  return await readMemoryFile({
   174|    workspaceDir: resolveAgentWorkspaceDir(params.cfg, params.agentId),
   175|    extraPaths: settings.extraPaths,
   176|    relPath: params.relPath,
   177|    from: params.from,
   178|    lines: params.lines,
   179|    defaultLines: contextLimits?.memoryGetDefaultLines,
   180|    maxChars: contextLimits?.memoryGetMaxChars,
   181|  });
   182|}
```
