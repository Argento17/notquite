# Reference

Details behind the [README](../README.md). Everything here is read from the
code in `notquite/`; if the two disagree, the code wins and this file has a bug.

## Commands

| Command | What it does |
|---|---|
| `notquite new --title T --owner O [--ships none\|repo\|live] [--prefix TASK]` | Reserves the next id on `origin`, or numbers it locally when there is no `origin` remote; writes `tasks/<id>.md` |
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

`tasks/<id>.md` starts with front matter between two `---` lines. Exactly these
fields, each once: `id`, `title`, `owner`, `status`, `ships`, `waits_on`,
`contract`. A value is either plain single-line text or JSON (a value starting
with `"` or `[` is parsed as JSON). This is not YAML.

- `id` matches the file name and the pattern `PREFIX-N`, for example `TASK-12`.
- `status`: `IN_PROGRESS`, `BLOCKED`, `DONE` or `CLOSED`.
- `ships`: `none`, `repo` or `live`.
- `waits_on`: a JSON list of `task:<id>` or `person:<name>`.
- `contract`: `none` or a repository-relative path.

Missing task dependencies and dependency cycles make `check` fail.

## How the status is computed

- **IN_PROGRESS** until there is an `implemented` receipt, the latest `test`
  receipt on the same commit and code after it is passing and valid, and every
  critical contract criterion is met. A later failing or invalid run revokes an
  earlier pass; a later passing run restores it. No receipt is ever removed.
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
can be committed after the evidence is recorded.

## Receipts

`.notquite/receipts/<id>.jsonl` holds one JSON object per line. Every receipt
has `task`, `kind`, `at`, `who` (`user.name@hostname`), `commit`, `code`,
`previous` (the hash of the receipt before it, or empty) and `hash` (SHA-256 of
the receipt without `hash`). Command receipts add `command`, `exit_code` and
`valid`. Read-backs add `target` and `output_sha256`. Merges add `ref`.

The checker refuses a broken chain, a receipt whose `code` does not match its
commit, and any change to receipts already in `HEAD` other than appending.
A ledger without a matching task file is reported.

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

## Line endings

Task files, receipts and claim tokens are always written with LF. Files read
back with CRLF, as Git on Windows may check them out, are treated the same.
