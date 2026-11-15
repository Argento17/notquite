"""Outside imports from undiscovered layouts cannot supply comparison evidence."""
import os
from pathlib import Path
import subprocess
import venv

import pytest

from test_environment import TEST_SITE

from workorder import gate, store
from test_workorder import cli, git, local_repo
from test_v05 import commit_all
from test_v15c_imports import setup_task


# A dependency-free PEP 660 backend, as in wo15c. pip writes real PEP 610
# metadata; the installed finder maps unipkg to pkg/unipkg outside the copy.
BACKEND = r'''import pathlib, zipfile

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    filename = 'wo_remapped-1.0-py3-none-any.whl'
    finder = "import sys, pathlib, importlib.util\nMAPPING = " + repr({'unipkg': str(pathlib.Path('pkg/unipkg').resolve())}) + "\n"
    finder += "class Finder:\n    @classmethod\n    def find_spec(cls, fullname, path=None, target=None):\n        if fullname in MAPPING:\n            return importlib.util.spec_from_file_location(fullname, pathlib.Path(MAPPING[fullname]) / '__init__.py')\n"
    finder += "def install():\n    sys.meta_path.append(Finder)\n"
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / filename, 'w') as z:
        z.writestr('__editable___wo_remapped_finder.py', finder)
        z.writestr('wo_remapped.pth', 'import __editable___wo_remapped_finder; __editable___wo_remapped_finder.install()\n')
        z.writestr('wo_remapped-1.0.dist-info/top_level.txt', 'unipkg\n')
        z.writestr('wo_remapped-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: wo-remapped\nVersion: 1.0\n')
        z.writestr('wo_remapped-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nGenerator: local-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('wo_remapped-1.0.dist-info/RECORD', '__editable___wo_remapped_finder.py,,\n')
    return filename
'''


