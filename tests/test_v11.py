"""Round-13 regressions, using local repositories and installed test tools."""

import os
import shutil

import pytest

from workorder import bootstrap, gate, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v09_setup import check_steps, execute


MUL = 'def mul(a, b):\n    return a * b\n'
DIV = '\ndef div(a, b):\n    return a / b\n'
MUL_TEST = 'from calc import mul\n\ndef test_mul():\n    assert mul(3, 4) == 12\n'
DIV_TEST = '\ndef test_div():\n    assert div(6, 3) == 2\n'


def calc_task(root, test_path, explicit=False):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'calc.py').write_text(MUL)
    (root / 'tests').mkdir()
    (root / 'tests/test_mul.py').write_text(MUL_TEST)
    path = task(root, ships='none', test=f'{PYTHON} -m pytest -q {test_path}')
    text = path.read_text() + '\n## Scope\n\nAdd division.\n\n## Acceptance\n\n- div(6, 3) is 2.\n'
    if explicit:
        text = text.replace('\ncontract:', f'\ntest_paths: ["{test_path}"]\ncontract:', 1)
    path.write_text(text)
    bootstrap.initialize(root)
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'division')
    return base


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('jq'), reason='needs POSIX shell and jq')
@pytest.mark.parametrize('explicit', [False, True])
def test_new_test_file_passes_generated_pull_request_command(local_repo, tmp_path, explicit):
    root = local_repo
    calc_task(root, 'tests/test_div.py', explicit)
    (root / 'calc.py').write_text(MUL + DIV)
    (root / 'tests/test_div.py').write_text('from calc import div\n' + DIV_TEST)
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert 'TASK-1 DONE:' in output and 'pending review' not in output
    commit_all(root)
    generated = (root / '.github/workflows/workorder.yml').read_text()
    command = next(command for step, command in check_steps(generated)
                   if "github.event_name == 'pull_request'" in step)
    arguments = execute(command, root, tmp_path, [])
    result = cli(root, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'no selected test files at REF' in result.stdout
    assert 'check: OK' in result.stdout


@pytest.mark.parametrize('imports', [
    'from calc import mul, div\n',
    'from calc import (\n    mul,\n    div,\n)\n',
])
def test_import_extension_and_new_test_pass_without_label(local_repo, imports):
    root = local_repo
    calc_task(root, 'tests/test_mul.py')
    (root / 'calc.py').write_text(MUL + DIV)
    (root / 'tests/test_mul.py').write_text(imports + MUL_TEST.split('\n', 1)[1] + DIV_TEST)
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert 'pending review' not in output
    commit_all(root)
    checked = cli(root, 'check', '--review', 'refuse', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task')
    assert checked.returncode == 0, checked.stdout + checked.stderr
    strict = cli(root, 'check', '--review', 'refuse', '--base', 'origin/main', '--strict-tests')
    assert strict.returncode == 1 and 'test files or pytest configuration changed' in strict.stdout


@pytest.mark.parametrize('body', [
    MUL_TEST.replace('== 12', '== 11'),
    MUL_TEST.replace('    assert mul(3, 4) == 12', '    assert True'),
    MUL_TEST.replace('    assert mul(3, 4) == 12\n', '    pass\n'),
    MUL_TEST.replace('    assert', '    assert not'),
])
def test_import_extension_does_not_hide_test_body_edits(local_repo, body):
    root = local_repo
    base = calc_task(root, 'tests/test_mul.py')
    (root / 'tests/test_mul.py').write_text(body.replace('import mul', 'import mul, div') + DIV_TEST)
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and 'lost or changed' in result.stdout


@pytest.mark.parametrize('replacement', [
    'from calc import div as mul',
    'from calc import mul, div as mul',
    'from other import mul, div',
    'from calc import *',
    'from calc import mul, div; mul = div',
])
def test_changed_import_bindings_still_need_review(local_repo, replacement):
    root = local_repo
    base = calc_task(root, 'tests/test_mul.py')
    (root / 'tests/test_mul.py').write_text(MUL_TEST.replace('from calc import mul', replacement))
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and 'lost or changed' in result.stdout


@pytest.mark.parametrize('snapshot_kind', ['working', 'staged', 'ref'])
def test_ignored_dependencies_are_linked_with_bounded_file_copies(local_repo, tmp_path, monkeypatch,
                                                                snapshot_kind):
    root = local_repo
    (root / '.gitignore').write_text('node_modules/\n__pycache__/\n.pytest_cache/\n')
    dependencies = root / 'node_modules'
    dependencies.mkdir()
    for number in range(1500):
        (dependencies / f'dep{number}.txt').write_text('dependency\n')
    (root / 'tracked.txt').write_text('base\n')
    (root / 'removed.txt').write_text('base\n')
    base = commit_all(root)
    (root / 'tracked.txt').write_text('index\n')
    (root / 'added.txt').write_text('staged addition\n')
    git(root, 'add', 'tracked.txt', 'added.txt')
    git(root, 'rm', 'removed.txt')
    (root / 'tracked.txt').write_text('working\n')
    (root / 'untracked.txt').write_text('untracked\n')
    copied = []
    original = shutil.copyfile

    def counted(source, destination, **kwargs):
        copied.append(str(source))
        return original(source, destination, **kwargs)

    monkeypatch.setattr(shutil, 'copyfile', counted)
    destination = tmp_path / 'snapshot'
    if snapshot_kind == 'ref':
        gate._ref_test_snapshot(root, destination, base)
    else:
        gate._test_snapshot(root, destination, staged=snapshot_kind == 'staged')
    assert len(copied) < 20, f'copied {len(copied)} files including ignored dependencies'
    assert (destination / 'node_modules/dep1499.txt').read_text() == 'dependency\n'
    assert (destination / 'node_modules').is_symlink()
    assert (destination / 'tracked.txt').read_text() == {
        'working': 'working\n', 'staged': 'index\n', 'ref': 'base\n'}[snapshot_kind]
    assert (destination / 'untracked.txt').exists() == (snapshot_kind == 'working')
    assert (destination / 'added.txt').exists() == (snapshot_kind != 'ref')
    assert (destination / 'removed.txt').exists() == (snapshot_kind == 'ref')
    if snapshot_kind == 'working':
        assert store._test_state(destination) == store._test_state(root)
    (destination / 'tracked.txt').write_text('copy only\n')
    assert (root / 'tracked.txt').read_text() == 'working\n'


@pytest.mark.parametrize('staged', [False, True])
def test_unowned_import_additions_pass_and_changed_assertions_refuse(local_repo, staged):
    probe = local_repo / 'test_calc.py'
    probe.write_text(MUL_TEST)
    base = commit_all(local_repo)
    probe.write_text(MUL_TEST.replace('import mul', 'import mul, div') + DIV_TEST)
    if staged:
        git(local_repo, 'add', 'test_calc.py')
    result = cli(local_repo, 'check', '--review', 'refuse', '--base', base, *(['--staged'] if staged else []))
    assert result.returncode == 0, result.stdout + result.stderr
    probe.write_text(probe.read_text().replace('== 12', '== 11'))
    if staged:
        git(local_repo, 'add', 'test_calc.py')
    refused = cli(local_repo, 'check', '--review', 'refuse', '--base', base, *(['--staged'] if staged else []))
    assert refused.returncode == 1 and 'lost or changed' in refused.stdout


@pytest.mark.parametrize('statement,extended', [
    ('import math', 'import math, decimal'),
    ('from math import floor as down', 'from math import floor as down, ceil as up'),
])
def test_import_additions_keep_existing_aliases(local_repo, statement, extended):
    probe = local_repo / 'test_calc.py'
    probe.write_text(statement + '\n\ndef test_one():\n    assert int(1) == 1\n')
    base = commit_all(local_repo)
    probe.write_text(probe.read_text().replace(statement, extended))
    checked = cli(local_repo, 'check', '--base', base)
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_new_import_name_cannot_shadow_an_existing_binding(local_repo):
    probe = local_repo / 'test_calc.py'
    probe.write_text('from helper import div\n' + MUL_TEST)
    base = commit_all(local_repo)
    probe.write_text(probe.read_text().replace('import mul', 'import mul, div'))
    result = cli(local_repo, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and 'lost or changed' in result.stdout


@pytest.mark.parametrize('snapshot_kind', ['working', 'staged', 'ref'])
def test_ignored_dependencies_do_not_replace_tracked_files(local_repo, tmp_path, snapshot_kind):
    root = local_repo
    (root / 'vendor').mkdir()
    (root / 'vendor/kept.py').write_text('value = "base"\n')
    base = commit_all(root)
    git(root, 'rm', 'vendor/kept.py')
    (root / '.gitignore').write_text('vendor/\n')
    commit_all(root)
    (root / 'vendor').mkdir(exist_ok=True)
    (root / 'vendor/kept.py').write_text('value = "ignored"\n')
    (root / 'vendor/dependency.txt').write_text('needed\n')
    destination = tmp_path / 'snapshot'
    if snapshot_kind == 'ref':
        gate._ref_test_snapshot(root, destination, base)
        assert (destination / 'vendor/kept.py').read_text() == 'value = "base"\n'
    else:
        gate._test_snapshot(root, destination, staged=snapshot_kind == 'staged')
        assert (destination / 'vendor/kept.py').read_text() == 'value = "ignored"\n'
    dependency = destination / 'vendor/dependency.txt'
    assert dependency.read_text() == 'needed\n'
    assert dependency.resolve() == root / 'vendor/dependency.txt'


@pytest.mark.parametrize('staged', [False, True])
def test_submodule_copy_uses_its_own_git_environment(local_repo, tmp_path, monkeypatch, staged):
    root = local_repo
    module = tmp_path / 'module'
    module.mkdir()
    git(module, 'init', '-q')
    git(module, 'config', 'user.name', 'dev')
    git(module, 'config', 'user.email', 'dev@example.invalid')
    (module / 'library.py').write_text('value = "committed"\n')
    (module / '.gitignore').write_text('node_modules/\n')
    commit_all(module)
    git(root, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(module), 'vendor')
    commit_all(root)
    (root / 'vendor/library.py').write_text('value = "working"\n')
    (root / 'vendor/node_modules').mkdir()
    (root / 'vendor/node_modules/needed.txt').write_text('dependency\n')
    (root / 'app.py').write_text('items = [1]\n')
    git(root, 'add', 'app.py')
    index = tmp_path / 'commit-index'
    index.write_bytes((root / '.git/index').read_bytes())
    before = index.read_bytes()
    for key, value in {'GIT_DIR': str(root / '.git'), 'GIT_WORK_TREE': str(root),
                       'GIT_INDEX_FILE': str(index)}.items():
        monkeypatch.setenv(key, value)
    destination = tmp_path / 'snapshot'
    gate._test_snapshot(root, destination, staged=staged)
    assert (destination / 'vendor/library.py').read_text() == 'value = "working"\n'
    dependency = destination / 'vendor/node_modules/needed.txt'
    assert dependency.read_text() == 'dependency\n'
    assert dependency.resolve() == root / 'vendor/node_modules/needed.txt'
    assert (destination / 'app.py').read_text() == 'items = [1]\n'
    assert index.read_bytes() == before


def test_uninitialized_submodule_stays_empty_in_test_copy(local_repo, tmp_path):
    root = local_repo
    git(root, 'update-index', '--add', '--cacheinfo', f'160000,{store.head(root)},vendor')
    git(root, 'commit', '-qm', 'Record uninitialized submodule')
    (root / 'vendor').mkdir()
    destination = tmp_path / 'snapshot'
    gate._test_snapshot(root, destination, staged=True)
    assert list((destination / 'vendor').iterdir()) == []


@pytest.mark.parametrize('command', ['make test', 'npm test'])
def test_new_wrapper_has_no_invented_test_paths(local_repo, command):
    root = local_repo
    (root / 'spec').mkdir()
    (root / 'spec/calc.js').write_text('assert.equal(mul(3, 4), 12);\n')
    refused = cli(root, 'new', '--title', 'Run tests', '--owner', 'dev', '--test', command)
    assert refused.returncode == 1 and '--test-paths' in refused.stderr
    ok(root, 'new', '--title', 'Run tests', '--owner', 'dev', '--test', command, '--test-paths', 'spec')
    record = store.record(root, 'TASK-1')
    assert record['test_paths'] == ['spec']
    base = commit_all(root)
    (root / 'spec/calc.js').write_text('assert.equal(true, true);\n')
    result = cli(root, 'check', '--base', base, '--strict-tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'runs a wrapper script' not in result.stdout
    assert 'spec/calc.js' in result.stdout


@pytest.mark.parametrize('argument', ['tests/test_future.py', 'tests/future', 'spec/future_spec.rb'])
def test_new_does_not_record_missing_paths(local_repo, argument):
    ok(local_repo, 'new', '--title', 'Future test', '--owner', 'dev',
       '--test', f'{PYTHON} -m pytest -q {argument}')
    assert 'test_paths' not in store.record(local_repo, 'TASK-1')


@pytest.mark.parametrize('path', ['spec/calc.js', 'nested/spec/fixture.json',
                                'src/calc_spec.rb', 'src/calc_tests.rs'])
def test_spec_and_plural_test_names_are_reviewed(local_repo, path):
    target = local_repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('original assertion\n')
    base = commit_all(local_repo)
    target.write_text('weakened assertion\n')
    result = cli(local_repo, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1, result.stdout + result.stderr
    assert path in result.stdout and 'workorder-allow:tests' in result.stdout
    assert cli(local_repo, 'check', '--review', 'refuse', '--base', base, '--allow', 'tests').returncode == 0
