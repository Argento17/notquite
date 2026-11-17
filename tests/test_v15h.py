"""Eighth-pass regressions using real commands and offline repositories."""
import pytest

from workorder import gate, store
from test_workorder import cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import check, claim
from test_v15d import hooked_commit, stop


BETA = 'def beta(n):\n    return n + 1\n'
GAMMA = 'def gamma():\n    return 3\n'
BETA_TEST = 'from beta import beta\n\ndef test_beta():\n    assert beta(2) == 3\n'
GAMMA_TEST = 'from gamma import gamma\n\ndef test_gamma():\n    assert gamma() == 3\n'


def define(root, tid, name, scope=None, kind=None):
    path = task(root, tid, ships='none', test=f'python -m pytest -q tests/test_{name}.py', kind=kind)
    fill_sections(path)
    if scope is not None:
        path.write_text(path.read_text().replace('contract: "none"',
                                                f'contract: "none"\ncode_paths: ["{scope}"]'))


def setup(root):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()


def start_branch(root):
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')


def two_functions(root):
    (root / 'beta.py').write_text(BETA)
    (root / 'gamma.py').write_text(GAMMA)
    (root / 'tests/test_beta.py').write_text(BETA_TEST)
    (root / 'tests/test_gamma.py').write_text(GAMMA_TEST)
    commit_all(root)


@pytest.mark.parametrize('unscoped', [False, True])
def test_second_task_test_edit_ignores_first_tasks_refactor(local_repo, unscoped):
    root = local_repo
    setup(root)
    define(root, 'TASK-1', 'beta', 'beta.py', kind='refactor')
    define(root, 'TASK-2', 'gamma', None if unscoped else 'gamma.py')
    start_branch(root)
    two_functions(root)
    for tid in ('TASK-1', 'TASK-2'):
        ok(root, 'done', '--task', tid)
    commit_all(root)
    git(root, 'switch', 'main')
    git(root, 'merge', '--ff-only', 'feature')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'followup')
    (root / 'beta.py').write_text('def beta(n):\n    value = n + 1\n    return value\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--refactor')
    commit_all(root)
    (root / 'tests/test_gamma.py').write_text(GAMMA_TEST + '    assert gamma() != 0\n')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    assert 'DONE, pending review' in output
    assert 'stub' not in output
    assert store.record(root, 'TASK-2')['status'] == 'DONE'
    git(root, 'add', '-A')
    for result in (stop(root), hooked_commit(root)):
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'stub' not in result.stdout + result.stderr
    refused = check(root)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert 'workorder-allow:TASK-2' in refused.stdout
    assert 'workorder-allow:TASK-1' not in refused.stdout
    accepted = check(root, '--allow', 'TASK-2')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert 'stub' not in accepted.stdout


@pytest.mark.parametrize('unscoped', [False, True])
def test_two_new_tasks_complete_with_their_own_functions(local_repo, unscoped):
    root = local_repo
    setup(root)
    start_branch(root)
    define(root, 'TASK-1', 'beta', None if unscoped else 'beta.py')
    define(root, 'TASK-2', 'gamma', 'gamma.py')
    two_functions(root)
    for tid in ('TASK-1', 'TASK-2'):
        ok(root, 'done', '--task', tid)
        assert store.record(root, tid)['status'] == 'DONE'
    commit_all(root)
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    for tid in ('TASK-1', 'TASK-2'):
        assert f'{tid} was written and finished in this pull request;' in result.stdout
    result = check(root, '--allow', 'TASK-1,TASK-2' + (',scope:TASK-2' if unscoped else ''))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('unscoped', [False, True])
