#!/usr/bin/env python3
"""
dream.py - Local AI memory with dreaming (memory consolidation).

Collects episodic memory from OpenCode, Claude Code, Codex and Hermes,
preserves it append-only, and consolidates it into abstract memory by
running "dreams" against a Dell Enterprise Hub (DEH) endpoint.

Usage:
  python3 dream.py sync              # ingest new sessions from all tools
  python3 dream.py status            # show memory store statistics
  python3 dream.py dream             # run one dreaming pass (consolidation)
  python3 dream.py show              # print latest crystallized memory
  python3 dream.py recall "query"    # search crystallized + episodic memory
  python3 dream.py eval              # compare last two dreams (health check)
  python3 dream.py rollback dream-0002   # repoint latest to older dream
  python3 dream.py models            # show DEH endpoint + served models
  python3 dream.py history           # show dreaming instrumentation log

Environment:
  DREAM_ROOT           storage location (default: ~/.dream-memory)
  DREAM_MODEL          model id (default: meta-models/Muse-Glimmer-30B)
  DREAM_DEH_ENDPOINT   explicit endpoint override (skips discovery)
  DREAM_DEH_SLUG       image-slug filter for endpoint discovery
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dream_memory.episodic import EpisodicStore
from dream_memory.dreaming import Dreamer, DreamStore, LocalLLM
from dream_memory.deh import DEHLLM
from dream_memory import evaluator, recall, context, inbox, connect
from dream_memory.collectors import all_collectors


def default_root() -> str:
    return os.environ.get("DREAM_ROOT",
                          os.path.join(os.path.expanduser("~"), ".dream-memory"))


def cmd_sync(args):
    root = args.root
    store = EpisodicStore(root)
    known = store.known_hashes()

    print(f"Syncing into {root}/episodic/")
    print(f"(already stored: {len(known)} sessions)\n")

    totals = {"stored": 0, "duplicates": 0, "found": 0}
    for c in all_collectors():
        if not c.available():
            print(f"  [skip] {c.tool}: source not found")
            continue
        t0 = time.time()
        try:
            sessions = c.collect(limit=args.limit)
        except Exception as ex:  # noqa: BLE001
            print(f"  [error] {c.tool}: {type(ex).__name__}: {ex}")
            continue
        # only new ones need dedup check
        new = [s for s in sessions if s.content_hash not in known]
        result = store.add_many(new) if new else {"stored": 0, "duplicates": 0,
                                                  "total": len(sessions)}
        dt = time.time() - t0
        totals["stored"] += result["stored"]
        totals["duplicates"] += result["duplicates"]
        totals["found"] += len(sessions)
        print(f"  [ok] {c.tool}: {len(sessions)} sessions found, "
              f"{result['stored']} new, {result['duplicates']} duplicates ({dt:.1f}s)")

    stats = store.stats()
    print(f"\nEpisodic store: {stats['sessions']} sessions, "
          f"{stats['messages']} messages")
    print(f"By tool: {stats['by_tool']}")
    return 0


def cmd_status(args):
    root = args.root
    store = EpisodicStore(root)
    stats = store.stats()

    print("=" * 60)
    print("  LOCAL MEMORY STATUS")
    print("=" * 60)
    print(f"\nData root: {root}")
    print(f"\nEPISODIC MEMORY (append-only, never modified)")
    print(f"  Sessions:  {stats['sessions']}")
    print(f"  Messages:  {stats['messages']}")
    for tool, n in sorted(stats["by_tool"].items()):
        print(f"    - {tool}: {n} sessions")

    ds = DreamStore(root)
    latest = ds.latest_id()
    print(f"\nCRYSTALLIZED MEMORY (output of dreaming)")
    if latest:
        art = ds.load(latest)
        print(f"  Latest dream: {art.id} ({art.timestamp[:19]})")
        print(f"  Entries:      {len(art.entries)}")
        print(f"  Dream IDs:    {', '.join(ds.dream_ids())}")
        cats = {}
        for e in art.entries:
            cats[e.category] = cats.get(e.category, 0) + 1
        print(f"  Categories:   {cats}")
        print(f"  Last dream:   {art.sessions_processed} sessions processed, "
              f"LLM={'yes' if art.stats.get('used_llm') else 'no'}")
    else:
        print("  (no dreams yet - run `python3 dream.py dream`)")

    llm = DEHLLM()
    info = llm.endpoint_info()
    print(f"\nDREAM ENGINE (Dell Enterprise Hub)")
    print(f"  Available: {'yes' if llm.available() else 'NO'}")
    print(f"  Endpoint:  {info.get('endpoint') or '(none discovered)'}"
          f"  [{info.get('source')}]")
    print(f"  SDK:       {'dell-ai installed' if info.get('sdk_installed') else 'not installed (docker scan fallback)'}")
    print(f"  Model:     {info.get('served_model') or info.get('configured_model')}")
    print()
    return 0


def cmd_dream(args):
    root = args.root
    d = Dreamer(root, model=args.model)
    epi = EpisodicStore(root)

    stats = epi.stats()
    if stats["sessions"] == 0:
        print("No episodic memory yet. Run `python3 dream.py sync` first.")
        return 1

    llm = DEHLLM(model=args.model)
    if not args.no_llm and not llm.available():
        print("No DEH endpoint reachable - falling back to heuristic consolidator.")
        print("Deploy first: dell-ai models deploy --model-id "
              "meta-models/Muse-Glimmer-30B \\")
        print("  --platform-id dell-pro-max-gb10 --engine docker --gpus 1\n")
    elif not args.no_llm:
        _info = llm.endpoint_info()
        print(f"Dreaming via DEH: {_info.get('served_model') or llm.model}")
        print(f"  endpoint {_info.get('endpoint')} [{_info.get('source')}]\n")

    print(f"Episodic memory: {stats['sessions']} sessions "
          f"({stats['messages']} messages)")
    print("Reading episodic memory (read-only)...\n")

    t0 = time.time()
    artifact, report = d.dream(use_llm=not args.no_llm,
                               max_sessions=args.max_sessions)
    dt = time.time() - t0

    if report.get("skipped"):
        print("Nothing new since last dream - no artifact written.")
        print(f"Latest dream remains: {report['dream_id'] or '(none)'}")
        _refresh_digests(root)
        return 0

    print("=" * 60)
    print(f"  DREAM COMPLETE: {report['dream_id']}")
    print("=" * 60)
    print(f"  Engine:        {'LLM (' + str(report['model']) + ')' if report['used_llm'] else 'heuristic'}")
    if report.get("llm_attempts", 1) > 1:
        print(f"  LLM attempts:  {report['llm_attempts']}"
              + ("" if report["used_llm"] else " (all degenerate)"))
    if report["llm_error"]:
        print(f"  LLM error:     {report['llm_error']} (fell back)")
    print(f"  New sessions:  {report['new_sessions']}")
    print(f"  Processed:     {report['batch_sessions']} sessions")
    print(f"  Entries:       {report['entries']} "
          f"({report['pruned']} pruned by importance cap)")
    print(f"  Duration:      {dt:.1f}s")
    print(f"\n  Saved: {os.path.join(root, 'dreaming', report['dream_id'] + '.json')}")
    print(f"  (previous dreams untouched - rollback available)\n")

    if artifact.entries:
        print("  Top memories:")
        for e in sorted(artifact.entries, key=lambda x: -x.importance)[:8]:
            print(f"    [{e.importance:.1f}] ({e.category}) {e.content[:90]}")

    _refresh_digests(root)
    return 0


def _refresh_digests(root):
    """Keep the always-on agent digests in step with the latest dream.

    Failure here must never fail a dream: the artifact is already written and
    is the source of truth. `dream.py context` can always regenerate later.
    """
    try:
        ds = DreamStore(root)
        last_id = ds.latest_id()
        art = ds.load(last_id) if last_id else None
        extra = inbox.pending_entries(root, ds)
        changed = [r for r in context.write_digests(root, art, extra=extra)
                   if r["changed"]]
        if changed:
            print(f"\n  Context digest refreshed ({len(changed)} file(s)):")
            for r in changed:
                print(f"    {r['path']}")
    except Exception as ex:
        print(f"\n  WARN: digest refresh failed ({ex}); "
              f"run `python3 dream.py context` to retry")


def cmd_show(args):
    root = args.root
    ds = DreamStore(root)
    latest = args.dream or ds.latest_id()
    if not latest:
        print("No dreams yet. Run `python3 dream.py dream`.")
        return 1
    art = ds.load(latest)
    if not art:
        print(f"Dream {latest} not found.")
        return 1

    print("=" * 60)
    print(f"  {art.id}  ({art.timestamp[:19]})")
    print(f"  {len(art.entries)} entries | from {art.sessions_processed} sessions"
          f" | previous: {art.previous_dream or 'none'}")
    print("=" * 60)
    by_cat: dict = {}
    for e in art.entries:
        by_cat.setdefault(e.category, []).append(e)
    for cat in ("fact", "preference", "project", "lesson", "task"):
        if cat not in by_cat:
            continue
        print(f"\n  {cat.upper()}")
        print("  " + "-" * 56)
        for e in sorted(by_cat[cat], key=lambda x: -x.importance):
            print(f"    [{e.importance:.1f}/{e.confidence:.1f}] {e.content}")
    other = [c for c in by_cat if c not in ("fact", "preference", "project",
                                            "lesson", "task")]
    for cat in other:
        print(f"\n  {cat.upper()}")
        for e in by_cat[cat]:
            print(f"    [{e.importance:.1f}] {e.content}")
    print()
    return 0


def cmd_recall(args):
    root = args.root
    res = recall.search(root, args.query, limit=args.limit)

    print(f"Query: {args.query}")
    print(f"Dream: {res['dream'] or '(none)'}\n")

    if res["crystallized"]:
        print("CRYSTALLIZED MEMORY (curated):")
        for e in res["crystallized"]:
            print(f"  [{e['score']:.2f}] ({e['category']}, "
                  f"imp={e['importance']:.1f}) {e['content']}")
    else:
        print("CRYSTALLIZED MEMORY: no matches")

    print()
    if res["episodic"]:
        print("EPISODIC MEMORY (raw evidence):")
        for e in res["episodic"]:
            when = (e["when"] or "")[:10]
            print(f"  [{e['score']:.2f}] {e['tool']} {when}")
            print(f"          {e['snippet']}")
    else:
        print("EPISODIC MEMORY: no matches")
    print()
    return 0


def cmd_eval(args):
    root = args.root
    ds = DreamStore(root)
    ids = ds.dream_ids()
    if len(ids) < 2:
        # run health self-test instead
        os.environ["DREAM_ROOT"] = root
        result = evaluator.run_self_test()
        print("Not enough dreams to compare (need 2+).")
        print("\nHEALTH SELF-TEST:")
        print(json.dumps(result, indent=2))
        return 0

    before = ds.load(ids[-2])
    after = ds.load(ids[-1])
    cmp = evaluator.compare(before, after)

    print("=" * 60)
    print(f"  DREAM COMPARISON: {cmp['before']} -> {cmp['after']}")
    print("=" * 60)
    print(f"  Entries:     {cmp['entries_before']} -> {cmp['entries_after']}")
    print(f"  Kept:        {cmp['kept']}")
    print(f"  Lost:        {cmp['lost']}")
    print(f"  Added:       {cmp['added']}")
    print(f"  Retention:   {cmp['retention_rate']*100:.1f}%")
    print(f"  Avg importance: {cmp['avg_importance_before']} -> "
          f"{cmp['avg_importance_after']}")
    print(f"  Categories before: {cmp['categories_before']}")
    print(f"  Categories after:  {cmp['categories_after']}")

    if cmp["degradation_warning"]:
        print("\n  ⚠ DEGRADATION WARNING: high-importance memories were lost:")
        for e in cmp["important_lost"]:
            print(f"    - [{e['importance']:.1f}] ({e['category']}) {e['content']}")
        print("  Consider: `python3 dream.py rollback <prev-dream>`")
    else:
        print("\n  ✓ No high-importance memory loss detected.")
    print()
    return 0


def cmd_rollback(args):
    root = args.root
    ds = DreamStore(root)
    if ds.set_latest(args.dream_id):
        print(f"Rolled back: latest now points to {args.dream_id}")
        print(f"(all dream files preserved; episodic memory untouched)")
        return 0
    print(f"Dream {args.dream_id} not found. Available: {', '.join(ds.dream_ids())}")
    return 1


def cmd_models(args):
    llm = DEHLLM()
    info = llm.endpoint_info()
    print("DEH endpoint:")
    print(f"  url:    {info.get('endpoint') or '(none discovered)'}")
    print(f"  source: {info.get('source')}")
    print(f"  sdk:    {'dell-ai installed' if info.get('sdk_installed') else 'not installed'}")
    print(f"  model:  {info.get('served_model') or info.get('configured_model')}")

    models = llm.list_models()
    if not models:
        print("\nNo models served (endpoint down or no container running).")
        print("Start one with: dell-ai models deploy --model-id "
              "meta-models/Muse-Glimmer-30B \\")
        print("  --platform-id dell-pro-max-gb10 --engine docker --gpus 1")
        return 1
    print(f"\nModels served by this endpoint ({len(models)}):")
    for m in models:
        marker = "  <-- current" if m == (info.get('served_model') or info.get('configured_model')) else ""
        print(f"  - {m}{marker}")
    print("\nSet with: DREAM_MODEL=<id> python3 dream.py dream")
    return 0


def cmd_context(args):
    """Regenerate the always-on digest in every agent's memory file."""
    root = args.root
    ds = DreamStore(root)
    last_id = ds.latest_id()
    art = ds.load(last_id) if last_id else None
    extra = inbox.pending_entries(root, ds)

    report = context.write_digests(root, art, extra=extra)

    src = "%s (%d entries)" % (last_id or "no dream yet",
                               len(art.entries) if art else 0)
    if extra:
        src += " + %d pending inbox" % len(extra)
    print("\nCONTEXT DIGEST  <-  crystallized memory")
    print("  source:  %s" % src)
    print("  budget:  memory %s / user %s / single %s chars" % (
        os.environ.get("DREAM_DIGEST_MEMORY_CHARS",
                       context.DEFAULT_MEMORY_CHARS),
        os.environ.get("DREAM_DIGEST_USER_CHARS",
                       context.DEFAULT_USER_CHARS),
        os.environ.get("DREAM_DIGEST_CHARS",
                       context.DEFAULT_SINGLE_CHARS)))
    print()
    for r in report:
        mark = "updated" if r["changed"] else "current"
        print("  [%-8s] %5d chars  %s" % (mark, r["chars"], r["path"]))
    print()
    return 0


