# Reference

Details behind the [README](../README.md). Everything here is read from the
code in `workorder/`; if the two disagree, the code wins and this file has a bug.

Install: `pipx install workorder`.

## Commands

| Command | What it does |
|---|---|
| `workorder init [--check] [--force] [--claude] [--update-workflow]` | Adds the README agent block once to AGENTS.md and CLAUDE.md, installs the hook, and writes the workflow if absent. --update-workflow rewrites it with a diff; combined with --check it only previews changes. With --force a different hook is saved as pre-commit.workorder-previous and called first; --check reports missing setup without writes and exits 1 if incomplete. --claude merges project hooks into .claude/settings.json |
| `workorder done --task ID [--note TEXT] [--timeout SECONDS] [--needs-review] [--no-code-change]` | Refuses uncommitted code or empty Scope/Acceptance; records implementation if its code changed and runs the declared test. Prints Acceptance for human confirmation, then sets DONE only if supported. On refusal prints Missing lines and preserves status; never commits or pushes |
| `workorder new --title T --owner O [--ships none\|repo\|live] [--prefix TASK] [--template extended\|simple] [--test COMMAND] [--local]` | Reserves the next id on `origin`, or numbers it locally when there is no `origin` remote or with `--local` (no tag is pushed); writes `tasks/<id>.md` (extended by default; `simple` writes the seven required fields only). `ships` defaults to `none` |
| `workorder receipt implemented --task ID` | Records the current commit as the implementation; refuses uncommitted or untracked code |
| `workorder receipt test --task ID [--timeout SECONDS] [-- CMD...]` | Runs `CMD` freshly (no shell), records its exit code against the current commit |
| `workorder receipt merged --task ID [--ref refs/heads/main]` | Fetches the branch from `origin` and records the commit it points to (needs `origin`) |
| `workorder receipt deployed --task ID --target NAME -- CMD...` | Runs a read-back command; valid only if it exits 0 and its output contains the full commit ID |
| `workorder receipt live --task ID --target NAME -- CMD...` | Same, recorded after the deployment |
| `workorder check [ID or tasks/ID.md]` / `check --task ID` | No argument: reads every task; one line per problem, or `check: OK`. With a task: an explanation of that task alone |
| `workorder check --staged` | For the pre-commit hook: checks the staged versions of the tasks this commit touches (task file, receipts, contract or its evidence) and the tasks that depend on them; refuses a commit that newly claims DONE or CLOSED on code other than the implemented code; notes changes to a task's requirements |
| `workorder check --base REF [--verbose] [--allow IDS] [--run-tests] [--strict-tests] [--all-tests] [--require-task] [--timeout SECONDS]` | Checks changed task records and receipts, all repository test roots, every task whose tests changed, and dependents. Refuses stale claims, changed receipts, changed requirements, weakened tests, new finished definitions, and removed tasks. `--allow` accepts reviewed definition/test changes, REF failures and outcome changes, and complete removal of finished or cancelled tasks. `--run-tests` reruns the declared tests of supported claims; see [base checks](#checks-against-a-base) |
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
why). Every command resolves the repository root, so commands also work from subdirectories. `--all-tests` requires `--run-tests`; `--strict-tests` and `--require-task` require `--base`. Without a base, `--run-tests --all-tests` checks current completion evidence and reruns finished tasks on current code.

`done` also requires the implemented code to differ from the target merge base.
Without a target, it compares with the commit that created the task (HEAD for an
uncommitted task). Otherwise it refuses: `TASK-1: no code changed since <ref>;
nothing was implemented`. Use `--no-code-change` for verification tasks.

When tests changed on purpose, rerun `done --task ID --needs-review`. Passing,
supported evidence can then write DONE and print `TASK-6 DONE, pending review:
the pull request needs the label workorder-allow:TASK-6 (its test files changed)`.
The flag does not accept failed current tests, missing evidence or dependencies.
The pull request check still needs that label. Pre-commit and Stop report test
changes as notes and continue to refuse unsupported completion claims.

