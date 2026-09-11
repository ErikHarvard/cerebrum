#!/usr/bin/env python3
"""
checks.py — the vault's build: one command runs every rule and says PASS or FAIL.

Metalogical ontosyntax. The three laws of thought, applied to a vault, are at once its
syntax, its algorithm and its logic: the rules that describe the vault are the rules that
check it.

  I.   IDENTITY           A is A. Each note is itself: one name, one note; a link names one note.
  II.  NON-CONTRADICTION  Not both A and not-A. No fact is kept in two places that can disagree,
                          and no note holds what the law forbids.
  III. EXCLUDED MIDDLE    A or not-A. Every file has exactly one standing — frozen, or in one PARA
                          home — and every rule ends PASS or FAIL, never silent.

Every rule carries a fixture: it must PASS on a clean scratch vault, then FAIL once its defect
is planted. `check` runs every fixture before it trusts any verdict — a rule that cannot fail
is not a rule — then runs the rules on the real vault. A rule that crashes is a FAIL.
"""
import re
import os, re, json, shutil, tempfile
from collections import defaultdict
import cerebrum as C

LAWS = {"I": "IDENTITY", "II": "NON-CONTRADICTION", "III": "EXCLUDED MIDDLE"}
RULES = []

class Rule:
    def __init__(self, law, name, why, fn, plant):
        self.law, self.name, self.why, self.fn, self.plant = law, name, why, fn, plant

def rule(law, name, why, plant):
    def deco(fn):
        RULES.append(Rule(law, name, why, fn, plant))
        return fn
    return deco

