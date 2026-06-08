# Auto-capture 与 user profile learning 示例

## Source: `opencode-mem/src/index.ts` lines 480-506

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/index.ts#L480-L506
- 说明: OpenCode event hook 在 session.idle 后延迟触发 auto-capture、用户画像学习、清理和 WAL checkpoint。

```ts
 480:     event: async (input: { event: { type: string; properties?: any } }) => {
 481:       const event = input.event;
 482:       if (event.type === "session.idle") {
 483:         if (!isConfigured() || !CONFIG.autoCaptureEnabled) return;
 484:         const sessionID = event.properties?.sessionID;
 485:         if (!sessionID) return;
 486: 
 487:         if (idleTimeout) clearTimeout(idleTimeout);
 488: 
 489:         idleTimeout = setTimeout(async () => {
 490:           try {
 491:             await performAutoCapture(ctx, sessionID, directory);
 492: 
 493:             if (webServer?.isServerOwner()) {
 494:               await performUserProfileLearning(ctx, directory);
 495:               const { cleanupService } = await import("./services/cleanup-service.js");
 496:               if (await cleanupService.shouldRunCleanup()) await cleanupService.runCleanup();
 497:               const { connectionManager } = await import("./services/sqlite/connection-manager.js");
 498:               connectionManager.checkpointAll();
 499:             }
 500:           } catch (error) {
 501:             log("Idle processing error", { error: String(error) });
 502:           } finally {
 503:             idleTimeout = null;
 504:           }
 505:         }, 10000);
 506:       }
```

## Source: `opencode-mem/src/services/auto-capture.ts` lines 17-90

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/auto-capture.ts#L17-L90
- 说明: auto-capture 取最后未捕获 user prompt，读取该 prompt 之后的 AI messages，构造上下文并生成 summary 后保存为 memory。

```ts
  17: export async function performAutoCapture(
  18:   ctx: PluginInput,
  19:   sessionID: string,
  20:   directory: string
  21: ): Promise<void> {
  22:   if (isCaptureRunning) return;
  23:   isCaptureRunning = true;
  24:   try {
  25:     const prompt = userPromptManager.getLastUncapturedPrompt(sessionID);
  26:     if (!prompt) {
  27:       return;
  28:     }
  29: 
  30:     if (!userPromptManager.claimPrompt(prompt.id)) {
  31:       return;
  32:     }
  33: 
  34:     if (!ctx.client) {
  35:       throw new Error("Client not available");
  36:     }
  37: 
  38:     const response = await ctx.client.session.messages({
  39:       path: { id: sessionID },
  40:     });
  41: 
  42:     if (!response.data) {
  43:       return;
  44:     }
  45: 
  46:     const messages = response.data;
  47: 
  48:     const promptIndex = messages.findIndex((m: any) => m.info?.id === prompt.messageId);
  49:     if (promptIndex === -1) {
  50:       return;
  51:     }
  52: 
  53:     const aiMessages = messages.slice(promptIndex + 1);
  54: 
  55:     if (aiMessages.length === 0) {
  56:       return;
  57:     }
  58: 
  59:     const { textResponses, toolCalls } = extractAIContent(aiMessages);
  60: 
  61:     if (textResponses.length === 0 && toolCalls.length === 0) {
  62:       return;
  63:     }
  64: 
  65:     const tags = getTags(directory);
  66:     const latestMemory = await getLatestProjectMemory(tags.project.tag);
  67: 
  68:     const context = buildMarkdownContext(prompt.content, textResponses, toolCalls, latestMemory);
  69: 
  70:     const summaryResult = await generateSummary(context, sessionID, prompt.content);
  71: 
  72:     if (!summaryResult || summaryResult.type === "skip") {
  73:       userPromptManager.deletePrompt(prompt.id);
  74:       return;
  75:     }
  76: 
  77:     const result = await memoryClient.addMemory(summaryResult.summary, tags.project.tag, {
  78:       source: "auto-capture" as any,
  79:       type: summaryResult.type as any,
  80:       tags: summaryResult.tags,
  81:       sessionID,
  82:       promptId: prompt.id,
  83:       captureTimestamp: Date.now(),
  84:       displayName: tags.project.displayName,
  85:       userName: tags.project.userName,
  86:       userEmail: tags.project.userEmail,
  87:       projectPath: tags.project.projectPath,
  88:       projectName: tags.project.projectName,
  89:       gitRepoUrl: tags.project.gitRepoUrl,
  90:     });
```

