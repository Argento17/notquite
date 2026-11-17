"""Round-22 lane F1: rules and messages that real-project verifiers tripped on."""
import json

from test_workorder import PYTHON, cli, git, local_repo, ok  # noqa: F401  (fixture)
from test_v05 import commit_all
from test_v18g import CODE, DOUBLE, TASK, landed

# Passes on the base code and with double() stubbed out: done refuses it.
WEAK = 'from calc import value\n\ndef test_double():\n    assert value() == 1\n'
UNFINISHED = ("TASK-1's code lands unfinished without done's evidence (receipts recorded by hand show only "
              "that its test passed); an approving review clears this, or a reviewer accepts it with the "
              "label workorder-allow:TASK-1")
RECORDED_RUN = "its recorded test run passed; done has not shown the test proves the change"
PR_CHECK = ('check', '--base', 'origin/main', '--require-task')


def hand_receipts(root):
    """The verifiers' route around a refused done: receipts recorded by hand, then committed."""
    landed(root, test=WEAK)
    refused = cli(root, 'done', '--task', 'TASK-1')
    assert refused.returncode == 1
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    recorded = ok(root, 'receipt', 'test', '--task', 'TASK-1')
    commit_all(root)
    return refused.stdout, recorded


# 1: an unfinished task's hand receipts do not land its code without a review

def test_unfinished_hand_receipts_need_a_review_or_label(local_repo):
    root = local_repo
    hand_receipts(root)
    agent = cli(root, *PR_CHECK)
    assert agent.returncode == 1, agent.stdout
    assert UNFINISHED in agent.stdout
    reviewed = ok(root, *PR_CHECK, '--approving-review')
    assert UNFINISHED + ' (cleared by the approving review)' in reviewed
    assert 'accepted by an approving review: TASK-1 (unfinished work)' in reviewed
    labelled = ok(root, *PR_CHECK, '--allow', 'TASK-1')
    assert UNFINISHED + ' (allowed)' in labelled


def test_done_records_satisfy_the_unfinished_rule_as_before(local_repo):
    root = local_repo
    hand_receipts(root)
    ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    commit_all(root)
    pending = cli(root, *PR_CHECK)
    assert "lands unfinished" not in pending.stdout
    assert 'workorder-allow:TASK-1' in pending.stdout  # the review the --needs-review record asks for


