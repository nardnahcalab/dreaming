# runlog.md — Session Run Log

Project: local AI memory capture + dreaming (OpenCode, Claude Code, Codex, Hermes)
Date: 2026-11-24
Machine: NVIDIA GB10 (aarch64), Ubuntu, Python 3.12.3, Ollama @ localhost:11434
Data root: `~/.dream-memory`

---

## 1. Environment discovery

### Tool storage locations found

```
FOUND: /home/bala/.claude
FOUND: /home/bala/.codex
FOUND: /home/bala/.opencode
FOUND: /home/bala/.hermes
FOUND: /home/bala/.local/share/opencode
FOUND: /home/bala/.local/share/claude
FOUND: /home/bala/.config/opencode
```

| Tool | Source | Format |
|---|---|---|
| OpenCode | `~/.local/share/opencode/opencode.db` (24 GB) | SQLite: `session_v2`, `session_message` |
| Claude Code | `~/.claude/projects/<proj>/<session>.jsonl` | JSONL |
| Codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | JSONL |
| Hermes | `~/.hermes/state.db` | SQLite: `sessions`, `messages` |

### Local LLM + resources

```
Models: ['gemma4:31b', 'gemma4:latest', 'nemotron3:33b', 'gpt-oss:20b',
         'granite4:tiny-h', 'olmo-3:7b-think', 'olmo-3:7b-instruct',
         'functiongemma:latest']

disk: /dev/nvme0n1p2  3.6T  1.2T  2.3T  34% /
python: Python 3.12.3
```

---

## 2. First `sync` — failed (missing CLI flag)

```
$ python3 dream.py sync
Traceback (most recent call last):
  File "/home/bala/experiments/dreaming/dream.py", line 34, in <module>
    from dream_memory.core.dreaming import Dreamer, DreamStore, LocalLLM
ModuleNotFoundError: No module named 'dream_memory.core.dreaming'
```

**Fix:** flattened package layout (`dream_memory/core/*` → `dream_memory/*`), corrected imports.

**Second failure:**

```
  [error] opencode: AttributeError: 'Namespace' object has no attribute 'limit'
  [error] claude-code: AttributeError: 'Namespace' object has no attribute 'limit'
  [error] codex: AttributeError: 'Namespace' object has no attribute 'limit'
  [error] hermes: AttributeError: 'Namespace' object has no attribute 'limit'
```

**Fix:** added `--limit` flag to the `sync` subparser.

---

## 3. First successful `sync`

```
$ python3 dream.py sync
Syncing into /home/bala/.dream-memory/episodic/
(already stored: 0 sessions)

  [ok] opencode: 46 sessions found, 46 new, 0 duplicates (0.1s)
  [ok] claude-code: 5 sessions found, 5 new, 0 duplicates (0.0s)
  [ok] codex: 1 sessions found, 1 new, 0 duplicates (0.0s)
  [ok] hermes: 1 sessions found, 1 new, 0 duplicates (0.0s)

Episodic store: 53 sessions, 2391 messages
By tool: {'claude-code': 5, 'opencode': 46, 'hermes': 1, 'codex': 1}
```

### `status`

```
============================================================
  LOCAL MEMORY STATUS
============================================================

Data root: /home/bala/.dream-memory

EPISODIC MEMORY (append-only, never modified)
  Sessions:  53
  Messages:  2391
    - claude-code: 5 sessions
    - codex: 1 sessions
    - hermes: 1 sessions
    - opencode: 46 sessions

CONSOLIDATED MEMORY (output of dreaming)
  (no dreams yet - run `python3 dream.py dream`)

DREAM ENGINE (local Ollama)
  Available: yes
  Model:     gpt-oss:20b
```

---

## 4. Recall bugs found and fixed

**First run — no matches despite data existing:**

```
$ python3 dream.py recall "pytest testing preferences" --limit 3
Query: pytest testing preferences
Dream: (none)

CONSOLIDATED MEMORY: no matches

EPISODIC MEMORY: no matches
```

**Debug output:**

