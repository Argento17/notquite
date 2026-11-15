# Reference

Details behind the [README](../README.md). Everything here is read from the
code in `workorder/`; if the two disagree, the code wins and this file has a bug.

Install: `pipx install workorder`.

## Commands

| Command | What it does |
|---|---|
| `workorder init [--check] [--force] [--claude] [--update-workflow] [--light] [--test-first]` | Adds the README agent block once to AGENTS.md and CLAUDE.md, installs the hook, and writes the workflow if absent. --update-workflow rewrites it with a diff; combined with --check it only previews changes. With --force a different hook is saved as pre-commit.workorder-previous and called first; --check reports missing setup without writes and exits 1 if incomplete. --claude merges project hooks into .claude/settings.json |
| `workorder done --task ID [--note TEXT] [--timeout SECONDS] [--jobs N] [--needs-review] [--no-code-change] [--refactor] [--require-test-first]` | Refuses uncommitted code or empty Scope/Acceptance; runs the declared test and prints Acceptance for human confirmation. Writes DONE, receipts and the note together when supported; a refusal preserves the record. Never commits or pushes |
| `workorder new --title T --owner O [--ships none\|repo\|live] [--prefix TASK] [--template extended\|simple] [--test COMMAND] [--test-paths PATH ...] [--code-paths PATH ...] [--local] [--test-first]` | Reserves the next id on `origin`, or numbers it locally when there is no `origin` remote or with `--local` (no tag is pushed); writes `tasks/<id>.md` (extended by default; `simple` starts with the seven required fields). `ships` defaults to `none`; supplied paths are optional members in either template |
| `workorder receipt implemented --task ID` | Records the current commit as the implementation; refuses uncommitted or untracked code |
| `workorder receipt test --task ID [--timeout SECONDS] [-- CMD...]` | Runs `CMD` freshly (no shell), records its exit code against the current commit |
| `workorder receipt merged --task ID [--ref refs/heads/main]` | Fetches the branch from `origin` and records the commit it points to (needs `origin`) |
| `workorder receipt deployed --task ID --target NAME -- CMD...` | Runs a read-back command; valid only if it exits 0 and its output contains the full commit ID |
| `workorder receipt live --task ID --target NAME -- CMD...` | Same, recorded after the deployment |
| `workorder check [ID or tasks/ID.md]` / `check --task ID` | No argument: reads every task; one line per problem, or `check: OK`. With a task: an explanation of that task alone |
| `workorder check --staged` | For the pre-commit hook: checks the staged versions of the tasks this commit touches (task file, receipts, contract or its evidence) and the tasks that depend on them; refuses a commit that newly claims DONE or CLOSED on code other than the implemented code, and command changes on unfinished tasks; notes a finished-task command repair if the new command passes, and other requirement changes |
| `workorder check --base REF [--verbose] [--allow IDS] [--run-tests] [--strict-tests] [--review warn\|refuse] [--all-tests] [--require-task] [--require-task-ignore GLOB] [--require-test-first] [--approving-review] [--timeout SECONDS] [--jobs N]` | Checks changed task records and receipts, all repository test roots, every task whose tests changed, and dependents. Refuses stale claims, changed receipts, unsupported definitions and removed tasks; test changes are refused unless a reviewer adds the label or approves the current head; --review warn is optional. `--allow` accepts reviewed definition/test changes, REF failures and outcome changes, and complete removal of finished or cancelled tasks. `--run-tests` reruns the declared tests of supported claims; see [base checks](#checks-against-a-base) |
| `workorder show --task ID` | The task as a record: Claimed and Evidence status, what is still owed, dependencies, title, owner and test; mission, summary and next when filled in; body sections, a dated receipt timeline, any computed merge and Progress notes. Prints a next step for outstanding evidence or unclaimed Acceptance work |
| `workorder note --task ID [--next TEXT] [NOTE]` | Sets next when supplied; adds `- YYYY-MM-DD: NOTE` at the end of Progress when a note is supplied. Give either or both |
| `workorder renew --task ID [--timeout SECONDS] [-- CMD...]` | Records `implemented` and the task's test again on the current commit: one step after a squash merge or rebase. The merge itself is computed, never recorded by `renew` |
| `workorder repair --task ID` | Verifies a conflicted legacy append, keeps the common prefix, and converts each side's extra lines to receipt files |
| `workorder claim --task ID` / `release --task ID [--force]` | Takes or gives up a task with a pushed tag; `--force` releases a claim whose clone or worktree is gone, and records that it was forced |
| `workorder claims` | Active claims |
| `workorder ready` | Open tasks: free, held, blocked, or waiting for delivery |
| `workorder hook session-start` / `hook stop` | Local Claude Code session context and stop check; see [agent hooks](#agent-hooks) |

Commands exit 1 when they refuse something, except `hook stop`, which exits 2 for unsupported finished claims. This includes a command receipt
whose command failed and a receipt that was recorded but cannot count (it says
why). Every command resolves the repository root, so commands also work from subdirectories. `--all-tests` requires `--run-tests`; `--strict-tests`, `--review` and `--require-task` require `--base`. Without a base, `--run-tests --all-tests` checks current completion evidence and reruns finished tasks on current code.

`done` also requires the implemented code to differ from the target merge base.
Without a target, it compares with the commit that created the task (HEAD for an
uncommitted task). Otherwise it refuses: `TASK-1: no code changed since <ref>;
nothing was implemented`. Documentation, ignore rules, license files and image assets do not count as code changes. Use `--no-code-change` only for verification tasks with no code edits, including
no edits to test scripts. The comparison uses the target merge base, or task
creation without a target. A changed code file refuses even with review flags:

```text
TASK-1: code changed (feature.py); --no-code-change is for verification tasks with no code change
```

The flag exempts that run from the base-code failure requirement below. The test
receipt records the exemption and optional `no_code_change_base` commit. Checks
validate the exemption against that base, and against the branch comparison
for an exemption first recorded on the branch. A receipt already on the base
keeps its historical claim. Old receipts use their implementation base when
available, then the usual target or task-creation comparison. A label cannot
accept a false exemption.

When tests changed on purpose, rerun `done --task ID --needs-review`. Passing,
supported evidence is recorded with optional `pending_review: true` on the test
receipt, and the command writes DONE:

```text
TASK-6 DONE, pending review: the pull request needs workorder-allow:TASK-6 or an approving review
```

Changed shared test data and affected finished tasks are checked too. Their notes
list any additional task labels or `workorder-allow:tests` for files without an owner.
Current test failures still refuse completion with `--needs-review`.

No status edit is needed after review. `show` says `waiting for review` while the
receipt has not landed on the target. Local hooks accept the pending claim with
a note. The pull-request check refuses it by default until the task label or a
current-head approving review accepts it, even if no test files changed.
The label supplies `--allow TASK-6`. The main check after merge needs no label.
Later `renew`, `receipt test` or `done` runs do not clear an unmerged review request.
The flag does not accept failed current tests, assertion-free Python tests,
missing evidence, changed test commands or dependencies. Without it, `done`
refuses test changes locally. Pre-commit and Stop report test-file changes as
notes. A command repair on a task already DONE/CLOSED at the comparison base is
also a note if the new command passes. Pre-commit runs it on the staged code;
Stop runs it on the working copy. The note names the task's review label for the
pull request. Renew the evidence for the repaired command first. `done` still
refuses changed commands, as do hooks for tasks unfinished at the base.
Unsupported completion claims and failing current commands still refuse.

For a declared refactor, use `done --task ID --refactor`. Its test files, including
selected helpers and configuration, must be unchanged since the merge base, and
its declared test must pass on both versions. A local target must be available.
A pytest command without paths includes discovered test files in this check.
Explicit `test_paths` cannot hide test files selected by the command.
The test receipt records optional `refactor: true`. The pull-request check reruns
the comparison with `--run-tests` and prints:

```text
TASK-3 is a declared refactor: tests unchanged, passing before and after
```

Changed test files refuse with `its test files changed; use --needs-review`.
`--refactor` cannot be combined with `--needs-review` or `--no-code-change`.
A later test receipt without the marker ends the declaration.

### Test-first work

`done` and `check --base REF` name where each selected finished task's test came
from: `TASK-1's test landed on origin/main before this change and is unchanged`
or `TASK-1's test was written in this change`. The comparison uses the merge base.
An unavailable local target is reported by `done`. If the base task or its test
is absent, the origin is unknown or the test is new; the output never claims
that it landed earlier without that evidence.
For inline Python tests, the unchanged command in the base task supplies that
provenance; there is no separate test file.

Write a failing test first, then open its task with
`workorder new --test-first --title "..." --owner you --test "python -m pytest -q tests/test_feature.py"`.
Use `--test-paths` for wrappers. Every declared path must exist and the command
must fail on the current code before the task or reservation is created. A
passing command, missing executable, missing dependency, timeout or empty pytest
collection is refused. Land the task and failing test on main before doing the
implementation on a new branch. Fill in Scope and Acceptance as usual.

`workorder init --test-first` adds `--require-test-first` to the generated
pull-request check; for an existing workflow, add `--update-workflow`.
`check --base origin/main --require-test-first` refuses a finished claim whose
test files were added or changed in the change. Only that task's explicit
`workorder-allow:ID` label accepts the exception; an approving review, the
`tests` label and `--review warn` do not. The file rule works without running tests;
use `--run-tests` to check the before/after results. `done --require-test-first`
checks the same provenance locally and refuses before recording receipts,
including with `--needs-review`. No new task or receipt member is required.

## Task files

Commit `tasks/` and `.workorder/` with the work. Checks normally read the working
tree; `--staged` reads the index, and a workflow reads its committed checkout.

Only files named `tasks/<id>.md` are task records; other files in `tasks/`
(a README or Markdown notes) are ignored. Other files there are refused because `tasks/` is outside the code fingerprint. A task file starts with front
matter between two `---` lines, one
`key: value` per line, each key once. These seven fields are required: `id`,
`title`, `owner`, `status`, `ships`, `waits_on`, `contract`.

A value is plain single-line text, a JSON string, or a list. `note` appends a line under Progress. `done` clears `next` and changes the status line to DONE after checking the claim, and `done --note` also appends a dated line. Other task text is preserved. `note --next "TEXT"` replaces or adds the next field, including existing folded/literal values; an empty string clears it. With no NOTE it adds no Progress entry. `done --note` writes the handover line and new receipts only when DONE is written. A refusal leaves both unchanged, so retries do not duplicate them. Use `workorder note` to record progress separately during unfinished work. Refusal says `TASK-1: DONE was not written; the status stays IN_PROGRESS`, then the Missing lines. If already finished, it says `TASK-7 stays DONE; this change was refused: <reason>`. After a real failure, Next says `fix the code or the test, then run workorder done again`. `done` refuses a missing or empty Scope or Acceptance section, including sections containing only HTML template comments: `fill in Scope and Acceptance first`. It checks this before recording evidence. It preserves the status line's plain, single-quoted or double-quoted style, and its existing line endings. Success says `TASK-1 DONE: its declared test passed on this code`, followed by `; still owes: merge of the tested code` when applicable (and any other delivery debt). A separate line asks you to commit tasks/ and .workorder/, adding `and push` only with an origin remote. It warns when this clone has no pre-commit hook. With a locally available target, it runs the test-change checks below against the merge base of HEAD and that ref, plus the tests of affected finished tasks, before writing DONE and refuses with exit 1 when they find a problem, preserving the prior status. It adds `a reviewer accepts it with the label workorder-allow:<ID> (or an approving review)` to the same diagnostic. It does not fetch. Without a target it prints `could not compare with the target: fetch origin first`. Local main/master is used on a branch when no remote target is available. A passing run that does not count reports whether files or HEAD changed while it ran; generated files such as __pycache__/ should be added to .gitignore before committing and running done again. `done` needs a concrete declared command; use the individual receipts for `test: any`. A task
file that cannot be read is reported as a problem of that task; the other tasks
are still checked. Fields starting with `x-` (such as `x-issue`) are your own:
they are kept and never checked.

### Task example

`workorder new --title "Sort items by due date" --owner alex --test "python -m pytest tests/test_list.py"` writes `tasks/TASK-<n>.md` with these fields and sections for you or the agent to fill in. A filled-in task:

```markdown
---
id: "TASK-4"
title: "Sort items by due date"
owner: "alex"
business_owner: "sam"
priority: "P2"
created_at: "2026-11-14"
mission: "Show what is due first"
depends_on: ["TASK-1"]
blocks: []
next: "Agree the order for items without a date"
summary: "A sort option on the list view"
status: "IN_PROGRESS"
ships: "repo"
waits_on: []
test: "python -m pytest tests/test_list.py"
contract: "none"
---

# TASK-4

## Context

Users scroll past finished items to find what is due. The list view is in app/list.py.

## Scope

In: a "due date" sort option on the list view.
Out: changing how dates are entered.

## Lanes

- Sorting logic and tests: the agent
- Wording of the sort option: sam

## Acceptance

- Items with a due date come first, earliest first.
- Items without a date keep their order, at the end.

## Progress

- 2026-11-14: sort implemented; undated items still to place.
```

- **For people and agents:** `mission`, `summary`, `next`, the owners, `priority` and the five sections say what the work is, what is in and out, who does what, and how it will be accepted. Workorder keeps them in the file and never treats them as evidence.
- **`status`** is the claim: `IN_PROGRESS`, `BLOCKED`, `DONE`, `CLOSED`, or `CANCELLED` for work that will not be done (it needs no evidence and leaves `ready`). You or the agent can edit it. `workorder done` sets DONE when its check supports the claim; the status alone never proves completion.
- **`test`** is the command that proves the task. A task needs one to be DONE: only a run of exactly that command counts, and `workorder receipt test --task <id>` runs it. For work no single command proves, `test: "any"` says so explicitly and accepts any recorded command. No shell runs it: words split on spaces, quotes group words, backslashes stay as written, and `&&` or pipes are refused (use a script).
- **`depends_on`** lists tasks this one needs. `waits_on` takes `task:TASK-2` or `person:alex` items (remove a person wait when it is answered). Both form one dependency list, and a task cannot be DONE while one is open. A dependency is finished when it says DONE or CLOSED, its evidence supports that, and its own dependencies are finished. `blocks` is informational: it is checked against the other tasks but never enforced.
- **`ships`** sets what CLOSED needs beyond DONE: `none` (the default), `repo` (the tested change on the target branch) or `live` (also deployment read-backs).
- **`contract`** is `none` or a path to a contract that maps each acceptance criterion to evidence Workorder can check ([contracts](#contracts)). Without one, the Acceptance list is for the agent and the reviewer; Workorder does not check whether its items are met.
- **Your own fields** start with `x-`, such as `x-issue: "#42"`. They are kept and never checked.

Front matter is a small, documented subset: single-line text (plain, quoted or JSON), lists, `>` and `|` blocks for `mission`, `next` and `summary`, `- TASK-1` lists and `#` comment lines. Other YAML, and unknown fields, are reported for that task, so a typo cannot quietly drop a dependency. The [reference](#what-yaml-is-read) lists exactly what is read.

Markdown files in `tasks/` whose names are not task ids, such as a README or notes, are ignored. Any other file there (code, for example) is reported, because `tasks/` is outside the code fingerprint and changes to it would never be checked.

For small work, `workorder new --template simple` writes the seven required fields only (`id`, `title`, `owner`, `status`, `ships`, `waits_on`, `contract`), the format of version 0.1.0; add `test:` before claiming DONE. Both formats can sit in the same repository. Version 0.1.0 refuses task files that use the other fields, so upgrade every clone and CI pin before using them.

### What YAML is read

Front matter is not parsed as YAML. Workorder reads this subset and refuses
everything else with a message naming the line:

- `key: value` on one line: plain text, a JSON string (`"..."`), or a single-quoted
  YAML string (`'...'`, with `''` for a quote).
- Comment lines starting with `#` at the left margin. A UTF-8 byte order mark
  at the start of the file is ignored.
- Lists, on one line: JSON (`["TASK-1", "task:TASK-2"]`) or simple
  (`[TASK-1, task:TASK-2]`, items without spaces or quotes).
- Indented lists for `depends_on`, `blocks`, `waits_on`, `test_paths` and `code_paths` only:

  ```yaml
  depends_on:
    - TASK-1
    - "TASK-2"
  ```

  The `- item` lines may also start at the left margin, as YAML allows.

  A list field with nothing after the colon and no items is an empty list.
- `>` (folded) and `|` (literal) blocks for `mission`, `next` and `summary`
  only, with optional `-` (strip) or `+` (keep) chomping. They follow YAML:
  `>` joins lines with spaces, keeps empty lines as line breaks and keeps lines
  indented further than the block on their own lines.
- Empty lines between fields.

Not read, and refused:

- anchors and aliases (`&`, `*`), tags (`!`), nested mappings, and
  multi-line plain or quoted values. A `#` after a value on the
  same line is not a comment here: it stays part of the value;
- block text on any field other than `mission`, `next` and `summary`, and
  indented lists on any field other than the four list fields;
- block indicators with an explicit indentation number (`|2`);
- a block line indented less than the block's first line (YAML would end the
  block or treat it as a comment; Workorder refuses rather than guess).

Block values were checked against PyYAML: for the supported forms they read
the same, except that Workorder refuses the less-indented lines above.

- `id` matches the file name and the pattern `PREFIX-N`, for example `TASK-12`.
- `status`: `IN_PROGRESS`, `BLOCKED`, `DONE`, `CLOSED` or `CANCELLED` (needs no
  evidence, is never a finished dependency, and is left out of `ready`).
- `ships`: `none`, `repo` or `live`.
- `waits_on`: a JSON list of `task:<id>` or `person:<name>`.
- `contract`: `none` or a repository-relative path.

### Extended fields

`workorder new` (and `--template extended`) writes these optional fields too,
in this order: `id`, `title`, `owner`, `business_owner`, `priority`, `created_at`,
`mission`, `depends_on`, `blocks`, `next`, `summary`, `status`, `ships`,
`waits_on`, `test`, `contract`. The body gets `## Context`, `## Scope`, `## Lanes`,
`## Acceptance` and `## Progress` sections. Any subset of the optional fields
may be used; any other field (except `x-` fields) is refused, so typos are caught.

- `business_owner`, `priority`, `mission`, `next`, `summary`: text, may be
  empty. Not used by any check.
- `created_at`: empty or an ISO date or time (`2026-11-14`,
  `2026-11-14T09:30:00Z`). `new` fills in today's UTC date.
- `depends_on`: task ids (`TASK-2` or `task:TASK-2`). Not the task itself.
- `test`: the task's test command as one line, or `any`. A DONE or CLOSED
  claim needs it set (an empty or missing `test` is owed as "a declared test").
  A command means only a `test` receipt whose command is exactly this counts,
  and `receipt test --task ID` with nothing after it runs it; `any` accepts any
  recorded command. `new --test COMMAND` fills it in. Words split on
  spaces, quotes group words, backslashes are kept as written, and shell
  operators (`&&`, `|`, `;`, `>`) are refused: put compound steps in a script.
- `test_timeout`: optional positive, finite seconds, for example `test_timeout: 60` or `0.5`. There is no deadline when absent. `--timeout SECONDS` overrides it for `done`, `receipt test`, `renew`, and `check --base REF --run-tests`, including REF comparisons and `--all-tests`. A timed-out run fails with recorded exit code 124 and says `test timed out after ... seconds (exit 124)`; receipt commands exit 1 and `done` leaves the status unchanged. On POSIX the deadline also kills child processes in the test process group.
- `test_paths`: an optional list of repository paths, for example
  `["tests/test_list.py", "tests/integration"]`. A folder includes every file
  below it. Paths must stay inside the repository. If absent, existing file
  and folder paths in the `test` command are used, including quoted paths and
  pytest node paths such as `tests/test_list.py::test_sort`. Both the base and
  current definitions are read, so deleting a file or removing a path from
  the task cannot hide a change. An explicit empty list selects no test files.
  Changing `test_paths` itself is refused under `--base` unless accepted by `--allow ID`; `--review warn` is optional.
  `new --test` writes inferred paths only when they exist at task creation.
  A test named in the command can be added later without editing the task.
  Wrapper commands require `new --test-paths PATH ...`; those paths may name
  future files or folders. The wrapper script alone does not describe its tests.
- `code_paths`: optional files or folders, for example `["src/calc.py", "src/math"]`.
  `new --code-paths src/calc.py src/math` writes this member in either template.
  Under `--require-task`, when every task adding a receipt
  declares this field, changed code must fall within their combined paths.
  An empty list covers no code. If any such task omits it, the usual rule applies.
  See [code changes](#code-changes-need-task-evidence).
- `merge_ref`: a full branch ref, such as `refs/heads/release` or
  `refs/remotes/origin/release`. Overrides the computed merge target. Short
  names and tag refs are refused. An absent target does not prove a merge.
- `blocks`: task ids. Informational: `check` reports a `blocks` entry that
  names an unknown task, or two tasks that each say they block the other, but
  never makes a task wait because of it.

### Dependencies

`waits_on` and `depends_on` form one dependency list: `waits_on` items plus
`task:<id>` for each `depends_on` id, without repeats. Listing a task in both
is allowed. A DONE or CLOSED claim is refused until every task dependency
is finished and no `person:` wait remains. Remove a person wait when it is answered. A task dependency is finished when
it says DONE or CLOSED, its evidence computes DONE or CLOSED, and its own
dependencies are finished, all the way down; a reopened or cancelled task is
not finished. `ready` lists open dependencies as blocked. A task may not both
wait for and block the same task. Missing task dependencies and dependency
cycles, across both fields, make `check` fail. Each cycle is reported on its
participating tasks. `check` continues checking other tasks, and `ready`
continues listing them.

Planning fields and sections do not supply evidence. `depends_on` adds
dependencies, `test` selects the command that counts, and `merge_ref` selects
the branch used to compute delivery.

## How the status is computed

- **IN_PROGRESS** until the task declares a `test` (or `any`), there is an
  `implemented` receipt, the latest `test`
  receipt on the same code after it is passing and valid, and every critical
  contract criterion is met. A later failing or invalid run on that code
  revokes an earlier pass; a later passing run restores it. Runs on different
  code are recorded but ignored, and the receipt command exits 1.
- **DONE** when that holds.
- **CLOSED** when DONE and delivery is complete:
  - `ships: none`: nothing more.
  - `ships: repo`: a computed merge (see below), or a `merged` receipt after
    the implementation, whose commit
    contains the implemented commit or whose code matches the implemented
    code. Any such receipt counts; a later one that cannot count does not undo
    it.
  - `ships: live`: also a valid `deployed` receipt after that merge, whose
    commit contains the implemented commit or holds its code, then a valid
    `live` receipt for the same commit and
    target. "Valid" means the read-back command exited 0 and returned the
    expected commit ID. It does not show which server answered or that it is
    production. With a computed merge, read-backs after the implementation
    are judged as after the merge. A deployment can also contain the matched
    squash or rebase commit. The live read-back must still follow deployment
    and name the same commit and target.

Only receipts after the newest `implemented` receipt count. Recording a new
implementation starts the evidence again.

`show` and plain `check` describe supported recorded evidence as
``recorded receipts support the claim (not re-run here; `check --run-tests` re-runs it)``.
Use `check --run-tests` to execute the commands again. Its summary counts tasks
whose declared tests ran, so one task tested on current and base code counts once.
Shared commands still run once per code version and timeout; each task they
check counts once. Unowned Python file comparisons are counted separately.

A `test` receipt is valid only if the command exited 0, and the working tree
was still clean with HEAD unchanged when it finished. When the task declares a
`test`, only receipts whose command is exactly that one decide.

"Code" is a SHA-256 over the commit's file listing, leaving out `tasks/`,
`.workorder/` and `.notquite/`. Committing task files and receipts does not change it, so they
can be committed after the evidence is recorded, and later runs on the same
code, on any commit, still count.

Recorded test evidence is evaluated against the newest `implemented` receipt. A
DONE or CLOSED claim must therefore be made on the implemented code:

- An uncommitted claim (the task says DONE or CLOSED, HEAD's copy does not) is
  compared with HEAD's code, or with the index under `--staged`.
- A committed claim is traced through the task file's history to the commit
  that made it (since `REF` with `--base REF`), and that commit's code is
  compared with the implementation the claim relied on, unless a newer
  `implemented` receipt was recorded after it. A claim carried into a verified
  squash or rebase is accepted on that merged change, from any checkout.
  For all merge methods (ancestry, same code, squash and rebase), a claim on
  commit C descending from the matched landing M must have the same content
  as M on every code path changed by the implementation. A revert, overwrite,
  deletion or rename of those paths before C needs `workorder renew --task ID`
  on the claimed code. Changes confined to unrelated paths are allowed.
  Changing DONE to CLOSED makes a new claim, including in the index or
  working tree; the earlier DONE does not excuse changed code at CLOSED.
  A valid claim at or before M stays valid when later commits change those paths.
- After a branch rebase, the claim also counts when its code diff from its
  merge base with the target has the same `git patch-id --stable` as the
  implementation's code diff from its own merge base with that target.
  Both patches must be nonempty. Task files and receipts are excluded.
  Unrelated changes on main do not invalidate the claim. A changed patch
  still needs fresh evidence. Merging main into the branch also works.

For the `--require-task` receipt check, a rebased branch based on the target is
also covered when every recorded implementation path has exactly its receipt's
content and mode, and every other code path matches the target. Deleted paths
must stay deleted. This uses the optional path records introduced in 0.12;
receipts without them retain their existing rules. A later edit on the branch
still needs a new receipt even when the declared test would pass.

A task imported unchanged from the target branch or a merge parent is not a
new claim: both its task file and all its receipts must match that source.
The staged hook reads MERGE_HEAD during a pending merge; committed checks also
inspect merge parents. This does not excuse reopening a task and making a new
claim on changed code. If your own claim becomes stale after merging main,
run `workorder renew --task ID` on the branch before pushing; the later squash
can then match the code tested after that merge.

`check <id>` adds a note when HEAD's code differs, and a `Command:` line naming
the command behind the deciding test run. Code that lands after a task is DONE
does not reopen it.

Receipts name commits. A receipt whose commit is not in this clone (after a
squash merge, a rebase or an amend once the originals are gone, or in a
shallow clone) is kept. It counts again when a commit in this clone holds
exactly the code it recorded: HEAD, or a commit that touched the task or its
receipts, such as the squash commit. New implemented receipts also record the
changed paths, their Git modes and blob IDs, and deleted paths. A landing that
carries that receipt and matches those paths counts even when parallel work
changed other files. This works after the source branches and commit objects
are gone. Otherwise it does not count, and
`renew --task ID` records the evidence again on the new commit.

## Computed merges

For `ships: repo` or `live`, no `merged` receipt is needed when Git history
shows one of these matches:

1. The implemented commit is an ancestor of the target.
2. A code-changing commit on the target holds exactly the implemented code.
3. The code diff from the merge base to the implementation has the same
   `git patch-id --stable` as a commit on the target after that base (squash;
   a single replayed implementation commit is reported as rebase).
4. Every code commit from the merge base to the implementation has a patch
   equivalent on the target after that base (rebase, using `git cherry`
   semantics). At least one code patch must match. Merge resolutions in the
   implementation range are not guessed from their separate parent patches.

Code patches exclude `tasks/`, `.workorder/` and `.notquite/`. Empty patches
and changes confined to those folders cannot supply a patch match. Tests,
contracts and dependencies still have to pass. An existing `merged` receipt
continues to count by the original ancestry or code-fingerprint rules.

Ancestry names the first landing on the target's first-parent history;
same-code and patch searches keep the earliest matching landing within the
search window. For fast-forwards, Git records no branch boundary: the path
comparison uses the task's most recent open period before implementation,
or the implementation's parent if the task was introduced later. This is
conservative when unrelated work also happened during that open period.
Within that range, commits recorded as another task's implementation are
excluded from this task's protected paths. Commits covered by this task's
previous implemented receipt are excluded too. Paths changed by the remaining
commits stay protected even when another task also changed them. Without those
receipt boundaries, the full implementation range still supplies the paths.
For a committed claim, these boundaries use the receipts in that commit, so
later task removal does not change which paths that claim protected.
Without the original history, new receipts protect their recorded implementation
paths. Old receipts conservatively protect the available landing tree.

The target is `merge_ref` when set; otherwise the first existing branch in
this order: `refs/remotes/origin/main`, `refs/remotes/origin/master`,
`refs/heads/main`, `refs/heads/master`. `origin/HEAD` is ignored, even if it
points to a feature branch. A remote `merge_ref` must name both the remote
and its branch (`refs/remotes/<remote>/<branch>`).
A commit only on local `main` is not merged when `origin/main` exists until
it is pushed (pushing updates the local copy of `origin/main`). Checks read local refs without
fetching. Checks use this target from any checkout, including feature
branches and detached checkouts. The checked-out feature branch is never
used as its own merge target.

The search examines up to 1,000 target commits after the merge base. Rebase
comparison also limits the implementation range to 1,000 commits; a partial
range never proves a rebase. Ancestry is checked before the bounded search.
Results and code fingerprints are cached in `.git/workorder-digests/`, keyed
by immutable commit IDs. The cache is local and can be removed. Each command
also shares receipt and Git reads in memory, then discards those reads.
Committed metadata blobs are read in batches. Linear task histories share one
history walk; merge histories retain Git's per-task history rules.

Patch checks need the original implementation history. If it is absent,
Workorder can still recognise exactly matching code or a landing that matches
the paths recorded in a new implementation receipt. Old receipts without those
paths retain their original exact-code rules. Fetch history or renew evidence
when neither match is available.
When the target's change differs, `next_step` suggests
`workorder renew --task ID` on main or `workorder receipt merged`. If the
matching change is on local main but not the selected origin/main, the next
step says to push main instead.
`show` and `check` explain a match, for example:
`merged into main by squash 1a2b3c4, computed`.

## Checks against a base

`check --base REF` uses `git merge-base HEAD REF` as the old revision for all
comparisons. Fetch first; new work only on the target is not a branch deletion.
Messages retain the target: `since origin/main (merge base abc1234)`. In this
section, REF tests and definitions mean the versions at that merge base.

`check --base REF` checks changed task records, receipts, contracts and evidence,
and their dependents. Test review covers repository test files and every task
whose selected tests changed, even when its task file and receipts are
untouched. Repository test folders have whole names `test`, `tests`, `testing`,
`__tests__`, `spec`, `specs`, `fixtures`, `testdata`, `test_data`, `testsupport`,
`test_support`, `golden` or `__snapshots__`, or start with `test_` or end with
`_test`/`_tests` (case-insensitive). Every file below them counts, including data.
Folders such as `latest` and `contest` are production folders. Test files also
include files named `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`,
`*.spec.*`, `*_spec.*`, `*_tests.*`, `testing.py`, `*_testing.py`, or starting with `expected`, `golden` or `snapshot`, plus `conftest.py` and pytest configuration. A test file at the
repository root does not make the root a test folder, so production code is
not a test file. Importing a package from a test does not make that package a
test file. Both REF and current definitions count. Metadata paths under
`tasks/`, `.workorder/` and `.notquite/` stay outside test discovery.

Test changes are refused unless a reviewer adds the label or approves the current head. `--review refuse` is
the default. `--review warn` is an option: each finding has a `Warning:` line
and a `Review: N test changes need a reviewer's look` summary. `check: OK` follows
the warnings. With `GITHUB_ACTIONS` set, warnings also have file and line
annotations. `--allow` accepts the corresponding findings. False claims and
current failures still refuse in both modes.

| Change or result | Default | Reviewer acceptance |
|---|---|---|
| Only added test lines, new test files, or added Python import names preserving existing bindings, when the base tests passed | Passes if the current test fails on base code | `--strict-tests` reviews additions too |
| Any change to a test whose base tests already failed, including an added line | Refused | `--allow ID`; `--allow tests` does not accept this |
| Removed or changed test lines, including a rename | Refused | `--allow tests` or any owning task accepts the one finding for that file |
| REF tests fail on current code, including a finished task whose base tests already failed | Refused | `--allow ID` accepts an intended behavior change or test rename |
| Fewer pytest passes, or more skips, xfails or deselections | Refused | `--allow ID` accepts changed outcomes |
| New skip/xfail markers or calls, or conftest or pytest configuration changes | Refused, including additions | `--allow ID`, or `--allow tests` for those lines |
| Pytest `-k` or `--deselect` changes | Refused | `--allow ID` only |
| Current declared tests fail for a DONE/CLOSED task | Refused | No label excuses the failure |
| Current tests fail or are missing for IN_PROGRESS/BLOCKED work | A note; no finished claim is made | Test edits and REF regressions still need review |
| A task was DONE/CLOSED at REF, is now IN_PROGRESS/BLOCKED, and its current test fails | Refused by the base check; local checks retain a progress note | Only that task's explicit `--allow ID` accepts the failure |

For a task being finished whose own code or tests changed, `done` and
`check --base REF --run-tests` also run its
current declared command on the merge-base code, overlaying its new or changed
test files. A passing run there refuses completion:

```text
TASK-2: its test passes on origin/main's code too, so it does not show the change works; make the test check the new behaviour
```

The diagnostic also gives `--allow TASK-2` advice for a reviewed refactor or
verification task. `--allow tests` alone does not accept it. `--review warn`
reports the finding as a warning. `done --needs-review` records evidence and
writes DONE pending review. `done --refactor` accepts unchanged tests passing on
both versions without a label. A `done --no-code-change` run is exempt;
its optional receipt marker carries the exemption into subsequent checks.
A later test receipt without the marker ends that exemption. Current failures
still refuse. Base-code commands that cannot start, time out, or lack external
dependencies are reported as comparison problems. A new repository module may
be missing at the base. A failure proves that the command distinguishes the
versions; it does not establish that every assertion expresses the requirement.
An unrelated task-file note or progress update does not trigger this comparison.
Task scope comes from `code_paths` when supplied, otherwise recorded implementation
paths, together with the task's selected test files. Shared configuration edits
still receive file review, with one finding naming all owning tasks.

For changed or added Python functions, `done` and the finished-task base check
with `--run-tests` make one further temporary copy. They compare function syntax
trees with the base, replace every changed or added function body with
`raise NotImplementedError('workorder stub')`, and run the task's command once
with all replacements together. Methods, nested functions and async functions
are included; whitespace and comments alone do not change a function. Test
material is excluded, and source files in the checkout are never rewritten.
If the command still passes, it does not exercise the changed functions and
completion is refused. The diagnostic names those functions and files.
Only the task's explicit reviewer label accepts this finding in a base check;
an approving review, `--allow tests`, `--needs-review` and `--review warn` do not.
Declared refactors and valid no-code-change runs are exempt. This check only
covers Python function changes; a failing run does not prove that every changed
function was exercised or that the test checks the intended result.

In new or changed Python test files, a test function without a recognised
assertion is refused by `done` and the base check, even without `--run-tests`:

```text
TASK-2: test_div in tests/test_div.py asserts nothing that can fail
```

An assertion expression with no name, attribute, subscript or call is constant
and treated like `assert True`. This includes `assert 1 == 1`, literal containers
and arithmetic on literals. Equality with the same name, attribute or subscript
on both sides, such as `assert value == value` or `assert row['id'] == row['id']`,
is also refused. Expressions are inspected, never evaluated.
`pytest.raises` (including aliases) and calls whose names start with `assert`,
`verify`, `check`, `expect` or `ensure` count. Other called functions count when
their bodies contain an assertion: Workorder follows up to four helper calls
within the test file, conftest.py files on its path, and imported test-material
modules. Import aliases and relative imports are understood. An uncalled helper
body does not count. Production imports do not become test material.

The task's explicit `workorder-allow:TASK-2` label accepts an unusual valid test;
`--allow tests` accepts this finding only for files with no task owner. An
approving review alone and `--review warn` do not accept it. `done` still refuses
unrecognised syntax; record passing evidence with `renew`, set the completion
claim after checking Acceptance, and obtain the task label for the pull request.
Current test failures still refuse even with the label. Unchanged files are left
alone. A missing new module alone cannot make an assertion-free function evidence
for a change. These checks do not judge assertion meaning.

Adding `div` to `from calc import mul` is an addition when the existing import
and bindings stay intact. This also covers parenthesised imports and aliases.
The exception applies to top-level Python imports; it does not accept changed
modules, removed names, rebinding a name used by the old tests, or changed test
bodies and assertions. New skip markers and configuration still need review.

With `--run-tests`, tasks this change works on and all tasks whose selected tests
changed run their current commands and their REF tests on current code. A task
is being worked on when its task file or receipts differ from REF. Its REF
comparison still runs when its tests are unchanged. Each comparison uses a
temporary copy: REF test files and command are applied over current code, added
test files are absent, and deleted files are restored. The checkout is never
overlaid. If the task or its selected tests did not exist at REF, a note explains
why no REF run is possible. Changed unowned Python test files also run with
`python -m pytest -q <path>` in temporary copies. Their base version must pass on
base code before a failure on current code is attributed to the change:

```text
tests/test_mul.py: its version from origin/main fails on this code (behaviour changed)
```

This finding replaces the removed-line finding for that file and needs
`workorder-allow:tests`; an approving review does not accept it. Other unowned
files receive static review because no test runner is declared for them.

Temporary copies use tracked files, plus untracked files that are not ignored
for a working-tree check. A staged check uses index contents; a REF copy uses
that revision's tracked files. Ignored dependencies such as `node_modules/`
and `.venv/` are shared through links. If links cannot be created, they are copied and a note explains the fallback. Tracked
files take precedence over those links. Python bytecode and pytest caches are
not linked. Tests can read the shared dependencies; writes to a linked
dependency also affect the local dependency. Source and test files stay in
independent copies. Internal absolute symlinks are rebased into the copy, including Windows extended paths and case variants.

When REF tests fail on current code, they also run on REF code before this change
is blamed. An existing REF-code failure is a note. A failure or timeout on current
code after a passing REF-code run needs acceptance. Commands that cannot start,
copy failures, unavailable reference files and unreadable pytest outcomes get
explicit diagnostics; acceptance cannot supply missing comparison results.
External command or dependency failures name the environment problem. A missing
module belonging to the repository is checked against REF: if the REF tests
pass there, a module rename or removal is a code change subject to review.

For example, an intentional change from 10% to 20% needs acceptance for edited
assertions and failing REF assertions:

```text
TASK-1: its tests from origin/main fail on this code; a reviewer accepts it with the label workorder-allow:TASK-1
```

A task owns the files its test command names, the folders it names, and
`test_paths`. A folder includes every file below it. It also owns `conftest.py`
and pytest configuration on the path to those files. A named file does not
include its siblings, and a command that names no path does not own every
folder called `tests`. `workorder new --test` writes `test_paths` when the
command names files or folders that exist in the repository and is not a wrapper. A word such as
`test` in `make test` or `npm test` does not supply a path unless it exists.
Helpers and data outside the recognised test material need explicit `test_paths`. A test file at the repository root
does not pull in the rest of the repository, and importing a package does not
make that package a test file. A missing named test never widens to unrelated
tests. An explicit empty `test_paths` selects no task test files, but cannot
hide repository tests from file review. `--allow tests` accepts every changed
test line in the change. It does not accept a pytest `-k` or `--deselect`
change, an owned base test that fails on the new code, or any change to a task test that
already failed at the base. For unowned old-test failures, `--allow tests` is the explicit acceptance.

A finished task whose declared test maps to no test files in either the base
or current code, when the repository has test files at the base, is refused:
`TASK-1: no test files are known for its test; set test_paths`.
Files named by `test_paths` or the command count when added on the branch.
New test files are additions and need no label under the default rules.

Commands through make, npm, npx, yarn, pnpm, go, cargo, tox, nox, sh or bash, and
non-test scripts, require explicit `test_paths` under `--base`. The
wrapper itself is also protected. Use those paths for dynamic imports and tests
hidden behind a wrapper. Declaring `test_paths` is part of the task definition;
changing it is refused unless accepted by `--allow ID` (`--review warn` is optional).
A script named by `test_paths` may itself be the test. `new` asks for
`--test-paths` up front for wrapper commands. An unfinished task gets a wrapper
note locally. A base check requires test paths only for tasks this change
touches through records, receipts or test files; an unrelated unfinished task
on main does not refuse the change.

Pytest configuration covers conftest.py, pytest.ini, .pytest.ini, pytest.toml,
.pytest.toml, tox.ini, setup.cfg and pytest sections of pyproject.toml, plus custom
configurations selected by a command. Unrelated TOML sections need no review.
Each changed configuration file produces one file finding naming its owners;
`--allow tests` or any owning task label accepts that file finding.
Without `--run-tests`, file, configuration and selection checks still apply and
a note says the REF comparison has not run. A passing command cannot establish
that its assertions express the right requirement.

Moving DONE/CLOSED back to IN_PROGRESS or BLOCKED usually needs `--allow ID`.
A new counting failed test receipt and the reopening in the same commit are
accepted as evidence that the task needs more work. Set the task to IN_PROGRESS
before committing that failure; retaining DONE is an unsupported claim.
Returning to an unfinished status keeps a failing current test as a local progress
note. A pull-request base check runs that reopened task's current command even
without `--run-tests`; if it fails, the pull request requires the explicit
`workorder-allow:ID` label from a reviewer. A failed receipt, an approving review
and `--review warn` do not accept that failure.
Moving any other status to CANCELLED needs reviewer acceptance for closing work without
finishing it. `--staged` reads the staged status and receipts.

Scope or Acceptance removals and rewording need `--allow ID`. Additions need it
too when the task was DONE or CLOSED at REF; filling in an open task does not.
Changes to `test`, its contract or evidence, and removed dependencies need review.
A task absent at REF needs no definition label when `--run-tests` proves its
current test fails on merge-base code and passes now, with no assertion-free
functions found. This also applies to new tasks whose receipts cover code under
`--require-task`. The reviewer sees:

```text
TASK-40 is new in this pull request; its test fails on origin/main and passes now
```

Otherwise a new finished task, or a new task supplying required code receipts,
still needs definition acceptance, without claiming that no task recorded code:

```text
TASK-4 is new in this pull request: a reviewer accepts its definition with the label workorder-allow:TASK-4 (or an approving review)
```

Removing a task is refused by default, including a task with no receipts. With
`--allow ID`, a task that was DONE, CLOSED or CANCELLED at REF may be removed with
all its receipts in both receipt folders. Partial removal, removal of unfinished
tasks and edits to retained receipts still refuse. Dependencies must remain valid.
Every commit in `REF..HEAD` is checked for receipt deletions or modifications,
including newly added receipts later deleted and edits restored before HEAD.
The complete task-removal exception also exempts that task's receipt history.

`--allow` takes comma-separated task ids and the special values `tests`,
`no-task` and `workflow`. Task ids accept the reviewed changes described above. `tests` accepts
changed test lines, whether owned or unowned. `no-task` bypasses only `--require-task`; it does not
accept test edits, changed receipts, unsupported claims or missing dependencies.
`workflow` accepts changes to the Workorder workflow only.
No allowance excuses a failing current test for a finished task.

### Current runs, cache and summaries

`--all-tests --run-tests` runs every DONE/CLOSED task's concrete declared command
on current code, once per distinct command and effective timeout. It works with
or without `--base`. A regression refuses with `regression: TASK-1's test fails
on this code`; the recorded evidence is unchanged. An unfinished task's failed
or missing current command is a progress note, except for the reopened-task
base check described above. `test: any` has no concrete
command to rerun. These checks write no receipts.

`check --jobs N` and `done --jobs N` run distinct current commands concurrently.
N must be a positive integer; the default is the CPU count, or one when it is
unknown. Identical commands and effective timeouts still share one run. Results
print in task order, regardless of which command finishes first. `--jobs 1`
runs them one at a time. Base-code, old-test and function-replacement comparisons
retain their own temporary copies.

`done`, `receipt test`, `renew` and explicit `check --run-tests` execute tests
freshly. Receipts always describe a run executed by that command. Only implicit
hook runs may reuse a local result, with `(reused from HH:MM UTC)` in the output.
Plain `check` reads evidence without requesting a test run.

The reuse key covers tracked and untracked non-ignored file contents, paths,
file types and executable bits (excluding tasks and receipts), the command and
effective timeout. It does not cover ignored dependencies, services or environment
variables. A metadata-only commit keeps the same key. Results live in
`.git/workorder-tests/`, are not cloned, and can be removed. CI runs bypass this
cache. Identical commands and timeouts share a run within one check. Unchanged REF
tests with the same command use that current run too; changed REF tests use
their own snapshots. Cache lookup happens before making a test copy. Staged
files are read with a single batched `git checkout-index` call using Git checkout conversion and staged attribute rules, so CRLF checkouts
can reuse the same run without using unstaged attributes. Use an explicit command to test changed external inputs.

Notes of the same kind are grouped, for example `Note: 200 tasks' tests passed
when this check ran them`. Exemptions remain individual task notes, including
no-code-change, refactor and pending-review declarations, and test-origin notes
for tests written in the change. Use `--verbose` for each ordinary task note. Every mode prints
a summary, except `--staged` with nothing in scope:

```text
checked 3 tasks (2 claimed DONE or CLOSED); ran 2 tasks' declared tests
```

The run count appears when tests run and counts tasks, including failures and
cached results. Current-code and base-code runs of one task count once. When all
comparisons were cached the summary says `(cached)`; when some were, it says
`(some comparisons cached)`. A missing executable does not count. If none ran, another line says
`no declared test ran in this check`. Task counts include unreadable and removed
tasks; unreadable claims are excluded from the claim count. An unfinished
single-task summary says `checked 1 task (not claimed finished)`.
Supported finished claims without a contract also get the Acceptance reminder;
unclaimed and refused tasks do not contribute to its count. Checks exit 0 for
acceptance and 1 for refusal.

A run reporting zero passed tests never supports DONE, including skipped or
xfailed-only runs. `done`, hooks and `check` refuse without extra flags, for
example `its test ran but no test passed (1 skipped)`. A label or approval cannot
accept it. New receipts record `valid: false` and optional `no_tests_passed` detail;
0.12.0 readers already refuse that validity value. Old receipts stay readable;
`--run-tests` checks their current results too.
The next step says the test must report at least one passing test.

Failing base-branch tests run on current code are headed
`Tests from origin/main on this code:` (using the selected ref). Identical output
is printed once, with a labeled reference when the base run shares that output.

Test time depends on distinct commands, their durations and the available workers.
Shared commands run once; independent current commands can overlap up to `--jobs`.
Tasks being worked on and tasks whose tests changed also need REF comparisons.
New workflows enable `--all-tests`; projects can deliberately choose narrower
checks.

For Python test functions, line edits need no review when their syntax trees
match after ignoring comments, docstrings and assert messages and ordering the
two operands of `==` or `!=`. Chained comparisons, changed conditions, decorators,
imports that change bindings, deletions and other edits still need review.
`--strict-tests` continues to review every test-file edit. Repeated reuse notices
for the same UTC time print once.

### Code changes need task evidence

`check --base REF --require-task` requires a new counting implementation or test
receipt for at least one task with a concrete declared command. That task must
be unfinished at the merge base, new with the proof described above, or accepted
by a reviewer with `--allow ID`. Renewing an unrelated finished task does not qualify without that approval. A test that
does not count or a `test: any` task does not meet the rule.
Progress notes, status edits, new task definitions, merge receipts and deployment
receipts alone do not meet it either. A reviewer can accept a code change without
that task evidence with `--allow no-task`.

The rule applies only to non-test code: all recognised test files and explicit `test_paths` are excluded. It also excludes `tasks/`, `.workorder/`, `.notquite/`, `docs/`, `.github/`,
`*.md`, `*.rst`, `*.txt`, `LICENSE*`, Git ignore/attribute rules, editor settings and common image/PDF assets. This exemption does not change the code
fingerprint used by receipts. Generated pull request checks enable the rule.

Lockfiles are always excluded from this task requirement, including in nested
folders: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`,
`Pipfile.lock`, `uv.lock`, `Cargo.lock`, `go.sum`, `Gemfile.lock` and `composer.lock`.
For other paths, repeat `--require-task-ignore GLOB`, for example
`--require-task-ignore 'generated/**' --require-task-ignore '*.svg'`.
Patterns match repository-relative paths. These exceptions affect only the
code-to-task requirement; they do not accept test changes or false finished claims.

A receipt recorded on a branch still covers its own changes when later code
matches the exact clean merge of that receipt's commit and the target. This
works on the pull request merge commit and after merging main into the branch,
including parallel pull requests with squash merges. A local squash commit with
the hook enabled also accepts the recorded paths when their staged contents,
modes and deletions match the implemented receipt, even when another task landed
first. Both claims remain checkable in a fresh clone after branch deletion; no
renewal is needed for those matching paths. It does not accept later
branch edits without a new receipt. When renewal is needed after main moved,
the check says `main moved since TASK-2 was tested: merge it and run workorder renew --task TASK-2`.


When all tasks adding receipts declare optional `code_paths`, changed code
outside their combined files and folders is refused, for example
`src/unrelated.py is outside the code paths of TASK-2`. A contributing task
label (or an approving review) accepts it; `workorder-allow:no-task` also
accepts the code-to-task requirement. Tasks without the field keep the
existing behavior; no task or receipt migration is needed. Files are exact
matches and folders include descendants, not similarly named siblings.
When a task omits `code_paths`, `done` lists the code files touched by the change:
`TASK-2 changed: calc.py, security.py; set code_paths to have changes outside them flagged`.
Test files and non-code paths are excluded from that list.

### GitHub Actions

The workflow runs on pull requests, submitted pull-request reviews and pushes to the default branch. Pull
requests use `--base` and read `workorder-allow:<ID>`, `workorder-allow:tests`,
`workorder-allow:no-task` and `workorder-allow:workflow` labels as data. Pushes run current claims and tests
without `--base`, so reviewer decisions already made on the pull request do not
need to be supplied again. Setup reads local `origin/HEAD` for the push branch,
then existing main/master refs, then defaults to main. This selection does not
change a task's merge-target rules.

```yaml
name: Workorder
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
  pull_request_review:
    types: [submitted]
  push:
    branches: ["main"]
permissions: {contents: read, pull-requests: read, issues: read}
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # receipts refer to earlier commits
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
      - uses: actions/setup-python@v5
        with: {python-version: '3.12'}
      - name: Install project test dependencies
        run: python -m pip install -e '.[test]'
      - run: python -m pip install workorder==0.15.0
      - name: Check tasks after merge
        if: github.event_name == 'push'
        run: workorder check --run-tests --all-tests
      - name: Check tasks and run their tests
        if: github.event_name == 'pull_request' || github.event_name == 'pull_request_review'
        env:
          BASE: origin/${{ github.event.pull_request.base.ref }}
          GH_TOKEN: ${{ github.token }}
          GH_REPO: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        # Optional: append --require-task-ignore GLOB for each code path exception, for example:
        # --require-task-ignore 'docs/**' --require-task-ignore '*.md'
        run: |
          # Reviewed task ids, unowned tests, or the code-to-task requirement.
          gh api --paginate --slurp "repos/$GH_REPO/issues/$PR_NUMBER/events" > "$RUNNER_TEMP/workorder-label-events.json"
          gh api --paginate --slurp "repos/$GH_REPO/pulls/$PR_NUMBER/reviews" > "$RUNNER_TEMP/workorder-reviews.json"
          allowances=$(python -m workorder.review_labels "$GITHUB_EVENT_PATH" "$RUNNER_TEMP/workorder-label-events.json" "$RUNNER_TEMP/workorder-reviews.json" "$BASE")
          # Test changes are refused unless a reviewer other than the author adds the label or approves this head; --review warn is optional.
          workorder check --base "$BASE" --run-tests --all-tests --require-task $allowances
```

The workflow reads every page of issue label events. Only a current allowance
whose latest add event was made by someone other than the pull request author
is passed to `--allow`. Author-added labels print one notice each and are ignored.
Removing a label cancels its prior approval; an author re-adding it does not
restore a reviewer's earlier approval. Missing event history or actor identity
supplies no allowance. Account IDs and case-insensitive logins identify authors.
The API uses the workflow's read-only token; a failed API request stops the check.

The workflow also reads every page of pull-request reviews. An APPROVED review
by someone other than the author must name the current head commit. Old-head,
dismissed and author reviews supply no allowance. A later request for changes
from that reviewer replaces their approval; an ordinary comment leaves it in
place. Account IDs and case-insensitive logins identify the author. An accepted
review passes `--approving-review`. Explicit labels alone supply `--allow`.
The check accepts ordinary findings through the review and reports them, for example:

```text
accepted by an approving review: TASK-3 (new task), tests/test_calc.py (changed lines)
```

Approval does not cover old-test failures on new code, assertion findings,
tests that still pass with changed functions replaced, test-first exceptions,
reopened tasks whose tests fail, or Workorder workflow changes.
The output names these with `approval did not cover:` and gives the required
task label (or `tests` for an unowned file). Failed current tests and unsupported
claims still refuse. The checkout uses the event's head commit, so a review for
an earlier head cannot accept changes on a later head.

Changes to `.github/workflows/workorder.yml` require the explicit reviewer label
`workorder-allow:workflow`. The rule also follows a renamed generated workflow
under `.github/workflows/` by its `python -m workorder.review_labels` command in
either the base or current contents, so removing that command is reviewed too.
An approving review, warning mode and task labels do not accept this finding.
This checks changes when Workorder runs; repository rules must still require
the check to run.

Anyone with triage rights can apply labels. Protect acceptance with CODEOWNERS
and required code owner review on task, test and configuration paths. For example:

```text
# .github/CODEOWNERS — replace @your-org/reviewers with your reviewers
/tasks/ @your-org/reviewers
/.workorder/ @your-org/reviewers
/tests/ @your-org/reviewers
/test/ @your-org/reviewers
/.github/ @your-org/reviewers
/conftest.py @your-org/reviewers
/pytest.ini @your-org/reviewers
/tox.ini @your-org/reviewers
/setup.cfg @your-org/reviewers
/pyproject.toml @your-org/reviewers
```

Add other test and nested configuration paths your project uses. Enable required
code owner review in branch protection; CODEOWNERS alone does not require it.

## Receipts

New receipts use this layout:

```text
.workorder/receipts/TASK-1/20261114T093000.123456Z-a1b2c3d4e5f6.json
```

Each file holds one packed JSON object and ends with LF. Its name is the UTC
time followed by the first 12 hex digits of its hash. Every receipt has
`task`, `kind`, `at`, `who` (`WORKORDER_AGENT` when set, else Git `user.name`), `commit`, `code`, `parents` and
`hash`. The hash is SHA-256 of the packed object without `hash`. Command
receipts add `command`, `exit_code` and `valid`. A test run made by
`done --no-code-change` also has `no_code_change: true` and optional `no_code_change_base` naming its comparison commit; ordinary runs omit them.
`done --refactor` adds optional `refactor: true`; `done --needs-review` adds
optional `pending_review: true`. Readers from 0.13.0 can read these members,
but 0.14 is needed to apply their new review rules. Read-backs add `target` and
`output_sha256`. Recorded merges add `ref`. New implemented receipts add
`implementation_base` and `implementation_paths`: a map from each changed path
to its Git mode, object type and blob ID (or `null` for a deletion). The recorded
path entries are checked against the original commit when it is available.

`parents` lists the hashes of all current heads for that task: receipts no
other receipt follows. The first receipt has no parents. Branches can add
files independently; the next receipt after merging follows both heads.
Writers for one task in one clone take the lock in `.git/workorder-locks/`.

Old `.workorder/receipts/<ID>.jsonl` and `.notquite/receipts/<ID>.jsonl` files
are still read and verified as `previous` chains. Leave them in place. If
both folders contain a chain for one task, one must be a prefix of the other.
New receipts never append to either file. The first new receipt follows the
last legacy hash. `entries()` returns the legacy chain first, then new
receipts with parents before children. Among available receipts it orders
by `at`, then hash. The last entry in that order is the newest. Each returned
object also has a read-only `missing` flag for an absent commit.

The checker refuses:

- Content whose hash does not match, or a filename that disagrees with the
  hash or UTC time.
- A missing parent: "a receipt this one follows is missing: removed or not
  yet merged". Invalid parents and cycles are refused too.
- A receipt whose code fingerprint disagrees with its commit.
- A committed receipt file that changed or disappeared. New files are
  allowed. A legacy ledger must retain its committed text as a prefix.
- A receipt ledger without its task file.

These rules apply to the working tree against HEAD, the index under
`check --staged`, and the base revision under `check --base REF`.
Line-ending conversion from LF to CRLF is allowed. A deletion already
committed with hooks skipped cannot be found by comparing HEAD with itself;
use `--base` to check both the earlier revision and intervening commits for
receipt-file deletions or modifications. The complete removal of
a finished or cancelled task has the explicit exception below.

### Repair

Run `workorder repair --task ID` on a legacy ledger with Git conflict markers
from two branches appending receipts. It accepts one complete append conflict,
including diff3 markers with an empty base part. It verifies the common prefix
and each side's full chain before writing anything. Ambiguous markers, changed
hashes, broken chains and lines after the conflict are refused.

Repair keeps the common prefix in the JSONL file. It converts each side's
extra receipts into files, preserving each side's order. The first on each
side follows the prefix's last hash, or has no parents for an empty prefix.
Hashes change because `parents` replaces `previous`. Contract references to
the original test hashes still count. Each converted receipt
also includes its original object in `legacy`. This lets HEAD and `--base`
verify an exact move of a committed legacy tail in the current snapshot.
The separate `--base` history check still refuses committed modifications
to the JSONL file anywhere in `REF..HEAD`, including appends or repairs;
new receipt files avoid modifying that ledger. This verified conversion is
the only exception to the legacy prefix rule; dropping or changing a receipt
is still refused. Repair prints the counts. Stage the prefix and new files
to finish resolving the conflict.

Anyone who can write files and compute SHA-256 can create a receipt that
passes these checks.

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
  the implemented code. It counts only when `requires` is `REAL_OR_TEST`.
- `MOCK`, `SYNTHETIC` and `UNKNOWN` never count.

Only critical criteria block DONE. `REAL` is a label the author declares: a file
and its hash identify the file, not where it came from. A `MOCK` result cannot
satisfy a request for real output. Keep contracts and evidence under
`.workorder/`, for example `.workorder/evidence/`, so adding evidence does not
change the implemented code fingerprint. See [examples](../examples/) for a
sample contract and tasks.

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

`claim` and `ready` also fetch origin's main branch (master if main is absent)
into the private `refs/workorder/upstream` ref in the same fetch as missing
claim tags. They refresh it even when all tags are already cached, without
changing the checkout or remote-tracking branches. `claim` first compares task text and receipts with the local copy and their common base. Upstream work absent locally refuses the claim, for example: `origin/main has newer work on TASK-1 (2 receipts, 1 note, by agent-b); pull first`. Local additions alone do not trigger it. A task already DONE, CLOSED or CANCELLED upstream is still refused even after pulling.
The name is the author of the latest commit touching the task or its receipts. `show` refreshes the same private ref with a three-second deadline and warns at the top: `origin/main has newer work on this task: pull before you start`. Offline it prints a short local-view note; it never reuses a stale upstream ref.
`ready` lists an outdated open task as
`finished on origin/main; pull to update`, even after its claim was released,
only when its local status differs from the upstream status.
When origin is unreachable, `ready` uses local records and its existing
offline note; it never reuses an old upstream snapshot. `claim` still refuses
when origin cannot be reached.
`release` normally needs the token in the clone that claimed the task. If that
clone or worktree is gone, `release --task ID --force` records a forced release.
A configured but unreachable origin makes `new` ask for `--local`.
Local numbering uses both task filenames and reservations already present under
`refs/tags/workorder/ids/`, including tags fetched earlier. It chooses the next
number for the requested prefix without contacting origin or creating a new tag.
It cannot account for reservations this clone has never fetched.
Ids, claims and releases appear in tag lists and `git fetch --tags` downloads
them. Filter out `workorder/*` when selecting a release tag. Tokens coordinate
clones; anyone with push access can write a release tag.
Force-pushes and tag deletion by anyone with push access bypass all of this;
protect `workorder/*` tags if it matters.

## Compatibility

Version 0.15 keeps the 0.14 task syntax, receipt hashes and matching rules.
Existing files need no migration or rewriting. The optional verification base
on new test receipts does not change the required fields. Older readers ignore
it; 0.15 checks whether the exemption actually has no code changes.
The optional `code_paths` field is understood from 0.13 onward; tasks that omit
it remain readable by 0.12.0.

- Workorder was called NotQuite up to 0.2.0 (PyPI `notquite`). From 0.3.0 the
  command, the package and the data paths are `workorder`: receipts in
  `.workorder/`, ids and claims under `refs/tags/workorder/`, claim tokens in
  `.git/workorder-claims/`. Earlier releases used `git mv .notquite .workorder`
  to move receipts. Version 0.4 also reads `.notquite/receipts/` in place;
  leave committed ledgers there. `.notquite/` stays outside the code fingerprint. Ids and
  claims made under `refs/tags/notquite/` are not read; task numbering
  continues from the task files.
- Task files in the seven-field format read exactly as in 0.1.0, and
  `workorder new --template simple` writes the same bytes as 0.1.0. From 0.3.0,
  plain `workorder new` writes the extended format.
- 0.1.0 refuses any task file that uses an extended field or the YAML forms
  above. Once one task uses them, every clone, hook and CI pin needs 0.2.0 or
  later.
- Version 0.4 writes separate receipt files and still reads legacy JSONL
  chains. Upgrade every writer and checker before using the new layout.
  Task files are left out of the code fingerprint, so
  switching a task from the simple to the extended format, in either YAML or
  JSON style, keeps its receipts valid.
- 0.3.0 requires a declared `test` for DONE and CLOSED. Tasks from earlier
  versions keep their receipts; add `test: "<command>"`, or `test: any` to keep
  the old behaviour, before claiming DONE.
- 0.3.0 reads the same receipts with three differences. A test run counts when
  its code matches the implemented code, on any commit, so a DONE that was
  hiding a later failing run on the same code becomes IN_PROGRESS. A receipt
  that is recorded but cannot count exits 1. New receipts and claims record `WORKORDER_AGENT` when set, otherwise `user.name`. Set `export WORKORDER_AGENT=agent-b` to distinguish agents sharing one Git identity.

Receipts and claims record the agent name, and that name is pushed. Set
`export WORKORDER_AGENT=agent-b` to distinguish agents sharing one Git identity.
Receipts from 0.2.0 or earlier also contain the computer's hostname and keep it.
Rewriting Git history changes the available record. Chat, issue and pull request
text are not checked. Tasks remain in `tasks/` until explicitly removed with
reviewer acceptance as described above.

## Line endings

New task files, receipts and claim tokens use LF. Updates to status, next and Progress preserve the task file's existing line endings and byte order mark using byte writes. Files read
back with CRLF, as Git on Windows may check them out, are treated the same.

## Reading show and check results

In both `show` and `check ID`, unclaimed tasks start with `TASK-1 IN_PROGRESS (not claimed finished); its test evidence is in place`, or `owes: ...`. Their evidence is not labelled DONE or CLOSED. Supported finished tasks print ``Evidence: recorded receipts support the claim (not re-run here; `check --run-tests` re-runs it)`` followed by the computed status.
Other claimed tasks retain their Claimed and Evidence statuses. If HEAD differs
from the implementation, a successful Result says the declared test passed on
implemented code, naming that commit; it does not say it passed on current code. A timeline test that does not count includes `(does not count: <reason>)`, even if the command exited 0. For implementation, tests or Acceptance work, `show` suggests `workorder done --task ID`. With `test: any`, that advice asks for a concrete command first; individual receipts remain available for tasks keeping `any`.

Before a merge, `merge of the tested code` is owed. Advice about differing merged code is used only if the target has commits since the merge base touching implementation paths. A stale claim does not change the recorded evidence's computed state; its Result names the mismatch, for example: `Result: the DONE claim is on code other than the tested code; run workorder renew --task TASK-1`.

## Task-first development

Write the task before its test. In a clone that has an origin remote and the
pre-commit hook from `workorder init`:

1. Fill in the task, including a concrete test command, Scope and Acceptance,
   while the test file does not exist yet. Commit that task and push it to the
   default branch. Do not skip the hook.
2. On a feature branch, add the failing test and a handover note
   (`workorder note --task ID --next "<next step>" "<what was done>"`). Commit
   them. A failing or missing test for unfinished work is a note, so the hook
   allows the commit. New test files are additions.
3. Implement the change and commit it.
4. Run `workorder done --task ID`. It runs the declared test and sets DONE only
   when the record supports the claim. Commit `tasks/` and `.workorder/`.
5. Push the branch. The check the generated workflow runs on a pull request is
   `workorder check --base origin/<base> --run-tests --all-tests --require-task`.
   It passes when the task already existed on the base and the evidence supports
   DONE.
6. Squash-merge into the default branch and commit, still without skipping the
   hook. On that branch, `workorder check --run-tests --all-tests` passes when
   the merged code matches the tested code.

Making a failing base test pass by adding `return`, or by adding a second
function with the same name, changes a test that already failed. That change
is refused unless a reviewer adds the label (`--allow ID`) or approves the current head; `--review warn` is optional. This also applies to a finished task whose base tests fail on the new code.

A conflicted squash can change the tested lines. The check on the default
branch can then fail. Run `workorder renew --task ID` there and test again.

## Team setup

Run `workorder init` in every clone: hooks are not cloned. It is idempotent and
reports the hook it installed when instructions are already committed. Its
read-only `--check` still advises running init if the hook is absent.
After writing setup, `init` lists the files it wrote and ends with an exact
`git add ... && git commit -m 'Set up Workorder'` command for those repository
files. With `--claude`, newly written `.claude/settings.json` is included. The
local `.git/hooks/pre-commit` is listed but never included in `git add`.
An unchanged setup reports `No files written.` without a commit command.
`--check` writes nothing and prints no commit instruction.
`init --light` writes a trial workflow with `--review warn` and without
`--require-task`; the default remains refusal plus a task requirement. The trial
workflow comment explains how to restore defaults: `workorder init --update-workflow`.
Use `--light --update-workflow` to switch an existing workflow to the trial;
adding `--check` previews either change without writing.
Existing workflows are left untouched unless `init --update-workflow` is used.
That option prints a unified diff and writes the new workflow; adding --check
shows the diff without writing and exits 1 if an update is needed.
A generated workflow installs project
test dependencies first, choosing and reporting the first match:

1. A `test` extra in pyproject.toml: `python -m pip install -e '.[test]'`.
2. requirements-dev.txt, otherwise requirements.txt: `python -m pip install -r FILE`.
3. A task command that uses pytest: `python -m pip install pytest`.
4. Otherwise a warning step says `No project test dependencies detected` and
   installs no extra test tools. Add the dependencies your tests need. The check
   reports `pytest is not installed` when a command needs it but it is absent.
   Pytest is installed automatically only for a detected pytest task.

It then installs Workorder at exactly the running version. For a Git installation,
PEP 610 direct_url.json supplies the URL and resolved commit:
`python -m pip install 'git+<url>@<commit>'`. A recorded subdirectory is retained.
Other installations use `workorder==0.15.0` for this release.
It runs `workorder check --base "origin/<base>" --run-tests --all-tests --require-task`,
passing task ids and the special values `tests`, `no-task` and `workflow` from
`workorder-allow:` labels added by someone other than the author to `--allow`.
Current-head approving reviews supply `--approving-review` separately. Pushes to the default branch run
`workorder check --run-tests --all-tests` without a base. See the runtime
trade-off above. Install additional non-Python dependencies in the workflow as
needed. The agent block says to work on a branch, open a pull request, and after
the merge run `workorder check <ID>` on main. Start with `git fetch origin`;
use `git pull --rebase origin main` only on main. On a feature branch, merge or
rebase main as needed, then renew if the tested code changed. It also says: "If `done` refuses
or prints a warning, report it; do not say the task is done."

The installed pre-commit hook is:

```sh
#!/bin/sh
previous="${0%/*}/pre-commit.workorder-previous"
if [ -f "$previous" ]; then
    "$previous" "$@" || exit $?
fi
if command -v workorder >/dev/null 2>&1; then
    exec workorder check --staged
fi
echo "pre-commit: warning: workorder is not on PATH (pipx install workorder); skipping task check" >&2
exit 0
```

To install it manually, save the block as `.git/hooks/pre-commit` and run
`chmod +x .git/hooks/pre-commit`.

The pre-commit hook prints one warning when Workorder is missing from PATH and
exits 0, so teammates without it can still commit. When Workorder becomes available it runs `check --staged`; empty staged
checks print nothing. When origin/main (or origin/master) is available locally,
it reviews tests for tasks this commit touches (and their dependents), compares their staged tests with the merge base, reruns them and prints
test-change notes with reviewer-label advice. It also runs affected finished tasks
whose recorded implementation paths or selected tests differ from the local merge base in staged code. Older
receipts without path information conservatively use their recorded code tree.
`done` uses this same affected-task scope. Unsupported DONE claims still
refuse with exit 1. Successful test output stays quiet; failures print full
output. The summary counts tasks whose tests ran and marks fully cached comparisons `(cached)`.
Temporary copies and their commands discard inherited Git repository variables,
so Git keeps the commit index intact, including in linked worktrees. It never fetches. The README
includes the installed agent block and examples.

## Agent hooks

`workorder init --claude` merges `.claude/settings.json` without discarding other
settings or hooks. It installs command hooks for SessionStart and Stop:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "workorder hook session-start"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "workorder hook stop"}]}]
  }
}
```

SessionStart lists tasks held by this clone and their `show` records, plus one
line pointing to `workorder ready`. It does not run the full readiness scan.
When a remote is configured, it re-reads claim and release tags from origin,
then reads the local tags and private claim tokens. If that read fails, it
keeps the claims already in this clone. A token left by a failed claim is not
ownership. Stop reads the hook JSON from stdin. When `stop_hook_active`
is true it exits 0 to prevent a stop loop. Otherwise it checks DONE/CLOSED claims only for tasks held by this clone or
whose task file differs from the merge base with the selected target branch. With no target, it compares task files with HEAD. Other tasks never block Stop. It exits 2
with the NOT DONE refusal lines on stderr if one of those claims lacks support.
With a locally available remote target, it runs the same test-change comparison
for those completion claims. A held task newly set to CANCELLED also refuses,
including a cancellation committed on this branch. Test-file changes are notes;
command repairs for tasks already finished at the base are notes if the new command passes; other command changes refuse;
cancellation refusals include reviewer-label advice. A valid claim or unfinished work without such definition changes exits 0. This checks task records;
it cannot detect a completion claim made only in chat.

`init --claude --check` reports missing hook setup without writing it. Codex users
keep the AGENTS.md block; it uses `workorder note --task ID --next "TEXT" "NOTE"`
to leave the next action and progress together.
