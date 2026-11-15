"""Round-17 regressions; all repositories are disposable local directories."""

import io
import sys

import pytest

from workorder import gate, hooks, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v04 import legacy_file, raw_receipt
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import branch, check, claim, CODE


@pytest.mark.parametrize('location', ['same', 'nested', 'conftest', 'import', 'alias', 'module', 'relative'])
def test_assertion_helpers_count(local_repo, location):
    root = local_repo
    helper = 'def equal(actual, expected):\n    assert actual == expected\n'
    # Keep helpers on the base so configuration review cannot mask assertion recognition.
    (root / 'tests').mkdir()
    (root / 'tests/__init__.py').write_text('')
    if location == 'conftest':
        (root / 'tests/conftest.py').write_text(helper)
    elif location in {'import', 'alias', 'module', 'relative'}:
        (root / 'tests/support.py').write_text(helper)
    branch(root, code=None)
    prefix, body = '', 'equal(div(6, 3), 2)'
    if location == 'same':
        prefix = helper
    elif location == 'nested':
        body = 'def equal(actual, expected):\n        assert actual == expected\n    equal(div(6, 3), 2)'
    elif location == 'conftest':
        prefix = 'from tests.conftest import equal\n'
    elif location == 'import':
        prefix = 'from tests.support import equal\n'
    elif location == 'alias':
        prefix = 'from tests.support import equal as matches\n'
        body = 'matches(div(6, 3), 2)'
    elif location == 'module':
        prefix = 'import tests.support as support\n'
        body = 'support.equal(div(6, 3), 2)'
    else:
        prefix = 'from .support import equal\n'
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text('from calc import div\n' + prefix + '\ndef test_div():\n    ' + body + '\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2')
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('name', ['assertEqual', 'verify', 'check_result', 'expect_value', 'ensure_equal'])
def test_assertion_call_names_count(local_repo, name):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE + '\ndef ' + name + '(a, b):\n    assert a == b\n')
    (root / 'tests/test_div.py').write_text(
        f'from calc import div, {name}\ndef test_div():\n    {name}(div(6, 3), 2)\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2')


def test_helpers_follow_calls_with_a_fixed_depth_and_ignore_uncalled_bodies(local_repo):
    root = local_repo
    base = store.head(root)
    path = root / 'test_chain.py'
    path.write_text('def equal(a, b):\n    assert a == b\n'
                    'def middle(a, b):\n    equal(a, b)\n'
                    'def test_chain():\n    middle(1, 1)\n')
    record = {'id': 'TASK-1', 'test': 'pytest test_chain.py'}
    assert gate.assertion_problems(root, base, record) == []
    path.write_text('def equal():\n    assert value\ndef test_chain():\n    pass\n')
    assert gate.assertion_problems(root, base, record)
    path.write_text('def circle():\n    circle()\ndef test_chain():\n    circle()\n')
    assert gate.assertion_problems(root, base, record)
    path.write_text('def helper0():\n    assert value\n' + ''.join(
        f'def helper{n}():\n    helper{n - 1}()\n' for n in range(1, 12)) +
        'def test_chain():\n    helper11()\n')
    assert gate.assertion_problems(root, base, record)


def test_conftest_helpers_are_resolved_only_on_the_test_path(local_repo):
    root = local_repo
    base = store.head(root)
    (root / 'tests').mkdir()
    (root / 'other').mkdir()
    (root / 'tests/conftest.py').write_text('def equal(a, b):\n    assert a == b\n')
    (root / 'tests/test_math.py').write_text('def test_math(equal):\n    equal(2, 2)\n')
    record = {'id': 'TASK-1', 'test': 'pytest tests/test_math.py'}
    assert gate.assertion_problems(root, base, record) == []
    (root / 'tests/conftest.py').rename(root / 'other/conftest.py')
    assert gate.assertion_problems(root, base, record)


