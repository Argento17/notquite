"""Round-19 regressions using public commands and local Git repositories."""

import pytest

from workorder import gate, store
from test_workorder import cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import claim
from test_v15d import hooked_commit


OLD_CODE = 'def convert(word):\n    return {"true": True, "false": False}[word]\n'
NEW_CODE = ('def _normalize_bool_word(word):\n    return word.strip().lower()\n\n'
            'def convert(word):\n    return {"true": True, "false": False, "enable": True}'
            '[_normalize_bool_word(word)]\n')
OLD_TEST = 'from calc import convert\n\ndef test_old():\n    assert convert("true") is True\n'
HONEST = '\ndef test_enable_word():\n    assert convert("enable") is True\n'
CALLABLE = ('\ndef test_callable():\n    from calc import _normalize_bool_word\n'
            '    assert callable(_normalize_bool_word)\n')
SIGNATURE = ('\ndef test_signature():\n    import inspect\n    from calc import _normalize_bool_word\n'
             '    assert "word" in inspect.signature(_normalize_bool_word).parameters\n')
PARAMETRIZED = ('\nimport pytest\n\n@pytest.mark.parametrize("word", ["enable", " ENABLE "])\n'
                'def test_enable_word(word):\n    assert convert(word) is True\n')


def define(root, tid='TASK-1', command='python -m pytest -q tests/test_basic.py', code='calc.py'):
    path = task(root, tid, ships='none', test=command)
    fill_sections(path)
    path.write_text(path.read_text().replace('contract: "none"', f'contract: "none"\ncode_paths: ["{code}"]'))


def shared_file(root, added, *, task_first=True, implicit=False, old_test=OLD_TEST, other_code=''):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'calc.py').write_text(OLD_CODE + other_code)
    (root / 'tests/test_basic.py').write_text(old_test)
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    command = 'python -m pytest -q' + ('' if implicit else ' tests/test_basic.py')
    if task_first:
        define(root, command=command)
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    if not task_first:
        define(root, command=command)
    (root / 'calc.py').write_text(NEW_CODE + other_code)
    (root / 'tests/test_basic.py').write_text(old_test + added)
    commit_all(root)


def check(root, *flags):
    return cli(root, 'check', '--base', 'origin/main', '--run-tests', *flags)


@pytest.mark.parametrize('task_first', [True, False], ids=['task-first', 'same-branch'])
@pytest.mark.parametrize('action', ['done', 'check'])
@pytest.mark.parametrize('added,name', [(CALLABLE, 'test_callable'), (SIGNATURE, 'test_signature')],
                         ids=['callable', 'signature'])
def test_shared_file_inspection_does_not_prove_change(local_repo, task_first, action, added, name):
    root = local_repo
    shared_file(root, added, task_first=task_first)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1')
        assert store.record(root, 'TASK-1')['status'] != 'DONE'
        assert store.entries(root, 'TASK-1') == []
    else:
        # Receipts and a DONE claim accepted by 0.15 remain readable, but need review here.
        claim(root, 'TASK-1')
        commit_all(root)
        result = check(root)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert 'its new or changed tests still pass with the changed functions stubbed out' in output
    assert name in output


@pytest.mark.parametrize('added,note', [(HONEST, False), (HONEST + CALLABLE, True), (PARAMETRIZED, False)],
                         ids=['honest', 'mixed', 'parametrized'])
def test_shared_file_behavior_reaches_done(local_repo, added, note):
    root = local_repo
    shared_file(root, added)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert store.record(root, 'TASK-1')['status'] == 'DONE'
    lines = [line for line in output.splitlines() if line.startswith('Note:') and 'stubbed out' in line]
    assert len(lines) == int(note), output
    if note:
        assert 'test_callable' in lines[0] and 'test_enable_word' not in lines[0]
    commit_all(root)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.startswith('Note:') and 'stubbed out' in line]
    assert len(lines) == int(note), result.stdout


