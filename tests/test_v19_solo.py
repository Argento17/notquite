"""Solo mode: the owner's own labels are owner acceptance, never review; service responses are recorded."""

import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from workorder import bootstrap, review_labels, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v09_setup import check_steps


SOLO = json.loads((Path(__file__).parent / 'fixtures/solo-label-events.json').read_text())
OWNER = 'accepted by the owner (solo mode; no independent review)'


@pytest.mark.parametrize('case', SOLO['cases'], ids=lambda case: case['name'])
def test_solo_labels_recorded_events(case):
    event = {'pull_request': SOLO['pull_request'], 'repository': case['repository']}
    owner = review_labels.verify_owner(event, case['permission'])
    allowed, accepted, notices = review_labels.solo_labels(SOLO['pull_request'], SOLO['pages'], owner)
    assert allowed == case['allowed']
    assert accepted == case['owner']
    assert notices == case['notices']


def test_without_solo_mode_the_owner_labels_stay_ignored():
    # The strict rule is unchanged even when the author owns the repository.
    allowed, notices = review_labels.accepted_labels(SOLO['pull_request'], SOLO['pages'])
    assert allowed == ['TASK-3', 'tests']
    assert notices == ['ignored workorder-allow:TASK-2: added by the pull request author',
                       'ignored workorder-allow:no-task: added by the pull request author']


def run_workflow(root, tmp_path, case, permission_fails=False):
    generated = (root / '.github/workflows/workorder.yml').read_text()
    step, command = next((step, command) for step, command in check_steps(generated) if '--base' in command)
    assert 'PR_AUTHOR: ${{ github.event.pull_request.user.login }}' in step
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': SOLO['pull_request'], 'repository': case['repository']}))
    pages = tmp_path / 'pages.json'
    pages.write_text(json.dumps(SOLO['pages']))
    permission = tmp_path / 'permission-answer.json'
    permission.write_text(json.dumps(case['permission']))
    binary = tmp_path / 'bin'
    binary.mkdir(exist_ok=True)
    gh = binary / 'gh'
    # All service traffic is replaced by recorded answers; unexpected API calls fail.
    gh.write_text(f'#!{PYTHON}\nimport json, sys\n'
                  'if sys.argv[1:] == ["api", "repos/example/project/collaborators/author/permission"]:\n'
                  f'    if {permission_fails!r}:\n'
                  '        print(json.dumps({"message": "Resource not accessible by integration"}))\n'
                  '        sys.exit(1)\n'
                  f'    print(open({str(permission)!r}).read())\n'
                  '    sys.exit(0)\n'
                  'assert sys.argv[1:4] == ["api", "--paginate", "--slurp"], sys.argv\n'
                  'assert sys.argv[4] in ["repos/example/project/issues/7/events", '
                  '"repos/example/project/pulls/7/reviews"], sys.argv\n'
                  f'print(open({str(pages)!r}).read() if sys.argv[4].endswith("events") else "[[]]")\n')
    gh.chmod(0o755)
    runner = binary / 'workorder'
    runner.write_text(f'#!{PYTHON}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
    runner.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + str(Path(PYTHON).parent) + os.pathsep + os.environ['PATH'],
           'PYTHONPATH': str(PACKAGE), 'GITHUB_EVENT_PATH': str(event), 'RUNNER_TEMP': str(tmp_path),
           'GH_REPO': 'example/project', 'PR_NUMBER': '7', 'PR_AUTHOR': 'author', 'BASE': 'origin/main'}
    result = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout), result.stderr


CASES = {case['name']: case for case in SOLO['cases']}


@pytest.mark.skipif(os.name == 'nt', reason='executes the generated POSIX workflow')
@pytest.mark.parametrize('name, fails', [('owner', False), ('admin of an organization repository', False),
                                         ('non-owner author', False), ('unverifiable permission', True)])
def test_generated_solo_workflow_passes_owner_labels_separately(local_repo, tmp_path, name, fails):
    root = local_repo
    ok(root, 'init', '--solo')
    args, errors = run_workflow(root, tmp_path, CASES[name], permission_fails=fails)
    expected = ['check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task',
                '--allow', 'TASK-3,tests']
    if CASES[name]['owner']:
        expected += ['--owner-accepts', 'TASK-2,no-task']
    assert args == expected, args
    for notice in CASES[name]['notices']:
        assert notice in errors