def cmd_connect(args):
    """Register the dream-memory MCP server with one or all agents."""
    agents = None if args.agent == "all" else [args.agent]
    results = connect.connect(agents)
    cmd = " ".join(connect.server_command())

    print("\nMCP REGISTRATION")
    print("  server command: %s\n" % cmd)
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print("  [%s] %-9s %s" % (mark, r["agent"],
                                  r["detail"].splitlines()[0]
                                  if r["detail"] else ""))
        if not r["ok"]:
            for ln in r["detail"].splitlines()[1:]:
                print("              %s" % ln)
    print()
    if all(r["ok"] for r in results):
        print("  Next: restart each agent, then ask it to call")
        print("        dream_recall / dream_status / dream_remember.\n")
        return 0
    print("  One or more agents failed -- see above.\n")
    return 1


def cmd_inbox(args):
    """Show memories agents have contributed but not yet dreamed."""
    root = args.root
    ds = DreamStore(root)
    pend = inbox.pending(root, ds)
    done = len(inbox.load_inbox(root)) - len(pend)

    if not inbox_mod_exists(root):
        print("Inbox is empty -- nothing remembered yet.")
        return 0

    print("\nMEMORY INBOX  (dream_remember write-back)")
    print("  pending: %d   already dreamed: %d\n" % (len(pend), done))
    for r in pend:
        print("  [%s] (%s, imp=%.1f, via %s)" % (
            r.get("id"), r.get("category", "fact"),
            float(r.get("importance", 0)), r.get("agent") or "?"))
        print("        %s" % r.get("content", ""))
    if not pend:
        print("  (none pending)")
    print()
    return 0


