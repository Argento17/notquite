"""Second-pass round-19 checks, using public commands in local repositories."""
import json
import sys
import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v15d import hooked_commit
from test_v16 import HONEST, OLD_TEST, define, shared_file, check


@pytest.mark.parametrize('inspect_next', [False, True], ids=['pull-request', 'done-next'])
def test_new_task_finished_in_same_pull_request_needs_acceptance(local_repo, inspect_next):
    root = local_repo
    shared_file(root, HONEST, task_first=False)
    output = ok(root, 'done', '--task', 'TASK-1')
    if inspect_next:
        assert 'Next:' in output and 'workorder-allow:TASK-1' in output
    commit_all(root)
    refused = check(root)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert ('TASK-1 was written and finished in this pull request; an approving review clears this '
            '(the reviewer reads its definition; a second pull request is not needed), or the label workorder-allow:TASK-1') in refused.stdout
    for flags in [('--allow', 'TASK-1'), ('--approving-review',)]:
        accepted = check(root, *flags)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_landed_task_and_unfinished_new_task_need_no_acceptance(local_repo):
    root = local_repo
    shared_file(root, HONEST)
    ok(root, 'done', '--task', 'TASK-1')
    define(root, 'TASK-2', code='future.py')
    commit_all(root)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'workorder-allow:' not in result.stdout


@pytest.mark.parametrize('path', ['NOTES.md', 'notes.rst', 'notes.txt', 'docs/example.py', 'doc/example.py'])
def test_documentation_after_done_never_stales_evidence(local_repo, path):
    root = local_repo
    shared_file(root, HONEST)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    document = root / path
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text('Notes about the change.\n')
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'renew' not in result.stdout


