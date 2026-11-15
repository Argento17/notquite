# What Workorder guarantees / what it does not

| What Workorder checks | What it does not establish |
| --- | --- |
| Receipts record commands, results and the code they refer to. | Anyone with write access can create a receipt. A receipt alone does not prove a command ran. |
| DONE/CLOSED claims are checked against evidence and dependencies. | Acceptance lists are not checked. A passing test does not prove a requirement is right or complete. |
| Plain `check` reads task records and evidence; `check ID` explains one task. | These modes do not rerun tests or inspect all changes since another branch. |
| `check --staged` checks staged work and dependents, including local test comparisons when the remote target is available. | Hooks can be skipped, are not cloned, and do not fetch missing refs. |
| `check --base REF` reviews repository test roots and every task whose tests changed, even if its task record is untouched. | Without `--run-tests`, it does not execute tests. |
| A task owns the files or folders its command or `test_paths` names, plus pytest configuration on that path. `--allow tests` accepts every changed test line. An added line in a test that already failed at the base needs `--allow ID`. | Pytest `-k` and `--deselect` changes, and a base test that fails on the new code, still need `--allow ID`. Assertion quality is a review decision. |
| `--base REF --run-tests` replays affected tasks' REF tests on current code, then checks failures on REF code before blaming the change. A finished task whose base tests failed must have those tests pass on the new code unless `--allow ID`. | Reviewer acceptance cannot supply a missing command, dependency or comparison result. |
| `--all-tests --run-tests` runs every DONE/CLOSED task's concrete command, with or without a base. Finished tasks' current failures refuse. | It cannot test behavior outside those commands. Unfinished tasks' failed or missing current tests are progress notes. |
| `--strict-tests` requires acceptance for protected additions and rejects completed tasks' pytest runs with no passes. | It cannot judge the quality of an assertion. |
| `--require-task` requires a new counting receipt on a task with a concrete command. `--allow no-task` bypasses only this rule. | It does not prove that the task describes every changed line. Notes and non-counting receipts do not satisfy it. |
| Discovery covers `test/`, `tests/`, `__tests__/`, `spec/`, `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`, `*.spec.*`, `*_spec.*`, `*_tests.*`, `conftest.py` and pytest configuration. A root-level test file or an import does not make production code a test file. Named tests count in either the base or current code; a finished task mapping to neither is refused when the base has tests. | Wrappers and dynamic imports require explicit `test_paths`; Workorder cannot infer arbitrary runtime behavior. |
| `done` and Stop check completion claims against a locally available remote target. Stop refusals exit 2 on stderr. Session start re-reads claim and release tags. | Claims made only in chat cannot be checked. A failed tag read keeps the claims already in this clone. |
| Local test results are reused for identical code, command and timeout; explicit receipt tests and renewals run freshly. A cached result still counts as a run. | Reusing a result does not detect changes outside those cache inputs. CI reruns bypass the persistent local cache. |
| `--allow ID` accepts reviewed definition/test changes or complete retirement. | It does not excuse a finished task's current failure, missing evidence, invalid dependencies or changed retained receipts. |

For command details and scope, see the [reference](reference.md).
