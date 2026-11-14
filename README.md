# NotQuite: a task format for coding agents

**Define the work. Track the evidence. Verify DONE.**

![NotQuite demo: a task is marked DONE, notquite check says NOT QUITE because the last passing test belongs to an older commit.](https://raw.githubusercontent.com/Argento17/notquite/main/docs/notquite-demo.gif)

```sh
pipx install notquite
```

No pipx? On Ubuntu or Debian: `sudo apt install pipx && pipx ensurepath`. Other systems: see the [pipx install guide](https://pipx.pypa.io/stable/how-to/install-pipx.html). Plain `pip install` outside a virtual environment is blocked on recent Ubuntu; the [quick start](#quick-start) uses one.

NotQuite keeps each task as a Markdown file in your repository. Your coding agent reads the task, works within it, records what it implemented and tested, and claims DONE. NotQuite checks that claim against the evidence.

- **Define:** a task file holds the mission, scope, acceptance criteria, dependencies, lanes and progress, in plain Markdown that people and agents read and write.
- **Track:** task ids and claims coordinated through Git across clones, dependencies between tasks, `notquite ready` for what can be picked up next, and receipts of what was implemented and tested.
- **Verify:** `status: DONE` is a claim. `notquite check` computes the actual state from the receipts and says what is missing.
- Works with Claude Code and Codex through a short block in `CLAUDE.md` / `AGENTS.md` ([see below](#working-with-coding-agents)).

It works alongside your CI and issue tracker, not instead of them.

## Why

Coding agents get their work from chat. The goal, the scope and what "done" means live in a conversation that ends, and the next session or the next agent starts from a summary. And DONE is whatever the agent says it is.

NotQuite moves the task into the repository, next to the code. The task file says what to build and how it will be accepted. The agent works from it, notes progress in it, and records evidence as it goes. When the task is marked DONE, NotQuite checks that a passing test was recorded on the code that was implemented, and says what is missing if not.

It runs on Git and Python's standard library. No server or database. Tested in CI on Linux and Windows (Python 3.10, 3.12, 3.13).

## A task

`notquite new --title "Sort items by due date" --owner alex` writes `tasks/TASK-<n>.md` with these fields and sections for you or the agent to fill in. A filled-in task:

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

- **For people and agents:** `mission`, `summary`, `next`, the owners, `priority` and the five sections say what the work is, what is in and out, who does what, and how it will be accepted. NotQuite keeps them in the file and never treats them as evidence.
- **`status`** is the claim: `IN_PROGRESS`, `BLOCKED`, `DONE` or `CLOSED`. You or the agent edit it. NotQuite never edits it and never trusts it.
- **`depends_on`** lists tasks this one needs. `waits_on` takes `task:TASK-2` or `person:alex` items (remove a person wait when it is answered). Both form one dependency list, and a task cannot be DONE while one is open. `blocks` is informational: it is checked against the other tasks but never enforced.
- **`ships`** sets what CLOSED needs beyond DONE: `none`, `repo` (the default) or `live`.
- **`contract`** is `none` or a path to a contract that maps each acceptance criterion to evidence NotQuite can check ([see below](#contracts-and-evidence)). Without one, the Acceptance list is for the agent and the reviewer; NotQuite does not read it.

Front matter is a small, documented subset: single-line text, JSON strings and lists, `>` and `|` blocks for `mission`, `next` and `summary`, and indented `- TASK-1` lists. Other YAML, and unknown fields, are refused, so a typo cannot quietly drop a dependency. The [reference](https://github.com/Argento17/notquite/blob/main/docs/reference.md#what-yaml-is-read) lists exactly what is read.

For small work, `notquite new --template simple` writes the seven required fields only (`id`, `title`, `owner`, `status`, `ships`, `waits_on`, `contract`), the format of version 0.1.0. Both formats can sit in the same repository. Version 0.1.0 refuses task files that use the other fields, so upgrade every clone and CI pin before using them.

## Working with coding agents

NotQuite reads task files and recorded evidence. It does not watch conversations, so a claim made only in chat is not caught. Tell the agent how to work with tasks. Add this to `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex):

```markdown
## Tasks
- Work from the task file. Read tasks/<id>.md first: the mission and Scope say what to do and what not to; the Acceptance list says how the work will be accepted.
- Before starting, run `notquite claim --task <id>` (needs an origin remote); run `notquite release --task <id>` when you stop.
- Add dated notes under Progress as you go, and keep `next` current.
- After committing code, run `notquite receipt implemented --task <id>`, then run the project's tests through `notquite receipt test --task <id> -- <test command>`.
- Set status DONE only when every Acceptance item is met, then run `notquite check <id>`. If it reports NOT QUITE, set the status back and report its Missing lines instead of saying the task is done.
```

## Quick start

Requires Git and Python 3.10 or newer. This uses a throwaway folder and needs no remote. On Windows, run the commands in Git Bash and activate with `. .venv/Scripts/activate`.

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install notquite

mkdir demo && cd demo
git init -q -b main
git config user.name "Your Name"
git config user.email you@example.com

printf 'def add(a, b):\n    return a + b\n' > calc.py
printf 'from calc import add\n\nassert add(2, 2) == 4\n' > test_calc.py
printf '__pycache__/\n' > .gitignore
git add . && git commit -qm "Add calc"

notquite new --title "Add calc" --owner you --ships none
notquite receipt implemented --task TASK-1
notquite receipt test --task TASK-1 -- python test_calc.py
notquite check TASK-1
```

The last command prints `Claimed: IN_PROGRESS`, `Computed: CLOSED` and `Result: no completion claimed`. The `status` line in `tasks/TASK-1.md` is the claim, and NotQuite never edits it. The evidence already supports CLOSED (this task ships nothing), so setting `status: CLOSED` would now pass `notquite check`.

To see it refuse, change `calc.py`, commit, run `notquite receipt implemented --task TASK-1`, set `status: DONE` in `tasks/TASK-1.md`, and run `notquite check TASK-1` again.

Run `notquite` at the root of your repository. Commit `tasks/` and `.notquite/` along with your code; the check reads what is committed.

## When DONE is not done

A test passed on one commit. Then the code changed, and the task was marked DONE without testing again:

```text
$ notquite check TASK-1
NOT QUITE  TASK-1

  Claimed:  DONE
  Computed: IN_PROGRESS
  Commit:   cfd328a
  Test:     no passing test for this commit; the last test ran on 945c15e
  Missing:  a passing test on cfd328a
  Next:     notquite receipt test --task TASK-1 -- <your test command>

  Result: NOT DONE. The status says DONE; the evidence above does not support it.
```

The exit code is 1, so an agent, a hook or a script sees the refusal. Running the tests through NotQuite records what actually happened. In this case the change broke them:

```text
$ notquite receipt test --task TASK-1 -- python test_calc.py
Traceback (most recent call last):
  ...
AssertionError
TASK-1 test cfd328a exit 1 - recorded
command: python test_calc.py
TASK-1 still owes: a passing test on cfd328a
```

## Tracking work

- **Ids.** `notquite new` writes `tasks/TASK-<n>.md`. With an `origin` remote, it first reserves the id there, so two clones cannot take the same id. Without one, it numbers tasks locally and says so; those ids are not coordinated with anyone else.
- **Claims.** `notquite claim --task TASK-1` pushes a tag to `origin`. If another clone already holds the task, Git rejects the push and the claim fails. `notquite claims` lists holders.
- **Ready.** `notquite ready` lists open tasks as free, held, blocked (with what they wait for) or waiting for delivery.
- **Status.** `notquite show --task TASK-1` prints the recorded and the computed status, and what is still owed.

`notquite release` works only in the clone that made the claim. That clone keeps a random token in its `.git` folder, and the claim tag stores only the token's SHA-256 hash. Release adds another tag, so the history stays on `origin`.

Ids, claims and releases live under `refs/tags/notquite/`. Hosting sites list them with your other tags, and `git fetch --tags` downloads them. The token is a courtesy between clones, not access control. Anyone with push access can push a release tag, force-push or delete tags. If that matters, protect `notquite/*` tags in your repository settings. A claim whose clone is gone can only be freed that way.

Claims always need `origin`. Without a remote there are no claims at all: NotQuite refuses `claim` rather than keep a local claim that nobody else could see. If `origin` is configured but unreachable, `new` and `claim` refuse rather than guess.

## What counts as DONE

1. `notquite receipt implemented` on a clean, committed tree. Uncommitted or untracked code is refused, so build output belongs in `.gitignore`.
2. The latest `notquite receipt test -- <command>` on that same code exited 0, and the tree and HEAD were unchanged when it finished. A later failure revokes DONE until a later run passes; every run stays in the receipts.
3. Every critical contract criterion met, if there is a contract.
4. Nothing left in `waits_on` or `depends_on`: each task dependency computes DONE or CLOSED, and no `person:` wait remains.

"Code" is every tracked file outside `tasks/` and `.notquite/`, so committing the task file and receipts does not change it: a later run on the same code still counts, pass or fail. A test recorded before the latest implementation, on different code, or while files or HEAD changed does not count. Each `receipt test` runs the command itself and records the real exit code. NotQuite does not choose the command; use the project's real tests.

A receipt that is recorded but cannot count, such as a test on code that changed since `receipt implemented`, says why and exits 1.

A DONE is about the last recorded implementation:

- The pre-commit hook refuses a commit that newly marks a task DONE or CLOSED when the code in that commit differs from the implemented code.
- `notquite check <id>` adds a note when the code has changed since the implementation.
- Code that lands after a task is DONE does not reopen it.

## What counts as CLOSED

DONE, plus what the task ships:

| `ships` | Also needs |
|---|---|
| `none` | nothing |
| `repo` | `receipt merged`: the tested code is on `origin`'s `main` (`--ref refs/heads/<branch>` to change) |
| `live` | the merge, then `receipt deployed --target <name> -- <command>`, then a later `receipt live --target <name> -- <command>` |

`receipt merged` and `receipt deployed` count only when that commit's code is exactly the implemented code: a fast-forward merge with nothing else landed on the branch. Once other work has landed, the receipt is recorded but does not count, so on a busy branch a `repo` or `live` task may not reach CLOSED.

A deployment or live read-back counts when its command exits 0 and its output contains the expected full commit ID. NotQuite reads back; it does not deploy. A passing read-back shows that the command returned that commit ID, nothing more. `receipt merged` reads from `origin`, so `repo` and `live` tasks need a remote.

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

Critical criteria must be met before DONE. This contract fails: a mock result cannot satisfy a request for real output. `REAL` is a label the author declares, so the file and its hash prove which file was meant, not where it came from, and the contract itself can be edited like any task file: review changes to it. See [examples/](https://github.com/Argento17/notquite/tree/main/examples) for sample tasks and this contract.

## Hooks and CI

The check runs wherever `notquite` is installed. Two optional places make it automatic.

Pre-commit hook, to refuse committing a false DONE:

```sh
printf '#!/bin/sh\nexec notquite check --staged\n' > .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

In CI, `notquite check` reads the task files and receipts committed with the code; it does not run your tests. GitHub Actions (`.github/workflows/notquite.yml` in your project):

```yaml
name: NotQuite
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
      - run: python -m pip install notquite==0.2.1
      - run: notquite check --base origin/${{ github.base_ref }}
```

`--base` also refuses if receipts already on the base branch were removed or changed. The pre-commit hook alone cannot promise that, because `git commit --no-verify` skips it.

Use merge commits for branches that carry receipts. Receipts name the commits they were recorded on. A squash merge, a rebase or an amend replaces those commits; once the originals are gone (the branch deleted, or never pushed), a fresh clone does not have them, and `notquite check` there refuses.

## Limitations

- Receipts are hash-chained, not signed. Anyone who can write files can forge one, and rewriting Git history rewrites the proof. NotQuite catches missing and stale evidence, not deliberate forgery.
- The test is whatever command is recorded. A passing test is only as good as its assertions, and a later passing run of a different command clears an earlier failure.
- Receipts already committed can only be appended to. The pre-commit hook enforces this against the previous commit, and `notquite check --base <branch>` against the base branch. Without `--base`, a commit made with `git commit --no-verify` can remove receipts unnoticed.
- Squash merges, rebases and amends of commits that receipts name are not supported yet (see [Hooks and CI](#hooks-and-ci)).
- Only task files are checked. Claims made in chat, issues or pull request text are not.
- Receipts and claims record your Git `user.name`, and it is pushed. Receipts written by 0.2.0 or earlier also carry the computer's hostname, and keep it.

## Contributing

Bug reports and small pull requests are welcome. See [CONTRIBUTING.md](https://github.com/Argento17/notquite/blob/main/CONTRIBUTING.md).

Feedback, issues and ideas welcome, and a star if you find it useful.

## License

MIT. See [LICENSE](https://github.com/Argento17/notquite/blob/main/LICENSE).
