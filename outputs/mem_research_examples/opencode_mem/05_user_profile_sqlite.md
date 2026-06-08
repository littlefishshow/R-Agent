# User profile SQLite schema 示例

## Source: `opencode-mem/src/services/user-profile/user-profile-manager.ts` lines 1-120

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/user-profile/user-profile-manager.ts#L1-L120
- 说明: 用户画像单独存入 user-profiles.db，包含 user_profiles 与 changelog 表，用 JSON profile_data 保存画像主体。

```ts
   1: import { getDatabase } from "../sqlite/sqlite-bootstrap.js";
   2: import { join } from "node:path";
   3: import { connectionManager } from "../sqlite/connection-manager.js";
   4: import { CONFIG } from "../../config.js";
   5: import type { UserProfile, UserProfileChangelog, UserProfileData } from "./types.js";
   6: import { safeArray, safeObject } from "./profile-utils.js";
   7: 
   8: const Database = getDatabase();
   9: type DatabaseType = typeof Database.prototype;
  10: 
  11: const USER_PROFILES_DB_NAME = "user-profiles.db";
  12: 
  13: export class UserProfileManager {
  14:   private db: DatabaseType;
  15:   private readonly dbPath: string;
  16: 
  17:   constructor() {
  18:     this.dbPath = join(CONFIG.storagePath, USER_PROFILES_DB_NAME);
  19:     this.db = connectionManager.getConnection(this.dbPath);
  20:     this.initDatabase();
  21:   }
  22: 
  23:   private initDatabase(): void {
  24:     this.db.run(`
  25:       CREATE TABLE IF NOT EXISTS user_profiles (
  26:         id TEXT PRIMARY KEY,
  27:         user_id TEXT NOT NULL UNIQUE,
  28:         display_name TEXT NOT NULL,
  29:         user_name TEXT NOT NULL,
  30:         user_email TEXT NOT NULL,
  31:         profile_data TEXT NOT NULL,
  32:         version INTEGER NOT NULL DEFAULT 1,
  33:         created_at INTEGER NOT NULL,
  34:         last_analyzed_at INTEGER NOT NULL,
  35:         total_prompts_analyzed INTEGER NOT NULL DEFAULT 0,
  36:         is_active BOOLEAN NOT NULL DEFAULT 1
  37:       )
  38:     `);
  39: 
  40:     this.db.run(`
  41:       CREATE TABLE IF NOT EXISTS user_profile_changelogs (
  42:         id TEXT PRIMARY KEY,
  43:         profile_id TEXT NOT NULL,
  44:         version INTEGER NOT NULL,
  45:         change_type TEXT NOT NULL,
  46:         change_summary TEXT NOT NULL,
  47:         profile_data_snapshot TEXT NOT NULL,
  48:         created_at INTEGER NOT NULL,
  49:         FOREIGN KEY (profile_id) REFERENCES user_profiles(id) ON DELETE CASCADE
  50:       )
  51:     `);
  52: 
  53:     this.db.run("CREATE INDEX IF NOT EXISTS idx_user_profiles_user_id ON user_profiles(user_id)");
  54:     this.db.run(
  55:       "CREATE INDEX IF NOT EXISTS idx_user_profiles_is_active ON user_profiles(is_active)"
  56:     );
  57:     this.db.run(
  58:       "CREATE INDEX IF NOT EXISTS idx_user_profile_changelogs_profile_id ON user_profile_changelogs(profile_id)"
  59:     );
  60:     this.db.run(
  61:       "CREATE INDEX IF NOT EXISTS idx_user_profile_changelogs_version ON user_profile_changelogs(version DESC)"
  62:     );
  63:   }
  64: 
  65:   getActiveProfile(userId: string): UserProfile | null {
  66:     const stmt = this.db.prepare(`
  67:       SELECT * FROM user_profiles 
  68:       WHERE user_id = ? AND is_active = 1
  69:       LIMIT 1
  70:     `);
  71: 
  72:     const row = stmt.get(userId) as any;
  73:     if (!row) return null;
  74: 
  75:     return this.rowToProfile(row);
  76:   }
  77: 
  78:   createProfile(
  79:     userId: string,
  80:     displayName: string,
  81:     userName: string,
  82:     userEmail: string,
  83:     profileData: UserProfileData,
  84:     promptsAnalyzed: number
  85:   ): string {
  86:     const id = `profile_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
  87:     const now = Date.now();
  88: 
  89:     const cleanedData: UserProfileData = {
  90:       preferences: safeArray(profileData.preferences),
  91:       patterns: safeArray(profileData.patterns),
  92:       workflows: safeArray(profileData.workflows),
  93:     };
  94: 
  95:     const stmt = this.db.prepare(`
  96:       INSERT INTO user_profiles (
  97:         id, user_id, display_name, user_name, user_email, 
  98:         profile_data, version, created_at, last_analyzed_at, 
  99:         total_prompts_analyzed, is_active
 100:       )
 101:       VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1)
 102:     `);
 103: 
 104:     stmt.run(
 105:       id,
 106:       userId,
 107:       displayName,
 108:       userName,
 109:       userEmail,
 110:       JSON.stringify(cleanedData),
 111:       now,
 112:       now,
 113:       promptsAnalyzed
 114:     );
 115: 
 116:     this.addChangelog(id, 1, "create", "Initial profile creation", cleanedData);
 117: 
 118:     return id;
 119:   }
 120: 
