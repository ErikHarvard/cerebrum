#!/usr/bin/env python3
"""Test suite for cerebrum.py. Builds synthetic vaults, mutates copies, asserts the
verifier's verdict AND that it names the change by path. Exits nonzero on any failure.
Touches nothing real (one gio-trash probe round-trips a scratch file outside the vault)."""
import os, sys, shutil, tempfile, json, atexit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The tests carry their own config: they never read the real vault's register.
_cfg_dir = tempfile.mkdtemp(prefix="cerebrum-testcfg-")
atexit.register(shutil.rmtree, _cfg_dir, True)
with open(os.path.join(_cfg_dir, "cerebrum.json"), "w") as _fh:
    json.dump({"vault": os.path.join(_cfg_dir, "no-vault"),
               "frozen": [{"root": r, "pins": []} for r in ("AGENT MEMORY", "Shared Shelf", "Journal")]}, _fh)
os.environ["CEREBRUM_CONFIG"] = os.path.join(_cfg_dir, "cerebrum.json")
import cerebrum as C

fails = []
def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond: fails.append(name)

def manifest(v):
    rows = [(C.sha256(os.path.join(v, r)), os.path.getsize(os.path.join(v, r)), r)
            for r in C.rels(v)]
    rows += [("DIR", 0, root) for root in C.FROZEN
             if os.path.isdir(os.path.join(v, root))]     # frozen-root dir markers
    return rows

def build(root):
    v = os.path.join(root, "vault")
    for d in ["AGENT MEMORY", ".trash", "Shared Shelf", "notes", "x", "y", "# INBOX"]:
        os.makedirs(os.path.join(v, d))                   # "Shared Shelf" stays EMPTY (frozen)
    open(os.path.join(v, "AGENT MEMORY", "keep.md"), "w").write("frozen note\n")
    open(os.path.join(v, ".trash", "old.md"), "w").write("trashed\n")
    open(os.path.join(v, "notes", "a.md"), "w").write("see [[b]] for more\n")
    open(os.path.join(v, "notes", "b.md"), "w").write("the target\n")
    open(os.path.join(v, "notes", "d.md"), "w").write("dangling [[ghost]] link\n")  # pre-existing broken
    open(os.path.join(v, "x", "Dup.md"), "w").write("one\n")
    open(os.path.join(v, "y", "Dup.md"), "w").write("two — different\n")
    open(os.path.join(v, "# INBOX", "law.md"), "w").write("the law, [[a]] and ![[pic.png]]\n")
    open(os.path.join(v, "pic.png"), "wb").write(b"\x89PNG fake image bytes")
    return v

def copy(v, dst):
    shutil.copytree(v, dst); return dst

def has(findings, *subs):
    return any(all(s in f for s in subs) for f in findings)

