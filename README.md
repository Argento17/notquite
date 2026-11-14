# Workorder: the task record for coding agents

**Every task recorded. A clear history. Agents always go back to it.**

![Workorder demo: a task is marked DONE, workorder check says NOT QUITE because the last passing test belongs to an older commit.](https://raw.githubusercontent.com/Argento17/workorder/main/docs/workorder-demo.gif)

```sh
pipx install workorder
```

No pipx? On Ubuntu or Debian: `sudo apt install pipx && pipx ensurepath`. Other systems: see the [pipx install guide](https://pipx.pypa.io/stable/how-to/install-pipx.html). Plain `pip install` outside a virtual environment is blocked on recent Ubuntu; the [quick start](#quick-start) uses one.

Workorder keeps each task as a record in your repository. A coding agent starts every session from the task, works within it, and leaves it up to date. The next session, or the next agent, picks up where the last one stopped.

- **Recorded:** each task is a Markdown file in `tasks/`. It holds the mission, scope, acceptance criteria, dependencies, the test that proves it, and progress notes, in a format people and agents both read and write.
- **A clear history:** receipts record what was implemented and tested, on which commit, with which command, by whom. They are chained and never rewritten. `workorder show` prints the task with that history as a timeline.
- **Agents go back to it:** a short block in `CLAUDE.md` / `AGENTS.md` makes the agent start each session with `workorder show` and end it with `workorder note` ([see below](#working-with-coding-agents)).
- **DONE is checked:** `status: DONE` is a claim. `workorder check` compares it with the history and says what is missing.

It works alongside your CI and issue tracker, not instead of them. Workorder was called NotQuite up to version 0.2.0.

## Why

Coding agents get their work from chat. The goal, the scope and what "done" means live in a conversation that ends, and the next session or the next agent starts from a summary. And DONE is whatever the agent says it is.

Workorder moves the task into the repository, next to the code, and keeps its history there. The task file says what to build, how it will be accepted and which test proves it. The agent comes back to it at the start of every session, notes progress in it, and records evidence as it goes. When the task is marked DONE, Workorder checks that a passing run of the task's test was recorded on the code that was implemented, and says what is missing if not.

It runs on Git and Python's standard library. No server or database. Tested in CI on Linux and Windows (Python 3.10, 3.12, 3.13).

## A task

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
- **`status`** is the claim: `IN_PROGRESS`, `BLOCKED`, `DONE`, `CLOSED`, or `CANCELLED` for work that will not be done (it needs no evidence and leaves `ready`). You or the agent edit it. Workorder never edits it and never trusts it.
- **`test`** is the command that proves the task. A task needs one to be DONE: only a run of exactly that command counts, and `workorder receipt test --task <id>` runs it. For work no single command proves, `test: "any"` says so explicitly and accepts any recorded command. No shell runs it: words split on spaces, quotes group words, backslashes stay as written, and `&&` or pipes are refused (use a script).
- **`depends_on`** lists tasks this one needs. `waits_on` takes `task:TASK-2` or `person:alex` items (remove a person wait when it is answered). Both form one dependency list, and a task cannot be DONE while one is open. A dependency is finished when it says DONE or CLOSED, its evidence supports that, and its own dependencies are finished. `blocks` is informational: it is checked against the other tasks but never enforced.
- **`ships`** sets what CLOSED needs beyond DONE: `none` (the default), `repo` (a merge receipt) or `live` (also deployment read-backs).
- **`contract`** is `none` or a path to a contract that maps each acceptance criterion to evidence Workorder can check ([see below](#contracts-and-evidence)). Without one, the Acceptance list is for the agent and the reviewer; Workorder does not read it.
- **Your own fields** start with `x-`, such as `x-issue: "#42"`. They are kept and never checked.

Front matter is a small, documented subset: single-line text (plain, quoted or JSON), lists, `>` and `|` blocks for `mission`, `next` and `summary`, `- TASK-1` lists and `#` comment lines. Other YAML, and unknown fields, are reported for that task, so a typo cannot quietly drop a dependency. The [reference](https://github.com/Argento17/workorder/blob/main/docs/reference.md#what-yaml-is-read) lists exactly what is read.

Files in `tasks/` whose names are not task ids, such as a README or a notes folder, are ignored.

For small work, `workorder new --template simple` writes the seven required fields only (`id`, `title`, `owner`, `status`, `ships`, `waits_on`, `contract`), the format of version 0.1.0; add `test:` before claiming DONE. Both formats can sit in the same repository. Version 0.1.0 refuses task files that use the other fields, so upgrade every clone and CI pin before using them.

## Working with coding agents

Workorder reads task files and recorded evidence. It does not watch conversations, so a claim made only in chat is not caught. Tell the agent how to work with tasks. Add this to `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex):

```markdown
## Tasks
- Start every session with `workorder show --task <id>` and work from what it prints: the mission and Scope say what to do and what not to; the Acceptance list says how the work will be accepted; `test` is the command that proves it; the history and progress say where the last session stopped.
- Before starting, run `workorder claim --task <id>` (needs an origin remote you can push to). Run `workorder release --task <id>` when you stop working on it, finished or not.
- After committing code, run `workorder receipt implemented --task <id>`, then `workorder receipt test --task <id>` (it runs the task's `test`).
- Set status DONE only when every Acceptance item is met, then run `workorder check <id>`. If it reports NOT QUITE, set the status back and report its Missing lines instead of saying the task is done.
- Before you stop, record where you are with `workorder note --task <id> "<what was done; what is next>"`, keep `next` current, then commit tasks/ and .workorder/ with your work and push.
- If the task's `ships` is `repo` or `live`: after the change is merged, on the main branch run `workorder receipt merged --task <id>` (or `workorder renew --task <id>` after a squash merge), set CLOSED only if `workorder check <id>` passes, then commit and push again.
```

## Quick start

Requires Git and Python 3.10 or newer. This uses a throwaway folder and needs no remote. On Windows, run the commands in Git Bash and activate with `. .venv/Scripts/activate`.

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install workorder

mkdir demo && cd demo
git init -q -b main
git config user.name "Your Name"
git config user.email you@example.com

printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'from calc import add\n\nassert add(2, 2) == 4\n' > test_calc.py
printf '__pycache__/\n' > .gitignore
git add . && git commit -qm "Add calc"

workorder new --title "Add calc" --owner you --test "python test_calc.py"
workorder receipt implemented --task TASK-1
workorder receipt test --task TASK-1
workorder note --task TASK-1 "add() works; next: subtraction"
workorder show --task TASK-1
```

`workorder show` prints the task as a record: its state (`recorded IN_PROGRESS; computed CLOSED`, because the evidence already supports it and this task ships nothing), what it still owes, its test, a timeline of the implementation and the passing test, and the progress note. The `status` line in `tasks/TASK-1.md` is the claim, and Workorder never edits it; setting `status: CLOSED` would now pass `workorder check`.

To see it refuse, change `calc.py`, commit, run `workorder receipt implemented --task TASK-1`, set `status: DONE` in `tasks/TASK-1.md`, and run `workorder check TASK-1`.

Run `workorder` at the root of your repository. Commit `tasks/` and `.workorder/` along with your code. Locally, `workorder check` reads the working tree (`--staged`: what is about to be committed); in CI it reads what was committed.

## When DONE is not done

A test passed on one commit. Then the code changed, and the task was marked DONE without testing again:

```text
$ workorder check TASK-1
NOT QUITE  TASK-1

  Claimed:  DONE
  Computed: IN_PROGRESS
  Commit:   75e3c96
  Test:     no passing test for this commit; the last test ran on cc96932
  Missing:  a passing test on 75e3c96
  Next:     workorder receipt test --task TASK-1 -- python test_calc.py

  Result: NOT DONE. The status says DONE; the evidence above does not support it.
```

The exit code is 1, so an agent, a hook or a script sees the refusal. Running the task's test through Workorder records what actually happened. In this case the change broke it:

```text
$ workorder receipt test --task TASK-1
Traceback (most recent call last):
  ...
AssertionError
TASK-1 test 75e3c96 exit 1 - recorded
command: python test_calc.py
TASK-1 still owes: a passing test on 75e3c96
```

## The record

- **`workorder show --task TASK-1`** prints the task as a record: recorded and computed status, what is still owed, title, mission, `next` and `test`, a dated timeline of every receipt (implemented, each test run with its command and result, merges and read-backs, and who recorded them), the Progress notes, and the next step.
- **`workorder note --task TASK-1 "..."`** adds a dated line at the end of the task's Progress section. It is the one change Workorder makes to a task file after `new`; every other byte is left alone.
- **Ids.** `workorder new` writes `tasks/TASK-<n>.md`. With an `origin` remote, it first reserves the id there, so two clones cannot take the same id. Without one, or with `--local`, it numbers tasks from the task files and says so; those ids are not coordinated with anyone else.
- **Claims.** `workorder claim --task TASK-1` pushes a tag to `origin`. If another clone already holds the task, Git rejects the push and the claim fails. `workorder claims` lists holders.
- **Ready.** `workorder ready` lists open tasks as free, held, blocked (with what they wait for) or waiting for delivery. It still lists tasks when `origin` cannot be reached, without claims, and shows a task file that cannot be read as such.

`workorder release` works in the clone that made the claim. That clone keeps a random token in its `.git` folder, and the claim tag stores only the token's SHA-256 hash. Release adds another tag, so the history stays on `origin`. If that clone or worktree is gone, `workorder release --task TASK-1 --force` releases the claim from anywhere and records that it was forced.

Ids, claims and releases live under `refs/tags/workorder/`. Hosting sites list them with your other tags, and `git fetch --tags` downloads them. The token is a courtesy between clones, not access control. Anyone with push access can push a release tag, force-push or delete tags. If that matters, protect `workorder/*` tags in your repository settings.

Claims always need `origin`. Without a remote there are no claims at all: Workorder refuses `claim` rather than keep a local claim that nobody else could see. If `origin` is configured but unreachable, `claim` refuses rather than guess, and `new` asks for `--local`.

## What counts as DONE

1. A declared `test` (or the explicit `test: "any"`).
2. `workorder receipt implemented` on a clean, committed tree. Uncommitted or untracked code is refused, so build output belongs in `.gitignore`.
3. The latest run of the task's test on that same code exited 0, and the tree and HEAD were unchanged when it finished. A later failure revokes DONE until a later run passes; once committed, every run stays in the receipts.
4. Every critical contract criterion met, if there is a contract.
5. Nothing left in `waits_on` or `depends_on`, and no `person:` wait.

"Code" is every tracked file outside `tasks/` and `.workorder/`, so committing the task file and receipts does not change it: a later run on the same code still counts, pass or fail. A test recorded before the latest implementation, on different code, or while files or HEAD changed does not count. A receipt that is recorded but cannot count, such as a test on code that changed since `receipt implemented`, says why and exits 1. `workorder check <id>` shows the command behind the deciding run.

A DONE is about the last recorded implementation:

- A DONE or CLOSED must be claimed on the implemented code. `workorder check` refuses a claim made, in the working tree or in a commit, on code that differs from the implementation it relies on, or over uncommitted code changes; the pre-commit hook and `workorder check --base` in CI apply the same rule.
- `workorder check <id>` adds a note when the code has changed since the implementation.
- Code that lands after a task is DONE does not reopen it.

## What counts as CLOSED

DONE, plus what the task ships:

| `ships` | Also needs |
|---|---|
| `none` | nothing |
| `repo` | `receipt merged`: the tested code is on `origin`'s `main` (`--ref refs/heads/<branch>` to change) |
| `live` | the merge, then `receipt deployed --target <name> -- <command>`, then a later `receipt live --target <name> -- <command>` |

`receipt merged` and `receipt deployed` count when that commit contains the implemented commit (a merge commit or a fast-forward, with any other work beside it) or holds exactly its code. A merge receipt that counted stays counted.

A deployment or live read-back counts when its command exits 0 and its output contains the expected full commit ID. Workorder reads back; it does not deploy. A passing read-back shows that the command returned that commit ID, nothing more. `receipt merged` reads from `origin`, so `repo` and `live` tasks need a remote.

## Squash merges and rebases

Receipts name the commits they were recorded on. A squash merge, a rebase or an amend replaces those commits. When the new commit holds exactly the code the receipts recorded (a squash onto an unchanged main), the evidence still counts: Workorder finds that code on the commit that brought the task in. When it does not (main had moved on), `workorder renew --task TASK-1` records `implemented` and the task's test again on the merged code, and the merge if the task ships, in one command. Other tasks are never affected. With merge commits nothing needs renewing.

## Contracts and evidence

A contract maps each acceptance criterion to the evidence that counts for it:

```json
{
  "task": "TASK-3",
  "criteria": [
    {
      "id": "C1",
      "ask": "Items remain after reloading the running app",
      "critical": true,
      "requires": "REAL",
      "evidence": [{"provenance": "MOCK", "ref": "a simulated browser result"}]
    }
  ]
}
```

| Label | Meaning | Can satisfy |
|---|---|---|
| `REAL` | a file in the repository and its `sha256` | `REAL`, `REAL_OR_TEST` |
| `TEST` | `receipt:<hash>` of a passing test on the implemented code | `REAL_OR_TEST` only |
| `MOCK`, `SYNTHETIC`, `UNKNOWN` | recorded for honesty | nothing |

Critical criteria must be met before DONE. This contract fails: a mock result cannot satisfy a request for real output. `REAL` is a label the author declares, so the file and its hash prove which file was meant, not where it came from. Keep contracts and evidence files under `.workorder/` (for example `.workorder/evidence/`): files there are outside the code fingerprint, so adding one does not change the implemented code. See [examples/](https://github.com/Argento17/workorder/tree/main/examples) for sample tasks and this contract.

## Hooks and CI

The check runs wherever `workorder` is installed. Two optional places make it automatic. Both judge the change: the tasks it touches (their task file, receipts, contract or evidence) and the tasks that depend on them. Plain `workorder check` reads every task.

Pre-commit hook, to refuse committing a false DONE:

```sh
cat > .git/hooks/pre-commit <<'EOF'
#!/bin/sh
command -v workorder >/dev/null 2>&1 && exec workorder check --staged
echo "pre-commit: workorder is not on PATH (pipx install workorder)" >&2
exit 1
EOF
chmod +x .git/hooks/pre-commit
```

GitHub Actions (`.github/workflows/workorder.yml` in your project):

```yaml
name: Workorder
on: [pull_request]
permissions: {contents: read}
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}   # receipts refer to earlier commits
      - uses: actions/setup-python@v5
        with: {python-version: '3.12'}
      - run: python -m pip install workorder==0.3.0
      - run: workorder check --base origin/${{ github.base_ref }} --run-tests
```

What `--base` adds, beyond what the pre-commit hook can promise (`git commit --no-verify` skips the hook):

- Receipts already on the base branch must still be there, unchanged.
- A commit since the base that marked a task DONE or CLOSED must carry its implemented code.
- A change to what a task requires (its `test`, its contract or its evidence, or a removed dependency) fails the check until a reviewer accepts it: `--allow TASK-4` (comma-separated ids) for a re-run started by a maintainer.
- With `--run-tests`, the check runs the declared test of every task the change claims DONE or CLOSED, on the code being merged, instead of trusting the receipts recorded on the agent's machine.

## Limitations

- Receipts are hash-chained, not signed. Anyone who can write files can forge one, and rewriting Git history rewrites the proof. Workorder catches missing and stale evidence, not deliberate forgery; `--run-tests` in CI is the answer when that matters.
- A passing test is only as good as its assertions, and the task's `test` is part of the task file: `workorder check --base` refuses a change to it until a reviewer allows it.
- Receipts already committed can only be appended to. The pre-commit hook enforces this against the previous commit, and `workorder check --base <branch>` against the base branch. Without `--base`, a commit made with `git commit --no-verify` can remove receipts unnoticed.
- A DONE is about the code that was implemented and tested. Code that lands after a task is DONE does not reopen it; `workorder check <id>` notes the change.
- Only task files are checked. Claims made in chat, issues or pull request text are not.
- Receipts and claims record your Git `user.name`, and it is pushed. Receipts written by 0.2.0 or earlier also carry the computer's hostname, and keep it.
- Ids, claims and releases are Git tags, so they show up in tag lists; a `git tag` that picks the newest tag should filter out `workorder/*`.
- Every task stays in `tasks/`; there is no archive yet. Plain `workorder check` reads all of them, while the hook and CI read only what a change touches.

## Contributing

Bug reports and small pull requests are welcome. See [CONTRIBUTING.md](https://github.com/Argento17/workorder/blob/main/CONTRIBUTING.md).

Feedback, issues and ideas welcome, and a star if you find it useful.

## License

MIT. See [LICENSE](https://github.com/Argento17/workorder/blob/main/LICENSE).
