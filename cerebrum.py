#!/usr/bin/env python3
"""
cerebrum.py — the Builder's tool for a self-organizing note vault.

Dependency-free (stdlib only). Read-and-verify core. The only mutating commands are
`move` and `undo`: `move` is a dry run unless given --apply and --i-ratified, writes every
operation to an undo log BEFORE performing it, and verifies after.

Subcommands:
  snapshot   full .tar.gz backup of the vault, outside the vault; verifies file counts
  manifest   path/size/sha256 for every file  -> state/manifest-<ts>.tsv
  inventory  links, name collisions, frozen-register membership -> state/inventory-<ts>.md
  verify     compare a before-manifest to the live vault: file count, hash multiset,
             frozen paths unmoved, links still resolve. GREEN/RED. With --expect, the
             changes a ratified plan predicted are the only ones allowed.
  selftest   prove the verifier can go RED: plant defects on a scratch copy, expect RED
  move       apply a ratified plan (mkdir / mv / append / trash / rmdir); dry run by default
  undo       reverse an applied plan from its undo log

--frozen-live (verify, move): the frozen folders are live substrate, written by their own
programs (an agent's memory, a sync job, an automation) while we work. Their changes are listed as NOTEs
with timestamps instead of failing the check; everything outside them stays strict.

The frozen register is vault paths other programs depend on; they MUST NOT be moved (moving
one breaks every program that reads it). Membership is data, from cerebrum.json.
Set VAULT=<dir> to point any command at a scratch copy instead of the live vault.
"""
import argparse, hashlib, json, os, re, subprocess, sys, tarfile, tempfile, shutil
from collections import defaultdict, Counter
from datetime import datetime
from urllib.parse import unquote

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("CEREBRUM_CONFIG", os.path.join(HERE, "cerebrum.json"))

