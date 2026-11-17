# Changelog

## 0.18.0 (17 Nov 2026)

- The README has a Speed section: `done` runs the task's test three times, and the project test (on by default
  when `init` finds pytest) adds one full run of the suite; `init --no-project-test` turns it off. `init` says so.
- The project-import check waits up to 60 s for a cold interpreter (was 10 s), so a slow machine no
  longer turns an honest comparison into "could not compare".
- An honest change to what an old test expects commits as work in progress with a note; `done` refuses it until
  `done --needs-review` records it, and the task's own label `workorder-allow:ID` then accepts what that run
  recorded, so one label clears the change. Local hooks refuse such findings only alongside a new DONE claim.
- A `done` run anywhere in the change counts as the completion record, so `renew` after `done` still commits.
- Project tests that newly fail need `workorder-allow:tests`; an approving review is not enough.
- Add `workorder retire ID --reason TEXT`: a DONE or CLOSED task becomes RETIRED,
  keeping its record, notes and receipts. It no longer owns its test files and its
  test no longer reruns in later checks. The pull-request check reports the
  retirement as a finding cleared by `workorder-allow:ID` or an approving review;
  RETIRED from an unfinished or new task is refused. Summaries count retired tasks
  separately; deleting a task file keeps the existing removal rule.
- Tie Acceptance items to tests: an item ending `(test: path::name)` names a pytest
  test that `done` requires to exist, be run by the task's test command, pass now
  and fail on the base code (refactor and verification tasks need only pass).
  `done` prints how many items are tied; `new --acceptance` writes items and `show`
  marks tied ones.
- Keep output readable for agents in `done`, `check`, the hooks and `show`:
  per-test findings of one kind are one line with a count and the first 5 names;
  failing test output shows at most 5 FAILED lines and the first 20 lines of the
  first failure, once per command run; each distinct Note prints once. The full
  text is saved in `.git/workorder/last-output.txt` and its path printed;
  `--verbose` (new for `done` and `show`; `WORKORDER_VERBOSE=1` for hooks) prints
  everything. A test name such as `test_x[SHOUT-1]` is no longer reported as
  another task's finding. A refused `done` with 1173 failing click tests went
  from 58,176 lines to 37.
- A first `workorder init` that finds pytest (pytest configuration in
  pyproject.toml, setup.cfg, pytest.ini or tox.ini, or a `tests/` or `test/`
  folder of pytest files) turns on the project test `python -m pytest -q` and
  prints one line saying so; `init --no-project-test` keeps it off or turns it
  off. Existing setups and configurations are not changed.
- Add `workorder new --kind feature|fix|refactor|verification`, written to the
  task's `kind` field; `show` prints it. `feature` and `fix` are now valid kinds.
- Claims are advisory: `note` and `receipt` warn when another clone holds the
  task's claim, and `done` refuses such a task unless `--take-over` is given,
  which adds the takeover to the task's Progress notes. The pull-request check
  prints a Note when a task is finished by someone other than its claim holder.
- One review rule: an approving review clears every finding except edits,
  deletions, new skips/xfails or weakening of existing tests, and changes to the
  Workorder workflow file; those need a label (`workorder-allow:tests` or
  `workorder-allow:ID`, and `workorder-allow:workflow`). Approval now also clears
  code with no task, scope findings, assertion/stub findings, test-first
  exceptions, reopened failing tasks, unavailable comparisons and refactor or
  verification completions not predeclared on the base. Edits of existing tests
  that approval used to clear now need the label. Refusals say which applies.
- A task written and finished in one pull request: the refusal says an approving
  review clears it and no second pull request is needed; `done` says the
  pull-request check will ask for an approving review.
- Plainer notes: a long-existing test file with an edited line is "edited in
  this change", not "written"; new tests in a shared test file are attributed to
  the tasks this change works on ("shared by ..." when several); a reused test
  result says "(result reused from the run at HH:MM UTC; nothing it covers changed since)".
- Local checks give the pull-request check's answer: `done`, the pre-commit hook and
  the Stop hook refuse, against the target's merge base, edited or removed tests,
  new skips/xfails and earlier test versions failing on the new code (including test
  files no task owns), and name the label the pull request needs. `WORKORDER_REVIEW=warn`
  turns those reviewer findings into notes locally, as `--review warn` does in CI;
  a task with `done --needs-review` keeps its pending findings as notes.
- `done` records `completion: true` in its test receipt. Pre-commit and Stop refuse a
  DONE/CLOSED claim new since the target on a task this change worked on without that
  record (a hand-set status); claims already on the target are not re-judged.
- Shorter docs: the README (1,935 to under 1,100 words) now leads with the task
  record, a quick start, the review rule with a one-screen table of findings and
  what clears each, and what Workorder does not do; edge cases moved to the
  reference. The reviewer guide starts with the same table. The reference has a
  contents list and lost its repetition (17,207 to under 13,000 words), and its
  review statements follow the one rule.
- The agent block added to AGENTS.md and CLAUDE.md is down from 339 to under 170
  words: start from `workorder show`, claim, work with a test that fails without
  it, `done`, `note --next`, never hand-edit status or receipts, run the
  pull-request check, and where the full output is saved. Running `init` again
  replaces a block an earlier version wrote, keeping the rest of the file.
- A project test that fails on the new code now needs `workorder-allow:tests`, as
  the one review rule says; an approving review alone no longer clears it.

## 0.17.0 (17 Nov 2026)

- Let pending-review "could not compare" claims commit with a Note while the
  pull request still requires the task label. Print a label-requirement Note in
  `done` and pre-commit for refactor/verification completions not predeclared on the base.
