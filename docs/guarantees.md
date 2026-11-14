# What Workorder guarantees / what it does not

| What Workorder guarantees | What it does not |
| --- | --- |
| Receipts record commands, results and the code they refer to. | Receipts are records anyone with write access can create. They do not prove that a command ran. |
| The workflow reruns declared tests on the code under review. | A stored passing receipt is no substitute for that CI rerun, which is what tests the code. |
| DONE/CLOSED claims are checked against recorded evidence and dependencies. | Acceptance lists are not checked. A passing test does not prove the requirement is right or complete. |
| Plain `check` reads all task records and their evidence. `check ID` explains one task. | These modes do not rerun tests or examine all definition changes since another branch. |
| `check --staged` checks staged task changes and dependents. When a remote target is available locally, it also compares and runs the task's tests. | Hooks can be skipped, are not cloned, and do not fetch missing refs. |
| `check --base REF` adds receipt history, definition and test-file checks for changed tasks. | Without `--run-tests`, it does not execute the tests. |
| `--base REF --run-tests` adds current tests and REF tests on current code for tasks being worked on. Failing REF tests are checked on REF code before blaming this change. | A missing command or dependency cannot be resolved by reviewer acceptance. |
| `--all-tests` also runs every DONE/CLOSED task's declared command, once per command and timeout. | It cannot test behavior outside those commands and assertions. |
| `--strict-tests` requires acceptance for all protected test edits and refuses pytest runs with no passes. | It cannot judge the quality of an assertion. |
| `--require-task` requires a new implementation or test receipt for code changes. | It does not prove that the task describes every changed line. |
| Default test coverage includes collection folders, helpers and imports within repository test roots. | Wrappers and dynamic imports need explicit `test_paths` to describe hidden tests. |
| `done` and Stop refuse test-change problems against a locally available remote target, with reviewer-label advice. | Chat-only completion claims cannot be checked. Local refs may be out of date. |
| `--allow ID` records a review decision in the check output for definition/test changes or complete retirement. | It does not excuse current test failures, missing evidence or changed receipts on a retained task. |

For command details and scope, see the [reference](reference.md).
