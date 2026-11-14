# Reference

Details behind the [README](../README.md). Everything here is read from the
code in `workorder/`; if the two disagree, the code wins and this file has a bug.

Install: `pipx install workorder`.

## Commands

| Command | What it does |
|---|---|
| `workorder init [--check] [--force] [--claude]` | Adds the README agent block once to AGENTS.md and CLAUDE.md, installs the hook, and writes the workflow if absent. A different hook needs --force; --check reports missing setup without writes and exits 1 if incomplete. --claude merges project hooks into .claude/settings.json |
| `workorder done --task ID [--note TEXT] [--timeout SECONDS]` | Refuses uncommitted code or empty Scope/Acceptance; records implementation if its code changed and runs the declared test. Prints Acceptance for human confirmation, then sets DONE only if supported. On refusal prints Missing lines and preserves status; never commits or pushes |
| `workorder new --title T --owner O [--ships none\|repo\|live] [--prefix TASK] [--template extended\|simple] [--test COMMAND] [--local]` | Reserves the next id on `origin`, or numbers it locally when there is no `origin` remote or with `--local` (no tag is pushed); writes `tasks/<id>.md` (extended by default; `simple` writes the seven required fields only). `ships` defaults to `none` |
| `workorder receipt implemented --task ID` | Records the current commit as the implementation; refuses uncommitted or untracked code |
| `workorder receipt test --task ID [--timeout SECONDS] [-- CMD...]` | Runs `CMD` (no shell), records its exit code against the current commit |
| `workorder receipt merged --task ID [--ref refs/heads/main]` | Fetches the branch from `origin` and records the commit it points to (needs `origin`) |
| `workorder receipt deployed --task ID --target NAME -- CMD...` | Runs a read-back command; valid only if it exits 0 and its output contains the full commit ID |
| `workorder receipt live --task ID --target NAME -- CMD...` | Same, recorded after the deployment |
| `workorder check [ID or tasks/ID.md]` / `check --task ID` | No argument: reads every task; one line per problem, or `check: OK`. With a task: an explanation of that task alone |
| `workorder check --staged` | For the pre-commit hook: checks the staged versions of the tasks this commit touches (task file, receipts, contract or its evidence) and the tasks that depend on them; refuses a commit that newly claims DONE or CLOSED on code other than the implemented code; notes changes to a task's requirements |
| `workorder check --base REF [--allow IDS] [--run-tests] [--strict-tests] [--all-tests] [--require-task] [--timeout SECONDS]` | Checks changed task records and receipts, their test files, and their dependents. Refuses stale claims, changed receipts, changed requirements, weakened tests, new finished definitions, and removed tasks. `--allow` accepts reviewed definition/test changes, REF failures and outcome changes, and complete removal of finished or cancelled tasks. `--run-tests` reruns the declared tests of supported claims; see [base checks](#checks-against-a-base) |
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
why). Every command resolves the repository root, so commands also work from subdirectories. `--all-tests` requires `--base --run-tests`; `--strict-tests` and `--require-task` require `--base`.

## Task files

Only files named `tasks/<id>.md` are task records; other files in `tasks/`
(a README, notes, subfolders) are ignored. A task file starts with front
matter between two `---` lines, one
`key: value` per line, each key once. These seven fields are required: `id`,
`title`, `owner`, `status`, `ships`, `waits_on`, `contract`.