def inbox_mod_exists(root):
    return os.path.exists(inbox.inbox_path(root))


def cmd_mcp(args):
    """Run the MCP stdio server (registered with agents by `connect`).

    Writes ONLY JSON-RPC frames to stdout; all logging goes to stderr.
    """
    from dream_memory.mcp_server import main as mcp_main
    return mcp_main()


def cmd_history(args):
    root = args.root
    path = os.path.join(root, "dreaming", "history.jsonl")
    if not os.path.exists(path):
        print("No dream history yet.")
        return 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ts = r.get("ts", "")[:19]
            ev = r.get("event")
            if ev == "dream":
                att = r.get("llm_attempts") or 1
                att_s = f" attempts={att}" if att > 1 else ""
                print(f"{ts}  {r.get('dream_id')}  sessions={r.get('sessions_processed')} "
                      f"entries={r.get('entries')} "
                      f"engine={'llm:'+str(r.get('model'))+att_s if r.get('used_llm') else 'heuristic'}")
            else:
                print(f"{ts}  {ev} {r.get('tool','')} {r.get('session','')}")
    return 0


def main():
    p = argparse.ArgumentParser(
        prog="dream.py",
        description="Local AI memory with dreaming (memory consolidation)")
    p.add_argument("--root", default=default_root(),
                   help="storage root (default: ~/.dream-memory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync", help="ingest sessions from all tools")
    sp.add_argument("--limit", type=int, default=None,
                    help="max sessions per tool (default: all)")
    sub.add_parser("status", help="show memory statistics")

    d = sub.add_parser("dream", help="run one dreaming pass")
    d.add_argument("--no-llm", action="store_true",
                   help="force heuristic consolidator (no model)")
    d.add_argument("--max-sessions", type=int, default=40)
    d.add_argument("--model", default=None, help="DEH model id")

    s = sub.add_parser("show", help="print crystallized memory")
    s.add_argument("--dream", default=None, help="specific dream id")

    r = sub.add_parser("recall", help="search both memory layers")
    r.add_argument("query")
    r.add_argument("--limit", type=int, default=8)

    sub.add_parser("eval", help="compare last two dreams")

    rb = sub.add_parser("rollback", help="repoint latest to older dream")
    rb.add_argument("dream_id")

    sub.add_parser("models", help="show DEH endpoint and served models")
    sub.add_parser("history", help="show dreaming log")

    sub.add_parser("context", help="regenerate always-on digest in agent memory files")

    c = sub.add_parser("connect", help="register MCP server with agents")
    c.add_argument("--agent", default="all",
                   choices=["all", "opencode", "claude", "codex", "hermes"],
                   help="which agent to register with (default: all)")

    sub.add_parser("inbox", help="show remembered-but-not-yet-dreamed entries")
    sub.add_parser("mcp", help="run the MCP stdio server (used by agents)")

    args = p.parse_args()
    handlers = {
        "sync": cmd_sync, "status": cmd_status, "dream": cmd_dream,
        "show": cmd_show, "recall": cmd_recall, "eval": cmd_eval,
        "rollback": cmd_rollback, "models": cmd_models, "history": cmd_history,
        "context": cmd_context, "connect": cmd_connect, "inbox": cmd_inbox,
        "mcp": cmd_mcp,
    }
    sys.exit(handlers[args.cmd](args))


if __name__ == "__main__":
    main()
