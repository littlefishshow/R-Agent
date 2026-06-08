# Active Memory 子 agent 运行

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/active-memory/index.ts#L2409-L2520
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/active-memory/index.ts`

## 说明

runEmbeddedAgent，lane=active-memory，仅允许 memory_search/memory_get，轻量 bootstrap，silentExpected。

```ts
  2409|async function runRecallSubagent(params: {
  2410|  api: OpenClawPluginApi;
  2411|  config: ResolvedActiveRecallPluginConfig;
  2412|  agentId: string;
  2413|  sessionKey?: string;
  2414|  sessionId?: string;
  2415|  messageProvider?: string;
  2416|  channelId?: string;
  2417|  query: string;
  2418|  searchQuery: string;
  2419|  currentModelProviderId?: string;
  2420|  currentModelId?: string;
  2421|  modelRef?: { provider: string; model: string };
  2422|  abortSignal?: AbortSignal;
  2423|  onSessionFile?: (sessionFile: string) => void;
  2424|}): Promise<RecallSubagentResult> {
  2425|  const workspaceDir = resolveAgentWorkspaceDir(params.api.config, params.agentId);
  2426|  const agentDir = resolveAgentDir(params.api.config, params.agentId);
  2427|  const modelRef =
  2428|    params.modelRef ??
  2429|    getModelRef(params.api, params.agentId, params.config, {
  2430|      modelProviderId: params.currentModelProviderId,
  2431|      modelId: params.currentModelId,
  2432|    });
  2433|  if (!modelRef) {
  2434|    return { rawReply: "NONE" };
  2435|  }
  2436|  const subagentSessionId = `active-memory-${Date.now().toString(36)}-${crypto.randomUUID().slice(0, 8)}`;
  2437|  const parentSessionKey =
  2438|    params.sessionKey ??
  2439|    resolveCanonicalSessionKeyFromSessionId({
  2440|      api: params.api,
  2441|      agentId: params.agentId,
  2442|      sessionId: params.sessionId,
  2443|    });
  2444|  const subagentScope = parentSessionKey ?? params.sessionId ?? crypto.randomUUID();
  2445|  const subagentSuffix = `active-memory:${crypto
  2446|    .createHash("sha1")
  2447|    .update(`${subagentScope}:${params.query}`)
  2448|    .digest("hex")
  2449|    .slice(0, 12)}`;
  2450|  const subagentSessionKey = parentSessionKey
  2451|    ? `${parentSessionKey}:${subagentSuffix}`
  2452|    : `agent:${params.agentId}:${subagentSuffix}`;
  2453|  const transientWorkspace = params.config.persistTranscripts
  2454|    ? undefined
  2455|    : await tempWorkspace({
  2456|        rootDir: resolvePreferredOpenClawTmpDir(),
  2457|        prefix: "openclaw-active-memory-",
  2458|      });
  2459|  const tempDir = transientWorkspace?.dir;
  2460|  const persistedDir = params.config.persistTranscripts
  2461|    ? resolveSafeTranscriptDir(
  2462|        resolvePersistentTranscriptBaseDir(params.api, params.agentId),
  2463|        params.config.transcriptDir,
  2464|      )
  2465|    : undefined;
  2466|  const sessionFile =
  2467|    persistedDir !== undefined
  2468|      ? path.join(persistedDir, `${subagentSessionId}.jsonl`)
  2469|      : path.join(requireTransientWorkspaceDir(tempDir), "session.jsonl");
  2470|  params.onSessionFile?.(sessionFile);
  2471|  if (persistedDir) {
  2472|    await fs.mkdir(persistedDir, { recursive: true, mode: 0o700 });
  2473|    await fs.chmod(persistedDir, 0o700).catch(() => undefined);
  2474|  }
  2475|  const prompt = buildRecallPrompt({
  2476|    config: params.config,
  2477|    query: params.query,
  2478|    searchQuery: params.searchQuery,
  2479|  });
  2480|  const { messageChannel, messageProvider } = resolveRecallRunChannelContext({
  2481|    api: params.api,
  2482|    agentId: params.agentId,
  2483|    sessionKey: parentSessionKey,
  2484|    sessionId: params.sessionId,
  2485|    messageProvider: params.messageProvider,
  2486|    channelId: params.channelId,
  2487|  });
  2488|
  2489|  try {
  2490|    const embeddedConfig = applyActiveMemoryRuntimeConfigSnapshot(params.api.config, params.config);
  2491|    const embeddedTimeoutMs = params.config.timeoutMs + params.config.setupGraceTimeoutMs;
  2492|    const result = await params.api.runtime.agent.runEmbeddedAgent({
  2493|      sessionId: subagentSessionId,
  2494|      sessionKey: subagentSessionKey,
  2495|      agentId: params.agentId,
  2496|      messageChannel,
  2497|      messageProvider,
  2498|      sessionFile,
  2499|      workspaceDir,
  2500|      agentDir,
  2501|      config: embeddedConfig,
  2502|      prompt,
  2503|      provider: modelRef.provider,
  2504|      model: modelRef.model,
  2505|      lane: ACTIVE_MEMORY_RECALL_LANE,
  2506|      timeoutMs: embeddedTimeoutMs,
  2507|      runId: subagentSessionId,
  2508|      trigger: "manual",
  2509|      toolsAllow: [...params.config.toolsAllow],
  2510|      disableMessageTool: true,
  2511|      allowGatewaySubagentBinding: true,
  2512|      bootstrapContextMode: "lightweight",
  2513|      verboseLevel: "off",
  2514|      thinkLevel: params.config.thinking,
  2515|      reasoningLevel: "off",
  2516|      silentExpected: true,
  2517|      authProfileFailurePolicy: "local",
  2518|      cleanupBundleMcpOnRunEnd: true,
  2519|      abortSignal: params.abortSignal,
  2520|    });
```
