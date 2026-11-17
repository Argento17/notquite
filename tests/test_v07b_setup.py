"""Setup and local agent-hook regressions; generated install commands never install anything."""

import importlib
import importlib.metadata
import io
import json
import os
import shlex
import shutil
import subprocess
import sys

import pytest

from workorder import bootstrap, gate, store
from test_workorder import PYTHON, clones, git, local_repo, task


def workflow_commands(root):
    lines = (root / '.github/workflows/workorder.yml').read_text().splitlines()
    commands = []
    for index, line in enumerate(lines):
        if line.startswith('      - run: '):
            commands.append(line.split('run: ', 1)[1])
        elif line == '        run: |':
            body = []
            for following in lines[index + 1:]:
                if not following.startswith('          '):
                    break
                body.append(following[10:])
            commands.append('\n'.join(body))
        elif line.startswith('        run: '):
            commands.append(line.split('run: ', 1)[1])
    return commands


def fake_commands(tmp_path):
    """Record process boundaries instead of allowing pip or CI to run."""
    folder = tmp_path / 'bin'
    folder.mkdir()
    log = tmp_path / 'commands.jsonl'
    for name in ('python', 'workorder', 'gh'):
        path = folder / name
        path.write_text(f'#!{PYTHON}\n'
                        'import json, os, pathlib, sys\n'
                        'with open(os.environ["COMMAND_LOG"], "a") as stream:\n'
                        '    stream.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]) + "\\n")\n'
                        'if sys.argv[1:3] == ["-m", "workorder.review_labels"]: print("--allow TASK-8")\n')
        path.chmod(0o755)
    return {**os.environ, 'PATH': str(folder), 'COMMAND_LOG': str(log),
            'BASE': 'origin/review-branch', 'GITHUB_EVENT_PATH': '/tmp/event.json',
            'RUNNER_TEMP': str(tmp_path), 'GH_REPO': 'example/project', 'PR_NUMBER': '7'}, log


@pytest.fixture
def published_install(monkeypatch, tmp_path):
    metadata = tmp_path / 'workorder-7.8.9.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Name: workorder\nVersion: 7.8.9\n')
    distribution = importlib.metadata.PathDistribution(metadata)
    monkeypatch.setattr(importlib.metadata, 'distribution', lambda name: distribution)
    monkeypatch.setattr(bootstrap, '__version__', '7.8.9')
    return metadata


@pytest.mark.parametrize('files,test_command,expected', [
    ({'pyproject.toml': '[project.optional-dependencies]\ntest = ["pytest"]\n',
      'requirements-dev.txt': 'wrong\n', 'requirements.txt': 'wrong\n'},
     'python -m pytest', ['python', '-m', 'pip', 'install', '-e', '.[test]']),
    ({'pyproject.toml': '[project]\nname = "sample"\n',
      'requirements-dev.txt': 'pytest\n', 'requirements.txt': 'wrong\n'},
     'python -m pytest', ['python', '-m', 'pip', 'install', '-r', 'requirements-dev.txt']),
    ({'requirements.txt': 'pytest\n'}, 'python -m pytest',
     ['python', '-m', 'pip', 'install', '-r', 'requirements.txt']),
    ({}, 'python -m pytest tests', ['python', '-m', 'pip', 'install', 'pytest']),
    ({}, 'python test_app.py', None),
])
@pytest.mark.skipif(os.name == 'nt', reason='executes the Linux GitHub Actions shell commands')
def test_workflow_installs_detected_dependencies_before_exact_workorder(
        local_repo, tmp_path, published_install, capsys, files, test_command, expected):
    for name, content in files.items():
        (local_repo / name).write_text(content)
    task(local_repo, test=test_command)
    assert bootstrap.initialize(local_repo) == 0
    output = capsys.readouterr().out
    environment, log = fake_commands(tmp_path)
    for command in workflow_commands(local_repo):
        completed = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=local_repo,
                                   env=environment, capture_output=True, text=True)
        if expected is None and 'add your test dependencies here' in command:
            assert 'warning' in completed.stderr
            assert 'add your test dependencies here' in completed.stderr
            assert not log.exists()  # the warning itself installs nothing
        assert completed.returncode == 0, completed.stderr
    actual = [json.loads(line) for line in log.read_text().splitlines()]
    installs = [args for args in actual if args[:4] == ['python', '-m', 'pip', 'install']]
    assert installs == ([expected] if expected else []) + [
        ['python', '-m', 'pip', 'install', 'workorder==7.8.9']]
    if expected:
        assert expected[-1] in output
    else:
        assert 'no project test dependencies detected' in output.lower()
    assert actual[-1] == ['workorder', 'check', '--base', 'origin/review-branch',
                          '--run-tests', '--all-tests', '--require-task', '--allow', 'TASK-8']


