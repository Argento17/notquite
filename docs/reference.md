# Reference

Details behind the [README](../README.md). Everything here is read from the
code in `workorder/`; if the two disagree, the code wins and this file has a bug.

## Commands

| Command | What it does |
|---|---|
| `workorder new --title T --owner O [--ships none\|repo\|live] [--prefix TASK] [--template extended\|simple]` | Reserves the next id on `origin`, or numbers it locally when there is no `origin` remote; writes `tasks/<id>.md` (extended by default; `simple` writes the seven required fields only) |
| `workorder receipt implemented --task ID` | Records the current commit as the implementation; refuses uncommitted or untracked code |
| `workorder receipt test --task ID -- CMD...` | Runs `CMD` (no shell), records its exit code against the current commit |
| `workorder receipt merged --task ID [--ref refs/heads/main]` | Fetches the branch from `origin` and records the commit it points to (needs `origin`) |
| `workorder receipt deployed --task ID --target NAME -- CMD...` | Runs a read-back command; valid only if it exits 0 and its output contains the full commit ID |
| `workorder receipt live --task ID --target NAME -- CMD...` | Same, recorded after the deployment |
| `workorder check [ID or tasks/ID.md]` | No argument: one line per refused claim in the repository, or `check: OK`; use this in hooks and CI. With a task: an explanation of that task alone |
| `workorder check --staged` | Checks the staged versions of task files and receipts (for pre-commit); also refuses a commit that newly claims DONE or CLOSED on code other than the implemented code |
| `workorder check --base REF` | Also refuses if receipts committed on `REF` were removed or changed (for CI: the pull request's base branch) |
| `workorder show --task ID` | Recorded status, computed status, what is still owed |
| `workorder claim --task ID` / `release --task ID` | Takes or gives up a task with a pushed tag |
| `workorder claims` | Active claims |
| `workorder ready` | Open tasks: free, held, blocked, or waiting for delivery |

Every command exits 1 when it refuses something, including a command receipt
whose command failed and a receipt that was recorded but cannot count (it says
why). Run commands from the repository root.

## Task files

`tasks/<id>.md` starts with front matter between two `---` lines, one
`key: value` per line, each key once. These seven fields are required: `id`,
`title`, `owner`, `status`, `ships`, `waits_on`, `contract`.

A value is plain single-line text, a JSON string, or a list. Workorder never
rewrites a task file after `new`, so whatever formatting you use is left alone,
byte for byte.

### What YAML is read

Front matter is not parsed as YAML. Workorder reads this subset and refuses
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
  block or treat it as a comment; Workorder refuses rather than guess).

Block values were checked against PyYAML: for the supported forms they read
the same, except that Workorder refuses the less-indented lines above.

- `id` matches the file name and the pattern `PREFIX-N`, for example `TASK-12`.
- `status`: `IN_PROGRESS`, `BLOCKED`, `DONE` or `CLOSED`.
- `ships`: `none`, `repo` or `live`.
- `waits_on`: a JSON list of `task:<id>` or `person:<name>`.
- `contract`: `none` or a repository-relative path.

### Extended fields

`workorder new` (and `--template extended`) writes these optional fields too,
in this order: `id`, `title`, `owner`, `business_owner`, `priority`, `created_at`,
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
  receipt on the same code after it is passing and valid, and every critical
  contract criterion is met. A later failing or invalid run on that code
  revokes an earlier pass; a later passing run restores it. Runs on different
  code are recorded but ignored, and the receipt command exits 1.
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
`.workorder/`. Committing task files and receipts does not change it, so they
can be committed after the evidence is recorded, and later runs on the same
code, on any commit, still count.

The verdict looks only at the newest `implemented` receipt, never at HEAD.
Two checks cover the gap: `check --staged` (the pre-commit hook) refuses a
commit that changes a task's status to DONE or CLOSED when the code in that
commit differs from the implemented code, and `check <id>` adds a note when
HEAD's code differs. Code that lands after a task is DONE is not re-tested.

Receipts name commits. If those commits disappear (a squash or rebase merge
whose branch is then deleted), `check` refuses in any clone that lacks them.
Use merge commits for branches that carry receipts.

## Receipts

`.workorder/receipts/<id>.jsonl` holds one JSON object per line. Every receipt
has `task`, `kind`, `at`, `who` (Git `user.name`; receipts written by 0.2.0 or
earlier carry `user.name@hostname`), `commit`, `code`,
`previous` (the hash of the receipt before it, or empty) and `hash` (SHA-256 of
the receipt without `hash`). Command receipts add `command`, `exit_code` and
`valid`. Read-backs add `target` and `output_sha256`. Merges add `ref`.

The checker refuses a broken chain, a receipt whose `code` does not match its
commit, and any change to receipts already in `HEAD` other than appending.
A ledger without a matching task file is reported.

The append-only rule compares the working tree or the index with `HEAD` only.
It protects receipts while you commit, through `check --staged` in the
pre-commit hook. Once a commit that drops receipts exists (for example one made
with `git commit --no-verify`), plain `check` on that commit compares it with
itself and passes. `check --base REF` compares every ledger committed on `REF`
with the current one and refuses if any receipt was removed or changed; run it
in CI against the pull request's base branch.

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

- `refs/tags/workorder/ids/<id>`: the id reservation made by `new`.
- `refs/tags/workorder/claims/<id>/<n>`: claim number `n`.
- `refs/tags/workorder/releases/<id>/<claim object>`: the release of that claim.

A push that would replace an existing tag is rejected by Git, so the first
clone to push wins. The claim token is kept in `.git/workorder-claims/<id>` and
is never committed or pushed. The claim tag stores only its SHA-256 hash
(`token_sha256`); `claim` and `release` compare against that hash.
Force-pushes and tag deletion by anyone with push access bypass all of this;
protect `workorder/*` tags if it matters.

## Compatibility

- Workorder was called NotQuite up to 0.2.0 (PyPI `notquite`). From 0.3.0 the
  command, the package and the data paths are `workorder`: receipts in
  `.workorder/`, ids and claims under `refs/tags/workorder/`, claim tokens in
  `.git/workorder-claims/`. To carry a NotQuite repository over, move the
  folder with `git mv .notquite .workorder` and commit. Old receipts still
  verify, because `.notquite/` stays outside the code fingerprint. Ids and
  claims made under `refs/tags/notquite/` are not read; task numbering
  continues from the task files.
- Task files in the seven-field format read exactly as in 0.1.0, and
  `workorder new --template simple` writes the same bytes as 0.1.0. From 0.3.0,
  plain `workorder new` writes the extended format.
- 0.1.0 refuses any task file that uses an extended field or the YAML forms
  above. Once one task uses them, every clone, hook and CI pin needs 0.2.0 or
  later.
- Receipts are unchanged. Task files are left out of the code fingerprint, so
  switching a task from the simple to the extended format, in either YAML or
  JSON style, keeps its receipts valid.
- 0.3.0 reads the same receipts with three differences. A test run counts when
  its code matches the implemented code, on any commit, so a DONE that was
  hiding a later failing run on the same code becomes IN_PROGRESS. A receipt
  that is recorded but cannot count exits 1. New receipts and claims record
  only `user.name`.

## Line endings

Task files, receipts and claim tokens are always written with LF. Files read
back with CRLF, as Git on Windows may check them out, are treated the same.
