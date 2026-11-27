# dream.py — Local AI Memory with Dreaming

Captures your interaction memory from **four coding agents** on this machine,
preserves it as immutable episodic memory, and consolidates it into abstract
memory by running "dreams" against a **Dell Enterprise Hub (DEH)** deployment —
a Dockerised SGLang server on this box, discovered via the `dell-ai` SDK.
Nothing leaves the box.

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
│  DREAMING (DEH endpoint, default meta-models/Muse-Glimmer-30B)  │
│  /v1/chat/completions · SGLang on dell-pro-max-gb10             │
│  extracts: fact | preference | project | lesson | task          │
│  caps entries by importance ("plan to forget")                  │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  CRYSTALLIZED STORE  ~/.dream-memory/dreaming/                  │
│  dream-0001.json, dream-0002.json, ...  (each NEW, immutable)   │
│  latest.txt          pointer — rollback = repoint to older dream│
│  history.jsonl       instrumentation log                        │
└─────────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
python3 dream.py sync        # ingest new sessions from all 4 tools
python3 dream.py status      # what's in memory
python3 dream.py dream       # run one dreaming pass (DEH endpoint)
python3 dream.py show        # print crystallized memory
python3 dream.py recall "pytest preferences"   # search both layers
python3 dream.py eval        # compare last two dreams (health check)
python3 dream.py rollback dream-0001          # repoint latest
python3 dream.py models      # show DEH endpoint + served models
python3 dream.py history     # dreaming instrumentation log
python3 dream.py context     # regenerate always-on digests in agent files
python3 dream.py connect     # register MCP server with the 4 agents
python3 dream.py inbox       # memories agents wrote back, not yet dreamed
python3 dream.py mcp         # run the MCP stdio server (agents call this)
```

### Agent integration

Crystallized memory reaches the agents two ways — both local, no cloud:

```bash
python3 dream.py connect     # one-shot: wires OpenCode, Claude Code,
                             # Codex and Hermes (their own CLIs + a
                             # timestamped backup of hermes config.yaml)
```

| Mechanism | Cost when idle | What it gives the agent |
|---|---|---|
| MCP tools `dream_recall` / `dream_status` / `dream_remember` | 0 tokens | on-demand deep search + write-back |
| Bounded digest in `CLAUDE.md`, `AGENTS.md`, `MEMORY.md`, `USER.md` | ~2 KB always | preferences/projects/lessons already in the prompt |

The digest is a managed block (`<!-- BEGIN dream-memory:managed -->` for
markdown, a real `\n§\n` entry for Hermes) so regeneration never touches your
own notes. `dream_remember` appends to an inbox the next dream folds in.

No dependencies beyond Python 3.12 stdlib. The `dell-ai` SDK is **optional**
(it improves endpoint discovery); without it the client falls back to a
`docker ps` scan, and with no endpoint at all the heuristic fallback keeps
the pipeline working offline.

### Dream engine (DEH)

```bash
# one-time: install the SDK into the project venv (system pip is externally managed)
uv venv .venv && uv pip install --python .venv/bin/python dell-ai
.venv/bin/dell-ai login --token "$HF_TOKEN"

# deploy the dream model (image already pulled; weights download at first boot)
.venv/bin/dell-ai models deploy -m meta-models/Muse-Glimmer-30B \
    -p dell-pro-max-gb10 -e docker -g 1

# verify, then dream
.venv/bin/dell-ai status
python3 dream.py models
python3 dream.py dream
```

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

| Env var             | Default                          | Purpose                                  |
|---------------------|----------------------------------|------------------------------------------|
| `DREAM_ROOT`        | `~/.dream-memory`                | storage location                         |
| `DREAM_MODEL`       | `meta-models/Muse-Glimmer-30B`   | model id sent in the chat body           |
| `DREAM_DEH_ENDPOINT`| *(auto-discovered)*              | explicit endpoint; skips discovery       |
| `DREAM_DEH_SLUG`    | `muse-glimmer`                   | image-slug filter for discovery          |
| `DREAM_TIMEOUT`     | `900`                            | seconds before a chat call is abandoned  |

```bash
DREAM_DEH_ENDPOINT=http://localhost:8081 python3 dream.py dream
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
  dreaming.py            Dreamer, DreamStore, heuristic fallback
  deh.py                 DEH endpoint discovery + OpenAI-compatible client
  evaluator.py           dream comparison + Zhang-style health self-test
  recall.py              two-layer search (crystallized + episodic)
  inbox.py               dream_remember write-back (append-only JSONL)
  context.py             bounded digest + managed blocks in agent files
  mcp_server.py          stdlib MCP stdio server (3 tools)
  connect.py             register the server with the 4 agent CLIs
test_memory_context.py   digest/inbox/guard regression tests
```

## Documentation

| Document | Contents |
|---|---|
| `architecture.md` | full component reference, storage layout, safety map, failure modes |
| `architecture.html` | animated visual architecture (blue = prompt/response, purple = information, gold = intelligence) |
| `agent_memory.html` | MCP tools + always-on digest: how memory reaches the 4 agents |
| `deh_migration.html` | Ollama → Dell Enterprise Hub migration reference |
| `runlog.md` | session log: every command, failure and fix |
| `learning_guide.html` / `DREAMING_IN_AI.md` | concepts and paper references |
