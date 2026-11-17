"""Round-21 lane F: project test on by default, new --kind, and advisory claims."""
import json
import os
import subprocess

import pytest

from workorder import store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok
from test_v05 import commit_all
from test_v17r import CODE, TEST, define, setup, branch, check


# Item 95: new --kind

@pytest.mark.parametrize('kind', ['feature', 'fix', 'refactor', 'verification'])
def test_new_kind_is_written_read_and_shown(local_repo, kind):
    root = local_repo
    ok(root, 'new', '--title', 'Kinds', '--owner', 'solo', '--kind', kind)
    text = (root / 'tasks/TASK-1.md').read_text()
    assert f'kind: "{kind}"\ncontract:' in text
    assert store.record(root, 'TASK-1')['kind'] == kind
    assert f'kind:     {kind}' in ok(root, 'show', '--task', 'TASK-1').splitlines()


def test_new_without_kind_writes_no_kind_field(local_repo):
    root = local_repo
    ok(root, 'new', '--title', 'Plain', '--owner', 'solo')
    assert 'kind' not in (root / 'tasks/TASK-1.md').read_text()
    assert not any(line.startswith('kind:') for line in ok(root, 'show', '--task', 'TASK-1').splitlines())


def test_new_refuses_an_unknown_kind(local_repo):
    result = cli(local_repo, 'new', '--title', 'Bad', '--owner', 'solo', '--kind', 'bugfix')
    assert result.returncode == 2 and 'invalid choice' in result.stderr
    assert not (local_repo / 'tasks').exists()


def test_new_kind_refactor_predeclares_the_refactor_completion(local_repo):
    """The flag writes the field the gate reads: a landed kind: refactor needs no label."""
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_value.py').write_text(TEST)
    ok(root, 'new', '--title', 'Tidy', '--owner', 'solo', '--ships', 'none', '--kind', 'refactor',
       '--test', 'python -m pytest -q tests/test_value.py', '--code-paths', 'calc.py')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('## Scope\n', '## Scope\n\nTidy value.\n').replace(
        '## Acceptance\n', '## Acceptance\n\n- value still returns 1.\n'))
    branch(root)
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 1 + 0'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--refactor')
    commit_all(root)
    result = check(root, '--approving-review')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'completion needs review' not in result.stdout


# Item 96: claims are advisory, done refuses another holder's task without --take-over

