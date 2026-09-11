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

# ==== the semantic organ ======================================================================
# Triage by local embeddings (they rank, never decide) → a reader's record for every line → two
# independent readings that must agree → a plan built only from what they agree on. The operators
# are a paradox audit's: split one note holding several subjects, gather many notes on one subject,
# mark a boundary — and, first, stand down where two notes are one subject seen from two uses.
import json, urllib.request
from collections import defaultdict, Counter

MIN_WORDS = 30            # below this a section is read, not ranked

def organ_cfg(cfg):
    return cfg.get("organ", {})

def sema_dir():
    d = os.path.join(C.STATE, "sema")
    os.makedirs(d, exist_ok=True)
    return d

def scope(vault, cfg):
    """The notes the organ reads: every movable note, except the generated registry, notes accepted
    as sensitive (never opened), and the config's organ.exclude."""
    import checks as K
    skip = set(cfg.get("accepted", {}).get("sensitive", [])) | set(organ_cfg(cfg).get("exclude", []))
    if cfg.get("registry"):
        skip.add(cfg["registry"])
    v = K.Vault(vault, cfg)
    vis = set(v.visible)
    return [p for p in v.movable if p.endswith(".md") and p in vis and p not in skip]

def read_bytes(vault, rel):
    with open(os.path.join(vault, rel), "rb") as fh:
        return fh.read()

# ---- repeats inside a note: the one redundancy found without a read --------------------------
LIST_MARK = re.compile(r"^(?:[-*+]|\d+[.)])\s+")

def line_key(raw):
    """What makes two lines copies: the same text, ignoring surrounding space and a list marker.
    Case, punctuation and wording all count — a variant is not a copy."""
    return re.sub(r"\s+", " ", LIST_MARK.sub("", raw.decode("utf-8", "replace").strip()))

def dedupe_bytes(data):
    """Keep the first copy of each repeated substantial line (four words or more, not a heading,
    not inside a code fence); drop the later copies. Returns (new bytes, dropped line numbers)."""
    seen, out, dropped, fence = set(), [], [], None
    for i, raw in enumerate(data.splitlines(keepends=True), 1):
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        m = FENCE.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            out.append(raw); continue
        if m:
            fence = m.group(1); out.append(raw); continue
        k = line_key(raw)
        if len(k.split()) >= 4 and not HEADING.match(line):
            if k in seen:
                dropped.append(i); continue
            seen.add(k)
        out.append(raw)
    return b"".join(out), dropped

def repeat_lines(data):
    """{line text: copies} for every substantial line that occurs more than once."""
    _new, dropped = dedupe_bytes(data)
    lines = data.splitlines(keepends=True)
    return dict(Counter(line_key(lines[i - 1]) for i in dropped)) if dropped else {}

# ---- embeddings: local, cached, ordinal -------------------------------------------------------
def ollama_embed(texts, cfg):
    oc = organ_cfg(cfg)
    url = oc.get("embed_url", "http://localhost:11434").rstrip("/") + "/api/embed"
    out = []
    for i in range(0, len(texts), 32):
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"}, data=json.dumps(
            {"model": oc.get("embed_model", "nomic-embed-text"), "input": texts[i:i + 32]}).encode())
        with urllib.request.urlopen(req, timeout=600) as r:
            out += json.load(r)["embeddings"]
    return out