```

## Source: `opencode-mem/src/services/user-profile/user-profile-manager.ts` lines 120-190

- Commit: `0a7805b8ddca859e97119f09dc63ceab5a532b94`
- URL: https://github.com/tickernelz/opencode-mem/blob/0a7805b8ddca859e97119f09dc63ceab5a532b94/src/services/user-profile/user-profile-manager.ts#L120-L190
- 说明: 更新画像时增加 version、累计 analyzed prompt 数，并写入 changelog snapshot。

```ts
 120: 
 121:   updateProfile(
 122:     profileId: string,
 123:     profileData: UserProfileData,
 124:     additionalPromptsAnalyzed: number,
 125:     changeSummary: string
 126:   ): void {
 127:     const now = Date.now();
 128: 
 129:     const cleanedData: UserProfileData = {
 130:       preferences: safeArray(profileData.preferences),
 131:       patterns: safeArray(profileData.patterns),
 132:       workflows: safeArray(profileData.workflows),
 133:     };
 134: 
 135:     const getVersionStmt = this.db.prepare(`SELECT version FROM user_profiles WHERE id = ?`);
 136:     const versionRow = getVersionStmt.get(profileId) as any;
 137:     const newVersion = (versionRow?.version || 0) + 1;
 138: 
 139:     const updateStmt = this.db.prepare(`
 140:       UPDATE user_profiles 
 141:       SET profile_data = ?, 
 142:           version = ?, 
 143:           last_analyzed_at = ?, 
 144:           total_prompts_analyzed = total_prompts_analyzed + ?
 145:       WHERE id = ?
 146:     `);
 147: 
 148:     updateStmt.run(
 149:       JSON.stringify(cleanedData),
 150:       newVersion,
 151:       now,
 152:       additionalPromptsAnalyzed,
 153:       profileId
 154:     );
 155: 
 156:     this.addChangelog(profileId, newVersion, "update", changeSummary, cleanedData);
 157: 
 158:     this.cleanupOldChangelogs(profileId);
 159:   }
 160: 
 161:   private addChangelog(
 162:     profileId: string,
 163:     version: number,
 164:     changeType: string,
 165:     changeSummary: string,
 166:     profileData: UserProfileData
 167:   ): void {
 168:     const id = `changelog_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
 169:     const now = Date.now();
 170: 
 171:     const stmt = this.db.prepare(`
 172:       INSERT INTO user_profile_changelogs (
 173:         id, profile_id, version, change_type, change_summary, 
 174:         profile_data_snapshot, created_at
 175:       )
 176:       VALUES (?, ?, ?, ?, ?, ?, ?)
 177:     `);
 178: 
 179:     stmt.run(id, profileId, version, changeType, changeSummary, JSON.stringify(profileData), now);
 180:   }
 181: 
 182:   private cleanupOldChangelogs(profileId: string): void {
 183:     const retentionCount = CONFIG.userProfileChangelogRetentionCount;
 184: 
 185:     const stmt = this.db.prepare(`
 186:       DELETE FROM user_profile_changelogs 
 187:       WHERE profile_id = ? 
 188:       AND id NOT IN (
 189:         SELECT id FROM user_profile_changelogs 
 190:         WHERE profile_id = ? 
```
