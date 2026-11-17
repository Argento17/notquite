# Reviewer guide

Reproduce a pull-request check with `workorder check --base origin/main --run-tests --all-tests --require-task`; use the actual target branch. Read each refusal below and the named test's output. `workorder check ID` explains one task's evidence.

Labels below must come from someone other than the pull request author. An approving review must cover the current head and come from another person. Locally, `--allow ID`, `--allow tests`, `--allow scope:ID`, `--allow no-task` and `--allow workflow` represent those reviewer labels. `--review warn` changes ordinary test-review findings to warnings; it does not accept explicit-label findings or missing evidence.

<!-- refusal-family records -->
## Record, configuration or dependency cannot be read
**Meaning:** A malformed task, missing field, invalid value/path/command, unreadable configuration or receipt, missing dependency, dependency cycle, or contradictory `blocks` prevents checking the record. A missing Git ref or merge base, or an incomplete/unreadable Git blob, also prevents a usable check.
**Clear:** Correct the named file or value, restore missing dependencies, remove cycles, or fetch the target ref; rerun `workorder check ID` and the original check. If Git cannot read a blob, restore the missing/corrupt Git data and retry; a cached read never supplies missing evidence. Labels do not repair malformed records. Set wrappers' `test_paths` to the actual test files; a changed definition needs the acceptance below.
Gate reads share validation until a test, hook/filter execution or Workorder write requires a fresh observation. Windows observations still reread file contents, including same-size edits with restored timestamps; a failed command also requires freshness before reuse.

<!-- refusal-family evidence -->
## Finished claim lacks evidence
**Meaning:** `NOT DONE`, `owes`, missing commits, stale receipts, a receipt ledger without a task, or contract evidence debt means DONE/CLOSED is unsupported. Contracts may require real artifacts; mock output cannot satisfy that requirement. Human dependencies and required merge/deploy/live evidence still matter.
**Clear:** Finish the missing work and record the requested receipt. For changed committed code, run `workorder renew --task ID` and commit its receipts. Use the named deployment/read-back commands where required. If unfinished, return the task to IN_PROGRESS with its failed receipt. No label supplies evidence or a missing dependency.
Protected test files retain their staleness checks, including doctests in Python docstrings and tests under docs/. Changing a task's test paths does not rewrite its old receipt's syntax map or by itself change another task's recorded code.
After a squash removes original commits, a renewed DONE/CLOSED claim still checks the recorded paths, deletions, modes and content. This rule also applies with Git read reuse disabled (`WORKORDER_GIT_CACHE=0`).

<!-- refusal-family current-tests -->
## A task's current test fails or runs nothing
**Meaning:** `its test failed`, `regression`, `no test passed`, missing test tools, missing imports, timeouts, collection errors or a missing concrete test command prevent a finished claim. A reopened task whose test fails also needs attention.
**Clear:** Install the project's test dependencies, correct the command, fix the code/test, and rerun `workorder renew --task ID`. For DONE/CLOSED, no label excuses a current failure or empty run. For an intentionally reopened IN_PROGRESS/BLOCKED task, only `workorder-allow:ID` accepts its failing test; approval alone does not. A pytest summary missing usable outcomes needs a working pytest run.
For a task being checked, staged comment or docstring edits to its code or tests still select the staged snapshot.
Test subprocesses refresh checkout bytecode on every run, so edits with the same size and timestamp are tested from current source. Dependency bytecode stays cached under the repository's Git directory; parallel runs lease separate cache slots. Checkout entries are removed after each run, including comparisons, and project `__pycache__` directories are untouched. Without writable Git metadata, runs use temporary caches.

<!-- refusal-family definitions -->
## New or changed task requirements
**Meaning:** `was written and finished in this pull request` requires definition review whenever a new task claims DONE/CLOSED, even if its test demonstrates the change. A new unfinished task supplying required code receipts keeps the earlier `is new in this pull request` rule: review is needed unless its test fails on base code and passes now. Scope/Acceptance edits, changed tests/contracts, removed dependencies, cancellation and unsupported reopening also change the agreement.
**Clear:** Prefer landing the task definition first, then implement it in another change. Otherwise a reviewer accepts the definition with `workorder-allow:ID` or an approving review. Restore unintended requirement edits. Fill empty Scope and Acceptance before `done`; acceptance review never supplies missing test evidence.

