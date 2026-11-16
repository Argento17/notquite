"""Compare code without documentation, while retaining historical receipt hashes."""
import ast
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


def same_python(before, after):
    if before is None or after is None:
        return False
    old = python_code(before)
    return old is not None and old == python_code(after)


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
        syntax = python_code(blobs[oid]) if oid in blobs else None
        if syntax is not None:
            tree[path] = mode + ' ' + kind + ' ' + s.digest(syntax.encode())
    return tree


def syntax_map(root, paths):
    normalized = _python_entries(root, paths)
    return {path: entry for path, entry in normalized.items()
            if path.endswith('.py') and entry != paths[path]}


def changed(root, paths, before, *, after=None, staged=False):
    """Keep task metadata and real code edits, omitting documentation-only changes."""
    from . import store as s
    from .gate import _test_bytes, test_material
    protected = test_material(root, before, staged)
    paths = {path for path in paths if path.startswith(('tasks/', '.workorder/', '.notquite/'))
             or path in protected or not documentation(path)}
    if not any(path.endswith('.py') for path in paths):
        return paths
    old_tree = s.code_tree(root, before)
    # Working-tree comparisons must also work while receipt conflicts remain
    # in the index. Read that index only when it is the requested snapshot.
    new_tree = s.code_tree(root, after or s.index_tree(root)) if after or staged else {}
    result = set()
    for path in paths:
        if path in protected or path.startswith(('tasks/', '.workorder/', '.notquite/')):
            result.add(path)
            continue
        if documentation(path):
            continue
        if path.endswith('.py'):
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
                continue
        result.add(path)
    return result
