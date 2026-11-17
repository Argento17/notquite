"""Editable dependencies are allowed; only the project's own imports are checked."""
import subprocess
import sys

import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo
from test_v05 import commit_all
from test_v15c_imports import BACKEND, editable, setup_task
from test_v15d_imports import remapped_editable


def install_helper(python, tmp_path):
    helper = tmp_path / 'helperlib'
    (helper / 'src/helperlib').mkdir(parents=True)
    (helper / 'src/helperlib/__init__.py').write_text('def value():\n    return 1\n')
    (helper / 'build_backend.py').write_text(BACKEND.replace('wo_sample', 'helperlib').replace('wo-sample', 'helperlib'))
    (helper / 'pyproject.toml').write_text(
        '[build-system]\nrequires = []\nbuild-backend = "build_backend"\nbackend-path = ["."]\n'
        '[project]\nname = "helperlib"\nversion = "1.0"\n')
    run = subprocess.run([python, '-m', 'pip', 'install', '--no-index', '--no-build-isolation',
                          '--no-deps', '-e', str(helper)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    return helper


@pytest.mark.parametrize('mode', ['feature', 'refactor'])
def test_app_with_editable_dependency_reaches_done(local_repo, tmp_path, mode):
    root = local_repo
    python = editable(root, tmp_path)
    install_helper(python, tmp_path)
    project = root / 'pyproject.toml'
    project.write_text(project.read_text() + '[project]\nname = "wo-sample"\nversion = "1.0"\n'
                       'dependencies = ["helperlib"]\n')
    source = root / 'src/wo_sample/__init__.py'
    source.write_text('import helperlib\ndef value():\n    return helperlib.value()\n')
    test = 'from wo_sample import value\ndef test_value():\n    assert value() == 1\n'
    setup_task(root, python, test if mode == 'refactor' else '')
    source.write_text('import helperlib\ndef value():\n    return helperlib.value() + '
                      + ('0' if mode == 'refactor' else '1') + '\n')
    if mode == 'feature':
        (root / 'tests/test_value.py').write_text(test.replace('== 1', '== 2'))
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1', *(['--refactor'] if mode == 'refactor' else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'TASK-1 DONE:' in result.stdout
    assert 'could not compare' not in result.stdout
    assert 'workorder-allow:' not in result.stdout
    assert store.record(root, 'TASK-1')['status'] == 'DONE'
    commit_all(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'workorder-allow:' not in result.stdout


@pytest.mark.parametrize('metadata', ['pyproject', 'setup.cfg'])
@pytest.mark.parametrize('checkout', ['original', 'second-clone'])
def test_sibling_dependency_does_not_hide_remapped_project(local_repo, tmp_path, metadata, checkout):
    root = local_repo
    python, site = remapped_editable(root, tmp_path)
    install_helper(python, tmp_path)
    project = root / 'pyproject.toml'
    text = project.read_text()
    if metadata == 'setup.cfg':
        project.write_text(text.replace('[project]\nname = "wo-remapped"\nversion = "1.0"\n', ''))
        (root / 'setup.cfg').write_text('[metadata]\nname = WO...ReMaPpEd\nversion = 1.0\n')
    else:
        project.write_text(text.replace('name = "wo-remapped"', 'name = "WO___ReMaPpEd"'))
    test = ('import helperlib\nfrom unipkg import value\ndef test_value():\n'
            '    assert value() == helperlib.value()\n')
    base, command = setup_task(root, python, test)
    if checkout == 'second-clone':
        clone = tmp_path / 'clone'
        git(tmp_path, 'clone', '-q', str(root), str(clone))
        git(clone, 'config', 'user.name', 'tester')
        git(clone, 'config', 'user.email', 'tester@example.invalid')
        git(clone, 'update-ref', 'refs/remotes/origin/main', base)
        root = clone
    (root / 'pkg/unipkg/__init__.py').write_text('def value():\n    return 0 + 1\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1', '--refactor')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'could not compare with origin/main: the tests import unipkg' in result.stdout
    assert 'tests import helperlib' not in result.stdout
    assert 'pythonpath =' not in result.stdout
    assert "install the project non-editable or run the tests so they import this checkout's code" in result.stdout
    assert store.entries(root, 'TASK-1') == []


def test_inline_dependency_outside_repository_is_allowed(local_repo, tmp_path, monkeypatch):
    outside = tmp_path / 'dependency'
    outside.mkdir()
    (outside / 'foreign.py').write_text('value = 123\n')
    monkeypatch.setenv('PYTHONPATH', str(outside))
    code, output = gate.run_snapshot_test(local_repo, tmp_path / 'copy',
        [PYTHON, '-c', 'from foreign import value; assert value == 123'], None, None)
    assert code == 0, (code, output)


def test_flat_python_module_does_not_block_non_python_command(local_repo, tmp_path):
    # local_repo contains app.py; it does not turn a native command into Python.
    code, output = gate.run_snapshot_test(local_repo, tmp_path / 'copy',
                                         ['git', '--version'], None, None)
    assert code == 0 and 'git version' in output, (code, output)


@pytest.mark.parametrize('metadata', ['project = "unrelated"\n', '[[project]]\nname = "unrelated"\n'])
def test_invalid_project_metadata_does_not_stop_other_test_tools(local_repo, tmp_path, metadata):
    (local_repo / 'pyproject.toml').write_text(metadata)
    commit_all(local_repo)
    code, output = gate.run_snapshot_test(local_repo, tmp_path / 'copy',
        [PYTHON, '-c', 'print("TEST RAN")'], None, None)
    assert code == 0 and 'TEST RAN' in output, (code, output)


@pytest.mark.parametrize('layout', ['src', 'flat'])
def test_outside_project_advice_names_its_source_root(local_repo, tmp_path, monkeypatch, layout):
    root = local_repo
    source = root / 'src' if layout == 'src' else root
    (source / 'ownpkg').mkdir(parents=True)
    (source / 'ownpkg/__init__.py').write_text('value = 1\n')
    (root / '.gitignore').write_text('__pycache__/\n')
    base, command = setup_task(root, PYTHON, 'from ownpkg import value\ndef test_value():\n    assert value == 1\n')
    startup = tmp_path / 'startup'
    startup.mkdir()
    (startup / 'sitecustomize.py').write_text(f'import sys\nsys.path.insert(0, {str(source)!r})\nimport ownpkg\n')
    monkeypatch.setenv('PYTHONPATH', str(startup))
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert isinstance(code, str) and 'tests import ownpkg' in code, (code, output)
    expected = 'src' if layout == 'src' else '.'
    assert f'pythonpath = ["{expected}"]' in code


@pytest.mark.parametrize('value', ['"""wo-remapped"""', r'"wo\u002dremapped"'])
@pytest.mark.parametrize('without_tomllib', [False, True])
def test_remapped_project_name_accepts_toml_strings(local_repo, tmp_path, monkeypatch, value, without_tomllib):
    python, site = remapped_editable(local_repo, tmp_path)
    project = local_repo / 'pyproject.toml'
    project.write_text(project.read_text().replace('"wo-remapped"', value))
    base, command = setup_task(local_repo, python, 'from unipkg import value\ndef test_value():\n    assert value() == 1\n')
    if without_tomllib:
        monkeypatch.setitem(sys.modules, 'tomllib', None)
    code, output = gate.run_ref_test(local_repo, tmp_path / 'copy', base, command, None, None)
    assert isinstance(code, str) and 'tests import unipkg' in code, (code, output)
