# Compaction 前 memory flush plan

Source: https://github.com/openclaw/openclaw/blob/538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d/extensions/memory-core/src/flush-plan.ts#L12-L142
Commit: `538d36eaaaa6349a6539a2ad3d13dac7ed4c1f1d`
Local source: `extensions/memory-core/src/flush-plan.ts`

## 说明

接近 compaction 时提示只 append 到 memory/YYYY-MM-DD.md，不覆盖 MEMORY.md 等。

```ts
    12|export const DEFAULT_MEMORY_FLUSH_SOFT_TOKENS = 4000;
    13|export const DEFAULT_MEMORY_FLUSH_FORCE_TRANSCRIPT_BYTES = 2 * 1024 * 1024;
    14|
    15|const MEMORY_FLUSH_TARGET_HINT =
    16|  "Store durable memories only in memory/YYYY-MM-DD.md (create memory/ if needed).";
    17|const MEMORY_FLUSH_APPEND_ONLY_HINT =
    18|  "If memory/YYYY-MM-DD.md already exists, APPEND new content only and do not overwrite existing entries.";
    19|const MEMORY_FLUSH_READ_ONLY_HINT =
    20|  "Treat workspace bootstrap/reference files such as MEMORY.md, DREAMS.md, SOUL.md, TOOLS.md, and AGENTS.md as read-only during this flush; never overwrite, replace, or edit them.";
    21|const MEMORY_FLUSH_REQUIRED_HINTS = [
    22|  MEMORY_FLUSH_TARGET_HINT,
    23|  MEMORY_FLUSH_APPEND_ONLY_HINT,
    24|  MEMORY_FLUSH_READ_ONLY_HINT,
    25|];
    26|
    27|export const DEFAULT_MEMORY_FLUSH_PROMPT = [
    28|  "Pre-compaction memory flush.",
    29|  MEMORY_FLUSH_TARGET_HINT,
    30|  MEMORY_FLUSH_READ_ONLY_HINT,
    31|  MEMORY_FLUSH_APPEND_ONLY_HINT,
    32|  "Do NOT create timestamped variant files (e.g., YYYY-MM-DD-HHMM.md); always use the canonical YYYY-MM-DD.md filename.",
    33|  `If nothing to store, reply with ${SILENT_REPLY_TOKEN}.`,
    34|].join(" ");
    35|
    36|const DEFAULT_MEMORY_FLUSH_SYSTEM_PROMPT = [
    37|  "Pre-compaction memory flush turn.",
    38|  "The session is near auto-compaction; capture durable memories to disk.",
    39|  MEMORY_FLUSH_TARGET_HINT,
    40|  MEMORY_FLUSH_READ_ONLY_HINT,
    41|  MEMORY_FLUSH_APPEND_ONLY_HINT,
    42|  `You may reply, but usually ${SILENT_REPLY_TOKEN} is correct.`,
    43|].join(" ");
    44|
    45|function formatDateStampInTimezone(nowMs: number, timezone: string): string {
    46|  const parts = new Intl.DateTimeFormat("en-US", {
    47|    timeZone: timezone,
    48|    year: "numeric",
    49|    month: "2-digit",
    50|    day: "2-digit",
    51|  }).formatToParts(new Date(nowMs));
    52|  const year = parts.find((part) => part.type === "year")?.value;
    53|  const month = parts.find((part) => part.type === "month")?.value;
    54|  const day = parts.find((part) => part.type === "day")?.value;
    55|  if (year && month && day) {
    56|    return `${year}-${month}-${day}`;
    57|  }
    58|  return new Date(resolveMemoryCoreNowMs(nowMs)).toISOString().slice(0, 10);
    59|}
    60|
    61|function normalizeNonNegativeInt(value: unknown): number | null {
    62|  if (typeof value !== "number" || !Number.isFinite(value)) {
    63|    return null;
    64|  }
    65|  const int = Math.floor(value);
    66|  return int >= 0 ? int : null;
    67|}
    68|
    69|function ensureNoReplyHint(text: string): string {
    70|  if (text.includes(SILENT_REPLY_TOKEN)) {
    71|    return text;
    72|  }
    73|  return `${text}\n\nIf no user-visible reply is needed, start with ${SILENT_REPLY_TOKEN}.`;
    74|}
    75|
    76|function ensureMemoryFlushSafetyHints(text: string): string {
    77|  let next = text.trim();
    78|  for (const hint of MEMORY_FLUSH_REQUIRED_HINTS) {
    79|    if (!next.includes(hint)) {
    80|      next = next ? `${next}\n\n${hint}` : hint;
    81|    }
    82|  }
    83|  return next;
    84|}
    85|
    86|function appendCurrentTimeLine(text: string, timeLine: string): string {
    87|  const trimmed = text.trimEnd();
    88|  if (!trimmed) {
    89|    return timeLine;
    90|  }
    91|  if (trimmed.includes("Current time:")) {
    92|    return trimmed;
    93|  }
    94|  return `${trimmed}\n${timeLine}`;
    95|}
    96|
    97|export function buildMemoryFlushPlan(
    98|  params: {
    99|    cfg?: OpenClawConfig;
   100|    nowMs?: number;
   101|  } = {},
   102|): MemoryFlushPlan | null {
   103|  const resolved = params;
   104|  const nowMs = resolveMemoryCoreNowMs(resolved.nowMs);
   105|  const cfg = resolved.cfg;
   106|  const defaults = cfg?.agents?.defaults?.compaction?.memoryFlush;
   107|  if (defaults?.enabled === false) {
   108|    return null;
   109|  }
   110|
   111|  const softThresholdTokens =
   112|    normalizeNonNegativeInt(defaults?.softThresholdTokens) ?? DEFAULT_MEMORY_FLUSH_SOFT_TOKENS;
   113|  const forceFlushTranscriptBytes =
   114|    parseNonNegativeByteSize(defaults?.forceFlushTranscriptBytes) ??
   115|    DEFAULT_MEMORY_FLUSH_FORCE_TRANSCRIPT_BYTES;
   116|  const reserveTokensFloor =
   117|    normalizeNonNegativeInt(cfg?.agents?.defaults?.compaction?.reserveTokensFloor) ??
   118|    DEFAULT_AGENT_COMPACTION_RESERVE_TOKENS_FLOOR;
   119|
   120|  const { timeLine, userTimezone } = resolveCronStyleNow(cfg ?? {}, nowMs);
   121|  const dateStamp = formatDateStampInTimezone(nowMs, userTimezone);
   122|  const relativePath = `memory/${dateStamp}.md`;
   123|
   124|  const promptBase = ensureNoReplyHint(
   125|    ensureMemoryFlushSafetyHints(defaults?.prompt?.trim() || DEFAULT_MEMORY_FLUSH_PROMPT),
   126|  );
   127|  const systemPrompt = ensureNoReplyHint(
   128|    ensureMemoryFlushSafetyHints(
   129|      defaults?.systemPrompt?.trim() || DEFAULT_MEMORY_FLUSH_SYSTEM_PROMPT,
   130|    ),
   131|  );
   132|
   133|  return {
   134|    softThresholdTokens,
   135|    forceFlushTranscriptBytes,
   136|    reserveTokensFloor,
   137|    model: defaults?.model?.trim() || undefined,
   138|    prompt: appendCurrentTimeLine(promptBase.replaceAll("YYYY-MM-DD", dateStamp), timeLine),
   139|    systemPrompt: systemPrompt.replaceAll("YYYY-MM-DD", dateStamp),
   140|    relativePath,
   141|  };
   142|}
```
