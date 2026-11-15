"""Round-17b repairs and review boundaries."""
import json
import os
import subprocess

import pytest

from workorder import gate, store, review_labels
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import CODE, TEST, branch, claim, check


@pytest.mark.parametrize('call', ['pytest.warns(UserWarning)', 'pytest.deprecated_call()',
                                  'pytest.raises(ValueError)', 'self.assertWarns(UserWarning)',
                                  'self.assertRaises(ValueError)', 'self.assertLogs()'])
def test_assertion_context_managers_count(local_repo, call):
    root = local_repo
    base = store.head(root)
    (root / 'test_context.py').write_text('import pytest\ndef test_context(self):\n    with ' + call + ':\n        action()\n')
    assert gate.assertion_problems(root, base, {'id': 'TASK-1', 'test': 'pytest test_context.py'}) == []


@pytest.mark.parametrize('imports,call', [('import pytest', 'pytest.fail'),
                                        ('import pytest as pt', 'pt.warns'),
                                        ('from pytest import deprecated_call as dc', 'dc')])
def test_pytest_assertion_aliases_count(local_repo, imports, call):
    base = store.head(local_repo)
    (local_repo / 'test_context.py').write_text(imports + '\ndef test_context():\n    ' + call + '()\n')
    assert gate.assertion_problems(local_repo, base, {'id': 'TASK-1', 'test': 'pytest test_context.py'}) == []


@pytest.mark.parametrize('edit', ['comment', 'add', 'class'])
def test_only_changed_test_functions_need_assertions(local_repo, edit):
    root = local_repo
    text = 'def test_smoke():\n    print("smoke")\n'
    if edit == 'class':
        text = 'class TestOld:\n    def test_smoke(self):\n        print("smoke")\n'
    path = root / 'test_smoke.py'
    path.write_text(text)
    base = commit_all(root)
    path.write_text(text + ('# explanation\n' if edit == 'comment' else
                           '\ndef test_new():\n    assert len([1]) == 1\n'))
    assert gate.assertion_problems(root, base, {'id': 'TASK-1', 'test': 'pytest test_smoke.py'}) == []


def test_smoke_test_is_explicitly_label_accepted(local_repo):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text('from calc import div\ndef test_div():\n    div(6, 3)\n')
    commit_all(root)
    claim(root)
    assert check(root).returncode == 1
    assert check(root, '--allow', 'TASK-2').returncode == 0


@pytest.mark.parametrize('command', ['pytest tests/test_missing.py', 'python tests/test_missing.py'])
def test_new_warns_for_missing_named_test(local_repo, command):
    result = cli(local_repo, 'new', '--title', 'test', '--owner', 'dev', '--test', command)
    assert result.returncode == 0
    assert 'Warning:' in result.stdout and 'tests/test_missing.py does not exist' in result.stdout


def test_new_accepts_test_created_in_same_change(local_repo):
    (local_repo / 'test_added.py').write_text('assert len([1]) == 1\n')
    result = cli(local_repo, 'new', '--title', 'test', '--owner', 'dev', '--test', 'python test_added.py')
    assert result.returncode == 0 and 'does not exist' not in result.stdout


def test_unfinished_command_can_be_repaired_before_receipts(local_repo):
    root = local_repo
    (root / 'test_ok.py').write_text('assert len([1]) == 1\n')
    fill_sections(task(root, ships='none', test='python test_typo.py'))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace(json.dumps('python test_typo.py'), json.dumps(store.join_command([PYTHON, 'test_ok.py']))))
    git(root, 'add', '.')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'workorder-allow:TASK-1' not in result.stdout


