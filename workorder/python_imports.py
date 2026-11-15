"""Keep Python source and import evidence local to a test checkout."""
import ast
import configparser
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
import ast, csv, importlib.metadata, importlib.util, json, pathlib, re, sys
names = set(json.loads(sys.argv[1]))
normalize = lambda name: re.sub(r'[-_.]+', '-', name).lower()
projects = {normalize(name) for name in json.loads(sys.argv[2]) if name}
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
print('WORKORDER_IMPORTS=' + json.dumps(locations))
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


def check(root, source, command, env, timeout=None):
    python = interpreter(command, root, env)
    # Flat utility scripts alone must not turn a native test command into a
    # Python comparison. Identifiable Python commands also check flat modules.
    names = sorted(set(packages(root, modules=python is not None))
                   | set(packages(source, modules=python is not None)))
    if python is None:
        return ImportFailure('could not verify the Python interpreter used by the test command') if names else None
    try:
        projects = sorted({project_name(root), project_name(source)} - {''})
        run = subprocess.run([*python, '-c', PROBE, json.dumps(names), json.dumps(projects)], cwd=root, env=env,
                             capture_output=True, text=True, errors='replace', timeout=10)
        line = next(line for line in run.stdout.splitlines() if line.startswith('WORKORDER_IMPORTS='))
        report = json.loads(line.split('=', 1)[1])
        if run.returncode:
            raise ValueError(run.stderr.strip())
        for name, paths in report.items():
            for path in paths:
                if not Path(path).resolve().is_relative_to(root.resolve()):
                    return ImportFailure(f'the tests import {name} from {path}, outside the comparison copy; '
                                         + import_advice(name, root, source))
    except (OSError, ValueError, configparser.Error, StopIteration, subprocess.SubprocessError) as error:
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
