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
Next lines; use IN_PROGRESS while fixing a task that was previously DONE.
Ordinary tasks need a code change since the target merge base (or task creation).
Use `--no-code-change` only when no code files changed. For intentional test edits,
`--needs-review` writes DONE pending a reviewer label or approval. Use `--refactor`
for unchanged tests passing before and after. `new --code-paths src/calc.py` sets code scope. [Task format](docs/reference.md#task-files).

## Working with coding agents

Run `workorder init` in every clone: Git does not clone hooks. It adds the block
below to AGENTS.md and CLAUDE.md, installs the pre-commit hook, and writes a
workflow when one is absent. Existing instructions stay in place.

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

The pre-commit hook checks staged work; finished-task command repairs get review notes if they pass. With a local target, `done`
also tests affected finished tasks. Fetch origin before `done`. A completed task's
test must fail on the target's merge-base code with the current test files.
`--refactor` accepts unchanged tests passing on both versions; `--no-code-change`
exempts verification work with no code edits. Python test functions need an assertion or an assertion helper;
literal-only expressions refuse. An unusual valid test needs its task label; see the reference for helper rules.
The default workflow checks pull requests with
`--base origin/<base> --run-tests --all-tests --require-task`, and default-branch
pushes with `--run-tests --all-tests`. Each distinct command runs once per code
version and timeout. If merged code differs, run `workorder renew --task <id>`.

An APPROVED review on the current head by someone other than the author accepts review findings, except old-test failures, assertion findings and test-first exceptions, which need explicit labels.
Review includes repository tests and every task whose tests changed. A task
owns the files or folders its test names, plus pytest configuration on that path.
New tests, harmless Python assertion edits and added import names need no
label when they check the new behaviour. `workorder-allow:<ID>` accepts that task's
findings; `workorder-allow:tests` accepts changed test lines; `workorder-allow:no-task`
accepts only the code-to-task requirement. Current failures for completed tasks
and runs reporting no passed tests still refuse. Labels added by the pull request's author are ignored; the workflow
reads label events to identify who added them. [Review rules](docs/reference.md#checks-against-a-base).

`init --check` reports missing setup without changing it. `init --update-workflow` shows a diff and refreshes the workflow. `--force` keeps a different hook as `pre-commit.workorder-previous` and calls it first.
Generated workflows install detected test dependencies and pin the running Workorder version or installed Git commit. With no detected test dependencies, they install no extra test tools and print a warning. [Team setup](docs/reference.md#team-setup).

[Full reference](docs/reference.md) · [Examples](examples/) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
