"""Keep Python source and import evidence local to a test checkout."""
import ast
import configparser
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shlex
import shutil
import subprocess
import tempfile
import time


class ImportFailure(str):
    """A comparison has no usable import evidence, regardless of its test exit."""


def environment(root, env):
    roots = [str(root / 'src')] if (root / 'src').is_dir() else []
    roots.append(str(root))
    if env.get('PYTHONPATH'):
        roots.append(env['PYTHONPATH'])
    return dict(env, PYTHONPATH=os.pathsep.join(roots))


def packages(root, *, modules=False):
    """Find package roots in repository source, including flat namespaces.

    Git excludes ignored dependencies and virtual environments. A namespace
    need not have an __init__.py, but must contain Python source in this tree.
    """
    from . import store as s
    listing = s._git_run(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                         required=False, isolated=True)
    paths = listing.stdout.split('\0') if not listing.returncode else []
    return package_names((relative for relative in paths if (root / relative).is_file()), modules=modules)


def package_names(paths, *, modules=False):
    """Apply the same package inventory to a checkout or revision path list."""
    from .gate import looks_like_test
    found = set()
    for relative in paths:
        path = Path(relative)
        if path.suffix != '.py' or looks_like_test(relative):
            continue
        parts = path.parts
        src = parts[0] == 'src'
        if src:
            parts = parts[1:]
        elif parts[0] in {'docs', 'doc', 'examples', 'example', 'scripts', 'tools', 'build', 'dist'}:
            continue
        if not parts:
            continue
        name = Path(parts[0]).stem if len(parts) == 1 else parts[0]
        if name.isidentifier() and (modules or src or len(parts) > 1):
            found.add(name)
    return sorted(found)


def project_name(root):
    """Read the distribution name without requiring tomllib on Python 3.10."""
    try:
        text = (root / 'pyproject.toml').read_text(encoding='utf-8')
        try:
            import tomllib
        except ImportError:
            name = _project_name_310(text)
        else:
            project = tomllib.loads(text).get('project', {})
            name = project.get('name', '') if isinstance(project, dict) else ''
        if isinstance(name, str) and name:
            return name
    except (OSError, ValueError):
        pass  # Unreadable pytest configuration must not stop other test tools.
    try:
        config = configparser.ConfigParser(interpolation=None)
        config.read(root / 'setup.cfg', encoding='utf-8')
        return config.get('metadata', 'name', fallback='').strip()
    except (OSError, ValueError, configparser.Error):
        return ''


def _project_name_310(text):
    # Read just [project].name on 3.10, including TOML's literal/basic and
    # multiline strings. Validate the decoded distribution name, not its spelling.
    section, lines = False, []
    for line in text.splitlines():
        if re.match(r'^\s*\[', line):
            section = bool(re.fullmatch(r'''\s*\[\s*(?:project|"project"|'project')\s*\]\s*(?:#.*)?''', line))
        elif section:
            lines.append(line)
    match = re.search(r'''(?ms)^\s*(?:name|"name"|'name')\s*=\s*("{3}|'{3}|"|')(.*?)\1\s*(?:#[^\n]*)?$''', '\n'.join(lines))
    if not match:
        return ''
    quote, value = match.groups()
    if len(quote) == 3:
        value = value.removeprefix('\n')
    if quote.startswith('"'):
        value = re.sub(r'\\\n\s*', '', value)
        try:
            value = ast.literal_eval(quote + value + quote)
        except (ValueError, SyntaxError):
            return ''
    return value if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value) else ''


_PYTHON_COMMAND = re.compile(r'(?:python|pypy)(?:\d+(?:\.\d+)*)?(?:\.exe)?')


