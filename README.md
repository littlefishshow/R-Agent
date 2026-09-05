<div align="center">

# R-Agent

**A local, tool-using AI research workbench**

Discover papers, read them deeply, navigate paper/code repositories, and run an
iterating AutoResearch loop — all from one controllable local agent.

English · [简体中文](README_zh.md)

[![GitHub stars](https://img.shields.io/github/stars/littlefishshow/R-Agent?style=flat-square)](https://github.com/littlefishshow/R-Agent/stargazers)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f?style=flat-square)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square)](https://www.python.org/)

<sub>Research Scout · Paper Reader · Repo Reader · AutoResearch · Cockpit GUI</sub>

</div>

---

> [!TIP]
> **Want a visual interface?** After installing dependencies and configuring
> `.env`, run `bash scripts/start_cockpit.sh` from the project root and open
> **http://127.0.0.1:5173** to use the visual Cockpit (learning-oriented chat /
> paper & Markdown reading / context branching). Stop it with
> `bash scripts/stop_cockpit.sh`. See [Visual Cockpit](#visual-cockpit).

R-Agent is not a chat wrapper. It behaves like a **cautious research assistant**:
it calls real tools, reads and writes files, searches the web, maintains a
structured run state and long-term memory, reuses skill packs, splits complex
work across parent/child agents, and — when asked — starts an AutoResearch loop
that edits code, runs validation, and records lessons.

It is also **DIY-friendly**: tell the agent in the terminal what capability you
want or how you'd like a class of tasks handled, and it will interactively help
you design, write, register, and validate a new tool, or distill a stable
workflow into `skills/**/SKILL.md`.

## Demo

The screenshots below show R-Agent running in the terminal and in the visual
Cockpit. The remaining recording slots can be filled from
[`assets/readme/demo/`](assets/readme/demo/) using the indicated file names.

<table>
<tr>
<td width="50%">

**Visual Cockpit**

[![R-Agent Visual Cockpit](r-Agent-gui.png)](r-Agent-gui.png)
_Read papers and Markdown notes with selected-text questions and context branches._

</td>
<td width="50%">

**CLI Research Workflow**

[![R-Agent CLI](r-agent-cli.png)](r-agent-cli.png)
_Run a research task from the CLI and track its live Todo progress._

</td>
</tr>
<tr>
<td width="50%">

**Deep Paper Reading**

<!-- ![Read paper](assets/readme/demo/read-paper.gif) -->
_`assets/readme/demo/read-paper.gif` — turn a PDF into a research note with figures._

</td>
<td width="50%">

**AutoResearch Loop**

<!-- ![AutoResearch](assets/readme/demo/autoresearch.gif) -->
_`assets/readme/demo/autoresearch.gif` — plan -> attempt -> conclude on a benchmark._

</td>
</tr>
</table>

## What You Get

- **A controllable agent runtime** — `core/agent.py` keeps the model-tool control
  loop; `core/middleware/` handles compression, deferred tools, output budgets,
  state tracking, and safety governance.
- **Structured run state** — `core/state.py` splits messages, summary, artifacts,
  delegation, skills, sandbox, and token accounting into separate channels
  instead of disguising everything as chat.
- **A full tool surface** — files, Shell/Python, Web Search/Extract, Memory,
  Skills, Todo/Delegate, artifact retrieval, and AutoResearch, with schema
  filtering, deferred exposure, execution-time guards, and process isolation.
- **Reusable skills** — stable workflows in `skills/**/SKILL.md`, loaded on demand
  (`skill_view`) and applied with tool allow-lists (`skill_activate`).
- **Two memory backends** — a readable Markdown `file` backend (`USER.md` /
  `MEMORY.md`) and a structured `deermem` backend (`facts.jsonl` + session facts)
  with admission gates, budgeted injection, retrieval, and governance.
- **Long-task context control** — request-scoped views, trigger/keep rules,
  rolling LLM summaries, durable context, and large tool outputs offloaded to
  artifacts.
- **A visual Cockpit** — a browser UI for tree-shaped conversations, a VSCode-style
  file system, PDF/Markdown reading, selected-text branches, and per-session tool
  toggles.
- **AutoResearch** — a `plan -> attempt -> conclude` loop for small, verifiable,
  metric-driven experiments, validated against a built-in benchmark suite.

## Quick Start

### 1. Set up the environment

Python 3.10+ is recommended. From the project root:

```bash
git clone https://github.com/littlefishshow/R-Agent.git
cd R-Agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` includes the OpenAI SDK, Rich, python-dotenv, pytest, voice
input dependencies, and everything the visual Cockpit needs (FastAPI / Uvicorn /
websockets / PyMuPDF / Pillow).

### 2. Configure `.env`

```bash
cp .env.example .env
```

Minimal configuration:

```env
# openai or azure
LLM_CLIENT_TYPE="openai"

# OpenAI / OpenAI-compatible API key
OPENAI_API_KEY="YOUR_API_KEY_HERE"

# Model name for OpenAI mode; deployment/endpoint name for Azure mode
LLM_MODEL="gpt-4o"

# Optional: a third-party OpenAI-compatible base URL
# OPENAI_BASE_URL="https://api.example.com/v1"
```

Azure mode adds:

```env
LLM_CLIENT_TYPE="azure"
AZURE_OPENAI_ENDPOINT="https://xxx.openai.azure.com/"
AZURE_OPENAI_API_VERSION="2024-02-01"
LLM_MODEL="your Azure deployment / endpoint name"
```

`.env.example` also ships a recommended runtime profile:

```env
DEFERRED_TOOLS_ENABLED=1
SESSION_SANDBOX_ENABLED=1
TOOL_SANITIZATION_MODE="audit"

DURABLE_CONTEXT_ENABLED=1
MEMORY_INJECTION_MODE="hidden_user"
MEMORY_PROVIDER="deermem"
MEMORY_WRITE_MIDDLEWARE_ENABLED=1
MEMORY_SESSION_FACTS_ENABLED=1
```

> **Defaults vs. example config.** Without these variables the code still runs
> with the `file` memory backend, durable context off, deferred tools off, and
> session sandbox off. Copying `.env.example` opts into the recommended combo
> above. Full semantics live in
> [`docs/03_上下文管理.md`](docs/03_上下文管理.md),
> [`docs/04_Memory系统.md`](docs/04_Memory系统.md), and
> [`docs/06_工具系统与沙箱.md`](docs/06_工具系统与沙箱.md).

For official Google search, add:

```env
GOOGLE_SEARCH_API_KEY="YOUR_GOOGLE_API_KEY"
GOOGLE_SEARCH_ENGINE_ID="YOUR_PROGRAMMABLE_SEARCH_ENGINE_ID"
```

then call `web_search(provider="google_cse")`. The default `provider="auto"`
order is `Bing -> Google Programmable Search (when configured) -> GroundRoute/Serper
(when configured) -> Yahoo -> DuckDuckGo`. Note `provider="google"` remains a
Serper-compatible alias.

### 3. Run the CLI agent

```bash
python main.py
```

Then type a natural-language task, for example:

```text
Find high-quality papers on test-time scaling from the last month, ranked by value
```

```text
Read outputs/papers/agent_RL/xxx.pdf and produce a research-grade note
```

```text
Read this paper's repo and tell me how the core method is implemented in code
```

## Visual Cockpit

R-Agent also ships a browser-based Cockpit for managing learning-oriented
conversations, paper files, Markdown notes, and context branches locally.

**Prerequisites**

- `pip install -r requirements.txt` (includes FastAPI / Uvicorn / websockets /
  PyMuPDF / Pillow).
- Node.js 18+ and npm (the frontend uses Vite; the first launch runs `npm install`).
- Frontend Markdown/math deps: `markdown-it` and `katex`. If the launcher reports
  them missing, run `cd app_gui_frontend && npm install` (or precisely
  `cd app_gui_frontend && npm install markdown-it katex && npm install -D @types/markdown-it @types/katex`).
  Do not install only at the repo root, or Vite may resolve back to the root
  `node_modules` and trip a font allow-list error.
- A configured `.env` (see [Quick Start step 2](#2-configure-env)).

**Start / stop**

```bash
bash scripts/start_cockpit.sh   # frontend: http://127.0.0.1:5173, API: http://127.0.0.1:8765
bash scripts/stop_cockpit.sh    # stop both and free the ports
```

Custom ports:

```bash
R_AGENT_COCKPIT_PORT=8765 R_AGENT_COCKPIT_FRONTEND_PORT=5173 bash scripts/start_cockpit.sh
```

The Cockpit currently supports a tree-shaped question chain, chat/file dual mode,
a VSCode-style file system mapped to `outputs/`, PDF reading with a text-selection
layer, Markdown notes, selected-text fork/branch windows, a per-session tool
toggle, and a resizable three-pane layout.

## Core Capabilities

| Capability | Where | Typical use |
|---|---|---|
| Agent Runtime | `core/agent.py` + `core/middleware/` | Control reasoning, tool calls, interruption, retries, forced wrap-up |
| ThreadState | `core/state.py` | Keep messages, summary, artifacts, delegation, skills, sandbox, tokens as separate channels |
| Tool system | `tools/registry.py` | Schema filtering, deferred exposure, execution guards, isolated subprocesses, timeouts |
| Skill system | `skills/**/SKILL.md` | Paper research, paper reading, repo reading, creative generation, GitHub workflows |
| Memory system | `file` (`USER.md`/`MEMORY.md`) or `deermem` (`facts.jsonl`) | Persist cross-session preferences and stable facts; retrieve session detail |
| Context control | `core/context_control.py` | Rolling LLM summary, trigger/keep, durable context, large-output artifacts |
| Todo / Delegate | Todo file + child `ThreadState` | Parallel research, complex maintenance, reduced parent context pressure |
| Session Sandbox | `sandbox/` | Isolate per-session workspace, Todo, run events, tool outputs, delegate contexts |
| Run Event Stream | `core/events.py` | Append run/LLM/tool/context/delegate/artifact events as JSONL and replay a run |
| Paper Research | `paper_research_scout` | Find recent / highly-cited / trending / code-backed papers |
| Paper Reading | `read_paper` | Deep PDF reading, figure capture, research-grade notes |
| Repo Reading | `paper_repo_code_research` | Map a paper's method to its code implementation |
| AutoResearch | `autoresearch/` | Small `plan -> attempt -> conclude` research loops |
| Cockpit GUI | `app_gui/` + `app_gui_frontend/` | Non-terminal reading, tree conversations, context auditing |

### Skill families

| Family | Location | Covers |
|---|---|---|
| Agent ops | `skills/agent_ops/` | Context control, dynamic todo delegation, progress saving, codebase inspection, tool-surface audit, AutoResearch workflow |
| Research productivity | `skills/productivity/` | `paper_research_scout`, `read_paper`, `paper_repo_code_research`, note correction, research explanation |
| Docs / office / KB | `skills/productivity/` | OCR & document processing, PDF, Notion, Airtable, Google Workspace, PowerPoint, maps, meeting pipelines |
| GitHub collaboration | `skills/github/` | Repo checks, code review, issues, PR workflow, repo management, auth |
| Creative & visual | `skills/creative/` | Diagrams, ASCII art/video, comics, infographics, web design, Manim, p5.js, pixel art, music, TouchDesigner |

## AutoResearch

`autoresearch/` is R-Agent's in-development autonomous research runtime. It
targets small, verifiable, metric-driven engineering/algorithm experiments — not
unbounded edits to large projects. The core loop is:

```text
Plan -> Attempt (edit/run) -> Conclude (evaluate/summarize) -> next Plan
```

**CLI**

```text
/autoresearch run <project_dir>
/autoresearch show [project_dir]
/autoresearch debug [on|off|show] [project_dir]
/autoresearch kill
```

**Tool entries** — `auto_research_run_v2` / `auto_research_v2_status` (current V3
three-step loop), `auto_research_run` / `auto_research_status` (legacy), and
`auto_research_stop`.

It is validated against the built-in `autoresearch/benchmarks/atr_playground`
suite, whose projects share a `prepare.py -> train/train.sh -> eval.sh ->
metrics.json` protocol: improve the official metric by editing `solution.py`,
`train/`, or `submission/` **without** touching the fixed evaluation files.
Coverage includes text cleaning, JSON/encoding repair, log classification,
information retrieval, dynamic programming, combinatorial optimization, and string
algorithms.

Safety boundaries: no automatic `git reset --hard`, no unsolicited repo init in
non-git projects, intermediate versions saved as artifact/patch/manifest rather
than frequent commits, and budgets/timeouts/debug/monitor/stop files bounding
long runs.

## Command Reference

Inside `python main.py`:

```text
/help                      Show help
/model                     View or switch model configuration
/mem                       View memory
/skill                     View skills
/tool                      View tools
/project_list              Load historical project progress context
/bbb                       Voice input (Enter to stop, Esc to cancel)
/autoresearch run <dir>    Start AutoResearch
/autoresearch show [dir]   Show AutoResearch progress
/autoresearch kill         Stop AutoResearch
exit / quit                Exit
```

**Gateway service mode (optional).** `gateway/` can wrap R-Agent as an HTTP
service and connect to WeChat, Feishu, and QQ official bots. See `gateway/`,
`Dockerfile.gateway`, `docker-compose.gateway.yml`, and `.env.gateway.example`.

## Documentation

`docs/` is a set of implementation tutorials written against the current source.
Read the capability map above, then dive into a topic:

| Tutorial | Answers |
|---|---|
| [`01_Agent循环中间件化.md`](docs/01_Agent循环中间件化.md) | How one round of decision, tool calls, middleware hooks, and forced wrap-up runs |
| [`02_ThreadState结构化状态.md`](docs/02_ThreadState结构化状态.md) | Which state channels exist and how artifact/delegation/skill merge |
| [`03_上下文管理.md`](docs/03_上下文管理.md) | How request views, trigger/keep, rolling summary, and durable context cooperate |
| [`04_Memory系统.md`](docs/04_Memory系统.md) | How the file/deermem backends, fact extraction, gates, retrieval, and governance work |
| [`05_子Agent委派契约.md`](docs/05_子Agent委派契约.md) | How Todo topology, child-agent isolation, budgets, and result contracts work |
| [`06_工具系统与沙箱.md`](docs/06_工具系统与沙箱.md) | How tool registration, permission filtering, process isolation, and session paths work |
| [`07_Skills与自定义Agent.md`](docs/07_Skills与自定义Agent.md) | How skill discovery, activation, and SOUL/Skill/Sub-agent composition work |
| [`08_运行事件流.md`](docs/08_运行事件流.md) | How RunEvent JSONL and live GUI events split responsibilities and replay |
| [`09_R-Agent整体流程图.md`](docs/09_R-Agent整体流程图.md) | End-to-end runtime flow diagram |
| [`10_AutoResearch上下文与长任务能力.md`](docs/10_AutoResearch上下文与长任务能力.md) | AutoResearch context handling and long-task capabilities |

## Repository Map

```text
R-Agent/
├── main.py                         # CLI entry point
├── core/
│   ├── agent.py                    # Agent loop, iteration budget, interruption, tool lifecycle
│   ├── state.py                    # ThreadState and durable-context projection
│   ├── context_control.py          # Context estimation, trigger/keep, rolling summary
│   ├── memory_provider.py          # file/deermem MemoryProvider
│   ├── memory_facts.py             # JSONL FactStore and session fact store
│   ├── memory_extractor.py         # LLM extraction of structured facts
│   ├── events.py                   # append-only RunEventStore
│   ├── middleware/                 # Lifecycle hooks and built-in middleware
│   └── context/                    # Large tool results and per-round output budgets
├── tools/                          # Tool registration: files/command/Memory/Skill/Todo/Delegate
├── skills/                         # Reusable workflows: paper research, reading, repo reading, ...
├── autoresearch/                   # AutoResearch runtime package
│   └── benchmarks/atr_playground/  # Built-in AutoResearch example benchmarks
├── app_gui/                        # Cockpit backend runtime / event / snapshot
├── app_gui_frontend/               # Cockpit frontend
├── gateway/                        # HTTP/Gateway/external platform integration
├── memories/                       # Markdown memory, facts.jsonl, session facts
├── docs/                           # Current R-Agent runtime tutorials
├── outputs/                        # Papers, notes, research outputs (usually gitignored)
├── sandbox/                        # Session workspace, Todo, RunEvent, tool/delegate artifacts
├── scripts/replay_events.py        # Replay global or per-session RunEvent JSONL
├── tests/                          # Automated tests
├── requirements.txt
├── .env.example
├── README.md
└── CHANGELOG.md                    # Standalone changelog
```

## Testing

```bash
PYTHONPATH=. python -m pytest
```

If pytest is missing from the current environment, run
`pip install -r requirements.txt`.

## Changelog

The changelog lives in a standalone file: [`CHANGELOG.md`](CHANGELOG.md).

## License

Released under the [MIT License](LICENSE). Third-party skills, packages, fonts,
and media retain their respective licenses.