- Treat declared code paths and package source as code even when named like tests;
  explicit test paths take precedence. Configure test filename and folder globs
  in `.workorder/config.json`. Comparison failures carry reviewer labels and
  pytest exits include their plain meaning.
- Require a task label for refactor or verification completions unless the base
  task already declares `kind: refactor` or `kind: verification`. This also covers
  hand-written completion claims with receipts but no implementation change.
- Enforce declared code scopes in pull-request checks, including light mode;
  unrelated receipts and approving reviews cannot bypass the required scope label.

## 0.16.0 (16 Nov 2026)

- Keep the read cache active with Git for Windows' default diff drivers. Raw
  evidence reads bypass helpers; caller-requested helper output stays fresh
  only when the selected paths use it. Escape characters a console cannot
  encode in diagnostics, including test IDs. Retry a briefly locked final
  comparison import report without changing the test's exit or accepting
  missing, unreadable or invalid evidence.

- Wait for briefly locked comparison import reports instead of aborting valid
  tests; missing, unreadable, or invalid import evidence still refuses the run.

- Keep Git reads reusable with ordinary LFS registrations, included configuration,
  and external ignore and attributes files; track their dependencies and use fresh
  reads for active helpers whose inputs cannot be inspected. Recognize squashed
  receipts independently in stale-claim checks, including with the read cache
  disabled. Cached task and receipt values no longer share caller mutations.
  Avoid rereading unchanged files during POSIX state checks, and support Git's
  hook environment. Give parallel test workers their own read sessions and reuse
  unchanged queries across external commands after validating their inputs.
  Validate compound completion and merge-base reads once per call.
  Share staged-tree reads without rewriting the caller's index.
  Keep fresh metadata reads task-specific and batch each group of blobs without
  retaining a connection between fresh calls. Classify protected test material
  only when a path might otherwise be omitted as documentation or unchanged syntax.
  Verify comparison imports during pytest startup before loading conftests or
  tests, avoiding a second interpreter for fresh runs; cached results still get
  a fresh import probe. Split each OS/Python CI combination across three test shards.

- Reuse read-only Git queries throughout each command, including `done`, while
  checking repository contents, HEAD, refs and the index before reuse. Invalidate
  reads after writes and external commands; fall back to fresh reads when state
  cannot be fully inspected. Share a framed `cat-file --batch` connection for
  blobs and HEAD lookups, renewing it when Git metadata changes and closing it
  when the command ends. Keep test and receipt checks unchanged.

- Run test evidence, comparisons and import probes with checkout bytecode
  refreshed on every run, including isolated Python and console pytest commands.
  Reuse dependency bytecode in Git-local cache slots, with exclusive slots for
  parallel runs; remove each checkout's entries after its run. Same-size edits
  with unchanged timestamps cannot reuse stale bytecode, and runs leave project
  bytecode caches untouched. Fall back to temporary caches without writable Git metadata.
  Discard test-result caches created before bytecode isolation.

- Keep pytest project-test output concise in done and pull-request checks:
  one passing note with pytest's counts, or failing-test summaries and failure
  sections capped at 60 lines each. Show only counts for the base rerun;
  read exact test outcomes from the report plugin, including parameter IDs.

- Require an approving review or the task's reviewer label when a task is
  written and finished in the same pull request. Recommend landing its
  definition first; done explains the review needed before merging.
- Add `init --project-test CMD`, stored in `.workorder/config.json`. Done and
  explicit test checks run it once, compare only failing pytest tests on the
  base, and distinguish old failures from new ones. A reviewer can accept a
  project failure with approval or `workorder-allow:tests`. Hooks do not run
  it. Without a configured command, print the limits of the declared tests.
- Apply pytest's selected pythonpath configuration and overrides in import probes,
  including isolated Python commands, consistently with pytest 8.3 and later.
  Recognize mandatory imports of packages absent
  from the base without mistaking an editable install for old-code evidence.
- Exclude documentation and Python changes with unchanged syntax apart from
  docstrings from code scope, new implementation path maps and staleness
  checks. Classify test material first and preserve its review and staleness
  protection regardless of suffix. Preserve the fingerprints in existing receipts.
- Name newly added tests in shared files. Judge skip and xfail additions by
  their outcomes when run. Keep the ancestry check on advice that main moved.
- Add a reviewer guide checked against executable diagnostics and linked from
  generated workflow failures. Shorten the generated agent instructions.
- Add offline-defined Click examples using its shared test files; preserve
  the pinned revisions for the separate real-project run.
- Show renewal advice during done for another task as well as in commit
  hooks. Check renewals of already landed finished tasks as regressions,
  without asking them to prove another task's new functions.

- For pytest commands, check new or changed test functions together against
  stubbed functions and base code. Older tests in a shared file cannot supply
  their failure. Name the selected tests in refusals and list tests that still
  pass under stubs in one Note when another selected test fails. Keep the whole
  command when no selected test function changed.
- Let finished tasks share Python test files: new tests, classes, fixtures and
  imports, and appended parameter cases, need no review when existing tests
  stay unchanged. Existing test edits still need review; task labels and
  approving reviews accept them for declared refactors too. Merge hooks give
  passing edits the same review note as other commits.
- Refuse a pull request when a task finished in that change has later edits
  to its code paths or recorded implementation files. Tell the agent to renew
  and commit the receipts before a squash. A merge hook prints that advice as
  a note so the merge can be committed.
- Discover files for pytest commands without paths from testpaths, or from
  recognized repository tests when testpaths is absent. Use the same discovery
  for task creation, completion, hooks and pull-request checks. If no files
  are known, new requests --test-paths before writing anything.
