"""Many-task reads share work without losing freshness or per-task refusals."""
import pytest

from workorder import gate, git_reads, store
from test_workorder import PYTHON, git, local_repo, task
from test_v05 import commit_all


@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.usefixtures('observable_git_environment')
def test_task_inventory_validates_once_per_read(local_repo, monkeypatch, staged):
    """Reading N records must not inspect the entire repository N times."""
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    for n in range(1, 41):
        task(local_repo, f'TASK-{n}')
    git(local_repo, 'add', 'tasks')
    calls = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        calls.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            records = gate.records(local_repo, staged, errors={})
            assert set(records) == {f'TASK-{n}' for n in range(1, 41)}
            assert all(r['status'] == 'IN_PROGRESS' for r in records.values())
    assert len(calls) == 2


@pytest.mark.parametrize('helper', ['changed_test_paths', 'changed_existing_test_paths',
                                    'test_origin', 'test_file_changes', 'assertion_problems'])
@pytest.mark.usefixtures('observable_git_environment')
def test_compound_review_validates_once_per_read(local_repo, monkeypatch, helper):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    probe = root / 'test_value.py'
    probe.write_text('def test_value():\n    assert int(7) > 0\n')
    task(root, test=f'{PYTHON} -m pytest test_value.py')
    base = commit_all(root)
    probe.write_text('def test_value():\n    assert True\n')
    record = store.record(root, 'TASK-1')
    calls = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        calls.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    args = ((root, record, base, 'main') if helper == 'test_origin' else
            (root, base, {'test_value.py'}) if helper.startswith('changed_') else
            (root, base, record))
    with store.read_session():
        for _ in range(2):
            result = getattr(gate, helper)(*args)
            if helper.startswith('changed_'):
                assert result == {'test_value.py'}
            elif helper == 'test_origin':
                assert result == (False, "TASK-1's test file was edited in this change (test_value.py)")
            elif helper == 'assertion_problems':
                assert len(result) == 1
                assert type(result[0]) is gate.AssertionProblem
                assert result[0].path == 'test_value.py'
                assert result[0].line == 1
                assert not result[0].label_only  # 0.18: an approving review clears it
                result[0].path = 'caller annotation'
                result[0].line = 99
                result[0].label_only = False
            else:
                assert 'lost or changed' in result
    assert len(calls) == 2


@pytest.mark.parametrize('cache', ['0', '1'])
@pytest.mark.parametrize('staged', [False, True])
def test_inventory_keeps_errors_copies_and_external_changes(local_repo, monkeypatch, cache, staged):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', cache)
    root = local_repo
    task(root, 'TASK-1')
    bad = task(root, 'TASK-2')
    bad.write_text('broken task\n')
    git(root, 'add', 'tasks')
    with store.read_session():
        errors = {'prior': 'keep'}
        first = gate.records(root, staged, errors)
        assert set(first) == {'TASK-1'}
        assert set(errors) == {'prior', 'TASK-2'}
        first['TASK-1']['waits_on'].append('person:unrelated')
        again_errors = {}
        assert gate.records(root, staged, again_errors)['TASK-1']['waits_on'] == []
        assert again_errors == {'TASK-2': errors['TASK-2']}
        with pytest.raises(store.Refused):
            gate.records(root, staged)
        task(root, 'TASK-2', status='BLOCKED')
        if staged:
            git(root, 'add', 'tasks')
        fresh_errors = {}
        assert gate.records(root, staged, fresh_errors)['TASK-2']['status'] == 'BLOCKED'
        assert fresh_errors == {}


@pytest.mark.parametrize('cache', ['0', '1'])
@pytest.mark.parametrize('staged', [False, True])
def test_review_reads_observe_external_edits(local_repo, monkeypatch, cache, staged):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', cache)
    root = local_repo
    probe = root / 'test_value.py'
    original = 'def test_value():\n    assert int(7) > 0\n'
    probe.write_text(original)
    task(root, test=f'{PYTHON} -m pytest test_value.py')
    base = commit_all(root)
    record = store.record(root, 'TASK-1')
    with store.read_session():
        for text, changed in [(original, False), ('def test_value():\n    assert True\n', True),
                              (original, False)]:
            probe.write_text(text)
            if staged:
                git(root, 'add', 'test_value.py')
            paths = {'test_value.py'} if changed else set()
            assert gate.changed_test_paths(root, base, {'test_value.py'}, staged) == paths
            assert gate.changed_existing_test_paths(root, base, {'test_value.py'}, staged) == paths
            assert gate.test_origin(root, record, base, 'main', staged)[0] is not changed
            assert bool(gate.test_file_changes(root, base, record, staged)) is changed
            problems = gate.assertion_problems(root, base, record, staged)
            assert bool(problems) is changed
            if problems:
                assert problems[0].path == 'test_value.py'
                assert not problems[0].label_only


