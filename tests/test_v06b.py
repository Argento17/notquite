"""Round-8 regressions. All Git remotes are disposable filesystem paths."""

import os
import shlex
import subprocess

import pytest

from workorder import bootstrap
from workorder import cli as command, gate, store
from test_workorder import PACKAGE, PYTHON, cli, clones, git, local_repo, ok, task, unmerged_feature
from test_v05 import commit_all


PASS = f'{PYTHON} -c pass'
NOTICE = 'Acceptance lists are not checked; 1 supported claim rests on its declared test only.'


def fill_sections(path):
    path.write_text(path.read_text() + "\n## Scope\n\nAdd calculation.\n\n## Acceptance\n\n- Adds correctly.\n")
    return path


def evidence(root, tid='TASK-1', verification=False):
    store.receipt(root, tid, 'implemented')
    store.receipt(root, tid, 'test', gate.declared_test(store.record(root, tid)), no_code_change=verification)


def supported(root, status='DONE', tid='TASK-1', test=PASS):
    path = task(root, tid, ships='none', test=test)
    if git(root, 'status', '--porcelain'):
        commit_all(root)
    evidence(root, tid, verification=True)  # fixture verifies code already present at the base
    path.write_text(path.read_text().replace('"IN_PROGRESS"', f'"{status}"'))
    return path


def test_init_check_is_read_only_and_install_is_idempotent(local_repo):
    root = local_repo
    before = git(root, 'status', '--porcelain', '--untracked-files=all')
    check = cli(root, 'init', '--check')
    assert check.returncode == 1
    for name in ('AGENTS.md', 'CLAUDE.md', 'pre-commit', '.github/workflows/workorder.yml'):
        assert name in check.stdout
    assert git(root, 'status', '--porcelain', '--untracked-files=all') == before
    (root / 'AGENTS.md').write_text('# Existing instructions\n')
    output = ok(root, 'init')
    assert 'AGENTS.md' in output and 'pre-commit' in output
    paths = [root / p for p in ('AGENTS.md', 'CLAUDE.md', '.git/hooks/pre-commit', '.github/workflows/workorder.yml')]
    snapshot = [p.read_bytes() for p in paths]
    assert snapshot[0].startswith(b'# Existing instructions\n')
    assert os.access(paths[2], os.X_OK)
    ok(root, 'init')
    ok(root, 'init', '--check')
    assert snapshot == [p.read_bytes() for p in paths]
    readme = (PACKAGE / 'README.md').read_text()
    assert snapshot[1].decode().strip() in readme
    reference = (PACKAGE / 'docs/reference.md').read_text()
    assert snapshot[2].decode().strip() in reference
    # The reference example has a test extra; this fixture has no test dependencies.
    example_dependencies = ("      - name: Install project test dependencies\n"
                            "        run: python -m pip install -e '.[test]'\n")
    placeholder = ("      # Replace this step with your test dependency installation.\n"
                   "      - name: Add your test dependencies here\n"
                   "        run: |\n"
                   '          echo "::warning::No project test dependencies detected; add your test dependencies here" >&2\n')
    # Compare the workflow shape with the documented example. A Git install
    # writes its commit pin; the example shows the release pin.
    pin = shlex.quote(bootstrap.workorder_install())
    generated = snapshot[3].decode()
    assert pin in generated
    assert generated.strip().replace(pin, 'workorder==0.15.0') in reference.replace(
        example_dependencies, placeholder)


def test_init_preserves_existing_hook_and_workflow(local_repo):
    root = local_repo
    hook = root / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\nexit 9\n')
    workflow = root / '.github/workflows/workorder.yml'
    workflow.parent.mkdir(parents=True)
    workflow.write_text('name: Custom\n')
    result = cli(root, 'init')
    assert result.returncode == 1 and '--force' in result.stderr + result.stdout
    assert hook.read_text() == '#!/bin/sh\nexit 9\n'
    ok(root, 'init', '--force')
    assert 'workorder check --staged' in hook.read_text()
    assert workflow.read_text() == 'name: Custom\n'