## Source: `opencode-mem/src/services/auto-capture.ts` lines 231-308

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/auto-capture.ts#L231-L308
- 说明: auto-capture 的 structured-output prompt，要求只捕获技术工作、抽取 title/summary/actions/files 等结构化信息。

```ts
 231:   context: string,
 232:   sessionID: string,
 233:   userPrompt: string
 234: ): Promise<{ summary: string; type: string; tags: string[] } | null> {
 235:   // Opencode provider path (when opencodeProvider + opencodeModel configured)
 236:   if (CONFIG.opencodeProvider && CONFIG.opencodeModel) {
 237:     if (CONFIG.memoryModel) {
 238:       log("opencodeProvider takes precedence over memoryModel for auto-capture");
 239:     }
 240: 
 241:     const { isProviderConnected, getV2Client, generateStructuredOutput } =
 242:       await import("./ai/opencode-provider.js");
 243: 
 244:     if (!isProviderConnected(CONFIG.opencodeProvider)) {
 245:       throw new Error(
 246:         `opencode provider '${CONFIG.opencodeProvider}' is not connected. Check your opencode provider configuration.`
 247:       );
 248:     }
 249: 
 250:     const v2Client = getV2Client();
 251:     if (!v2Client) {
 252:       throw new Error(
 253:         "opencode-mem: v2 client not initialized; cannot perform structured-output capture"
 254:       );
 255:     }
 256: 
 257:     const { detectLanguage, getLanguageName } = await import("./language-detector.js");
 258:     const targetLang =
 259:       CONFIG.autoCaptureLanguage === "auto" || !CONFIG.autoCaptureLanguage
 260:         ? detectLanguage(userPrompt)
 261:         : CONFIG.autoCaptureLanguage;
 262:     const langName = getLanguageName(targetLang);
 263: 
 264:     const systemPrompt = `You are a technical memory recorder for a software development project.
 265: 
 266: RULES:
 267: 1. ONLY capture technical work (code, bugs, features, architecture, config)
 268: 2. SKIP non-technical by returning type="skip"
 269: 3. NO meta-commentary or behavior analysis
 270: 4. Include specific file names, functions, technical details
 271: 5. Generate 2-4 technical tags (e.g., "react", "auth", "bug-fix")
 272: 6. You MUST write the summary in ${langName}.
 273: 
 274: FORMAT:
 275: ## Request
 276: [1-2 sentences: what was requested, in ${langName}]
 277: 
 278: ## Outcome
 279: [1-2 sentences: what was done, include files/functions, in ${langName}]
 280: 
 281: SKIP if: greetings, casual chat, no code/decisions made
 282: CAPTURE if: code changed, bug fixed, feature added, decision made`;
 283: 
 284:     const aiPrompt = `${context}
 285: 
 286: Analyze this conversation. If it contains technical work (code, bugs, features, decisions), create a concise summary and relevant tags. If it's non-technical (greetings, casual chat, incomplete requests), return type="skip" with empty summary.`;
 287: 
 288:     const { z } = await import("zod");
 289:     const schema = z.object({
 290:       summary: z.string(),
 291:       type: z.string(),
 292:       tags: z.array(z.string()),
 293:     });
 294: 
 295:     const result = await generateStructuredOutput({
 296:       client: v2Client,
 297:       providerID: CONFIG.opencodeProvider,
 298:       modelID: CONFIG.opencodeModel,
 299:       systemPrompt,
 300:       userPrompt: aiPrompt,
 301:       schema,
 302:     });
 303: 
 304:     return {
 305:       summary: result.summary,
 306:       type: result.type,
 307:       tags: (result.tags || []).map((t: string) => t.toLowerCase().trim()),
 308:     };
