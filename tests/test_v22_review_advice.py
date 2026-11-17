"""Round-22 polish P1: review advice, labels and credit agree with what the check does."""
import re

from test_workorder import cli, git, ok
from test_v18l_local_checks import commit, new_task, repo, staged

CHECK = ('check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task')
APPROVED = '(cleared by the approving review)'


def branch_task(tmp_path, files):
    """TASK-1 written on the branch (new in the pull request), as the verifiers' TASK-3 was."""
    root = repo(tmp_path, files)
    git(root, 'switch', '-qc', 'fix')
    new_task(root, 'tests/test_floor.py')  # its push sends main, still at setup: the task is new here
    return root


def edited_old_test(tmp_path):
    root = branch_task(tmp_path, {'calc.py': 'def half(x):\n    return x / 2\n',
                                  'tests/test_half.py': 'from calc import half\n\n\ndef test_half():\n'
                                                        '    assert half(5) == 2.5\n'})
    (root / 'calc.py').write_text('def half(x):\n    return x // 2\n')
    (root / 'tests/test_floor.py').write_text('from calc import half\n\n\ndef test_floor():\n    assert half(5) == 2\n')
    (root / 'tests/test_half.py').write_text('from calc import half\n\n\ndef test_half():\n    assert half(5) == 2\n')
    return root


def lines_with(text, marker):
    return [line for line in text.splitlines() if marker in line]


def test_every_line_names_the_one_label_that_clears_a_reviewed_old_test_change(tmp_path):
    root = edited_old_test(tmp_path)
    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 0, result.stdout + result.stderr
    # The hook names the real task, never a placeholder id.
    assert 'done --task TASK-1 --needs-review' in result.stdout and '--task ID' not in result.stdout
    commit(root, 'fix')

    refused = cli(root, 'done', '--task', 'TASK-1')
    assert refused.returncode == 1
    nxt = lines_with(refused.stdout, 'Next:')
    assert nxt == ['  Next:     fix the test, or run workorder done --task TASK-1 --needs-review; the pull request '
                   'then needs workorder-allow:TASK-1 (an approving review is not enough: it changes existing tests)'], \
        refused.stdout

    done = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    pending = lines_with(done, 'Pending:')
    assert pending and not any('workorder-allow:tests' in line for line in pending), done
    assert 'will ask for an approving review' not in done, done
    assert any('workorder-allow:TASK-1' in line for line in lines_with(done, 'Next:')), done

    git(root, 'add', '-A')
    result = staged(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'done refuses this until' not in result.stdout, result.stdout
    assert "(recorded in TASK-1's pending review)" in result.stdout
    commit(root, 'done pending review')

    for flags in ([], ['--allow', 'tests'], ['--approving-review'], ['--allow', 'tests', '--approving-review']):
        result = cli(root, *CHECK, *flags)
        assert result.returncode == 1, (flags, result.stdout)  # approval alone never clears test edits
        assert not any('workorder-allow:tests' in line for line in lines_with(result.stdout, 'Pending:')), result.stdout
        assert '(allowed by workorder-allow:tests)' not in result.stdout, result.stdout

    result = cli(root, *CHECK, '--allow', 'TASK-1', '--approving-review')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'accepted by the label workorder-allow:TASK-1: TASK-1 (new task), TASK-1 (pending review)' in result.stdout
    assert not re.search(r'accepted by an approving review: .*pending review', result.stdout), result.stdout
    assert 'DONE pending review' not in result.stdout
    assert 'TASK-1 DONE; its pending review was cleared by workorder-allow:TASK-1' in result.stdout


def test_approved_and_unapproved_runs_report_the_same_hand_receipt_facts(tmp_path):
    root = branch_task(tmp_path, {'calc.py': 'def half(x):\n    return x / 2\n'})
    (root / 'calc.py').write_text('def half(x):\n    return x // 2\n')
    (root / 'tests/test_floor.py').write_text('from calc import half\n\n\ndef test_floor():\n    assert half(4) == 2\n')
    commit(root, 'fix')
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(root, 'receipt', 'test', '--task', 'TASK-1')
    commit(root, 'receipts by hand')
    facts = ["TASK-1's code lands unfinished without done's evidence",
             "pass on origin/main's code too, so it does not show the change works",
             'TASK-1 is new in this pull request']
    plain = cli(root, *CHECK)
    assert plain.returncode == 1
    for fact in facts:
        assert any(fact in line and not line.startswith('Note:') for line in plain.stdout.splitlines()), \
            (fact, plain.stdout)
    approved = cli(root, *CHECK, '--approving-review')
    assert approved.returncode == 0, approved.stdout + approved.stderr
    for fact in facts:
        assert any(fact in line and line.endswith(APPROVED) for line in approved.stdout.splitlines()), \
            (fact, approved.stdout)
    assert 'base code not run' not in approved.stdout


def test_a_test_first_exception_cleared_by_review_is_listed(tmp_path):
    root = repo(tmp_path, {'calc.py': 'def half(x):\n    return x / 2\n'})
    new_task(root, 'tests/test_floor.py')
    git(root, 'switch', '-qc', 'fix')
    (root / 'calc.py').write_text('def half(x):\n    return x // 2\n')
    (root / 'tests/test_floor.py').write_text('from calc import half\n\n\ndef test_floor():\n    assert half(5) == 2\n')
    commit(root, 'fix')
    ok(root, 'done', '--task', 'TASK-1')
    commit(root, 'done')
    flags = (*CHECK, '--require-test-first')
    result = cli(root, *flags)
    assert result.returncode == 1 and 'its test must land on origin/main, failing, before the work' in result.stdout
    result = cli(root, *flags, '--approving-review')
    assert result.returncode == 0, result.stdout + result.stderr
    assert any('its test must land on origin/main, failing, before the work' in line and line.endswith(APPROVED)
               for line in result.stdout.splitlines()), result.stdout
    assert 'TASK-1 (test-first exception)' in result.stdout


def test_help_and_generated_workflow_state_the_review_rule(tmp_path):
    text = ' '.join(ok(tmp_path, 'check', '--help').split())
    assert 'existing tests that now fail' in text
    assert 'failed current tests and unsupported claims always refuse' in text
    root = repo(tmp_path, {'calc.py': 'def half(x):\n    return x / 2\n'})
    workflow = root / '.github/workflows/workorder.yml'
    current = workflow.read_text()
    assert 'or approves this head' not in current and 'approval is not enough' in current
    # A workflow written by 1.0 differs only in that comment: not reported as needing an update.
    from workorder import bootstrap
    workflow.write_text(current.replace(bootstrap.NEW_REVIEW_COMMENT, bootstrap.OLD_REVIEW_COMMENT))
    result = cli(root, 'init', '--check', '--update-workflow')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'up to date (only its review comment is older' in result.stdout
    workflow.write_text(current.replace('--require-task', '--review warn'))
    assert cli(root, 'init', '--check', '--update-workflow').returncode == 1
