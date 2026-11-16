"""Repository setup text, also shown in the README and reference."""

from importlib import metadata
import difflib
import json
import os
import re
import shlex
from urllib.parse import quote
from . import __version__
from . import store as s


AGENT_BLOCK = r"""## Tasks
- Start from the task: if the repository has an origin remote, run `git fetch origin`. On main, update with `git pull --rebase origin main`. On a feature branch, merge or rebase main as needed. Use the default branch's name. Run `workorder show --task <id>`; follow its mission, Scope, Acceptance, test, history and progress.
- Set `WORKORDER_AGENT` when sharing a Git identity. Run `workorder claim --task <id>` before work (needs a writable origin), and `workorder release --task <id>` when stopping, finished or not.
- Create tasks with `workorder new --code-paths <files or folders>`. Land the task first, then implement on a branch and open a pull request. A task written and finished in one pull request needs an approving review or `workorder-allow:<id>`.
- Confirm every Acceptance item, commit code, fetch origin if present, then run `workorder done --task <id> --note "<what was done>"`. Workorder checks test evidence, not Acceptance. Report refusals and warnings; do not claim completion after them.
- Use `done --task <id> --refactor` for unchanged tests passing before and after; use `done --task <id> --needs-review` for intended test edits. Report pending review: the pull request needs the task's label or approval. Refused done writes no notes or receipts; use `workorder note` for handover.
- Keep unfinished progress with `workorder note --task <id> --next "<next step>" "<progress>"`; done clears next. Before stopping, commit tasks/ and .workorder/ with the work and push.
- Renew only when the task's own code or tests changed after DONE: `workorder renew --task <id>`. Commit its receipts. After merge, run `workorder check <id>` on main; renew and test again if needed.
"""

HOOK = r"""#!/bin/sh
previous="${0%/*}/pre-commit.workorder-previous"
if [ -f "$previous" ]; then
    "$previous" "$@" || exit $?
fi
if command -v workorder >/dev/null 2>&1; then
    exec workorder check --staged
fi
echo "pre-commit: warning: workorder is not on PATH (pipx install workorder, or put the virtualenv that has workorder on PATH); skipping task check" >&2
exit 0
"""

WORKFLOW = r"""name: Workorder
on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]
  pull_request_review:
    types: [submitted]
  push:
    branches: @DEFAULT_BRANCH@
permissions: {contents: read, pull-requests: read, issues: read}
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # receipts refer to earlier commits
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
      - uses: actions/setup-python@v5
        with: {python-version: '3.12'}
@DEPENDENCIES@      - run: python -m pip install @WORKORDER@
      - name: Check tasks after merge
        if: github.event_name == 'push'
        run: |
          trap 'status=$?; if [ "$status" -ne 0 ]; then echo "Reviewer guide: https://github.com/Argento17/workorder/blob/main/docs/reviewer-guide.md" >&2; fi; exit "$status"' 0
          workorder check --run-tests --all-tests
      - name: Check tasks and run their tests
        if: github.event_name == 'pull_request' || github.event_name == 'pull_request_review'
        env:
          BASE: origin/${{ github.event.pull_request.base.ref }}
          GH_TOKEN: ${{ github.token }}
          GH_REPO: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        # Optional: append --require-task-ignore GLOB for each code path exception, for example:
        # --require-task-ignore 'docs/**' --require-task-ignore '*.md'
        run: |
          trap 'status=$?; if [ "$status" -ne 0 ]; then echo "Reviewer guide: https://github.com/Argento17/workorder/blob/main/docs/reviewer-guide.md" >&2; fi; exit "$status"' 0
          # Reviewed task ids, unowned tests, or the code-to-task requirement.
          gh api --paginate --slurp "repos/$GH_REPO/issues/$PR_NUMBER/events" > "$RUNNER_TEMP/workorder-label-events.json"
          gh api --paginate --slurp "repos/$GH_REPO/pulls/$PR_NUMBER/reviews" > "$RUNNER_TEMP/workorder-reviews.json"
          allowances=$(python -m workorder.review_labels "$GITHUB_EVENT_PATH" "$RUNNER_TEMP/workorder-label-events.json" "$RUNNER_TEMP/workorder-reviews.json" "$BASE")
          @REVIEW_COMMENT@
          workorder check --base "$BASE" --run-tests --all-tests @REVIEW_OPTIONS@ $allowances
"""


def default_branch(root):
    """Choose the push trigger from local refs without fetching during setup."""
    remote_head = s.git(root, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD',
                        required=False).stdout.strip()
    prefix = 'refs/remotes/origin/'
    if remote_head.startswith(prefix):
        return remote_head[len(prefix):]
    for branch in ('main', 'master'):
        if any(s.git(root, 'show-ref', '--verify', '--quiet', ref, required=False).returncode == 0
               for ref in (f'refs/remotes/origin/{branch}', f'refs/heads/{branch}')):
            return branch
    return 'main'


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


def dependency_group(path):
    if not path.is_file():
        return None
    text = path.read_text(encoding='utf-8')
    try:
        import tomllib
    except ImportError:
        groups, section = set(), False
        for line in text.splitlines():
            if re.match(r'^\s*\[', line):
                section = bool(re.match(r"^\s*\[\s*['\"]?dependency-groups['\"]?\s*\]", line))
            elif section:
                match = re.match(r"^\s*['\"]?(test|tests|dev)['\"]?\s*=\s*\[", line)
                if match:
                    groups.add(match[1])
    else:
        try:
            groups = tomllib.loads(text).get('dependency-groups', {})
        except ValueError as error:
            raise s.Refused(f'pyproject.toml cannot be read: {error}') from None
    return next((name for name in ('test', 'tests', 'dev') if name in groups), None)


