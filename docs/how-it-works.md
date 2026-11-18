# How Workorder works

A coding agent's session can end mid-task, and the next one starts without knowing what was done or what remains. Agents also say DONE when nothing shows the work was done.

Workorder gives every task the same structure. `workorder new` writes each one in the same form: mission, scope, acceptance, test command, dependencies and progress, and the code paths when you name them. Every task follows the same lifecycle, and every agent session starts from `workorder show` and ends with `workorder done`, or with `workorder note --next` when the work is unfinished. So any agent or person who picks up a task finds it in the same form, checked the same way.

The proof of DONE comes from the task's own test: `workorder done` writes DONE only when the test passes now, fails on the code before the change, and fails with the change stubbed out. It refuses while Scope or Acceptance is empty.

The three charts below show a task's life, what `workorder done` checks, and what a reviewer can accept in a pull request. The task files live in `tasks/` and the receipts of each test run in `.workorder/`.

## A task's life

```mermaid
flowchart LR
    N["Task written<br/>workorder new"] --> IP["IN_PROGRESS"]
    IP -->|"workorder done<br/>(the evidence supports it)"| D["DONE"]
    IP -->|"workorder done --needs-review<br/>(a finding a reviewer must accept)"| P["DONE, pending review"]
    P -->|"the pull request gets the label<br/>(or, where the rule allows,<br/>an approving review) and merges"| D
    D -->|"status set to CLOSED by hand<br/>once delivery is recorded"| C["CLOSED"]
    D -->|"workorder retire"| R["RETIRED"]
    C -->|"workorder retire"| R
    D -->|"reopened by hand"| IP
    R -->|"reopened by hand"| IP
    IP -->|"status set by hand"| X["CANCELLED"]
```

`workorder new` writes the task as IN_PROGRESS; there is no separate open status, and BLOCKED is set by hand while work waits. Only `done` writes DONE, and only when the checks below pass; with `--needs-review` it writes DONE and records the findings a reviewer must accept, with no status edit needed after the review. CLOSED is DONE plus delivery (`ships: none` needs nothing more, `repo` the merge, `live` also deployment read-backs), and the pull-request check treats DONE to CLOSED as a new claim. Retiring a DONE or CLOSED task, cancelling a task, or reopening a finished task whose test now fails needs an approving review or the label `workorder-allow:ID` in the pull request.

## What `workorder done` checks

```mermaid
flowchart TD
    S["workorder done --task ID"] --> Q1{"Code and test<br/>committed?"}
    Q1 -->|no| N1["NOT DONE: commit the code first"]
    Q1 -->|yes| Q2{"A declared test, Scope and Acceptance<br/>filled in, and code changed<br/>since the task landed?"}
    Q2 -->|no| N2["NOT DONE: names what is missing"]
    Q2 -->|yes| Q3{"Does the test pass now, and<br/>the project test, when one is set?"}
    Q3 -->|no| N3["NOT DONE: fix the code or the test"]
    Q3 -->|yes| Q4{"Does the test assert nothing, or still<br/>pass with the changed functions stubbed out?"}
    Q4 -->|yes| N4["NOT DONE: it asserts nothing,<br/>or does not exercise the change"]
    Q4 -->|no| Q5{"Does the test pass on<br/>the code before the change?"}
    Q5 -->|yes| N5["NOT DONE: it does not show the change works"]
    Q5 -->|no| Q6{"Existing tests edited, removed,<br/>newly skipped, or failing<br/>on the new code?"}
    Q6 -->|yes| N6["NOT DONE: names the label it needs"]
    Q6 -->|no| OK["DONE, with receipts in .workorder/"]
    N4 -.->|"intended"| NR["workorder done --needs-review<br/>DONE, pending review"]
    N5 -.->|"intended"| NR
    N6 -.->|"intended"| NR
```

`done` stops at the first refusal and prints what is missing and the next step; the last two questions are one comparison with the base, so their findings come together. The stubbed-out check covers changed Python functions only, and a task declared as a refactor or verification (`--refactor`, `--no-code-change`) need not fail on the code before. A refusal leaves the status as it was; `done` never commits or pushes.

## The review rule

```mermaid
flowchart TD
    F["A finding in the<br/>pull-request check"] --> Q1{"A finished task's test fails now,<br/>or a DONE lacks evidence?"}
    Q1 -->|yes| X["Fix it, finish the work,<br/>or workorder renew<br/>no review or label clears it"]
    Q1 -->|no| Q2{"Does it change existing tests<br/>(edited, removed, skipped, weakened,<br/>or now failing, including the project test)<br/>or the Workorder workflow file?"}
    Q2 -->|yes| L["A label only<br/>workorder-allow:ID,<br/>workorder-allow:tests<br/>or workorder-allow:workflow"]
    Q2 -->|no| A["An approving review<br/>or a label<br/>workorder-allow:ID,<br/>no-task or scope:ID"]
```

Labels must come from someone other than the pull request's author, and an approving review must come from another person and cover the current head. A project test that already failed on the base is only a Note. Each refusal names what clears it; the [reviewer guide](reviewer-guide.md) has the full table and explains each finding.
