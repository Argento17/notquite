"""Repository setup text, also shown verbatim in the README."""

from importlib import metadata
import json
import os
import re
import shlex
from urllib.parse import quote
from . import __version__
from . import store as s


AGENT_BLOCK = r"""## Tasks
- Start every session from the task: if the repository has an origin remote, update first: `git pull --rebase origin main` (or your branch's upstream). Then run `workorder show --task <id>` and work from what it prints: the mission and Scope say what to do and what not to; the Acceptance list says how the work will be accepted; `test` is the command that proves it; the history and progress say where the last session stopped.
- Before starting, run `workorder claim --task <id>` (needs an origin remote you can push to). Run `workorder release --task <id>` when you stop working on it, finished or not.
- Work on a branch and open a pull request for review.
- After committing code and confirming every Acceptance item is met, run `workorder done --task <id> --note "<what was done; what is next>"`. It records implementation when needed, runs the declared test, and sets DONE only when its check supports the claim. If it refuses, report its Missing lines instead of saying the task is done. Acceptance lists are not checked by Workorder.
- Before you stop, record progress and the next step with `workorder note --task <id> --next "<next step>" "<what was done>"`, then commit tasks/ and .workorder/ with your work and push.
- After merge, run `workorder check <id>` on main; if the merged code differs, run `workorder renew --task <id>` there and test again.
"""

HOOK = r"""#!/bin/sh
if command -v workorder >/dev/null 2>&1; then
    exec workorder check --staged
fi
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
@DEPENDENCIES@      - run: python -m pip install @WORKORDER@
      - name: Check tasks and run their tests
        env:
          BASE: origin/${{ github.base_ref }}
        run: |
          # task ids from pull request labels named workorder-allow:<ID>
          allow=$(jq -r '[.pull_request.labels[].name | select(test("^workorder-allow:[A-Z][A-Z0-9]*-[1-9][0-9]*$")) | sub("^workorder-allow:"; "")] | join(",")' "$GITHUB_EVENT_PATH")
          workorder check --base "$BASE" --run-tests --all-tests --require-task ${allow:+--allow "$allow"}
"""


def has_test_extra(path):
    """Detect the standard test extra, including on Python 3.10 without tomllib."""
    if not path.is_file():
        return False
    text = path.read_text(encoding='utf-8')
    try:
        import tomllib
    except ImportError:
        # Only the table and key are needed; do not parse dependency expressions as Python.
        section = False
        for line in text.splitlines():
            if re.match(r'^\s*\[', line):
                section = bool(re.match(
                    r'''^\s*\[\s*["']?project["']?\s*\.\s*["']?optional-dependencies["']?\s*\]\s*(?:#.*)?$''',
                    line))
            elif section and re.match(r'''^\s*(?:test|"test"|'test')\s*=\s*\[''', line):
                return True
        return False
    try:
        project = tomllib.loads(text).get('project', {})
    except ValueError as error:
        raise s.Refused(f'pyproject.toml cannot be read: {error}') from None
    return 'test' in project.get('optional-dependencies', {})


def project_install(root):
    if has_test_extra(s.local_path(root, 'pyproject.toml')):
        return "python -m pip install -e '.[test]'"
    for name in ('requirements-dev.txt', 'requirements.txt'):
        if s.local_path(root, name).is_file():
            return f'python -m pip install -r {name}'
    from . import gate
    if any(gate.uses_pytest(record) for record in gate.records(root, errors={}).values()):
        return 'python -m pip install pytest'
    return None