def interpreter(command, root, env):
    first = command[0]
    name = Path(first).name.lower()
    if _PYTHON_COMMAND.fullmatch(name):
        flags = []
        words = iter(command[1:])
        for word in words:
            if word in {'-m', '-c', '-', '--'} or not word.startswith('-'):
                break
            flags.append(word)
            if word in {'-W', '-X', '--check-hash-based-pycs'}:
                value = next(words, None)
                if value is None:
                    return None
                flags.append(value)
        return [first, *flags]
    # Console pytest uses its shebang interpreter, which may be a different venv.
    executable = (root / first) if '/' in first or '\\' in first else shutil.which(first, path=env.get('PATH'))
    if executable:
        try:
            content = Path(executable).read_bytes()
            windows_launcher = content.startswith(b'MZ')
            if windows_launcher:
                # distlib appends the interpreter shebang immediately before
                # its zip payload in pip-generated Windows console launchers.
                match = re.search(rb'#!([^\r\n]+)\r?\nPK\x03\x04', content)
                line = '#!' + match[1].decode('utf-8') if match else ''
            else:
                line = content.split(b'\n', 1)[0].decode('utf-8')
            if line.startswith('#!'):
                words = shlex.split(line[2:], posix=not windows_launcher)
                words = [word.strip('"') for word in words]
                path_type = PureWindowsPath if windows_launcher else Path
                if words and re.search(r'(?:python|pypy)', path_type(words[0]).name):
                    return words
                if len(words) >= 2 and path_type(words[0]).name == 'env' and re.search(r'(?:python|pypy)', words[1]):
                    return words[1:]
        except (OSError, UnicodeError, ValueError):
            pass
    return None


def _clear_checkout_bytecode(path, source, owner=None):
    """Remove project caches, retaining only ignored, untracked installed libraries."""
    if path.is_symlink():
        path.unlink()
        return
    if not path.exists():
        return
    if owner is None:
        shutil.rmtree(path)
        return
    if (source.name == 'site-packages'
            and any((parent / 'pyvenv.cfg').is_file() for parent in list(source.parents)[1:3])):
        from . import store as s
        relative = source.relative_to(owner).as_posix()
        ignored = s._git_run(owner, 'check-ignore', '-q', '--', relative, required=False, isolated=True)
        tracked = s._git_run(owner, 'ls-files', '-z', '--', relative, required=False, isolated=True)
        if ignored.returncode == 0 and tracked.returncode == 0 and not tracked.stdout:
            return
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            _clear_checkout_bytecode(child, source / child.name, owner)
        else:
            child.unlink()
    try:
        path.rmdir()
    except OSError as error:
        if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
            raise


@contextmanager
def _bytecode_directory(root, owner):
    """Lease a reusable cache without serializing parallel test processes."""
    from . import store as s
    handle = None
    try:
        result = s._git_run(owner, 'rev-parse', '--git-path', 'workorder-bytecode',
                            required=False, isolated=True)
        if result.returncode:
            raise OSError('no repository cache')
        parent = (owner / result.stdout.strip()).resolve()
        index = 0
        while True:
            slot = parent / str(index)
            slot.mkdir(parents=True, exist_ok=True)
            handle = (slot / 'lock').open('a+b')
            try:
                # OS locks are released even if Workorder is killed. Separate
                # slots also isolate overlapping runs of the same checkout.
                if os.name == 'nt':
                    import msvcrt
                    if not handle.tell():
                        handle.write(b'0')
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                handle.close()
                handle = None
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                index += 1
        cache = slot / 'pycache'
        cache.mkdir(exist_ok=True)
        # CPython strips a drive letter and leading separators, but retains
        # UNC server/share components. Cover a symlink spelling and real cwd.
        checkouts = {}
        for source in (root.absolute(), root.resolve()):
            name = source.as_posix()
            if len(name) > 1 and name[1] == ':' and name[0] != '/':
                name = name[2:]
            checkouts[cache.joinpath(*name.lstrip('/').split('/'))] = source
        # Temporary checkout paths must disappear entirely. Only the real
        # repository may retain its ignored virtualenv's installed libraries.
        keep = root == owner
        for path, source in checkouts.items():
            _clear_checkout_bytecode(path, source, source if keep else None)
    except OSError:
        if handle is not None:
            handle.close()
        # Missing/unwritable Git metadata must not prevent current-source tests.
        with tempfile.TemporaryDirectory(prefix='workorder-bytecode-', ignore_cleanup_errors=True) as cache:
            yield cache
        return
    try:
        yield str(cache)
    finally:
        try:
            for path, source in checkouts.items():
                try:
                    _clear_checkout_bytecode(path, source, source if keep else None)
                except OSError:
                    pass  # The next lease must clear successfully or use a fresh cache.
                # Remove empty parents of temporary comparison checkouts too.
                for directory in path.parents:
                    if directory == cache:
                        break
                    try:
                        directory.rmdir()
                    except OSError:
                        break
        finally:
            handle.close()


