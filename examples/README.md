# Examples

Invented records for Pocket List, an invented to-do web app. They sit outside
`tasks/`, so they are reading material until you copy them in.

- `tasks/TASK-1.md` is being built.
- `tasks/TASK-2.md` waits for a person to supply text.
- `tasks/TASK-3.md` says DONE with no receipts at all.
- `tasks/TASK-4.md` uses the extended format and depends on TASK-1.
- `contracts/save-items.json` asks for REAL evidence and offers only a MOCK.

To watch the false DONE fail, from the root of a Git repository with
workorder installed:

```sh
mkdir -p tasks && cp examples/tasks/TASK-3.md tasks/
workorder check TASK-3
```

To add the contract, copy it to `.workorder/contracts/save-items.json` and set
TASK-3's `contract` field to that path. It still fails: a mock result cannot
satisfy a request for real output.

## Installed upstream projects

Run `sh examples/real-projects.sh python` from a checkout with Workorder installed.
It needs Python 3.12 and network access, creates fresh temporary clones and
virtual environments, installs each project editable, and checks six cases each
in Click and attrs, plus three Click cases using its existing
`tests/test_basic.py`. Eight honest changes must reach DONE without a label;
seven shortcuts must be refused by both done and the pull-request check. Logs
and resolved commit IDs remain in the printed temporary folder.

The shared-file cases append a callable-only test that must be refused and a
test of the new boolean option behavior that must reach DONE. The third case
finishes a task that runs tests from the shared folder, then adds a new test
function there for a second task. Its pull-request check must pass without a
label or changes to the finished task. Task definitions land before the changes
that finish them.

Click uses commit `e38188e9e205005d5af5f9e59df51904c984586a` and attrs commit
`3dca08ce5bbe673d7df25f44f3dda92505d1043d` (release 25.3.0); set `ATTRS_COMMIT` to another full
reviewed commit SHA to override it. Commit IDs are recorded in results.json.
