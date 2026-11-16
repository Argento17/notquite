"""Bounded, command-local read caching and a repository's cat-file connection."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import threading


class ReadCache(dict):
    def __init__(self):
        super().__init__()
        self.states = {}
        self.queries = {}
        self.batches = {}
        self.stack = []
        self.generation = 0
        self.disabled = set()
        self.configurations = {}
        self.contents = {}
        self.snapshots = {}

    def fork(self):
        """Copy observations before a worker starts; its first read revalidates them."""
        child = ReadCache()
        for name in ('states', 'queries', 'configurations', 'contents', 'snapshots'):
            setattr(child, name, deepcopy(getattr(self, name)))
        # Derived values can depend on the calling context; cat-file processes
        # and checking stacks belong to their original thread. Neither is shared.
        return child

    def clear(self, *, queries=True):
        super().clear()
        if queries:
            self.queries.clear()
        self.generation += 1

    @contextmanager
    def checking(self, root, *, isolated=False):
        identity = (root, isolated)
        reusable = self.stack[-1][1] if self.stack and self.stack[-1][0] == identity else True
        if (not self.stack or self.stack[-1][0] != identity
                or self.stack[-1][2] != self.generation):
            state = None if identity in self.disabled else repository_state(
                Path(root), isolated=isolated, configurations=self.configurations,
                contents=self.contents, snapshots=self.snapshots)
            reusable = state is not None
            if not reusable:
                self.disabled.add(identity)
            previous = self.states.get(identity)
            if not reusable or previous != state:
                self.clear(queries=False)
                # A comparison checkout has its own state; observing it must
                # not evict unchanged Git reads from the source repository.
                for key in list(self.queries):
                    if key[1][0] == root:
                        del self.queries[key]
                self.states[identity] = state
                # cat-file itself caches the index and replacement refs.
                if state is None or previous is not None and previous[1] != state[1]:
                    for key in list(self.batches):
                        if key[0] == root:
                            self.batches.pop(key).close()
        self.stack.append((identity, reusable, self.generation))
        try:
            yield reusable
        finally:
            self.stack.pop()

    def close(self):
        for batch in self.batches.values():
            batch.close()


def _configuration_path(value, root, env, *, origin=None):
    """Git decoded config quoting; only includes resolve beside their origin."""
    if value == '~' or value.startswith('~/'):
        value = str(Path(env.get('HOME') or Path.home()) / value[2:])
    elif value.startswith('~'):
        value = os.path.expanduser(value)
    if value.startswith('%(prefix)'):
        raise ValueError('runtime Git prefix is not observable')
    path = Path(value)
    return path if path.is_absolute() else (origin.parent if origin else root) / path


def _file_bytes(path, info, contents):
    key = os.fspath(path)
    stamp = (info.st_dev, info.st_ino, info.st_mode, info.st_size,
             info.st_mtime_ns, info.st_ctime_ns)
    saved = contents.get(key) if contents is not None and os.name != 'nt' else None
    if saved is not None and saved[0] == stamp:
        return saved[1]
    raw = Path(path).read_bytes()
    if contents is not None and os.name != 'nt':
        contents[key] = (stamp, raw)
    return raw


def _configuration_files(paths, contents=None, observe=None):
    """Follow config symlinks, including broken links that may acquire a target."""
    digest = hashlib.sha256()
    remaining = 32 * 1024 * 1024
    if len(paths) > 4096:
        raise ValueError('too many configuration dependencies')
    for path in sorted(paths):
        digest.update(os.fsencode(path))
        try:
            info = observe(path, True) if observe else path.stat()
        except FileNotFoundError:
            digest.update(b'missing')
            continue
        if not stat.S_ISREG(info.st_mode):
            if os.path.samefile(path, os.devnull):
                digest.update(b'empty')
                continue
            raise ValueError('non-file configuration dependency')
        remaining -= info.st_size
        if remaining < 0:
            raise ValueError('large configuration dependencies')
        digest.update(str(info.st_mode).encode())
        digest.update(_file_bytes(path, info, contents))
    return digest.digest()


def _git_flag(value):
    """Git's boolean flags also accept zero integers, including scaled/hex zero."""
    return bool(value) and value.lower() not in {'false', 'no', 'off'} and not re.fullmatch(
        r'\s*[+-]?(?:0x)?0+[kmg]?', value, flags=re.ASCII | re.IGNORECASE)


