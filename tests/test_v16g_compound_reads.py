"""Compound reads validate state once without losing input or repository changes."""
import pytest

from workorder import gate, git_reads, store
from test_workorder import PYTHON, git, local_repo, task
from test_v05 import commit_all


@pytest.fixture
def selection_repo(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    (root / 'tests/test_app.py').write_text('def test_app():\n    assert True\n')
    (root / 'checks').mkdir()
    (root / 'checks/example.py').write_text('def test_extra():\n    assert True\n')
    task(root, test=f'{PYTHON} -m pytest tests/test_app.py')
    base = commit_all(root)
    return root, base, store.record(root, 'TASK-1')


@pytest.mark.parametrize('helper', [
    'merge_target', 'source_tree', 'same_source_code',
    'repository_test_files', 'recorded_branch_code', 'comparison_base',
])
def test_compound_read_observes_state_once_per_call(selection_repo, monkeypatch, helper):
    root, base, record = selection_repo
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    implemented = {'commit': base, 'implementation_paths': {
        'app.py': store.code_tree(root, base)['app.py']}}
    calls = {
        'merge_target': ((root, record), ('refs/heads/main', base)),
        'source_tree': ((root, base), {'app.py', 'tests/test_app.py', 'checks/example.py'}),
        'same_source_code': ((root, base, 'HEAD'), True),
        'repository_test_files': ((root, base, {'TASK-1': record}), {'tests/test_app.py'}),
        'recorded_branch_code': ((root, implemented, base, 'HEAD'), True),
        'comparison_base': ((root, 'main'), (base, f'main (merge base {git(root, "rev-parse", "--short", base)})')),
    }
    args, expected = calls[helper]
    inspections = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        inspections.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            result = getattr(gate, helper)(*args)
            assert (set(result) if helper == 'source_tree' else result) == expected
    assert len(inspections) == 2, len(inspections)


@pytest.mark.parametrize('fresh', [False, True])
def test_comparison_base_observes_merge_head_and_target_changes(local_repo, monkeypatch, fresh):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '0' if fresh else '1')
    root = local_repo
    base = store.head(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'feature.py').write_text('feature = True\n')
    feature = commit_all(root)
    git(root, 'checkout', '-q', 'main')
    (root / 'main.py').write_text('main = True\n')
    target = commit_all(root)
    git(root, 'checkout', '-q', 'feature')
    merge_head = root / '.git/MERGE_HEAD'
    with store.read_session():
        assert gate.comparison_base(root, 'main')[0] == base
        merge_head.write_text(target + '\n')
        assert gate.comparison_base(root, 'main')[0] == target
        merge_head.write_text(feature + '\n')
        assert gate.comparison_base(root, 'main')[0] == base
        merge_head.unlink()
        assert gate.comparison_base(root, 'main')[0] == base
        git(root, 'update-ref', 'refs/heads/main', feature)
        assert gate.comparison_base(root, 'main')[0] == feature
        git(root, 'update-ref', '-d', 'refs/heads/main')
        with pytest.raises(store.Refused, match='base main is not in this clone'):
            gate.comparison_base(root, 'main')


def test_merge_target_observes_mutated_override_and_external_ref(selection_repo):
    root, base, record = selection_repo
    (root / 'app.py').write_text('items = [1]\n')
    changed = commit_all(root)
    git(root, 'branch', 'feature', base)
    with store.read_session():
        assert gate.merge_target(root, record) == ('refs/heads/main', changed)
        record['merge_ref'] = 'refs/heads/feature'
        assert gate.merge_target(root, record) == ('refs/heads/feature', base)
        git(root, 'update-ref', 'refs/heads/feature', changed)
        assert gate.merge_target(root, record) == ('refs/heads/feature', changed)


def test_source_tree_preserves_results_and_observes_mutable_protection(selection_repo):
    root, base, _ = selection_repo
    raw = '100644 blob ' + git(root, 'rev-parse', f'{base}:app.py')
    protected = set()
    with store.read_session():
        tree = gate.source_tree(root, base, protected)
        normalized = tree['app.py']
        assert normalized != raw
        tree.clear()
        assert gate.source_tree(root, base, protected)['app.py'] == normalized
        protected.add('app.py')
        assert gate.source_tree(root, base, protected)['app.py'] == raw
        protected.clear()
        assert gate.source_tree(root, base, protected)['app.py'] == normalized


def test_source_comparison_observes_changed_head(selection_repo):
    root, base, _ = selection_repo
    with store.read_session():
        before = gate.source_tree(root, 'HEAD')
        assert gate.same_source_code(root, base, 'HEAD')
        (root / 'app.py').write_text('items = [1]\n')
        commit_all(root)
        assert gate.source_tree(root, 'HEAD')['app.py'] != before['app.py']
        assert not gate.same_source_code(root, base, 'HEAD')


def test_repository_test_files_observe_definitions_new_files_and_return_mutation(selection_repo):
    root, base, record = selection_repo
    records = {'TASK-1': record}
    with store.read_session():
        found = gate.repository_test_files(root, base, records)
        assert found == {'tests/test_app.py'}
        found.clear()
        assert gate.repository_test_files(root, base, records) == {'tests/test_app.py'}
        record['test'] = f'{PYTHON} -m pytest checks'
        assert gate.repository_test_files(root, base, records) == {'tests/test_app.py', 'checks/example.py'}
        (root / 'tests/test_new.py').write_text('def test_new():\n    assert True\n')
        assert gate.repository_test_files(root, base, records) == {
            'tests/test_app.py', 'tests/test_new.py', 'checks/example.py'}


def test_recorded_branch_match_observes_nested_inputs_and_unrecorded_code(selection_repo):
    root, base, _ = selection_repo
    paths = {'app.py': store.code_tree(root, base)['app.py']}
    implemented = {'commit': base, 'implementation_paths': dict(paths)}
    with store.read_session():
        assert gate.recorded_branch_code(root, implemented, base, 'HEAD')
        implemented['implementation_paths'].clear()
        assert not gate.recorded_branch_code(root, implemented, base, 'HEAD')
        implemented['implementation_paths'].update(paths)
        assert gate.recorded_branch_code(root, implemented, base, 'HEAD')
        (root / 'unrecorded.py').write_text('changed = True\n')
        commit_all(root)
        assert not gate.recorded_branch_code(root, implemented, base, 'HEAD')
