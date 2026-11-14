# Shortcuts Workorder refuses

A test command can exit successfully after its useful assertion has disappeared
or when it never covers the new code. The first seven examples introduce the
same bug: multiplication adds instead when the first argument is negative. The
original test checks `mul(-3, 4) == -12`; the bug returns `1`.

All eight shortcuts keep their own selected test command passing. Workorder
refuses the first seven without reviewer acceptance. The eighth adds broken
division code outside the existing tests and records no task work, so
`--require-task` refuses it. The ninth is an honest new feature, accepted without
a label or `--allow`.

Each example starts in its own disposable Git repository. `main` has correct
multiplication, two passing tests, and a completed `TASK-1`. For cases 1–7 the
changed code is committed and `workorder renew --task TASK-1` records fresh
implementation and passing-test receipts. Those receipts put the task in scope
for comparison; stale evidence is not the reason for these refusals.

Every Workorder result below comes from:

```sh
workorder check --base main --run-tests --all-tests --require-task
```

The own-test command is `python -m pytest -q tests/test_calc.py`, except for case
7, which runs the narrowed command it claims. Output excerpts retain the actual
messages and pytest summaries; elapsed times vary.

Captured with `workorder 0.8.0` and pytest 9.2.0 on Linux.

## 1. Replace the assertion with `assert True`

```diff
-    assert mul(-3, 4) == -12
+    assert True
```

The changed assertion always succeeds. Removed or changed test lines require
reviewer acceptance, and replaying the tests from `main` exposes the bug.

Own test (exit 0):

```text
2 passed in 0.00s
```

Workorder (exit 1):

```text
TASK-1: its test files lost or changed 1 lines since main (tests/test_calc.py); a reviewer accepts it with --allow TASK-1
TASK-1: its tests from main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
```

## 2. Add a skip marker

```python
@pytest.mark.skip(reason="flaky")
def test_mul_negative():
    assert mul(-3, 4) == -12
```

One remaining test passes. The new skip marker requires reviewer acceptance.

Own test (exit 0):

```text
1 passed, 1 skipped in 0.00s
```

Workorder (exit 1):

```text
TASK-1: new skip or xfail behavior since main (tests/test_calc.py: pytest.mark.skip); a reviewer accepts it with --allow TASK-1
```

## 3. Call `pytest.skip()`

```python
def test_mul_negative():
    pytest.skip("later")
    assert mul(-3, 4) == -12
```

The assertion is still present but never runs. Workorder detects the new skip call.

Own test (exit 0):

```text
1 passed, 1 skipped in 0.00s
```

Workorder (exit 1):

```text
TASK-1: new skip or xfail behavior since main (tests/test_calc.py: pytest.skip); a reviewer accepts it with --allow TASK-1
```

## 4. Skip through `conftest.py`

```python
import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "negative" in item.name:
            item.add_marker(pytest.mark.skip(reason="env"))
```

The test file is unchanged. Adding this root `conftest.py` changes pytest's
configuration and requires reviewer acceptance.

Own test (exit 0):

```text
1 passed, 1 skipped in 0.00s
```

Workorder (exit 1):

```text
TASK-1: pytest configuration changed since main (conftest.py); a reviewer accepts it with --allow TASK-1
```

## 5. Delete the failing test

```diff
-def test_mul_negative():
-    assert mul(-3, 4) == -12
```

The surviving test passes. Workorder counts the removed lines and restores the
base test in a temporary copy for its comparison run.

Own test (exit 0):

```text
1 passed in 0.00s
```

Workorder (exit 1):

```text
TASK-1: its test files lost or changed 3 lines since main (tests/test_calc.py); a reviewer accepts it with --allow TASK-1
TASK-1: its tests from main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
```

## 6. Change the expected value to match the bug

```diff
-    assert mul(-3, 4) == -12
+    assert mul(-3, 4) == 1
```

Both current tests pass, but the changed expectation requires acceptance and the
original test still fails on the buggy code.

Own test (exit 0):

```text
2 passed in 0.00s
```

Workorder (exit 1):