class Vault:
    """Everything the rules read, gathered once."""
    def __init__(self, root, cfg):
        self.root, self.cfg = root, cfg
        self.frozen = [f["root"] for f in cfg.get("frozen", [])] + [".obsidian", ".trash"]
        self.para = list(cfg.get("para", C.PARA))
        self.accepted = cfg.get("accepted", {})
        self.files = sorted(C.rels(root))
        self.movable = [p for p in self.files if not self.is_frozen(p)]
        self.visible = [p for p in self.files if not any(s.startswith(".") for s in p.split("/"))]
    def is_frozen(self, p):
        return any(p == r or p.startswith(r + "/") for r in self.frozen)
    def text(self, p):
        with open(os.path.join(self.root, p), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    def hash(self, p):
        return C.sha256(os.path.join(self.root, p))

MD = lambda p: p.endswith(".md")
def base(p):
    b = os.path.basename(p).lower()
    return b[:-3] if b.endswith(".md") else b

def _body(text):
    """The note without its frontmatter and code blocks — what a person reads."""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        text = text[end + 4:] if end >= 0 else text
    return C._strip_code(text)

# ---- I. IDENTITY -------------------------------------------------------------
def _plant_same_name(v):
    os.makedirs(os.path.join(v.root, "2 AREAS", "A"), exist_ok=True)
    open(os.path.join(v.root, "2 AREAS", "A", "Twin Name.md"), "w").write("one\n")
    open(os.path.join(v.root, "3 RESOURCES", "Twin Name.md"), "w").write("two\n")

@rule("I", "one name, one note",
      "a [[link]] resolves by name — two different notes under one name make it ambiguous", _plant_same_name)
def r_one_name(v):
    groups, out = defaultdict(list), []
    for p in v.visible:
        if MD(p):
            groups[base(p)].append(p)
    ok = set(v.accepted.get("duplicates", []))
    for b, ps in sorted(groups.items()):
        if len(ps) < 2 or all(v.is_frozen(p) for p in ps):
            continue
        if len({v.hash(p) for p in ps}) > 1:
            out.append(f"different notes share the name '{b}': " + " | ".join(ps))
        elif not set(ps) <= ok:
            out.append(f"unaccepted copies share the name '{b}': " + " | ".join(ps))
    return out

def _plant_duplicate(v):
    src = next(p for p in v.movable if MD(p))
    shutil.copy(os.path.join(v.root, src), os.path.join(v.root, "4 ARCHIVE", "Renamed Copy.md"))

@rule("I", "no silent duplicates",
      "one version of anything (Forte): a copy nobody accepted is a second version", _plant_duplicate)
def r_duplicates(v):
    by = defaultdict(list)
    for p in v.movable:
        if os.path.getsize(os.path.join(v.root, p)) > 0:
            by[v.hash(p)].append(p)
    ok = set(v.accepted.get("duplicates", []))
    return [" = ".join(ps) for ps in by.values() if len(ps) > 1 and not set(ps) <= ok]

def _plant_broken_link(v):
    open(os.path.join(v.root, "# INBOX", "Dangling.md"), "w").write("see [[No Such Note Anywhere]]\n")

@rule("I", "every link names one note",
      "a link that names nothing — or two different things — has no identity", _plant_broken_link)
def r_links(v):
    names, paths, files = defaultdict(list), set(), defaultdict(list)
    for p in v.visible:
        paths.add(p.lower())
        files[os.path.basename(p).lower()].append(p)
        if MD(p):
            names[base(p)].append(p)
            paths.add(p[:-3].lower())
    ok = {(x["in"].lower(), x["to"].lower()) for x in v.accepted.get("broken_links", []) if isinstance(x, dict)}
    verbatim = set(v.accepted.get("verbatim", []))    # word-for-word merges: their links are record, not navigation
    sensitive = set(v.accepted.get("sensitive", []))  # never opened
    out = []
    for p in v.movable:
        if not MD(p) or p in verbatim or p in sensitive:
            continue
        for m in C.WIKI.finditer(C._strip_code(v.text(p))):
            t = m.group(1).split("|")[0].split("#")[0].strip()
            tl = t.lower()
            if not t or (p.lower(), tl) in ok:
                continue
            if "/" in t:
                hits = [t] if (tl in paths or tl + ".md" in paths) else []
            elif "." in os.path.basename(t) and not tl.endswith(".md"):
                hits = files.get(tl, [])
            else:
                hits = names.get(tl[:-3] if tl.endswith(".md") else tl, [])
            if not hits:
                out.append(f"{p} → {m.group(0)} names nothing")
            elif len(hits) > 1 and len({v.hash(h) for h in hits}) > 1:
                out.append(f"{p} → {m.group(0)} could be {len(hits)} different notes")
    return out

# ---- II. NON-CONTRADICTION ---------------------------------------------------
def _plant_rotted_pin(v):
    empty = os.path.join(os.path.dirname(v.root), "rotted-pin.txt")
    open(empty, "w").close()
    v.cfg["frozen"][0]["pins"] = [[empty, "NOT THERE"]]

@rule("II", "the frozen register agrees with the code",
      "the register says code depends on each frozen folder; if the code no longer says so, "
      "the register claims what isn't true", _plant_rotted_pin)
def r_pins(v):
    out = []
    for f in v.cfg.get("frozen", []):
        if not os.path.isdir(os.path.join(v.root, f["root"])):
            out.append(f"frozen folder is missing: {f['root']}")
        for path, literal in f.get("pins", []):
            fp = os.path.expanduser(path)
            if not os.path.isfile(fp):
                out.append(f"{f['root']}: the pinning file is gone: {path}")
            elif literal not in open(fp, encoding="utf-8", errors="replace").read():
                out.append(f"{f['root']}: {path} no longer contains {literal!r}")
    return out

def _plant_law_elsewhere(v):
    law = os.path.join(v.root, v.cfg["law"])
    os.rename(law, os.path.join(v.root, "# INBOX", os.path.basename(law)))

@rule("II", "the law is where it says it is",
      "M1 + M2: the law is a note in the vault, filed in the folder it names as its home", _plant_law_elsewhere)
def r_law(v):
    law = v.cfg.get("law")
    if not law:
        return ["no law configured"]
    if not os.path.isfile(os.path.join(v.root, law)):
        return [f"the law is not at {law}"]
    home = os.path.dirname(law)
    where = [l for l in v.text(law).splitlines() if l.startswith("**Where.**")]
    if not where:
        return ["the law has no '**Where.**' line saying where it lives"]
    return [] if any(home in l for l in where) else [f"the law's '**Where.**' line does not name its own home ({home})"]

SECRETS = [
    ("Hugging Face token", r"\bhf_[A-Za-z0-9]{30,}"),
    ("GitHub token", r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}"),
    ("API secret key", r"\bsk-(?:ant-)?[A-Za-z0-9_-]{30,}"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

def _plant_secret(v):
    open(os.path.join(v.root, "3 RESOURCES", "Keys.md"), "w").write(
        "token: hf_" + "Ab3" * 12 + "\n")    # a fake token, assembled at run time so none ships in the source

@rule("II", "no secrets in notes",
      "the law keeps passwords and keys out of notes; a note holding one contradicts it "
      "(reported by kind and place, never by value)", _plant_secret)
def r_secrets(v):
    skip, out = set(v.accepted.get("sensitive", [])), []
    for p in v.movable:
        if not MD(p) or p in skip:
            continue
        t = v.text(p)
        for kind, pat in SECRETS:
            n = len(re.findall(pat, t))
            if n:
                out.append(f"{p}: {n} × {kind}")
    return out

def _plant_dead_pointer(v):
    v.cfg["pointers"] = [{"file": v.cfg["pointers"][0]["file"], "path": "1 PROJECTS/Gone.md"}]

@rule("II", "outside pointers still land",
      "a file outside the vault names a note by path; if the note moved, the pointer now says "
      "something false", _plant_dead_pointer)
def r_pointers(v):
    out = []
    for ptr in v.cfg.get("pointers", []):
        fp = os.path.expanduser(ptr["file"])
        if not os.path.exists(os.path.join(v.root, ptr["path"])):
            out.append(f"{ptr['file']} points at a missing note: {ptr['path']}")
        elif not os.path.isfile(fp) or ptr["path"] not in open(fp, encoding="utf-8", errors="replace").read():
            out.append(f"{ptr['file']} no longer names {ptr['path']}")
    return out

# ---- III. EXCLUDED MIDDLE ----------------------------------------------------
def _plant_stray(v):
    open(os.path.join(v.root, "Stray at the root.md"), "w").write("lost\n")

@rule("III", "every file has one standing",
      "each file is frozen or in exactly one PARA home — nothing loose at the root", _plant_stray)
def r_standing(v):
    allowed = set(v.para) | set(v.frozen) | set(v.accepted.get("top_level", []))
    return [f"outside PARA and the frozen register: {t}" for t in sorted(os.listdir(v.root)) if t not in allowed]

def _plant_second_meta(v):
    os.makedirs(os.path.join(v.root, "3 RESOURCES", os.path.basename(os.path.dirname(v.cfg["law"]))))

@rule("III", "one meta-area, no tower",
      "M3: the system's knowledge of itself lives in one Area; a second one is a tower", _plant_second_meta)
def r_one_meta(v):
    name = os.path.basename(os.path.dirname(v.cfg.get("law", "")))
    hits = []
    for r, ds, _ in os.walk(v.root):
        rel = os.path.relpath(r, v.root)
        ds[:] = [d for d in ds if not v.is_frozen(os.path.normpath(os.path.join(rel, d)))]
        hits += [os.path.normpath(os.path.join(rel, d)) for d in ds if d == name]
    return [] if len(hits) == 1 else [f"{len(hits)} folders named {name}: {hits}"]

def _plant_query_only(v):
    open(os.path.join(v.root, v.cfg["meta_dirs"][0], "Only a query.md"), "w").write("```dataview\nLIST\n```\n")

@rule("III", "meta-notes read without plugins",
      "M4: every note about the system is text a person can read with every plugin gone", _plant_query_only)
def r_plain(v):
    out = []
    for d in v.cfg.get("meta_dirs", []):
        for p in v.movable:
            if MD(p) and p.startswith(d + "/"):
                prose = [l for l in _body(v.text(p)).splitlines() if l.strip() and not l.lstrip().startswith("#")]
                if not prose:
                    out.append(f"nothing readable outside queries: {p}")
    return out

def _plant_daily_root(v):
    core = os.path.join(v.root, ".obsidian", "core-plugins.json")
    d = json.load(open(core))
    d["daily-notes"] = True
    json.dump(d, open(core, "w"))
    dn = os.path.join(v.root, ".obsidian", "daily-notes.json")
    if os.path.exists(dn):
        os.remove(dn)

@rule("III", "new notes are born in a home",
      "Obsidian's own settings create notes and attachments; each must land in a PARA home, "
      "or the root fills again", _plant_daily_root)
def r_obsidian_homes(v):
    o = os.path.join(v.root, ".obsidian")
    def load(f):
        p = os.path.join(o, f)
        return json.load(open(p)) if os.path.exists(p) else {}
    app, core = load("app.json"), load("core-plugins.json")
    if isinstance(core, list):
        core = {k: True for k in core}
    homes = []
    if app.get("newFileLocation", "root") != "current":
        homes.append(("new notes", app.get("newFileFolderPath", "") if app.get("newFileLocation") == "folder" else ""))
    if core.get("daily-notes"):
        homes.append(("daily notes", load("daily-notes.json").get("folder", "")))
    out = [] if app.get("attachmentFolderPath", "/") not in ("", "/") else ["attachments land at the vault root"]
    for what, folder in homes:
        folder = folder.strip("/")
        if not folder:
            out.append(f"{what} land at the vault root")
        elif folder.split("/")[0] not in v.para:
            out.append(f"{what} land outside PARA: {folder}")
        elif not os.path.isdir(os.path.join(v.root, folder)):
            out.append(f"{what} go to a folder that does not exist: {folder}")
    return out

# ---- II, continued: the registry --------------------------------------------------
def _plant_stale_registry(v):
    with open(os.path.join(v.root, v.cfg["registry"]), "a", encoding="utf-8") as fh:
        fh.write("a line nobody generated\n")

@rule("II", "the registry agrees with the vault",
      "the registry is generated from the files; one that differs from a fresh generation tells "
      "something the vault no longer says", _plant_stale_registry)
def r_registry(v):
    import registry
    if not v.cfg.get("registry"):
        return []
    if not os.path.isfile(os.path.join(v.root, v.cfg["registry"])):
        return [f"never generated: {v.cfg['registry']} — run: cerebrum.py registry --write"]
    return [] if registry.fresh(v.root, v.cfg) else [f"stale: {v.cfg['registry']} — run: cerebrum.py registry --write"]

# ---- Law Revision I (2026-09-11): six rules that were checkable and unchecked ----------------
def _inbox_archive(v):
    """The one inbox and the one archive: the first and last PARA folders."""
    return v.para[0], v.para[-1]

def _plant_second_inbox(v):
    os.makedirs(os.path.join(v.root, v.para[2], "Inbox"))
    open(os.path.join(v.root, v.para[2], "Inbox", "x.md"), "w").write("x\n")

@rule("I", "one inbox, one archive",
      "an inbox of inboxes is dispersal; archiving the archive is the archive — no folder outside the "
      "frozen register is named like either, except the two", _plant_second_inbox)
def r_one_inbox(v):
    inbox, arch = _inbox_archive(v)
    out = []
    for r, ds, _ in os.walk(v.root):
        rel = os.path.relpath(r, v.root)
        ds[:] = [d for d in ds if not v.is_frozen(os.path.normpath(os.path.join(rel, d)))]
        for d in ds:
            p = os.path.normpath(os.path.join(rel, d))
            if p not in (inbox, arch) and re.search(r"inbox|archive", d, re.I):
                out.append(f"a second {'inbox' if re.search('inbox', d, re.I) else 'archive'}: {p}")
    return out

def _plant_notebook_of_one(v):
    d = os.path.join(v.root, v.para[2], "Lonely"); os.makedirs(d); open(os.path.join(d, "only.md"), "w").write("x\n")

@rule("III", "no notebook of one",
      "notebooks nest by subject as deep as meaning requires, and a notebook is a name for a whole — one that holds a "
      "single thing is a name for nothing: the thing belongs one level up (the keeper's ruling, 2026-09-11)", _plant_notebook_of_one)
def r_notebook_of_one(v):
    out = []
    for r, ds, fs in os.walk(v.root):
        rel = os.path.relpath(r, v.root)
        ds[:] = [d for d in ds if not v.is_frozen(os.path.normpath(os.path.join(rel, d)))]
        if rel == "." or rel in v.para:
            continue
        kids = len(ds) + len([f for f in fs if not f.startswith(".")])
        if kids == 1:
            out.append(f"a notebook of one: {rel} — holds only {(ds or [f for f in fs if not f.startswith('.')])[0]}")
    return out

def _plant_empty_folder(v):
    os.makedirs(os.path.join(v.root, v.para[2], "Nothing here"))

@rule("III", "no empty folder",
      "L2: a container that holds nothing fails 'what breaks if removed?' — the PARA roots themselves excepted", _plant_empty_folder)
def r_no_empty(v):
    out = []
    for r, ds, fs in os.walk(v.root):
        rel = os.path.relpath(r, v.root)
        ds[:] = [d for d in ds if not v.is_frozen(os.path.normpath(os.path.join(rel, d)))]
        if rel != "." and rel not in v.para and not ds and not fs:
            out.append(f"empty folder: {rel}")
    return out

def law_rule_names(text):
    """The rule names the law's §XV table claims, in its 'Rules' column: cells split on ' · '."""
    sec = _section(text, "XV")
    names = set()
    for line in sec.splitlines():
        if line.startswith("| **"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[-1]:
                names |= {x.strip() for x in cells[-1].split("·") if x.strip()}
    return names

def law_plan_ops(text):
    """The operations the law's plan-language table (§VII) names: the first backticked word of each row."""
    return {m.group(1) for m in re.finditer(r"^\| `([a-z]+)\b", _section(text, "VII"), re.M)}

def _section(text, roman):
    m = re.search(rf"^## {roman}\. .*?$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    n = re.search(r"^## ", rest, re.M)
    return rest[:n.start()] if n else rest

def _plant_renamed_rule(v):
    p = os.path.join(v.root, v.cfg["law"])
    t = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(t.replace(RULES[0].name, RULES[0].name + " (renamed)", 1))

@rule("II", "the law describes the tool it governs",
      "the rule names in the law's §XV table are exactly the check's rules, and the operations in its "
      "plan-language table are exactly the ones the mover accepts", _plant_renamed_rule)
def r_law_describes_tool(v):
    law = v.cfg.get("law")
    if not law or not os.path.isfile(os.path.join(v.root, law)):
        return ["no law to read"]
    text, out = v.text(law), []
    claimed, real = law_rule_names(text), {r.name for r in RULES}
    for x in sorted(real - claimed):
        out.append(f"the check has a rule the law's §XV table does not name: {x}")
    for x in sorted(claimed - real):
        out.append(f"the law's §XV table names a rule the check does not have: {x}")
    ops, real_ops = law_plan_ops(text), set(C.ARITY) | {"merge"}
    for x in sorted(real_ops - ops):
        out.append(f"the mover accepts an operation the law's plan language does not list: {x}")
    for x in sorted(ops - real_ops):
        out.append(f"the law lists an operation the mover does not accept: {x}")
    return out

def _frontmatter(text):
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    fm = {}
    for line in (text[4:end] if end >= 0 else "").splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, val = line.partition(":")
            fm[k.strip()] = val.strip()
    return fm

def _plant_goalless_project(v):
    d = os.path.join(v.root, v.para[1], "Thing")
    os.makedirs(d); open(os.path.join(d, "Thing — Project.md"), "w").write("---\ngoal: \ndeadline: \n---\n# Thing\n")

@rule("III", "a project is a goal with a deadline",
      "every folder in the projects category has a project note whose goal and deadline are filled — "
      "precision required in exactly one place, the definition of a project", _plant_goalless_project)
def r_projects(v):
    root, out = os.path.join(v.root, v.para[1]), []
    if not os.path.isdir(root):
        return []
    for d in sorted(os.listdir(root)):
        if not os.path.isdir(os.path.join(root, d)) or v.is_frozen(f"{v.para[1]}/{d}"):
            continue
        notes = [p for p in v.movable if MD(p) and p.startswith(f"{v.para[1]}/{d}/")]
        fms = [(p, _frontmatter(v.text(p))) for p in notes]
        proj = [(p, fm) for p, fm in fms if "goal" in fm and "deadline" in fm]
        if not proj:
            out.append(f"{v.para[1]}/{d}: no project note (a note with goal and deadline properties)")
        elif not any(fm["goal"] and fm["deadline"] for _, fm in proj):
            out.append(f"{proj[0][0]}: goal or deadline is blank")
    return out

def _plant_stale_exception(v):
    shutil.rmtree(os.path.join(v.root, "EXTRA"))

@rule("II", "every accepted exception still applies",
      "the config forgives named things; an exception for something that no longer exists is a false "
      "claim that silently widens what the check forgives", _plant_stale_exception)
def r_exceptions(v):
    a, out = v.accepted, []
    ex = lambda p: os.path.lexists(os.path.join(v.root, p))
    dups = a.get("duplicates", [])
    for i in range(0, len(dups) - 1, 2):
        x, y = dups[i], dups[i + 1]
        if not (ex(x) and ex(y)):
            out.append(f"accepted duplicate pair, one is gone: {x} · {y}")
        elif v.hash(x) != v.hash(y):
            out.append(f"accepted duplicate pair no longer identical: {x} · {y}")
    if len(dups) % 2:
        out.append(f"accepted duplicates: an odd entry, no pair: {dups[-1]}")
    sensitive = set(a.get("sensitive", []))
    links_in = {}
    for p in v.movable:
        if MD(p) and p not in sensitive:
            links_in[p.lower()] = {m.group(1).split("|")[0].split("#")[0].strip().lower() for m in C.WIKI.finditer(C._strip_code(v.text(p)))}
    names = {base(p) for p in v.visible if MD(p)} | {os.path.basename(p).lower() for p in v.visible}
    for x in a.get("broken_links", []):
        if not isinstance(x, dict) or "in" not in x or "to" not in x:
            out.append(f"accepted broken link must be a pair {{in: note, to: target}}, not {x!r}"); continue
        n, t = x["in"].lower(), x["to"].lower()
        if n not in links_in:
            out.append(f"accepted broken link: the note is gone: {x['in']}")
        elif t not in links_in[n]:
            out.append(f"accepted broken link: {x['in']} no longer links to {x['to']}")
        elif t in names:
            out.append(f"accepted broken link now resolves: {x['to']}")
    for p in a.get("sensitive", []):
        if not ex(p): out.append(f"accepted sensitive note is gone: {p}")
    for d in a.get("top_level", []):
        if not ex(d): out.append(f"accepted top-level folder is gone: {d}")
    for p in a.get("verbatim", []):
        if not ex(p): out.append(f"accepted verbatim merge has not been made: {p}")
        elif "Merged word for word" not in v.text(p)[:400]: out.append(f"accepted verbatim merge is not one: {p}")
    return out

# ---- the harness ---------------------------------------------------------------
def _scratch():
    """A minimal vault every rule passes — the control for every fixture."""
    s = tempfile.mkdtemp(prefix="cerebrum-check-")
    v = os.path.join(s, "vault")
    for d in ["# INBOX", "1 PROJECTS", "2 AREAS/META", "3 RESOURCES", "4 ARCHIVE", "FROZEN", ".obsidian"]:
        os.makedirs(os.path.join(v, d))
    rules = " · ".join(r.name for r in RULES)
    ops = "\n".join(f"| `{o} …` | does | refused |" for o in sorted(set(C.ARITY) | {"merge"}))
    open(os.path.join(v, "2 AREAS/META/LAW.md"), "w").write(
        "# Law\n\n**Where.** `2 AREAS/META/` — inside the folders, not above them.\n\n## VII. Rite\n\n| Operation | Does | Refused when |\n|---|---|---|\n"
        + ops + "\n\n## XV. Check\n\n| Law | At the vault | Rules |\n|---|---|---|\n| **All** | rules | " + rules + " |\n")
    os.makedirs(os.path.join(v, "EXTRA")); open(os.path.join(v, "EXTRA/kept.md"), "w").write("kept\n"); open(os.path.join(v, "EXTRA/also.md"), "w").write("also\n")
    open(os.path.join(v, "1 PROJECTS/Plan.md"), "w").write("see [[Idea]]\n")
    open(os.path.join(v, "3 RESOURCES/Idea.md"), "w").write("an idea\n")
    open(os.path.join(v, "FROZEN/organ.md"), "w").write("live\n")
    json.dump({"newFileLocation": "folder", "newFileFolderPath": "# INBOX", "attachmentFolderPath": "./"},
              open(os.path.join(v, ".obsidian/app.json"), "w"))
    json.dump({"daily-notes": False}, open(os.path.join(v, ".obsidian/core-plugins.json"), "w"))
    pin = os.path.join(s, "organ.py")
    open(pin, "w").write('ROOT = "FROZEN"\nNOTE = "3 RESOURCES/Idea.md"\n')
    cfg = {"law": "2 AREAS/META/LAW.md", "meta_dirs": ["2 AREAS/META"], "registry": "2 AREAS/META/REGISTRY.md",
           "frozen": [{"root": "FROZEN", "pins": [[pin, '"FROZEN"']]}], "accepted": {"top_level": ["EXTRA"]},
           "pointers": [{"file": pin, "path": "3 RESOURCES/Idea.md"}]}
    import registry
    registry.write(v, cfg)
    return s, v, cfg

def selftest():
    """Each rule must PASS on the clean scratch vault and FAIL once its defect is planted.
    Returns the list of rules that did not prove themselves (empty = all proven)."""
    bad = []
    for r in RULES:
        s, root, cfg = _scratch()
        try:
            if r.fn(Vault(root, cfg)):
                bad.append(f"{r.name}: fails on the clean control vault")
                continue
            r.plant(Vault(root, cfg))
            if not r.fn(Vault(root, cfg)):
                bad.append(f"{r.name}: did NOT catch its planted defect")
        except Exception as e:
            bad.append(f"{r.name}: fixture crashed: {e}")
        finally:
            shutil.rmtree(s, ignore_errors=True)
    return bad

def run(root, cfg, say=print):
    """The build: every fixture first, then every rule on the vault. True only if all pass."""
    bad = selftest()
    say(f"check    : {len(RULES)} rules · fixtures: "
        + ("every rule caught its planted defect" if not bad else f"{len(bad)} did not prove themselves"))
    for b in bad:
        say(f"    FAIL  selftest — {b}")
    v, fails = Vault(root, cfg), 0
    for law, title in LAWS.items():
        say(f"  {law}. {title}")
        for r in (x for x in RULES if x.law == law):
            try:
                problems = r.fn(v)
            except Exception as e:
                problems = [f"the rule crashed: {e}"]          # a crash is a FAIL, never a skip
            fails += bool(problems)
            say(f"    {'FAIL' if problems else 'PASS'}  {r.name}" + (f" — {len(problems)} problem(s)" if problems else ""))
            for p in problems[:8]:
                say(f"          {p}")
            if len(problems) > 8:
                say(f"          … {len(problems) - 8} more")
    ok = not bad and not fails
    say("check    : " + ("GREEN — every rule passes" if ok
                         else f"RED — {fails} rule(s) failing" + (" · selftest broken" if bad else "")))
    return ok