@contextmanager
def bytecode_cache(command, root, env, *, cache_root=None):
    """Refresh checkout bytecode, retaining cached dependencies between runs."""
    with _bytecode_directory(root, cache_root if cache_root is not None else root) as cache:
        env = dict(env, PYTHONPYCACHEPREFIX=cache)
        words = list(command)
        python = (interpreter(words, root, env)
                  if _PYTHON_COMMAND.fullmatch(Path(words[0]).name.lower()) else None)
        if python:
            executable = root / python[0]
            virtualenv = executable.parent.parent
            if virtualenv.is_symlink() and (virtualenv / 'pyvenv.cfg').is_file():
                # Snapshots link ignored virtualenvs. Use the real environment's
                # directory so its dependencies keep stable cache paths. Do not
                # redirect partially copied environments containing tracked code,
                # or resolve Python's own symlink to the base interpreter.
                python[0] = str(virtualenv.resolve() / executable.parent.name / executable.name)
            # Replace both spellings of a declared prefix: repeated -X options
            # need not use the last value. Leave script/module arguments alone.
            flags = []
            options = iter(python[1:])
            for word in options:
                if word in {'-W', '-X', '--check-hash-based-pycs'}:
                    value = next(options)
                    if word != '-X' or value.partition('=')[0] != 'pycache_prefix':
                        flags.extend([word, value])
                elif not (word.startswith('-X') and word[2:].partition('=')[0] == 'pycache_prefix'):
                    flags.append(word)
            words = [python[0], *flags, '-X', 'pycache_prefix=' + cache, *words[len(python):]]
        yield words, env


def pytest_arguments(command):
    """Recognize direct pytest invocations without mistaking Python -c text for a runner."""
    if Path(command[0]).name.lower() in {'pytest', 'py.test', 'pytest.exe', 'py.test.exe'}:
        return list(command[1:])
    for index, word in enumerate(command[1:], 1):
        if word == '-m':
            return list(command[index + 2:]) if command[index + 1:index + 2] == ['pytest'] else None
        if word == '-c':
            from .pytest_selection import ISOLATED_RUNNER
            return list(command[index + 2:]) if command[index + 1:index + 2] == [ISOLATED_RUNNER] else None
    return None


_IMPORT_REPORT = r'''
import ast, csv, importlib.metadata, importlib.util, json, pathlib, re, sys

def import_locations(names, projects):
    names = set(names)
    normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
    projects = {normalize(name) for name in projects if name}
    for distribution in importlib.metadata.distributions():
        # Other distributions are dependencies, wherever and however installed.
        # Matching by name also covers a second clone using the first one's install.
        if normalize(distribution.metadata.get('Name', '')) not in projects:
            continue
        names.update((distribution.read_text('top_level.txt') or '').split())
        # Some Python versions filter missing files from Distribution.files. An
        # editable RECORD may still name sources provided by its finder or .pth.
        entries = [row[0] for row in csv.reader((distribution.read_text('RECORD') or '').splitlines()) if row]
        for entry in entries:
            path = pathlib.PurePosixPath(str(entry))
            if path.suffix == '.pth':
                pth = pathlib.Path(distribution.locate_file(entry))
                for line in pth.read_text(encoding='utf-8').splitlines():
                    if not line.strip() or line.startswith(('#', 'import ' , 'import\t')):
                        continue
                    source = pth.parent / line.strip()
                    if source.is_dir():
                        for child in source.iterdir():
                            if child.suffix == '.py' or child.is_dir() and any(child.rglob('*.py')):
                                if child.stem.isidentifier():
                                    names.add(child.stem)
            if path.suffix not in ('.py', '.so', '.pyd') or not path.parts:
                continue
            name = path.parts[0].split('.')[0]
            if name.isidentifier() and not name.startswith('__editable__'):
                names.add(name)
            if path.name.startswith('__editable__') and path.suffix == '.py':
                # Read the installed finder without importing additional modules or
                # evaluating its mapping expressions. Setuptools emits literal maps.
                tree = ast.parse(pathlib.Path(distribution.locate_file(entry)).read_text(encoding='utf-8'))
                for node in tree.body:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
                    if any(isinstance(target, ast.Name) and target.id in ('MAPPING', 'NAMESPACES') for target in targets):
                        mapping = ast.literal_eval(node.value)
                        names.update(name.split('.')[0] for name in mapping)
    locations = {}
    for name in sorted(names):
        if not name.isidentifier():
            continue
        spec = importlib.util.find_spec(name)
        if spec is not None:
            paths = list(spec.submodule_search_locations or [])
            if spec.origin and spec.origin not in ('built-in', 'frozen'):
                paths.append(spec.origin)
            locations[name] = paths
    return locations
'''