@pytest.mark.parametrize('expression', ['1 == 1', '2 + 2 == 4', 'not False', '[1] == [1]', '"x" in "xyz"'])
def test_constant_expressions_cannot_use_a_missing_module_as_red(local_repo, expression):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(f'from calc import div\ndef test_div():\n    assert {expression}\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'asserts nothing that can fail' in result.stdout
    assert store.entries(root, 'TASK-2') == []
    claim(root)
    result = check(root)
    assert result.returncode == 1 and 'asserts nothing that can fail' in result.stdout


def test_unusual_assertion_needs_its_task_label(local_repo):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE + '\ndef equal(a, b):\n    assert a == b\n')
    (root / 'tests/test_div.py').write_text('from calc import div, equal\ndef test_div():\n    equal(div(6, 3), 2)\n')
    commit_all(root)
    claim(root)
    for options in [[], ['--approving-review'], ['--review', 'warn'], ['--allow', 'tests']]:
        refused = check(root, *options)
        assert refused.returncode == 1, refused.stdout
        assert 'workorder-allow:TASK-2' in refused.stdout
    accepted = check(root, '--allow', 'TASK-2')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def verification_branch(root, target):
    (root / '.gitignore').write_text('__pycache__/\n')
    (root / 'feature.py').write_text('def double(x):\n    return 2 * x\n')
    (root / 'test_feature.py').write_text('from feature import double\nassert double(2) == 4\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, 'test_feature.py'])))
    base = commit_all(root)
    git(root, 'checkout', '-qb', 'feature')
    if not target:
        git(root, 'branch', '-D', 'main')
    return base


@pytest.mark.parametrize('target', [False, True])
@pytest.mark.parametrize('change', ['modify', 'add', 'delete', 'test'])
def test_no_code_change_refuses_code_edits(local_repo, target, change):
    root = local_repo
    verification_branch(root, target)
    if change == 'modify':
        (root / 'feature.py').write_text('def double(x):\n    return 4\n')
    elif change == 'add':
        (root / 'extra.py').write_text('broken = True\n')
    elif change == 'delete':
        (root / 'app.py').unlink()
    else:
        (root / 'test_feature.py').write_text('from feature import double\nassert double(2) == 4\n# edited\n')
    commit_all(root)
    before = (root / 'tasks/TASK-1.md').read_bytes()
    result = cli(root, 'done', '--task', 'TASK-1', '--no-code-change', '--needs-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert '--no-code-change is for verification tasks with no code change' in result.stdout
    expected = {'modify': 'feature.py', 'add': 'extra.py', 'delete': 'app.py', 'test': 'test_feature.py'}[change]
    assert f'TASK-1: code changed ({expected})' in result.stdout
    assert (root / 'tasks/TASK-1.md').read_bytes() == before
    assert store.entries(root, 'TASK-1') == []


@pytest.mark.parametrize('target', [False, True])
@pytest.mark.parametrize('mode', ['plain', 'base', 'staged'])
def test_old_no_code_change_receipt_cannot_exempt_changed_code(local_repo, target, mode):
    root = local_repo
    base = verification_branch(root, target)
    (root / 'feature.py').write_text('def double(x):\n    return 4\n')
    commit_all(root)
    ok(root, 'renew', '--task', 'TASK-1')
    # Model a receipt written by 0.14.0, with a valid hash and no new members.
    latest = store.entries(root, 'TASK-1')[-1]
    store.append(root, 'TASK-1', {key: latest[key] for key in
        ('kind', 'commit', 'code', 'command', 'exit_code', 'valid')} | {'no_code_change': True})
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    if mode == 'staged':
        git(root, 'add', 'tasks', '.workorder')
    flags = ['--staged'] if mode == 'staged' else ['--base', base, '--allow', 'TASK-1,tests', '--review', 'warn'] if mode == 'base' else []
    result = cli(root, 'check', *flags)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-1: code changed (feature.py); --no-code-change is for verification tasks with no code change' in result.stdout


def finished_command(root, target):
    verification_branch(root, target)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    base = commit_all(root)
    if target:
        git(root, 'update-ref', 'refs/heads/main', base)
    (root / 'test_feature.py').rename(root / 'test_math.py')
    definition = root / 'tasks/TASK-1.md'
    definition.write_text(definition.read_text().replace('test_feature.py', 'test_math.py'))
    commit_all(root)
    ok(root, 'renew', '--task', 'TASK-1')
    # Without a target, HEAD must still contain the old command for a local comparison.
    if not target:
        git(root, 'reset', '--soft', base)
    return base


@pytest.mark.parametrize('target', [False, True])
@pytest.mark.parametrize('action', ['staged', 'stop'])
def test_finished_test_command_repair_is_a_local_note(local_repo, monkeypatch, capsys, target, action):
    root = local_repo
    base = finished_command(root, target)
    if action == 'staged':
        git(root, 'add', '.')
        result = cli(root, 'check', '--staged')
        assert result.returncode == 0, result.stdout + result.stderr
        output = result.stdout
    else:
        monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
        assert hooks.run(root, 'stop') == 0
        output = capsys.readouterr().out
    assert output.count('Note: TASK-1: test command changed and passes on this code; '
                        'after committing run workorder renew --task TASK-1; '
                        'the pull request needs workorder-allow:TASK-1') == 1
    assert 'test changed:' not in output
    assert 'workorder-allow:TASK-1' in output
    result = cli(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('staged_passes', [False, True])
def test_command_repair_uses_staged_test_not_working_copy(local_repo, staged_passes):
    root = local_repo
    finished_command(root, True)
    path = root / 'test_math.py'
    passing = path.read_text()
    path.write_text(passing if staged_passes else 'raise AssertionError("staged failure")\n')
    git(root, 'add', '.')
    path.write_text('raise AssertionError("unstaged failure")\n' if staged_passes else passing)
    result = cli(root, 'check', '--staged')
    assert result.returncode == (0 if staged_passes else 1), result.stdout + result.stderr
    if not staged_passes:
        assert 'staged failure' in result.stdout + result.stderr


def test_done_still_refuses_finished_task_command_repair(local_repo):
    root = local_repo
    finished_command(root, True)
    result = cli(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'test changed:' in result.stdout


def test_verification_after_landing_uses_the_new_run_base(local_repo):
    root = local_repo
    verification_branch(root, True)
    (root / 'feature.py').write_text('def double(x):\n    return x + x\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--refactor')
    commit_all(root)
    git(root, 'update-ref', 'refs/heads/main', 'HEAD')
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    ok(root, 'check')


def test_existing_verification_receipt_keeps_its_historical_claim(local_repo):
    root = local_repo
    verification_branch(root, True)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    base = commit_all(root)
    (root / 'feature.py').write_text('def double(x):\n    return x + x\n')
    store.add_note(root, 'TASK-1', 'Still covered by the original test')
    ok(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')


def test_repaired_legacy_verification_keeps_its_historical_claim(local_repo):
    root = local_repo
    verification_branch(root, True)
    first = raw_receipt(root, previous='')
    command = store.split_command(store.record(root, 'TASK-1')['test'])
    a = raw_receipt(root, 'test', previous=first['hash'], at=1,
                    command=command, no_code_change=True)
    b = raw_receipt(root, 'test', previous=first['hash'], at=2,
                    command=command, no_code_change=True)
    path = legacy_file(root, [first, b])
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    base = commit_all(root)
    path.write_text(store.packed(first) + '\n<<<<<<< main\n' + store.packed(b)
                    + '\n=======\n' + store.packed(a) + '\n>>>>>>> side\n')
    ok(root, 'repair', '--task', 'TASK-1')
    (root / 'feature.py').write_text('def double(x):\n    return x + x\n')
    ok(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')


@pytest.mark.parametrize('global_asserts', [False, True])
def test_helper_uses_its_defining_scope(local_repo, global_asserts):
    root = local_repo
    base = store.head(root)
    global_body = 'assert a == b' if global_asserts else 'pass'
    nested_body = 'pass' if global_asserts else 'assert a == b'
    (root / 'test_scope.py').write_text(
        f'def equal(a, b):\n    {global_body}\n'
        'def helper(a, b):\n    equal(a, b)\n'
        'def test_scope():\n'
        f'    def equal(a, b):\n        {nested_body}\n'
        '    helper(1, 1)\n')
    problems = gate.assertion_problems(root, base, {'id': 'TASK-1', 'test': 'pytest test_scope.py'})
    assert bool(problems) is not global_asserts


@pytest.mark.parametrize('later_class', [False, True])
def test_same_file_class_helper_counts(local_repo, later_class):
    root = local_repo
    base = store.head(root)
    source = (
        'class TestMath:\n'
        '    def equal(self, a, b):\n        assert a == b\n'
        '    def test_math(self):\n        self.equal(2, 2)\n')
    if later_class:
        source = source.replace('assert a == b', 'Utility.equal(a, b)')
        source += 'class Utility:\n    @staticmethod\n    def equal(a, b):\n        assert a == b\n'
    (root / 'test_math.py').write_text(source)
    assert gate.assertion_problems(root, base, {'id': 'TASK-1', 'test': 'pytest test_math.py'}) == []


def test_command_repair_cannot_reuse_another_tasks_working_copy_pass(local_repo):
    root = local_repo
    (root / 'test_new.py').write_text('assert int(1) == 1\n')
    (root / 'test_old.py').write_text('assert int(1) == 1\n')
    for number, filename in [(1, 'test_new.py'), (2, 'test_old.py')]:
        fill_sections(task(root, f'TASK-{number}', ships='none', test=store.join_command([PYTHON, filename])))
    commit_all(root)
    for number in (1, 2):
        ok(root, 'done', '--task', f'TASK-{number}', '--no-code-change')
    commit_all(root)
    definition = root / 'tasks/TASK-2.md'
    definition.write_text(definition.read_text().replace('test_old.py', 'test_new.py'))
    ok(root, 'renew', '--task', 'TASK-2')
    store.add_note(root, 'TASK-1', 'Select this task too')
    path = root / 'test_new.py'
    path.write_text('raise AssertionError("staged failure")\n')
    git(root, 'add', '.')
    path.write_text('assert int(1) == 1\n')
    result = cli(root, 'check', '--staged', '--run-tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'staged failure' in result.stdout + result.stderr


def test_cli_helper_allows_completion_longer_than_thirty_seconds(local_repo):
    root = local_repo
    command = store.join_command([PYTHON, '-c', 'import time; time.sleep(31)'])
    fill_sections(task(root, ships='none', test=command))
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert 'TASK-1 DONE:' in output


def test_verification_receipt_survives_a_squash_without_its_original_commit(local_repo, tmp_path):
    root = local_repo
    verification_branch(root, True)
    store.add_note(root, 'TASK-1', 'Verify the existing implementation')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    original = store.entries(root, 'TASK-1')[0]['commit']
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '--squash', 'feature')
    commit_all(root)
    git(root, 'branch', '-D', 'feature')
    clone = tmp_path / 'fresh'
    git(tmp_path, 'clone', '--no-local', '--single-branch', '-q', str(root), str(clone))
    assert not store.has_commit(clone, original)
    ok(clone, 'check')