def test_only_changed_tests_must_fail_on_base(local_repo):
    root = local_repo
    old = OLD_TEST + HONEST
    added = '\ndef test_still_true():\n    assert convert("true") is True\n'
    shared_file(root, added, old_test=old)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'new or changed tests' in result.stdout and 'test_still_true' in result.stdout
    assert 'passes on' in result.stdout or 'pass on' in result.stdout


def test_duplicate_new_test_name_cannot_fall_back_to_old_tests(local_repo):
    root = local_repo
    shared_file(root, CALLABLE + CALLABLE)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'new or changed tests still pass' in result.stdout and 'test_callable' in result.stdout


def test_configured_python_functions_select_changed_tests(local_repo):
    root = local_repo
    shared_file(root, CALLABLE.replace('test_callable', 'check_callable'),
                old_test=OLD_TEST.replace('test_old', 'check_old'))
    config = root / 'pyproject.toml'
    config.write_text(config.read_text() + 'python_functions = ["check_*"]\n')
    result = gate.stub_problem(root, 'origin/main', store.record(root, 'TASK-1'))
    assert result is not None and 'new or changed tests still pass' in result
    assert 'check_callable' in result


@pytest.mark.parametrize('config,text', [
    ('pyproject.toml', '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'),
    ('pytest.ini', '[pytest]\ntestpaths = tests\n'),
    ('tox.ini', '[pytest]\ntestpaths = tests\n'),
    ('setup.cfg', '[tool:pytest]\ntestpaths = tests\n'),
    (None, None),
])
def test_whole_suite_new_done_hook_and_check_agree(local_repo, config, text):
    root = local_repo
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'calc.py').write_text(OLD_CODE)
    (root / 'tests/test_basic.py').write_text(OLD_TEST)
    if config:
        (root / config).write_text(text)
    ok(root, 'new', '--title', 'Enable word', '--owner', 'dev', '--test', 'python -m pytest -q',
       '--code-paths', 'calc.py')
    fill_sections(root / 'tasks/TASK-1.md')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    (root / 'calc.py').write_text(NEW_CODE)
    (root / 'tests/test_basic.py').write_text(OLD_TEST + HONEST)
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    git(root, 'add', '-A')
    result = hooked_commit(root)
    assert result.returncode == 0, result.stdout + result.stderr
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'no test files are known' not in result.stdout


@pytest.mark.parametrize('action', ['done', 'check'])
def test_whole_suite_selects_only_changed_tests(local_repo, action):
    root = local_repo
    shared_file(root, CALLABLE, implicit=True)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1')
    else:
        claim(root, 'TASK-1')
        commit_all(root)
        result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'new or changed tests still pass with the changed functions stubbed out (test_callable)' in result.stdout


def test_whole_suite_without_tests_refuses_new_before_writing(local_repo):
    before = {path.relative_to(local_repo): path.read_bytes() for path in local_repo.rglob('*')
              if path.is_file() and '.git' not in path.parts}
    result = cli(local_repo, 'new', '--title', 'Empty suite', '--owner', 'dev', '--test', 'python -m pytest -q')
    assert result.returncode == 1, result.stdout + result.stderr
    assert '--test-paths' in result.stdout + result.stderr
    after = {path.relative_to(local_repo): path.read_bytes() for path in local_repo.rglob('*')
             if path.is_file() and '.git' not in path.parts}
    assert after == before


@pytest.mark.parametrize('flags', ['--log-level INFO', '--'])
@pytest.mark.parametrize('implicit', [False, True], ids=['named-file', 'whole-suite'])
def test_selected_tests_preserve_pytest_options(local_repo, flags, implicit):
    root = local_repo
    shared_file(root, CALLABLE, implicit=implicit)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('pytest -q', f'pytest -q {flags}'))
    # The option is part of the initial task definition on both sides.
    result = gate.stub_problem(root, 'origin/main', store.record(root, 'TASK-1'))
    assert result is not None
    assert 'new or changed tests still pass' in result


def test_filtered_out_addition_keeps_whole_command_check(local_repo):
    root = local_repo
    shared_file(root, CALLABLE)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('pytest -q tests/', 'pytest -q -k old tests/'))
    result = gate.stub_problem(root, 'origin/main', store.record(root, 'TASK-1'))
    assert result is None, result


