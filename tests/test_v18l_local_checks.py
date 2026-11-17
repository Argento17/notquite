"""Round-21 lane L: done, the pre-commit check and the Stop hook give the pull-request check's answer."""
import json
import os
import re
import subprocess

import pytest

from test_workorder import PACKAGE, PYTHON, cli, git, ok


STYLE = 'import re\n\n\ndef unstyle(s):\n    return re.sub(r"\\x1b\\[[0-9;]*m", "", s)\n'
STYLE_TEST = 'from style import unstyle\n\n\ndef test_unstyle():\n    assert unstyle("\\x1b[1;31mx\\x1b[0m") == "x"\n'


def repo(tmp_path, files):
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '-q', '--bare', str(origin))
    root = tmp_path / 'work'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.name', 'solo')
    git(root, 'config', 'user.email', 'solo@example.com')
    (root / 'tests').mkdir()
    (root / '.gitignore').write_text('__pycache__/\n')
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    for name, text in files.items():
        (root / name).write_text(text)
    ok(root, 'init')
    git(root, 'remote', 'add', 'origin', str(origin))
    commit(root, 'setup')
    git(root, 'push', '-q', 'origin', 'main')
    return root


def commit(root, title):
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', title)


def new_task(root, test_file, code='calc.py'):
    ok(root, 'new', '--title', 'Work', '--owner', 'solo', '--test',
       f'python -m pytest -q {test_file}', '--code-paths', code)
    path = root / 'tasks/TASK-1.md'
    text = path.read_text()
    text = re.sub(r'## Scope\n.*?(?=\n## )', '## Scope\n\nIn: it.\n', text, flags=re.S)
    text = re.sub(r'## Acceptance\n.*?(?=\n## )', '## Acceptance\n\n- works.\n', text, flags=re.S)
    path.write_text(text)
    commit(root, 'task')
    git(root, 'push', '-q', 'origin', 'main')


def stop(root, env=None):
    return subprocess.run([PYTHON, '-m', 'workorder', 'hook', 'stop'], cwd=root, input='{}',
                          env=dict(env or os.environ, PYTHONPATH=str(PACKAGE)),
                          capture_output=True, encoding='utf-8')


def staged(root, env=None):
    return subprocess.run([PYTHON, '-m', 'workorder', 'check', '--staged'], cwd=root,
                          env=dict(env or os.environ, PYTHONPATH=str(PACKAGE)),
                          capture_output=True, encoding='utf-8')


def warn():
    return dict(os.environ, WORKORDER_REVIEW='warn')


def fable(tmp_path):
    """A legitimate new function, plus a narrowed old one and a new skip in a test no task owns."""
    root = repo(tmp_path, {'style.py': STYLE, 'tests/test_style.py': STYLE_TEST})
    new_task(root, 'tests/test_strip.py', 'style.py')
    git(root, 'switch', '-qc', 'work')
    (root / 'style.py').write_text(STYLE.replace('[0-9;]*m', '[0-9]*m') +
                                   '\n\ndef strip_styles(s):\n    return unstyle(s)\n')
    (root / 'tests/test_style.py').write_text(
        'import pytest\n' + STYLE_TEST.replace('def test_unstyle():\n',
                                               'def test_unstyle():\n    pytest.skip("flaky on CI")\n'))
    (root / 'tests/test_strip.py').write_text(
        'from style import strip_styles\n\n\ndef test_strip():\n    assert strip_styles("\\x1b[1mx\\x1b[0m") == "x"\n')
    git(root, 'add', '-A')
    return root