@pytest.mark.parametrize('action', ['done', 'check', 'precommit', 'stop'])
def test_calling_other_tasks_function_does_not_prove_own_change(local_repo, unscoped, action):
    root = local_repo
    setup(root)
    define(root, 'TASK-1', 'beta', None if unscoped else 'beta.py')
    define(root, 'TASK-2', 'gamma', 'gamma.py')
    start_branch(root)
    two_functions(root)
    # It imports beta but only exercises gamma. Stubbing both hides the mistake.
    (root / 'tests/test_beta.py').write_text(
        'from beta import beta\nfrom gamma import gamma\n\ndef test_beta():\n    assert gamma() == 3\n')
    commit_all(root)
    claim(root, 'TASK-2')
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1')
    else:
        claim(root, 'TASK-1')
        git(root, 'add', '-A')
        result = check(root) if action == 'check' else hooked_commit(root) if action == 'precommit' else stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == (1 if action in {'done', 'check'} else 0), output
    assert 'stubbed out (test_beta)' in output and 'beta in beta.py' in output
    assert 'gamma in gamma.py' not in output


@pytest.mark.parametrize('helper_at_base', [False, True])
@pytest.mark.parametrize('selection', ['file', 'folder', 'discovery'])
def test_new_tests_with_new_conftest_have_no_base_findings(local_repo, helper_at_base, selection):
    root = local_repo
    setup(root)
    define(root, 'TASK-1', 'add', 'calc.py')
    if selection != 'file':
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('tests/test_add.py', 'tests' if selection == 'folder' else ''))
    if helper_at_base:
        (root / 'tests/helper.py').write_text('def expected():\n    return 4\n')
    start_branch(root)
    (root / 'calc.py').write_text('def add(a, b):\n    return a + b\n')
    (root / 'tests/conftest.py').write_text('import pytest\n\n@pytest.fixture\ndef two():\n    return 2\n')
    if not helper_at_base:
        (root / 'tests/helper.py').write_text('def expected():\n    return 4\n')
    (root / 'tests/test_add.py').write_text(
        'from calc import add\nfrom helper import expected\n\ndef test_add(two):\n    assert add(two, two) == expected()\n')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert "TASK-1's test was written in this change" in output
    assert store.record(root, 'TASK-1')['status'] == 'DONE'
    commit_all(root)
    result = check(root)
    output += result.stdout + result.stderr
    assert result.returncode == 0, output
    for wrong in ('configuration changed', 'already fail', 'from origin/main fail', 'REF comparison skipped', 'workorder-allow:'):
        assert wrong not in output, output


