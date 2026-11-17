"""Standard editable installs serve the comparison copy, never the work tree.

Each backend's install is written as its files would be, without network or
build tools: a .pth path line (hatchling, flit_core, poetry-core, pdm-backend,
setuptools' src layout and compat mode), setuptools' editable finder (its
MAPPING/NAMESPACES template) or the editables redirector (hatchling
dev-mode-exact, pdm-backend's editables backend). Every case also exposes a
namespace folder, tools/ (as pydantic's docs/: a name the package inventory skips,
found only through the .pth; docs/ itself would meet Workorder's own docs/ that
the test CLI puts on PYTHONPATH), which spans every sys.path folder.
"""
import os
from pathlib import Path
import subprocess
import venv

import pytest

from test_environment import TEST_SITE

from workorder import gate
from workorder.python_imports import ImportFailure
from test_workorder import PACKAGE, PYTHON, git
from test_workorder import local_repo  # noqa: F401  (fixture)
from test_v05 import commit_all
from test_v15c_imports import setup_task


# setuptools' editable_wheel _FINDER_TEMPLATE (setuptools 68 to 80), filled in.
SETUPTOOLS_FINDER = '''import sys
from importlib.machinery import ModuleSpec, PathFinder
from importlib.machinery import all_suffixes as module_suffixes
from importlib.util import spec_from_file_location
from itertools import chain
from pathlib import Path

MAPPING = {mapping!r}
NAMESPACES = {namespaces!r}
PATH_PLACEHOLDER = {name!r} + ".__path_hook__"


class _EditableFinder:  # MetaPathFinder
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if fullname in MAPPING:
            pkg_path = MAPPING[fullname]
            return cls._find_spec(fullname, Path(pkg_path))
        parent, _, child = fullname.rpartition(".")
        if parent and parent in MAPPING:
            return PathFinder.find_spec(fullname, path=[MAPPING[parent]])
        return None

    @classmethod
    def _find_spec(cls, fullname, candidate_path):
        init = candidate_path / "__init__.py"
        candidates = (candidate_path.with_suffix(x) for x in module_suffixes())
        for candidate in chain([init], candidates):
            if candidate.exists():
                return spec_from_file_location(fullname, candidate)


class _EditableNamespaceFinder:  # PathEntryFinder
    @classmethod
    def _path_hook(cls, path):
        if path == PATH_PLACEHOLDER:
            return cls
        raise ImportError

    @classmethod
    def _paths(cls, fullname):
        return NAMESPACES[fullname] or MAPPING.get(fullname) or [PATH_PLACEHOLDER]

    @classmethod
    def find_spec(cls, fullname, target=None):
        if fullname in NAMESPACES:
            spec = ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = cls._paths(fullname)
            return spec
        return None

    @classmethod
    def find_module(cls, fullname):
        return None


def install():
    if not any(finder == _EditableFinder for finder in sys.meta_path):
        sys.meta_path.append(_EditableFinder)
    if not NAMESPACES:
        return
    if not any(hook == _EditableNamespaceFinder._path_hook for hook in sys.path_hooks):
        sys.path_hooks.append(_EditableNamespaceFinder._path_hook)
    if PATH_PLACEHOLDER not in sys.path:
        sys.path.append(PATH_PLACEHOLDER)
'''

# editables.redirector (editables 0.5), which hatchling and pdm-backend install.
REDIRECTOR = '''import importlib.abc, importlib.util, sys


class RedirectingFinder(importlib.abc.MetaPathFinder):
    _redirections = {}

    @classmethod
    def map_module(cls, name, path):
        cls._redirections[name] = path

    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if "." in fullname:
            return None
        if path is not None:
            return None
        try:
            redir = cls._redirections[fullname]
        except KeyError:
            return None
        return importlib.util.spec_from_file_location(fullname, redir)

    @classmethod
    def install(cls):
        for f in sys.meta_path:
            if f == cls:
                break
        else:
            sys.meta_path.append(cls)

    @classmethod
    def invalidate_caches(cls):
        pass
'''

