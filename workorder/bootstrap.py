"""Repository setup text, also shown verbatim in the README."""

from . import store as s


AGENT_BLOCK = r"""## Tasks
- Start every session with `git pull`, then `workorder show --task <id>` and work from what it prints: the mission and Scope say what to do and what not to; the Acceptance list says how the work will be accepted; `test` is the command that proves it; the history and progress say where the last session stopped.
- Before starting, run `workorder claim --task <id>` (needs an origin remote you can push to). Run `workorder release --task <id>` when you stop working on it, finished or not.
- After committing code and confirming every Acceptance item is met, run `workorder done --task <id> --note "<what was done; what is next>"`. It records implementation when needed, runs the declared test, and sets DONE only when its check supports the claim. If it refuses, report its Missing lines instead of saying the task is done. Acceptance lists are not checked by Workorder.
- Before you stop, record where you are with `workorder note --task <id> "<what was done; what is next>"`, keep `next` current, then commit tasks/ and .workorder/ with your work and push.
- If the task's `ships` is `repo` or `live`: after the change is merged, run `workorder check <id>` on main. It computes the merge from Git history. No merge receipt or extra commit is needed. If the merged change differs, run `workorder renew --task <id>` on main and test it again.
"""

HOOK = r"""#!/bin/sh
command -v workorder >/dev/null 2>&1 && exec workorder check --staged
echo "pre-commit: warning: workorder is not on PATH (pipx install workorder); skipping task check" >&2
exit 0
"""

WORKFLOW = r"""name: Workorder
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
permissions: {contents: read}
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}   # receipts refer to earlier commits
      - uses: actions/setup-python@v5
        with: {python-version: '3.12'}
      - run: python -m pip install workorder==0.6.0
      - name: Check tasks and run their tests
        env:
          BASE: origin/${{ github.base_ref }}
        run: |
          # task ids from pull request labels named workorder-allow:<ID>
          allow=$(jq -r '[.pull_request.labels[].name | select(test("^workorder-allow:[A-Z][A-Z0-9]*-[1-9][0-9]*$")) | sub("^workorder-allow:"; "")] | join(",")' "$GITHUB_EVENT_PATH")
          workorder check --base "$BASE" --run-tests --strict-tests ${allow:+--allow "$allow"}
"""


def initialize(root, check=False, force=False):
    """Report or install the agent instructions, hook and workflow; never replace a workflow."""
    hook = root / s.git(root, 'rev-parse', '--git-path', 'hooks/pre-commit').stdout.strip()
    hook_name = '.git/hooks/pre-commit'
    hook_exists = hook.exists()
    different = hook_exists and hook.read_text(encoding='utf-8') != HOOK
    if different and not check and not force:
        raise s.Refused(f'{hook_name} is different; refusing to overwrite it without --force')
    missing = False
    for name in ('AGENTS.md', 'CLAUDE.md'):
        path = s.local_path(root, name)
        original = path.read_text(encoding='utf-8') if path.exists() else ''
        if AGENT_BLOCK.strip() in original:
            print(f'{name}: agent block already present')
        elif check:
            missing = True
            print(f'{name}: missing agent block')
        else:
            path.write_text(original + ('\n\n' if original else '') + AGENT_BLOCK,
                            encoding='utf-8', newline='\n')
            print(f'{name}: added agent block')
    if hook_exists and not different and hook.stat().st_mode & 0o111:
        print(f'{hook_name}: already installed')
    elif check:
        missing = True
        detail = 'different hook (use --force to replace)' if different else 'missing or not executable'
        print(f'{hook_name}: {detail}')
    else:
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(HOOK, encoding='utf-8', newline='\n')
        hook.chmod(hook.stat().st_mode | 0o111)
        print(f'{hook_name}: installed' + (' (replaced with --force)' if different else ''))
    path = s.local_path(root, '.github/workflows/workorder.yml')
    if path.exists():
        print('.github/workflows/workorder.yml: already present; skipped')
    elif check:
        missing = True
        print('.github/workflows/workorder.yml: missing')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(WORKFLOW, encoding='utf-8', newline='\n')
        print('.github/workflows/workorder.yml: written')
    return int(missing)