def workorder_install():
    """PEP 610 records the immutable revision of a pip VCS installation."""
    requirement = f'workorder=={__version__}'
    try:
        direct = json.loads(metadata.distribution('workorder').read_text('direct_url.json') or '{}')
    except (metadata.PackageNotFoundError, OSError, ValueError):
        return requirement
    if not isinstance(direct, dict):
        return requirement
    vcs, url = direct.get('vcs_info'), direct.get('url')
    if (not isinstance(vcs, dict) or vcs.get('vcs') != 'git' or not isinstance(url, str) or not url.strip()
            or not isinstance(vcs.get('commit_id'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', vcs['commit_id'])):
        return requirement
    base, _, fragment = url.partition('#')
    requirement = (base if base.startswith('git+') else 'git+' + base) + '@' + vcs['commit_id']
    subdirectory = direct.get('subdirectory')
    if isinstance(subdirectory, str) and subdirectory:
        fragment = '&'.join(part for part in (fragment, 'subdirectory=' + quote(subdirectory, safe='/')) if part)
    return requirement + ('#' + fragment if fragment else '')


def claude_settings(root):
    """Prepare the complete merge before init writes anything; unknown settings stay intact."""
    path = s.local_path(root, '.claude/settings.json')
    try:
        original = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    except ValueError as error:
        raise s.Refused(f'.claude/settings.json cannot be read: {error}; existing settings were kept') from None
    if not isinstance(original, dict) or not isinstance(original.get('hooks', {}), dict):
        raise s.Refused('.claude/settings.json must contain an object with a hooks object; existing settings were kept')
    updated = {**original, 'hooks': dict(original.get('hooks', {}))}
    changed = False
    for event, command in (('SessionStart', 'workorder hook session-start'), ('Stop', 'workorder hook stop')):
        groups = updated['hooks'].get(event, [])
        if (not isinstance(groups, list) or any(not isinstance(group, dict)
                or not isinstance(group.get('hooks'), list)
                or any(not isinstance(hook, dict) for hook in group['hooks']) for group in groups)):
            raise s.Refused(f'.claude/settings.json has invalid {event} hooks; existing settings were kept')
        present = any(hook.get('type') == 'command' and hook.get('command') == command
                      and group.get('matcher', '') in ('', '*')
                      for group in groups for hook in group['hooks'])
        if not present:
            updated['hooks'][event] = groups + [{'hooks': [{'type': 'command', 'command': command}]}]
            changed = True
    return path, updated, changed


def initialize(root, check=False, force=False, claude=False):
    """Report or install the agent instructions, hook and workflow; never replace a workflow."""
    settings = claude_settings(root) if claude else None
    hook = root / s.git(root, 'rev-parse', '--git-path', 'hooks/pre-commit').stdout.strip()
    hook_name = '.git/hooks/pre-commit'
    hook_exists = hook.exists()
    different = hook_exists and hook.read_text(encoding='utf-8') != HOOK
    if different and not check and not force:
        raise s.Refused(f'{hook_name} is different; refusing to overwrite it without --force')
    if not hook_exists and any(
            AGENT_BLOCK.strip() in s.git(root, 'show', f'HEAD:{name}', required=False).stdout
            for name in ('AGENTS.md', 'CLAUDE.md')):
        print('Hooks are not cloned; run workorder init in every clone.')
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
    # Git for Windows runs hooks without an executable bit; elsewhere the bit is required.
    runnable = hook_exists and (os.name == 'nt' or bool(hook.stat().st_mode & 0o111))
    if hook_exists and not different and runnable:
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
        dependencies = project_install(root)
        dependency_step = ('      - name: Install project test dependencies\n'
                           f'        run: {dependencies}\n') if dependencies else ''
        workflow = WORKFLOW.replace('@DEPENDENCIES@', dependency_step).replace(
            '@WORKORDER@', shlex.quote(workorder_install()))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(workflow, encoding='utf-8', newline='\n')
        print('.github/workflows/workorder.yml: written')
        print('CI dependencies: ' + (dependencies or 'no project test dependencies detected'))
    if settings:
        path, updated, changed = settings
        if not changed:
            print('.claude/settings.json: hooks already installed')
        elif check:
            missing = True
            print('.claude/settings.json: missing Workorder hooks')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(updated, indent=2) + '\n', encoding='utf-8', newline='\n')
            print('.claude/settings.json: installed SessionStart and Stop hooks')
    return int(missing)
