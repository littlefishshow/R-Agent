# memory_get 精确读取

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/memory-core/src/tools.ts#L593-L677
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/memory-core/src/tools.ts`

## 说明

按 path/from/lines 读取 memory 文件片段，适合 search 后精读证据。

```ts
   593|export function createMemoryGetTool(options: {
   594|  config?: OpenClawConfig;
   595|  getConfig?: () => OpenClawConfig | undefined;
   596|  agentId?: string;
   597|  agentSessionKey?: string;
   598|}) {
   599|  return createMemoryTool({
   600|    options,
   601|    label: "Memory Get",
   602|    name: "memory_get",
   603|    description:
   604|      "Safe exact excerpt read from MEMORY.md or memory/*.md. Defaults to a bounded excerpt when lines are omitted, includes truncation/continuation info when more content exists, and `corpus=wiki` reads from registered compiled-wiki supplements.",
   605|    parameters: MemoryGetSchema,
   606|    execute:
   607|      ({ cfg, agentId }) =>
   608|      async (_toolCallId, params) => {
   609|        const rawParams = asToolParamsRecord(params);
   610|        const relPath = readStringParam(rawParams, "path", { required: true });
   611|        const from = readPositiveIntegerParam(rawParams, "from");
   612|        const lines = readPositiveIntegerParam(rawParams, "lines");
   613|        const requestedCorpus = readStringParam(rawParams, "corpus") as
   614|          | "memory"
   615|          | "wiki"
   616|          | "all"
   617|          | undefined;
   618|        const { readAgentMemoryFile, resolveMemoryBackendConfig } = await loadMemoryToolRuntime();
   619|        if (requestedCorpus === "wiki") {
   620|          const supplement = await getSupplementMemoryReadResult({
   621|            relPath,
   622|            from: from ?? undefined,
   623|            lines: lines ?? undefined,
   624|            agentSessionKey: options.agentSessionKey,
   625|            corpus: requestedCorpus,
   626|          });
   627|          return jsonResult(
   628|            supplement ?? {
   629|              path: relPath,
   630|              text: "",
   631|              disabled: true,
   632|              error: "wiki corpus result not found",
   633|            },
   634|          );
   635|        }
   636|        const resolved = resolveMemoryBackendConfig({ cfg, agentId });
   637|        if (resolved.backend === "builtin") {
   638|          return await executeMemoryReadResult({
   639|            read: async () =>
   640|              await readAgentMemoryFile({
   641|                cfg,
   642|                agentId,
   643|                relPath,
   644|                from: from ?? undefined,
   645|                lines: lines ?? undefined,
   646|              }),
   647|            requestedCorpus,
   648|            relPath,
   649|            from: from ?? undefined,
   650|            lines: lines ?? undefined,
   651|            agentSessionKey: options.agentSessionKey,
   652|          });
   653|        }
   654|        const memory = await getMemoryManagerContextWithPurpose({
   655|          cfg,
   656|          agentId,
   657|          purpose: "status",
   658|        });
   659|        if ("error" in memory) {
   660|          return jsonResult({ path: relPath, text: "", disabled: true, error: memory.error });
   661|        }
   662|        return await executeMemoryReadResult({
   663|          read: async () =>
   664|            await memory.manager.readFile({
   665|              relPath,
   666|              from: from ?? undefined,
   667|              lines: lines ?? undefined,
   668|            }),
   669|          requestedCorpus,
   670|          relPath,
   671|          from: from ?? undefined,
   672|          lines: lines ?? undefined,
   673|          agentSessionKey: options.agentSessionKey,
   674|        });
   675|      },
   676|  });
   677|}
```