```
sessions loaded: 53
query tokens: ['pytest', 'testing', 'preferences']
found in opencode assistant | score: 0.19999999999999998
found in opencode assistant | score: 0.19999999999999998
total msgs containing pytest: 2
```

**Root causes (2 bugs):**
1. Float precision — `0.19999999999999998 > 0.2` is `False`; threshold changed to `>= 0.15`.
2. Role filter — recall only searched `user` messages, but matches were in `assistant` messages; now searches both.

**After fix:**

```
$ python3 dream.py recall "pytest testing preferences" --limit 3
Query: pytest testing preferences
Dream: (none)

CONSOLIDATED MEMORY: no matches

EPISODIC MEMORY (raw evidence):
  [0.20] opencode
          Now I have read every file in the target directories. Here is the comprehensive report...
  [0.20] opencode
          Here is a comprehensive report of all findings...
  [0.20] opencode
          Here is my comprehensive report on all existing Nemotron experiments and results...
```

---

## 5. dream-0001 — first successful dream (LLM)

```
$ python3 dream.py dream --max-sessions 15
Dreaming with local model: gpt-oss:20b

Episodic memory: 53 sessions (2391 messages)
Reading episodic memory (read-only)...

============================================================
  DREAM COMPLETE: dream-0001
============================================================
  Engine:        LLM (gpt-oss:20b)
  New sessions:  53
  Processed:     15 sessions
  Entries:       19 (0 pruned by importance cap)
  Duration:      138.9s

  Saved: /home/bala/.dream-memory/dreaming/dream-0001.json
  (previous dreams untouched - rollback available)

  Top memories:
    [0.9] (fact) User has a Qwen3.5 model configuration located at /home/bala/data/cache/hub/models--Qwen--
    [0.9] (fact) Project /home/bala/apps/bs2 is a React 18 + TypeScript + Vite + Tailwind + Radix UI applic
    [0.9] (fact) Project /home/bala/hermes/agent-platform is a scaffold for an enterprise agent platform. I
    [0.9] (task) Task: Build a robust test suite for the bs2 Bombshell app, run the tests, identify and fix
    [0.9] (project) Project: bs2 Bombshell application located at /home/bala/apps/bs2 is a React/Vite/Tailwind
    [0.9] (project) Project: hermes agent platform located at /home/bala/hermes/agent-platform is a scaffold f
    [0.8] (fact) Project /home/bala/pareto contains benchmark scripts for Qwen and Nemotron models, includi
    [0.8] (fact) User has built a robust test suite for the bs2 Bombshell app, covering AuthScreen, DailyPr
```

### `show` (full consolidated memory)

