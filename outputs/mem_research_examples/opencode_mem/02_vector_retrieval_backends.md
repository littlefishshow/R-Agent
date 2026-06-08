# 向量检索后端与降级示例

## Source: `opencode-mem/src/services/sqlite/vector-search.ts` lines 21-130

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/sqlite/vector-search.ts#L21-L130
- 说明: 新版写入 memories 表后调用可插拔 VectorBackend；查询时先 rebuild 缓存并同时查 content/tags，异常时降级到精确扫描。

```ts
  21:   constructor(backend?: VectorBackend, fallbackBackend: VectorBackend = new ExactScanBackend()) {
  22:     this.backendPromise = backend
  23:       ? Promise.resolve(backend)
  24:       : createVectorBackend({ vectorBackend: CONFIG.vectorBackend });
  25:     this.fallbackBackend = fallbackBackend;
  26:   }
  27: 
  28:   private async getBackend(): Promise<VectorBackend> {
  29:     return this.backendPromise;
  30:   }
  31: 
  32:   async insertVector(db: DatabaseType, record: MemoryRecord, shard?: ShardInfo): Promise<void> {
  33:     const insertMemory = db.prepare(`
  34:       INSERT INTO memories (
  35:         id, content, vector, tags_vector, container_tag, tags, type, created_at, updated_at,
  36:         metadata, display_name, user_name, user_email, project_path, project_name, git_repo_url
  37:       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  38:     `);
  39: 
  40:     insertMemory.run(
  41:       record.id,
  42:       record.content,
  43:       toBlob(record.vector),
  44:       toBlob(record.tagsVector),
  45:       record.containerTag,
  46:       record.tags || null,
  47:       record.type || null,
  48:       record.createdAt,
  49:       record.updatedAt,
  50:       record.metadata || null,
  51:       record.displayName || null,
  52:       record.userName || null,
  53:       record.userEmail || null,
  54:       record.projectPath || null,
  55:       record.projectName || null,
  56:       record.gitRepoUrl || null
  57:     );
  58: 
  59:     try {
  60:       if (shard) {
  61:         const backend = await this.getBackend();
  62:         await backend.insert({ id: record.id, vector: record.vector, shard, kind: "content" });
  63:         if (record.tagsVector) {
  64:           await backend.insert({ id: record.id, vector: record.tagsVector, shard, kind: "tags" });
  65:         }
  66:       }
  67:     } catch (error) {
  68:       db.prepare(`DELETE FROM memories WHERE id = ?`).run(record.id);
  69:       throw error;
  70:     }
  71:   }
  72: 
  73:   async searchInShard(
  74:     shard: ShardInfo,
  75:     queryVector: Float32Array,
  76:     containerTag: string,
  77:     limit: number,
  78:     queryText?: string
  79:   ): Promise<SearchResult[]> {
  80:     const db = connectionManager.getConnection(shard.dbPath);
  81:     const backend = await this.getBackend();
  82:     let contentResults;
  83:     let tagsResults;
  84: 
  85:     try {
  86:       await backend.rebuildFromShard({ db, shard, kind: "content" });
  87:       await backend.rebuildFromShard({ db, shard, kind: "tags" });
  88: 
  89:       contentResults = await backend.search({
  90:         db,
  91:         shard,
  92:         kind: "content",
  93:         queryVector,
  94:         limit: limit * 4,
  95:       });
  96:       tagsResults = await backend.search({
  97:         db,
  98:         shard,
  99:         kind: "tags",
 100:         queryVector,
 101:         limit: limit * 4,
 102:       });
 103:     } catch (error) {
 104:       log("Vector search degraded to exact scan in shard", {
 105:         shardId: shard.id,
 106:         backend: backend.getBackendName(),
 107:         error: String(error),
 108:       });
 109: 
 110:       await this.fallbackBackend.rebuildFromShard({ db, shard, kind: "content" });
 111:       await this.fallbackBackend.rebuildFromShard({ db, shard, kind: "tags" });
 112:       contentResults = await this.fallbackBackend.search({
 113:         db,
 114:         shard,
 115:         kind: "content",
 116:         queryVector,
 117:         limit: limit * 4,
 118:       });
 119:       tagsResults = await this.fallbackBackend.search({
 120:         db,
 121:         shard,
 122:         kind: "tags",
 123:         queryVector,
 124:         limit: limit * 4,
 125:       });
 126:     }
 127: 
 128:     const scoreMap = new Map<string, { contentSim: number; tagsSim: number }>();
 129: 
 130:     for (const r of contentResults) {
```

## Source: `opencode-mem/src/services/sqlite/vector-search.ts` lines 128-180

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/sqlite/vector-search.ts#L128-L180
- 说明: 将 content 相似度与 tags 相似度合并加权，再按 container_tag 过滤取回完整 memory。

```ts
 128:     const scoreMap = new Map<string, { contentSim: number; tagsSim: number }>();
 129: 
 130:     for (const r of contentResults) {
 131:       scoreMap.set(r.id, { contentSim: 1 - r.distance, tagsSim: 0 });
 132:     }
 133: 
 134:     for (const r of tagsResults) {
 135:       const entry = scoreMap.get(r.id);
 136:       if (entry) {
 137:         entry.tagsSim = 1 - r.distance;
 138:       } else {
 139:         scoreMap.set(r.id, { contentSim: 0, tagsSim: 1 - r.distance });
 140:       }
 141:     }
 142: 
 143:     const ids = Array.from(scoreMap.keys());
 144:     if (ids.length === 0) return [];
 145: 
 146:     const placeholders = ids.map(() => "?").join(",");
 147:     const rows = db
 148:       .prepare(
 149:         containerTag === ""
 150:           ? `
 151:       SELECT * FROM memories
 152:       WHERE id IN (${placeholders})
 153:     `
 154:           : `
 155:       SELECT * FROM memories
 156:       WHERE id IN (${placeholders}) AND container_tag = ?
 157:     `
 158:       )
 159:       .all(...ids, ...(containerTag === "" ? [] : [containerTag])) as any[];
 160: 
 161:     const queryWords = queryText
 162:       ? queryText
 163:           .toLowerCase()
 164:           .split(/[\s,]+/)
 165:           .filter((w) => w.length > 1)
 166:       : [];
 167: 
 168:     const hydratedResults = rows.map((row: any) => {
 169:       const scores = scoreMap.get(row.id)!;
 170:       const memoryTagsStr = row.tags || "";
 171:       const memoryTags = memoryTagsStr.split(",").map((t: string) => t.trim().toLowerCase());
 172: 
 173:       let exactMatchBoost = 0;
 174:       if (queryWords.length > 0 && memoryTags.length > 0) {
 175:         const matches = queryWords.filter((w) =>
 176:           memoryTags.some((t: string) => t.includes(w) || w.includes(t))
 177:         ).length;
 178:         exactMatchBoost = matches / Math.max(queryWords.length, 1);
 179:       }
 180: 
```

## Source: `opencode-mem/src/services/vector-backends/usearch-backend.ts` lines 78-136

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/vector-backends/usearch-backend.ts#L78-L136
- 说明: USearch 后端使用内存索引搜索，并从 SQLite memories 表读取向量重建索引。

```ts
  78:   async search(args: VectorBackendSearchParams): Promise<BackendSearchResult[]> {
  79:     const indexKey = this.getIndexKey(args.shard, args.kind);
  80:     const cache = await this.getOrCreateIndex(indexKey);
  81:     try {
  82:       const matches = cache.index.search(args.queryVector, args.limit);
  83:       return Array.from(matches.keys as Iterable<bigint>, (key, index) => {
  84:         const id = cache.keyToId.get(key);
  85:         if (!id) {
  86:           throw new Error(
  87:             `USearch index metadata missing for key ${String(key)} in ${cache.indexKey}`
  88:           );
  89:         }
  90:         return {
  91:           id,
  92:           distance: matches.distances[index] ?? 0,
  93:         };
  94:       });
  95:     } catch (error) {
  96:       throw new Error(`USearch search failed for ${indexKey}: ${String(error)}`);
  97:     }
  98:   }
  99: 
 100:   async rebuildFromShard(args: { db: unknown; shard: ShardInfo; kind: VectorKind }): Promise<void> {
 101:     const indexKey = this.getIndexKey(args.shard, args.kind);
 102:     const existing = this.indexes.get(indexKey);
 103:     if (existing?.initialized) {
 104:       return;
 105:     }
 106: 
 107:     const column = args.kind === "tags" ? "tags_vector" : "vector";
 108:     const rows = (
 109:       args.db as {
 110:         prepare: (sql: string) => {
 111:           all: () => Array<{
 112:             id: string;
 113:             vector?: Uint8Array | ArrayBuffer | null;
 114:             tags_vector?: Uint8Array | ArrayBuffer | null;
 115:           }>;
 116:         };
 117:       }
 118:     )
 119:       .prepare(`SELECT id, ${column} FROM memories WHERE ${column} IS NOT NULL`)
 120:       .all();
 121: 
 122:     const cache = await this.createEmptyIndex(indexKey);
 123:     this.indexes.set(indexKey, cache);
 124: 
 125:     for (const row of rows) {
 126:       const raw = args.kind === "tags" ? row.tags_vector : row.vector;
 127:       const vector = this.decodeVector(raw);
 128:       if (vector.length === 0) continue;
 129:       this.upsertItem(cache, { id: row.id, vector });
 130:     }
 131: 
 132:     cache.initialized = true;
 133:   }
 134: 
 135:   async deleteShardIndexes(args: { shard: ShardInfo }): Promise<void> {
 136:     for (const kind of ["content", "tags"] as const) {
```

## Source: `opencode-mem/src/services/vector-backends/exact-scan-backend.ts` lines 51-100

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/vector-backends/exact-scan-backend.ts#L51-L100
- 说明: ExactScan 后端直接扫描 SQLite BLOB 向量，计算 cosine similarity 作为可靠降级路径。

```ts
  51:   async search(args: VectorBackendSearchParams): Promise<BackendSearchResult[]> {
  52:     const column = args.kind === "tags" ? "tags_vector" : "vector";
  53:     const rows = (
  54:       args.db as {
  55:         prepare: (sql: string) => { all: () => VectorRow[] };
  56:       }
  57:     )
  58:       .prepare(`SELECT id, ${column} FROM memories WHERE ${column} IS NOT NULL`)
  59:       .all();
  60: 
  61:     if (rows.length === 0) {
  62:       return [];
  63:     }
  64: 
  65:     const rankedRows: RankedRow[] = rows
  66:       .map((row) => ({
  67:         id: row.id,
  68:         vector: this.decodeVector(args.kind === "tags" ? row.tags_vector : row.vector),
  69:       }))
  70:       .filter((row) => row.vector.length > 0);
  71: 
  72:     return this.rankVectors(rankedRows, args.queryVector, args.limit);
  73:   }
  74: 
  75:   async rebuildFromShard(_args: {
  76:     db: unknown;
  77:     shard: ShardInfo;
  78:     kind: VectorKind;
  79:   }): Promise<void> {}
  80: 
  81:   async deleteShardIndexes(_args: { shard: ShardInfo }): Promise<void> {}
  82: 
  83:   private decodeVector(value: Uint8Array | ArrayBuffer | null | undefined): Float32Array {
  84:     if (!value) {
  85:       return new Float32Array();
  86:     }
  87: 
  88:     if (value instanceof Uint8Array) {
  89:       return new Float32Array(
  90:         value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength)
  91:       );
  92:     }
  93: 
  94:     return new Float32Array(value);
  95:   }
  96: 
  97:   private cosineSimilarity(a: Float32Array, b: Float32Array): number {
  98:     if (a.length !== b.length) {
  99:       return 0;
 100:     }
```

## Source: `opencode-mymem/src/services/sqlite/vector-search.ts` lines 1-90

- Commit: `410a7b26fc8860f2fc86bc684bcf0ca54b1de732`
- URL: https://github.com/epoch-chrono/opencode-mymem/blob/410a7b26fc8860f2fc86bc684bcf0ca54b1de732/src/services/sqlite/vector-search.ts#L1-L90
- 说明: 旧 fork 的 sqlite-vec 实现：插入 vec_memories/vec_tags，使用 MATCH + k 查询距离。

```ts
   1: import { Database } from "bun:sqlite";
   2: import { connectionManager } from "./connection-manager.js";
   3: import { log } from "../logger.js";
   4: import type { MemoryRecord, SearchResult, ShardInfo } from "./types.js";
   5: 
   6: export class VectorSearch {
   7:   insertVector(db: Database, record: MemoryRecord): void {
   8:     const insertMemory = db.prepare(`
   9:       INSERT INTO memories (
  10:         id, content, vector, container_tag, tags, type, created_at, updated_at,
  11:         metadata, display_name, user_name, user_email, project_path, project_name, git_repo_url
  12:       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  13:     `);
  14: 
  15:     const vectorBuffer = new Uint8Array(record.vector.buffer);
  16: 
  17:     insertMemory.run(
  18:       record.id,
  19:       record.content,
  20:       vectorBuffer,
  21:       record.containerTag,
  22:       record.tags || null,
  23:       record.type || null,
  24:       record.createdAt,
  25:       record.updatedAt,
  26:       record.metadata || null,
  27:       record.displayName || null,
  28:       record.userName || null,
  29:       record.userEmail || null,
  30:       record.projectPath || null,
  31:       record.projectName || null,
  32:       record.gitRepoUrl || null
  33:     );
  34: 
  35:     const insertVec = db.prepare(`
  36:       INSERT INTO vec_memories (memory_id, embedding) VALUES (?, ?)
  37:     `);
  38:     insertVec.run(record.id, vectorBuffer);
  39: 
  40:     if (record.tagsVector) {
  41:       const tagsVectorBuffer = new Uint8Array(record.tagsVector.buffer);
  42:       const insertTagsVec = db.prepare(`
  43:         INSERT INTO vec_tags (memory_id, embedding) VALUES (?, ?)
  44:       `);
  45:       insertTagsVec.run(record.id, tagsVectorBuffer);
  46:     }
  47:   }
  48: 
  49:   searchInShard(
  50:     shard: ShardInfo,
  51:     queryVector: Float32Array,
  52:     containerTag: string,
  53:     limit: number,
  54:     queryText?: string
  55:   ): SearchResult[] {
  56:     const db = connectionManager.getConnection(shard.dbPath);
  57:     const queryBuffer = new Uint8Array(queryVector.buffer);
  58: 
  59:     const contentResults = db
  60:       .prepare(
  61:         `
  62:       SELECT memory_id, distance FROM vec_memories 
  63:       WHERE embedding MATCH ? AND k = ?
  64:       ORDER BY distance
  65:     `
  66:       )
  67:       .all(queryBuffer, limit * 4) as any[];
  68: 
  69:     const tagsResults = db
  70:       .prepare(
  71:         `
  72:       SELECT memory_id, distance FROM vec_tags 
  73:       WHERE embedding MATCH ? AND k = ?
  74:       ORDER BY distance
  75:     `
  76:       )
  77:       .all(queryBuffer, limit * 4) as any[];
  78: 
  79:     const scoreMap = new Map<string, { contentSim: number; tagsSim: number }>();
  80: 
  81:     for (const r of contentResults) {
  82:       scoreMap.set(r.memory_id, { contentSim: 1 - r.distance, tagsSim: 0 });
  83:     }
  84: 
  85:     for (const r of tagsResults) {
  86:       const entry = scoreMap.get(r.memory_id) || { contentSim: 0, tagsSim: 0 };
  87:       entry.tagsSim = 1 - r.distance;
  88:       scoreMap.set(r.memory_id, entry);
  89:     }
  90: 
```
