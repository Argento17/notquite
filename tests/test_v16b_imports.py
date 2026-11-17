"""Pytest import settings and new packages retain honest comparison evidence."""
import pytest

from workorder import gate, pytest_selection, store
from workorder.python_imports import ImportFailure
from test_workorder import cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v15c_imports import editable, setup_task


TEST_VALUE = 'from wo_sample import value\ndef test_value():\n    assert value() == 1\n'


@pytest.mark.parametrize('flag', ['-I', '-E'])
@pytest.mark.parametrize('config', ['pyproject', 'ini', 'cfg', 'nested', 'explicit'])
def test_probe_applies_selected_pytest_pythonpath(local_repo, tmp_path, flag, config):
    root = local_repo
    python = editable(root, tmp_path)
    if config == 'pyproject':
        project = root / 'pyproject.toml'
        project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    elif config == 'ini':
        (root / 'pytest.ini').write_text('[pytest]\npythonpath = src\n')
    elif config == 'cfg':
        (root / 'setup.cfg').write_text('[tool:pytest]\npythonpath = src\n')
    elif config == 'nested':
        (root / 'tests').mkdir()
        (root / 'tests/pytest.ini').write_text('[pytest]\npythonpath = ../src\n')
    else:
        (root / 'settings').mkdir()
        (root / 'settings/custom.ini').write_text('[pytest]\npythonpath = ../src\n')
        # This ignored config must not override the explicitly selected file.
        (root / 'pytest.ini').write_text('[pytest]\npythonpath = missing\n')
    base, command = setup_task(root, python, TEST_VALUE)
    command.insert(1, flag)
    if config == 'explicit':
        command += ['-c', 'settings/custom.ini']
    if config == 'pyproject':
        command = tuple(command)  # Parallel test workers retain immutable commands.
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert code == 0, (code, output)
    assert '1 passed' in output


def test_probe_does_not_combine_ignored_pytest_config(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    (root / 'pytest.ini').write_text('[pytest]\n')
    base, command = setup_task(root, python, TEST_VALUE)
    command.insert(1, '-I')
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert isinstance(code, ImportFailure) and 'outside the comparison copy' in code, (code, output)


def test_isolated_editable_refactor_uses_working_pythonpath_advice(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    (root / 'tests').mkdir()
    (root / 'tests/test_value.py').write_text(TEST_VALUE)
    command = [python, '-I', '-m', 'pytest', '-q', 'tests/test_value.py']
    fill_sections(task(root, ships='none', test=store.join_command(command)))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'src/wo_sample/__init__.py').write_text('def value():\n    return 0 + 1\n')
    commit_all(root)
    refused = cli(root, 'done', '--task', 'TASK-1', '--refactor')
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert 'could not compare' in refused.stdout
    assert 'pythonpath = ["src"]' in refused.stdout
    assert store.entries(root, 'TASK-1') == []
    # Configure both revisions, so this tests import advice without requiring a
    # separate acceptance decision for newly changed pytest configuration.
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    configured = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', configured)
    (root / 'src/wo_sample/__init__.py').write_text('def value():\n    return 1 + 0\n')
    commit_all(root)
    done = cli(root, 'done', '--task', 'TASK-1', '--refactor')
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'TASK-1 DONE:' in done.stdout


@pytest.mark.parametrize('runner', ['pytest', 'script'])
def test_new_editable_package_counts_as_failing_base(local_repo, tmp_path, runner):
    root = local_repo
    # Keep existing unrelated source in base: only imported project packages
    # decide whether the new-code comparison is already known to fail.
    (root / 'oldpkg').mkdir()
    (root / 'oldpkg/__init__.py').write_text('value = 9\n')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    python = editable(root, tmp_path)
    (root / 'tests').mkdir()
    text = TEST_VALUE if runner == 'pytest' else 'from wo_sample import value\nassert value() == 1\n'
    (root / 'tests/test_value.py').write_text(text)
    command = ([python, '-m', 'pytest', '-q', 'tests/test_value.py'] if runner == 'pytest'
               else [python, 'tests/test_value.py'])
    fill_sections(task(root, ships='none', test=store.join_command(command)))
    commit_all(root)
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None,
                                   overlays={'tests/test_value.py': (text.encode(), 0o100644)})
    assert isinstance(code, int) and code != 0, (code, output)
    assert 'wo_sample' in output
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'TASK-1 DONE:' in result.stdout
    assert 'could not compare' not in result.stdout