- Keep existing task files and receipts readable. Set the package and current
  installation examples to 0.16.0.

## 0.15.0 (16 Nov 2026)

- Limit --no-code-change to the task's declared code paths and test files.
  Without declared paths, exclude changes covered by another task's new
  implementation or test receipts, while keeping the task's own tests protected.
  Use the same scope for done and receipt checks. A task label can accept a
  false exemption in the pull-request check, including existing receipts.
- Let done finish alongside another task's own pending-review completion
  recorded in this change, when its receipts cover its current test files.
  Only that task's own test edits and recorded pending findings stop blocking.
  Other findings still block and appear once in Other; --needs-review remains
  the route for intentional changes. Current failures and pull-request review
  requirements keep their protections.

- Let finished declared refactors commit passing test edits. Local hooks advise
  renew --no-refactor or done --needs-review after committing; failing edits
  still refuse, and the pull request still needs the task label.
- Let done --needs-review record assertion, stub and unavailable-comparison
  findings as DONE pending review. Store the pending findings in the test
  receipt and list them under review. Label-only findings still need the task
  label; failures, missing evidence, command and dependency changes still refuse.
- Treat Python tests with unchanged syntax trees as unchanged throughout hooks,
  done, renew and base checks, including comment and blank-line edits.
- Exclude egg-info, dist-info, Python bytecode and pytest cache output from task
  and scope requirements and new implementation path maps. Keep existing
  receipts readable, including those that recorded generated files.
- Describe unchanged task files after unrelated work lands. Tell agents to
  renew only for their task's own code or test changes. Omit renewal advice
  when receipts already cover the commit, pluralize missing-test notes, and
  use “no task changed in this change” for empty base checks with --run-tests.

- Limit each task's stub check to changed functions in its declared code paths.
  Without a declaration, exclude other tasks' declared paths. If none belong
  to the task, omit the check and its note in done, hooks and base checks.
- Treat tests absent at the base as written in the change, including new helpers
  and fixtures. A new conftest needs configuration review only for existing
  tests below its folder; edits to an existing conftest keep their review rule.
- Make done's Next line name a working review route for findings requiring a
  task label. Assertion, stub and unavailable-comparison findings can use
  --needs-review; the pull request still requires the label. Mixed findings
  retain the general next step.
- Say that a test-first failure is required, and omit the skipped-base-test
  comparison note when the selected tests were written in the change.
- Apply stub and target-code proof checks only to completion claims new in the
  change, or renewed claims with changed code. Keep running affected finished
  tasks' tests; their failures still refuse. A second task in the same file or
  another module within the old task's scope no longer blames the old claim.
  Stop also checks affected code and renewed receipts. Done omits successful
  old-test runs from Other and names an old task's failing test on refusal.
- Keep passing test renames to one repair finding in local hooks. Hide test
  output for unfinished tasks in pre-commit; a missing declared test gets one
  note saying the unfinished task may land.
- Say "no task changed in this change" for bare check --base too. Add the
  second honest task flow to the offline examples, now 16 cases.
- Let finished tasks with test receipts commit passing command repairs even
  when completion was recorded on the branch. Pre-commit and Stop print one
  repair note, without a command-change refusal beside it. Failing commands
  still refuse; renewal and pull-request review remain required.
- During a pending merge, omit the test-change note for staged test content
  identical to the incoming parent's content. Branch test edits still count.
- Use singular words for one other task's findings, and explain unchanged-task
  runs of --all-tests plainly. Point missing-hook users to their virtualenv as
  well as pipx. Add task-first advice when done finds no code change, and say
  that --run-tests with a task name refuses without running tests.
- Explain the expected missing-test warning and computed CLOSED in the quick
  start. Keep note --next for unfinished work and tell agents sharing a Git
  identity to set WORKORDER_AGENT in the generated instructions and README.
- Put each test checkout's src/ and root first on PYTHONPATH. Probe project
  imports with the test interpreter before using a comparison, including a
  cached or parallel run. Outside imports are unavailable comparisons accepted
  only by the task label, never evidence of weak tests or a passing refactor.
  Check repository packages and top-level names of installed distributions
  matching the project's pyproject.toml or setup.cfg name (PEP 503 normalised),
  using top-level names, RECORD and finder mappings, including .pth source roots.
  Allow unrelated dependencies wherever installed, including editable siblings
  and Workorder itself. Give source-layout advice only for src/ and root packages.
  Init notices installed src packages. Add real offline editable-install tests,
  including remapped package folders and second clones sharing an install.
- Give each test subprocess a temporary pytest cache through PYTEST_ADDOPTS,
  preserving user options and quoting Windows paths. Clean it up after the run;
  parallel commands no longer race on the checkout's .pytest_cache. Workorder's
  own result-cache keys are unchanged.
- Count pytest.warns, deprecated_call, raises and fail, plus unittest assertion
  context managers. Scan only added or changed test functions against the base
  syntax tree; leave unchanged functions and comment-only edits alone.
- Warn about missing paths named by new --test. Permit local command repairs on
  unfinished tasks without receipts. Renew preserves a refactor declaration
  after later commits and rebases, rechecks its conditions, and accepts
  --refactor explicitly. Use renew --no-refactor to drop it on purpose.
- Require workorder-allow:scope:ID or an approving review for code outside a
  task's scope; a test-change label cannot accept it. Print shared assertion
  findings once. Done lists its task's findings, one summary for other tasks,
  and one next step. Unsupported claims no longer display Evidence: CLOSED.
- Detect PEP 735 test, tests or dev dependency groups. Generated workflows
  upgrade pip to 25.1 or newer and install the selected group and project.