def fake_embed(texts, cfg=None, dim=64):
    """A deterministic stand-in for tests: a hashed bag of words. It ranks shared vocabulary —
    enough for a test, and exactly why a real run uses a real model."""
    vs = []
    for t in texts:
        v = [0.0] * dim
        for w in re.findall(r"[^\W_]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
        vs.append(v)
    return vs

def embedder():
    return fake_embed if os.environ.get("CEREBRUM_EMBED") == "fake" else ollama_embed

def sections_of(data, rel):
    out = []
    for p in segment(data):
        text = p["bytes"].decode("utf-8", "replace")
        out.append({"note": rel, "pid": p["id"], "start": p["start"], "end": p["end"],
                    "words": len(text.split()), "sha": hashlib.sha256(p["bytes"]).hexdigest()[:12],
                    "heading": p["heading"][:80], "text": text})
    return out

WINDOW_CHARS = 1500      # measured 2026-09-11: 300 words of links came to 6,000 characters and overflowed the model

def windows(text, size=WINDOW_CHARS):
    """Cut on whitespace into pieces of at most `size` characters. Dense text — links, code, other
    scripts — makes more tokens per word, so the budget is characters, not words; a single token
    longer than the budget is cut too. Rejoined with spaces, the pieces are the text's words, all."""
    out, cur = [], ""
    for w in text.split():
        while len(w) > size:
            if cur:
                out.append(cur); cur = ""
            out.append(w[:size]); w = w[size:]
        if cur and len(cur) + 1 + len(w) > size:
            out.append(cur); cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return out + [cur] if cur or not out else out

def section_vectors(secs, cfg, embed, cache_path=None):
    """One unit vector per section — the mean of its windows' embeddings — cached by model, window
    size and section hash, so an unchanged section is never embedded twice."""
    import numpy as np
    model = "fake" if embed is fake_embed else organ_cfg(cfg).get("embed_model", "nomic-embed-text")
    cache = {}
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    key = lambda s: f"{model}:{WINDOW_CHARS}:{hashlib.sha256(s['text'].encode()).hexdigest()}"
    todo = list({key(s): s for s in secs if key(s) not in cache}.items())
    wins, owner = [], []
    for n, (_k, s) in enumerate(todo):
        for piece in windows(s["text"]):
            wins.append("clustering: " + piece); owner.append(n)
    if wins:
        vecs, acc = embed(wins, cfg), defaultdict(list)
        for n, v in zip(owner, vecs):
            acc[n].append(v)
        for n, (k, _s) in enumerate(todo):
            cache[k] = [round(float(x), 5) for x in np.mean(np.array(acc[n], dtype=float), axis=0)]
        if cache_path:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
    M = np.array([cache[key(s)] for s in secs], dtype=float).reshape(len(secs), -1)
    norm = np.linalg.norm(M, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return M / norm

# A fixed probe of the model itself: it must rank a paraphrase above an unrelated sentence.
PROBE = ("Every note goes where it will be used soonest: projects first, then areas, then resources, else the archive.",
         "File each note in the place you'll need it next — a current project if one fits, otherwise an ongoing "
         "responsibility, then a topic of interest, and failing all that, storage.",
         "Deadlift with a neutral spine; brace the core before the bar leaves the floor.")

def index(vault, cfg, embed=None, cache_path=None, top_pairs=150, probe=None):
    """Triage for the readers: which notes may hold several subjects, which sections of different
    notes may be one subject, which lines repeat inside a note. Every score only ranks; the
    readings decide. Two controls: identical copies must find each other at 1.0 (the search
    looked), and the model must rank a paraphrase above an unrelated sentence (it reads meaning,
    not only words) — skipped for the test stand-in, which reads only words."""
    import numpy as np
    embed = embed or embedder()
    notes = scope(vault, cfg)
    num = {rel: n for n, rel in enumerate(notes)}
    secs, note_sha, reps = [], {}, {}
    for rel in notes:
        data = read_bytes(vault, rel)
        note_sha[rel] = hashlib.sha256(data).hexdigest()
        secs += sections_of(data, rel)
        r = repeat_lines(data)
        if r:
            reps[rel] = {"lines": len(r), "extra_copies": sum(r.values()),
                         "top": sorted(r.items(), key=lambda kv: -kv[1])[:5]}
    big = [s for s in secs if s["words"] >= MIN_WORDS]
    V = section_vectors(big, cfg, embed, cache_path) if big else np.zeros((0, 1))
    ref = lambda s: {"note": s["note"], "pid": s["pid"], "heading": s["heading"], "words": s["words"]}

    within, by_note = [], defaultdict(list)          # one note: coherence of its sections to its centroid
    for k, s in enumerate(big):
        by_note[s["note"]].append(k)
    for rel, ks in by_note.items():
        if len(ks) < 2:
            continue
        w = np.array([big[k]["words"] for k in ks], dtype=float)
        c = (V[ks] * w[:, None]).sum(0)
        c /= (np.linalg.norm(c) or 1.0)
        sims = V[ks] @ c
        within.append({"note": rel, "coherence": round(float((sims * w).sum() / w.sum()), 3),
                       "sections": len(ks), "words": int(w.sum()),
                       "farthest": [ref(big[ks[j]]) for j in np.argsort(sims)[:3]]})
    within.sort(key=lambda x: x["coherence"])

    best = []                                        # across notes: each section's nearest in ANOTHER note
    nid = np.array([num[s["note"]] for s in big])
    for a in range(0, len(big), 512):
        blk = V[a:a + 512] @ V.T
        blk[nid[a:a + 512][:, None] == nid[None, :]] = -2.0
        best += [(float(blk[r, j]), a + r, int(j)) for r, j in enumerate(blk.argmax(1))]
    top_of = {k: (sim, j) for sim, k, j in best}
    best.sort(key=lambda t: -t[0])
    across, seen = [], set()
    for sim, k, j in best:
        pr = (min(k, j), max(k, j))
        if sim < -1.5 or pr in seen:
            continue
        seen.add(pr)
        across.append({"sim": round(sim, 3), "a": ref(big[k]), "b": ref(big[j])})
        if len(across) >= top_pairs:
            break

    copies = defaultdict(list)
    for rel, h in note_sha.items():
        copies[h].append(rel)
    control = []
    for g in sorted(sorted(x) for x in copies.values() if len(x) > 1):
        ks = [k for k, s in enumerate(big) if s["note"] in g]
        control.append({"copies": g, "sections": len(ks), "found": all(
            top_of[k][0] >= 0.999 and big[top_of[k][1]]["note"] in g for k in ks) if ks else None})
    run_probe = (embed is not fake_embed) if probe is None else probe
    probe = None
    if run_probe:
        P = np.array(embed(["clustering: " + t for t in PROBE], cfg), dtype=float)
        P /= np.linalg.norm(P, axis=1, keepdims=True)
        probe = {"paraphrase": round(float(P[0] @ P[1]), 3), "unrelated": round(float(P[0] @ P[2]), 3)}
        probe["ok"] = probe["paraphrase"] > probe["unrelated"]
    return {"when": C.ts(), "model": "fake" if embed is fake_embed else organ_cfg(cfg).get("embed_model", "nomic-embed-text"),
            "notes": len(notes), "sections": len(secs), "ranked": len(big), "note_sha": note_sha,
            "section_list": [{k: v for k, v in s.items() if k != "text"} for s in secs],
            "within": within, "across": across,
            "note_pairs": [[a, b, n] for (a, b), n in Counter(tuple(sorted((x["a"]["note"], x["b"]["note"])))
                                                               for x in across).most_common()],
            "repeats": reps, "control": control, "probe": probe}

def index_ok(ix):
    """The index is believed only if every copy group found itself and the model probe passed."""
    return all(c["found"] is not False for c in ix["control"]) and (ix["probe"] is None or ix["probe"]["ok"])

# ---- placement: where a new piece of text belongs, by meaning ---------------------------------
# Measured 2026-09-11 on this vault (25 sections lifted from their notes, own note excluded): the
# embedding put a section back in its own FOLDER 11/25 times raw, 13/25 mean-centred. So the
# embedding is a SHORTLIST for a reader, never a verdict — the organ run forward: shortlist, two
# readers, agreement, the keeper. Centring (subtracting the corpus mean) is kept: it widens the gap
# between the lead and the median from 0.12 to 0.42 with no loss, so the shortlist is at least sharp.
def _targets(cfg, notes):
    """Where a new text may be placed: never the archive, never the system's own notes."""
    arch = list(cfg.get("para", C.PARA))[-1]
    meta = tuple(d + "/" for d in cfg.get("meta_dirs", []))
    return [n for n in notes if not n.startswith(arch + "/") and not n.startswith(meta)]

def _corpus(vault, cfg, embed, cache_path, exclude=()):
    import numpy as np
    notes = [n for n in _targets(cfg, scope(vault, cfg)) if n not in set(exclude)]
    secs = []
    for rel in notes:
        secs += [x for x in sections_of(read_bytes(vault, rel), rel) if x["words"] >= MIN_WORDS]
    if not secs:
        return notes, secs, None, None
    M = section_vectors(secs, cfg, embed, cache_path)
    mu = M.mean(0)
    return notes, secs, M, mu

def _centre(X, mu):
    import numpy as np
    Y = X - mu
    n = np.linalg.norm(Y, axis=1, keepdims=True); n[n == 0] = 1.0
    return Y / n

def place(vault, cfg, text, embed=None, cache_path=None, exclude=(), top=8):
    """The shortlist: each section of the new text against every section of every placeable note,
    mean-centred cosine. Returns ranked notes (the nearest section in each), ranked folders, the lean
    (the folder the nearest notes share, if they share one), and a verdict that is only ever
    SHORTLIST, NO HOME (the lead comes no nearer than unrelated text does) or EMPTY. A reader
    decides; the keeper ratifies. `exclude`: the text's own note, when it is already in the vault."""
    import numpy as np
    embed = embed or embedder()
    q = [x for x in sections_of(text.encode("utf-8") if isinstance(text, str) else text, "(new)") if x["words"] > 0]
    if not q:
        return {"verdict": "EMPTY", "notes": [], "folders": [], "lean": None, "why": "no words to place"}
    notes, secs, M, mu = _corpus(vault, cfg, embed, cache_path, exclude)
    if M is None:
        return {"verdict": "NO HOME", "notes": [], "folders": [], "lean": None, "why": "nothing placeable in scope to compare with"}
    Q = _centre(section_vectors(q, cfg, embed, cache_path), mu)
    S = _centre(M, mu)
    best = (Q @ S.T).max(0)
    by_note = defaultdict(list)
    for k, x in enumerate(secs):
        by_note[x["note"]].append(k)
    ranked = []
    for rel, ks in by_note.items():
        k = max(ks, key=lambda k: best[k])
        ranked.append({"note": rel, "sim": round(float(best[k]), 3),
                       "section": {"pid": secs[k]["pid"], "heading": secs[k]["heading"], "start": secs[k]["start"], "end": secs[k]["end"]}})
    ranked.sort(key=lambda r: -r["sim"])
    median = float(np.median([r["sim"] for r in ranked])) if ranked else 0.0
    ranked = ranked[:top]
    folders = []
    for r in ranked:
        d = os.path.dirname(r["note"])
        if d not in [f["folder"] for f in folders]:
            folders.append({"folder": d, "sim": r["sim"], "by": r["note"]})
    floor = None
    if embed is not fake_embed:                     # the model's own score for unrelated text, centred the same way
        P = _centre(np.array(embed(["clustering: " + t for t in PROBE], cfg), dtype=float), mu)
        floor = round(float(P[0] @ P[2]), 3)
    lead = ranked[0]
    top3 = [os.path.dirname(r["note"]) for r in ranked[:3] if r["sim"] > median]   # only notes above the median count
    shared = [d for d in set(top3) if top3.count(d) >= 2]
    lean = shared[0] if shared else (top3[0] if top3 else None)                    # a shared folder, else the lead's
    if floor is not None and lead["sim"] <= floor:
        verdict, why = "NO HOME", f"the nearest note scores {lead['sim']}, no nearer than unrelated text ({floor}) — a reader must place it from the text alone"
    else:
        verdict = "SHORTLIST"
        why = (f"nearest `{lead['note']}` at {lead['sim']} (median note {median:.3f}); "
               + (f"{top3.count(lean)} of the nearest above the median share `{lean}/`" if lean and top3.count(lean) >= 2
                  else f"lean `{lean}/` by the lead alone" if lean else "nothing above the median")
               + " — a shortlist for the reader, not a placement")
    return {"verdict": verdict, "why": why, "notes": ranked, "folders": folders, "lean": lean, "floor": floor,
            "median": round(median, 3), "query_sections": len(q)}

def place_control(vault, cfg, embed=None, cache_path=None, seed=11):
    """The search must prove it looked: a section lifted from a placeable note, with the note still
    present, must rank that note first at ~1.0; excluded, the note must not appear. Returns problems."""
    import random
    embed = embed or embedder()
    rnd = random.Random(seed)
    cands = []
    for rel in _targets(cfg, scope(vault, cfg)):
        ss = [x for x in sections_of(read_bytes(vault, rel), rel) if x["words"] >= MIN_WORDS]
        if ss:
            cands.append((rel, ss))
    if not cands:
        return ["no placeable note with a rankable section to lift a control from"]
    rel, ss = rnd.choice(cands)
    sec = rnd.choice(ss)
    r = place(vault, cfg, sec["text"], embed, cache_path)
    probs = []
    if not r["notes"] or r["notes"][0]["note"] != rel:
        probs.append(f"control: a section of {rel} did not rank it first (top: {r['notes'][0]['note'] if r['notes'] else None})")
    elif r["notes"][0]["sim"] < 0.999:
        probs.append(f"control: its own section scored {r['notes'][0]['sim']}, not 1.0")
    r2 = place(vault, cfg, sec["text"], embed, cache_path, exclude=[rel])
    if any(n["note"] == rel for n in r2["notes"]):
        probs.append(f"control: an excluded note still appears ({rel})")
    return probs

def place_calibrate(vault, cfg, embed=None, cache_path=None, n=25, seed=7):
    """How far the shortlist can be trusted on THIS vault, measured: sections lifted from notes that
    have at least two rankable sections, own note excluded — how often the nearest other note is in
    the same folder, and how often the note's own remaining sections would have beaten every other
    note. Returned with every report; it is the number a reader weighs the shortlist by."""
    import random, numpy as np
    embed = embed or embedder()
    notes, secs, M, mu = _corpus(vault, cfg, embed, cache_path)
    if M is None:
        return {"trials": 0}
    S = _centre(M, mu)
    note_of = np.array([x["note"] for x in secs])
    by_note = defaultdict(list)
    for k, x in enumerate(secs):
        by_note[x["note"]].append(k)
    multi = sorted(k for k, v in by_note.items() if len(v) >= 2)
    rnd = random.Random(seed)
    trials = [(rel, rnd.choice(by_note[rel])) for rel in rnd.sample(multi, min(n, len(multi)))]
    folder_hit = own_wins = 0
    for rel, k in trials:
        mask = note_of != rel
        sims = S[mask] @ S[k]
        top = note_of[mask][int(np.argmax(sims))]
        folder_hit += os.path.dirname(top) == os.path.dirname(rel)
        own = S[[j for j in by_note[rel] if j != k]] @ S[k]
        own_wins += float(own.max()) > float(sims.max())
    return {"trials": len(trials), "same_folder_top1": folder_hit, "own_note_wins": own_wins, "seed": seed,
            "sections": len(secs), "notes": len(notes)}

def place_md(r, label, cal=None):
    L = [f"# Placement shortlist — {label}", "", f"**{r['verdict']}** — {r.get('why', '')}", ""]
    if cal and cal.get("trials"):
        L += [f"**How far to trust this list, measured on this vault:** of {cal['trials']} sections lifted from their notes, the nearest "
              f"other note was in the same folder {cal['same_folder_top1']} times, and the note's own remaining sections would have won "
              f"{cal['own_note_wins']} times. The list narrows the reading; it does not place.", ""]
    if r.get("notes"):
        L += ["| # | note | nearest section | sim |", "|---|---|---|---|"]
        for n, x in enumerate(r["notes"], 1):
            L.append(f"| {n} | `{x['note']}` | {x['section']['pid']} L{x['section']['start']}–L{x['section']['end']} {x['section']['heading']} | {x['sim']} |")
        L += ["", "Folders, by their nearest note: " + " · ".join(f"`{f['folder']}/` ({f['sim']})" for f in r["folders"]),
              f"Lean: `{r['lean']}/`" if r.get("lean") else "Lean: none — the nearest notes are in different folders", ""]
    if r.get("floor") is not None:
        L.append(f"Floor: unrelated text scores about {r['floor']} on this model, centred the same way; a lead at or below it is NO HOME.")
    L += ["", "Every score only ranks. Two readers read the text against this list and record where it belongs; where they agree, "
          "the keeper ratifies, and the placement runs as a plan (`mv` into the folder, or `extend` the note, the source archived).", ""]
    return "\n".join(L)

def cmd_place(V, cfg, argv):
    """place <file-or-vault-note> [--exclude <note>]… [--calibrate] — the shortlist for a reader."""
    args, exclude, recal = argv[1:], [], False
    while "--exclude" in args:
        i = args.index("--exclude"); exclude.append(args[i + 1]); del args[i:i + 2]
    if "--calibrate" in args:
        recal = True; args.remove("--calibrate")
    if len(args) != 1:
        print("place    : need one file (a path on disk, or a note path inside the vault)"); return 2
    src = args[0]
    inside = os.path.isfile(os.path.join(V, src))
    path = os.path.join(V, src) if inside else os.path.expanduser(src)
    if not os.path.isfile(path):
        print(f"place    : no such file: {src}"); return 2
    if inside and src not in exclude:
        exclude.append(src)                           # a note already in the vault must not find itself
    with open(path, "rb") as fh:
        text = fh.read()
    cache = os.path.join(sema_dir(), "embeddings.json")
    probs = place_control(V, cfg, cache_path=cache)
    print("control  : " + ("the search proves it looked" if not probs else f"{len(probs)} problem(s)"))
    for p in probs:
        print("   " + p)
    if probs:
        return 1
    calp = os.path.join(sema_dir(), "place-calibration.json")
    cal = None
    if os.path.exists(calp) and not recal:
        with open(calp, encoding="utf-8") as fh:
            cal = json.load(fh)
    if cal is None or cal.get("sections") != len(_corpus(V, cfg, embedder(), cache)[1]):
        cal = place_calibrate(V, cfg, cache_path=cache)
        cal["when"] = C.ts()
        with open(calp, "w", encoding="utf-8") as fh:
            json.dump(cal, fh, indent=1)
    print(f"measured : same-folder top-1 {cal.get('same_folder_top1')}/{cal.get('trials')} · own-note-wins {cal.get('own_note_wins')}/{cal.get('trials')} (sections {cal.get('sections')})")
    r = place(V, cfg, text, cache_path=cache, exclude=exclude)
    out = os.path.join(sema_dir(), f"place-{C.ts()}.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(place_md(r, src, cal))
    print(f"place    : {src} — {r['verdict']}")
    print("   " + r.get("why", ""))
    for n, x in enumerate(r.get("notes", [])[:8], 1):
        print(f"   {n}. {x['sim']:.3f}  {x['note']}  ← {x['section']['pid']} {x['section']['heading'][:50]}")
    print(f"   lean: {r['lean'] + '/' if r.get('lean') else 'none'}   → {out}")
    return 0

# ---- a synthesis: traceable, or it is a new claim --------------------------------------------
SOURCE_LINE = re.compile(r"^- \[(S\d+)\] `([^`]+)` (P\d{3}) sha:([0-9a-f]{12})")
CITE = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]")
RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")

def check_synthesis(draft, content_of):
    """Every paragraph of the body cites a source — [S1] or [S1, S3]; every source is listed under
    '## Sources' as  - [S1] `note path` P012 sha:<12 hex> — …  and that part of that note must still
    hold exactly those bytes. content_of(path) → the note's bytes, or None. Returns the problems."""
    text = draft.decode("utf-8", "replace")
    m = re.search(r"(?m)^##\s+Sources\s*$", text)
    if not m:
        return ["no '## Sources' section"]
    body, foot, probs, srcs, cited = text[:m.start()], text[m.end():], [], {}, set()
    if body.startswith("---\n"):
        end = body.find("\n---", 4)
        body = body[end + 4:] if end >= 0 else body
    for line in foot.splitlines():
        if line.strip():
            mm = SOURCE_LINE.match(line)
            if not mm:
                probs.append(f"not a source line: {line[:80]}"); continue
            if mm.group(1) in srcs:
                probs.append(f"{mm.group(1)} listed twice")
            srcs[mm.group(1)] = mm.groups()[1:]
    for p in re.split(r"\n\s*\n", C._strip_code(body)):
        claim = [l for l in p.splitlines() if l.strip() and not l.lstrip().startswith("#") and not RULE.match(l)]
        if not claim:
            continue                                     # a heading or a rule, not a claim
        ids = [i.strip() for c in CITE.findall(p) for i in c.split(",")]
        if not ids:
            probs.append(f"a paragraph cites nothing: “{claim[0].strip()[:60]}”")
        cited |= set(ids)
    probs += [f"{s} is cited but not listed under Sources" for s in sorted(cited - set(srcs))]
    probs += [f"{s} is listed but never cited" for s in sorted(set(srcs) - cited)]
    for sid, (path, pid, sha12) in sorted(srcs.items()):
        data = content_of(path)
        part = next((q for q in segment(data) if q["id"] == pid), None) if data is not None else None
        if data is None:
            probs.append(f"{sid}: no such note: {path}")
        elif part is None:
            probs.append(f"{sid}: {path} has no part {pid}")
        elif hashlib.sha256(part["bytes"]).hexdigest()[:12] != sha12:
            probs.append(f"{sid}: {path} {pid} changed since it was cited")
    return probs

# ---- the reading: one record for every line ---------------------------------------------------
OPS = ("keep", "merge", "synthesize", "mark", "link", "dedupe", "horizon")
PLACING = ("keep", "merge")              # these put lines somewhere; every other act leaves them where they are
RELATIONS = ("", "≡", "=", "≅", "≠", "parallax")
READING_FORMAT = """A reading is JSON lines, one record each:
  note      the note's vault path            note_sha  sha256 of the note as read — pins the record
  lines     "*" = every line no other record of this note names; or parts "P003-P010"; or lines "L40-L97"
  subject   what these lines are about       use       what they are for (project/area/resource/archive …)
  op        keep | merge | synthesize | mark | link | dedupe | horizon
  dest      keep, merge: the note they belong in ("" = where they are) · synthesize: the synthesis note
  relation  merge: ≡ (one text) or = (same claims) · link: ≅ (same structure, another domain) · parallax
  with      link: the notes it relates to    why       one line: the reason"""

def load_reading(path):
    """A file of JSON lines, or a folder of them (one per reader's slice)."""
    files = sorted(os.path.join(path, f) for f in os.listdir(path) if f.endswith(".jsonl")) \
        if os.path.isdir(path) else [path]
    recs = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if line.strip():
                    try:
                        r = json.loads(line)
                    except ValueError as e:
                        r = {"_bad": str(e)}
                    r["_at"] = f"{os.path.basename(f)}:{n}"
                    recs.append(r)
    return recs

def dest_problem(d, cfg):
    if not d.endswith(".md"):
        return f"a destination must be a note (.md): {d}"
    if d.split("/")[0] not in list(cfg.get("para", C.PARA)):
        return f"a destination must be in a PARA home: {d}"
    if d.count("/") > 2:
        return f"deeper than category → container → note: {d}"
    return f"a destination inside the frozen register: {d}" if C.is_frozen(d) else ""

def validate(r, sha, cfg):
    op, d, out = r.get("op"), r.get("dest", "") or "", []
    if r.get("note_sha") != sha:
        out.append("the note changed since it was read (note_sha differs) — read it again")
    if op not in OPS:
        out.append(f"unknown op: {op!r}")
    if r.get("relation", "") not in RELATIONS:
        out.append(f"unknown relation: {r.get('relation')!r}")
    for f in ("subject", "use"):
        if not str(r.get(f, "")).strip():
            out.append(f"no {f}")
    if op == "synthesize" and not d:
        out.append("synthesize needs dest: the synthesis note")
    if d and op in PLACING + ("synthesize",) and dest_problem(d, cfg):
        out.append(dest_problem(d, cfg))
    if op == "merge" and r.get("relation") not in ("≡", "="):
        out.append("merge needs relation ≡ or = — ≅ is a link, parallax a keep (PT 9.2)")
    if op == "link" and not r.get("with"):
        out.append("link needs with: the notes it relates to")
    return out

def effective(vault, cfg, recs, notes=None):
    """Coverage: each line of each note in scope → the one record that places it. A line named by
    two records, a line nobody read, a record for an older version of its note: each is a problem.
    Returns (eff {note: {line: record}} for every note read cleanly, problems)."""
    notes = list(scope(vault, cfg) if notes is None else notes)
    by_note, probs, inscope = defaultdict(list), [], set(notes)
    for r in recs:
        if "_bad" in r:
            probs.append(f"{r.get('_at', 'a record')}: not JSON: {r['_bad']}")
        elif r.get("note") not in inscope:
            probs.append(f"{r.get('_at', 'a record')}: not a note in scope: {r.get('note')!r}")
        else:
            by_note[r["note"]].append(r)
    eff = {}
    for rel in notes:
        data = read_bytes(vault, rel)
        sha, n = hashlib.sha256(data).hexdigest(), len(data.splitlines(keepends=True))
        if not by_note.get(rel):
            probs.append(f"not read: {rel}"); continue
        parts, assign, star, ok = segment(data), {}, [], True
        for r in by_note[rel]:
            bad = validate(r, sha, cfg)
            if bad:
                probs += [f"{r.get('_at', 'a record')}: {rel}: {b}" for b in bad]; ok = False; continue
            spec = str(r.get("lines", "*")).strip() or "*"
            if spec == "*":
                star.append(r); continue
            try:
                ls = [l for a, b in spans(spec, parts) for l in range(a, b + 1)]
            except (KeyError, ValueError) as e:
                probs.append(f"{r.get('_at', 'a record')}: {rel}: no such part or line: {e}"); ok = False; continue
            twice = sorted({l for l in ls if l in assign} | {l for l, c in Counter(ls).items() if c > 1})
            if any(not 1 <= l <= n for l in ls):
                probs.append(f"{r.get('_at', 'a record')}: {rel}: lines outside 1–{n}"); ok = False
            elif twice:
                probs.append(f"{r.get('_at', 'a record')}: {rel}: L{twice[0]} placed twice"); ok = False
            else:
                assign.update({l: r for l in ls})
        if len(star) > 1:
            probs.append(f"{rel}: {len(star)} '*' records — at most one"); ok = False
        rest = [l for l in range(1, n + 1) if l not in assign]
        if rest and star:
            assign.update({l: star[0] for l in rest})
        elif rest:
            probs.append(f"{rel}: {len(rest)} line(s) not read — the first is L{rest[0]}"); ok = False
        if ok:
            for r in {id(x): x for x in assign.values()}.values():
                if r["op"] == "merge" and r.get("relation") == "≡" and r.get("dest"):
                    d = r["dest"]
                    if not os.path.isfile(os.path.join(vault, d)):
                        probs.append(f"{r.get('_at', 'a record')}: {rel}: ≡ into a note that does not exist: {d}"); ok = False; continue
                    have = {ln.strip() for ln in read_bytes(vault, d).decode("utf-8", "replace").splitlines() if ln.strip()}
                    mine = data.decode("utf-8", "replace").splitlines()
                    missing = [l for l, x in assign.items() if x is r and mine[l - 1].strip() and mine[l - 1].strip() not in have]
                    if missing:
                        probs.append(f"{r.get('_at', 'a record')}: {rel}: ≡ claimed, but L{missing[0]} is not in {d} — a variant is =, not ≡"); ok = False
        if ok:
            eff[rel] = assign
    return eff, probs

def final_dest(r, rel):
    return (r.get("dest") or rel) if r["op"] in PLACING else rel

def nonblank(vault, rel):
    return [i for i, raw in enumerate(read_bytes(vault, rel).splitlines(keepends=True), 1) if raw.strip()]

def _group_ids(eff, nb):
    """dest → an id for the exact set of non-blank lines placed there; likewise per synthesis note."""
    place, syn = defaultdict(list), defaultdict(list)
    for rel, assign in eff.items():
        for l in nb[rel]:
            place[final_dest(assign[l], rel)].append((rel, l))
            if assign[l]["op"] == "synthesize":
                syn[assign[l]["dest"]].append((rel, l))
    h = lambda xs: hashlib.sha1(repr(sorted(xs)).encode()).hexdigest()
    return {d: h(x) for d, x in place.items()}, {d: h(x) for d, x in syn.items()}

def agree_detail(vault, effA, effB):
    """Line by line — non-blank lines; a blank line follows its neighbours — two independent readings
    are compared on two separate facts, never one label (the codex keeps a verdict and its cause
    apart): WHERE the line goes, and WHAT ELSE is done to it — mark, link, horizon, synthesis,
    dedupe. An existing note is known by its path; a new note has no path yet, so it is known by
    exactly what it holds: two readers agree on it only if they fill it with the same lines, whatever
    they call it. A dispute on where is the codex's vacuous case — nothing selected — and blocks its
    note. A dispute only on what else blocks nothing: the lines go where both put them, the act is
    not done, and the dispute goes to the keeper.
    Returns (agreed {note: {line: record}}, place disputes, act disputes)."""
    notes = sorted(set(effA) & set(effB))
    nb = {rel: nonblank(vault, rel) for rel in notes}
    gA, sA = _group_ids({r: effA[r] for r in notes}, nb)
    gB, sB = _group_ids({r: effB[r] for r in notes}, nb)
    def where(r, rel, g):
        d = final_dest(r, rel)
        return ("path", d) if os.path.isfile(os.path.join(vault, d)) else ("new", os.path.dirname(d), g[d])
    def what(r, s):
        return ("" if r["op"] in PLACING else r["op"], s.get(r["dest"]) if r["op"] == "synthesize" else None,
                tuple(sorted(r.get("with", []))) if r["op"] == "link" else ())
    def extend_run(runs, cur, rel, l, a, b):
        if cur and cur["a"] is a and cur["b"] is b:
            cur["last"] = l; return cur
        runs.append({"note": rel, "first": l, "last": l, "a": a, "b": b})
        return runs[-1]
    agreed, runs, act_runs, plain = {}, [], [], {}
    for rel in notes:
        agreed[rel], cur, cur_act = {}, None, None
        for l in nb[rel]:
            a, b = effA[rel][l], effB[rel][l]
            if where(a, rel, gA) != where(b, rel, gB):
                cur, cur_act = extend_run(runs, cur, rel, l, a, b), None
            elif what(a, sA) == what(b, sB):
                agreed[rel][l], cur, cur_act = a, None, None
            else:                                   # the act is not done: the line only stays where both put it
                agreed[rel][l] = a if a["op"] in PLACING else plain.setdefault(id(a), dict(a, op="keep", dest=""))
                cur, cur_act = None, extend_run(act_runs, cur_act, rel, l, a, b)
    return agreed, runs, act_runs

def agree(vault, effA, effB):
    """(agreed, place disputes) — see agree_detail."""
    return agree_detail(vault, effA, effB)[:2]

# ---- the keeper's ruling: a dispute resolved by the person, recorded as data ----------------
def load_rulings(path):
    """TSV, one ruling per line: note <TAB> reader (A|B) <TAB> why. A `#` line with no tab is a
    comment — a note path may itself begin with `#` (the inbox does)."""
    out = []
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip() or (line.startswith("#") and "\t" not in line):
                continue
            f = line.rstrip("\n").split("\t")
            out.append({"note": f[0].strip(), "reader": f[1].strip() if len(f) > 1 else "",
                        "why": f[2].strip() if len(f) > 2 else "", "_at": f"{os.path.basename(path)}:{n}"})
    return out

def rule(vault, effA, effB, rulings):
    """The keeper resolves a dispute by choosing one reader's reading of a note; that reading then
    stands for both, and the plan follows. A ruling reaches only a note the readers disputed — on
    WHERE or on WHAT ELSE — and both read: it cannot put an op into the plan that neither reader
    proposed, nor decide what was never in question, and it must say why. Returns
    (effA, effB, ruled, problems); on any problem nothing is ruled."""
    _, runs, acts = agree_detail(vault, effA, effB)
    disputed = {x["note"] for x in runs} | {x["note"] for x in acts}
    probs, ruled, A, B = [], [], dict(effA), dict(effB)
    for r in rulings:
        note, who, at = r["note"], r["reader"], r.get("_at", "a ruling")
        if who not in ("A", "B"):
            probs.append(f"{at}: reader must be A or B, not {who!r}"); continue
        if note not in effA or note not in effB:
            probs.append(f"{at}: {note}: not read by both readers"); continue
        if note not in disputed:
            probs.append(f"{at}: {note}: the readers did not dispute it — nothing to rule"); continue
        if not r.get("why"):
            probs.append(f"{at}: {note}: a ruling needs its reason"); continue
        A[note] = B[note] = (effA if who == "A" else effB)[note]
        ruled.append(dict(r))
    return (A, B, ruled, probs) if not probs else (effA, effB, [], probs)

# ---- the proposal: a plan made only of what both readings agree on ------------------------------
def _spec(lines):
    runs = []
    for l in sorted(lines):
        if runs and l == runs[-1][1] + 1:
            runs[-1][1] = l
        else:
            runs.append([l, l])
    return ",".join(f"L{a}-L{b}" if a != b else f"L{a}" for a, b in runs)

LEAVE = "(leave)"                        # a ≡-merged line goes nowhere: its destination already holds it

def propose(vault, cfg, agreed, runs, today=None):
    """Turn agreed lines into a plan the mover can prove. A note acts only if every non-blank line of
    it is agreed; a new note is made only if no line meant for it is under dispute; nothing lands in a
    note under dispute. A note whose lines go to more than one place, or into a note that is not
    new, is archived whole first ('<name> — original (<date>)') and partitioned from there: every line
    placed exactly once, the original kept. A `merge =` record gathers: the lines go into the note
    that holds the same claims in other words, for the keeper to distil by hand. A `merge ≡` record
    sends nothing: the destination already holds those very lines, so they are LEFT in the archived
    original and the destination is not touched — appending them would make the duplicate the
    reader found. Returns (plan lines, report)."""
    today = today or C.datetime.now().strftime("%Y-%m-%d")
    arch = list(cfg.get("para", C.PARA))[-1]
    exists = lambda p: os.path.lexists(os.path.join(vault, p))
    data = {rel: read_bytes(vault, rel) for rel in agreed}
    nb = {rel: nonblank(vault, rel) for rel in agreed}
    blocked = {r["note"] for r in runs}
    feeders = defaultdict(set)                      # dest → notes whose agreed lines go there
    for rel in agreed:
        for l, r in agreed[rel].items():
            feeders[final_dest(r, rel)].add(rel)
    while True:                                     # a dispute blocks every note it would leave half-done
        bad = set()
        for d, fs in feeders.items():
            if d in blocked or (exists(d) and d not in agreed) or (not exists(d) and fs & blocked):
                bad |= fs - blocked
        if not bad:
            break
        blocked |= bad
    place = {}                                      # every line → its final note; a blank line follows its neighbour
    for rel in sorted(set(agreed) - blocked):
        n, last = len(data[rel].splitlines(keepends=True)), None
        fd = {l: (LEAVE if agreed[rel][l]["op"] == "merge" and agreed[rel][l].get("relation") == "≡"
                  else final_dest(agreed[rel][l], rel)) for l in nb[rel]}
        first = fd[nb[rel][0]] if nb[rel] else rel
        place[rel] = {}
        for l in range(1, n + 1):
            last = fd.get(l, last)
            place[rel][l] = last or first
    contrib, order = defaultdict(list), []          # dest → [(note, lines)] in note order
    for rel in sorted(place):
        byd = defaultdict(list)
        for l, d in place[rel].items():
            byd[d].append(l)
        for d, ls in byd.items():
            order += [] if d in contrib else [d]
            contrib[d].append((rel, ls))
    moved, dissolved, used = {}, {}, set()
    for rel in sorted(place):
        ds = set(place[rel].values())
        if ds == {rel}:
            continue
        d = next(iter(ds))
        if len(ds) == 1 and d != LEAVE and not exists(d) and contrib[d][0][0] == rel:
            moved[rel] = d; continue
        stem, k = os.path.splitext(os.path.basename(rel))[0], 1
        arc = f"{arch}/{stem} — original ({today}).md"
        while exists(arc) or arc in used:
            k += 1; arc = f"{arch}/{stem} — original ({today}) {k}.md"
        used.add(arc); dissolved[rel] = arc
    plan, made = [], set()
    def mkdirs(p):
        segs = os.path.dirname(p).split("/")
        for i in range(1, len(segs) + 1):
            d = "/".join(segs[:i])
            if d and not os.path.isdir(os.path.join(vault, d)) and d not in made:
                made.add(d); plan.append(f"mkdir\t{d}")
    for rel, arc in dissolved.items():
        mkdirs(arc); plan += [f"mv\t{rel}\t{arc}", f"partition\t{arc}\t{hashlib.sha256(data[rel]).hexdigest()}"]
    for rel, d in moved.items():
        mkdirs(d); plan.append(f"mv\t{rel}\t{d}")
    for rel, arc in dissolved.items():
        left = [l for l, d in place[rel].items() if d == LEAVE]
        if left:
            plan.append(f"leave\t{arc}\t{_spec(left)}")
    gathered = {}
    for d in order:
        if d == LEAVE:
            continue
        live, first = exists(d) and d not in dissolved and d not in moved, True
        for rel, ls in contrib[d]:
            if (rel == d and rel not in dissolved) or moved.get(rel) == d:
                first = False; continue             # already there: its own lines, or the note itself moved in
            if first and not live:
                mkdirs(d)
            plan.append(f"{'extend' if live or not first else 'compose'}\t{d}\t{dissolved[rel]}\t{_spec(ls)}")
            first = False
        if len(contrib[d]) > 1:
            gathered[d] = [(rel, len(ls)) for rel, ls in contrib[d]]
    dedupe = [rel for rel in place if set(place[rel].values()) == {rel}
              and any(agreed[rel][l]["op"] == "dedupe" for l in nb[rel])]
    for rel in dedupe:
        plan.append(f"dedupe\t{rel}\t{arch}/{os.path.splitext(os.path.basename(rel))[0]} — before dedupe ({today}).md")
    acts = defaultdict(list)                        # the acts that need a person, not a mover
    for rel in place:
        seen = set()
        for l in nb[rel]:
            r = agreed[rel][l]
            if r["op"] in ("synthesize", "mark", "link", "horizon") and id(r) not in seen:
                seen.add(id(r)); acts[r["op"]].append((rel, r))
    names = defaultdict(set)
    for p in C.rels(vault):
        if p.endswith(".md") and p not in dissolved and p not in moved:
            names[os.path.basename(p).lower()].add(p)
    clash = sorted(d for d in order if not exists(d) and names.get(os.path.basename(d).lower(), set()) - {d})
    return plan, {"moved": moved, "split": {rel: sorted({d for d in place[rel].values()}) for rel in dissolved},
                  "gathered": gathered, "dedupe": dedupe, "acts": dict(acts), "blocked": sorted(blocked),
                  "disputes": runs, "acting": sorted(place), "name_clashes": clash,
                  "parallax": sum(1 for rel in place for r in {id(x): x for x in agreed[rel].values()}.values()
                                  if r.get("relation") == "parallax")}

# ---- a second read of part of the vault ------------------------------------------------------
def second_read_set(vault, cfg, effA, sample=0.15, seed=11, done=()):
    """When the first reader read everything, the second must read: every note the first wants to
    change or act on, and every existing note it would put lines into — no change runs without both
    readers — plus a seeded sample of the notes the first keeps, to measure how far its 'keep' can
    be trusted. `done`: notes the second reader has already read. Returns (must, sampled)."""
    import random
    must = set()
    for rel, assign in effA.items():
        for r in {id(x): x for x in assign.values()}.values():
            d = final_dest(r, rel)
            if r["op"] != "keep" or d != rel:
                must.add(rel)
                if d != rel and os.path.isfile(os.path.join(vault, d)):
                    must.add(d)
    keeps = sorted(set(effA) - must - set(done))
    k = min(len(keeps), max(1, round(len(keeps) * sample))) if keeps and sample else 0
    return sorted(must), sorted(random.Random(seed).sample(keeps, k))

def second_read_missing(vault, cfg, effA, effB):
    """The gate on a partial second read: the notes it had to read and did not."""
    return [n for n in second_read_set(vault, cfg, effA, sample=0)[0] if n not in effB]

# ---- convergence: the pass closes, or it did not ---------------------------------------------
def converge_targets(cfg, expect):
    """Every note a plan made, changed or moved — outside the archive, which keeps originals."""
    arch = list(cfg.get("para", C.PARA))[-1]
    ps = list(expect.get("added", {})) + list(expect.get("hashes", {})) + list(expect.get("moves", {}).values())
    return sorted({p for p in ps if p.endswith(".md") and p.split("/")[0] != arch})

def converge(vault, touched, effA, effB):
    """Read again by both readers, each touched note must come back placed where it is: nothing to
    split, gather, move or rewrite (∂ = 1; the Shadow Law's postcondition). More work on a second
    pass means the first did not close — a merge that made a note of two subjects fails here."""
    probs = []
    for t in touched:
        for name, eff in (("A", effA), ("B", effB)):
            if t not in eff:
                probs.append(f"reader {name} has not read {t} again"); continue
            for l in nonblank(vault, t):
                r = eff[t][l]
                if r["op"] not in ("keep", "link") or final_dest(r, t) != t:
                    probs.append(f"reader {name}: {t} L{l} — still {r['op']} → {final_dest(r, t)}"); break
    return probs

# ---- intake: one new note, read by two, placed by agreement — or told exactly what is missing ---
def intake(vault, cfg, note, recsA, recsB, today=None):
    """The intake gate for one new note. Both readers must have read it; every existing note either
    reader would put its lines into must be read by both as well (nothing lands in an unread note);
    then agreement and the plan. Returns a report: 'status' is PLAN (lines ready), READ (notes both
    must still read — listed), DISPUTE (the readers disagree on where), or PROBLEM (coverage). An
    empty plan is never called success: a blocked note is READ or DISPUTE, said so."""
    seen = {note}
    for rs in (recsA, recsB):
        for r in rs:
            d = r.get("dest") or ""
            if r.get("op") in PLACING and d and os.path.isfile(os.path.join(vault, d)):
                seen.add(d)
    want = sorted(seen)
    eA, pA = effective(vault, cfg, recsA, notes=want)
    eB, pB = effective(vault, cfg, recsB, notes=want)
    if note not in eA or note not in eB:
        return {"status": "PROBLEM", "problems": [p for p in pA + pB if p.startswith(("not read: " + note, note)) or note in p] or pA + pB}
    _, runs0, _ = agree_detail(vault, {note: eA[note]}, {note: eB[note]})     # first: do they agree WHERE it goes?
    if runs0:
        return {"status": "DISPUTE", "disputes": runs0}
    missing = sorted(n for n in want if n not in eA or n not in eB)
    if missing:
        return {"status": "READ", "must_read": missing,
                "why": "an existing note a reader would put lines into must be read by both before anything lands in it"}
    agreed, runs, acts = agree_detail(vault, eA, eB)
    plan, rep = propose(vault, cfg, agreed, runs, today)
    if any(r["note"] == note for r in runs):
        return {"status": "DISPUTE", "disputes": [r for r in runs if r["note"] == note], "act_disputes": acts}
    if note in rep["blocked"]:
        return {"status": "READ", "must_read": [], "why": f"blocked by the plan builder: {rep['blocked']} — a destination is under dispute or unread"}
    if not plan:
        dest = {final_dest(r, note) for r in agreed[note].values()}
        return {"status": "STAYS", "why": f"both readers keep it where it is ({sorted(dest)})", "acts": rep["acts"]}
    return {"status": "PLAN", "plan": plan, "report": rep}

def cmd_intake(V, cfg, argv):
    """intake <note> <A> <B> — the gate above, printed; writes the plan when there is one."""
    if len(argv) != 4:
        print("intake   : need <note> <reading A> <reading B>"); return 2
    note, A, B = argv[1], load_reading(argv[2]), load_reading(argv[3])
    r = intake(V, cfg, note, A, B)
    print(f"intake   : {note} — {r['status']}")
    for k in ("why",):
        if r.get(k): print("   " + r[k])
    for p in r.get("problems", [])[:10]: print("   " + p)
    for n in r.get("must_read", []): print("   must read (both): " + n)
    for d in r.get("disputes", []): print(f"   dispute L{d['first']}–L{d['last']}: A → {final_dest(d['a'], note)} · B → {final_dest(d['b'], note)}")
    if r["status"] == "PLAN":
        p = os.path.join(sema_dir(), f"intake-{C.ts()}.tsv")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(f"# intake plan for {note} — proposed, NOT ratified\n" + "".join(l + "\n" for l in r["plan"]))
        errs = C.simulate(V, C.load_plan(p), C.manifest_rows(V))[0]
        print("\n".join("   " + l for l in r["plan"])); print("   dry run: " + ("OK" if not errs else str(errs[:2]))); print("   → " + p)
        return 0 if not errs else 1
    return 0 if r["status"] in ("PLAN", "STAYS") else 1

# ---- what a person reads ---------------------------------------------------------------------
def index_md(ix):
    L = [f"# Organ index — {ix['when']}", "", f"{ix['notes']} notes · {ix['sections']} sections · {ix['ranked']} ranked "
         f"(≥ {MIN_WORDS} words) · model `{ix['model']}`. Scores only rank; the readings decide.", "", "## Controls", ""]
    L += [f"- copies {' = '.join(c['copies'])}: " + {True: "found each other", False: "NOT FOUND — do not trust this index",
                                                        None: "too short to rank"}[c["found"]] for c in ix["control"]]
    if ix["probe"]:
        L.append(f"- model probe: paraphrase {ix['probe']['paraphrase']} vs unrelated {ix['probe']['unrelated']} — "
                 + ("OK" if ix["probe"]["ok"] else "FAILED"))
    L += ["", "## Notes that may hold several subjects — least coherent first", ""]
    L += [f"- {w['coherence']:.3f} · `{w['note']}` ({w['sections']} sections, {w['words']:,} words) — farthest: "
          + ", ".join(f"{f['pid']} “{f['heading']}”" for f in w["farthest"]) for w in ix["within"][:40]]
    L += ["", "## Sections that may be one subject in two notes — strongest first", ""]
    L += [f"- {x['sim']:.3f} · `{x['a']['note']}` {x['a']['pid']} “{x['a']['heading']}” ↔ `{x['b']['note']}` "
          f"{x['b']['pid']} “{x['b']['heading']}”" for x in ix["across"][:80]]
    L += ["", "## Lines repeated inside a note", ""]
    L += [f"- `{rel}` — {r['lines']} line(s), {r['extra_copies']} extra copies"
          for rel, r in sorted(ix["repeats"].items(), key=lambda kv: -kv[1]["extra_copies"])]
    return "\n".join(L) + "\n"

def proposal_md(rep, plan_path, errors, agreed_lines, total_lines):
    q = lambda p: f"`{p}`"
    L = [f"# The organ's proposal — {C.datetime.now():%Y-%m-%d}", "",
         f"Read twice, independently: {agreed_lines:,} of {total_lines:,} lines agreed. {len(rep['acting'])} notes act; "
         f"{len(rep['blocked'])} wait for the keeper. Plan: {q(plan_path)} — dry run "
         + ("OK." if not errors else f"REFUSED ({len(errors)})."), ""] + [f"- ERROR {e}" for e in errors[:20]]
    def sec(title, rows, cap=300):
        L.extend(["", f"## {title}", ""] + (rows[:cap] or ["- none"]) + ([f"- … {len(rows) - cap} more"] if len(rows) > cap else []))
    sec("Split (∂) — one note held several subjects; its original is kept whole in the archive",
        [f"- {q(r)} → " + " · ".join(q(d) for d in ds) for r, ds in sorted(rep["split"].items())])
    sec("Gathered (γ) — one subject, from several notes, into one",
        [f"- {q(d)} ← " + " · ".join(f"{q(r)} ({n} lines)" for r, n in fs) for d, fs in sorted(rep["gathered"].items())])
    sec("Moved", [f"- {q(r)} → {q(d)}" for r, d in sorted(rep["moved"].items())])
    sec("Repeats removed — the original kept whole in the archive", [f"- {q(r)}" for r in sorted(rep["dedupe"])])
    for op, title in (("synthesize", "Syntheses proposed — written after you ratify; the sources stay"),
                      ("mark", "Boundaries to mark (δ)"), ("link", "Links (≅) — one structure in two domains: never merged"),
                      ("horizon", "The readers could not tell (horizon)")):
        sec(title, [f"- {q(r)} {x.get('lines', '*')} — {x.get('dest') or ', '.join(x.get('with', []))} — {x.get('why', '')}"
                    for r, x in rep["acts"].get(op, [])])
    sec("Name clashes — a new note would share its name with another", [f"- {q(d)}" for d in rep["name_clashes"]])
    sec("The keeper's list — the two readings disagree, so nothing is selected",
        [f"- {q(x['note'])} L{x['first']}–L{x['last']} — A: {x['a']['op']} → {x['a'].get('dest') or 'here'} "
         f"({x['a'].get('why', '')}) · B: {x['b']['op']} → {x['b'].get('dest') or 'here'} ({x['b'].get('why', '')})"
         for x in rep["disputes"]])
    L += ["", f"Parallax — kept apart on purpose: {rep['parallax']} record(s)."]
    return "\n".join(L) + "\n"

USAGE = """semantics.py — the semantic organ
  segment  "<note>"          the note's parts → state/parts-<name>.tsv
  parts    "<note>"          print the note's sha and parts (what a reader cites)
  index                      triage: sections + local embeddings → state/sema/index-<ts>.{json,md}
  place    <file> [--exclude <note>] [--calibrate]
                             the shortlist for a reader: the nearest notes (nearest section in each) and
                             folders by meaning, with how far the list can be trusted, measured on this
                             vault; NO HOME when nothing comes nearer than unrelated text. A reader
                             places; the keeper ratifies. A note already in the vault never finds itself
  reading  <A>               coverage of one reading (a .jsonl file, or a folder of them)
  reading  <file> --slice <slices.json> <id>   coverage of one reader's slice: its units, every line once
  intake   <note> <A> <B>    one new note: both read it; every existing note a reader would put it into
                             must be read by both; then agreement → plan (or READ / DISPUTE / STAYS, said so)
  propose  <A> <B> [--ruling <rulings.tsv>]
                             coverage of both → (the keeper's rulings on disputed notes: note TAB A|B TAB why)
                             → agreement → plan + proposal in state/sema/ → dry run
  converge <expect.json> <A> <B>   after a plan ran: its notes, read again, must need nothing more
  second   <A> [<B so far>]  the second reader's worklist: every change A proposes, the notes it would
                             change into, a sample of A's keeps → state/sema/slices-B.json
"""

def make_slices(vault, notes, budget=330_000, prefix="s"):
    """Pack notes into readers' slices of at most `budget` bytes, largest first. A note over the budget
    is cut at its parts (a part over the budget, at its lines) into line ranges covering it once."""
    units = []
    for n in sorted(notes, key=lambda n: -os.path.getsize(os.path.join(vault, n))):
        data = read_bytes(vault, n)
        if len(data) <= budget:
            units.append([n, "*", len(data)]); continue
        lines, acc, start = data.splitlines(keepends=True), 0, 1
        for p in segment(data):
            pieces = [(p["start"], p["end"], len(p["bytes"]))]
            if len(p["bytes"]) > budget:
                pieces, a, b = [], p["start"], 0
                for i in range(p["start"], p["end"] + 1):
                    b += len(lines[i - 1])
                    if b >= budget or i == p["end"]:
                        pieces.append((a, i, b)); a, b = i + 1, 0
            for s, e, b in pieces:
                if acc and acc + b > budget:
                    units.append([n, f"L{start}-L{s - 1}", acc]); start, acc = s, 0
                acc += b
        units.append([n, f"L{start}-L{len(lines)}", acc])
    slices = []
    for u in sorted(units, key=lambda u: -u[2]):
        for sl in slices:
            if sl["bytes"] + u[2] <= budget:
                sl["units"].append(u[:2]); sl["bytes"] += u[2]; break
        else:
            slices.append({"units": [u[:2]], "bytes": u[2]})
    for i, sl in enumerate(slices, 1):
        sl["id"] = f"{prefix}{i:02d}"
    return slices

def cmd_second(V, cfg, argv):
    """The second reader's worklist, sliced: what it must read, and a sample of the first's keeps."""
    effA, probs = effective(V, cfg, load_reading(argv[1]))
    if probs:
        print(f"second   : the first reading is incomplete — {len(probs)} problem(s)")
        for p in probs[:20]:
            print("   " + p)
        return 1
    done = sorted({r.get("note") for r in load_reading(argv[2])} & set(effA)) if len(argv) == 3 else []
    must, sampled = second_read_set(V, cfg, effA, done=done)
    todo = [n for n in must + sampled if n not in done]
    slices, d = make_slices(V, todo, prefix="b"), sema_dir()
    with open(os.path.join(d, "slices-B.json"), "w", encoding="utf-8") as fh:
        json.dump(slices, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(d, "second-read.json"), "w", encoding="utf-8") as fh:
        json.dump({"must": must, "sampled": sampled, "done": done}, fh, ensure_ascii=False, indent=1)
    print(f"second   : must {len(must)} · sampled keeps {len(sampled)} · already read {len(done)} → "
          f"{len(todo)} note(s) in {len(slices)} slice(s) → {os.path.join(d, 'slices-B.json')}")
    return 0

def cmd_propose(V, cfg, argv):
    """Coverage of both readings — the second may be partial if it covers every change the first
    proposes — then the keeper's rulings if any, agreement, the plan, its dry run, and the proposal
    a person reads."""
    rulings = load_rulings(argv[4]) if argv[3:4] == ["--ruling"] else []
    recsA, recsB = load_reading(argv[1]), load_reading(argv[2])
    inB = sorted({r.get("note") for r in recsB} & set(scope(V, cfg)))
    effs = []
    for path, recs, notes in ((argv[1], recsA, None), (argv[2], recsB, inB)):
        eff, probs = effective(V, cfg, recs, notes=notes)
        print(f"coverage : {path} — " + ("every line placed once" if not probs else f"{len(probs)} problem(s)"))
        for p in probs[:20]:
            print("   " + p)
        if probs:
            return 1
        effs.append(eff)
    effA, effB = effs
    partial = set(effB) != set(effA)
    if partial:
        missing = second_read_missing(V, cfg, effA, effB)
        print(f"second   : {len(effB)} of {len(effA)} notes read twice — " + (
            "it covers every change the first read proposes" if not missing
            else f"it MISSES {len(missing)} note(s) the first read would change"))
        for n in missing[:20]:
            print("   " + n)
        if missing:
            return 1
    ruled = []
    if rulings:
        effA, effB, ruled, probs = rule(V, effA, effB, rulings)
        print(f"ruling   : {argv[4]} — " + (f"{len(ruled)} note(s) ruled" if not probs else f"{len(probs)} problem(s)"))
        for p in probs[:20]:
            print("   " + p)
        if probs:
            return 1
    agreed, runs, act_runs = agree_detail(V, effA, effB)
    plan, rep = propose(V, cfg, agreed, runs)
    rep["act_disputes"], rep["rulings"] = act_runs, ruled
    base = os.path.join(sema_dir(), f"proposal-{C.ts()}")
    with open(base + ".tsv", "w", encoding="utf-8") as fh:
        fh.write("# the organ's plan — proposed, NOT ratified\n" + "".join(l + "\n" for l in plan))
    errors = C.simulate(V, C.load_plan(base + ".tsv"), C.manifest_rows(V))[0] if plan else []
    md = proposal_md(rep, base + ".tsv", errors, sum(len(a) for a in agreed.values()),
                     sum(len(nonblank(V, r)) for r in agreed))
    if ruled:
        md += ("\n## The keeper ruled — one reader's reading stands for both\n\n"
               + "\n".join(f"- `{x['note']}` — reader {x['reader']}: {x['why']}" for x in ruled) + "\n")
    md += ("\n## The readers agree where the lines go, but not on what else to do — so it is not done\n\n"
           + ("\n".join(f"- `{x['note']}` L{x['first']}–L{x['last']} — A: {x['a']['op']} ({x['a'].get('why', '')}) · "
                        f"B: {x['b']['op']} ({x['b'].get('why', '')})" for x in act_runs) or "- none") + "\n")
    if partial:
        must = set(second_read_set(V, cfg, effA, sample=0)[0])
        checked = set(effB) - must
        kept = len(checked - {r["note"] for r in runs})
        rep["read_once"], rep["keep_check"] = len(set(effA) - set(effB)), [kept, len(checked)]
        md = md.replace("Read twice, independently:", "Read twice where it mattered:", 1).replace("\n\n", (
            f"\n\nThe first reader read every note. The second re-read every note the first would change or put "
            f"lines into, and {len(checked)} of the notes it kept — agreeing on {kept} of them. "
            f"{rep['read_once']} notes were read once, and stay as they are.\n\n"), 1)
    with open(base + ".md", "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1, default=str)
    print(f"propose  : {len(runs)} dispute(s) · {len(plan)} plan line(s) · dry run "
          + ("OK" if not errors else f"REFUSED ({len(errors)})") + f" → {base}.md")
    return 0 if not errors else 1

def slice_problems(vault, cfg, recs, units):
    """One reader's slice: each unit is a whole note ("*") or a line range of one. Every line of every
    unit is placed exactly once, by records pinned to the note as it is now — and nothing outside."""
    probs, want, got, stars = [], {}, defaultdict(Counter), Counter()
    for note, spec in units:
        data = read_bytes(vault, note)
        lines = set(range(1, len(data.splitlines(keepends=True)) + 1)) if spec == "*" else \
            {l for a, b in spans(spec, segment(data)) for l in range(a, b + 1)}
        want[note] = want.get(note, set()) | lines
    for r in recs:
        at, note = r.get("_at", "a record"), r.get("note")
        if "_bad" in r:
            probs.append(f"{at}: not JSON: {r['_bad']}"); continue
        if note not in want:
            probs.append(f"{at}: not in this slice: {note!r}"); continue
        data = read_bytes(vault, note)
        n, bad = len(data.splitlines(keepends=True)), validate(r, hashlib.sha256(data).hexdigest(), cfg)
        probs += [f"{at}: {note}: {b}" for b in bad]
        spec = str(r.get("lines", "*")).strip() or "*"
        if spec == "*":
            if len(want[note]) != n:
                probs.append(f"{at}: {note}: '*' in a slice holding only part of the note — name the lines")
            stars[note] += 1; continue
        try:
            ls = [l for a, b in spans(spec, segment(data)) for l in range(a, b + 1)]
        except (KeyError, ValueError) as e:
            probs.append(f"{at}: {note}: no such part or line: {e}"); continue
        out = [l for l in ls if l not in want[note]]
        if out:
            probs.append(f"{at}: {note}: L{out[0]} is outside this slice"); continue
        got[note].update(ls)
    for note, lines in want.items():
        twice = sorted(l for l, c in got[note].items() if c > 1)
        missing = sorted(lines - set(got[note]))
        if twice:
            probs.append(f"{note}: L{twice[0]} placed twice")
        if stars[note] > 1:
            probs.append(f"{note}: {stars[note]} '*' records — at most one")
        if missing and not stars[note]:
            probs.append(f"{note}: {len(missing)} line(s) not read — the first is L{missing[0]}")
    return probs

def main(argv):
    V, cfg = C.VAULT, C.CFG
    if argv[:1] == ["reading"] and len(argv) == 5 and argv[2] == "--slice":
        with open(os.path.expanduser(argv[3]), encoding="utf-8") as fh:
            sl = next((s for s in json.load(fh) if s["id"] == argv[4]), None)
        if sl is None:
            print(f"reading  : no slice {argv[4]!r} in {argv[3]}"); return 2
        probs = slice_problems(V, cfg, load_reading(argv[1]), [tuple(u) for u in sl["units"]])
        print(f"reading  : slice {argv[4]} — {len(sl['units'])} unit(s) · {len(probs)} problem(s)")
        for p in probs[:40]:
            print("   " + p)
        return 0 if not probs else 1
    if argv[:1] == ["intake"]:
        return cmd_intake(V, cfg, argv)
    if argv[:1] == ["place"] and len(argv) >= 2:
        return cmd_place(V, cfg, argv)
    if argv[:1] == ["segment"] and len(argv) == 2:
        return cmd_segment(argv[1])
    if argv[:1] == ["parts"] and len(argv) == 2:
        data = read_bytes(V, argv[1])
        print(f"{argv[1]}\tnote_sha {hashlib.sha256(data).hexdigest()}\t{len(data.splitlines())} lines")
        for p in segment(data):
            print(f"{p['id']}\tL{p['start']}-L{p['end']}\t{len(p['bytes'].split())} words\t{p['heading'][:70]}")
        return 0
    if argv == ["index"]:
        ix, stamp = index(V, cfg, cache_path=os.path.join(sema_dir(), "embeddings.json")), C.ts()
        base = os.path.join(sema_dir(), f"index-{stamp}")
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(ix, fh, ensure_ascii=False, indent=1)
        with open(base + ".md", "w", encoding="utf-8") as fh:
            fh.write(index_md(ix))
        print(f"index    : {ix['notes']} notes · {ix['sections']} sections · {ix['ranked']} ranked → {base}.md")
        print("           controls: " + ("PASS" if index_ok(ix) else "FAIL — do not trust this index"))
        return 0 if index_ok(ix) else 1
    if argv[:1] == ["reading"] and len(argv) == 2:
        eff, probs = effective(V, cfg, load_reading(argv[1]))
        print(f"reading  : {len(eff)} notes read cleanly · {len(probs)} problem(s)")
        for p in probs[:40]:
            print("   " + p)
        return 0 if not probs else 1
    if argv[:1] == ["second"] and len(argv) in (2, 3):
        return cmd_second(V, cfg, argv)
    if argv[:1] == ["propose"] and (len(argv) == 3 or (len(argv) == 5 and argv[3] == "--ruling")):
        return cmd_propose(V, cfg, argv)
    if argv[:1] == ["converge"] and len(argv) == 4:
        with open(argv[1], encoding="utf-8") as fh:
            touched = converge_targets(cfg, json.load(fh))
        effA, pA = effective(V, cfg, load_reading(argv[2]), notes=touched)
        effB, pB = effective(V, cfg, load_reading(argv[3]), notes=touched)
        probs = pA + pB + converge(V, touched, effA, effB)
        print(f"converge : {len(touched)} note(s) the plan touched · " + ("CLOSED — nothing more to do" if not probs else "OPEN"))
        for p in probs[:40]:
            print("   " + p)
        return 0 if not probs else 1
    print(USAGE + "\n" + READING_FORMAT)
    return 2

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