def _git_configuration(root, gitdir, common, env, environment, configurations, contents, observe):
    """Discover with Git's parser once, then observe inputs without a process."""
    key = ('configuration', root, gitdir, common, environment)
    previous = configurations.get(key)
    if previous is not None:
        signature = _configuration_files(previous['configuration_paths'], contents, observe)
        if signature == previous['signature']:
            return previous
    run = subprocess.run(['git', 'config', '--null', '--list', '--show-origin', '--includes'],
                         cwd=root, env=env, capture_output=True, timeout=30)
    if run.returncode:
        raise ValueError('Git configuration cannot be inspected')
    fields = run.stdout.decode('utf-8', 'surrogateescape').split('\0')
    if fields.pop() != '' or len(fields) % 2:
        raise ValueError('unexpected Git config framing')
    records = []
    for origin, entry in zip(fields[::2], fields[1::2]):
        name, separator, value = entry.partition('\n')
        records.append((origin, name, value if separator else None))
    home = Path(env.get('HOME') or Path.home())
    xdg = Path(env.get('XDG_CONFIG_HOME') or home / '.config')
    paths = {directory / name for directory in (gitdir, common)
             for name in ('config', 'config.worktree', 'HEAD')}
    if env.get('GIT_CONFIG_GLOBAL') is not None:
        paths.add(_configuration_path(env['GIT_CONFIG_GLOBAL'], root, env))
    else:
        paths.update((home / '.gitconfig', xdg / 'git/config'))
    if not _git_flag(env.get('GIT_CONFIG_NOSYSTEM')):
        paths.add(_configuration_path(env.get('GIT_CONFIG_SYSTEM', '/etc/gitconfig'), root, env))
    if env.get('GIT_CONFIG'):
        paths.add(_configuration_path(env['GIT_CONFIG'], root, env))
    # Git reports platform/build-specific defaults, including missing files.
    # Older versions omit these; standard defaults and actual origins remain.
    defaults_key = ('configuration-defaults', environment)
    defaults = configurations.get(defaults_key)
    if defaults is None:
        variables = subprocess.run(['git', 'var', '-l'], cwd=root, env=env,
                                   capture_output=True, timeout=30)
        if variables.returncode:
            raise ValueError('Git configuration paths cannot be inspected')
        defaults = {}
        selected = None
        for line in variables.stdout.decode('utf-8', 'surrogateescape').splitlines():
            name, separator, value = line.partition('=')
            if separator:
                selected = name if name in {'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
                                            'GIT_ATTR_SYSTEM'} else None
                if selected:
                    defaults[selected] = [value] if value else []
            elif selected:
                defaults[selected].append(line)
        configurations[defaults_key] = defaults
    for name in ('GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL'):
        paths.update(_configuration_path(value, root, env) for value in defaults.get(name, ()))
    attributes = {xdg / 'git/attributes'}
    if not _git_flag(env.get('GIT_ATTR_NOSYSTEM')):
        attributes.update(_configuration_path(value, root, env)
                          for value in defaults.get('GIT_ATTR_SYSTEM', ('/etc/gitattributes',)))
    inputs = {xdg / 'git/ignore'}
    filters = set()
    unsafe = bool(env.get('GIT_EXTERNAL_DIFF'))
    effective = {}
    for source, name, value in records:
        origin = None
        if source.startswith('file:'):
            origin = _configuration_path(source[5:], root, env)
            paths.add(origin)
        elif source not in {'command line:', 'standard input:'}:
            raise ValueError('unknown Git config origin')
        if (name == 'include.path' or name.startswith('includeif.') and name.endswith('.path')) and value:
            paths.add(_configuration_path(value, root, env, origin=origin))
        effective[name] = value
    for name, value in effective.items():
        if name == 'core.attributesfile':
            attributes.discard(xdg / 'git/attributes')
        if name in {'core.excludesfile', 'core.attributesfile'} and value:
            path = _configuration_path(value, root, env)
            (attributes if name == 'core.attributesfile' else inputs).add(path)
        if name.startswith('filter.') and name.rsplit('.', 1)[-1] in {'clean', 'smudge', 'process'} and value:
            filters.add(name[len('filter.'):].rsplit('.', 1)[0])
        if name == 'core.fsmonitor' and (value is None or value.lower() not in {'', 'false', 'no', 'off', '0'}):
            unsafe = True
        if value and (name == 'diff.external' or name.startswith('diff.') and name.endswith(('.textconv', '.command'))):
            unsafe = True
    result = {'configuration_paths': paths, 'signature': _configuration_files(paths, contents, observe),
              'inputs': inputs, 'attributes': attributes, 'filters': filters, 'unsafe': unsafe}
    configurations[key] = result
    return result


