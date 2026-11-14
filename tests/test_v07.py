"""0.7 usability regressions, with disposable local repositories only."""

import re
import subprocess

import pytest

from workorder import bootstrap, cli as command, gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import PASS, evidence, supported


@pytest.mark.parametrize('reason', ['test', 'wait'])
def test_done_refusal_reports_the_status_it_kept(local_repo, reason):
    root = local_repo
    path = task(root, ships='none', test=PASS if reason == 'wait' else f'{PYTHON} -c "exit(1)"',
                waits=['person:alex'] if reason == 'wait' else [])
    before = path.read_bytes()
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1 and path.read_bytes() == before
    notice = 'TASK-1: DONE was not written; the status stays IN_PROGRESS'
    assert notice in result.stdout
    assert result.stdout.index(notice) < result.stdout.index('Missing:')
    assert ('person:alex' if reason == 'wait' else 'a passing test') in result.stdout
    assert 'Claimed:  DONE' not in result.stdout and 'The status says DONE' not in result.stdout


@pytest.mark.parametrize('reason', ['tracked', 'untracked', 'missing-test', 'any'])
def test_done_saves_note_before_early_refusal(local_repo, reason):
    root = local_repo
    path = task(root, ships='none', test=None if reason == 'missing-test' else 'any' if reason == 'any' else PASS)
    if reason in {'tracked', 'untracked'}:
        (root / ('app.py' if reason == 'tracked' else 'new.py')).write_text('unfinished = True\n')
    result = cli(root, 'done', '--task', 'TASK-1', '--note', 'Keep this handover')
    assert result.returncode == 1 and store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
    assert re.search(r'^- \d{4}-\d{2}-\d{2}: Keep this handover$', path.read_text(), re.M)
    assert not store.entries(root, 'TASK-1')


@pytest.mark.parametrize('final_error', [False, True])
def test_done_final_refusal_restores_status_and_keeps_note(local_repo, monkeypatch, capsys, final_error):
    path = task(local_repo, status='BLOCKED', ships='none', test=PASS)
    explain = gate.explain

    def refuse(root, tid, claim=None):
        if claim is not None:
            return explain(root, tid, claim)
        assert store.record(root, tid)['status'] == 'DONE'
        if final_error:
            raise store.Refused('final refusal')
        return ['  Claimed:  DONE', '  Missing:  final refusal', 'The status says DONE'], True

    monkeypatch.setattr(gate, 'explain', refuse)
    monkeypatch.chdir(local_repo)
    assert command.main(['done', '--task', 'TASK-1', '--note', 'Keep final handover']) == 1
    output = capsys.readouterr().out
    assert store.record(local_repo, 'TASK-1')['status'] == 'BLOCKED'
    assert ': Keep final handover' in path.read_text()
    assert 'TASK-1: DONE was not written; the status stays BLOCKED' in output
    assert 'Missing:  final refusal' in output
    assert 'Claimed:  DONE' not in output and 'The status says DONE' not in output


@pytest.mark.parametrize('remote', [None, 'backup', 'origin'])
def test_done_success_only_suggests_push_with_origin(local_repo, tmp_path, remote):
    task(local_repo, ships='none', test=PASS)
    if remote:
        git(local_repo, 'remote', 'add', remote, str(tmp_path / 'unused-remote'))
    output = ok(local_repo, 'done', '--task', 'TASK-1')
    success = next(line for line in output.splitlines() if line.startswith('TASK-1 DONE:'))
    assert success == ('TASK-1 DONE: claim supported; commit tasks/ and .workorder/'
                       + (' and push' if remote == 'origin' else ''))


def test_done_names_template_acceptance_as_unfilled(local_repo):
    ok(local_repo, 'new', '--title', 'Add calc', '--owner', 'solo', '--test', PASS)
    output = ok(local_repo, 'done', '--task', 'TASK-1')
    assert 'the Acceptance list is not filled in' in output
    assert '(empty)' not in output


@pytest.mark.parametrize('work', ['no-receipts', 'failed-test', 'acceptance', 'missing-test',
                                 'any-no-receipts', 'any-acceptance'])
def test_show_next_step_uses_done(local_repo, work):
    task(local_repo, ships='none', test=None if work == 'missing-test' else 'any' if work.startswith('any-') else PASS)
    if work in {'failed-test', 'acceptance', 'any-acceptance'}:
        store.receipt(local_repo, 'TASK-1', 'implemented')
        store.receipt(local_repo, 'TASK-1', 'test', [PYTHON, '-c', 'pass'])
        if work == 'failed-test':
            # Keep the declared command identical while recording a later failure.
            store.append(local_repo, 'TASK-1', dict(kind='test', commit=store.head(local_repo),
                         code=store.code_digest(local_repo, store.head(local_repo)),
                         command=[PYTHON, '-c', 'pass'], exit_code=1, valid=False))
    output = ok(local_repo, 'show', '--task', 'TASK-1')
    step = next(line for line in output.splitlines() if line.startswith('next step:'))
    assert 'workorder done --task TASK-1' in step
    assert 'workorder receipt implemented' not in step
    if work.startswith('any-'):
        assert 'concrete test:' in step


def test_agent_block_updates_origin_explicitly():
    assert ('if the repository has an origin remote, update first: `git pull --rebase origin main` '
            "(or your branch's upstream)") in bootstrap.AGENT_BLOCK
    assert '`git pull`' not in bootstrap.AGENT_BLOCK
    assert bootstrap.AGENT_BLOCK.strip() in (PACKAGE / 'README.md').read_text()