def redirector_files(target):
    return {'_wo_sample.pth': 'import _editable_impl_wo_sample\n',
            '_editable_impl_wo_sample.py': ('from editables.redirector import RedirectingFinder as F\nF.install()\n'
                                            f'F.map_module({"wo_sample"!r}, {str(target)!r})\n')}


def finder_files(mapping, namespaces):
    return {'__editable__.wo_sample-1.0.pth': ('import __editable___wo_sample_1_0_finder; '
                                               '__editable___wo_sample_1_0_finder.install()\n'),
            '__editable___wo_sample_1_0_finder.py': SETUPTOOLS_FINDER.format(
                mapping=mapping, namespaces=namespaces, name='__editable__.wo_sample-1.0.finder')}


# backend: (layout, files written to site-packages as a function of the project root)
# layout: flat = wo_sample/ and tools/plugins/ at the root; src = under src/;
# lib = wo_sample under lib/ (a package-dir remap), namespace tools/ at the root.
BACKENDS = {
    'hatchling': ('flat', lambda root: {'_editable_impl_wo_sample.pth': f'{root}\n'}),
    'hatchling-src': ('src', lambda root: {'_editable_impl_wo_sample.pth': f'{root / "src"}\n'}),
    'hatchling-exact': ('flat', lambda root: redirector_files(root / 'wo_sample/__init__.py')),
    'setuptools-finder': ('flat', lambda root: finder_files(
        {'wo_sample': str(root / 'wo_sample')},
        {'tools': [str(root / 'tools')], 'tools.plugins': [str(root / 'tools/plugins')]})),
    'setuptools-src': ('src', lambda root: {'__editable__.wo_sample-1.0.pth': f'{root / "src"}\n'}),
    'setuptools-compat': ('flat', lambda root: {'__editable__.wo_sample-1.0.pth': f'{root}\n'}),
    'flit_core': ('flat', lambda root: {'wo_sample.pth': f'{root}\n'}),
    'flit_core-src': ('src', lambda root: {'wo_sample.pth': f'{root / "src"}\n'}),
    'poetry-core': ('flat', lambda root: {'wo_sample.pth': f'{root}\n'}),
    'poetry-core-src': ('src', lambda root: {'wo_sample.pth': f'{root / "src"}\n'}),
    'pdm-backend': ('src', lambda root: {'wo_sample.pth': f'{root / "src"}\n'}),
    'pdm-backend-editables': ('src', lambda root: redirector_files(root / 'src/wo_sample/__init__.py')),
}
# Package-dir remaps are neither layout: they keep the refusal (as in test_v15d_imports).
REMAPPED = {
    'setuptools-remapped': ('lib', lambda root: finder_files({'wo_sample': str(root / 'lib/wo_sample')}, {})),
    'editables-remapped': ('lib', lambda root: redirector_files(root / 'lib/wo_sample/__init__.py')),
}


def folders(layout):
    package = {'flat': 'wo_sample', 'src': 'src/wo_sample', 'lib': 'lib/wo_sample'}[layout]
    namespace = 'src/wo_ns/plugins' if layout == 'src' else 'tools/plugins'
    return package, namespace


def write_code(root, layout, value):
    package, namespace = folders(layout)
    (root / package).mkdir(parents=True, exist_ok=True)
    (root / package / '__init__.py').write_text(f'def value():\n    return {value!r}\n')
    (root / namespace).mkdir(parents=True, exist_ok=True)
    (root / namespace / 'hook.py').write_text(f'VALUE = {value!r}\n')