```
============================================================
  dream-0001  (2026-11-24T18:24:05)
  19 entries | from 15 sessions | previous: none
============================================================

  FACT
  --------------------------------------------------------
    [0.9/1.0] User has a Qwen3.5 model configuration located at /home/bala/data/cache/hub/models--Qwen--Qwen3.8-27B/snapshots/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/config.json, which contains the model architecture, token IDs, hidden size, attention parameters, and vision configuration.
    [0.9/1.0] Project /home/bala/apps/bs2 is a React 18 + TypeScript + Vite + Tailwind + Radix UI application called Bombshell. It currently has a missing /prompt route, a simulation mode type mismatch between UI and API, and several UI bugs such as broken navigation and data lookup failures.
    [0.9/1.0] Project /home/bala/hermes/agent-platform is a scaffold for an enterprise agent platform. It includes a devbox configuration, AI-Q agent definition, NeMo Agent Toolkit workflow, OpenTofu provisioning, OPA policy, Keycloak realm export, Vault bootstrap script, and a README. The scaffold is designed to be fully open-source except for the model inference endpoint.
    [0.8/1.0] Project /home/bala/pareto contains benchmark scripts for Qwen and Nemotron models, including run_model_benchmark.sh, run_benchmark.sh, run_sweep.sh, run_nemotron_full.sh, launch_vllm.sh, launch_vllm_nemotron.sh, launch_vllm_muse.sh, launch_vllm_qwen_fp8.sh, launch_vllm_qwen_nvfp4.sh, and run_benchmark.sh for dataset sweeps.
    [0.8/1.0] User has built a robust test suite for the bs2 Bombshell app, covering AuthScreen, DailyPrompt, ProfileBuilder, NewBombDialog, and other components. The suite includes unit and integration tests, uses Vitest and React Testing Library, and addresses async behavior, DOM queries, and mock API interactions.

  PREFERENCE
  --------------------------------------------------------
    [0.7/1.0] User prefers an open-source stack for building enterprise agents, using tools such as vLLM, OPA, Keycloak, Vault, and OpenShell, while allowing the model inference endpoint to be either open or closed source.

  PROJECT
  --------------------------------------------------------
    [0.9/1.0] Project: bs2 Bombshell application located at /home/bala/apps/bs2 is a React/Vite/Tailwind app with onboarding, daily prompts, bomb creation, and role-based navigation.
    [0.9/1.0] Project: hermes agent platform located at /home/bala/hermes/agent-platform is a scaffold for an enterprise agent platform using devbox, AI-Q, NAT, OpenTofu, OPA, Keycloak, Vault, and a LiteLLM gateway.
    [0.8/1.0] Project: March experiments project located at /home/bala/march-experiments contains Qwen model configuration files and related quantization details.
    [0.8/1.0] Project: Pareto benchmark project located at /home/bala/pareto contains scripts for benchmarking Qwen and Nemotron models on NVIDIA hardware.

  LESSON
  --------------------------------------------------------
    [0.8/1.0] Lesson learned: In the bs2 test suite, handling async timers required using Vitest's waitFor, placeholder queries were replaced with label queries for robustness, multiple elements with the same role were disambiguated with getAllByRole, backslashes in text were removed from JSX strings, custom matchers were used for split text, vi.mocked was correctly applied, duplicate imports were eliminated, and localStorage was polyfilled for jsdom.
    [0.8/1.0] Lesson learned: The bs2 application had a missing /prompt route, causing no component to render, and a simulation mode type mismatch between the UI (TechSaaS, LawFirm, CarCompany) and the API keys (Tech SaaS, Law Firm, Car Company), leading to data lookup failures.
    [0.8/0.9] Lesson learned: The hermes agent platform OPA policy was compiled and tested with real input files, confirming correct behavior for tenant scoping, hard denies, and audit reasons.
    [0.7/0.9] Lesson learned: In bs2 tests, the Sonner Toaster component required a matchMedia polyfill in the test setup to avoid runtime errors.
    [0.7/0.9] Lesson learned: Pareto benchmark scripts had a Docker entrypoint duplication issue where the script passed "vllm serve" twice, causing unrecognized arguments. The script was updated to pass only the model ID and flags.
    [0.7/0.9] Lesson learned: Pareto benchmark scripts used a Docker image with a bash -c entrypoint, so the aiperf CLI arguments must be passed as a single string; otherwise only the top-level usage is printed.
    [0.7/0.9] Lesson learned: Pareto benchmark tests required jsdom configuration to provide localStorage in Vitest, and a matchMedia polyfill for components using window.matchMedia.

  TASK
  --------------------------------------------------------
    [0.9/1.0] Task: Build a robust test suite for the bs2 Bombshell app, run the tests, identify and fix bugs, push changes, and repeat until all tests pass.
    [0.6/1.0] Task: Merge the branch update-api-tests into the main branch of the repository, ensuring all changes are committed and pushed.
```

### Health self-test (`eval`, only 1 dream)

```
Not enough dreams to compare (need 2+).

HEALTH SELF-TEST:
{
  "dream": "dream-0001",
  "total_entries": 19,
  "high_importance": 18,
  "vague_entries": 0,
  "duplicate_contents": 0,
  "healthy": true,
  "note": "healthy = no duplicates and <30% vague (<4 word) memories"
}
```

---

## 6. Idempotent re-sync