PROBE_TIMEOUT = 10
# The integrated guard also waits for interpreter startup, pytest/plugins and
# fresh project bytecode. The runner still enforces any earlier test deadline.
GUARD_STARTUP_TIMEOUT = 60


PROBE = _IMPORT_REPORT + r'''
names = json.loads(sys.argv[1])
projects = json.loads(sys.argv[2])
pytest_args = json.loads(sys.argv[3])
pytest_available = pytest_args is not None and importlib.util.find_spec('pytest') is not None
# Emit this before loading pytest plugins; flush so even direct plugin writes
# cannot precede the interpreter's own availability result.
print('WORKORDER_PYTEST_AVAILABLE=' + json.dumps(pytest_available), flush=True)
if pytest_available:
    import pytest
    from _pytest.config import get_config
    # Let the test interpreter's pytest select and parse its own config. Its
    # pythonpath entries are relative to that file and prepend sys.path, even
    # with -I/-E. Stop before conftest loading and final option validation:
    # repository conftests may themselves register command-line options.
    class PathsReady(Exception):
        pass
    class ProbeConfiguration:
        @pytest.hookimpl(tryfirst=True)
        def pytest_load_initial_conftests(self, early_config):
            # Pytest 8 applies these in another initial-conftest hook; pytest 9
            # applies them before this hook. Apply the parsed setting ourselves
            # so the probe does not depend on hook order or pytest version.
            for path in reversed(early_config.getini('pythonpath')):
                sys.path.insert(0, str(path))
            raise PathsReady
    config = get_config(pytest_args)
    config.pluginmanager.register(ProbeConfiguration())
    if json.loads(sys.argv[4]):
        spec = importlib.util.spec_from_file_location('__workorder_test_selection',
            pathlib.Path.cwd() / '__workorder_test_selection.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        config.pluginmanager.register(plugin)
    try:
        config.parse(pytest_args)
    except PathsReady:
        pass
print('WORKORDER_IMPORTS=' + json.dumps(import_locations(names, projects)))
'''


def import_advice(name, root, source):
    for relative in ('src', '.'):
        for checkout in (root, source):
            base = checkout / relative
            package = base / name
            if (base / (name + '.py')).is_file() or package.is_dir() and any(package.rglob('*.py')):
                return (f'add pythonpath = ["{relative}"] under [tool.pytest.ini_options] '
                        'or install the project non-editable')
    return "install the project non-editable or run the tests so they import this checkout's code"


def _import_failure(report, root, source):
    for name, paths in report.items():
        for path in paths:
            if not Path(path).resolve().is_relative_to(root.resolve()):
                return ImportFailure(f'the tests import {name} from {path}, outside the comparison copy; '
                                     + import_advice(name, root, source))
    return None


def integrated_pytest(command, root, env):
    """Only pytest invocations whose interpreter and entry point are understood."""
    python = interpreter(command, root, env)
    if python is None:
        return False
    if Path(command[0]).name.lower() in {'pytest', 'py.test', 'pytest.exe', 'py.test.exe'}:
        return True
    if _PYTHON_COMMAND.fullmatch(Path(command[0]).name.lower()):
        from .pytest_selection import ISOLATED_RUNNER
        entry = list(command[len(python):len(python) + 2])
        return entry in (['-m', 'pytest'], ['-c', ISOLATED_RUNNER])
    return False


