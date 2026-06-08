# chat.message 注入与 compaction restore 示例

## Source: `opencode-mem/src/index.ts` lines 147-228

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/index.ts#L147-L228
- 说明: chat.message hook 在首轮/always/compaction 后条件满足时，把项目 memories 格式化为 synthetic text part 并 unshift 到用户消息前。

```ts
 147:   return {
 148:     "chat.message": async (input, output) => {
 149:       if (!isConfigured() || !CONFIG.chatMessage.enabled) return;
 150: 
 151:       try {
 152:         const textParts = output.parts.filter(
 153:           (p): p is Part & { type: "text"; text: string } => p.type === "text"
 154:         );
 155: 
 156:         if (textParts.length === 0) return;
 157:         const userMessage = textParts.map((p) => p.text).join("\n");
 158:         if (!userMessage.trim()) return;
 159: 
 160:         userPromptManager.savePrompt(input.sessionID, output.message.id, directory, userMessage);
 161: 
 162:         const messagesResponse = await ctx.client.session.messages({
 163:           path: { id: input.sessionID },
 164:         });
 165:         const messages = messagesResponse.data || [];
 166: 
 167:         const hasNonSyntheticUserMessages = messages.some(
 168:           (m) =>
 169:             m.info.role === "user" &&
 170:             !m.parts.every((p) => p.type !== "text" || p.synthetic === true)
 171:         );
 172: 
 173:         const lastMessage = messages.length > 0 ? messages[messages.length - 1] : null;
 174:         const isAfterCompaction = lastMessage?.info?.summary === true;
 175: 
 176:         const shouldInject =
 177:           CONFIG.chatMessage.injectOn === "always" ||
 178:           !hasNonSyntheticUserMessages ||
 179:           (isAfterCompaction &&
 180:             messages.filter(
 181:               (m) =>
 182:                 m.info.role === "user" &&
 183:                 !m.parts.every((p) => p.type !== "text" || p.synthetic === true)
 184:             ).length === 1);
 185: 
 186:         if (!shouldInject) return;
 187: 
 188:         const listResult = await memoryClient.listMemories(
 189:           tags.project.tag,
 190:           CONFIG.chatMessage.maxMemories
 191:         );
 192: 
 193:         let memories = listResult.success ? listResult.memories : [];
 194: 
 195:         if (CONFIG.chatMessage.excludeCurrentSession) {
 196:           memories = memories.filter((m: any) => m.metadata?.sessionID !== input.sessionID);
 197:         }
 198: 
 199:         if (CONFIG.chatMessage.maxAgeDays) {
 200:           const cutoffDate = Date.now() - CONFIG.chatMessage.maxAgeDays * 86400000;
 201:           memories = memories.filter((m: any) => new Date(m.createdAt).getTime() > cutoffDate);
 202:         }
 203: 
 204:         if (memories.length === 0) return;
 205: 
 206:         const projectMemories = {
 207:           results: memories.map((m: any) => ({
 208:             similarity: 1.0,
 209:             memory: m.summary,
 210:           })),
 211:           total: memories.length,
 212:           timing: 0,
 213:         };
 214: 
 215:         const userId = tags.user.userEmail || null;
 216:         const memoryContext = formatContextForPrompt(userId, projectMemories);
 217: 
 218:         if (memoryContext) {
 219:           const contextPart: Part = {
 220:             id: `prt-memory-context-${Date.now()}`,
 221:             sessionID: input.sessionID,
 222:             messageID: output.message.id,
 223:             type: "text",
 224:             text: memoryContext,
 225:             synthetic: true,
 226:           } as any;
 227:           output.parts.unshift(contextPart);
 228:         }
```

## Source: `opencode-mem/src/index.ts` lines 508-550

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/index.ts#L508-L550
- 说明: session.compacted 事件后按 sessionID 搜索相关 memories，并通过 session.prompt(noReply=true) 注入 Memory Restored 内容。

```ts
 508:       if (event.type === "session.compacted") {
 509:         if (!isConfigured() || !CONFIG.compaction.enabled) return;
 510: 
 511:         const sessionID = event.properties?.sessionID;
 512:         if (!sessionID) return;
 513: 
 514:         try {
 515:           const tags = getTags(directory);
 516: 
 517:           const memoriesResult = await memoryClient.searchMemoriesBySessionID(
 518:             sessionID,
 519:             tags.project.tag,
 520:             CONFIG.compaction.memoryLimit
 521:           );
 522: 
 523:           if (!memoriesResult.success || memoriesResult.results.length === 0) {
 524:             return;
 525:           }
 526: 
 527:           const memoryContext = formatMemoriesForCompaction(memoriesResult.results);
 528: 
 529:           await ctx.client.session.prompt({
 530:             path: { id: sessionID },
 531:             body: {
 532:               parts: [{ id: `prt-compaction-${Date.now()}`, type: "text", text: memoryContext }],
 533:               noReply: true,
 534:             },
 535:           });
 536: 
 537:           if (ctx.client?.tui) {
 538:             await ctx.client.tui
 539:               .showToast({
 540:                 body: {
 541:                   title: "Memory Restored",
 542:                   message: `${memoriesResult.results.length} memories injected after compaction`,
 543:                   variant: "success",
 544:                   duration: 3000,
 545:                 },
 546:               })
 547:               .catch(() => {});
 548:           }
 549: 
 550:           log("Compaction memory injected", {
```

## Source: `opencode-mem/src/services/context.ts` lines 1-140

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/context.ts#L1-L140
- 说明: context formatter 将 search results 转成提示词片段；其中包含 user profile section 和 project memories section。

```ts
   1: import { CONFIG } from "../config.js";
   2: import { getUserProfileContext } from "./user-profile/profile-context.js";
   3: 
   4: interface MemoryResultMinimal {
   5:   similarity: number;
   6:   memory?: string;
   7:   chunk?: string;
   8: }
   9: 
  10: interface MemoriesResponseMinimal {
  11:   results?: MemoryResultMinimal[];
  12: }
  13: 
  14: export function formatContextForPrompt(
  15:   userId: string | null,
  16:   projectMemories: MemoriesResponseMinimal
  17: ): string {
  18:   const parts: string[] = ["[MEMORY]"];
  19: 
  20:   if (CONFIG.injectProfile && userId) {
  21:     const profileContext = getUserProfileContext(userId);
  22:     if (profileContext) {
  23:       parts.push("\n" + profileContext);
  24:     }
  25:   }
  26: 
  27:   const projectResults = projectMemories.results || [];
  28:   if (projectResults.length > 0) {
  29:     parts.push("\nProject Knowledge:");
  30:     projectResults.forEach((mem) => {
  31:       const similarity = Math.round(mem.similarity * 100);
  32:       const content = mem.memory || mem.chunk || "";
  33:       parts.push(`- [${similarity}%] ${content}`);
  34:     });
  35:   }
  36: 
  37:   if (parts.length === 1) {
  38:     return "";
  39:   }
  40: 
  41:   return parts.join("\n");
  42: }
```
