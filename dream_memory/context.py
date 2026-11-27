"""
Always-on context digest: a bounded, generated summary of crystallized
memory written into each agent's native memory file.

Why this exists
---------------
The MCP tool costs nothing when idle, but the facts an agent needs on *every*
turn (user preferences, project shape, hard-won lessons) must already be in
the prompt -- waiting for the model to decide to call a tool means paying the
re-derivation cost instead. This module writes those facts as a small bounded
block into the file each agent already loads into its system prompt.

Managed blocks
--------------
Everything we write is wrapped in markers and nothing else in the file is
touched, so a user's own notes survive regeneration:

  markdown targets   <!-- BEGIN dream-memory:managed --> ... <!-- END ... -->
  hermes targets     [dream-memory:managed] ... [/dream-memory:managed]

Hermes splits MEMORY.md / USER.md on the literal ``"\\n\\u00a7\\n"`` and
dedupes the resulting entries, so a Hermes block is written as exactly ONE
entry using that delimiter, and must never itself contain the delimiter.
Hermes also threat-scans entries at snapshot time; the digest is plain
prose so it passes, but content should stay free of URLs/tokens/commands.

Category split (Hermes)
-----------------------
Hermes keeps two files with different jobs: USER.md is "who the user is",
MEMORY.md is "agent notes". Preferences map to the former, everything else
to the latter. Single-file targets receive the whole digest.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from .schema import DreamArtifact, MemoryEntry

# --- markers ---------------------------------------------------------------
MD_BEGIN = "<!-- BEGIN dream-memory:managed -->"
MD_END = "<!-- END dream-memory:managed -->"
Hermes_BEGIN = "[dream-memory:managed]"
Hermes_END = "[/dream-memory:managed]"

# Hermes' own entry delimiter (ENTRY_DELIMITER in hermes' memory_tool.py).
HERMES_DELIM = "\n\u00a7\n"

# --- budgets (characters) --------------------------------------------------
DEFAULT_MEMORY_CHARS = 1400   # hermes MEMORY.md (file limit 2200)
DEFAULT_USER_CHARS = 900      # hermes USER.md    (file limit 1375)
DEFAULT_SINGLE_CHARS = 2000   # claude / codex / canonical digest

# --- targets ---------------------------------------------------------------
# (key, path, kind)  kind: "hermes_memory" | "hermes_user" | "markdown"
def default_targets(root: Optional[str] = None) -> List[Tuple[str, str, str]]:
    """Where to write digests.

    SAFETY (guards a real data-loss bug): agent files are only written when
    `root` is the default memory root -- i.e. when the digest actually
    represents the memory those agents are fed from. A scratch root (tests,
    `dream.py --root ...`, experiments) must never clobber the live agent
    files. Override with DREAM_DIGEST_HOME (relocates) or DREAM_DIGEST_FORCE=1
    (explicit opt-in).
    """
    # DREAM_DIGEST_HOME relocates every agent file under one root. Tests use
    # it so a scratch memory root can never clobber the real agent configs.
    home = os.environ.get("DREAM_DIGEST_HOME") or os.path.expanduser("~")

    if os.environ.get("DREAM_DIGEST_HOME") is None and \
            os.environ.get("DREAM_DIGEST_FORCE") != "1":
        default_root = os.path.expanduser("~/.dream-memory")
        effective = root or os.environ.get("DREAM_ROOT") or default_root
        if os.path.abspath(effective) != os.path.abspath(default_root):
            return []

    return [
        ("hermes_memory", os.path.join(home, ".hermes/memories/MEMORY.md"),
         "hermes_memory"),
        ("hermes_user", os.path.join(home, ".hermes/memories/USER.md"),
         "hermes_user"),
        ("claude", os.path.join(home, ".claude/CLAUDE.md"), "markdown"),
        ("codex", os.path.join(home, ".codex/AGENTS.md"), "markdown"),
    ]


def canonical_path(root: str) -> str:
    return os.path.join(root, "context", "digest.md")


def _sort_key(e: MemoryEntry) -> Tuple[float, float, str]:
    # importance desc, then confidence desc, then stable by content
    return (-e.importance, -e.confidence, e.content)


def collect(root: str, dream: Optional[DreamArtifact],
            extra: Optional[List[MemoryEntry]] = None) -> List[MemoryEntry]:
    """
    The digest's source: latest dream's entries plus any pending
    inbox memories (so `dream_remember` takes effect immediately).
    """
    entries: List[MemoryEntry] = list(dream.entries) if dream else []
    entries.extend(extra or [])

    # Dedup by normalised content, keeping the highest-importance copy.
    seen: Dict[str, MemoryEntry] = {}
    for e in entries:
        key = " ".join(e.content.lower().split())
        if not key:
            continue
        if key not in seen or e.importance > seen[key].importance:
            seen[key] = e
    return sorted(seen.values(), key=_sort_key)


def _fmt(e: MemoryEntry) -> str:
    return "- (%s, imp=%.1f) %s" % (e.category, e.importance,
                                    e.content.strip())


def build_blocks(entries: List[MemoryEntry],
                 memory_chars: int, user_chars: int
                 ) -> Tuple[str, str]:
    """Return (memory_body, user_body), each within budget and WITHOUT a
    header -- callers add it, so single-file targets only pay for one."""
    memory_lines: List[str] = []
    user_lines: List[str] = []
    mem_used = usr_used = 0

    for e in entries:
        line = _fmt(e)
        if e.category == "preference":
            if usr_used + len(line) + 1 > user_chars:
                continue
            user_lines.append(line)
            usr_used += len(line) + 1
        else:
            if mem_used + len(line) + 1 > memory_chars:
                continue
            memory_lines.append(line)
            mem_used += len(line) + 1

    return "\n".join(memory_lines), "\n".join(user_lines)


HEADER = ("Auto-generated by `dream.py context` from crystallized memory. "
          "Do not edit -- it is rewritten on every regeneration.")


# --- managed block upsert --------------------------------------------------
def upsert_block(text: str, body: str, begin: str, end: str) -> str:
    """Replace our block in `text`, or append it. Never touches other content.

    Fail-safe: an empty `body` never deletes anything -- if we cannot locate a
    complete block, the original text is returned untouched rather than risk
    dropping foreign content.
    """
    block = "%s\n%s\n%s" % (begin, body, end) if body else ""
    if not block:
        return text
    start = text.find(begin)
    stop = text.find(end)

    if start != -1 and stop != -1 and stop > start:
        stop += len(end)
        merged = text[:start] + block + text[stop:]
    elif block:
        sep = "" if not text or text.endswith("\n") else "\n"
        merged = text + sep + block + "\n"
    else:
        merged = text

    # collapse the blank-line pile-up that repeated regeneration can cause
    while "\n\n\n" in merged:
        merged = merged.replace("\n\n\n", "\n\n")
    return merged.rstrip("\n") + "\n"


def upsert_hermes(text: str, body: str) -> str:
    """Hermes variant: our block is exactly ONE entry in Hermes' own format.

    Hermes parses MEMORY.md / USER.md by splitting on ``HERMES_DELIM`` and
    re-joins with the same delimiter whenever its `memory` tool writes. So the
    block must be a real entry, not just marker-delimited text -- otherwise a
    later Hermes write merges our digest into one of its entries permanently.

    DANGER (real bug this guards): if a previous generation embedded our
    markers inside a larger entry, dropping that entry wholesale would delete
    the user's own memory along with it. We therefore strip only the text
    between our markers and keep everything else in the entry.
    """
    block = ("%s\n%s\n%s" % (Hermes_BEGIN, body, Hermes_END)) if body else ""

    entries = [e.strip() for e in text.split(HERMES_DELIM) if e.strip()]
    kept: List[str] = []
    for e in entries:
        if Hermes_BEGIN not in e:
            kept.append(e)
            continue
        # Split out our markers; preserve any foreign text around them.
        before = e.split(Hermes_BEGIN, 1)[0]
        after = e.split(Hermes_END, 1)[1] if Hermes_END in e else ""
        remainder = ("%s\n%s" % (before.strip(), after.strip())).strip()
        if remainder:
            kept.append(remainder)

    if block:
        kept.append(block)

    # FAIL-SAFE: never return empty for non-empty input. If our parse found
    # nothing worth keeping we would otherwise truncate the user's file.
    if not kept:
        return text
    return HERMES_DELIM.join(kept) + "\n"


def _read(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def write_digests(root: str, dream: Optional[DreamArtifact],
                  extra: Optional[List[MemoryEntry]] = None,
                  memory_chars: Optional[int] = None,
                  user_chars: Optional[int] = None,
                  single_chars: Optional[int] = None,
                  targets: Optional[List[Tuple[str, str, str]]] = None
                  ) -> List[Dict[str, Any]]:
    """
    Regenerate every digest target. Returns a report:
    [{"key", "path", "changed", "chars"}, ...]
    """
    import os as _os
    memory_chars = memory_chars or int(_os.environ.get(
        "DREAM_DIGEST_MEMORY_CHARS", DEFAULT_MEMORY_CHARS))
    user_chars = user_chars or int(_os.environ.get(
        "DREAM_DIGEST_USER_CHARS", DEFAULT_USER_CHARS))
    single_chars = single_chars or int(_os.environ.get(
        "DREAM_DIGEST_CHARS", DEFAULT_SINGLE_CHARS))

    entries = collect(root, dream, extra)
    mem_lines, usr_lines = build_blocks(entries, memory_chars, user_chars)
    mem_body = (HEADER + "\n" + mem_lines) if mem_lines else ""
    usr_body = (HEADER + "\n" + usr_lines) if usr_lines else ""

    # Canonical artifact: both blocks, always written.
    canon = canonical_path(root)
    os.makedirs(os.path.dirname(canon), exist_ok=True)
    canon_text = ("%s\n%s\n%s\n\n%s\n%s\n%s\n" % (
        MD_BEGIN, mem_body, MD_END, MD_BEGIN, usr_body, MD_END)) if entries else ""
    old_canon = _read(canon)
    if old_canon != canon_text:
        with open(canon, "w", encoding="utf-8") as f:
            f.write(canon_text)

    report: List[Dict[str, Any]] = [{
        "key": "canonical", "path": canon,
        "changed": old_canon != canon_text, "chars": len(canon_text),
    }]

    for key, path, kind in (targets if targets is not None
                            else default_targets(root)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old = _read(path)

        if kind == "hermes_memory":
            body = mem_body
            new = upsert_hermes(old, body)
        elif kind == "hermes_user":
            body = usr_body
            new = upsert_hermes(old, body)
        else:
            # single-file targets get the combined digest under ONE header
            lines = [l for l in (mem_lines, usr_lines) if l]
            body = HEADER + "\n" + "\n\n".join(lines) if lines else ""
            if len(body) > single_chars:
                body = body[:single_chars].rsplit("\n", 1)[0]
            new = upsert_block(old, body, MD_BEGIN, MD_END)

        changed = new != old
        # Absolute guard: never truncate a file that had content.
        if changed and not new and old:
            report.append({"key": key, "path": path, "changed": False,
                           "chars": len(old), "skipped_empty": True})
            continue
        if changed:
            _backup(path, old)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
        report.append({"key": key, "path": path, "changed": changed,
                       "chars": len(body)})

    return report


def _backup(path: str, old: str) -> None:
    """One rolling safety copy of the pre-change content.

    Guarded a real data-loss bug: a malformed managed block once swallowed a
    user's own memory entry. One generation of recovery is enough, and it
    never multiplies files.
    """
    if not old:
        return
    try:
        with open(path + ".dream-bak", "w", encoding="utf-8") as f:
            f.write(old)
    except OSError:
        pass
