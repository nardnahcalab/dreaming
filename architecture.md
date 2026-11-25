# architecture.md — System Architecture

**Project:** local AI memory capture + dreaming (memory consolidation)
**Entry point:** `python3 dream.py <command>`
**Language:** Python 3.12 (stdlib only — no third-party dependencies)
**Runtime dependencies:** local Ollama server (optional; heuristic fallback)
**Data:** 100% local, `~/.dream-memory` by default

---

## 1. Design goals

| Goal | How the architecture achieves it |
|---|---|
| **Capture everything** | 4 collectors normalize 4 different on-disk formats into one schema |
| **Never lose the past** | Episodic store is append-only, content-addressed, write-once |
| **Consolidate safely** | Anthropic-style: each dream writes a NEW artifact; input untouched |
| **Stay local** | Sources read via read-only URIs; LLM is Ollama on localhost |
| **Recover from mistakes** | `latest.txt` pointer → rollback to any prior dream in one command |
| **Prove it works** | Every dream is compared to its predecessor (eval) and logged |
| **Run unattended** | `run_dream.sh` = sync → dream → degradation alert, cron-ready |

### Architecture style: Anthropic-style (parallel artifact)

Chosen over OpenAI-style in-place rewriting because Zhang et al.
(arXiv:2605.12978) demonstrated three failure modes of iterated in-place
memory rewriting by LLMs: **misgrouping**, **interference**, **overfit**.
Here the input (episodic) is never modified and every dream is a new file,
so degradation is always detectable and reversible.

---

## 2. High-level data flow

```
┌────────────────────────────────────────────────────────────────────────┐
│  SOURCES  (opened READ-ONLY; never written to)                         │
│                                                                        │
│  OpenCode      ~/.local/share/opencode/opencode.db      SQLite (24GB)  │
│  Claude Code   ~/.claude/projects/<proj>/<id>.jsonl     JSONL          │
│  Codex         ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl  JSONL     │
│  Hermes        ~/.hermes/state.db                       SQLite         │
└───────────────────────────────┬────────────────────────────────────────┘
                                │  collectors.py
                                │  (parse → _clean() → Message/Session)
                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 1: EPISODIC MEMORY      ~/.dream-memory/episodic/               │
│  (raw, immutable, append-only — the "hippocampus")                     │
│                                                                        │
│  sessions/<content_hash>.json   written ONCE (open mode "x")           │
│  index.json                     hash → metadata (atomic replace)       │
│  ingest_log.jsonl               append-only audit log                  │
└───────────────────────────────┬────────────────────────────────────────┘
                                │  Dreamer.dream()  (READ-ONLY access)
                                │  local Ollama gpt-oss:20b  ──or──  regex
                                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 2: CRYSTALLIZED MEMORY  ~/.dream-memory/dreaming/               │
│  (curated, versioned, rollback-able — the "neocortex")                 │
│                                                                        │
│  dream-0001.json  dream-0002.json  ...   immutable artifacts           │
│  latest.txt                          pointer (only thing ever updated) │
│  history.jsonl                       append-only instrumentation       │
└───────────────────────────────┬────────────────────────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
     recall.py            evaluator.py          rollback (CLI)
   2-layer search     compare dream N-1→N      repoint latest.txt
   (curated + raw)    + health self-test
```

**Invariant:** arrows only point *down*. Nothing ever writes upward into a
higher-trust layer, and nothing ever rewrites an existing artifact.

---

## 3. Repository layout

```
dreaming/
├── dream.py                  CLI entry point (argparse, 10 subcommands)
├── run_dream.sh              cron wrapper: sync → dream → alert
│
├── dream_memory/             ★ the production pipeline package
│   ├── __init__.py
│   ├── schema.py             normalized data structures (shared contract)
│   ├── collectors.py         4 tool-specific collectors → Session
│   ├── episodic.py           EpisodicStore: append-only layer 1
│   ├── dreaming.py           LocalLLM, prompts, DreamStore, Dreamer
│   ├── evaluator.py          dream comparison + health self-test
│   └── recall.py             two-layer keyword/score search
│
├── core/                     reference/learning implementation
│   ├── memory/data_structures.py        Session, MemoryEntry, MemoryStore
│   ├── consolidation/anthropic_style.py AnthropicDreams (+ selective forgetting)
│   ├── consolidation/openai_style.py    OpenAIDreaming (+ temporal awareness)
│   └── evaluation/evaluator.py          MemoryEvaluator, ZhangBenchmarkEvaluator
│
├── examples/compare_architectures.py     side-by-side demo of both styles
│
├── *.html  open_learning.sh              interactive learning center
├── README_DREAM_MEMORY.md                pipeline docs + architecture ASCII
├── runlog.md                             full session run log
├── architecture.md                       this file
└── .gitignore                            excludes *.db, .dream-memory/, pycache
```