def load_config(path):
    """The one source of truth for a vault: its path, PARA homes, frozen register (each root
    with the code that pins it), accepted exceptions, and pointers into it from outside."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

CFG = load_config(CONFIG_PATH)
REAL_VAULT = os.path.expanduser(CFG.get("vault", ""))
VAULT = os.environ.get("VAULT", REAL_VAULT)
SNAPS = os.environ.get("CEREBRUM_SNAPSHOTS", os.path.join(HERE, "snapshots"))
STATE = os.environ.get("CEREBRUM_STATE", os.path.join(HERE, "state"))   # a rehearsal keeps its state apart
TRASH = os.path.join(os.environ.get("XDG_DATA_HOME", f"{HOME}/.local/share"), "Trash")

# Frozen register — relative to the vault root. A path is frozen if it equals a root or
# lives under it. Obsidian's own folders are always frozen; the rest come from the config,
# the single source for the verifier, the mover, the checks and the registry.
FROZEN = list(dict.fromkeys([f["root"] for f in CFG.get("frozen", [])] + [".obsidian", ".trash"]))

# The PARA homes a movable file may land in (the law, §IV).
PARA = tuple(CFG.get("para", ["# INBOX", "1 PROJECTS", "2 AREAS", "3 RESOURCES", "4 ARCHIVE"]))

def ts():
    return datetime.now().strftime("%Y%m%d-%H%M%S")

def rels(vault):
    for root, _dirs, files in os.walk(vault):
        for f in files:
            yield os.path.relpath(os.path.join(root, f), vault)

def is_frozen(rel):
    return any(rel == p or rel.startswith(p + os.sep) for p in FROZEN)

def _is_real(vault):
    return bool(REAL_VAULT) and os.path.realpath(vault) == os.path.realpath(REAL_VAULT)

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()

def _hash_all(vault):
    """path -> sha256 for every file now. A file that vanishes between listing and hashing
    (a live organ replacing it) is skipped — it is simply not there any more."""
    out = {}
    for r in rels(vault):
        try:
            out[r] = sha256(os.path.join(vault, r))
        except FileNotFoundError:
            pass
    return out

def ensure_dirs():
    os.makedirs(SNAPS, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)

def _obsidian_running():
    """Obsidian rewrites .obsidian/workspace.json while open — a spurious diff source.
    -x (exact), never -f (which would self-match this script's path)."""
    try:
        return subprocess.run(["pgrep", "-x", "obsidian"],
                              capture_output=True).returncode == 0
    except Exception:
        return False

# ---- snapshot ----------------------------------------------------------------
def take_snapshot(vault):
    """Archive file by file, so a file a live organ removes mid-archive is noted rather than
    aborting the backup. OK only if every MOVABLE file listed is in the archive.
    Returns (ok, archive path, summary lines)."""
    ensure_dirs()
    out = f"{SNAPS}/vault-{ts()}.tar.gz"
    listed, vanished = [], []
    with tarfile.open(out, "w:gz") as tar:
        for root, _dirs, files in os.walk(vault):
            rel_root = os.path.relpath(root, vault)
            tar.add(root, arcname="vault" if rel_root == "." else f"vault/{rel_root}",
                    recursive=False)
            for f in files:
                rel = os.path.normpath(os.path.join(rel_root, f))
                listed.append(rel)
                try:
                    tar.add(os.path.join(root, f), arcname=f"vault/{rel}", recursive=False)
                except FileNotFoundError:
                    vanished.append(rel)
    with tarfile.open(out, "r:gz") as tar:
        names = {m.name[len("vault/"):] for m in tar.getmembers() if m.isfile()}
    movable = [r for r in listed if not is_frozen(r)]
    missing_movable = [r for r in movable if r not in names]
    ok = not missing_movable and len(names) == len(listed) - len(vanished)
    lines = [f"files listed={len(listed)}  in archive={len(names)}  "
             f"movable {len(movable) - len(missing_movable)}/{len(movable)}  "
             f"{'OK' if ok else 'MISMATCH — DO NOT PROCEED'}"]
    if vanished:
        lines.append(f"{len(vanished)} file(s) vanished while archiving"
                     + (" — all inside frozen (live) folders" if not missing_movable
                        else " — including MOVABLE: " + "; ".join(missing_movable[:5])))
    return ok, out, lines

def cmd_snapshot(_a):
    ok, out, lines = take_snapshot(VAULT)
    print(f"snapshot : {out}")
    for line in lines:
        print("           " + line)
    return 0 if ok else 1

# ---- manifest ----------------------------------------------------------------
def manifest_rows(vault):
    """(sha256, size, path) for every file now, plus a DIR marker for every frozen root that
    exists — so verify can catch the deletion of an EMPTY frozen dir (one
    with 0 files), which a file-list alone never sees. A file a live organ removes mid-scan is skipped."""
    rows = []
    for rel, h in _hash_all(vault).items():
        try:
            rows.append((h, os.path.getsize(os.path.join(vault, rel)), rel))
        except FileNotFoundError:
            pass
    rows.sort(key=lambda r: r[2])
    return rows + [("DIR", 0, root) for root in FROZEN if os.path.isdir(os.path.join(vault, root))]

def cmd_manifest(_a):
    ensure_dirs()
    all_rows = manifest_rows(VAULT)
    rows = [r for r in all_rows if r[0] != "DIR"]
    out = f"{STATE}/manifest-{ts()}.tsv"
    with open(out, "w", encoding="utf-8") as fh:
        for h, sz, rel in all_rows:
            fh.write(f"{h}\t{sz}\t{rel}\n")
    movable = [r for r in rows if not is_frozen(r[2])]
    print(f"manifest : {out}")
    print(f"           total={len(rows)}  frozen={len(rows)-len(movable)}  "
          f"movable={len(movable)}  frozen-dirs-marked={len(all_rows) - len(rows)}")
    return 0

# ---- inventory ---------------------------------------------------------------
# Wikilinks/embeds are Obsidian's default and the vault's only real link type
# (inventory found 9 wikilinks, 1 md-link). Scope: we check wikilinks/embeds, not
# markdown-path links — deliberately, so we don't ship an untested resolution path.
WIKI = re.compile(r"!?\[\[([^\]]+)\]\]")

def _strip_code(text):
    """Remove fenced blocks and inline-code spans — Obsidian does not resolve
    [[links]] inside them, so neither do we (a note ABOUT links is not full of links)."""
    # A fence closes only on a line of the same character at least as long; an unclosed
    # fence runs to the end of the note, as in Obsidian.
    text = re.sub(r"(?ms)^[ ]{0,3}(`{3,}|~{3,})[^\n]*\n.*?^[ ]{0,3}\1[`~]*[ \t]*$", "", text)
    text = re.sub(r"(?ms)^[ ]{0,3}(?:`{3,}|~{3,}).*\Z", "", text)
    text = re.sub(r"`[^`\n]*`", "", text)
    return text

def _index(vault):
    by_base = defaultdict(list)   # lower basename (no .md) -> [relpath]
    by_rel = set()
    for rel in rels(vault):
        if rel.endswith(".md"):
            by_rel.add(rel[:-3])
            by_base[os.path.basename(rel)[:-3].lower()].append(rel)
    return by_base, by_rel

def cmd_inventory(_a):
    ensure_dirs()
    by_base, by_rel = _index(VAULT)

    collisions = {b: ps for b, ps in by_base.items() if len(ps) > 1}
    coll_lines = []
    for b, ps in sorted(collisions.items()):
        hashes = {sha256(os.path.join(VAULT, p)) for p in ps if os.path.exists(os.path.join(VAULT, p))}
        tag = "IDENTICAL" if len(hashes) == 1 else "DIFFERENT — link collision risk"
        coll_lines.append(f"- **{b}** ({len(ps)}×, {tag})")
        for p in ps:
            coll_lines.append(f"    - `{p}`")

    unresolved, ambiguous, total_links = [], [], 0
    for rel in rels(VAULT):
        if not rel.endswith(".md") or is_frozen(rel):
            continue
        try:
            text = open(os.path.join(VAULT, rel), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in WIKI.finditer(_strip_code(text)):
            total_links += 1
            target = m.group(1).split("|")[0].split("#")[0].strip()
            if not target:
                continue
            if "/" in target:
                if target not in by_rel:
                    unresolved.append((rel, m.group(0)))
                continue
            hits = by_base.get(target.lower(), [])
            if len(hits) == 0:
                unresolved.append((rel, m.group(0)))
            elif len(hits) > 1:
                ambiguous.append((rel, m.group(0), hits))

    out = f"{STATE}/inventory-{ts()}.md"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# Vault inventory — {ts()}\n\n")
        fh.write(f"Vault: `{VAULT}`\n\n")
        fh.write(f"- wikilinks/embeds scanned (outside frozen): **{total_links}**\n")
        fh.write(f"- unresolved: **{len(unresolved)}**\n")
        fh.write(f"- ambiguous (basename matches >1 file): **{len(ambiguous)}**\n")
        fh.write(f"- name collisions among all .md: **{len(collisions)}**\n\n")
        fh.write("## Name collisions\n" + ("\n".join(coll_lines) or "_none_") + "\n\n")
        fh.write("## Unresolved links\n")
        fh.write("\n".join(f"- `{r}` → `{lk}`" for r, lk in unresolved) or "_none_")
        fh.write("\n\n## Ambiguous links\n")
        fh.write("\n".join(f"- `{r}` → `{lk}` could be: {hits}" for r, lk, hits in ambiguous) or "_none_")
        fh.write("\n")
    print(f"inventory: {out}")
    print(f"           links={total_links}  unresolved={len(unresolved)}  "
          f"ambiguous={len(ambiguous)}  collisions={len(collisions)}")
    return 0

# ---- verify ------------------------------------------------------------------
def _read_manifest(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            h, sz, rel = line.rstrip("\n").split("\t", 2)
            rows.append((h, int(sz), rel))
    return rows

def _mtime(vault, p):
    full = os.path.join(vault, p)
    if os.path.lexists(full):
        return f"  (mtime {datetime.fromtimestamp(os.path.getmtime(full)):%Y-%m-%d %H:%M:%S})"
    return ""

def _verify(vault, before_rows, expected_moves=None, expected_removed=None, expected_hashes=None,
            frozen_live=False, expected_added=None):
    """Compare a before-manifest to the live vault, naming exactly what changed BY PATH.
    With no expectations this is a pure integrity check: the vault must equal the manifest.
    A ratified plan supplies expectations DERIVED from the plan (never read off the result):
      expected_moves    {src: dst}, or a set of (src, dst) pairs
      expected_removed  paths trashed on purpose — each must leave an identical copy behind
      expected_hashes   {final path: sha256} for content the plan changes (a merge)
    Every change outside the expectations is RED, and so is every expectation that didn't happen.
    frozen_live: changes inside the frozen folders are made by their own organs while we work;
      they are listed as NOTE lines with timestamps (the law's §VII.0: "reports it with its
      timestamp instead of guessing") and never make the verdict RED. Frozen ROOTS must still
      exist, and everything outside the frozen folders is held to the full standard.
    Returns (ok, findings): ok is False iff some finding is RED; NOTE lines follow the REDs."""
    findings, notes = [], []
    exp_moves = dict(expected_moves or {})
    exp_removed = set(expected_removed or ())
    exp_hashes = dict(expected_hashes or {})
    exp_added = dict(expected_added or {})                           # {new path: sha256} (a merge)
    before_dirs = [r[2] for r in before_rows if r[0] == "DIR"]         # frozen roots recorded as dirs
    before_all = {r[2]: r[0] for r in before_rows if r[0] != "DIR"}    # path -> hash (manifest)
    after_all = _hash_all(vault)                                       # path -> hash (now)
    if frozen_live:
        fb = {p: h for p, h in before_all.items() if is_frozen(p)}
        fa = {p: h for p, h in after_all.items() if is_frozen(p)}
        for p in sorted(set(fb) - set(fa)):
            notes.append(f"NOTE frozen (live) removed: {p}")
        for p in sorted(set(fa) - set(fb)):
            notes.append(f"NOTE frozen (live) added: {p}{_mtime(vault, p)}")
        for p in sorted(set(fb) & set(fa)):
            if fb[p] != fa[p]:
                notes.append(f"NOTE frozen (live) changed: {p}{_mtime(vault, p)}")
        before = {p: h for p, h in before_all.items() if not is_frozen(p)}
        after = {p: h for p, h in after_all.items() if not is_frozen(p)}
    else:
        before, after = before_all, after_all
    b, a = set(before), set(after)
    removed, added = b - a, a - b
    after_hashes = set(after_all.values())   # a trashed file's twin may live anywhere
    touched = set()                          # every path involved in a change, for the frozen check

    # (1) ratified moves, matched exactly by (src, dst) BEFORE any hash pairing — so two
    # identical files moved to different places can never be cross-paired.
    # A path a planned move vacates and a planned new note fills again (a split note's own lines,
    # recomposed where the note was): the old bytes are held to the move, the path to the new note.
    reused = {s for s in exp_moves if s in exp_added}
    for s, d in sorted(exp_moves.items()):
        if (s in removed or (s in reused and s in a)) and d in added:
            if after[d] != exp_hashes.get(d, before[s]):
                findings.append(f"RED moved but content differs from the plan: {s} -> {d}")
            removed.discard(s); added.discard(d); touched |= {s, d}
        else:
            findings.append(f"RED planned move did not happen: {s} -> {d}")

    # (2) in-place changes: only the plan's content changes, and exactly those bytes
    for p in sorted((b & a) - reused):              # a reused path is checked as a planned new note, below
        if before[p] != after[p]:
            touched.add(p)
            if exp_hashes.get(p) != after[p]:
                findings.append(f"RED changed-in-place: {p}{_mtime(vault, p)}")
        elif p in exp_hashes and exp_hashes[p] != after[p]:
            findings.append(f"RED planned change did not happen: {p}")

    # (3) planned removals — lossless only if an identical copy is still in the vault
    for p in sorted(exp_removed):
        if p in removed:
            removed.discard(p); touched.add(p)
            if before[p] not in after_hashes:
                findings.append(f"RED trashed with no identical copy left: {p}")
        else:
            findings.append(f"RED planned removal did not happen: {p}")

    # (3b) planned new files (a merge): present, holding exactly the planned bytes
    for p, h in sorted(exp_added.items()):
        if p in added or (p in reused and p in a):
            added.discard(p); touched.add(p)
            if after[p] != h:
                findings.append(f"RED created but content differs from the plan: {p}")
        else:
            findings.append(f"RED planned new file did not appear: {p}")

    # (4) anything else is unplanned: equal-content removed→added pairs are moves;
    # the rest are removals and additions
    added_by_hash = defaultdict(list)
    for p in sorted(added):
        added_by_hash[after[p]].append(p)
    for p in sorted(removed):
        touched.add(p)
        pool = added_by_hash.get(before[p])
        if pool:
            d = pool.pop(0); added.discard(d); touched.add(d)
            findings.append(f"RED moved (unratified): {p} -> {d}")
        else:
            findings.append(f"RED removed: {p}")
    for p in sorted(added):
        touched.add(p)
        findings.append(f"RED added: {p}{_mtime(vault, p)}")

    # (5) frozen: every root recorded as a dir must still be a dir (catches deleting an
    # empty frozen dir, which the file-list alone cannot see), and no frozen path may change.
    # Under frozen_live the partition above keeps frozen paths out of `touched`.
    for root in before_dirs:
        if not os.path.isdir(os.path.join(vault, root)):
            findings.append(f"RED frozen root missing or not a directory: {root}")
    for p in sorted(touched):
        if is_frozen(p):
            findings.append(f"RED frozen touched: {p}{_mtime(vault, p)}")

    # (6) links: RED only for a link that RESOLVED before and does NOT now — the operation
    # broke it. Pre-existing broken links are not this op's fault. Scans ALL notes including
    # frozen (a frozen note may link to a movable note a rename/discard would break), and
    # resolves attachments (embeds like ![[x.png]]) as well as wikilinks. Under frozen_live,
    # a link whose only before-target was a frozen file broke by an organ's write: a NOTE.
    def index(paths):
        bb, allrel, allbase = defaultdict(list), set(), defaultdict(list)
        for r in paths:
            rl = r.lower(); allrel.add(rl); allbase[os.path.basename(rl)].append(r)
            if r.endswith(".md"):
                bb[os.path.basename(r)[:-3].lower()].append(r)
        return bb, allrel, allbase

    def resolves(t, idx):
        bb, allrel, allbase = idx
        tl = t.lower()
        if "/" in t:                                   # a path-style target
            return tl in allrel or (tl + ".md") in allrel
        if "." in os.path.basename(t):                 # an attachment (has an extension)
            return tl in allbase
        return len(bb.get(tl, [])) > 0                 # a wikilink to a note basename

    ib, ia = index(before_all.keys()), index(after_all.keys())
    ib_movable = index(p for p in before_all if not is_frozen(p))
    for rel in after_all:
        if not rel.endswith(".md"):
            continue
        try:
            text = open(os.path.join(vault, rel), encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for m in WIKI.finditer(_strip_code(text)):
            t = m.group(1).split("|")[0].split("#")[0].strip()
            if t and resolves(t, ib) and not resolves(t, ia):
                if frozen_live and not resolves(t, ib_movable):
                    notes.append(f"NOTE link to a frozen (live) file now broken: {rel} -> {m.group(0)}")
                else:
                    findings.append(f"RED link broken by operation: {rel} -> {m.group(0)}")
    return (len(findings) == 0, findings + notes)

def _print_findings(findings, limit=60):
    reds = [f for f in findings if not f.startswith("NOTE")]
    notes = [f for f in findings if f.startswith("NOTE")]
    for f in reds[:limit]:
        print("   " + f)
    if notes:
        print(f"   {len(notes)} NOTE(s) — frozen folders written by their own organs meanwhile:")
        for f in notes[:8]:
            print("     " + f)
        if len(notes) > 8:
            print(f"     … {len(notes) - 8} more")

def cmd_verify(a):
    if not a.before:
        print("verify: need --before <manifest.tsv>"); return 2
    if _is_real(VAULT) and _obsidian_running():
        print("verify   : ⚠ Obsidian is running — it rewrites .obsidian/workspace.json; a "
              "changed-in-place there is expected, not real drift. Close it for a clean run.")
    exp = {}
    if a.expect:
        with open(a.expect, encoding="utf-8") as fh:
            exp = json.load(fh)
    ok, findings = _verify(VAULT, _read_manifest(a.before), exp.get("moves"), exp.get("removed"),
                           exp.get("hashes"), frozen_live=a.frozen_live, expected_added=exp.get("added"))
    scope = " (scope: frozen folders live)" if a.frozen_live else ""
    print("verify   : GREEN — vault matches the manifest" + (" and the plan" if a.expect else "")
          + " (frozen intact, no link broken)" + scope if ok else "verify   : RED" + scope)
    _print_findings(findings)
    return 0 if ok else 1

# ---- selftest ----------------------------------------------------------------
def cmd_selftest(_a):
    """Prove the verifier goes RED on planted defects (an instrument must prove it looked)."""
    tmp = tempfile.mkdtemp(prefix="cerebrum-selftest-")
    try:
        v = os.path.join(tmp, "vault")
        os.makedirs(os.path.join(v, "A"))
        open(os.path.join(v, "keep.md"), "w").write("[[note1]] and [[note2]]\n")
        open(os.path.join(v, "note1.md"), "w").write("one\n")
        open(os.path.join(v, "A", "note2.md"), "w").write("two\n")
        before = [(sha256(os.path.join(v, r)), os.path.getsize(os.path.join(v, r)), r)
                  for r in rels(v)]
        # control: clean copy must be GREEN
        ok, _ = _verify(v, before)
        assert ok, "control should be GREEN"
        results = []
        # defect 1: delete a file
        v1 = os.path.join(tmp, "d1"); shutil.copytree(v, v1)
        os.remove(os.path.join(v1, "note1.md"))
        r1, _ = _verify(v1, before); results.append(("deleted file", not r1))
        # defect 2: corrupt a file (content changes -> hash multiset changes)
        v2 = os.path.join(tmp, "d2"); shutil.copytree(v, v2)
        open(os.path.join(v2, "note1.md"), "w").write("ONE changed\n")
        r2, _ = _verify(v2, before); results.append(("corrupted file", not r2))
        # defect 3: break a link (rename target away)
        v3 = os.path.join(tmp, "d3"); shutil.copytree(v, v3)
        os.rename(os.path.join(v3, "note1.md"), os.path.join(v3, "renamed.md"))
        r3, _ = _verify(v3, before); results.append(("broken link", not r3))
        allred = all(caught for _, caught in results)
        print("selftest : " + ("PASS — verifier goes RED on every planted defect"
                                if allred else "FAIL — verifier MISSED a defect (do not trust it)"))
        for name, caught in results:
            print(f"   {'RED as expected' if caught else 'MISSED!!'}: {name}")
        return 0 if allred else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ---- plan: move / undo -------------------------------------------------------
# A plan is a TSV of operations, applied in order. Lines starting with '#' are comments.
#   mkdir  <dir>                      its parent must already exist
#   mv     <src> <dst>                a file or a folder; never onto an existing path
#   append <dst.md> <src> <heading>   dst := dst + separator + heading + src  (src is kept)
#   trash  <file>                     only if an identical copy survives the whole plan
#   rmdir  <dir>                      only if empty at that point
#   merge  <new.md> <src> [<src> …]   a new note holding each source word for word, under a
#                                     heading naming it; prefix a source with code: to fence it.
#                                     Sources are kept, and may be frozen (read, never written).
#   compose <new.md> <src.md> <spec>  a new note made of those pieces of src, verbatim, in that order
#   extend  <note.md> <src.md> <spec> those pieces appended to an existing note, which stays as it was
#   leave   <src.md> <spec>           pieces deliberately left only in src, placed nowhere else
#   partition <src.md> <sha256>       src is distributed completely: its bytes must still hash to
#                                     sha256, and every line is composed, extended or left exactly once
#   synthesize <new.md> <draft>       a new note holding the draft's bytes (draft: a file outside the
#                                     vault). Every paragraph cites a source part ([S1]) listed under
#                                     '## Sources' with its hash; an uncited paragraph or a changed
#                                     source is refused. The sources are only read.
#   dedupe  <note.md> <archive.md>    later copies of repeated lines dropped from the note; the whole
#                                     original is kept at <archive.md> — nothing is lost
#   <spec>: comma-separated parts (P012), runs of parts (P011-P022) or line ranges (L1542-L1600),
#   from `semantics.py segment`, in the order they are to appear.
ARITY = {"mkdir": 1, "rmdir": 1, "trash": 1, "mv": 2, "append": 3,
         "compose": 3, "extend": 3, "leave": 2, "partition": 2, "synthesize": 2, "dedupe": 2}

def load_plan(path):
    ops = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            op, *args = line.split("\t")
            bad_arity = len(args) < 2 if op == "merge" else (op not in ARITY or len(args) != ARITY[op])
            if bad_arity or not all(args):
                raise ValueError(f"plan line {n}: bad op or arity: {line!r}")
            ops.append((n, op, args))
    return ops

def _sep(heading):
    return f"\n\n---\n\n## {heading}\n\n".encode()

FENCE = "`" * 8     # longer than any fence a source may hold, so a source's own code blocks stay inside

def _merge_bytes(dst, sources, read):
    """The merged note's bytes, and where each source's own bytes sit inside it: {path: (offset, length)}.
    Each source is copied unchanged — byte for byte — so it can always be sliced back out."""
    title = os.path.splitext(os.path.basename(dst))[0]
    parts = [f"# {title}\n\nMerged word for word from {len(sources)} notes. Each part below is its source, "
             f"unchanged, under a heading naming it; the sources themselves are kept.\n".encode()]
    where = {}
    for s in sources:
        code = s.startswith("code:")
        path = s[5:] if code else s
        data = read(path)
        parts.append(f"\n\n---\n\n# {os.path.basename(path)}\n\n*{len(data):,} bytes · sha256 "
                     f"{hashlib.sha256(data).hexdigest()[:16]} · from `{path}`*\n\n".encode())
        if code:
            parts.append((FENCE + "\n").encode())
        where[path] = (sum(map(len, parts)), len(data))
        parts.append(data)
        if code:
            parts.append(("\n" + FENCE + "\n").encode())
    return b"".join(parts), where

def _dirs(vault):
    out = set()
    for root, dirs, _files in os.walk(vault):
        for d in dirs:
            out.add(os.path.relpath(os.path.join(root, d), vault))
    return out

def simulate(vault, ops, before_rows):
    """Run the plan on a model of the tree — nothing on disk changes. Returns
    (errors, expect, report). `expect` is DERIVED from the plan and the before-manifest,
    never read off a result: {"moves": {orig: final}, "removed": [orig], "hashes": {final: sha}}."""
    errors = []
    files = {r[2]: r[0] for r in before_rows if r[0] != "DIR"}   # current path -> hash
    before_hash = dict(files)
    origin = {p: p for p in files}                                 # current path -> original path
    dirs = _dirs(vault)
    buf = {}                                                       # current path -> simulated bytes
    removed, contained = [], {}
    placed, pinned = defaultdict(list), {}                         # src -> line numbers placed; src -> line count

    def exists(p): return p in files or p in dirs
    def parent_ok(p): return os.path.dirname(p) == "" or os.path.dirname(p) in dirs
    def case_clash(p):
        par, low = os.path.dirname(p), os.path.basename(p).lower()
        return any(q != p and os.path.dirname(q) == par and os.path.basename(q).lower() == low
                   for q in list(files) + list(dirs))
    def content(p):
        if p not in buf:
            with open(os.path.join(vault, origin[p]), "rb") as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != before_hash[origin[p]]:
                errors.append(f"changed since the manifest: {origin[p]}")
            buf[p] = data
        return buf[p]
    def rename_prefix(s, d):
        pre = s + "/"
        for q in [q for q in files if q.startswith(pre)]:
            nq = d + "/" + q[len(pre):]
            files[nq] = files.pop(q); origin[nq] = origin.pop(q)
            if q in buf: buf[nq] = buf.pop(q)
        for q in [q for q in dirs if q == s or q.startswith(pre)]:
            dirs.discard(q); dirs.add(d + q[len(s):])

    for n, op, args in ops:
        paths = args[:2] if op in ("mv", "append", "dedupe") else [] if op in ("leave", "partition") else args[:1]
        if any(is_frozen(p) for p in paths):
            errors.append(f"line {n}: touches the frozen register: {paths}"); continue
        if op in ("synthesize", "dedupe"):
            import semantics
            new = args[0] if op == "synthesize" else args[1]          # the file this op creates
            if exists(new): errors.append(f"line {n}: {op} would clobber: {new}"); continue
            if not parent_ok(new): errors.append(f"line {n}: {op} destination folder missing: {new}"); continue
            if not new.endswith(".md"): errors.append(f"line {n}: {op} target is not a note: {new}"); continue
            if case_clash(new): errors.append(f"line {n}: {op} target differs only by case from an existing name: {new}"); continue
        if op == "synthesize":
            try:
                with open(os.path.expanduser(args[1]), "rb") as fh:
                    data = fh.read()
            except OSError as e:
                errors.append(f"line {n}: synthesis draft unreadable: {e}"); continue
            probs = semantics.check_synthesis(data, lambda p: content(p) if p in files else None)
            if probs:
                errors += [f"line {n}: synthesis {args[0]}: {p}" for p in probs]; continue
            buf[args[0]], files[args[0]] = data, hashlib.sha256(data).hexdigest()
            origin[args[0]] = "(created) " + args[0]
            continue
        if op == "dedupe":
            src, arc = args
            if src not in files or not src.endswith(".md"):
                errors.append(f"line {n}: dedupe needs an existing note: {src}"); continue
            data = content(src)
            new_b, dropped = semantics.dedupe_bytes(data)
            if not dropped:
                errors.append(f"line {n}: nothing repeats in {src}"); continue
            buf[arc], files[arc], origin[arc] = data, hashlib.sha256(data).hexdigest(), "(created) " + arc
            buf[src], files[src] = new_b, hashlib.sha256(new_b).hexdigest()
            continue
        if op == "mkdir":
            (p,) = args
            if exists(p): errors.append(f"line {n}: mkdir target exists: {p}"); continue
            if not parent_ok(p): errors.append(f"line {n}: mkdir parent missing: {p}"); continue
            dirs.add(p)
        elif op == "rmdir":
            (p,) = args
            if p not in dirs: errors.append(f"line {n}: rmdir target is not a folder: {p}"); continue
            if any(q.startswith(p + "/") for q in list(files) + list(dirs)):
                errors.append(f"line {n}: rmdir target not empty: {p}"); continue
            dirs.discard(p)
        elif op == "mv":
            s, d = args
            if not exists(s): errors.append(f"line {n}: mv source missing: {s}"); continue
            if exists(d): errors.append(f"line {n}: mv would clobber: {d}"); continue
            if not parent_ok(d): errors.append(f"line {n}: mv destination folder missing: {d}"); continue
            if d.startswith(s + "/"): errors.append(f"line {n}: mv into itself: {s} -> {d}"); continue
            if case_clash(d): errors.append(f"line {n}: mv destination differs only by case from an existing name: {d}"); continue
            if s in files:
                files[d] = files.pop(s); origin[d] = origin.pop(s)
                if s in buf: buf[d] = buf.pop(s)
            else:
                rename_prefix(s, d)
        elif op == "append":
            d, s, heading = args
            if d not in files or s not in files:
                errors.append(f"line {n}: append needs two existing files: {d}, {s}"); continue
            if not d.endswith(".md"): errors.append(f"line {n}: append target is not a note: {d}"); continue
            new = content(d) + _sep(heading) + content(s)
            buf[d] = new
            files[d] = hashlib.sha256(new).hexdigest()
        elif op == "trash":
            (p,) = args
            if p not in files: errors.append(f"line {n}: trash target is not a file: {p}"); continue
            removed.append(origin[p]); files.pop(p); origin.pop(p); buf.pop(p, None)
        elif op == "merge":
            dst, srcs = args[0], args[1:]
            paths = [s[5:] if s.startswith("code:") else s for s in srcs]
            if exists(dst): errors.append(f"line {n}: merge would clobber: {dst}"); continue
            if not parent_ok(dst): errors.append(f"line {n}: merge destination folder missing: {dst}"); continue
            if not dst.endswith(".md"): errors.append(f"line {n}: merge target is not a note: {dst}"); continue
            if case_clash(dst): errors.append(f"line {n}: merge target differs only by case from an existing name: {dst}"); continue
            missing = [p for p in paths if p not in files]
            if missing: errors.append(f"line {n}: merge sources missing: {missing}"); continue
            data, where = _merge_bytes(dst, srcs, content)
            buf[dst] = data
            files[dst] = hashlib.sha256(data).hexdigest()
            origin[dst] = "(created) " + dst
            contained.update({origin[p]: [dst, off, ln] for p, (off, ln) in where.items()})
        elif op in ("compose", "extend", "leave", "partition"):
            import semantics
            src = args[1] if op in ("compose", "extend") else args[0]
            if src not in files:
                errors.append(f"line {n}: {op} source missing: {src}"); continue
            data_src = content(src)
            n_lines = len(data_src.splitlines(keepends=True))
            if op == "partition":
                if hashlib.sha256(data_src).hexdigest() != args[1]:
                    errors.append(f"line {n}: {src} changed since it was segmented — segment it again and re-plan"); continue
                pinned[origin[src]] = n_lines
                continue
            try:
                sp = semantics.spans(args[2] if op in ("compose", "extend") else args[1], semantics.segment(data_src))
            except (KeyError, ValueError) as e:
                errors.append(f"line {n}: {src}: no such part or line: {e}"); continue
            bad = [(a, b) for a, b in sp if not 1 <= a <= b <= n_lines]
            if bad:
                errors.append(f"line {n}: line range(s) outside {src} (1–{n_lines}): {bad}"); continue
            covered = [ln for a, b in sp for ln in range(a, b + 1)]
            if op == "leave":
                placed[origin[src]] += covered
                continue
            dst, piece = args[0], semantics.take(data_src, sp)
            if op == "compose":
                if exists(dst): errors.append(f"line {n}: compose would clobber: {dst}"); continue
                if not parent_ok(dst): errors.append(f"line {n}: compose destination folder missing: {dst}"); continue
                if not dst.endswith(".md"): errors.append(f"line {n}: compose target is not a note: {dst}"); continue
                if case_clash(dst): errors.append(f"line {n}: compose target differs only by case from an existing name: {dst}"); continue
                data = piece
                origin[dst] = "(created) " + dst
            else:
                if dst not in files or not dst.endswith(".md"):
                    errors.append(f"line {n}: extend needs an existing note: {dst}"); continue
                old_b = content(dst)
                data = old_b + (b"\n" if old_b.endswith(b"\n") else b"\n\n") + piece
            base, off = len(data) - len(piece), 0
            for a, b in sp:
                ln = len(semantics.take(data_src, [(a, b)]))
                contained[f"{origin[src]}#L{a}-L{b}"] = [dst, base + off, ln]
                off += ln
            buf[dst] = data
            files[dst] = hashlib.sha256(data).hexdigest()
            placed[origin[src]] += covered

    # a declared partition: every line of the source placed exactly once — composed, extended or left
    for src, n_lines in pinned.items():
        used = Counter(placed[src])
        missing = [ln for ln in range(1, n_lines + 1) if not used[ln]]
        twice = sorted(ln for ln, c in used.items() if c > 1)
        if missing:
            errors.append(f"partition of {src}: {len(missing)} line(s) have no place — the first is L{missing[0]}")
        if twice:
            errors.append(f"partition of {src}: {len(twice)} line(s) placed more than once — the first is L{twice[0]}")

    # a trashed file is lossless only if an identical copy survives the WHOLE plan
    surviving = set(files.values())
    for o in removed:
        if before_hash[o] not in surviving:
            errors.append(f"trash would lose content — no identical copy survives: {o}")

    old = lambda c: origin[c] in before_hash
    moves = {origin[c]: c for c in files if old(c) and origin[c] != c}
    hashes = {c: h for c, h in files.items() if old(c) and h != before_hash[origin[c]]}
    added = {c: h for c, h in files.items() if not old(c)}
    expect = {"moves": moves, "removed": sorted(removed), "hashes": hashes, "added": added,
              "contained": contained}

    movable = [c for c in files if not is_frozen(c)]
    report = {
        "by_top": dict(Counter(c.split("/")[0] if "/" in c else "(vault root)" for c in movable)),
        "unrouted": sorted(c for c in movable if c.split("/")[0] not in PARA),
        "too_deep": sorted(c for c in movable if c.split("/")[0] in PARA[1:] and c.count("/") > 2),
        "final": {origin[c]: c for c in movable},
    }
    return errors, expect, report

def _gio_trash(abs_path):
    """Real removal: the desktop trash (recoverable). Returns where the file went."""
    r = subprocess.run(["gio", "trash", "--", abs_path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gio trash failed: {r.stderr.strip()}")
    return _find_in_trash(abs_path) or ""

def _find_in_trash(abs_path):
    """The freedesktop trash keeps info/<name>.trashinfo with Path=<url-encoded original>.
    Return files/<name> for the newest entry that came from abs_path."""
    info, best = os.path.join(TRASH, "info"), None
    for f in (os.listdir(info) if os.path.isdir(info) else []):
        if not f.endswith(".trashinfo"):
            continue
        try:
            text = open(os.path.join(info, f), encoding="utf-8").read()
        except OSError:
            continue
        m = re.search(r"^Path=(.*)$", text, re.M)
        dd = re.search(r"^DeletionDate=(.*)$", text, re.M)
        if m and unquote(m.group(1)) == abs_path:
            date = dd.group(1) if dd else ""
            if best is None or date > best[0]:
                best = (date, os.path.join(TRASH, "files", f[:-len(".trashinfo")]))
    return best[1] if best else None

def _scratch_trash_fn(bin_dir):
    """Removal for a scratch copy: a folder beside it, so a rehearsal never fills the real trash."""
    def fn(abs_path):
        os.makedirs(bin_dir, exist_ok=True)
        dst = os.path.join(bin_dir, f"{len(os.listdir(bin_dir))}-{os.path.basename(abs_path)}")
        os.rename(abs_path, dst)
        return dst
    return fn

def _log(fh, *fields):
    fh.write("\t".join([datetime.now().isoformat(timespec="seconds"), *map(str, fields)]) + "\n")
    fh.flush(); os.fsync(fh.fileno())

def apply_plan(vault, ops, before_rows, trash_fn, undo_path, frozen_live=False):
    """Act on the plan. Every op is logged BEFORE it runs and re-sensed after — an organ
    reports what happened, never that it ran. Stops at the first surprise, then verifies
    the whole vault against expectations derived before anything moved."""
    errors, expect, _ = simulate(vault, ops, before_rows)
    if errors:
        return False, ["refused — the dry run has errors:"] + errors, expect
    J = lambda p: os.path.join(vault, p)
    with open(undo_path, "a", encoding="utf-8") as log:
        _log(log, "plan-begin", vault)
        for n, op, args in ops:
            try:
                if op == "mkdir":
                    _log(log, "mkdir", args[0])
                    os.mkdir(J(args[0]))
                    ok = os.path.isdir(J(args[0]))
                elif op == "rmdir":
                    _log(log, "rmdir", args[0])
                    os.rmdir(J(args[0]))                          # refuses if not empty
                    ok = not os.path.lexists(J(args[0]))
                elif op == "mv":
                    s, d = args
                    if os.path.lexists(J(d)):
                        return False, [f"line {n}: stopped — destination appeared: {d}"], expect
                    _log(log, "mv", s, d)
                    os.rename(J(s), J(d))
                    ok = os.path.lexists(J(d)) and not os.path.lexists(J(s))
                elif op == "append":
                    d, s, heading = args
                    with open(J(d), "rb") as fh: old = fh.read()
                    with open(J(s), "rb") as fh: new = old + _sep(heading) + fh.read()
                    _log(log, "append", d, s, len(old), hashlib.sha256(old).hexdigest())
                    tmp = J(d) + ".cerebrum-tmp"
                    with open(tmp, "wb") as fh:
                        fh.write(new); fh.flush(); os.fsync(fh.fileno())
                    shutil.copymode(J(d), tmp)
                    os.replace(tmp, J(d))
                    ok = sha256(J(d)) == hashlib.sha256(new).hexdigest()
                elif op == "trash":
                    (p,) = args
                    _log(log, "trash", p)
                    where = trash_fn(J(p))
                    _log(log, "trashed-to", p, where)
                    ok = not os.path.lexists(J(p))
                elif op == "merge":
                    dst, srcs = args[0], args[1:]
                    if os.path.lexists(J(dst)):
                        return False, [f"line {n}: stopped — destination appeared: {dst}"], expect
                    def _read(p):
                        with open(J(p), "rb") as fh:
                            return fh.read()
                    data, _ = _merge_bytes(dst, srcs, _read)
                    made = hashlib.sha256(data).hexdigest()
                    _log(log, "merge", dst, made)
                    with open(J(dst), "xb") as fh:
                        fh.write(data); fh.flush(); os.fsync(fh.fileno())
                    ok = sha256(J(dst)) == made
                elif op == "synthesize":
                    dst = args[0]
                    if os.path.lexists(J(dst)):
                        return False, [f"line {n}: stopped — destination appeared: {dst}"], expect
                    with open(os.path.expanduser(args[1]), "rb") as fh:
                        data = fh.read()
                    made = hashlib.sha256(data).hexdigest()
                    _log(log, "synthesize", dst, made)
                    with open(J(dst), "xb") as fh:
                        fh.write(data); fh.flush(); os.fsync(fh.fileno())
                    ok = sha256(J(dst)) == made          # a draft changed since the dry run fails the verify
                elif op == "dedupe":
                    src, arc = args
                    import semantics
                    if os.path.lexists(J(arc)):
                        return False, [f"line {n}: stopped — destination appeared: {arc}"], expect
                    with open(J(src), "rb") as fh:
                        old = fh.read()
                    new_b = semantics.dedupe_bytes(old)[0]
                    h_old, h_new = hashlib.sha256(old).hexdigest(), hashlib.sha256(new_b).hexdigest()
                    _log(log, "dedupe", src, arc, h_old, h_new)
                    with open(J(arc), "xb") as fh:              # the whole original first
                        fh.write(old); fh.flush(); os.fsync(fh.fileno())
                    tmp = J(src) + ".cerebrum-tmp"
                    with open(tmp, "wb") as fh:
                        fh.write(new_b); fh.flush(); os.fsync(fh.fileno())
                    shutil.copymode(J(src), tmp)
                    os.replace(tmp, J(src))
                    ok = sha256(J(arc)) == h_old and sha256(J(src)) == h_new
                elif op in ("compose", "extend"):
                    dst, src, spec = args
                    import semantics
                    with open(J(src), "rb") as fh:
                        data_src = fh.read()
                    piece = semantics.take(data_src, semantics.spans(spec, semantics.segment(data_src)))
                    if op == "compose":
                        if os.path.lexists(J(dst)):
                            return False, [f"line {n}: stopped — destination appeared: {dst}"], expect
                        made = hashlib.sha256(piece).hexdigest()
                        _log(log, "compose", dst, made)
                        with open(J(dst), "xb") as fh:
                            fh.write(piece); fh.flush(); os.fsync(fh.fileno())
                        ok = sha256(J(dst)) == made
                    else:
                        with open(J(dst), "rb") as fh:
                            old_b = fh.read()
                        new = old_b + (b"\n" if old_b.endswith(b"\n") else b"\n\n") + piece
                        _log(log, "extend", dst, src, len(old_b), hashlib.sha256(old_b).hexdigest())
                        tmp = J(dst) + ".cerebrum-tmp"
                        with open(tmp, "wb") as fh:
                            fh.write(new); fh.flush(); os.fsync(fh.fileno())
                        shutil.copymode(J(dst), tmp)
                        os.replace(tmp, J(dst))
                        ok = sha256(J(dst)) == hashlib.sha256(new).hexdigest()
                elif op in ("leave", "partition"):
                    ok = True                                  # declarations, checked in the dry run
            except Exception as e:
                return False, [f"line {n}: {op} failed: {e}"], expect
            if not ok:
                return False, [f"line {n}: {op} did not take effect: {args}"], expect
        _log(log, "plan-end", len(ops))
    ok, findings = _verify(vault, before_rows, expect["moves"], expect["removed"], expect["hashes"],
                           frozen_live=frozen_live, expected_added=expect.get("added"))
    return ok, findings, expect

def undo_plan(vault, undo_path):
    """Reverse an applied plan from its undo log, newest first. Each step checks the state
    before acting, so a half-finished run (a crash between a log line and its op) undoes
    cleanly. Returns notes on anything it could not restore."""
    with open(undo_path, encoding="utf-8") as fh:
        lines = [l.rstrip("\n").split("\t") for l in fh if l.strip()]
    begin = [f for f in lines if f[1] == "plan-begin"]
    if begin and os.path.realpath(begin[0][2]) != os.path.realpath(vault):
        raise RuntimeError(f"undo log belongs to another vault: {begin[0][2]}")
    trashed_to = {f[2]: f[3] for f in lines if f[1] == "trashed-to"}
    J = lambda p: os.path.join(vault, p)
    notes = []
    for f in reversed(lines):
        op, args = f[1], f[2:]
        if op == "mkdir":
            if os.path.isdir(J(args[0])):
                os.rmdir(J(args[0]))
        elif op == "rmdir":
            if not os.path.lexists(J(args[0])):
                os.mkdir(J(args[0]))
        elif op == "mv":
            s, d = args
            if os.path.lexists(J(d)) and not os.path.lexists(J(s)):
                os.rename(J(d), J(s))
        elif op in ("append", "extend"):
            d, _s, n_old, sha_old = args
            with open(J(d), "rb") as fh: data = fh.read()
            if hashlib.sha256(data).hexdigest() != sha_old:
                cut = data[:int(n_old)]
                if hashlib.sha256(cut).hexdigest() != sha_old:
                    notes.append(f"could not un-merge {d} — it no longer starts with its original bytes; restore it from the snapshot")
                    continue
                with open(J(d), "wb") as fh: fh.write(cut)
        elif op == "dedupe":
            src, arc, h_old, h_new = args
            if os.path.lexists(J(arc)) and sha256(J(arc)) == h_old:
                cur = sha256(J(src)) if os.path.lexists(J(src)) else None
                if cur == h_new:
                    with open(J(arc), "rb") as fh: old = fh.read()
                    with open(J(src), "wb") as fh: fh.write(old)
                if cur in (h_new, h_old):
                    os.remove(J(arc))
                else:
                    notes.append(f"could not undo the dedupe of {src} — its original is at {arc}")
            elif os.path.lexists(J(arc)):
                notes.append(f"{arc} changed after the dedupe — left in place")
        elif op in ("merge", "compose", "synthesize"):
            dst, made = args[0], args[1]
            if os.path.lexists(J(dst)):
                if sha256(J(dst)) == made:
                    os.remove(J(dst))
                else:
                    notes.append(f"{dst} changed after the merge — left in place")
        elif op == "trash":
            p, where = args[0], trashed_to.get(args[0])
            if os.path.lexists(J(p)):
                continue
            if where and os.path.lexists(where):
                os.rename(where, J(p))
                info = os.path.join(TRASH, "info", os.path.basename(where) + ".trashinfo")
                if where.startswith(os.path.join(TRASH, "files") + "/") and os.path.exists(info):
                    os.remove(info)
            else:
                notes.append(f"could not restore {p} — recover it from the trash or the snapshot")
    return notes

def cmd_move(a):
    if not a.plan or not a.before:
        print("move: need --plan <plan.tsv> and --before <manifest.tsv>"); return 2
    real = _is_real(VAULT)
    ops = load_plan(a.plan)
    before_rows = _read_manifest(a.before)
    errors, expect, rep = simulate(VAULT, ops, before_rows)
    print(f"move     : {len(ops)} ops from {a.plan}")
    print(f"           on {'THE LIVE VAULT' if real else 'a scratch copy: ' + VAULT}")
    if a.frozen_live:
        print("           scope: frozen folders are live — their writes are listed, not failed")
    print(f"           derived: {len(expect['moves'])} file moves · {len(expect['removed'])} trashed · "
          f"{len(expect['hashes'])} content change(s)")
    print("           movable files land in: "
          + " · ".join(f"{k} {v}" for k, v in sorted(rep["by_top"].items())))
    if rep["unrouted"]:
        print(f"           ⚠ unrouted ({len(rep['unrouted'])}): " + "; ".join(rep["unrouted"][:12]))
    if rep["too_deep"]:
        print("           ⚠ deeper than PARA → container → note: " + "; ".join(rep["too_deep"][:12]))
    for e in errors:
        print("   ERROR " + e)
    if errors:
        print("move     : REFUSED — the dry run has errors; nothing changed"); return 1
    if a.show:
        for o, c in sorted(rep["final"].items()):
            if o != c:
                print(f"   {o}  →  {c}")
        for o in expect["removed"]:
            print(f"   {o}  →  (trash)")
    if not a.apply:
        print("move     : dry run OK — nothing changed. Add --apply --i-ratified to act."); return 0
    if not a.i_ratified:
        print("move: refused. --apply needs --i-ratified (the keeper has ratified this plan)."); return 2
    if real and _obsidian_running():
        print("move: refused. Close Obsidian first — it rewrites files while open."); return 2
    pre_ok, pre = _verify(VAULT, before_rows, frozen_live=a.frozen_live)
    if not pre_ok:
        print("move: refused. The vault no longer matches the before-manifest:")
        _print_findings(pre, limit=20)
        return 1
    ensure_dirs()
    stamp = ts()
    undo_path = f"{STATE}/undo-{stamp}.tsv"
    with open(f"{STATE}/expect-{stamp}.json", "w", encoding="utf-8") as fh:
        json.dump(expect, fh, ensure_ascii=False, indent=1)
    trash_fn = _gio_trash if real else _scratch_trash_fn(
        os.path.join(os.path.dirname(os.path.abspath(VAULT)), "_trash"))
    ok, findings, _ = apply_plan(VAULT, ops, before_rows, trash_fn, undo_path,
                                 frozen_live=a.frozen_live)
    print(f"move     : undo log   {undo_path}")
    print(f"           expected   {STATE}/expect-{stamp}.json")
    print("move     : " + ("GREEN — applied and verified against the plan" if ok else "RED"))
    _print_findings(findings)
    return 0 if ok else 1

def cmd_undo(a):
    if not a.log:
        print("undo: need --log <undo.tsv>"); return 2
    real = _is_real(VAULT)
    if real and not a.i_ratified:
        print("undo: refused on the live vault without --i-ratified."); return 2
    if real and _obsidian_running():
        print("undo: refused. Close Obsidian first."); return 2
    notes = undo_plan(VAULT, a.log)
    print("undo     : done" + (" — with notes:" if notes else ""))
    for n in notes:
        print("   " + n)
    return 1 if notes else 0

def cmd_check(_a):
    """The vault's build: every rule under the three laws of thought, each proven able to fail."""
    import checks
    return 0 if checks.run(VAULT, CFG) else 1

def cmd_registry(a):
    """The registry, generated from the files. Prints by default; --write replaces the note."""
    import registry
    if not CFG.get("registry"):
        print('registry: no "registry" path in cerebrum.json'); return 2
    if a.write:
        print(f"registry : wrote {registry.write(VAULT, CFG)}")
    else:
        sys.stdout.write(registry.build(VAULT, CFG))
    return 0

def main():
    ap = argparse.ArgumentParser(description="Builder tool for the metacursive vault")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="run every rule (the vault's build)")
    g = sub.add_parser("registry", help="generate the registry of notes and meta-notes")
    g.add_argument("--write", action="store_true", help="replace the registry note in the vault")
    sub.add_parser("snapshot")
    sub.add_parser("manifest")
    sub.add_parser("inventory")
    v = sub.add_parser("verify"); v.add_argument("--before"); v.add_argument("--expect")
    v.add_argument("--frozen-live", dest="frozen_live", action="store_true")
    sub.add_parser("selftest")
    m = sub.add_parser("move"); m.add_argument("--before"); m.add_argument("--plan")
    m.add_argument("--apply", action="store_true")
    m.add_argument("--show", action="store_true", help="list every planned move")
    m.add_argument("--frozen-live", dest="frozen_live", action="store_true")
    m.add_argument("--i-ratified", dest="i_ratified", action="store_true")
    u = sub.add_parser("undo"); u.add_argument("--log")
    u.add_argument("--i-ratified", dest="i_ratified", action="store_true")
    a = ap.parse_args()
    if a.cmd != "selftest" and not VAULT:
        print("cerebrum: no vault configured — set \"vault\" in cerebrum.json "
              "(see cerebrum.example.json) or run with VAULT=<dir>")
        return 2
    return {"snapshot": cmd_snapshot, "manifest": cmd_manifest, "inventory": cmd_inventory,
            "verify": cmd_verify, "selftest": cmd_selftest, "move": cmd_move,
            "undo": cmd_undo, "check": cmd_check,
            "registry": cmd_registry}[a.cmd](a)

if __name__ == "__main__":
    sys.exit(main())