@pytest.mark.parametrize('conftest_folder', ['tests', '.'])
def test_new_conftest_only_names_tasks_with_existing_tests_below_it(local_repo, conftest_folder):
    root = local_repo
    setup(root)
    define(root, 'TASK-1', 'beta', 'beta.py')
    define(root, 'TASK-2', 'gamma', 'gamma.py')
    (root / 'beta.py').write_text(BETA)
    (root / 'tests/test_beta.py').write_text(BETA_TEST)
    commit_all(root)
    claim(root, 'TASK-1')
    start_branch(root)
    (root / 'gamma.py').write_text(GAMMA)
    (root / 'tests/test_gamma.py').write_text(GAMMA_TEST)
    (root / conftest_folder / 'conftest.py').write_text('import pytest\n\n@pytest.fixture\ndef two():\n    return 2\n')
    commit_all(root)
    claim(root, 'TASK-2')
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    findings = [line for line in result.stdout.splitlines() if 'configuration changed' in line]
    assert len(findings) == 1, result.stdout
    assert 'TASK-1' in findings[0] and 'TASK-2' not in findings[0], result.stdout
    accepted = check(root, '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


@pytest.mark.parametrize('kind', ['assertion', 'stub'])
def test_done_next_names_real_review_route_for_unusual_tests(local_repo, kind):
    root = local_repo
    setup(root)
    define(root, 'TASK-1', 'beta', 'beta.py')
    start_branch(root)
    (root / 'beta.py').write_text(BETA)
    body = 'beta(2)' if kind == 'assertion' else 'assert callable(beta)'
    (root / 'tests/test_beta.py').write_text('from beta import beta\n\ndef test_beta():\n    ' + body + '\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    next_line = next(line for line in result.stdout.splitlines() if 'Next:' in line)
    assert 'fix the test, or run workorder done --task TASK-1 --needs-review' in next_line
    assert 'workorder-allow:TASK-1' in next_line
    assert store.entries(root, 'TASK-1') == []
    output = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert 'TASK-1 DONE, pending review' in output
    assert check(root).returncode == 1
    assert check(root, '--approving-review').returncode == 1
    accepted = check(root, '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_done_next_for_unavailable_comparison_accepts_needs_review(local_repo, tmp_path):
    from test_v15d_imports import remapped_editable
    from test_v15c_imports import setup_task
    root = local_repo
    python, _ = remapped_editable(root, tmp_path)
    setup_task(root, python, '')
    (root / 'pkg/unipkg/__init__.py').write_text('def value():\n    return 2\n')
    (root / 'tests/test_value.py').write_text('from unipkg import value\ndef test_value():\n    assert value() == 2\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1 and 'could not compare' in result.stdout
    next_line = next(line for line in result.stdout.splitlines() if 'Next:' in line)
    assert 'workorder done --task TASK-1 --needs-review' in next_line
    assert 'workorder-allow:TASK-1' in next_line
    output = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert 'DONE, pending review' in output
    commit_all(root)
    assert check(root, '--approving-review').returncode == 1
    accepted = check(root, '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_mixed_done_findings_keep_general_next(capsys):
    from workorder.cli import done_refusal
    done_refusal('TASK-1', 'IN_PROGRESS', [
        '  Missing:  TASK-1: its test still passes with the changed functions stubbed out (beta in beta.py); '
        'it does not exercise the change; a reviewer accepts it with the label workorder-allow:TASK-1',
        '  Missing:  commit the code first'])
    assert 'Next:     address the findings, then run workorder done --task TASK-1 again' in capsys.readouterr().out


def test_test_first_expected_failure_is_success_message(local_repo):
    root = local_repo
    (root / 'test_beta.py').write_text('assert int("1") == 2\n')
    output = ok(root, 'new', '--test-first', '--title', 'Beta', '--owner', 'dev', '--test', 'python test_beta.py')
    assert 'test fails as required' in output
    assert 'Failure:' not in output
    assert output.index('test fails as required') < output.index('TASK-1 opened')


def test_new_script_test_does_not_print_skipped_base_comparison(local_repo):
    root = local_repo
    setup(root)
    path = task(root, ships='none', test='python test_calc.py')
    fill_sections(path)
    start_branch(root)
    (root / 'calc.py').write_text('def add(a, b):\n    return a + b\n')
    (root / 'test_calc.py').write_text('from calc import add\n\nassert add(2, 2) == 4\n')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert "TASK-1's test was written in this change" in output
    assert 'REF comparison skipped' not in output


def test_new_parent_conftest_does_not_count_old_child_conftest_as_test(local_repo):
    root = local_repo
    setup(root)
    define(root, 'TASK-1', 'beta', 'beta.py')
    (root / 'tests/conftest.py').write_text('# shared setup, no tests yet\n')
    start_branch(root)
    (root / 'conftest.py').write_text('import pytest\n@pytest.fixture\ndef two():\n    return 2\n')
    (root / 'beta.py').write_text(BETA)
    (root / 'tests/test_beta.py').write_text(
        'from beta import beta\ndef test_beta(two):\n    assert beta(two) == 3\n')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert 'configuration changed' not in output
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_test_first_refusal_next_does_not_offer_ineffective_review_flag(local_repo):
    root = local_repo
    setup(root)
    define(root, 'TASK-1', 'beta', 'beta.py')
    start_branch(root)
    (root / 'beta.py').write_text(BETA)
    (root / 'tests/test_beta.py').write_text(BETA_TEST)
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1', '--require-test-first')
    assert result.returncode == 1, result.stdout + result.stderr
    next_line = next(line for line in result.stdout.splitlines() if 'Next:' in line)
    assert '--needs-review' not in next_line
    assert 'workorder-allow:TASK-1' in next_line
    assert 'land the failing test before the work' in next_line
    assert cli(root, 'done', '--task', 'TASK-1', '--require-test-first', '--needs-review').returncode == 1
    claim(root, 'TASK-1')
    accepted = check(root, '--require-test-first', '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