@pytest.mark.skipif(os.name == 'nt', reason='builds an empty POSIX PATH for the shell hook')
def test_installed_hook_warns_without_workorder(local_repo, tmp_path):
    ok(local_repo, 'init')
    empty = tmp_path / 'empty-bin'
    empty.mkdir()
    result = subprocess.run(['/bin/sh', str(local_repo / '.git/hooks/pre-commit')], cwd=local_repo,
                            env={**os.environ, 'PATH': str(empty)}, capture_output=True, text=True)
    assert result.returncode == 0
    assert 'workorder' in (result.stdout + result.stderr).lower()
    assert 'warn' in (result.stdout + result.stderr).lower()


def test_done_records_only_changed_implementation_and_note(local_repo):
    root = local_repo
    path = task(root, ships='none', test=PASS)
    path.write_text(path.read_text() + '\n## Scope\n\nAdd calculation.\n\n## Acceptance\n\n- Adds correctly.\n')
    original_head = store.head(root)
    output = ok(root, 'done', '--no-code-change', '--task', 'TASK-1', '--note', 'Ready for review')
    assert 'Acceptance is not checked by Workorder: confirm each item is met.' in output
    assert '- Adds correctly.' in output
    success = 'TASK-1 DONE: its declared test passed on this code'
    assert success in output and output.index('Acceptance') < output.index(success)
    assert store.record(root, 'TASK-1')['status'] == 'DONE'
    assert ': Ready for review' in path.read_text()
    assert [r['kind'] for r in store.entries(root, 'TASK-1')] == ['implemented', 'test']
    assert store.head(root) == original_head
    commit_all(root)
    ok(root, 'done', '--no-code-change', '--task', 'TASK-1')
    assert [r['kind'] for r in store.entries(root, 'TASK-1')] == ['implemented', 'test', 'test']
    (root / 'app.py').write_text('x = 2\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    assert [r['kind'] for r in store.entries(root, 'TASK-1')][-2:] == ['implemented', 'test']


@pytest.mark.parametrize('reason', ['test', 'dependency', 'contract', 'dirty', 'missing-test', 'any'])
def test_done_refusal_keeps_status(local_repo, reason):
    root = local_repo
    test = f'{PYTHON} -c "raise SystemExit(3)"' if reason == 'test' else PASS
    test = None if reason == 'missing-test' else 'any' if reason == 'any' else test
    path = task(root, status='BLOCKED', ships='none', test=test,
                waits=['person:sam'] if reason == 'dependency' else (),
                contract='contract.json' if reason == 'contract' else 'none')
    fill_sections(path)
    if reason == 'contract':
        (root / 'contract.json').write_text('{"task":"TASK-1","criteria":[{"id":"C1","ask":"proof",'
                                          '"critical":true,"requires":"REAL","evidence":[]}]}')
        commit_all(root)
    if reason == 'dirty':
        (root / 'uncommitted.py').write_text('pass\n')
    before = path.read_bytes()
    result = cli(root, 'done', '--no-code-change', '--task', 'TASK-1')
    assert result.returncode == 1
    assert path.read_bytes() == before
    assert 'Missing:' in result.stdout + result.stderr
    if reason == 'dirty':
        assert not store.entries(root, 'TASK-1')


@pytest.mark.parametrize('change', ['prepend', 'new', 'untracked', 'staged'])
def test_strict_tests_requires_allow_for_additions(local_repo, change):
    root = local_repo
    folder = root / 'tests'
    folder.mkdir()
    probe = folder / 'test_app.py'
    probe.write_text('assert True\n')
    task(root, ships='none', test=f'{PYTHON} -m pytest tests')
    base = commit_all(root)
    store.add_note(root, "TASK-1", "Working on these tests")
    if change == 'prepend':
        probe.write_text('import sys; sys.exit(0)\n' + probe.read_text())
    else:
        (folder / 'test_new.py').write_text('assert True\n')
    if change == 'staged':
        git(root, 'add', '-A')
    elif change != 'untracked':
        commit_all(root)
    extra = ['--staged'] if change == 'staged' else []
    ok(root, 'check', '--review', 'refuse', '--base', base, *extra)
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests', *extra)
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout
    assert 'test files' in result.stdout
    ok(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests', '--allow', 'TASK-1', *extra)


@pytest.mark.parametrize('config', ['conftest.py', 'tests/nested/conftest.py', 'pytest.ini', 'tox.ini', 'setup.cfg', 'pyproject.toml'])
def test_strict_pytest_config_requires_each_pytest_task(local_repo, config):
    root = local_repo
    task(root, ships='none', test=f'{PYTHON} -m pytest tests')
    task(root, 'TASK-2', ships='none', test='pytest other')
    task(root, 'TASK-3', ships='none', test=f'{PYTHON} test_plain.py')
    base = commit_all(root)
    store.add_note(root, "TASK-1", "Working on these tests")
    store.add_note(root, "TASK-2", "Working on pytest configuration")
    path = root / config
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[tool.pytest.ini_options]\naddopts = "--ignore=tests"\n' if config.endswith('.toml') else '# skip tests\n')
    commit_all(root)
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests', '--allow', 'TASK-1')
    # One approval covers the shared configuration file; it names every owner.
    assert result.returncode == 0, result.stdout + result.stderr
    unapproved = cli(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests')
    assert unapproved.returncode == 1
    assert 'TASK-1' in unapproved.stdout
    if not config.startswith('tests/'):
        assert 'TASK-2' in unapproved.stdout
    assert 'TASK-3:' not in unapproved.stdout


def test_strict_pyproject_ignores_other_sections(local_repo):
    root = local_repo
    task(root, ships='none', test=f'{PYTHON} -m pytest tests')
    path = root / 'pyproject.toml'
    path.write_text('[tool.pytest.ini_options]\naddopts = "-q"\n[project]\nname = "before"\n')
    base = commit_all(root)
    path.write_text(path.read_text().replace('before', 'after'))
    commit_all(root)
    ok(root, 'check', '--base', base, '--strict-tests')


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
def test_all_tests_runs_untouched_claims_once_without_history_change(local_repo, status):
    root = local_repo
    (root / 'test_probe.py').write_text('from pathlib import Path\nprint("REGRESSION_RUN")\nassert Path("app.py").read_text() != "broken\\n"\n')
    commit_all(root)
    for tid in ('TASK-1', 'TASK-2'):
        supported(root, tid=tid, status=status, test=f'{PYTHON} test_probe.py')
    base = commit_all(root)
    (root / 'app.py').write_text('broken\n')
    commit_all(root)
    before = {tid: gate.verdict(root, store.record(root, tid)) for tid in ('TASK-1', 'TASK-2')}
    receipts = {p: p.read_bytes() for p in (root / '.workorder').rglob('*.json')}
    ok(root, 'check', '--base', base, '--run-tests')
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests')
    assert result.returncode == 1
    assert result.stdout.count('REGRESSION_RUN') == 1
    for tid in before:
        assert f"regression: {tid}'s test fails on this code" in result.stdout
        assert gate.verdict(root, store.record(root, tid)) == before[tid]
    assert receipts == {p: p.read_bytes() for p in (root / '.workorder').rglob('*.json')}
    assert "no task changed in this change; ran 2 finished tasks' declared tests" in result.stdout


def test_require_task_records_code_changes(local_repo):
    root = local_repo
    task(root, ships='none', test=PASS)
    base = commit_all(root)
    (root / 'app.py').write_text('value = 3\n')
    commit_all(root)
    ok(root, 'check', '--base', base)
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 1 and 'this change edits code but no task records it' in result.stdout
    store.add_note(root, 'TASK-1', 'Records the change')
    refused = cli(root, 'check', '--base', base, '--require-task')
    assert refused.returncode == 1 and 'implemented or test receipt' in refused.stdout
    git(root, 'restore', 'tasks/TASK-1.md')
    store.receipt(root, 'TASK-1', 'implemented')
    ok(root, 'check', '--base', base, '--require-task')


@pytest.mark.parametrize('mode', ['plain', 'single', 'named', 'staged', 'base'])
def test_check_explains_supported_claims_without_contract(local_repo, mode):
    root = local_repo
    task(root, ships='none', test=PASS)
    base = commit_all(root)
    supported(root)
    git(root, 'add', '-A')
    args = {'plain': [], 'single': ['TASK-1'], 'named': ['--task', 'TASK-1'],
            'staged': ['--staged'], 'base': ['--base', base]}[mode]
    assert NOTICE in ok(root, 'check', *args)


def test_staged_empty_scope_is_silent(local_repo):
    (local_repo / 'app.py').write_text('new = True\n')
    git(local_repo, 'add', 'app.py')
    assert ok(local_repo, 'check', '--staged') == ''


@pytest.mark.parametrize('kind', ['receipts', 'note', 'status'])
def test_stale_clone_refuses_claim_and_show_warns(clones, kind):
    one, two = clones
    if kind == 'receipts':
        store.receipt(one, 'TASK-1', 'implemented')
        store.receipt(one, 'TASK-1', 'test', [PYTHON, '-c', 'pass'])
        store.add_note(one, 'TASK-1', 'Implemented; finish acceptance next')
    elif kind == 'note':
        store.add_note(one, 'TASK-1', 'Hand over without finishing')
    else:
        path = one / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('IN_PROGRESS', 'BLOCKED'))
    commit_all(one)
    git(one, 'push', '-q', 'origin', 'HEAD:main')
    head = store.head(two)
    result = cli(two, 'claim', '--task', 'TASK-1')
    assert result.returncode == 1
    assert 'origin/main has newer work on TASK-1' in result.stderr
    assert 'by one); pull first' in result.stderr
    if kind == 'receipts':
        assert '(2 receipts, 1 note, by one)' in result.stderr
    shown = ok(two, 'show', '--task', 'TASK-1')
    assert shown.splitlines()[0] == 'origin/main has newer work on this task: pull before you start'
    assert store.head(two) == head
    assert not store.active_claims(two)
    git(two, 'pull', '--ff-only', '-q')
    assert 'newer work' not in ok(two, 'show', '--task', 'TASK-1')
    ok(two, 'claim', '--task', 'TASK-1')


def test_show_offline_is_bounded_and_does_not_reuse_stale_ref(local_repo, monkeypatch, capsys):
    root = local_repo
    task(root, ships='none')
    git(root, 'remote', 'add', 'origin', str(root / 'absent.git'))
    real_run = subprocess.run
    timeouts = []

    def offline(args, **kwargs):
        if args[0] == 'git' and 'fetch' in args:
            timeouts.append(kwargs.get('timeout'))
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, 'run', offline)
    monkeypatch.chdir(root)
    assert command.main(['show', '--task', 'TASK-1']) == 0
    captured = capsys.readouterr()
    assert timeouts and all(0 < value <= 5 for value in timeouts)
    assert 'showing local task' in captured.out
    assert not captured.err and 'newer work' not in captured.out


@pytest.mark.parametrize('status', ['IN_PROGRESS', 'BLOCKED', 'CANCELLED'])
def test_show_unclaimed_headline(local_repo, status):
    root = local_repo
    task(root, status=status, ships='none', test=PASS)
    evidence(root)
    output = ok(root, 'show', '--task', 'TASK-1')
    assert output.splitlines()[0] == f'TASK-1 {status} (not claimed finished); its test evidence is in place'
    assert 'Evidence: CLOSED' not in output


def test_show_unclaimed_headline_names_owed_evidence(local_repo):
    task(local_repo, ships='none', test=PASS)
    output = ok(local_repo, 'show', '--task', 'TASK-1')
    assert output.splitlines()[0] == 'TASK-1 IN_PROGRESS (not claimed finished); owes: implementation'


@pytest.mark.parametrize('target', ['unchanged', 'unrelated', 'overlap'])
def test_before_merge_advice_distinguishes_target_changes(local_repo, target):
    root = local_repo
    task(root, ships='repo', test=PASS)
    commit_all(root)
    unmerged_feature(root)
    evidence(root)
    commit_all(root)
    if target != 'unchanged':
        git(root, 'checkout', 'main')
        (root / ('feature.py' if target == 'overlap' else 'other.py')).write_text('different = True\n')
        commit_all(root)
        git(root, 'checkout', 'feature')
    shown = ok(root, 'show', '--task', 'TASK-1')
    assert 'merge of the tested code' in shown
    assert ('the merged code differs' in shown) == (target == 'overlap')
    if target != 'overlap':
        assert 'renew --task' not in shown


@pytest.mark.parametrize('reason', ['before', 'wrong-command', 'different-code', 'invalid', 'superseded'])
def test_timeline_marks_passing_runs_that_do_not_count(local_repo, reason):
    root = local_repo
    task(root, ships='none', test=PASS)
    if reason != 'before':
        store.receipt(root, 'TASK-1', 'implemented')
    if reason == 'different-code':
        (root / 'app.py').write_text('changed = True\n')
        commit_all(root)
    cmd = [PYTHON, '-c', 'print(42)'] if reason == 'wrong-command' else [PYTHON, '-c', 'pass']
    if reason == 'invalid':
        cmd = [PYTHON, '-c', 'from pathlib import Path; Path("dirty.py").write_text("pass")']
    store.receipt(root, 'TASK-1', 'test', cmd)
    if reason == 'before':
        store.receipt(root, 'TASK-1', 'implemented')
    if reason == 'superseded':
        store.receipt(root, 'TASK-1', 'test', [PYTHON, '-c', 'pass'])
    shown = ok(root, 'show', '--task', 'TASK-1')
    assert '(does not count:' in shown
    if reason != 'invalid':
        assert 'test PASS:' in shown


def test_subdirectory_commands_resolve_root(local_repo):
    root = local_repo
    sub = root / 'nested/inner'
    sub.mkdir(parents=True)
    ok(sub, 'new', '--title', 'Task', '--owner', 'agent', '--test', PASS)
    ok(sub, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(sub, 'receipt', 'test', '--task', 'TASK-1')
    ok(sub, 'note', '--task', 'TASK-1', 'From subdirectory')
    assert 'TASK-1' in ok(sub, 'show', '--task', 'TASK-1')
    ok(sub, 'check', '--task', 'TASK-1')
    assert not (sub / 'tasks').exists()


def test_agent_identity_overrides_git_for_claims_and_receipts(clones, monkeypatch):
    one, _ = clones
    monkeypatch.setenv('WORKORDER_AGENT', 'agent-b')
    assert 'as agent-b' in ok(one, 'claim', '--task', 'TASK-1')
    ok(one, 'receipt', 'implemented', '--task', 'TASK-1')
    assert store.entries(one, 'TASK-1')[-1]['who'] == 'agent-b'
    monkeypatch.delenv('WORKORDER_AGENT')
    ok(one, 'receipt', 'implemented', '--task', 'TASK-1')
    assert store.entries(one, 'TASK-1')[-1]['who'] == 'one'


@pytest.mark.parametrize('summary', ['2 skipped', 'no tests ran'])
def test_pytest_no_pass_refuses_with_or_without_strict(local_repo, summary, monkeypatch):
    root = local_repo
    (root / '.git/info/exclude').write_text('__pycache__/\n.pytest_cache/\n')
    probe = root / 'tests/test_probe.py'
    probe.parent.mkdir()
    probe.write_text('import os, pytest\nif not os.getenv("WO_EMPTY"):\n'
                     '    @pytest.mark.skip(reason="later")\n    def test_later(): pass\n'
                     '    @pytest.mark.skip(reason="later")\n    def test_also_later(): pass\n')
    test = f'{PYTHON} -m pytest -q tests/test_probe.py'
    task(root, ships='none', test=test)
    base = commit_all(root)
    supported(root, test=test)
    # Real pytest supplies the summary; CI runs fresh after the environment changes.
    monkeypatch.setenv('CI', 'true')
    # Empty collection fails with exit 5; still report why.
    if summary == 'no tests ran':
        monkeypatch.setenv('WO_EMPTY', '1')
    before = {p: p.read_bytes() for p in (root / '.workorder').rglob('*.json')}
    result = cli(root, 'check', '--base', base, '--run-tests')
    assert result.returncode == 1
    assert f'its test ran but no test passed ({summary})' in result.stdout
    strict = cli(root, 'check', '--base', base, '--run-tests', '--strict-tests', '--allow', 'TASK-1')
    assert strict.returncode == 1
    assert f'TASK-1: its test ran but no test passed ({summary})' in strict.stdout
    assert before == {p: p.read_bytes() for p in (root / '.workorder').rglob('*.json')}


def test_stale_result_names_claim_without_contradicting_computed(local_repo):
    root = local_repo
    task(root, ships='repo', test=PASS)
    commit_all(root)
    unmerged_feature(root)
    evidence(root)
    commit_all(root)
    (root / 'feature.py').write_text('untested = True\n')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('IN_PROGRESS', 'DONE'))
    commit_all(root)
    result = cli(root, 'check', 'TASK-1')
    assert result.returncode == 1 and 'Evidence: DONE' in result.stdout
    assert 'Result: the DONE claim is on code other than the tested code; run workorder renew --task TASK-1' in result.stdout
    assert 'Result: NOT DONE' not in result.stdout


@pytest.mark.parametrize('block', [False, True])
def test_done_preserves_bom_crlf_and_body_status(local_repo, block):
    root = local_repo
    path = task(root, ships='none', test=PASS)
    if block:
        path.write_text(path.read_text().replace('status:', 'mission: |\n  Example\n  ---\n  More text\nstatus:'))
    original = ('\ufeff' + path.read_text().replace('Add a list filter', 'A --- separator')
                + '\n## Scope\nAdd calculation.\n\n## Acceptance\n- All good.\n\nstatus: leave body alone\n').replace('\n', '\r\n').encode()
    path.write_bytes(original)
    ok(root, 'done', '--no-code-change', '--task', 'TASK-1')
    assert path.read_bytes() == original.replace(b'status: "IN_PROGRESS"', b'status: "DONE"', 1)


def test_done_restores_status_if_final_check_errors(local_repo, monkeypatch, capsys):
    root = local_repo
    path = task(root, status='BLOCKED', ships='none', test=PASS)
    fill_sections(path)
    before = path.read_bytes()
    real_explain = gate.explain

    def final_error(root, tid, claim=None, rerun=False):
        if claim is None:
            assert store.record(root, tid)['status'] == 'DONE'
            raise store.Refused('final check refused')
        return real_explain(root, tid, claim, rerun=rerun)

    monkeypatch.setattr(gate, 'explain', final_error)
    monkeypatch.chdir(root)
    assert command.main(['done', '--no-code-change', '--task', 'TASK-1']) == 1
    assert path.read_bytes() == before
    assert 'Missing:  final check refused' in capsys.readouterr().out


def test_strict_pytest_default_discovery_protects_added_tests(local_repo):
    root = local_repo
    task(root, ships='none', test=f'{PYTHON} -m pytest')
    base = commit_all(root)
    store.add_note(root, "TASK-1", "Working on these tests")
    (root / 'test_new.py').write_text('def test_new(): assert 1 == 1\n')
    commit_all(root)
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests')
    # A pathless pytest command does not own a newly added test file.
    assert result.returncode == 1 and 'workorder-allow:tests' in result.stdout
    ok(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests', '--allow', 'tests')


def test_claim_local_progress_is_not_stale(clones):
    one, _ = clones
    store.add_note(one, 'TASK-1', 'Local work in progress')
    commit_all(one)
    assert 'newer work' not in ok(one, 'show', '--task', 'TASK-1')
    ok(one, 'claim', '--task', 'TASK-1')


def test_acceptance_summary_counts_only_supported_contractless_claims(local_repo):
    root = local_repo
    for tid in ('TASK-1', 'TASK-2'):
        supported(root, tid=tid, status='CLOSED' if tid == 'TASK-2' else 'DONE')
    task(root, 'TASK-3', status='DONE', ships='none', test=PASS)
    task(root, 'TASK-4', status='IN_PROGRESS', ships='none', test=PASS)
    evidence(root, 'TASK-4')
    result = cli(root, 'check')
    assert result.returncode == 1
    assert 'Acceptance lists are not checked; 2 supported claims rest on their declared tests only.' in result.stdout


def test_docs_state_review_rights_and_opt_in_policies():
    text = (PACKAGE / 'docs/reference.md').read_text()
    for phrase in ('triage rights', 'CODEOWNERS', 'required', '--strict-tests', '--all-tests', '--require-task',
                   'WORKORDER_AGENT', 'temporary copy', 'pipx install workorder', 'workorder==0.15.0'):
        assert phrase in text
    readme = (PACKAGE / 'README.md').read_text()
    for target in ('docs/reference.md', 'docs/guarantees.md', 'docs/shortcuts.md'):
        assert target in readme


@pytest.mark.parametrize('quiet,outcome', [('-qq', 'skip'), ('-q', 'xfail'), ('-qq', 'xfail'), ('-qq', 'mixed'),
                                          ('-qq -s', 'skip'), ('-qq -o console_output_style=classic', 'skip'),
                                          ('-qq -o console_output_style=count', 'skip')])
def test_strict_pytest_counts_quiet_and_expected_failure_outcomes(local_repo, quiet, outcome):
    root = local_repo
    (root / '.git/info/exclude').write_text('__pycache__/\n.pytest_cache/\n')
    probe = root / 'test_outcomes.py'
    mark = 'skip' if outcome in {'skip', 'mixed'} else 'xfail'
    probe.write_text(f'import pytest\n@pytest.mark.{mark}(reason="later")\ndef test_later(): assert False\n'
                     + ('def test_ok(): assert True\n' if outcome == 'mixed' else ''))
    test = f'{PYTHON} -m pytest {quiet} test_outcomes.py'
    task(root, ships='none', test=test)
    base = commit_all(root)
    supported(root, test=test)
    result = cli(root, 'check', '--base', base, '--run-tests', '--strict-tests')
    assert result.returncode == (0 if outcome == 'mixed' else 1)
    if outcome == 'mixed':
        assert 'only skipped' not in result.stdout and 'no test passed' not in result.stdout
    else:
        assert 'TASK-1: its test ran but no test passed' in result.stdout


@pytest.mark.parametrize('config,option,filename', [
    ('pytest.ini', '-c pytest.ini', 'test_app.py'),
    ('pytest.ini', '', 'check_app.py'),
    ('pyproject.toml', '', 'check_app.py'),
])
def test_strict_pytest_configuration_keeps_default_discovery(local_repo, config, option, filename):
    root = local_repo
    (root / config).write_text('[tool.pytest.ini_options]\npython_files = ["check_*.py"]\n' if config.endswith('toml')
                               else '[pytest]\npython_files = test_*.py check_*.py\n')
    task(root, ships='none', test=f'{PYTHON} -m pytest {option}')
    probe = root / filename
    probe.write_text('def test_app(): assert 1 == 1\n')
    base = commit_all(root)
    store.add_note(root, "TASK-1", "Working on these tests")
    probe.write_text('import sys; sys.exit(0)\n' + probe.read_text())
    commit_all(root)
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests')
    # python_files still marks the file as a test. A pathless command does not own it.
    assert result.returncode == 1 and 'workorder-allow:tests' in result.stdout
    ok(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests', '--allow', 'tests')


def test_done_failed_test_keeps_record_unchanged(local_repo):
    root = local_repo
    fill_sections(task(root, status='BLOCKED', ships='none', test=f'{PYTHON} -c "raise SystemExit(3)"'))
    result = cli(root, 'done', '--task', 'TASK-1', '--note', 'Test failed; investigate next session')
    assert result.returncode == 1 and 'Missing:' in result.stdout
    assert store.record(root, 'TASK-1')['status'] == 'BLOCKED'
    assert ': Test failed; investigate next session' not in (root / 'tasks/TASK-1.md').read_text()
    assert not store.entries(root, 'TASK-1')


@pytest.mark.parametrize('config,option', [('pyproject.toml', ''), ('custom.ini', '-c custom.ini'),
                                          ('custom.toml', '--config-file=custom.toml')])
def test_strict_pytest_custom_config_and_globs(local_repo, config, option):
    root = local_repo
    (root / config).write_text('[tool.pytest.ini_options]\npython_files = [\n  "check_[ab].py",\n]\n'
                               if config.endswith('toml') else '[pytest]\npython_files = check_[ab].py\n')
    task(root, ships='none', test=f'{PYTHON} -m pytest {option}')
    probe = root / 'check_a.py'
    probe.write_text('def test_app(): assert 1 == 1\n')
    base = commit_all(root)
    store.add_note(root, "TASK-1", "Working on these tests")
    probe.write_text('import sys; sys.exit(0)\n' + probe.read_text())
    commit_all(root)
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests')
    # A custom discovery pattern still marks the file. The pathless command does not own it.
    assert result.returncode == 1 and 'workorder-allow:tests' in result.stdout
    ok(root, 'check', '--review', 'refuse', '--base', base, '--strict-tests', '--allow', 'tests')
