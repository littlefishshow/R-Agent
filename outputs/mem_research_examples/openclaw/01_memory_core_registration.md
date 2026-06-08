# memory-core 注册 tools/prompt/flush/runtime

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/memory-core/index.ts#L64-L206
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/memory-core/index.ts`

## 说明

注册 memory_search、memory_get、promptBuilder、flushPlanResolver 与 runtime。

```ts
    64|const MemorySearchSchema = {
    65|  type: "object",
    66|  properties: {
    67|    query: { type: "string" },
    68|    maxResults: { type: "integer", minimum: 1 },
    69|    minScore: { type: "number" },
    70|    corpus: { type: "string", enum: ["memory", "wiki", "all", "sessions"] },
    71|  },
    72|  required: ["query"],
    73|  additionalProperties: false,
    74|} as const satisfies TSchema;
    75|
    76|const MemoryGetSchema = {
    77|  type: "object",
    78|  properties: {
    79|    path: { type: "string" },
    80|    from: { type: "integer", minimum: 1 },
    81|    lines: { type: "integer", minimum: 1 },
    82|    corpus: { type: "string", enum: ["memory", "wiki", "all"] },
    83|  },
    84|  required: ["path"],
    85|  additionalProperties: false,
    86|} as const satisfies TSchema;
    87|
    88|function createLazyMemoryTool(params: {
    89|  options: MemoryToolOptions;
    90|  label: string;
    91|  name: "memory_search" | "memory_get";
    92|  description: string;
    93|  parameters: typeof MemorySearchSchema | typeof MemoryGetSchema;
    94|  load: (module: MemoryToolsModule, options: MemoryToolOptions) => AnyAgentTool | null;
    95|}): AnyAgentTool | null {
    96|  if (!hasMemoryToolContext(params.options)) {
    97|    return null;
    98|  }
    99|
   100|  let toolPromise: Promise<AnyAgentTool | null> | undefined;
   101|  const loadTool = async () => {
   102|    toolPromise ??= loadMemoryToolsModule().then((module) => params.load(module, params.options));
   103|    return await toolPromise;
   104|  };
   105|
   106|  return {
   107|    label: params.label,
   108|    name: params.name,
   109|    description: params.description,
   110|    parameters: params.parameters,
   111|    execute: async (toolCallId, toolParams, signal, onUpdate) => {
   112|      const tool = await loadTool();
   113|      if (!tool) {
   114|        return jsonResult({
   115|          disabled: true,
   116|          unavailable: true,
   117|          error: "memory search unavailable",
   118|        });
   119|      }
   120|      return await tool.execute(toolCallId, toolParams, signal, onUpdate);
   121|    },
   122|  };
   123|}
   124|
   125|function createLazyMemorySearchTool(options: MemoryToolOptions): AnyAgentTool | null {
   126|  return createLazyMemoryTool({
   127|    options,
   128|    label: "Memory Search",
   129|    name: "memory_search",
   130|    description:
   131|      "Mandatory recall step: semantically search MEMORY.md + memory/*.md (and optional session transcripts) before answering questions about prior work, decisions, dates, people, preferences, or todos. Optional `corpus=wiki` or `corpus=all` also searches registered compiled-wiki supplements. `corpus=memory` restricts hits to indexed memory files (excludes session transcript chunks from ranking). `corpus=sessions` restricts hits to indexed session transcripts (same visibility rules as session history tools). If response has disabled=true, memory retrieval is unavailable and should be surfaced to the user.",
   132|    parameters: MemorySearchSchema,
   133|    load: (module, loadOptions) => module.createMemorySearchTool(loadOptions),
   134|  });
   135|}
   136|
   137|function createLazyMemoryGetTool(options: MemoryToolOptions): AnyAgentTool | null {
   138|  return createLazyMemoryTool({
   139|    options,
   140|    label: "Memory Get",
   141|    name: "memory_get",
   142|    description:
   143|      "Safe exact excerpt read from MEMORY.md or memory/*.md. Defaults to a bounded excerpt when lines are omitted, includes truncation/continuation info when more content exists, and `corpus=wiki` reads from registered compiled-wiki supplements.",
   144|    parameters: MemoryGetSchema,
   145|    load: (module, loadOptions) => module.createMemoryGetTool(loadOptions),
   146|  });
   147|}
   148|
   149|function resolveMemoryToolOptions(ctx: OpenClawPluginToolContext): MemoryToolOptions {
   150|  const getConfig = () => ctx.getRuntimeConfig?.() ?? ctx.runtimeConfig ?? ctx.config;
   151|  return {
   152|    config: getConfig(),
   153|    getConfig,
   154|    agentId: ctx.agentId,
   155|    agentSessionKey: ctx.sessionKey,
   156|    sandboxed: ctx.sandboxed,
   157|  };
   158|}
   159|
   160|const memoryRuntime: MemoryPluginRuntime = {
   161|  async getMemorySearchManager(params) {
   162|    const { memoryRuntime: runtime } = await loadRuntimeProviderModule();
   163|    return await runtime.getMemorySearchManager(params);
   164|  },
   165|  resolveMemoryBackendConfig(params) {
   166|    return resolveMemoryBackendConfig(params);
   167|  },
   168|  async closeAllMemorySearchManagers() {
   169|    const { memoryRuntime: runtime } = await loadRuntimeProviderModule();
   170|    await runtime.closeAllMemorySearchManagers?.();
   171|  },
   172|  async closeMemorySearchManager(params) {
   173|    const { memoryRuntime: runtime } = await loadRuntimeProviderModule();
   174|    await runtime.closeMemorySearchManager?.(params);
   175|  },
   176|};
   177|export default definePluginEntry({
   178|  id: "memory-core",
   179|  name: "Memory (Core)",
   180|  description: "File-backed memory search tools and CLI",
   181|  kind: "memory",
   182|  register(api) {
   183|    configureMemoryCoreDreamingState(<T>(options: OpenKeyedStoreOptions) =>
   184|      api.runtime.state.openKeyedStore<T>(options),
   185|    );
   186|    registerBuiltInMemoryEmbeddingProviders(api);
   187|    registerShortTermPromotionDreaming(api);
   188|    api.registerMemoryCapability({
   189|      promptBuilder: buildPromptSection,
   190|      flushPlanResolver: buildMemoryFlushPlan,
   191|      runtime: memoryRuntime,
   192|      publicArtifacts: {
   193|        async listArtifacts(params) {
   194|          const { listMemoryCorePublicArtifacts } = await import("./src/public-artifacts.js");
   195|          return await listMemoryCorePublicArtifacts(params);
   196|        },
   197|      },
   198|    });
   199|
   200|    api.registerTool((ctx) => createLazyMemorySearchTool(resolveMemoryToolOptions(ctx)), {
   201|      names: ["memory_search"],
   202|    });
   203|
   204|    api.registerTool((ctx) => createLazyMemoryGetTool(resolveMemoryToolOptions(ctx)), {
   205|      names: ["memory_get"],
   206|    });
```
