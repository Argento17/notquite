# Workorder: the task record for coding agents

**Every task recorded. A clear history. Agents always go back to it.**
[Guarantees and limits](docs/guarantees.md) · [Shortcuts Workorder flags](docs/shortcuts.md)

Workorder keeps each task as a record in your repository. A coding agent starts every session from the task, works within it, and leaves it up to date. The next session, or the next agent, picks up where the last one stopped.

Each task is a Markdown file in `tasks/`: its mission, scope, acceptance criteria,
dependencies, test command and progress. Receipts in `.workorder/` record the
implementation, commands, results and code they refer to. `workorder show` prints
that history. `status: DONE` is a claim; `workorder check` says whether the record
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

Edit `tasks/TASK-1.md`: replace the template comments under Scope and Acceptance with:

```markdown
## Scope

In: add two numbers with `add(a, b)` in calc.py.
Out: subtraction and other operations.

## Acceptance

- `add(2, 2)` returns `4`.
```

Land the task on main first, as below, then do the work on a branch. If task and
code share a pull request, no label is needed when its test fails before the change and passes after:

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
only when the evidence supports it. Commit `tasks/` and `.workorder/` with your
work. It never commits or pushes for you. If it refuses, follow its Missing and
Next lines. A reopened DONE/CLOSED task can stay IN_PROGRESS or BLOCKED while being fixed;
if its test still fails, the pull request needs the reviewer's explicit `workorder-allow:<ID>` label. Approval or warning mode is insufficient.
Ordinary tasks need a code change since the target merge base (or task creation).
Use `--no-code-change` only when no code files changed. For intentional test edits,
`--needs-review` writes DONE pending a reviewer label or approval. Use `--refactor`
for unchanged tests passing before and after. Plain `renew` keeps that declaration; `renew --no-refactor` drops it. `new --code-paths src/calc.py` sets code scope. [Task format](docs/reference.md#task-files).

For installed `src/` projects (including `pip install -e .`), copies put `src/` and the copy root first on `PYTHONPATH` and verify package locations before comparing.

## Working with coding agents

Run `workorder init` in every clone: Git does not clone hooks. It adds the block
below to AGENTS.md and CLAUDE.md, installs the pre-commit hook, and writes a
workflow when one is absent. It lists the files written and an exact commit command; the local hook is listed but never staged.

```markdown
## Tasks
- Start every session from the task: if the repository has an origin remote, run `git fetch origin`. On main, update with `git pull --rebase origin main`. On a feature branch, merge or rebase main as needed, then run `workorder renew --task <id>` if the tested code changed. Use the repository's default branch name if it is not main. Then run `workorder show --task <id>` and work from what it prints: the mission and Scope say what to do and what not to; the Acceptance list says how the work will be accepted; `test` is the command that proves it; the history and progress say where the last session stopped.
- Before starting, run `workorder claim --task <id>` (needs an origin remote you can push to). Run `workorder release --task <id>` when you stop working on it, finished or not.
- When creating a task, use `workorder new --code-paths <files or folders>` to set its code scope. Work on a branch and open a pull request. Land the task on main first, or include it with code whose test fails on the merge-base code and passes now; that new task needs no label. Otherwise a reviewer must accept the new definition with `workorder-allow:<id>` or an approving review.
- After committing code and confirming every Acceptance item is met, fetch origin before `done` when there is an origin remote: `git fetch origin`. Then run `workorder done --task <id> --note "<what was done; what is next>"`. It records implementation when needed, runs the declared test on current and target code, checks affected finished tasks, and sets DONE only when its check supports the claim. If `done` refuses or prints a warning, report it; do not say the task is done. Acceptance lists are not checked by Workorder.
- For a refactor with unchanged test files that pass before and after, use `done --task <id> --refactor`. For intentional test changes, use `done --task <id> --needs-review`. It writes DONE with a pending review receipt; the pull request still needs `workorder-allow:<id>` or an approving review. Report the pending review to the user. A refused `done` writes no Progress note or receipts; use `workorder note` to leave a handover while fixing it.
- Before you stop, record progress and the next step with `workorder note --task <id> --next "<next step>" "<what was done>"`, then commit tasks/ and .workorder/ with your work and push.
- After merge, run `workorder check <id>` on main; if the merged code differs, run `workorder renew --task <id>` there and test again.
```

`workorder init --claude` installs Claude Code SessionStart and Stop hooks. SessionStart shows held tasks;
Stop checks relevant completion claims and writes refusals to stderr with exit 2. The stop-loop guard exits 0.
[Agent hook details](docs/reference.md#agent-hooks).

## Hooks and workflows

`workorder init --light` writes a trial workflow with `--review warn` and no `--require-task`.
For test-first work, write a failing test, run `new --test-first --test "..."`, and land the task and test on main before implementation.
Use `init --test-first` to add `check --require-test-first`; tests added or changed with the work need the task's explicit reviewer label.

The pre-commit hook checks staged work; finished-task command repairs get review notes if they pass. With a local target, `done` also tests finished tasks whose recorded code or tests changed. Fetch origin before `done`.

A completed task's test must fail on the target's merge-base code when its own code or tests changed. `--refactor` accepts unchanged tests passing on both versions; `--no-code-change` exempts work with no code edits. Each exemption remains a separate task note.

New or changed Python test functions need an assertion or helper (including `pytest.warns`); literal-only assertions and `value == value` refuse. Tests must also fail when all changed Python functions are replaced with bodies that raise an error in a temporary copy. Refactor and no-code-change runs are exempt; unusual valid tests need the task's explicit reviewer label. [Test checks](docs/reference.md#checks-against-a-base).

The default workflow checks pull requests with `--base origin/<base> --run-tests --all-tests --require-task`, and default-branch pushes with `--run-tests --all-tests`.
`check/done --jobs N` runs distinct current commands concurrently, defaulting to the CPU count; output stays in task order. Identical commands and timeouts share one run. If merged code differs, run `workorder renew --task <id>`. Renewal preserves a refactor declaration.
Lockfiles need no task evidence. Repeat `check --require-task-ignore GLOB` for other path exceptions; these affect only the code-to-task requirement.

An APPROVED current-head review by someone other than the author accepts ordinary review findings. Old-test failures, assertion findings, tests that pass with changed functions replaced, test-first exceptions, reopened tasks with failing tests and workflow edits need explicit labels.

`workorder-allow:scope:<ID>` (or an approving review) accepts scope findings; `workorder-allow:<ID>` accepts that task's test findings; `workorder-allow:tests` accepts changed test lines; `workorder-allow:no-task` accepts only the code-to-task requirement. `workorder-allow:workflow` accepts changes to the default or renamed generated workflow.
Shared test and configuration files produce one finding naming their owners. Current failures for completed tasks and runs reporting no passed tests still refuse. Author-added labels are ignored. [Review rules](docs/reference.md#checks-against-a-base).

`init --check` reports missing setup without changing it. `init --update-workflow` shows a diff and refreshes the workflow. `--force` keeps a different hook as `pre-commit.workorder-previous` and calls it first.
Generated workflows install detected test dependencies and pin the running Workorder version or installed Git commit. With no detected test dependencies, they install no extra test tools and print a warning. [Team setup](docs/reference.md#team-setup).
Existing 0.14 receipts need no migration. Local task IDs respect reservation tags already fetched; missing test-origin evidence never claims that a test landed earlier.

[Full reference](docs/reference.md) · [Real-project proof](examples/real-projects.sh) · [Examples](examples/) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
