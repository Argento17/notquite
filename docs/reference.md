# Reference

Details behind the [README](../README.md), read from the code in `workorder/`; if
the two disagree, the code wins and this file has a bug. For a refusal's meaning
and what clears it, use the [reviewer guide](reviewer-guide.md).

Install: `pipx install workorder`. To run the README quick start on Windows, use Git Bash and `python` for `python3`.

**The review rule.** An approving review clears every finding except changes to
existing tests (edited, removed, skipped, weakened, or existing tests that now
fail, including project tests) and changes to the Workorder workflow file. Those
need a label: `workorder-allow:ID` or `workorder-allow:tests`, and
`workorder-allow:workflow`. No review or label excuses a current test failure or
missing evidence. Locally, `--allow` stands for labels and `--approving-review`
for an approving review.

Contents: [Commands](#commands) · [Task files](#task-files) ·
[How the status is computed](#how-the-status-is-computed) ·
[Computed merges](#computed-merges) · [Checks against a base](#checks-against-a-base) ·
[Receipts](#receipts) · [Contracts](#contracts) · [Claims and ids](#claims-and-ids) ·
[Compatibility](#compatibility) · [Line endings](#line-endings) ·
[Reading show and check results](#reading-show-and-check-results) ·
[Task-first development](#task-first-development) · [Team setup](#team-setup) ·
[Agent hooks](#agent-hooks)

## Commands

| Command | What it does |
|---|---|
| `workorder init [--check] [--force] [--claude] [--update-workflow] [--light] [--test-first] [--solo] [--project-test CMD \| --no-project-test]` | Adds the README agent block once to AGENTS.md and CLAUDE.md, installs the pre-commit hook, and writes the workflow if absent. A first init that finds pytest sets the project test `python -m pytest -q` ([project test](#current-runs-cache-and-summaries)); `--project-test` stores another command in `.workorder/config.json`, `--no-project-test` turns it off. `--update-workflow` rewrites the workflow and shows a diff (only a preview with `--check`). `--check` reports missing setup without writing and exits 1 if incomplete. `--force` keeps a different hook as `pre-commit.workorder-previous` and calls it first. `--claude` merges hooks into `.claude/settings.json`. `--solo`: [solo mode](#solo-mode) |
| `workorder new --title T --owner O [--ships none\|repo\|live] [--prefix TASK] [--template extended\|simple] [--test COMMAND] [--test-paths PATH ...] [--code-paths PATH ...] [--acceptance TEXT ...] [--kind feature\|fix\|refactor\|verification] [--local] [--test-first]` | Reserves the next id on `origin`, or numbers it locally with no `origin` or with `--local` (no tag pushed), and writes `tasks/<id>.md` (extended by default; `simple` writes the seven required fields). `ships` defaults to `none`. `--acceptance` (repeatable) writes Acceptance items; `--kind` writes `kind` (none by default) |
| `workorder done --task ID [--note TEXT] [--timeout SECONDS] [--jobs N] [--needs-review] [--no-code-change] [--refactor] [--require-test-first] [--take-over] [--verbose]` | Runs the declared test and writes DONE, receipts and the note together when the evidence supports it; a refusal changes nothing. Prints the Acceptance list for confirmation. Never commits or pushes |
| `workorder show --task ID [--verbose]` | The task as a record: claimed and evidence status, what is owed, dependencies, kind, mission, summary, next, body sections, a dated receipt timeline, any computed merge and Progress. Prints a next step for owed evidence or unclaimed Acceptance work |
| `workorder note --task ID [--next TEXT] [NOTE]` | Sets `next`, adds `- YYYY-MM-DD: NOTE` to Progress, or both |
| `workorder renew --task ID [--refactor \| --no-refactor] [--timeout SECONDS] [-- CMD...]` | Records `implemented` and the task's test again on the current commit: one step after a squash merge or rebase. The merge itself is computed, never recorded |
| `workorder retire ID --reason TEXT` | Marks a DONE or CLOSED task RETIRED and adds the reason to Progress; see [retiring](#task-files) |
| `workorder receipt implemented --task ID` | Records the current commit as the implementation; refuses uncommitted or untracked code |
| `workorder receipt test --task ID [--timeout SECONDS] [-- CMD...]` | Runs `CMD` freshly (no shell) and records its exit code against the current commit |
| `workorder receipt merged --task ID [--ref refs/heads/main]` | Fetches the branch from `origin` and records the commit it points to |
| `workorder receipt deployed --task ID --target NAME -- CMD...` | Runs a read-back command; valid only if it exits 0 and prints the full commit ID |
| `workorder receipt live --task ID --target NAME -- CMD...` | The same, recorded after the deployment |
| `workorder check [ID]` / `check --task ID` | No argument: every task, one line per problem, or `check: OK`. With a task (or `tasks/ID.md`): that task explained |
| `workorder check --staged` | The pre-commit hook: checks the staged tasks this commit touches (task file, receipts, contract or its evidence) and their dependents. Refuses a commit that newly claims DONE or CLOSED on code other than the implemented code, and command changes on unfinished tasks |
| `workorder check --base REF [--verbose] [--allow IDS] [--run-tests] [--strict-tests] [--review warn\|refuse] [--all-tests] [--require-task] [--require-task-ignore GLOB] [--require-test-first] [--approving-review] [--owner-accepts LABELS] [--timeout SECONDS] [--jobs N]` | The pull-request check: changed task records and receipts, all repository test roots, every task whose tests changed, and dependents. Refuses stale claims, changed receipts, unsupported definitions and removed tasks; see [checks against a base](#checks-against-a-base) |
| `workorder repair --task ID` | Verifies a conflicted legacy append, keeps the common prefix and converts each side's extra lines to receipt files |
| `workorder claim --task ID` / `release --task ID [--force]` | Takes or gives up a task with a pushed tag; see [claims](#claims-and-ids) |
| `workorder claims` / `workorder ready` | Active claims / open tasks: free, held, blocked or waiting for delivery |
| `workorder hook session-start` / `hook stop` | Claude Code session context and stop check; see [agent hooks](#agent-hooks) |

Commands exit 1 when they refuse something, including a command receipt whose
command failed or a receipt that was recorded but cannot count (it says why).
`hook stop` exits 2 for unsupported finished claims. Commands work from
subdirectories. `--all-tests` requires `--run-tests`; `--strict-tests`, `--review`
and `--require-task` require `--base`. Without a base, `--run-tests --all-tests`
checks current evidence and reruns finished tasks on current code.

### What done checks

`done` refuses uncommitted code, an empty Scope or Acceptance (including sections
holding only template comments: `fill in Scope and Acceptance first`), and a task
another clone has claimed unless `--take-over` is given (the takeover is added to
Progress). It needs a concrete declared command; use individual receipts for
`test: any`.

**Acceptance tied to tests.** An item ending `(test: path::name)` must name a
pytest test that exists, is run by the task's command, passes now and fails on
the base code (refactor and verification tasks need only pass). `done` prints, for
example, `Acceptance: 2 of 3 items tied to tests that pass now and failed before;
1 item not tied to a test (a reviewer confirms it)`. `show` marks tied items.
No label applies to this `done` refusal.

**A code change.** The implemented code must differ from the target merge base,
or from the commit that created the task without a target (HEAD for an
uncommitted task). Otherwise: `TASK-1: no code changed since <ref>; nothing was
implemented; land the task on main first, then do the work on a branch`.

Test material is classified first: declared test paths, task test folders,
recognized test-command files, fixtures and helpers keep test-change review
whatever their suffix. Outside it, these are not code changes: documentation
(Markdown, reStructuredText, plain text, files under doc/docs), ignore rules,
license files, image assets, edits only to Python docstrings, and Python
comments, formatting or extra parentheses with unchanged syntax. They need no
code receipts and leave existing evidence current. Changed executable statements
and file modes count. Existing receipt hashes stay readable.

**`--no-code-change`** is for verification tasks whose own code and tests are
unchanged. Declared `code_paths` and the task's test files set the boundary;
without code paths, changes covered by another task's new implementation or
passing test receipts in this change are excluded (only matching file versions;
later edits and this task's tests stay protected). `done` refuses changes inside
the boundary even with `--needs-review`:

```text
TASK-1: code changed (feature.py); --no-code-change is for verification tasks with no code change
```

The flag exempts the run from the base-code failure requirement. The test receipt
records the exemption and an optional `no_code_change_base` commit; checks
validate it against that base, and against the branch comparison for an exemption
first recorded on the branch. A receipt already on the base keeps its claim; old
receipts use their implementation base when available. In a pull-request check,
an approving review or `workorder-allow:ID` accepts a false exemption, including
one in an older receipt; warning mode and another task's label do not. A
hand-written DONE with receipts but no code change follows the same rule.

**`--refactor`** declares unchanged tests passing before and after. The task's
test files, including selected helpers and configuration, must be unchanged since
the merge base, and the test must pass on both versions; a local target must be
available. A pytest command without paths includes discovered test files, and
explicit `test_paths` cannot hide files the command selects. The receipt records
`refactor: true`. `renew` and `renew --refactor` keep the declaration after later
commits and rebases (rechecking both versions); `renew --no-refactor` or a later
plain `receipt test` drops it. `--refactor` cannot be combined with
`--needs-review` or `--no-code-change`. The pull-request check reruns the
comparison with `--run-tests` and prints:

```text
TASK-3 is a declared refactor: tests unchanged, passing before and after
```

A new `done --refactor` with changed test files refuses with `its test files
changed; use --needs-review`. For an already finished declared refactor, passing
test edits are notes in pre-commit and Stop for work in progress; after committing, run
`workorder renew --task ID --no-refactor` or `workorder done --task ID --needs-review`.
Those edits change existing tests, so the pull request needs `workorder-allow:ID`.

A refactor or no-code-change completion needs an approving review or
`workorder-allow:ID` unless the task on the base already declares the matching
`kind: refactor` or `kind: verification`. With a local target, `done` and
pre-commit print one Note per task naming that requirement.

**`--needs-review`** is for intended test changes. Passing, supported evidence is
recorded with `pending_review: true` on the test receipt and DONE is written;
`pending_findings` records each message and whether it needs the task label.
`show`, `check` and `done` list them:

```text
TASK-6 DONE, pending review: the pull request needs workorder-allow:TASK-6 or an approving review
```

It does not accept failed current tests, missing evidence, or changed test
commands or dependencies. Assertion findings, a test that passes with changed
functions stubbed out, and unavailable comparisons can be recorded pending review.
Edits of existing tests need the label; the other findings clear with an approving
review too. No status edit is needed after review. `show` says `waiting for
review` until the receipt lands on the target. Local hooks accept the pending
claim with a Note (including a "could not compare" finding in pre-commit); the
pull-request check refuses it until the label or, where the rule allows, a
current-head approving review accepts it, even if no test files changed. A
predeclared kind does not accept an unavailable comparison. Later `renew`,
`receipt test` or `done` runs do not clear an unmerged review request; the main
check after the merge needs no label.

**Other tasks.** With a locally available target, `done` runs the test-change
checks against the merge base of HEAD and that ref, plus the tests of affected
finished tasks and shared test data, before writing DONE. It does not fetch;
without a target it prints `could not compare with the target: fetch origin
first`, and uses local main/master on a branch when no remote target exists.
Another task's findings stop blocking plain `done` only when that task has its
own `done --needs-review` completion in this change, with receipts covering its
current test files, and only for its own test edits and recorded findings.
Everything else blocks and appears once in `Other:`; the pull-request check still
requires that task's review. Notes name extra task labels, or
`workorder-allow:tests` for files without an owner.

**Messages.** A refusal says `TASK-1: DONE was not written; the status stays
IN_PROGRESS`, then the distinct Missing lines and one Next line; findings about
other tasks get one summary line (`check --base REF --run-tests` shows details).
A finished task says `TASK-7 stays DONE; this change was refused:` and the reason.
After a real failure Next says `fix the code or the test, then run workorder done
again`. Success says `TASK-1 DONE: its declared test passed on this code`, plus
`; still owes: merge of the tested code` (or other delivery debt) when it applies,
and a line asking you to commit tasks/ and .workorder/ (`and push` with an origin
remote). It warns when the clone has no pre-commit hook. A task not yet on the
base gets a note that the pull-request check will ask for an approving review.
A passing run that does not count says whether files or HEAD changed while it
ran; add generated files such as `__pycache__/` to .gitignore. The diagnostic
names the label needed and mentions an approving review only where the rule
allows one.

**Local checks give the pull-request check's answer.** `done` compares the change with
the target's merge base as the pull-request check does: edited, removed or weakened
tests, new skips or xfails, and earlier tests failing on the new code (also in test
files no task owns) refuse `done`, naming the label the pull request needs. For an
intended change, commit the work, then run `workorder done --task ID --needs-review`;
the task's own label `workorder-allow:ID` then accepts what that run recorded,
including changes to test files no task owns, so one label clears it. The pre-commit
and Stop hooks refuse these findings only when the change also makes a new DONE or
CLOSED claim without a review request; for work in progress they are notes. They also
refuse a DONE/CLOSED claim new since the target, on a task the change touched, that no
`done` run in this change recorded (`says DONE without a workorder done completion
record`): set it back to IN_PROGRESS and run `workorder done --task ID`. `renew` after
`done` keeps that record. Claims already on the target are not re-judged.
`WORKORDER_REVIEW=warn` is the local equivalent of `--review warn`.

**Test edits on finished tasks.** Without `--needs-review`, `done` refuses test
changes and changed commands. The pre-commit hook notes test changes, including
branch edits made while merging main; during a pending merge, files whose staged
content equals the incoming parent's get no note. For a finished task (DONE or
CLOSED with test receipts, even finished on this branch) whose test command or
files changed, a passing new command makes the local hooks note the stale
receipt: pre-commit runs the staged code, Stop uses staged test edits when
present, otherwise the working copy. Commit the change, run `workorder renew
--task ID` and commit its receipts; the note then drops the renewal step. The
pull request needs `workorder-allow:ID`. Failing tests still refuse; tasks without
test receipts keep the existing command checks. An unfinished task with no
receipts can change its command locally without a label. `new --test` warns about
a named path that does not exist yet (creating it first, even untracked, avoids
the warning).

A finished task does not own later Python additions: new test functions or
classes, fixtures, imports, and cases appended to a literal
`pytest.mark.parametrize` list (old cases, other decorator arguments and body
unchanged) need no note, label or renewal, even for a declared refactor. Changing
or removing an existing function, fixture, class member or module statement
needs review; conftest and non-Python files keep their rules. Python test files
whose syntax trees are unchanged (comments, blank lines) count as unchanged
everywhere.

### Test-first work

`done` and `check --base REF` say where each selected finished task's test came
from: `TASK-1's test landed on origin/main before this change and is unchanged`,
`TASK-1's test was written in this change`, or, for an existing file,
`TASK-1's test file was edited in this change (tests/test_value.py)` or
`TASK-1's new tests were written in this change (test_enable_word)`. New tests in
a shared file are attributed to the tasks this change works on (`shared by ...`
when several). The comparison uses the merge base; without the base task or test
the output never claims an earlier landing. For inline Python tests, the base
task's unchanged command supplies the provenance.

Write a failing test first, then run
`workorder new --test-first --title "..." --owner you --test "python -m pytest -q tests/test_feature.py"`
(`--test-paths` for wrappers). Every declared path must exist and the command
must fail on the current code before the task or reservation is created; a
passing command, missing executable or dependency, timeout or empty pytest
collection is refused. Land the task and failing test on main, then implement
on a branch.

`workorder init --test-first` adds `--require-test-first` to the generated check
(`--update-workflow` for an existing workflow). `check --base origin/main
--require-test-first` refuses a finished claim whose test files were added or
changed with the work, without running tests (`--run-tests` adds the
before/after results). An approving review or `workorder-allow:ID` accepts the
exception; the `tests` label and `--review warn` do not. `done
--require-test-first` checks the same provenance locally and refuses before
recording receipts, even with `--needs-review`.

## Task files

Commit `tasks/` and `.workorder/` with the work. Checks read the working tree;
`--staged` reads the index, and a workflow reads its committed checkout.

Only `tasks/<id>.md` files are task records. Markdown files there with other
names (a README, notes) are ignored; any other file is refused, because `tasks/`
is outside the code fingerprint. A task file starts with front matter between two
`---` lines, one `key: value` per line, each key once. Seven fields are required:
`id`, `title`, `owner`, `status`, `ships`, `waits_on`, `contract`. A file that
cannot be read is a problem for that task only. Fields starting with `x-` (such
as `x-issue: "#42"`) are yours: kept and never checked.

`note` appends a dated line under Progress; `note --next "TEXT"` replaces or adds
`next` (including folded or literal values; an empty string clears it). `done`
clears `next`, changes the status line to DONE and, with `--note`, appends a dated
line, only when DONE is written, so retries do not duplicate it. It keeps the
status line's quoting style and the file's line endings; other text is preserved.

`workorder retire ID --reason TEXT` turns a DONE or CLOSED task RETIRED and adds
the reason to Progress. Its record, notes and receipts stay; it no longer owns its
test files and its test no longer reruns. Summaries count retired tasks
separately. The pull request needs `workorder-allow:ID` or an approving review;
RETIRED from any other status is refused (`only a DONE or CLOSED task can be
retired`). Deleting a task file keeps the removal rule in
[receipts](#receipts).

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

`mission`, `summary`, `next`, the owners, `priority` and the five sections say
what the work is, what is in and out, who does what and how it will be accepted.
Workorder keeps them and never treats them as evidence; without a
[contract](#contracts), Acceptance items are checked only when tied to a test.

`workorder new --template simple` writes only the seven required fields (the
0.1.0 format); add `test:` before claiming DONE. Both formats can share a
repository, but 0.1.0 refuses the other fields, so upgrade every clone and
workflow pin first.

### What YAML is read

Front matter is not parsed as YAML. Workorder reads this subset and refuses
everything else, and unknown fields, with a message naming the line, so a typo
cannot quietly drop a dependency:

- `key: value` on one line: plain text, a JSON string (`"..."`), or a
  single-quoted string (`'...'`, `''` for a quote). A `#` after a value is part
  of the value.
- `#` comment lines at the left margin and empty lines between fields. A UTF-8
  byte order mark is ignored.
- One-line lists: JSON (`["TASK-1", "task:TASK-2"]`) or simple
  (`[TASK-1, task:TASK-2]`, items without spaces or quotes).
- Indented `- item` lists (also at the left margin) for `depends_on`, `blocks`,
  `waits_on`, `test_paths` and `code_paths` only; nothing after the colon and no
  items is an empty list:

  ```yaml
  depends_on:
    - TASK-1
    - "TASK-2"
  ```

- `>` (folded) and `|` (literal) blocks for `mission`, `next` and `summary` only,
  with optional `-` or `+` chomping, read as YAML does (checked against PyYAML).

Refused: anchors and aliases, tags, nested mappings, multi-line plain or quoted
values, blocks or indented lists on other fields, explicit indentation numbers
(`|2`), and a block line indented less than its first line (YAML would end the
block; Workorder refuses rather than guess).

### Extended fields

`workorder new` (`--template extended`) writes, in this order: `id`, `title`,
`owner`, `business_owner`, `priority`, `created_at`, `mission`, `depends_on`,
`blocks`, `next`, `summary`, `status`, `ships`, `waits_on`, `test`, `contract`,
and the sections `## Context`, `## Scope`, `## Lanes`, `## Acceptance` and
`## Progress`. Any subset of optional fields may be used.

- `id`: matches the file name and `PREFIX-N`, for example `TASK-12`.
- `status` is the claim: `IN_PROGRESS`, `BLOCKED`, `DONE`, `CLOSED`, `CANCELLED`
  (work that will not be done: needs no evidence, is never a finished dependency,
  is left out of `ready`) or `RETIRED`. People and agents may edit it, but the
  status alone never proves completion; `done` sets DONE when the check supports it.
- `ships`: what CLOSED needs beyond DONE: `none` (default), `repo` (the tested
  change on the target branch) or `live` (also deployment read-backs).
- `contract`: `none` or a repository-relative path ([contracts](#contracts)).
- `business_owner`, `priority`, `mission`, `next`, `summary`: text, may be
  empty, not used by any check.
- `created_at`: empty or an ISO date or time; `new` fills in today's UTC date.
- `depends_on`: task ids (`TASK-2` or `task:TASK-2`), not the task itself.
  `waits_on`: a JSON list of `task:<id>` or `person:<name>`.
- `test`: the command that proves the task, or `any`. DONE or CLOSED needs it
  (missing or empty is owed as "a declared test"). Only a `test` receipt whose
  command is exactly this counts; `receipt test --task ID` with no command runs
  it; `any` accepts any recorded command. `new --test` fills it in. No shell runs
  it: words split on spaces, quotes group words, backslashes stay as written, and
  `&&`, `|`, `;` and `>` are refused (use a script).
- `test_timeout`: optional positive seconds (`60` or `0.5`); no deadline when
  absent. `--timeout SECONDS` overrides it for `done`, `receipt test`, `renew` and
  `check --base REF --run-tests`, including base runs and `--all-tests`. A timed-out
  run fails with exit code 124 (`test timed out after ... seconds (exit 124)`);
  receipt commands exit 1 and `done` leaves the status unchanged. On POSIX the
  deadline also kills the test's process group.
- `test_paths`: repository paths, for example `["tests/test_list.py",
  "tests/integration"]`; a folder includes every file below it. If absent,
  existing paths in the `test` command are used, including quoted paths and
  pytest node paths such as `tests/test_list.py::test_sort`; `new --test` writes
  inferred paths only when they exist. An empty list selects no test files. Base
  and current definitions are both read, so removing a path cannot hide a change.
  Changing `test_paths` under `--base` needs `workorder-allow:ID`. Wrapper
  commands need `new --test-paths PATH ...`, which may name future files.
- `code_paths`: files or folders, for example `["src/calc.py", "src/math"]`
  (`new --code-paths`). The pull-request check requires changed code to fall
  within the combined paths of tasks adding implementation or test receipts; an
  empty list covers no code, and unrelated or unscoped receipts cannot disable
  declared scopes ([code changes](#code-changes-need-task-evidence)).
- `kind`: `feature`, `fix`, `refactor` or `verification` (`new --kind`; `show`
  prints it). `feature` and `fix` are descriptive. `refactor` and
  `verification` predeclare the completion exemption only when present in the
  task on the comparison base; adding it with the work does not.
- `merge_ref`: a full branch ref (`refs/heads/release`,
  `refs/remotes/origin/release`) that overrides the merge target. Short names
  and tags are refused; an absent target does not prove a merge.
- `blocks`: task ids, informational. `check` reports an unknown task or two tasks
  blocking each other, but never makes a task wait.

### Dependencies

`waits_on` items plus `task:<id>` for each `depends_on` id form one dependency
list (listing a task in both is allowed). DONE or CLOSED is refused until every
task dependency is finished and no `person:` wait remains; remove a person wait
when it is answered. A task dependency is finished when it says DONE or CLOSED,
its evidence supports that, and its own dependencies are finished, all the way
down; a reopened or cancelled task is not. `ready` lists open dependencies as
blocked. A task may not both wait for and block the same task. Missing
dependencies and cycles make `check` fail on the tasks involved; other tasks are
still checked and listed. Planning fields do not supply evidence: `depends_on`
adds dependencies, `test` selects the command that counts and `merge_ref` the
delivery target.

## How the status is computed

- **IN_PROGRESS** until the task declares a `test` (or `any`), has an
  `implemented` receipt, the latest `test` receipt on the same code after it is
  passing and valid, and every critical contract criterion is met. A later
  failing or invalid run on that code revokes an earlier pass; a later passing
  run restores it. Runs on different code are recorded but ignored, and the
  receipt command exits 1.
- **DONE** when that holds.
- **CLOSED** when DONE and delivery is complete. `ships: none` needs nothing
  more (`show` prints `computed: CLOSED` for a supported DONE claim).
  `ships: repo` needs a computed merge (below) or a `merged` receipt after the
  implementation whose commit contains the implemented commit or code; any such
  receipt counts. `ships: live` also needs a valid `deployed` receipt after that
  merge whose commit contains the implemented commit or code (or the matched
  squash or rebase commit), then a valid `live` receipt for the same commit and
  target. Valid means the read-back command exited 0 and printed the expected
  commit ID; it does not show which server answered. With a computed merge,
  read-backs after the implementation count as after the merge.

Only receipts after the newest `implemented` receipt count. A `test` receipt is
valid only if the command exited 0 and the working tree was still clean with HEAD
unchanged when it finished; with a declared `test`, only receipts of exactly that
command decide.

`show` and plain `check` describe supported evidence as ``recorded receipts
support the claim (not re-run here; `check --run-tests` re-runs it)``. `check
--run-tests` counts tasks whose declared tests ran, once each; shared commands run
once per code version and timeout, and unowned Python file comparisons are counted
separately.

"Code" is a SHA-256 over the commit's file listing, leaving out `tasks/`,
`.workorder/` and `.notquite/`. Committing task files and receipts does not
change it, so later runs on the same code, on any commit, still count.

A DONE or CLOSED claim must be made on the implemented code:

- An uncommitted claim is compared with HEAD's code (the index under `--staged`).
- A committed claim is traced through the task file's history to the commit that
  made it (since `REF` with `--base REF`), and that code is compared with the
  implementation it relied on, unless a newer `implemented` receipt followed.
  A claim carried into a verified squash or rebase is accepted from any checkout.
  For every merge method, a claim on commit C descending from the matched landing
  M must match M on every code path the implementation changed; a revert,
  overwrite, deletion or rename of those paths needs `workorder renew --task ID`.
  Unrelated paths may change. Changing DONE to CLOSED is a new claim. A valid
  claim at or before M stays valid when later commits change those paths.
- After a branch rebase, the claim also counts when its code diff from the merge
  base has the same `git patch-id --stable` as the implementation's (both
  nonempty, task files and receipts excluded). Merging main into the branch
  also works; a changed patch needs fresh evidence.

For `--require-task`, a rebased branch is also covered when every recorded
implementation path has the same code and mode as its receipt and every other
code path matches the target (documentation and docstring-only edits ignored,
deleted paths still deleted). Receipts before 0.12 lack these path records and
keep their rules. A later edit still needs a new receipt.

A task imported unchanged from the target or a merge parent (task file and all
receipts identical) is not a new claim. The staged hook reads MERGE_HEAD during a
pending merge; committed checks inspect merge parents. If your claim becomes
stale after merging main, renew it on the branch before pushing, so the later
squash matches the tested code. For a task finished in the pull request, later
changes to its `code_paths` (or the implementation receipt's files) refuse:

```text
TASK-1: its code changed after it was finished (calc.py); run workorder renew --task TASK-1 and commit the receipts, so its evidence matches this code and survives a squash merge
```

`done` and pre-commit print this advice as a Note. A finished claim already on
the target may be renewed with passing tests without proving a new code change;
reopening and finishing it again is a new claim with that obligation. `check <id>`
notes when HEAD's code differs and names the command behind the deciding run.
Code that lands after DONE does not reopen a task.

A receipt whose commit is missing from this clone (after a squash, rebase or
amend, or in a shallow clone) is kept. It counts again when a commit here holds
exactly the code it recorded: HEAD, or a commit that touched the task or its
receipts. New `implemented` receipts also record changed paths, Git modes, blob
IDs and deletions, so a landing matching those paths counts even when parallel
work changed other files and the source commits are gone. Otherwise run
`renew --task ID`.

## Computed merges

For `ships: repo` or `live`, no `merged` receipt is needed when Git history shows:

1. the implemented commit is an ancestor of the target;
2. a code-changing commit on the target holds exactly the implemented code;
3. the code diff from the merge base to the implementation has the same
   `git patch-id --stable` as a target commit after that base (squash; a single
   replayed commit is reported as rebase); or
4. every code commit from the merge base to the implementation has a patch
   equivalent on the target (rebase, `git cherry` semantics), with at least one
   code patch. Merge resolutions are not guessed from parent patches.

Patches exclude `tasks/`, `.workorder/` and `.notquite/`; empty patches cannot
match. Tests, contracts and dependencies still apply. Existing `merged` receipts
keep the ancestry or code-fingerprint rules.

Ancestry names the first landing on the target's first-parent history; other
searches keep the earliest match. For fast-forwards the path comparison uses
the task's most recent open period before implementation, or the
implementation's parent if the task came later (conservative when unrelated work
happened then). Commits recorded as another task's implementation, or covered by
this task's previous implemented receipt, are excluded from the protected paths;
other paths stay protected even when another task changed them. A committed claim
uses the receipts in that commit. Without the original history, new receipts
protect their recorded paths and old receipts the landing tree.

The target is `merge_ref` when set, otherwise the first existing of
`refs/remotes/origin/main`, `refs/remotes/origin/master`, `refs/heads/main`,
`refs/heads/master`. `origin/HEAD` is ignored. A remote `merge_ref` names the
remote and branch (`refs/remotes/<remote>/<branch>`). A commit only on local
`main` is not merged while `origin/main` exists, until pushed. Checks read local
refs without fetching, from any checkout; a feature branch is never its own target.

The search examines up to 1,000 target commits after the merge base, and up to
1,000 implementation commits for rebases (a partial range never proves one);
ancestry is checked first. Results and fingerprints are cached in
`.git/workorder-digests/` by commit ID (local, safe to remove). Each command
shares receipt and Git reads in memory and reads metadata blobs in batches.
Git read reuse accounts for included configuration and external ignore and
attributes files; filters such as Git LFS disable it only where attributes
select them, and helpers with unobservable inputs get fresh reads.
`WORKORDER_GIT_CACHE=0` disables reuse for diagnosis; the rules are the same.

Without the original history, Workorder still recognises exactly matching code
or a landing matching a new receipt's recorded paths; otherwise fetch history or
renew. When the target's change differs, the next step suggests `workorder renew
--task ID` on main or `workorder receipt merged`, or to push main when the match
is only on local main. `show` and `check` explain a match, for example
`merged into main by squash 1a2b3c4, computed`.

## Checks against a base

`check --base REF` compares with `git merge-base HEAD REF`; here, REF tests and
definitions mean the versions there. Fetch first; new work only on the target is
not a branch deletion. Messages keep the target: `since origin/main (merge base abc1234)`.
It checks changed task records, receipts, contracts and evidence, their
dependents, repository test files, and every task whose selected tests changed,
even when its task file and receipts are untouched.

**Which files are tests.** Test folders are named `test`, `tests`, `testing`,
`__tests__`, `spec`, `specs`, `fixtures`, `testdata`, `test_data`, `testsupport`,
`test_support`, `golden` or `__snapshots__`, or start with `test_` or end with
`_test`/`_tests` (case-insensitive, whole names: `latest` and `contest` are
production); every file below them counts, data included. Test files are also
`test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`, `*.spec.*`, `*_spec.*`,
`*_tests.*`, `testing.py`, `*_testing.py`, names starting `expected`, `golden` or
`snapshot`, `conftest.py` and pytest configuration. A root-level test file does
not make the root a test folder; importing a package does not make it a test
file. REF and current definitions both count; `tasks/`, `.workorder/` and
`.notquite/` are never tests. Declared `code_paths` and Python package source
(`src/` packages and namespaces, flat packages with packaging metadata) beat these
guesses: `src/click/testing.py` is code. Explicit `test_paths` from either task
version beat code, including package-internal tests.

In `.workorder/config.json`, `test_file_patterns` and `test_directory_patterns`
are lists of nonempty globs replacing those defaults (`[]` turns a guess off),
for example `{"test_file_patterns": ["test_*.py", "*_test.py"], "test_directory_patterns": ["tests", "checks"]}`.
File globs are case-sensitive; directory names are lowercased. Explicit task
paths and pytest's own discovery and configuration still count.

**Review modes.** `--review refuse` is the default. `--review warn` prints each
test-review finding as `Warning:` with a `Review: N test changes need a reviewer's
look` summary, then `check: OK` (with file and line annotations under
`GITHUB_ACTIONS`). False claims and current failures refuse in both modes.
`--allow` supplies labels; `--approving-review` stands for an approving review
of the current head.

| Change or result | Default | What clears it |
|---|---|---|
| Pure Python additions to a finished task's tests, including appended parameter cases (old cases and body unchanged) | Passes; its tests still run | Nothing needed |
| Only added test lines, new test files or added import names, when the base tests passed | Passes if the current test fails on base code | `--strict-tests` reviews additions too |
| Any change, even an added line, to a test whose base tests already failed | Refused | `--allow ID` only |
| Removed or changed test lines, including a rename | Refused | `--allow tests` or an owning task's label |
| REF tests fail on current code (also for a finished task whose base tests failed) | Refused | `--allow ID` for an intended change or rename |
| Fewer pytest passes, or more skips, xfails or deselections | Refused | `--allow ID` |
| A new skip/xfail that suppresses a test here, or conftest/pytest configuration changes | Refused, additions too; a false condition alone does not refuse | `--allow ID`, or `--allow tests` for those lines |
| Pytest `-k` or `--deselect` changes | Refused | `--allow ID` only |
| A DONE/CLOSED task's current tests fail | Refused | Nothing |
| IN_PROGRESS/BLOCKED work's current tests fail or are missing | A note | Test edits and REF regressions still need review |
| A task DONE/CLOSED at REF is reopened and its current test fails | Refused (locally a note) | `--allow ID` or an approving review |

Approval is not enough for the `--allow` rows except the last: they change existing tests.

**The test must fail on base code.** For a task being finished whose own code or
tests changed, `done` and `check --base REF --run-tests` run its current command on
the merge-base code with its new or changed test files overlaid. A pass refuses:

```text
TASK-2: its test passes on origin/main's code too, so it does not show the change works; make the test check the new behaviour
```

For pytest, this and the stub check select the task's new or changed test
functions (syntax and decorators compared with the base; comments ignored) and
run them together once, parameter cases included: at least one must fail on base
code and at least one under stubs, and refusals name them. With no changed
function, the whole command (with its filters) decides. A new function or module
may fail on the base with ImportError or AttributeError. A failure shows the
command tells the versions apart, not that each assertion fits the requirement.
Task scope is
`code_paths`, else recorded implementation paths, plus its test files; shared
configuration edits get one file finding naming all owners.

An approving review or `workorder-allow:ID` accepts this finding; `--allow tests`
does not, and `--review warn` makes it a warning. `done --needs-review` records
it and writes DONE pending review. Locally, `done --refactor` accepts unchanged
tests passing on both versions and `done --no-code-change` a verification task;
in a pull request these need an approving review or `workorder-allow:ID` unless
the base task declares the matching `kind: refactor` or `kind: verification` (a
kind added on the branch does not count). With a local target, both flags and
pre-commit print a Note per such task: `the pull request needs workorder-allow:TASK-1`.
A hand-written DONE/CLOSED with receipts but no production-code change follows
the verification rule. The receipt marker carries the exemption; a later test
receipt without it ends it. Base-code commands that cannot start, time out or
lack external dependencies are comparison problems.

These proofs apply to completions made in this change: a new DONE/CLOSED since
the merge base, including a reopened task finished again. Landed completions and
renewals of finished claims already on the target owe nothing new. An unchanged
finished claim owes no proof for another task's new function, even in its files;
its test still runs and must pass. Hooks note a passing test rename; the pull-request check reviews it.

**Stubbed-out code.** For changed or added Python functions (methods, nested and
async included; whitespace and comments do not count), `done` and the
finished-task base check with `--run-tests` make one more temporary copy, replace
the task's changed function bodies with `raise NotImplementedError('workorder stub')`
and run the command once. Test material is excluded; checkout files are never
rewritten. Only functions in the task's `code_paths` are replaced; without them,
other tasks' declared paths are excluded. A task with no changed function of its
own gets no stub check. If the selected tests still pass, completion is refused,
naming tests, functions and files; mixed results list passing tests in one Note.
An approving review or `workorder-allow:ID` accepts it; `--allow tests` and
`--review warn` do not; `done --needs-review` records it pending review. Declared
refactors and valid no-code-change runs are exempt. A failure does not prove every
function was exercised.

Selected test files absent at the base are new tests; new helpers or fixtures do
not make missing old tests a failed comparison. A new
`conftest.py` needs configuration review only for tasks with existing test files
below it; an edited one keeps the configuration rule.

**Tests must assert something.** `done` and the base check, even without
`--run-tests`, refuse an added or changed Python test function with no
recognised assertion:

```text
TASK-2: test_div in tests/test_div.py asserts nothing that can fail
```

Constant assertions (no name, attribute, subscript or call: `assert 1 == 1`,
literal containers, literal arithmetic) count as `assert True`, and `==` or `is`
with the same name, attribute or subscript on both sides (`assert row['id'] ==
row['id']`) is refused. Expressions are inspected, never run. `pytest.raises`,
`pytest.warns`, `pytest.deprecated_call`, `pytest.fail` (and aliases),
`self.assertWarns`, `self.assertRaises`, `self.assertLogs`, and calls named
`assert*`, `verify*`, `check*`, `expect*` or `ensure*` count. Other calls count when
their bodies assert, followed up to four calls deep through the test file,
conftest.py files on its path and imported test material (aliases and relative
imports understood); uncalled helpers do not count and production imports are
not test material. Only added or changed functions are scanned. A missing new
module cannot make an assertion-free function evidence. 

An approving review or `workorder-allow:TASK-2` accepts an unusual valid test or
a smoke test that only checks a call does not raise; `--allow tests` accepts it
only in files with no task owner; `--review warn` does not. `done` still refuses:
fix the test, or use `done --task TASK-2 --needs-review` and get that review.

Adding `div` to `from calc import mul` (also parenthesised or aliased) is an
addition when existing top-level imports and bindings stay intact. Changed
modules, removed names, rebinding a name old tests use, changed bodies or
assertions, new skip markers and configuration still need review.

**Old tests on new code.** With `--run-tests`, tasks this change works on (task
file or receipts differ from REF) and tasks whose selected tests changed run
their current command and their REF tests on current code. Each comparison uses
a temporary copy: REF test files and command over current code, added test files
absent, deleted files restored; the checkout is never overlaid. If the task or
its tests did not exist at REF, a note says so. Changed unowned Python test files
run with `python -m pytest -q <path>`; their base version must pass on base code
before a failure is blamed on the change:

```text
tests/test_mul.py: its version from origin/main fails on this code (behaviour changed)
```

That replaces the file's removed-line finding and needs `workorder-allow:tests`;
an approving review is not enough. Other unowned files get static review.

Copies use tracked files plus untracked non-ignored files (a staged check uses
the index; a REF copy that revision). Ignored dependencies such as `node_modules/`
and `.venv/` are linked, or copied with a note if links fail; tracked files win,
bytecode and pytest caches are not linked, and writes to a linked dependency
reach the local one. Source and test files are independent copies. Internal
absolute symlinks are rebased, including Windows extended paths and case variants.

REF tests failing on current code also run on REF code: an existing failure there
is a note; a failure or timeout only on current code needs acceptance. Commands
that cannot start, copy failures, missing reference files and unreadable pytest
outcomes get explicit diagnostics that no acceptance replaces; external failures
name the environment problem. A missing repository module whose REF tests pass at
REF is a reviewed code change. An intentional change from 10% to 20% needs
acceptance for edited and failing REF assertions:

```text
TASK-1: its tests from origin/main fail on this code; a reviewer accepts it with the label workorder-allow:TASK-1
```

**What a task owns.** The files and folders its command names (a folder includes
everything below it, a file not its siblings), its `test_paths`, and `conftest.py`
and pytest configuration on their path. A pytest command without paths uses
`testpaths` from pytest.ini, pyproject.toml, tox.ini or setup.cfg, else every
recognised test file; if none is found, `new` refuses and asks for `--test-paths`.
`new --test` writes `test_paths` for existing named files or folders (`test` in
`make test` is not a path unless it exists). Helpers and data outside recognised
test material need explicit `test_paths`. A missing named test never widens to
others. An empty `test_paths` selects no task tests but cannot hide repository
tests from review. `--allow tests` accepts every changed test line and unowned
old-test failures, but not `-k`/`--deselect` changes, an owned base test failing
on new code, or changes to a task test that already failed at the base.

A finished task whose test maps to no test files in either version, when the
base has test files, is refused: `TASK-1: no test files are known for its test; set test_paths`.
Files named by `test_paths` or the command count when added on the branch, as
additions needing no label.

Commands through make, npm, npx, yarn, pnpm, go, cargo, tox, nox, sh or bash, and
non-test scripts, need explicit `test_paths` under `--base` (also for dynamic
imports); the wrapper is protected too, and a script in `test_paths` may be the
test. `new` asks for `--test-paths` for wrappers; an unfinished task gets a local
note. Only tasks the change touches must have them. Changing `test_paths` needs
`--allow ID`.

Pytest configuration is conftest.py, pytest.ini, .pytest.ini, pytest.toml,
.pytest.toml, tox.ini, setup.cfg, pytest sections of pyproject.toml and custom
configurations a command selects (other TOML sections need no review). Each changed file gives one finding naming its
owners, accepted by `--allow tests` or an owning task's label. Without
`--run-tests`, file, configuration and selection checks still apply and a note
says the REF comparison did not run.

**Status and definition changes.** Moving DONE/CLOSED back to IN_PROGRESS or
BLOCKED needs the task label or an approving review, unless a new counting failed
test receipt arrives in the same commit (set IN_PROGRESS before committing it;
keeping DONE is unsupported). A pull-request check runs a reopened task's command
even without `--run-tests`; a failure needs an approving review or
`workorder-allow:ID` (not a failed receipt or `--review warn`). Moving any other
status to CANCELLED, or DONE/CLOSED to RETIRED, needs the label or an approving
review; RETIRED from any other status is refused. `--staged` reads the staged
status and receipts.

Removing or rewording Scope or Acceptance needs the label or an approving review;
additions need it only if the task was DONE/CLOSED at REF. Contract or evidence
changes and removed dependencies need review; `test` and `test_paths` changes
need the label. A task absent at REF that claims DONE/CLOSED always needs
definition review, even if its test fails on base code and passes now:

```text
TASK-4 was written and finished in this pull request; an approving review clears this (the reviewer reads its definition; a second pull request is not needed), or the label workorder-allow:TASK-4
```

A new unfinished task supplying code receipts avoids that review when, with
`--run-tests`, its test fails on base code and passes now; otherwise it reports
`is new in this pull request`, cleared the same way.

Removing a task is refused, even with no receipts. With an approving review or
`--allow ID`, a task DONE, CLOSED, RETIRED or CANCELLED at REF may be removed with
all its receipts in both folders, which also exempts its receipt history. Partial
removal, removing unfinished tasks and editing kept receipts refuse; dependencies
must stay valid. Every commit in `REF..HEAD` is checked for receipt edits or
deletions, including ones later restored.

`--allow` takes comma-separated task ids and `tests`, `no-task` and `workflow`.
`tests` accepts changed test lines, owned or not. `no-task` bypasses only
`--require-task`. `workflow` accepts Workorder workflow changes only. Nothing
excuses a finished task's failing current test.

### Current runs, cache and summaries

**Project test.** Set an optional repository command:

```sh
workorder init --project-test "python -m pytest"
```

It is stored as `project_test` in `.workorder/config.json`; `init --check
--project-test CMD` checks without writing. A first `init` (no workflow, agent
block or `project_test` yet) that finds pytest configuration (pyproject.toml
`[tool.pytest...]`, setup.cfg `[tool:pytest]`, pytest.ini, tox.ini `[pytest]`) or a
`tests/` or `test/` folder of `test_*.py`/`*_test.py` files sets `python -m pytest -q`
(`python3` if only that is on PATH; pytest applies `testpaths`) and says so, or leaves it off and says so when
that interpreter lacks pytest. `init --no-project-test` keeps it off or removes
it; later plain `init` runs leave it. Commit the configuration. It runs freshly in
`done` and `check --run-tests`, not pre-commit, and writes no receipt.

**Speed.** The project test adds one full run of your suite to every `done` and to
the pull-request check, so each takes at least as long as the suite. For a suite
that takes minutes, run `workorder init --no-project-test`, or set a faster command
with `workorder init --project-test "CMD"`.

For a failing pytest command, only the failing nodes rerun on the base: a test
failing there too is a Note; one that passed there is a new regression. Missing
base results, collection or setup failures and unidentifiable failures are
reported as undecided. Only `workorder-allow:tests` accepts a project finding
(existing tests now fail); an approving review and `--review warn` do not.

Without `project_test`, output says `only the declared tests ran; the project's
other tests did not run (set a project test with workorder init --project-test)`.

**Finished tasks.** `--all-tests --run-tests`, with or without `--base`, runs
every DONE/CLOSED task's concrete command on current code, once per distinct
command and timeout: `regression: TASK-1's test fails on this code` refuses and
leaves the evidence unchanged. Unfinished tasks' failures are notes (except
reopened tasks above); `test: any` has nothing to rerun; no receipts are written.
With no changed task the summary says `no task changed in this change; ran 2
finished tasks' declared tests`. New workflows enable `--all-tests`.

`--jobs N` (`check`, `done`) runs distinct current commands concurrently; the
default is the CPU count (one if unknown). Identical commands and timeouts share
a run; results print in task order; comparisons keep their own copies.

**Reuse.** `done`, `receipt test`, `renew` and explicit `check --run-tests` run
tests freshly, so receipts describe their own runs. Only implicit hook runs reuse
a result, printing `(result reused from the run at HH:MM UTC; nothing it covers
changed since)` once per time. The key covers non-ignored file contents, paths,
types and executable bits (not tasks or receipts), the command and timeout; not
ignored dependencies, services or environment variables; a metadata-only commit
keeps it. Results live in
`.git/workorder-tests/` (not cloned, removable); CI runs bypass them. Unchanged
REF tests with the same command share the current run. Staged files are read with
one `git checkout-index` call using staged attributes, so CRLF checkouts reuse
runs. 

**Output.** Notes of one kind are grouped (`Note: 200 tasks' tests passed when
this check ran them`); exemption and test-origin notes stay per task; `--verbose`
prints all. Every mode prints a summary, except `--staged` with nothing in scope:

```text
checked 3 tasks (2 claimed DONE or CLOSED); ran 2 tasks' declared tests
```

`done`, `check`, the hooks and `show` shorten long output: each distinct Note
once; per-test findings of one kind as one line with a count and 5 names (`1173
project tests fail on this code that pass on origin/main: ..., ... (+1168 more)`);
failing output as pytest's first 5 FAILED lines, the first 20 lines of the first
failure (once per command run) and the counts. The full text is saved in
`.git/workorder/last-output.txt` (never the work tree) and its path printed;
`--verbose` prints everything, and hooks read `WORKORDER_VERBOSE=1`. Base-branch
tests failing on current code are headed `Tests from origin/main on this code:`.

The run count counts tasks (a task's current and base runs once), with `(cached)`
or `(some comparisons cached)`; a missing executable does not count. Otherwise a
line says `no declared test ran in this check`, or just `no task changed in this
change` when nothing changed, even after merging main's test edit. Unreadable and removed tasks are counted, but not
as claims; one unfinished task reads `checked 1 task (not claimed finished)`.
Supported claims without a contract get the Acceptance reminder. Checks exit 0
to accept and 1 to refuse.

A run with zero passed tests, even all skipped or xfailed, never supports DONE:
`its test ran but no test passed (1 skipped)`, with no label or approval to accept
it. New receipts record `valid: false` (0.12.0 readers refuse it) and optional
`no_tests_passed`; old receipts stay readable and `--run-tests` rechecks them.

Python test line edits need no review when syntax trees match ignoring comments,
docstrings, assert messages and the order of `==`/`!=` operands; other edits
(chained comparisons, conditions, decorators, rebinding imports, deletions) do.
`--strict-tests` reviews every test-file edit.

### Code changes need task evidence

`check --base REF --require-task` requires a new counting implementation or test
receipt for a task with a concrete command: a new unfinished task (with the
definition rule above) or a task unfinished at the merge base. Renewing an
unrelated finished task, `test: any`, a test that does not count, notes, status
edits, new definitions and merge or deployment receipts do not qualify. An
approving review, `--allow ID` or `--allow no-task` accepts code without such
evidence.

Only non-test code counts: recognised test files, `test_paths`, `tasks/`,
`.workorder/`, `.notquite/`, `docs/`, `.github/`, `*.md`, `*.rst`, `*.txt`,
`LICENSE*`, Git ignore/attribute rules, editor settings and common image/PDF
assets are excluded (receipt fingerprints are unchanged). Generated
pull-request checks enable the rule. `*.egg-info/`, `*.dist-info/`,
`__pycache__/`, `.pytest_cache/` and `.pyc` files are generated output: never code,
no label needed, and left out of new path maps. Lockfiles are always excluded,
in any folder: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`,
`Pipfile.lock`, `uv.lock`, `Cargo.lock`, `go.sum`, `Gemfile.lock` and `composer.lock`.
Repeat `--require-task-ignore GLOB` for other repository-relative paths, for
example `--require-task-ignore 'generated/**' --require-task-ignore '*.svg'`;
these exceptions affect only this rule.

A branch receipt still covers its changes when later code matches the clean merge
of its commit and the target: on the merge commit, after merging main, and with
parallel squash-merged pull requests. A local squash commit with the hook also
accepts recorded paths whose staged contents, modes and deletions match the
receipt, even after another task landed. Both stay checkable in a fresh clone.
Later branch edits need a new receipt; when main moved, the check says
`main moved since TASK-2 was tested: merge it and run workorder renew --task TASK-2`.

Implementation covers a task's `code_paths`. When tasks adding receipts declare
scopes, code outside them is refused, even without `--require-task`:
`src/unrelated.py is outside the code paths of TASK-2`. An approving review or a
contributing task's `workorder-allow:scope:TASK-2` accepts it; warning mode,
`workorder-allow:TASK-2`, `no-task` and `--require-task-ignore` do not. Unrelated
receipts or unscoped co-tasks cannot turn scopes off; legacy unscoped tasks keep
old behavior. Files match exactly; folders include descendants. When a task omits
`code_paths`, `done` lists the code it touched:
`TASK-2 changed: calc.py, security.py; set code_paths to have changes outside them flagged`.

### GitHub Actions

The workflow runs on pull requests, submitted reviews and pushes to the default branch. Pull requests
use `--base` and read `workorder-allow:` labels as data. Pushes check current claims and tests without
`--base`, so decisions made on the pull request are not needed again. Setup picks the push branch
from local `origin/HEAD`, then main/master refs, then main; task merge targets are unaffected.

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
      - run: python -m pip install workorder==0.18.0
      - name: Check tasks after merge
        if: github.event_name == 'push'
        run: |
          trap 'status=$?; if [ "$status" -ne 0 ]; then echo "Reviewer guide: https://github.com/Argento17/workorder/blob/main/docs/reviewer-guide.md" >&2; fi; exit "$status"' 0
          workorder check --run-tests --all-tests
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
          trap 'status=$?; if [ "$status" -ne 0 ]; then echo "Reviewer guide: https://github.com/Argento17/workorder/blob/main/docs/reviewer-guide.md" >&2; fi; exit "$status"' 0
          # Reviewed task ids, unowned tests, or the code-to-task requirement.
          gh api --paginate --slurp "repos/$GH_REPO/issues/$PR_NUMBER/events" > "$RUNNER_TEMP/workorder-label-events.json"
          gh api --paginate --slurp "repos/$GH_REPO/pulls/$PR_NUMBER/reviews" > "$RUNNER_TEMP/workorder-reviews.json"
          allowances=$(python -m workorder.review_labels "$GITHUB_EVENT_PATH" "$RUNNER_TEMP/workorder-label-events.json" "$RUNNER_TEMP/workorder-reviews.json" "$BASE")
          # Test changes are refused unless a reviewer other than the author adds the label or approves this head; --review warn is optional.
          workorder check --base "$BASE" --run-tests --all-tests --require-task $allowances
```

A label counts only when its latest add event (from every page of issue events) was made by someone
other than the pull request author; author-added labels are ignored with a notice (in
[solo mode](#solo-mode), a confirmed owner's are owner acceptance). Removing a label
cancels it, and an author re-adding it does not restore a reviewer's add. An APPROVED review counts
only when someone other than the author made it on the current head; old-head, dismissed and author
reviews do not, and a later request for changes from that reviewer replaces it. Missing history or
actor identity supplies nothing. Account IDs and case-insensitive logins identify the author. The API
uses the workflow's read-only token; a failed request stops the check. Labels supply `--allow`; a
counted review supplies `--approving-review`. The checkout uses the event's head commit, so a review
of an earlier head cannot accept a later one.

The review rule: an approving review clears every finding except changes to existing tests (edited,
removed, skipped, weakened, or existing tests that now fail, including project tests), which need
`workorder-allow:ID` or `workorder-allow:tests`, and changes to the Workorder workflow file, which need
`workorder-allow:workflow`. Approval therefore also clears code with no task, scope findings, a task
written and finished in one change, assertion and stub findings, test-first exceptions, reopened
tasks whose tests fail, unavailable comparisons and refactor/verification completions not predeclared
on the base. Each refusal says which applies (`an approving review clears this`, or
`a reviewer accepts it with the label ... (an approving review is not enough: ...)`). Accepted
findings print as `accepted by an approving review: TASK-3 (new task)` (with a label as well, the
label's items are listed there too); label-only findings left print as `approval did not cover:`. Failed current tests and unsupported
claims always refuse.

The workflow rule covers `.github/workflows/workorder.yml` and a renamed generated workflow, found by
its `python -m workorder.review_labels` command in the base or current contents (so removing that
command counts); only `workorder-allow:workflow` accepts it. Workorder checks the change only when it runs: make the check required with a
repository rule.

Anyone with triage rights can apply labels. Protect acceptance with CODEOWNERS and required code
owner review on task, test and configuration paths, for example:

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

Add your other test and configuration paths. CODEOWNERS alone does not require code owner review;
turn that on in a repository rule.

## Receipts

New receipts use this layout:

```text
.workorder/receipts/TASK-1/20261114T093000.123456Z-a1b2c3d4e5f6.json
```

Each file is one packed JSON object ending with LF, named by UTC time and the first 12 hex digits of
its hash. Every receipt has `task`, `kind`, `at`, `who` (`WORKORDER_AGENT` when set, else Git
`user.name`), `commit`, `code`, `parents` and `hash` (SHA-256 of the packed object without `hash`).
Optional members, which older receipts may omit:

- Command receipts: `command`, `exit_code`, `valid`. Read-backs: `target`, `output_sha256`. Merges: `ref`.
- `done --no-code-change`: `no_code_change: true` and `no_code_change_base`. `done --refactor`:
  `refactor: true`. `done --needs-review`: `pending_review: true` and `pending_findings` (objects
  with `message` and `label_only`). 0.13.0 reads these; 0.14 applies their review rules.
- Implemented receipts: `implementation_base` and `implementation_paths` (each changed path's mode,
  object type and blob ID, `null` for a deletion), checked against the original commit when
  available. From 0.16, `implementation_syntax`: per Python file, a syntax hash without docstrings,
  so a fresh clone recognizes a squash after docstring-only edits. Without the old blob or this
  map, later docstring changes need fresh evidence.

`parents` lists the task's current heads (receipts nothing follows); the first receipt has none.
Branches add files independently, and the next receipt after a merge follows both heads. Writers
for one task in one clone take a lock in `.git/workorder-locks/`.

Legacy `.workorder/receipts/<ID>.jsonl` and `.notquite/receipts/<ID>.jsonl` chains are still read and
verified; leave them in place. If both exist for a task, one must be a prefix of the other. New
receipts never append to them and the first follows the last legacy hash. `entries()` returns the
legacy chain, then new receipts parents first, then by `at` and hash; the last is the newest, and
each has a read-only `missing` flag for an absent commit.

The checker refuses a hash or filename that does not match the content; a missing parent ("a receipt
this one follows is missing: removed or not yet merged"), invalid parents or cycles; a code
fingerprint that disagrees with its commit; a committed receipt that changed or disappeared (new
files are fine; a legacy ledger must keep its committed text as a prefix); and a ledger without its
task file. It compares the working tree with HEAD, the index under `--staged`, and the base under
`--base REF`, where every commit between is checked too, so deletions committed with hooks skipped
are found. LF to CRLF conversion is allowed. Complete removal of a finished or cancelled task is the
exception described under base checks.

### Repair

`workorder repair --task ID` resolves one complete Git append conflict in a legacy ledger (diff3
markers with an empty base too). It verifies the common prefix and both chains before writing;
ambiguous markers, changed hashes, broken chains and lines after the conflict are refused. It keeps
the prefix and converts each side's extra receipts into files, in order, following the prefix's last
hash. Hashes change because `parents` replaces `previous`; contract references to the old test hashes
still count, and each file keeps the original in `legacy` so HEAD and `--base` verify the move. This
is the only exception to the prefix rule; `--base` still refuses committed edits of the JSONL file in
`REF..HEAD`. It prints counts; stage the prefix and new files to finish.

Anyone who can write files and compute SHA-256 can create a receipt that passes these checks.

## Contracts

```json
{"task": "TASK-3", "criteria": [{"id": "C1", "ask": "...", "critical": true,
  "requires": "REAL", "evidence": [{"provenance": "REAL", "ref": "evidence/run.txt",
  "sha256": "..."}]}]}
```

- `task` names the task; `criteria` is a non-empty list with distinct `id`s, each with `ask`,
  `critical` (true or false), `requires` (`REAL` or `REAL_OR_TEST`) and `evidence`.
- `REAL` evidence: `ref` is a repository file and `sha256` the hash of its bytes. If Git converts
  line endings (`core.autocrlf` on Windows) the hash changes: mark evidence files `-text` in `.gitattributes`.
- `TEST` evidence: `ref` is `receipt:<hash>` of a passing, valid test receipt on the implemented
  code; it counts only for `REAL_OR_TEST`.
- `MOCK`, `SYNTHETIC` and `UNKNOWN` never count.

Only critical criteria block DONE. `REAL` is the author's declaration: a hash identifies a file, not
where it came from. Keep contracts and evidence under `.workorder/` (for example
`.workorder/evidence/`) so evidence does not change the code fingerprint. See [examples](../examples/).

## Claims and ids

All are tags on `origin` pointing at small JSON blobs: `refs/tags/workorder/ids/<id>` (reserved by
`new`), `refs/tags/workorder/claims/<id>/<n>` (claim `n`) and
`refs/tags/workorder/releases/<id>/<claim object>`. Git rejects a push replacing a tag, so the first
clone wins. The claim token stays in `.git/workorder-claims/<id>`, never committed; the tag stores
only its SHA-256 (`token_sha256`). `release` needs the token; if the claiming clone or worktree is
gone, `release --task ID --force` records a forced release.

Without `origin`, `new` numbers ids locally and `claim`, `release` and `claims` refuse. An
unreachable origin makes `new` ask for `--local`. Local numbering uses task files and reservation
tags already fetched; it cannot see reservations this clone never fetched.

`claim` and `ready` fetch origin's main (or master) into the private `refs/workorder/upstream` ref,
leaving the checkout and remote-tracking branches alone. `claim` refuses when upstream has work on
the task absent locally, for example
`origin/main has newer work on TASK-1 (2 receipts, 1 note, by agent-b); pull first` (the author of
the latest commit touching it), when the task is already DONE, CLOSED or CANCELLED upstream, and when
origin cannot be reached. `show` refreshes the ref within three seconds and warns
`origin/main has newer work on this task: pull before you start`. `ready` marks a task whose local
status differs from upstream as `finished on origin/main; pull to update`. Offline, `show` and `ready`
use local records with a note; a stale upstream snapshot is never reused.

`git fetch --tags` downloads these tags; filter out `workorder/*` when choosing a release tag. Tokens
coordinate clones, but anyone with push access can write, force-push or delete tags. Protect
`workorder/*` tags if that matters.

## Compatibility

Existing task files and receipts need no migration. Optional members added since 0.13 (such as
`code_paths` and the verification base) are ignored by older readers.

- NotQuite (PyPI `notquite`) was renamed at 0.3.0: command, package and paths became `workorder`
  (`.workorder/`, `refs/tags/workorder/`, `.git/workorder-claims/`). Committed `.notquite/receipts/`
  ledgers (moved before 0.4 with `git mv .notquite .workorder`) are read in place and stay outside the code fingerprint; `refs/tags/notquite/` is not
  read, and numbering continues from task files.
- Seven-field task files read as in 0.1.0, and `new --template simple` writes the same bytes. 0.1.0
  refuses extended fields, so once a task uses them every clone, hook and workflow pin needs 0.2.0+.
- 0.4 writes receipt files and still reads JSONL chains; upgrade every writer and checker first.
  Switching a task between simple and extended format keeps its receipts valid.
- Since 0.3.0 DONE and CLOSED need a declared `test` (`test: any` keeps the old behaviour), a test
  run counts when its code matches the implemented code on any commit (so a DONE hiding a later
  failing run becomes IN_PROGRESS), and a receipt that cannot
  count exits 1.

Receipts and claims record and push the agent name. Run `export WORKORDER_AGENT=agent-b` to tell
apart agents sharing one Git identity; receipts from 0.2.0 or earlier also keep the hostname.
Rewriting Git history changes the record. Chat, issue and pull request text are not checked. Tasks stay in `tasks/` until removed with reviewer acceptance.

## Line endings

New task files, receipts and claim tokens use LF. Status, next and Progress updates keep the file's
line endings and byte order mark. CRLF checkouts, as Git on Windows may make, read the same.

## Reading show and check results

In `show` and `check ID`, an unclaimed task starts
`TASK-1 IN_PROGRESS (not claimed finished); its test evidence is in place`, or `owes: ...`. A
supported finished task prints
``Evidence: recorded receipts support the claim (not re-run here; `check --run-tests` re-runs it)``
and its computed status. If HEAD differs from the implementation, Result names the implemented
commit the test passed on. A test that does not count shows `(does not count: <reason>)`, even with
exit 0. `show` suggests `workorder done --task ID` for outstanding work, first asking for a concrete
command when the task has `test: any`. A named check says `TASK-1's files are unchanged since <sha>`
when later commits leave its code paths and tests alone.

Before a merge, `merge of the tested code` is owed; advice about differing merged code appears only
when the target changed implementation paths since the merge base. An unsupported claim shows
`Evidence: claim not supported on this code` and a Result such as
`Result: the DONE claim is on code other than the tested code; run workorder renew --task TASK-1`.

## Task-first development

In a clone with an origin remote and the pre-commit hook:

1. Write the task (concrete test command, Scope, Acceptance) before the test exists. Commit and push
   it to the default branch without skipping the hook.
2. On a feature branch, add the failing test and
   `workorder note --task ID --next "<next step>" "<what was done>"`; commit. A failing or missing
   test for unfinished work is only a note.
3. Implement and commit.
4. Run `workorder done --task ID` and commit `tasks/` and `.workorder/`.
5. Push. The pull-request check
   `workorder check --base origin/<base> --run-tests --all-tests --require-task` passes when the
   task was on the base and the evidence supports DONE.
6. Squash-merge without skipping the hook. `workorder check --run-tests --all-tests` on the default
   branch passes when the merged code is the tested code; after a conflicted squash, run
   `workorder renew --task ID` there.

Making a failing base test pass by adding `return`, or a second function of the same name, edits an
existing test: it needs the task's label (`--allow ID`), not an approving review. So does a finished
task whose base tests fail on the new code.

## Team setup

Install with `pipx install workorder`. On Ubuntu or Debian, get pipx with
`sudo apt install pipx && pipx ensurepath`; elsewhere see the
[pipx install guide](https://pipx.pypa.io/stable/how-to/install-pipx.html). Plain `pip install`
outside a virtual environment is blocked on recent Ubuntu.

Run `workorder init` in every clone: hooks are not cloned. It is idempotent, lists the files it wrote
and ends with an exact `git add ... && git commit -m 'Set up Workorder'` command (including a new
`.claude/settings.json` with `--claude`; the local hook is listed but never added). Unchanged setup
prints `No files written.`. `init --check` writes nothing, advises init when the hook is absent and
exits 1 if setup is incomplete. `init --update-workflow` prints a diff and rewrites an existing
workflow (with `--check`, it only previews). `init --light` writes a trial workflow with `--review warn`
and no `--require-task`; `--light --update-workflow` switches to it, and `--update-workflow` restores
the defaults. `--update-workflow --solo` switches to [solo mode](#solo-mode); `--update-workflow` alone leaves it.

A generated workflow installs project test dependencies from the first match, and says which:

1. A `test`, `tests` or `dev` group under `[dependency-groups]`:
   `python -m pip install --upgrade 'pip>=25.1' && python -m pip install --group GROUP .`.
2. A `test` extra in pyproject.toml: `python -m pip install -e '.[test]'`.
3. requirements-dev.txt, otherwise requirements.txt: `python -m pip install -r FILE`.
4. A task command that uses pytest: `python -m pip install pytest`.
5. Otherwise a warning step says `No project test dependencies detected` and installs nothing; add
   what your tests need. The check reports `pytest is not installed` when a command needs it.

`--group` needs pip 25.1; the workflow upgrades pip and fails rather than skipping a group. Otherwise
use `uv pip install --group tests -e .`, or install the group's requirements, then
`python -m pip install -e .`. Add non-Python dependencies yourself.

It then installs Workorder at exactly the running version: a Git install's PEP 610 direct_url.json
gives `python -m pip install 'git+<url>@<commit>'` (with any subdirectory), otherwise
`workorder==0.18.0`. Pull requests run
`workorder check --base "origin/<base>" --run-tests --all-tests --require-task` with labels added by
someone other than the author (task ids, `scope:<ID>`, `tests`, `no-task`, `workflow`) as `--allow`,
and `--approving-review` for a current-head approval. Pushes run `workorder check --run-tests --all-tests`.

The agent block in AGENTS.md and CLAUDE.md is shown in the README. Agents should also work on a
branch, open a pull request, run `workorder release --task ID` when stopping, and after the merge run
`workorder check <ID>` on main.

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
echo "pre-commit: warning: workorder is not on PATH (pipx install workorder, or put the virtualenv that has workorder on PATH); skipping task check" >&2
exit 0
```

To install it by hand, save it as `.git/hooks/pre-commit` and `chmod +x` it.

Without Workorder on PATH the hook warns and exits 0, so teammates can still commit. Otherwise it
runs `check --staged` (silent when nothing relevant is staged). With origin/main or origin/master
available locally, it compares staged tests of the tasks this commit touches, and their dependents,
with the merge base, reruns them and prints test-change notes with label advice. It also runs
finished tasks whose implementation paths or tests differ from the merge base in staged code (older
receipts without paths use their code tree); `done` uses the same scope. Unsupported DONE claims exit
1. Passing output is quiet; failures are shortened, with the full text in
`.git/workorder/last-output.txt` (`WORKORDER_VERBOSE=1` prints everything). The summary counts tasks whose tests ran and marks fully cached comparisons `(cached)`. Temporary copies drop inherited Git variables, so the index stays intact in linked
worktrees. The hook never fetches.

### Solo mode

For a developer working alone with coding agents. `workorder init --solo` records `"solo": true` in
`.workorder/config.json` and writes a workflow that also asks GitHub for the author's permission
(`gh api repos/OWNER/REPO/collaborators/AUTHOR/permission`, read-only token). Without `--solo` nothing changes.
Solo mode applies only when the base branch records it too: a pull request cannot turn it on for itself.

A `workorder-allow:*` label the author added then counts only if GitHub confirms the author as the
repository owner (by account ID in the event) or an admin (`"permission": "admin"` for that account);
an unverifiable answer accepts nothing. It clears what the same label from a reviewer would, reaches
the check as `--owner-accepts` (locally: `check --base REF --owner-accepts TASK-3,tests`), and prints as
owner acceptance, never review:

```text
Note: accepted by the owner (solo mode; no independent review): TASK-3 (new task), tests/test_calc.py (changed lines)
checked 1 task (1 claimed DONE or CLOSED); solo mode: the owner's labels count as acceptance, not independent review
```

Every check still runs and no finding disappears; another person's label or approval still counts
as review. The limit: the owner's labels are the only human step, and an agent acting with the
owner's GitHub credentials could add them, so give agents a token without permission to label pull requests.

## Agent hooks

`workorder init --claude` merges these hooks into `.claude/settings.json`, keeping other settings:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "workorder hook session-start"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "workorder hook stop"}]}]
  }
}
```

SessionStart lists the tasks this clone holds with their `show` records and points to
`workorder ready` without running the full scan. With a remote it re-reads claim and release tags first; if that fails it keeps the
claims already known. A token left by a failed claim is not ownership.

Stop reads the hook JSON from stdin and exits 0 when `stop_hook_active` is true, to prevent a loop.
It checks DONE/CLOSED claims only for tasks this clone holds or whose task file differs from the
merge base with the target (HEAD without one), and exits 2 with the NOT DONE lines on stderr when one
lacks support; other tasks never block it. With a local target it runs the same test-change comparison as pre-commit, using staged
test edits when present. Newly cancelling a held task also refuses, with label advice. Test-file
changes and passing command repairs on finished tasks are notes; tasks without test receipts keep the plain command checks. It checks task records, not chat.
`init --claude --check` reports missing hooks. Codex users rely on the AGENTS.md block.

### Installed Python projects and comparison copies

Tests run with the checkout's `src/` (when present) and root ahead of the inherited `PYTHONPATH`.
For pytest the import probe applies pytest's actual configuration and `pythonpath` (relative to the
selected file, including `-c`), also under `-I` and `-E`, so another checkout's editable install
cannot supply the project. Base-code, old-test, stub and REF runs use the temporary copy's roots.

Before using a copy's result, even a cached one or an inline `python -c`, a probe with the same
interpreter, flags, environment and directory locates the project's own top-level packages and
modules. It also reads installed distributions named like `[project] name` or `[metadata] name`
(PEP 503): `top_level.txt`, `RECORD`, editable finders and .pth roots cover remapped layouts such as
`pkg/unipkg`. Other dependencies may resolve anywhere; Workorder itself can stay editable. `init`
prints a line when its environment finds an installed package for a src layout. An outside import reports:

```text
TASK-1: could not compare with origin/main: the tests import click from /checkout/src/click, outside the comparison copy; add pythonpath = ["src"] under [tool.pytest.ini_options] or install the project non-editable
```

The advice names `src` or `.` only when that layout holds the package; otherwise it says to install
non-editable. `could not compare` also covers failed copies, unavailable base runs and an
unidentified Python interpreter in a wrapper. It proves nothing about the test. An approving review
or `workorder-allow:TASK-1` (`workorder-allow:tests` for an unowned file) accepts it; warning mode and
a base task's kind do not. `done --needs-review` can record it, and pre-commit then allows the commit
with a Note. Current failures still refuse. Pytest exits are explained, such as
`5 (no tests were collected)`. Tests or plugins can still change imports after the probe.

For explicit pytest `.py` files and direct Python scripts, only a mandatory first import of project
packages absent at the base can establish a failing base before the probe; other selections use the
ordinary probe and run.

Each test run gets a temporary pytest cache through `-o cache_dir=...` appended to `PYTEST_ADDOPTS`,
removed afterwards (cleanup errors ignored), so concurrent runs never share `.pytest_cache`. The
result-cache key ignores it.

During a merge (`MERGE_HEAD`), comparisons use the merge base with the incoming parents, so test
changes brought by main are not branch edits. Test edits made on the branch or while resolving the
merge are notes in pre-commit and need the task's label in the pull-request check.

`sh examples/real-projects.sh python` installs Click and attrs editable in a fresh temporary
directory and runs 15 cases: an honest feature, fix and refactor and the assert-True,
edited-expected-value and skip shortcuts in each, plus three Click cases appending to
`tests/test_basic.py` (a callable-only test refused, an honest test reaching DONE, and a second task
adding a test in a finished task's folder without a label). Eight are honest and seven refused. Task
definitions land first. It needs network access, prints one PASS/FAIL line, and runs on Linux with
Python 3.12. It covers these operations, not each project's full suite.