@pytest.mark.parametrize('check', [False, True])
def test_init_reminds_clones_to_install_the_hook(local_repo, tmp_path, check):
    ok(local_repo, 'init')
    commit_all(local_repo)
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(local_repo), str(clone))
    hook = clone / '.git/hooks/pre-commit'
    assert not hook.exists()
    result = cli(clone, 'init', *(['--check'] if check else []))
    assert result.returncode == int(check)
    assert 'Hooks are not cloned; run workorder init in every clone.' in result.stdout
    assert hook.exists() != check
    if not check:
        assert 'Hooks are not cloned' not in ok(clone, 'init')


@pytest.mark.parametrize('ships', ['none', 'repo'])
def test_conflicted_rebase_show_and_check_keep_same_evidence_verdict(local_repo, ships):
    root = local_repo
    path = task(root, ships=ships, test=PASS)
    commit_all(root)
    git(root, 'checkout', '-q', '-b', 'feature')
    (root / 'app.py').write_text("items = ['tested']\n")
    commit_all(root, 'Implement task')
    evidence(root)
    path.write_text(path.read_text().replace('IN_PROGRESS', 'DONE'))
    commit_all(root, 'Claim task')
    git(root, 'checkout', '-q', 'main')
    (root / 'app.py').write_text("items = ['main']\n")
    commit_all(root, 'Conflicting main change')
    git(root, 'checkout', '-q', 'feature')
    conflict = subprocess.run(['git', 'rebase', 'main'], cwd=root, capture_output=True, text=True)
    assert conflict.returncode == 1 and 'CONFLICT' in conflict.stdout
    (root / 'app.py').write_text("items = ['resolved differently']\n")
    git(root, 'add', 'app.py')
    git(root, '-c', 'core.editor=true', 'rebase', '--continue')
    before = {p: p.read_bytes() for p in (root / '.workorder').rglob('*.json')}
    checked = cli(root, 'check', 'TASK-1')
    shown = ok(root, 'show', '--task', 'TASK-1')
    expected = 'CLOSED' if ships == 'none' else 'DONE'
    assert checked.returncode == 1 and f'Computed: {expected}' in checked.stdout
    assert f'recorded DONE; computed {expected}' in shown
    reason = 'the DONE claim is on code other than the tested code; run workorder renew --task TASK-1'
    assert reason in shown and reason in checked.stdout
    assert before == {p: p.read_bytes() for p in (root / '.workorder').rglob('*.json')}


@pytest.mark.parametrize('status', ['IN_PROGRESS', 'BLOCKED'])
def test_check_unclaimed_test_evidence_never_says_closed(local_repo, status):
    task(local_repo, ships='none', status=status, test=PASS)
    evidence(local_repo)
    checked = ok(local_repo, 'check', 'TASK-1')
    shown = ok(local_repo, 'show', '--task', 'TASK-1')
    headline = f'TASK-1 {status} (not claimed finished); its test evidence is in place'
    assert checked.startswith(headline) and shown.startswith(headline)
    assert 'CLOSED' not in checked and 'CLOSED' not in shown


def test_single_commit_rebase_is_named_rebase(local_repo):
    root = local_repo
    path = task(root, ships='repo', test=PASS)
    commit_all(root)
    git(root, 'checkout', '-q', '-b', 'feature')
    (root / 'app.py').write_text("items = ['tested']\n")
    original = commit_all(root, 'Implement task')
    evidence(root)
    path.write_text(path.read_text().replace('IN_PROGRESS', 'DONE'))
    commit_all(root, 'Claim task')
    git(root, 'checkout', '-q', 'main')
    (root / 'other.py').write_text('unrelated = True\n')
    commit_all(root, 'Unrelated main work')
    git(root, 'checkout', '-q', 'feature')
    git(root, 'rebase', '-q', 'main')
    landing = git(root, 'rev-parse', 'HEAD^')
    assert landing != original
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--ff-only', 'feature')
    for args in [('show', '--task', 'TASK-1'), ('check', 'TASK-1')]:
        output = ok(root, *args)
        assert f'merged into main by rebase {landing[:7]}, computed' in output
        assert 'by squash' not in output


@pytest.mark.parametrize('all_tests', [False, True])
def test_skipped_only_rerun_does_not_also_say_passed(local_repo, all_tests):
    root = local_repo
    (root / '.git/info/exclude').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'test_later.py').write_text('import pytest\n@pytest.mark.skip(reason="later")\ndef test_later(): pass\n')
    test = f'{PYTHON} -m pytest -q test_later.py'
    task(root, ships='none', test=test)
    base = commit_all(root)
    supported(root, test=test)
    flags = ['--all-tests'] if all_tests else []
    output = ok(root, 'check', '--base', base, '--run-tests', *flags)
    assert output.count('its test ran, but reported only skipped tests') == 1
    assert 'its test passed when this check ran it' not in output
    strict = cli(root, 'check', '--base', base, '--run-tests', '--strict-tests', *flags)
    assert strict.returncode == 1 and 'no test passed (1 skipped)' in strict.stdout


def test_release_versions_and_readme_pin():
    from workorder import __version__
    assert __version__ == '0.7.0'
    assert 'version = "0.7.0"' in (PACKAGE / 'pyproject.toml').read_text()
    assert 'workorder==0.7.0' in (PACKAGE / 'README.md').read_text()


def test_init_workflow_uses_running_version(local_repo, monkeypatch):
    # A future release must not leave a hard-coded older pin in init.
    monkeypatch.setattr(bootstrap, '__version__', '7.8.9', raising=False)
    assert bootstrap.initialize(local_repo) == 0
    assert 'workorder==7.8.9' in (local_repo / '.github/workflows/workorder.yml').read_text()
