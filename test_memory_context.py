#!/usr/bin/env python3
"""
Regression tests for the context digest + inbox write-back.

Run:  python3 test_memory_context.py

The hermes tests exist specifically because of a real data-loss bug: an
earlier version of upsert_hermes() deleted a user's own memory entry when
our managed markers had been embedded inside it. Test 1 and 2 pin that down.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dream_memory import context, inbox                      # noqa: E402
from dream_memory.dreaming import DreamStore                 # noqa: E402
from dream_memory.schema import DreamArtifact, MemoryEntry, now_iso  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s  %s" % (name, detail))
        FAILS.append(name)


def entries_of(text):
    return [e.strip() for e in text.split(context.HERMES_DELIM) if e.strip()]


# ---------------------------------------------------------------------------
print("\n1. hermes: foreign content survives marker removal (data-loss bug)")
orig = "User prefers dark mode and tabs over spaces."
# the broken pre-fix shape: markers embedded INSIDE a larger entry
merged = orig + "\n" + context.Hermes_BEGIN + "\nold body\n" + context.Hermes_END
out = context.upsert_hermes(merged, "new body")
ents = entries_of(out)
check("original entry kept", any(orig in e for e in ents), repr(out))
check("old body gone", not any("old body" in e for e in ents), repr(out))
check("new body present", any("new body" in e for e in ents), repr(out))
check("exactly 2 entries", len(ents) == 2, "got %d: %r" % (len(ents), ents))

print("\n2. hermes: plain hermes content is untouched by a fresh insert")
out = context.upsert_hermes(orig + "\n", "digest line")
ents = entries_of(out)
check("hermes entry first", ents[0] == orig, repr(ents))
check("managed block second", "[dream-memory:managed]" in ents[1], repr(ents))
check("delimiter present", context.HERMES_DELIM in out)

print("\n3. hermes: regeneration is idempotent (no entry growth)")
a = context.upsert_hermes(orig + "\n", "digest line")
b = context.upsert_hermes(a, "digest line")
check("stable across runs", a == b, "%r != %r" % (a, b))
c = context.upsert_hermes(b, "updated digest")
check("body replaced not appended",
      len(entries_of(c)) == 2 and "updated digest" in c, repr(c))

print("\n4. markdown: managed block replaced in place, other text kept")
doc = "# My notes\n\nDo not delete me.\n\n" + context.MD_BEGIN + "\nold\n" + context.MD_END + "\n\nMore notes.\n"
out = context.upsert_block(doc, "fresh", context.MD_BEGIN, context.MD_END)
check("heading kept", "# My notes" in out, repr(out))
check("other note kept", "Do not delete me." in out and "More notes." in out)
check("old replaced", "old" not in out and "fresh" in out, repr(out))
check("single block", out.count(context.MD_BEGIN) == 1, repr(out))

print("\n5. markdown: append when no block exists")
out = context.upsert_block("# Hi\n", "body", context.MD_BEGIN, context.MD_END)
check("appended", context.MD_BEGIN in out and "# Hi" in out, repr(out))
check("idempotent", context.upsert_block(out, "body",
                                         context.MD_BEGIN,
                                         context.MD_END) == out)

print("\n6. category split: preference -> user block, rest -> memory block")
def E(c, cat, imp=0.5):
    return MemoryEntry(id=c, content=c, category=cat, importance=imp)
entries = [E("likes tabs", "preference", 0.9),
           E("project X", "project", 0.8),
           E("lesson Y", "lesson", 0.7),
           E("fact Z", "fact", 0.6)]
mem, usr = context.build_blocks(entries, 1400, 900)
check("preference in user block", "likes tabs" in usr and "project X" not in usr)
check("non-preference in memory block",
      "project X" in mem and "lesson Y" in mem and "fact Z" in mem)
check("preference excluded from memory", "likes tabs" not in mem)

print("\n7. budget: blocks never exceed their char budget")
big = [E("x" * 200 + str(i), "fact", 0.9 - i * 0.001) for i in range(50)]
mem, usr = context.build_blocks(big, 500, 300)
check("memory within 500", len(mem) <= 500, len(mem))
check("user within 300", len(usr) <= 300, len(usr))

print("\n8. dedup: identical content keeps the highest importance")
dup = [E("same thing", "fact", 0.3), E("same thing", "fact", 0.9)]
col = context.collect("/nonexistent", None, extra=dup)
check("one copy", len(col) == 1, len(col))
check("keeps 0.9", abs(col[0].importance - 0.9) < 1e-9, col[0].importance)

print("\n9. inbox: remember -> pending -> consumed cycle")
root = tempfile.mkdtemp(prefix="dream-inbox-")
try:
    ds = DreamStore(root)
    r = inbox.remember(root, "remembered fact", category="preference",
                       importance=0.9, agent="opencode")
    check("record has id", r["id"].startswith("rem-"), r["id"])
    pend = inbox.pending(root, ds)
    check("pending before dream", len(pend) == 1, len(pend))

    pe = inbox.pending_entries(root, ds)
    check("as MemoryEntry", len(pe) == 1 and pe[0].category == "preference")

    # simulate a dream consuming it
    art = DreamArtifact(id="dream-0001", timestamp=now_iso(),
                        entries=pe, stats={"inbox_ids": [r["id"]]})
    ds.save(art)
    check("consumed after dream", len(inbox.pending(root, ds)) == 0,
          inbox.pending(root, ds))
    check("never deleted from file", len(inbox.load_inbox(root)) == 1)

    # invalid category falls back to fact, values clamped
    r2 = inbox.remember(root, "another", category="bogus",
                        importance=9, confidence=-3)
    check("bad category -> fact", r2["category"] == "fact", r2["category"])
    check("importance clamped", r2["importance"] == 1.0, r2["importance"])
    check("confidence clamped", r2["confidence"] == 0.0, r2["confidence"])

    # malformed line is skipped, not fatal
    with open(inbox.inbox_path(root), "a") as f:
        f.write("{not json\n")
    check("malformed line skipped", len(inbox.load_inbox(root)) == 2)
finally:
    shutil.rmtree(root, ignore_errors=True)

print("\n10. write_digests: targets isolated by DREAM_DIGEST_HOME")
root = tempfile.mkdtemp(prefix="dream-ctx-")
home = tempfile.mkdtemp(prefix="dream-home-")
old_home = os.environ.get("DREAM_DIGEST_HOME")
os.environ["DREAM_DIGEST_HOME"] = home
try:
    art = DreamArtifact(id="dream-0001", timestamp=now_iso(),
                        entries=[E("likes tabs", "preference", 0.9),
                                 E("project X", "project", 0.8)])
    rep = context.write_digests(root, art)
    check("all 5 targets written", len(rep) == 5, len(rep))
    claude = os.path.join(home, ".claude/CLAUDE.md")
    check("claude file created", os.path.exists(claude))
    txt = open(claude, encoding="utf-8").read()
    check("both categories present",
          "likes tabs" in txt and "project X" in txt, txt)
    check("single header", txt.count("Auto-generated by") == 1, txt)

    user = open(os.path.join(home, ".hermes/memories/USER.md"),
                encoding="utf-8").read()
    memf = open(os.path.join(home, ".hermes/memories/MEMORY.md"),
                encoding="utf-8").read()
    check("user file has preference only",
          "likes tabs" in user and "project X" not in user, user)
    check("memory file has project only",
          "project X" in memf and "likes tabs" not in memf, memf)

    # second run: unchanged, and no backup yet (first write had no old content)
    rep2 = context.write_digests(root, art)
    check("second run unchanged", not any(r["changed"] for r in rep2),
          [r for r in rep2 if r["changed"]])
    check("no backup for empty origin", not os.path.exists(claude + ".dream-bak"))

    # third run: content changes -> rolling backup of the previous content
    art2 = DreamArtifact(id="dream-0002", timestamp=now_iso(),
                         entries=[E("likes spaces", "preference", 0.9),
                                  E("project Y", "project", 0.8)])
    rep3 = context.write_digests(root, art2)
    check("third run changed", any(r["changed"] for r in rep3))
    check("backup created on change", os.path.exists(claude + ".dream-bak"))
    bak = open(claude + ".dream-bak", encoding="utf-8").read()
    check("backup holds previous content", "project X" in bak, bak[:120])
finally:
    if old_home is None:
        os.environ.pop("DREAM_DIGEST_HOME", None)
    else:
        os.environ["DREAM_DIGEST_HOME"] = old_home
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)

print("\n11. root guard: non-default root never touches real agent files")
# Guards the real bug: `dream.py --root <scratch>` regenerated digests into
# the live ~/.claude/CLAUDE.md because only DREAM_ROOT env was checked.
root = tempfile.mkdtemp(prefix="dream-scratch-")
old_home = os.environ.pop("DREAM_DIGEST_HOME", None)
old_root = os.environ.pop("DREAM_ROOT", None)
try:
    check("targets empty for scratch root",
          context.default_targets(root) == [], context.default_targets(root))
    art = DreamArtifact(id="dream-0001", timestamp=now_iso(),
                        entries=[E("scratch fact", "fact", 0.9)])
    rep = context.write_digests(root, art)
    check("only canonical written", len(rep) == 1 and
          rep[0]["key"] == "canonical", rep)
    check("canonical exists", os.path.exists(context.canonical_path(root)))
    # explicit opt-in still works
    os.environ["DREAM_DIGEST_FORCE"] = "1"
    check("force restores targets", len(context.default_targets(root)) == 4)
    # DREAM_DIGEST_HOME relocates (test isolation path)
    os.environ.pop("DREAM_DIGEST_FORCE", None)
    home = tempfile.mkdtemp(prefix="dream-home2-")
    os.environ["DREAM_DIGEST_HOME"] = home
    check("digest home relocates targets",
          len(context.default_targets(root)) == 4)
    shutil.rmtree(home, ignore_errors=True)
finally:
    if old_home is None:
        os.environ.pop("DREAM_DIGEST_HOME", None)
    else:
        os.environ["DREAM_DIGEST_HOME"] = old_home
    if old_root is None:
        os.environ.pop("DREAM_ROOT", None)
    else:
        os.environ["DREAM_ROOT"] = old_root
    os.environ.pop("DREAM_DIGEST_FORCE", None)
    shutil.rmtree(root, ignore_errors=True)

print("\n12. degenerate LLM output is rejected (real bug: content '...')")
from dream_memory.dreaming import _degenerate, _extract_json  # noqa: E402
check("ellipsis rejected", _degenerate("..."))
check("short rejected", _degenerate("foo"))
check("punctuation rejected", _degenerate("---- !!! ???"))
check("near-empty rejected", _degenerate("a b c d e f g"))
check("repeated chars rejected", _degenerate("aaaa aaaa aaaa aaaa"))
check("placeholder word rejected", _degenerate("placeholder"))
check("real memory accepted",
      not _degenerate("User prefers pytest over unittest for Python tests"))
check("short real accepted",
      not _degenerate("bs2 uses Vite"))
# a degenerate-only payload must parse but be filtered by the dream loop
d = _extract_json('{"entries": [{"content": "...", "category": "fact",'
                  ' "importance": 0.8, "confidence": 0.95}]}')
check("payload parses", len(d["entries"]) == 1)
check("its only entry is degenerate",
      _degenerate(str(d["entries"][0]["content"])))

print("\n13. _parse_entries filters junk and keeps good memories")
from dream_memory.dreaming import _parse_entries, LLM_ATTEMPTS  # noqa: E402
from dream_memory.schema import Session  # noqa: E402
fake_batch = [Session(id="s", tool="opencode", title="t", messages=[])]
good = {"content": "User prefers pytest over unittest for Python tests",
        "category": "preference", "importance": 0.9, "confidence": 0.8}
junk = {"content": "...", "category": "fact", "importance": 0.9}
badcat = {"content": "The bs2 app uses Vite and Tailwind",
          "category": "banana", "importance": 0.5}

got = _parse_entries({"entries": [good, junk, badcat, "not-a-dict", None]},
                     fake_batch)
check("2 usable of 5", len(got) == 2, len(got))
check("good kept", any("pytest" in e.content for e in got))
check("unknown category -> fact",
      any(e.category == "fact" and "Vite" in e.content for e in got))
check("sources from batch",
      all(e.sources == [fake_batch[0].content_hash] for e in got))
check("empty on all-junk", _parse_entries({"entries": [junk]}, fake_batch) == [])
check("empty on missing key", _parse_entries({}, fake_batch) == [])
check("empty on non-list", _parse_entries({"entries": {"a": 1}}, fake_batch) == [])
check("retry budget > 1", LLM_ATTEMPTS >= 2, LLM_ATTEMPTS)

print("\n14. connect: 'already exists' is success, not failure")
from dream_memory.connect import _already, server_command  # noqa: E402
check("claude dup detected",
      _already("MCP server dream already exists in user config"))
check("opencode dup detected", _already("Server 'dream' already registered"))
check("generic fail still fails", not _already("command not found"))
check("empty still fails", not _already(""))
check("codex auth failure still fails",
      not _already("Your access token could not be refreshed because your "
                   "refresh token was revoked"))
cmd = server_command()
check("server command absolute", os.path.isabs(cmd[0]), cmd)
check("server command ends with mcp", cmd[-1] == "mcp", cmd)
check("server command has dream.py", any(a.endswith("dream.py") for a in cmd), cmd)

print("\n" + "=" * 60)
if FAILS:
    print("FAILURES (%d): %s" % (len(FAILS), FAILS))
    sys.exit(1)
print("ALL CONTEXT/INBOX TESTS PASSED")
sys.exit(0)
