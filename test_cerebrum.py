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

print("== semantics: a note cut into parts loses nothing; compose places each part once, verbatim ==")
import semantics as SEM
note = (b"Intro line, no heading\n"
        b"# Alpha\nalpha text\n```\n# not a heading inside a fence\n```\n"
        b"## Beta\nbeta text\n"
        b"# Gamma\ngamma text\n")
parts = SEM.segment(note)
check("the parts rejoin to the note, byte for byte", b"".join(p["bytes"] for p in parts) == note)
check("a '#' line inside a code fence is not a cut", [p["heading"] for p in parts] == ["", "Alpha", "Beta", "Gamma"])
check("ranges read as parts, order kept", SEM.parse_ids("P001-P003,P000") == ["P001", "P002", "P003", "P000"])
tmp7 = tempfile.mkdtemp(prefix="cerebrum-compose-")
try:
    v = build(tmp7)
    open(os.path.join(v, "notes", "dump.md"), "wb").write(note)
    before = manifest(v)
    sha = C.sha256(os.path.join(v, "notes", "dump.md"))
    ops = write_plan(os.path.join(tmp7, "p.tsv"), [
        "partition\tnotes/dump.md\t" + sha,
        "compose\tnotes/Alpha and Gamma.md\tnotes/dump.md\tP003,P001",
        "compose\tnotes/Beta.md\tnotes/dump.md\tP002",
        "leave\tnotes/dump.md\tP000"])
    errs, exp, _ = C.simulate(v, ops, before)
    check("a complete partition passes the dry run", errs == [])
    undo7 = os.path.join(tmp7, "u.tsv")
    ok, f, _ = C.apply_plan(v, ops, before, C._scratch_trash_fn(os.path.join(tmp7, "_t")), undo7)
    check("apply → GREEN; the composed notes are the planned new files", ok)
    if not ok: print("     ", f[:4])
    ag = open(os.path.join(v, "notes", "Alpha and Gamma.md"), "rb").read()
    check("a composed note is its parts, verbatim, in the planned order — after a header naming the source (D1)",
          ag.endswith(parts[3]["bytes"] + parts[1]["bytes"]) and ag.startswith(b"*Composed verbatim from `notes/dump.md`, ")
          and b"L" in ag[:120] and ag == C.provenance("compose", "notes/dump.md", SEM.spans("P003,P001", SEM.segment(open(os.path.join(v, "notes", "dump.md"), "rb").read()))) + parts[3]["bytes"] + parts[1]["bytes"])
    check("the source is untouched", C.sha256(os.path.join(v, "notes", "dump.md")) == sha)
    def errs_for(lines):
        return C.simulate(v, write_plan(os.path.join(tmp7, "q.tsv"), lines), manifest(v))[0]
    check("a part with no place is refused (excluded middle)",
          errs_for(["partition\tnotes/dump.md\t" + sha, "compose\tnotes/X.md\tnotes/dump.md\tP001-P003"]) != [])
    check("a part placed twice is refused (non-contradiction)",
          errs_for(["partition\tnotes/dump.md\t" + sha, "compose\tnotes/X.md\tnotes/dump.md\tP000-P003",
                    "compose\tnotes/Y.md\tnotes/dump.md\tP002"]) != [])
    check("a source changed since it was segmented is refused",
          errs_for(["partition\tnotes/dump.md\t" + "0" * 64, "compose\tnotes/X.md\tnotes/dump.md\tP000-P003"]) != [])
    check("an unknown part is refused", errs_for(["compose\tnotes/X.md\tnotes/dump.md\tP009"]) != [])
    check("composing onto an existing note is refused", errs_for(["compose\tnotes/a.md\tnotes/dump.md\tP001"]) != [])
    check("undo removes exactly the composed notes; the vault equals its before-manifest",
          C.undo_plan(v, undo7) == [] and C._verify(v, before)[0])
    bm = manifest(v)
    ops = write_plan(os.path.join(tmp7, "p2.tsv"), [
        "partition\tnotes/dump.md\t" + sha,
        "extend\tnotes/b.md\tnotes/dump.md\tL2-L6",
        "compose\tnotes/Rest.md\tnotes/dump.md\tL7-L10,L1"])
    check("line ranges and extend: a complete partition by lines passes the dry run", C.simulate(v, ops, bm)[0] == [])
    b_before = open(os.path.join(v, "notes", "b.md"), "rb").read()
    undo8 = os.path.join(tmp7, "u2.tsv")
    ok, f, _ = C.apply_plan(v, ops, bm, C._scratch_trash_fn(os.path.join(tmp7, "_t2")), undo8)
    check("apply → GREEN (an extended note's new bytes are the planned ones)", ok)
    if not ok: print("     ", f[:4])
    lines = note.splitlines(keepends=True)
    b_now = open(os.path.join(v, "notes", "b.md"), "rb").read()
    check("extend keeps the old note as it was and appends the lines verbatim, after one line naming their source (D1)",
          b_now == b_before + b"\n" + C.provenance("extend", "notes/dump.md", [(2, 6)]) + b"".join(lines[1:6])
          and C.provenance("extend", "notes/dump.md", [(2, 6)]).startswith("*Appended verbatim from `notes/dump.md`, L2\u2013L6.*".encode()))
    rest = open(os.path.join(v, "notes", "Rest.md"), "rb").read()
    check("a composed note from line ranges holds them in the planned order",
          rest == C.provenance("compose", "notes/dump.md", [(7, 10), (1, 1)]) + b"".join(lines[6:10]) + lines[0]
          and rest.startswith("*Composed verbatim from `notes/dump.md`, L7\u2013L10, L1 \u2014".encode()))
    check("undo cuts the extended note back exactly and removes the composed one",
          C.undo_plan(v, undo8) == [] and open(os.path.join(v, "notes", "b.md"), "rb").read() == b_before
          and C._verify(v, bm)[0])
    check("a line placed nowhere is refused",
          C.simulate(v, write_plan(os.path.join(tmp7, "p3.tsv"), [
              "partition\tnotes/dump.md\t" + sha, "compose\tnotes/Rest.md\tnotes/dump.md\tL2-L10"]), manifest(v))[0] != [])
finally:
    shutil.rmtree(tmp7, ignore_errors=True)

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

print("== the semantic organ: scope, index controls, coverage, agreement ==")
SUNO = ("suno prompt formula verse chorus bridge tempo mood genre vocal style hook melody lyric rhythm "
        "beat producer arrangement sample layer synth bass drum mix master song track release playlist")
LIFT = ("barbell deadlift spine brace core hinge hips grip chalk plates rack squat bench press sets reps "
        "rest load progression warmup cooldown mobility stretch recovery sleep protein strength power")