## Task files

Commit `tasks/` and `.workorder/` with the work. Checks normally read the working
tree; `--staged` reads the index, and a workflow reads its committed checkout.

Only files named `tasks/<id>.md` are task records; other files in `tasks/`
(a README or Markdown notes) are ignored. Other files there are refused because `tasks/` is outside the code fingerprint. A task file starts with front
matter between two `---` lines, one
`key: value` per line, each key once. These seven fields are required: `id`,
`title`, `owner`, `status`, `ships`, `waits_on`, `contract`.

A value is plain single-line text, a JSON string, or a list. `note` appends a line under Progress. `done` changes the status line to DONE after checking the claim, and `done --note` also appends a dated line. Other task text is preserved. `note --next "TEXT"` replaces or adds the next field, including existing folded/literal values; an empty string clears it. With no NOTE it adds no Progress entry. `done --note` keeps the handover line on every refusal, including uncommitted code or a missing declared test. Refusal says `TASK-1: DONE was not written; the status stays IN_PROGRESS`, using the actual status, then the Missing lines. `done` refuses a missing or empty Scope or Acceptance section, including sections containing only HTML template comments: `fill in Scope and Acceptance first`. It checks this before recording evidence. It preserves the status line's plain, single-quoted or double-quoted style, and its existing line endings. Success says `TASK-1 DONE: its declared test passed on this code`, followed by `; still owes: merge of the tested code` when applicable (and any other delivery debt). A separate line asks you to commit tasks/ and .workorder/, adding `and push` only with an origin remote. It warns when this clone has no pre-commit hook. If origin/main (or origin/master) exists locally, it runs the test-change checks below against the merge base of HEAD and that ref before writing DONE and refuses with exit 1 when they find a problem, preserving the prior status. It adds `a reviewer can accept it in the pull request with the workorder-allow:<ID> label` to the same diagnostic. It does not fetch. A passing run that does not count reports whether files or HEAD changed while it ran; generated files such as __pycache__/ should be added to .gitignore before committing and running done again. `done` needs a concrete declared command; use the individual receipts for `test: any`. A task
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
- Indented lists for `depends_on`, `blocks`, `waits_on` and `test_paths` only:

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
  Changing `test_paths` itself needs `--allow ID` under `--base`.
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
receipts, such as the squash commit. Otherwise it does not count, and
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
Without the original history, the available landing tree is protected.

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
Workorder can still recognise exactly matching code. It does not reconstruct
a missing patch from the receipt. Fetch that history or renew the evidence.
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
untouched. Repository test files include every file under a folder whose name
contains `test` or `fixture`, such as `testsupport/`, `fixtures/`, `testdata/`
and `test_data/` (including JSON, YAML, CSV and text data), and files named `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`
or `*.spec.*`, plus `conftest.py` and pytest configuration. A test file at the
repository root does not make the root a test folder, so production code is
not a test file. Importing a package from a test does not make that package a
test file. Both REF and current definitions count. Metadata paths under
`tasks/`, `.workorder/` and `.notquite/` stay outside test discovery.

| Change or result | Default | Reviewer acceptance |
|---|---|---|
| Only added test lines or new test files, when the base tests passed | Passes | `--strict-tests` requires acceptance for additions too |
| Any change to a test whose base tests already failed, including an added line | Needs review | `--allow ID`; `--allow tests` does not accept this |
| Removed or changed test lines, including a rename | Needs review | `--allow ID` for that task; `--allow tests` accepts every changed test line |
| REF tests fail on current code, including a finished task whose base tests already failed | Needs review | `--allow ID` accepts an intended behavior change or test rename |
| Fewer pytest passes, or more skips, xfails or deselections | Needs review | `--allow ID` accepts changed outcomes |
| New skip/xfail markers or calls, or conftest or pytest configuration changes | Needs review, including additions | `--allow ID`, or `--allow tests` for those lines |
| Pytest `-k` or `--deselect` changes | Needs review | `--allow ID` only |
| Current declared tests fail for a DONE/CLOSED task | Refused | No label excuses the failure |
| Current tests fail or are missing for IN_PROGRESS/BLOCKED work | A note; no finished claim is made | Test edits and REF regressions still need review |