`dream_memory/` is the system that runs. `core/` is the research-grade
reference implementation (both architectures + Zhang benchmark tests) used
to validate the design decisions before building the pipeline.

---

## 4. Component reference

### 4.1 `dream_memory/schema.py` — the shared contract

Every component speaks these five dataclasses. Collectors produce them,
stores serialize them, the dreamer consumes them — so no component ever
needs to know which tool a session came from.

| Dataclass | Role | Key fields |
|---|---|---|
| `Message` | one normalized turn | `role` (`user`/`assistant`/`system`/`tool`), `content`, `timestamp`, `meta` |
| `Session` | unit of episodic memory | `id`, `tool`, `messages[]`, `project`, `started`/`ended`, `title`, `meta` |
| `MemoryEntry` | one crystallized memory | `content`, `category`, `importance`, `confidence`, `sources[]` (session hashes), `created_dream`, `last_seen_dream` |
| `DreamArtifact` | output of one dream | `id` (`dream-NNNN`), `entries[]`, `previous_dream`, `stats{}` |
| *(helpers)* | | `now_iso()`, `stable_json()` |

**Critical methods:**

- `Session.content_hash` — SHA-256 over `(tool, id, role, content)*`, truncated
  to 24 hex chars. This is the dedup key: identical sessions are stored once,
  forever. Pure function of content, so it is stable across runs.
- `Session.transcript_text(max_chars)` — renders the session for the LLM:
  only `user`/`assistant` roles, each message clipped at 3,000 chars,
  whole transcript clipped at `max_chars`. This is where prompt size is
  ultimately controlled.
- `MemoryEntry.from_dict()` — filters unknown keys, so older dream files
  remain loadable after schema evolution (forward compatibility).

**Category taxonomy** (fixed set, enforced on LLM output):
`fact | preference | project | lesson | task`.

---

### 4.2 `dream_memory/collectors.py` — ingestion layer

Four collectors, one interface:

```python
class XCollector:
    tool: str                 # "opencode" | "claude-code" | "codex" | "hermes"
    def available(self) -> bool
    def collect(self, limit: Optional[int] = None) -> List[Session]
```

`all_collectors()` returns them in fixed order; `dream.py sync` iterates
that list, skips unavailable sources, and reports per-tool counts.

| Collector | Source | Parsing strategy |
|---|---|---|
| **OpenCodeCollector** | `opencode.db` SQLite | `session_v2` rows joined to `session_message` rows; JSON `data` column → `content` parts; keeps `type ∈ {user, assistant}`; `directory` → `project` |
| **ClaudeCodeCollector** | `~/.claude/projects/*/*.jsonl` | one JSON object per line; `type ∈ {user, assistant}` → `message.content`; skips `/subagents/` files (children of main sessions), skips `<system-reminder` content; `ai-title` line → title |
| **CodexCollector** | `~/.codex/sessions/**/rollout-*.jsonl` | prefers `response_item` where `payload.type == "message"`; falls back to `event_msg` `user_message`/`agent_message` with a 80-char prefix dedup guard; `session_meta.cwd` → `project` |
| **HermesCollector** | `state.db` SQLite | `sessions` joined to `messages`; keeps `role ∈ {user, assistant}`; `cwd` → `project` |

**Shared helpers:**

- `_iso(ts)` — best-effort timestamp normalization (s / ms / µs / ISO).
- `_clean(text)` — extracts plain text from `str` or list-of-blocks
  (`text`, `thinking`, `output_text`); **drops `tool_use`/`tool_result`
  blocks** as noise. This is the main noise filter before storage.

**Safety properties:**
- Both SQLite sources open with `file:...?mode=ro` URI mode — the 24 GB
  live OpenCode database is never locked or written.
- JSONL files opened with `errors="replace"` — a corrupt line never
  crashes a sync.
- `limit` applies per tool (most recent N), for sampling/testing.

---

### 4.3 `dream_memory/episodic.py` — Layer 1, the immutable store

Implements safety principle #1 (*preserve the input*).

```
~/.dream-memory/episodic/
├── sessions/<content_hash>.json   # one file per session, written ONCE
├── index.json                     # hash → {tool, session_id, project, title,
│                                  #         started, ended, messages, stored_at}
└── ingest_log.jsonl               # append-only: stored / skip_duplicate
```

