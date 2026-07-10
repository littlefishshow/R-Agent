---
name: "paper_research_scout"
description: "Research radar workflow for discovering, ranking, and triaging recent papers/articles by relevance, quality, citations, institutional signal, and community/code traction before handing off deep reading or repo analysis."
---

# Paper Research Scout

## When to Use
Use this skill when the user wants to **discover, monitor, rank, and triage papers/articles** before deep reading. Typical requests include:

- Find the newest, most influential, most cited, or socially popular papers for a topic.
- Build a weekly paper radar for a field, conference, lab, author, benchmark, or method family.
- Track top venues, top labs, Hugging Face Daily Papers, arXiv Sanity, X/Twitter discussion, GitHub stars/trending, and Connected Papers neighborhoods.
- Compare candidate papers by relevance, quality, novelty, institutional/venue signal, citation signal, code availability, and community traction.
- Produce a shortlist for later handoff: use `read_paper` for deep paper reading, `research_explainer_md` for explanatory write-ups, `paper_repo_code_research` for associated repositories/code, and `autoresearch` for iterative research/program exploration.

Do **not** use this skill as a substitute for full paper understanding or code audit. It is a discovery-and-screening workflow.

## Inputs / Clarifying Questions
Collect enough constraints before searching. If missing, ask concise clarifying questions.

### Required Inputs
- **Topic / query**: keywords, paper title seed, author/lab, venue, method, benchmark, disease/domain, or application.
- **Time window**: e.g. last 7 days, last month, 2024+, since last major conference, all-time highly cited.
- **Output size**: e.g. top 5/10/20 papers, grouped by subtopic, or daily/weekly digest.

### Optional Inputs
- **Priority objective**: latest, most cited, highest quality, best institutions, strongest code, social buzz, survey/review only, production relevance.
- **Domain constraints**: AI/ML, biology, medicine, economics, systems, robotics, etc.
- **Allowed sources**: web only, academic databases, arXiv, Semantic Scholar, Google Scholar, PubMed, ACL Anthology, OpenReview, conference pages, GitHub, X/Twitter, Hugging Face.
- **Quality bar**: peer-reviewed only vs. preprints allowed; top venues only; top labs only; minimum citations/stars.
- **Exclusions**: non-English, non-open-access, patents, blog posts, duplicate arXiv/conference versions, papers already read.
- **User workflow**: one-off shortlist, weekly radar, daily news scan, topic-specific monitoring, or literature map expansion.

### Clarifying Questions to Ask When Ambiguous
1. What is the exact topic and what should count as in-scope/out-of-scope?
2. Should I optimize for **recency**, **citations**, **institution/venue quality**, **social/code traction**, or a balanced score?
3. What time range and how many papers should be returned?
4. Are preprints acceptable, or only peer-reviewed papers from named venues?
5. Do you want final candidates only, or a transparent longlist with rejection reasons?

## Information Source Priority
Use multiple sources because each source has bias and latency. Do not claim a source was checked unless it was actually searched/extracted. For any **current ranking, trending, star count, citation count, Daily Papers position, or social heat**, use live web/tool lookup and label the **retrieval date**.

### Tier 1: Primary Academic and Venue Sources
Prioritize official or near-primary sources for metadata and publication status:
- Publisher / venue pages: NeurIPS, ICML, ICLR/OpenReview, ACL Anthology, CVF/CVPR/ICCV/ECCV, SIGGRAPH, KDD, WWW, EMNLP, Nature/Science/Cell, IEEE/ACM, PubMed.
- arXiv paper pages and category feeds.
- Authors' institutional pages or lab publication pages.
- Official conference accepted-paper lists, proceedings, oral/spotlight/award pages.

### Tier 2: Scholarly Indexes and Citation Graphs
Use these for citation counts, related work, influential prior/follow-up papers, and deduplication:
- Semantic Scholar.
- Google Scholar when accessible; if inaccessible, mark citation count as `N/A` or use another stated source.
- Connected Papers / Litmaps / ResearchRabbit style graph pages when accessible.
- Crossref, OpenAlex, DBLP, PubMed, Dimensions, Scopus/Web of Science if available.

