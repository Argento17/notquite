"""Compare code without documentation, while retaining historical receipt hashes."""
import ast
import hashlib
from pathlib import PurePosixPath


def documentation(path):
    value = PurePosixPath(path.lower())
    return (bool({'docs', 'doc'} & set(value.parts[:-1]))
            or value.suffix in {'.md', '.markdown', '.mdown', '.rst', '.rest', '.txt', '.text'})


class _WithoutDocstrings(ast.NodeTransformer):
    def generic_visit(self, node):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node) is not None:
                node.body = node.body[1:]
        # Python 3.12 added this empty field to functions and classes. Empty
        # type parameters must not change receipts made on supported 3.10/3.11.
        if hasattr(node, 'type_params') and not node.type_params:
            node._fields = tuple(field for field in node._fields if field != 'type_params')
        return super().generic_visit(node)


def python_code(content):
    try:
        return ast.dump(_WithoutDocstrings().visit(ast.parse(content)))
    except (SyntaxError, ValueError, UnicodeError):
        return None


# Normalizing is a pure function of the source, and one command compares the same
# sources many times (base, branch, index, stubbed copies): parse each source once.
_DIGESTS = {}


def python_digest(content):
    """The digest of python_code(content), or None when it does not parse."""
    raw = content if isinstance(content, bytes) else content.encode('utf-8', 'surrogatepass')
    key = (isinstance(content, bytes), hashlib.sha256(raw).digest())
    found = _DIGESTS.get(key, False)
    if found is False:
        code = python_code(content)
        found = None if code is None else hashlib.sha256(code.encode()).hexdigest()
        if len(_DIGESTS) >= 65536:
            _DIGESTS.clear()
        _DIGESTS[key] = found
    return found


def same_python(before, after):
    if before is None or after is None:
        return False
    old = python_digest(before)
    return old is not None and old == python_digest(after)


def entries(root, tree, syntax=None, *, missing_ok=False, protected=None):
    """Modes stay significant; normalize Python blobs only for comparisons."""
    from . import store as s
    from .gate import test_material
    protected = test_material(root, 'HEAD') if protected is None else protected
    tests = {path: entry for path, entry in tree.items()
             if path in protected and not s.generated_path(path)}
    code = {path: entry for path, entry in tree.items()
            if path not in protected and not documentation(path) and not s.generated_path(path)}
    return {**_python_entries(root, code, syntax, missing_ok=missing_ok), **tests}


def _python_entries(root, tree, syntax=None, *, missing_ok=False):
    """Normalize an already selected immutable map, without consulting task definitions."""
    from . import store as s
    tree = dict(tree)
    syntax = syntax or {}
    tree.update({path: entry for path, entry in syntax.items() if path in tree})
    python = {path: entry for path, entry in tree.items()
              if path not in syntax and path.endswith('.py') and entry
              and entry.startswith(('100644 blob ', '100755 blob '))}
    oids = {entry.rsplit(' ', 1)[-1] for entry in python.values()}
    if missing_ok:
        # Older receipts have no syntax map. Missing pre-squash blobs cannot
        # establish equivalence, but must not make ordinary raw matches fail.
        oids = {oid for oid in oids if not s.git(root, 'cat-file', '-e', oid, required=False).returncode}
    blobs = s.read_blobs(root, oids)
    for path, entry in python.items():
        mode, kind, oid = entry.split()
        syntax = python_digest(blobs[oid]) if oid in blobs else None
        if syntax is not None:
            tree[path] = mode + ' ' + kind + ' ' + syntax
    return tree


def syntax_map(root, paths):
    normalized = _python_entries(root, paths)
    return {path: entry for path, entry in normalized.items()
            if path.endswith('.py') and entry != paths[path]}


def changed(root, paths, before, *, after=None, staged=False):
    """Keep task metadata and real code edits, omitting documentation-only changes."""
    from . import store as s
    from .gate import _test_bytes, test_material
    paths = set(paths)
    metadata = {path for path in paths if path.startswith(('tasks/', '.workorder/', '.notquite/'))}
    omissions = {path for path in paths - metadata if documentation(path)}
    python = {path for path in paths - metadata - omissions if path.endswith('.py')}
    old_tree = s.code_tree(root, before) if python else {}
    # Working-tree comparisons must also work while receipt conflicts remain
    # in the index. Read that index only when it is the requested snapshot.
    new_tree = s.code_tree(root, after or s.index_tree(root)) if python and (after or staged) else {}
    for path in python:
        old = old_tree.get(path, '').split(' ', 1)[0]
        new = new_tree.get(path, '').split(' ', 1)[0]
        if not after and not staged:
            try:
                new = '100755' if (root / path).stat().st_mode & 0o111 else '100644'
            except OSError:
                new = ''
        if (old in {'100644', '100755'} and old == new
                and same_python(_test_bytes(root, path, before),
                                _test_bytes(root, path, after, staged))):
            omissions.add(path)
    # Classification only matters for paths we might omit. A real code change
    # remains significant whether it is a test or production code; computing all
    # tasks' test material for each such comparison adds work without evidence.
    protected = test_material(root, before, staged) if omissions else set()
    return paths - (omissions - protected)