SAME = ("river stone lantern harbor meadow copper violet orchard glacier canyon ember falcon marble "
        "thistle willow quartz saffron tundra beacon cedar lagoon pepper sparrow velvet cobalt ridge "
        "juniper harvest compass garnet mosaic")
def organ_vault(root):
    v = os.path.join(root, "vault")
    files = {
        "# INBOX/Mixed.md": f"# Suno\n{SUNO}\n\n# Deadlift\n{LIFT}\n",
        "# INBOX/Two.md": f"# Alpha\nalpha {SAME}\n\n# Beta\nbeta {LIFT}\n",
        "# INBOX/Secret.md": "password: never read this\n",
        "3 RESOURCES/Songs.md": f"# Songwriting\n{SUNO} lyrics\n\n# Hooks\n{SUNO} hooks\n",
        "3 RESOURCES/Training.md": f"# Lifting\n{LIFT} lifting\n\n# Program\n{LIFT} program\n",
        "2 AREAS/Copy A.md": f"# Same\n{SAME}\n",
        "4 ARCHIVE/Copy B.md": f"# Same\n{SAME}\n",
        "2 AREAS/Aph.md": ("# Sayings\n- the rejection of pleasure is the pleasure of rejection\n"
                           "- a distinct saying about the sea and the sky\n"
                           "* the rejection of pleasure is the pleasure of rejection\n"
                           "- a third line with enough words\n"
                           "-   the rejection of pleasure is the pleasure of rejection\n"),
        "AGENT MEMORY/mem.md": "frozen memory\n"}
    for p, t in files.items():
        os.makedirs(os.path.dirname(os.path.join(v, p)), exist_ok=True)
        open(os.path.join(v, p), "w").write(t)
    cfg = {"para": list(C.PARA), "frozen": [{"root": "AGENT MEMORY", "pins": []}],
           "accepted": {"sensitive": ["# INBOX/Secret.md"]}}
    return v, cfg
def rec(v, note, lines="*", op="keep", dest="", relation="", with_=None):
    return {"note": note, "note_sha": C.sha256(os.path.join(v, note)), "lines": lines, "op": op, "dest": dest,
            "relation": relation, "with": with_ or [], "subject": "s", "use": "u", "why": "test"}
def keep_all(v, cfg, **override):
    return [override.get(n) or rec(v, n) for n in SEM.scope(v, cfg)]
tmp8 = tempfile.mkdtemp(prefix="cerebrum-organ-")
try:
    v, ocfg = organ_vault(tmp8)
    sc = SEM.scope(v, ocfg)
    check("scope: the sensitive note is never read, the frozen folder never", "# INBOX/Secret.md" not in sc
          and not any(p.startswith("AGENT MEMORY") for p in sc) and "3 RESOURCES/Songs.md" in sc)
    calls = []
    counting = lambda t, c=None: (calls.append(len(t)), SEM.fake_embed(t))[1]
    cache = os.path.join(tmp8, "cache.json")
    ix = SEM.index(v, ocfg, embed=counting, cache_path=cache, probe=False)   # a word-counter cannot pass a paraphrase probe
    check("index control: the identical copies find each other", ix["control"] and ix["control"][0]["found"] is True)
    check("  … and the index is believed", SEM.index_ok(ix))
    check("the note holding two subjects ranks least coherent", ix["within"][0]["note"] == "# INBOX/Mixed.md")
    mixed_suno = [x for x in ix["across"] if {x["a"]["note"], x["b"]["note"]} >= {"# INBOX/Mixed.md", "3 RESOURCES/Songs.md"}]
    check("a section on one subject finds its subject in another note", bool(mixed_suno))
    check("repeats inside a note: one line, two extra copies (list markers and spacing ignored)",
          ix["repeats"].get("2 AREAS/Aph.md", {}).get("extra_copies") == 2)
    n_calls = len(calls)
    SEM.index(v, ocfg, embed=counting, cache_path=cache, probe=False)
    check("the cache: an unchanged vault is not embedded again", n_calls > 0 and len(calls) == n_calls)
    blind = SEM.index(v, ocfg, embed=lambda t, c=None: [[1.0] * 8 for _ in t], probe=True)
    check("an embedder that cannot tell texts apart fails the control AND the probe (red paths)",
          not SEM.index_ok(blind) and blind["control"][0]["found"] is False and blind["probe"]["ok"] is False)

    eff, probs = SEM.effective(v, ocfg, keep_all(v, ocfg))
    check("coverage: a reading that places every line once is clean", probs == [] and len(eff) == len(sc))
    def probs_for(recs):
        return SEM.effective(v, ocfg, recs)[1]
    check("coverage red: a note nobody read", any("not read" in p for p in probs_for(keep_all(v, ocfg)[1:])))
    twice = keep_all(v, ocfg) + [rec(v, "# INBOX/Mixed.md", "P000")] + [rec(v, "# INBOX/Mixed.md", "L2")]
    check("coverage red: a line placed twice", any("placed twice" in p for p in probs_for(
        [r for r in twice if not (r["note"] == "# INBOX/Mixed.md" and r["lines"] == "*")])))
    stale = rec(v, "# INBOX/Mixed.md"); stale["note_sha"] = "0" * 64
    check("coverage red: a record for an older version of its note",
          any("changed since it was read" in p for p in probs_for(keep_all(v, ocfg, **{"# INBOX/Mixed.md": stale}))))
    check("coverage red: an unknown part", any("no such part" in p for p in probs_for(
        keep_all(v, ocfg, **{"# INBOX/Mixed.md": rec(v, "# INBOX/Mixed.md", "P009")}))))
    check("coverage red: lines nobody placed (no '*')", any("not read — the first" in p for p in probs_for(
        keep_all(v, ocfg, **{"# INBOX/Mixed.md": rec(v, "# INBOX/Mixed.md", "P001")}))))
    check("a merge must name ≡ or = — a same-structure pair is a link (PT 9.2)", any("merge needs relation" in p for p in probs_for(
        keep_all(v, ocfg, **{"# INBOX/Mixed.md": rec(v, "# INBOX/Mixed.md", op="merge", dest="3 RESOURCES/Songs.md", relation="≅")}))))
    split = lambda a, b: [rec(v, "# INBOX/Two.md", "P000", dest=a), rec(v, "# INBOX/Two.md", "P001", dest=b)]
    base = [r for r in keep_all(v, ocfg) if r["note"] != "# INBOX/Two.md"]
    eA, pA = SEM.effective(v, ocfg, base + split("3 RESOURCES/Alpha.md", "2 AREAS/Beta.md"))
    eB, pB = SEM.effective(v, ocfg, base + split("3 RESOURCES/First.md", "2 AREAS/Second.md"))
    check("  (the split readings are clean — the agreement below looked at the split)", pA == pB == [] and "# INBOX/Two.md" in eA)
    check("agreement: a new note is known by what it holds, not by its name", SEM.agree(v, eA, eB)[1] == [])
    eC = SEM.effective(v, ocfg, base + split("3 RESOURCES/Alpha.md", "3 RESOURCES/Beta.md"))[0]
    runs = SEM.agree(v, eA, eC)[1]
    check("agreement red: the readers send lines to different places → a dispute for the keeper",
          len(runs) == 1 and runs[0]["note"] == "# INBOX/Two.md")
