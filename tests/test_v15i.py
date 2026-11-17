"""Ninth-pass regressions: public commands, disposable repositories, installed hooks."""
import pytest

from workorder import gate, store
from test_workorder import cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v15d import hooked_commit, stop
from test_v15f import commit_with_hook, finished_on_branch, rename_branch_test

CODE = 'def add(a, b):\n    return a + b\n'
TEST = 'from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n'
EDITED = TEST + '    assert add(-1, 1) == 0\n'
REFACTOR = 'def add(a, b):\n    result = a + b\n    return result\n'


def prepare(root, test_text=TEST, code=CODE, kind=None):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    path = task(root, ships='none', test='python -m pytest -q tests/test_add.py', kind=kind)
    fill_sections(path)
    path.write_text(path.read_text().replace('contract: "none"', 'contract: "none"\ncode_paths: ["calc.py"]'))
    commit_with_hook(root)
    git(root, 'switch', '-c', 'feature')
    (root / 'calc.py').write_text(code)
    (root / 'tests/test_add.py').write_text(test_text)
    commit_with_hook(root)


def land(root):
    ok(root, 'done', '--task', 'TASK-1')
    commit_with_hook(root)
    git(root, 'switch', 'main')
    git(root, 'merge', '--ff-only', 'feature')
    git(root, 'switch', '-c', 'followup')


def pr(root, *flags):
    return cli(root, 'check', '--base', 'main', '--run-tests', '--require-task', *flags)


def assert_review(root, tid='TASK-1', *, label_only=False):
    result = pr(root)
    assert result.returncode == 1 and f'workorder-allow:{tid}' in result.stdout, result.stdout + result.stderr
    if label_only:
        for flags in (['--approving-review'], ['--review', 'warn'], ['--allow', 'tests']):
            result = pr(root, *flags)
            assert result.returncode == 1, result.stdout + result.stderr
    result = pr(root, '--allow', tid)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('repair', ['renew', 'done'])