A value is plain single-line text, a JSON string, or a list. `note` appends a line under Progress. `done` changes the status line to DONE after checking the claim, and `done --note` also appends a dated line. Other task text is preserved. `note --next "TEXT"` replaces or adds the next field, including existing folded/literal values; an empty string clears it. With no NOTE it adds no Progress entry. `done --note` keeps the handover line on every refusal, including uncommitted code or a missing declared test. Refusal says `TASK-1: DONE was not written; the status stays IN_PROGRESS`, using the actual status, then the Missing lines. `done` refuses a missing or empty Scope or Acceptance section, including sections containing only HTML template comments: `fill in Scope and Acceptance first`. It checks this before recording evidence. It preserves the status line's plain, single-quoted or double-quoted style, and its existing line endings. Success says `TASK-1 DONE: its declared test passed on this code`, followed by `; still owes: merge of the tested code` when applicable (and any other delivery debt). A separate line asks you to commit tasks/ and .workorder/, adding `and push` only with an origin remote. It warns when this clone has no pre-commit hook. If origin/main (or origin/master) exists locally, it runs the test-change checks below against that ref before writing DONE and prints refusals as warnings. It does not fetch; these warnings do not prevent DONE when the current declared test and other evidence pass. `done` needs a concrete declared command; use the individual receipts for `test: any`. A task
file that cannot be read is reported as a problem of that task; the other tasks
are still checked. Fields starting with `x-` (such as `x-issue`) are your own:
they are kept and never checked.

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
is finished and no `person:` wait remains. A task dependency is finished when
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

The verdict looks only at the newest `implemented` receipt, never at HEAD. A
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

`check --base REF` checks changed task records, receipts, contracts and evidence,
and their dependents. Test review applies only to tasks this change works on:
their task file or receipts differ from REF. Other tasks get current test runs
through `--all-tests`; changes to shared tests alone do not select them for review.

| Change or result for a task this change works on | Default | What `--allow ID` accepts |
|---|---|---|
| Only added test lines or new test files | Passes without a label | Not needed; with `--strict-tests`, accepts additions too |
| Removed or changed test lines, including a rename | Needs review | The reviewer has read and accepted those edits |
| REF tests fail on current code | Needs review | An intended behavior change or accepted test rename |
| Fewer pytest passes, or more skips, xfails or deselections | Needs review | The changed test outcomes |
| New skip/xfail markers or calls, `-k`/`--deselect`, conftest or pytest configuration changes | Needs review | Those changes to what runs |
| Current declared tests fail | Refused | Nothing; current tests must still pass |
| `--all-tests` finds another task's current test failing | Refused | Nothing; other tasks' checks stay active |

With `--run-tests`, every task this change works on with a concrete command runs
its current tests, including unfinished tasks. Its REF tests run over current
code in a temporary copy, even if the test files did not change. Added files are
absent in the REF copy; deleted files are restored, and it uses the REF command.
The checkout is never overlaid. If the task or its selected tests did not exist
at REF, there is no REF run. Every skipped comparison prints a note explaining
why, including unavailable commands, copy failures and missing reference files.
A comparison that cannot start or whose pytest outcomes cannot be read still
refuses; `--allow` cannot supply missing results. A command that runs and fails,
including a timeout, can be accepted as a REF-test failure. The current command
must pass, including the strict-mode rule below.

For example, an intentional change from 10% to 20% needs acceptance of the
edited assertions and failing REF assertions for each task being worked on:

```text
TASK-1: its tests from origin/main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
```

`test_paths` selects files or folders; otherwise paths named in `test` are used.
Pytest with no collection paths uses its discovery patterns. Both REF and current
definitions count. A named file that does not exist in either version selects no
files and prints a note; it never expands to unrelated repository tests.
An explicit empty `test_paths` list also selects no test files.

Pytest configuration covers conftest.py, pytest.ini, .pytest.ini, pytest.toml,
.pytest.toml, tox.ini, setup.cfg and pytest sections of pyproject.toml, plus custom
configurations selected by the command. Unrelated TOML sections need no review.
Without `--run-tests`, the file, configuration and selection checks still apply;
Workorder notes that it has not run the REF comparison. A passing test says
nothing about whether its assertion expresses the right requirement.

Scope or Acceptance removals and rewording need `--allow ID`. Additions need it
too when the task was DONE or CLOSED at REF; filling in an open task does not.

A task file absent at the base but claimed DONE or CLOSED in this change
needs acceptance of its definition:

```text
TASK-4 was created and claimed DONE in the same change; a reviewer accepts its definition with --allow TASK-4
```

Removing a task file is refused by default, even if it has no receipts. With
`--allow ID`, a task that was DONE, CLOSED or CANCELLED at the base may be
removed together with all its receipts in both receipt folders. Partial
removal, removal of an unfinished task, and changes to receipts of a retained
task are still refused. Dependents must still have valid dependencies.

The check also walks every commit in `REF..HEAD` and refuses receipt files
deleted or modified there, naming the paths in either receipt directory.
This includes receipts first added after REF and edits later restored, which
comparing the base and HEAD alone cannot detect. The complete task removal
exception above also exempts that task's receipts from this history check.

`--allow` takes comma-separated ids. It accepts definition changes and the
complete removal described above. It does not excuse missing evidence,
unfinished dependencies, changed receipts on a retained task, or current test failures.

Every check mode prints a summary after its result and notes, except `--staged` with nothing in scope, which prints nothing:

```text
checked 3 tasks (2 claimed DONE or CLOSED); ran 2 declared tests
```

The test count appears only with `--run-tests`. It counts commands actually
started, including failures and REF comparison runs. A command that could not start does not count.
If none ran, another line says `no declared test ran in this check`. A task
with `test: any` has no declared command to rerun. Tasks this change works on get both runs when REF tests exist. With `--all-tests`, every DONE/CLOSED task has its declared test run on the current code, once per distinct command and effective timeout, including tasks outside the selected history checks. Accepted edits and REF failures do not disable these runs. Counts describe the
selected tasks, including unreadable or removed tasks; unreadable claims are
not included in the claim count. A single-task check counts that task only;
an unfinished task's summary says `checked 1 task (not claimed finished)`.
Supported claims without a contract also print: `Acceptance lists are not checked; N supported claims rest on their declared tests only.` This counts supported DONE/CLOSED claims only, not unfinished tasks or refused claims.
Exit codes are unchanged: 0 for acceptance, 1 for refusal.

`--all-tests` costs the runtime of every distinct declared command, including work finished long ago; tasks this change works on also need REF comparison runs. It catches current regressions that receipt history alone cannot reveal. New workflows enable it by default; large projects can choose narrower checks deliberately.

`--run-tests` prints a note when pytest reports only skipped tests: `TASK-2: its test ran, but reported only skipped tests`. It does not also say the test passed. With `--strict-tests --run-tests`, pytest summaries with no passed tests fail: `TASK-3: its test ran but no test passed (2 skipped)` or `(no tests ran)`. Command failures still fail normally. Tests run without a shell and these checks write no receipts.

`check --base REF --run-tests --all-tests` runs every DONE/CLOSED task's declared command once per distinct command on current code. Failures print `regression: TASK-1's test fails on this code` and exit 1; computed history is unchanged. `check --base REF --require-task` requires at least one new `implemented` or `test` receipt when code changes. A Progress note, status edit, new task definition, or a merge/deployment receipt alone is insufficient. It excludes `tasks/`, `.workorder/`, `.notquite/`, `docs/`, `.github/`, `*.md`, `*.rst`, `*.txt` and `LICENSE*` paths from this code-change rule. This does not change the code fingerprint used by receipts. Both flags are enabled in newly generated workflows.

### GitHub Actions

The hook and plain local checks judge recorded evidence. This workflow runs
the declared tests again. It reads `workorder-allow:<ID>` labels from the pull
request event and passes their ids to `--allow`. Anyone with triage rights can apply these labels. Protect acceptance with CODEOWNERS and required code owner review on `tasks/`, test files, and test configuration paths; the [README label example](../README.md#hooks-and-workflows) shows the entries. CODEOWNERS alone does not require review. Label text is read as data;
it is never inserted into shell code.

```yaml
name: Workorder
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
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
      - run: python -m pip install workorder==0.8.0
      - name: Check tasks and run their tests
        env:
          BASE: origin/${{ github.base_ref }}
        run: |
          # task ids from pull request labels named workorder-allow:<ID>
          allow=$(jq -r '[.pull_request.labels[].name | select(test("^workorder-allow:[A-Z][A-Z0-9]*-[1-9][0-9]*$")) | sub("^workorder-allow:"; "")] | join(",")' "$GITHUB_EVENT_PATH")
          workorder check --base "$BASE" --run-tests --all-tests --require-task ${allow:+--allow "$allow"}