def install(root, tmp_path, backend):
    """A venv holding only the editable install's own files, as each backend writes them."""
    layout, files = {**BACKENDS, **REMAPPED}[backend]
    environment = tmp_path / 'environment'
    venv.EnvBuilder().create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = Path(subprocess.check_output([str(python), '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'test_tools.pth').write_text(str(TEST_SITE) + '\n')
    written = files(root)
    if 'editables' in ''.join(written.values()):
        (site / 'editables').mkdir()
        (site / 'editables/__init__.py').write_text('')
        (site / 'editables/redirector.py').write_text(REDIRECTOR)
    for name, text in written.items():
        (site / name).write_text(text)
    info = site / 'wo_sample-1.0.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: wo-sample\nVersion: 1.0\n')
    (info / 'RECORD').write_text(''.join(f'{name},,\n' for name in written) + 'wo_sample-1.0.dist-info/METADATA,,\n')
    write_code(root, layout, 'base')
    (root / 'pyproject.toml').write_text('[project]\nname = "wo-sample"\nversion = "1.0"\n')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    return str(python), layout


def source_text(layout):
    namespace = 'wo_ns.plugins' if layout == 'src' else 'tools.plugins'
    return (f'from wo_sample import value\nfrom {namespace} import hook\n\n\n'
            'def test_value():\n    assert value() == "base"\n    assert hook.VALUE == "base"\n')


@pytest.mark.parametrize('backend', sorted(BACKENDS))
def test_comparison_copy_imports_its_own_code_through_each_editable_install(local_repo, tmp_path, backend):
    root = local_repo
    python, layout = install(root, tmp_path, backend)
    base, command = setup_task(root, python, source_text(layout))
    # The work tree now differs from base: any import from the work tree fails the test.
    write_code(root, layout, 'work')
    commit_all(root)
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert code == 0, (code, output)
    assert '1 passed' in output
    # The staged/new-code copy too, with the work tree's own code changed after it.
    git(root, 'checkout', '-q', base, '--', '.')
    code, output = gate.run_snapshot_test(root, tmp_path / 'snapshot', command, None, None)
    assert code == 0, (code, output)


@pytest.mark.parametrize('backend', ['hatchling', 'setuptools-finder', 'pdm-backend'])
def test_done_hook_and_pull_request_check_with_a_standard_editable_install(local_repo, tmp_path, backend):
    root = local_repo
    python, layout = install(root, tmp_path, backend)
    base, command = setup_task(root, python, '')
    package = folders(layout)[0]
    (root / package / '__init__.py').write_text(
        'def value():\n    return "base"\n\n\ndef shout(text):\n    return text.upper()\n')
    (root / 'tests/test_value.py').write_text('from wo_sample import shout\n\n\n'
                                             'def test_shout():\n    assert shout("a") == "A"\n')
    commit_all(root)
    # The test helper's PYTHONPATH (Workorder's repository) would add its own tests/
    # and docs/ to these namespaces; import Workorder through a folder of its own.
    path = tmp_path / 'workorder-path'
    path.mkdir()
    (path / 'workorder').symlink_to(PACKAGE / 'workorder', target_is_directory=True)

    def cli(root, *args):
        return subprocess.run([PYTHON, '-m', 'workorder', *args], cwd=root, capture_output=True,
                              encoding='utf-8', env=dict(os.environ, PYTHONPATH=str(path), GIT_TERMINAL_PROMPT='0'))
    done = cli(root, 'done', '--task', 'TASK-1')
    assert done.returncode == 0, done.stdout + done.stderr
    assert 'TASK-1 DONE:' in done.stdout
    assert 'outside the comparison copy' not in done.stdout + done.stderr
    git(root, 'add', '-A')
    hook = cli(root, 'check', '--staged')
    assert hook.returncode == 0, hook.stdout + hook.stderr
    commit_all(root)
    pull = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task')
    assert pull.returncode == 0, pull.stdout + pull.stderr
    assert 'outside the comparison copy' not in pull.stdout + pull.stderr


def test_copy_imports_leave_installed_packages_in_site_packages(local_repo, tmp_path):
    root = local_repo
    python, layout = install(root, tmp_path, 'hatchling')
    base, command = setup_task(root, python,
        source_text(layout) + '\n\ndef test_installed():\n    import os, pytest\n'
        f'    assert os.path.realpath(pytest.__file__).startswith({os.path.realpath(TEST_SITE)!r})\n')
    write_code(root, layout, 'work')
    commit_all(root)
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert code == 0, (code, output)
    assert '2 passed' in output


@pytest.mark.parametrize('flag', ['-I', '-E'])
def test_ignored_environment_keeps_the_refusal_with_advice_that_works(local_repo, tmp_path, flag):
    # -I/-E skip PYTHONPATH, so Workorder cannot move the install; pytest's pythonpath
    # puts the copy first but a namespace folder still spans the work tree.
    root = local_repo
    python, layout = install(root, tmp_path, 'hatchling')
    (root / 'pyproject.toml').write_text('[project]\nname = "wo-sample"\nversion = "1.0"\n'
                                         '[tool.pytest.ini_options]\npythonpath = ["."]\n')
    base, command = setup_task(root, python, source_text(layout))
    write_code(root, layout, 'work')
    commit_all(root)
    isolated = [command[0], flag, *command[1:]]
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, isolated, None, None)
    assert isinstance(code, ImportFailure), (code, output)
    # tests/ and tools/ are both namespace folders the install exposes.
    assert any(f'the tests import {name} from {root / name},' in code for name in ('tests', 'tools')), code
    assert 'drop -I/-E from the test command or install the project non-editable' in code
    assert 'pythonpath' not in code
    # The advice works: the same command without the flag compares on the copy.
    code, output = gate.run_ref_test(root, tmp_path / 'copy2', base, command, None, None)
    assert code == 0, (code, output)


def test_work_tree_folder_missing_from_the_copy_is_refused_with_advice(local_repo, tmp_path):
    # setuptools' strict mode links sources into an ignored build/ folder the copy lacks.
    root = local_repo
    python, layout = install(root, tmp_path, 'setuptools-compat')
    tree = root / 'build/__editable__.wo_sample-1.0'
    pth = next(Path(p) for p in Path(python).parent.parent.rglob('__editable__.wo_sample-1.0.pth'))
    pth.write_text(f'{tree}\n')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\nbuild/\n')
    base, command = setup_task(root, python, source_text(layout))
    (tree / 'tools/plugins').mkdir(parents=True)
    (tree / 'tools/plugins/hook.py').write_text('VALUE = "work"\n')
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert isinstance(code, ImportFailure), (code, output)
    assert f'the tests import tools from {tree / "tools"}' in code
    assert 'a work-tree folder the comparison copy does not have (Git ignores it)' in code
    assert 'install the project non-editable, or editable from its tracked sources' in code


@pytest.mark.parametrize('backend', sorted(REMAPPED))
def test_a_package_dir_remap_still_imports_the_work_tree_and_is_refused(local_repo, tmp_path, backend):
    root = local_repo
    python, layout = install(root, tmp_path, backend)
    base, command = setup_task(root, python, source_text(layout))
    write_code(root, layout, 'work')
    commit_all(root)
    code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
    assert isinstance(code, ImportFailure), (code, output)
    assert f'the tests import wo_sample from {root / "lib/wo_sample"}' in code
    assert "install the project non-editable or run the tests so they import this checkout's code" in code


FINDER_STATE = '''import os, sys


def inside_copy(path):
    return os.path.realpath(path).startswith(os.path.realpath(os.getcwd()) + os.sep)


def test_finders_serve_the_copy():
    finder = sys.modules.get('__editable___wo_sample_1_0_finder')
    redirector = sys.modules.get('editables.redirector')
    assert finder or redirector
    if finder:
        assert inside_copy(finder.MAPPING['wo_sample']), finder.MAPPING
        assert all(inside_copy(path) for paths in finder.NAMESPACES.values() for path in paths), finder.NAMESPACES
    if redirector:
        assert inside_copy(redirector.RedirectingFinder._redirections['wo_sample'])
'''


@pytest.mark.parametrize('backend', ['setuptools-finder-plain', 'hatchling-exact', 'pdm-backend-editables'])
def test_the_installed_finders_map_the_copy(local_repo, tmp_path, backend):
    # The copy's root on PYTHONPATH already wins over a top-level MAPPING entry; the
    # finders themselves must still serve the copy (submodule lookups, reloads, tools).
    if backend == 'setuptools-finder-plain':
        BACKENDS[backend] = ('flat', lambda root: finder_files({'wo_sample': str(root / 'wo_sample')}, {}))
    try:
        root = local_repo
        python, layout = install(root, tmp_path, backend)
        base, command = setup_task(root, python, FINDER_STATE)
        code, output = gate.run_ref_test(root, tmp_path / 'copy', base, command, None, None)
        assert code == 0, (code, output)
    finally:
        BACKENDS.pop('setuptools-finder-plain', None)