def test_globbed_testpaths_find_test_files(local_repo):
    root = local_repo
    (root / 'packages/one/tests').mkdir(parents=True)
    (root / 'packages/one/tests/test_basic.py').write_text(OLD_TEST)
    (root / 'pytest.ini').write_text('[pytest]\ntestpaths = packages/*/tests\n')
    output = ok(root, 'new', '--title', 'Nested suite', '--owner', 'dev', '--test', 'python -m pytest -q')
    assert 'TASK-1 opened' in output


@pytest.mark.parametrize('has_tests', [False, True])
def test_whole_suite_in_repository_without_commits(tmp_path, has_tests):
    root = tmp_path / 'unborn'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    if has_tests:
        (root / 'test_basic.py').write_text(OLD_TEST)
    result = cli(root, 'new', '--title', 'Suite', '--owner', 'dev', '--test', 'python -m pytest -q')
    assert result.returncode == int(not has_tests), result.stdout + result.stderr
    if not has_tests:
        assert '--test-paths' in result.stderr
        assert not (root / 'tasks').exists()


def test_pytest_usage_error_is_not_stub_evidence(local_repo):
    root = local_repo
    shared_file(root, CALLABLE)
    record = store.record(root, 'TASK-1')
    record['test'] += ' --not-a-pytest-option'
    result = gate.stub_problem(root, 'origin/main', record)
    assert result is not None and 'could not run' in result


@pytest.mark.parametrize('statement', ['test_old.__test__ = False', 'del test_old'])
def test_added_statement_disabling_old_test_needs_review(local_repo, statement):
    root = local_repo
    original = finished_folder(root)
    git(root, 'switch', '-c', 'disable-old')
    path = root / 'tests/types/test_Path.py'
    path.write_text(original + '\n' + statement + '\n' +
                    '\ndef test_remaining():\n    assert convert("false") is False\n')
    commit_all(root)
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'workorder-allow:TASK-1' in result.stdout