```

## Source: `opencode-mem/src/services/user-memory-learning.ts` lines 1-90

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/user-memory-learning.ts#L1-L90
- 说明: 用户画像学习入口：按用户读取未分析 prompts，构造上下文、调用 LLM 更新 profile，并记录学习结果。

```ts
   1: import type { PluginInput } from "@opencode-ai/plugin";
   2: import { getTags } from "./tags.js";
   3: import { log } from "./logger.js";
   4: import { CONFIG } from "../config.js";
   5: import { userPromptManager } from "./user-prompt/user-prompt-manager.js";
   6: import type { UserPrompt } from "./user-prompt/user-prompt-manager.js";
   7: import { userProfileManager } from "./user-profile/user-profile-manager.js";
   8: import type { UserProfile, UserProfileData } from "./user-profile/types.js";
   9: 
  10: let isLearningRunning = false;
  11: 
  12: export async function performUserProfileLearning(
  13:   ctx: PluginInput,
  14:   directory: string
  15: ): Promise<void> {
  16:   if (isLearningRunning) return;
  17:   isLearningRunning = true;
  18:   try {
  19:     const count = userPromptManager.countUnanalyzedForUserLearning();
  20:     const threshold = CONFIG.userProfileAnalysisInterval;
  21: 
  22:     if (count < threshold) {
  23:       return;
  24:     }
  25: 
  26:     const prompts = userPromptManager.getPromptsForUserLearning(threshold);
  27: 
  28:     if (prompts.length === 0) {
  29:       return;
  30:     }
  31: 
  32:     const tags = getTags(directory);
  33:     const userId = tags.user.userEmail || "unknown";
  34: 
  35:     const existingProfile = userProfileManager.getActiveProfile(userId);
  36: 
  37:     const context = buildUserAnalysisContext(prompts, existingProfile);
  38: 
  39:     const updatedProfileData = await analyzeUserProfile(context, existingProfile);
  40: 
  41:     if (!updatedProfileData) {
  42:       userPromptManager.markMultipleAsUserLearningCaptured(prompts.map((p) => p.id));
  43:       return;
  44:     }
  45: 
  46:     if (existingProfile) {
  47:       const changeSummary = generateChangeSummary(
  48:         JSON.parse(existingProfile.profileData),
  49:         updatedProfileData
  50:       );
  51:       userProfileManager.updateProfile(
  52:         existingProfile.id,
  53:         updatedProfileData,
  54:         prompts.length,
  55:         changeSummary
  56:       );
  57:     } else {
  58:       userProfileManager.createProfile(
  59:         userId,
  60:         tags.user.displayName || "Unknown",
  61:         tags.user.userName || "unknown",
  62:         tags.user.userEmail || "unknown",
  63:         updatedProfileData,
  64:         prompts.length
  65:       );
  66:     }
  67: 
  68:     userPromptManager.markMultipleAsUserLearningCaptured(prompts.map((p) => p.id));
  69: 
  70:     if (CONFIG.showUserProfileToasts) {
  71:       await ctx.client?.tui
  72:         .showToast({
  73:           body: {
  74:             title: "User Profile Updated",
  75:             message: `Analyzed ${prompts.length} prompts and updated your profile`,
  76:             variant: "success",
  77:             duration: 3000,
  78:           },
  79:         })
  80:         .catch(() => {});
  81:     }
  82:   } finally {
  83:     isLearningRunning = false;
  84:   }
  85: }
  86: 
  87: function generateChangeSummary(oldProfile: UserProfileData, newProfile: UserProfileData): string {
  88:   const changes: string[] = [];
  89: 
  90:   const prefDiff = newProfile.preferences.length - oldProfile.preferences.length;
```

## Source: `opencode-mem/src/services/user-memory-learning.ts` lines 132-220

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/user-memory-learning.ts#L132-L220
- 说明: 用户画像分析 prompt：从用户 prompts 中学习偏好、沟通风格、工作流、技术栈等，并要求保留/演化既有画像。

```ts
 132:    - Assign confidence 0.5-1.0 based on evidence strength
 133:    - Include 1-3 example prompts as evidence
 134: 
 135: 2. **Patterns** (max ${CONFIG.userProfileMaxPatterns})
 136:    - Recurring topics, problem domains, technical interests
 137:    - Track frequency of occurrence
 138: 
 139: 3. **Workflows** (max ${CONFIG.userProfileMaxWorkflows})
 140:    - Development sequences, habits, learning style
 141:    - Break down into steps if applicable
 142: 
 143: ${existingProfile ? "Merge with existing profile, incrementing frequencies and updating confidence scores." : "Create initial profile with conservative confidence scores."}`;
 144: }
 145: 
 146: async function analyzeUserProfile(
 147:   context: string,
 148:   existingProfile: UserProfile | null
 149: ): Promise<UserProfileData | null> {
 150:   if (CONFIG.opencodeProvider && CONFIG.opencodeModel) {
 151:     const { isProviderConnected, getV2Client, generateStructuredOutput } =
 152:       await import("./ai/opencode-provider.js");
 153: 
 154:     if (!isProviderConnected(CONFIG.opencodeProvider)) {
 155:       throw new Error(
 156:         `opencode provider '${CONFIG.opencodeProvider}' is not connected. Check your opencode provider configuration.`
 157:       );
 158:     }
 159: 
 160:     const v2Client = getV2Client();
 161:     if (!v2Client) {
 162:       throw new Error(
 163:         "opencode-mem: v2 client not initialized; cannot perform user-profile learning"
 164:       );
 165:     }
 166: 
 167:     const systemPrompt = `You are a user behavior analyst for a coding assistant.
 168: 
 169: Your task is to analyze user prompts and ${existingProfile ? "update" : "create"} a comprehensive user profile.
 170: 
 171: CRITICAL: Detect the language used by the user in their prompts. You MUST output all descriptions, categories, and text in the SAME language as the user's prompts.
 172: 
 173: Use the update_user_profile tool to save the ${existingProfile ? "updated" : "new"} profile.`;
 174: 
 175:     const { z } = await import("zod");
 176:     const schema = z.object({
 177:       preferences: z.array(
 178:         z.object({
 179:           category: z.string(),
 180:           description: z.string(),
 181:           confidence: z.number(),
 182:           evidence: z.array(z.string()),
 183:         })
 184:       ),
 185:       patterns: z.array(
 186:         z.object({
 187:           category: z.string(),
 188:           description: z.string(),
 189:         })
 190:       ),
 191:       workflows: z.array(
 192:         z.object({
 193:           description: z.string(),
 194:           steps: z.array(z.string()),
 195:         })
 196:       ),
 197:     });
 198: 
 199:     const result = await generateStructuredOutput({
 200:       client: v2Client,
 201:       providerID: CONFIG.opencodeProvider,
 202:       modelID: CONFIG.opencodeModel,
 203:       systemPrompt,
 204:       userPrompt: context,
 205:       schema,
 206:     });
 207: 
 208:     if (existingProfile) {
 209:       const existingData: UserProfileData = JSON.parse(existingProfile.profileData);
 210:       return userProfileManager.mergeProfileData(
 211:         existingData,
 212:         result as unknown as Partial<UserProfileData>
 213:       );
 214:     }
 215:     return result as UserProfileData;
 216:   }
 217: 
 218:   if (!CONFIG.memoryModel || !CONFIG.memoryApiUrl) {
 219:     log("User Profile Config Check Failed:", {
 220:       memoryModel: CONFIG.memoryModel,
```
