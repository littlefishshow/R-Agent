# memory 文件路径白名单与扫描

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/packages/memory-host-sdk/src/host/internal.ts#L103-L195
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `packages/memory-host-sdk/src/host/internal.ts`

## 说明

只把 MEMORY.md、DREAMS.md、memory/ 下文件和配置 extraPaths 纳入 memory 范围，跳过 symlink。

```ts
   103|export function isMemoryPath(relPath: string): boolean {
   104|  const normalized = normalizeRelPath(relPath);
   105|  if (!normalized) {
   106|    return false;
   107|  }
   108|  if (normalized === CANONICAL_ROOT_MEMORY_FILENAME || normalized.toLowerCase() === "dreams.md") {
   109|    return true;
   110|  }
   111|  return normalized.startsWith("memory/");
   112|}
   113|
   114|function isAllowedMemoryFilePath(filePath: string, multimodal?: MemoryMultimodalSettings): boolean {
   115|  if (filePath.endsWith(".md")) {
   116|    return true;
   117|  }
   118|  return (
   119|    classifyMemoryMultimodalPath(filePath, multimodal ?? DISABLED_MULTIMODAL_SETTINGS) !== null
   120|  );
   121|}
   122|
   123|function shouldDescendMemoryEntry(
   124|  entry: WalkDirectoryEntry,
   125|  shouldSkipPath?: (absPath: string) => boolean,
   126|): boolean {
   127|  if (shouldSkipPath?.(entry.path)) {
   128|    return false;
   129|  }
   130|  return entry.kind === "directory" && entry.name !== ".openclaw-repair";
   131|}
   132|
   133|async function collectMemoryFilesFromDir(
   134|  dir: string,
   135|  files: string[],
   136|  multimodal?: MemoryMultimodalSettings,
   137|  shouldSkipPath?: (absPath: string) => boolean,
   138|): Promise<void> {
   139|  const scan = await walkDirectory(dir, {
   140|    symlinks: "skip",
   141|    descend: (entry) => shouldDescendMemoryEntry(entry, shouldSkipPath),
   142|    include: (entry) =>
   143|      !shouldSkipPath?.(entry.path) &&
   144|      entry.kind === "file" &&
   145|      isAllowedMemoryFilePath(entry.path, multimodal),
   146|  });
   147|  files.push(...scan.entries.map((entry) => entry.path));
   148|}
   149|
   150|export async function listMemoryFiles(
   151|  workspaceDir: string,
   152|  extraPaths?: string[],
   153|  multimodal?: MemoryMultimodalSettings,
   154|): Promise<string[]> {
   155|  const result: string[] = [];
   156|  const memoryDir = path.join(workspaceDir, "memory");
   157|
   158|  const shouldSkipWorkspaceMemoryPath = (absPath: string): boolean =>
   159|    shouldSkipRootMemoryAuxiliaryPath({ workspaceDir, absPath });
   160|
   161|  const addMarkdownFile = async (absPath: string) => {
   162|    try {
   163|      const stat = await statRegularFile(absPath);
   164|      if (stat.missing) {
   165|        return;
   166|      }
   167|      if (!absPath.endsWith(".md")) {
   168|        return;
   169|      }
   170|      result.push(absPath);
   171|    } catch {}
   172|  };
   173|
   174|  const memoryFile = await resolveCanonicalRootMemoryFile(workspaceDir);
   175|  if (memoryFile) {
   176|    await addMarkdownFile(memoryFile);
   177|  }
   178|  try {
   179|    const dirStat = await fs.lstat(memoryDir);
   180|    if (!dirStat.isSymbolicLink() && dirStat.isDirectory()) {
   181|      await collectMemoryFilesFromDir(memoryDir, result, multimodal, shouldSkipWorkspaceMemoryPath);
   182|    }
   183|  } catch {}
   184|
   185|  const normalizedExtraPaths = normalizeExtraMemoryPaths(workspaceDir, extraPaths);
   186|  if (normalizedExtraPaths.length > 0) {
   187|    for (const inputPath of normalizedExtraPaths) {
   188|      if (shouldSkipWorkspaceMemoryPath(inputPath)) {
   189|        continue;
   190|      }
   191|      try {
   192|        const stat = await fs.lstat(inputPath);
   193|        if (stat.isSymbolicLink()) {
   194|          continue;
   195|        }
```
