"""Round-16 regressions, using disposable local Git repositories only."""

import io
import json
import sys

import pytest

from workorder import gate, hooks, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections


CODE = 'def div(a, b):\n    return a / b\n'
TEST = 'from calc import div\n\ndef test_div():\n    assert div(6, 3) == 2\n'


def branch(root, tid='TASK-2', test_text=None, code=CODE, new=False):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir(exist_ok=True)
    if code is not None:
        (root / 'calc.py').write_text(code)
    if test_text is not None:
        (root / 'tests/test_div.py').write_text(test_text)
    if not new:
        fill_sections(task(root, tid, ships='none', test=store.join_command(
            [PYTHON, '-m', 'pytest', '-q', 'tests/test_div.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    if new:
        fill_sections(task(root, tid, ships='none', test=store.join_command(
            [PYTHON, '-m', 'pytest', '-q', 'tests/test_div.py'])))
    return base


def claim(root, tid='TASK-2'):
    ok(root, 'receipt', 'implemented', '--task', tid)
    ok(root, 'receipt', 'test', '--task', tid)
    store.set_task_field(root, tid, 'status', 'DONE')


def check(root, *extra):
    return cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests',
               '--require-task', *extra)


def test_declared_refactor_completes_and_is_visible_to_reviewer(local_repo):
    root = local_repo
    branch(root, 'TASK-3', TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / (b)'))
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-3', '--refactor')
    assert 'TASK-3 DONE:' in output
    assert store.record(root, 'TASK-3')['status'] == 'DONE'
    assert store.entries(root, 'TASK-3')[-1].get('refactor') is True
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('TASK-3 is a declared refactor: tests unchanged, passing before and after') == 1
    # A later ordinary run must not inherit the declaration.
    ok(root, 'receipt', 'test', '--task', 'TASK-3')
    result = check(root)
    assert result.returncode == 1 and "its test passes on origin/main's code too" in result.stdout


@pytest.mark.parametrize('change', ['comment', 'new-test', 'config'])
def test_refactor_refuses_any_changed_test_material(local_repo, change):
    root = local_repo
    branch(root, 'TASK-3', TEST)
    (root / 'calc.py').write_text(CODE + '# rearranged\n')
    if change == 'config':
        (root / 'pytest.ini').write_text('[pytest]\naddopts = -q\n')
    else:
        (root / 'tests/test_div.py').write_text(TEST + (
            '# comment\n' if change == 'comment' else '\ndef test_more():\n    assert div(8, 2) == 4\n'))
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-3', '--refactor')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-3: its test files changed; use --needs-review' in result.stdout
    assert store.entries(root, 'TASK-3') == []


def test_refactor_requires_base_tests_to_pass(local_repo):
    root = local_repo
    branch(root, 'TASK-3', TEST, CODE.replace('a / b', 'a * b'))
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-3', '--refactor')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-3: a declared refactor needs its test to pass on origin/main' in result.stdout
    assert store.entries(root, 'TASK-3') == []


def test_refactor_requires_a_local_merge_base(local_repo):
    root = local_repo
    branch(root, 'TASK-3', TEST)
    git(root, 'update-ref', '-d', 'refs/remotes/origin/main')
    git(root, 'branch', '-D', 'main')
    (root / 'calc.py').write_text(CODE + '# refactor\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-3', '--refactor')
    assert result.returncode == 1
    assert 'a declared refactor needs a locally available target' in result.stdout


@pytest.mark.parametrize('changed_tests', [False, True])
def test_pending_review_writes_done_and_waits_for_reviewer(local_repo, monkeypatch, capsys, changed_tests):
    root = local_repo
    branch(root, 'TASK-6', TEST)
    if changed_tests:
        (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
        (root / 'tests/test_div.py').write_text(TEST.replace('== 2', '== 18'))
        commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-6', '--needs-review',
                *([] if changed_tests else ['--no-code-change']))
    expected = 'TASK-6 DONE, pending review: the pull request needs workorder-allow:TASK-6 or an approving review'
    assert expected in output
    assert store.record(root, 'TASK-6')['status'] == 'DONE'
    assert store.entries(root, 'TASK-6')[-1].get('pending_review') is True
    shown = ok(root, 'show', '--task', 'TASK-6')
    assert 'waiting for review' in shown
    assert 'run workorder done' not in shown
    git(root, 'add', 'tasks', '.workorder')
    staged = ok(root, 'check', '--staged')
    assert 'pending review' in staged
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert hooks.run(root, 'stop') == 0
    assert 'pending review' in capsys.readouterr().out
    result = check(root)
    assert result.returncode == 1 and 'pending review' in result.stdout
    for flags in [[], ['--approving-review']]:
        accepted = check(root, '--allow', 'TASK-6', *flags)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--squash', 'feature')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    ok(root, 'check', '--run-tests', '--all-tests')
    assert 'waiting for review' not in ok(root, 'show', '--task', 'TASK-6')


@pytest.mark.parametrize('body', ['assert True', 'assert 1', 'assert "x"', 'pass', 'return', '"docstring"'])
@pytest.mark.parametrize('action', ['done', 'check'])
def test_new_module_import_cannot_make_assertion_free_test_count(local_repo, body, action):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text('from calc import div\n\ndef test_div():\n    ' + body + '\n')
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2', '--needs-review')
    else:
        claim(root)
        result = check(root, '--allow', 'TASK-2,tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-2: test_div in tests/test_div.py asserts nothing that can fail' in result.stdout


def test_changed_python_test_is_checked_without_running_tests(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'tests/test_div.py').write_text(TEST.replace('assert div(6, 3) == 2', 'assert True'))
    result = cli(root, 'check', '--base', 'origin/main', '--allow', 'TASK-2,tests')
    assert result.returncode == 1, result.stdout
    assert 'test_div in tests/test_div.py asserts nothing that can fail' in result.stdout


@pytest.mark.parametrize('body', [
    'assert div(6, 3) == 2',
    'assert True\n    assert div(6, 3) == 2',
    'with pytest.raises(ZeroDivisionError):\n        div(1, 0)',
    'with raises(ZeroDivisionError):\n        div(1, 0)',
    'self = unittest.TestCase()\n    self.assertEqual(div(6, 3), 2)',
    'probe = Mock(return_value=div(6, 3))\n    probe()\n    probe.assert_called_once()',
])
def test_real_assertion_syntax_remains_accepted(local_repo, body):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(
        'from calc import div\nimport pytest, unittest\nfrom pytest import raises\n'
        'from unittest.mock import Mock\n\ndef test_div():\n    ' + body + '\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2')
    assert check(root).returncode == 0


@pytest.mark.parametrize('status', ['DONE', 'IN_PROGRESS'])
def test_new_task_with_red_before_green_needs_no_label(local_repo, status):
    root = local_repo
    branch(root, 'TASK-40', code=None, new=True)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(TEST)
    commit_all(root)
    claim(root, 'TASK-40')
    store.set_task_field(root, 'TASK-40', 'status', status)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('TASK-40 is new in this pull request; its test fails on origin/main and passes now') == 1
    assert 'accepts its definition' not in result.stdout


def test_new_task_without_red_still_needs_definition_review(local_repo):
    root = local_repo
    branch(root, 'TASK-40', TEST, new=True)
    (root / 'calc.py').write_text(CODE + '# comment\n')
    commit_all(root)
    claim(root, 'TASK-40')
    result = check(root)
    assert result.returncode == 1
    assert 'TASK-40 is new in this pull request: a reviewer accepts its definition' in result.stdout


@pytest.mark.parametrize('template', ['extended', 'simple'])
def test_new_code_paths_are_written_and_limit_scope(local_repo, template):
    root = local_repo
    result = cli(root, 'new', '--title', 'Divide', '--owner', 'you', '--template', template,
                 '--test', store.join_command([PYTHON, 'test_div.py']), '--code-paths', 'calc.py', 'src/math')
    assert result.returncode == 0, result.stdout + result.stderr
    assert store.record(root, 'TASK-1')['code_paths'] == ['calc.py', 'src/math']
    base = commit_all(root)
    (root / 'calc.py').write_text(CODE)
    (root / 'security.py').write_text('enabled = False\n')
    (root / 'test_div.py').write_text('assert 6 / 3 == 2\n')
    commit_all(root)
    ok(root, 'renew', '--task', 'TASK-1')
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 1 and 'security.py is outside the code paths of TASK-1' in result.stdout


def test_done_without_code_paths_names_only_changed_code(local_repo):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'security.py').write_text('enabled = False\n')
    (root / 'tests/test_div.py').write_text(TEST)
    (root / 'README.md').write_text('Divide numbers.\n')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-2')
    assert output.count('TASK-2 changed: calc.py, security.py; set code_paths to have changes outside them flagged') == 1


def test_refused_done_retry_writes_no_duplicate_notes_or_receipts(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    (root / 'tests/test_div.py').write_text(TEST.replace('== 2', '== 18'))
    commit_all(root)
    before = (root / 'tasks/TASK-2.md').read_bytes()
    for _ in range(2):
        result = cli(root, 'done', '--task', 'TASK-2', '--note', 'Changed the answer')
        assert result.returncode == 1
        assert (root / 'tasks/TASK-2.md').read_bytes() == before
        assert store.entries(root, 'TASK-2') == []
    ok(root, 'done', '--task', 'TASK-2', '--note', 'Changed the answer', '--needs-review')
    assert (root / 'tasks/TASK-2.md').read_text().count('Changed the answer') == 1
    assert [r['kind'] for r in store.entries(root, 'TASK-2')] == ['implemented', 'test']


def test_base_test_output_is_labeled_before_pending_success(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    (root / 'tests/test_div.py').write_text(TEST.replace('== 2', '== 18'))
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    assert 'Tests from origin/main on this code:' in output
    assert output.index('Tests from origin/main on this code:') < output.index('FAILED')
    assert '\nFailure:\n' not in output


def test_identical_current_and_base_failure_keeps_base_label(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    commit_all(root)
    store.add_note(root, 'TASK-2', 'Check the failing implementation')
    result = check(root)
    assert result.returncode == 1
    assert 'Tests from origin/main on this code:' in result.stdout
    assert result.stdout.count('short test summary info') == 1


def test_zero_pass_advice_requests_a_passing_test(local_repo):
    root = local_repo
    branch(root, test_text='import pytest\n@pytest.mark.skip\ndef test_div():\n    assert False\n')
    result = cli(root, 'done', '--task', 'TASK-2', '--no-code-change')
    assert result.returncode == 1
    assert 'the test must report at least one passing test' in result.stdout
    assert 'waits_on' not in result.stdout


def test_duplicate_note_does_not_count_one_task_twice():
    line = 'TASK-2: its test passed when this check ran it'
    assert gate.summarize_notes([line, line]) == [line]


def test_fresh_clone_init_reports_installation_without_repeat_advice(local_repo, tmp_path):
    ok(local_repo, 'init')
    commit_all(local_repo)
    clone = tmp_path / 'fresh'
    git(tmp_path, 'clone', '-q', str(local_repo), str(clone))
    output = ok(clone, 'init')
    assert '.git/hooks/pre-commit: installed' in output
    assert 'run workorder init' not in output
    assert (clone / '.git/hooks/pre-commit').exists()


@pytest.mark.parametrize('action', ['done', 'check'])
def test_assertion_free_test_cannot_hide_behind_pathless_command(local_repo, action):
    root = local_repo
    branch(root, code=None)
    definition = root / 'tasks/TASK-2.md'
    definition.write_text(definition.read_text().replace(' -q tests/test_div.py', ' -q'))
    # Put the command change on the base to isolate assertion checking.
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text('from calc import div\ndef test_div():\n    assert True\n')
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2', '--needs-review')
    else:
        claim(root)
        result = check(root, '--allow', 'TASK-2,tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'test_div in tests/test_div.py asserts nothing that can fail' in result.stdout


def test_unowned_assertion_free_test_is_refused(local_repo):
    root = local_repo
    base = store.head(root)
    (root / 'test_new.py').write_text('def test_new():\n    assert True\n')
    result = cli(root, 'check', '--base', base, '--allow', 'tests')
    assert result.returncode == 1
    assert 'test_new in test_new.py asserts nothing that can fail' in result.stdout


def test_refactor_cannot_keep_declaration_after_test_edit(local_repo):
    root = local_repo
    branch(root, 'TASK-3', TEST)
    (root / 'calc.py').write_text(CODE + '# refactor\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-3', '--refactor')
    (root / 'tests/test_div.py').write_text(TEST + '# edited later\n')
    result = check(root, '--allow', 'TASK-3,tests')
    assert result.returncode == 1
    assert 'TASK-3: its test files changed; use --needs-review' in result.stdout


def test_refactor_current_failure_cannot_write_done(local_repo):
    root = local_repo
    branch(root, 'TASK-3', TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-3', '--refactor')
    assert result.returncode == 1 and 'fix the code or the test' in result.stdout
    assert store.record(root, 'TASK-3')['status'] == 'IN_PROGRESS'
    assert store.entries(root, 'TASK-3') == []


def test_refactor_base_zero_pass_does_not_count(local_repo):
    root = local_repo
    branch(root, 'TASK-3', TEST, CODE.replace('a / b', '0'))
    definition = root / 'tasks/TASK-3.md'
    definition.write_text(definition.read_text().replace(' -m pytest -q tests/test_div.py', ' tests/test_div.py'))
    (root / 'tests/test_div.py').write_text(
        'import sys\nsys.path.insert(0, ".")\nfrom calc import div\n'
        'print("1 passed in 0.1s" if div(6, 3) == 2 else "1 skipped in 0.1s")\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-3', '--refactor')
    assert result.returncode == 1
    assert 'a declared refactor needs its test to pass on origin/main' in result.stdout


@pytest.mark.parametrize('body', ['assert True\n    assert 1', 'def helper():\n        assert False\n    return'])
def test_only_constant_or_nested_assertions_do_not_count(local_repo, body):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text('from calc import div\ndef test_div():\n    ' + body + '\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2')
    assert result.returncode == 1
    assert 'test_div in tests/test_div.py asserts nothing that can fail' in result.stdout


@pytest.mark.parametrize('action', ['done', 'check'])
def test_pathless_refactor_refuses_changed_collected_tests(local_repo, action):
    root = local_repo
    branch(root, 'TASK-3', TEST)
    definition = root / 'tasks/TASK-3.md'
    definition.write_text(definition.read_text().replace(' -q tests/test_div.py', ' -q'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE + '# refactor\n')
    commit_all(root)
    if action == 'check':
        ok(root, 'done', '--task', 'TASK-3', '--refactor')
    (root / 'tests/test_div.py').write_text(TEST + '# test edited\n')
    commit_all(root)
    result = (cli(root, 'done', '--task', 'TASK-3', '--refactor') if action == 'done'
              else check(root, '--allow', 'TASK-3,tests'))
    assert result.returncode == 1
    assert 'TASK-3: its test files changed; use --needs-review' in result.stdout


@pytest.mark.parametrize('rerun', ['renew', 'receipt', 'done'])
def test_pending_review_survives_later_evidence(local_repo, rerun):
    root = local_repo
    branch(root, 'TASK-6', TEST, CODE.replace('a / b', 'a * b'))
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-6', '--needs-review')
    commit_all(root)
    args = ['receipt', 'test'] if rerun == 'receipt' else [rerun]
    ok(root, *args, '--task', 'TASK-6')
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-6: pending review' in result.stdout
    assert 'waiting for review' in ok(root, 'show', '--task', 'TASK-6')
    assert check(root, '--allow', 'TASK-6').returncode == 0


@pytest.mark.parametrize('action', ['done', 'check'])
def test_conditional_test_definition_cannot_supply_empty_evidence(local_repo, action):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(
        'from calc import div\nif True:\n    def test_div():\n        assert True\n')
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2')
    else:
        claim(root)
        result = check(root)
    assert result.returncode == 1
    assert 'test_div in tests/test_div.py asserts nothing that can fail' in result.stdout