<!-- refusal-family test-edits -->
## Test files, selection or outcomes changed
**Meaning:** `test changed`, `test_paths changed`, edited/removed tests, changed fixtures/configuration, fewer passes or more skips/xfails/deselections need review. Added tests also need review with `--strict-tests` or when their old tests already failed. A new skip/xfail that actually suppresses a test counts; a false condition does not.
Explicit `test_paths` stay protected regardless of suffix. Otherwise declared `code_paths` and package source (such as `src/click/testing.py`) are code before filename/folder guesses apply. Configure those guesses with `test_file_patterns` and `test_directory_patterns` lists in `.workorder/config.json`; see the reference for defaults. Helpers, fixtures and non-code test material remain protected, including .txt, Markdown and doc/docs paths.
**Clear:** Restore unintended edits, or have a reviewer accept them with the owning task's label or an approving review. `workorder-allow:tests` accepts shared/unowned file edits, not a task's command, selection or outcome finding. Unchanged Python tests may gain new functions, fixtures, imports or appended parameter cases without review. A refactor with edited tests needs `done --needs-review` or `renew --no-refactor` and review.

<!-- refusal-family old-tests -->
## Earlier tests fail on the new code
**Meaning:** `its tests from ... fail on this code` means the change no longer satisfies the earlier test version. An unowned test can report the same regression.
**Clear:** Restore the behavior, or have a reviewer accept the intended behavior change/test rename with `workorder-allow:ID`; use `workorder-allow:tests` for an unowned test. Reviewer approval alone does not accept this failure. The current task test must still pass.

<!-- refusal-family weak-tests -->
## The test does not show the change works
**Meaning:** `asserts nothing`, tests passing on base code, or tests still passing with changed functions `stubbed out` show missing proof. New/changed pytest functions are checked together, so old unrelated tests cannot supply that proof. A false no-code-change exemption and a refactor whose earlier test fails are also refused. A refactor/verification completion needs review unless the base task already declares the matching `kind: refactor` or `kind: verification`. Hand-written DONE/CLOSED plus receipts with no production-code change follows the verification rule.
**Clear:** Test the changed result and rerun `done`. For a true refactor use `done --refactor` with unchanged tests passing before and after; use `--no-code-change` only when the task's code/tests are unchanged. A test passing on base code permits ordinary reviewer acceptance. Assertion/stub findings and exemptions not predeclared on the base require the explicit `workorder-allow:ID` label; approval and warning mode do not accept them. `done --needs-review` can record eligible findings pending that review, but cannot accept failed tests or an invalid exemption locally.
With a local target, `done --refactor`, `done --no-code-change` and pre-commit print one Note per task naming the required pull-request label when the base lacks the matching kind. A kind added only on the implementation branch does not remove the requirement.

<!-- refusal-family test-first -->
## The test did not land first
**Meaning:** `--require-test-first` found a finished task whose test was added or changed with the work, or whose earlier test cannot be established.
**Clear:** Land the task and failing test before implementation. For an intentional exception, a reviewer adds `workorder-allow:ID`; an approving review or warning mode does not suffice. Provenance is separate from whether an assertion is useful.

<!-- refusal-family scope -->
## Code lies outside the task's scope
**Meaning:** Changed production files fall outside the combined `code_paths` of tasks adding implementation/test receipts. Unrelated or unscoped receipts cannot disable declared scopes. This applies in light mode too.
**Clear:** Restore unrelated changes or record work under a task whose scope covers them. Otherwise a reviewer adds `workorder-allow:scope:ID` for a claiming task. An approving review, warning mode, a task's ordinary label and `no-task` do not accept scope findings.

<!-- refusal-family code-receipts -->
## No task records changed code
**Meaning:** `--require-task` found production changes without new, counting implementation/test evidence covering the current code. Renewing an unrelated finished task is insufficient.
**Clear:** Record the appropriate task's implementation or test and commit its receipts. A task written and finished in this change separately needs definition review. A reviewer can accept the applicable task or use `workorder-allow:no-task` for this requirement alone. Deliberate path exclusions use `--require-task-ignore GLOB`. Documentation outside test material and Python production-code edits with identical syntax apart from docstrings need no code receipt; comments and extra parentheses alone also do not qualify as a refactor.