| Method | Behavior |
|---|---|
| `add(session)` | if file exists → log `skip_duplicate`, return `False`; else `open(path, "x")` (exclusive create — race-safe), then update index, log `stored` |
| `add_many(sessions)` | loops `add`, returns `{stored, duplicates, total}` |
| `known_hashes()` | fast path: `sync` filters candidates against this before touching disk |
| `all_sessions(tool=None)` | loads everything, sorted by `started` (chronological) |
| `get(hash)` | lazy single-session load |
| `stats()` | `{sessions, messages, by_tool}` — powers `dream.py status` |

**Design notes:**
- Content-addressed storage ⇒ re-running `sync` is a no-op (verified:
  46/5/1/1 sessions found, **0 new, 0 duplicates** on second run).
- `index.json` is metadata only and rewritten via **atomic replace**
  (`tmp` + `os.replace`), so a crash mid-write can't corrupt it.
  Session *content* files are never rewritten after creation.
- `ingest_log.jsonl` gives a full audit trail of every ingest decision.

---

### 4.4 `dream_memory/dreaming.py` — the consolidation engine

The largest module; five parts.

#### (a) `LocalLLM` — Ollama client (stdlib `urllib` only)

| Method | Purpose |
|---|---|
| `available()` | 5s probe of `GET /api/tags` |
| `list_models()` | model names for `dream.py models` |
| `chat(system, prompt, temp=0.2)` | `POST /api/chat` with `stream:false`, `format:"json"`, `timeout=900` |

Model selection: constructor arg → `DREAM_MODEL` env → default `gpt-oss:20b`.
JSON mode is requested from Ollama; `_extract_json()` still defends against
markdown fences and surrounding prose.

#### (b) Prompt construction

- `DREAM_SYSTEM` — the system prompt. Defines the 5 categories, the
  durability rule ("only what matters in FUTURE conversations"), the
  specificity rule (with a worked example), importance/confidence
  semantics, an anti-hallucination rule, and the strict output schema.
- `_build_dream_prompt(sessions, previous_entries)` — assembles:
  1. previous crystallized memory (JSON) + *update rule* (keep / revise /
     drop / add),
  2. each new transcript with a header (`tool`, title, project, when),
  3. closing instruction.
- **Adaptive budget:** `total_budget = 120,000 chars`, divided across the
  batch minus the size of the previous-memory JSON, clamped to
  `[2,000 … 12,000]` chars per session. This keeps prompts near ~30K
  tokens (measured: 38 sessions → 118,740 chars) inside local context
  windows. *This fixed a real 456K-char timeout failure — see runlog §8.*

#### (c) `heuristic_consolidate(sessions, previous)` — offline fallback

Deterministic regex classifier, no model required:
- Only scans `user` messages, lines 15–300 chars.
- Ordered pattern families: preference → fact → project → lesson
  (`_PREFERENCE_PATTERNS` etc.).