@pytest.mark.parametrize('subdirectory', [None, 'packages/workorder'])
@pytest.mark.skipif(os.name == 'nt', reason='executes the Linux GitHub Actions shell commands')
def test_workflow_preserves_installed_git_commit(local_repo, tmp_path, published_install,
                                                subdirectory):
    direct = {'url': 'https://example.com/team/workorder.git',
              'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40, 'requested_revision': 'moving-main'}}
    if subdirectory:
        direct['subdirectory'] = subdirectory
    (published_install / 'direct_url.json').write_text(json.dumps(direct))
    (local_repo / 'requirements.txt').write_text('# no extra packages needed for this fixture\n')
    bootstrap.initialize(local_repo)
    environment, log = fake_commands(tmp_path)
    for command in workflow_commands(local_repo):
        result = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=local_repo,
                                env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    installs = [json.loads(line) for line in log.read_text().splitlines()
                if json.loads(line)[:4] == ['python', '-m', 'pip', 'install']]
    suffix = '#subdirectory=packages/workorder' if subdirectory else ''
    assert installs == [['python', '-m', 'pip', 'install', '-r', 'requirements.txt'],
                        ['python', '-m', 'pip', 'install',
                         'git+https://example.com/team/workorder.git@' + 'a' * 40 + suffix]]


def test_test_extra_detection_works_without_tomllib(local_repo, monkeypatch, published_install):
    monkeypatch.setitem(sys.modules, 'tomllib', None)
    (local_repo / 'pyproject.toml').write_text(
        '[project]\nname = "sample"\n\n[project.optional-dependencies]\n'
        '"test" = [\n  "pytest",\n]\n\n[tool.pytest.ini_options]\naddopts = "-q"\n')
    bootstrap.initialize(local_repo)
    installs = [shlex.split(command) for command in workflow_commands(local_repo)
                if command.startswith('python -m pip install')]
    assert installs[0] == ['python', '-m', 'pip', 'install', '-e', '.[test]']


def test_init_claude_merges_settings_and_is_idempotent(local_repo):
    path = local_repo / '.claude/settings.json'
    path.parent.mkdir()
    existing = {'permissions': {'allow': ['Read']}, 'model': 'custom', 'hooks': {
        'SessionStart': [{'matcher': 'resume', 'hooks': [{'type': 'command', 'command': 'echo existing'}]}],
        'PreToolUse': [{'hooks': [{'type': 'command', 'command': 'echo unrelated'}]}]}}
    path.write_text(json.dumps(existing))
    assert bootstrap.initialize(local_repo, claude=True) == 0
    actual = json.loads(path.read_text())
    assert actual['permissions'] == existing['permissions']
    assert actual['model'] == 'custom'
    assert actual['hooks']['PreToolUse'] == existing['hooks']['PreToolUse']
    assert actual['hooks']['SessionStart'][0] == existing['hooks']['SessionStart'][0]
    for event, command in [('SessionStart', 'workorder hook session-start'),
                           ('Stop', 'workorder hook stop')]:
        assert sum(hook.get('command') == command
                   for group in actual['hooks'][event] for hook in group['hooks']) == 1
    before = path.read_bytes()
    assert bootstrap.initialize(local_repo, claude=True) == 0
    assert bootstrap.initialize(local_repo, check=True, claude=True) == 0
    assert path.read_bytes() == before


def test_init_claude_check_is_read_only_when_hooks_are_missing(local_repo):
    bootstrap.initialize(local_repo)
    path = local_repo / '.claude/settings.json'
    before = git(local_repo, 'status', '--porcelain', '--untracked-files=all')
    assert bootstrap.initialize(local_repo, check=True, claude=True) == 1
    assert not path.exists()
    assert git(local_repo, 'status', '--porcelain', '--untracked-files=all') == before


@pytest.mark.parametrize('text', ['{ broken', '[]', '{"hooks": []}', '{"hooks": {"Stop": {}}}'])
def test_init_claude_refuses_malformed_settings_without_discarding_them(local_repo, text):
    path = local_repo / '.claude/settings.json'
    path.parent.mkdir()
    path.write_text(text)
    before = git(local_repo, 'status', '--porcelain', '--untracked-files=all')
    with pytest.raises(store.Refused, match='settings.json'):
        bootstrap.initialize(local_repo, claude=True)
    assert path.read_text() == text
    assert git(local_repo, 'status', '--porcelain', '--untracked-files=all') == before


@pytest.mark.skipif(os.name == 'nt', reason='executes a POSIX pre-commit hook')
def test_missing_tool_warns_once_per_commit_and_runs_the_check_when_present(local_repo, tmp_path):
    bootstrap.initialize(local_repo)
    hook = local_repo / '.git/hooks/pre-commit'
    folder = tmp_path / 'empty-bin'
    folder.mkdir()
    environment = {**os.environ, 'PATH': str(folder)}

    def invoke():
        return subprocess.run(['/bin/sh', str(hook)], cwd=local_repo, env=environment,
                              capture_output=True, text=True)

    missing = invoke()
    assert missing.returncode == 0 and missing.stdout == ''
    assert missing.stderr.count('warning') == 1
    tool = folder / 'workorder'
    tool.write_text('#!/bin/sh\n[ "$1 $2" = "check --staged" ] || exit 9\nexit 0\n')
    tool.chmod(0o755)
    installed = invoke()
    assert installed.returncode == 0 and installed.stdout == installed.stderr == ''
    before = git(local_repo, 'status', '--porcelain', '--untracked-files=all')
    tool.unlink()
    assert invoke().stderr.count('warning') == 1
    assert git(local_repo, 'status', '--porcelain', '--untracked-files=all') == before


def local_claim(root, tid, token='our-token', generation=1, released=False):
    path = store.claim_token_path(root, tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('our-token')
    value = {'task': tid, 'who': 'local-agent', 'at': '2026-11-14T00:00:00Z',
             'token_sha256': store.token_hash(token), 'nonce': 'one'}
    oid = store.git(root, 'hash-object', '-w', '--stdin', data=json.dumps(value)).stdout.strip()
    git(root, 'update-ref', f'refs/tags/workorder/claims/{tid}/{generation}', oid)
    if released:
        git(root, 'update-ref', f'refs/tags/workorder/releases/{tid}/{oid}', oid)


def refuse_network(monkeypatch, allow_claim_refresh=False):
    original = store._git_run

    def local_git(root, *args, **kwargs):
        # SessionStart re-reads claim and release tags. Other hook commands still stay offline.
        if allow_claim_refresh and args and args[0] in {'fetch', 'ls-remote'} and 'workorder' in ' '.join(args):
            return original(root, *args, **kwargs)
        assert args[0] not in {'fetch', 'ls-remote', 'push'}, 'hook attempted a network command'
        return original(root, *args, **kwargs)

    monkeypatch.setattr(store, '_git_run', local_git)


def test_session_start_shows_ready_and_only_tasks_held_in_this_clone(local_repo, monkeypatch, capsys):
    hooks = importlib.import_module('workorder.hooks')
    for number in range(1, 6):
        task(local_repo, f'TASK-{number}', ships='none')
    local_claim(local_repo, 'TASK-1')
    local_claim(local_repo, 'TASK-2', token='another-clone')
    local_claim(local_repo, 'TASK-3', released=True)
    local_claim(local_repo, 'TASK-4')
    local_claim(local_repo, 'TASK-4', token='new-holder', generation=2)
    token = store.claim_token_path(local_repo, 'TASK-5')
    token.write_text('token-with-no-fetched-tag-yet')
    git(local_repo, 'remote', 'add', 'origin', 'https://invalid.example/no-network.git')
    refuse_network(monkeypatch, allow_claim_refresh=True)
    assert hooks.run(local_repo, 'session-start') == 0
    output = capsys.readouterr().out
    assert 'workorder ready' in output
    assert 'TASK-1 held' in output
    assert 'TASK-1 IN_PROGRESS (not claimed finished)' in output
    for tid in ('TASK-2', 'TASK-3', 'TASK-4', 'TASK-5'):
        assert f'{tid} IN_PROGRESS (not claimed finished)' not in output


def test_session_start_tracks_successful_claim_and_release_without_fetching(clones, monkeypatch, capsys):
    hooks = importlib.import_module('workorder.hooks')
    root, _ = clones
    store.claim(root, 'TASK-1')
    with monkeypatch.context() as no_network:
        refuse_network(no_network, allow_claim_refresh=True)
        assert hooks.run(root, 'session-start') == 0
    assert 'workorder show --task TASK-1' in capsys.readouterr().out
    store.release(root, 'TASK-1')
    refuse_network(monkeypatch, allow_claim_refresh=True)
    assert hooks.run(root, 'session-start') == 0
    assert 'workorder show --task TASK-1' not in capsys.readouterr().out


def test_session_start_does_not_hold_a_task_after_losing_a_claim_race(clones, monkeypatch, capsys):
    hooks = importlib.import_module('workorder.hooks')
    root, winner = clones
    original = store.reserve

    def lose_race(repo, ref, value):
        with monkeypatch.context() as restore:
            restore.setattr(store, 'reserve', original)
            store.claim(winner, 'TASK-1')
        return original(repo, ref, value)

    monkeypatch.setattr(store, 'reserve', lose_race)
    with pytest.raises(store.Refused, match='claimed by'):
        store.claim(root, 'TASK-1')
    assert store.claim_token_path(root, 'TASK-1').exists()
    refuse_network(monkeypatch, allow_claim_refresh=True)
    assert hooks.run(root, 'session-start') == 0
    assert 'workorder show --task TASK-1' not in capsys.readouterr().out


@pytest.mark.parametrize('status,expected', [('IN_PROGRESS', 0), ('DONE', 2), ('CLOSED', 2)])
def test_stop_blocks_only_unsupported_completion_claims(local_repo, monkeypatch, capsys, status, expected):
    hooks = importlib.import_module('workorder.hooks')
    task(local_repo, status=status, ships='none')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"stop_hook_active": false}'))
    refuse_network(monkeypatch)
    assert hooks.run(local_repo, 'stop') == expected
    output = capsys.readouterr()
    assert output.out == ''
    if expected:
        assert 'NOT DONE  TASK-1' in output.err
    else:
        assert output.err == ''


def test_stop_allows_supported_completion_without_rerunning_test(local_repo, monkeypatch, capsys):
    hooks = importlib.import_module('workorder.hooks')
    path = task(local_repo, ships='none', test=f'{PYTHON} -c pass')
    store.receipt(local_repo, 'TASK-1', 'implemented')
    store.receipt(local_repo, 'TASK-1', 'test', gate.declared_test(store.record(local_repo, 'TASK-1')))
    path.write_text(path.read_text().replace('"IN_PROGRESS"', '"DONE"'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    refuse_network(monkeypatch)
    assert hooks.run(local_repo, 'stop') == 0
    output = capsys.readouterr()
    assert output.out == output.err == ''
    assert len(store.entries(local_repo, 'TASK-1')) == 2


def test_stop_active_guard_prevents_a_loop(local_repo, monkeypatch, capsys):
    hooks = importlib.import_module('workorder.hooks')
    task(local_repo, status='DONE')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"stop_hook_active": true}'))
    assert hooks.run(local_repo, 'stop') == 0
    output = capsys.readouterr()
    assert output.out == output.err == ''


@pytest.mark.parametrize('status,expected', [('IN_PROGRESS', 0), ('DONE', 2), ('CLOSED', 2)])
def test_stop_handles_malformed_tasks_according_to_their_claim(local_repo, monkeypatch, capsys,
                                                             status, expected):
    hooks = importlib.import_module('workorder.hooks')
    path = task(local_repo, status=status)
    path.write_text(path.read_text().replace('contract:', 'unknown_field:'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert hooks.run(local_repo, 'stop') == expected
    output = capsys.readouterr()
    assert output.out == ''
    assert ('NOT DONE' in output.err) == bool(expected)


@pytest.mark.parametrize('direct', [
    {'url': 'https://example.com/workorder.git', 'vcs_info': {'vcs': 'git', 'commit_id': int('1' * 40)}},
    {'url': '', 'vcs_info': {'vcs': 'git', 'commit_id': 'a' * 40}},
])
def test_workflow_invalid_git_metadata_falls_back_to_running_version(local_repo, published_install, direct):
    (published_install / 'direct_url.json').write_text(json.dumps(direct))
    bootstrap.initialize(local_repo)
    installs = [shlex.split(command) for command in workflow_commands(local_repo)
                if command.startswith('python -m pip install')]
    assert installs == [['python', '-m', 'pip', 'install', 'workorder==7.8.9']]


def test_git_install_pin_follows_metadata_not_the_readme_text(local_repo, published_install):
    from test_workorder import PACKAGE
    direct = {'url': 'https://example.com/team/workorder.git',
              'vcs_info': {'vcs': 'git', 'commit_id': 'b' * 40}}
    (published_install / 'direct_url.json').write_text(json.dumps(direct))
    bootstrap.initialize(local_repo)
    text = (local_repo / '.github/workflows/workorder.yml').read_text()
    pin = bootstrap.workorder_install()
    assert pin == 'git+https://example.com/team/workorder.git@' + 'b' * 40
    assert shlex.quote(pin) in text
    assert 'workorder==7.8.9' not in text
    reference = (PACKAGE / 'docs/reference.md').read_text()
    example_dependencies = ("      - name: Install project test dependencies\n"
                            "        run: python -m pip install -e '.[test]'\n")
    placeholder = ("      # Replace this step with your test dependency installation.\n"
                   "      - name: Add your test dependencies here\n"
                   "        run: |\n"
                   '          echo "::warning::No project test dependencies detected; add your test dependencies here" >&2\n')
    assert text.strip().replace(shlex.quote(pin), 'workorder==0.17.0') in reference.replace(
        example_dependencies, placeholder)