<!-- refusal-family imports -->
## The test could not run against the intended copy
**Meaning:** `could not compare` can name an external project import, an unidentified interpreter, an unavailable dependency, an invalid pytest setup or a failed temporary-copy operation. It proves neither weakness nor a passing refactor.
**Clear:** Install dependencies and make the tests import this checkout's project, using a suitable pytest `pythonpath` or non-editable install; fetch missing refs and fix the named command/setup error. Recheck isolated Python commands too. An unavailable comparison can instead be accepted with `workorder-allow:ID` (`workorder-allow:tests` for an unowned file); approval and warning mode do not suffice. This accepts the missing comparison, never a failing current task test. Pytest exit codes include plain meanings, such as `5 (no tests were collected)`.
A predeclared task kind does not accept an unavailable comparison. `done --needs-review` can record it; pre-commit prints a Note and permits committing the pending claim, while the pull-request check keeps refusing until the explicit task label is supplied.
Fresh pytest comparisons verify imports in the test interpreter before loading conftests or tests, using pytest's selected configuration and plugins. This startup has a 60-second limit; an earlier declared test timeout still stops the run with exit 124, and the test's deadline is not reset after verification. Cached test results still require a fresh standalone import probe with its own 10-second limit. Missing or timed-out verification cannot establish a passing comparison. A missing-pytest warning requires a separate import probe to confirm absence; duplicate availability markers are refused.
Temporary comparison copies are removed after their Git processes exit, including failed or timed-out runs.

<!-- refusal-family workflow -->
## Workorder workflow changed
**Meaning:** The default workflow or a renamed generated workflow changed, including its removal.
**Clear:** Restore it, or have a reviewer inspect the change and add `workorder-allow:workflow`. Reviewer approval alone or warning mode does not accept it. `workorder init --update-workflow` previews and refreshes generated setup; review and commit the resulting file.

<!-- refusal-family project-test -->
## The optional project test finds a regression
**Meaning:** The project test failed. Workorder reruns identifiable failing pytest tests on the base: an old failure is a note, a newly failing test refuses, and an unavailable result stays explicit. Other test tools cannot supply this per-test distinction.
Passing pytest project tests print one note with pytest's counts. Failures show the failed-test summary, up to 60 lines per failing test (with a truncation notice), and counts; base reruns show only counts. Passing-test output is omitted. Startup and interrupted-run diagnostics remain visible; unreadable or missing reports cannot establish a base comparison.
**Clear:** Fix the regression or test environment. A reviewer may accept a project finding with `workorder-allow:tests` or an approving review; task-current failures still refuse. Old project failures need no label. Set the command with `workorder init --project-test "python -m pytest"`; without one, the note says only declared task tests ran. Pre-commit does not run this optional command.

<!-- refusal-family renewal -->
## Evidence predates a later code change
**Meaning:** `its code changed after it was finished` identifies a new completion whose recorded implementation differs from the code about to land. A separate message can say main moved since the task was tested.
**Clear:** Commit the updated code, merge/rebase the current target when requested, run `workorder renew --task ID`, and commit the receipts. `done` and pre-commit show renewal advice as a Note; the pull-request check requires current evidence. Renewing an unchanged finished claim already on the target does not owe fresh base-code or stub proof. Reopening and finishing it again makes a new claim.

<!-- refusal-family removal -->
## Task or receipt history was removed or changed
**Meaning:** A task was removed, receipts were edited/deleted, or retained receipt hashes/history no longer agree. Every commit since the base matters, including edits later restored.
**Clear:** Restore receipt history; append new evidence instead. `workorder repair --task ID` handles supported legacy append conflicts. A reviewer may retire a task finished/cancelled at the base with `workorder-allow:ID` or approval only when its task and all receipts are removed. Partial retirement, removal of unfinished work, changed retained receipts and broken dependencies still refuse.