finally:
    shutil.rmtree(tmp8, ignore_errors=True)

print("== the organ: agreed readings → a plan the mover proves → lossless apply → convergence → undo ==")
tmp9 = tempfile.mkdtemp(prefix="cerebrum-organ2-")
try:
    v, ocfg = organ_vault(tmp9)
    def reading(two_a, two_b):
        r = [x for x in keep_all(v, ocfg) if x["note"] not in
             ("# INBOX/Mixed.md", "# INBOX/Two.md", "2 AREAS/Copy A.md", "2 AREAS/Aph.md")]
        return r + [rec(v, "# INBOX/Mixed.md", "P000", "merge", "3 RESOURCES/Songs.md", "="),
                    rec(v, "# INBOX/Mixed.md", "P001", dest="3 RESOURCES/Training.md"),
                    rec(v, "# INBOX/Two.md", "P000", dest=two_a), rec(v, "# INBOX/Two.md", "P001", dest=two_b),
                    rec(v, "2 AREAS/Copy A.md", dest="3 RESOURCES/Copy A.md"), rec(v, "2 AREAS/Aph.md", op="dedupe")]
    eA, pA = SEM.effective(v, ocfg, reading("3 RESOURCES/Alpha.md", "2 AREAS/Beta.md"))
    eB, pB = SEM.effective(v, ocfg, reading("3 RESOURCES/First.md", "2 AREAS/Second.md"))
    agreed, runs = SEM.agree(v, eA, eB)
    check("two clean readings that agree", pA == pB == [] and runs == [] and len(agreed) == len(SEM.scope(v, ocfg)))
    plan, rep = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")
    check("the plan splits the two mixed notes, moves one, dedupes one",
          set(rep["split"]) == {"# INBOX/Mixed.md", "# INBOX/Two.md"} and rep["dedupe"] == ["2 AREAS/Aph.md"]
          and rep["moved"] == {"2 AREAS/Copy A.md": "3 RESOURCES/Copy A.md"})
    ops = write_plan(os.path.join(tmp9, "plan.tsv"), plan)
    before = manifest(v)
    errs, exp, _ = C.simulate(v, ops, before)
    check("the organ's plan passes the mover's dry run (every line of a split note placed once)", errs == [])
    if errs: print("     ", errs[:4], plan)
    orig = {n: open(os.path.join(v, n), "rb").read() for n in ("# INBOX/Mixed.md", "# INBOX/Two.md", "2 AREAS/Aph.md")}
    undo9 = os.path.join(tmp9, "undo.tsv")
    ok, f, _ = C.apply_plan(v, ops, before, C._scratch_trash_fn(os.path.join(tmp9, "_t")), undo9)
    check("apply → GREEN", ok)
    if not ok: print("     ", f[:6])
    rd = lambda p: open(os.path.join(v, p), "rb").read()
    mixed, two = orig["# INBOX/Mixed.md"].splitlines(keepends=True), orig["# INBOX/Two.md"].splitlines(keepends=True)
    check("each split note is kept whole in the archive",
          rd("4 ARCHIVE/Mixed — original (2026-01-01).md") == orig["# INBOX/Mixed.md"]
          and rd("4 ARCHIVE/Two — original (2026-01-01).md") == orig["# INBOX/Two.md"])
    check("its pieces land verbatim: gathered into existing notes, composed into new ones",
          rd("3 RESOURCES/Songs.md").endswith(b"".join(mixed[0:3])) and rd("3 RESOURCES/Training.md").endswith(b"".join(mixed[3:]))
          and rd("3 RESOURCES/Alpha.md").endswith(b"".join(two[0:3])) and rd("2 AREAS/Beta.md").endswith(b"".join(two[3:]))
          and rd("3 RESOURCES/Alpha.md").startswith(b"*Composed verbatim from `4 ARCHIVE/Two"))
    check("dedupe: one copy of the repeated line stays; the original, all three copies, is archived",
          rd("2 AREAS/Aph.md").decode().count("pleasure of rejection") == 1
          and rd("4 ARCHIVE/Aph — before dedupe (2026-01-01).md") == orig["2 AREAS/Aph.md"])
    touched = SEM.converge_targets(ocfg, exp)
    reread = lambda redo=None: SEM.effective(
        v, ocfg, [redo if redo and t == redo["note"] else rec(v, t) for t in touched], notes=touched)[0]
    check("convergence: everything the plan made or moved, read again, needs nothing more",
          "3 RESOURCES/Alpha.md" in touched and SEM.converge(v, touched, reread(), reread()) == [])
    again = rec(v, "3 RESOURCES/Alpha.md", dest="2 AREAS/Beta.md")
    check("convergence red: a second pass that still moves lines → the pass did not close",
          SEM.converge(v, touched, reread(again), reread()) != [])
    check("undo restores the vault exactly", C.undo_plan(v, undo9) == [] and C._verify(v, before)[0])
    eC = SEM.effective(v, ocfg, reading("3 RESOURCES/Alpha.md", "3 RESOURCES/Beta.md"))[0]
    agreed2, runs2 = SEM.agree(v, eA, eC)
    plan2, rep2 = SEM.propose(v, ocfg, agreed2, runs2, today="2026-01-01")
    check("a dispute blocks its note, and every new note it would leave half-made — nothing else",
          "# INBOX/Two.md" in rep2["blocked"] and "# INBOX/Mixed.md" in rep2["split"]
          and not any("Two" in x or "Alpha" in x or "Beta" in x for x in plan2))
finally:
    shutil.rmtree(tmp9, ignore_errors=True)

