# cerebrum

**A recursively self-organizing note vault: organized, checked and tracked by its own rules — without losing a byte.**

cerebrum keeps a folder of markdown notes (an Obsidian vault, or any folder of notes) organized the way Tiago Forte's PARA method describes — Inbox, Projects, Areas, Resources, Archive — and proves every change it makes. It never deletes on its own, never touches folders other programs depend on, and checks itself the way a build checks a program: one command runs every rule, and every rule has first shown that it can fail.

Standard library Python 3.12, no dependencies, nothing sent anywhere.

## What it does

- **Organizes by plan, not by guess.** A plan is a small tab-separated file of operations (below). cerebrum dry-runs it, refuses anything unsafe — overwriting a note, touching a frozen folder, trashing the only copy of something — and derives exactly what the vault must look like afterwards.
- **Rehearses before it acts.** `rehearse.py` applies the plan to a copy of the vault, plants defects the verifier must catch, and undoes the plan to prove it can.
- **Changes nothing it didn't plan.** Every operation is written to an undo log before it runs. Afterwards every file is compared by SHA-256 with the before-manifest; any change the plan didn't predict fails the run.
- **Merges without loss.** `merge` builds a new note holding each source byte for byte under a heading naming it, so any source can always be sliced back out. The sources are kept.
- **Checks the whole vault.** `cerebrum.py check` runs every rule and exits non-zero on any failure. Each rule carries a planted defect it must catch before its verdict counts; a rule that can't fail isn't a rule, and a rule that crashes is a failure, never a skip.
- **Tracks every note and meta-note.** `cerebrum.py registry --write` generates one note listing every note, the system's own notes, the frozen folders with the exact code lines that depend on them, and proposals: notes fully contained in another (removable when you say), overlapping notes (merge candidates), plans already carried out. It is generated, never hand-kept, leaves itself out of everything it counts, and the check fails when it goes stale.

## The three laws

The rules are the three classical laws of thought, applied to a vault — *metalogical ontosyntax*: the rules that describe the vault are the rules that check it.

| Law | In a vault | Rules |
|---|---|---|
| **I. Identity** — A is A | Each note is itself: one name, one note; a link names one note. | one name, one note · no silent duplicates · every link names one note |
| **II. Non-contradiction** — not both A and not-A | No fact is kept in two places that can disagree; no note holds what the rules forbid. | the frozen register agrees with the code · the law is where it says it is · no secrets in notes · outside pointers still land · the registry agrees with the vault |
| **III. Excluded middle** — A or not-A | Every file has exactly one standing; every rule ends PASS or FAIL. | every file has one standing · one meta-area, no tower · meta-notes read without plugins · new notes are born in a home |

## Frozen folders

Some folders are read or written by other programs — a sync script, a journal tool, an AI agent's memory. List each in `cerebrum.json` with the file and the exact text that depends on it. The mover never touches them, and `check` fails the day the code no longer says what the register claims. When those programs are running, `--frozen-live` lists their writes with timestamps instead of failing, and holds everything else to the full standard.

## Quick start

```sh
cp cerebrum.example.json cerebrum.json        # your vault path, PARA names, frozen folders
python3 cerebrum.py check                     # the vault's build
python3 cerebrum.py registry --write          # the registry note
python3 cerebrum.py snapshot                  # a full backup, outside the vault
python3 cerebrum.py manifest                  # path, size and SHA-256 of every file
python3 cerebrum.py move --plan plan.tsv --before state/manifest-<time>.tsv          # dry run
python3 rehearse.py --plan plan.tsv                                                 # prove it on a copy
python3 cerebrum.py move --plan plan.tsv --before state/manifest-<time>.tsv --apply --i-ratified
python3 cerebrum.py undo --log state/undo-<time>.tsv --i-ratified                   # if you change your mind
```

Close Obsidian before moving files; the mover refuses while it is open.

## The plan language

