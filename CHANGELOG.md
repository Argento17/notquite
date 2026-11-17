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

## Before 0.18

Earlier releases, first under the name NotQuite, built the task record (task files, receipts, `show`,
`note`, claims and dependencies), `done` and its checks (a task's test must pass now, fail on the code
before the change and stop passing when the change is stubbed out), the pre-commit hook, the
pull-request check and the reviewer guide.
