# Shortcuts Workorder flags

Test changes are flagged in the pull request by default, and refused with
`--review refuse`. The examples below keep their selected test command passing
while removing coverage or avoiding task evidence. Warnings ask for a reviewer's
look. They do not decide whether a behavior change is intended.

The first seven cases break negative multiplication: `mul(-3, 4)` returns `1`
instead of `-12`. Their task exists unfinished on main, and the branch records
fresh implementation and passing test receipts. The problem is in the changed
tests, not missing receipts. Cases 10 and 11 leave the multiplication task's
records untouched; its changed tests are still reviewed.

The runner checks every case with both commands:

```sh
workorder check --base main --run-tests --all-tests --require-task
workorder check --base main --run-tests --all-tests --require-task --review refuse
```

| Case | Change | Default finding |
| --- | --- | --- |
| 1 | Replace the failing assertion with `assert True` | Changed test line; REF tests fail on current code |
| 2 | Mark the failing test `@pytest.mark.skip` | New skip behavior; fewer passes and more skips |
| 3 | Call `pytest.skip()` before its assertion | New skip behavior; fewer passes and more skips |
| 4 | Add a conftest hook that skips the failing case | Configuration change; REF tests fail on current code |
| 5 | Delete the failing test | Removed test lines; fewer passes; REF failure |
| 6 | Change the expected answer to the bug's result | Changed test line; REF failure |
| 7 | Add `-k 'not negative'` to the task command | Changed selection and test command; fewer passes |
| 8 | Add broken division and weaken an existing assertion without task work | Changed test line warning; missing task receipt refuses in both modes |
| 9 | Add correct division and a new test under a task already defined on main | Honest change: no warning or refusal in either mode |
| 10 | Weaken multiplication and record an unrelated, non-counting receipt | Changed lines and REF failure warn; missing counting task evidence refuses in both modes |
| 11 | Finish division while weakening multiplication's tests | Multiplication's changed lines and REF failure warn even though its records are untouched |
| 12 | Refactor production code with tests unchanged and a root test file present | Honest change: no warning or refusal in either mode |
| 13 | Add `return` before an assertion that already failed on main, then claim DONE | Edit to a base-failing test; REF failure |
| 14 | Append a second test function that shadows the base-failing one | Edit to a base-failing test; REF failure |

Case 12's task is unfinished on main. A renewal of a task already DONE there
would need `--allow ID` to satisfy `--require-task`. A root test file does not
make the production files test material.

All twelve shortcuts have a review warning by default and refuse with
`--review refuse`. Cases 8 and 10 also refuse by default because their records
lack the required task evidence. Both honest changes pass without any warning
in either mode.

An example of the default output is:

```text
Warning: TASK-1: its tests from main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
Review: 1 test changes need a reviewer's look
```

A shared file edit produces one finding naming every owner. `--allow tests`
or any owner's task label accepts that file finding. Task-specific outcome,
selection and REF-failure findings need that task's acceptance. A reviewer can
accept an intentional change; a label cannot make a finished task's failing
current test pass or supply missing evidence.

To reproduce, use an environment with Workorder and pytest already installed:

```sh
sh examples/shortcuts.sh /path/to/venv/bin/python
```

The interpreter defaults to `python3`. An optional second argument selects the
parent folder for results. The script creates a fresh disposable directory and
keeps complete setup, pytest, default-check and refusal-check logs, plus
`results.json`. It checks exits and diagnostics directly, without piping a test
run. It uses no remote and does not commit to this checkout. It requires a
POSIX shell, Python and Git. A successful run ends with:

```text
PASS: 14 cases verified; 12 shortcuts flagged by default and refused with --review refuse; honest changes pass with no warning.
```

See the [reference](reference.md#checks-against-a-base) and
[guarantees](guarantees.md) for review scope and limits.
