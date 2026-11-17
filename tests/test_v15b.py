"""Second-pass regressions, with real commands in disposable repositories."""

import json

import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import branch, check, claim, CODE, TEST


def finished_tasks(root, count=1, no_code_change=True, legacy=False):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'calc.py').write_text(CODE)
    for number in range(1, count + 1):
        name = f'test_{number}.py'
        (root / name).write_text('from calc import div\nassert div(6, 3) == 2\n')
        fill_sections(task(root, f'TASK-{number}', ships='none', test=store.join_command([PYTHON, name])))
    commit = commit_all(root)
    for number in range(1, count + 1):
        tid = f'TASK-{number}'
        common = dict(commit=commit, code=store.code_digest(root, commit))
        store.append(root, tid, dict(common, kind='implemented',
                                    **({} if legacy else dict(implementation_base=commit, implementation_paths={}))))
        store.append(root, tid, dict(common, kind='test', command=[PYTHON, f'test_{number}.py'], exit_code=0,
                                    valid=True, **({'no_code_change': True} if no_code_change else {})))
        store.set_task_field(root, tid, 'status', 'DONE')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    return base


@pytest.mark.parametrize('action', ['done', 'check'])
@pytest.mark.parametrize('body', ['assert callable(div)', 'assert int("1") == 1'])
def test_stub_refuses_test_that_does_not_call_changed_function(local_repo, body, action):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text('from calc import div\ndef test_div():\n    ' + body + '\n')
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2')
        assert store.entries(root, 'TASK-2') == []
    else:
        claim(root)
        result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-2: its new or changed tests still pass with the changed functions stubbed out (test_div); it does not exercise the change' in result.stdout
    if action == 'check':
        # 0.18 one review rule: an approving review clears a stub finding.
        assert 'an approving review clears this' in next(line for line in result.stdout.splitlines() if 'stubbed out' in line)
        assert check(root, '--allow', 'TASK-2').returncode == 0
        assert check(root, '--approving-review').returncode == 0
        assert check(root, '--review', 'warn').returncode == 1


@pytest.mark.parametrize('expression', ['r == r', 'r.value == r.value', 'r[0] == r[0]'])
def test_self_equality_is_not_an_assertion(local_repo, expression):
    root = local_repo
    base = store.head(root)
    (root / 'test_self.py').write_text('def test_self():\n    assert ' + expression + '\n')
    assert gate.assertion_problems(root, base, {'id': 'TASK-1', 'test': 'pytest test_self.py'})


def test_changed_method_stub_ignores_unchanged_functions_and_uses_ast(local_repo):
    root = local_repo
    before = 'def helper():\n    return 2\nclass Calc:\n    def sub(self, a, b):\n        return a + b\n'
    branch(root, code=before)
    (root / 'calc.py').write_text(before.replace('a + b', 'a - b') + '\ndef added():\n    return 9\n')
    (root / 'tests/test_div.py').write_text('from calc import Calc, helper\ndef test_div():\n    assert helper() == 2\n    assert callable(Calc.sub)\n')
    commit_all(root)
    claim(root)
    result = check(root)
    assert result.returncode == 1
    line = next(line for line in result.stdout.splitlines() if 'stubbed out' in line)
    assert 'Calc.sub' in line and 'added' in line and 'helper' not in line


@pytest.mark.parametrize('status', ['IN_PROGRESS', 'BLOCKED'])
def test_reopened_finished_failure_needs_task_label(local_repo, status):
    root = local_repo
    finished_tasks(root)
    store.set_task_field(root, 'TASK-1', 'status', status)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    git(root, 'add', '.')
    local = cli(root, 'check', '--staged')
    assert local.returncode == 0, local.stdout + local.stderr
    def reopened_check(*flags):
        return cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests', *flags)
    result = reopened_check()
    expected = ('TASK-1 was reopened while its test fails on this code; an approving review clears this, '
                'or a reviewer accepts it with the label workorder-allow:TASK-1')
    assert result.returncode == 1 and expected in result.stdout
    assert reopened_check('--allow', 'TASK-1').returncode == 0
    assert reopened_check('--approving-review').returncode == 0  # 0.18 one review rule
    assert reopened_check('--review', 'warn').returncode == 1