- During a merge commit, include MERGE_HEAD when finding the target merge base,
  so target-owned test changes are not attributed to the branch's refactor.
- Add an editable src case to shortcuts.sh and a Linux/Python 3.12 job for
  real-projects.sh, exercising Click and attrs with three honest changes and
  three shortcuts each. Network setup must be verified on a connected host.

- Count assertion helpers in the test file, path conftest files and imported
  test material, following up to four calls. Recognise assert, verify, check,
  expect and ensure names. A task label can accept unusual assertion syntax;
  an approving review alone or warning mode cannot.
- Treat literal-only comparisons, other constant expressions, and equality or
  identity of the same name, attribute or subscript like assert True, so a
  missing module at the base cannot supply their only failure.
- Run each task's test once in a temporary copy where all changed or added
  Python function bodies raise NotImplementedError. Refuse tests that still
  pass; only the task's explicit reviewer label accepts the finding in a base
  check. Declared refactors and valid no-code-change runs are exempt.
- Refuse --no-code-change when code changed since the merge base or task
  creation. Validate receipt exemptions too, and retain the comparison base
  in an optional test receipt member. Existing files need no migration.
- Let pre-commit and Stop note a repaired command for an already finished task
  when the new command passes. Pre-commit runs the staged code. The note names
  the review label; done still refuses changed commands. A passing finished-task
  test rename or edit can be committed before renewal; the note says to run
  renew after committing. The pre-commit hook notes test changes, including
  branch test edits made while merging main. The pull-request check refuses them
  without the reviewer label (or an approving review where allowed).
- Keep failures as local progress notes when finished tasks are reopened, but
  require the task's explicit reviewer label when its current test fails in a
  pull-request base check. Approval and warning mode do not accept the failure.
- Keep no-code-change, refactor, pending-review and new-test-origin notes
  separate for each task. Missing base task or test evidence no longer implies
  that a test landed earlier.
- Report each shared configuration edit once with all its owners, without
  duplicate per-task notes. Done always groups other tasks into one Other line.
  Explain new --local in words instead of printing the flag as its reason. Run the
  red-before-green comparison only when the task's own code or tests changed.
- Exclude lockfiles from the code-to-task requirement and add repeatable
  `check --require-task-ignore GLOB` path exceptions, with a workflow example.
- Add positive `check/done --jobs N`, defaulting to the CPU count. Distinct
  current test commands run concurrently with stable task output order;
  identical commands still share a run. Local checks select affected finished
  tasks by their recorded implementation paths and selected test paths.
- Require the explicit `workorder-allow:workflow` reviewer label for changes
  to the default or renamed generated Workorder workflow. Author-added labels
  remain ignored.
- End init with its written-file list and exact add/commit command, including
  Claude settings when written and listing the local hook without staging it.
  Local task IDs now account for reservation tags already present in the clone.
- Give the test CLI helper 120 seconds for slower Windows runs. Update the
  documentation and version to 0.15.0.

## 0.14.0 (15 Nov 2026)

- Show whether a finished task's tests landed before the change. Add
  `new --test-first`, `done/check --require-test-first` and `init --test-first`.
- Compare unowned Python tests from the base on current code. An approving
  review cannot accept old-test failures or test-first exceptions; explicit
  reviewer labels are required, and the check names uncovered findings.
- Keep receipts through clean rebases when recorded paths match and all other
  changes come from the target. Later edits still need new receipts.
- Distinguish recorded evidence from fresh test runs and count tested tasks
  instead of counting current-code and base-code runs as separate tests.
- Skip the four pytest.toml cases below pytest 9 and exercise pytest 8.3.3 in
  one test job. Keep the declared pytest minimum at 7.
- Add `done --refactor` for unchanged test files passing on merge-base and current
  code. Record the optional declaration and show it to the pull-request reviewer.
- Have `done --needs-review` write DONE with an optional pending review receipt.
  Hooks note it; the pull request needs a task label or an approving review;
  main accepts it after merge. `show` says when it is waiting for review.
- Refuse new or changed Python test functions with no assertions or only
  constant-true assertions, including tests whose only base failure is an import.
- Accept a new task definition without a label when its test is proven to fail
  on merge-base code and pass now, with no assertion-free functions found.
- Add `new --code-paths`; ask agents to set scope and name changed code when a
  task omits it.
- Label base-branch test output, keep notes and receipts unchanged on refused
  `done` runs, give useful zero-pass advice, and count distinct tasks in notes.
  Explain fresh-clone setup and include the completion commit in the quick start.
- Preserve existing task files and receipts without migration. New receipt
  members are optional. Update the docs and version to 0.14.0.

## 0.13.0 (15 Nov 2026)

- Refuse DONE when a run reports zero passed tests, without extra flags.
- Accept review findings through a current-head approving review by someone
  other than the pull request author; retain reviewer labels and list what
  each approval accepted. Run generated checks on submitted reviews too.
- Ignore harmless Python test-function edits: comments, docstrings, assertion
  messages and reversed equality or inequality operands.
- Accept parallel local squash commits with the hook enabled when staged
  paths match their implemented receipts, including fresh clones afterward.
- Give one sentence of reviewer advice and one notice per reused UTC time.
- Add optional task `code_paths` to limit code coverage under `--require-task`
  when every task adding receipts declares its files or folders.

- Run a finished task's current test on merge-base code with its new or changed
  test files. Refuse tests that already pass there; reviewers can accept
  refactors with `--allow ID`. Preserve `--no-code-change` verification exemptions
  in an optional test receipt member accepted by 0.12.0 readers.
- Have `done` and task-related staged checks run affected finished tasks. Report
  a missing local target, and ask agents to fetch origin before `done`.