_GUARD = r'''
import os, pytest

@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config):
    previous = sys.path[:]
    try:
        # Pytest 8 applies pythonpath in another early hook; pytest 9 already
        # applied it. Restore our temporary additions before pytest continues.
        for path in reversed(early_config.getini('pythonpath')):
            sys.path.insert(0, str(path))
        locations = import_locations(_DATA['names'], _DATA['projects'])
        root = pathlib.Path(_DATA['root']).resolve()
        valid = all(pathlib.Path(path).resolve().is_relative_to(root)
                    for paths in locations.values() for path in paths)
        report = {'ok': valid, 'imports': locations}
    except Exception as error:
        report = {'ok': False, 'error': str(error) or type(error).__name__}
    finally:
        sys.path[:] = previous
    destination = pathlib.Path(_DATA['report'])
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(report), encoding='utf-8')
    os.replace(temporary, destination)
    if not report['ok']:
        raise SystemExit(4)
'''


_GUARDED_RUNNER = r'''
import importlib.util, json, os, pathlib, runpy, sys
data = json.loads(sys.argv[1])
arguments = sys.argv[2:]
spec = importlib.util.spec_from_file_location(data['module'], data['plugin'])
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
from _pytest import config as pytest_config
original = pytest_config.get_config
def get_config(*args, **kwargs):
    config = original(*args, **kwargs)
    config.pluginmanager.register(plugin, data['module'])
    return config
pytest_config.get_config = get_config
if data['kind'] == 'selection':
    sys.argv = ['-c', *arguments]
    exec(data['selection'], {'__name__': '__main__'})
elif data['kind'] == 'console':
    if not getattr(sys.flags, 'safe_path', sys.flags.isolated):
        sys.path[0] = data['path0']
    sys.argv = [data['console'], *arguments]
    runpy.run_path(data['console'], run_name='__main__')
else:
    if not getattr(sys.flags, 'safe_path', sys.flags.isolated):
        sys.path[0] = os.getcwd()
    sys.argv = ['pytest', *arguments]
    runpy.run_module('pytest', run_name='__main__', alter_sys=True)
'''


