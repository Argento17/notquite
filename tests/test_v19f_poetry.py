"""init installs a Poetry project's test or dev dependencies in the generated workflow (found on rich)."""

import shlex
import sys

import pytest

from workorder import bootstrap
from test_workorder import cli, local_repo  # noqa: F401  (fixture)


RICH = ('[tool.poetry]\nname = "demo"\nversion = "1.0"\n\n'
        '[tool.poetry.dependencies]\npython = ">=3.9.0"\npygments = "^2.13.0"\n\n'
        '[tool.poetry.dev-dependencies]\npytest = "^7.0.0"\n\n'
        '[build-system]\nrequires = ["poetry-core>=1.0.0"]\nbuild-backend = "poetry.core.masonry.api"\n\n'
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
GROUPS = ('[tool.poetry]\nname = "demo"\nversion = "1.0"\n\n'
          '[tool.poetry.group.test]\noptional = true\n\n'
          '[tool.poetry.group.test.dependencies]\npytest = "^8.0"\nhypothesis = { version = "~6.1", extras = ["cli"] }\n'
          'local = { path = "../local" }  # not a pip requirement\n\n'
          '[tool.poetry.group.dev.dependencies]\nruff = "*"\nfreezegun = "1.5.1"\nzero = "^0.2.3"\n'
          'either = "^1.0 || ^2.0"\n\n'
          '[tool.poetry.group.docs.dependencies]\nsphinx = "*"\n')
CASES = {'dev-dependencies': (RICH, ["'pytest>=7.0.0,<8'"], 'dev'),
         'groups': (GROUPS, ["'pytest>=8.0,<9'", "'hypothesis[cli]>=6.1,<6.2'", 'ruff', 'freezegun==1.5.1',
                             "'zero>=0.2.3,<0.3'", 'either'], 'test and dev')}


@pytest.mark.parametrize('case', sorted(CASES))
def test_init_installs_poetry_test_dependencies(local_repo, case):
    text, requirements, groups = CASES[case]
    (local_repo / 'pyproject.toml').write_text(text)
    result = cli(local_repo, 'init')
    assert result.returncode == 0, result.stdout + result.stderr
    command = ' '.join(['python -m pip install -e .', *requirements])
    workflow = (local_repo / '.github/workflows/workorder.yml').read_text()
    assert f'      - name: Install project test dependencies\n        run: {command}\n' in workflow
    assert 'No project test dependencies detected' not in workflow and 'sphinx' not in workflow
    assert (f'CI dependencies: {command} (from the Poetry {groups} dependencies in pyproject.toml; '
            'after changing them, run workorder init --update-workflow)') in result.stdout
    assert 'no project test dependencies detected' not in result.stdout.lower()


@pytest.mark.parametrize('case', sorted(CASES))
def test_poetry_dependencies_without_tomllib(tmp_path, monkeypatch, case):
    """Python 3.10 has no tomllib; the line scan finds the same groups and requirements."""
    text, requirements, groups = CASES[case]
    path = tmp_path / 'pyproject.toml'
    path.write_text(text)
    found = bootstrap.poetry_dependencies(path)
    assert found == (groups.split(' and '), [shlex.split(item)[0] for item in requirements])
    monkeypatch.setitem(sys.modules, 'tomllib', None)  # import tomllib raises ImportError
    assert bootstrap.poetry_dependencies(path) == found


def test_a_poetry_project_without_a_test_or_dev_group_is_not_guessed(local_repo):
    (local_repo / 'pyproject.toml').write_text(
        '[tool.poetry]\nname = "demo"\nversion = "1.0"\n\n[tool.poetry.group.docs.dependencies]\nsphinx = "*"\n')
    result = cli(local_repo, 'init')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'CI dependencies: no project test dependencies detected' in result.stdout
    assert 'poetry install' not in (local_repo / '.github/workflows/workorder.yml').read_text()