- Match whole test-folder names and the documented prefixes and suffixes, so
  `latest` and `contest` remain production folders. Include `specs`.
- Read staged checkout contents in one batch for fingerprints and test copies,
  preserving staged attributes and line endings without a Git process per file.
- Read paginated label events in generated workflows. Ignore allowances added
  by the pull request author, including labels they remove and re-add.
- Print `NOT DONE` for unsupported claims, keeping the existing exit codes.
- Add opt-in `init --light`: review warnings and no task requirement. Keep the
  default workflow unchanged in those options; document how to switch back.
- Keep existing task and receipt formats readable without migration. Update
  documentation and installation pins to 0.13.0.

## 0.12.0 (15 Nov 2026)

- Refuse test review findings by default; `--review warn` remains optional.
  `done --needs-review` records evidence and leaves IN_PROGRESS with the
  reviewer-label note. The reviewed pull request can carry DONE and its label;
  the check on main after merge needs no label.
- Give one definition-acceptance message for new tasks whose receipts cover
  the code; document landing the task first or reviewing both in one pull request.
- Record implementation paths, modes, blob IDs and deletions so matching squash
  merges survive deleted branches and fresh clones, including parallel work.
  Continue reading old receipts with their existing matching rules.
- Scope wrapper requirements to changed tasks and their tests. Ask for explicit
  `new --test-paths` for wrappers. Refuse changed task test commands in done,
  pre-commit and Stop, including checks without a remote.
- Print warnings before `check: OK`, and show each test output once.
- Look up reusable current and REF results before making test copies. Keep
  explicit runs fresh and staged content separate from unstaged changes.
- Run each distinct declared command and timeout once on current code, including
  unchanged REF comparisons. Document how runtime grows with finished tasks.
- Normalize CRLF when comparing Python import additions. Apply Git checkout
  conversion to staged test copies so Windows hooks can reuse working results.
- Update version, workflow, agent instructions, reference and shortcut examples
  to 0.12.0. Keep README within 130 lines.

## 0.11.0 (not released on its own; included in 0.12.0)

- Run completion evidence fresh for done, receipt test, renew and explicit
  check --run-tests. Only implicit hooks reuse local runs, with the run's UTC time.
- Keep branch receipts usable after target-only merges, including parallel
  pull requests and squash merges. Later branch code edits still need receipts.
- Report a shared test-file edit once, naming its owners; any owner or --allow
  tests can accept that file. Test-only changes need no --require-task receipt;
  renewals of already finished tasks count only with --allow ID.
- Treat pytest failure/error summaries as failures even with exit 0. Label
  failure output once and exclude refused claims from supported summaries.
- Recognise testing helpers and expected, golden and snapshot data. Ignore docs,
  ignore rules and other non-code material when done requires implementation.
- Clear next on DONE; explain refusals of already finished tasks directly.
  Unfinished wrapper tasks get local notes. Tests skip unavailable Node.js.
- Rebase Windows extended internal symlink paths in test copies; copy ignored
  dependencies with a note when links are unavailable. Fix Windows test quoting.
- Add check --base --review warn|refuse, default warn, with GitHub annotations
  and a review summary. Test changes are flagged in the pull request by default,
  and refused with --review refuse. Current failures and false claims still refuse.
  Update workflows, local review advice and the examples for both modes.

- Count a finished task's named test files when they exist in the current code,
  including new files in repositories that already have tests. The generated
  pull-request check accepts these additions without a label.
- Accept added test functions and added names in Python imports when existing
  bindings stay intact. Removed or changed assertions and test bodies still
  need review; strict checks still require review for additions.
- Build temporary test copies from Git's file lists. Working copies include
  untracked files that are not ignored; staged copies use the index. Link
  ignored dependencies into current and REF copies instead of copying them.
- Write inferred test_paths only for files and folders that exist. Wrapper
  commands such as make test and npm test keep their request for test_paths.
  Recognise spec/ folders and files named *_spec.* or *_tests.*.
- Update package versions and install pins to 0.11.0.

## 0.10.0 (not released on its own; included in 0.12.0)

- Isolate temporary Git copies and their test commands from the hook's Git
  environment, preserving commit indexes for -a, paths, amend and linked worktrees.
- Compare tests and requirements with the target merge base; keep its name in
  diagnostics. New work only on the target no longer looks like removed work.
- Add done --needs-review for intentional test changes pending a reviewer label.
  Local hooks report these as notes and still refuse unsupported DONE claims.
- Require a code change for done; --no-code-change permits verification tasks.
  --require-task counts tasks from the merge base, or new tasks accepted by --allow ID.
- Infer helpers and data under folders containing test or fixture in their names.
- Preserve and chain an existing hook with init --force. Keep successful hook
  test output quiet, count cached results, and warn when no test dependencies
  were detected instead of stopping the generated workflow.
- Isolate test environments from ambient CI and GITHUB_ACTIONS settings.

- When a task's test files changed and its tests already fail at the base, any
  change to those files needs --allow ID. A finished task whose base tests
  failed must have those tests pass on the new code unless --allow ID.
- A task owns only the files or folders its test command or test_paths names,
  plus conftest.py and pytest configuration on the path to them. `new --test`
  records test_paths when the command names files or folders. --allow tests
  accepts every changed test line.
- Recognise test files named *.test.*, *.spec.* or *_test.go, and files under
  test/, tests/ or __tests__/. A finished task whose test maps to no test
  files, in a repository that has test files, is refused until test_paths is set.
- Count cached test results in the run summary, and say (cached) when they were
  reused. Do not suggest a reviewer label for a failing current test.
