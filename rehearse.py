#!/usr/bin/env python3
"""
rehearse.py — prove a ratified plan on a scratch copy of the vault before it touches the vault.

    python3 rehearse.py --plan state/plan-<name>.tsv [--frozen-live] [--no-snapshot] [--keep]

The law (§VI) believes neither the mover nor the verifier until both are seen to work on a
copy: the plan must verify GREEN there, planted defects must go RED, and undo must restore
the copy exactly.

  1. Snapshot the live vault (also a fresh backup) and extract it to a private scratch folder.
     --no-snapshot copies the vault directly instead (no new backup file).
  2. Manifest the copy, then apply the plan to it; trashed files go to a scratch trash.
  3. The copy must verify GREEN against the plan.
  4. Planted defects, chosen from the plan's own expectations, must go RED: a planned file
     lost after the move; a planned content change tampered with (if the plan has one).
     A write inside a frozen folder must be RED under the strict check and only a NOTE
     under --frozen-live. With every defect removed, the copy must be GREEN again.
  5. Undo; the copy must equal its before-manifest again, under the strict check.

The copy holds the whole vault, sensitive notes included; it is deleted at the end unless
--keep. The live vault is only read. Exit 0 only if every step passed.
"""
import argparse, os, shutil, sys, tarfile, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cerebrum as C

def _copy_live(source, dest):
    """copytree that tolerates files a live organ removes mid-copy — inside frozen folders only."""
    try:
        shutil.copytree(source, dest, symlinks=True)
    except shutil.Error as e:
        bad = [src for src, _dst, _why in e.args[0]
               if not C.is_frozen(os.path.relpath(src, source))]
        if bad:
            raise

def rehearse(plan, source, frozen_live=False, keep=False, snapshot=True, say=print):
    results = []
    def step(name, ok, detail=""):
        results.append(ok)
        say(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
        return ok
    first_red = lambda fs: next((f for f in fs if f.startswith("RED")), "")

    scratch = tempfile.mkdtemp(prefix="cerebrum-rehearsal-")
    try:
        # 1. a copy of the vault
        if snapshot:
            ok, snap, _lines = C.take_snapshot(source)
            if not step("snapshot of the vault", ok, snap):
                return False
            with tarfile.open(snap, "r:gz") as tar:
                tar.extractall(scratch, filter="data")
            vault = os.path.join(scratch, "vault")
        else:
            vault = os.path.join(scratch, "vault")
            _copy_live(source, vault)
        before = C.manifest_rows(vault)

        # 2–3. the plan, applied to the copy
        ops = C.load_plan(plan)
        errors, expect, _rep = C.simulate(vault, ops, before)
        if not step("dry run", not errors, "; ".join(errors[:3])):
            return False
        say(f"        derived: {len(expect['moves'])} file moves · {len(expect['removed'])} trashed · "
            f"{len(expect['hashes'])} content change(s) · {len(expect.get('added', {}))} new note(s)")
        undo = os.path.join(scratch, "undo.tsv")
        ok, findings, _ = C.apply_plan(vault, ops, before,
                                       C._scratch_trash_fn(os.path.join(scratch, "_trash")),
                                       undo, frozen_live=frozen_live)
        step("apply → GREEN against the plan", ok, first_red(findings))

        def verdict(**kw):
            return C._verify(vault, before, expect["moves"], expect["removed"], expect["hashes"],
                             expected_added=expect.get("added"), **kw)

        # 4a. a planned file (or, failing that, any movable file) lost after the move
        target = (sorted(expect["moves"].values()) or sorted(expect.get("added", {})) or
                  sorted(r[2] for r in before if r[0] != "DIR" and not C.is_frozen(r[2])))[0]
        held = os.path.join(scratch, "_held")
        os.rename(os.path.join(vault, target), held)
        okd, fd = verdict(frozen_live=frozen_live)
        os.rename(held, os.path.join(vault, target))
        step("planted loss → RED", not okd and bool(first_red(fd)), target)

        # 4b. a planned content change tampered with
        changed = sorted(expect["hashes"]) or sorted(expect.get("added", {}))
        if changed:
            p = os.path.join(vault, changed[0])
            with open(p, "rb") as fh:
                data = fh.read()
            with open(p, "ab") as fh:
                fh.write(b"tampered")
            okt, _ = verdict(frozen_live=frozen_live)
            with open(p, "wb") as fh:
                fh.write(data)
            step("planted tamper of a changed or new note → RED", not okt, changed[0])

        # 4c. a write inside a frozen folder: RED when strict, a NOTE when frozen folders are live
        root = next((r for r in C.FROZEN if r not in (".obsidian", ".trash")
                     and os.path.isdir(os.path.join(vault, r))), None)
        if root:
            probe = os.path.join(vault, root, "_rehearsal-probe.md")
            with open(probe, "w") as fh:
                fh.write("a live organ writes\n")
            ok_strict, _ = verdict()
            ok_live, f_live = verdict(frozen_live=True)
            os.remove(probe)
            step("frozen write → RED under the strict check", not ok_strict, root)
            step("frozen write → a NOTE, not a failure, under --frozen-live",
                 ok_live and any(f.startswith("NOTE") and "_rehearsal-probe" in f for f in f_live))

        # control: every plant removed → GREEN, strict (a scratch copy has no live writers)
        okc, fc = verdict()
        step("control: defects removed → GREEN (strict)", okc, first_red(fc))

        # 5. undo
        notes = C.undo_plan(vault, undo)
        oku, fu = C._verify(vault, before)
        step("undo → the copy equals its before-manifest (strict)", not notes and oku,
             (notes or [first_red(fu)])[0])
        return all(results)
    finally:
        if keep:
            say(f"  kept: {scratch}  (it holds the whole vault — delete it when done)")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

def main():
    ap = argparse.ArgumentParser(description="Prove a ratified plan on a scratch copy of the vault.")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--frozen-live", dest="frozen_live", action="store_true",
                    help="apply as the live run will: frozen-folder writes listed, not failed")
    ap.add_argument("--no-snapshot", dest="no_snapshot", action="store_true",
                    help="copy the vault directly instead of snapshotting it (no new backup)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the scratch copy (it holds the whole vault, sensitive notes included)")
    a = ap.parse_args()
    print(f"rehearse : {a.plan} on a scratch copy of {C.VAULT}"
          + ("  (--frozen-live)" if a.frozen_live else ""))
    ok = rehearse(a.plan, C.VAULT, frozen_live=a.frozen_live, keep=a.keep,
                  snapshot=not a.no_snapshot)
    print("rehearse : " + ("PASS — proven on a copy; run it live with cerebrum.py move --apply --i-ratified"
                           if ok else "FAIL — do not run this plan live"))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