print("== synthesize: every paragraph cites a source part, by hash; dedupe keeps only what repeats out ==")
import hashlib
tmp10 = tempfile.mkdtemp(prefix="cerebrum-synth-")
try:
    v, ocfg = organ_vault(tmp10)
    songs = open(os.path.join(v, "3 RESOURCES", "Songs.md"), "rb").read()
    sha12 = hashlib.sha256(SEM.segment(songs)[0]["bytes"]).hexdigest()[:12]
    n_draft = [0]
    def draft(body, sources=None):
        n_draft[0] += 1
        path = os.path.join(tmp10, f"draft-{n_draft[0]}.md")
        src = sources if sources is not None else f"- [S1] `3 RESOURCES/Songs.md` P000 sha:{sha12} — “Songwriting”\n"
        open(path, "w").write(f"# Songs, gathered\n\n{body}\n\n## Sources\n{src}")
        return path
    before = manifest(v)
    good = draft("A song prompt names its verse, chorus and tempo. [S1]")
    ops = write_plan(os.path.join(tmp10, "p.tsv"), [f"synthesize\t3 RESOURCES/Songs — synthesis.md\t{good}"])
    check("a synthesis whose every paragraph cites an unchanged source passes the dry run", C.simulate(v, ops, before)[0] == [])
    undo10 = os.path.join(tmp10, "u.tsv")
    ok, f, _ = C.apply_plan(v, ops, before, C._scratch_trash_fn(os.path.join(tmp10, "_t")), undo10)
    check("apply → GREEN; the note is the draft byte for byte; the source untouched",
          ok and open(os.path.join(v, "3 RESOURCES", "Songs — synthesis.md"), "rb").read() == open(good, "rb").read()
          and open(os.path.join(v, "3 RESOURCES", "Songs.md"), "rb").read() == songs)
    check("undo removes exactly the synthesis", C.undo_plan(v, undo10) == [] and C._verify(v, before)[0])
    refused = lambda path: C.simulate(v, write_plan(os.path.join(tmp10, "q.tsv"), [f"synthesize\t3 RESOURCES/S.md\t{path}"]), before)[0]
    check("traceability red: a paragraph that cites nothing is a new claim", any("cites nothing" in e for e in refused(
        draft("A cited claim. [S1]\n\nAn uncited claim, a new one."))))
    check("traceability red: a source changed since it was cited", any("changed since it was cited" in e for e in refused(
        draft("A claim. [S1]", f"- [S1] `3 RESOURCES/Songs.md` P000 sha:{'0' * 12}\n"))))
    check("traceability red: a cited source that is not listed", any("not listed" in e for e in refused(draft("A claim. [S2]"))))
    check("traceability red: a part that does not exist", any("has no part" in e for e in refused(
        draft("A claim. [S1]", f"- [S1] `3 RESOURCES/Songs.md` P009 sha:{sha12}\n"))))
    check("dedupe with nothing repeated is refused", C.simulate(v, write_plan(os.path.join(tmp10, "d.tsv"),
          ["dedupe\t3 RESOURCES/Songs.md\t4 ARCHIVE/Songs — before dedupe.md"]), before)[0] != [])
    check("dedupe onto an existing note is refused (no clobber)", C.simulate(v, write_plan(os.path.join(tmp10, "e.tsv"),
          ["dedupe\t2 AREAS/Aph.md\t4 ARCHIVE/Copy B.md"]), before)[0] != [])
    new_b, dropped = SEM.dedupe_bytes(open(os.path.join(v, "2 AREAS", "Aph.md"), "rb").read())
    check("dedupe drops only the later copies — every distinct line survives, once", len(dropped) == 2
          and new_b.decode().count("pleasure of rejection") == 1 and "sea and the sky" in new_b.decode())
finally:
    shutil.rmtree(tmp10, ignore_errors=True)

print("== a reader's slice: its units, every line once, nothing outside ==")
tmp11 = tempfile.mkdtemp(prefix="cerebrum-slice-")
try:
    v, ocfg = organ_vault(tmp11)
    units = [("# INBOX/Mixed.md", "L1-L3"), ("3 RESOURCES/Songs.md", "*")]
    sp = lambda recs: SEM.slice_problems(v, ocfg, recs, units)
    good = [rec(v, "# INBOX/Mixed.md", "L1-L3"), rec(v, "3 RESOURCES/Songs.md")]
    check("a slice whose units are each placed once is clean", sp(good) == [])
    check("slice red: a line of the unit left out", any("not read" in p for p in sp(
        [rec(v, "# INBOX/Mixed.md", "L1-L2"), rec(v, "3 RESOURCES/Songs.md")])))
    check("slice red: a record reaching past the unit", any("outside this slice" in p for p in sp(
        [rec(v, "# INBOX/Mixed.md", "L1-L5"), rec(v, "3 RESOURCES/Songs.md")])))
    check("slice red: '*' where the slice holds only part of the note", any("only part of the note" in p for p in sp(
        [rec(v, "# INBOX/Mixed.md"), rec(v, "3 RESOURCES/Songs.md")])))
    check("slice red: a note that is not in the slice", any("not in this slice" in p for p in sp(good + [rec(v, "# INBOX/Two.md")])))
finally:
    shutil.rmtree(tmp11, ignore_errors=True)

print("== a second read of part of the vault: it must cover every change the first proposes ==")
tmp12 = tempfile.mkdtemp(prefix="cerebrum-second-")
try:
    v, ocfg = organ_vault(tmp12)
    recsA = [r for r in keep_all(v, ocfg) if r["note"] != "# INBOX/Mixed.md"] + [
        rec(v, "# INBOX/Mixed.md", "P000", "merge", "3 RESOURCES/Songs.md", "="),
        rec(v, "# INBOX/Mixed.md", "P001", dest="3 RESOURCES/Training.md")]
    effA = SEM.effective(v, ocfg, recsA)[0]
    must, sampled = SEM.second_read_set(v, ocfg, effA, sample=0.5, seed=1)
    check("the notes the first read would change, and the notes it would put lines into, must be read again",
          must == ["# INBOX/Mixed.md", "3 RESOURCES/Songs.md", "3 RESOURCES/Training.md"])
    check("a seeded sample of its keeps comes from the rest — the same every time", bool(sampled)
          and not set(sampled) & set(must) and sampled == SEM.second_read_set(v, ocfg, effA, sample=0.5, seed=1)[1])
    effB = SEM.effective(v, ocfg, [r for r in recsA if r["note"] in must], notes=must)[0]
    check("a second read covering every change passes the gate", SEM.second_read_missing(v, ocfg, effA, effB) == [])
    effB2 = SEM.effective(v, ocfg, [r for r in recsA if r["note"] in must[:2]], notes=must[:2])[0]
    check("gate red: a second read that skipped a note the first would put lines into",
          SEM.second_read_missing(v, ocfg, effA, effB2) == ["3 RESOURCES/Training.md"])
    agreed, runs = SEM.agree(v, effA, effB)
    plan, _ = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")
    check("from a partial second read, the agreed change still makes a plan the mover proves",
          bool(plan) and C.simulate(v, write_plan(os.path.join(tmp12, "p.tsv"), plan), manifest(v))[0] == [])
    open(os.path.join(v, "3 RESOURCES", "Big.md"), "w").write(
        "# A\n" + ("word " * 60 + "\n") * 5 + "# B\n" + ("more " * 60 + "\n") * 5)
    sl = SEM.make_slices(v, ["3 RESOURCES/Big.md", "3 RESOURCES/Songs.md"], budget=700)
    got = sorted(l for s in sl for n, spec in s["units"] if n == "3 RESOURCES/Big.md"
                 for a, b in [[int(x[1:]) for x in spec.split("-")]] for l in range(a, b + 1))
    check("slices: a note over the budget is cut into line ranges that cover it exactly once; a small one stays whole",
          got == list(range(1, 13)) and ["3 RESOURCES/Songs.md", "*"] in [u for s in sl for u in s["units"]])