<!-- refusal-family pending -->
## Completion is waiting for review
**Meaning:** `pending review` records an intentional exception from `done --needs-review`; local DONE does not mean the pull request has been accepted. The printed findings may name other affected tasks or shared tests too.
**Clear:** Review every recorded finding. Add `workorder-allow:ID` for each required task, or approve the current head where those findings permit approval; explicit-label findings keep that rule. Renewing does not clear an unmerged review request. After the tested implementation lands on the target, the pending request no longer blocks it.

Diagnostics escape characters the console cannot encode, preserving test IDs and paths. Brief import-report sharing locks are retried, including the final read; missing, persistently unreadable or invalid evidence still refuses.

The test in `tests/test_v16b_guide.py` requires these meanings and remedies, and records a hash of each Python module's executable messages, conditions and returned findings. A new or changed refusal requires guide review before updating those hashes. The check also catches some unrelated code edits; comments and docstrings are ignored. It detects changes, but cannot judge whether an explanation is correct.

<!-- refusal-inventory workorder/__init__.py c98fe59586516c5a002b3bd50cc92e0e258a1dd45a0f0eb5ed923b719d699afb records -->
<!-- refusal-inventory workorder/__main__.py cb39dacc1a942b75221dd33c3d3828202d9efb797bf559090b1b4a407ae8d5d6 records -->
<!-- refusal-inventory workorder/bootstrap.py afbf495c278cce882cf2ada62aa0733d152031899bc34b014662db31bf13b82e records,workflow,project-test -->
<!-- refusal-inventory workorder/cli.py 80e0b2ffe288e3cbb46c898776b31fa0c8ca317547390b86c8c92928246576dc records,evidence,current-tests,definitions,weak-tests,test-first,pending,renewal,project-test -->
<!-- refusal-inventory workorder/gate.py 61f972f495d0ffb5033be3b3308d34a5af41afe3509c38894b561f6fa552c67b code-receipts,current-tests,definitions,evidence,imports,old-tests,pending,project-test,records,removal,renewal,scope,test-edits,test-first,weak-tests,workflow -->
<!-- refusal-inventory workorder/hooks.py 4c890e78327bd094ac6f84882d7e42640cb88130f5232d92f7f23bbea6502a79 evidence,current-tests,test-edits,pending,renewal -->
<!-- refusal-inventory workorder/project_report.py 974ac6ecacb3575182a0903cdff7d011d68e052daf0deffea905cb03443d1346 project-test -->
<!-- refusal-inventory workorder/project_tests.py 1730660955101d7371e31e3502dc368de1af06e7bd6c400acc84f4309c54c0a9 project-test,records -->
<!-- refusal-inventory workorder/pytest_selection.py bcff8a23918c0320f921d46b229359bc41237be3055be093a6e746860395d35b weak-tests,imports -->
<!-- refusal-inventory workorder/python_imports.py 6c7412fae56fed15a3a03a97cce2e13a2c817e8ad8d3edacc2d5f84de537d420 imports -->
<!-- refusal-inventory workorder/python_stubs.py a241120e76a9632ba6013f520a30b13afcb8e06faa69af57175829255950cbda weak-tests -->
<!-- refusal-inventory workorder/python_tests.py 31645fe31f89adb02d2d05c83eb6844a9f8adf5c8c554e26a49996c6e23313ac test-edits -->
<!-- refusal-inventory workorder/review_labels.py c473f08753a21712cbb29e516376bacd376c55a7523f59a9f8985294cc5a6f0f definitions,test-edits,old-tests,scope,workflow,pending,project-test -->
<!-- refusal-inventory workorder/source.py 0add0ef1557d79323de7dbb42b2454f5db074fcf0e8272f538d125de536fbd6d code-receipts,renewal,weak-tests -->
<!-- refusal-inventory workorder/store.py 01600835b4480ed2a4fbbcee622ff196731da965b99b052e00891c8eaabc18b1 records,evidence,current-tests,removal -->
<!-- refusal-inventory workorder/git_reads.py c406e897d3776a2eefb175775f1cf8144f2d6991a666432e1337bb739c5c0371 records,evidence -->
<!-- refusal-inventory workorder/console.py 6634087244aa3cbf3741281f4b740117043f662e24e309c6c5af10d7c543b681 records,current-tests -->
