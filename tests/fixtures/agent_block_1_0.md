## Tasks
- Start from the task: if the repository has an origin remote, run `git fetch origin`. On main, update with `git pull --rebase origin main`. On a feature branch, merge or rebase main as needed. Run `workorder show --task <id>` and follow its Scope, Acceptance, test and progress.
- Set `WORKORDER_AGENT` when sharing a Git identity. Run `workorder claim --task <id>` before work.
- Do the work with a test that fails without it. Commit, then run `workorder done --task <id> --note "<what was done>"`. Report refusals; never claim completion after one or run `workorder accept`.
- Keep unfinished progress with `workorder note --task <id> --next "<next step>" "<progress>"`; done clears next.
- Never edit a task's status or receipts by hand. Commit tasks/ and .workorder/ with the work.
- Renew only when the task's own code or tests changed after DONE: `workorder renew --task <id>`.
- Before saying done, run `workorder check --base origin/main --run-tests --all-tests --require-task`.
- Output is shortened; the full text is in `.git/workorder/last-output.txt`.
