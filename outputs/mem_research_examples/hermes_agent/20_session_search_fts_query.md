# SessionDB.search_messages FTS5/trigram/LIKE retrieval

Source: https://github.com/NousResearch/hermes-agent/blob/4d18717b6c798d4f6bab9e736c6ed10c5a8365f4/hermes_state.py#L2863-L3105

Local clone source path: `hermes_state.py` (temporary clone; cleaned after research)

```python
2863:     def search_messages(
2864:         self,
2865:         query: str,
2866:         source_filter: List[str] = None,
2867:         exclude_sources: List[str] = None,
2868:         role_filter: List[str] = None,
2869:         limit: int = 20,
2870:         offset: int = 0,
2871:         sort: str = None,
2872:         include_inactive: bool = False,
2873:     ) -> List[Dict[str, Any]]:
2874:         """
2875:         Full-text search across session messages using FTS5.
2876: 
2877:         Supports FTS5 query syntax:
2878:           - Simple keywords: "docker deployment"
2879:           - Phrases: '"exact phrase"'
2880:           - Boolean: "docker OR kubernetes", "python NOT java"
2881:           - Prefix: "deploy*"
2882: 
2883:         Returns matching messages with session metadata, content snippet,
2884:         and surrounding context (1 message before and after the match).
2885: 
2886:         ``sort`` controls temporal ordering:
2887:           - ``None`` (default): FTS5 BM25 relevance only. Time-neutral.
2888:           - ``"newest"``: order by message timestamp DESC, then by rank.
2889:           - ``"oldest"``: order by message timestamp ASC, then by rank.
2890: 
2891:         The short-CJK LIKE fallback already orders by timestamp DESC and
2892:         ignores ``sort``. The trigram CJK path honours ``sort`` like the main
2893:         FTS5 path.
2894: 
2895:         Rewound (``active=0``) rows are excluded by default. Pass
2896:         ``include_inactive=True`` to search every row.
2897:         """
2898:         if not self._fts_enabled:
2899:             return []
2900: 
2901:         if not query or not query.strip():
2902:             return []
2903: 
2904:         query = self._sanitize_fts5_query(query)
2905:         if not query:
2906:             return []
2907: 
2908:         # Normalise sort. Anything not in the allowed set falls back to None
2909:         # (FTS5 rank-only) so callers can pass through user input without
2910:         # validation.
2911:         if isinstance(sort, str):
2912:             sort_norm = sort.strip().lower()
2913:             if sort_norm not in ("newest", "oldest"):
2914:                 sort_norm = None
2915:         else:
2916:             sort_norm = None
2917: 
2918:         # ORDER BY shared across the main FTS5 path and trigram CJK path.
2919:         # With sort set, timestamp is primary and rank is the tiebreaker.
2920:         if sort_norm == "newest":
2921:             order_by_sql = "ORDER BY m.timestamp DESC, rank"
2922:         elif sort_norm == "oldest":
2923:             order_by_sql = "ORDER BY m.timestamp ASC, rank"
2924:         else:
2925:             order_by_sql = "ORDER BY rank"
2926: 
2927:         # Build WHERE clauses dynamically
2928:         where_clauses = ["messages_fts MATCH ?"]
2929:         params: list = [query]
2930:         if not include_inactive:
2931:             where_clauses.append("m.active = 1")
2932: 
2933:         if source_filter is not None:
2934:             source_placeholders = ",".join("?" for _ in source_filter)
2935:             where_clauses.append(f"s.source IN ({source_placeholders})")
2936:             params.extend(source_filter)
2937: 
2938:         if exclude_sources is not None:
2939:             exclude_placeholders = ",".join("?" for _ in exclude_sources)
2940:             where_clauses.append(f"s.source NOT IN ({exclude_placeholders})")
2941:             params.extend(exclude_sources)
2942: 
2943:         if role_filter:
2944:             role_placeholders = ",".join("?" for _ in role_filter)
2945:             where_clauses.append(f"m.role IN ({role_placeholders})")
2946:             params.extend(role_filter)
2947: 
2948:         where_sql = " AND ".join(where_clauses)
2949:         params.extend([limit, offset])
2950: 
2951:         sql = f"""
2952:             SELECT
2953:                 m.id,
2954:                 m.session_id,
2955:                 m.role,
2956:                 snippet(messages_fts, 0, '>>>', '<<<', '...', 40) AS snippet,
2957:                 m.content,
2958:                 m.timestamp,
2959:                 m.tool_name,
2960:                 s.source,
2961:                 s.model,
2962:                 s.started_at AS session_started
2963:             FROM messages_fts
2964:             JOIN messages m ON m.id = messages_fts.rowid
2965:             JOIN sessions s ON s.id = m.session_id
2966:             WHERE {where_sql}
2967:             {order_by_sql}
2968:             LIMIT ? OFFSET ?
2969:         """
2970: 
2971:         # CJK queries bypass the unicode61 FTS5 table.  The default tokenizer
2972:         # splits CJK characters into individual tokens, so "大别山项目" becomes
2973:         # "大 AND 别 AND 山 AND 项 AND 目" — producing false positives and
2974:         # missing exact phrase matches.
2975:         #
2976:         # For queries with 3+ CJK characters, we use the trigram FTS5 table
2977:         # (indexed substring matching with ranking and snippets).  For shorter
2978:         # CJK queries (1-2 chars), trigram can't match (it needs ≥9 UTF-8
2979:         # bytes = 3 CJK chars), so we fall back to LIKE.
2980:         is_cjk = self._contains_cjk(query)
2981:         if is_cjk:
2982:             raw_query = query.strip('"').strip()
2983:             cjk_count = self._count_cjk(raw_query)
2984: 
2985:             # Per-token CJK length check (#20494): trigram needs >=3 CJK chars
2986:             # per token. A query like "广西 OR 桂林 OR 漓江" has cjk_count=6
2987:             # (>=3) but each individual token is only 2 chars — trigram returns 0.
2988:             # Route to LIKE when any non-operator CJK token is <3 CJK chars.
2989:             _tokens_for_check = [
2990:                 t for t in raw_query.split()
2991:                 if t.upper() not in {"AND", "OR", "NOT"} and self._contains_cjk(t)
2992:             ]
2993:             _any_short_cjk = any(
2994:                 self._count_cjk(t) < 3 for t in _tokens_for_check
2995:             )
2996: 
2997:             if cjk_count >= 3 and not _any_short_cjk:
2998:                 # Trigram FTS5 path — quote each non-operator token to handle
2999:                 # FTS5 special chars (%, *, etc.) while preserving boolean
3000:                 # operators (AND, OR, NOT) for multi-term queries.
3001:                 tokens = raw_query.split()
3002:                 parts = []
3003:                 for tok in tokens:
3004:                     if tok.upper() in {"AND", "OR", "NOT"}:
3005:                         parts.append(tok)
3006:                     else:
3007:                         parts.append('"' + tok.replace('"', '""') + '"')
3008:                 trigram_query = " ".join(parts)
3009:                 tri_where = ["messages_fts_trigram MATCH ?"]
3010:                 tri_params: list = [trigram_query]
3011:                 if not include_inactive:
3012:                     tri_where.append("m.active = 1")
3013:                 if source_filter is not None:
3014:                     tri_where.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
3015:                     tri_params.extend(source_filter)
3016:                 if exclude_sources is not None:
3017:                     tri_where.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
3018:                     tri_params.extend(exclude_sources)
3019:                 if role_filter:
3020:                     tri_where.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
3021:                     tri_params.extend(role_filter)
3022:                 tri_sql = f"""
3023:                     SELECT
3024:                         m.id,
3025:                         m.session_id,
3026:                         m.role,
3027:                         snippet(messages_fts_trigram, 0, '>>>', '<<<', '...', 40) AS snippet,
3028:                         m.content,
3029:                         m.timestamp,
3030:                         m.tool_name,
3031:                         s.source,
3032:                         s.model,
3033:                         s.started_at AS session_started
3034:                     FROM messages_fts_trigram
3035:                     JOIN messages m ON m.id = messages_fts_trigram.rowid
3036:                     JOIN sessions s ON s.id = m.session_id
3037:                     WHERE {' AND '.join(tri_where)}
3038:                     {order_by_sql}
3039:                     LIMIT ? OFFSET ?
3040:                 """
3041:                 tri_params.extend([limit, offset])
3042:                 with self._lock:
3043:                     try:
3044:                         tri_cursor = self._conn.execute(tri_sql, tri_params)
3045:                     except sqlite3.OperationalError:
3046:                         matches = []
3047:                     else:
3048:                         matches = [dict(row) for row in tri_cursor.fetchall()]
3049:             else:
3050:                 # Short / mixed CJK query: trigram cannot match tokens with
3051:                 # <3 CJK chars. Fall back to LIKE substring search.
3052:                 # For multi-token OR queries (e.g. "广西 OR 桂林 OR 漓江"),
3053:                 # build one LIKE condition per non-operator token so each term
3054:                 # is matched independently (#20494).
3055:                 non_op_tokens = [
3056:                     t for t in raw_query.split()
3057:                     if t.upper() not in {"AND", "OR", "NOT"}
3058:                 ] or [raw_query]
3059:                 token_clauses = []
3060:                 like_params: list = []
3061:                 for tok in non_op_tokens:
3062:                     esc = tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
3063:                     token_clauses.append(
3064:                         "(m.content LIKE ? ESCAPE '\\' OR m.tool_name LIKE ? ESCAPE '\\' OR m.tool_calls LIKE ? ESCAPE '\\')"
3065:                     )
3066:                     like_params += [f"%{esc}%", f"%{esc}%", f"%{esc}%"]
3067:                 like_where = [f"({' OR '.join(token_clauses)})"]
3068:                 if source_filter is not None:
3069:                     like_where.append(f"s.source IN ({','.join('?' for _ in source_filter)})")
3070:                     like_params.extend(source_filter)
3071:                 if exclude_sources is not None:
3072:                     like_where.append(f"s.source NOT IN ({','.join('?' for _ in exclude_sources)})")
3073:                     like_params.extend(exclude_sources)
3074:                 if role_filter:
3075:                     like_where.append(f"m.role IN ({','.join('?' for _ in role_filter)})")
3076:                     like_params.extend(role_filter)
3077:                 like_sql = f"""
3078:                     SELECT m.id, m.session_id, m.role,
3079:                            substr(m.content,
3080:                                   max(1, instr(m.content, ?) - 40),
3081:                                   120) AS snippet,
3082:                            m.content, m.timestamp, m.tool_name,
3083:                            s.source, s.model, s.started_at AS session_started
3084:                     FROM messages m
3085:                     JOIN sessions s ON s.id = m.session_id
3086:                     WHERE {' AND '.join(like_where)}
3087:                     ORDER BY m.timestamp DESC
3088:                     LIMIT ? OFFSET ?
3089:                 """
3090:                 like_params.extend([limit, offset])
3091:                 # instr() for snippet uses first search token
3092:                 like_params = [non_op_tokens[0]] + like_params
3093:                 with self._lock:
3094:                     like_cursor = self._conn.execute(like_sql, like_params)
3095:                     matches = [dict(row) for row in like_cursor.fetchall()]
3096:         else:
3097:             with self._lock:
3098:                 try:
3099:                     cursor = self._conn.execute(sql, params)
3100:                 except sqlite3.OperationalError:
3101:                     # FTS5 query syntax error despite sanitization — return empty
3102:                     return []
3103:                 else:
3104:                     matches = [dict(row) for row in cursor.fetchall()]
3105: 
```