finally:
    shutil.rmtree(tmp12, ignore_errors=True)

print("== WHERE a line goes and WHAT ELSE is done to it are two facts: an act dispute blocks nothing ==")
tmp13 = tempfile.mkdtemp(prefix="cerebrum-acts-")
try:
    v, ocfg = organ_vault(tmp13)
    base = [r for r in keep_all(v, ocfg) if r["note"] != "# INBOX/Two.md"]
    to_beta = rec(v, "# INBOX/Two.md", "P001", dest="2 AREAS/Beta.md")
    eB = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"), to_beta])[0]
    eA = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000", op="mark"), to_beta])[0]
    agreed, runs, acts = SEM.agree_detail(v, eA, eB)
    check("the readers agree where every line goes and differ only on marking P000",
          runs == [] and len(acts) == 1 and acts[0]["note"] == "# INBOX/Two.md")
    plan, rep = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")
    check("so the split still runs, and the disputed mark is not done",
          any("2 AREAS/Beta.md" in x for x in plan) and not rep["acts"].get("mark")
          and C.simulate(v, write_plan(os.path.join(tmp13, "p.tsv"), plan), manifest(v))[0] == [])
    eS = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000", op="synthesize", dest="3 RESOURCES/Syn.md"), to_beta])[0]
    agreed2, runs2, acts2 = SEM.agree_detail(v, eS, eB)
    plan2 = SEM.propose(v, ocfg, agreed2, runs2, today="2026-01-01")[0]
    check("a disputed synthesis leaves its lines where they are — they never follow the synthesis note",
          runs2 == [] and len(acts2) == 1 and not any("Syn.md" in x for x in plan2)
          and C.simulate(v, write_plan(os.path.join(tmp13, "q.tsv"), plan2), manifest(v))[0] == [])
    eP = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000", dest="3 RESOURCES/Elsewhere.md"), to_beta])[0]
    check("a dispute on WHERE still blocks the note", SEM.agree_detail(v, eP, eB)[1] != [])
finally:
    shutil.rmtree(tmp13, ignore_errors=True)

print("== the keeper's ruling: a disputed note follows the reader the person chose — and nothing else ==")
tmp13b = tempfile.mkdtemp(prefix="cerebrum-ruling-")
try:
    v, ocfg = organ_vault(tmp13b)
    base = [r for r in keep_all(v, ocfg) if r["note"] != "# INBOX/Two.md"]
    to_beta = rec(v, "# INBOX/Two.md", "P001", dest="2 AREAS/Beta.md")
    eB = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"), to_beta])[0]
    eP = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000", dest="3 RESOURCES/Elsewhere.md"), to_beta])[0]
    check("before the ruling the WHERE dispute blocks the note",
          SEM.agree_detail(v, eP, eB)[1] != [] and not any("Two" in x for x in
              SEM.propose(v, ocfg, *SEM.agree_detail(v, eP, eB)[:2], today="2026-01-01")[0]))
    rl = [{"note": "# INBOX/Two.md", "reader": "A", "why": "the keeper says P000 is a resource", "_at": "r:1"}]
    A2, B2, ruled, probs = SEM.rule(v, eP, eB, rl)
    agreed, runs, _ = SEM.agree_detail(v, A2, B2)
    plan = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")[0]
    check("ruled for A: the dispute is gone and A's placement is in the plan",
          probs == [] and len(ruled) == 1 and runs == [] and any("Elsewhere.md" in x for x in plan)
          and C.simulate(v, write_plan(os.path.join(tmp13b, "p.tsv"), plan), manifest(v))[0] == [])
    A3, B3, _, probs3 = SEM.rule(v, eP, eB, [dict(rl[0], reader="B")])
    plan3 = SEM.propose(v, ocfg, *SEM.agree_detail(v, A3, B3)[:2], today="2026-01-01")[0]
    check("ruled for B: B's placement instead, and nothing of A's",
          probs3 == [] and not any("Elsewhere.md" in x for x in plan3) and any("Beta.md" in x for x in plan3))
    undisputed = next(r["note"] for r in base)
    for bad, label in ((dict(rl[0], note=undisputed), "a ruling on a note the readers did not dispute"),
                       (dict(rl[0], reader="C"), "a ruling for a reader that does not exist"),
                       (dict(rl[0], why=""), "a ruling without its reason"),
                       (dict(rl[0], note="3 RESOURCES/Nowhere.md"), "a ruling on a note not read by both")):
        A4, B4, ruled4, probs4 = SEM.rule(v, eP, eB, [rl[0], bad])
        check(f"{label} is refused — and then NOTHING is ruled",
              len(probs4) == 1 and ruled4 == [] and A4 is eP and B4 is eB)
    rp = os.path.join(tmp13b, "rulings.tsv")
    open(rp, "w", encoding="utf-8").write("# a comment\n\n# INBOX/Two.md\tA\tthe keeper says so\n")
    check("a rulings file loads: comments and blank lines skipped, fields stripped",
          [(x["note"], x["reader"], x["why"]) for x in SEM.load_rulings(rp)] == [("# INBOX/Two.md", "A", "the keeper says so")])
finally:
    shutil.rmtree(tmp13b, ignore_errors=True)

print("== a path vacated and filled again: a split note's own lines recomposed where it was ==")
tmp14 = tempfile.mkdtemp(prefix="cerebrum-reuse-")
try:
    v, ocfg = organ_vault(tmp14)
    base = [r for r in keep_all(v, ocfg) if r["note"] != "# INBOX/Two.md"]
    eX = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"),
                                        rec(v, "# INBOX/Two.md", "P001", dest="2 AREAS/Beta.md")])[0]
    agreed, runs, _ = SEM.agree_detail(v, eX, eX)
    plan = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")[0]
    pp = os.path.join(tmp14, "p.tsv")
    ops = write_plan(pp, plan)
    before = manifest(v)
    errs, exp, _ = C.simulate(v, ops, before)
    arc = exp["moves"].get("# INBOX/Two.md", "")
    check("the plan archives the note and recomposes its own lines at the same path",
          errs == [] and arc.startswith("4 ARCHIVE/") and "# INBOX/Two.md" in exp["added"])
    two = open(os.path.join(v, "# INBOX", "Two.md"), "rb").read()
    undo14 = os.path.join(tmp14, "u.tsv")
    ok, f, _ = C.apply_plan(v, ops, before, C._scratch_trash_fn(os.path.join(tmp14, "_t")), undo14)
    check("apply → verify GREEN: the old bytes held to the move, the path to the new note", ok)
    if not ok: print("     ", f[:4])
    vv = lambda w: C._verify(w, before, exp["moves"], exp["removed"], exp["hashes"], expected_added=exp["added"])[0]
    t1 = copy(v, os.path.join(tmp14, "t1")); open(os.path.join(t1, "# INBOX", "Two.md"), "ab").write(b"x")
    check("red: the recomposed note tampered", not vv(t1))
    t2 = copy(v, os.path.join(tmp14, "t2")); os.remove(os.path.join(t2, arc))
    check("red: the archived original lost", not vv(t2))
    t3 = copy(v, os.path.join(tmp14, "t3")); open(os.path.join(t3, arc), "ab").write(b"x")
    check("red: the archived original altered", not vv(t3))
    check("undo restores the note exactly", C.undo_plan(v, undo14) == [] and C._verify(v, before)[0]
          and open(os.path.join(v, "# INBOX", "Two.md"), "rb").read() == two)
    check("and the rehearsal of that plan passes end to end", R.rehearse(pp, v, snapshot=False, say=quiet))