Citation counts must include the source and retrieval date, e.g. `Citations: 128 (Semantic Scholar, retrieved YYYY-MM-DD)`. If not available, write `Citations: N/A` rather than guessing.

### Structured Metrics Lookup (Use Skill-Local Scripts First)
For volatile metrics, prefer structured APIs through the skill-local script before using generic `web_extract` on web pages. The script is intentionally kept under this skill and is **not** registered as a global Agent Loop tool.

Run from the project root with `run_command`:

```bash
python3 skills/productivity/paper_research_scout/scripts/paper_metrics_lookup.py \
  --title "<paper title>" \
  --doi "<doi-if-known>" \
  --arxiv-id "<arxiv-id-if-known>" \
  --github-repo "owner/repo" \
  --pretty \
  --output sandbox/paper_research_scout/<safe_name>_metrics.json
```

Read-history filtering is also skill-local. Before showing final recommendations, check candidates against `references/read_papers.json`:

```bash
python3 skills/productivity/paper_research_scout/scripts/paper_read_history.py check --title "<paper title>" --arxiv-id "<arxiv-id-if-known>" --doi "<doi-if-known>" --url "<paper-url-if-known>"
```

For batch candidate filtering, write candidates to `sandbox/paper_research_scout/candidates.json` and run:

```bash
python3 skills/productivity/paper_research_scout/scripts/paper_read_history.py filter --input sandbox/paper_research_scout/candidates.json --output sandbox/paper_research_scout/candidates_unread.json
```

If a paper is already read, omit it from top recommendations by default. Mention it only in a short "Already Read / Suppressed" note when useful, unless the user explicitly asks to include previously read papers.

Environment variables supported by the script:
- `SEMANTIC_SCHOLAR_API_KEY` for higher Semantic Scholar limits.
- `GITHUB_TOKEN` for higher GitHub API limits.
- `OPENALEX_MAILTO` / `CROSSREF_MAILTO` for polite OpenAlex/Crossref requests.
- `SERPAPI_API_KEY` only when the user explicitly needs Google Scholar-style citation lookup through SerpApi.

The script annotates API records with `title_match_confidence` when a target title is provided. Treat low-confidence or `ok=false` mismatch results as unusable for citation counts, even if the source returned a number. Semantic Scholar `429` should be handled by setting `SEMANTIC_SCHOLAR_API_KEY` and retrying later with backoff, not by repeated anonymous calls.

Structured source priority:
1. **Citations**: Semantic Scholar `citationCount` by DOI/arXiv/title → DataCite `citationCount` for DataCite/arXiv DOIs → OpenAlex `cited_by_count` by DOI/title with strict title validation → Crossref `is-referenced-by-count` by DOI → optional SerpApi Google Scholar. Do not directly scrape Google Scholar as a default workflow.
2. **Hugging Face heat**: use official Hugging Face Papers JSON endpoints (`/api/daily_papers`, `/api/papers/search`, `/api/papers/{paperId}`) via the script. Avoid parsing the rendered Daily Papers page except as last-resort evidence.
3. **OpenReview**: do not treat a browser verification page as unavailable paper evidence. For public single-forum lookup, use the script's `--openreview-forum`; for venue-wide/private data use official `openreview-py` (`api2.openreview.net` for newer venues, `api.openreview.net` for legacy venues) and respect permissions.
4. **GitHub/code signal**: use GitHub API fields beyond stars: forks, open issues, pushed/released dates, license, language, archive status, topics. Stars are a weak community-interest signal, not a quality proof.

When metrics disagree, report multiple source-specific values with retrieval dates instead of silently reconciling them. Example: `Semantic Scholar: 117; DataCite: 9; OpenAlex: discarded due to title mismatch; Crossref: N/A (retrieved YYYY-MM-DD)`. If an API result title does not match the target paper, discard that citation count and state the mismatch.


### Tier 3: Discovery Feeds and Community Signals
Use for freshness and popularity, not as sole quality evidence:
- Hugging Face Daily Papers and paper pages.
- arXiv Sanity / ar5iv / alphaXiv-style discussion pages if accessible.
- X/Twitter posts from authors, labs, conferences, credible researchers; record limitations if search is unavailable.
- GitHub Trending, repository stars/forks/issues/releases, Papers with Code.
- Reddit/Hacker News/blog newsletters only as weak signals unless corroborated.