| Operation | Does |
|---|---|
| `mkdir <dir>` | makes a folder whose parent exists |
| `mv <src> <dst>` | moves a note or a folder — never onto an existing path |
| `merge <new.md> <src> [<src> …]` | a new note holding each source word for word; `code:<src>` fences a source so its brackets aren't read as links |
| `append <dst.md> <src> <heading>` | appends a note to another under a heading |
| `trash <file>` | to the desktop trash, only while an identical copy survives the plan |
| `rmdir <dir>` | removes a folder only if it is already empty |
| `compose <new.md> <src.md> <spec>` | a new note made of those pieces of a note, verbatim, in the order given |
| `extend <note.md> <src.md> <spec>` | appends those pieces to an existing note, whose old text stays exactly as it was |
| `leave <src.md> <spec>` | pieces deliberately left only in the source |
| `partition <src.md> <sha256>` | declares the source is distributed completely: it must still be the note that was cut, and every one of its lines must be composed, extended or left exactly once |

A `<spec>` lists pieces in order: a part (`P012`), a run of parts (`P011-P022`) or a line range (`L1542-L1600`).

## Organizing by meaning

Moving whole notes organizes where they sit; `semantics.py` organizes what they say.

1. `python3 semantics.py segment "<note>"` cuts a note into parts at its headings — never inside a code block — and writes a table of them. Rejoined, the parts are the note, byte for byte.
2. A reader — you, or an AI assistant you ask — reads the parts and writes a plan: what each piece is about, where it belongs, and in what order. Pieces too large to be one topic can be cut at any line.
3. The dry run checks the form: every line lands in exactly one place or is explicitly left (excluded middle), unchanged (identity), and never twice (non-contradiction). A plan for a note that has changed since it was cut is refused.
4. `rehearse.py` proves the plan on a copy; then it runs, and the vault is verified against it.

What no tool can check is whether a piece was placed *well* — that is meaning, and it stays with the reader who proposed the plan and the person who approves it.

## The semantic organ

Word matching finds copies; it cannot see the same idea in other words, or one note holding several subjects. The organ reads for meaning, and proves the form of what it proposes. Its operators come from a paradox audit: **split** one note holding several subjects, **gather** several notes on one subject, **mark** a boundary — and, before any of these, **stand down** where two notes are one subject seen from two uses (a source and its summary): nothing is mistyped there, so nothing merges. Before any merge, the relation is named: an identical copy (≡) is trashed, the same claims in other words (=) are merged, the same structure in another domain (≅) is linked — never merged.

```sh
python3 semantics.py index                 # triage: sections + local embeddings → candidates (ranks only)
python3 semantics.py parts "<note>"        # the note's hash and parts, for a reader to cite
python3 semantics.py reading <A>           # coverage: every line of every note placed exactly once
python3 semantics.py propose <A> <B>       # two independent readings → agreement → a plan + a proposal
python3 semantics.py converge <expect.json> <A> <B>   # after the plan ran: nothing more to do, or it didn't close
```

- **Two readings, independently.** A reading is JSON lines: for each run of lines, what it is about, what it is for, and where it belongs (`python3 semantics.py` prints the format). A plan is built only from lines both readings place the same way; a new note is known by exactly what it holds, not by what a reader calls it. Where they disagree, nothing is selected — the lines go to the keeper.
- **Nothing is lost.** A note whose lines go to more than one place is archived whole first, then partitioned: every line placed exactly once. Sources stay until a person removes them.
- **Gates, each with a planted defect it must catch:** coverage, agreement, traceability (every paragraph of a synthesis cites the source parts it came from, by hash), lossless verify, and convergence (the notes a plan touched, read again, need nothing more).
- **Triage is ordinal.** The index needs numpy and a local embedding model (ollama's `nomic-embed-text` by default; set `organ.embed_model` in `cerebrum.json`). Its scores only rank candidates for the readers. Two controls: identical copies must find each other, and the model must rank a paraphrase above an unrelated sentence. Notes accepted as sensitive are never read.

## Tests

```sh
python3 test_cerebrum.py
```

Every rule, the verifier, the mover, the merge, the rehearsal and the registry are tested against scratch vaults with planted defects; the suite also checks that it catches a rule that can never fail.

## Where the ideas come from

- Tiago Forte, *Building a Second Brain* (2022) and *The PARA Method* (2023): the four homes, "always start with the archives", one version of anything, organize just in time.
- The [LogOS](https://github.com/ErikHarvard/LogOS) build discipline: one command checks everything; a gate that can fail soft is not a gate; expected values are derived, never captured; trackers are generated, not maintained; an audit whose input includes its own output is a mirror, not an instrument.
- The three laws of thought — identity, non-contradiction, excluded middle — as the syntax of the system.
