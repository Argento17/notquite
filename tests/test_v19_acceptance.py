"""Lane ACC: test evidence and task acceptance are separate states."""
import datetime as dt
import json
import os
import shlex
import subprocess
from pathlib import Path

from test_workorder import PACKAGE, PYTHON, git, local_repo  # noqa: F401  (fixture)
from test_v05 import commit_all
from test_v18g import landed

TWO_ITEMS = '- double(2) is 4 (test: tests/test_double.py::test_double)\n- the docs mention it'


def run(root, *args, agent=None):
    env = {key: value for key, value in os.environ.items() if key != 'WORKORDER_AGENT'}
    env.update(PYTHONPATH=str(PACKAGE), GIT_TERMINAL_PROMPT='0')
    if agent is not None:
        env['WORKORDER_AGENT'] = agent
    return subprocess.run([PYTHON, '-m', 'workorder', *args], cwd=root, env=env,
                          capture_output=True, encoding='utf-8', timeout=300)


def ok(root, *args, agent=None):
    result = run(root, *args, agent=agent)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def finished(root, acceptance=TWO_ITEMS):
    """TASK-1 is defined on origin/main and finished on the feature branch."""
    landed(root, acceptance=acceptance)
    done = ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    return done


def today():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def test_done_show_and_check_name_evidence_and_acceptance_apart(local_repo):
    root = local_repo
    done = finished(root)
    awaiting = ('Acceptance: tied tests pass (1 of 2 items); awaiting a person (1 item); '
                'a person confirms with workorder accept --task TASK-1')
    assert '  Evidence: verified: the declared test passed on this code\n  ' + awaiting in done
    assert 'TASK-1 DONE: its declared test passed on this code' in done
    shown = ok(root, 'show', '--task', 'TASK-1')
    assert '  Claimed:  DONE\n  Evidence: verified: recorded receipts support the claim' in shown
    assert awaiting in shown
    checked = ok(root, 'check', 'TASK-1')
    assert awaiting in checked
    assert ('Evidence: verified for 1 supported claim (its declared test); Acceptance: 1 awaiting a person\n'
            'checked 1 task (1 claimed DONE or CLOSED)') in checked


def test_untied_items_await_a_person(local_repo):
    root = local_repo
    done = finished(root, acceptance='- double(2) is 4\n- the docs mention it')
    assert 'Acceptance: awaiting a person (2 items); a person confirms with' in done


def test_accept_records_a_person_and_refuses_an_agent(local_repo):
    root = local_repo
    landed(root, acceptance=TWO_ITEMS)
    early = run(root, 'accept', '--task', 'TASK-1')
    assert early.returncode == 1 and 'TASK-1 is IN_PROGRESS; acceptance needs it DONE' in early.stderr
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    receipts = sorted((root / '.workorder/receipts/TASK-1').iterdir())
    agent = run(root, 'accept', '--task', 'TASK-1', agent='bot')
    assert agent.returncode == 1
    assert 'TASK-1: an agent cannot accept a task (WORKORDER_AGENT is set)' in agent.stderr
    assert sorted((root / '.workorder/receipts/TASK-1').iterdir()) == receipts
    accepted = ok(root, 'accept', '--task', 'TASK-1', '--note', 'read the docs page')
    assert f'Acceptance: confirmed by solo on {today()}' in accepted
    new = sorted(set((root / '.workorder/receipts/TASK-1').iterdir()) - set(receipts))
    assert len(new) == 1
    item = json.loads(new[0].read_text())
    assert item['kind'] == 'accepted' and item['who'] == 'solo' and item['note'] == 'read the docs page'
    commit_all(root)
    shown = ok(root, 'show', '--task', 'TASK-1')
    assert f'  Acceptance: confirmed by solo on {today()}\n' in shown
    assert 'accepted on ' in shown and ': read the docs page  (solo)' in shown
    assert 'Acceptance: 1 confirmed by a person' in ok(root, 'check', '--require-acceptance')
    # The receipt is chained and hashed like the others: an edited note is refused.
    new[0].write_text(new[0].read_text().replace('read the docs page', 'checked nothing'))
    edited = run(root, 'show', '--task', 'TASK-1')
    assert edited.returncode == 1 and 'receipt' in edited.stderr


def test_receipt_command_cannot_write_an_acceptance(local_repo):
    root = local_repo
    finished(root)
    result = run(root, 'receipt', 'accepted', '--task', 'TASK-1')
    assert result.returncode == 2 and "invalid choice: 'accepted'" in result.stderr


