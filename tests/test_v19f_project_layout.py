"""The base rerun of failing project tests uses the rootdir and configuration file of the current run.

Found on rich: tests/pytest.ini holds only junit_family, the full run's rootdir is the
repository (pyproject.toml), and naming tests/... nodes on the base made pytest choose
tests/ instead, so every node id changed and nothing was collected (exit 5).
"""

import pytest

from test_workorder import cli, git, local_repo  # noqa: F401  (fixture)
from test_v05 import commit_all
from test_v16b_project import settings


OLD = 'def test_old():\n    assert False, "already broken on the base"\n'
LAYOUTS = {
    # name: (files, project test command, folder the node ids are relative to)
    'subfolder ini under pyproject testpaths': (
        {'pyproject.toml': '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
         'tests/pytest.ini': '[pytest]\njunit_family = legacy\n'},
        'python -m pytest -q', 'tests/'),
    'subfolder ini, no root configuration': (
        {'tests/pytest.ini': '[pytest]\njunit_family = legacy\n'},
        'python -m pytest -q', 'tests/'),
    'subfolder ini under root pytest.ini testpaths': (
        {'pytest.ini': '[pytest]\ntestpaths = tests\n', 'tests/setup.cfg': '[tool:pytest]\nxfail_strict = true\n'},
        'python -m pytest -q', 'tests/'),
    'rootdir option': (
        {'tests/pytest.ini': '[pytest]\njunit_family = legacy\n'},
        'python -m pytest -q --rootdir=tests', ''),
    'config file option': (
        {'tests/pytest.ini': '[pytest]\njunit_family = legacy\n'},
        'python -m pytest -q -c tests/pytest.ini', ''),
}


def layout(root, name, *, new_failure):
    files, command, prefix = LAYOUTS[name]
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    for path, text in files.items():
        (root / path).write_text(text)
    (root / 'value.py').write_text('value = 1\n')
    (root / 'tests/test_old.py').write_text(OLD)
    (root / 'tests/test_value.py').write_text('import value\n\ndef test_value():\n    assert value.value == 1\n')
    settings(root, command)
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-q', '-c', 'feature')
    (root / 'value.py').write_text('value = 2\n' if new_failure else 'value = 1\nother = 2\n')
    commit_all(root)
    return prefix


@pytest.mark.parametrize('name', sorted(LAYOUTS))
def test_a_test_failing_on_the_base_too_is_only_a_note(local_repo, name):
    root = local_repo
    prefix = layout(root, name, new_failure=False)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f'Note: the project test also fails on origin/main: {prefix}test_old.py::test_old' in output
    assert 'could not compare' not in output
    assert git(root, 'status', '--porcelain', '--untracked-files=all') == ''


@pytest.mark.parametrize('name', sorted(LAYOUTS))
def test_a_new_failure_is_still_refused_in_the_same_layouts(local_repo, name):
    root = local_repo
    prefix = layout(root, name, new_failure=True)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert (f'the project test fails on this code: {prefix}test_value.py::test_value (passes on origin/main)'
            in output)
    assert f'Note: the project test also fails on origin/main: {prefix}test_old.py::test_old' in output
    assert 'could not compare' not in output


def test_a_configuration_file_added_in_this_change_is_absent_on_the_base(local_repo):
    """Pinning the current run's pytest.ini must not name a file the base does not have."""
    root = local_repo
    layout(root, 'subfolder ini, no root configuration', new_failure=False)
    (root / 'pytest.ini').write_text('[pytest]\nxfail_strict = true\n')
    commit_all(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    # The changed configuration is its own finding; the old failure is still compared, not refused.
    assert result.returncode == 1 and 'tests: pytest configuration changed since origin/main' in output, output
    assert 'Note: the project test also fails on origin/main: tests/test_old.py::test_old' in output
    assert 'could not compare' not in output