### Tier 4: General Web and News
Use for context, adoption, controversy, or non-academic relevance:
- Google/Bing/web search results, institutional news, company blogs, technical blogs.
- Treat marketing claims cautiously and corroborate with paper/repo/benchmark evidence.

## Quality / Heat / Relevance Scoring
Create a transparent score; adapt weights to the user's objective. Default balanced ranking: **100 points**.

### Default Score Components
1. **Relevance to user need (0-30)**
   - Directly addresses query, task, domain, or benchmark.
   - Matches time window and constraints.
   - Penalize tangential or overly broad papers.

2. **Research quality signal (0-25)**
   - Peer-reviewed top venue, oral/spotlight/award, reputable journal, strong methodology, clear evaluation.
   - Author/lab/institution credibility is a signal, not proof.
   - Penalize weak baselines, unclear claims, missing ablations, non-reproducible hype.

3. **Novelty / contribution significance (0-15)**
   - New method, dataset, benchmark, theory, empirical finding, or strong synthesis.
   - Penalize incremental variants unless highly useful.

4. **Influence / citation signal (0-10)**
   - Citation count from named source and retrieval date.
   - For very recent papers, normalize expectations and do not over-penalize low citations.

5. **Community and social heat (0-10)**
   - HF Daily Papers ranking/upvotes, arXiv Sanity popularity, X/Twitter credible discussion, newsletter mentions.
   - Must be current and retrieved live; otherwise mark `N/A`.

6. **Code / artifact / reproducibility signal (0-10)**
   - Official code, active repo, stars/forks, license, data availability, model weights, reproducible scripts.
   - For code-heavy analysis, hand off to `paper_repo_code_research`.

### Alternative Weight Presets
- **Latest radar**: recency/social/code 45, relevance 30, quality 20, citations 5.
- **Canonical/highly cited**: citations 35, quality 30, relevance 25, code/social 10.
- **Top institution/venue watch**: venue/lab 35, relevance 30, recency 20, citations/social/code 15.
- **Implementation shortlist**: code/artifacts 35, relevance 30, quality 20, social/citations 15.

Always show enough evidence that the user can understand why a paper ranked where it did.

## Execution Steps

### 1. Define the Search Plan
- Restate the topic, time window, ranking objective, exclusions, and output format.
- Identify likely synonyms, key authors/labs, venues, and adjacent subtopics.
- Decide whether the task is:
  - one-off discovery,
  - weekly digest,
  - topic-specific monitoring,
  - daily information filtering,
  - citation/classic-paper mapping,
  - or related-paper expansion from seed papers.

### 2. Build a Longlist
Use live searches appropriate to the task. Example source sequence:
1. Search official venue/proceeding pages and arXiv for the topic/time window.
2. Normalize each candidate by DOI/arXiv ID/title, then call `scripts/paper_metrics_lookup.py` for structured citation, HF, GitHub, and OpenReview metrics before falling back to generic webpage extraction.
3. Check normalized candidates against `scripts/paper_read_history.py` / `references/read_papers.json`; remove already-read papers from recommendations unless the user asks otherwise.
4. Search Semantic Scholar/OpenAlex-style indexes for related papers; use Google Scholar only through an approved SERP API or manual/user-provided evidence.
5. Search Hugging Face Papers through `/api/daily_papers`, `/api/papers/search`, and `/api/papers/{paperId}` for recent community visibility and upvotes.
6. Search GitHub/Papers with Code for official implementations; record forks/activity/license/issues in addition to stars.
7. Search X/Twitter/social web only if the tool/search environment can access it; otherwise state it was unavailable.
8. Search Connected Papers or graph tools for seed expansion when a seed paper is provided.

Record for each candidate:
- title,
- authors,
- year/date,
- venue/status,
- URL/DOI/arXiv ID,
- citation source/count or `N/A`,
- code/repo URL, stars, forks, activity/license signals, or `N/A`,
- discovery source(s),
- retrieval date for volatile metrics.