With `--run-tests`, tasks this change works on and all tasks whose selected tests
changed run their current commands and their REF tests on current code. A task
is being worked on when its task file or receipts differ from REF. Its REF
comparison still runs when its tests are unchanged. Each comparison uses a
temporary copy: REF test files and command are applied over current code, added
test files are absent, and deleted files are restored. The checkout is never
overlaid. If the task or its selected tests did not exist at REF, a note explains
why no REF run is possible. Unowned test edits receive file review without an
invented task command.

When REF tests fail on current code, they also run on REF code before this change
is blamed. An existing REF-code failure is a note. A failure or timeout on current
code after a passing REF-code run needs acceptance. Commands that cannot start,
copy failures, unavailable reference files and unreadable pytest outcomes get
explicit diagnostics; acceptance cannot supply missing comparison results.
External command or dependency failures name the environment problem. A missing
module belonging to the repository is checked against REF: if the REF tests
pass there, a module rename or removal is a code change subject to review.

For example, an intentional change from 10% to 20% requires acceptance of the
edited assertions and failing REF assertions for each owning task:

```text
TASK-1: its tests from origin/main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
```

A task owns the files its test command names, the folders it names, and
`test_paths`. A folder includes every file below it. It also owns `conftest.py`
and pytest configuration on the path to those files. A named file does not
include its siblings, and a command that names no path does not own every
folder called `tests`. `workorder new --test` writes `test_paths` when the
command names files or folders. Helpers and data count only when they are
inside a named folder or named themselves. A test file at the repository root
does not pull in the rest of the repository, and importing a package does not
make that package a test file. A missing named test never widens to unrelated
tests. An explicit empty `test_paths` selects no task test files, but cannot
hide repository tests from file review. `--allow tests` accepts every changed
test line in the change. It does not accept a pytest `-k` or `--deselect`
change, a base test that fails on the new code, or any change to a test that
already failed at the base.

A finished task whose declared test maps to no test files at the base, when
the repository has test files there, is refused:
`TASK-1: no test files are known for its test; set test_paths`.

Commands through make, npm, npx, yarn, pnpm, go, cargo, tox, nox, sh or bash, and
non-test scripts, require explicit `test_paths` under `--base`. The
wrapper itself is also protected. Use those paths for dynamic imports and tests
hidden behind a wrapper. Declaring `test_paths` is part of the task definition;
changing it needs `--allow ID`.

Pytest configuration covers conftest.py, pytest.ini, .pytest.ini, pytest.toml,
.pytest.toml, tox.ini, setup.cfg and pytest sections of pyproject.toml, plus custom
configurations selected by a command. Unrelated TOML sections need no review.
Without `--run-tests`, file, configuration and selection checks still apply and
a note says the REF comparison has not run. A passing command cannot establish
that its assertions express the right requirement.

Moving DONE/CLOSED back to IN_PROGRESS or BLOCKED usually needs `--allow ID`.
A new counting failed test receipt and the reopening in the same commit are
accepted as evidence that the task needs more work. Set the task to IN_PROGRESS
before committing that failure; retaining DONE is an unsupported claim. Moving
any other status to CANCELLED needs reviewer acceptance for closing work without
finishing it. `--staged` reads the staged status and receipts.

Scope or Acceptance removals and rewording need `--allow ID`. Additions need it
too when the task was DONE or CLOSED at REF; filling in an open task does not.
Changes to `test`, its contract or evidence, and removed dependencies need review.
A task absent at REF but claimed DONE or CLOSED in this change needs acceptance
of its definition:

```text
TASK-4 was created and claimed DONE in the same change; a reviewer accepts its definition with --allow TASK-4
```

Removing a task is refused by default, including a task with no receipts. With
`--allow ID`, a task that was DONE, CLOSED or CANCELLED at REF may be removed with
all its receipts in both receipt folders. Partial removal, removal of unfinished
tasks and edits to retained receipts still refuse. Dependencies must remain valid.
Every commit in `REF..HEAD` is checked for receipt deletions or modifications,
including newly added receipts later deleted and edits restored before HEAD.
The complete task-removal exception also exempts that task's receipt history.

`--allow` takes comma-separated task ids and the special values `tests` and
`no-task`. Task ids accept the reviewed changes described above. `tests` accepts
unowned test edits only. `no-task` bypasses only `--require-task`; it does not
accept test edits, changed receipts, unsupported claims or missing dependencies.
No allowance excuses a failing current test for a finished task.

### Current runs, cache and summaries

`--all-tests --run-tests` runs every DONE/CLOSED task's concrete declared command
on current code, once per distinct command and effective timeout. It works with
or without `--base`. A regression refuses with `regression: TASK-1's test fails
on this code`; the recorded evidence is unchanged. An unfinished task's failed
or missing current command is a progress note. `test: any` has no concrete
command to rerun. These checks write no receipts.

Local commands reuse test results for identical code, command and effective
timeout. `done`, `check --run-tests`, pre-commit and Stop share these local results.
Plain `check` reads evidence without requesting a test run. Explicit
`receipt test` and `renew` run freshly and update the cache. Code, command or
timeout changes cause another run. CI runs bypass the persistent local cache;
identical commands still share results within one check. REF-on-current and
REF-on-REF runs use their own code snapshots. Successful comparison output stays
quiet; failures and other diagnostics show the relevant output. The cache is
local to `.git/workorder-tests/` and can be removed. Changes to dependencies,
services or environment variables do not change these keys; request a fresh
receipt test or renewal when those inputs change.

Notes of the same kind are grouped, for example `Note: 200 tasks' tests passed
when this check ran them`. Use `--verbose` for each task note. Every mode prints
a summary, except `--staged` with nothing in scope:

```text
checked 3 tasks (2 claimed DONE or CLOSED); ran 2 declared tests
```

The run count appears only with `--run-tests` and counts declared commands this
check used, including failures, REF comparisons and cached results. When every
counted run was cached the summary says `(cached)`; when some were, it says
`(N cached)`. A missing executable does not count. If none ran, another line says
`no declared test ran in this check`. Task counts include unreadable and removed
tasks; unreadable claims are excluded from the claim count. An unfinished
single-task summary says `checked 1 task (not claimed finished)`.
Supported finished claims without a contract also get the Acceptance reminder;
unclaimed and refused tasks do not contribute to its count. Checks exit 0 for
acceptance and 1 for refusal.

Pytest runs reporting only skips get `its test ran, but reported only skipped
tests`, without a passing-test note. With `--strict-tests --run-tests`, no passes
refuse a finished claim, for example `its test ran but no test passed (2 skipped)`.

Running every distinct command costs more time as completed tasks accumulate.
Tasks being worked on and tasks whose tests changed also need REF comparisons.
New workflows enable `--all-tests`; projects can deliberately choose narrower
checks.

### Code changes need task evidence

`check --base REF --require-task` requires a new counting implementation or test
receipt for at least one task with a concrete declared command. That task must
exist at the merge base, or a reviewer must accept the new task with `--allow ID`. A test that
does not count or a `test: any` task does not meet the rule.
Progress notes, status edits, new task definitions, merge receipts and deployment
receipts alone do not meet it either. A reviewer can accept a code change without
that task evidence with `--allow no-task`.

The rule excludes `tasks/`, `.workorder/`, `.notquite/`, `docs/`, `.github/`,
`*.md`, `*.rst`, `*.txt` and `LICENSE*`. This exemption does not change the code
fingerprint used by receipts. Generated pull request checks enable the rule.