@pytest.mark.parametrize('flag', ['-I', '-E'])
def test_isolated_editable_feature_reaches_done(local_repo, tmp_path, flag):
    root = local_repo
    python = editable(root, tmp_path)
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    (root / 'tests').mkdir()
    command = [python, flag, '-m', 'pytest', '-q', 'tests/test_value.py']
    fill_sections(task(root, ships='none', test=store.join_command(command)))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'src/wo_sample/__init__.py').write_text('def value():\n    return 2\n')
    (root / 'tests/test_value.py').write_text(TEST_VALUE.replace('== 1', '== 2'))
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'TASK-1 DONE:' in result.stdout


def test_pytest_pythonpath_precedes_interpreter_startup_path(local_repo, tmp_path, monkeypatch):
    root = local_repo
    python = editable(root, tmp_path)
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    base, command = setup_task(root, python, TEST_VALUE)
    startup = tmp_path / 'startup'
    startup.mkdir()
    (startup / 'sitecustomize.py').write_text(f'import sys\nsys.path.insert(0, {str(root / "src")!r})\n')
    monkeypatch.setenv('PYTHONPATH', str(startup))
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert code == 0, (code, output)


def test_configured_isolated_broken_refactor_is_refused(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    base, command = setup_task(root, python, TEST_VALUE)
    command.insert(1, '-I')
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(root), str(clone))
    git(clone, 'config', 'user.name', 'tester')
    git(clone, 'config', 'user.email', 'tester@example.invalid')
    git(clone, 'update-ref', 'refs/remotes/origin/main', base)
    fill_sections(task(clone, ships='none', test=store.join_command(command)))
    (clone / 'src/wo_sample/__init__.py').write_text('def value():\n    return 99\n')
    commit_all(clone)
    result = cli(clone, 'done', '--task', 'TASK-1', '--refactor')
    assert result.returncode == 1, result.stdout + result.stderr
    assert store.entries(clone, 'TASK-1') == []


def test_new_package_does_not_hide_existing_import_outside_copy(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    # Keep an imported editable package in base and add a second package later.
    base, command = setup_task(root, python, TEST_VALUE)
    (root / 'src/newpkg').mkdir()
    (root / 'src/newpkg/__init__.py').write_text('value = 9\n')
    text = 'import newpkg\n' + TEST_VALUE
    (root / 'tests/test_value.py').write_text(text)
    command.insert(1, '-I')
    commit_all(root)
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None,
                                   overlays={'tests/test_value.py': (text.encode(), 0o100644)})
    assert isinstance(code, ImportFailure) and 'outside the comparison copy' in code, (code, output)


def test_optional_new_import_does_not_supply_failing_base(local_repo, tmp_path):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    base = commit_all(root)
    python = editable(root, tmp_path)
    (root / 'tests').mkdir()
    text = ('try:\n    import wo_sample\nexcept ImportError:\n    pass\n'
            'def test_value():\n    assert 1 == 1\n')
    (root / 'tests/test_value.py').write_text(text)
    commit_all(root)
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base,
                                   [python, '-m', 'pytest', '-q', 'tests/test_value.py'], None, None,
                                   overlays={'tests/test_value.py': (text.encode(), 0o100644)})
    assert isinstance(code, ImportFailure), (code, output)