def test_staged_refuses_new_skip_and_changed_old_test_no_task_owns(tmp_path):
    root = fable(tmp_path)
    result = staged(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ('tests/test_style.py: its version from origin/main fails on this code (behaviour changed); '
            'a reviewer accepts it with the label workorder-allow:tests') in result.stdout
    assert 'tests: new skip or xfail behavior since origin/main' in result.stdout
    warned = staged(root, warn())
    assert warned.returncode == 0, warned.stdout + warned.stderr
    assert 'Note: tests: new skip or xfail behavior' in warned.stdout


def test_done_refuses_new_skip_and_changed_old_test_no_task_owns(tmp_path):
    root = fable(tmp_path)
    git(root, 'commit', '-qm', 'work')  # git() runs without the repository hook
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-1: DONE was not written; the status stays IN_PROGRESS' in result.stdout
    assert ('Missing:  tests/test_style.py: its version from origin/main fails on this code '
            '(behaviour changed); a reviewer accepts it with the label workorder-allow:tests\n') in result.stdout
    assert 'Missing:  tests: new skip or xfail behavior' in result.stdout
    assert 'status: "IN_PROGRESS"' in (root / 'tasks/TASK-1.md').read_text()
    assert not git(root, 'status', '--porcelain')


def finished_mul(tmp_path):
    root = repo(tmp_path, {'calc.py': 'def add(a, b):\n    return a + b\n'})
    new_task(root, 'tests/test_mul.py')
    git(root, 'switch', '-qc', 'work')
    with (root / 'calc.py').open('a') as stream:
        stream.write('\n\ndef mul(a, b):\n    return a * b\n')
    (root / 'tests/test_mul.py').write_text(
        'from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n\n\n'
        'def test_mul_neg():\n    assert mul(-3, 4) == -12\n')
    commit(root, 'mul')
    ok(root, 'done', '--task', 'TASK-1')
    commit(root, 'records')
    git(root, 'switch', '-q', 'main')
    git(root, 'merge', '-q', 'work')
    git(root, 'push', '-q', 'origin', 'main')
    git(root, 'switch', '-qc', 'next')
    return root


def test_hooks_refuse_a_deleted_test_of_a_finished_task(tmp_path):
    root = finished_mul(tmp_path)
    (root / 'calc.py').write_text('def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return abs(a * b)\n')
    (root / 'tests/test_mul.py').write_text('from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ('TASK-1: its tests from origin/main fail on this code; '
            'a reviewer accepts it with the label workorder-allow:TASK-1') in result.stdout
    assert staged(root, warn()).returncode == 0
    git(root, 'commit', '-qm', 'regression')
    result = stop(root)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'NOT DONE  TASK-1: its tests from origin/main fail on this code' in result.stderr
    assert stop(root, warn()).returncode == 0


def test_an_honest_fix_to_a_finished_task_still_commits(tmp_path):
    root = finished_mul(tmp_path)
    with (root / 'calc.py').open('a') as stream:
        stream.write('\n\ndef sub(a, b):\n    return a - b\n')
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 0, result.stdout + result.stderr
    git(root, 'commit', '-qm', 'sub')
    assert stop(root).returncode == 0


def hand_done(tmp_path, test):
    root = repo(tmp_path, {'calc.py': 'def add(a, b):\n    return a + b\n'})
    new_task(root, 'tests/test_mul.py')
    git(root, 'switch', '-qc', 'work')
    with (root / 'calc.py').open('a') as stream:
        stream.write('\n\ndef mul(a, b):\n    return a * b\n')
    (root / 'tests/test_mul.py').write_text(test)
    commit(root, 'mul')
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(root, 'receipt', 'test', '--task', 'TASK-1')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('status: "IN_PROGRESS"', 'status: "DONE"'))
    git(root, 'add', '-A')
    return root


@pytest.mark.parametrize('weak', [False, True])
def test_hooks_refuse_a_hand_set_done_without_a_completion_record(tmp_path, weak):
    test = ('from calc import mul\n\n\ndef test_mul():\n    mul(3, 4)\n    assert True\n' if weak else
            'from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    root = hand_done(tmp_path, test)
    missing = 'TASK-1: says DONE without a workorder done completion record for this change'
    result = staged(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert missing in result.stdout
    assert 'run workorder done --task TASK-1' in result.stdout
    if weak:
        assert ('TASK-1: test_mul in tests/test_mul.py asserts nothing that can fail; '
                'a reviewer accepts it with the label workorder-allow:TASK-1') in result.stdout
    git(root, 'commit', '-qm', 'hand done')
    result = stop(root)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'NOT DONE  ' + missing in result.stderr


def test_done_writes_a_completion_record_that_commits(tmp_path):
    root = hand_done(tmp_path, 'from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('status: "DONE"', 'status: "IN_PROGRESS"'))
    commit(root, 'receipts')
    ok(root, 'done', '--task', 'TASK-1')
    receipts = [json.loads(p.read_text()) for p in (root / '.workorder').rglob('*.json')]
    assert [item for item in receipts if item.get('completion') is True and item['kind'] == 'test']
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 0, result.stdout + result.stderr
    git(root, 'commit', '-qm', 'done')
    assert stop(root).returncode == 0


def test_a_hand_set_done_already_on_the_target_is_not_rejudged(tmp_path):
    root = hand_done(tmp_path, 'from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    git(root, 'commit', '-qm', 'hand done')
    git(root, 'switch', '-q', 'main')
    git(root, 'merge', '-q', 'work')
    git(root, 'push', '-q', 'origin', 'main')
    git(root, 'switch', '-qc', 'later')
    (root / 'NOTES.md').write_text('notes\n')
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 0, result.stdout + result.stderr
    git(root, 'commit', '-qm', 'notes')
    assert stop(root).returncode == 0


def test_a_completion_record_is_only_true_on_a_test_receipt(tmp_path):
    root = hand_done(tmp_path, 'from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    from workorder import store
    kinds = set()
    for path in (root / '.workorder').rglob('*.json'):
        item = json.loads(path.read_text())
        item.pop('hash')
        kinds.add(item['kind'])
        for value in ([False, 'yes'] if item['kind'] == 'test' else [True]):
            changed = {**item, 'completion': value}
            with pytest.raises(store.Refused, match='invalid completion record'):
                store.verify_receipt(root, 'TASK-1', {**changed, 'hash': store.digest(store.packed(changed).encode())})
    assert kinds == {'implemented', 'test'}


def test_hooks_refuse_a_removed_test_of_a_finished_task_without_a_code_change(tmp_path, monkeypatch):
    root = finished_mul(tmp_path)
    (root / 'tests/test_mul.py').write_text('from calc import mul\n\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ('TASK-1: its test files lost or changed existing Python statements since origin/main') in result.stdout
    assert 'workorder-allow:TASK-1' in result.stdout
    assert 'If intended: the pull request needs the label named above' in result.stdout
    git(root, 'commit', '-qm', 'remove a test')
    result = stop(root)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'its test files lost or changed' in result.stderr
    monkeypatch.setenv('WORKORDER_REVIEW', 'warn')
    assert stop(root).returncode == 0
