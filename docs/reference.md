# Reference

Details behind the [README](../README.md). Everything here is read from the
code in `notquite/`; if the two disagree, the code wins and this file has a bug.

## Commands

| Command | What it does |
|---|---|
| `notquite new --title T --owner O [--ships none\|repo\|live] [--prefix TASK] [--template simple\|extended]` | Reserves the next id on `origin`, or numbers it locally when there is no `origin` remote; writes `tasks/<id>.md` |
| `notquite receipt implemented --task ID` | Records the current commit as the implementation; refuses uncommitted or untracked code |
| `notquite receipt test --task ID -- CMD...` | Runs `CMD` (no shell), records its exit code against the current commit |
| `notquite receipt merged --task ID [--ref refs/heads/main]` | Fetches the branch from `origin` and records the commit it points to (needs `origin`) |
| `notquite receipt deployed --task ID --target NAME -- CMD...` | Runs a read-back command; valid only if it exits 0 and its output contains the full commit ID |
| `notquite receipt live --task ID --target NAME -- CMD...` | Same, recorded after the deployment |
| `notquite check [ID or tasks/ID.md]` | No argument: one line per refused claim in the repository, or `check: OK`; use this in hooks and CI. With a task: an explanation of that task alone |
| `notquite check --staged` | Checks the staged versions of task files and receipts (for pre-commit) |
| `notquite show --task ID` | Recorded status, computed status, what is still owed |
| `notquite claim --task ID` / `release --task ID` | Takes or gives up a task with a pushed tag |
| `notquite claims` | Active claims |
| `notquite ready` | Open tasks: free, held, blocked, or waiting for delivery |

Every command exits 1 when it refuses something, including a command receipt
whose command failed. Run commands from the repository root.

## Task files

`tasks/<id>.md` starts with front matter between two `---` lines, one
`key: value` per line, each key once. These seven fields are required: `id`,
`title`, `owner`, `status`, `ships`, `waits_on`, `contract`.

A value is plain single-line text, a JSON string, or a list. NotQuite never
rewrites a task file after `new`, so whatever formatting you use is left alone,
byte for byte.

### What YAML is read

Front matter is not parsed as YAML. NotQuite reads this subset and refuses
everything else with a message naming the line:

- `key: value` on one line: plain text, or a JSON string (`"..."`).
- Lists, on one line: JSON (`["TASK-1", "task:TASK-2"]`) or simple
  (`[TASK-1, task:TASK-2]`, items without spaces or quotes).
- Indented lists for `depends_on`, `blocks` and `waits_on` only:

  ```yaml
  depends_on:
    - TASK-1
    - "TASK-2"
  ```

  A list field with nothing after the colon and no items is an empty list.
- `>` (folded) and `|` (literal) blocks for `mission`, `next` and `summary`
  only, with optional `-` (strip) or `+` (keep) chomping. They follow YAML:
  `>` joins lines with spaces, keeps empty lines as line breaks and keeps lines
  indented further than the block on their own lines.
- Empty lines between fields.

Not read, and refused:

- comment lines (`# ...`), anchors and aliases (`&`, `*`), tags (`!`), nested
  mappings, and multi-line plain or quoted values. A `#` after a value on the
  same line is not a comment here: it stays part of the value;
- block text on any field other than `mission`, `next` and `summary`, and
  indented lists on any field other than the three list fields;
- block indicators with an explicit indentation number (`|2`);
- a block line indented less than the block's first line (YAML would end the
  block or treat it as a comment; NotQuite refuses rather than guess).

Block values were checked against PyYAML: for the supported forms they read
the same, except that NotQuite refuses the less-indented lines above.

- `id` matches the file name and the pattern `PREFIX-N`, for example `TASK-12`.
- `status`: `IN_PROGRESS`, `BLOCKED`, `DONE` or `CLOSED`.
- `ships`: `none`, `repo` or `live`.
- `waits_on`: a JSON list of `task:<id>` or `person:<name>`.
- `contract`: `none` or a repository-relative path.

### Extended fields

`notquite new --template extended` writes these optional fields too, in this
order: `id`, `title`, `owner`, `business_owner`, `priority`, `created_at`,
`mission`, `depends_on`, `blocks`, `next`, `summary`, `status`, `ships`,
`waits_on`, `contract`. The body gets `## Context`, `## Scope`, `## Lanes`,
`## Acceptance` and `## Progress` sections. Any subset of the optional fields
may be used; any other field is refused, so typos are caught.

- `business_owner`, `priority`, `mission`, `next`, `summary`: text, may be
  empty. Not used by any check.
- `created_at`: empty or an ISO date or time (`2026-11-14`,
  `2026-11-14T09:30:00Z`). `new` fills in today's UTC date.
- `depends_on`: task ids (`TASK-2` or `task:TASK-2`). Not the task itself.
- `blocks`: task ids. Informational: `check` reports a `blocks` entry that
  names an unknown task, or two tasks that each say they block the other, but
  never makes a task wait because of it.

### Dependencies

`waits_on` and `depends_on` form one dependency list: `waits_on` items plus
`task:<id>` for each `depends_on` id, without repeats. Listing a task in both
is allowed. A DONE or CLOSED claim is refused until every task dependency
computes DONE or CLOSED and no `person:` wait remains. A task may not both
wait for and block the same task. Missing task dependencies and dependency
cycles, across both fields, make `check` fail.