def test_exemptions_are_never_grouped(local_repo):
    root = local_repo
    finished_tasks(root, count=2)
    for number in (1, 2):
        store.add_note(root, f'TASK-{number}', 'Verify existing code')
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    for number in (1, 2):
        assert result.stdout.count(f'TASK-{number} finished with --no-code-change') == 1
    notes = [f"TASK-{n}'s test was written in this change" for n in (1, 2)]
    notes += [f'TASK-{n} DONE pending review' for n in (1, 2)]
    notes += [f'TASK-{n} is a declared refactor' for n in (1, 2)]
    assert gate.summarize_notes(notes) == notes


def test_config_change_is_one_finding_and_does_not_demand_red_on_untouched_tasks(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    (root / 'tests').mkdir()
    (root / 'calc.py').write_text(CODE)
    for n in (1, 2):
        name = f'tests/test_{n}.py'
        (root / name).write_text(TEST)
        fill_sections(task(root, f'TASK-{n}', ships='none', test=store.join_command([PYTHON, '-m', 'pytest', '-q', name])))
    commit = commit_all(root)
    for n in (1, 2):
        claim(root, f'TASK-{n}')
    base = commit_all(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = [".", "tests"]\n')
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests')
    findings = [line for line in result.stdout.splitlines() if 'pytest configuration changed' in line]
    assert len(findings) == 1, result.stdout
    assert 'TASK-1' in findings[0] and 'TASK-2' in findings[0]
    assert 'its test passes on' not in result.stdout
    assert result.returncode == 1
    allowed = cli(root, 'check', '--base', base, '--run-tests', '--all-tests', '--allow', 'tests')
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


@pytest.mark.parametrize('path', ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'Pipfile.lock',
                                  'uv.lock', 'Cargo.lock', 'go.sum', 'Gemfile.lock', 'composer.lock', 'nested/uv.lock'])
def test_lockfiles_do_not_require_task(local_repo, path):
    root = local_repo
    base = store.head(root)
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text('changed\n')
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 0, result.stdout + result.stderr


def test_repeatable_require_task_ignore_only_ignores_matching_paths(local_repo):
    root = local_repo
    base = store.head(root)
    (root / 'Dockerfile').write_text('FROM scratch\n')
    (root / 'config').mkdir()
    (root / 'config/settings.yaml').write_text('hello: world\n')
    args = ['check', '--base', base, '--require-task', '--require-task-ignore', 'Dockerfile',
            '--require-task-ignore', 'config/*.yaml']
    assert cli(root, *args).returncode == 0
    (root / 'unowned.py').write_text('value = 3\n')
    result = cli(root, *args)
    assert result.returncode == 1 and 'no task records it' in result.stdout


@pytest.mark.parametrize('filename', ['workorder.yml', 'task-check.yml'])
def test_workorder_workflow_edits_need_workflow_label(local_repo, filename):
    root = local_repo
    ok(root, 'init')
    original = root / '.github/workflows/workorder.yml'
    path = original.with_name(filename)
    if path != original:
        original.rename(path)
    base = commit_all(root)
    path.write_text(path.read_text().replace('--all-tests', ''))
    for flags in [[], ['--approving-review'], ['--review', 'warn'], ['--allow', 'tests']]:
        result = cli(root, 'check', '--base', base, *flags)
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'workorder-allow:workflow' in result.stdout
        assert 'or an approving review' not in next(line for line in result.stdout.splitlines() if 'workorder-allow:workflow' in line and not line.startswith('Note:'))
    assert cli(root, 'check', '--base', base, '--allow', 'workflow').returncode == 0


def test_done_dirty_init_files_says_to_commit_setup(local_repo):
    root = local_repo
    task(root)
    commit_all(root)
    ok(root, 'init', '--claude')
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1
    assert 'commit the files written by workorder init' in result.stdout


def test_missing_test_cannot_be_called_unchanged_when_config_exists(local_repo):
    root = local_repo
    (root / 'pytest.ini').write_text('[pytest]\n')
    base = commit_all(root)
    record = {'id': 'TASK-1', 'test': 'pytest tests/test_missing.py'}
    unchanged, text = gate.test_origin(root, record, base, 'origin/main')
    assert not unchanged and 'landed on' not in text


def test_local_regression_ignores_unrelated_code_with_empty_recorded_paths(local_repo):
    root = local_repo
    base = finished_tasks(root)
    # An unrelated file existed at the time of the empty implementation receipt.
    (root / 'app.py').write_text('items = [1]\n')
    # A failure in the old test must not be run on this unrelated change.
    (root / 'calc.py').write_text('raise AssertionError("not selected")\n')
    # Record only app.py as changed to exercise the selection independently of the worktree.
    git(root, 'add', 'app.py')
    counts = {}
    assert gate.local_regression_problems(root, base, staged=True, counts=counts) == []
    assert counts.get('tests', 0) == 0


@pytest.mark.parametrize('jobs', [None, '2'])
@pytest.mark.parametrize('mode', ['plain', 'base', 'staged', 'done'])
def test_distinct_finished_commands_run_concurrently_in_fixed_order(local_repo, tmp_path, jobs, mode):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n')
    rendezvous = tmp_path / 'rendezvous'
    rendezvous.mkdir()
    for n in (1, 2):
        script = root / f'test_run{n}.py'
        script.write_text('import time\nfrom pathlib import Path\n'
                          f'p = Path({str(rendezvous)!r})\n(p / "{n}").touch()\n'
                          'deadline = time.monotonic() + 3\n'
                          'while len(list(p.iterdir())) < 2 and time.monotonic() < deadline: time.sleep(.02)\n'
                          'assert len(list(p.iterdir())) == 2, "commands did not overlap"\n'
                          f'print("command {n}")\n')
        fill_sections(task(root, f'TASK-{n}', ships='none', test=store.join_command([PYTHON, script.name])))
        path = root / f'tasks/TASK-{n}.md'
        path.write_text(path.read_text().replace('contract: "none"', 'contract: "none"\ncode_paths: ["app.py"]'))
    commit = commit_all(root)
    for n in (1, 2):
        common = dict(commit=commit, code=store.code_digest(root, commit))
        store.append(root, f'TASK-{n}', dict(common, kind='implemented', implementation_base=commit, implementation_paths={}))
        store.append(root, f'TASK-{n}', dict(common, kind='test', command=[PYTHON, f'test_run{n}.py'], exit_code=0, valid=True, no_code_change=True))
        store.set_task_field(root, f'TASK-{n}', 'status', 'DONE')
    if mode != 'plain':
        base = commit_all(root)
        git(root, 'checkout', '-qb', 'feature')
        (root / 'app.py').write_text('items = [1]\n')
        if mode == 'staged':
            git(root, 'add', '.')
            args = ['check', '--staged']
        elif mode == 'done':
            (root / 'test_third.py').write_text('from app import items\nassert items == [1]\n')
            fill_sections(task(root, 'TASK-3', ships='none', test=store.join_command([PYTHON, 'test_third.py'])))
            commit_all(root)
            args = ['done', '--task', 'TASK-3']
        else:
            args = ['check', '--base', base, '--run-tests']
    else:
        args = ['check', '--run-tests', '--all-tests']
    args += ['--jobs', jobs] if jobs else []
    result = cli(root, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode != 'staged':  # successful hook output stays quiet
        assert result.stdout.index('command 1') < result.stdout.index('command 2')
    else:
        assert 'ran 2 tasks' in result.stdout


@pytest.mark.parametrize('ci', [False, True])
def test_stub_runs_once_per_task_and_obeys_local_cache(local_repo, monkeypatch, ci):
    from workorder.cli import finish_task
    import shutil
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(TEST)
    commit_all(root)
    runs = []
    original = store.run_test

    def observe(command, cwd, *args, **kwargs):
        source = cwd / 'calc.py'
        if source.exists() and 'workorder stub' in source.read_text():
            runs.append(str(cwd))
        return original(command, cwd, *args, **kwargs)

    monkeypatch.setattr(store, 'run_test', observe)
    assert finish_task(root, 'TASK-2') == 0
    assert len(runs) == 1
    shutil.rmtree(root / '.git/workorder-tests', ignore_errors=True)
    git(root, 'add', '.')
    runs.clear()
    if ci:
        monkeypatch.setenv('CI', 'true')
    for _ in range(2):
        assert gate.check(root, staged=True) == []
    assert len(runs) == (2 if ci else 1)


def test_done_exposes_changed_test_origin_without_grouping(local_repo):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(TEST)
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-2')
    assert output.count("TASK-2's test was written in this change") == 1


def test_jobs_one_serializes_and_shared_commands_run_only_once(local_repo, tmp_path):
    root = local_repo
    counter = tmp_path / 'runs'
    script = ('from pathlib import Path; '
              f'p=Path({str(counter)!r}); p.write_text(p.read_text()+"run\\n" if p.exists() else "run\\n")')
    (root / 'run_shared.py').write_text(script + '\n')
    command = [PYTHON, 'run_shared.py']
    for n in (1, 2):
        fill_sections(task(root, f'TASK-{n}', ships='none', test=store.join_command(command)))
    commit = commit_all(root)
    for n in (1, 2):
        common = dict(commit=commit, code=store.code_digest(root, commit))
        store.append(root, f'TASK-{n}', dict(common, kind='implemented'))
        store.append(root, f'TASK-{n}', dict(common, kind='test', command=command, exit_code=0, valid=True, no_code_change=True))
        store.set_task_field(root, f'TASK-{n}', 'status', 'DONE')
    result = cli(root, 'check', '--run-tests', '--all-tests', '--jobs', '1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert counter.read_text() == 'run\n'


@pytest.mark.parametrize('scope', ['calc.py', './calc.py', 'src/../calc.py'])
def test_code_only_edit_selects_finished_task_in_precommit(local_repo, scope):
    root = local_repo
    finished_tasks(root)
    definition = root / 'tasks/TASK-1.md'
    definition.write_text(definition.read_text().replace('contract: "none"', f'contract: "none"\ncode_paths: [{json.dumps(scope)}]'))
    commit_all(root)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    git(root, 'add', 'calc.py')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 1 and "TASK-1's test fails on this code" in result.stdout


@pytest.mark.parametrize('action', ['done', 'check'])
def test_top_level_self_equality_cannot_finish_wrong_function(local_repo, action):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, 'test_calc.py'])))
    base = commit_all(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'calc.py').write_text('def mul(a, b):\n    return a + b\n')
    (root / 'test_calc.py').write_text('from calc import mul\nr = mul(-3, 4)\nassert r == r\n')
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1')
    else:
        claim(root, 'TASK-1')
        result = cli(root, 'check', '--base', base, '--run-tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'asserts nothing that can fail' in result.stdout


def test_legacy_receipt_still_protects_landed_code(local_repo):
    root = local_repo
    finished_tasks(root, legacy=True)
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    git(root, 'add', 'calc.py')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "TASK-1's test fails on this code" in result.stdout


def test_parallel_base_copy_does_not_restore_deleted_code_from_ignored_files(local_repo):
    root = local_repo
    base = finished_tasks(root)
    (root / 'calc.py').unlink()
    ignore = root / '.gitignore'
    ignore.write_text(ignore.read_text() + 'calc.py\n')
    commit_all(root)
    (root / 'calc.py').write_text(CODE)
    store.add_note(root, 'TASK-1', 'Check the code deletion')
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "cannot import this repository's own module 'calc'" in result.stdout