- Cover tasks/, .workorder/, tests and .github/ in the CODEOWNERS example.
  Session start re-reads claim and release tags, so a force-release from
  another clone is no longer shown as held.
- Update package versions and install pins to 0.10.0.

## 0.9.0 (not released on its own; included in 0.12.0)

- Review all repository test roots and every task whose tests changed, including
  untouched task records. Existing test edits need the owning task's acceptance,
  or --allow tests for unowned tests. Pure additions still pass by default.
- Replay changed tasks' REF tests and check failures on REF code before blaming
  the change. Require explicit test_paths for wrappers and diagnose repository
  module removals separately from missing external dependencies.
- Keep failing or missing current tests for unfinished tasks as progress notes.
  Allow reopening a finished task with a new counting failed receipt in the same
  commit; unsupported DONE claims still refuse.
- Require a new counting receipt on a task with a concrete command for code
  changes under --require-task. --allow no-task accepts only this requirement.
- Cache local test results by code, command and timeout. Explicit receipt test
  and renew runs stay fresh; CI runs bypass the persistent cache. Keep successful
  comparison output quiet and show diagnostic output when needed.
- Make Stop refusals exit 2 on stderr and keep the stop-loop guard at exit 0.
  Explain ships:none claims as supported with nothing to ship, and distinguish
  implemented code from current code in successful results.
- Generate pull request and default-branch push workflows, using local refs to
  choose the push branch. Accept tests and no-task reviewer labels. Push checks
  run --run-tests --all-tests without a base.
- Start agent instructions with fetch; pull --rebase only on main, and merge or
  rebase main into a feature branch as needed before renewing changed evidence.
- Shorten the README, preserve its introduction, and move detailed rules to the
  reference. Add two shortcut cases covering unrelated receipts and task work.
- Update package versions and install pins to 0.9.0.
- Infer test files from folders named tests or test, folders a command names,
  test_*.py, *_test.py, conftest.py and pytest configuration. A root-level test
  file does not make production code a test file, and an import does not make
  a package a test file.
- Document task-first development: define the task, commit a failing test with
  a handover note, implement, run done, then let the generated check and the
  check on the default branch pass without skipping the hook.

## 0.8.0 (not released on its own; included in 0.12.0)

- Refuse test-change problems in done, pre-commit and Stop when the target is
  available locally; keep status and give workorder-allow:<ID> label advice.
- Check failing REF tests on REF code before attributing a failure, and reuse
  that run. Name missing test commands or modules as environment problems.
- Cover whole pytest collection folders and imported test helpers by default.
  Warn about wrapper scripts and protect the script itself.
- Require reviewer acceptance for reopening finished tasks or cancelling work;
  Stop refuses a held task newly cancelled here. Stop refusals exit 1.
- Write a failing dependency placeholder when init cannot infer dependencies;
  add init --update-workflow with a diff and a read-only --check preview.
- Group repeated check notes; --verbose prints each one. Explain why a passing
  done run does not count and how to ignore generated files.
- Add docs/guarantees.md and tell agents to report done refusals and warnings.

- Use one test-change rule for tasks whose file or receipts change: additions
  need no label; removed or changed lines need reviewer acceptance. Accept
  intended REF-test failures and reduced pytest outcomes with --allow ID,
  while current tests and other tasks' --all-tests checks stay active.
- Keep missing named tests from widening to repository-wide discovery, and
  explain every skipped REF comparison. Handle Windows snapshot paths and
  preserve task line endings and byte order marks during updates.
- Recognise task files and receipts imported unchanged from a target or merge
  parent as existing claims. Give branch renewal advice for stale own claims.
- Require a new implemented or test receipt for code changes under
  --require-task; exclude documentation and non-code paths.
- Require acceptance for Scope and Acceptance additions to finished tasks.
- Limit Stop checks to held or locally changed task files. SessionStart shows
  held tasks and points to ready without scanning all readiness.
- Report undecodable task files individually; only suggest pulling finished
  upstream work when the local status differs.
- Add the portable shortcut catalogue and its actual 0.8 output.
- Update package versions and install pins to 0.8.0.

## 0.7.0 (not released on its own; included in 0.12.0)

- Compare changed tests with their base versions on current code in a temporary
  copy. Passing additions and edits preserving those tests need no label;
  refuse old-test failures, fewer pytest passes and more skipped/xfailed/deselected
  tests. New skips, selection flags and pytest configuration need acceptance.
  Strict mode still requires acceptance for all test edits.
- Keep test-change acceptance scoped to current work: untouched tasks already
  DONE/CLOSED at the base need no label for shared test changes, while runtime
  comparisons and all-task regression runs still cover them.
- Generate workflows that install detected project test dependencies, pin the
  running Workorder version (or PEP 610 Git commit), then check with --all-tests
  and --require-task and reviewer allow labels. Report the dependency choice.
- Refuse done until Scope and Acceptance are filled in, keep existing status
  quotes, name merge debt on success, and warn about missing clone hooks and
  test weakening against locally available origin/main before writing DONE.
- Add init --claude project hooks: session context for locally held tasks and a
  Stop refusal for unsupported finished claims, with stop-loop prevention.
- Use Claimed/Evidence consistently and say the declared test passed on this
  code. Warn once when the pre-commit hook cannot find Workorder.
- Add test_timeout and --timeout to test-running commands; record timed-out
  tests as failed runs with exit 124.
- Add note --next with an optional Progress note. Teach the agent block to use
  it, work on a branch, open a pull request and check on main after merging.

- When `done` refuses, say DONE was not written and name the unchanged status
  before the Missing lines. Keep dated `--note` text on early refusals too,
  including uncommitted code and missing test commands.
