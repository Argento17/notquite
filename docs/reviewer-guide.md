# Reviewer guide

Reproduce a pull-request check with `workorder check --base origin/main --run-tests --all-tests --require-task`; use the actual target branch. Read each refusal below and the named test's output. `workorder check ID` explains one task's evidence.

**The review rule:** an approving review clears every finding except changes to existing tests (edited, removed, skipped, weakened, or existing tests that now fail, including project tests) and changes to the Workorder workflow file; those need a label (`workorder-allow:tests` or the narrower `workorder-allow:ID`; `workorder-allow:workflow`). Each refusal says which: "an approving review clears this" or "a reviewer accepts it with the label ... (an approving review is not enough: ...)". No review or label excuses a current test failure or missing evidence.

Locally, `done` refuses what this check would refuse and names the label; an intended test change is recorded with `done --needs-review`, and the task's own label then clears it. Pre-commit and Stop refuse those findings only with a new DONE claim, and refuse a hand-set DONE that no `done` run recorded.

| Finding | What clears it |
|---|---|
| A finished task's test fails now, runs no tests, or times out | Fix it; nothing else |
| A DONE claim lacks evidence, or its code changed after it was finished | Finish the work or `workorder renew --task ID`, and commit the receipts |
| A task, receipt or configuration cannot be read; receipts edited or deleted; a dependency missing | Fix or restore the named file |
| A project test that already failed on the base | Nothing; it is a Note |
| A task written and finished in this pull request, or its Scope, Acceptance or dependencies changed | Approving review, or `workorder-allow:ID` |
| Changed code that no task records (`--require-task`) | Approving review, or `workorder-allow:no-task` |
| Changed code outside the task's `code_paths` | Approving review, or `workorder-allow:scope:ID` |
| The test asserts nothing, passes on the base code, or still passes with the change stubbed out | A better test; or approving review, or `workorder-allow:ID` |
| A refactor or no-code-change completion the base task did not declare | Approving review, or `workorder-allow:ID` |
| The test did not land first (`--require-test-first`) | Approving review, or `workorder-allow:ID` |
| The comparison with the base could not run | Fix the setup; or approving review, or `workorder-allow:ID` |
| A reopened task's test fails; a task retired; a finished task removed with all its receipts | Approving review, or `workorder-allow:ID` |
| An existing test edited, removed, skipped or weakened, or a task's test command or test paths changed | `workorder-allow:ID` (covers what `done --needs-review` recorded), or `workorder-allow:tests` for shared files; not an approving review |
| Existing tests fail on the new code, including the project test | `workorder-allow:ID` (covers what `done --needs-review` recorded), or `workorder-allow:tests` for project and unowned tests; not an approving review |
| The Workorder workflow file changed | `workorder-allow:workflow`; not an approving review |

Labels below must come from someone other than the pull request author. An approving review must cover the current head and come from another person. In solo mode (`workorder init --solo`), a label the author added counts only when GitHub confirms the author as the repository owner or an admin; it clears what the same label would and prints as `accepted by the owner (solo mode; no independent review)`, never as review, and the summary line ends with `solo mode: ...`. Read such a run as the owner's acceptance only: nobody else looked at it. Locally, `--allow ID`, `--allow tests`, `--allow scope:ID`, `--allow no-task` and `--allow workflow` represent those reviewer labels, and `--approving-review` an approving review; in solo mode `--owner-accepts LABELS` represents the owner's own labels. `--review warn` changes ordinary test-review findings to warnings; it does not accept explicit-label findings or missing evidence.