### 3. Deduplicate and Normalize
- Merge arXiv, conference, journal, and OpenReview versions.
- Prefer the peer-reviewed/proceedings version as canonical, while preserving arXiv link if useful.
- Normalize title variants, author order, year, venue, and repo links.
- Mark withdrawn, superseded, or non-paper items clearly.
- Filter out papers already present in `references/read_papers.json` using arXiv ID, DOI, URL, or normalized title.

### 4. Score and Filter
- Apply the selected scoring weights.
- Keep a rejection list for papers excluded due to irrelevance, age, weak evidence, missing access, duplication, or lack of code if code was required.
- Do not invent missing metrics. Use `N/A` and explain the consequence.

### 5. Validate Top Candidates
For each top candidate, perform quick verification:
- Does the abstract/introduction match the user's need?
- Is the venue/status accurately stated?
- Are citations/stars/social signals attributed to a source and retrieval date?
- Is the code official or third-party?
- Are there obvious red flags: retracted/withdrawn, unverified benchmark claims, inaccessible PDF, suspicious repo, or hype-only social activity?

### 6. Produce the Deliverable
Return a ranked shortlist plus methodology. Include exact retrieval date for volatile metrics. If the user asked for monitoring, include a repeatable watchlist and query schedule.

### 7. Hand Off for Deeper Work
- For deep comprehension of selected papers, recommend `read_paper`.
- For a plain-language explainer or synthesis, recommend `research_explainer_md`.
- For implementation/repository evaluation, recommend `paper_repo_code_research`.
- For iterative project-level research experiments, recommend `autoresearch`.

## Output Templates

### A. Ranked Paper Scout Report
```markdown
# Paper Scout Report: <topic>
Retrieval date: <YYYY-MM-DD>
Search objective: <latest / highly cited / top venue / balanced / implementation-ready>
Scope: <time window, venues/sources, exclusions>

## Method
Sources actually checked:
- <source> — query/URL: <...> — retrieved <date>
- <source> — query/URL: <...> — retrieved <date>

Scoring weights: Relevance <x>, Quality <x>, Novelty <x>, Citations <x>, Heat <x>, Code <x>.
Limitations: <unavailable sources, blocked pages, missing metrics>

## Top Recommendations
| Rank | Score | Paper | Why it matters | Venue/Status | Citations | Heat | Code |
|---:|---:|---|---|---|---|---|---|
| 1 | 87 | <Title> (<Year>) | <1-2 line rationale> | <venue/status> | <S2/DataCite/OpenAlex/Crossref counts + date or N/A> | <HF upvotes/comments, X/arXiv signal or N/A> | <repo stars/forks/activity/license + date or N/A> |

## Candidate Notes
### 1. <Title>
- Authors/institutions: <...>
- Link: <paper URL>
- Core contribution: <...>
- Evidence for ranking: <quality, relevance, novelty, citation/social/code signals>
- Structured metrics checked: <script output path or API sources; include retrieval date>
- Caveats: <...>
- Recommended next action: <read_paper / paper_repo_code_research / monitor>

## Rejected / Lower-Priority Items
| Paper | Reason |
|---|---|
| <Title> | <out of scope / duplicate / weak evidence / too old / missing code> |

## Already Read / Suppressed
| Paper | Matched read-history key | Local note/path |
|---|---|---|
| <Title> | <arXiv/DOI/title match> | <outputs/papers/... or N/A> |

## Next Monitoring Queries
- <venue/feed/query to repeat weekly>
- <lab/author/repo watch item>
```

### B. Weekly Research Radar
```markdown
# Weekly Research Radar: <field/topic>
Week ending: <YYYY-MM-DD>
Retrieval date: <YYYY-MM-DD>

## Must-Read This Week
1. <paper> — <why now> — <source/date>

## Rising / Socially Hot
- <paper> — <HF Daily Papers / arXiv Sanity / X / GitHub signal with date>

## Strong Institutions / Venues
- <paper> — <lab/venue signal>

## Implementation-Ready
- <paper> — <official repo, stars, license, model/data availability>

## Watchlist for Next Week
- <authors/labs/venues/arXiv queries/repos>

## Limitations
- <sources unavailable, citation lag, social search gaps>
```

