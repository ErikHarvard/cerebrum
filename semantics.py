#!/usr/bin/env python3
"""
semantics.py — organizing what a note says, not only where it sits.

A note is cut into parts at its headings, a cut that loses nothing: the parts, in order, are the
note byte for byte. A reader — a person, or Claude at the keeper's request — decides what each
part is about, where it belongs and in what order, and says so in a plan with `compose` (and
`leave`, for parts deliberately left only in the source). The Builder checks the form: every part
lands in exactly one place or is explicitly left (excluded middle), unchanged (identity), and never
twice (non-contradiction). Whether a part was placed WELL is meaning — a reader's judgment,
ratified by the keeper — and no instrument can check it (the Gödel bound).

    python3 semantics.py segment "<note path inside the vault>"   → state/parts-<name>.tsv
"""
import os, re, sys, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cerebrum as C

FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")

def segment(data):
    """Cut a note's bytes into parts at its ATX headings, never inside a code fence. Returns a list
    of dicts: id, start and end (1-based line numbers, inclusive), level (0 for text before the
    first heading), heading, bytes. Joining every part's bytes gives back the note exactly."""
    parts, cur, fence = [], None, None
    for i, raw in enumerate(data.splitlines(keepends=True), 1):
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        m = FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            h = None
        elif m:
            fence, h = m.group(1), None
        else:
            h = HEADING.match(line)
        if h or cur is None:
            cur = {"start": i, "level": len(h.group(1)) if h else 0,
                   "heading": h.group(2).strip() if h else "", "raw": []}
            parts.append(cur)
        cur["raw"].append(raw)
        cur["end"] = i
    for n, p in enumerate(parts):
        p["id"] = f"P{n:03d}"
        p["bytes"] = b"".join(p.pop("raw"))
    if b"".join(p["bytes"] for p in parts) != data:
        raise AssertionError("segmentation lost bytes")          # an instrument that must never pass silently
    return parts

def parse_ids(spec):
    """'P003-P007,P044' → ['P003', 'P004', 'P005', 'P006', 'P007', 'P044'] (order kept)."""
    out = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            a, b = chunk.split("-")
            out += [f"P{n:03d}" for n in range(int(a[1:]), int(b[1:]) + 1)]
        elif chunk:
            out.append(chunk)
    return out

def spans(spec, parts):
    """A spec → [(first line, last line)], in the order given. Each item is a part (P012), a run of
    parts (P011-P022), or a line range (L1542-L1600, or L1542 for one line) — lines are the finest
    cut, for a part too large to be one thing."""
    by_id = {p["id"]: p for p in parts}
    out = []
    for chunk in (c.strip() for c in spec.split(",")):
        if not chunk:
            continue
        if chunk.startswith("L"):
            a, _, b = chunk.partition("-")
            out.append((int(a[1:]), int((b or a).lstrip("L"))))
        else:
            for pid in parse_ids(chunk):
                if pid not in by_id:
                    raise KeyError(pid)
                out.append((by_id[pid]["start"], by_id[pid]["end"]))
    return out

def take(data, spans_):
    """The bytes of those line spans, in order — every line exactly as it stands in the note."""
    lines = data.splitlines(keepends=True)
    return b"".join(b"".join(lines[s - 1:e]) for s, e in spans_)

def describe(p):
    text = p["bytes"].decode("utf-8", "replace")
    body = [l.strip() for l in text.splitlines()[1 if p["level"] else 0:] if l.strip()]
    return {"words": len(text.split()), "sha": hashlib.sha256(p["bytes"]).hexdigest()[:12],
            "first": body[0][:100] if body else ""}

def cmd_segment(rel):
    path = os.path.join(C.VAULT, rel)
    with open(path, "rb") as fh:
        data = fh.read()
    parts = segment(data)
    C.ensure_dirs()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", os.path.splitext(os.path.basename(rel))[0]).strip("-").lower()
    out = os.path.join(C.STATE, f"parts-{slug}.tsv")
    clean = lambda s: s.replace("\t", " ").replace("\n", " ")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# parts of {rel}\tsha256 {hashlib.sha256(data).hexdigest()}\t{len(parts)} parts\n")
        fh.write("id\tstart\tend\tlevel\twords\tsha\theading\tfirst line\n")
        for p in parts:
            d = describe(p)
            fh.write("\t".join([p["id"], str(p["start"]), str(p["end"]), str(p["level"]), str(d["words"]),
                                d["sha"], clean(p["heading"]), clean(d["first"])]) + "\n")
    levels = {}
    for p in parts:
        levels[p["level"]] = levels.get(p["level"], 0) + 1
    print(f"segment  : {rel} → {len(parts)} parts ({', '.join(f'level {k}: {v}' for k, v in sorted(levels.items()))})")
    print(f"           rejoined = the note, byte for byte · {out}")
    return 0

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "segment":
        sys.exit(cmd_segment(sys.argv[2]))
    print(__doc__)
    sys.exit(2)
