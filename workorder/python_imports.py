"""Keep Python source and import evidence local to a test checkout."""
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
import importlib.util, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
locations = {}
for name in json.loads(sys.argv[2]):
    spec = importlib.util.find_spec(name)
    if spec is not None:
        paths = list(spec.submodule_search_locations or [])
        if spec.origin and spec.origin not in ('built-in', 'frozen'):
            paths.append(spec.origin)
        locations[name] = paths
print('WORKORDER_IMPORTS=' + json.dumps(locations))
'''


def check(root, source, command, env, timeout=None):
    names = sorted(set(packages(root)) | set(packages(source)))
    if not names:
        return None
    python = interpreter(command, root, env)
    if python is None:
        return ImportFailure('could not verify the Python interpreter used by the test command')
    try:
        run = subprocess.run([*python, '-c', PROBE, str(root), json.dumps(names)], cwd=root, env=env,
                             capture_output=True, text=True, errors='replace', timeout=timeout or 10)
        line = next(line for line in run.stdout.splitlines() if line.startswith('WORKORDER_IMPORTS='))
        locations = json.loads(line.split('=', 1)[1])
        if run.returncode:
            raise ValueError(run.stderr.strip())
        for name, paths in locations.items():
            for path in paths:
                if not Path(path).resolve().is_relative_to(root.resolve()):
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