def remapped_editable(root, tmp_path):
    environment = tmp_path / 'environment'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = Path(subprocess.check_output([str(python), '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'test_tools.pth').write_text(str(TEST_SITE) + '\n')
    (root / 'pkg/unipkg').mkdir(parents=True)
    (root / 'pkg/unipkg/__init__.py').write_text('def value():\n    return 1\n')
    (root / 'build_backend.py').write_text(BACKEND)
    (root / 'pyproject.toml').write_text(
        '[build-system]\nrequires = []\nbuild-backend = "build_backend"\nbackend-path = ["."]\n'
        '[project]\nname = "wo-remapped"\nversion = "1.0"\n'
        '[tool.setuptools]\npackages = ["unipkg"]\npackage-dir = {"unipkg" = "pkg/unipkg"}\n')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n*.egg-info/\n')
    result = subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-build-isolation',
        '--no-deps', '-e', str(root)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return str(python), site


@pytest.mark.parametrize('mode', ['feature', 'refactor'])
def test_remapped_editable_is_unavailable_not_evidence(local_repo, tmp_path, mode):
    root = local_repo
    python, site = remapped_editable(root, tmp_path)
    test = 'from unipkg import value\ndef test_value():\n    assert value() == 1\n'
    setup_task(root, python, test if mode == 'refactor' else '')
    (root / 'pkg/unipkg/__init__.py').write_text(
        'def value():\n    return ' + ('(1)' if mode == 'refactor' else '2') + '\n')
    if mode == 'feature':
        (root / 'tests/test_value.py').write_text(test.replace('== 1', '== 2'))
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1', *(['--refactor'] if mode == 'refactor' else []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'could not compare with origin/main: the tests import unipkg' in result.stdout
    assert 'still passes with the changed functions stubbed out' not in result.stdout
    assert "passes on origin/main's code too" not in result.stdout
    assert store.entries(root, 'TASK-1') == []


@pytest.mark.parametrize('metadata', ['top_level', 'record', 'finder', 'pth'])
def test_editable_inventory_covers_indirect_imports(local_repo, tmp_path, metadata):
    root = local_repo
    python, site = remapped_editable(root, tmp_path)
    info = next(site.glob('wo_remapped-*.dist-info'))
    if metadata != 'top_level':
        (info / 'top_level.txt').unlink()
    if metadata == 'record':
        # Editable distributions may declare their top-level sources in RECORD.
        with (info / 'RECORD').open('a') as file:
            file.write('\nunipkg/__init__.py,,\n')
    if metadata != 'finder':
        (site / 'wo_remapped.pth').write_text(str(root / 'pkg') + '\n')
        (site / '__editable___wo_remapped_finder.py').unlink()
        record = info / 'RECORD'
        record.write_text('\n'.join(line for line in record.read_text().splitlines()
                                    if line and not line.startswith('__editable__')) + '\n')
    # No literal unipkg import: only the interpreter's distribution metadata can
    # find it. The installed editable finder supplies the mapping fallback.
    base, command = setup_task(root, python,
        'import importlib\ndef test_value():\n    name = "uni" + "pkg"\n'
        '    assert importlib.import_module(name).value() == 1\n')
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert isinstance(code, str) and 'tests import unipkg' in code, (code, output)


@pytest.mark.parametrize('copy_kind', ['snapshot', 'ref'])
def test_imported_external_module_rechecked_after_cache_hit(local_repo, tmp_path, monkeypatch, copy_kind):
    from test_workorder import PYTHON
    root = local_repo
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'unseen.py').write_text('value = 1\n')
    (root / 'unseen.py').write_text('value = 1\n')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    base, command = setup_task(root, PYTHON,
        'from unseen import value\nimport pathlib, pytest\n'
        'def test_value():\n    assert value == 1\n')
    def run(destination):
        if copy_kind == 'ref':
            return gate.run_ref_test(root, destination, base, command, None, None)
        return gate.run_snapshot_test(root, destination, command, None, None)
    with store.test_reuse():
        code, output = run(tmp_path / 'seed')
    assert code == 0, output  # stdlib and ordinary site-packages must stay usable
    startup = tmp_path / 'startup'
    startup.mkdir()
    (startup / 'sitecustomize.py').write_text('import sys\nsys.path.insert(0, ' + repr(str(outside)) + ')\nimport unseen\n')
    monkeypatch.setenv('PYTHONPATH', str(startup))
    with store.test_reuse():
        code, output = run(tmp_path / 'copy')
    assert isinstance(code, str) and 'tests import unseen' in code, (code, output)


def test_environment_site_packages_on_pth_are_allowed(local_repo, tmp_path):
    environment = tmp_path / 'environment'
    venv.EnvBuilder().create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = Path(subprocess.check_output([str(python), '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'test_tools.pth').write_text(str(TEST_SITE) + '\n')
    (local_repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    base, command = setup_task(local_repo, str(python),
        'import pytest, pathlib\ndef test_value():\n    assert pathlib.Path(".").is_dir()\n')
    code, output = gate.run_ref_test(local_repo, tmp_path / 'copy', base, command, None, None)
    assert code == 0, (code, output)


def test_inline_test_imports_cannot_resolve_outside_copy(local_repo, tmp_path, monkeypatch):
    from test_workorder import PYTHON
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'foreign.py').write_text('value = 123\n')
    # foreign is this project's module, not an unrelated external dependency.
    (local_repo / 'foreign.py').write_text('value = 123\n')
    (outside / 'sitecustomize.py').write_text(f'import sys\nsys.path.insert(0, {str(outside)!r})\nimport foreign\n')
    commit_all(local_repo)
    monkeypatch.setenv('PYTHONPATH', str(outside))
    code, output = gate.run_snapshot_test(local_repo, tmp_path / 'copy',
        [PYTHON, '-c', 'from foreign import value; assert value == 123'], None, None)
    assert isinstance(code, str) and 'tests import foreign' in code, (code, output)