None of the extended fields or sections can satisfy or replace evidence; the
only extended field that affects a verdict is `depends_on`, and it can only
add requirements.

## How the status is computed

- **IN_PROGRESS** until there is an `implemented` receipt, the latest `test`
  receipt on the same commit and code after it is passing and valid, and every
  critical contract criterion is met. A later failing or invalid run on that
  commit revokes an earlier pass; a later passing run restores it. Runs on any
  other commit are recorded but ignored.
- **DONE** when that holds.
- **CLOSED** when DONE and delivery is complete:
  - `ships: none`: nothing more.
  - `ships: repo`: a `merged` receipt, after the implementation, whose code
    matches the implemented code.
  - `ships: live`: also a valid `deployed` receipt after that merge, with the
    implemented code, then a valid `live` receipt for the same commit and
    target. "Valid" means the read-back command exited 0 and returned the
    expected commit ID. It does not show which server answered or that it is
    production.

Only receipts after the newest `implemented` receipt count. Recording a new
implementation starts the evidence again.

A `test` receipt is valid only if the command exited 0, and the working tree
was still clean with HEAD unchanged when it finished.

"Code" is a SHA-256 over the commit's file listing, leaving out `tasks/` and
`.notquite/`. Committing task files and receipts does not change it, so they
can be committed after the evidence is recorded. The commit does change, so a
test run after that commit does not count, pass or fail, until a new
`implemented` receipt is recorded on it.

The verdict looks only at the newest `implemented` receipt, never at HEAD:
code committed after it without a new `implemented` receipt is not checked.

Receipts name commits. If those commits disappear (a squash or rebase merge
whose branch is then deleted), `check` refuses in any clone that lacks them.
Use merge commits for branches that carry receipts.

## Receipts

`.notquite/receipts/<id>.jsonl` holds one JSON object per line. Every receipt
has `task`, `kind`, `at`, `who` (`user.name@hostname`), `commit`, `code`,
`previous` (the hash of the receipt before it, or empty) and `hash` (SHA-256 of
the receipt without `hash`). Command receipts add `command`, `exit_code` and
`valid`. Read-backs add `target` and `output_sha256`. Merges add `ref`.

The checker refuses a broken chain, a receipt whose `code` does not match its
commit, and any change to receipts already in `HEAD` other than appending.
A ledger without a matching task file is reported.

The append-only rule compares the working tree or the index with `HEAD` only.
It protects receipts while you commit, through `check --staged` in the
pre-commit hook. Once a commit that drops receipts exists (for example one made
with `git commit --no-verify`), `check` on that commit compares it with itself
and passes; it does not look at earlier commits.

None of this is signed. Anyone who can write the file and run SHA-256 can
create a receipt that passes.

## Contracts

```json
{"task": "TASK-3", "criteria": [{"id": "C1", "ask": "...", "critical": true,
  "requires": "REAL", "evidence": [{"provenance": "REAL", "ref": "evidence/run.txt",
  "sha256": "..."}]}]}
```

- `task` must name the task. `criteria` is a non-empty list with distinct `id`s.
- Each criterion needs `ask`, `critical` (true or false), `requires` (`REAL` or
  `REAL_OR_TEST`) and an `evidence` list.
- `REAL` evidence: `ref` is a file in the repository and `sha256` its hash. It
  counts when the hash matches. The hash is of the file's bytes, so if Git
  converts line endings on checkout (`core.autocrlf` on Windows), a text
  evidence file's hash changes. Mark evidence files `-text` in
  `.gitattributes` to keep them byte-for-byte.
- `TEST` evidence: `ref` is `receipt:<hash>` of a passing, valid test receipt on
  the implemented commit. It counts only when `requires` is `REAL_OR_TEST`.
- `MOCK`, `SYNTHETIC` and `UNKNOWN` never count.

Only critical criteria block DONE.

## Claims and ids

All are tags on `origin` pointing at small JSON blobs. Without an `origin`
remote, `new` numbers ids locally (nothing is reserved) and `claim`, `release`
and `claims` refuse; there is no local claim.

- `refs/tags/notquite/ids/<id>`: the id reservation made by `new`.
- `refs/tags/notquite/claims/<id>/<n>`: claim number `n`.
- `refs/tags/notquite/releases/<id>/<claim object>`: the release of that claim.

A push that would replace an existing tag is rejected by Git, so the first
clone to push wins. The claim token is kept in `.git/notquite-claims/<id>` and
is never committed or pushed. The claim tag stores only its SHA-256 hash
(`token_sha256`); `claim` and `release` compare against that hash.
Force-pushes and tag deletion by anyone with push access bypass all of this;
protect `notquite/*` tags if it matters.

## Compatibility

- Task files in the seven-field format read exactly as in 0.1.0, and plain
  `notquite new` writes the same bytes as 0.1.0.
- 0.1.0 refuses any task file that uses an extended field or the YAML forms
  above. Once one task uses them, every clone, hook and CI pin needs 0.2.0 or
  later.
- Receipts are unchanged. Task files are left out of the code fingerprint, so
  switching a task from the simple to the extended format, in either YAML or
  JSON style, keeps its receipts valid.

## Line endings

Task files, receipts and claim tokens are always written with LF. Files read
back with CRLF, as Git on Windows may check them out, are treated the same.