### C. Seed Expansion with Connected Papers / Citation Graphs
```markdown
# Related Paper Map: <seed paper>
Retrieval date: <YYYY-MM-DD>
Seed: <title, URL>
Graph/citation sources checked: <Connected Papers/Semantic Scholar/OpenAlex/etc.>

## Closest Prior Work
| Paper | Relationship | Evidence | Citations |

## Important Follow-Ups
| Paper | Relationship | Evidence | Citations |

## Divergent but Relevant Branches
| Paper | Why relevant | Caveat |

## Suggested Reading Order
1. <canonical background>
2. <seed>
3. <best follow-up>
```

## Degradation Strategies and Boundaries

### Degradation Strategies
- If Google Scholar is inaccessible or no approved SERP API key is available, use OpenAlex/Semantic Scholar/Crossref/PubMed/DBLP and mark Google Scholar as unavailable rather than scraping it.
- If X/Twitter search is inaccessible, do not infer social heat; use HF Daily Papers, GitHub, arXiv Sanity, newsletters, or mark `N/A`.
- If Connected Papers is inaccessible, approximate graph expansion with Semantic Scholar references/citations, OpenAlex concepts, or manual citation chasing.
- If citation counts differ across sources, report the source used and optionally list multiple counts with dates rather than reconciling silently.
- If current trending pages cannot be accessed, first try structured APIs (HF Papers API, GitHub API, OpenReview API/openreview-py). If APIs also fail, state the limitation and avoid ranking by trend.
- If the topic is too broad, first produce subtopics and ask the user to choose or run a broad scan with explicit low confidence.

### Boundaries
- Do not fabricate citation counts, rankings, star counts, social posts, or venue acceptance status.
- Do not claim to have checked pages, feeds, X/Twitter, GitHub, HF Daily Papers, arXiv Sanity, Connected Papers, or Google Scholar unless actually searched/extracted.
- Do not treat social heat as proof of scientific correctness.
- Do not over-rank famous institutions when the paper itself is weak or irrelevant.
- Do not provide medical, legal, financial, or safety-critical conclusions solely from paper discovery; recommend expert review.
- Respect paywalls and access limits; summarize only what can be legitimately accessed.

## Deliverables
Depending on the request, deliver one or more of:
- Ranked shortlist of papers with evidence, scores, and caveats.
- Weekly/daily research radar digest.
- Topic watchlist: venues, labs, authors, arXiv categories, HF/GitHub queries, and social searches.
- Citation/related-paper map from seed papers.
- Implementation-ready shortlist with repository metadata.
- Rejected-item list with reasons.
- Suggested next actions and handoff targets (`read_paper`, `research_explainer_md`, `paper_repo_code_research`, `autoresearch`).

## Acceptance Checklist
Before finalizing a paper scouting answer, verify:

- [ ] The user's topic, time window, ranking objective, and output size are stated.
- [ ] Sources actually checked are listed; unavailable sources are not implied.
- [ ] For top candidates, structured metrics were attempted via `scripts/paper_metrics_lookup.py` or an equivalent official API path before generic page extraction.
- [ ] Any current/trending/ranking/social/star/citation metrics were retrieved live and include retrieval date.
- [ ] Citation counts include named sources (Semantic Scholar/DataCite/OpenAlex/Crossref/optional SerpApi GS), retrieval dates, and title-match validation where applicable, or are marked `N/A` with reason.
- [ ] GitHub stars/forks/activity/license signals include source and retrieval date, or are marked `N/A`.
- [ ] Paper status/venue is not overstated; preprint vs. peer-reviewed is clear.
- [ ] Duplicates and version conflicts are handled.
- [ ] Already-read papers were checked via `scripts/paper_read_history.py`; filtered papers are not shown as new recommendations unless requested.
- [ ] Ranking criteria and weights are transparent.
- [ ] Caveats and limitations are included.
- [ ] Recommended next step is clear, including handoff to `read_paper` or `paper_repo_code_research` when appropriate.
