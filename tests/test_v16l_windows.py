"""Windows pipe failures and byte-exact result reuse, exercised on every OS."""
import errno

import pytest

from workorder import store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections


@pytest.mark.parametrize('operation', ['write', 'flush'])
def test_dead_batch_einval_is_refused_reaped_and_retried(local_repo, monkeypatch, operation):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    with store.read_session():
        assert store.blob_bytes(local_repo, 'HEAD:app.py') == b'items = []\n'
        process = next(iter(store._READS.get().batches.values())).process
        process.kill()
        process.wait(timeout=5)
        close = process.stdin.close

        def invalid(*args):
            raise OSError(errno.EINVAL, 'Invalid argument')

        def close_invalid():
            close()
            invalid()

        # Windows reports EINVAL both on the dead pipe and when close flushes
        # it. Keep the real child and handles; emulate just those OS errors.
        monkeypatch.setattr(process.stdin, operation, invalid)
        monkeypatch.setattr(process.stdin, 'close', close_invalid)
        try:
            with pytest.raises(store.Refused, match='Invalid argument'):
                store.blob_bytes(local_repo, 'HEAD:missing')
            assert process.returncode is not None
            assert process.stdin.closed and process.stdout.closed
            assert store.blob_bytes(local_repo, 'HEAD:./app.py') == b'items = []\n'
        finally:
            process.stdout.close()


def test_batch_close_einval_still_waits_for_live_child(local_repo, monkeypatch):
    batch = store.Batch(local_repo)
    process = batch.process
    close = process.stdin.close

    def close_invalid():
        close()
        raise OSError(errno.EINVAL, 'Invalid argument')

    monkeypatch.setattr(process.stdin, 'close', close_invalid)
    try:
        batch.close()
        assert process.returncode is not None
        assert process.stdin.closed and process.stdout.closed
    finally:
        process.wait(timeout=5)
        process.stdout.close()


@pytest.mark.parametrize('byte_sensitive', [False, True])
def test_lf_editor_and_crlf_checkout_reuse_only_identical_bytes(local_repo, byte_sensitive):
    root = local_repo
    git(root, 'config', 'core.autocrlf', 'true')
    counter = root.parent / 'executions'
    program = (
        'from pathlib import Path; import sys; '
        'counter = Path(sys.argv[1]); '
        'counter.write_text(counter.read_text() + "run\\n" if counter.exists() else "run\\n"); '
        'print(repr(Path("app.py").read_bytes()))'
    )
    if byte_sensitive:
        program += '; assert Path("app.py").read_bytes() == b"items = []\\n"'
    command = store.join_command([PYTHON, '-c', program, str(counter)])
    fill_sections(task(root, ships='none', test=command))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    assert (root / 'app.py').read_bytes() == b'items = []\n'
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    # Explicit completion records evidence and verifies the claim afresh.
    assert counter.read_text().splitlines() == ['run', 'run']
    git(root, 'add', 'tasks', '.workorder')

    first = cli(root, 'check', '--staged')
    assert first.returncode == (1 if byte_sensitive else 0), first.stdout + first.stderr
    assert '(reused from ' not in first.stdout
    assert counter.read_text().splitlines() == ['run', 'run', 'run']
    second = cli(root, 'check', '--staged')
    assert second.returncode == first.returncode, second.stdout + second.stderr
    assert '(reused from ' in second.stdout
    assert counter.read_text().splitlines() == ['run', 'run', 'run']
    assert (root / 'app.py').read_bytes() == b'items = []\n'
    if byte_sensitive:
        assert 'AssertionError' in first.stdout + first.stderr
        assert 'AssertionError' in second.stdout + second.stderr
