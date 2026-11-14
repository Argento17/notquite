# Examples

Invented records for Pocket List, an invented to-do web app. They sit outside
`tasks/`, so they are reading material until you copy them in.

- `tasks/TASK-1.md` is being built.
- `tasks/TASK-2.md` waits for a person to supply text.
- `tasks/TASK-3.md` says DONE with no receipts at all.
- `tasks/TASK-4.md` uses the extended format and depends on TASK-1.
- `contracts/save-items.json` asks for REAL evidence and offers only a MOCK.

To watch the false DONE fail, from the root of a Git repository with
notquite installed:

```sh
mkdir -p tasks && cp examples/tasks/TASK-3.md tasks/
notquite check TASK-3
```

To add the contract, copy it to `.notquite/contracts/save-items.json` and set
TASK-3's `contract` field to that path. It still fails: a mock result cannot
satisfy a request for real output.
