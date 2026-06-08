# SQLite schema 与 watcher

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/memory-core/src/memory/manager-sync-ops.ts#L525-L590
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/memory-core/src/memory/manager-sync-ops.ts`

## 说明

ensureSchema 建索引结构；ensureWatcher 监听 MEMORY.md、memory/、extraPaths 并异步同步。

```ts
   525|  protected ensureSchema() {
   526|    const result = ensureMemoryIndexSchema({
   527|      db: this.db,
   528|      embeddingCacheTable: EMBEDDING_CACHE_TABLE,
   529|      cacheEnabled: this.cache.enabled,
   530|      ftsTable: FTS_TABLE,
   531|      ftsEnabled: this.fts.enabled,
   532|      ftsTokenizer: this.settings.store.fts.tokenizer,
   533|    });
   534|    this.fts.available = result.ftsAvailable;
   535|    if (result.ftsError) {
   536|      this.fts.loadError = result.ftsError;
   537|      // Only warn when hybrid search is enabled; otherwise this is expected noise.
   538|      if (this.fts.enabled) {
   539|        log.warn(`fts unavailable: ${result.ftsError}`);
   540|      }
   541|    }
   542|  }
   543|
   544|  protected ensureWatcher() {
   545|    if (!this.sources.has("memory") || !this.settings.sync.watch) {
   546|      return;
   547|    }
   548|    if (this.watcher || this.nativeMemoryWatchPairs.length > 0) {
   549|      // Already initialized — preserve idempotence.
   550|      return;
   551|    }
   552|    // Core paths preserve original symlink-follow behavior (chokidar/fs.watch
   553|    // resolve through symlinks by default); extraPaths preserves the original
   554|    // explicit symlink-skip policy.
   555|    const fileWatchPaths = new Set<string>([path.join(this.workspaceDir, "MEMORY.md")]);
   556|    const dirWatchPaths = new Set<string>([path.join(this.workspaceDir, "memory")]);
   557|    const additionalPaths = normalizeExtraMemoryPaths(this.workspaceDir, this.settings.extraPaths);
   558|    for (const entry of additionalPaths) {
   559|      try {
   560|        const stat = fsSync.lstatSync(entry);
   561|        if (stat.isSymbolicLink()) {
   562|          continue;
   563|        }
   564|        if (stat.isDirectory()) {
   565|          dirWatchPaths.add(entry);
   566|          continue;
   567|        }
   568|        if (
   569|          stat.isFile() &&
   570|          (normalizeLowercaseStringOrEmpty(entry).endsWith(".md") ||
   571|            classifyMemoryMultimodalPath(entry, this.settings.multimodal) !== null)
   572|        ) {
   573|          fileWatchPaths.add(entry);
   574|        }
   575|      } catch {
   576|        // Skip missing/unreadable additional paths.
   577|      }
   578|    }
   579|    const markDirty = (watchPath?: string, stats?: MemoryWatchEventStats) => {
   580|      recordMemoryWatchEventPath(this.pendingWatchPaths, watchPath, stats);
   581|      this.dirty = true;
   582|      this.scheduleWatchSync();
   583|    };
   584|    // Native recursive fs.watch for directory paths — one watcher per
   585|    // directory on macOS (FSEvents) and Windows (ReadDirectoryChangesW).
   586|    // Avoids chokidar's per-file fs.watch fan-out on large memory trees.
   587|    //
   588|    // Linux is intentionally handled by a separate directory-tree watcher
   589|    // below: Node's `fs.watch(dir, { recursive: true })` routes through
   590|    // `internal/fs/recursive_watch` and watches every file. Watching
```