def test_refactor_test_edit_commits_and_advised_repair_works(local_repo, repair):
    root = local_repo
    prepare(root)
    land(root)
    (root / 'calc.py').write_text(REFACTOR)
    commit_with_hook(root)
    ok(root, 'done', '--task', 'TASK-1', '--refactor')
    commit_with_hook(root)
    (root / 'tests/test_add.py').write_text(EDITED)
    git(root, 'add', '-A')
    expected = ('TASK-1: test files changed and passes on this code; it is a declared refactor, '
                'so after committing run workorder renew --task TASK-1 --no-refactor '
                '(or workorder done --task TASK-1 --needs-review); the pull request needs workorder-allow:TASK-1')
    for result in (stop(root), hooked_commit(root)):
        assert result.returncode == 0, result.stdout + result.stderr
        assert expected in result.stdout + result.stderr
    result = stop(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout + result.stderr
    flags = ['--no-refactor'] if repair == 'renew' else ['--needs-review']
    ok(root, repair, '--task', 'TASK-1', *flags)
    output = commit_with_hook(root)
    assert 'after committing run workorder renew' not in output, output
    assert_review(root)


@pytest.mark.parametrize('hook', ['precommit', 'stop'])
def test_refactor_failing_test_edit_still_refuses(local_repo, hook):
    root = local_repo
    prepare(root)
    land(root)
    (root / 'calc.py').write_text(REFACTOR)
    commit_with_hook(root)
    ok(root, 'done', '--task', 'TASK-1', '--refactor')
    commit_with_hook(root)
    (root / 'tests/test_add.py').write_text(TEST.replace('== 4', '== 5'))
    git(root, 'add', '-A')
    result = hooked_commit(root) if hook == 'precommit' else stop(root)
    assert result.returncode == (1 if hook == 'precommit' else 2), result.stdout + result.stderr
    assert 'passes on this code' not in result.stdout + result.stderr


@pytest.mark.parametrize('test_text,finding', [
    ('from calc import add\n\ndef test_add():\n    result = add(2, 2)\n    if result != 4:\n        raise AssertionError(result)\n', 'asserts nothing that can fail'),
    ('from calc import add\n\ndef test_add():\n    assert callable(add)\n', 'still pass with the changed functions stubbed out'),
    ('from calc import add\n\ndef test_add():\n    assert True\n', 'asserts nothing that can fail'),
])
def test_label_only_findings_have_done_route(local_repo, test_text, finding):
    root = local_repo
    prepare(root, test_text)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1 and finding in result.stdout, result.stdout
    output = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    next_line = next(line for line in result.stdout.splitlines() if 'Next:' in line)
    assert ('fix the test, or run workorder done --task TASK-1 --needs-review; '
            'the pull request needs workorder-allow:TASK-1') in next_line
    assert 'TASK-1 DONE, pending review' in output and finding in output, output
    assert store.record(root, 'TASK-1')['status'] == 'DONE'
    run = store.entries(root, 'TASK-1')[-1]
    assert run['pending_review'] is True
    assert any(finding in item['message'] and item['label_only'] for item in run['pending_findings'])
    for result in (stop(root),):
        assert result.returncode == 0, result.stdout + result.stderr
        review_lines = [line for line in result.stdout.splitlines() if 'pending review:' in line]
        assert review_lines and all('or an approving review' not in line for line in review_lines), result.stdout
    commit_with_hook(root)
    assert_review(root, label_only=True)
    output = ok(root, 'check', 'TASK-1')
    assert finding in output, output


def test_shared_file_stub_finding_reaches_pending_review(local_repo):
    root = local_repo
    prepare(root, code=CODE + '\ndef mul(a, b):\n    return a * b\n', kind='refactor')
    path = task(root, 'TASK-2', ships='none', test='python -m pytest -q tests/test_mul.py')
    fill_sections(path)
    path.write_text(path.read_text().replace('contract: "none"', 'contract: "none"\ncode_paths: ["calc.py"]'))
    mul_test = 'from calc import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n'
    (root / 'tests/test_mul.py').write_text(mul_test)
    commit_with_hook(root)
    ok(root, 'done', '--task', 'TASK-2')
    land(root)
    (root / 'calc.py').write_text(REFACTOR + '\ndef mul(a, b):\n    return a * b\n')
    commit_with_hook(root)
    ok(root, 'done', '--task', 'TASK-1', '--refactor')
    commit_with_hook(root)
    (root / 'tests/test_mul.py').write_text(mul_test + '    assert mul(0, 3) == 0\n')
    commit_with_hook(root)
    output = ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    assert 'TASK-2 DONE, pending review' in output
    assert 'stubbed out (test_mul)' in output and 'add in calc.py' in output
    commit_with_hook(root)
    assert_review(root, 'TASK-2', label_only=True)


@pytest.mark.parametrize('action', ['hook', 'done', 'check', 'refactor'])
def test_comment_only_finished_test_is_unchanged(local_repo, action):
    root = local_repo
    prepare(root)
    land(root)
    if action == 'refactor':
        (root / 'calc.py').write_text(REFACTOR)
    (root / 'tests/test_add.py').write_text(TEST.replace('    assert', '    # check addition\n\n    assert') + '\n')
    git(root, 'add', '-A')
    if action == 'hook':
        for result in (stop(root), hooked_commit(root)):
            assert result.returncode == 0, result.stdout + result.stderr
            assert 'test files changed' not in result.stdout + result.stderr
            assert 'workorder-allow:TASK-1' not in result.stdout + result.stderr
    else:
        commit_with_hook(root)
        result = (cli(root, 'done', '--task', 'TASK-1', *(['--refactor'] if action == 'refactor' else []))
                  if action != 'check' else pr(root))
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'workorder-allow:TASK-1' not in result.stdout + result.stderr


GENERATED = ['src/sample.egg-info/PKG-INFO', 'src/sample.dist-info/METADATA',
             'src/__pycache__/sample.cpython-312.pyc', 'sample.pyc', '.pytest_cache/v/cache/nodeids',
             'tests/__pycache__/sample.cpython-312.pyc', 'tests/sample.egg-info/PKG-INFO',
             'tests/sample.dist-info/METADATA', 'tests/.pytest_cache/v/cache/nodeids']


def write_generated(root):
    for relative in GENERATED:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('generated\n')
    git(root, 'add', '-f', *GENERATED)


def test_generated_paths_not_in_scope_or_implementation_receipts(local_repo):
    root = local_repo
    prepare(root)
    write_generated(root)
    commit_with_hook(root)
    ok(root, 'done', '--task', 'TASK-1')
    implemented = gate.newest_implementation(store.entries(root, 'TASK-1'))
    assert not set(GENERATED) & implemented['implementation_paths'].keys()
    commit_with_hook(root)
    result = pr(root)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('remove', [False, True])
def test_generated_only_change_needs_no_task(local_repo, remove):
    root = local_repo
    write_generated(root)
    if remove:
        commit_all(root)
    base = store.head(root)
    if remove:
        git(root, 'rm', '-r', *GENERATED)
    commit_all(root)
    result = cli(root, 'check', '--base', base, '--run-tests', '--require-task')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'workorder-allow' not in result.stdout


@pytest.mark.parametrize('own_change', [False, True])
def test_named_check_reports_task_file_freshness(local_repo, own_change):
    root = local_repo
    prepare(root)
    land(root)
    implemented = gate.newest_implementation(store.entries(root, 'TASK-1'))
    path = root / ('calc.py' if own_change else 'other.py')
    path.write_text(REFACTOR if own_change else 'value = 1\n')
    commit_with_hook(root)
    output = ok(root, 'check', 'TASK-1')
    if own_change:
        assert 'the code has changed since' in output
    else:
        assert f"TASK-1's files are unchanged since {implemented['commit'][:7]}" in output, output
        assert 'the code has changed since' not in output


def test_renewed_receipts_commit_has_no_repair_note(local_repo):
    root = local_repo
    finished_on_branch(root)
    rename_branch_test(root)
    commit_with_hook(root)
    ok(root, 'renew', '--task', 'TASK-1')
    git(root, 'add', '-A')
    for result in (stop(root), hooked_commit(root)):
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'after committing run workorder renew' not in result.stdout + result.stderr
    # Review is still required after renewing.
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout


def test_two_missing_tests_use_plural_words(local_repo):
    root = local_repo
    ok(root, 'init')
    for n in (1, 2):
        fill_sections(task(root, f'TASK-{n}', ships='none', test=f'python -m pytest tests/test_{n}.py'))
    output = commit_with_hook(root)
    assert 'Note: 2 tasks: their test files are not there yet; an unfinished task may land' in output, output


def test_merge_only_test_change_reports_no_task_changed(local_repo):
    root = local_repo
    prepare(root)
    land(root)
    git(root, 'switch', 'main')
    (root / 'tests/test_add.py').write_text(EDITED)
    commit_with_hook(root)
    git(root, 'switch', 'followup')
    git(root, 'merge', '--no-commit', '--no-ff', 'main')
    commit_with_hook(root)
    output = ok(root, 'check', '--base', 'main', '--run-tests')
    assert output.splitlines()[-1] == 'no task changed in this change', output
    assert 'checked 0 tasks' not in output and 'no declared test ran' not in output


@pytest.mark.parametrize('path_map', [False, True])
def test_existing_receipt_with_generated_paths_survives_removal(local_repo, path_map):
    root = local_repo
    prepare(root)
    definition = root / 'tasks/TASK-1.md'
    definition.write_text(definition.read_text().replace('code_paths: ["calc.py"]\n', ''))
    write_generated(root)
    commit_with_hook(root)
    commit = store.head(root)
    # Construct the exact optional path map older releases recorded, before committing it.
    paths = {'calc.py', 'tests/test_add.py', *GENERATED}
    tree = store.code_tree(root, commit)
    data = dict(kind='implemented', commit=commit, code=store.code_digest(root, commit))
    if path_map:
        data.update(implementation_base=git(root, 'rev-parse', 'main'),
                    implementation_paths={path: tree[path] for path in paths})
    store.append(root, 'TASK-1', data)
    ok(root, 'receipt', 'test', '--task', 'TASK-1')
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_with_hook(root)
    git(root, 'switch', 'main')
    git(root, 'merge', '--ff-only', 'feature')
    git(root, 'switch', '-c', 'cleanup')
    git(root, 'rm', '-r', *GENERATED)
    commit_with_hook(root)
    result = pr(root, '--all-tests')
    assert result.returncode == 0, result.stdout + result.stderr
    output = ok(root, 'check', 'TASK-1')
    assert "TASK-1's files are unchanged since" in output, output


def test_pending_comparison_finding_is_recorded_and_displayed(local_repo, tmp_path):
    from test_v15d_imports import remapped_editable
    from test_v15c_imports import setup_task
    root = local_repo
    python, _ = remapped_editable(root, tmp_path)
    setup_task(root, python, '')
    (root / 'pkg/unipkg/__init__.py').write_text('def value():\n    return 2\n')
    (root / 'tests/test_value.py').write_text('from unipkg import value\ndef test_value():\n    assert value() == 2\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    run = store.entries(root, 'TASK-1')[-1]
    assert any('could not compare' in item['message'] and item['label_only'] for item in run['pending_findings'])
    output = ok(root, 'show', '--task', 'TASK-1')
    assert 'Pending:' in output and 'could not compare' in output, output
    commit_all(root)
    for flags in ([], ['--approving-review'], ['--review', 'warn']):
        result = cli(root, 'check', '--base', 'origin/main', '--run-tests', *flags)
        assert result.returncode == 1 and 'Pending:' in result.stdout, result.stdout + result.stderr
    ok(root, 'check', '--base', 'origin/main', '--run-tests', '--allow', 'TASK-1')


@pytest.mark.parametrize('blocker', ['failure', 'command', 'dependency', 'evidence'])
def test_needs_review_still_refuses_nonreviewable_findings(local_repo, blocker):
    root = local_repo
    prepare(root)
    if blocker == 'failure':
        (root / 'tests/test_add.py').write_text(TEST.replace('== 4', '== 5'))
    elif blocker == 'command':
        # Evidence exists for the declared command before it is changed.
        ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('pytest -q', 'pytest -x -q'))
    elif blocker == 'dependency':
        fill_sections(task(root, 'TASK-2', ships='none', test='python -c pass'))
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('waits_on: []', 'waits_on: ["task:TASK-2"]'))
    else:
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('contract: "none"', 'contract: "missing.json"'))
    commit_all(root)
    before = store.entries(root, 'TASK-1')
    result = cli(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
    assert store.entries(root, 'TASK-1') == before


def test_generated_uncommitted_files_do_not_block_done_or_claim_commit(local_repo):
    root = local_repo
    prepare(root)
    write_generated(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert 'TASK-1 DONE:' in output
    commit_with_hook(root)
    result = pr(root)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('action', ['hook', 'done', 'check'])
def test_comment_only_conftest_is_unchanged(local_repo, action):
    root = local_repo
    prepare(root)
    path = root / 'tests/conftest.py'
    path.write_text('import pytest\n')
    commit_with_hook(root)
    land(root)
    path.write_text('# Fixture configuration\n\nimport pytest\n')
    git(root, 'add', '-A')
    if action == 'hook':
        results = (stop(root), hooked_commit(root))
    else:
        commit_with_hook(root)
        results = (cli(root, 'done', '--task', 'TASK-1') if action == 'done' else pr(root),)
    for result in results:
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'workorder-allow:' not in result.stdout + result.stderr
        assert 'pytest configuration changed' not in result.stdout + result.stderr