def test_added_test_name_can_match_an_existing_local(local_repo):
    root = local_repo
    finished_folder(root)
    path = root / 'tests/types/test_Path.py'
    path.write_text(OLD_TEST.replace('    assert', '    test_added = 1\n    assert'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'add-independent-test')
    path.write_text(path.read_text() + '\ndef test_added():\n    assert convert("false") is False\n')
    commit_all(root)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'workorder-allow:TASK-1' not in result.stdout


def finished_folder(root, parametrized=False):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests/types').mkdir(parents=True)
    (root / 'calc.py').write_text(OLD_CODE)
    text = (OLD_TEST if not parametrized else
            'import pytest\nfrom calc import convert\n\n@pytest.mark.parametrize("word,expected", '
            '[("true", True), ("false", False)])\ndef test_old(word, expected):\n'
            '    assert convert(word) is expected\n')
    (root / 'tests/types/test_Path.py').write_text(text)
    define(root, command='python -m pytest -q tests/types')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'refactor')
    (root / 'calc.py').write_text(OLD_CODE.replace('    return ', '    mapping = ').replace('[word]', '\n    return mapping[word]'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--refactor')
    commit_all(root)
    git(root, 'switch', 'main')
    git(root, 'merge', '--ff-only', 'refactor')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    return text


@pytest.mark.parametrize('edit', [False, True], ids=['addition', 'existing-test-edit'])
def test_finished_folder_two_clone_merge(local_repo, edit):
    root = local_repo
    original = finished_folder(root)
    other = root.parent / 'second'
    git(root.parent, 'clone', '-q', str(root), str(other))
    git(other, 'config', 'user.name', 'second')
    git(other, 'config', 'user.email', 'second@example.com')
    ok(other, 'init')
    git(other, 'switch', '-c', 'second-task')
    define(other, 'TASK-2', 'python -m pytest -q tests/types/test_Path.py', 'extra.py')
    (other / 'extra.py').write_text('def extra():\n    return 2\n')
    (other / 'tests/types/test_Path.py').write_text(original +
        '\ndef test_extra():\n    from extra import extra\n    assert extra() == 2\n')
    commit_all(other)
    ok(other, 'done', '--task', 'TASK-2')
    commit_all(other)
    # Main advances after TASK-2 finishes; its clone then commits the merge with the real hook.
    (root / 'unrelated.py').write_text('value = 1\n')
    commit_all(root)
    git(other, 'fetch', '-q', 'origin')  # local filesystem only
    git(other, 'merge', '--no-commit', 'origin/main')
    if edit:
        path = other / 'tests/types/test_Path.py'
        path.write_text(path.read_text().replace('convert("true") is True', 'convert("false") is False'))
    git(other, 'add', '-A')
    result = hooked_commit(other)
    assert result.returncode == 0, result.stdout + result.stderr
    if not edit:
        assert 'workorder-allow:TASK-1' not in result.stdout + result.stderr
    result = check(other)
    assert result.returncode == int(edit), result.stdout + result.stderr
    if edit:
        assert 'workorder-allow:TASK-1' in result.stdout
        for flag in [('--allow', 'TASK-1'), ('--approving-review',)]:
            accepted = check(other, *flag)
            assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    else:
        assert 'workorder-allow:TASK-1' not in result.stdout


@pytest.mark.parametrize('change', ['append', 'change', 'remove'])
def test_finished_parametrize_cases(local_repo, change):
    root = local_repo
    original = finished_folder(root, parametrized=True)
    git(root, 'switch', '-c', 'cases')
    if change == 'append':
        text = original.replace('("false", False)]', '("false", False), ("true", True)]')
    elif change == 'change':
        text = original.replace('("false", False)', '("true", True)')
    else:
        text = original.replace(', ("false", False)', '')
    (root / 'tests/types/test_Path.py').write_text(text)
    git(root, 'add', '-A')
    hook = hooked_commit(root)
    assert hook.returncode == 0, hook.stdout + hook.stderr
    result = check(root)
    assert result.returncode == int(change != 'append'), result.stdout + result.stderr
    if change == 'append':
        assert 'workorder-allow:TASK-1' not in result.stdout + hook.stdout + hook.stderr
    else:
        assert 'workorder-allow:TASK-1' in result.stdout
        accepted = check(root, '--allow', 'TASK-1')
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr


@pytest.mark.parametrize('declared', [True, False], ids=['code-paths', 'receipt-paths'])
def test_merge_renew_squash_survives_fresh_single_branch_clone(local_repo, declared):
    root = local_repo
    shared_file(root, HONEST, other_code='\n\ndef unchanged():\n    return 0\n\n\ndef unrelated():\n    return 41\n')
    if not declared:
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('code_paths: ["calc.py"]\n', ''))
        commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    git(root, 'switch', 'main')
    source = root / 'calc.py'
    source.write_text(source.read_text().replace('return 41', 'return 42'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', 'feature')
    git(root, 'merge', '--no-commit', 'main')
    expected = ('TASK-1: its code changed after it was finished (calc.py); '
                'run workorder renew --task TASK-1 and commit the receipts, '
                'so its evidence matches this code and survives a squash merge')
    hook = hooked_commit(root)
    assert hook.returncode == 0, hook.stdout + hook.stderr
    assert 'Note: ' + expected in hook.stdout + hook.stderr
    result = check(root)
    assert result.returncode == 1, result.stdout + result.stderr
    assert expected in result.stdout
    ok(root, 'renew', '--task', 'TASK-1')
    commit_all(root)
    result = check(root)
    assert result.returncode == 0, result.stdout + result.stderr
    git(root, 'switch', 'main')
    git(root, 'merge', '--squash', 'feature')
    commit_all(root)
    fresh = root.parent / 'fresh'
    git(root.parent, 'clone', '--single-branch', '--branch', 'main', '--no-local', str(root), str(fresh))
    output = ok(fresh, 'check', '--run-tests', '--all-tests')
    assert 'is not in this clone' not in output
