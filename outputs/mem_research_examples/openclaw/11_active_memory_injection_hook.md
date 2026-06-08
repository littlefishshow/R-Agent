# before_prompt_build 注入

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/active-memory/index.ts#L3003-L3104
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/active-memory/index.ts`

## 说明

在主 prompt build 前召回 memory，返回 prependContext。

```ts
  3003|    const beforePromptBuildTimeoutMs = config.timeoutMs + config.setupGraceTimeoutMs;
  3004|    api.on(
  3005|      "before_prompt_build",
  3006|      async (event, ctx) => {
  3007|        try {
  3008|          refreshLiveConfigFromRuntime();
  3009|          const resolvedAgentId = resolveStatusUpdateAgentId(ctx);
  3010|          const resolvedSessionKey =
  3011|            ctx.sessionKey?.trim() ||
  3012|            (resolvedAgentId
  3013|              ? resolveCanonicalSessionKeyFromSessionId({
  3014|                  api,
  3015|                  agentId: resolvedAgentId,
  3016|                  sessionId: ctx.sessionId,
  3017|                })
  3018|              : undefined);
  3019|          const effectiveAgentId =
  3020|            resolvedAgentId || resolveStatusUpdateAgentId({ sessionKey: resolvedSessionKey });
  3021|          if (await isSessionActiveMemoryDisabled({ api, sessionKey: resolvedSessionKey })) {
  3022|            await persistPluginStatusLines({
  3023|              api,
  3024|              agentId: effectiveAgentId,
  3025|              sessionKey: resolvedSessionKey,
  3026|            });
  3027|            return undefined;
  3028|          }
  3029|          if (!isEnabledForAgent(config, effectiveAgentId)) {
  3030|            await persistPluginStatusLines({
  3031|              api,
  3032|              agentId: effectiveAgentId,
  3033|              sessionKey: resolvedSessionKey,
  3034|            });
  3035|            return undefined;
  3036|          }
  3037|          if (!isEligibleInteractiveSession(ctx)) {
  3038|            await persistPluginStatusLines({
  3039|              api,
  3040|              agentId: effectiveAgentId,
  3041|              sessionKey: resolvedSessionKey,
  3042|            });
  3043|            return undefined;
  3044|          }
  3045|          if (
  3046|            !isAllowedChatType(config, {
  3047|              ...ctx,
  3048|              sessionKey: resolvedSessionKey ?? ctx.sessionKey,
  3049|              mainKey: api.config.session?.mainKey,
  3050|            })
  3051|          ) {
  3052|            await persistPluginStatusLines({
  3053|              api,
  3054|              agentId: effectiveAgentId,
  3055|              sessionKey: resolvedSessionKey,
  3056|            });
  3057|            return undefined;
  3058|          }
  3059|          if (
  3060|            !isAllowedChatId(config, {
  3061|              sessionKey: resolvedSessionKey ?? ctx.sessionKey,
  3062|              messageProvider: ctx.messageProvider,
  3063|            })
  3064|          ) {
  3065|            await persistPluginStatusLines({
  3066|              api,
  3067|              agentId: effectiveAgentId,
  3068|              sessionKey: resolvedSessionKey,
  3069|            });
  3070|            return undefined;
  3071|          }
  3072|          const recentTurns = extractRecentTurns(event.messages);
  3073|          const query = buildQuery({
  3074|            latestUserMessage: event.prompt,
  3075|            recentTurns,
  3076|            config,
  3077|          });
  3078|          const searchQuery = buildSearchQuery({
  3079|            latestUserMessage: event.prompt,
  3080|            recentTurns,
  3081|          });
  3082|          const result = await maybeResolveActiveRecall({
  3083|            api,
  3084|            config,
  3085|            agentId: effectiveAgentId,
  3086|            sessionKey: resolvedSessionKey,
  3087|            sessionId: ctx.sessionId,
  3088|            messageProvider: ctx.messageProvider,
  3089|            channelId: ctx.channelId,
  3090|            query,
  3091|            searchQuery,
  3092|            currentModelProviderId: ctx.modelProviderId,
  3093|            currentModelId: ctx.modelId,
  3094|          });
  3095|          if (!result.summary) {
  3096|            return undefined;
  3097|          }
  3098|          const promptPrefix = buildPromptPrefix(result.summary);
  3099|          if (!promptPrefix) {
  3100|            return undefined;
  3101|          }
  3102|          return {
  3103|            prependContext: promptPrefix,
  3104|          };
```