### GitHub Actions

The workflow runs on pull requests and pushes to the default branch. Pull
requests use `--base` and read `workorder-allow:<ID>`, `workorder-allow:tests` and
`workorder-allow:no-task` labels as data. Pushes run current claims and tests
without `--base`, so reviewer decisions already made on the pull request do not
need to be supplied again. Setup reads local `origin/HEAD` for the push branch,
then existing main/master refs, then defaults to main. This selection does not
change a task's merge-target rules.

```yaml
name: Workorder
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
  push:
    branches: ["main"]
permissions: {contents: read}
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}   # receipts refer to earlier commits
      - uses: actions/setup-python@v5
        with: {python-version: '3.12'}
      - name: Install project test dependencies
        run: python -m pip install -e '.[test]'
      - run: python -m pip install workorder==0.10.0
      - name: Check tasks after merge
        if: github.event_name == 'push'
        run: workorder check --run-tests --all-tests
      - name: Check tasks and run their tests
        if: github.event_name == 'pull_request'
        env:
          BASE: origin/${{ github.base_ref }}
        run: |
          # Reviewed task ids, unowned tests, or the code-to-task requirement.
          allow=$(jq -r '[.pull_request.labels[].name | select(test("^workorder-allow:([A-Z][A-Z0-9]*-[1-9][0-9]*|tests|no-task)$")) | sub("^workorder-allow:"; "")] | join(",")' "$GITHUB_EVENT_PATH")
          workorder check --base "$BASE" --run-tests --all-tests --require-task ${allow:+--allow "$allow"}
```

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
receipts add `command`, `exit_code` and `valid`. Read-backs add `target` and
`output_sha256`. Recorded merges add `ref`.

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
Ids, claims and releases appear in tag lists and `git fetch --tags` downloads
them. Filter out `workorder/*` when selecting a release tag. Tokens coordinate
clones; anyone with push access can write a release tag.
Force-pushes and tag deletion by anyone with push access bypass all of this;
protect `workorder/*` tags if it matters.

## Compatibility

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

In both `show` and `check ID`, unclaimed tasks start with `TASK-1 IN_PROGRESS (not claimed finished); its test evidence is in place`, or `owes: ...`. Their evidence is not labelled DONE or CLOSED. Supported finished tasks with `ships: none` print `Evidence: supports the claim (nothing to ship)`.
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
needs `--allow ID`. A finished task must also make the base tests pass on the
new code unless `--allow ID`.

A conflicted squash can change the tested lines. The check on the default
branch can then fail. Run `workorder renew --task ID` there and test again.

## Team setup

Run `workorder init` in every clone: hooks are not cloned. It is idempotent and
prints a reminder when instructions are committed but the hook is absent.
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
Other installations use `workorder==0.10.0` for this release.
It runs `workorder check --base "origin/<base>" --run-tests --all-tests --require-task`,
passing task ids and the special values `tests` and `no-task` from
`workorder-allow:` labels to `--allow`. Pushes to the default branch run
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
it compares the staged tests with the merge base, reruns them and prints
test-change notes with reviewer-label advice. Unsupported DONE claims still
refuse with exit 1. Successful test output stays quiet; failures print full
output. The summary counts cached results as run and marks them `(cached)`.
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
with the NOT QUITE refusal lines on stderr if one of those claims lacks support.
With a locally available remote target, it runs the same test-change comparison
for those completion claims. A held task newly set to CANCELLED also refuses,
including a cancellation committed on this branch. Test changes are notes;
cancellation refusals include reviewer-label advice. A valid claim or other unfinished work exits 0. This checks task records;
it cannot detect a completion claim made only in chat.

`init --claude --check` reports missing hook setup without writing it. Codex users
keep the AGENTS.md block; it uses `workorder note --task ID --next "TEXT" "NOTE"`
to leave the next action and progress together.