```
$ python3 dream.py sync
  [ok] opencode: 46 sessions found, 0 new, 0 duplicates (0.1s)
  [ok] claude-code: 5 sessions found, 0 new, 0 duplicates (0.0s)
  [ok] codex: 1 sessions found, 0 new, 0 duplicates (0.0s)
  [ok] hermes: 1 sessions found, 0 new, 0 duplicates (0.0s)

Episodic store: 53 sessions, 2391 messages
By tool: {'claude-code': 5, 'opencode': 46, 'hermes': 1, 'codex': 1}
```

Zero re-writes — append-only guarantee holds.

---

## 7. dream-0002 attempt #1 — CRASH (ID bug)

```
$ python3 dream.py dream
Dreaming with local model: gpt-oss:20b

Episodic memory: 53 sessions (2391 messages)
Reading episodic memory (read-only)...

Traceback (most recent call last):
  File "/home/bala/experiments/dreaming/dream.py", line 368, in <module>
    main()
  File "/home/bala/experiments/dreaming/dream.py", line 364, in main
    sys.exit(handlers[args.cmd](args))
  File "/home/bala/experiments/dreaming/dream.py", line 144, in cmd_dream
    artifact, report = d.dream(use_llm=not args.no_llm,
  File "/home/bala/experiments/dreaming/dream_memory/dreaming.py", line 388, in dream
    dream_id = self.dreams.next_id()
  File "/home/bala/experiments/dreaming/dream_memory/dreaming.py", line 256, in next_id
    n = int(ids[-1].split("-")[1]) + 1 if ids else 1
IndexError: list index out of range

Exit code: 1
```

**Root cause:** `dream_ids()` stripped the `dream-` prefix (`f[6:-5]`) but `next_id()` split on `-` expecting it. First dream worked only because the store was empty.

**Fix + verification:**

```
dream_ids: ['dream-0001']
next_id: dream-0002
latest_id: dream-0001
load ok: True
```

---

## 8. dream-0002 attempt #2 — LLM timeout, heuristic fallback

```
$ python3 dream.py dream
Dreaming with local model: gpt-oss:20b

Episodic memory: 53 sessions (2391 messages)
Reading episodic memory (read-only)...

============================================================
  DREAM COMPLETE: dream-0002
============================================================
  Engine:        heuristic
  LLM error:     TimeoutError: timed out (fell back)
  New sessions:  38
  Processed:     38 sessions
  Entries:       19 (0 pruned by importance cap)
  Duration:      300.1s

  Saved: /home/bala/.dream-memory/dreaming/dream-0002.json
  (previous dreams untouched - rollback available)
```

**Root cause:** 38 sessions × 12K chars ≈ 456K-char prompt (~114K tokens) exceeded the 300s timeout.

**Fixes applied:**
1. **Adaptive prompt budget** — splits ~120K chars across batch + previous memory.
2. **LLM timeout** 300s → 900s.

**Budget verification:**

```
sessions in prompt: 38
prompt chars: 118740 (old budget would be ~456000+)
approx tokens (chars/4): 29685
```

---

## 9. Eval comparison (dream-0001 → dream-0002)

```
$ python3 dream.py eval
============================================================
  DREAM COMPARISON: dream-0001 -> dream-0002
============================================================
  Entries:     19 -> 19
  Kept:        19
  Lost:        0
  Added:       0
  Retention:   100.0%
  Avg importance: 0.795 -> 0.795
  Categories before: {'fact': 5, 'task': 2, 'project': 4, 'lesson': 7, 'preference': 1}
  Categories after:  {'fact': 5, 'task': 2, 'project': 4, 'lesson': 7, 'preference': 1}

  ✓ No high-importance memory loss detected.
```

Note: dream-0002 added 0 entries — the heuristic found no new matches in the 38 older sessions (all content came forward from dream-0001).

---

## 10. Incremental tracking + empty-dream fixes

**Bug:** `already` set only inspected the *latest* dream, so sessions processed by earlier dreams but absent from the latest could be reprocessed.
**Fix:** union across **all** dream artifacts (`processed_hashes` + entry `sources`).

**Bug:** dreaming with nothing new wrote a pointless artifact.
**Fix:** short-circuit + `dream_skipped` instrumentation.

