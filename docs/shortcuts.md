# Shortcuts Workorder refuses

A test command can exit successfully after its useful assertion has disappeared
or when it never covers the new code. The first seven examples introduce the
same bug: multiplication adds instead when the first argument is negative. The
original test checks `mul(-3, 4) == -12`; the bug returns `1`.

All ten shortcuts keep their own selected test command passing. Workorder
refuses the first seven without reviewer acceptance. The eighth adds broken
division without task evidence. The ninth is an honest feature, accepted without
a label. Cases 10 and 11 weaken multiplication's assertion while recording
unrelated task activity; both refuse even though multiplication's task file and
receipts are untouched. Case 12 refactors `src/app.py` while a root-level test
file already exists and the tests stay unchanged; the check accepts it.

Each example starts in its own disposable Git repository. `main` has correct
multiplication, two passing tests, and a completed `TASK-1`. For cases 1–7 the
changed code is committed and `workorder renew --task TASK-1` records fresh
implementation and passing-test receipts. Stale evidence is not the reason for these refusals. Test review also covers
tests of tasks whose records remain untouched, as cases 10 and 11 demonstrate.

Every Workorder result below comes from:

```sh
workorder check --base main --run-tests --all-tests --require-task
```

The own-test command is `python -m pytest -q tests/test_calc.py`, except case
7, which runs its narrowed command, and cases 10–12, which run all of `tests/`. Output excerpts retain the actual
messages and pytest summaries; elapsed times vary.

Captured with `workorder 0.9.0` and pytest 9.2.0 on Linux.

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
evidence: `--require-task` finds no new counting implementation or test receipt
on a task with a concrete command. It does
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

All three tests pass. Both tasks' test comparisons preserve the base tests.
The test additions need no label; no existing assertion or configuration was
weakened.

Own test (exit 0):

```text
3 passed in 0.00s
```

Workorder (exit 0):

```text
check: OK
```

## 10. Add an unrelated non-counting receipt

Change the multiplication function to the negative-number bug and change its
assertion to expect `1`. Add an unrelated passing test and a new `TASK-2` with
`test: any`. After committing, record:

```sh
workorder receipt test --task TASK-2 -- python -c pass
```

The command passes, but the receipt does not count: TASK-2 has no implemented
receipt. Recording it exits 1. Commit the receipt anyway; TASK-1's task file and
receipts stay untouched. The current three tests pass, but TASK-1's edited test
is reviewed and its REF test exposes the bug. The new receipt also fails to
satisfy `--require-task`, which needs counting evidence on a concrete command.

Own test (exit 0):

```text
3 passed in 0.00s
```

Workorder (exit 1):

```text
TASK-1: its test files lost or changed 1 lines since main (tests/test_calc.py); a reviewer accepts it with --allow TASK-1
TASK-1: its tests from main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
this change edits code but no task records it with a new implemented or test receipt; run workorder receipt implemented --task ID or workorder receipt test --task ID
```

## 11. Work on division while weakening multiplication

At `main`, TASK-1 declares `python -m pytest -q tests/mul`, and the open TASK-2
declares `python -m pytest -q tests/div`. On the feature branch, add correct
division and its test in `tests/div/test_div.py`. In the same change, introduce
the multiplication bug and change `tests/mul/test_calc.py` to expect its wrong
answer. Record completion only for TASK-2:

```sh
workorder done --task TASK-2
```

TASK-2 now has counting evidence and its tests pass. TASK-1's task file and
receipts remain byte-for-byte unchanged; the runner checks this. All three
current tests pass, but the check reviews TASK-1 because its selected tests
changed. Its original multiplication assertion still fails on the buggy code.

Own test (exit 0):

```text
3 passed in 0.00s
```

Workorder (exit 1):

```text
TASK-1: its test files lost or changed 1 lines since main (tests/mul/test_calc.py); a reviewer accepts it with --allow TASK-1
TASK-1: its tests from main fail on this code; a reviewer accepts this intended behavior change or test rename with --allow TASK-1
```

## 12. Refactor production code while a root-level test file exists

At `main`, `test_probe.py` sits at the repository root and `tests/test_app.py`
imports `src.app`. On the feature branch, change only the body of `shape` in
`src/app.py` (`return text.strip()` becomes `return ''.join(text.split())`).
The tests are byte-for-byte unchanged. `workorder renew --task TASK-1` records
the current code, then:

```sh
workorder check --base main --run-tests --all-tests --require-task
```

The root test file does not make `src/app.py` a test file, so the check accepts
the refactor.

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
PASS: 12 cases verified; 10 shortcuts refused; honest changes accepted without --allow.
```

The runner uses POSIX shell and Python instead of platform-specific `sed -i`
forms. It was verified on Linux; macOS execution has not been tested here.

## What reviewer acceptance means

These refusals identify changes that need review; they do not decide whether a
behavior change is desirable. With `--allow TASK-1`, a reviewer can accept that
task's changed test lines or configuration, failed replay of the base tests,
fewer passing tests, or more skipped, xfailed, or deselected tests. Current
declared tests for finished tasks must still pass, missing evidence still matters, and `--all-tests` runs every completed task's current command.
Failed or missing current tests for unfinished work are notes; test edits and
REF regressions still need review.

Comparisons against base tests cover tasks being worked on and every task
whose tests changed. Repository tests outside those tasks are still reviewed;
`--allow tests` accepts only unowned test changes. New counting implementation
or test receipts on tasks with concrete commands satisfy `--require-task`;
notes, status edits and non-counting receipts do not. `--allow no-task` bypasses
only that receipt requirement. See the [base-check reference](reference.md#checks-against-a-base)
for the policy and its limits.