def test_a_later_code_change_makes_acceptance_stale(local_repo):
    root = local_repo
    finished(root)
    ok(root, 'accept', '--task', 'TASK-1')
    commit_all(root)
    (root / 'calc.py').write_text((root / 'calc.py').read_text().replace('2 * x', 'x + x'))
    commit_all(root)
    change = git(root, 'rev-parse', '--short=7', 'HEAD').strip()
    shown = ok(root, 'show', '--task', 'TASK-1')
    assert (f'Acceptance: stale since {change} (confirmed by solo on {today()}; the task\'s code changed after it);'
            ' a person accepts again with workorder accept --task TASK-1') in shown
    again = run(root, 'accept', '--task', 'TASK-1')
    assert again.returncode == 1 and 'its code changed after the tested commit' in again.stderr
    refused = run(root, 'check', '--require-acceptance')
    assert refused.returncode == 1
    assert 'TASK-1: Evidence: verified; Acceptance: stale since' in refused.stdout
    ok(root, 'renew', '--task', 'TASK-1')
    commit_all(root)
    assert f'Acceptance: stale since {change}' in ok(root, 'check', 'TASK-1')
    ok(root, 'accept', '--task', 'TASK-1')
    commit_all(root)
    assert f'Acceptance: confirmed by solo on {today()}' in ok(root, 'check', 'TASK-1', '--require-acceptance')


def test_unrelated_code_change_keeps_acceptance(local_repo):
    root = local_repo
    finished(root)
    ok(root, 'accept', '--task', 'TASK-1')
    commit_all(root)
    (root / 'other.py').write_text('x = 1\n')
    commit_all(root)
    assert f'Acceptance: confirmed by solo on {today()}' in ok(root, 'show', '--task', 'TASK-1')
    # Renewing the same task code (as after a squash merge) keeps the acceptance.
    ok(root, 'renew', '--task', 'TASK-1')
    commit_all(root)
    assert f'Acceptance: confirmed by solo on {today()}' in ok(root, 'check', 'TASK-1')


def test_pull_request_check_notes_acceptance_and_review_confirms_it(local_repo):
    root = local_repo
    finished(root)
    base = ['check', '--base', 'origin/main', '--run-tests']
    plain = ok(root, *base)
    assert ('Note: TASK-1: Evidence: verified; Acceptance: tied tests pass (1 of 2 items); '
            'awaiting a person (1 item)\n') in plain
    assert 'Acceptance: 1 awaiting a person' in plain
    refused = run(root, *base, '--require-acceptance')
    assert refused.returncode == 1
    assert ('TASK-1: Evidence: verified; Acceptance: tied tests pass (1 of 2 items); awaiting a person (1 item); '
            '--require-acceptance needs a person: workorder accept --task TASK-1, or an approving review') in refused.stdout
    reviewed = ok(root, *base, '--require-acceptance', '--approving-review', '--approved-by', 'bob')
    assert 'Note: TASK-1: Evidence: verified; Acceptance: confirmed in review by bob' in reviewed
    assert 'Acceptance: 1 confirmed by a person' in reviewed
    wrong = run(root, *base, '--approved-by', 'bob')
    assert wrong.returncode == 1 and '--approved-by goes with --approving-review' in wrong.stderr


def test_acceptance_recorded_in_the_pull_request_needs_a_review_when_required(local_repo):
    root = local_repo
    finished(root)
    ok(root, 'accept', '--task', 'TASK-1')
    commit_all(root)
    base = ['check', '--base', 'origin/main', '--run-tests']
    noted = ok(root, *base)
    assert f'Acceptance: confirmed by solo on {today()} (recorded in this change)' in noted
    refused = run(root, *base, '--require-acceptance')
    assert refused.returncode == 1
    assert ('(recorded in this change); --require-acceptance needs an approving review here: '
            'Workorder cannot tell who wrote a receipt file') in refused.stdout
    ok(root, *base, '--require-acceptance', '--approving-review', '--approved-by', 'bob')


def test_init_require_acceptance_writes_the_workflow_option(local_repo):
    root = local_repo
    ok(root, 'init')
    workflow = root / '.github/workflows/workorder.yml'
    command = next(line for line in workflow.read_text().splitlines() if 'workorder check --base' in line)
    assert '--require-acceptance' not in command
    ok(root, 'init', '--update-workflow', '--require-acceptance')
    command = next(line for line in workflow.read_text().splitlines() if 'workorder check --base' in line)
    assert '--require-acceptance' in shlex.split(command)


def test_review_labels_name_the_approving_reviewers(tmp_path):
    from workorder import review_labels
    pr = {'user': {'login': 'author'}, 'head': {'sha': 'head'}, 'labels': []}
    reviews = [[{'user': {'login': 'carol'}, 'commit_id': 'old', 'state': 'APPROVED'},
                {'user': {'login': 'bob'}, 'commit_id': 'head', 'state': 'APPROVED'},
                {'user': {'login': 'author'}, 'commit_id': 'head', 'state': 'APPROVED'},
                {'user': {'login': 'eve;rm'}, 'commit_id': 'head', 'state': 'APPROVED'}]]
    assert review_labels.approving_reviewers(pr, reviews) == ['bob', 'eve;rm']
    files = []
    for name, data in [('event', {'pull_request': pr}), ('labels', [[]]), ('reviews', reviews)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(data))
        files.append(str(path))
    result = subprocess.run([PYTHON, '-m', 'workorder.review_labels', *files, 'origin/main'],
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    # Only plain GitHub logins reach the unquoted shell word.
    assert shlex.split(result.stdout) == ['--approving-review', '--approved-by', 'bob']