finally:
    shutil.rmtree(tmp14, ignore_errors=True)

print("== embedding windows: a character budget, never a word count; no word lost ==")
dense = " ".join(f"https://example.com/video/BV{i:08d}?spm_id_from=333.337.search-card.all.click" for i in range(300))
ws = SEM.windows(dense + " " + "x" * 4000)
check("every window fits the budget, even a 4,000-character token", all(len(w) <= SEM.WINDOW_CHARS for w in ws))
check("rejoined, the windows are every word of the text", "".join(" ".join(ws).split()) == "".join((dense + " " + "x" * 4000).split()))
check("300 words of links make several windows, not one", len(SEM.windows(dense)) > 1)

print("== Law Revision I: six new rules, each red on its planted defect and green on the control ==")
import checks as CK
bad = CK.selftest()
for name in ["one inbox, one archive", "three levels, no deeper", "no empty folder", "the law describes the tool it governs",
             "a project is a goal with a deadline", "every accepted exception still applies"]:
    check(f"rule '{name}' exists and proves itself", any(r.name == name for r in CK.RULES) and not any(b.startswith(name) for b in bad))
check("the selftest is clean for every rule", bad == [])
check("the law's two tables are read where they are written: the scratch law names every rule and every op",
      (lambda s: (CK.law_rule_names(open(os.path.join(s[1], s[2]["law"])).read()) == {r.name for r in CK.RULES}
                  and CK.law_plan_ops(open(os.path.join(s[1], s[2]["law"])).read()) == set(C.ARITY) | {"merge"}))(CK._scratch()))

print("== pass (D2): loss is counted from the manifest's own rows — zero unchanged, one per file gone ==")
tmp15 = tempfile.mkdtemp(prefix="cerebrum-pass-")
try:
    v = os.path.join(tmp15, "vault"); os.makedirs(os.path.join(v, "3 RESOURCES")); os.makedirs(os.path.join(v, "FROZEN"))
    open(os.path.join(v, "3 RESOURCES", "a.md"), "w").write("a\n"); open(os.path.join(v, "FROZEN", "f.md"), "w").write("f\n")
    mp = os.path.join(tmp15, "manifest.tsv")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write("# a comment\n")
        for p in sorted(C.rels(v)):
            fh.write(f"{C.sha256(os.path.join(v, p))}\t{os.path.getsize(os.path.join(v, p))}\t{p}\n")
    frozen = lambda p: p.startswith("FROZEN/")
    check("an unchanged vault has loss 0", C.loss_since(v, mp, frozen) == [])
    os.rename(os.path.join(v, "3 RESOURCES", "a.md"), os.path.join(v, "3 RESOURCES", "renamed.md"))
    check("a moved or renamed file is not a loss — its bytes are still in the vault", C.loss_since(v, mp, frozen) == [])
    os.rename(os.path.join(v, "3 RESOURCES", "renamed.md"), os.path.join(v, "3 RESOURCES", "a.md"))
    open(os.path.join(v, "3 RESOURCES", "a.md"), "a").write("edited\n")
    check("a file edited in place is not a loss — its path is still there", C.loss_since(v, mp, frozen) == [])
    os.remove(os.path.join(v, "3 RESOURCES", "a.md"))
    check("a removed movable file is one loss, named", C.loss_since(v, mp, frozen) == ["3 RESOURCES/a.md"])
    os.remove(os.path.join(v, "FROZEN", "f.md"))
    check("a file gone inside the frozen register is not the pass's loss", C.loss_since(v, mp, frozen) == ["3 RESOURCES/a.md"])
finally:
    shutil.rmtree(tmp15, ignore_errors=True)

print("== place: a shortlist by meaning that proves it looked, says how far to trust itself, and can say no home ==")
tmp16 = tempfile.mkdtemp(prefix="cerebrum-place-")
try:
    v, ocfg = organ_vault(tmp16)
    r = SEM.place(v, ocfg, f"{SUNO} a new verse about the same songs", SEM.fake_embed, exclude=["# INBOX/Mixed.md"])
    check("a text about songs shortlists the songs note first, at its own section, and leans to its folder",
          r["verdict"] == "SHORTLIST" and r["notes"][0]["note"] == "3 RESOURCES/Songs.md"
          and r["notes"][0]["section"]["heading"] == "Songwriting" and r["lean"] == "3 RESOURCES")
    check("the lifting note ranks below the songs note for it", [n["note"] for n in r["notes"]].index("3 RESOURCES/Training.md") > 0)
    check("the archive is never a target: its copy is absent though it holds the same words",
          all(not n["note"].startswith("4 ARCHIVE/") for n in SEM.place(v, ocfg, SAME, SEM.fake_embed)["notes"]))
    r2 = SEM.place(v, ocfg, f"{SUNO} a new verse", SEM.fake_embed, exclude=["# INBOX/Mixed.md", "3 RESOURCES/Songs.md"])
    check("an excluded note never appears — the text's own note cannot find itself",
          all(n["note"] != "3 RESOURCES/Songs.md" for n in r2["notes"]))
    check("the control passes: a lifted section ranks its note first at 1.0, and not when excluded",
          SEM.place_control(v, ocfg, SEM.fake_embed) == [])
    real = SEM.place
    SEM.place = lambda *a, **k: {"verdict": "NO HOME", "notes": [], "folders": [], "lean": None}
    try:
        check("a placement that never looks fails the control", SEM.place_control(v, ocfg, SEM.fake_embed) != [])
    finally:
        SEM.place = real
    cal = SEM.place_calibrate(v, ocfg, SEM.fake_embed, n=10)
    check("calibration measures on this vault: trials run, both counts bounded by them",
          cal["trials"] > 0 and 0 <= cal["same_folder_top1"] <= cal["trials"] and 0 <= cal["own_note_wins"] <= cal["trials"])
    check("empty text is EMPTY, not a home", SEM.place(v, ocfg, "   ", SEM.fake_embed)["verdict"] == "EMPTY")
    md = SEM.place_md(r, "test", cal)
    check("the report names the verdict, the nearest note, its section, and how far to trust the list",
          "SHORTLIST" in md and "Songs.md" in md and "Songwriting" in md and "How far to trust" in md)
