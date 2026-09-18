#!/usr/bin/env python3
"""The Archivist — a local model behind a local page, keeping the keeper's Second Brain.

He stores and retrieves by meaning, through the organ, never past it:
  capture → # INBOX (the keeper's click is the capture)
  place   → the shortlist, two local readers, the intake gate  (a proposal)
  ratify  → the keeper's word on the page → snapshot, rehearsal, the mover, verify, the Acta
  ask     → sections retrieved by meaning, an answer as a Markdown file with its sources; the vault read-only

The model is whatever OpenAI-compatible server the config names (llama-server by default). Nothing here
moves a file: the mover does, under every gate the law gives it. Nothing here deletes.
"""
import os, sys, json, re, time, hashlib, threading, subprocess, urllib.request, http.server
from datetime import datetime
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import cerebrum as C, semantics as S, checks as K

CFG = C.CFG.get("archivist", {})
LLM_URL = CFG.get("llm_url", "http://127.0.0.1:8080")
MODEL = CFG.get("model", "local")
CTX_CHARS = int(CFG.get("ctx_chars", 9000))            # what fits beside the brief in the model's window
PORT = int(CFG.get("port", 8765))
ANSWERS = os.path.join(C.STATE, "answers")
CACHE_PATH = None                                        # the embeddings cache; None = the organ's own (state/sema)
AUTO_PLACE = bool(CFG.get("auto_place", True))           # 2026-09-15, the keeper: an agreed placement moves itself; the inbox is a doorway, not a heap
QUEUE_DIR = os.path.join(C.STATE, "queue")               # agreed plans waiting for Obsidian to close
LLM_UNIT = CFG.get("llm_unit", "archivist-llm.service")  # 2026-09-17, the keeper: the model runs only when he turns it on (the page's switch)

def _obsidian_open(vault):
    return C._is_real(vault) and C._obsidian_running()
LOG = os.path.join(C.STATE, "archivist.log")

def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')}\t{msg}\n")

# ---- the ledger: every input, where it went and why; every retrieval, what went out and why ---------
LEDGER_DIR = os.path.join(C.STATE, "ledger")

def ledger(kind, **rec):
    """The Builder's ledger (cerebrum.ledger), directed at LEDGER_DIR so tests can point it elsewhere."""
    real = C.STATE
    try:
        C.STATE = os.path.dirname(LEDGER_DIR); return C.ledger(kind, **rec)
    finally:
        C.STATE = real

