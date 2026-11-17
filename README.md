# Workorder: the task record for coding agents

**Every task recorded. A clear history. Agents always go back to it.**
[Reviewer guide](docs/reviewer-guide.md) · [Guarantees and limits](docs/guarantees.md) · [Shortcuts Workorder flags](docs/shortcuts.md)

Workorder keeps each task as a record in your repository. A coding agent starts every session from the task, works within it, and leaves it up to date. The next session, or the next agent, picks up where the last one stopped.

Each task is a Markdown file in `tasks/`: its mission, scope, acceptance criteria, dependencies, test command and progress. Receipts in `.workorder/` record the
implementation, commands, results and code they refer to. `workorder show` prints that history. `status: DONE` is a claim; `workorder check` says whether the record
supports it. Acceptance lists still need a person or agent to check them.

Requires Git and Python 3.10 or newer. It uses Python's standard library, with no server or database. Tested on Linux and Windows with Python 3.10, 3.12 and 3.13.

## Install
```sh
pipx install workorder
```

No pipx? On Ubuntu or Debian: `sudo apt install pipx && pipx ensurepath`. Other systems: see the [pipx install guide](https://pipx.pypa.io/stable/how-to/install-pipx.html). Plain `pip install` outside a virtual environment is blocked on recent Ubuntu; use `pipx install workorder` instead.

## Quick start

This uses a throwaway folder and needs no remote. On Windows, use Git Bash and replace `python3` with `python`.

```sh
mkdir demo && cd demo
git init -q -b main
git config user.name "Your Name"
git config user.email you@example.com
workorder init
printf '__pycache__/\n' > .gitignore

workorder new --title "Add calc" --owner you --test "python3 test_calc.py" --code-paths calc.py
```

`new` warns that the test does not exist; this is expected because you write it on the branch. Edit `tasks/TASK-1.md`: replace the template comments under Scope and Acceptance with:

```markdown
## Scope

In: add two numbers with `add(a, b)` in calc.py.
Out: subtraction and other operations.

## Acceptance

- `add(2, 2)` returns `4`.
```

Land the task on main first, as below, then do the work on a branch. A task written
and finished in one pull request always needs a reviewer label or approving review:

```sh
git add . && git commit -qm "Workorder setup and TASK-1"
git switch -c add-calc

printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'from calc import add\n\nassert add(2, 2) == 4\n' > test_calc.py
git add . && git commit -qm "Add calc"
```

Confirm every Acceptance item is met, then:

```sh
workorder done --task TASK-1 --note "add() works; next: subtraction"
workorder show --task TASK-1
git add tasks/ .workorder/ && git commit -qm "Record TASK-1 completion"
```

`done` records implementation when needed, runs the declared test, and sets DONE
only when the evidence supports it. With `ships: none`, `show` prints `computed: CLOSED` for a supported DONE claim. Commit `tasks/` and `.workorder/` with your
work. It never commits or pushes for you. If it refuses, follow its Missing and
Next lines. A reopened DONE/CLOSED task can stay IN_PROGRESS or BLOCKED while being fixed;
if its test still fails, the pull request needs the reviewer's explicit `workorder-allow:<ID>` label. Approval or warning mode is insufficient.
Ordinary tasks need a code change since the target merge base (or task creation).
Use `--no-code-change` only when the task's own code and test files are unchanged. For intentional test edits, `--needs-review` writes DONE pending a reviewer label or approval where allowed. Use `--refactor`
for unchanged tests passing before and after. Refactor and no-code-change completions need `workorder-allow:ID` on the pull request unless the base task already declares the matching `kind: refactor` or `kind: verification`; approval alone is insufficient. `done` and pre-commit print a Note when that label is needed. Hand-written DONE with receipts but no code change follows the verification rule. Plain `renew` keeps a refactor declaration; `renew --no-refactor` drops it. `new --kind feature|fix|refactor|verification` writes the task's kind. Claims are advisory: `note` and `receipt` warn when another clone holds the task, and `done` refuses it unless `--take-over` is given (recorded in Progress); the pull-request check prints a Note for such a finish. `new --code-paths src/calc.py` sets code scope; files outside claiming tasks' scopes need `workorder-allow:scope:ID`. [Task format](docs/reference.md#task-files).

For installed `src/` projects (including `pip install -e .`), test copies verify project imports using pytest's actual configuration and `pythonpath`, including isolated Python commands.
Only the project's own packages must resolve inside the copy; editable dependencies may live elsewhere. Declared code paths and package source (including `src/click/testing.py`) are code unless explicitly in `test_paths`. Configure test name/folder globs in `.workorder/config.json`. Unavailable comparisons need the task label (`tests` for unowned files); pytest exits include their meaning. Documentation and Python docstring-only edits need no code receipts or renewal.

## Working with coding agents

Run `workorder init` in every clone: Git does not clone hooks. It adds the block
below to AGENTS.md and CLAUDE.md, installs the pre-commit hook, and writes a
workflow when one is absent. It lists the files written and an exact commit command; the local hook is listed but never staged.

```markdown
## Tasks
- Start from the task: if the repository has an origin remote, run `git fetch origin`. On main, update with `git pull --rebase origin main`. On a feature branch, merge or rebase main as needed. Use the default branch's name. Run `workorder show --task <id>`; follow its mission, Scope, Acceptance, test, history and progress.
- Set `WORKORDER_AGENT` when sharing a Git identity. Run `workorder claim --task <id>` before work (needs a writable origin), and `workorder release --task <id>` when stopping, finished or not.
- Create tasks with `workorder new --code-paths <files or folders>`. Land the task first, then implement on a branch and open a pull request. A task written and finished in one pull request needs an approving review or `workorder-allow:<id>`.
- Confirm every Acceptance item, commit code, fetch origin if present, then run `workorder done --task <id> --note "<what was done>"`. Workorder checks test evidence, not Acceptance. Report refusals and warnings; do not claim completion after them.
- Use `done --task <id> --refactor` for unchanged tests passing before and after, or `--no-code-change` when the task's code and tests are unchanged. These completions need `workorder-allow:<id>` unless the base task declares the matching `kind: refactor` or `kind: verification`; an approving review does not clear them.
- Use `done --task <id> --needs-review` for intended test edits or eligible comparison findings; report pending review. Ordinary test edits need the task's label or an approving review. A "could not compare", assertion or stub finding needs `workorder-allow:<id>` even with a predeclared kind; scope findings need `workorder-allow:scope:<id>`. Approval does not clear these explicit-label findings. Current test failures still refuse. Refused done writes no notes or receipts; use `workorder note` for handover.
- Keep unfinished progress with `workorder note --task <id> --next "<next step>" "<progress>"`; done clears next. Before stopping, commit tasks/ and .workorder/ with the work and push.
- Renew only when the task's own code or tests changed after DONE: `workorder renew --task <id>`. Commit its receipts. After merge, run `workorder check <id>` on main; renew and test again if needed.
```

`workorder init --claude` installs Claude Code SessionStart and Stop hooks. SessionStart shows held tasks;
Stop checks relevant completion claims and writes refusals to stderr with exit 2. The stop-loop guard exits 0.
[Agent hook details](docs/reference.md#agent-hooks).

## Hooks and workflows

`workorder init --light` writes a trial workflow with `--review warn` and no `--require-task`.
For test-first work, write a failing test, run `new --test-first --test "..."`, and land the task and test on main before implementation.
Use `init --test-first` to add `check --require-test-first`; tests added or changed with the work need the task's explicit reviewer label.

The pre-commit hook checks staged work; finished-task command repairs get review notes if they pass. Pending-review "could not compare" claims can commit with a Note; the pull request still needs the task's explicit label. With a local target, `done` also tests affected finished tasks. Another task's own test edits and recorded findings stop blocking only after its own `done --needs-review` in this change covers its current tests. Other findings still block plain `done`; all appear once in `Other:`. Fetch origin before `done`.

A new or reopened completion must show its change with tests that fail on the target's merge-base code. Renewing a finished claim already on the target does not owe new base-code or stub proof. `--refactor` accepts unchanged tests passing on both versions; `--no-code-change` checks the task's code paths and tests. Without code paths, other tasks' new receipts exclude their recorded changes. A false exemption needs the task label.

New or changed Python test functions need an assertion or helper (including `pytest.warns`); literal-only assertions and `value == value` refuse. For pytest, the new or changed tests run together against base code and against changed functions replaced with bodies that raise an error; at least one must fail in each check. If none changed, the whole command decides. Mixed stub results list passing tests in one Note. Refactor and no-code-change runs are exempt; unusual valid tests can use `done --needs-review` and need the task's explicit reviewer label. [Test checks](docs/reference.md#checks-against-a-base).

Finished tasks allow new Python tests, fixtures, imports and appended parameter cases when existing tests stay unchanged. Existing test edits need the task label or an approving review, including for declared refactors. A pytest command without paths uses `testpaths`, or recognized repository tests; `new` asks for `--test-paths` if none are found.

The default workflow checks pull requests with `--base origin/<base> --run-tests --all-tests --require-task`, and default-branch pushes with `--run-tests --all-tests`. A first `init` that finds pytest tests or configuration turns on the whole-project command `python -m pytest -q` and says so (`init --no-project-test` turns it off); set another with `workorder init --project-test "python -m pytest"`. `done` and `check --run-tests` run it, pre-commit does not. Without one, output says only declared task tests ran.
`check/done --jobs N` runs distinct current commands concurrently, defaulting to the CPU count; output stays in task order. Identical commands and timeouts share one run. If the task's own code or test files changed, run `workorder renew --task <id>`. Renewal preserves a refactor declaration.
Generated Python metadata and caches, and lockfiles, need no task evidence. Repeat `check --require-task-ignore GLOB` for other path exceptions; these affect only the code-to-task requirement.

An APPROVED current-head review by someone other than the author accepts ordinary review findings. Old-test failures, assertion findings, tests that pass with changed functions replaced, test-first exceptions, reopened tasks with failing tests, unavailable comparisons, scope findings, workflow edits and refactor/verification exemptions not predeclared on the base need explicit labels.

Only `workorder-allow:scope:<ID>` accepts scope findings; `workorder-allow:<ID>` accepts that task's reviewable test findings; `workorder-allow:tests` accepts changed test lines; `workorder-allow:no-task` accepts only the code-to-task requirement. `workorder-allow:workflow` accepts changes to the default or renamed generated workflow.
Shared test and configuration files produce one finding naming their owners. Current failures for completed tasks and runs reporting no passed tests still refuse. Author-added labels are ignored. [Review rules](docs/reference.md#checks-against-a-base).

`init --check` reports missing setup without changing it. `init --update-workflow` shows a diff and refreshes the workflow. `--force` keeps a different hook as `pre-commit.workorder-previous` and calls it first.
Generated workflows install detected test dependencies and pin the running Workorder version or installed Git commit. With no detected test dependencies, they install no extra test tools and print a warning. [Team setup](docs/reference.md#team-setup).
Existing task files and receipts need no migration. Before a squash, the pull-request check requires a task finished in that change to renew if its code changed after completion; `done` and pre-commit print that advice as a Note. Local task IDs respect reservation tags already fetched.

[Full reference](docs/reference.md) · [Real-project proof](examples/real-projects.sh) · [Examples](examples/) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
