# fallback 向量扫描与 FTS/BM25

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/memory-core/src/memory/manager-search.ts#L227-L432
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/memory-core/src/memory/manager-search.ts`

## 说明

分批 exact scan 计算 cosine；关键词走 FTS5/BM25，MATCH 失败 fallback LIKE。

```ts
   227|async function searchChunksByEmbedding(params: {
   228|  db: DatabaseSync;
   229|  providerModel: string;
   230|  sourceFilter: { sql: string; params: SearchSource[] };
   231|  queryVec: number[];
   232|  limit: number;
   233|  snippetMaxChars: number;
   234|}): Promise<SearchRowResult[]> {
   235|  if (params.limit <= 0) {
   236|    return [];
   237|  }
   238|  // Keep batches bounded instead of calling `.all()` across the entire chunks
   239|  // table, and do not hold a sqlite iterator open across the setImmediate yield
   240|  // below. The rowid cursor keeps memory bounded without OFFSET rescans.
   241|  const stmt = params.db.prepare(
   242|    `SELECT rowid, id, path, start_line, end_line, text, embedding, source\n` +
   243|      `  FROM chunks\n` +
   244|      ` WHERE model = ? AND rowid > ?${params.sourceFilter.sql}\n` +
   245|      ` ORDER BY rowid ASC\n` +
   246|      ` LIMIT ?`,
   247|  );
   248|  type ChunkEmbeddingRow = {
   249|    rowid: number | bigint;
   250|    id: string;
   251|    path: string;
   252|    start_line: number;
   253|    end_line: number;
   254|    text: string;
   255|    embedding: string;
   256|    source: SearchSource;
   257|  };
   258|
   259|  const topResults: SearchRowResult[] = [];
   260|  let lastRowid = 0;
   261|  while (true) {
   262|    const batch = stmt.all(
   263|      params.providerModel,
   264|      lastRowid,
   265|      ...params.sourceFilter.params,
   266|      FALLBACK_VECTOR_BATCH_SIZE,
   267|    ) as ChunkEmbeddingRow[];
   268|    if (batch.length === 0) {
   269|      break;
   270|    }
   271|    for (const row of batch) {
   272|      const score = cosineSimilarity(params.queryVec, parseEmbedding(row.embedding));
   273|      if (Number.isFinite(score)) {
   274|        const result: SearchRowResult = {
   275|          id: row.id,
   276|          path: row.path,
   277|          startLine: row.start_line,
   278|          endLine: row.end_line,
   279|          score,
   280|          snippet: truncateUtf16Safe(row.text, params.snippetMaxChars),
   281|          source: row.source,
   282|        };
   283|        if (topResults.length < params.limit) {
   284|          topResults.push(result);
   285|          if (topResults.length === params.limit) {
   286|            topResults.sort((a, b) => b.score - a.score);
   287|          }
   288|        } else {
   289|          const lowest = topResults.at(-1);
   290|          if (lowest && result.score > lowest.score) {
   291|            topResults[topResults.length - 1] = result;
   292|            topResults.sort((a, b) => b.score - a.score);
   293|          }
   294|        }
   295|      }
   296|    }
   297|    const nextRowid = batch.at(-1)?.rowid;
   298|    lastRowid = typeof nextRowid === "bigint" ? Number(nextRowid) : (nextRowid ?? lastRowid);
   299|    if (batch.length < FALLBACK_VECTOR_BATCH_SIZE) {
   300|      break;
   301|    }
   302|    await yieldToEventLoop();
   303|  }
   304|  topResults.sort((a, b) => b.score - a.score);
   305|  return topResults;
   306|}
   307|
   308|export async function searchKeyword(params: {
   309|  db: DatabaseSync;
   310|  ftsTable: string;
   311|  providerModel: string | undefined;
   312|  query: string;
   313|  ftsTokenizer?: "unicode61" | "trigram";
   314|  limit: number;
   315|  snippetMaxChars: number;
   316|  sourceFilter: { sql: string; params: SearchSource[] };
   317|  buildFtsQuery: (raw: string) => string | null;
   318|  bm25RankToScore: (rank: number) => number;
   319|  boostFallbackRanking?: boolean;
   320|}): Promise<Array<SearchRowResult & { textScore: number }>> {
   321|  if (params.limit <= 0) {
   322|    return [];
   323|  }
   324|  const plan = planKeywordSearch({
   325|    query: params.query,
   326|    ftsTokenizer: params.ftsTokenizer,
   327|    buildFtsQuery: params.buildFtsQuery,
   328|  });
   329|  if (!plan.matchQuery && plan.substringTerms.length === 0) {
   330|    return [];
   331|  }
   332|
   333|  // When providerModel is undefined (FTS-only mode), search all models
   334|  const modelClause = params.providerModel ? " AND model = ?" : "";
   335|  const modelParams = params.providerModel ? [params.providerModel] : [];
   336|  const substringClause = plan.substringTerms.map(() => " AND text LIKE ? ESCAPE '\\'").join("");
   337|  const substringParams = plan.substringTerms.map((term) => `%${escapeLikePattern(term)}%`);
   338|
   339|  let rows: Array<{
   340|    id: string;
   341|    path: string;
   342|    source: SearchSource;
   343|    start_line: number;
   344|    end_line: number;
   345|    text: string;
   346|    rank: number;
   347|  }>;
   348|  let usedMatch = false;
   349|
   350|  if (plan.matchQuery) {
   351|    try {
   352|      rows = params.db
   353|        .prepare(
   354|          `SELECT id, path, source, start_line, end_line, text,\n` +
   355|            `       bm25(${params.ftsTable}) AS rank\n` +
   356|            `  FROM ${params.ftsTable}\n` +
   357|            ` WHERE ${params.ftsTable} MATCH ?${substringClause}${modelClause}${params.sourceFilter.sql}\n` +
   358|            ` ORDER BY rank ASC\n` +
   359|            ` LIMIT ?`,
   360|        )
   361|        .all(
   362|          plan.matchQuery,
   363|          ...substringParams,
   364|          ...modelParams,
   365|          ...params.sourceFilter.params,
   366|          params.limit,
   367|        ) as typeof rows;
   368|      usedMatch = true;
   369|    } catch (matchErr) {
   370|      // FTS5 MATCH can fail on certain token patterns depending on the
   371|      // Node.js sqlite runtime and tokenizer (e.g. unicode61 vs trigram).
   372|      // Log the root cause, then fall back to per-token LIKE-based substring
   373|      // search so results are still returned instead of being silently dropped.
   374|      console.warn(`memory search: FTS5 MATCH failed, falling back to LIKE: ${String(matchErr)}`);
   375|      const queryTokens = normalizeStringEntries(params.query.match(FTS_QUERY_TOKEN_RE) ?? []);
   376|      const allTerms = uniqueStrings([...queryTokens, ...plan.substringTerms]);
   377|      const fallbackLikeClause = allTerms.map(() => " AND text LIKE ? ESCAPE '\\'").join("");
   378|      const fallbackLikeParams = allTerms.map((term) => `%${escapeLikePattern(term)}%`);
   379|      rows = params.db
   380|        .prepare(
   381|          `SELECT id, path, source, start_line, end_line, text,\n` +
   382|            `       0 AS rank\n` +
   383|            `  FROM ${params.ftsTable}\n` +
   384|            ` WHERE 1=1${fallbackLikeClause}${modelClause}${params.sourceFilter.sql}\n` +
   385|            ` LIMIT ?`,
   386|        )
   387|        .all(
   388|          ...fallbackLikeParams,
   389|          ...modelParams,
   390|          ...params.sourceFilter.params,
   391|          params.limit,
   392|        ) as typeof rows;
   393|    }
   394|  } else {
   395|    rows = params.db
   396|      .prepare(
   397|        `SELECT id, path, source, start_line, end_line, text,\n` +
   398|          `       0 AS rank\n` +
   399|          `  FROM ${params.ftsTable}\n` +
   400|          ` WHERE 1=1${substringClause}${modelClause}${params.sourceFilter.sql}\n` +
   401|          ` LIMIT ?`,
   402|      )
   403|      .all(
   404|        ...substringParams,
   405|        ...modelParams,
   406|        ...params.sourceFilter.params,
   407|        params.limit,
   408|      ) as typeof rows;
   409|  }
   410|
   411|  return rows.map((row) => {
   412|    const textScore = usedMatch ? params.bm25RankToScore(row.rank) : 1;
   413|    const score = params.boostFallbackRanking
   414|      ? scoreFallbackKeywordResult({
   415|          query: params.query,
   416|          path: row.path,
   417|          text: row.text,
   418|          ftsScore: textScore,
   419|        })
   420|      : textScore;
   421|    return {
   422|      id: row.id,
   423|      path: row.path,
   424|      startLine: row.start_line,
   425|      endLine: row.end_line,
   426|      score,
   427|      textScore,
   428|      snippet: truncateUtf16Safe(row.text, params.snippetMaxChars),
   429|      source: row.source,
   430|    };
   431|  });
   432|}
```
