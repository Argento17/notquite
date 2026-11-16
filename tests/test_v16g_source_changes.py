"""Classify test material only before omitting a potentially meaningful path."""
import os

import pytest

from workorder import gate, source
from test_workorder import git, local_repo
from test_v05 import commit_all


@pytest.mark.parametrize('snapshot', ['worktree', 'staged', 'revision'])
@pytest.mark.parametrize('change', ['body', 'mode', 'nonpython', 'metadata', 'empty', 'delete', 'invalid'])
def test_retained_changes_do_not_reclassify_every_task(local_repo, monkeypatch, snapshot, change):
    root = local_repo
    base = git(root, 'rev-parse', 'HEAD')
    path = 'app.py'
    if change == 'body':
        (root / path).write_text('items = [1]\n')
    elif change == 'mode':
        if os.name == 'nt':
            pytest.skip('executable bit comparison on POSIX')
        (root / path).chmod(0o755)
    elif change == 'nonpython':
        path = 'data.json'
        (root / path).write_text('{}\n')
    elif change == 'metadata':
        path = 'tasks/notes.md'
        (root / 'tasks').mkdir()
        (root / path).write_text('notes\n')
    elif change == 'delete':
        (root / path).unlink()
    elif change == 'invalid':
        (root / path).write_text('this is invalid Python!\n')
    paths = set() if change == 'empty' else {path}
    git(root, 'add', '-A')
    kwargs = {'staged': True} if snapshot == 'staged' else {}
    if snapshot == 'revision':
        git(root, 'commit', '--allow-empty', '-qm', 'change')
        kwargs = {'after': git(root, 'rev-parse', 'HEAD')}
    calls = []
    actual = gate.test_material

    def counted(*args, **kwargs):
        calls.append(args)
        return actual(*args, **kwargs)

    monkeypatch.setattr(gate, 'test_material', counted)
    assert source.changed(root, paths, base, **kwargs) == paths
    assert calls == []


@pytest.mark.parametrize('snapshot', ['worktree', 'staged', 'revision'])
def test_omission_candidates_preserve_tests_and_remove_only_source_docs(local_repo, snapshot):
    root = local_repo
    (root / 'tests').mkdir()
    for relative in ('app.py', 'tests/test_app.py'):
        (root / relative).write_text('"""old docs"""\nvalue = 3\n')
    (root / 'tests/expected.txt').write_text('old\n')
    (root / 'README.md').write_text('old\n')
    base = commit_all(root)
    for relative in ('app.py', 'tests/test_app.py'):
        (root / relative).write_text('"""new docs"""\nvalue = 3\n')
    (root / 'tests/expected.txt').write_text('new\n')
    (root / 'README.md').write_text('new\n')
    git(root, 'add', '-A')
    kwargs = {'staged': True} if snapshot == 'staged' else {}
    if snapshot == 'revision':
        kwargs = {'after': commit_all(root)}
    paths = {'app.py', 'tests/test_app.py', 'tests/expected.txt', 'README.md'}
    assert source.changed(root, paths, base, **kwargs) == {'tests/test_app.py', 'tests/expected.txt'}
