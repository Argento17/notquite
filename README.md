# Workorder: the task record for coding agents

**Every task recorded. A clear history. Agents always go back to it.**

![Workorder demo: a task is marked DONE, workorder check says NOT QUITE because the last passing test belongs to an older commit.](https://raw.githubusercontent.com/Argento17/workorder/main/docs/workorder-demo.gif)

**Until 0.6.0 is on PyPI:** install from GitHub with `pipx install "git+https://github.com/Argento17/workorder@v0.6.0"`. Released form:

```sh
pipx install workorder
```

No pipx? On Ubuntu or Debian: `sudo apt install pipx && pipx ensurepath`. Other systems: see the [pipx install guide](https://pipx.pypa.io/stable/how-to/install-pipx.html). Plain `pip install` outside a virtual environment is blocked on recent Ubuntu; the [quick start](#quick-start) uses one.

Workorder keeps each task as a record in your repository. A coding agent starts every session from the task, works within it, and leaves it up to date. The next session, or the next agent, picks up where the last one stopped.

- **Recorded:** each task is a Markdown file in `tasks/`. It holds the mission, scope, acceptance criteria, dependencies, the test that proves it, and progress notes, in a format people and agents both read and write.
- **A clear history:** receipts record what was implemented and tested, on which commit, with which command, by whom. Each receipt is a separate file, linked to the receipts it follows. Committed receipts stay unchanged. `workorder show` prints the task with that history as a timeline.
- **Agents go back to it:** a short block in `CLAUDE.md` / `AGENTS.md` makes the agent start each session with `workorder show` and end it with `workorder note` ([see below](#working-with-coding-agents)).
- **DONE is checked:** `status: DONE` is a claim. `workorder check` compares it with the history and says what is missing.

Workorder was called NotQuite up to version 0.2.0.

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
- **`ships`** sets what CLOSED needs beyond DONE: `none` (the default), `repo` (the tested change on the target branch) or `live` (also deployment read-backs).
- **`contract`** is `none` or a path to a contract that maps each acceptance criterion to evidence Workorder can check ([see below](#contracts-and-evidence)). Without one, the Acceptance list is for the agent and the reviewer; Workorder does not read it.
- **Your own fields** start with `x-`, such as `x-issue: "#42"`. They are kept and never checked.

Front matter is a small, documented subset: single-line text (plain, quoted or JSON), lists, `>` and `|` blocks for `mission`, `next` and `summary`, `- TASK-1` lists and `#` comment lines. Other YAML, and unknown fields, are reported for that task, so a typo cannot quietly drop a dependency. The [reference](https://github.com/Argento17/workorder/blob/main/docs/reference.md#what-yaml-is-read) lists exactly what is read.

Markdown files in `tasks/` whose names are not task ids, such as a README or notes, are ignored. Any other file there (code, for example) is reported, because `tasks/` is outside the code fingerprint and changes to it would never be checked.

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
- If the task's `ships` is `repo` or `live`: after the change is merged, run `workorder check <id>` on main. It computes the merge from Git history. No merge receipt or extra commit is needed. If the merged change differs, run `workorder renew --task <id>` on main and test it again.
```

## Quick start

Requires Git and Python 3.10 or newer. This uses a throwaway folder and needs no remote. On Windows, run the commands in Git Bash and activate with `. .venv/Scripts/activate`.

**Until 0.6.0 is on PyPI:** after activating the virtual environment, replace the released install command below with `python -m pip install "git+https://github.com/Argento17/workorder@v0.6.0"`.

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install workorder==0.6.0

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

To see it refuse, change `calc.py`, commit, run `workorder receipt implemented --task TASK-1`, set `status: DONE` in `tasks/TASK-1.md`, and run `workorder check TASK-1`. After the NOT QUITE result, set the status back to `IN_PROGRESS` before committing the task file; with the hook installed, the unsupported DONE claim would refuse that commit.

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

- **`workorder show --task TASK-1`** prints the task as a record: recorded and computed status, what is still owed, dependencies, title, owner and `test`, plus mission, summary and `next` when filled in. It prints every section of the task (Context, Scope, Lanes, Acceptance), a dated timeline of every receipt (implemented, each test run with its command and result, merges and read-backs, and who recorded them), any computed merge, and the Progress notes. When work is still owed it prints a next step. If only unclaimed Acceptance work remains, that step is to finish the Acceptance items, set status DONE and run `workorder check TASK-1`.
- **`workorder note --task TASK-1 "..."`** adds a dated line at the end of the task's Progress section. It is the one change Workorder makes to a task file after `new`; every other byte is left alone.
- **Ids.** `workorder new` writes `tasks/TASK-<n>.md`. With an `origin` remote, it first reserves the id there, so two clones cannot take the same id. Without one, or with `--local`, it numbers tasks from the task files and says so; those ids are not coordinated with anyone else.
- **Claims.** `workorder claim --task TASK-1` pushes a tag to `origin`. If another clone already holds the task, Git rejects the push and the claim fails. It also fetches origin's main branch (master when main is absent) and refuses a task already DONE, CLOSED or CANCELLED there, even after its holder released it; pull to update your task files. `workorder claims` lists holders.
- **Ready.** `workorder ready` lists open tasks as free, held, blocked (with what they wait for) or waiting for delivery. It fetches upstream task status too: an outdated local task says `finished on origin/main; pull to update`. It still lists local tasks when `origin` cannot be reached, with the existing note and without remote claims or status, and shows a task file that cannot be read as such.

`workorder release` works in the clone that made the claim. That clone keeps a random token in its `.git` folder, and the claim tag stores only the token's SHA-256 hash. Release adds another tag, so the history stays on `origin`. If that clone or worktree is gone, `workorder release --task TASK-1 --force` releases the claim from anywhere and records that it was forced.

Ids, claims and releases live under `refs/tags/workorder/`. Hosting sites list them with your other tags, and `git fetch --tags` downloads them. The token is a courtesy between clones, not access control. Anyone with push access can push a release tag, force-push or delete tags. If that matters, protect `workorder/*` tags in your repository settings.

Claims always need `origin`. Without a remote there are no claims at all: Workorder refuses `claim` rather than keep a local claim that nobody else could see. If `origin` is configured but unreachable, `claim` refuses rather than guess, and `new` asks for `--local`.

## What counts as DONE

1. A declared `test` (or the explicit `test: "any"`).
2. `workorder receipt implemented` on a clean, committed tree. Uncommitted or untracked code is refused, so build output belongs in `.gitignore`.
3. The latest run of the task's test on that same code exited 0, and the tree and HEAD were unchanged when it finished. A later failure revokes DONE until a later run passes; once committed, every run stays in the receipts.
4. Every critical contract criterion met, if there is a contract.
5. Nothing left in `waits_on` or `depends_on`, and no `person:` wait.

"Code" is every tracked file outside `tasks/`, `.workorder/` and `.notquite/`, so committing the task file and receipts does not change it: a later run on the same code still counts, pass or fail. A test recorded before the latest implementation, on different code, or while files or HEAD changed does not count. A receipt that is recorded but cannot count, such as a test on code that changed since `receipt implemented`, says why and exits 1. `workorder check <id>` shows the command behind the deciding run.

A DONE is about the last recorded implementation:

- A DONE or CLOSED must be claimed on the implemented code. `workorder check` refuses a claim made, in the working tree or in a commit, on code that differs from the implementation it relies on, or over uncommitted code changes; the pre-commit hook and `workorder check --base` in CI apply the same rule.
- `workorder check <id>` adds a note when the code has changed since the implementation.
- Code that lands after a task is DONE does not reopen it.

## What counts as CLOSED

DONE, plus what the task ships:

| `ships` | Also needs |
|---|---|
| `none` | nothing |
| `repo` | the tested change is on the target branch, or a qualifying `merged` receipt |
| `live` | the merge, then `receipt deployed --target <name> -- <command>`, then a later `receipt live --target <name> -- <command>` |

Workorder chooses `merge_ref` when set, otherwise `origin/main`, `origin/master`, local `main`, then local `master`, in that order. It ignores `origin/HEAD`. A commit only on your local `main` does not count as merged when `origin/main` exists until it is pushed. Set `merge_ref: refs/heads/release` in the task to choose another target. Checks use local refs and do not fetch. Checks on feature branches and detached checkouts use that same target.

A recorded `merged` receipt still counts when its commit contains the implemented commit or holds exactly its code. A merge receipt that counted stays counted. Deployment read-backs can also name a commit containing the computed squash or rebase merge.

A deployment or live read-back counts when its command exits 0 and its output contains the expected full commit ID. Workorder reads back; it does not deploy. A passing read-back shows that the command returned that commit ID, nothing more. `receipt merged` reads from `origin` and needs a remote. Computed merges do not need one.

## Squash merges and rebases

Workorder computes the merge without writing a receipt. It checks whether the
target contains the implemented commit, holds the same code, has the same code
patch after a squash, or has a patch equivalent to every code commit after a
rebase. Changes to task files and receipts do not count as code patches.
`show` and `check` name the matching commit and how it matched, from any
checkout. A feature branch is never used as its own merge target.

A DONE or CLOSED claimed on a commit after the matched landing must preserve
the content of every code path changed by the implementation at that landing.
Reverting or overwriting those files before claiming completion is refused;
run `workorder renew --task TASK-1` on the claimed code. This applies to
ancestry, same-code, squash and rebase matches. Unrelated paths may change.
When a branch contains several tasks, each task protects its own implementation
paths. Commits recorded as another task's implementation are excluded, as are
commits covered by this task's previous implemented receipt. A path this task
also changed stays protected.
A transition from DONE to CLOSED is a new claim and is checked again.
A valid claim already made at or before the landing stays valid when later
commits change those files: later work does not reopen a finished task.

Rebasing a finished branch over unrelated changes keeps its DONE claim when
the claim and implementation have the same code patch relative to their own
merge bases with the target. Merging main into the branch also keeps the claim.
If a matching squash is on local main but not origin/main, the next step says
to push main.

A squash onto a main that changed other files can count. If main changed the
same lines and the resulting patch differs, it does not count. Run
`workorder renew --task TASK-1` on main to record and test the merged code,
or record `workorder receipt merged` when its commit carries the tested code.

Patch comparisons need the original commits in the clone. When they are gone,
the same-code check can still work. Otherwise fetch the original history or
renew the evidence. Searches examine at most 1,000 target commits and 1,000
implementation commits. Results are cached under `.git/workorder-digests/`.

## Receipt files

New receipts live in
`.workorder/receipts/<ID>/<UTC time>-<first 12 hash digits>.json`, one packed
JSON object per file. Each names its `parents`: all current heads for that task.
Two branches can add receipts for the same task without editing the same file.
After the branches merge, the next receipt names both heads.

Old `.workorder/receipts/<ID>.jsonl` and `.notquite/receipts/<ID>.jsonl` chains
still verify. New receipts follow their last hash and leave the old file alone.
Missing parents, changed hashes, wrong filenames, and changes or deletions to
committed receipts are refused. `check --base REF` also checks every commit
in `REF..HEAD` for removed or modified receipt files, including a receipt
added and then deleted, or changed and restored, after `REF`. It names each
affected path. Complete removal of a finished task with `--allow ID` retains
its exception for that task's receipts.
For an old append conflict, run `workorder repair --task ID`. It verifies both
sides, keeps the common prefix, and converts the extra lines into receipt files.
See the [reference](docs/reference.md#receipts) for the repair rules.

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

The hook and a local `workorder check` judge recorded evidence. Plain `check`
reads every task. The hook checks staged task files, receipts, contracts and
evidence, plus their dependents. In CI, `check --base <main> --run-tests` is
where each task's declared test is run again on the code being checked.
`--base` also includes tasks whose test files changed. A reviewer can accept
changes to a task's requirements with a `workorder-allow:<ID>` pull request
label, passed as `--allow` in the example below.

Pre-commit hook, to refuse committing a false DONE:

**Until 0.6.0 is on PyPI:** install the command with `pipx install "git+https://github.com/Argento17/workorder@v0.6.0"`; the hook's install hint below is the released form.

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

**Until 0.6.0 is on PyPI:** replace the released install step below with `python -m pip install "git+https://github.com/Argento17/workorder@v0.6.0"`.

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
      - run: python -m pip install workorder==0.6.0
      - name: Check tasks and run their tests
        env:
          BASE: origin/${{ github.base_ref }}
        run: |
          # task ids from pull request labels named workorder-allow:<ID>
          allow=$(jq -r '[.pull_request.labels[].name | select(test("^workorder-allow:[A-Z][A-Z0-9]*-[1-9][0-9]*$")) | sub("^workorder-allow:"; "")] | join(",")' "$GITHUB_EVENT_PATH")
          workorder check --base "$BASE" --run-tests ${allow:+--allow "$allow"}
```

What `--base` adds, beyond what the pre-commit hook can promise (`git commit --no-verify` skips the hook):

- Receipts already on the base branch must stay unchanged. Receipt files deleted or modified in any commit since the base are refused with their paths, even if absent at the base or restored at HEAD. With `--allow ID`, a task that was DONE, CLOSED or CANCELLED at the base may be removed with all its receipts.
- A commit since the base that marked a task DONE or CLOSED must carry its implemented code.
- A change to what a task requires (its `test`, its contract or its evidence, a removed dependency, or a removed or reworded line of its Scope or Acceptance) fails the check until a reviewer accepts it: `--allow TASK-4` (comma-separated ids) for a re-run started by a maintainer. Adding lines is not a change of requirement.
- Removing or changing lines in a task's test files needs `--allow ID`. Pure additions pass. Set `test_paths` to name files or folders; otherwise Workorder uses paths in the `test` command.
- A task created and claimed DONE or CLOSED in the same change needs `--allow ID` to accept its definition.
- With `--run-tests`, the check runs the declared tests of supported DONE or CLOSED claims in scope. It reports how many tasks it checked and how many tests actually ran. If none ran, it says so plainly.

Only reviewers who may accept these changes should apply the allow labels.
`--allow` accepts the named definition changes or complete task removal; it
does not waive missing evidence, changed receipts on a retained task, or a
failing test.

## Limitations

- Anyone who can write files and compute SHA-256 can create a receipt. Rewriting Git history also rewrites its record. `--run-tests` runs the declared tests during the check.
- A passing test is only as good as its assertions, and the task's `test` is part of the task file: `workorder check --base` refuses a change to it until a reviewer allows it.
- Committed receipt files stay unchanged; new files can be added. Legacy ledgers keep their committed prefix. The pre-commit hook enforces this against the previous commit, and `workorder check --base <branch>` also refuses receipt-file deletions and modifications anywhere in the intervening history. Without `--base`, a commit made with `git commit --no-verify` can remove receipts unnoticed.
- A DONE is about the code that was implemented and tested. Code that lands after a task is DONE does not reopen it; `workorder check <id>` notes the change.
- Only task files are checked. Claims made in chat, issues or pull request text are not.
- Receipts and claims record your Git `user.name`, and it is pushed. Receipts written by 0.2.0 or earlier also carry the computer's hostname, and keep it.
- Ids, claims and releases are Git tags, so they show up in tag lists; a `git tag` that picks the newest tag should filter out `workorder/*`.
- Tasks stay in `tasks/` until removed with reviewer acceptance through `check --base --allow`. Plain `workorder check` reads all current tasks, while the hook and `--base` read what a change touches.

## Contributing

Bug reports and small pull requests are welcome. See [CONTRIBUTING.md](https://github.com/Argento17/workorder/blob/main/CONTRIBUTING.md).

Feedback, issues and ideas welcome, and a star if you find it useful.

## License

MIT. See [LICENSE](https://github.com/Argento17/workorder/blob/main/LICENSE).
