# Workorder: the task record for coding agents

**Every task recorded. A clear history. Agents always go back to it.**

[Guarantees and limits](docs/guarantees.md) · [Shortcuts Workorder refuses](docs/shortcuts.md)

Workorder keeps each task as a record in your repository. A coding agent starts every session from the task, works within it, and leaves it up to date. The next session, or the next agent, picks up where the last one stopped.

Each task is a Markdown file in `tasks/`: its mission, scope, acceptance criteria,
dependencies, test command and progress. Receipts in `.workorder/` record the
implementation, commands, results and code they refer to. `workorder show` prints
that history. `status: DONE` is a claim; `workorder check` says whether the record
supports it. Acceptance lists still need a person or agent to check them.

Requires Git and Python 3.10 or newer. It uses Python's standard library, with
no server or database. Tested on Linux and Windows with Python 3.10, 3.12 and 3.13.

## Install

```sh
pipx install workorder
```

No pipx? On Ubuntu or Debian: `sudo apt install pipx && pipx ensurepath`. Other systems: see the [pipx install guide](https://pipx.pypa.io/stable/how-to/install-pipx.html). Plain `pip install` outside a virtual environment is blocked on recent Ubuntu; use `pipx install workorder` instead.

## Quick start

This uses a throwaway folder and needs no remote. On Windows, use Git Bash and replace `python3` with `python`.

```sh
pipx install workorder
mkdir demo && cd demo
git init -q -b main
git config user.name "Your Name"
git config user.email you@example.com
workorder init
printf '__pycache__/\n' > .gitignore

workorder new --title "Add calc" --owner you --test "python3 test_calc.py"
```

Edit `tasks/TASK-1.md`: replace the template comments under Scope and Acceptance with:

```markdown
## Scope

In: add two numbers with `add(a, b)` in calc.py.
Out: subtraction and other operations.

## Acceptance

- `add(2, 2)` returns `4`.
```

Commit the task, then do the work and commit it:

```sh
git add . && git commit -qm "Workorder setup and TASK-1"

printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'from calc import add\n\nassert add(2, 2) == 4\n' > test_calc.py
git add . && git commit -qm "Add calc"
```

Confirm every Acceptance item is met, then:

```sh
workorder done --task TASK-1 --note "add() works; next: subtraction"
workorder show --task TASK-1
```

`done` records implementation when needed, runs the declared test, and sets DONE
only when the evidence supports it. Commit `tasks/` and `.workorder/` with your
work. It never commits or pushes for you. If it refuses, follow its Missing and
Next lines; use IN_PROGRESS while fixing a task that was previously DONE.
Ordinary tasks need a code change since the target merge base (or task creation).
Use `--no-code-change` for verification tasks. Intentional test edits can finish
with `--needs-review`; tell the reviewer the `workorder-allow:<ID>` label is needed.
[Task format and command details](docs/reference.md#task-files).

## Working with coding agents

Run `workorder init` in every clone: Git does not clone hooks. It adds the block
below to AGENTS.md and CLAUDE.md, installs the pre-commit hook, and writes a
workflow when one is absent. Existing instructions stay in place.

```markdown
## Tasks
- Start every session from the task: if the repository has an origin remote, run `git fetch origin`. On main, update with `git pull --rebase origin main`. On a feature branch, merge or rebase main as needed, then run `workorder renew --task <id>` if the tested code changed. Use the repository's default branch name if it is not main. Then run `workorder show --task <id>` and work from what it prints: the mission and Scope say what to do and what not to; the Acceptance list says how the work will be accepted; `test` is the command that proves it; the history and progress say where the last session stopped.
- Before starting, run `workorder claim --task <id>` (needs an origin remote you can push to). Run `workorder release --task <id>` when you stop working on it, finished or not.
- Work on a branch and open a pull request for review.
- After committing code and confirming every Acceptance item is met, run `workorder done --task <id> --note "<what was done; what is next>"`. It records implementation when needed, runs the declared test, and sets DONE only when its check supports the claim. If `done` refuses or prints a warning, report it; do not say the task is done. Acceptance lists are not checked by Workorder.
- If `done` refuses because you changed tests on purpose, rerun with `--needs-review` and tell the user the pull request needs the label `workorder-allow:<id>`.
- Before you stop, record progress and the next step with `workorder note --task <id> --next "<next step>" "<what was done>"`, then commit tasks/ and .workorder/ with your work and push.
- After merge, run `workorder check <id>` on main; if the merged code differs, run `workorder renew --task <id>` there and test again.
```

`workorder init --claude` also installs Claude Code SessionStart and Stop hooks.
SessionStart shows held tasks; Stop checks relevant completion claims and writes
refusals to stderr with exit 2. The stop-loop guard exits 0.
[Agent hook details](docs/reference.md#agent-hooks).

## Hooks and workflows

The pre-commit hook checks staged work. When a remote target is available
locally, local checks compare tests with its merge base and report review notes. The generated workflow checks
pull requests with `--base origin/<base> --run-tests --all-tests --require-task`
and default-branch pushes with `--run-tests --all-tests`. A conflicted squash
can leave main with code that differs from the tested code, so the check on
main can fail. Run `workorder renew --task <id>` on main and test again.

Review includes repository tests and every task whose tests changed. A task
owns the files or folders its test names, plus pytest configuration on that
path. New test files and added Python import names that keep existing bindings
pass without a label. `workorder-allow:<ID>` accepts that task's files. `workorder-allow:tests`
accepts every changed test line. `workorder-allow:no-task` accepts only the
code-to-task requirement. Current failures for completed tasks still refuse.
[Review rules and the full workflow](docs/reference.md#checks-against-a-base).

`init --check` reports missing setup without changing it. `init --update-workflow`
shows a diff and refreshes the workflow. `--force` keeps a different hook as
`pre-commit.workorder-previous` and calls it first.
Generated workflows install detected test dependencies and pin the running
Workorder version or installed Git commit. With no detected test dependencies,
they install no extra test tools and print a warning. [Team setup](docs/reference.md#team-setup).

[Full reference](docs/reference.md) · [Examples](examples/) ·
[Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