def test_isolated_selection_loads_plugin_without_exposing_checkout(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_value.py').write_text(TEST_VALUE + '\ndef test_other():\n    assert False\n')
    # Root modules must stay unavailable under -I unless pytest config names
    # them. Loading the selection plugin must not weaken that isolation.
    (root / 'root_only.py').write_text('value = 1\n')
    (root / 'tests/test_value.py').write_text(
        'import importlib.util\nfrom wo_sample import value\n'
        'def test_value():\n    assert value() == 1\n'
        '    assert importlib.util.find_spec("root_only") is None\n'
        'def test_other():\n    assert False\n')
    for relative, (content, _) in pytest_selection.overlay(['tests/test_value.py::test_value']).items():
        (root / relative).write_bytes(content)
    command = pytest_selection.command([python, '-I', '-m', 'pytest', '-q', 'tests/test_value.py'],
                                       ['tests/test_value.py::test_value'])
    result = store.run_test(command, root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 passed, 1 deselected' in result.stdout


def test_ignored_new_package_import_does_not_supply_failing_base(local_repo, tmp_path):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    base = commit_all(root)
    python = editable(root, tmp_path)
    (root / 'tests').mkdir()
    other = 'def test_other():\n    assert 1 == 1\n'
    (root / 'tests/test_value.py').write_text(TEST_VALUE)
    (root / 'tests/test_other.py').write_text(other)
    commit_all(root)
    command = [python, '-m', 'pytest', '-q', 'tests', '--ignore', 'tests/test_value.py']
    # Include a selected test with no project import: the ignored file cannot
    # prove that the tests would fail without the new package.
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None,
        overlays={'tests/test_value.py': (TEST_VALUE.encode(), 0o100644),
                  'tests/test_other.py': (other.encode(), 0o100644)})
    assert isinstance(code, ImportFailure), (code, output)


def test_probe_accepts_options_defined_by_conftest(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '\n[tool.pytest.ini_options]\npythonpath = ["src"]\n')
    (root / 'conftest.py').write_text(
        'def pytest_addoption(parser):\n    parser.addoption("--sample-mode", action="store_true")\n')
    base, command = setup_task(root, python, TEST_VALUE)
    command.insert(1, '-I')
    command.append('--sample-mode')
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert code == 0, (code, output)


@pytest.mark.parametrize('runner', ['pytest', 'pytest-multiple', 'script'])
@pytest.mark.parametrize('helper_import', ['import setup_imports', 'from setup_imports import installed'])
def test_import_side_effect_cannot_supply_failing_base(local_repo, tmp_path, runner, helper_import):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'tests/setup_imports.py').write_text(
        'import sys, types\n'
        'installed = types.ModuleType("wo_sample")\n'
        'installed.value = 7\n'
        'sys.modules["wo_sample"] = installed\n')
    base = commit_all(root)
    python = editable(root, tmp_path)
    # No changed function exists for the stub check to catch: the local helper
    # makes the new package importable even on code predating that package.
    (root / 'src/wo_sample/__init__.py').write_text('value = 7\n')
    text = (helper_import + '\n' if runner != 'pytest-multiple' else '') + 'import wo_sample\n'
    text += ('def test_value():\n    assert wo_sample.value == 7\n' if runner != 'script'
             else 'assert wo_sample.value == 7\n')
    (root / 'tests/test_value.py').write_text(text)
    overlays = {'tests/test_value.py': (text.encode(), 0o100644)}
    if runner == 'pytest-multiple':
        setup = helper_import + '\ndef test_setup():\n    assert True\n'
        (root / 'tests/test_setup.py').write_text(setup)
        overlays['tests/test_setup.py'] = (setup.encode(), 0o100644)
    commit_all(root)
    command = ([python, '-m', 'pytest', '-q', 'tests/test_value.py'] if runner != 'script'
               else [python, 'tests/test_value.py'])
    if runner == 'pytest-multiple':
        command.insert(-1, 'tests/test_setup.py')
    copy = tmp_path / 'copy'
    code, output = gate.run_ref_test(root, copy, base, command, None, None, overlays=overlays)
    actual = store.run_test(command, copy)
    assert actual.returncode == 0, actual.stdout + actual.stderr
    assert isinstance(code, ImportFailure), (code, output)