def project_install(root):
    group = dependency_group(s.local_path(root, 'pyproject.toml'))
    if group:
        return f"python -m pip install --upgrade 'pip>=25.1' && python -m pip install --group {group} ."
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


def initialize(root, check=False, force=False, claude=False, update_workflow=False, light=False, test_first=False,
               project_test=None):
    """Report or install setup; rewrite an existing workflow only when explicitly requested."""
    from .python_imports import installed_src
    from . import project_tests
    project_matches = project_tests.configure(root, project_test, check=True) if project_test is not None else True
    installed = installed_src(root)
    if installed:
        print('src layout: installed ' + ', '.join(installed) + '; comparison copies put src/ first and verify imports.')
    settings = claude_settings(root) if claude else None
    written = []
    hook = root / s.git(root, 'rev-parse', '--git-path', 'hooks/pre-commit').stdout.strip()
    hook_name = '.git/hooks/pre-commit'
    hook_exists = hook.exists()
    different = hook_exists and hook.read_text(encoding='utf-8') != HOOK
    previous = hook.with_name('pre-commit.workorder-previous')
    if different and not check and not force:
        raise s.Refused(f'{hook_name} is different; refusing to overwrite it without --force')
    if different and not check and previous.exists():
        raise s.Refused(f'{previous.name} already exists; keep both existing hooks before installing another')
    if not hook_exists and any(
            AGENT_BLOCK.strip() in s.git(root, 'show', f'HEAD:{name}', required=False).stdout
            for name in ('AGENTS.md', 'CLAUDE.md')):
        if check:
            print('Hooks are not cloned; run workorder init in every clone.')
        else:
            print('Hooks are not cloned; setting up the pre-commit hook in this clone.')
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
            written.append(name)
            print(f'{name}: added agent block')
    # Git for Windows runs hooks without an executable bit; elsewhere the bit is required.
    runnable = hook_exists and (os.name == 'nt' or bool(hook.stat().st_mode & 0o111))
    if hook_exists and not different and runnable:
        print(f'{hook_name}: already installed')
    elif check:
        missing = True
        detail = 'different hook (use --force to chain)' if different else 'missing or not executable'
        print(f'{hook_name}: {detail}')
    else:
        hook.parent.mkdir(parents=True, exist_ok=True)
        if different:
            hook.rename(previous)
            print(f'{hook_name}: kept as {previous.name}; the new hook calls it first')
        hook.write_text(HOOK, encoding='utf-8', newline='\n')
        hook.chmod(hook.stat().st_mode | 0o111)
        written.append(hook_name)
        print(f'{hook_name}: installed')
    path = s.local_path(root, '.github/workflows/workorder.yml')
    if path.exists() and not update_workflow:
        print('.github/workflows/workorder.yml: already present; skipped')
    elif check and not update_workflow:
        missing = True
        print('.github/workflows/workorder.yml: missing')
    else:
        dependencies = project_install(root)
        dependency_step = ('      - name: Install project test dependencies\n'
                           f'        run: {dependencies}\n') if dependencies else (
                               '      # Replace this step with your test dependency installation.\n'
                               '      - name: Add your test dependencies here\n'
                               '        run: |\n'
                               '          echo "::warning::No project test dependencies detected; add your test dependencies here" >&2\n')
        workflow = WORKFLOW.replace('@DEPENDENCIES@', dependency_step).replace(
            '@WORKORDER@', shlex.quote(workorder_install())).replace(
            '@DEFAULT_BRANCH@', json.dumps([default_branch(root)])).replace(
            '@REVIEW_OPTIONS@', ('--review warn' if light else '--require-task')
            + (' --require-test-first' if test_first else '')).replace(
            '@REVIEW_COMMENT@', '# Trial setup: restore defaults with workorder init --update-workflow.' if light
            else '# Test changes are refused unless a reviewer other than the author adds the label or approves this head; --review warn is optional.')
        original = path.read_text(encoding='utf-8') if path.exists() else ''
        if update_workflow:
            print(''.join(difflib.unified_diff(original.splitlines(keepends=True),
                                              workflow.splitlines(keepends=True),
                                              fromfile=str(path.relative_to(root)),
                                              tofile=str(path.relative_to(root)))), end='')
        if check:
            missing |= original != workflow
            print('.github/workflows/workorder.yml: ' + ('needs update' if original != workflow else 'up to date'))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(workflow, encoding='utf-8', newline='\n')
            written.append('.github/workflows/workorder.yml')
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
            written.append('.claude/settings.json')
            print('.claude/settings.json: installed SessionStart and Stop hooks')
    if project_test is not None and not project_matches:
        if check:
            missing = True
            print('.workorder/config.json: project test needs update')
        else:
            project_tests.configure(root, project_test)
            written.append(project_tests.CONFIG)
            print('.workorder/config.json: project test set')
    if not check:
        print('Files written:' if written else 'No files written.')
        for name in written:
            print(f'  {name}')
        tracked = [name for name in written if name != hook_name]
        if tracked:
            print('git add ' + shlex.join(tracked) + " && git commit -m 'Set up Workorder'")
    return int(missing)