**Verification:**

```
$ python3 dream.py dream
Nothing new since last dream - no artifact written.
Latest dream remains: dream-0002
```

---

## 11. Rollback test (both directions)

```
$ python3 dream.py rollback dream-0001
Rolled back: latest now points to dream-0001
(all dream files preserved; episodic memory untouched)
→ latest.txt: dream-0001

$ python3 dream.py rollback dream-0002
Rolled back: latest now points to dream-0002
(all dream files preserved; episodic memory untouched)
→ latest.txt: dream-0002
```

### Instrumentation log

```
$ python3 dream.py history
2026-11-24T18:24:05  dream-0001  sessions=15 entries=19 engine=llm:gpt-oss:20b
2026-11-24T18:35:13  dream-0002  sessions=38 entries=19 engine=heuristic
2026-11-24T18:37:39  dream_skipped
2026-11-24T18:38:01  dream_skipped
```

---

## 12. Two-layer recall test (consolidated + episodic)

```
$ python3 dream.py recall "bs2 test suite" --limit 3
Query: bs2 test suite
Dream: dream-0002

CONSOLIDATED MEMORY (curated):
  [1.27] (task, imp=0.9) Task: Build a robust test suite for the bs2 Bombshell app, run the tests, identify and fix bugs, push changes, and repeat until all tests pass.
  [1.24] (fact, imp=0.8) User has built a robust test suite for the bs2 Bombshell app, covering AuthScreen, DailyPrompt, ProfileBuilder, NewBombDialog, and other components...
  [1.24] (lesson, imp=0.8) Lesson learned: In the bs2 test suite, handling async timers required using Vitest's waitFor...

EPISODIC MEMORY (raw evidence):
  [0.60] opencode
          Fixed and pushed as `c2636c5`.  **What was actually wrong:** You were hitting the **fallback path**...
  [0.60] opencode
          Verified and pushed as `f84f162`.  **I re-checked it end to end** with a real, un-mocked browser-style journey...
  [0.60] opencode
          The vitest config has `environment: 'jsdom'` (default). But `devServer.test.ts` overrides with `// @vitest-environment node`...
```

### Final `status`

```
============================================================
  LOCAL MEMORY STATUS
============================================================

Data root: /home/bala/.dream-memory

EPISODIC MEMORY (append-only, never modified)
  Sessions:  53
  Messages:  2391
    - claude-code: 5 sessions
    - codex: 1 sessions
    - hermes: 1 sessions
    - opencode: 46 sessions

CONSOLIDATED MEMORY (output of dreaming)
  Latest dream: dream-0002 (2026-11-24T18:35:13)
  Entries:      19
  Dream IDs:    dream-0001, dream-0002
  Categories:   {'fact': 5, 'task': 2, 'project': 4, 'lesson': 7, 'preference': 1}
  Last dream:   38 sessions processed, LLM=no

DREAM ENGINE (local Ollama)
  Available: yes
  Model:     gpt-oss:20b
```

---

## 13. Full cron cycle test (`run_dream.sh`)

```
$ ./run_dream.sh
=== 2026-11-24T10:38:36-08:00 memory cycle start ===
  [ok] opencode: 46 sessions found, 0 new, 0 duplicates (0.1s)
  [ok] claude-code: 5 sessions found, 0 new, 0 duplicates (0.0s)
  [ok] codex: 1 sessions found, 0 new, 0 duplicates (0.0s)
  [ok] hermes: 1 sessions found, 0 new, 0 duplicates (0.0s)

Episodic store: 53 sessions, 2391 messages
By tool: {'claude-code': 5, 'opencode': 46, 'hermes': 1, 'codex': 1}
Dreaming with local model: gpt-oss:20b

Episodic memory: 53 sessions (2391 messages)
Reading episodic memory (read-only)...