<!-- refusal-family records -->
## Record, configuration or dependency cannot be read
**Meaning:** A malformed task, missing field, invalid value/path/command, unreadable configuration or receipt, missing dependency, dependency cycle, or contradictory `blocks` prevents checking the record. A missing Git ref or merge base, or an incomplete/unreadable Git blob, also prevents a usable check.
**Clear:** Correct the named file or value, restore missing dependencies, remove cycles, or fetch the target ref; rerun `workorder check ID` and the original check. If Git cannot read a blob, restore the missing/corrupt Git data and retry; a cached read never supplies missing evidence. Labels do not repair malformed records. Set wrappers' `test_paths` to the actual test files; a changed definition needs the acceptance below.
Gate reads share validation until a test, hook/filter execution or Workorder write requires a fresh observation. When the repository is too large to observe, only reads that cannot change are shared (objects by full ID, commit listings, index listings while the index file is unchanged) plus the work-tree diff within one comparison; pre-commit returns at once when only documentation no task names is staged. Windows observations still reread file contents, including same-size edits with restored timestamps; a failed command also requires freshness before reuse.

<!-- refusal-family evidence -->
## Finished claim lacks evidence
**Meaning:** `NOT DONE`, `owes`, missing commits, stale receipts, a receipt ledger without a task, or contract evidence debt means DONE/CLOSED is unsupported. Contracts may require real artifacts; mock output cannot satisfy that requirement. Human dependencies and required merge/deploy/live evidence still matter.
**Clear:** Finish the missing work and record the requested receipt. For changed committed code, run `workorder renew --task ID` and commit its receipts. Use the named deployment/read-back commands where required. If unfinished, return the task to IN_PROGRESS with its failed receipt. No label supplies evidence or a missing dependency.
Protected test files retain their staleness checks, including doctests in Python docstrings and tests under docs/. Changing a task's test paths does not rewrite its old receipt's syntax map or by itself change another task's recorded code.
Claims are advisory: `note` and `receipt` warn when another clone holds the task's claim, and `done` refuses with `is claimed by` until the holder releases it or `done --take-over` is used, which adds the takeover to Progress. The pull-request check only prints a Note when a task was finished by someone other than its claim holder.
After a squash removes original commits, a renewed DONE/CLOSED claim still checks the recorded paths, deletions, modes and content. This rule also applies with Git read reuse disabled (`WORKORDER_GIT_CACHE=0`).
`Evidence: verified` is not acceptance. With `--require-acceptance`, `Acceptance: awaiting a person`, a `stale since` acceptance (its code changed after it) or one `recorded in this change` refuses: approve the head (`confirmed in review by NAME`), or have a person run `workorder accept --task ID` (refused when `WORKORDER_AGENT` or an agent session variable such as `CLAUDECODE` is set; an agent can unset them) and land it first. Tied tests alone do not count.

<!-- refusal-family current-tests -->
## A task's current test fails or runs nothing
**Meaning:** `its test failed`, `regression`, `no test passed`, missing test tools, missing imports, timeouts, collection errors or a missing concrete test command prevent a finished claim. A reopened task whose test fails also needs attention.
**Clear:** Install the project's test dependencies, correct the command, fix the code/test, and rerun `workorder renew --task ID`. For DONE/CLOSED, no label excuses a current failure or empty run. For an intentionally reopened IN_PROGRESS/BLOCKED task, an approving review or `workorder-allow:ID` accepts its failing test. A pytest summary missing usable outcomes needs a working pytest run.
For a task being checked, staged comment or docstring edits to its code or tests still select the staged snapshot.
Test subprocesses refresh checkout bytecode on every run, so edits with the same size and timestamp are tested from current source. Dependency bytecode stays cached under the repository's Git directory; parallel runs lease separate cache slots. Checkout entries are removed after each run, including comparisons, and project `__pycache__` directories are untouched. Without writable Git metadata, runs use temporary caches.