def test_init_solo_records_mode_states_the_limit_and_strict_init_restores(local_repo):
    root = local_repo
    output = ok(root, 'init', '--solo')
    config = json.loads((root / '.workorder/config.json').read_text())
    assert config['solo'] is True
    assert 'the only human step' in output
    assert 'token without permission to label pull requests' in output
    workflow = root / '.github/workflows/workorder.yml'
    generated = workflow.read_text()
    assert 'review_labels --solo' in generated
    assert 'collaborators/$PR_AUTHOR/permission' in generated
    before = workflow.read_bytes()
    ok(root, 'init', '--solo', '--check', '--update-workflow')
    assert workflow.read_bytes() == before
    # Without --solo nothing about solo mode is kept: the strict workflow comes back and the record goes.
    output = ok(root, 'init', '--update-workflow')
    assert 'solo mode turned off' in output
    assert 'solo' not in json.loads((root / '.workorder/config.json').read_text())
    assert '--solo' not in workflow.read_text()


def test_init_without_solo_writes_no_solo_record(local_repo):
    root = local_repo
    ok(root, 'init')
    assert 'solo' not in (root / '.github/workflows/workorder.yml').read_text()
    config = root / '.workorder/config.json'
    assert not config.exists() or 'solo' not in json.loads(config.read_text())


@pytest.mark.parametrize('args, message', [
    (['--solo', '--light'], '--solo and --light'),
    (['--solo'], 'init --update-workflow --solo'),
])
def test_init_solo_refusals(local_repo, args, message):
    root = local_repo
    if args == ['--solo']:
        ok(root, 'init')  # an existing workflow is rewritten only with --update-workflow
    result = cli(root, 'init', *args)
    assert result.returncode == 1 and message in result.stderr, result.stdout + result.stderr


def accepted_change(root, solo=True):
    if solo:
        (root / '.workorder').mkdir(exist_ok=True)
        (root / '.workorder/config.json').write_text(json.dumps({'solo': True}) + '\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_calc.py').write_text('def test_calc():\n    assert 1 == 1\n')
    base = commit_all(root)
    fill_sections(task(root, 'TASK-3', ships='none', test=store.join_command([PYTHON, 'tests/test_calc.py'])))
    (root / 'tests/test_calc.py').write_text('def test_calc():\n    assert 2 == 2\n')
    commit_all(root)
    store.receipt(root, 'TASK-3', 'implemented')
    store.receipt(root, 'TASK-3', 'test', [PYTHON, 'tests/test_calc.py'])
    store.set_task_field(root, 'TASK-3', 'status', 'DONE')
    return base


def test_owner_acceptance_is_printed_as_owner_acceptance_never_review(local_repo):
    root = local_repo
    base = accepted_change(root)
    refused = cli(root, 'check', '--base', base)
    assert refused.returncode == 1
    output = ok(root, 'check', '--base', base, '--owner-accepts', 'TASK-3,tests,no-task')
    assert f'{OWNER}: TASK-3 (new task), tests/test_calc.py (changed lines)' in output
    assert 'approving review:' not in output and 'accepted by review' not in output
    summary = output.strip().splitlines()[-1]
    assert 'solo mode' in summary, output
    # Nothing disappears: the owner's labels accept exactly what the same labels from a reviewer would,
    # line for line, and only the wording of the acceptance differs.
    reviewed = ok(root, 'check', '--base', base, '--allow', 'TASK-3,tests,no-task')
    assert OWNER not in reviewed
    owner_lines = [line for line in output.splitlines() if not line.startswith(f'Note: {OWNER}')]
    assert [line.replace(' (accepted by the owner in solo mode; no independent review)', '')
            for line in owner_lines] == [re.sub(r' \((?:definition )?allowed\)$', '', line)
                                         for line in reviewed.splitlines()]
    assert output.count('(accepted by the owner in solo mode; no independent review)') == 3


def test_reviewer_label_stays_review_beside_owner_labels(local_repo):
    root = local_repo
    base = accepted_change(root)
    output = ok(root, 'check', '--base', base, '--allow', 'TASK-3', '--owner-accepts', 'tests,no-task')
    owner_line = next((line for line in output.splitlines() if OWNER in line), '')
    assert 'TASK-3 (new task)' not in owner_line
    # The changed test lines rest on a reviewer's TASK-3 label as well, so they are not owner-only.
    assert 'tests/test_calc.py (changed lines)' not in owner_line


