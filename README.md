# Workorder: the task record for coding agents

Every task is a Markdown file in `tasks/` that agents and people read and write: its mission, scope, acceptance, test command and progress, with receipts in `.workorder/`.
Together they give a clear history; the next session, or the next agent, picks up from it.
`workorder done` refuses a DONE that the evidence does not support: the task's test must pass now and fail on the code before the change.

![Comic: a coding agent says DONE and Workorder refuses until the task's test passes; then the next session picks up where the work left off from `workorder show`](https://raw.githubusercontent.com/Argento17/workorder/main/docs/workorder-demo.gif)

[Reviewer guide](docs/reviewer-guide.md) · [Reference](docs/reference.md) · [Guarantees and limits](docs/guarantees.md) · [Shortcuts Workorder flags](docs/shortcuts.md)

Needs Git and Python 3.10 or newer; no server or database. Tested in CI on Linux and Windows (Python 3.10, 3.12, 3.13).

## Quick start

```sh
pipx install workorder
```

No pipx? On Ubuntu or Debian: `sudo apt install pipx && pipx ensurepath`; plain `pip install` outside a virtual environment is blocked there.

Try it in a throwaway folder:

```sh
mkdir demo && cd demo && git init -q -b main
git config user.name "Your Name" && git config user.email you@example.com
workorder init
printf '__pycache__/\n' > .gitignore
workorder new --title "Add calc" --owner you --test "python3 test_calc.py" --code-paths calc.py
```

`new` warns that the test does not exist yet. In `tasks/TASK-1.md`, replace the comments under Scope and Acceptance:

```markdown
## Scope

In: add two numbers with `add(a, b)` in calc.py.
Out: subtraction and other operations.

## Acceptance

- `add(2, 2)` returns `4`.
```

Land the task on main, then do the work on a branch:

```sh
git add . && git commit -qm "Workorder setup and TASK-1"
git switch -c add-calc
printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'from calc import add\n\nassert add(2, 2) == 4\n' > test_calc.py
git add . && git commit -qm "Add calc"
workorder done --task TASK-1 --note "add() works; next: subtraction"
workorder show --task TASK-1
git add tasks/ .workorder/ && git commit -qm "Record TASK-1 completion"
```

`done` runs the declared test and sets DONE only when the evidence supports it, as the pull-request check would. If it refuses, follow its Missing and Next lines. It never commits or pushes.
If the task's own code or test files changed, run `workorder renew --task <id>`.

On a pull request, `workorder init` adds a workflow that runs the pull-request check:

```sh
workorder check --base origin/main --run-tests --all-tests --require-task
```

Run it locally to see what the reviewer will see.

## The review rule

An approving review clears every finding except changes to existing tests and changes to the Workorder workflow file; those need a label from someone other than the author.

| Finding | What clears it |
|---|---|
| A finished task's test fails now, runs no tests, or times out | Fix it; nothing else |
| A DONE claim lacks evidence, or its code changed after it was finished | Finish the work or `workorder renew --task ID`, and commit the receipts |
| A task, receipt or configuration cannot be read; receipts edited or deleted; a dependency missing | Fix or restore the named file |
| A project test that already failed on the base | Nothing; it is a Note |
| A task written and finished in this pull request, or its Scope, Acceptance or dependencies changed | Approving review, or `workorder-allow:ID` |
| Changed code that no task records (`--require-task`) | Approving review, or `workorder-allow:no-task` |
| Changed code outside the task's `code_paths` | Approving review, or `workorder-allow:scope:ID` |
| The test asserts nothing, passes on the base code, or still passes with the change stubbed out | A better test; or approving review, or `workorder-allow:ID` |
| A refactor or no-code-change completion the base task did not declare | Approving review, or `workorder-allow:ID` |
| The test did not land first (`--require-test-first`) | Approving review, or `workorder-allow:ID` |
| The comparison with the base could not run | Fix the setup; or approving review, or `workorder-allow:ID` |
| A reopened task's test fails; a task retired; a finished task removed with all its receipts | Approving review, or `workorder-allow:ID` |
| An existing test edited, removed, skipped or weakened, or a task's test command or test paths changed | `workorder-allow:ID` (covers what `done --needs-review` recorded), or `workorder-allow:tests` for shared files; not an approving review |
| Existing tests fail on the new code, including the project test | `workorder-allow:ID` (covers what `done --needs-review` recorded), or `workorder-allow:tests` for project and unowned tests; not an approving review |
| The Workorder workflow file changed | `workorder-allow:workflow`; not an approving review |

Each refusal says which applies; the [reviewer guide](docs/reviewer-guide.md) explains each finding.

## Working with coding agents

Run `workorder init` in every clone: Git does not clone hooks. It installs the pre-commit hook, writes the workflow when none exists, and adds this block to AGENTS.md and CLAUDE.md:

```markdown
## Tasks
- Start from the task: if the repository has an origin remote, run `git fetch origin`. On main, update with `git pull --rebase origin main`. On a feature branch, merge or rebase main as needed. Run `workorder show --task <id>` and follow its Scope, Acceptance, test and progress.
- Set `WORKORDER_AGENT` when sharing a Git identity. Run `workorder claim --task <id>` before work.
- Do the work with a test that fails without it. Commit, then run `workorder done --task <id> --note "<what was done>"`. Report refusals; do not claim completion after one.
- Keep unfinished progress with `workorder note --task <id> --next "<next step>" "<progress>"`; done clears next.
- Never edit a task's status or receipts by hand. Commit tasks/ and .workorder/ with the work.
- Renew only when the task's own code or tests changed after DONE: `workorder renew --task <id>`.
- Before saying done, run `workorder check --base origin/main --run-tests --all-tests --require-task`.
- Output is shortened; the full text is in `.git/workorder/last-output.txt`.
```

`workorder init --claude` also installs Claude Code hooks that show held tasks at session start and check completion claims at stop. See [agent hooks](docs/reference.md#agent-hooks).

## What it does not do

- It does not check Acceptance items, except those ending `(test: path::name)`, which `done` ties to that pytest test. People or agents confirm the rest.
- Per-test findings, tied Acceptance items and the project test's base comparison need pytest; the stubbed-out code check covers Python functions only. Other test commands count as a whole by exit code.
- Local hooks can be skipped with `git commit --no-verify`. The pull-request check is the one that counts.
- It does not prove the software works in production or read agent conversations.

[Full reference](docs/reference.md) · [Team setup](docs/reference.md#team-setup) · [Examples](examples/) · [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