<!-- refusal-family definitions -->
## New or changed task requirements
**Meaning:** `was written and finished in this pull request` requires definition review whenever a new task claims DONE/CLOSED, even if its test demonstrates the change. A new unfinished task supplying required code receipts keeps the earlier `is new in this pull request` rule: review is needed unless its test fails on base code and passes now. Scope/Acceptance edits, changed tests/contracts, removed dependencies, cancellation and unsupported reopening also change the agreement.
**Clear:** A task written and finished in one pull request needs no second pull request: an approving review clears it (the reviewer reads its definition), or `workorder-allow:ID`. `done` says so when it finishes a task that is not on the base yet. Restore unintended requirement edits. Fill empty Scope and Acceptance before `done`; acceptance review never supplies missing test evidence.
`status changed: DONE -> RETIRED` comes from `workorder retire ID --reason TEXT`: the task keeps its record and receipts but no longer owns its test files, and its test no longer reruns. Retiring removes a protection, so a reviewer accepts it with `workorder-allow:ID` or an approving review. `only a DONE or CLOSED task can be retired` has no label: set the status back.

<!-- refusal-family test-edits -->
## Test files, selection or outcomes changed
**Meaning:** `test changed`, `test_paths changed`, edited/removed tests (named per function where Python can tell: `assertion changed in test_x`), changed fixtures/configuration, fewer passes or more skips/xfails/deselections need review. Added tests also need review with `--strict-tests` or when their old tests already failed. A new skip/xfail that actually suppresses a test counts; a false condition does not.
Explicit `test_paths` stay protected regardless of suffix. Otherwise declared `code_paths` and package source (such as `src/click/testing.py`) are code before filename/folder guesses apply. Configure those guesses with `test_file_patterns` and `test_directory_patterns` lists in `.workorder/config.json`; see the reference for defaults. Helpers, fixtures and non-code test material remain protected, including .txt, Markdown and doc/docs paths.
**Clear:** Restore unintended edits, or have a reviewer accept them with the owning task's label `workorder-allow:ID`; an approving review is not enough, because they edit or weaken existing tests. `workorder-allow:tests` accepts shared/unowned file edits, not a task's command, selection or outcome finding. Added tests that are not edits of existing tests clear with an approving review. Unchanged Python tests may gain new functions, fixtures, imports or appended parameter cases without review. A refactor with edited tests needs `done --needs-review` or `renew --no-refactor` and review.

<!-- refusal-family old-tests -->
## Earlier tests fail on the new code
**Meaning:** `its tests from ... fail on this code` means the change no longer satisfies the earlier test version. An unowned test can report the same regression.
**Clear:** Restore the behavior, or have a reviewer accept the intended behavior change/test rename with `workorder-allow:ID`; use `workorder-allow:tests` for an unowned test. Reviewer approval alone does not accept this failure: it changes what existing tests expect. The current task test must still pass.

<!-- refusal-family weak-tests -->
## The test does not show the change works
**Meaning:** `asserts nothing`, tests passing on base code, or tests still passing with changed functions `stubbed out` show missing proof. New/changed pytest functions are checked together, so old unrelated tests cannot supply that proof. A false no-code-change exemption and a refactor whose earlier test fails are also refused. A refactor/verification completion needs review unless the base task already declares the matching `kind: refactor` or `kind: verification`. Hand-written DONE/CLOSED plus receipts with no production-code change follows the verification rule.
**Clear:** Test the changed result and rerun `done`. For a true refactor use `done --refactor` with unchanged tests passing before and after; use `--no-code-change` only when the task's code/tests are unchanged. An approving review or `workorder-allow:ID` accepts a test passing on base code, assertion/stub findings and exemptions not predeclared on the base; warning mode does not accept the explicit assertion/stub/exemption findings. `done --needs-review` can record eligible findings pending that review, but cannot accept failed tests or an invalid exemption locally.
An Acceptance item ending `(test: path::name)` (a full stop may follow) is tied to that pytest test; an item with a `(test:` that does not read as a tie refuses (`is not read as a tie to a test`). A tied item: `done` refuses (`acceptance item ...`) unless it exists, is run by the task's test command, passes now and fails on the base code (a `kind: refactor` or `kind: verification` task, or `--refactor`/`--no-code-change`, needs only the pass). Fix the code, the test or the item; no label applies to `done`.
With a local target, `done --refactor`, `done --no-code-change` and pre-commit print one Note per task naming the required pull-request review or label when the base lacks the matching kind. A kind added only on the implementation branch does not remove the requirement. `workorder new --kind refactor` (or `verification`) writes the kind when the task is created; `feature` and `fix` are descriptive only.

