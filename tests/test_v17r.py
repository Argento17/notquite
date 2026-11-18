"""Round-19 reviewer reproductions in disposable, offline repositories."""
import json

import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import claim


CODE = 'def value():\n    return 1\n'
TEST = 'from calc import value\n\ndef test_value():\n    assert value() == 1\n'


def define(root, tid='TASK-1', code_paths=('calc.py',), kind=None, test_paths=None):
    path = task(root, tid, ships='none', test='python -m pytest -q tests/test_value.py')
    fill_sections(path)
    for key, value in [('code_paths', code_paths), ('kind', kind), ('test_paths', test_paths)]:
        if value is not None:
            path.write_text(path.read_text().replace('\ncontract:', f'\n{key}: {json.dumps(value)}\ncontract:'))
    return path


def setup(root, *, kind=None, scoped=True):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_value.py').write_text(TEST)
    define(root, kind=kind, code_paths=('calc.py',) if scoped else None)


def branch(root):
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')


def check(root, *flags):
    return cli(root, 'check', '--base', 'origin/main', '--run-tests', *flags)


def package(root, *, namespace=False):
    (root / 'src/demo').mkdir(parents=True)
    if not namespace:
        (root / 'src/demo/__init__.py').write_text('')
    (root / 'src/demo/testing.py').write_text(CODE)
    (root / 'pyproject.toml').write_text(
        '[project]\nname = "demo"\nversion = "1.0"\n'
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n')


@pytest.mark.parametrize('scoped', [False, True])
@pytest.mark.parametrize('namespace', [False, True])
def test_testing_module_is_code_in_done_and_pr(local_repo, scoped, namespace):
    root = local_repo
    setup(root)
    package(root, namespace=namespace)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('calc.py', 'src/demo/testing.py'))
    if not scoped:
        path.write_text(path.read_text().replace('code_paths: ["src/demo/testing.py"]\n', ''))
    (root / 'tests/test_value.py').write_text(TEST.replace('from calc', 'from demo.testing'))
    branch(root)
    (root / 'src/demo/testing.py').write_text(CODE + '\ndef new_value():\n    return 2\n')
    with (root / 'tests/test_value.py').open('a') as stream:
        stream.write('\ndef test_new_value():\n    from demo.testing import new_value\n    assert new_value() == 2\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('flags', [[], ['--review', 'warn']])
def test_unowned_package_testing_module_is_not_collected(local_repo, flags):
    root = local_repo
    package(root)
    branch(root)
    (root / 'src/demo/testing.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    result = check(root, *flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'could not compare' not in result.stdout


@pytest.mark.parametrize('path', ['testing.py', 'testing/helper.py', 'spec/value.py', 'expected.txt'])
@pytest.mark.parametrize('explicit_test', [False, True])
def test_declared_code_beats_names_but_explicit_tests_win(local_repo, path, explicit_test):
    root = local_repo
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CODE)
    define(root, code_paths=(path,), test_paths=(path,) if explicit_test else None)
    branch(root)
    record = store.record(root, 'TASK-1')
    assert (path in gate.test_material(root, 'origin/main')) is explicit_test
    assert (path in gate.strict_test_files(root, record, 'origin/main')) is explicit_test


@pytest.mark.parametrize('key,custom,added,removed', [
    ('test_file_patterns', ['verify_*.py'], 'verify_value.py', 'testing.py'),
    ('test_directory_patterns', ['checks'], 'checks/helper.py', 'fixtures/helper.py'),
])
def test_configurable_test_names_and_folders(local_repo, key, custom, added, removed):
    root = local_repo
    for path in (added, removed):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(CODE)
    (root / '.workorder').mkdir(exist_ok=True)
    (root / '.workorder/config.json').write_text(json.dumps({key: custom}))
    branch(root)
    material = gate.test_material(root, 'origin/main')
    assert added in material
    assert removed not in material


@pytest.mark.parametrize('key', ['test_file_patterns', 'test_directory_patterns'])
@pytest.mark.parametrize('value', ['tests', [1], ['']])
def test_invalid_classification_config_refuses(local_repo, key, value):
    root = local_repo
    (root / '.workorder').mkdir(exist_ok=True)
    (root / '.workorder/config.json').write_text(json.dumps({key: value}))
    with pytest.raises(store.Refused, match=key):
        gate.test_material(root, 'HEAD')


@pytest.mark.parametrize('usage_error', [False, True])
def test_unowned_comparison_failure_has_meaning_and_explicit_label(local_repo, usage_error):
    root = local_repo
    (root / 'testing.py').write_text(CODE)
    if usage_error:
        (root / 'pytest.ini').write_text('[pytest]\naddopts = --workorder-nonexistent-option\n')
    branch(root)
    (root / 'testing.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    result = check(root, '--review', 'warn')
    assert result.returncode == 1, result.stdout + result.stderr
    assert ('usage error' if usage_error else 'no tests were collected') in result.stdout
    assert 'workorder-allow:tests' in result.stdout
    # 0.18 one review rule: an approving review clears the unavailable comparison.
    result = check(root, '--approving-review', '--review', 'warn')
    assert result.returncode == 0, result.stdout + result.stderr
    result = check(root, '--allow', 'tests')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('completion', ['refactor', 'no-code-change', 'receipts'])
@pytest.mark.parametrize('approval', [False, True])
def test_late_exemptions_need_task_label(local_repo, completion, approval):
    root = local_repo
    setup(root)
    branch(root)
    if completion == 'refactor':
        (root / 'calc.py').write_text(CODE.replace('return 1', 'return 1 + 0'))
        commit_all(root)
    if completion == 'receipts':
        claim(root, 'TASK-1')
    else:
        ok(root, 'done', '--task', 'TASK-1', '--' + completion)
    commit_all(root)
    result = check(root, '--review', 'warn', *(['--approving-review'] if approval else []))
    # 0.18 one review rule: an approving review clears a late exemption.
    assert result.returncode == int(not approval), result.stdout + result.stderr
    if not approval:
        assert 'completion needs review' in result.stdout
        assert 'workorder-allow:TASK-1' in result.stdout
    result = check(root, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('completion,kind', [('refactor', 'refactor'), ('no-code-change', 'verification'),
                                            ('receipts', 'verification')])
@pytest.mark.parametrize('early', [False, True])
def test_only_base_kind_preapproves_exemption(local_repo, completion, kind, early):
    root = local_repo
    setup(root, kind=kind if early else None)
    branch(root)
    if not early:
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('\ncontract:', f'\nkind: {kind}\ncontract:'))
    if completion == 'refactor':
        (root / 'calc.py').write_text(CODE.replace('return 1', 'return 1 + 0'))
    if not early or completion == 'refactor':
        commit_all(root)
    if completion == 'receipts':
        claim(root, 'TASK-1')
    else:
        ok(root, 'done', '--task', 'TASK-1', '--' + completion)
    commit_all(root)
    result = check(root)
    assert result.returncode == (0 if early else 1), result.stdout + result.stderr
    if not early:
        assert 'completion needs review' in result.stdout
    result = check(root, '--approving-review')  # 0.18: approval clears it either way
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['bugfix', '', ['refactor']])
def test_invalid_task_kind_refuses(local_repo, kind):
    define(local_repo, kind=kind)
    with pytest.raises(store.Refused, match='kind must be feature, fix, refactor or verification'):
        store.record(local_repo, 'TASK-1')


@pytest.mark.parametrize('require_task', [False, True])
@pytest.mark.parametrize('other_receipt', [False, True])
def test_outside_scope_needs_scope_label(local_repo, require_task, other_receipt):
    root = local_repo
    setup(root)
    define(root, tid='TASK-4', code_paths=('calc.py',))
    (root / 'outside.py').write_text(CODE)
    branch(root)
    (root / 'outside.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-4')
    if other_receipt:
        # An unrelated unscoped receipt must not turn scope enforcement off.
        store.append(root, 'TASK-1', dict(kind='merged', commit=store.head(root),
                                        code=store.code_digest(root, 'HEAD'), ref='refs/heads/main'))
    commit_all(root)
    flags = ['--require-task'] if require_task else []
    result = check(root, *flags, '--review', 'warn')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'outside.py is outside the code paths of TASK-4' in result.stdout
    assert 'workorder-allow:scope:TASK-4' in result.stdout
    # 0.18 one review rule: an approving review clears a scope finding.
    result = check(root, *flags, '--approving-review', '--review', 'warn')
    assert result.returncode == 0, result.stdout + result.stderr
    assert check(root, *flags, '--allow', 'TASK-4').returncode == 1
    result = check(root, *flags, '--allow', 'scope:TASK-4' + (',TASK-4' if require_task else ''))  # round 22: the hand receipts of an unfinished task also need its label
    assert result.returncode == 0, result.stdout + result.stderr


def test_multifile_scope_union_covers_implementation(local_repo):
    root = local_repo
    setup(root)
    (root / 'other.py').write_text(CODE)
    define(root, tid='TASK-4', code_paths=('other.py',))
    branch(root)
    for name in ('calc.py', 'other.py'):
        (root / name).write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    for tid in ('TASK-1', 'TASK-4'):
        ok(root, 'receipt', 'implemented', '--task', tid)
    commit_all(root)
    result = cli(root, 'check', '--base', 'origin/main', '--require-task', '--allow', 'TASK-1,TASK-4')  # round 22: the hand receipts of an unfinished task also need its label
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('failure', ['exception', 'usage', 'empty', 'timeout'])
def test_task_comparison_failure_is_clearable_only_by_task_label(local_repo, monkeypatch, failure):
    root = local_repo
    setup(root)
    branch(root)
    (root / 'calc.py').write_text(CODE + '\ndef added():\n    return 2\n')
    (root / 'tests/test_value.py').write_text(TEST + '\ndef test_added():\n    from calc import added\n    assert added() == 2\n')
    commit_all(root)
    claim(root, 'TASK-1')
    commit_all(root)

    def unavailable(*args, **kwargs):
        if failure == 'exception':
            raise OSError('comparison checkout is unavailable')
        return {'usage': 4, 'empty': 5, 'timeout': 124}[failure], ''

    monkeypatch.setattr(gate, 'run_ref_test', unavailable)
    refused = gate.check(root, base='origin/main', run_tests=True, review='warn')
    assert any('could not compare' in item and 'workorder-allow:TASK-1' in item for item in refused)
    # 0.18 one review rule: an approving review clears an unavailable comparison.
    assert gate.check(root, base='origin/main', run_tests=True, approving_review=True, review='warn') == []
    notes = []
    assert gate.check(root, base='origin/main', run_tests=True, allow=['TASK-1'], notes=notes) == []
    assert any('could not compare' in note and 'allowed' in note for note in notes)


@pytest.mark.parametrize('code,meaning', [(1, 'tests failed'), (2, 'interrupted'), (3, 'internal error'),
                                         (4, 'usage error'), (5, 'no tests were collected'), (124, 'timed out')])
def test_current_pytest_exit_has_plain_meaning(local_repo, code, meaning):
    record = {'id': 'TASK-1', 'status': 'DONE', 'test': 'python -m pytest -q'}
    results = {gate._test_key(record, None): (code, '')}
    finding = gate.run_declared_test(local_repo, record, results=results)
    assert meaning in finding
    assert f'exit {code}' in finding


def test_package_explicit_test_paths_survive_removal(local_repo):
    root = local_repo
    package(root)
    path = define(root, code_paths=('src/demo',), test_paths=('src/demo/testing.py',))
    branch(root)
    path.write_text(path.read_text().replace('test_paths: ["src/demo/testing.py"]\n', ''))
    assert 'src/demo/testing.py' in gate.test_material(root, 'origin/main')


def test_staged_classification_uses_staged_definitions_and_config(local_repo):
    root = local_repo
    (root / 'testing.py').write_text(CODE)
    define(root, code_paths=('testing.py',))
    branch(root)
    git(root, 'add', '-A')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('code_paths: ["testing.py"]\n', ''))
    assert 'testing.py' not in gate.test_material(root, 'origin/main', staged=True)
    # The base declaration is retained, so classification remains code locally too.
    assert 'testing.py' not in gate.test_material(root, 'origin/main')
    (root / '.workorder').mkdir(exist_ok=True)
    (root / '.workorder/config.json').write_text('{"test_file_patterns": "invalid"}')
    gate.test_material(root, 'origin/main', staged=True)
    with pytest.raises(store.Refused, match='test_file_patterns'):
        gate.test_material(root, 'origin/main')