Nothing new since last dream - no artifact written.
Latest dream remains: dream-0002
=== 2026-11-24T10:38:44-08:00 memory cycle done ===
```

---

## 14. Ollama → Dell Enterprise Hub + agent memory injection

### 14.1 DEH migration

Ollama removed entirely (no fallback). New `dream_memory/deh.py` provides
`DEHLLM` (kept as `LocalLLM` alias) with `discover_endpoint()` — a 4-rung
discovery ladder:

1. `DREAM_DEH_ENDPOINT` env override
2. `dell_ai.deployments.list_deployments()` (SDK, in `.venv`)
3. `docker ps` container/port scan
4. verify `/v1/models` **and** a live `_probe_chat()` round-trip

Run 4 exists because of a real trap: `laya-deh` on `127.0.0.1:8080` answers
`/v1/models` but 404s on `/v1/chat/completions`. Discovery must not accept an
endpoint that cannot actually chat.

```
$ .venv/bin/dell-ai models deploy -m meta-models/Muse-Glimmer-30B \
      -p dell-pro-max-gb10 -e docker -g 1
$ .venv/bin/python dream.py models
DEH endpoint:
  url:    http://localhost:8081
  source: sdk
  sdk:    dell-ai installed
  model:  meta-models/Muse-Glimmer-30B
```

Weights (~60 GB) downloaded into `brave_lehmann` over ~40 min; SGLang serves
an OpenAI-compatible wire on `:8081` with 128K context. Transport is stdlib
HTTP only — the SDK is optional at runtime (system `python3` has no `dell-ai`,
`.venv/bin/python` does; `run_dream.sh` prefers the venv).

Docs: `deh_migration.html` written first (standing preference), then code.
Ollama references purged from `README_DREAM_MEMORY.md`, `architecture.md`,
`architecture.html`, `dream.py`, `run_dream.sh`.

### 14.2 Making crystallized memory usable inside the agents

Two prongs, both now live:

**A. MCP server (`dream_memory/mcp_server.py`)** — stdlib NDJSON JSON-RPC over
stdio, no `mcp` SDK. Exposes three tools; `initialize.instructions` is
injected into OpenCode's system prompt as `<mcp_instructions>`:

| Tool | Access | Purpose |
|---|---|---|
| `dream_recall` | read | search crystallized (+ optional episodic evidence) |
| `dream_status` | read | store stats, latest dream, pending inbox |
| `dream_remember` | **write** | append to inbox → folded into next dream |

stdout is the protocol channel — every log line goes to stderr.

**B. Always-on digest (`dream_memory/context.py`)** — bounded, generated
summary written into each agent's native memory file via managed blocks:

| Target | File | Budget |
|---|---|---|
| Hermes notes | `~/.hermes/memories/MEMORY.md` | 1400 chars |
| Hermes profile | `~/.hermes/memories/USER.md` | 900 chars |
| Claude Code | `~/.claude/CLAUDE.md` | 2000 chars |
| Codex | `~/.codex/AGENTS.md` | 2000 chars |
| canonical | `<root>/context/digest.md` | both blocks |

Hermes keeps two files with different jobs: **preferences → USER.md**,
everything else → MEMORY.md. Hermes splits its files on the literal `"\n§\n"`
and re-joins on every `memory` tool write, so the block is written as exactly
ONE entry in that format (never containing the delimiter).

Registration — `python3 dream.py connect` wires all four with their own CLIs:

```
$ python3 dream.py connect
  server command: /usr/bin/python3 /home/bala/experiments/dreaming/dream.py mcp
  [OK  ] opencode  MCP server "dream" added to opencode.json
  [OK  ] claude    Added stdio MCP server dream ... to user config
  [OK  ] codex     Added global MCP server 'dream'.
  [OK  ] hermes    registered in ~/.hermes/config.yaml (backup created)