- Carries forward all previous entries (they're already curated),
  dedups by lowercased content, assigns `importance=0.6, confidence=0.5`.

Used when Ollama is unreachable, `--no-llm` is passed, or the LLM call
raises (timeout, connection, bad JSON).

#### (d) `DreamStore` — versioned artifact storage

```
~/.dream-memory/dreaming/
├── dream-0001.json   # immutable (open mode "x"; FileExistsError if re-saved)
├── dream-0002.json
├── latest.txt        # the ONLY mutable file: current pointer
└── history.jsonl     # append-only instrumentation
```

| Method | Notes |
|---|---|
| `dream_ids()` | full ids, zero-padded ⇒ `sorted()` == chronological |
| `next_id()` | `dream-{n+1:04d}` |
| `save(artifact)` | refuses to overwrite an existing dream, then repoints `latest.txt` |
| `set_latest(id)` | **rollback** — validates the file exists, repoints pointer |
| `log(record)` | appends to `history.jsonl` with `ts` |

#### (e) `Dreamer.dream()` — the orchestration loop

```
1. load ALL episodic sessions
2. compute `already` = union over EVERY dream artifact of
     {entry.sources} ∪ {stats.processed_hashes}
   (union across all dreams — not just latest — so a session
    processed by dream-0001 is never reprocessed by dream-0003)
3. new_sessions = all − already;  batch = last max_sessions of them
4. if batch empty → log dream_skipped, return early (no empty artifact)
5. build prompt (previous entries + batch transcripts)
6. try LLM → validate/clamp JSON into MemoryEntry[]
   on ANY exception → record llm_error, fall back to heuristic
7. dedup by content, sort by importance, cap at max_entries ("plan to forget")
8. assemble DreamArtifact with stats{} (incl. processed_hashes)
9. dreams.save()   → NEW immutable file + repoint latest.txt
10. dreams.log()   → instrumentation record
11. return (artifact, report)
```

**Failure handling:** every LLM error is captured into `stats.llm_error`
and surfaced in the CLI, so a dream always *completes* — worst case with
the heuristic engine. The failed attempt is still visible in `history.jsonl`.

---

### 4.5 `dream_memory/evaluator.py` — safety principle #3

| Function | Purpose |
|---|---|
| `compare(before, after)` | diffs two dreams: `kept / lost / added`, `retention_rate`, `avg_importance` before/after, category histograms, and **`important_lost`** — any lost entry with `importance ≥ 0.7`. Sets `degradation_warning` if any exist (Zhang's degradation risk made measurable) |
| `run_self_test()` | single-dream health check: total entries, high-importance count, **vague entries** (<4 words), **duplicate contents**. `healthy = no dupes && <30% vague` |

`dream.py eval` uses `compare()` when 2+ dreams exist, otherwise falls back
to `run_self_test()`.

---

### 4.6 `dream_memory/recall.py` — two-layer retrieval

Mirrors the research: curated abstract memory first, raw episodic as
evidence.

| Layer | Scoring | Threshold |
|---|---|---|
| **Crystallized** | `1.0 × (hits/query_tokens)` over `content + category`, **+ 0.3 × importance** (curated memories rank higher) | `≥ 0.15` |
| **Episodic** | `0.6 × (hits/query_tokens)` over each message (`user` **and** `assistant`) | `≥ 0.15` |

- `_tokenize()` — lowercase split on non-alphanumerics, tokens >2 chars.
- Returns `{query, crystallized[], episodic[], dream}` sorted by score,
  episodic snippets flattened to 300 chars.
- Pure stdlib (no TF-IDF/embeddings) — deliberate: keeps the system
  dependency-free. *(Two bugs were fixed here: role filter excluded
  assistant messages; float precision broke `> 0.2` — see runlog §4.)*

---

### 4.7 `dream.py` — CLI

Global flag: `--root` (default `DREAM_ROOT` env or `~/.dream-memory`).

| Command | Handler | What it does |
|---|---|---|
| `sync [--limit N]` | `cmd_sync` | run all collectors, filter by known hashes, store new sessions; per-tool timing + final stats |
| `status` | `cmd_status` | episodic stats, latest dream summary, Ollama availability |
| `dream [--no-llm] [--max-sessions N] [--model M]` | `cmd_dream` | one consolidation pass; prints engine, counts, duration, top memories; handles `skipped` |
| `show [--dream ID]` | `cmd_show` | print entries grouped by category, sorted by importance |
| `recall QUERY [--limit N]` | `cmd_recall` | two-layer search |
| `eval` | `cmd_eval` | `compare()` of last two dreams, or health self-test; prints ⚠ on degradation |
| `rollback ID` | `cmd_rollback` | repoint `latest.txt` to any prior dream |
| `models` | `cmd_models` | list Ollama models |
| `history` | `cmd_history` | pretty-print `history.jsonl` |

---

### 4.8 `run_dream.sh` — unattended operation

```bash
sync  →  dream --max-sessions $DREAM_MAX_SESSIONS  →  eval | grep "DEGRADATION WARNING"
```
- `set -u`, per-step `|| echo WARN` so one failure doesn't abort the cycle.
- Exit path logs `ALERT:` on degradation ⇒ `grep ALERT <log>` finds regressions.
- Cron: `0 3 * * * /home/bala/experiments/dreaming/run_dream.sh >> ~/.dream-memory/dream_cron.log 2>&1`

---

### 4.9 `core/` — reference implementation (research layer)

Not invoked by `dream.py`; exists to encode and test the design rationale.

| Module | Contents |
|---|---|
| `core/memory/data_structures.py` | richer `Session`/`MemoryEntry`/`MemoryStore` with `MemoryType` enum (incl. `PREFERENCES`), `SessionState`, `ConsolidationResult` |
| `core/consolidation/openai_style.py` | `OpenAIDreaming` — in-place rewrite (the anti-pattern, kept for comparison) + `OpenAIDreamingWithTemporalAwareness` |
| `core/consolidation/anthropic_style.py` | `AnthropicDreams` — parallel artifact, `rollback_to_previous()`, `evaluate_dream()` + `AnthropicDreamsWithSelectiveForgetting` |
| `core/evaluation/evaluator.py` | `MemoryEvaluator` + **`ZhangBenchmarkEvaluator`** implementing the three failure modes: *misgrouping*, *interference*, *overfit*; plus `create_example_test_cases()` / `create_example_zhang_test_data()` |
| `examples/compare_architectures.py` | runs both styles side by side and prints the Zhang benchmark |

---

## 5. On-disk storage (complete)

```
~/.dream-memory/                      default root (DREAM_ROOT)
├── episodic/
│   ├── sessions/<hash>.json          53 files, write-once, immutable
│   ├── index.json                    metadata only, atomic replace
│   └── ingest_log.jsonl              append-only audit
├── dreaming/
│   ├── dream-0001.json … N           immutable artifacts
│   ├── latest.txt                    current pointer (rollback target)
│   └── history.jsonl                 append-only instrumentation
└── dream_cron.log                    (created by cron redirect)

Total: ~1.8 MB for 53 sessions / 2,391 messages / 2 dreams
```

**Mutability matrix**

| File | Written | Ever updated? |
|---|---|---|
| `episodic/sessions/*.json` | once (`"x"` mode) | **never** |
| `episodic/index.json` | on add | yes — metadata only, atomic |
| `episodic/ingest_log.jsonl` | on add/skip | append-only |
| `dreaming/dream-NNNN.json` | once (`"x"` mode) | **never** |
| `dreaming/latest.txt` | on save/rollback | yes — the single rollback lever |
| `dreaming/history.jsonl` | on dream/skip | append-only |
| Source DBs / JSONL | — | **never (read-only)** |

---

## 6. Safety principles → implementation map

| # | Principle | Implementation | Verification |
|---|---|---|---|
| 1 | **Preserve the input** | write-once session files; `"x"` open mode; SQLite `mode=ro`; dreams immutable | re-sync = 0 writes (runlog §6) |
| 2 | **Instrument the loop** | `ingest_log.jsonl` + `history.jsonl` (every dream, skip, error) | `dream.py history` |
| 3 | **Compare with an eval** | `evaluator.compare()` + `run_self_test()`; degradation warning on lost `importance ≥ 0.7` | 100% retention dream-0001→0002 (runlog §9) |
| 4 | **Plan to forget** | `max_entries` importance cap on crystallized store; **episodic never pruned** | `entries_pruned` in stats |

---

## 7. Configuration

| Env var | Default | Purpose |
|---|---|---|
| `DREAM_ROOT` | `~/.dream-memory` | storage root |
| `DREAM_MODEL` | `gpt-oss:20b` | Ollama model for dreaming |
| `DREAM_MAX_SESSIONS` | `20` | batch cap for `run_dream.sh` |

CLI overrides: `--root`, `--model`, `--max-sessions`, `--no-llm`.

Available local models on this machine: `gemma4:31b`, `nemotron3:33b`,
`gpt-oss:20b`, `granite4:tiny-h`, `olmo-3:7b-*`, `functiongemma`.

---

## 8. Failure modes & mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| Ollama down / unreachable | `available()` probe | heuristic consolidator runs; pipeline continues |
| LLM timeout (large batch) | `TimeoutError` in `chat()` | caught → `stats.llm_error` → heuristic; 900s timeout + 120K-char budget make it rare |
| Malformed LLM JSON | `_extract_json()` raises | caught → heuristic fallback |
| Corrupt source line | `json.JSONDecodeError` | line skipped, session continues |
| Source tool absent | `available()` | collector skipped with `[skip]` message |
| Crash during index write | — | `tmp` + `os.replace` atomic rename |
| Bad dream produced | `eval` degradation warning | `rollback dream-NNNN` (pointer-only, instant) |
| Nothing new to dream | `batch == empty` | early return, `dream_skipped` logged, no empty artifact |
| Concurrent sync | — | exclusive `"x"` create on session files |

---

## 9. Extension points

- **New tool:** implement `available()` + `collect()` in `collectors.py`,
  append to `all_collectors()`. Schema/store/dreamer unchanged.
- **Better extraction:** replace `DREAM_SYSTEM` / add a summarization pass —
  artifact format and rollback semantics are unaffected.
- **Semantic recall:** swap `recall._score()` for embeddings; the two-layer
  result shape (`crystallized[]` + `episodic[]`) stays the same.
- **Scheduled dreaming:** `run_dream.sh` under cron/systemd timer (documented
  in `README_DREAM_MEMORY.md`).

---

## 10. Related documents

- `README_DREAM_MEMORY.md` — quick start, ASCII architecture, cron setup
- `runlog.md` — full session log: every command, failure, and fix
- `DREAMING_IN_AI.md` / `learning_guide.html` — concepts and references
  (OpenAI Dreaming V3 vs Anthropic Dreams; Zhang et al. arXiv:2605.12978)
- `core/` + `examples/compare_architectures.py` — architecture validation