def _active_filter(raw, filters):
    # Git splits attribute lines and words using ASCII bytes. Unicode line or
    # space characters are legal inside a filter name, not separators.
    for line in raw.split(b'\n'):
        if not line.lstrip(b' \t\r\v\f').startswith(b'#'):
            for name in re.findall(rb'(?:^|\s)filter=([^\s]+)', line):
                if name.decode('utf-8', 'surrogateescape') in filters:
                    return True
    return False


def _indexed_filter(root, gitdir, common, env, environment, filters, configurations, metadata):
    """Inspect indexed attribute candidates and the paths to which they may apply."""
    # The observed metadata already includes index, split-index files, objects
    # and replacement refs, including an externally selected GIT_INDEX_FILE.
    signature = metadata
    key = ('indexed-filters', root, gitdir, common, environment, frozenset(filters))
    previous = configurations.get(key)
    if previous is not None and previous[0] == signature:
        return previous[1]
    listing = subprocess.run(['git', 'ls-files', '--stage', '-z'],
                             cwd=root, env=env, capture_output=True, timeout=30)
    if listing.returncode:
        raise ValueError('indexed attributes cannot be inspected')
    entries = listing.stdout.split(b'\0')
    if len(listing.stdout) > 32 * 1024 * 1024 or len(entries) > 4097:
        raise ValueError('large index inventory')
    oids, paths = set(), set()
    for entry in entries:
        if not entry:
            continue
        header, separator, path = entry.partition(b'\t')
        fields = header.split()
        if not separator or len(fields) != 3:
            raise ValueError('invalid index inventory')
        if fields[0] in {b'100644', b'100755'}:
            paths.add(path)
            if path.rsplit(b'/', 1)[-1] == b'.gitattributes':
                oids.add(fields[1])
    candidate = False
    if oids:
        blobs = subprocess.run(['git', 'cat-file', '--batch'], cwd=root, env=env,
                               input=b''.join(oid + b'\n' for oid in sorted(oids)),
                               capture_output=True, timeout=30)
        if blobs.returncode:
            raise ValueError('indexed attribute blobs cannot be inspected')
        rest = blobs.stdout
        if len(rest) > 32 * 1024 * 1024:
            raise ValueError('large indexed attributes')
        for oid in sorted(oids):
            header, separator, rest = rest.partition(b'\n')
            parts = header.split()
            if not separator or len(parts) != 3 or parts[:2] != [oid, b'blob']:
                raise ValueError('invalid indexed attributes')
            size = int(parts[2])
            if len(rest) < size + 1 or rest[size:size + 1] != b'\n':
                raise ValueError('truncated indexed attributes')
            candidate |= _active_filter(rest[:size], filters)
            rest = rest[size + 1:]
    result = candidate, paths
    configurations[key] = signature, result
    return result