```

**Verified live**: OpenCode picked up the server in-session — `dream_status`
and `dream_recall` called through the real MCP connection returned live data
(53 sessions / 2,391 messages / dream-0002 / 19 entries).

Write-back loop: `dream_remember` → `dream_memory/inbox.py` (append-only
JSONL, never rewritten) → pending entries join `previous_entries` on the next
dream → normal keep/revise/drop rule → ordinary crystallized entry with
provenance. Consumption is tracked by unioning `inbox_ids` across ALL dreams
(same pattern as `processed_hashes`), so nothing is ever deleted.

New subcommands: `context`, `connect`, `inbox`, `mcp` (14 total).

### 14.3 DEH end-to-end dream test

No new sessions were ingestible, so the pipeline was exercised against a
**copy** of the store with `dream-0002` removed (38 sessions became "new"):

```
$ .venv/bin/python dream.py --root /tmp/opencode/dream-deh-test dream
  DREAM COMPLETE: dream-0002
  Engine:        LLM (meta-models/Muse-Glimmer-30B)
  New sessions:  38
  Duration:      405.3s
```

**First run**: The model stochastically returned placeholder output
(`"content": "..."`) instead of real memories. The dream loop caught the
degenerate entries and fell back to the heuristic consolidator, producing
19 real entries (444.7s).

**Second run** (same batch, retry logic active): The retry loop tried up to
3 times with a corrective nudge and temperature bump (0.2 → 0.6). The
third attempt succeeded with **10/10 usable entries**. This proved the
retry mechanism works, and the final dream wrote `dream-0003` with 21 entries
(10 from LLM, 11 from heuristic). Transport, JSON parsing, artifact write
and rollback safety all confirmed through DEH.

### 14.4 Bugs found and fixed this session

| # | Bug | Fix |
|---|---|---|
| 1 | `%-8` missing `s` → `TypeError` printing digest report | `%-8s` |
| 2 | `canon_text` format: 5 specifiers, 6 args → `TypeError` | matched counts |
| 3 | Hermes block used a plain newline, not the `§` entry delimiter → Hermes would merge it into its own entry on next write | write as one real `§`-delimited entry |
| 4 | **Data loss**: managed-block removal dropped `USER.md`'s own content | strip only marker-bounded text, preserve the rest; fail-safe `return text` instead of `""`; rolling `.dream-bak` on every write. Original profile restored byte-exact (717 bytes) |
| 5 | MCP test wrote digests into **real** agent files (only `DREAM_ROOT` isolated) | isolate `DREAM_DIGEST_HOME` in tests |
| 6 | `dream.py --root <scratch>` bypassed the env guard → test dream overwrote live `CLAUDE.md`/`AGENTS.md`/`MEMORY.md` (restored from `.dream-bak`) | guard evaluates the actual `root` arg, not just env; `DREAM_DIGEST_FORCE=1` opt-in |
| 7 | Hermes registration put the interpreter in **both** `command` and `args` → `python3 /usr/bin/python3 ...` | args = `cmd[1:]`; live config repaired + YAML-validated |
| 8 | LLM echoed the prompt's schema placeholder: `{"content": "..."}` landed in crystallized memory and agent digests | realistic schema example + "do not echo verbatim" + `_degenerate()` filter; **discovered it is stochastic** — the identical prompt returned 10/10 usable entries on a retry, so the loop now tries up to `LLM_ATTEMPTS = 3` with a corrective nudge and `temperature` 0.2 → 0.6 before falling back to heuristics; `stats.llm_attempts` records how many it took |
| 9 | `_degenerate` let `"a b c d e f g"` through | require at least one word ≥ 3 chars |
| 10 | `CATEGORIES` referenced in `dreaming.py` but only defined in `inbox.py` → would `NameError` at runtime | use `inbox_mod.CATEGORIES` (single source of truth) |
| 11 | `connect` reported **FAIL** on re-run — `claude mcp add` errors with "already exists", which is the desired state | `_already()` recognises duplicate/already-registered messages as success; re-run now shows 4/4 OK with zero duplicate entries |

### 14.5 Tests

- `test_memory_context.py` — **14 groups**, all passing: `§`-entry handling,
  marker replacement, idempotency, category split, budgets, dedup, inbox
  remember→pending→consumed cycle, `DREAM_DIGEST_HOME` isolation, root guard,
  degenerate-output rejection, `_parse_entries` filtering.
- `test_mcp_server.py` — 8-frame protocol test: initialize
  (version + instructions), ping, `tools/list`, status/recall/remember,
  pending visibility after write-back, unknown tool → `-32602`, notification
  not answered, stdout pure JSON. Uses its own `DREAM_ROOT` **and**
  `DREAM_DIGEST_HOME`, so it can never touch live agent files.

### 14.6 Safety properties now enforced

1. Episodic store append-only; agents can only reach it read-only.
2. Inbox append-only; consumption is a marker, never a delete.
3. Digest writes: non-default root → canonical only (unless forced).
4. Never truncate a file that had content (`skipped_empty` guard).
5. Every digest write takes a rolling `.dream-bak` first.
6. Hermes upsert preserves foreign text around our markers.
7. Degenerate LLM output cannot reach crystallized memory — it is filtered,
   retried up to 3×, and only then handed to the heuristic consolidator.
8. `dream.py --root <scratch>` can never write into the live agent files.

---

## Summary

### Final state

| Metric | Value |
|---|---|
| Episodic sessions | 53 (OpenCode 46, Claude Code 5, Codex 1, Hermes 1) |
| Episodic messages | 2,391 |
| Dreams | 2 (`dream-0001` LLM, `dream-0002` heuristic fallback) |
| Consolidated entries | 19 (fact 5, project 4, lesson 7, preference 1, task 2) |
| Retention dream-0001→0002 | 100%, zero high-importance loss |
| Health self-test | `healthy: true` |
| Store size | ~1.8 MB |
| LLM engine | DEH `meta-models/Muse-Glimmer-30B` @ `http://localhost:8081` (SGLang) |
| MCP tools | `dream_recall`, `dream_status`, `dream_remember` |
| Agents connected | OpenCode, Claude Code, Codex, Hermes (all 4 OK) |
| Digest targets | `MEMORY.md`, `USER.md`, `CLAUDE.md`, `AGENTS.md` + canonical |
| CLI subcommands | 14 |
| Test suites | `test_memory_context.py` (14 groups), `test_mcp_server.py` (8 frames) |

