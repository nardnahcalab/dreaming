# dream.py — Local AI Memory with Dreaming

Captures your interaction memory from **four coding agents** on this machine,
preserves it as immutable episodic memory, and consolidates it into abstract
memory by running "dreams" with a **local Ollama model**. Nothing leaves the box.

```
┌─────────────────────────────────────────────────────────────────┐
│  SOURCES (read-only, never written to)                          │
│  OpenCode    ~/.local/share/opencode/opencode.db   (SQLite)     │
│  Claude Code ~/.claude/projects/<proj>/<id>.jsonl  (JSONL)      │
│  Codex       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl       │
│  Hermes      ~/.hermes/state.db                     (SQLite)    │
└──────────────────────────┬──────────────────────────────────────┘
                           │  collectors.py  (normalize → Session)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  EPISODIC STORE   ~/.dream-memory/episodic/                     │
│  sessions/<hash>.json   written ONCE, never modified            │
│  index.json             metadata only                          │
│  ingest_log.jsonl       append-only operation log               │
└──────────────────────────┬──────────────────────────────────────┘
                           │  dreaming.py  (read-only)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  DREAMING (local Ollama, default gpt-oss:20b)                   │
│  extracts: fact | preference | project | lesson | task          │
│  caps entries by importance ("plan to forget")                  │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  CONSOLIDATED STORE  ~/.dream-memory/dreaming/                  │
│  dream-0001.json, dream-0002.json, ...  (each NEW, immutable)   │
│  latest.txt          pointer — rollback = repoint to older dream│
│  history.jsonl       instrumentation log                        │
└─────────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
python3 dream.py sync        # ingest new sessions from all 4 tools
python3 dream.py status      # what's in memory
python3 dream.py dream       # run one dreaming pass (local LLM)
python3 dream.py show        # print consolidated memory
python3 dream.py recall "pytest preferences"   # search both layers
python3 dream.py eval        # compare last two dreams (health check)
python3 dream.py rollback dream-0001          # repoint latest
python3 dream.py models      # list local Ollama models
python3 dream.py history     # dreaming instrumentation log
```

No dependencies beyond Python 3.12 stdlib. Ollama optional (heuristic
fallback keeps the pipeline working offline).

## The four safety principles (from the learning guide)

1. **Preserve the input** — episodic store is append-only; session files
   are written with exclusive-create (`"x"`) and never opened for writing
   again. Dreaming reads it read-only.
2. **Instrument the loop** — every dream appends to `dreaming/history.jsonl`;
   every ingest appends to `episodic/ingest_log.jsonl`.
3. **Compare with an eval** — `dream.py eval` diffs the last two dreams and
   warns if any high-importance memory was lost (Zhang's degradation risk).
4. **Plan to forget** — consolidation caps the entry list by importance;
   episodic memory itself is **never** pruned.

## Why Anthropic-style (parallel artifact)

Zhang et al. (arXiv:2605.12978) showed that iterated in-place rewriting of
memory by LLMs causes three failure modes: **misgrouping**, **interference**,
**overfit**. This system therefore never rewrites an existing dream: each pass
writes `dream-NNNN.json` as a new file, and `latest.txt` is just a pointer
that can be rolled back at any time.

## Configuration

| Env var       | Default              | Purpose                        |
|---------------|----------------------|--------------------------------|
| `DREAM_ROOT`  | `~/.dream-memory`    | storage location               |
| `DREAM_MODEL` | `gpt-oss:20b`        | Ollama model used for dreaming |

```bash
DREAM_MODEL=nemotron3:33b python3 dream.py dream
python3 dream.py dream --max-sessions 15 --no-llm   # heuristic only
```

## Scheduling (automatic daily dreaming)

```bash
# one full cycle: sync all 4 tools -> dream -> health check
./run_dream.sh

# install: every day at 3am
crontab -e
0 3 * * * /home/bala/experiments/dreaming/run_dream.sh >> ~/.dream-memory/dream_cron.log 2>&1
```

`run_dream.sh` logs an `ALERT` line if the eval detects high-importance
memory loss, so `grep ALERT ~/.dream-memory/dream_cron.log` finds regressions.

## Layout

```
dream.py                 CLI entry point
dream_memory/
  schema.py              Session, Message, MemoryEntry, DreamArtifact
  collectors.py          4 tool collectors → normalized Session objects
  episodic.py            append-only store
  dreaming.py            Dreamer, DreamStore, LocalLLM, heuristic fallback
  evaluator.py           dream comparison + Zhang-style health self-test
  recall.py              two-layer search (consolidated + episodic)
```