def write_plan(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return C.load_plan(path)

def log_rows(path):
    with open(path, encoding="utf-8") as fh:
        return [l.rstrip("\n").split("\t") for l in fh if l.strip()]

print("== is_frozen matrix ==")
check("AGENT MEMORY frozen", C.is_frozen("AGENT MEMORY/sub/x.md"))
check(".trash frozen", C.is_frozen(".trash/old.md"))
check("movable not frozen", not C.is_frozen("notes/a.md"))
check("false-prefix not frozen (Journalfoo)", not C.is_frozen("Journalfoo/x.md"))

tmp = tempfile.mkdtemp(prefix="cerebrum-test-")
try:
    v = build(tmp)
    before = manifest(v)

    print("== control ==")
    ok, f = C._verify(v, before)
    check("clean vault is GREEN (pre-existing [[ghost]] does NOT fail it)", ok)

    print("== move, basename unchanged: RED+named without a proposal; GREEN when ratified ==")
    v1 = copy(v, os.path.join(tmp, "m1"))
    os.makedirs(os.path.join(v1, "notes", "sub"))
    os.rename(os.path.join(v1, "notes", "b.md"), os.path.join(v1, "notes", "sub", "b.md"))
    ok, f = C._verify(v1, before)
    check("unratified move is RED", not ok)
    check("  finding names the move by path", has(f, "moved", "notes/b.md", "notes/sub/b.md"))
    ok2, f2 = C._verify(v1, before, expected_moves={("notes/b.md", "notes/sub/b.md")})
    check("ratified move is GREEN (link survives, move expected)", ok2)

    print("== rename that breaks a link: RED even when the move is ratified ==")
    v2 = copy(v, os.path.join(tmp, "m2"))
    os.rename(os.path.join(v2, "notes", "b.md"), os.path.join(v2, "notes", "c.md"))
    ok, f = C._verify(v2, before, expected_moves={("notes/b.md", "notes/c.md")})
    check("ratified rename that breaks [[b]] is still RED", not ok)
    check("  and it names the broken link", has(f, "link broken"))

    print("== delete a file ==")
    v3 = copy(v, os.path.join(tmp, "m3"))
    os.remove(os.path.join(v3, "notes", "a.md"))
    ok, f = C._verify(v3, before)
    check("deleted file is RED and named", not ok and has(f, "removed", "notes/a.md"))

    print("== corrupt a file in place ==")
    v4 = copy(v, os.path.join(tmp, "m4"))
    open(os.path.join(v4, "notes", "b.md"), "w").write("SILENTLY CHANGED\n")
    ok, f = C._verify(v4, before)
    check("corrupted file is RED and named", not ok and has(f, "changed-in-place", "notes/b.md"))

    print("== add a file ==")
    v5 = copy(v, os.path.join(tmp, "m5"))
    open(os.path.join(v5, "notes", "extra.md"), "w").write("uninvited\n")
    ok, f = C._verify(v5, before)
    check("added file is RED and named", not ok and has(f, "added", "notes/extra.md"))

    print("== remove a FROZEN file ==")
    v6 = copy(v, os.path.join(tmp, "m6"))
    os.remove(os.path.join(v6, "AGENT MEMORY", "keep.md"))
    ok, f = C._verify(v6, before)
    check("frozen file removed is RED (frozen touched)", not ok and has(f, "frozen touched"))

    print("== delete an EMPTY frozen dir (invisible to a file list) ==")
    v7 = copy(v, os.path.join(tmp, "m7"))
    os.rmdir(os.path.join(v7, "Shared Shelf"))
    ok, f = C._verify(v7, before)
    check("empty frozen dir removed is RED", not ok and has(f, "frozen root missing"))

    print("== rename an attachment (embed target) ==")
    v8 = copy(v, os.path.join(tmp, "m8"))
    os.rename(os.path.join(v8, "pic.png"), os.path.join(v8, "pic2.png"))
    ok, f = C._verify(v8, before)
    check("renamed attachment breaks the embed → RED link", not ok and has(f, "link broken"))

    print("== collision detection ==")
    idx = C._index(v)[0]
    check("Dup collision detected", len(idx.get("dup", [])) == 2)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== mover: plan → dry run → apply → verify → undo ==")
tmp2 = tempfile.mkdtemp(prefix="cerebrum-mover-")
try:
    v = build(tmp2)
    os.makedirs(os.path.join(v, "Old"))
    open(os.path.join(v, "Old", "twin.md"), "w").write("same bytes\n")
    open(os.path.join(v, "loose-twin.md"), "w").write("same bytes\n")
    open(os.path.join(v, "merge-src.md"), "w").write("# Source\nidea [[b]]\n")
    before = manifest(v)
    ops = write_plan(os.path.join(tmp2, "plan.tsv"), [
        "# test plan",
        "mkdir\t2 AREAS",
        "mkdir\t4 ARCHIVE",
        "append\tnotes/a.md\tmerge-src.md\tMerged from merge-src",
        "trash\tloose-twin.md",
        "mv\tnotes\t2 AREAS/notes",
        "mv\tmerge-src.md\t4 ARCHIVE/merge-src (source).md",
        "mv\tOld/twin.md\t4 ARCHIVE/twin.md",
        "rmdir\tOld",
    ])
    errs, exp, rep = C.simulate(v, ops, before)
    check("dry run of a good plan has no errors", errs == [])
    check("dry run changed nothing", C._verify(v, before)[0])
    check("derived: notes/a.md → 2 AREAS/notes/a.md", exp["moves"].get("notes/a.md") == "2 AREAS/notes/a.md")
    check("derived: the merged note has a planned new hash", "2 AREAS/notes/a.md" in exp["hashes"])
    check("derived: loose-twin.md is the one trashed file", exp["removed"] == ["loose-twin.md"])

    trash_bin = os.path.join(tmp2, "_trash")
    undo = os.path.join(tmp2, "undo.tsv")
    ok, f, _ = C.apply_plan(v, ops, before, C._scratch_trash_fn(trash_bin), undo)
    check("apply → verify GREEN against the derived plan", ok)
    if not ok: print("     ", f[:6])
    with open(os.path.join(v, "2 AREAS", "notes", "a.md"), "rb") as fh:
        merged = fh.read()
    check("merge = original + separator + source, byte-exact",
          merged == b"see [[b]] for more\n" + C._sep("Merged from merge-src") + b"# Source\nidea [[b]]\n")
    check("emptied folder removed", not os.path.exists(os.path.join(v, "Old")))
    check("trashed file sits in the trash, not deleted", len(os.listdir(trash_bin)) == 1)
    check("every op was logged before it ran (8 ops + begin/end + trashed-to)",
          [r[1] for r in log_rows(undo)] == ["plan-begin", "mkdir", "mkdir", "append", "trash",
                                             "trashed-to", "mv", "mv", "mv", "rmdir", "plan-end"])

    print("== planted defects after apply go RED ==")
    va = copy(v, os.path.join(tmp2, "planted1"))
    os.remove(os.path.join(va, "2 AREAS", "notes", "b.md"))
    okp, fp = C._verify(va, before, exp["moves"], exp["removed"], exp["hashes"])
    check("a lost file → RED, named", not okp and has(fp, "notes/b.md"))
    vb = copy(v, os.path.join(tmp2, "planted2"))
    open(os.path.join(vb, "2 AREAS", "notes", "a.md"), "ab").write(b"tampered")
    okq, fq = C._verify(vb, before, exp["moves"], exp["removed"], exp["hashes"])
    check("a merged note that differs from the plan → RED", not okq and has(fq, "content differs"))
    vc = copy(v, os.path.join(tmp2, "planted3"))
    os.rename(os.path.join(vc, "2 AREAS", "notes", "d.md"), os.path.join(vc, "4 ARCHIVE", "d.md"))
    okr, fr = C._verify(vc, before, exp["moves"], exp["removed"], exp["hashes"])
    check("an extra, unplanned move → RED", not okr and has(fr, "did not happen", "notes/d.md"))
    vd = copy(v, os.path.join(tmp2, "planted4"))
    os.remove(os.path.join(vd, "4 ARCHIVE", "twin.md"))
    oks, fs = C._verify(vd, before, exp["moves"], exp["removed"], exp["hashes"])
    check("trashed file whose twin is also gone → RED (no identical copy left)",
          not oks and has(fs, "no identical copy left"))

    print("== undo restores the before-state ==")
    notes = C.undo_plan(v, undo)
    check("undo reports nothing unrestorable", notes == [])
    okU, fU = C._verify(v, before)
    check("after undo the vault matches the before-manifest (GREEN, no expectations)", okU)
    if not okU: print("     ", fU[:6])
    try:
        C.undo_plan(os.path.join(tmp2, "planted1"), undo); refused = False
    except RuntimeError:
        refused = True
    check("undo refuses a log that belongs to another vault", refused)
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print("== mover refuses bad plans in the dry run ==")
tmp3 = tempfile.mkdtemp(prefix="cerebrum-refuse-")
try:
    v = build(tmp3)
    open(os.path.join(v, "unique.md"), "w").write("only copy\n")
    before = manifest(v)
    def errs_for(lines):
        return C.simulate(v, write_plan(os.path.join(tmp3, "p.tsv"), lines), before)[0]
    check("mv onto an existing file refused (no clobber)", errs_for(["mv\tx/Dup.md\ty/Dup.md"]) != [])
    check("frozen source refused", errs_for(["mv\tAGENT MEMORY\tz"]) != [])
    check("frozen destination refused", errs_for(["mv\tunique.md\tShared Shelf/unique.md"]) != [])
    check("trash with no surviving copy refused (lossless)", errs_for(["trash\tunique.md"]) != [])
    check("rmdir of a non-empty folder refused", errs_for(["rmdir\tnotes"]) != [])
    check("mv into a missing folder refused", errs_for(["mv\tunique.md\tnowhere/unique.md"]) != [])
    check("mv into itself refused", errs_for(["mkdir\tnotes/inner", "mv\tnotes\tnotes/inner/notes"]) != [])
    check("case-only name clash refused", errs_for(["mv\tunique.md\tnotes/A.md"]) != [])
    try:
        write_plan(os.path.join(tmp3, "q.tsv"), ["mv\tonly-one"]); bad = False
    except ValueError:
        bad = True
    check("a one-argument mv is refused as bad arity", bad)
    check("a good one-liner passes", errs_for(["mv\tunique.md\tnotes/unique.md"]) == [])
    open(os.path.join(v, "notes", "b.md"), "w").write("edited after the manifest\n")
    check("append of a file changed since the manifest refused",
          errs_for(["append\tnotes/a.md\tnotes/b.md\tH"]) != [])
finally:
    shutil.rmtree(tmp3, ignore_errors=True)

print("== scoped check (frozen_live): frozen folders are live, everything else strict ==")
tmp4 = tempfile.mkdtemp(prefix="cerebrum-scope-")
try:
    v = build(tmp4)
    open(os.path.join(v, "AGENT MEMORY", "linked.md"), "w").write("a frozen note\n")
    open(os.path.join(v, "notes", "points-at-frozen.md"), "w").write("see [[linked]]\n")
    before = manifest(v)
    # a live program writes while we work: change, add, and remove inside a frozen folder
    vl = copy(v, os.path.join(tmp4, "live"))
    open(os.path.join(vl, "AGENT MEMORY", "keep.md"), "a").write("a new entry\n")
    open(os.path.join(vl, "AGENT MEMORY", "new-mem.md"), "w").write("fresh memory\n")
    os.remove(os.path.join(vl, "AGENT MEMORY", "linked.md"))     # also breaks [[linked]]
    check("strict: frozen writes are RED", not C._verify(vl, before)[0])
    ok_live, f_live = C._verify(vl, before, frozen_live=True)
    check("scoped: the same frozen writes are GREEN", ok_live)
    check("  … each listed as a NOTE by path (changed, added, removed)",
          has(f_live, "NOTE", "changed", "keep.md") and has(f_live, "NOTE", "added", "new-mem.md")
          and has(f_live, "NOTE", "removed", "linked.md"))
    check("  … a link broken by a frozen file vanishing is a NOTE, not RED",
          has(f_live, "NOTE", "[[linked]]") and not any(x.startswith("RED") for x in f_live))
    n = [0]
    def red_live(mutate, **kw):
        n[0] += 1
        w = copy(vl, os.path.join(tmp4, f"w{n[0]}"))
        mutate(w)
        return not C._verify(w, before, frozen_live=True, **kw)[0]
    check("scoped still RED: a movable file removed",
          red_live(lambda w: os.remove(os.path.join(w, "notes", "b.md"))))
    check("scoped still RED: a movable file changed",
          red_live(lambda w: open(os.path.join(w, "notes", "b.md"), "w").write("x\n")))
    check("scoped still RED: a movable file added",
          red_live(lambda w: open(os.path.join(w, "stray.md"), "w").write("x\n")))
    check("scoped still RED: an unplanned movable move",
          red_live(lambda w: (os.makedirs(os.path.join(w, "z")),
                              os.rename(os.path.join(w, "notes", "d.md"), os.path.join(w, "z", "d.md")))))
    check("scoped still RED: a frozen ROOT deleted",
          red_live(lambda w: os.rmdir(os.path.join(w, "Shared Shelf"))))
    check("scoped still RED: a PLANNED rename that breaks a link to a movable note",
          red_live(lambda w: os.rename(os.path.join(w, "notes", "b.md"), os.path.join(w, "notes", "renamed.md")),
                   expected_moves={("notes/b.md", "notes/renamed.md")}))

    # a live program writes to a frozen file DURING the apply
    vm = build(os.path.join(tmp4, "mid"))
    open(os.path.join(vm, "t1.md"), "w").write("twin\n")
    open(os.path.join(vm, "x", "t2.md"), "w").write("twin\n")
    bm = manifest(vm)
    ops = write_plan(os.path.join(tmp4, "mid.tsv"),
                     ["mkdir\t3 RESOURCES", "trash\tt1.md", "mv\tnotes\t3 RESOURCES/notes"])
    inner = C._scratch_trash_fn(os.path.join(tmp4, "mid_trash"))
    def organ_writes_too(p):
        open(os.path.join(vm, "AGENT MEMORY", "keep.md"), "a").write("an entry during the run\n")
        return inner(p)
    okM, fM, _ = C.apply_plan(vm, ops, bm, organ_writes_too, os.path.join(tmp4, "mid-undo.tsv"),
                              frozen_live=True)
    check("scoped apply while a live program writes mid-run → GREEN, and the write is listed",
          okM and has(fM, "NOTE", "keep.md"))
    check("  … the same end state under the strict check is RED", not C._verify(
          vm, bm, {"notes/a.md": "3 RESOURCES/notes/a.md", "notes/b.md": "3 RESOURCES/notes/b.md",
                   "notes/d.md": "3 RESOURCES/notes/d.md"}, ["t1.md"], {})[0])
finally:
    shutil.rmtree(tmp4, ignore_errors=True)

print("== rehearse.py: a good plan PASSES; a blind verifier or a refused plan FAILS ==")
import rehearse as R
tmp5 = tempfile.mkdtemp(prefix="cerebrum-rehearse-test-")
leftovers = lambda: {d for d in os.listdir(tempfile.gettempdir()) if d.startswith("cerebrum-rehearsal-")}
before_scratch = leftovers()
try:
    v = build(tmp5)
    open(os.path.join(v, "twin-a.md"), "w").write("same\n")
    open(os.path.join(v, "x", "twin-b.md"), "w").write("same\n")
    src_before = manifest(v)
    plan = os.path.join(tmp5, "plan.tsv")
    write_plan(plan, ["mkdir\t2 AREAS", "append\tnotes/a.md\tx/Dup.md\tH",
                      "trash\ttwin-a.md", "mv\tnotes\t2 AREAS/notes"])
    quiet = lambda *a, **k: None
    check("a good plan rehearses PASS", R.rehearse(plan, v, snapshot=False, say=quiet))
    check("  … and the source vault is untouched", C._verify(v, src_before)[0])
    real_verify = C._verify
    C._verify = lambda *a, **k: (True, [])             # a verifier that never looks
    try:
        blind = R.rehearse(plan, v, snapshot=False, say=quiet)
    finally:
        C._verify = real_verify
    check("with a verifier that never looks, the rehearsal FAILS", not blind)
    bad = os.path.join(tmp5, "bad.tsv")
    write_plan(bad, ["mv\tx/Dup.md\ty/Dup.md"])         # would clobber
    check("a plan the dry run refuses → the rehearsal FAILS",
          not R.rehearse(bad, v, snapshot=False, say=quiet))
    check("no scratch copies left behind", leftovers() == before_scratch)
finally:
    shutil.rmtree(tmp5, ignore_errors=True)

print("== merge: word for word, byte-lossless, sources kept; frozen sources read, never written ==")
tmp6 = tempfile.mkdtemp(prefix="cerebrum-merge-")
try:
    v = build(tmp6)
    open(os.path.join(v, "AGENT MEMORY", "scroll.md"), "w").write("# A frozen scroll\nwith [[links]] inside\n")
    open(os.path.join(v, "notes", "code.md"), "w").write("#!/usr/bin/env python3\nx = arr[[1, 2]]\n```\ninner fence\n```\n")
    before = manifest(v)
    ops = write_plan(os.path.join(tmp6, "p.tsv"),
                     ["merge\tnotes/Merged.md\tAGENT MEMORY/scroll.md\tcode:notes/code.md"])
    errs, exp, _ = C.simulate(v, ops, before)
    check("merging a frozen source into a new movable note passes the dry run", errs == [])
    check("  derived: the merged note is the one expected new file", list(exp.get("added", {})) == ["notes/Merged.md"])
    undo = os.path.join(tmp6, "undo.tsv")
    ok, f, _ = C.apply_plan(v, ops, before, C._scratch_trash_fn(os.path.join(tmp6, "_t")), undo)
    check("apply → GREEN (a new note the plan predicted is not an unplanned addition)", ok)
    if not ok: print("     ", f[:4])
    merged = open(os.path.join(v, "notes", "Merged.md"), "rb").read()
    srcs = {p: open(os.path.join(v, p), "rb").read() for p in ("AGENT MEMORY/scroll.md", "notes/code.md")}
    check("every source sits byte for byte at its recorded place",
          all(merged[o:o + n] == srcs[p] for p, (d, o, n) in exp["contained"].items()) and len(exp["contained"]) == 2)
    stripped = C._strip_code(merged.decode())
    check("a fenced code part — even one holding its own ``` block — is not read for links", "[[1, 2]]" not in stripped)
    check("a prose part keeps its links", "[[links]]" in stripped)
    vb = copy(v, os.path.join(tmp6, "tampered"))
    open(os.path.join(vb, "notes", "Merged.md"), "ab").write(b"x")
    okb, fb = C._verify(vb, before, exp["moves"], exp["removed"], exp["hashes"], expected_added=exp["added"])
    check("a merged note that differs from the plan → RED", not okb and has(fb, "created but content differs"))
    now = manifest(v)
    check("merging onto an existing note is refused (no clobber)",
          C.simulate(v, write_plan(os.path.join(tmp6, "q.tsv"), ["merge\tnotes/a.md\tnotes/b.md"]), now)[0] != [])
    check("merging INTO a frozen folder is refused",
          C.simulate(v, write_plan(os.path.join(tmp6, "r.tsv"), ["merge\tAGENT MEMORY/In.md\tnotes/a.md"]), now)[0] != [])
    notes = C.undo_plan(v, undo)
    check("undo removes exactly the merged note; the vault equals its before-manifest",
          notes == [] and C._verify(v, before)[0])
finally:
    shutil.rmtree(tmp6, ignore_errors=True)

print("== checks.py: every rule proves it can fail; the harness catches vacuous and crashing rules ==")
import checks as K
quiet = lambda *a, **k: None
bad = K.selftest()
check(f"every rule ({len(K.RULES)}) passes the clean vault and catches its planted defect", bad == [])
for b in bad:
    print("     ", b)
check("each of the three laws has at least one rule", {r.law for r in K.RULES} == set(K.LAWS))
def _probe(fn):
    K.RULES.append(K.Rule("I", "probe rule", "test only", fn, lambda v: None))
    try:
        return K.selftest()
    finally:
        K.RULES.pop()
check("a rule that can never fail is caught as vacuous",
      any("probe rule: did NOT catch" in b for b in _probe(lambda v: [])))
check("a rule that fails the clean vault is caught",
      any("probe rule: fails on the clean control" in b for b in _probe(lambda v: ["always"])))
def _boom(v):
    raise RuntimeError("boom")
K.RULES.append(K.Rule("III", "crashing rule", "test only", _boom, lambda v: None))
try:
    s, root, cfg = K._scratch()
    crashed_run = K.run(root, cfg, say=quiet)
    shutil.rmtree(s, ignore_errors=True)
finally:
    K.RULES.pop()
check("a crashing rule turns the whole check RED — never a skip", crashed_run is False)
s, root, cfg = K._scratch()
check("the clean scratch vault checks GREEN end to end", K.run(root, cfg, say=quiet) is True)
shutil.rmtree(s, ignore_errors=True)

print("== registry: generated, idempotent, leaves itself out, goes stale when the vault changes ==")
import registry as REG
s, root, cfg = K._scratch()                  # the scratch vault comes with a generated registry
try:
    first = open(os.path.join(root, cfg["registry"]), encoding="utf-8").read()
    check("regenerating an unchanged vault gives the same bytes", REG.build(root, cfg) == first)
    check("the registry does not list itself", f"`{cfg['registry']}`" not in first)
    check("the frozen register shows where the code pins each folder, by line", "organ.py:1" in first)
    check("fresh() agrees before any change", REG.fresh(root, cfg))
    open(os.path.join(root, "3 RESOURCES", "New thought.md"), "w").write("a new note\n")
    check("a new note makes the registry stale", not REG.fresh(root, cfg))
    REG.write(root, cfg)
    check("rewriting it makes it fresh again", REG.fresh(root, cfg))
    big = "\n".join(f"line number {i} with enough words in it" for i in range(30)) + "\n"
    open(os.path.join(root, "3 RESOURCES", "Whole.md"), "w").write(big + "and one more line of words here\n")
    open(os.path.join(root, "4 ARCHIVE", "Part.md"), "w").write(big)
    out = REG.build(root, cfg)
    check("a note wholly inside another is proposed as removable",
          "`4 ARCHIVE/Part.md` — 100% inside `3 RESOURCES/Whole.md`" in out)
    check("  … and its container is not also listed as 'overlapping' (one fact, seen from both sides)",
          "`3 RESOURCES/Whole.md` —" not in out.split("**Overlapping**")[1])
    shutil.copy(os.path.join(root, "4 ARCHIVE", "Part.md"), os.path.join(root, "3 RESOURCES", "Part copy.md"))
    out = REG.build(root, cfg)
    check("identical copies are listed once, as copies — not each as 'inside' the other",
          "`3 RESOURCES/Part copy.md` = `4 ARCHIVE/Part.md`" in out and "`4 ARCHIVE/Part.md` — 100% inside" not in out)
    check("the registry's title comes from the config, not from any one vault",
          out.split("\n# ", 1)[1].startswith("REGISTRY\n"))
finally:
    shutil.rmtree(s, ignore_errors=True)

print("== verbatim merges: their links are record, not navigation ==")
s, root, cfg = K._scratch()
try:
    open(os.path.join(root, "3 RESOURCES", "Old record.md"), "w").write("see [[a-dead-memory-slug]]\n")
    check("a dead link in an ordinary note is RED", bool(K.r_links(K.Vault(root, cfg))))
    cfg.setdefault("accepted", {})["verbatim"] = ["3 RESOURCES/Old record.md"]
    check("the same link inside a note accepted as verbatim is not", not K.r_links(K.Vault(root, cfg)))
finally:
    shutil.rmtree(s, ignore_errors=True)

print("== live trash mechanism: gio trash + undo round-trip on a scratch file (not the vault) ==")
probe = os.path.join(C.HERE, f"_probe-{os.getpid()}")
try:
    pv = os.path.join(probe, "vault")
    os.makedirs(pv)
    open(os.path.join(pv, "a.txt"), "w").write("probe twin\n")
    open(os.path.join(pv, "b.txt"), "w").write("probe twin\n")
    before = manifest(pv)
    ops = write_plan(os.path.join(probe, "plan.tsv"), ["trash\ta.txt"])
    undo = os.path.join(probe, "undo.tsv")
    ok, f, _ = C.apply_plan(pv, ops, before, C._gio_trash, undo)
    check("gio trash applied and verified GREEN", ok)
    where = [r[3] for r in log_rows(undo) if r[1] == "trashed-to"]
    found = bool(where) and bool(where[0]) and os.path.exists(where[0])
    check("the file was found in the desktop trash via its .trashinfo", found)
    info = os.path.join(C.TRASH, "info", os.path.basename(where[0]) + ".trashinfo") if found else ""
    check("  and its .trashinfo exists before the undo", found and os.path.exists(info))
    notes = C.undo_plan(pv, undo)
    check("undo restored it from the desktop trash", notes == [] and C._verify(pv, before)[0])
    check("  and removed its .trashinfo (no ghost left in the trash)", found and not os.path.exists(info))
finally:
    shutil.rmtree(probe, ignore_errors=True)

print()
print(f"RESULT: {'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
