"""Keep Python source and import evidence local to a test checkout."""
import ast
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shlex
import shutil
import subprocess


class ImportFailure(str):
    """A comparison has no usable import evidence, regardless of its test exit."""


def environment(root, env):
    roots = [str(root / 'src')] if (root / 'src').is_dir() else []
    roots.append(str(root))
    if env.get('PYTHONPATH'):
        roots.append(env['PYTHONPATH'])
    return dict(env, PYTHONPATH=os.pathsep.join(roots))


def packages(root):
    """Find package roots in repository source, including flat namespaces.

    Git excludes ignored dependencies and virtual environments. A namespace
    need not have an __init__.py, but must contain Python source in this tree.
    """
    from . import store as s
    listing = s._git_run(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                         required=False, isolated=True)
    paths = listing.stdout.split('\0') if not listing.returncode else []
    return package_names(relative for relative in paths if (root / relative).is_file())


def package_names(paths):
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
        if name.isidentifier() and (src or len(parts) > 1):
            found.add(name)
    return sorted(found)


def interpreter(command, root, env):
    first = command[0]
    name = Path(first).name.lower()
    if re.fullmatch(r'(?:python|pypy)(?:\d+(?:\.\d+)*)?(?:\.exe)?', name):
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


PROBE = r'''
import ast, csv, importlib.metadata, importlib.util, json, pathlib, site, sys, sysconfig
root = pathlib.Path(sys.argv[1]).resolve()
names = set(json.loads(sys.argv[2]))
for distribution in importlib.metadata.distributions():
    direct = distribution.read_text('direct_url.json')
    if not direct or json.loads(direct).get('dir_info', {}).get('editable') is not True:
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
allowed = set(site.getsitepackages())
allowed.update(path for path in sys.path if pathlib.Path(path).name in ('site-packages', 'dist-packages'))
allowed.update(sysconfig.get_path(key) for key in ('purelib', 'platlib', 'stdlib', 'platstdlib'))
if site.ENABLE_USER_SITE:
    allowed.add(site.getusersitepackages())
allowed = [str(pathlib.Path(path).resolve()) for path in allowed if path]
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
print('WORKORDER_IMPORTS=' + json.dumps({'locations': locations, 'allowed': allowed}))
'''


def test_imports(root, command):
    """Read top-level imports in test material without executing that material."""
    from . import store as s
    from .gate import looks_like_test
    listing = s._git_run(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                         required=False, isolated=True)
    paths = {root / path for path in listing.stdout.split('\0')
             if path.endswith('.py') and looks_like_test(path)}
    for word in command[1:]:
        path = root / word.split('::', 1)[0]
        if path.suffix == '.py':
            paths.add(path)
    names = set()
    sources = []
    if '-c' in command:
        position = command.index('-c') + 1
        if position < len(command):
            sources.append(command[position])
    for path in paths:
        try:
            sources.append(path.read_bytes())
        except OSError:
            continue
    for source_text in sources:
        try:
            tree = ast.parse(source_text)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names.add(node.module.split('.')[0])
    return names


def check(root, source, command, env, timeout=None):
    names = sorted(set(packages(root)) | set(packages(source)) | test_imports(root, command))
    python = interpreter(command, root, env)
    if python is None:
        return ImportFailure('could not verify the Python interpreter used by the test command') if names else None
    try:
        run = subprocess.run([*python, '-c', PROBE, str(root), json.dumps(names)], cwd=root, env=env,
                             capture_output=True, text=True, errors='replace', timeout=10)
        line = next(line for line in run.stdout.splitlines() if line.startswith('WORKORDER_IMPORTS='))
        report = json.loads(line.split('=', 1)[1])
        if run.returncode:
            raise ValueError(run.stderr.strip())
        allowed = [root.resolve(), *map(Path, report['allowed'])]
        for name, paths in report['locations'].items():
            for path in paths:
                if not any(Path(path).resolve().is_relative_to(parent) for parent in allowed):
                    return ImportFailure(f'the tests import {name} from {path}, outside the comparison copy; '
                                         'add pythonpath = ["src"] under [tool.pytest.ini_options] '
                                         'or install the package non-editable')
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError) as error:
        return ImportFailure(f'could not verify project imports: {error or "the import probe failed"}')
    return None


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
