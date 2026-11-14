"""Run generated event commands locally; never install packages or contact a remote."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from workorder import bootstrap, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, task


def workflow(root):
    bootstrap.initialize(root)
    return (root / '.github/workflows/workorder.yml').read_text()


def check_steps(text):
    """Read generated run blocks without adding a YAML dependency to the tests."""
    result = []
    for step in re.split(r'(?m)^      - ', text)[1:]:
        lines = step.splitlines()
        body = []
        for index, line in enumerate(lines):
            if line == '        run: |':
                for following in lines[index + 1:]:
                    if not following.startswith('          '):
                        break
                    body.append(following[10:])
            elif line.startswith('        run: '):
                body.append(line.split('run: ', 1)[1])
        command = '\n'.join(body)
        if 'workorder check' in command:
            result.append((step, command))
    return result


def execute(command, root, tmp_path, labels):
    binary = tmp_path / 'bin'
    binary.mkdir()
    log = tmp_path / 'args.json'
    tool = binary / 'workorder'
    tool.write_text(f'#!{PYTHON}\nimport json, os, pathlib, sys\n'
                    'pathlib.Path(os.environ["ARGS_LOG"]).write_text(json.dumps(sys.argv[1:]))\n')
    tool.chmod(0o755)
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': {'labels': [{'name': name} for name in labels]}}))
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
           'ARGS_LOG': str(log), 'GITHUB_EVENT_PATH': str(event), 'BASE': 'origin/main'}
    process = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=root, env=env,
                             capture_output=True, text=True)
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(log.read_text())


@pytest.mark.skipif(os.name == 'nt', reason='executes the GitHub Actions POSIX run block')
def test_default_branch_push_runs_current_tests_without_a_base(local_repo, tmp_path):
    steps = check_steps(workflow(local_repo))
    push = [command for step, command in steps if "github.event_name == 'push'" in step]
    assert len(push) == 1, 'push needs its own current-code check'
    assert execute(push[0], local_repo, tmp_path, []) == [
        'check', '--run-tests', '--all-tests']


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('jq'), reason='needs POSIX shell and jq')
@pytest.mark.parametrize('labels,allowed', [
    (['workorder-allow:TASK-8', 'workorder-allow:tests', 'workorder-allow:no-task'],
     'TASK-8,tests,no-task'),
    (['workorder-allow:TASK-8;touch injected', 'workorder-allow:all', 'other'], None),
])
def test_pull_request_labels_are_filtered_as_data(local_repo, tmp_path, labels, allowed):
    steps = check_steps(workflow(local_repo))
    pull = [(step, command) for step, command in steps if '--base' in command]
    assert len(pull) == 1
    arguments = execute(pull[0][1], local_repo, tmp_path, labels)
    expected = ['check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task']
    if allowed:
        expected += ['--allow', allowed]
    assert arguments == expected
    assert "github.event_name == 'pull_request'" in pull[0][0]
    assert not (local_repo / 'injected').exists()


@pytest.mark.parametrize('origin_head,local_branch,expected', [
    ('release/2026', 'main', 'release/2026'),
    (None, 'main', 'main'),
    (None, 'master', 'master'),
    (None, 'topic', 'main'),
])
def test_push_branch_is_inferred_without_network(local_repo, monkeypatch,
                                                origin_head, local_branch, expected):
    if local_branch != 'main':
        git(local_repo, 'branch', '-m', local_branch)
    if origin_head:
        git(local_repo, 'update-ref', 'refs/remotes/origin/' + origin_head, 'HEAD')
        git(local_repo, 'symbolic-ref', 'refs/remotes/origin/HEAD',
            'refs/remotes/origin/' + origin_head)
    original = store._git_run

    def no_network(root, *args, **kwargs):
        assert args[0] not in {'fetch', 'push', 'ls-remote'}
        return original(root, *args, **kwargs)

    monkeypatch.setattr(store, '_git_run', no_network)
    generated = workflow(local_repo)
    trigger = re.search(r'(?m)^  push:\n    branches: (.+)$', generated)
    assert trigger, 'workflow must run on default-branch pushes'
    assert json.loads(trigger[1]) == [expected]


def hook_env():
    return dict(os.environ, PYTHONPATH=str(PACKAGE), PYTHONDONTWRITEBYTECODE='1',
                PATH=str(Path(PYTHON).parent) + os.pathsep + os.environ['PATH'],
                GIT_TERMINAL_PROMPT='0')


def hooked(root, *args):
    """Run git with the repository hook. Never pass --no-verify."""
    assert '--no-verify' not in args
    result = subprocess.run(['git', '-c', 'commit.gpgsign=false', *args], cwd=root,
                            env=hook_env(), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_task_first_flow_passes_the_hook_and_the_generated_command(tmp_path):
    remote = tmp_path / 'origin.git'
    root = tmp_path / 'repo'
    git(tmp_path, 'init', '--bare', '-q', '--initial-branch=main', str(remote))
    git(tmp_path, 'clone', '-q', str(remote), str(root))
    git(root, 'config', 'user.name', 'dev')
    git(root, 'config', 'user.email', 'dev@example.invalid')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'app.py').write_text('value = 1\n')
    path = task(root, ships='none', test=f'{PYTHON} -m pytest -q tests/test_app.py')
    path.write_text(path.read_text() + '\n## Scope\n\nAdd shape.\n\n## Acceptance\n\n- shape strips spaces.\n')
    assert bootstrap.initialize(root) == 0
    hooked(root, 'add', '-A')
    hooked(root, 'commit', '-qm', 'Define the task before its test exists')
    hooked(root, 'push', '-q', 'origin', 'HEAD:main')
    hooked(root, 'checkout', '-qb', 'feature')
    (root / 'tests').mkdir()
    (root / 'tests/test_app.py').write_text(
        'import app\n\ndef test_shape():\n    assert app.shape(" ab ") == "ab"\n')
    noted = cli(root, 'note', '--task', 'TASK-1', '--next', 'Implement shape',
                'Test fails until shape exists')
    assert noted.returncode == 0, noted.stdout + noted.stderr
    hooked(root, 'add', '-A')
    hooked(root, 'commit', '-qm', 'Add a failing test and a handover note')
    (root / 'app.py').write_text('def shape(text):\n    return text.strip()\n')
    hooked(root, 'add', '-A')
    hooked(root, 'commit', '-qm', 'Implement shape')
    done = cli(root, 'done', '--task', 'TASK-1')
    assert done.returncode == 0, done.stdout + done.stderr
    hooked(root, 'add', '-A')
    hooked(root, 'commit', '-qm', 'Record completion')
    hooked(root, 'push', '-q', 'origin', 'HEAD:feature')
    checked = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task')
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert 'lost or changed' not in checked.stdout and 'Traceback' not in checked.stderr
    hooked(root, 'checkout', '-q', 'main')
    hooked(root, 'merge', '-q', '--squash', 'feature')
    hooked(root, 'commit', '-qm', 'Squash feature into main')
    on_main = cli(root, 'check', '--run-tests', '--all-tests')
    assert on_main.returncode == 0, on_main.stdout + on_main.stderr
    assert cli(root, 'check').returncode == 0


def test_claude_stop_command_blocks_at_the_client_boundary(local_repo):
    bootstrap.initialize(local_repo, claude=True)
    settings = json.loads((local_repo / '.claude/settings.json').read_text())
    command = settings['hooks']['Stop'][-1]['hooks'][0]['command']
    assert command == 'workorder hook stop'

    def stop(payload):
        return subprocess.run(command, cwd=local_repo, env=hook_env(), input=payload,
                              shell=True, capture_output=True, text=True)

    task(local_repo, status='DONE')
    blocked = stop('{}')
    assert blocked.returncode == 2
    assert 'NOT QUITE' in blocked.stderr and 'owes' in blocked.stderr
    looping = stop('{"stop_hook_active": true}')
    assert looping.returncode == 0
    assert looping.stderr == ''
    (local_repo / 'tasks/TASK-1.md').unlink()
    path = task(local_repo, ships='none', test=f'{PYTHON} -c pass')
    path.write_text(path.read_text() + '\n## Scope\n\nReady.\n\n## Acceptance\n\n- Ready.\n')
    git(local_repo, 'add', '-A')
    git(local_repo, 'commit', '-qm', 'Record a task that is still in progress')
    store.receipt(local_repo, 'TASK-1', 'implemented')
    store.receipt(local_repo, 'TASK-1', 'test', [PYTHON, '-c', 'pass'])
    path.write_text(path.read_text().replace('"IN_PROGRESS"', '"DONE"'))
    allowed = stop('{}')
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    assert 'NOT QUITE' not in allowed.stderr