finally:
    shutil.rmtree(tmp16, ignore_errors=True)

print("== merge: a copy or the same claims sends nothing — the source is archived, the destination untouched ==")
tmp17 = tempfile.mkdtemp(prefix="cerebrum-merge-")
try:
    v, ocfg = organ_vault(tmp17)
    base = [r for r in keep_all(v, ocfg) if r["note"] != "2 AREAS/Copy A.md"]
    e = SEM.effective(v, ocfg, base + [rec(v, "2 AREAS/Copy A.md", "*", op="merge", dest="4 ARCHIVE/Copy B.md", relation="≡")])[0]
    agreed, runs, _ = SEM.agree_detail(v, e, e)
    plan = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")[0]
    b_before = open(os.path.join(v, "4 ARCHIVE", "Copy B.md"), "rb").read()
    ops = write_plan(os.path.join(tmp17, "p.tsv"), plan)
    errs = C.simulate(v, ops, manifest(v))[0]
    check("≡ into its twin: the copy is archived whole and its lines LEFT there; nothing is appended anywhere",
          errs == [] and any(l.startswith("mv\t2 AREAS/Copy A.md\t4 ARCHIVE/Copy A — original") for l in plan)
          and any(l.startswith("leave\t") for l in plan) and not any(l.startswith(("extend\t", "compose\t")) for l in plan))
    ok, f, _ = C.apply_plan(v, ops, manifest(v), C._scratch_trash_fn(os.path.join(tmp17, "_t")), os.path.join(tmp17, "u.tsv"))
    check("applied: the destination's bytes are exactly what they were",
          ok and open(os.path.join(v, "4 ARCHIVE", "Copy B.md"), "rb").read() == b_before and not os.path.exists(os.path.join(v, "2 AREAS", "Copy A.md")))
    v, ocfg = organ_vault(tempfile.mkdtemp(prefix="cerebrum-merge2-", dir=tmp17))
    base = [r for r in keep_all(v, ocfg) if r["note"] != "3 RESOURCES/Songs.md"]
    e = SEM.effective(v, ocfg, base + [rec(v, "3 RESOURCES/Songs.md", "P000"),
                                        rec(v, "3 RESOURCES/Songs.md", "P001", op="merge", dest="3 RESOURCES/Training.md", relation="=")])[0]
    agreed, runs, _ = SEM.agree_detail(v, e, e)
    plan = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")[0]
    t_before = open(os.path.join(v, "3 RESOURCES", "Training.md"), "rb").read()
    ops = write_plan(os.path.join(tmp17, "q.tsv"), plan)
    check("= for part of a note GATHERS: the kept part recomposed at its path, the merged part appended into the destination (the brief's γ)",
          C.simulate(v, ops, manifest(v))[0] == [] and any(l.startswith("compose\t3 RESOURCES/Songs.md\t") for l in plan)
          and any(l.startswith("extend\t3 RESOURCES/Training.md\t") for l in plan) and not any(l.startswith("leave\t") for l in plan))
    probs = SEM.effective(v, ocfg, base + [rec(v, "3 RESOURCES/Songs.md", "*", op="merge", dest="3 RESOURCES/Training.md", relation="≡")])[1]
    check("≡ claimed for text the destination does not hold is refused as a variant", any("≡ claimed" in p for p in probs))
    probs = SEM.effective(v, ocfg, base + [rec(v, "3 RESOURCES/Songs.md", "*", op="merge", dest="3 RESOURCES/Nowhere.md", relation="≡")])[1]
    check("≡ into a note that does not exist is refused", any("does not exist" in p for p in probs))
finally:
    shutil.rmtree(tmp17, ignore_errors=True)

print("== the mover refuses a live run without a snapshot taken since the last pass (§VII.1) ==")
tmp18 = tempfile.mkdtemp(prefix="cerebrum-snap-")
try:
    sn, st = os.path.join(tmp18, "snaps"), os.path.join(tmp18, "state"); os.makedirs(sn); os.makedirs(st)
    check("no snapshot at all → refused", C.snapshot_stale(sn, st) != "")
    open(os.path.join(sn, "vault-1.tar.gz"), "w").write("x"); os.utime(os.path.join(sn, "vault-1.tar.gz"), (1000, 1000))
    check("a snapshot with no pass since → allowed", C.snapshot_stale(sn, st) == "")
    open(os.path.join(st, "undo-2.tsv"), "w").write("x"); os.utime(os.path.join(st, "undo-2.tsv"), (2000, 2000))
    check("a pass ran after the snapshot → refused", "older than the last pass" in C.snapshot_stale(sn, st))
    open(os.path.join(sn, "vault-3.tar.gz"), "w").write("x"); os.utime(os.path.join(sn, "vault-3.tar.gz"), (3000, 3000))
    check("a fresh snapshot after the pass → allowed", C.snapshot_stale(sn, st) == "")
finally:
    shutil.rmtree(tmp18, ignore_errors=True)

print("== intake: one new note — an unread destination is READ, never a silent empty plan ==")
tmp19 = tempfile.mkdtemp(prefix="cerebrum-intake-")
try:
    v, ocfg = organ_vault(tmp19)
    new_note = "# INBOX/Captured.md"; open(os.path.join(v, "# INBOX", "Captured.md"), "w").write(f"# Verse\n{SUNO} a new verse\n")
    A = [rec(v, new_note, "*", dest="3 RESOURCES/Songs.md")]; B = [rec(v, new_note, "*", dest="3 RESOURCES/Songs.md")]
    r = SEM.intake(v, ocfg, new_note, A, B, today="2026-01-01")
    check("both place it into an existing note nobody read → READ, naming that note", r["status"] == "READ" and r["must_read"] == ["3 RESOURCES/Songs.md"])
    A2, B2 = A + [rec(v, "3 RESOURCES/Songs.md")], B + [rec(v, "3 RESOURCES/Songs.md")]
    r = SEM.intake(v, ocfg, new_note, A2, B2, today="2026-01-01")
    check("both read the destination too → PLAN: the note archived and its lines appended into the destination",
          r["status"] == "PLAN" and any(l.startswith("extend\t3 RESOURCES/Songs.md\t") for l in r["plan"])
          and any(l.startswith("mv\t# INBOX/Captured.md\t4 ARCHIVE/") for l in r["plan"])
          and C.simulate(v, write_plan(os.path.join(tmp19, "p.tsv"), r["plan"]), manifest(v))[0] == [])
    r = SEM.intake(v, ocfg, new_note, A2, [rec(v, new_note, "*", dest="3 RESOURCES/Training.md"), rec(v, "3 RESOURCES/Songs.md"), rec(v, "3 RESOURCES/Training.md")], today="2026-01-01")
    check("the readers disagree on where → DISPUTE, never a plan", r["status"] == "DISPUTE" and "plan" not in r)
    r = SEM.intake(v, ocfg, new_note, [rec(v, new_note)], [rec(v, new_note)], today="2026-01-01")
    check("both keep it where it is → STAYS", r["status"] == "STAYS")
    r = SEM.intake(v, ocfg, new_note, A2, [], today="2026-01-01")
    check("a reader who did not read it → PROBLEM", r["status"] == "PROBLEM")
    r = SEM.intake(v, ocfg, new_note, [rec(v, new_note, "*", dest="2 AREAS/New Verse.md")], [rec(v, new_note, "*", dest="2 AREAS/New Verse.md")], today="2026-01-01")
    check("both place it as a new note in a folder → PLAN: a move", r["status"] == "PLAN" and r["plan"] == ["mv\t# INBOX/Captured.md\t2 AREAS/New Verse.md"])