- Ask for a push after `done` only when the repository has an origin remote.
- Refuse template-only Acceptance as not filled in; show the edit in the
  quick start. Use `done` in `show` next steps for implementation, tests and
  remaining Acceptance work.
- Update the agent block to pull from origin with an explicit branch and
  rebase choice, only when origin exists. Explain that every clone runs the
  idempotent `init`, and remind clones with committed instructions but no hook.
- Keep `show` and `check` evidence verdicts consistent for stale claims, with
  the same explanation and renewal advice. Describe unclaimed tasks as
  unfinished even when their test evidence is in place.
- Name a single replayed implementation commit as rebase instead of squash.
- Report skipped-only reruns without also saying the test passed; preserve
  ordinary and strict exit codes.
- Update package versions and install pins to 0.7.0. New workflows written
  by `init` pin the running version.

## 0.6.0 (not released on its own; included in 0.12.0)

- On Windows, receipt writers for one task no longer fail with "permission denied"
  while another writer releases the task's lock.
- `init --check` accepts a hook without an executable bit on Windows, where Git
  runs it anyway.
- Add idempotent `init`, its read-only `--check`, and `--force` for a different hook.
- Add `done --task ID [--note TEXT]`: record changed implementation, run the declared test, show Acceptance for confirmation, and set DONE only when supported. Preserve status on refusal; never commit or push.
- Add opt-in `check --strict-tests`: require acceptance for additions and pytest configuration changes as well as removed or changed test lines. Refuse pytest reruns reporting no passed tests; ordinary reruns note skipped-only output.
- Add opt-in `--all-tests` for current-code regression runs of every DONE/CLOSED task, once per command, without changing history; add `--require-task` for code changes without task records.
- Say when supported claims without contracts rest on declared tests alone.
- Refuse claims from stale clones when upstream has task text or receipts missing locally. Refresh `show` with a short timeout and warn before work starts; start the agent block with `git pull`.
- Headline unclaimed work as unfinished, explain uncounted timeline tests, and distinguish pre-merge advice from a changed merge. Make Result wording name a stale claim without contradicting computed evidence.
- Accept `check --task ID`, resolve repository roots from subdirectories, and use `WORKORDER_AGENT` before Git user.name for claims and receipts.
- Let teammates without Workorder commit with a hook warning; keep empty staged checks silent.
- Update the agent block, quick start and strict CI example. Explain who can apply allow labels and recommend CODEOWNERS plus required review.

- Keep each task's protected paths separate when several tasks land in one
  squash. Exclude other tasks' recorded implementation commits and commits
  covered by the task's previous implemented receipt. Keep protecting paths
  the current task also changed.
- Correct `renew` help: it records implementation and tests; merges are computed.
- List the checks performed by `check --base`, with `--allow` and `--run-tests`.
- Explain in the task template that status is your claim and `show` and `check`
  compute what the receipts support.
- Give `show` a next step when only unclaimed Acceptance work remains, and
  document which empty planning fields it omits.
- Reset the unsupported status in the README refusal demo before committing.
- Update package versions and install pins to 0.6.0.

## 0.5.0 (14 Nov 2026)

- Check claims after a matched landing against its implementation paths for
  ancestry, same-code, squash and rebase merges. Refuse changed tested paths
  with renewal advice, including a new CLOSED after DONE; preserve valid
  earlier claims when later code changes.
- Refuse receipt files deleted or modified in any commit since `--base`,
  including receipts added and deleted within the range and restored edits.
  Keep the whole finished-task removal exception with `--allow ID`.
- Select merge targets from `merge_ref`, origin/main, origin/master, local
  main, then local master. Ignore origin/HEAD and validate remote branch refs.
- Read Git diffs losslessly so non-UTF-8 repository content can be checked.
- Fetch upstream task status with claim tags. Refuse claims on tasks already
  DONE, CLOSED or CANCELLED on origin/main, and show them in `ready` with
  advice to pull, even after their previous holder released them.
- Share receipt reads within a command, read committed metadata in batches,
  and walk linear task histories once. Keep CRLF receipt checks.
- Compute target merges from feature branches and detached checkouts. Keep
  finished claims after a rebase when their code patches still match.
- Suggest pushing main when a matching squash has not reached origin/main.
- Print checked task and claim counts. With `--run-tests`, count tests that
  actually ran and say plainly when none ran.
- Add `test_paths`. Base checks include changed test files and require
  reviewer acceptance for removed or changed lines, including renames.
- Require acceptance for a task created and claimed finished in one change.
- Allow complete removal of a task that was DONE, CLOSED or CANCELLED at the
  base with `--allow ID`. Keep refusing partial removal or unfinished tasks.
- Report dependency cycles on their tasks and keep checking and listing the rest.
- Document reviewer allow labels and declared test reruns in GitHub Actions.

## 0.4.0 (not released on its own; included in 0.5.0)

- Write one file per receipt. Parent hashes let branches add receipts for the
  same task and merge without editing one shared file. Legacy JSONL chains
  still verify, including receipts kept under `.notquite/`.
- Refuse changed or deleted committed receipt files in plain, staged and
  base checks. Verify file names, hashes and missing parents.
- Add `workorder repair --task ID` for verified legacy append conflicts.
- Compute merges from ancestry, identical code, squash patches and rebased
  patches. No merge receipt or extra commit on main is needed.
- Select the target from origin/HEAD or origin/main, else local main; allow a task
  to override it with `merge_ref`. Checks do not fetch.
- Bound and cache merge searches. Show the match in `show` and `check`, and
  explain how to renew evidence when the merged code differs.
