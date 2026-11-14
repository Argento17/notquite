# Changelog

## 0.3.0

NotQuite is now **Workorder**: a task format for coding agents (define the
work, track the evidence, verify DONE), with the DONE check as one part. It
works alongside CI, not instead of it. The check still says NOT QUITE when it
refuses a claim.

- New name everywhere: PyPI project `workorder`, the `workorder` command and
  package, receipts in `.workorder/`, ids and claims under
  `refs/tags/workorder/`. A repository with NotQuite receipts carries over with
  `git mv .notquite .workorder` (see docs/reference.md, Compatibility).
- `workorder new` writes the extended task by default; `--template simple`
  writes the 0.1.0 seven-field format. The README leads with the task format.

Fixes for what five independent reviews found; every finding below is now a
regression test.

- A task dependency counts as finished only when its own dependencies are
  finished too. A task waiting on a person no longer lets the tasks that
  depend on it pass as DONE, and `ready` lists it as blocked.
- `check --base REF` also refuses a commit since `REF` that newly claimed DONE
  or CLOSED on code other than the implementation it relied on: the CI form of
  the pre-commit rule.
- A merge receipt that counted stays counted when a later one cannot count.
- `check <id>` shows the command behind the deciding test run.
- Only `tasks/<id>.md` files are task records; a README or notes in `tasks/`
  no longer break every check. A broken contract file is named in the error.

From the second, neutral four-reviewer round:

- A task can declare its own `test` command (`workorder new --test ...`). Once
  set, only a run of exactly that command counts, and `workorder receipt test
  --task ID` runs it.
- Plain `workorder check` refuses a DONE or CLOSED claimed, in the working
  tree or in a commit, on code other than the implementation it relies on.
- Merges and deployments count when they contain the implemented commit, so a
  task can reach CLOSED on a busy main with merge commits.
- After a squash merge, a rebase or an amend, receipts whose commits are gone
  no longer stop `check` for the whole repository: they stop counting, and the
  task's evidence is recorded again on the new commit.
- `ready` lists every task even when one claim is refused; `show` lists open
  dependencies under "still owes"; a stray tag under `claims/` is ignored;
  claiming a task this checkout lacks says so.
- Front matter also reads `#` comment lines, single-quoted values,
  left-margin `- item` lists and a UTF-8 byte order mark.
- The README's pre-commit hook says what to do when `workorder` is not on
  PATH, and the agent instructions cover the task's `test`, committing,
  pushing and recording the merge.

From the third four-reviewer round:

- `test` commands split without escape characters, so Windows paths work, and
  shell operators such as `&&` are refused instead of passed as arguments.
- Plain `check` refuses a new DONE made over uncommitted code changes, and
  `check <id>` notes uncommitted changes. While a dependency is open, the
  computed status reads BLOCKED.
- `check --base` judges the change: problems already on the base branch, on
  tasks the change does not touch, and changes to a task's `test`, contract
  or dependencies are printed as notes. The pre-commit hook treats problems
  already in HEAD the same way, so an old problem no longer blocks unrelated
  commits.
- `release --force` frees a claim whose clone or worktree is gone;
  `new --local` numbers a task without pushing a tag; `ready` works when
  origin cannot be reached and shows a BLOCKED status as blocked.
- New status `CANCELLED` for work that will not be done. `ships` now
  defaults to `none`.
- Receipt commands for one task are serialised by a lock, so parallel runs
  keep the chain. Commit fingerprints are cached in `.git` between runs.
- Stray files in `.workorder/receipts/` are ignored, a missing contract
  evidence file is unmet evidence rather than an error, quoted list items and
  person names with spaces are read, and an unknown status shows its value.
- A problem counts as "already so on base" only when the check refuses that
  task on the base revision itself (checked in a temporary worktree), so a
  change to a contract or its evidence can no longer pass as an old problem.
  An in-place contract edit is noted for review. Commands are shown the way
  they are read, so Windows paths print without extra quoting.

- A test run counts when its code matches the implemented code, on any commit.
  Committing the task file and receipts no longer hides later runs: a later
  failing run revokes DONE again, and a passing run after that commit counts.
  A DONE that was hiding such a failure becomes IN_PROGRESS.
- The pre-commit check (`check --staged`) refuses a commit that newly marks a
  task DONE or CLOSED when the code in that commit differs from the implemented
  code. `check <id>` notes when the code has changed since the implementation,
  and its `Next:` line then says to record `implemented` again.
- `check --base REF` refuses if receipts committed on `REF` were removed or
  changed, so a `--no-verify` commit cannot drop a failing run past CI. The
  README's Actions example uses it.
- A receipt that is recorded but cannot count (a test on changed code, a merge
  without the tested code, a read-back out of order, delivery receipts a task
  does not ship) says why and exits 1. Before, most of these exited 0.
- New receipts and claims record only Git `user.name`, not the hostname.
- Documentation states what is not covered yet: squash and rebase merges, and
  `merged`/`deployed` receipts on a branch where other work has landed.

## 0.2.0 (as NotQuite)

- Optional extended task format. `notquite new --template extended` adds
  `business_owner`, `priority`, `created_at`, `mission`, `depends_on`,
  `blocks`, `next` and `summary`, plus Context, Scope, Lanes, Acceptance and
  Progress sections. The default seven-field format is unchanged.
- `depends_on` and `waits_on` form one dependency list. `blocks` is checked
  for consistency but never enforced. Extended fields never count as evidence.
- A small YAML subset is read: `>` and `|` blocks for `mission`, `next` and
  `summary`, indented `- item` lists for `depends_on`, `blocks` and
  `waits_on`, simple `[TASK-1, TASK-2]` lists and empty lines between fields.
  Other YAML is refused with an explanation (see docs/reference.md).
- Compatibility: v0.1.0 refuses task files that use extended fields. Once any
  task uses them, run v0.2.0 everywhere, including CI pins.

## 0.1.0 (as NotQuite)

First public release.

- Task records in `tasks/*.md`.
- Hash-chained, append-only receipts for implementation, tests, merge,
  deployment and live read-back. The latest test run on the implemented commit
  decides: a later failure revokes DONE until a later run passes.
- Works in a repository without a remote for local tasks; ids and claims are
  coordinated through `origin` when there is one.
- `notquite check` refuses DONE and CLOSED claims the evidence does not support;
  `notquite check <id>` explains why and what to do next.
- Optional contracts that map each ask to evidence; MOCK, SYNTHETIC and UNKNOWN
  evidence never count.
- Task claims as Git tags.
- Pre-commit hook and GitHub Actions workflow.
- Tested in CI on Linux and Windows with Python 3.10, 3.12 and 3.13.