def test_implementation_receipt_excludes_documentation(local_repo):
    root = local_repo
    shared_file(root, HONEST)
    (root / 'NOTES.md').write_text('Notes.\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    implementation = gate.newest_implementation(store.entries(root, 'TASK-1'))
    assert 'calc.py' in implementation['implementation_paths']
    assert 'NOTES.md' not in implementation['implementation_paths']


@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.parametrize('path,declared,protected', [
    ('NOTES.md', False, False),
    ('docs/notes.txt', False, False),
    ('tests/data.txt', False, True),
    ('checks/verify.txt', True, True),
    ('docs/check.txt', True, True),
])
def test_documentation_exclusion_preserves_test_material(local_repo, path, declared, protected, staged):
    root = local_repo
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('assert int(1) == 1\n')
    if declared:
        definition = task(root, ships='none', test=store.join_command([PYTHON, path]))
        definition.write_text(definition.read_text().replace(
            '---\n\n', f'test_paths: {json.dumps([path])}\n---\n\n'))
    base = commit_all(root)
    target.write_text('assert int(2) == 2\n')
    if staged:
        git(root, 'add', path)
    result = cli(root, 'check', '--base', base, '--require-task', '--review', 'refuse',
                 *(['--staged'] if staged else []))
    assert result.returncode == int(protected), result.stdout + result.stderr
    assert 'no task records it' not in result.stdout
    if protected:
        assert path in result.stdout
        assert ('workorder-allow:TASK-1' if declared else 'workorder-allow:tests') in result.stdout
    else:
        assert 'workorder-allow:' not in result.stdout


@pytest.mark.parametrize('body_change', [False, True])
def test_docstrings_alone_do_not_need_a_task(local_repo, body_change):
    root = local_repo
    old = ('"""Module typo."""\nclass Calc:\n    """Class typo."""\n'
           '    def add(self, a, b):\n        """Add typo."""\n        return a + b\n')
    (root / 'calc.py').write_text(old)
    commit_all(root)
    git(root, 'switch', '-c', 'docs')
    new = old.replace('typo', 'corrected')
    if body_change:
        new = new.replace('a + b', 'a - b')
    (root / 'calc.py').write_text(new)
    commit_all(root)
    result = cli(root, 'check', '--base', 'main', '--require-task')
    assert result.returncode == int(body_change), result.stdout + result.stderr
    assert ('no task records it' in result.stdout) == body_change


def test_docstring_after_done_never_stales_evidence(local_repo):
    root = local_repo
    shared_file(root, HONEST)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    path = root / 'calc.py'
    path.write_text('"""Boolean conversion."""\n' + path.read_text())
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'renew' not in result.stdout


@pytest.mark.parametrize('staged', [False, True])
def test_uncommitted_docstring_keeps_finished_claim(local_repo, staged):
    root = local_repo
    shared_file(root, HONEST)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    path = root / 'calc.py'
    path.write_text('"""Boolean conversion."""\n' + path.read_text())
    if staged:
        git(root, 'add', 'calc.py')
    output = ok(root, 'check', 'TASK-1')
    assert 'uncommitted code changes' not in output


@pytest.mark.parametrize('staged_passes', [False, True])
def test_docstring_staging_preserves_stop_snapshot(local_repo, staged_passes):
    from test_v15d import stop
    from test_v15g import two_tasks as landed_tasks
    root = local_repo
    landed_tasks(root)
    path = root / 'calc.py'
    good = '"""Arithmetic helpers."""\n' + path.read_text()
    bad = good.replace('a + b', 'a - b')
    path.write_text(good if staged_passes else bad)
    git(root, 'add', 'calc.py')
    path.write_text(bad if staged_passes else good)
    result = stop(root)
    assert result.returncode == (0 if staged_passes else 2), result.stdout + result.stderr


def test_docstring_after_done_survives_fresh_squash_clone(local_repo):
    root = local_repo
    shared_file(root, HONEST)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    path = root / 'calc.py'
    path.write_text('"""Boolean conversion."""\n' + path.read_text())
    commit_all(root)
    git(root, 'switch', 'main')
    git(root, 'merge', '--squash', 'feature')
    commit_all(root)
    fresh = root.parent / 'fresh'
    git(root.parent, 'clone', '--single-branch', '--branch', 'main', '--no-local', str(root), str(fresh))
    ok(fresh, 'check', '--run-tests', '--all-tests')


def test_semantic_receipt_map_is_checked_against_original_commit(local_repo):
    root = local_repo
    shared_file(root, HONEST)
    ok(root, 'done', '--task', 'TASK-1')
    receipts = [json.loads(path.read_text()) for path in (root / '.workorder/receipts/TASK-1').glob('*.json')]
    receipt = next(value for value in receipts if value['kind'] == 'implemented')
    receipt['implementation_syntax'] = {'calc.py': '100644 blob ' + '0' * 64}
    receipt.pop('hash')
    receipt['hash'] = store.digest(store.packed(receipt).encode())
    with pytest.raises(store.Refused, match='implementation syntax'):
        store.verify_receipt(root, 'TASK-1', receipt)


def test_test_path_change_does_not_invalidate_historical_syntax_map(local_repo):
    root = local_repo
    (root / 'docs').mkdir()
    probe = root / 'docs/check.py'
    probe.write_text('def value():\n    return 1\nassert value() == 1\n')
    definition = task(root, ships='none', test=store.join_command([PYTHON, 'docs/check.py']))
    definition.write_text(definition.read_text().replace(
        '---\n\n', 'test_paths: ["docs/check.py"]\n---\n\n'))
    commit_all(root)
    git(root, 'switch', '-c', 'feature')
    probe.write_text(probe.read_text().replace('return 1', 'return 0 + 1'))
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    original = store.entries(root, 'TASK-1')[0]
    assert 'docs/check.py' in original['implementation_syntax']
    definition.write_text(definition.read_text().replace('docs/check.py', 'other.py'))
    commit_all(root)
    assert store.entries(root, 'TASK-1')[0]['hash'] == original['hash']
    ok(root, 'show', '--task', 'TASK-1')


def test_protected_doctest_edit_stales_implementation_evidence(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n')
    (root / 'docs').mkdir()
    probe = root / 'docs/check.py'
    probe.write_text('"""\n>>> from calc import value\n>>> value()\n2\n"""\n')
    (root / 'calc.py').write_text('def value():\n    return 0\n')
    definition = task(root, ships='none', test=store.join_command([PYTHON, '-m', 'doctest', 'docs/check.py']))
    definition.write_text(definition.read_text().replace(
        '---\n\n', 'test_paths: ["docs/check.py"]\ncode_paths: ["calc.py"]\n---\n\n'))
    commit_all(root)
    git(root, 'switch', '-c', 'feature')
    (root / 'calc.py').write_text('def value():\n    return 2\n')
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(root, 'receipt', 'test', '--task', 'TASK-1')
    commit_all(root)
    probe.write_text(probe.read_text().replace('\n2\n', '\n3\n'))
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root)
    result = cli(root, 'check')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'was marked DONE' in result.stdout and 'renew' in result.stdout


def test_historical_test_classification_does_not_invent_code_changes(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n')
    (root / 'calc.py').write_text('def value():\n    return 0\n')
    (root / 'test_calc.py').write_text('from calc import value\nassert value() == 1\n')
    task(root, ships='none', test=store.join_command([PYTHON, 'test_calc.py']))
    (root / 'docs').mkdir()
    (root / 'docs/check.py').write_text('assert int(1) == 1\n')
    definition = task(root, 'TASK-2', ships='none', test=store.join_command([PYTHON, 'docs/check.py']))
    definition.write_text(definition.read_text().replace(
        '---\n\n', 'test_paths: ["docs/check.py"]\n---\n\n'))
    commit_all(root)
    git(root, 'switch', '-c', 'feature')
    (root / 'calc.py').write_text('def value():\n    return 1\n')
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(root, 'receipt', 'test', '--task', 'TASK-1')
    commit_all(root)
    definition.write_text(definition.read_text().replace('docs/check.py', 'other.py'))
    (root / 'NOTES.md').write_text('Test path updated.\n')
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root)
    ok(root, 'check')


def test_shared_test_origin_names_only_new_tests(local_repo):
    root = local_repo
    shared_file(root, HONEST)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert "TASK-1's new tests were written in this change (test_enable_word)" in output
    assert "TASK-1's test was written in this change" not in output


@pytest.mark.parametrize('marker,refused', [
    ('skipif(sys.platform == "win32", reason="Windows")', sys.platform == 'win32'),
    ('xfail(sys.platform == "win32", reason="Windows")', sys.platform == 'win32'),
    ('skip(reason="later")', True),
    ('skipif(True, reason="later")', True),
    ('xfail(True, reason="later")', True),
])
def test_only_executed_skip_or_xfail_counts(local_repo, marker, refused):
    root = local_repo
    added = '\nimport sys\nimport pytest\n\n@pytest.mark.' + marker + HONEST
    if marker.startswith('xfail(True'):
        added = added.replace('convert("enable") is True', 'convert("enable") is False')
    shared_file(root, added)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == int(refused), result.stdout + result.stderr
    if not refused:
        commit_all(root)
        result = check(root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'new skip or xfail' not in result.stdout


def test_unchanged_main_is_not_reported_as_moved(local_repo):
    root = local_repo
    shared_file(root, HONEST)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    path = root / 'calc.py'
    path.write_text(path.read_text() + '\ndef another():\n    return 3\n')
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 1
    assert 'main moved since' not in result.stdout


def test_existing_unrelated_skip_does_not_activate_new_false_marker(local_repo):
    root = local_repo
    old = OLD_TEST + '\nimport pytest\n@pytest.mark.skip(reason="old")\ndef test_old_skip():\n    assert False\n'
    added = '\n@pytest.mark.skipif(False, reason="other platform")' + HONEST
    shared_file(root, added, old_test=old)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert 'new skip or xfail' not in output
    commit_all(root)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr


ADD = 'def add(a, b):\n    return a + b\n'
SUB = '\ndef sub(a, b):\n    return a - b\n'


def two_tasks(root, *, landed=False):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    for tid, name in [('TASK-1', 'add'), ('TASK-2', 'sub')]:
        define(root, tid, f'python -m pytest -q tests/test_{name}.py')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    (root / 'calc.py').write_text(ADD)
    (root / 'tests/test_add.py').write_text('from calc import add\ndef test_add():\n    assert add(2, 2) == 4\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    if landed:
        git(root, 'switch', 'main')
        git(root, 'merge', '--ff-only', 'feature')
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        git(root, 'switch', '-c', 'second')
    (root / 'calc.py').write_text(ADD + SUB)
    (root / 'tests/test_sub.py').write_text('from calc import sub\ndef test_sub():\n    assert sub(5, 2) == 3\n')


def test_second_task_commit_and_done_explain_renew_before_squash(local_repo):
    root = local_repo
    two_tasks(root)
    expected = 'TASK-1: its code changed after it was finished (calc.py); run workorder renew --task TASK-1'
    git(root, 'add', '-A')
    hook = hooked_commit(root)
    assert hook.returncode == 0, hook.stdout + hook.stderr
    assert 'Note: ' + expected in hook.stdout + hook.stderr
    output = ok(root, 'done', '--task', 'TASK-2')
    assert 'Note: ' + expected in output
    ok(root, 'renew', '--task', 'TASK-1')
    commit_all(root)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    git(root, 'switch', 'main')
    git(root, 'merge', '--squash', 'feature')
    commit_all(root)
    fresh = root.parent / 'fresh'
    git(root.parent, 'clone', '--single-branch', '--branch', 'main', '--no-local', str(root), str(fresh))
    output = ok(fresh, 'check', '--run-tests', '--all-tests')
    assert 'is not in this clone' not in output


@pytest.mark.parametrize('break_old', [False, True])
def test_renew_landed_task_does_not_prove_another_tasks_implementation(local_repo, break_old):
    root = local_repo
    two_tasks(root, landed=True)
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2')
    if break_old:
        (root / 'calc.py').write_text(ADD.replace('a + b', 'a - b') + SUB)
        commit_all(root)
    renewed = cli(root, 'renew', '--task', 'TASK-1')
    assert renewed.returncode == int(break_old), renewed.stdout + renewed.stderr
    commit_all(root)
    result = check(root)
    assert result.returncode == int(break_old), result.stdout + result.stderr
    if not break_old:
        assert 'stubbed out' not in result.stdout and 'workorder-allow:' not in result.stdout