- Count deployment and live read-backs after a computed merge.
- `ready` lists every task not yet claimed DONE or CLOSED as open work, even
  when its test evidence is already complete; only a claimed task can wait for
  delivery or drop off the list.
- `show` and the receipt commands no longer say a task "still owes nothing"
  before it is claimed: they name its Acceptance items and the DONE claim.
- `claim` says plainly that you now hold the task.

## 0.3.0 (not released on its own; included in 0.5.0)

NotQuite is now **Workorder: the task record for coding agents**. Every task is
recorded in the repository with a clear history, agents go back to it at the
start of every session, and DONE is checked against that history. At that release the check retained the old refusal wording.

Name and format
- New name everywhere: PyPI project `workorder`, the `workorder` command and
  package, receipts in `.workorder/`, ids and claims under
  `refs/tags/workorder/`. A NotQuite repository carries over with
  `git mv .notquite .workorder` (see docs/reference.md, Compatibility).
- `workorder new` writes the extended task by default; `--template simple`
  writes the 0.1.0 seven-field format. `ships` defaults to `none`.
- A task declares its `test` (`new --test ...`); DONE needs it, and only a run
  of exactly that command counts. `test: any` accepts any command explicitly.
  Commands split without a shell: backslashes are kept (Windows paths work),
  shell operators are refused.
- New status `CANCELLED`. Your own fields start with `x-`. Front matter also
  reads `#` comments, single-quoted values, left-margin lists, quoted list
  items and a UTF-8 byte order mark.

The record
- `workorder show` prints the task as a record: state, what is owed, title,
  mission, next and test, every section of the task, a dated timeline of its
  receipts, its Progress notes and the next step.
- `workorder note` adds a dated line under Progress.
- The agent instructions start every session with `show`, end it with `note`,
  and cover the task's test, committing, pushing and the merge.

Checking
- A test run counts when its code matches the implemented code, on any commit:
  a later failing run revokes DONE, also after the receipts are committed.
- `check` refuses a DONE or CLOSED claimed, in the working tree or in a commit,
  on code other than the implementation it relies on, or over uncommitted code.
- A dependency is finished when it says DONE or CLOSED, its evidence supports
  that, and its own dependencies are finished; `ready` shows the rest as
  blocked, and a computed status reads BLOCKED while a dependency is open.
- The pre-commit hook (`--staged`) and CI (`--base REF`) judge the change: the
  tasks it touches (task file, receipts, contract or evidence) and the tasks
  that depend on them. They stay fast as tasks pile up, and an old problem
  elsewhere does not block anyone. Plain `check` reads every task.
- `check --base` refuses removed or changed receipts, a stale DONE claimed
  since the base, and any change to a task's test, contract, evidence or
  dependencies, or a removed or reworded Scope or Acceptance line, unless a
  reviewer passes `--allow ID`. `--run-tests` runs each claimed task's declared
  test on the code being merged.
- A task file that cannot be read is a problem of that task only; `show`,
  `ready` and `check ID` keep working for the others.

Merges, claims and the rest
- Merges and deployments count when they contain the implemented commit, so a
  task reaches CLOSED on a busy main with merge commits; a merge receipt that
  counted stays counted.
- After a squash merge, rebase or amend, receipts whose commits are gone count
  again when a commit in this clone holds exactly their code (the squash
  commit, if main had not moved); otherwise `workorder renew` records the
  evidence again on the new commit in one step.
- A receipt that is recorded but cannot count says why and exits 1.
- `release --force` frees a claim whose clone or worktree is gone; `new --local`
  numbers a task without pushing a tag, and `new` suggests it when origin
  cannot be reached; `ready` works offline; stray claim tags are ignored.
- Receipt commands for one task are serialised by a lock; commit fingerprints
  are cached in `.git`; reading one ledger no longer lists the whole folder.
- New receipts and claims record only Git `user.name`, not the hostname.
- The changes above came from independent blind reviews; each has a
  regression test that fails without it.

## 0.2.0 (as NotQuite)

- Optional extended task format. `notquite new --template extended` adds
  `business_owner`, `priority`, `created_at`, `mission`, `depends_on`,
  `blocks`, `next` and `summary`, plus Context, Scope, Lanes, Acceptance and
  Progress sections. The default seven-field format is unchanged.
- `depends_on` and `waits_on` form one dependency list. `blocks` is checked
  for consistency but never enforced. Extended fields never count as evidence.
- A small YAML subset is read: `>` and `|` blocks for `mission`, `next` and
  `summary`, indented `- item` lists for `depends_on`, `blocks` and
  `waits_on`, simple `[TASK-1, TASK-2]` lists and empty lines between fields.
  Other YAML is refused with an explanation (see docs/reference.md).
- Compatibility: v0.1.0 refuses task files that use extended fields. Once any
  task uses them, run v0.2.0 everywhere, including CI pins.

## 0.1.0 (as NotQuite)

First public release.

- Task records in `tasks/*.md`.
- Hash-chained, append-only receipts for implementation, tests, merge,
  deployment and live read-back. The latest test run on the implemented commit
  decides: a later failure revokes DONE until a later run passes.
- Works in a repository without a remote for local tasks; ids and claims are
  coordinated through `origin` when there is one.
- `notquite check` refuses DONE and CLOSED claims the evidence does not support;
  `notquite check <id>` explains why and what to do next.
- Optional contracts that map each ask to evidence; MOCK, SYNTHETIC and UNKNOWN
  evidence never count.
- Task claims as Git tags.
- Pre-commit hook and GitHub Actions workflow.
- Tested in CI on Linux and Windows with Python 3.10, 3.12 and 3.13.