def test_current_failure_still_refuses_with_exemption_label(local_repo):
    root = local_repo
    setup(root)
    branch(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    commit_all(root)
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    result = check(root, '--allow', 'TASK-1,tests,scope:TASK-1', '--approving-review', '--review', 'warn')
    assert result.returncode == 1
    assert any(text in result.stdout for text in ('test failed', 'test fails', 'no test passed')), result.stdout


def test_unscoped_work_receipt_cannot_disable_another_scope(local_repo):
    root = local_repo
    setup(root)
    define(root, tid='TASK-4', code_paths=None)
    (root / 'outside.py').write_text(CODE)
    branch(root)
    (root / 'outside.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    for tid in ('TASK-1', 'TASK-4'):
        ok(root, 'receipt', 'implemented', '--task', tid)
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 1
    assert 'outside.py is outside the code paths of TASK-1' in result.stdout
    assert check(root, '--require-task', '--allow', 'scope:TASK-1,TASK-1,TASK-4').returncode == 0  # round 22: the hand receipts of an unfinished task also need its label


def test_package_testing_module_does_not_require_a_test_file_mapping(local_repo):
    root = local_repo
    package(root)
    path = task(root, ships='none', test='python -c pass')
    path.write_text(path.read_text().replace('\ncontract:', '\ncode_paths: ["src/demo/testing.py"]\ncontract:'))
    branch(root)
    (root / 'src/demo/testing.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    claim(root, 'TASK-1')
    commit_all(root)
    result = cli(root, 'check', '--base', 'origin/main')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('packaged', [False, True])
def test_flat_package_source_requires_packaging_metadata(local_repo, packaged):
    root = local_repo
    (root / 'demo').mkdir()
    (root / 'demo/__init__.py').write_text('')
    (root / 'demo/testing.py').write_text(CODE)
    if packaged:
        (root / 'pyproject.toml').write_text('[project]\nname = "demo"\nversion = "1.0"\n')
    branch(root)
    assert ('demo/testing.py' in gate.test_material(root, 'origin/main')) is not packaged


def test_require_task_ignore_does_not_waive_scope(local_repo):
    root = local_repo
    setup(root)
    (root / 'outside.py').write_text(CODE)
    branch(root)
    (root / 'outside.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    commit_all(root)
    result = check(root, '--require-task', '--require-task-ignore', 'outside.py')
    assert result.returncode == 1
    assert 'workorder-allow:scope:TASK-1' in result.stdout


def test_src_testing_module_keeps_namespace_import_verification():
    from workorder.python_imports import package_names
    assert package_names(['src/demo/testing.py']) == ['demo']