<!-- refusal-family test-first -->
## The test did not land first
**Meaning:** `--require-test-first` found a finished task whose test was added or changed with the work, or whose earlier test cannot be established.
**Clear:** Land the task and failing test before implementation. For an intentional exception, an approving review or `workorder-allow:ID` clears it; warning mode does not. Provenance is separate from whether an assertion is useful.

<!-- refusal-family scope -->
## Code lies outside the task's scope
**Meaning:** Changed production files fall outside the combined `code_paths` of tasks adding implementation/test receipts. Unrelated or unscoped receipts cannot disable declared scopes. This applies in light mode too.
**Clear:** Restore unrelated changes or record work under a task whose scope covers them. Otherwise an approving review clears it, or a reviewer adds `workorder-allow:scope:ID` for a claiming task. Warning mode, a task's ordinary label and `no-task` do not accept scope findings.

<!-- refusal-family code-receipts -->
## No task records changed code
**Meaning:** `--require-task` found production changes without new, counting implementation/test evidence covering the current code. Renewing an unrelated finished task is insufficient.
**Clear:** Run `workorder done --task ID` (or `--needs-review`) and commit its receipts. Unfinished work needs `workorder receipt implemented --task ID`, then `workorder receipt test --task ID`, and an approving review or `workorder-allow:ID`: hand receipts show only that the test passed, not that it fails on the base code and with the change stubbed out; adding the first Workorder workflow needs no label. A task written and finished in this change separately needs definition review. An approving review clears it; a reviewer can also accept the applicable task or use `workorder-allow:no-task` for this requirement alone. Deliberate path exclusions use `--require-task-ignore GLOB`. Documentation outside test material and Python production-code edits with identical syntax apart from docstrings need no code receipt; comments and extra parentheses alone also do not qualify as a refactor.