finally:
    shutil.rmtree(tmp19, ignore_errors=True)

print("== insert: lines land after the section the readers named, verbatim, with provenance; undo cuts them back out ==")
tmp20 = tempfile.mkdtemp(prefix="cerebrum-insert-")
try:
    v, ocfg = organ_vault(tmp20)
    songs = os.path.join(v, "3 RESOURCES", "Songs.md"); s_before = open(songs, "rb").read()
    parts = SEM.segment(s_before); end_p0 = parts[0]["end"]
    ops = write_plan(os.path.join(tmp20, "p.tsv"), [f"insert\t3 RESOURCES/Songs.md\tL{end_p0}\t# INBOX/Two.md\tP001"])
    before = manifest(v); errs, exp, _ = C.simulate(v, ops, before)
    check("the dry run accepts an insert after a real line", errs == [])
    ok, f, _ = C.apply_plan(v, ops, before, C._scratch_trash_fn(os.path.join(tmp20, "_t")), os.path.join(tmp20, "u.tsv"))
    s_now = open(songs, "rb").read(); two = open(os.path.join(v, "# INBOX", "Two.md"), "rb").read()
    piece = SEM.take(two, SEM.spans("P001", SEM.segment(two)))
    head = b"".join(s_before.splitlines(keepends=True)[:end_p0]); tail = b"".join(s_before.splitlines(keepends=True)[end_p0:])
    check("applied GREEN: head of the note, then the provenance line and the piece, then the rest — byte for byte",
          ok and s_now.startswith(head) and s_now.endswith(tail) and piece in s_now and b"*Inserted verbatim from `# INBOX/Two.md`" in s_now
          and s_now.index(piece) > len(head) and len(s_now) == len(head) + len(tail) + len(s_now) - len(head) - len(tail))
    check("the verifier's expected bytes are the dry run's, and they match", exp["hashes"]["3 RESOURCES/Songs.md"] == C.sha256(songs))
    check("undo cuts exactly the inserted region back out", C.undo_plan(v, os.path.join(tmp20, "u.tsv")) == [] and open(songs, "rb").read() == s_before)
    check("insert after a line the note does not have is refused",
          C.simulate(v, write_plan(os.path.join(tmp20, "q.tsv"), ["insert\t3 RESOURCES/Songs.md\tL999\t# INBOX/Two.md\tP001"]), manifest(v))[0] != [])
    check("insert into a note that does not exist is refused",
          C.simulate(v, write_plan(os.path.join(tmp20, "r.tsv"), ["insert\t3 RESOURCES/Nope.md\tL1\t# INBOX/Two.md\tP001"]), manifest(v))[0] != [])
    # the reading side: `at` names the section; agreement; the plan says insert
    base = [r for r in keep_all(v, ocfg) if r["note"] != "# INBOX/Two.md"]
    a = rec(v, "# INBOX/Two.md", "P001", dest="3 RESOURCES/Songs.md"); a["at"] = "after P000"
    b = rec(v, "# INBOX/Two.md", "P001", dest="3 RESOURCES/Songs.md"); b["at"] = "after P000"
    eA = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"), a])[0]
    eB = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"), b])[0]
    agreed, runs, acts = SEM.agree_detail(v, eA, eB)
    plan = SEM.propose(v, ocfg, agreed, runs, today="2026-01-01")[0]
    check("both readers name the same section → the plan inserts after its last line",
          runs == [] and acts == [] and any(l.startswith(f"insert\t3 RESOURCES/Songs.md\tL{end_p0}\t") for l in plan)
          and C.simulate(v, write_plan(os.path.join(tmp20, "s.tsv"), plan), manifest(v))[0] == [])
    b2 = dict(b, at="after P001")
    eB2 = SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"), b2])[0]
    agreed2, runs2, acts2 = SEM.agree_detail(v, eA, eB2)
    plan2 = SEM.propose(v, ocfg, agreed2, runs2, today="2026-01-01")[0]
    check("they agree on the note but not the section → the lines still go there, appended; the slot is an act dispute for the keeper",
          runs2 == [] and len(acts2) == 1 and any(l.startswith("extend\t3 RESOURCES/Songs.md\t") for l in plan2) and not any(l.startswith("insert") for l in plan2))
    bad = dict(a, at="after nowhere")
    check("a malformed `at` is refused", any("`at`" in p for p in SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"), bad])[1]))
    bad2 = dict(rec(v, "# INBOX/Two.md", "P001"), at="after P000")
    check("`at` without a destination is refused", any("`at`" in p for p in SEM.effective(v, ocfg, base + [rec(v, "# INBOX/Two.md", "P000"), bad2])[1]))
finally:
    shutil.rmtree(tmp20, ignore_errors=True)

print("== registry: a live note inside its archived original is not proposed for removal ==")
s, root, cfg = K._scratch()
try:
    big = "\n".join(f"line number {i} with enough words in it" for i in range(30)) + "\n"
    open(os.path.join(root, "4 ARCHIVE", "Full — original.md"), "w").write(big + "and a line that moved elsewhere\n")
    open(os.path.join(root, "3 RESOURCES", "Kept part.md"), "w").write(big)
    out = REG.build(root, cfg)
    check("it is listed as an original kept in the archive",
          "`4 ARCHIVE/Full — original.md` holds all of `3 RESOURCES/Kept part.md`" in out)
    check("  … and not as removable", "`3 RESOURCES/Kept part.md` — 100% inside" not in out)
finally:
    shutil.rmtree(s, ignore_errors=True)

print()
print(f"RESULT: {'ALL PASS' if not fails else 'FAILURES: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