# ---- the model ----------------------------------------------------------------------------------
def llm(messages, temperature=0.2, max_tokens=700, seed=None, url=None):
    body = {"model": MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if seed is not None:
        body["seed"] = seed
    req = urllib.request.Request((url or LLM_URL).rstrip("/") + "/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        log(f"model\tHTTP {e.code}\t{detail}")
        raise ModelRefused(e.code, detail) from None

class ModelRefused(Exception):
    def __init__(self, code, detail):
        super().__init__(f"the model refused (HTTP {code}): {detail}"); self.code, self.detail = code, detail
    def overflow(self):
        """(prompt tokens, window) when the refusal is an overflow, else None."""
        m = re.search(r"\((\d+) tokens\) exceeds the available context size \((\d+) tokens\)", self.detail)
        return (int(m.group(1)), int(m.group(2))) if m else None

def n_ctx(url=None):
    """The model's window, asked of the server; 4096 if it will not say."""
    try:
        d = json.load(urllib.request.urlopen((url or LLM_URL).rstrip("/") + "/props", timeout=5))
        return int(d.get("default_generation_settings", {}).get("n_ctx") or 4096)
    except Exception:
        return 4096

def token_count(text, url=None):
    """Counted by the server's own tokenizer when it offers one; else a conservative 2.2 chars per token
    (measured 2026-09-11 on this vault's text: 13,964 chars → 5,982 tokens)."""
    try:
        req = urllib.request.Request((url or LLM_URL).rstrip("/") + "/tokenize", data=json.dumps({"content": text}).encode(),
                                     headers={"Content-Type": "application/json"})
        return len(json.load(urllib.request.urlopen(req, timeout=60))["tokens"])
    except Exception:
        return int(len(text) / 2.2) + 1

def model_reachable():
    try:
        urllib.request.urlopen(LLM_URL.rstrip("/") + "/v1/models", timeout=5); return True
    except Exception:
        return False

def model_unit_state():
    """The model server's systemd user unit: active, activating, inactive, failed — or unknown if systemctl cannot say."""
    try:
        return subprocess.run(["systemctl", "--user", "is-active", LLM_UNIT], capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"

def switch_model(on):
    """The keeper's switch: start or stop the model server. Off by default; it holds ~9 GB of memory while it runs."""
    act = "start" if on else "stop"
    try:
        r = subprocess.run(["systemctl", "--user", act, LLM_UNIT], capture_output=True, text=True, timeout=60)
        err = r.stderr.strip() if r.returncode else ""
    except Exception as e:
        err = repr(e)
    log(f"model\t{act}\t{LLM_UNIT}\t{err[:200] or 'ok'}")
    return {"ok": not err, "state": model_unit_state(), "error": err[:300] or None}

# ---- polish: a raw thought made fit for the vault, with the raw kept verbatim beneath -----------------
POLISH = """You are the Archivist's editor. Rewrite the keeper's raw note into clean, professional Markdown for a personal knowledge vault:
fix spelling, grammar and punctuation; organize into short paragraphs, with a heading or two only where the text has clear parts; keep the keeper's
own terms and names exactly; keep every claim and drop none; ADD NOTHING — no new facts, no examples, no commentary, no preamble.
Output only the polished Markdown."""

def polish(text, url=None):
    """The polished text, and the raw kept beneath it, folded and verbatim. If the model returns nothing usable,
    the raw text is used as is and the header says so."""
    raw = text.strip("\n")
    try:
        out = llm([{"role": "system", "content": POLISH}, {"role": "user", "content": raw}], temperature=0.1,
                  max_tokens=min(1500, int(len(raw) / 2) + 400), url=url).strip()
    except Exception as e:
        log(f"polish\tfailed\t{e!r}"); out = ""
    ok = bool(out) and len(out) >= len(raw) * 0.5
    body = (out if ok else raw) + "\n\n> [!note]- Raw capture, verbatim — the keeper's own words as sent\n" + "\n".join("> " + l for l in raw.splitlines()) + "\n"
    return body, ok

# ---- capture: the keeper's click puts a note in the inbox ---------------------------------------
def safe_title(title):
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", title)                      # a newline in a filename breaks every manifest after it
    t = re.sub(r"[\\/:*?\"<>|]", "—", t.strip()).strip(". ")
    return (t or "Captured")[:120]

def capture(vault, cfg, title, text, source="", do_polish=False, url=None):
    inbox = list(cfg.get("para", C.PARA))[0]
    name = safe_title(title); rel = f"{inbox}/{name}.md"; k = 1
    while os.path.lexists(os.path.join(vault, rel)):
        k += 1; rel = f"{inbox}/{name} {k}.md"
    polished = False
    if do_polish:
        text, polished = polish(text, url=url)
    head = (f"---\ncaptured: {datetime.now().strftime('%Y-%m-%d %H:%M')}\nvia: the Archivist\n" + (f"source: {source}\n" if source else "")
            + ("polished: by the Archivist — spelling, grammar and structure; the raw capture is kept verbatim at the end\n" if polished
               else "polished: no — the raw text as sent\n" if do_polish else "") + "---\n\n")
    body = text if text.endswith("\n") else text + "\n"
    with open(os.path.join(vault, rel), "xb") as fh:
        fh.write((head + body).encode("utf-8"))
    log(f"capture\t{rel}")
    ledger("intake", event="capture", note=rel, title=title, source=source, polished=polished, chars=len(text))
    return rel

def polish_note(vault, cfg, rel, url=None):
    """Polish a note the Archivist captured into the inbox (and only such a note): the header is kept, the body
    polished, the raw body kept verbatim beneath. A note already polished, or not the Archivist's, is refused."""
    inbox = list(cfg.get("para", C.PARA))[0]
    p = os.path.join(vault, rel)
    if not rel.startswith(inbox + "/") or not os.path.isfile(p):
        return {"error": "only a note in the inbox"}
    text = open(p, encoding="utf-8").read()
    if not text.startswith("---\n") or "via: the Archivist" not in text.split("\n---", 1)[0]:
        return {"error": "only a note the Archivist captured (its header says so)"}
    head, body = text.split("\n---\n", 1)
    if "polished: by the Archivist" in head:
        return {"error": "already polished"}
    new_body, ok = polish(body.strip("\n"), url=url)
    head = head.replace("polished: no — the raw text as sent\n", "")
    head += "\npolished: by the Archivist — spelling, grammar and structure; the raw capture is kept verbatim at the end" if ok else "\npolished: no — the model returned nothing usable"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(head + "\n---\n\n" + new_body)
    log(f"polish\t{rel}\t{'ok' if ok else 'raw kept'}")
    return {"note": rel, "polished": ok}

# ---- the local readers ----------------------------------------------------------------------------
BRIEF = """You are one of two independent readers of a personal Obsidian vault organized by PARA:
`# INBOX` (unsorted), `1 PROJECTS` (a goal with a deadline), `2 AREAS` (a standing responsibility), `3 RESOURCES` (a topic), `4 ARCHIVE`.
A note goes where it will be used soonest: project, else area, else resource. At most category → container → note.
You are given ONE note, its parts (P000, P001, …), and a shortlist of the notes nearest to it in meaning with how far that list can be trusted.
Decide where the note's lines belong. Write JSON records, one per line, no prose, no code fences:
{"lines": "*" or "P001-P004", "op": "keep" or "merge", "dest": "" (stays) or "folder/Existing Note.md" or "folder/New Note.md", "relation": "" or "=" or "≡", "at": "" or "after P012", "subject": "...", "use": "project: X" / "area: X" / "resource: X", "why": "one line, from the text"}
Rules: every part exactly once; "*" alone means the whole note. `dest` "" means it stays where it is. Use an existing note's exact path from the shortlist when the lines belong inside it, and `at` for the section of THAT note after which they go (its parts are listed). `op` is "keep" for lines that belong in the destination but are NOT there yet (the usual case — new material joining its note); `merge` ONLY when the destination ALREADY holds these very claims ("=") or this very text ("≡") — a duplicate, not new material. A new note needs a clear title in the right folder. Prefer keeping over moving unless the reason is plain from the text. Never invent paths."""

def reader_prompt(vault, cfg, note, shortlist, cal, dest_parts, url=None, max_tokens=700):
    """The reader's prompt, made to FIT the model's window: the server counts the tokens; what does not fit is
    dropped in this order — the destinations' part lists (to the lead only, then to 20 parts), then the note's
    text (cut, with the parts list left whole so the reader still sees the shape)."""
    data = S.read_bytes(vault, note); parts = S.segment(data)
    text = data.decode("utf-8", "replace")
    def plist_text(cap):
        shown = parts[:cap]
        return "\n".join(f"{p['id']} L{p['start']}-L{p['end']} {p['heading'][:60]}" for p in shown) + (
            f"\n… {len(parts) - cap} more parts up to {parts[-1]['id']} (L{parts[-1]['end']}); \"*\" covers them all" if len(parts) > cap else "")
    plist = plist_text(80)
    sl = "\n".join(f"{i}. {x['note']}  (sim {x['sim']}; nearest section {x['section']['pid']} {x['section']['heading'][:50]})"
                   for i, x in enumerate(shortlist.get("notes", [])[:6], 1)) or "(nothing near)"
    trust = (f"Measured on this vault: of {cal['trials']} sections lifted from their notes, the nearest other note was in the same folder "
             f"{cal['same_folder_top1']} times. The list narrows your reading; it does not place.") if cal and cal.get("trials") else ""
    def dp_text(dests, cap):
        out = ""
        for d, ps in dests.items():
            out += f"\nParts of `{d}` (for `at`):\n" + "\n".join(f"  {p['id']} L{p['start']}-L{p['end']} {p['heading'][:50]}" for p in ps[:cap]) + ("\n  …\n" if len(ps) > cap else "\n")
        return out
    stays = ("" if shortlist.get("notes") else
             "\nThis note is a DESTINATION being read so that lines may land in it. If it stays as it is — the usual case — answer with exactly one record: "
             '{"lines": "*", "op": "keep", "dest": "", "subject": "…", "use": "…", "why": "…"}. Add records only for a run that belongs elsewhere.\n')
    def build(dp, body, pl):
        return (f"NOTE: `{note}`\nParts:\n{pl}\n\nShortlist (nearest notes by meaning):\n{sl}\n{trust}{stays}\n{dp}\n"
                f"--- the note's text ---\n{body}\n--- end ---\nWrite the records now.")
    limit = min(n_ctx(url), int(CFG.get("prompt_tokens", 6000)) + max_tokens) - max_tokens - 96   # a CPU model slows with context: cap the prompt
    lead = dict(list(dest_parts.items())[:1])
    for dests, cap, pcap in ((dest_parts, 60, 80), (dest_parts, 20, 80), (lead, 20, 60), (lead, 8, 40), ({}, 0, 40), ({}, 0, 20), ({}, 0, 10)):
        dp, pl = dp_text(dests, cap), plist_text(pcap)
        body = text
        for _ in range(12):
            p = build(dp, body, pl)
            if token_count(BRIEF + p, url) <= limit:
                return p
            keep = int(len(body) * 0.7)
            if keep < 400:
                break
            body = text[:keep] + f"\n[… {len(text) - keep} more characters not shown — judge the whole from its parts list]"
    return build("", text[:400] + "\n[… cut to fit the model's window]", plist_text(10))

def parse_records(out):
    recs = []
    for line in out.splitlines():
        line = line.strip().strip("`")
        if not line.startswith("{"):
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            recs.append(r)
    return recs

def local_read(vault, cfg, note, shortlist, cal, who, tries=3, url=None):
    """One local reader: prompt, parse, fill what the model must not compute (note, sha), validate, retry with
    the problems. Returns (records, problems, transcript)."""
    dest_parts = {}
    for x in shortlist.get("notes", [])[:6]:
        d = x["note"]
        if os.path.isfile(os.path.join(vault, d)):
            dest_parts[d] = S.segment(S.read_bytes(vault, d))
    sha = hashlib.sha256(S.read_bytes(vault, note)).hexdigest()
    system = BRIEF + f"\nYou are reader {who}."
    seed = 11 if who == "A" else 23
    transcript, probs, recs, refusal = [], ["no answer"], [], ""
    for attempt in range(tries):
        # every attempt is a fresh two-message conversation, refitted to the window; a retry carries only the refusal
        extra = int(len(refusal) / 2.2) + 1
        prompt = reader_prompt(vault, cfg, note, shortlist, cal, dest_parts, url=url, max_tokens=700 + extra) + refusal
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        log(f"reader\t{who}\t{note}\tattempt {attempt}\ttokens {token_count(system + prompt, url)}")
        try:
            out = llm(msgs, temperature=0.2 if who == "A" else 0.5, seed=seed + attempt, url=url)
        except ModelRefused as e:
            ov = e.overflow()
            if not ov:
                raise
            over = ov[0] - ov[1] + 256                    # the template's own tokens, and a margin, were not in our count
            prompt = reader_prompt(vault, cfg, note, shortlist, cal, dest_parts, url=url, max_tokens=700 + extra + over) + refusal
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
            log(f"reader\t{who}\t{note}\tattempt {attempt}\trefit after overflow by {ov[0] - ov[1]}\ttokens {token_count(system + prompt, url)}")
            out = llm(msgs, temperature=0.2 if who == "A" else 0.5, seed=seed + attempt, url=url)
        transcript.append(out)
        recs = parse_records(out)
        for r in recs:
            r["note"], r["note_sha"] = note, sha
            r.setdefault("relation", ""); r.setdefault("with", []); r.setdefault("at", ""); r.setdefault("dest", "")
            r.setdefault("subject", "?"); r.setdefault("use", "?"); r.setdefault("why", "")
            r["_at"] = f"reader-{who}:{attempt}"
        if not recs:
            probs = ["no JSON records in the answer"]
        else:
            _, probs = S.effective(vault, cfg, recs, notes=[note])
        if not probs:
            break
        refusal = "\n\nYour previous records were refused by the organ:\n" + "\n".join("- " + p for p in probs[:8]) + "\nWrite the corrected records, all of them, JSON lines only."
    return recs, probs, transcript

# ---- place: shortlist → two readers → the intake gate ---------------------------------------------
def place_note(vault, cfg, note, url=None, say=lambda *_: None):
    cache = CACHE_PATH or os.path.join(S.sema_dir(), "embeddings.json")
    probs = S.place_control(vault, cfg, cache_path=cache)
    if probs:
        return {"status": "PROBLEM", "problems": ["the shortlist's control failed: " + p for p in probs]}
    calp = os.path.join(S.sema_dir(), "place-calibration.json")
    cal = json.load(open(calp)) if os.path.exists(calp) else S.place_calibrate(vault, cfg, cache_path=cache)
    sl = S.place(vault, cfg, S.read_bytes(vault, note), cache_path=cache, exclude=[note])
    say("shortlist ready")
    A, pA, tA = local_read(vault, cfg, note, sl, cal, "A", url=url); say("reader A done")
    B, pB, tB = local_read(vault, cfg, note, sl, cal, "B", url=url); say("reader B done")
    if pA or pB:
        probs = [f"reader A: {p}" for p in pA] + [f"reader B: {p}" for p in pB]
        log(f"place\t{note}\tPROBLEM\t" + " · ".join(probs)[:400])
        return {"status": "PROBLEM", "shortlist": sl, "problems": probs, "readers": {"A": A, "B": B}, "transcripts": {"A": tA, "B": tB}}
    r = S.intake(vault, cfg, note, A, B)
    if r["status"] == "READ":                               # one more round: both read what they would put lines into
        for d in r["must_read"]:
            sl_d = {"notes": []}
            a2, pa2, _ = local_read(vault, cfg, d, sl_d, cal, "A", url=url)
            b2, pb2, _ = local_read(vault, cfg, d, sl_d, cal, "B", url=url)
            if pa2 or pb2:
                probs = [f"second round on {d}: " + p for p in pa2 + pb2]
                log(f"place\t{note}\tPROBLEM\t" + " · ".join(probs)[:400])
                return {"status": "PROBLEM", "shortlist": sl, "problems": probs, "readers": {"A": A, "B": B}}
            A, B = A + a2, B + b2
        r = S.intake(vault, cfg, note, A, B)
    r["shortlist"] = sl; r["readers"] = {"A": A, "B": B}
    if r["status"] == "DISPUTE":
        ledger("intake", event="dispute", note=note, runs=[{"lines": f"L{d['first']}-L{d['last']}", "A": S.final_dest(d["a"], note), "B": S.final_dest(d["b"], note),
                                                              "A_why": d["a"].get("why", ""), "B_why": d["b"].get("why", "")} for d in r.get("disputes", [])])
    if r["status"] == "PLAN":
        os.makedirs(S.sema_dir(), exist_ok=True)
        p = C.fresh_path(os.path.join(S.sema_dir(), f"intake-{C.ts()}.tsv"))
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(f"# the Archivist's intake plan for {note} — proposed, NOT ratified\n" + "".join(l + "\n" for l in r["plan"]))
        r["plan_path"] = p
        r["placed_into"] = _plan_destination(r["plan"])
    log(f"place\t{note}\t{r['status']}\t" + (r.get("why", "") or ", ".join(r.get("must_read", [])))[:200])
    ledger("intake", event="place", note=note, status=r["status"], plan=r.get("plan_path", ""),
           shortlist=[(x["note"], x["sim"]) for x in sl.get("notes", [])[:5]],
           readers={w: [{"lines": x["lines"], "op": x["op"], "dest": x.get("dest", ""), "at": x.get("at", ""), "why": x.get("why", "")} for x in r["readers"][w] if x["note"] == note] for w in ("A", "B")},
           plan_lines=r.get("plan", []), auto_place=AUTO_PLACE)
    if r["status"] == "PLAN" and AUTO_PLACE and r.get("plan_path"):
        if _obsidian_open(vault):
            enqueue(r["plan_path"]); r["queued"] = True
            log(f"auto-place\t{note}\tQUEUED until Obsidian closes\t{r.get('placed_into', '')}")
        else:
            r["placed"] = ratify(vault, cfg, r["plan_path"], intake=True)
            log(f"auto-place\t{note}\t{'GREEN' if r['placed'].get('ok') else 'RED'}\t{r.get('placed_into', '')}")
    return r

def _plan_destination(plan_lines):
    """The note a capture lands in: the first insert/append/merge/extend/compose target, else the mv destination."""
    for l in plan_lines:
        f = l.split("\t")
        if f[0] in ("insert", "append", "merge", "extend", "compose") and len(f) > 1:
            return f[1]
    for l in plan_lines:
        f = l.split("\t")
        if f[0] == "mv" and len(f) > 2 and not f[2].startswith("4 ARCHIVE/"):
            return f[2]
    return ""

def _plan_source(plan_path):
    """The inbox note a plan starts from: its first mv's source (an intake plan always begins by archiving the capture)."""
    for l in open(plan_path, encoding="utf-8"):
        f = l.rstrip("\n").split("\t")
        if f[0] == "mv" and len(f) > 1:
            return f[1]
    return ""

def enqueue(plan_path):
    os.makedirs(QUEUE_DIR, exist_ok=True)
    with open(os.path.join(QUEUE_DIR, os.path.basename(plan_path)), "w", encoding="utf-8") as fh:
        fh.write(os.path.abspath(plan_path) + "\n")

def drain_queue(vault, cfg):
    """Run every queued agreed placement, oldest first, if Obsidian is closed. Returns [(plan, ok)]. A plan the dry run
    now refuses (the vault changed) is left in the queue and logged; the note stays in the inbox for the keeper."""
    if not os.path.isdir(QUEUE_DIR) or _obsidian_open(vault):
        return []
    out = []
    for q in sorted(os.listdir(QUEUE_DIR), key=lambda n: os.path.getmtime(os.path.join(QUEUE_DIR, n))):
        qp = os.path.join(QUEUE_DIR, q); plan = open(qp, encoding="utf-8").read().strip()
        if not os.path.isfile(plan):
            os.remove(qp); continue
        src = _plan_source(plan)
        if src and not os.path.isfile(os.path.join(vault, src)):     # the keeper removed or moved the note himself (2026-09-15)
            os.remove(qp); log(f"auto-place\twithdrawn {q}\tthe note is gone: {src} — the keeper's hand")
            out.append((plan, False)); continue
        res = ratify(vault, cfg, plan, intake=True)
        if res.get("ok"):
            log(f"auto-place\tqueued {q}\tGREEN"); os.remove(qp)
        else:                                        # withdrawn with its reason on record; the note stays in the inbox for the keeper
            why = " · ".join(f"{s[0]}: {str(s[1])[:120]}" for s in res.get("steps", []))
            log(f"auto-place\tqueued {q}\tRED — withdrawn: {why[:300]}")
            ledger("intake", event="auto-place-red", plan=plan, steps=res.get("steps", []), detail=(res.get("detail") or "")[:600])
            os.remove(qp)
        out.append((plan, bool(res.get("ok"))))
    return out

def queue_worker(vault, cfg, every=15):
    """In the service: every `every` seconds, if Obsidian has been closed for two looks in a row, drain the queue."""
    was_closed = False
    while True:
        time.sleep(every)
        try:
            closed = not _obsidian_open(vault)
            if closed and was_closed and os.path.isdir(QUEUE_DIR) and os.listdir(QUEUE_DIR):
                drain_queue(vault, cfg)
            was_closed = closed
        except Exception as e:
            log(f"error\tqueue\t{e!r}")

# ---- ratify: the keeper's word → the mover under every gate ----------------------------------------
RATIFY_LOCK = threading.Lock()      # one run on the vault at a time: the page, a handler and the queue worker share the mover (2026-09-15)

def ratify(vault, cfg, plan_path, frozen_live=True, intake=False):
    """Rehearsal first (it moves nothing), then snapshot, manifest, the mover, a separate verify, the Acta, the registry.
    Serialised: two runs at once would read each other's moves as unratified."""
    with RATIFY_LOCK:
        return _ratify(vault, cfg, plan_path, frozen_live, intake)

def _ratify(vault, cfg, plan_path, frozen_live, intake):
    import subprocess
    py, here = sys.executable, HERE
    env = dict(os.environ, VAULT=vault)
    def run(*args):
        r = subprocess.run([py, *args], cwd=here, capture_output=True, text=True, env=env)
        return r.returncode, (r.stdout + r.stderr).strip()
    steps = []
    if C._is_real(vault) and C._obsidian_running():
        return {"ok": False, "steps": [("Obsidian", "refused — close Obsidian first")]}
    rc, out = run("rehearse.py", "--plan", plan_path, "--no-snapshot", *(["--frozen-live"] if frozen_live else []))
    steps.append(("rehearsal", [l for l in out.splitlines() if l.startswith("rehearse : ")][-1] if out else ""))
    if rc: return {"ok": False, "steps": steps, "detail": out[-1500:]}
    rc, out = run("cerebrum.py", "snapshot"); steps.append(("snapshot", out.splitlines()[-1] if out else ""))
    if rc: return {"ok": False, "steps": steps}
    rc, out = run("cerebrum.py", "manifest"); steps.append(("manifest", out.splitlines()[-1] if out else ""))
    if rc: return {"ok": False, "steps": steps}
    before = sorted(__import__("glob").glob(os.path.join(C.STATE, "manifest-*.tsv")), key=os.path.getmtime)[-1]
    rc, out = run("cerebrum.py", "move", "--plan", plan_path, "--before", before, "--apply", "--i-ratified", *(["--frozen-live"] if frozen_live else []))
    steps.append(("the mover", [l for l in out.splitlines() if l.startswith("move")][-1] if out else ""))
    if rc:
        if intake:
            undos = [u for u in __import__("glob").glob(os.path.join(C.STATE, "undo-*.tsv")) if os.path.getmtime(u) >= os.path.getmtime(before)]
            if undos:                                # the mover acted before it went red: reverse what it did
                _auto_undo(run, steps, max(undos, key=os.path.getmtime))
        return {"ok": False, "steps": steps, "detail": out[-1500:]}
    undo = sorted(__import__("glob").glob(os.path.join(C.STATE, "undo-*.tsv")), key=os.path.getmtime)[-1]
    expect = sorted(__import__("glob").glob(os.path.join(C.STATE, "expect-*.json")), key=os.path.getmtime)[-1]
    rc, out = run("cerebrum.py", "verify", "--before", before, "--expect", expect, *(["--frozen-live"] if frozen_live else []))
    steps.append(("verify, separately", out.splitlines()[0] if out else ""))
    if rc:
        if intake:                                   # an agreed placement that fails its verify undoes itself
            _auto_undo(run, steps, undo)
        return {"ok": False, "steps": steps, "detail": out[-1500:]}
    acta_entry(vault, cfg, plan_path, undo, intake=intake)
    rc, out = run("cerebrum.py", "registry", "--write"); steps.append(("registry", out.splitlines()[-1] if out else ""))
    log(f"ratify\t{plan_path}\tGREEN\t{undo}")
    ledger("intake", event="ratify", plan=plan_path, by="the keeper, on the page", undo=undo, expect=expect, verify="GREEN",
           ops=[l.split("\t")[1:] for l in open(undo, encoding="utf-8").read().splitlines() if l.split("\t")[1:2] not in (["plan-begin"], ["plan-end"], ["trashed-to"])])
    return {"ok": True, "steps": steps, "undo": undo}

def _auto_undo(run, steps, undo):
    rc2, out2 = run("cerebrum.py", "undo", "--log", undo, "--i-ratified")
    last = out2.splitlines()[-1] if out2 else ""
    if rc2 == 0 and "verified" in out2:
        steps.append(("undone", last)); log(f"auto-place\tRED → undone\t{undo}")
    else:                                            # refused (Obsidian reopened) or itself red: say so, never call it undone
        steps.append(("undo NOT done", last)); log(f"auto-place\tRED and the undo did not complete: {last[:160]}\t{undo}")

def acta_entry(vault, cfg, plan_path, undo, intake=False):
    if not cfg.get("law"):                 # a vault without a law (a test fixture) has no Acta to write
        return
    acta = os.path.join(vault, os.path.dirname(cfg["law"]), "ACTA CEREBRI.md")
    if not os.path.isfile(acta):
        return
    ops = []
    for ln in open(undo, encoding="utf-8").read().splitlines():
        f = ln.split("\t")
        if len(f) >= 2 and f[1] not in ("plan-begin", "plan-end", "trashed-to"):
            ops.append(f"> - `{f[1]}` " + " → ".join(f"`{x}`" for x in f[2:4]))
    with open(acta, "a", encoding="utf-8") as fh:
        head = ("Placed by the Archivist on the agreement of two readers — the keeper's standing word (2026-09-15)" if intake
                else "Placed by the Archivist, ratified by the keeper on the page")
        fh.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} — {head}\n"
                 f"Plan `{os.path.basename(plan_path)}`: shortlist, two local readers, the intake gate; snapshot, rehearsal on record, the mover, verify in a separate run — GREEN. Undo: `{os.path.basename(undo)}`.\n"
                 f"> [!note]- Operations ({len(ops)})\n" + "\n".join(ops) + "\n")

# ---- ask: retrieval by meaning, an answer as a Markdown file, the vault read-only ---------------------
def _lexical_order(question, secs):
    """The lexical channel: BM25 over each section's note title, heading and text. Measured 2026-09-15:
    a question that names a note by its title ranked that note 18th–86th by meaning alone (nomic, centred);
    by words it ranked first. Names and coinages are what this vault is made of."""
    import math
    import numpy as np
    import unicodedata
    tok = lambda t: re.findall(r"\w+", unicodedata.normalize("NFC", t).lower())     # every letter, not only ASCII (KOINŌNIA, ΣΟΦΙΩΝ)
    docs = [tok(f"{os.path.splitext(os.path.basename(x['note']))[0]} {x['heading']} {x['text']}") for x in secs]
    n = len(docs)
    if n == 0:
        return np.zeros(0, dtype=int)
    avg = sum(map(len, docs)) / n
    df = {}
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    q = [w for w in set(tok(question)) if w in df]
    sc = np.zeros(n)
    for i, d in enumerate(docs):
        if not q:
            break
        c = {}
        for w in d:
            c[w] = c.get(w, 0) + 1
        norm = 1.2 * (0.25 + 0.75 * len(d) / avg)
        for w in q:
            if w in c:
                sc[i] += math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * c[w] * 2.2 / (c[w] + norm)
    order = np.argsort(-sc, kind="stable")
    return order[sc[order] > 0]                    # only sections that matched a word are ranked; silence is not a rank

def _fuse(orders, n, depth=200, k=60):
    """Reciprocal-rank fusion: a section ranked early by either channel comes early in the fused order."""
    import numpy as np
    sc = np.zeros(n)
    for o in orders:
        for r, j in enumerate(o[:depth]):
            sc[int(j)] += 1.0 / (k + r + 1)
    return np.argsort(-sc, kind="stable")

def retrieve(vault, cfg, question, k=6, cache_path=None, lexical=True):
    """The passages nearest the question: by meaning (centred section embeddings) AND by words (BM25 over
    title, heading and text), fused by reciprocal rank. `lexical=False` is the meaning-only channel, kept
    so a test can show what it misses."""
    import numpy as np
    embed = S.embedder()
    notes, secs, M, mu = S._corpus(vault, cfg, embed, cache_path)
    if M is None:
        return []
    q = S._centre(S.section_vectors([{"text": question, "words": len(question.split())}], cfg, embed, cache_path), mu)
    sims = (q @ S._centre(M, mu).T)[0]
    dense = np.argsort(-sims, kind="stable")
    order = _fuse([dense, _lexical_order(question, secs)], len(secs)) if lexical else dense
    out, chars = [], 0
    for j in order:
        x = secs[int(j)]
        if chars + len(x["text"]) > CTX_CHARS:
            continue
        out.append({"note": x["note"], "pid": x["pid"], "start": x["start"], "end": x["end"], "heading": x["heading"],
                    "sim": round(float(sims[j]), 3), "text": x["text"]})
        chars += len(x["text"])
        if len(out) >= k:
            break
    return out

def ask(vault, cfg, question, url=None, cache_path=None):
    cache_path = cache_path or CACHE_PATH or os.path.join(S.sema_dir(), "embeddings.json")
    hits = retrieve(vault, cfg, question, cache_path=cache_path)
    ctx = "\n\n".join(f"[{i}] `{h['note']}` {h['pid']} L{h['start']}–L{h['end']} {h['heading']}\n{h['text']}" for i, h in enumerate(hits, 1))
    msgs = [{"role": "system", "content": "You are the Archivist of a personal vault. Answer the keeper's question from the passages given, "
             "in Markdown, quoting or closely paraphrasing them and citing each claim with its number like [2]. If the passages do not "
             "answer it, say what is missing. Never invent a passage."},
            {"role": "user", "content": f"Question: {question}\n\nPassages:\n{ctx or '(none found)'}\n\nAnswer:"}]
    answer = llm(msgs, temperature=0.2, max_tokens=900, url=url) if hits else "Nothing in the vault came near this question."
    md = (f"# {question.strip()}\n\n*Answered by the Archivist, {datetime.now().strftime('%Y-%m-%d %H:%M')}, from the vault as it stands. "
          f"Sources are the vault's own passages; nothing in the vault was changed.*\n\n{answer.strip()}\n\n## Sources\n"
          + "\n".join(f"- [{i}] `{h['note']}` {h['pid']} (L{h['start']}–L{h['end']}) {h['heading']}" for i, h in enumerate(hits, 1)))
    os.makedirs(ANSWERS, exist_ok=True)
    path = os.path.join(ANSWERS, f"answer-{C.ts()}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    log(f"ask\t{question[:80]}\t{len(hits)} passages\t{path}")
    ledger("retrieval", event="ask", question=question, answer=path, model=MODEL, url=url or LLM_URL,
           passages=[{"note": h["note"], "pid": h["pid"], "lines": f"L{h['start']}-L{h['end']}", "heading": h["heading"], "sim": h["sim"]} for h in hits],
           why="the passages nearest the question by meaning (centred section embeddings) and by words (BM25 over title, heading, text), fused by reciprocal rank, bounded by the model's window; copied out as a Markdown file, the vault untouched")
    return {"markdown": md, "path": path, "sources": [{k: v for k, v in h.items() if k != "text"} for h in hits]}

# ---- titles judged by a reader: is the title the fixed point of the whole? --------------------------
TITLE_BRIEF = """You judge whether a note's TITLE names the whole note — its fixed point: the one phrase the entire note collapses to
(Name(x) = ρ(ρ(x))). You see the title, the folder, the note's parts (its headings in order), its first lines, and what a reader
called its subject. Answer with ONE JSON object and nothing else:
{"fixed_point": true or false, "better": "" or a better title (a short noun phrase in the keeper's own terms, no punctuation the file system forbids), "why": "one line"}
A title is the fixed point when a reader who knows only the title would expect exactly these parts and no others. A title that
names a part, a person, a date, a genre, or a container instead of the whole is not. Keep the keeper's names and neologisms."""

def _fixed_point(j):
    """Only a judged `true` is a fixed point. A model error, an empty reply, or the string \"false\" is NOT a hold —
    an instrument that counts its own failures as passes cannot go red (found 2026-09-15)."""
    return isinstance(j, dict) and j.get("fixed_point") is True

def judge_titles(vault, cfg, url=None, notes=None, say=lambda *_: None):
    """Every placeable note's title judged by the local model against the note's parts and the readers' subject.
    Returns rows; writes state/sema/titles-read-<ts>.md. Nothing moves — a rename is the keeper's, in Obsidian."""
    subjects = {}
    for f in sorted(__import__("glob").glob(os.path.join(S.sema_dir(), "reading-A", "*.jsonl"))):
        for rec in S.load_reading(f):
            if rec.get("lines") == "*" and rec.get("subject"):
                subjects[rec["note"]] = rec["subject"]
    rows = []
    targets = notes or S._targets(cfg, S.scope(vault, cfg))
    for i, rel in enumerate(targets, 1):
        data = S.read_bytes(vault, rel); parts = S.segment(data)
        heads = [p["heading"] for p in parts if p["heading"]][:40]
        text = data.decode("utf-8", "replace")
        first = "\n".join(l for l in text.splitlines() if l.strip())[:600]
        user = (f"TITLE: {os.path.splitext(os.path.basename(rel))[0]}\nFOLDER: {os.path.dirname(rel)}\n"
                f"PARTS ({len(parts)}): " + " · ".join(heads) + ("\n…" if len([p for p in parts if p["heading"]]) > 40 else "") +
                f"\nREADER'S SUBJECT: {subjects.get(rel, '(none recorded)')}\nFIRST LINES:\n{first}")
        try:
            out = llm([{"role": "system", "content": TITLE_BRIEF}, {"role": "user", "content": user}], temperature=0.1, max_tokens=200, url=url)
            j = next((x for x in parse_records(out)), None) or {}
        except Exception as e:
            j = {"error": repr(e)}
        rows.append({"note": rel, "fixed_point": _fixed_point(j), "better": str(j.get("better", "") or ""), "why": str(j.get("why", "") or j.get("error", "")),
                     "inbound": len(S.inbound_links(vault, cfg, rel))})
        say(f"{i}/{len(targets)} {rel[-50:]} → {'fixed point' if rows[-1]['fixed_point'] else 'NOT: ' + rows[-1]['better']}")
    fails = [r for r in rows if not r["fixed_point"]]
    md = [f"# Titles judged as fixed points — by the local reader, {datetime.now().strftime('%Y-%m-%d %H:%M')}", "",
          f"Of {len(rows)} placeable notes, **{len(rows) - len(fails)}** titles name the whole note; **{len(fails)}** do not, with a better name proposed. "
          "A rename is the keeper's, in Obsidian, so links follow; notes with inbound links are marked.", "",
          "| note | proposed title | why | inbound links |", "|---|---|---|---|"]
    md += [f"| `{r['note']}` | {r['better']} | {r['why'][:120]} | {r['inbound']} |" for r in fails]
    md += ["", "The embedding instrument was tried first and proved blind to titles on this model (its control: a note's own first heading ranked its note first 2 of 88 times), so the judgment is a reading, one model, one pass — a proposal, not a verdict."]
    os.makedirs(S.sema_dir(), exist_ok=True)
    path = os.path.join(S.sema_dir(), f"titles-read-{C.ts()}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    log(f"titles\t{len(rows)} judged\t{len(fails)} not fixed points\t{path}")
    return rows, path

# ---- the page ------------------------------------------------------------------------------------------
PAGE = r"""<!doctype html><meta charset="utf-8"><title>The Archivist</title>
<style>body{font:15px/1.45 system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1c1b19;background:#faf8f3}
h1{font-weight:600;margin:0 0 4px}small{color:#6b6760}textarea,input{width:100%;box-sizing:border-box;font:inherit;padding:8px;border:1px solid #cfc9bd;border-radius:6px;background:#fff}
textarea{min-height:160px}button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid #8a8378;background:#fff;cursor:pointer;margin:6px 6px 0 0}
button.primary{background:#2f4f3e;color:#fff;border-color:#2f4f3e}section{border-top:1px solid #e3ded4;padding:18px 0}
pre{white-space:pre-wrap;background:#fff;border:1px solid #e3ded4;border-radius:6px;padding:10px;max-height:420px;overflow:auto}
.ok{color:#2f6f3e}#modelbar button{margin:0 0 0 8px}.bad{color:#9b2c2c}.muted{color:#6b6760}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:4px 6px;border-bottom:1px solid #eee;font-size:14px}</style>
<h1>The Archivist</h1><small id="status">…</small>
<p id="modelbar" class="muted">model …</p>
<section><h2>Store</h2><p class="muted">Paste what belongs in the vault. It is captured to the inbox as a note; then the organ reads it — the shortlist, two readers, the gate — and proposes. Nothing moves until you ratify.</p>
<input id="title" placeholder="Title (if empty, the first words of the text)"><textarea id="text" placeholder="The text — raw is fine; the Archivist polishes it if the box is ticked, and keeps your raw words beneath"></textarea><input id="source" placeholder="Source (optional): who wrote it, where it came from">
<label><input type="checkbox" id="polish" checked> Polish before storing — spelling, grammar, structure; no new claims; the raw capture kept verbatim in a folded block</label><br>
<button class="primary" onclick="store()">Capture and place</button><button onclick="placeExisting()">Place an inbox note by path…</button><button onclick="polishExisting()">Polish an inbox note by path…</button>
<pre id="storeOut" class="muted">—</pre><div id="ratify"></div></section>
<section><h2>Ask</h2><p class="muted">The vault is read-only here. The answer cites the passages it used and is saved as a Markdown file you can copy.</p>
<input id="q" placeholder="Your question"><button class="primary" onclick="askQ()">Ask</button><button onclick="copyAns()">Copy the Markdown</button>
<pre id="askOut" class="muted">—</pre></section>
<section><h2>Log</h2><pre id="log" class="muted">—</pre></section>
<script>
const $=id=>document.getElementById(id);let lastMd="",lastPlan="";
async function api(path,body){const r=await fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});return r.json();}
let modelOn=false,polling=null;
async function status(){const s=await api("/api/status");$("status").textContent=`vault ${s.vault} · check ${s.check} · inbox ${s.inbox} · Obsidian ${s.obsidian?"open (ratify refused while open)":"closed"}`;$("log").textContent=s.log.join("\n")||"—";modelBar(s);}
function modelBar(s){modelOn=!!s.model;const up=s.model_unit==="active"||s.model_unit==="activating";
if(s.model){$("modelbar").innerHTML=`<span class="ok">Model: ON</span> — using about 9 GB of memory<button onclick="setModel(false)">Turn the model off</button>`;}
else if(up){$("modelbar").innerHTML=`<span class="muted">Model: loading… (a few seconds, up to a minute after a reboot)</span><button onclick="setModel(false)">Cancel</button>`;}
else{$("modelbar").innerHTML=`<span class="bad">Model: OFF</span>${s.model_unit==="failed"?" (it failed to start; see the log)":""}<button class="primary" onclick="setModel(true)">Turn the model on</button>`;}
if(up&&!s.model){if(!polling)polling=setInterval(status,3000);}else if(polling){clearInterval(polling);polling=null;}}
async function setModel(on){$("modelbar").innerHTML=`<span class="muted">${on?"starting":"stopping"}…</span>`;const r=await api("/api/model",{on});if(!r.ok)alert("Could not "+(on?"start":"stop")+" the model: "+(r.error||"unknown"));status();}
function needModel(out){if(modelOn)return true;$(out).textContent="The model is off. Press “Turn the model on” at the top, wait until it says ON, then try again.";return false;}
function show(r){let t=`${r.status}${r.why?" — "+r.why:""}\n`;if(r.problems)t+=r.problems.map(p=>"! "+p).join("\n")+"\n";if(r.must_read)t+="must read: "+r.must_read.join(", ")+"\n";
if(r.shortlist&&r.shortlist.notes)t+="\nshortlist:\n"+r.shortlist.notes.slice(0,6).map((n,i)=>`  ${i+1}. ${n.sim}  ${n.note}`).join("\n")+"\n";
if(r.readers){for(const w of["A","B"]){t+=`\nreader ${w}:\n`+ (r.readers[w]||[]).map(x=>`  ${x.lines} ${x.op} → ${x.dest||"(stays)"}${x.at?" "+x.at:""} — ${x.why}`).join("\n")+"\n";}}
if(r.disputes)t+="\ndisputes: "+r.disputes.map(d=>`L${d.first}–L${d.last}`).join(", ")+"\n";if(r.plan)t+="\nplan:\n  "+r.plan.join("\n  ")+"\n";$("storeOut").textContent=t;
$("ratify").innerHTML=r.status==="PLAN"?`<button class="primary" onclick="doRatify()">Ratify — run this plan on the vault</button> <span class="muted">snapshot · rehearsal · the mover · verify · the Acta</span>`:"";lastPlan=r.plan_path||"";}
async function store(){if(!needModel("storeOut"))return;$("storeOut").textContent="capturing…";const c=await api("/api/capture",{title:$("title").value||$("text").value.trim().split(/\s+/).slice(0,8).join(" "),text:$("text").value,source:$("source").value,polish:$("polish").checked});if(c.error){$("storeOut").textContent="! "+c.error;return;}
$("storeOut").textContent=`captured → ${c.note}\nreading (shortlist, two readers, the gate)… this takes a minute or two on the local model`;show(await api("/api/place",{note:c.note}));status();}
async function polishExisting(){if(!needModel("storeOut"))return;const n=prompt("Inbox note path, e.g. # INBOX/Captured.md");if(!n)return;$("storeOut").textContent="polishing…";const r=await api("/api/polish",{note:n});$("storeOut").textContent=r.error?"! "+r.error:`polished ${r.note} (raw kept beneath) — now place it`;}
async function placeExisting(){if(!needModel("storeOut"))return;const n=prompt("Inbox note path, e.g. # INBOX/Something.md");if(!n)return;$("storeOut").textContent="reading…";show(await api("/api/place",{note:n}));}
async function doRatify(){if(!lastPlan)return;$("ratify").innerHTML="running…";const r=await api("/api/ratify",{plan:lastPlan});$("ratify").innerHTML=`<span class="${r.ok?"ok":"bad"}">${r.ok?"GREEN — placed and verified":"REFUSED / RED"}</span><pre>${r.steps.map(s=>s.join(": ")).join("\n")}${r.detail?"\n"+r.detail:""}</pre>`;status();}
async function askQ(){if(!needModel("askOut"))return;$("askOut").textContent="retrieving and answering…";const r=await api("/api/ask",{q:$("q").value});lastMd=r.markdown||"";$("askOut").textContent=(r.markdown||("! "+r.error))+(r.path?`\n\n(saved: ${r.path})`:"");}
function copyAns(){if(lastMd)navigator.clipboard.writeText(lastMd);}status();</script>"""

_status_cache = {"when": 0.0, "ok": None}

PLUGIN_ORIGIN = "app://obsidian.md"      # the Obsidian plugin (Electron) — the one cross-origin caller allowed

def vault_rel(vault, note):
    """A note path as the vault knows it, or None: relative, inside the vault after resolving, never `..`."""
    if not note or os.path.isabs(note) or ".." in note.split("/") or "\\" in note:
        return None
    root = os.path.realpath(vault); full = os.path.realpath(os.path.join(vault, note))
    return note if full.startswith(root + os.sep) else None

class Handler(http.server.BaseHTTPRequestHandler):
    vault, cfg = C.VAULT, C.CFG
    port = PORT
    def _own_origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}", PLUGIN_ORIGIN}
    def _origin_ok(self):
        """A browser sends Origin on every POST; a page from anywhere else on the web must be refused, or any open tab
        could write into the vault (found 2026-09-15). No Origin = not a browser (curl, a script) — the keeper's own shell."""
        o = self.headers.get("Origin")
        return o is None or o in self._own_origins()
    def _cors(self):
        if self.headers.get("Origin") == PLUGIN_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", PLUGIN_ORIGIN); self.send_header("Vary", "Origin")
    def do_OPTIONS(self):
        """The plugin's JSON POST is preflighted; answer it for the plugin's origin only."""
        if self.headers.get("Origin") != PLUGIN_ORIGIN:
            self.send_response(403); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.send_response(204); self._cors(); self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0"); self.end_headers()
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False, default=str).encode()
        try:
            self.send_response(code); self._cors(); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):     # the client left mid-request (a killed placement client, 2026-09-15): the work stands, the reply cannot
            log(f"client gone\t{self.path}\tthe reply could not be sent")
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/api/model":                       # the switch's own state — no vault check, cheap enough to poll
            return self._json({"model": model_reachable(), "model_unit": model_unit_state()})
        if self.path == "/api/status":
            v = K.Vault(self.vault, self.cfg)
            inbox = len([p for p in v.movable if p.startswith(list(self.cfg.get("para", C.PARA))[0] + "/") and p.endswith(".md")])
            tail = open(LOG, encoding="utf-8").read().splitlines()[-12:] if os.path.exists(LOG) else []
            now = time.time()
            if now - _status_cache["when"] > 60:                  # the check takes seconds; once a minute is enough for a status line
                _status_cache.update(when=now, ok=K.run(self.vault, self.cfg, say=lambda *_: None))
            ok = _status_cache["ok"]
            return self._json({"vault": os.path.basename(self.vault), "check": "GREEN" if ok else "RED", "inbox": inbox,
                               "model": model_reachable(), "model_unit": model_unit_state(), "obsidian": C._obsidian_running(), "log": tail})
        b = PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        if not self._origin_ok():
            return self._json({"error": "refused: this page is not the Archivist's own"}, 403)
        if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            return self._json({"error": "refused: JSON only"}, 415)
        try:
            n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body is not an object")
        except (ValueError, TypeError) as e:
            return self._json({"error": f"bad request: {e}"}, 400)
        try:
            if self.path == "/api/model":
                return self._json(switch_model(bool(body.get("on"))))
            if self.path == "/api/capture":
                if not (body.get("text") or "").strip():
                    return self._json({"error": "nothing to capture"}, 400)
                return self._json({"note": capture(self.vault, self.cfg, body.get("title") or "Captured", body["text"], body.get("source", ""), do_polish=bool(body.get("polish")))})
            if self.path in ("/api/polish", "/api/place"):
                note = vault_rel(self.vault, str(body.get("note", "")))
                if not note or not os.path.isfile(os.path.join(self.vault, note)):
                    return self._json({"error": f"no such note in the vault: {body.get('note', '')}"}, 400)
            if self.path == "/api/polish":
                return self._json(polish_note(self.vault, self.cfg, note))
            if self.path == "/api/place":
                return self._json(place_note(self.vault, self.cfg, note))
            if self.path == "/api/ratify":
                p = str(body.get("plan", ""))
                sema = os.path.realpath(S.sema_dir()) + os.sep
                if not (p and os.path.isfile(p) and os.path.realpath(p).startswith(sema)
                        and re.fullmatch(r"intake-\d{8}-\d{6}(-\d+)?\.tsv", os.path.basename(p))):
                    return self._json({"ok": False, "steps": [("plan", "not a plan the Archivist wrote")]})
                return self._json(ratify(self.vault, self.cfg, p))
            if self.path == "/api/ask":
                if not (body.get("q") or "").strip():
                    return self._json({"error": "no question"}, 400)
                return self._json(ask(self.vault, self.cfg, body["q"]))
            return self._json({"error": "unknown"}, 404)
        except Exception as e:
            log(f"error\t{self.path}\t{e!r}")
            return self._json({"error": repr(e)}, 500)

def serve(port=PORT):
    Handler.port = port
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if AUTO_PLACE:
        threading.Thread(target=queue_worker, args=(Handler.vault, Handler.cfg), daemon=True).start()
    print(f"the Archivist : http://127.0.0.1:{port}/  — vault {Handler.vault}  — model {LLM_URL}")
    log(f"serve\t{port}\t{Handler.vault}")
    srv.serve_forever()

if __name__ == "__main__":
    if sys.argv[1:2] == ["titles"]:
        rows, path = judge_titles(C.VAULT, C.CFG, say=print)
        print(f"titles   : {sum(1 for r in rows if r['fixed_point'])}/{len(rows)} fixed points → {path}"); sys.exit(0)
    if sys.argv[1:2] in (["--help"], ["-h"]):
        print("usage: archivist.py [port]  |  archivist.py titles"); sys.exit(0)
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else PORT)