class _ImportGuard:
    def __init__(self, command, root, source, env, directory):
        from .pytest_selection import ISOLATED_RUNNER
        self.root, self.source = root, source
        self.directory = directory
        self.report = directory / 'report.json'
        self.pycache_prefix = None
        self.timeout = GUARD_STARTUP_TIMEOUT
        module = '__workorder_import_guard_' + os.urandom(8).hex()
        plugin = directory / (module + '.py')
        data = dict(names=sorted(set(packages(root, modules=True)) | set(packages(source, modules=True))),
                    projects=sorted({project_name(root), project_name(source)} - {''}),
                    root=str(root), report=str(self.report))
        plugin.write_text(_IMPORT_REPORT + '\n_DATA = ' + repr(data) + '\n' + _GUARD, encoding='utf-8')
        python = interpreter(command, root, env)
        flags = iter(python[1:])
        ignore_environment = False
        for flag in flags:
            if flag in {'-W', '-X', '--check-hash-based-pycs'}:
                next(flags, None)
            elif flag.startswith('-') and not flag.startswith('--') and set(flag[1:]) & {'I', 'E'}:
                ignore_environment = True
        self.command, self.env = list(command), dict(env)
        if ignore_environment or ISOLATED_RUNNER in command:
            kind = ('selection' if ISOLATED_RUNNER in command else
                    'module' if _PYTHON_COMMAND.fullmatch(Path(command[0]).name.lower()) else 'console')
            runner = dict(module=module, plugin=str(plugin), kind=kind, selection=ISOLATED_RUNNER)
            if kind == 'console':
                runner['console'] = (command[0] if '/' in command[0] or '\\' in command[0]
                                     else shutil.which(command[0], path=env.get('PATH')))
                runner['path0'] = str((root / runner['console']).resolve().parent)
            bootstrap = ''
            if kind != 'selection':
                first = runner['path0'] if kind == 'console' else str(root.resolve())
                # Restore script/-m startup before even importing the guard's
                # standard-library dependencies; -E keeps that path, -I/-P do not.
                bootstrap = ('import sys\nif not getattr(sys.flags, "safe_path", sys.flags.isolated):\n'
                             f'    sys.path[0] = {first!r}\n')
            self.command = [*python, '-c', bootstrap + _GUARDED_RUNNER, json.dumps(runner),
                            *pytest_arguments(command)]
        else:
            # Keep the real console/module entry point and all its arguments.
            # A trailing private search path cannot shadow checkout/dependencies.
            self.env['PYTHONPATH'] = os.pathsep.join(filter(None, (env.get('PYTHONPATH'), str(directory))))
            end = self.command.index('--') if '--' in self.command else len(self.command)
            self.command[end:end] = ['-p', module]

    def status(self):
        self.report_locked = False
        try:
            value = json.loads(self.report.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {'error': 'invalid import guard report'}
        except PermissionError:
            # Windows can deny a read while the child publishes the report.
            # Keep waiting within the startup deadline; finish still refuses
            # a report that remains inaccessible when the child exits.
            self.report_locked = True
            return None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {'error': 'unreadable import guard report'}

    def finish(self, run):
        if run.returncode == 124:
            return run
        report = self.status()
        # The child can exit before the parent first reads its published report.
        # Allow a brief sharing violation here too, without accepting missing,
        # persistently unreadable or invalid evidence, or changing the test exit.
        deadline = time.monotonic() + 0.25
        while report is None and self.report_locked and time.monotonic() < deadline:
            time.sleep(0.01)
            report = self.status()
        if (report is None and run.returncode and
                re.search(r"No module named ['\"]?_?pytest['\"]?(?:\r?\n|$)", run.stderr or '')):
            # The original interpreter may lack pytest entirely. Preserve its
            # exit only after a separate probe verifies that fact and project
            # imports, outside this guard's temporary environment and lease.
            run.import_guard_missing_pytest = run.returncode
        try:
            failure = (_import_failure(report['imports'], self.root, self.source)
                       if report and 'imports' in report else
                       ImportFailure('could not verify project imports: '
                                     + (report.get('error', 'invalid import guard report') if report
                                        else 'the import guard did not complete')))
        except (OSError, ValueError, TypeError, AttributeError):
            failure = ImportFailure('could not verify project imports: invalid import guard report')
        if failure:
            run.returncode = failure
        elif not report.get('ok'):
            run.returncode = ImportFailure('could not verify project imports: the import guard refused the run')
        return run

    def close(self):
        if self.pycache_prefix:
            for source in (self.directory.absolute(), self.directory.resolve()):
                name = source.as_posix()
                if len(name) > 1 and name[1] == ':' and name[0] != '/':
                    name = name[2:]
                path = Path(self.pycache_prefix).joinpath(*name.lstrip('/').split('/'))
                try:
                    _clear_checkout_bytecode(path, source)
                except OSError:
                    pass


@contextmanager
def pytest_guard(command, root, source, env):
    if source is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix='workorder-imports-', ignore_cleanup_errors=True) as temporary:
        guard = _ImportGuard(command, root, source, env, Path(temporary))
        try:
            yield guard
        finally:
            guard.close()


