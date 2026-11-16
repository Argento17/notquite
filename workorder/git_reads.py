"""Bounded, command-local read caching and a repository's cat-file connection."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
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

    def clear(self, *, queries=True):
        super().clear()
        if queries:
            self.queries.clear()
        self.generation += 1

    @contextmanager
    def checking(self, root, *, isolated=False):
        identity = (root, isolated)
        reusable = True
        if not self.stack or self.stack[-1] != identity:
            state = None if identity in self.disabled else repository_state(Path(root), isolated=isolated)
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
        self.stack.append(identity)
        try:
            yield reusable
        finally:
            self.stack.pop()

    def close(self):
        for batch in self.batches.values():
            batch.close()


def repository_state(root, *, isolated=False):
    """Read state without spawning Git. If inspection is incomplete, do not reuse.

    Contents, rather than just mtime/size, cover restored timestamps on Windows.
    Bound the work on large/ignored trees: those reads safely fall back to Git.
    Git's private caches are excluded; object availability is checked as metadata.
    """
    if isolated:
        from .store import copy_environment
        env = copy_environment()
    else:
        env = os.environ
    root = root.resolve()
    digest = hashlib.sha256()
    budget = [4096, 32 * 1024 * 1024]

    def visit(path, *, tree=False, metadata=False):
        try:
            info = path.lstat()
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
        elif stat.S_ISREG(info.st_mode):
            budget[1] -= info.st_size
            if budget[1] < 0:
                raise ValueError('large files')
            digest.update(path.read_bytes())
        elif stat.S_ISDIR(info.st_mode) and tree:
            for child in sorted(path.iterdir()):
                if not metadata and child.name == '.git':
                    if path != root:
                        raise ValueError('nested repository')
                    continue
                visit(child, tree=True, metadata=metadata)
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError('special file')

    def config(path):
        visit(path, metadata=True)
        if path.is_file():
            raw = path.read_bytes().lower()
            # These settings can read arbitrary files or execute helpers.
            # Fall back to fresh Git instead of guessing their dependencies.
            if any(word in raw for word in (b'include', b'excludesfile', b'attributesfile',
                                            b'fsmonitor', b'textconv', b'[filter', b'external')):
                raise ValueError('external Git configuration')

    try:
        supported = {'GIT_INDEX_FILE', 'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM',
                     'GIT_CONFIG_NOSYSTEM', 'GIT_TERMINAL_PROMPT', 'GIT_OPTIONAL_LOCKS',
                     'GIT_PAGER', 'GIT_EDITOR', 'GIT_PREFIX'}
        if env.get('GIT_PREFIX') or any(key.startswith('GIT_') and key not in supported for key in env):
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
        for directory in {gitdir, common}:
            for name in ('HEAD', 'index', 'packed-refs',
                         'refs', 'shallow', 'info', 'commondir', 'objects'):
                visit(directory / name, tree=True, metadata=True)
            config(directory / 'config')
            config(directory / 'config.worktree')
        if (common / 'objects/info/alternates').exists():
            return None
        if env.get('GIT_INDEX_FILE'):
            visit(root / env['GIT_INDEX_FILE'], metadata=True)
        for name in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM'):
            if os.environ.get(name):
                config(Path(os.environ[name]))
        config(Path.home() / '.gitconfig')
        config(Path('/etc/gitconfig'))
        config(Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'git/config')
        visit(Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'git/ignore', metadata=True)
        visit(Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'git/attributes', metadata=True)
        digest.update(repr(sorted(os.environ.items())).encode('utf-8', 'surrogateescape'))
        return working, digest.digest()
    except (OSError, ValueError):
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
