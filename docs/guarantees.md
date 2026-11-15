# What Workorder guarantees / what it does not

Ordinary test changes need a reviewer label or a current-head approving review.
`--review warn` reports those findings without refusing them. The explicit-label
exceptions below still refuse. A reviewer decides whether the intended behavior
is right.

The hooks run the test commands written in task files. Task files deserve the
same review as code. `show` and plain `check` read recorded receipts; they do not
rerun those commands. Use `check --run-tests` for a fresh run.

| What Workorder checks | What it does not establish |
| --- | --- |
| Receipts record commands, results and the code they refer to. `done`, `receipt test`, `renew` and explicit `check --run-tests` execute fresh tests. | Anyone with write access can create a receipt. Receipt contents alone do not prove a command ran. |
| DONE/CLOSED claims are checked against evidence and dependencies. Failed current tests refuse in both review modes, including pytest failure or error summaries with exit 0. | Acceptance lists still need a person or agent to check them. |
| Plain `check` reads evidence; `check ID` explains one task. | Neither reruns tests or reviews all branch changes. |
| The pre-commit hook reviews test changes for tasks the commit touches and their dependents. Hooks note command repairs for tasks already finished at the base when the new command passes; unfinished tasks with no receipts may repair a command locally. Other command changes refuse. | Hooks can be skipped, are not cloned, and do not fetch missing refs. Unowned test edits are reviewed by `check --base`. |
| Unfinished tasks outside the changed task records and test files do not refuse unrelated changes. | Wrapper commands need explicit `test_paths`; `new` requests them before writing. |
| `check --base REF` reviews repository test files and every task whose tests changed. `--run-tests` also compares REF tests on current code. | Without `--run-tests`, only reopened finished tasks require a current run. Tests cannot cover behavior outside their commands. |
| File edits produce one finding naming their owners. `--allow tests` or any owning task accepts that file finding. | Task-specific outcome and selection findings need that task's approval to silence them. |
| Changed or removed test lines, REF failures, fewer passes, new skips or xfails, changed selection/configuration and edits to base-failing tests are refused until accepted. `--review warn` makes these findings warnings. | A reviewer decides whether a test or behavior change is appropriate. |
| `--all-tests --run-tests` runs every distinct DONE/CLOSED command and timeout once on current code; unchanged REF commands share that run. `check/done --jobs N` runs independent current commands concurrently, defaulting to the CPU count, and prints results in task order. | Unfinished tasks' current failures are progress notes, except when the task was finished at the base. More workers do not make a test stronger. |
| A DONE/CLOSED task reopened as IN_PROGRESS/BLOCKED may record failure locally. The base check reruns it and needs its explicit task label if the current test fails. | A failed receipt, approving review or warning mode does not accept that pull request failure. |
| `--require-task` applies to non-test code. It needs a new counting receipt on a task unfinished at the merge base, a new task proven red before green, or explicit `--allow ID`. Lockfiles are excluded; repeated `--require-task-ignore GLOB` adds path exceptions. | Renewing an unrelated finished task does not satisfy it without that approval. It does not prove the task describes every changed line. `--allow no-task` and path exceptions affect only this rule. |
| New implementation receipts retain changed paths, blob IDs, modes and deletions, so matching squashes survive deleted branches and fresh clones. | A different landing on those paths still needs fresh evidence. Old receipts retain their existing matching rules. |
| A branch receipt survives target-only changes when the current code exactly matches a clean merge of the receipt's code and the target. A rebase also counts when recorded paths match the receipt and every other path matches the target. | Later branch edits and changes to recorded paths still need fresh receipts. |
| Only implicit hook runs may reuse local results, marked `(reused from HH:MM UTC)`. | Reuse does not detect changed environment variables, services or ignored dependencies. Explicit commands run fresh; CI bypasses the persistent cache. |
| `--allow ID` accepts reviewed definition/test changes or complete retirement. | It never supplies missing evidence or excuses current failures for finished tasks, invalid dependencies or changed retained receipts. |
| When a finished task's own code or tests changed, its current test must fail on the merge-base code with its new or changed tests. `done --refactor` accepts unchanged tests passing before and after; `done --no-code-change` exempts verification runs only when no code files changed; receipt exemptions are checked too. | Unrelated progress notes do not trigger this comparison. A failure does not establish that every assertion checks the requirement. Missing external dependencies do not count as proof. |
| `done` and finished-task base checks replace the task's changed or added Python function bodies with `raise NotImplementedError('workorder stub')` in one temporary copy and run its command once. Declared code paths set the boundary; without them, other tasks' declared paths are excluded. With no matching function there is no stub check. A command that still passes needs the task's explicit reviewer label in the base check. | Declared refactors and valid no-code-change runs are exempt. This covers Python functions only; a failure does not show that every function was exercised. Approval and warning mode do not accept a passing result. |
| Every added or changed Python test function needs a nonconstant assertion or a recognised assertion helper. Warning/exception/log assertion contexts and pytest.fail count; nearby helpers are followed for up to four calls. Equality of a name, attribute or subscript with itself is refused too. | This is a syntax check, not a judgment of assertion meaning. Unchanged functions are ignored even in edited files. Its task label can accept unusual syntax or a smoke test; approval alone and warning mode cannot. |
| Comparison copies put their src/ and root first on PYTHONPATH and probe project package locations with the test interpreter before using a result, including cache hits. Outside imports require the explicit task label. | An unavailable comparison proves neither weakness nor a passing refactor. The probe cannot prevent a test or plugin from changing imports later. |
| `done --needs-review` writes DONE with a pending review receipt. Local hooks note it; the pull request needs a task label or an approving review. After merge, main accepts it. | The local record cannot tell whether a remote review has been submitted. |
| No-code-change, refactor, pending-review and new-test-origin notes name each task, even when ordinary notes are grouped. Missing base task or test evidence never produces a claim that the test landed earlier. | The origin statement describes available Git history; it cannot recover missing history. |
| Refused `done` runs leave Progress and receipts unchanged. `new --code-paths` sets optional scope; `done` names changed code when scope is absent. | Out-of-scope files require `workorder-allow:scope:ID` or approval; test-change labels do not cover them. Scope is optional and does not prove that changed lines satisfy Acceptance. |
| `done` and task-related staged checks run finished tasks whose recorded implementation paths or selected test paths changed. Staged checks compare staged code with the local merge base, including the incoming parent during a merge commit. | They do not fetch; a missing target is reported by `done`. Old receipts without paths require a broader code scope. |
| Generated workflows accept allowance labels only when the latest add event names someone other than the pull request author. | Label access still needs repository review rules. A label never excuses a finished task's current failure. |
| Base checks require `workorder-allow:workflow` for changes to the default Workorder workflow or a renamed generated workflow identified by its review-label command. | An approving review and warning mode cannot accept this. Repository rules must still require the workflow to run. |
| An approving review accepts ordinary review findings. Old tests failing on new code need the owning task's label, or `tests` for an unowned file. | Approval alone does not accept these behaviour changes. Unowned Python tests are compared using pytest; other unowned files receive static review. |
| `--require-test-first` refuses finished claims with tests added or changed in the change unless the task label accepts them. `new --test-first` requires existing, failing tests. | A failing test can still be weak. Neither provenance nor assertion syntax establishes that it checks the requirement. |

Generated workflows detect PEP 735 test/tests/dev groups and install them with pip 25.1 or newer. `renew` preserves a refactor declaration across rebases.

Version 0.15 retains 0.14 receipts without migration. `init` lists its written
files and prints the exact commit command; the local hook is listed but excluded
from staging. Local task numbering respects reservation tags already fetched,
but cannot know reservations absent from this clone.

Recognised test folders have whole names `test`, `tests`, `testing`, `__tests__`,
`spec`, `specs`, `fixtures`, `testdata`, `test_data`, `testsupport`, `test_support`,
`golden` or `__snapshots__`, or start with `test_` or end with `_test`/`_tests`,
case-insensitively. All files below them count. `latest` and `contest` do not.
Filenames include `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`,
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
