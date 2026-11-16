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
in Click and attrs. Three honest changes must reach DONE without a label; three
shortcuts must be refused by both done and the base check. Logs and resolved
commit IDs remain in the printed temporary folder.

Click uses commit `e38188e9e205005d5af5f9e59df51904c984586a` and attrs commit
`3dca08ce5bbe673d7df25f44f3dda92505d1043d` (release 25.3.0); set `ATTRS_COMMIT` to another full
reviewed commit SHA to override it. Commit IDs are recorded in results.json.
