# Changelog

## 0.2.0 (proposed, unreleased)

- Optional extended task format. `notquite new --template extended` adds
  `business_owner`, `priority`, `created_at`, `mission`, `depends_on`,
  `blocks`, `next` and `summary`, plus Context, Scope, Lanes, Acceptance and
  Progress sections. The default seven-field format is unchanged.
- `depends_on` and `waits_on` form one dependency list. `blocks` is checked
  for consistency but never enforced. Extended fields never count as evidence.
- Lists may also be written as `[TASK-1, TASK-2]`. Unsupported YAML, such as
  indented lists and multi-line values, is refused with an explanation.
- Compatibility: v0.1.0 refuses task files that use extended fields. Once any
  task uses them, run v0.2.0 everywhere, including CI pins.

## 0.1.0

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