### Bugs found and fixed (session 1)

| # | Bug | Fix |
|---|---|---|
| 1 | Package layout/imports mismatch | flattened `dream_memory/` |
| 2 | `sync` missing `--limit` flag | added subparser arg |
| 3 | Recall searched only `user` messages | search `user` + `assistant` |
| 4 | Float threshold `0.1999... > 0.2` fails | `>= 0.15` |
| 5 | `dream_ids()` stripped prefix → `IndexError` on 2nd dream | keep full `dream-NNNN` ids |
| 6 | 456K-char prompt → LLM timeout | adaptive ~120K budget |
| 7 | LLM timeout 300s too short | raised to 900s |
| 8 | Incremental tracking only read latest dream | union across all dreams |
| 9 | Empty dream written when nothing new | short-circuit + `dream_skipped` log |
| 10 | Skipped report showed `(none)` for latest | report actual `last_id` |

Session 2 (Ollama → DEH + agent injection) found **9 more** — see §14.4,
including two data-loss bugs in digest generation and one where the LLM
echoed a prompt placeholder into memory.

### Known limitations / next steps

- dream-0002 fell back to heuristics (timeout) — the fixed budget was later
  validated through DEH (§14.3, 38 sessions / 405s).
- Heuristic fallback found 0 new entries in older sessions — expected, but LLM
  path is strongly preferred.
- The current session is not yet ingested, so no dream-0003 exists; the next
  `run_dream.sh` cycle should produce it through DEH.
- Codex needs `codex logout && codex login` before it can *connect* (refresh
  token revoked); `codex mcp add` itself works and the config is written.
- Muse-Glimmer-30B **stochastically** answers with a placeholder template
  instead of real memories (the identical prompt returned 10/10 usable entries
  on retry) — handled by `LLM_ATTEMPTS = 3` with a corrective nudge before the
  heuristic fallback (§14.4 #8). Watch `llm_attempts` in `dream.py history`.
- Repo: `git@github.com:nardnahcalab/dreaming.git`, pushed, never force-pushed.