def check(root, source, command, env, timeout=None, *, require_missing_pytest=False):
    python = interpreter(command, root, env)
    # Flat utility scripts alone must not turn a native test command into a
    # Python comparison. Identifiable Python commands also check flat modules.
    names = sorted(set(packages(root, modules=python is not None))
                   | set(packages(source, modules=python is not None)))
    if python is None:
        return ImportFailure('could not verify the Python interpreter used by the test command') if names else None
    try:
        from .pytest_selection import ISOLATED_RUNNER
        projects = sorted({project_name(root), project_name(source)} - {''})
        probe = [*python, '-c', PROBE, json.dumps(names), json.dumps(projects),
                 json.dumps(pytest_arguments(command)), json.dumps(ISOLATED_RUNNER in command)]
        from . import store as s
        with s.changing_repository(), bytecode_cache(probe, root, env, cache_root=source) as (words, probe_env):
            run = subprocess.run(words, cwd=root, env=probe_env,
                                 capture_output=True, text=True, errors='replace', timeout=PROBE_TIMEOUT)
        line = next(line for line in run.stdout.splitlines() if line.startswith('WORKORDER_IMPORTS='))
        report = json.loads(line.split('=', 1)[1])
        if run.returncode:
            raise ValueError(run.stderr.strip())
        if require_missing_pytest:
            available = [line for line in run.stdout.splitlines()
                         if line.startswith('WORKORDER_PYTEST_AVAILABLE=')]
            if (len(available) != 1
                    or json.loads(available[0].split('=', 1)[1]) is not False):
                return ImportFailure('could not verify project imports: the import guard did not complete')
        return _import_failure(report, root, source)
    except (OSError, ValueError, configparser.Error, StopIteration, subprocess.SubprocessError) as error:
        return ImportFailure(f'could not verify project imports: {error or "the import probe failed"}')
    return None


def missing_base_import(root, source, command):
    """Recognize a mandatory import of wholly new code in an old-code copy.

    Only explicit pytest files or Python scripts qualify. Optional imports, imports hidden behind
    executable setup, dynamic imports, and any imported project package still
    present in the copy retain the ordinary import probe.
    """
    from . import gate, store
    args = pytest_arguments(command)
    if args is None:
        python = interpreter(command, root, store.copy_environment())
        if python is None or len(command) <= len(python) or command[len(python)].startswith('-'):
            return None
        selections = [command[len(python)]]
    else:
        definition = {'test': store.join_command(['pytest', *args])}
        selections = gate.pytest_collection_paths(definition)
    if not selections:
        return None
    files = set()
    for selection in selections:
        path = root / selection
        if path.suffix != '.py' or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            return None
        files.add(path)
    current = set(packages(source, modules=True))
    existing = set(packages(root, modules=True))
    if current <= existing:
        return None
    imported, required, seen = set(), set(), set()
    pending = list(files)
    for file in files:
        for parent in (file.parent, *file.parent.parents):
            if not parent.is_relative_to(root):
                break
            conftest = parent / 'conftest.py'
            if conftest.is_file() and conftest.read_text(encoding='utf-8').strip():
                return None  # Fixture startup may provide an otherwise missing import.
    try:
        while pending:
            file = pending.pop()
            if file in seen:
                continue
            seen.add(file)
            tree = ast.parse(file.read_bytes())
            if file in files:
                # Every selected file must reach its missing project import
                # before other executable code: even importing a local helper
                # can populate sys.modules for this file or another test file.
                for node in tree.body:
                    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        continue
                    if isinstance(node, ast.ImportFrom) and node.module == '__future__' and not node.level:
                        continue
                    if not isinstance(node, (ast.Import, ast.ImportFrom)):
                        return None
                    names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or '']
                    first = {name.split('.')[0] for name in names if not getattr(node, 'level', 0)}
                    if not first or not first <= current:
                        return None
                    required.update(first)
                    break
                else:
                    return None
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id == '__import__'
                    or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'
                ):
                    return None
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or '']
                else:
                    continue
                for name in names:
                    top = name.split('.')[0]
                    imported.add(top)
                    # Inspect local helpers and package sources too, so they
                    # cannot hide an imported package that existed in base.
                    for folder in (file.parent, root, root / 'src', source, source / 'src'):
                        module = folder / top
                        if module.with_suffix('.py').is_file():
                            pending.append(module.with_suffix('.py'))
                        elif module.is_dir():
                            pending.extend(module.rglob('*.py'))
                        else:
                            continue
                        break
    except (OSError, ValueError, SyntaxError, UnicodeError):
        return None
    own = imported & current
    mandatory = required & own
    return sorted(mandatory)[0] if mandatory and not own & existing else None


def installed_src(root):
    """Use the invoking environment to notice an installed src package at init."""
    import importlib.util
    if not (root / 'src').is_dir():
        return []
    found = []
    for name in packages(root):
        try:
            if importlib.util.find_spec(name) is not None:
                found.append(name)
        except (ImportError, ValueError):
            pass
    return found
