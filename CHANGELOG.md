# Changelog

## 0.3.0

NotQuite is now **Workorder: the task record for coding agents**. Every task is
recorded in the repository with a clear history, agents go back to it at the
start of every session, and DONE is checked against that history. It works
alongside CI, not instead of it. The check still says NOT QUITE when it refuses
a claim.

Name and format
- New name everywhere: PyPI project `workorder`, the `workorder` command and
  package, receipts in `.workorder/`, ids and claims under
  `refs/tags/workorder/`. A NotQuite repository carries over with
  `git mv .notquite .workorder` (see docs/reference.md, Compatibility).
- `workorder new` writes the extended task by default; `--template simple`
  writes the 0.1.0 seven-field format. `ships` defaults to `none`.
- A task declares its `test` (`new --test ...`); DONE needs it, and only a run
  of exactly that command counts. `test: any` accepts any command explicitly.
  Commands split without a shell: backslashes are kept (Windows paths work),
  shell operators are refused.
- New status `CANCELLED`. Your own fields start with `x-`. Front matter also
  reads `#` comments, single-quoted values, left-margin lists, quoted list
  items and a UTF-8 byte order mark.

The record
- `workorder show` prints the task as a record: state, what is owed, title,
  mission, next and test, every section of the task, a dated timeline of its
  receipts, its Progress notes and the next step.
- `workorder note` adds a dated line under Progress.
- The agent instructions start every session with `show`, end it with `note`,
  and cover the task's test, committing, pushing and the merge.

Checking
- A test run counts when its code matches the implemented code, on any commit:
  a later failing run revokes DONE, also after the receipts are committed.
- `check` refuses a DONE or CLOSED claimed, in the working tree or in a commit,
  on code other than the implementation it relies on, or over uncommitted code.
- A dependency is finished when it says DONE or CLOSED, its evidence supports
  that, and its own dependencies are finished; `ready` shows the rest as
  blocked, and a computed status reads BLOCKED while a dependency is open.
- The pre-commit hook (`--staged`) and CI (`--base REF`) judge the change: the
  tasks it touches (task file, receipts, contract or evidence) and the tasks
  that depend on them. They stay fast as tasks pile up, and an old problem
  elsewhere does not block anyone. Plain `check` reads every task.
- `check --base` refuses removed or changed receipts, a stale DONE claimed
  since the base, and any change to a task's test, contract, evidence or
  dependencies, or a removed or reworded Scope or Acceptance line, unless a
  reviewer passes `--allow ID`. `--run-tests` runs each claimed task's declared
  test on the code being merged.
- A task file that cannot be read is a problem of that task only; `show`,
  `ready` and `check ID` keep working for the others.

Merges, claims and the rest
- Merges and deployments count when they contain the implemented commit, so a
  task reaches CLOSED on a busy main with merge commits; a merge receipt that
  counted stays counted.
- After a squash merge, rebase or amend, receipts whose commits are gone count
  again when a commit in this clone holds exactly their code (the squash
  commit, if main had not moved); otherwise `workorder renew` records the
  evidence again on the new commit in one step.
- A receipt that is recorded but cannot count says why and exits 1.
- `release --force` frees a claim whose clone or worktree is gone; `new --local`
  numbers a task without pushing a tag, and `new` suggests it when origin
  cannot be reached; `ready` works offline; stray claim tags are ignored.
- Receipt commands for one task are serialised by a lock; commit fingerprints
  are cached in `.git`; reading one ledger no longer lists the whole folder.
- New receipts and claims record only Git `user.name`, not the hostname.
- The changes above came from independent blind reviews; each has a
  regression test that fails without it.

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
