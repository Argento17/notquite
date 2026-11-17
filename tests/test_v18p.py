"""Lane P (0.18): one review rule and plainer notes, in disposable offline repositories.

An approving review clears every finding except edits, deletions, new skips/xfails or weakening
of existing tests, and changes to the Workorder workflow file; those two need a label.
"""
from types import SimpleNamespace

import pytest

from workorder import gate, store
from test_workorder import cli, git, local_repo, ok
from test_v05 import commit_all
from test_v14 import claim
from test_v17r import CODE, TEST, branch, check, define, setup


CLEARS = 'an approving review clears this'


def test_approval_clears_scope(local_repo):
    root = local_repo
    setup(root)
    (root / 'outside.py').write_text(CODE)
    branch(root)
    (root / 'outside.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if 'outside the code paths of TASK-1' in line)
    assert f'{CLEARS}, or a reviewer accepts it with the label workorder-allow:scope:TASK-1' in line
    result = check(root, '--require-task', '--approving-review')
    assert result.returncode == 0, result.stdout + result.stderr
    assert check(root, '--require-task', '--allow', 'scope:TASK-1,TASK-1').returncode == 0  # round 22: the hand receipts of an unfinished task also need its label


def test_approval_clears_code_without_task(local_repo):
    root = local_repo
    setup(root)
    branch(root)
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 2'))
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if 'no task records it' in line)
    assert f'{CLEARS}, or a reviewer accepts it with the label workorder-allow:no-task' in line
    result = check(root, '--require-task', '--approving-review')
    assert result.returncode == 0, result.stdout + result.stderr
    assert check(root, '--require-task', '--allow', 'no-task').returncode == 0


def test_task_written_and_finished_in_one_change(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'calc.py').write_text(CODE)
    branch(root)
    define(root, tid='TASK-2')
    (root / 'calc.py').write_text(CODE + '\ndef double():\n    return 2\n')
    (root / 'tests/test_value.py').write_text(
        'from calc import double\n\ndef test_double():\n    assert double() == 2\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2')
    assert result.returncode == 0, result.stdout + result.stderr
    assert ('the pull-request check will ask for an approving review: this task is new in this change'
            in result.stdout)
    commit_all(root)
    result = check(root, '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if 'written and finished' in line)
    assert CLEARS in line and 'a second pull request is not needed' in line
    result = check(root, '--require-task', '--approving-review')
    assert result.returncode == 0, result.stdout + result.stderr
    assert check(root, '--require-task', '--allow', 'TASK-2').returncode == 0


@pytest.mark.parametrize('completion', ['refactor', 'no-code-change'])
@pytest.mark.parametrize('kind_in_change', [False, True])
def test_approval_clears_completion_kind_not_on_base(local_repo, completion, kind_in_change):
    root = local_repo
    setup(root)
    branch(root)
    if kind_in_change:
        define(root, kind='refactor' if completion == 'refactor' else 'verification')
        commit_all(root)
    if completion == 'refactor':
        (root / 'calc.py').write_text(CODE.replace('return 1', 'return 1 + 0'))
        commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--' + completion)
    commit_all(root)
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if 'completion needs review' in line)
    assert f'{CLEARS}, or a reviewer accepts it with the label workorder-allow:TASK-1' in line
    result = check(root, '--approving-review')
    assert result.returncode == 0, result.stdout + result.stderr