@pytest.mark.parametrize('explicit', [True, False])
def test_renew_preserves_refactor_after_rebase(local_repo, explicit):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / b'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--refactor')
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    (root / 'other.py').write_text('value = 2\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'checkout', '-q', 'feature')
    git(root, 'rebase', 'main')
    result = cli(root, 'renew', '--task', 'TASK-2', *(['--refactor'] if explicit else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert store.entries(root, 'TASK-2')[-1].get('refactor') is True
    assert check(root).returncode == 0


def test_test_label_cannot_accept_scope(local_repo):
    root = local_repo
    branch(root, code=None)
    path = root / 'tasks/TASK-2.md'
    path.write_text(path.read_text().replace('contract:', 'code_paths: ["calc.py"]\ncontract:'))
    (root / 'calc.py').write_text(CODE)
    (root / 'termui.py').write_text('unexpected = 1\n')
    (root / 'tests/test_div.py').write_text(TEST)
    commit_all(root)
    claim(root)
    result = check(root, '--allow', 'TASK-2')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'workorder-allow:scope:TASK-2' in result.stdout
    assert check(root, '--allow', 'TASK-2,scope:TASK-2').returncode == 0
    assert check(root, '--approving-review').returncode == 0


def test_scope_label_parser_keeps_reviewer_boundary():
    label = 'workorder-allow:scope:TASK-4'
    pr = {'user': {'login': 'author'}, 'labels': [{'name': label}]}
    event = {'event': 'labeled', 'label': {'name': label}, 'actor': {'login': 'reviewer'}}
    assert review_labels.accepted_labels(pr, [[event]])[0] == ['scope:TASK-4']
    event['actor']['login'] = 'author'
    assert review_labels.accepted_labels(pr, [[event]])[0] == []


@pytest.mark.parametrize('group', ['test', 'tests', 'dev'])
def test_init_installs_pep735_group(local_repo, group):
    (local_repo / 'pyproject.toml').write_text(f'[dependency-groups]\n{group} = ["pytest"]\n')
    result = cli(local_repo, 'init')
    assert result.returncode == 0, result.stdout + result.stderr
    workflow = (local_repo / '.github/workflows/workorder.yml').read_text()
    assert f'pip install --group {group} .' in workflow
    assert 'pip>=25.1' in workflow
    assert 'no project test dependencies detected' not in result.stdout.lower()


def test_merge_target_test_changes_are_not_refactor_edits(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / b'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--refactor')
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    (root / 'tests/test_div.py').write_text(TEST + '\ndef test_more():\n    assert div(8, 2) == 4\n')
    target = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', target)
    git(root, 'checkout', '-q', 'feature')
    git(root, 'merge', '--no-commit', '--no-ff', 'main')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 0, result.stdout + result.stderr
    assert gate.comparison_base(root, 'origin/main')[0] == target
    # A branch-owned test edit during the merge still refuses the refactor.
    (root / 'tests/test_div.py').write_text(TEST + '# branch edit\n')
    git(root, 'add', 'tests/test_div.py')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 1 and 'its test files changed' in result.stdout


def test_done_reports_other_findings_once_in_summary(local_repo):
    root = local_repo
    branch(root, code=None)
    (root / 'tests/test_other.py').write_text('def test_other():\n    assert len([1]) == 1\n')
    fill_sections(task(root, 'TASK-3', ships='none', test=store.join_command([PYTHON, '-m', 'pytest', '-q', 'tests/test_other.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(TEST)
    (root / 'tests/test_other.py').write_text('def test_other():\n    assert True\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2')
    assert result.returncode == 1
    missing = [line for line in result.stdout.splitlines() if 'Missing:' in line]
    assert not any('TASK-3' in line for line in missing), result.stdout
    assert result.stdout.count('other tasks have findings') == 1, result.stdout
    assert result.stdout.count('Next:') == 1, result.stdout


def test_needs_review_refusal_does_not_advise_same_flag(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / b'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--refactor')
    commit_all(root)
    (root / 'tests/test_div.py').write_text(TEST + '# clarification\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2', '--needs-review')
    assert 'use --needs-review' not in result.stdout
    assert result.returncode == 0, result.stdout + result.stderr


def test_failure_output_is_printed_once(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / b'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--refactor')
    commit_all(root)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    commit_all(root)
    result = check(root)
    assert result.returncode == 1
    assert result.stdout.count('E       assert 18 == 2') == 1, result.stdout
    failures = [line for line in result.stdout.splitlines() if 'test fails on this code' in line or 'its test failed when' in line]
    assert len(failures) == 1, result.stdout


def test_debt_never_displays_closed_evidence(local_repo):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(TEST)
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
    ok(root, 'receipt', 'test', '--task', 'TASK-2')
    commit_all(root)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    commit_all(root)
    store.set_task_field(root, 'TASK-2', 'status', 'DONE')
    result = cli(root, 'check', 'TASK-2')
    assert result.returncode == 1
    assert 'NOT DONE' in result.stdout
    assert 'Evidence: CLOSED' not in result.stdout


def test_shared_assertion_finding_is_reported_once(local_repo):
    root = local_repo
    for tid in ('TASK-1', 'TASK-2'):
        fill_sections(task(root, tid, ships='none', test='pytest test_shared.py'))
    base = commit_all(root)
    (root / 'test_shared.py').write_text('def test_shared():\n    assert True\n')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1
    assert result.stdout.count('test_shared in test_shared.py asserts nothing that can fail') == 1, result.stdout
