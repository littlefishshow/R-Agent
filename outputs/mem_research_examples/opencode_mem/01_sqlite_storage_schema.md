# SQLite 存储与分片 schema 示例

## Source: `opencode-mem/src/services/sqlite/shard-manager.ts` lines 110-182

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/sqlite/shard-manager.ts#L110-L182
- 说明: 新版 opencode-mem 用 metadata.db 记录 shards，每个 shard SQLite 文件内建 memories 表；向量以 BLOB 持久化，另有 container_tag/type/metadata/profile/project 字段和索引。

```ts
 110:   createShard(scope: "user" | "project", scopeHash: string, shardIndex: number): ShardInfo {
 111:     const fullPath = this.getShardPath(scope, scopeHash, shardIndex);
 112:     const storedPath = join(`${scope}s`, basename(fullPath)).replace(/\\/g, "/");
 113:     const now = Date.now();
 114: 
 115:     const stmt = this.metadataDb.prepare(`
 116:       INSERT INTO shards (scope, scope_hash, shard_index, db_path, vector_count, is_active, created_at)
 117:       VALUES (?, ?, ?, ?, 0, 1, ?)
 118:     `);
 119: 
 120:     const result = stmt.run(scope, scopeHash, shardIndex, storedPath, now);
 121: 
 122:     const db = connectionManager.getConnection(fullPath);
 123:     this.initShardDb(db);
 124: 
 125:     return {
 126:       id: Number(result.lastInsertRowid),
 127:       scope,
 128:       scopeHash,
 129:       shardIndex,
 130:       dbPath: fullPath,
 131:       vectorCount: 0,
 132:       isActive: true,
 133:       createdAt: now,
 134:     };
 135:   }
 136: 
 137:   private initShardDb(db: DatabaseType): void {
 138:     db.run(`
 139:       CREATE TABLE IF NOT EXISTS shard_metadata (
 140:         key TEXT PRIMARY KEY,
 141:         value TEXT NOT NULL
 142:       )
 143:     `);
 144: 
 145:     db.run(`
 146:       INSERT OR REPLACE INTO shard_metadata (key, value) 
 147:       VALUES ('embedding_dimensions', '${CONFIG.embeddingDimensions}')
 148:     `);
 149: 
 150:     db.run(`
 151:       INSERT OR REPLACE INTO shard_metadata (key, value) 
 152:       VALUES ('embedding_model', '${CONFIG.embeddingModel}')
 153:     `);
 154: 
 155:     db.run(`
 156:       CREATE TABLE IF NOT EXISTS memories (
 157:         id TEXT PRIMARY KEY,
 158:         content TEXT NOT NULL,
 159:         vector BLOB NOT NULL,
 160:         tags_vector BLOB,
 161:         container_tag TEXT NOT NULL,
 162:         tags TEXT,
 163:         type TEXT,
 164:         created_at INTEGER NOT NULL,
 165:         updated_at INTEGER NOT NULL,
 166:         metadata TEXT,
 167:         display_name TEXT,
 168:         user_name TEXT,
 169:         user_email TEXT,
 170:         project_path TEXT,
 171:         project_name TEXT,
 172:         git_repo_url TEXT,
 173:         is_pinned INTEGER DEFAULT 0
 174:       )
 175:     `);
 176: 
 177:     db.run(`CREATE INDEX IF NOT EXISTS idx_container_tag ON memories(container_tag)`);
 178:     db.run(`CREATE INDEX IF NOT EXISTS idx_type ON memories(type)`);
 179:     db.run(`CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at DESC)`);
 180:     db.run(`CREATE INDEX IF NOT EXISTS idx_is_pinned ON memories(is_pinned)`);
 181:   }
 182: 
```

## Source: `opencode-mymem/src/services/sqlite/connection-manager.ts` lines 87-107