<!-- refusal-family imports -->
## The test could not run against the intended copy
**Meaning:** `could not compare` can name an external project import, an unidentified interpreter, an unavailable dependency, an invalid pytest setup or a failed temporary-copy operation. It proves neither weakness nor a passing refactor.
**Clear:** Install dependencies and make the tests import this checkout's project, using a suitable pytest `pythonpath` or non-editable install; fetch missing refs and fix the named command/setup error. Recheck isolated Python commands too. An unavailable comparison can instead be accepted with an approving review or `workorder-allow:ID` (`workorder-allow:tests` for an unowned file); warning mode does not suffice. This accepts the missing comparison, never a failing current task test. Pytest exit codes include plain meanings, such as `5 (no tests were collected)`.
A predeclared task kind does not accept an unavailable comparison. `done --needs-review` can record it; pre-commit prints a Note and permits committing the pending claim, while the pull-request check keeps refusing until an approving review or the task label is supplied.
Fresh pytest comparisons verify imports in the test interpreter before loading conftests or tests, using pytest's selected configuration and plugins. This startup has a 60-second limit; an earlier declared test timeout still stops the run with exit 124, and the test's deadline is not reset after verification. Cached test results still require a fresh standalone import probe with its own 10-second limit. Missing or timed-out verification cannot establish a passing comparison. A missing-pytest warning requires a separate import probe to confirm absence; duplicate availability markers are refused.
Standard editable installs of a flat or src layout (a `.pth` naming the work tree or its `src/`, setuptools' editable finder, the editables redirector) serve the comparison copy: in the copy's test interpreter those locations point at the same place in the copy. Everything else still counts as outside the copy and refuses: a package-dir remap, a folder Git ignores (setuptools' strict link tree; advice: install non-editable or from tracked sources), another checkout's install, `PYTHONPATH`, and any interpreter run with `-I`/`-E` (advice for a namespace folder: drop the flag or install non-editable).
Temporary comparison copies are removed after their Git processes exit, including failed or timed-out runs.

<!-- refusal-family workflow -->
## Workorder workflow changed
**Meaning:** The default workflow or a renamed generated workflow changed, including its removal.
**Clear:** Restore it, or have a reviewer inspect the change and add `workorder-allow:workflow`. Reviewer approval alone or warning mode does not accept it. `workorder init --update-workflow` previews and refreshes generated setup; review and commit the resulting file. Switching to or from solo mode (`init --update-workflow --solo`) is such a change; in solo mode the owner's own label accepts it as owner acceptance.

<!-- refusal-family project-test -->
## The optional project test finds a regression
**Meaning:** The project test failed. Workorder reruns identifiable failing pytest tests on the base, with the rootdir and configuration file of the current run: an old failure is a note, a newly failing test refuses, and an unavailable result stays explicit. Other test tools cannot supply this per-test distinction.
Passing pytest project tests print one note with pytest's counts. Failures show the first 5 FAILED lines, the first 20 lines of the first failure (once per command run), and counts; newly failing tests are one finding line with a count and the first 5 names. `--verbose` prints every line; the full text is also saved in `.git/workorder/last-output.txt`. Base reruns show only counts (`the rerun on BASE of the N project tests that fail on this code`); failures that already fail on the base print no failure body, and test output lines are cut at 300 characters (the saved file keeps them). Passing-test output is omitted. Startup and interrupted-run diagnostics remain visible; unreadable or missing reports cannot establish a base comparison.
**Clear:** Fix the regression or test environment. A reviewer accepts a project finding only with `workorder-allow:tests` (an approving review is not enough: existing tests now fail); task-current failures still refuse. Old project failures need no label. A first `workorder init` that finds pytest configuration or a `tests/`/`test/` folder of pytest files turns on `python -m pytest -q` and says so (left off when that `python` cannot import pytest); `init --no-project-test` turns it off. Set another command with `workorder init --project-test "python -m pytest"`; without one, the note says only declared task tests ran. Pre-commit does not run this optional command.

<!-- refusal-family renewal -->
## Evidence predates a later code change
**Meaning:** `its code changed after it was finished` identifies a new completion whose recorded implementation differs from the code about to land. A separate message can say main moved since the task was tested.
**Clear:** Commit the updated code, merge/rebase the current target when requested, run `workorder renew --task ID`, and commit the receipts. `done` and pre-commit show renewal advice as a Note; the pull-request check requires current evidence. Renewing an unchanged finished claim already on the target does not owe fresh base-code or stub proof. Reopening and finishing it again makes a new claim.

<!-- refusal-family removal -->
## Task or receipt history was removed or changed
**Meaning:** A task was removed, receipts were edited/deleted, or retained receipt hashes/history no longer agree. Every commit since the base matters, including edits later restored.
**Clear:** Restore receipt history; append new evidence instead. `workorder repair --task ID` handles supported legacy append conflicts. A RETIRED task's file stays; deleting it follows the same rule. A reviewer may remove a task finished/cancelled/retired at the base with `workorder-allow:ID` or approval only when its task and all receipts are removed. Partial retirement, removal of unfinished work, changed retained receipts and broken dependencies still refuse.

<!-- refusal-family pending -->
## Completion is waiting for review
**Meaning:** `pending review` records an intentional exception from `done --needs-review`; local DONE does not mean the pull request has been accepted. The printed findings may name other affected tasks or shared tests too.
**Clear:** Review every recorded finding. Add `workorder-allow:ID` for each required task, or approve the current head unless a finding edits existing tests (the pending message says which); `workorder-allow:tests` does not clear a task's recorded review. Renewing does not clear an unmerged review request. After the tested implementation lands on the target, the pending request no longer blocks it.

Diagnostics escape characters the console cannot encode, preserving test IDs and paths. Brief import-report sharing locks are retried, including the final read; missing, persistently unreadable or invalid evidence still refuses.

The test in `tests/test_v16b_guide.py` requires these meanings and remedies, and records a hash of each Python module's executable messages, conditions and returned findings. A new or changed refusal requires guide review before updating those hashes. The check also catches some unrelated code edits; comments and docstrings are ignored. It detects changes, but cannot judge whether an explanation is correct.

<!-- refusal-inventory workorder/__init__.py 380af974005fd27b7af20d39092b8daaff3a18899d489e02bb77d388ae3402c7 records -->
<!-- refusal-inventory workorder/__main__.py cb39dacc1a942b75221dd33c3d3828202d9efb797bf559090b1b4a407ae8d5d6 records -->
<!-- refusal-inventory workorder/bootstrap.py 1d84f97a254426844821a20c3413a0263d9d86912b111aef3f32c91b1a064fa7 records,workflow,project-test -->
<!-- refusal-inventory workorder/cli.py 0b2208f3a5fe87e6b1d502b7723db71c33ef9bb56db162a6f062b59ed8f7450d records,evidence,current-tests,definitions,weak-tests,test-first,pending,renewal,project-test -->
<!-- refusal-inventory workorder/gate.py 8b5d5113d658f41cb5eedea07c902d8afc6b198d0ebbcc285a378c45b4d9d8ef code-receipts,current-tests,definitions,evidence,imports,old-tests,pending,project-test,records,removal,renewal,scope,test-edits,test-first,weak-tests,workflow -->
<!-- refusal-inventory workorder/hooks.py 84c2d9a3f4cb85cc24bccd023e98b2959733d3dcae092541ecc3a82c6bff3d63 evidence,current-tests,test-edits,pending,renewal -->
<!-- refusal-inventory workorder/project_report.py 966cf398e998321996e0e60827eaadb08c6dbe625a7126a0aa8d08c4fd2460ae project-test -->
<!-- refusal-inventory workorder/project_tests.py 0865ab8b955a28051dfc9889dca21a7852f123c129914866692fec5e280e6959 project-test,records -->
<!-- refusal-inventory workorder/pytest_selection.py bcff8a23918c0320f921d46b229359bc41237be3055be093a6e746860395d35b weak-tests,imports -->
<!-- refusal-inventory workorder/python_imports.py f5509e0980385e3528ad9c4392c976f74485a7ef1a1111a43afb1e25164d7f8b imports -->
<!-- refusal-inventory workorder/python_stubs.py a241120e76a9632ba6013f520a30b13afcb8e06faa69af57175829255950cbda weak-tests -->
<!-- refusal-inventory workorder/python_tests.py 31645fe31f89adb02d2d05c83eb6844a9f8adf5c8c554e26a49996c6e23313ac test-edits -->
<!-- refusal-inventory workorder/review_labels.py eb4f44bed0237af818fbf4bbe9a940c491ec4d700d4cdcd8213289f44ea93b95 definitions,test-edits,old-tests,scope,workflow,pending,project-test -->
<!-- refusal-inventory workorder/source.py 75a84c3309bdf83af6a2401e372e8ceafe62bb3ed97fe74720aa5fa91fa974b6 code-receipts,renewal,weak-tests -->
<!-- refusal-inventory workorder/store.py 4f86d1dd6dca9df237f1a62558ebdcfced0e08c18e32d005b74fe1d65c8d004b records,evidence,current-tests,removal,pending -->
<!-- refusal-inventory workorder/git_reads.py be4d583a490607027cd80ef7152fb188267a913bc9f24d4c2621e0b3dc3d6ada records,evidence -->
<!-- refusal-inventory workorder/timing.py 37bdb892641d496f8a9cae72b0d4c678ad6792a11710367957ad4be560842cfa current-tests -->
<!-- refusal-inventory workorder/console.py b86449fbb740d9ad027aa86965ef6940480eea58be72f17fa267d953c13c7787 records,current-tests -->