```

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

## Line endings

New task files, receipts and claim tokens use LF. Updates to status, next and Progress preserve the task file's existing line endings and byte order mark using byte writes. Files read
back with CRLF, as Git on Windows may check them out, are treated the same.

## Reading show and check results

In both `show` and `check ID`, unclaimed tasks start with `TASK-1 IN_PROGRESS (not claimed finished); its test evidence is in place`, or `owes: ...`. Their evidence is not labelled DONE or CLOSED. Claimed tasks retain their Claimed and Evidence statuses. A timeline test that does not count includes `(does not count: <reason>)`, even if the command exited 0. For implementation, tests or Acceptance work, `show` suggests `workorder done --task ID`. With `test: any`, that advice asks for a concrete command first; individual receipts remain available for tasks keeping `any`.

Before a merge, `merge of the tested code` is owed. Advice about differing merged code is used only if the target has commits since the merge base touching implementation paths. A stale claim does not change the recorded evidence's computed state; its Result names the mismatch, for example: `Result: the DONE claim is on code other than the tested code; run workorder renew --task TASK-1`.

## Team setup

Run `workorder init` in every clone: hooks are not cloned. It is idempotent and
prints a reminder when instructions are committed but the hook is absent.
Existing workflows are left untouched. A generated workflow installs project
test dependencies first, choosing and reporting the first match:

1. A `test` extra in pyproject.toml: `python -m pip install -e '.[test]'`.
2. requirements-dev.txt, otherwise requirements.txt: `python -m pip install -r FILE`.
3. A task command that uses pytest: `python -m pip install pytest`.
4. Otherwise no inferred test dependency install; init says so.

It then installs Workorder at exactly the running version. For a Git installation,
PEP 610 direct_url.json supplies the URL and resolved commit:
`python -m pip install 'git+<url>@<commit>'`. A recorded subdirectory is retained.
Other installations use `workorder==0.8.0` for this release.
It runs `workorder check --base "origin/<base>" --run-tests --all-tests --require-task`,
passing task ids from `workorder-allow:<ID>` labels to `--allow`. See the runtime
trade-off above. Install additional non-Python dependencies in the workflow as
needed. The agent block says to work on a branch, open a pull request, and after
the merge run `workorder check <ID>` on main.

The pre-commit hook prints one warning when Workorder is missing from PATH and
exits 0, so teammates without it can still commit. When Workorder becomes available it runs `check --staged`; empty staged
checks print nothing. The README includes the installed agent block and examples.

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
line pointing to `workorder ready`. It does not run the full readiness scan. It reads local claim tags and private claim tokens;
successful claims and releases cache their tags locally for these hooks. A token
left by a failed claim is not ownership. It does not fetch, so another clone's
release or reassignment becomes visible after the local tags are refreshed. Stop reads the hook JSON from stdin. When `stop_hook_active`
is true it exits 0 to prevent a stop loop. Otherwise it checks DONE/CLOSED claims only for tasks held by this clone or
whose task file differs from the selected target branch. With no target, it compares task files with HEAD. Other tasks never block Stop. It exits 2
with the NOT QUITE refusal lines on stderr if one of those claims lacks support. A valid claim or unfinished work exits 0. This checks task records;
it cannot detect a completion claim made only in chat.

`init --claude --check` reports missing hook setup without writing it. Codex users
keep the AGENTS.md block; it uses `workorder note --task ID --next "TEXT" "NOTE"`
to leave the next action and progress together.