- Commit: `410a7b26fc8860f2fc86bc684bcf0ca54b1de732`
- URL: https://github.com/epoch-chrono/opencode-mymem/blob/410a7b26fc8860f2fc86bc684bcf0ca54b1de732/src/services/sqlite/connection-manager.ts#L87-L107
- 说明: 旧 fork 使用 sqlite-vec 时，对 SQLite 连接设置 WAL/busy_timeout/cache 等 PRAGMA，并加载 sqlite-vec 扩展。

```ts
  87:   private initDatabase(db: Database): void {
  88:     db.run("PRAGMA busy_timeout = 5000");
  89:     db.run("PRAGMA journal_mode = WAL");
  90:     db.run("PRAGMA synchronous = NORMAL");
  91:     db.run("PRAGMA cache_size = -64000");
  92:     db.run("PRAGMA temp_store = MEMORY");
  93:     db.run("PRAGMA foreign_keys = ON");
  94: 
  95:     try {
  96:       sqliteVec.load(db);
  97:     } catch (error) {
  98:       throw new Error(
  99:         `Failed to load sqlite-vec extension: ${error}\n\n` +
 100:           `This usually means SQLite extension loading is disabled.\n` +
 101:           `On macOS, you must use Homebrew SQLite instead of Apple's SQLite.\n\n` +
 102:           `Solution:\n` +
 103:           `1. Install: brew install sqlite\n` +
 104:           `2. Configure customSqlitePath in ~/.config/opencode/opencode-mem.jsonc`
 105:       );
 106:     }
 107: 
```

## Source: `opencode-mymem/src/services/sqlite/shard-manager.ts` lines 145-190

- Commit: `410a7b26fc8860f2fc86bc684bcf0ca54b1de732`
- URL: https://github.com/epoch-chrono/opencode-mymem/blob/410a7b26fc8860f2fc86bc684bcf0ca54b1de732/src/services/sqlite/shard-manager.ts#L145-L190
- 说明: 旧 fork 在 shard 内同时创建 memories 普通表和 vec0 虚拟表 vec_memories/vec_tags。

```ts
 145:     db.run(`
 146:       INSERT OR REPLACE INTO shard_metadata (key, value) 
 147:       VALUES ('embedding_model', '${CONFIG.embeddingModel}')
 148:     `);
 149: 
 150:     db.run(`
 151:       CREATE TABLE IF NOT EXISTS memories (
 152:         id TEXT PRIMARY KEY,
 153:         content TEXT NOT NULL,
 154:         vector BLOB NOT NULL,
 155:         container_tag TEXT NOT NULL,
 156:         tags TEXT,
 157:         type TEXT,
 158:         created_at INTEGER NOT NULL,
 159:         updated_at INTEGER NOT NULL,
 160:         metadata TEXT,
 161:         display_name TEXT,
 162:         user_name TEXT,
 163:         user_email TEXT,
 164:         project_path TEXT,
 165:         project_name TEXT,
 166:         git_repo_url TEXT,
 167:         is_pinned INTEGER DEFAULT 0
 168:       )
 169:     `);
 170: 
 171:     db.run(`
 172:       CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories USING vec0(
 173:         memory_id TEXT PRIMARY KEY,
 174:         embedding float32[${CONFIG.embeddingDimensions}] distance_metric=cosine
 175:       )
 176:     `);
 177: 
 178:     db.run(`
 179:       CREATE VIRTUAL TABLE IF NOT EXISTS vec_tags USING vec0(
 180:         memory_id TEXT PRIMARY KEY,
 181:         embedding float32[${CONFIG.embeddingDimensions}] distance_metric=cosine
 182:       )
 183:     `);
 184: 
 185:     db.run(`CREATE INDEX IF NOT EXISTS idx_container_tag ON memories(container_tag)`);
 186:     db.run(`CREATE INDEX IF NOT EXISTS idx_type ON memories(type)`);
 187:     db.run(`CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at DESC)`);
 188:     db.run(`CREATE INDEX IF NOT EXISTS idx_is_pinned ON memories(is_pinned)`);
 189:   }
 190: 
```
