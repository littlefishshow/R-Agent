# memory_search 工具执行流程

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/memory-core/src/tools.ts#L331-L590
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/memory-core/src/tools.ts`

## 说明

解析 query/corpus，调用 MemorySearchManager.search；无结果可强制 sync 重试；做可见性过滤、citation、recall tracking。

```ts
   331|export function createMemorySearchTool(options: {
   332|  config?: OpenClawConfig;
   333|  getConfig?: () => OpenClawConfig | undefined;
   334|  agentId?: string;
   335|  agentSessionKey?: string;
   336|  sandboxed?: boolean;
   337|}) {
   338|  return createMemoryTool({
   339|    options,
   340|    label: "Memory Search",
   341|    name: "memory_search",
   342|    description:
   343|      "Mandatory recall step: semantically search MEMORY.md + memory/*.md (and optional session transcripts) before answering questions about prior work, decisions, dates, people, preferences, or todos. Optional `corpus=wiki` or `corpus=all` also searches registered compiled-wiki supplements. `corpus=memory` restricts hits to indexed memory files (excludes session transcript chunks from ranking). `corpus=sessions` restricts hits to indexed session transcripts (same visibility rules as session history tools). If response has disabled=true, memory retrieval is unavailable; you must tell the user and include the warning/action guidance.",
   344|    parameters: MemorySearchSchema,
   345|    execute:
   346|      ({ cfg, agentId }) =>
   347|      async (_toolCallId, params) => {
   348|        const rawParams = asToolParamsRecord(params);
   349|        const query = readStringParam(rawParams, "query", { required: true });
   350|        const maxResults = readPositiveIntegerParam(rawParams, "maxResults");
   351|        const minScore = readFiniteNumberParam(rawParams, "minScore");
   352|        const requestedCorpus = readStringParam(rawParams, "corpus") as
   353|          | "memory"
   354|          | "wiki"
   355|          | "all"
   356|          | "sessions"
   357|          | undefined;
   358|        const cooldownKey = resolveMemorySearchToolCooldownKey({
   359|          agentId,
   360|          agentSessionKey: options.agentSessionKey,
   361|        });
   362|        const cooldown =
   363|          requestedCorpus === "wiki" ? undefined : readMemorySearchToolCooldown(cooldownKey);
   364|        let activeUnavailablePhase: "memory" | "supplement" | undefined;
   365|        let failedUnavailablePhase: "memory" | "supplement" | undefined;
   366|        const runUnavailablePhase = async <T>(
   367|          phase: "memory" | "supplement",
   368|          task: () => Promise<T>,
   369|        ): Promise<T> => {
   370|          activeUnavailablePhase = phase;
   371|          try {
   372|            return await task();
   373|          } catch (error) {
   374|            failedUnavailablePhase = phase;
   375|            throw error;
   376|          } finally {
   377|            if (activeUnavailablePhase === phase) {
   378|              activeUnavailablePhase = undefined;
   379|            }
   380|          }
   381|        };
   382|
   383|        const outcome = await runMemorySearchToolWithDeadline({
   384|          timeoutMs: MEMORY_SEARCH_TOOL_TIMEOUT_MS,
   385|          run: async () => {
   386|            const { resolveMemoryBackendConfig } = await loadMemoryToolRuntime();
   387|            const shouldQuerySupplements = requestedCorpus === "wiki" || requestedCorpus === "all";
   388|            const shouldQueryMemory = requestedCorpus !== "wiki" && !cooldown;
   389|            if (cooldown && !shouldQuerySupplements) {
   390|              return jsonResult(buildMemorySearchUnavailableResult(cooldown.error));
   391|            }
   392|            const memory = shouldQueryMemory
   393|              ? await runUnavailablePhase(
   394|                  "memory",
   395|                  async () => await getMemoryManagerContext({ cfg, agentId }),
   396|                )
   397|              : null;
   398|            if (shouldQueryMemory && memory && "error" in memory && !shouldQuerySupplements) {
   399|              recordMemorySearchToolCooldown(
   400|                cooldownKey,
   401|                memory.error ?? "memory search unavailable",
   402|              );
   403|              return jsonResult(buildMemorySearchUnavailableResult(memory.error));
   404|            }
   405|
   406|            const citationsMode = resolveMemoryCitationsMode(cfg);
   407|            const includeCitations = shouldIncludeCitations({
   408|              mode: citationsMode,
   409|              sessionKey: options.agentSessionKey,
   410|            });
   411|            const pluginConfig = resolveMemoryCorePluginConfig(cfg);
   412|            const dreamingEnabled = resolveMemoryDreamingConfig({
   413|              pluginConfig,
   414|              cfg,
   415|            }).enabled;
   416|            const dreaming = resolveMemoryDeepDreamingConfig({
   417|              pluginConfig,
   418|              cfg,
   419|            });
   420|            const searchStartedAt = Date.now();
   421|            let rawResults: MemorySearchResult[] = [];
   422|            let surfacedMemoryResults: Array<MemorySearchResult & { corpus: MemorySource }> = [];
   423|            let provider: string | undefined;
   424|            let model: string | undefined;
   425|            let fallback: unknown;
   426|            let searchMode: string | undefined;
   427|            let pausedIndexIdentityReason: string | undefined;
   428|            let searchDebug:
   429|              | {
   430|                  backend: string;
   431|                  configuredMode?: string;
   432|                  effectiveMode?: string;
   433|                  fallback?: string;
   434|                  searchMs: number;
   435|                  hits: number;
   436|                }
   437|              | undefined;
   438|            if (shouldQueryMemory && memory && !("error" in memory)) {
   439|              await runUnavailablePhase("memory", async () => {
   440|                let activeMemory = memory;
   441|                const runtimeDebug: MemorySearchRuntimeDebug[] = [];
   442|                const qmdSearchModeOverride = resolveActiveMemoryQmdSearchModeOverride(
   443|                  cfg,
   444|                  options.agentSessionKey,
   445|                );
   446|                const searchSources: MemorySource[] | undefined =
   447|                  requestedCorpus === "sessions"
   448|                    ? (["sessions"] as MemorySource[])
   449|                    : requestedCorpus === "memory"
   450|                      ? (["memory"] as MemorySource[])
   451|                      : undefined;
   452|                const searchOptions = {
   453|                  maxResults,
   454|                  minScore,
   455|                  sessionKey: options.agentSessionKey,
   456|                  qmdSearchModeOverride,
   457|                  onDebug: (debug: MemorySearchRuntimeDebug) => {
   458|                    runtimeDebug.push(debug);
   459|                  },
   460|                  ...(searchSources ? { sources: searchSources } : {}),
   461|                };
   462|                try {
   463|                  rawResults = await activeMemory.manager.search(query, searchOptions);
   464|                } catch (error) {
   465|                  if (!isClosedMemoryStoreError(error)) {
   466|                    throw error;
   467|                  }
   468|                  const refreshed = await getMemoryManagerContext({ cfg, agentId });
   469|                  if ("error" in refreshed) {
   470|                    throw error;
   471|                  }
   472|                  activeMemory = refreshed;
   473|                  rawResults = await activeMemory.manager.search(query, searchOptions);
   474|                }
   475|                const statusBeforeRetry = activeMemory.manager.status();
   476|                pausedIndexIdentityReason =
   477|                  resolvePausedMemoryIndexIdentityReason(statusBeforeRetry);
   478|                if (pausedIndexIdentityReason) {
   479|                  return;
   480|                }
   481|                if (rawResults.length === 0 && activeMemory.manager.sync) {
   482|                  await activeMemory.manager.sync({ reason: "search", force: true });
   483|                  rawResults = await activeMemory.manager.search(query, searchOptions);
   484|                  pausedIndexIdentityReason = resolvePausedMemoryIndexIdentityReason(
   485|                    activeMemory.manager.status(),
   486|                  );
   487|                  if (pausedIndexIdentityReason) {
   488|                    return;
   489|                  }
   490|                }
   491|                rawResults = await filterMemorySearchHitsBySessionVisibility({
   492|                  cfg,
   493|                  agentId,
   494|                  requesterSessionKey: options.agentSessionKey,
   495|                  sandboxed: options.sandboxed === true,
   496|                  hits: rawResults,
   497|                });
   498|                if (requestedCorpus === "sessions") {
   499|                  rawResults = rawResults.filter((hit) => hit.source === "sessions");
   500|                } else if (requestedCorpus === "memory") {
   501|                  rawResults = rawResults.filter((hit) => hit.source === "memory");
   502|                }
   503|                const status = activeMemory.manager.status();
   504|                const decorated = decorateCitations(rawResults, includeCitations);
   505|                const resolved = resolveMemoryBackendConfig({ cfg, agentId });
   506|                const memoryResults =
   507|                  status.backend === "qmd"
   508|                    ? clampResultsByInjectedChars(decorated, resolved.qmd?.limits.maxInjectedChars)
   509|                    : decorated;
   510|                surfacedMemoryResults = memoryResults.map((result) => ({
   511|                  ...result,
   512|                  corpus: result.source,
   513|                }));
   514|                if (dreamingEnabled) {
   515|                  queueShortTermRecallTracking({
   516|                    workspaceDir: status.workspaceDir,
   517|                    query,
   518|                    rawResults,
   519|                    surfacedResults: memoryResults,
   520|                    timezone: dreaming.timezone,
   521|                  });
   522|                }
   523|                provider = status.provider;
   524|                model = status.model;
   525|                fallback = status.fallback;
   526|                const latestDebug = runtimeDebug.at(-1);
   527|                searchMode = latestDebug?.effectiveMode;
   528|                searchDebug = {
   529|                  backend: status.backend,
   530|                  configuredMode: latestDebug?.configuredMode,
   531|                  effectiveMode:
   532|                    status.backend === "qmd"
   533|                      ? (latestDebug?.effectiveMode ?? latestDebug?.configuredMode)
   534|                      : "n/a",
   535|                  fallback: latestDebug?.fallback,
   536|                  searchMs: Math.max(0, Date.now() - searchStartedAt),
   537|                  hits: rawResults.length,
   538|                };
   539|              });
   540|              if (pausedIndexIdentityReason) {
   541|                return jsonResult(
   542|                  buildPausedMemoryIndexUnavailableResult(pausedIndexIdentityReason),
   543|                );
   544|              }
   545|            }
   546|            const supplementResults = shouldQuerySupplements
   547|              ? await runUnavailablePhase(
   548|                  "supplement",
   549|                  async () =>
   550|                    await searchMemoryCorpusSupplements({
   551|                      query,
   552|                      maxResults,
   553|                      agentSessionKey: options.agentSessionKey,
   554|                      corpus: requestedCorpus,
   555|                    }),
   556|                )
   557|              : [];
   558|            // Wiki and memory scores use incomparable scales, so corpus=all first
   559|            // balances candidate selection and then backfills any unused slots.
   560|            const effectiveMax = Math.max(1, maxResults ?? 10);
   561|            const results = mergeMemorySearchCorpusResults({
   562|              memoryResults: surfacedMemoryResults,
   563|              supplementResults,
   564|              maxResults: effectiveMax,
   565|              balanceCorpora: requestedCorpus === "all",
   566|            });
   567|            return jsonResult({
   568|              results,
   569|              provider,
   570|              model,
   571|              fallback,
   572|              citations: citationsMode,
   573|              mode: searchMode,
   574|              debug: searchDebug,
   575|            });
   576|          },
   577|        });
   578|        if (outcome.status === "unavailable") {
   579|          const unavailablePhase = failedUnavailablePhase ?? activeUnavailablePhase;
   580|          const shouldRecordCooldown =
   581|            requestedCorpus !== "wiki" &&
   582|            (requestedCorpus !== "all" || unavailablePhase === "memory");
   583|          if (shouldRecordCooldown) {
   584|            recordMemorySearchToolCooldown(cooldownKey, outcome.error);
   585|          }
   586|          return jsonResult(buildMemorySearchUnavailableResult(outcome.error));
   587|        }
   588|        return outcome.value;
   589|      },
   590|  });
```