@pytest.mark.parametrize('turned_on_here', [False, True])
def test_owner_labels_need_solo_mode_on_the_base_and_a_base(local_repo, turned_on_here):
    root = local_repo
    base = accepted_change(root, solo=False)
    if turned_on_here:
        # A change cannot turn solo mode on for itself: the base's record decides.
        (root / '.workorder/config.json').write_text(json.dumps({'solo': True}) + '\n')
        commit_all(root)
    result = cli(root, 'check', '--base', base, '--owner-accepts', 'TASK-3,tests,no-task')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'owner labels not used (TASK-3, no-task, tests): solo mode is not recorded' in result.stdout
    assert 'workorder-allow:TASK-3' in result.stdout and OWNER not in result.stdout
    assert 'solo mode:' not in result.stdout.strip().splitlines()[-2]  # the summary, above the verdict line
    result = cli(root, 'check', '--owner-accepts', 'TASK-3')
    assert result.returncode == 1 and '--owner-accepts' in result.stderr


def test_without_solo_mode_the_summary_does_not_mention_it(local_repo):
    root = local_repo
    base = accepted_change(root, solo=False)
    output = ok(root, 'check', '--base', base, '--allow', 'TASK-3,tests,no-task')
    assert 'solo mode' not in output


def test_owner_label_needs_the_confirmed_account_not_its_login():
    # Another account now using the owner's login added the label: nothing is accepted.
    request = {'user': {'login': 'author', 'id': 10}, 'labels': [{'name': 'workorder-allow:TASK-2'}]}
    pages = [[{'event': 'labeled', 'label': {'name': 'workorder-allow:TASK-2'}, 'actor': {'login': 'Author', 'id': 13}}]]
    event = {'pull_request': request, 'repository': {'owner': {'login': 'author', 'id': 10}}}
    allowed, accepted, notices = review_labels.solo_labels(request, pages, review_labels.verify_owner(event, None))
    assert (allowed, accepted) == ([], [])
    assert notices == ['ignored workorder-allow:TASK-2: added by the pull request author, '
                       'whose owner or admin permission could not be verified (solo mode)']


def test_owner_workflow_label_is_label_only_as_before(local_repo):
    root = local_repo
    (root / '.workorder').mkdir()
    (root / '.workorder/config.json').write_text(json.dumps({'solo': True}) + '\n')
    base = commit_all(root)
    workflow = root / '.github/workflows/workorder.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text('name: Workorder\n')
    base = commit_all(root)  # an existing workflow: adding the first one needs no label
    workflow.write_text('name: Workorder changed\n')
    commit_all(root)
    refused = cli(root, 'check', '--base', base, '--approving-review')
    assert refused.returncode == 1 and 'Workorder workflow changed' in refused.stdout
    output = ok(root, 'check', '--base', base, '--owner-accepts', 'workflow')
    assert 'Workorder workflow changed' in output
    assert 'workorder-allow:workflow (an approving review is not enough: it changes the Workorder workflow)' \
        ' (accepted by the owner in solo mode; no independent review)' in output
    assert f'{OWNER}: workflow' in output


def test_solo_mode_keeps_another_persons_approval(tmp_path):
    # An approving review by someone else still counts in solo mode, beside the owner's labels.
    files = []
    approval = [[{'user': {'login': 'reviewer', 'id': 20}, 'commit_id': 'head', 'state': 'APPROVED'}]]
    for name, data in [('permission', None), ('event', {'pull_request': SOLO['pull_request'],
                                                        'repository': CASES['owner']['repository']}),
                       ('labels', SOLO['pages']), ('reviews', approval)]:
        path = tmp_path / (name + '.json')
        path.write_text('not json' if data is None else json.dumps(data))
        files.append(str(path))
    result = subprocess.run([PYTHON, '-m', 'workorder.review_labels', '--solo', *files, 'origin/main'],
                            env={**os.environ, 'PYTHONPATH': str(PACKAGE)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['--allow', 'TASK-3,tests', '--owner-accepts', 'TASK-2,no-task',
                                     '--approving-review', '--approved-by', 'reviewer']


def test_owner_notes_read_the_finding_owner_when_no_label_is_cited():
    from workorder import gate
    notes = ['tests: test_x in tests/test_x.py asserts nothing that can fail (allowed)',
             'TASK-3, TASK-4: test_y in tests/test_y.py asserts nothing that can fail (allowed)',
             'tests/test_z.py: something (allowed)']
    rewritten, items = gate.owner_notes(notes, {'tests', 'TASK-3'})
    assert rewritten == ['tests: test_x in tests/test_x.py asserts nothing that can fail' + gate.OWNER_SUFFIX,
                         notes[1], notes[2]]
    assert items == ['tests']