def test_finished_task_passes_the_pr_check_without_the_unfinished_finding(local_repo):
    root = local_repo
    landed(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    output = ok(root, *PR_CHECK)
    assert 'lands unfinished' not in output


# 2: show and receipt test say what the recorded run shows

def test_hand_receipts_do_not_claim_the_test_evidence_is_in_place(local_repo):
    root = local_repo
    refused, recorded = hand_receipts(root)
    shown = ok(root, 'show', '--task', 'TASK-1')
    for output in (recorded, shown):
        assert 'evidence for its test is in place' not in output
        assert 'test evidence is in place' not in output
        assert f'still owes: its Acceptance items, then workorder done ({RECORDED_RUN})' in output
    assert shown.splitlines()[0] == f'TASK-1 IN_PROGRESS (not claimed finished); {RECORDED_RUN}'


# 3: the advice names done first, and both receipts in order

def test_no_task_advice_names_done_and_the_receipt_order(local_repo):
    root = local_repo
    landed(root)
    result = cli(root, *PR_CHECK)
    assert result.returncode == 1
    line = next(line for line in result.stdout.splitlines() if 'no task records it' in line)
    assert 'run workorder done --task ID (or with --needs-review)' in line
    assert 'first run workorder receipt implemented --task ID, then workorder receipt test --task ID' in line
    assert 'implemented --task ID or workorder receipt test' not in line


# 4: adding the first Workorder workflow is setup, not weakening

def test_first_workflow_needs_no_label_but_changes_and_removals_do(local_repo):
    root = local_repo
    base = git(root, 'rev-parse', 'HEAD')
    workflow = root / '.github/workflows/workorder.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text('name: Workorder\n')
    added = commit_all(root, 'Set up Workorder')
    output = ok(root, 'check', '--base', base)
    assert ('.github/workflows/workorder.yml: Workorder workflow added; none existed on '
            f'{base}, so no label is needed') in output
    assert 'Workorder workflow changed' not in output
    workflow.write_text('name: Workorder (weaker)\n')
    commit_all(root, 'Change it')
    changed = cli(root, 'check', '--base', added)
    assert changed.returncode == 1 and 'Workorder workflow changed' in changed.stdout
    git(root, 'rm', '-q', '.github/workflows/workorder.yml')
    commit_all(root, 'Remove it')
    removed = cli(root, 'check', '--base', added, '--approving-review')
    assert removed.returncode == 1 and 'Workorder workflow changed' in removed.stdout


def test_a_second_workflow_running_the_label_step_still_needs_the_label(local_repo):
    root = local_repo
    workflow = root / '.github/workflows/workorder.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text('name: Workorder\n')
    base = commit_all(root, 'Set up Workorder')
    (root / '.github/workflows/other.yml').write_text('run: python -m workorder.review_labels\n')
    commit_all(root, 'Another one')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and 'other.yml: Workorder workflow changed' in result.stdout


# 5: the help states the review rule

def test_check_help_states_the_approving_review_rule(local_repo):
    text = ' '.join(ok(local_repo, 'check', '--help').split())
    assert ('an approving review clears every finding except edits to existing tests and Workorder workflow '
            'changes, which need labels') in text
    assert 'old-test failures' not in text and 'test-first exceptions' not in text
    assert 'workflow for Workorder workflow changes' in text


# 6: done names the proof, refusing and succeeding

def test_shortcut_refusal_names_the_base_and_the_stub_facts(local_repo):
    root = local_repo
    landed(root, test=WEAK)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1
    assert "pass on origin/main's code too, so it does not show the change works" in result.stdout
    assert 'still pass with the changed functions stubbed out' in result.stdout


def test_done_success_states_the_proof_once(local_repo):
    root = local_repo
    landed(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert ("  Evidence: verified: passes now, failed on origin/main's code, failed with the change stubbed out"
            in output.splitlines())
    assert 'the declared test passed on this code' not in output
    assert 'TASK-1 DONE: evidence verified' in output.splitlines()


# 7: wording

def test_done_and_reused_results_say_who_ran_the_test(local_repo):
    root = local_repo
    landed(root)
    done = ok(root, 'done', '--task', 'TASK-1')
    assert 'Note: TASK-1: its test passed when done ran it' in done
    assert 'when this check ran it' not in done
    git(root, 'add', '-A')
    staged = ok(root, 'check', '--staged')
    assert '(result reused from the run at' in staged
    assert 'Note: TASK-1: its test passed in the reused result (not run again)' in staged
    assert 'when this check ran it' not in staged


def test_unfinished_missing_test_file_is_one_note(local_repo):
    root = local_repo
    landed(root)
    git(root, 'rm', '-q', 'tests/test_double.py')
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    commit_all(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Note: TASK-1: its test file is not there yet; an unfinished task may land' in result.stdout
    assert 'Failure:' not in result.stdout
    assert 'file or directory not found' not in result.stdout + result.stderr


def test_silent_failing_project_test_has_no_empty_failure_block(local_repo):
    root = local_repo
    (root / '.workorder').mkdir()
    command = f'{PYTHON} -c "import sys; sys.exit(3)"'
    (root / '.workorder/config.json').write_text(json.dumps({'project_test': command}) + '\n')
    base = commit_all(root)
    (root / 'app.py').write_text('items = [1]\n')
    commit_all(root)
    result = cli(root, 'check', '--base', base, '--run-tests')
    assert result.returncode == 1
    assert 'the project test fails on this code (exit 3)' in result.stdout
    assert 'Failure:' not in result.stdout


def test_skipped_comparison_note_names_the_base_not_ref(local_repo):
    root = local_repo
    hand_receipts(root)
    output = cli(root, 'check', '--base', 'origin/main').stdout
    assert 'TASK-1: comparison with origin/main skipped: use --run-tests to run it' in output
    assert 'REF' not in output


def test_approving_review_note_does_not_claim_an_allow(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'calc.py').write_text(CODE)
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-qc', 'feature')
    (root / 'tasks').mkdir()
    (root / 'tests').mkdir()
    (root / 'tasks/TASK-1.md').write_text(TASK.format(acceptance='- double(2) is 4'))
    (root / 'calc.py').write_text(CODE + 'def double(x):\n    return 2 * x\n')
    (root / 'tests/test_double.py').write_text(DOUBLE)
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    reviewed = ok(root, 'check', '--base', base, '--approving-review')
    assert 'TASK-1 was written and finished in this pull request' in reviewed
    assert '(definition allowed)' not in reviewed and '(cleared by the approving review)' in reviewed
    assert '(definition allowed)' in ok(root, 'check', '--base', base, '--allow', 'TASK-1')


def test_a_failing_check_ends_with_a_verdict(local_repo):
    root = local_repo
    hand_receipts(root)
    result = cli(root, *PR_CHECK)
    assert result.returncode == 1
    assert result.stdout.splitlines()[-1] == 'check: NOT OK (1 finding)'
    assert 'check: OK' not in result.stdout


def test_review_warn_makes_the_unfinished_finding_a_warning(local_repo):
    root = local_repo
    hand_receipts(root)
    warned = cli(root, *PR_CHECK, '--review', 'warn')
    assert warned.returncode == 0, warned.stdout
    assert 'Warning: ' + UNFINISHED in warned.stdout
