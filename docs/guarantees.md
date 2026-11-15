# What Workorder guarantees / what it does not

Test changes are refused unless a reviewer adds the label. `--review warn` is an
option that reports review findings without refusing them. A reviewer decides
whether the intended behavior is right.

| What Workorder checks | What it does not establish |
| --- | --- |
| Receipts record commands, results and the code they refer to. `done`, `receipt test`, `renew` and explicit `check --run-tests` execute fresh tests. | Anyone with write access can create a receipt. Receipt contents alone do not prove a command ran. |
| DONE/CLOSED claims are checked against evidence and dependencies. Failed current tests refuse in both review modes, including pytest failure or error summaries with exit 0. | Acceptance lists still need a person or agent to check them. |
| Plain `check` reads evidence; `check ID` explains one task. | Neither reruns tests or reviews all branch changes. |
| The pre-commit hook reviews test changes for tasks the commit touches and their dependents. Hooks report test-review notes and refuse changed task test commands. | Hooks can be skipped, are not cloned, and do not fetch missing refs. Unowned test edits are reviewed by `check --base`. |
| Unfinished tasks outside the changed task records and test files do not refuse unrelated changes. | Wrapper commands need explicit `test_paths`; `new` requests them before writing. |
| `check --base REF` reviews repository test files and every task whose tests changed. `--run-tests` also compares REF tests on current code. | Without `--run-tests`, it does not execute tests. Tests cannot cover behavior outside their commands. |
| File edits produce one finding naming their owners. `--allow tests` or any owning task accepts that file finding. | Task-specific outcome and selection findings need that task's approval to silence them. |
| Changed or removed test lines, REF failures, fewer passes, new skips or xfails, changed selection/configuration and edits to base-failing tests are refused until accepted. `--review warn` makes these findings warnings. | A reviewer decides whether a test or behavior change is appropriate. |
| `--all-tests --run-tests` runs every distinct DONE/CLOSED command and timeout once on current code; unchanged REF commands share that run. `--strict-tests` also reviews additions and refuses finished pytest runs with no passes. | Unfinished tasks' current failures are progress notes. |
| `--require-task` applies to non-test code. It needs a new counting receipt on a task unfinished at the merge base, or explicit `--allow ID` for a new or already finished task. | Renewing an unrelated finished task does not satisfy it without that approval. It does not prove the task describes every changed line. `--allow no-task` bypasses only this rule. |
| New implementation receipts retain changed paths, blob IDs, modes and deletions, so matching squashes survive deleted branches and fresh clones. | A different landing on those paths still needs fresh evidence. Old receipts retain their existing matching rules. |
| A branch receipt survives target-only changes when the current code exactly matches a clean merge of the receipt's code and the target. | Later branch edits and conflict resolutions still need fresh receipts. |
| Only implicit hook runs may reuse local results, marked `(reused from HH:MM UTC)`. | Reuse does not detect changed environment variables, services or ignored dependencies. Explicit commands run fresh; CI bypasses the persistent cache. |
| `--allow ID` accepts reviewed definition/test changes or complete retirement. | It never supplies missing evidence or excuses current failures for finished tasks, invalid dependencies or changed retained receipts. |

Recognised test material includes every file under folders whose names contain
`test` or `fixture`, and folders named `spec`, `__snapshots__` or `golden`, wherever
they are. Filenames include `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`,
`*.spec.*`, `*_spec.*`, `*_tests.*`, `testing.py`, `*_testing.py`, and any filename
starting with `expected`, `golden` or `snapshot`. This includes data files such
as `data/expected.json`. Pytest configuration and `conftest.py` are also reviewed.
A test at the repository root does not make all production files test material.
Other helpers need explicit `test_paths`; imports and wrapper commands do not
reveal every file a test uses. A script can itself be the declared test file.

Temporary test copies rebase internal absolute symlinks, including Windows
extended paths. Ignored dependencies are linked; when links are unavailable,
they are copied with a note. Writing through a shared dependency link changes
that dependency in the source checkout. Tracked source and test files are copied.

For command details and scope, see the [reference](reference.md).