def edited_existing_test(root):
    setup(root)
    branch(root)
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 2'))
    (root / 'tests/test_value.py').write_text(TEST.replace('== 1', '== 2'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    commit_all(root)


def test_edited_existing_test_needs_label_even_with_approval(local_repo):
    root = local_repo
    edited_existing_test(root)
    for flags in [[], ['--approving-review']]:
        result = check(root, *flags)
        assert result.returncode == 1, result.stdout + result.stderr
        assert ('(an approving review is not enough: it edits existing tests)' in result.stdout
                or 'an approving review is not enough: it changes existing tests' in result.stdout), result.stdout
        assert CLEARS not in result.stdout, result.stdout
    result = check(root, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr


def test_approval_note_names_what_it_did_not_cover(local_repo):
    root = local_repo
    edited_existing_test(root)
    result = check(root, '--approving-review')
    assert 'approval did not cover' in result.stdout


def test_reopened_failing_test_is_cleared_by_approval(local_repo):
    root = local_repo
    setup(root)
    commit_all(root)
    claim(root, 'TASK-1')
    branch(root)
    store.set_task_field(root, 'TASK-1', 'status', 'IN_PROGRESS')
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 3'))
    commit_all(root)
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if 'was reopened' in line)
    assert f'{CLEARS}, or a reviewer accepts it with the label workorder-allow:TASK-1' in line
    assert check(root, '--approving-review').returncode == 0


def test_origin_says_edited_for_existing_file(local_repo):
    root = local_repo
    setup(root)
    commit_all(root)
    claim(root, 'TASK-1')
    branch(root)
    (root / 'tests/test_value.py').write_text(TEST + '\n\ndef test_other():\n    assert value() != 2\n')
    record = store.record(root, 'TASK-1')
    _, origin = gate.test_origin(root, record, 'origin/main', 'origin/main')
    assert origin == "TASK-1's new tests were written in this change (test_other)"
    (root / 'tests/test_value.py').write_text(TEST.replace('== 1', '== 1 and True'))
    gate.s.invalidate_reads()
    _, origin = gate.test_origin(root, record, 'origin/main', 'origin/main')
    assert origin == "TASK-1's test file was edited in this change (tests/test_value.py)"
    assert 'written' not in origin
    define(root, tid='TASK-2', code_paths=('calc.py',), test_paths=['tests/test_new.py'])
    path = root / 'tasks/TASK-2.md'
    path.write_text(path.read_text().replace('tests/test_value.py', 'tests/test_new.py'))
    (root / 'tests/test_new.py').write_text(TEST)
    gate.s.invalidate_reads()
    _, origin = gate.test_origin(root, store.record(root, 'TASK-2'), 'origin/main', 'origin/main')
    assert origin == "TASK-2's test was written in this change"


def test_shared_test_file_attributes_new_test_to_touched_task(local_repo):
    root = local_repo
    setup(root)
    commit_all(root)
    claim(root, 'TASK-1')
    define(root, tid='TASK-3')
    commit_all(root)
    claim(root, 'TASK-3')
    define(root, tid='TASK-5')
    branch(root)
    (root / 'calc.py').write_text(CODE + '\ndef other():\n    return 5\n')
    with (root / 'tests/test_value.py').open('a') as stream:
        stream.write('\n\ndef test_other():\n    from calc import other\n    assert other() == 5\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-5')
    commit_all(root)
    result = check(root, '--verbose')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TASK-5's new tests were written in this change (test_other)" in result.stdout
    assert "TASK-1's new tests" not in result.stdout
    assert "TASK-3's new tests" not in result.stdout


def test_shared_attribution_names_tasks_once():
    origins = {'TASK-1': ("TASK-1's new tests were written in this change (test_a)", ('t.py::test_a',)),
               'TASK-3': ("TASK-3's new tests were written in this change (test_a)", ('t.py::test_a',)),
               'TASK-4': ("TASK-4's new tests were written in this change (test_a)", ('t.py::test_a',))}
    notes = ['first'] + [origin for origin, _ in origins.values()]
    result = gate.attribute_origins(notes, origins, worked={'TASK-1', 'TASK-3'})
    assert result == ['first', 'New tests were written in this change (test_a); shared by TASK-1, TASK-3']


def test_reused_result_is_explained(capsys):
    run = SimpleNamespace(cached=True, reused_at='12:34', returncode=0, stdout='', stderr='')
    with store.test_output_session():
        gate.test_result(run, {})
    assert ('(result reused from the run at 12:34 UTC; nothing it covers changed since)'
            in capsys.readouterr().out)


def test_edit_whose_old_test_still_passes_needs_label(local_repo):
    root = local_repo
    setup(root)
    branch(root)
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 2 - 1'))
    (root / 'tests/test_value.py').write_text(TEST.replace('== 1', 'in (1,)'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    commit_all(root)
    result = check(root, '--approving-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'an approving review is not enough' in result.stdout
    assert 'its tests from' not in result.stdout  # the old test passes: only the edit needs the label
    result = check(root, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
