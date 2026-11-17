"""Round-22 polish P1 item 6: a new test file for other work staged with a task's DONE receipts."""
import pytest

from test_workorder import cli, git, ok
from test_v18l_local_checks import commit, new_task, repo, staged


OTHER = 'from calc import half\n\n\ndef test_other():\n    assert half(4) == 2\n'
STALE = 'differs from the implemented commit'


def done_task(tmp_path, test='tests/test_floor.py'):
    root = repo(tmp_path, {'calc.py': 'def half(x):\n    return x / 2\n'})
    new_task(root, test)
    git(root, 'switch', '-qc', 'fix')
    (root / 'calc.py').write_text('def half(x):\n    return x // 2\n')
    (root / 'tests/test_floor.py').write_text('from calc import half\n\n\ndef test_floor():\n    assert half(5) == 2\n')
    commit(root, 'fix')
    ok(root, 'done', '--task', 'TASK-1')
    return root


def test_a_new_test_file_for_other_work_does_not_make_the_done_claim_stale(tmp_path):
    """Before 1.0.1 the hook said the code differs from the implemented commit and advised renew,
    and the pull-request check refused the claim too, though TASK-1's code and test were unchanged."""
    root = done_task(tmp_path)
    (root / 'tests/test_other.py').write_text(OTHER)
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert STALE not in result.stdout and 'renew' not in result.stdout
    git(root, 'commit', '-qm', 'done, and another test')
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task')
    assert 'NOT DONE' not in result.stdout and 'on code other than the implemented' not in result.stdout, result.stdout
    # The new file is still reviewed as what it is: a change no task records.
    assert 'no task records it' in result.stdout


@pytest.mark.parametrize('change', ['own test', 'code', 'conftest', 'collected'])
def test_a_change_the_task_test_can_see_still_makes_the_claim_stale(tmp_path, change):
    root = done_task(tmp_path, 'tests' if change == 'collected' else 'tests/test_floor.py')
    if change == 'own test':
        (root / 'tests/test_floor.py').write_text(
            'from calc import half\n\n\ndef test_floor():\n    assert half(5) == 2\n    assert half(1) == 0\n')
    elif change == 'code':
        (root / 'calc.py').write_text('def half(x):\n    return x // 2\n\n\nLIMIT = 3\n')
    elif change == 'conftest':
        (root / 'tests/conftest.py').write_text('import pytest\n')
    else:
        (root / 'tests/test_other.py').write_text(OTHER)
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 1 and STALE in result.stdout, result.stdout + result.stderr
