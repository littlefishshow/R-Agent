# 向量检索与 sqlite-vec fallback

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/memory-core/src/memory/manager-search.ts#L130-L224
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/memory-core/src/memory/manager-search.ts`

## 说明

优先 sqlite-vec KNN；不可用时走 chunks embedding exact scan。

```ts
   130|export async function searchVector(params: {
   131|  db: DatabaseSync;
   132|  vectorTable: string;
   133|  providerModel: string;
   134|  queryVec: number[];
   135|  limit: number;
   136|  snippetMaxChars: number;
   137|  ensureVectorReady: (dimensions: number) => Promise<boolean>;
   138|  sourceFilterVec: { sql: string; params: SearchSource[] };
   139|  sourceFilterChunks: { sql: string; params: SearchSource[] };
   140|}): Promise<SearchRowResult[]> {
   141|  if (params.queryVec.length === 0 || params.limit <= 0) {
   142|    return [];
   143|  }
   144|  if (await params.ensureVectorReady(params.queryVec.length)) {
   145|    // Use sqlite-vec's native KNN (MATCH ? AND k = ?) for candidate selection,
   146|    // which runs in ~O(log N + k) via the vec0 index, instead of the previous
   147|    // full-table scan over vec_distance_cosine(). Keep vec_distance_cosine() in
   148|    // the SELECT so `score = 1 - dist` stays in the cosine [0, 1] range the
   149|    // downstream merge/minScore pipeline expects. (chunks_vec is created with
   150|    // sqlite-vec's default L2 distance, so v.distance cannot be used directly
   151|    // for scoring.)
   152|    const qBlob = vectorToBlob(params.queryVec);
   153|    const runVectorQuery = (candidateLimit: number) =>
   154|      params.db
   155|        .prepare(
   156|          `SELECT c.id, c.path, c.start_line, c.end_line, c.text,\n` +
   157|            `       c.source,\n` +
   158|            `       vec_distance_cosine(v.embedding, ?) AS dist\n` +
   159|            `  FROM ${params.vectorTable} v\n` +
   160|            `  JOIN chunks c ON c.id = v.id\n` +
   161|            ` WHERE v.embedding MATCH ? AND k = ? AND c.model = ?${params.sourceFilterVec.sql}\n` +
   162|            ` ORDER BY dist ASC\n` +
   163|            ` LIMIT ?`,
   164|        )
   165|        .all(
   166|          qBlob,
   167|          qBlob,
   168|          candidateLimit,
   169|          params.providerModel,
   170|          ...params.sourceFilterVec.params,
   171|          params.limit,
   172|        ) as Array<{
   173|        id: string;
   174|        path: string;
   175|        start_line: number;
   176|        end_line: number;
   177|        text: string;
   178|        source: SearchSource;
   179|        dist: number;
   180|      }>;
   181|
   182|    const candidateLimit = params.limit * VECTOR_KNN_OVERSAMPLE_FACTOR;
   183|    let rows = runVectorQuery(candidateLimit);
   184|    if (rows.length < params.limit) {
   185|      const matchingChunkCount = readCount(
   186|        params.db
   187|          .prepare(
   188|            `SELECT COUNT(*) AS count FROM chunks c WHERE c.model = ?${params.sourceFilterVec.sql}`,
   189|          )
   190|          .get(params.providerModel, ...params.sourceFilterVec.params) as
   191|          | { count?: number | bigint }
   192|          | undefined,
   193|      );
   194|      if (matchingChunkCount > rows.length) {
   195|        const vectorCount = readCount(
   196|          params.db.prepare(`SELECT COUNT(*) AS count FROM ${params.vectorTable}`).get() as
   197|            | { count?: number | bigint }
   198|            | undefined,
   199|        );
   200|        if (vectorCount > candidateLimit) {
   201|          rows = runVectorQuery(vectorCount);
   202|        }
   203|      }
   204|    }
   205|
   206|    return rows.map((row) => ({
   207|      id: row.id,
   208|      path: row.path,
   209|      startLine: row.start_line,
   210|      endLine: row.end_line,
   211|      score: 1 - row.dist,
   212|      snippet: truncateUtf16Safe(row.text, params.snippetMaxChars),
   213|      source: row.source,
   214|    }));
   215|  }
   216|
   217|  return await searchChunksByEmbedding({
   218|    db: params.db,
   219|    providerModel: params.providerModel,
   220|    sourceFilter: params.sourceFilterChunks,
   221|    queryVec: params.queryVec,
   222|    limit: params.limit,
   223|    snippetMaxChars: params.snippetMaxChars,
   224|  });
```