def _effective_filter(root, gitdir, common, env, environment, filters, paths,
                      configurations, signature):
    """Let Git resolve patterns, macros, overrides and missing-worktree fallback."""
    key = ('effective-filters', root, gitdir, common, environment, frozenset(filters))
    previous = configurations.get(key)
    if previous is not None and previous[0] == signature:
        return previous[1]
    if len(paths) > 4096:
        raise ValueError('large attribute path inventory')
    data = b''.join(path + b'\0' for path in sorted(paths))
    if len(data) > 32 * 1024 * 1024:
        raise ValueError('large attribute path inventory')
    result = subprocess.run(['git', 'check-attr', '-z', '--stdin', '--all'],
                            cwd=root, env=env, input=data, capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 32 * 1024 * 1024:
        raise ValueError('effective attributes cannot be inspected')
    values = result.stdout.split(b'\0')
    if values.pop() != b'' or len(values) % 3:
        raise ValueError('unexpected Git attribute framing')
    names = {name.encode('utf-8', 'surrogateescape') for name in filters}
    active = any(attribute == b'filter' and value in names
                 for attribute, value in zip(values[1::3], values[2::3]))
    configurations[key] = signature, active
    return active


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _unchanged(observed):
    for (path, follow), before in observed.items():
        try:
            after = _stamp(os.stat(path, follow_symlinks=follow))
        except FileNotFoundError:
            after = None
        except OSError:
            return False
        if before != after:
            return False
    return True


def repository_state(root, *, isolated=False, configurations=None, contents=None, snapshots=None):
    """Observe state, discovering configuration with Git once per input change.

    Contents, rather than just mtime/size, cover restored timestamps on Windows.
    POSIX change times allow reusing bytes when a file has not been modified;
    restoring mtime still changes ctime. Windows always reads the bytes because
    its ctime can describe creation rather than the last metadata change.
    Bound the work on large/ignored trees: those reads safely fall back to Git.
    Git's private caches are excluded; object availability is checked as metadata.
    """
    if isolated:
        from .store import copy_environment
        env = copy_environment()
    else:
        env = dict(os.environ)
    environment = tuple(sorted(env.items()))
    identity = (root, isolated)
    previous = snapshots.get(identity) if snapshots is not None and os.name != 'nt' else None
    if previous is not None and previous[0] == environment and _unchanged(previous[1]):
        return previous[2]
    observed = {}

    def observe(path, follow=False):
        key = (os.fspath(path), follow)
        try:
            info = os.stat(path, follow_symlinks=follow)
        except FileNotFoundError:
            observed[key] = None
            raise
        observed[key] = _stamp(info)
        return info

    # Follow the original spelling too: a directory or gitfile may be reached
    # through a symlink which is retargeted without touching its old target.
    try:
        observe(root, True)
        observe(root / '.git', True)
    except OSError:
        return None
    root = root.resolve()
    if configurations is None:
        configurations = {}
    digest = hashlib.sha256()
    budget = [4096, 32 * 1024 * 1024]
    attribute_contents = []
    working_paths = set()
    git_roots = set()
    private_caches = {'workorder-digests', 'workorder-tests', 'workorder-bytecode'}

    def children(path, info):
        key = ('children', os.fspath(path))
        stamp = (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                 info.st_mtime_ns, info.st_ctime_ns)
        saved = contents.get(key) if contents is not None and os.name != 'nt' else None
        if saved is not None and saved[0] == stamp:
            return saved[1]
        with os.scandir(path) as directory:
            names = sorted(child.name for child in directory)
        if contents is not None and os.name != 'nt':
            contents[key] = (stamp, names)
        return names

    def visit(path, *, tree=False, metadata=False, info=None):
        try:
            info = info or observe(path)
        except FileNotFoundError:
            digest.update(b'missing' + os.fsencode(path))
            return
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError('large tree')
        digest.update(os.fsencode(path) + str(info.st_mode).encode())
        if stat.S_ISLNK(info.st_mode):
            if metadata:
                raise ValueError('indirect Git metadata')
            digest.update(os.fsencode(os.readlink(path)))
            target = Path(path).resolve()
            if target.is_relative_to(root / '.git'):
                # A task/config symlink can read a file in an otherwise private
                # cache directory. Its explicit target remains an input.
                visit(target, tree=True, metadata=True)
        elif stat.S_ISREG(info.st_mode):
            budget[1] -= info.st_size
            if budget[1] < 0:
                raise ValueError('large files')
            raw = _file_bytes(path, info, contents)
            digest.update(raw)
            if not metadata:
                working_paths.add(os.fsencode(Path(path).relative_to(root).as_posix()))
            if os.path.basename(path) == '.gitattributes':
                attribute_contents.append(raw)
        elif stat.S_ISDIR(info.st_mode) and tree:
            for name in children(path, info):
                if metadata and os.fspath(path) in git_roots and name in (
                        private_caches | {'config', 'config.worktree'}):
                    continue  # configuration dependencies are observed separately
                if not metadata and name == '.git':
                    if os.fspath(path) != os.fspath(root):
                        raise ValueError('nested repository')
                    continue
                visit(os.path.join(path, name), tree=True, metadata=metadata)
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError('special file')

    try:
        # GIT_CONFIG redirects `git config`, but other commands keep reading
        # runtime repository configuration. One discovery cannot describe both.
        if 'GIT_CONFIG' in env:
            return None
        supported = {'GIT_INDEX_FILE', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM',
                     'GIT_CONFIG_NOSYSTEM', 'GIT_TERMINAL_PROMPT', 'GIT_OPTIONAL_LOCKS',
                     'GIT_PAGER', 'GIT_EDITOR', 'GIT_PREFIX', 'GIT_EXEC_PATH',
                     'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT',
                     'GIT_ATTR_NOSYSTEM', 'GIT_REFLOG_ACTION', 'GIT_EXTERNAL_DIFF',
                     'GIT_PAGER_IN_USE', 'GIT_FLUSH'}
        prefixes = ('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_', 'GIT_AUTHOR_', 'GIT_COMMITTER_', 'GIT_TRACE')
        if env.get('GIT_PREFIX') or any(key.startswith('GIT_') and key not in supported
                                       and not key.startswith(prefixes) for key in env):
            return None
        visit(root, tree=True)
        working = digest.digest()
        digest = hashlib.sha256()
        gitdir = Path(env.get('GIT_DIR', root / '.git'))
        if not gitdir.is_absolute():
            gitdir = root / gitdir
        if gitdir.is_file():
            visit(gitdir)
            gitdir = root / gitdir.read_text().strip().removeprefix('gitdir: ')
        common = gitdir
        if (gitdir / 'commondir').is_file():
            common = gitdir / (gitdir / 'commondir').read_text().strip()
        common = Path(env.get('GIT_COMMON_DIR', common))
        observe(gitdir, True)
        observe(common, True)
        gitdir, common = gitdir.resolve(), common.resolve()
        digest.update(os.fsencode(gitdir) + b'\0' + os.fsencode(common))
        git_roots = {os.fspath(gitdir), os.fspath(common)}
        for directory in {gitdir, common}:
            # Reflogs, pseudorefs and in-progress merge/rebase state affect Git
            # reads too. Only Workorder's non-evidence caches are excluded.
            visit(directory, tree=True, metadata=True)
        if (common / 'objects/info/alternates').exists():
            return None
        if env.get('GIT_INDEX_FILE'):
            visit(root / env['GIT_INDEX_FILE'], metadata=True)
        configuration = _git_configuration(root, gitdir, common, env, environment, configurations, contents, observe)
        if configuration['unsafe']:
            return None
        digest.update(configuration['signature'])
        attributes = configuration['attributes'] | {directory / 'info/attributes' for directory in (gitdir, common)}
        digest.update(_configuration_files(configuration['inputs'] | attributes, contents, observe))
        filters = configuration['filters']
        if filters:
            attribute_contents.extend(path.read_bytes() for path in attributes if path.is_file())
            metadata = digest.digest()
            indexed_candidate, indexed_paths = _indexed_filter(
                root, gitdir, common, env, environment, filters, configurations, metadata)
            if indexed_candidate or any(_active_filter(raw, filters) for raw in attribute_contents):
                if _effective_filter(root, gitdir, common, env, environment, filters,
                                     working_paths | indexed_paths, configurations, (working, metadata)):
                    return None
        digest.update(repr(environment).encode('utf-8', 'surrogateescape'))
        result = working, digest.digest()
        if snapshots is not None and os.name != 'nt':
            snapshots[identity] = environment, observed, result
        return result
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None


class Batch:
    """Serialize framed requests; a timer also bounds blocked pipes on Windows."""
    def __init__(self, root):
        self.process = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=root,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
        self.lock = threading.Lock()

    def read(self, oid):
        with self.lock:
            timer = threading.Timer(30, self.process.kill)
            timer.daemon = True
            timer.start()
            try:
                self.process.stdin.write(oid.encode('utf-8', 'surrogateescape') + b'\n')
                self.process.stdin.flush()
                header = self.process.stdout.readline().split()
                if header and header[-1] == b'missing':
                    return None
                if len(header) != 3:
                    raise ValueError('Git could not read a task or receipt blob')
                size = int(header[2])
                raw = self.process.stdout.read(size)
                if len(raw) != size or self.process.stdout.read(1) != b'\n':
                    raise ValueError('Git returned an incomplete blob')
                return header[0].decode('ascii'), header[1], raw
            finally:
                timer.cancel()

    def close(self):
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass  # The failed request still needs its process and stdout reaped.
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