@pytest.mark.parametrize('helper', ['base_problems', 'stale_claim', 'pending_review', 'local_test_update'])
@pytest.mark.usefixtures('observable_git_environment')
def test_evidence_reviews_validate_once_per_read(local_repo, monkeypatch, helper):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    for n in range(1, 5):
        task(root, f'TASK-{n}', ships='none')
    commit_all(root)
    for n in range(1, 5):
        tid = f'TASK-{n}'
        receipt = store.receipt(root, tid, 'implemented')
        store.append(root, tid, dict(kind='test', commit=receipt['commit'], code=receipt['code'],
                                    command=[PYTHON, '-c', 'pass'], exit_code=0, valid=True))
        store.set_task_field(root, tid, 'status', 'DONE')
    base = commit_all(root)
    record = store.record(root, 'TASK-1')
    calls = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        calls.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            if helper == 'base_problems':
                assert gate.base_problems(root, base) == []
            elif helper == 'stale_claim':
                assert gate.stale_claim(root, record, base=base) is None
            elif helper == 'pending_review':
                assert gate.pending_review(root, record, base) is False
            else:
                assert gate.local_test_update(root, record) == (None, None)
    assert len(calls) == 2


@pytest.mark.parametrize('helper', ['task_code_paths', 'task_change_touched', 'requirement_changes'])
@pytest.mark.usefixtures('observable_git_environment')
def test_task_change_reads_validate_once(local_repo, monkeypatch, helper):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    task(root, test=f'{PYTHON} -c pass')
    base = commit_all(root)
    record = store.record(root, 'TASK-1')
    (root / 'app.py').write_text('items = [1]\n')
    calls = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        calls.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            if helper == 'task_code_paths':
                assert gate.task_code_paths(root, record, base) == {'app.py'}
            elif helper == 'task_change_touched':
                assert gate.task_change_touched(root, base, record) is True
            else:
                assert gate.requirement_changes(root, base, record) == []
    assert len(calls) == 2


@pytest.mark.usefixtures('observable_git_environment')
def test_task_review_preparation_validates_as_one_read(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    probe = root / 'test_value.py'
    probe.write_text('def test_value():\n    assert int(7) > 0\n')
    task(root, test=f'{PYTHON} -m pytest test_value.py')
    base = commit_all(root)
    probe.write_text('def test_value():\n    assert True\n')
    record = store.record(root, 'TASK-1')
    calls = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        calls.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        notes = []
        problems = gate.test_change_problems(root, base, record, run_tests=False, notes=notes)
        assert any('asserts nothing that can fail' in str(problem) for problem in problems)
        assert any('REF comparison skipped' in note for note in notes)
    assert len(calls) == 1


@pytest.mark.parametrize('cache', ['0', '1'])
def test_local_test_updates_run_again_and_keep_failures(local_repo, tmp_path, monkeypatch, cache):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', cache)
    root = local_repo
    counter, failing = tmp_path / 'runs', tmp_path / 'fail'
    probe = root / 'test_value.py'
    body = (f'from pathlib import Path\ndef test_value():\n'
            f'    with Path({str(counter)!r}).open("a") as stream: stream.write("run\\n")\n'
            f'    assert not Path({str(failing)!r}).exists()\n'
            '    assert int(7) > 0\n')
    probe.write_text(body)
    command = [PYTHON, '-m', 'pytest', '-q', 'test_value.py']
    task(root, ships='none', test=store.join_command(command))
    commit_all(root)
    receipt = store.receipt(root, 'TASK-1', 'implemented')
    store.append(root, 'TASK-1', dict(kind='test', commit=receipt['commit'], code=receipt['code'],
                                   command=command, exit_code=0, valid=True))
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root)
    probe.write_text(body.replace('> 0', '>= 0'))
    record = store.record(root, 'TASK-1')
    counts = {}
    with store.read_session():
        for _ in range(2):
            note, failure = gate.local_test_update(root, record, counts=counts)
            assert note and failure is None
        failing.touch()
        note, failure = gate.local_test_update(root, record, counts=counts)
        assert note is None and failure
    assert counter.read_text().splitlines() == ['run', 'run', 'run']
    assert counts['tests'] == 3


@pytest.mark.parametrize('cache', ['0', '1', 'unobservable'])
def test_read_scope_refreshes_after_test_command(local_repo, monkeypatch, cache):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', cache)
    if cache == 'unobservable':
        monkeypatch.setattr(git_reads, 'repository_state', lambda *a, **kw: None)
    command = ('from pathlib import Path; import subprocess; '
               'Path("app.py").write_text("items = [2]\\n"); '
               'subprocess.run(["git", "add", "app.py"], check=True)')
    with store.read_session(), store.read_scope(local_repo):
        assert store.blob_bytes(local_repo, ':app.py') == b'items = []\n'
        assert store.run_test([PYTHON, '-c', command], local_repo).returncode == 0
        assert store.blob_bytes(local_repo, ':app.py') == b'items = [2]\n'


@pytest.mark.usefixtures('observable_git_environment')
def test_local_regression_selection_validates_once_for_all_tasks(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    for n in range(1, 41):
        task(root, f'TASK-{n}', status='DONE', ships='none')
    commit_all(root)
    calls = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        calls.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        counts = {}
        assert gate.local_regression_problems(root, 'HEAD', counts=counts) == []
        assert counts.get('tests', 0) == 0
    assert len(calls) == 1


@pytest.mark.usefixtures('observable_git_environment')
def test_hook_preparation_has_one_validation_per_task(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    for n in range(1, 41):
        task(root, f'TASK-{n}', ships='none')
    commit_all(root)
    (root / 'NOTES.md').write_text('notes\n')
    git(root, 'add', 'NOTES.md')
    calls = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        calls.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    counts = {}
    assert gate.check(root, staged=True, counts=counts) == []
    assert counts['tasks'] == 0
    # One read per task plus bounded command-wide preparation and reporting.
    assert len(calls) <= 60