@pytest.fixture
def claimed(tmp_path):
    """A landed task in a shared remote, claimed by clone one; clone two works on a branch."""
    remote, seed = tmp_path / 'remote.git', tmp_path / 'seed'
    git(tmp_path, 'init', '--bare', '-q', '--initial-branch=main', str(remote))
    git(tmp_path, 'clone', '-q', str(remote), str(seed))
    git(seed, 'config', 'user.name', 'seed')
    git(seed, 'config', 'user.email', 'seed@example.com')
    setup(seed)
    # The failing test lands first; the branch makes it pass.
    (seed / 'tests/test_value.py').write_text(TEST.replace('== 1', '== 2'))
    commit_all(seed)
    git(seed, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
    clones = []
    for name in ('one', 'two'):
        path = tmp_path / name
        git(tmp_path, 'clone', '-q', str(remote), str(path))
        git(path, 'config', 'user.name', name)
        git(path, 'config', 'user.email', f'{name}@example.com')
        clones.append(path)
    one, two = clones
    ok(one, 'claim', '--task', 'TASK-1')
    git(two, 'switch', '-q', '-c', 'feature')
    (two / 'calc.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(two)
    return one, two


def test_note_and_receipt_warn_about_another_holder(claimed):
    one, two = claimed
    noted = cli(two, 'note', '--task', 'TASK-1', 'started on calc')
    assert noted.returncode == 0, noted.stdout + noted.stderr
    assert 'Warning: TASK-1 is held by one since' in noted.stdout
    assert 'started on calc' in (two / 'tasks/TASK-1.md').read_text()
    commit_all(two)
    recorded = cli(two, 'receipt', 'implemented', '--task', 'TASK-1')
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    assert 'Warning: TASK-1 is held by one since' in recorded.stdout
    # The holder's own clone is not warned.
    assert 'Warning' not in ok(one, 'note', '--task', 'TASK-1', 'mine')


def test_done_refuses_another_holders_task_without_take_over(claimed):
    one, two = claimed
    before = (two / 'tasks/TASK-1.md').read_bytes()
    refused = cli(two, 'done', '--task', 'TASK-1')
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert 'Warning: TASK-1 is held by one since' in refused.stdout
    assert 'Missing:  TASK-1 is claimed by one' in refused.stdout
    assert 'done --task TASK-1 --take-over' in refused.stdout
    assert (two / 'tasks/TASK-1.md').read_bytes() == before
    assert not (two / '.workorder/receipts').exists()


def test_done_take_over_records_it_and_the_pr_check_notes_it(claimed):
    one, two = claimed
    done = cli(two, 'done', '--task', 'TASK-1', '--take-over')
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'TASK-1 DONE' in done.stdout
    text = (two / 'tasks/TASK-1.md').read_text()
    assert 'status: "DONE"' in text or 'status: DONE' in text
    assert 'taken over from one (claim held since' in text and ') by two' in text
    commit_all(two)
    result = check(two)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Note: TASK-1 was finished by two while one held its claim' in result.stdout


def test_done_by_the_holder_or_after_release_has_no_claim_finding(claimed):
    one, two = claimed
    ok(one, 'release', '--task', 'TASK-1')
    done = cli(two, 'done', '--task', 'TASK-1')
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'held by' not in done.stdout
    commit_all(two)
    result = check(two)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'held its claim' not in result.stdout


# Item 94: a first init turns the project test on when it finds pytest tests

def config(root):
    path = root / '.workorder/config.json'
    return json.loads(path.read_text()) if path.exists() else None


@pytest.mark.parametrize('name,text', [
    ('pyproject.toml', '[project]\nname = "demo"\n\n[tool.pytest.ini_options]\ntestpaths = ["checks"]\n'),
    ('setup.cfg', '[metadata]\nname = demo\n\n[tool:pytest]\naddopts = -q\n'),
    ('tox.ini', '[tox]\nenvlist = py\n\n[pytest]\naddopts = -q\n'),
    ('pytest.ini', '[pytest]\n'),
    ('tests/test_value.py', 'def test_value():\n    assert True\n'),
    ('test/sub/value_test.py', 'def test_value():\n    assert True\n'),
])
def test_first_init_turns_on_the_detected_project_test(local_repo, name, text):
    root = local_repo
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)
    output = ok(root, 'init')
    assert config(root) == {'project_test': 'python -m pytest -q'}
    found = name if '/' not in name else name.split('/')[0] + '/'
    assert (f'.workorder/config.json: project test on: python -m pytest -q (pytest found in {found}); '
            'done and the pull-request check run your full suite once; if it takes minutes, '
            'turn it off with workorder init --no-project-test') in output.splitlines()
    assert '  .workorder/config.json' in output.splitlines()


@pytest.mark.parametrize('name,text', [
    (None, None),
    ('pyproject.toml', '[project]\nname = "demo"\n[tool.ruff]\nline-length = 100\n'),
    ('setup.cfg', '[metadata]\nname = demo\n'),
    ('tox.ini', '[tox]\nenvlist = py\n'),
    ('tests/helpers.py', 'VALUE = 1\n'),
    ('test_calc.py', 'assert True\n'),
])
def test_init_without_pytest_leaves_the_project_test_off(local_repo, name, text):
    root = local_repo
    if name:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    output = ok(root, 'init')
    assert config(root) is None
    assert '.workorder/config.json' not in output


def test_init_after_setup_and_no_project_test_keep_the_choice(local_repo):
    root = local_repo
    ok(root, 'init')
    commit_all(root)
    # An existing setup (as after an upgrade) is not changed by a later init.
    (root / 'tests').mkdir()
    (root / 'tests/test_value.py').write_text('def test_value():\n    assert True\n')
    assert '.workorder/config.json' not in ok(root, 'init')
    assert config(root) is None
    # --no-project-test on a first init keeps it off; it also turns a configured one off.
    other = root.parent / 'other'
    other.mkdir()
    git(other, 'init', '-q', '-b', 'main')
    (other / 'tests').mkdir()
    (other / 'tests/test_value.py').write_text('def test_value():\n    assert True\n')
    assert 'project test on' not in ok(other, 'init', '--no-project-test')
    assert config(other) is None
    ok(root, 'init', '--project-test', 'python -m pytest')
    assert 'project test turned off' in ok(root, 'init', '--no-project-test')
    assert config(root) == {}
    assert cli(root, 'init', '--project-test', 'x', '--no-project-test').returncode == 2


def test_detected_project_test_refuses_a_broken_other_test(local_repo):
    """Round-21 reviewer case: a change broke tests outside the task's test; done said DONE."""
    root = local_repo
    setup(root)
    (root / 'other.py').write_text('VALUES = ("1", "true")\n')
    (root / 'tests/test_other.py').write_text(
        'from other import VALUES\n\ndef test_true():\n    assert "true" in VALUES\n')
    (root / 'tests/test_value.py').write_text(TEST.replace('== 1', '== 2'))
    assert 'project test on: python -m pytest -q' in ok(root, 'init')
    branch(root)
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 2'))
    (root / 'other.py').write_text('VALUES = ("1",)\n')
    commit_all(root)
    done = cli(root, 'done', '--task', 'TASK-1')
    assert done.returncode == 1, done.stdout + done.stderr
    assert 'the project test fails on this code: tests/test_other.py::test_true (passes on' in done.stdout
    (root / 'other.py').write_text('VALUES = ("1", "true")\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    (root / 'other.py').write_text('VALUES = ("1",)\n')
    commit_all(root)
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'tests/test_other.py::test_true' in result.stdout
    assert 'check: OK' not in result.stdout


def test_init_check_reports_the_detected_project_test_without_writing(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    (root / 'tests/test_value.py').write_text('def test_value():\n    assert True\n')
    result = cli(root, 'init', '--check')
    assert result.returncode == 1  # the rest of the setup is missing
    assert ('.workorder/config.json: init would turn the project test on: python -m pytest -q '
            '(pytest found in tests/); --no-project-test keeps it off') in result.stdout.splitlines()
    assert config(root) is None


@pytest.mark.skipif(os.name == 'nt', reason='uses a POSIX shell script as a stand-in python')
def test_init_leaves_the_project_test_off_when_python_cannot_import_pytest(local_repo, tmp_path):
    root = local_repo
    (root / 'tests').mkdir()
    (root / 'tests/test_value.py').write_text('def test_value():\n    assert True\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'python'
    fake.write_text('#!/bin/sh\nexit 1\n')
    fake.chmod(0o755)
    env = dict(os.environ, PYTHONPATH=str(PACKAGE), PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
    result = subprocess.run([PYTHON, '-m', 'workorder', 'init'], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert ('.workorder/config.json: pytest found in tests/, but python cannot import pytest here; project test '
            'left off (set one with workorder init --project-test CMD)') in result.stdout.splitlines()
    assert config(root) is None