```text
TASK-1: its test files lost or changed 1 lines since main (tests/test_calc.py); a reviewer accepts it with --allow TASK-1
TASK-1: its tests from main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
```

## 7. Narrow the command with `-k`

```sh
python -m pytest -q tests/test_calc.py -k 'not negative'
```

The task's `test` field is changed to that command and its new receipt passes.
Workorder reports both the selection change and the changed task requirement.
The full suite would still fail; the green result below belongs to the narrowed
command.

Own test (exit 0):

```text
1 passed, 1 deselected in 0.00s
```

Workorder (exit 1):

```text
TASK-1: pytest selection changed with -k or --deselect since main; a reviewer accepts it with --allow TASK-1
TASK-1: test changed: 'python -m pytest -q tests/test_calc.py' -> "python -m pytest -q tests/test_calc.py -k 'not negative'" since main; a reviewer accepts it with --allow TASK-1
```

## 8. Add broken code without recording a task

```python
# Added to calc.py; mul and its tests stay unchanged:
def div(a, b):
    return a * b
```

Commit only this code addition, keeping the original tests and all task records.
The new `div(6, 3)` returns `18` instead of `2`. Both existing multiplication tests
still pass because they never call division. Workorder refuses the missing task
evidence: `--require-task` finds no new implementation or test receipt. It does
not discover the division bug from the passing multiplication tests. A Progress
note or a status edit alone would not supply the required new receipt.

Own test (exit 0):

```text
2 passed in 0.00s
```

Workorder (exit 1):

```text
this change edits code but no task records it with a new implemented or test receipt; run workorder receipt implemented --task ID or workorder receipt test --task ID
```

## 9. Honest change: a new feature and test

```python
# Added to calc.py:
def div(a, b):
    return a / b


# Added to tests/test_calc.py:
def test_div():
    from calc import div
    assert div(6, 3) == 2
```

Multiplication stays correct and its tests are preserved. `TASK-2` describes
division on `main` before implementation; the change adds the function and test,
then records completion with `workorder done --task TASK-2`. Defining the task
beforehand avoids the separate review requirement for a task first created and
claimed DONE in the same change.

All three tests pass. The new task's test comparison passes, and the old
completed task needs no label because its task file and receipts are untouched.

Own test (exit 0):

```text
3 passed in 0.00s
```

Workorder (exit 0):

```text
check: OK
```

## Reproduce the catalogue

From a clone of this repository, use a Python environment with Workorder and
pytest already installed:

```sh
sh examples/shortcuts.sh /path/to/venv/bin/python
```

The Python argument defaults to `python3`. An optional second argument chooses
the parent directory for results:

```sh
sh examples/shortcuts.sh /path/to/venv/bin/python /tmp
```

The script uses the Workorder installed for that interpreter and prints its
version. It creates a unique `workorder-shortcuts-*` directory, with a disposable
repository and complete `setup.txt`, `pytest.txt`, and `check.txt` logs for every
case, plus `environment.txt` and `results.json`. It checks expected process exits
and diagnostic text, exits nonzero on a mismatch, and preserves the logs. It
never deletes a supplied directory, contacts a remote, or commits to the clone
from which it was launched. No reported test run is piped into a filtering tool.
A successful run ends with:

```text
PASS: 9 cases verified; 8 shortcuts refused; honest feature accepted without --allow.
```

The runner uses POSIX shell and Python instead of platform-specific `sed -i`
forms. It was verified on Linux; macOS execution has not been tested here.

## What reviewer acceptance means

These refusals identify changes that need review; they do not decide whether a
behavior change is desirable. With `--allow TASK-1`, a reviewer can accept that
task's changed test lines or configuration, failed replay of the base tests,
fewer passing tests, or more skipped, xfailed, or deselected tests. The current
declared test must still pass, missing evidence still matters, and `--all-tests`
still runs other completed tasks' current commands.

Comparisons against base tests apply to tasks whose task file or receipts are
touched. `--all-tests` separately checks current regressions in the remaining
completed tasks. New implementation or test receipts satisfy `--require-task`;
notes and status edits alone do not. See the [base-check reference](reference.md#checks-against-a-base)
for the policy and its limits.
