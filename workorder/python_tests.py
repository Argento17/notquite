"""Compare existing Python test statements while allowing additions."""

import ast
import copy
import symtable


def _names(tree):
    """Names that an added definition or import must not replace."""
    scope = symtable.symtable(ast.unparse(tree), '<tests>', 'exec')
    if isinstance(tree, ast.ClassDef):
        scope = scope.get_children()[0]
    found = set(scope.get_identifiers())
    def globals_in(child):
        found.update(symbol.get_name() for symbol in child.get_symbols() if symbol.is_global())
        for nested in child.get_children():
            globals_in(nested)
    for child in scope.get_children():
        globals_in(child)
    return found


def _bindings(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {item.asname or item.name.split('.')[0] for item in node.names}
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}


def _parametrize_values(node):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'parametrize' and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == 'mark'):
        return None
    value = node.args[1] if len(node.args) >= 2 else next(
        (item.value for item in node.keywords if item.arg == 'argvalues'), None)
    return value if isinstance(value, (ast.List, ast.Tuple)) else None


def _preserved_decorator(old, new):
    if ast.dump(old) == ast.dump(new):
        return True
    before, after = _parametrize_values(old), _parametrize_values(new)
    if before is None or after is None or len(after.elts) <= len(before.elts):
        return False
    if [ast.dump(item) for item in before.elts] != [ast.dump(item) for item in after.elts[:len(before.elts)]]:
        return False
    restored = copy.deepcopy(new)
    _parametrize_values(restored).elts = copy.deepcopy(before.elts)
    return ast.dump(old) == ast.dump(restored)


def _preserved_statement(old, new, used):
    if ast.dump(old) == ast.dump(new):
        return True
    if type(old) is not type(new):
        return False
    if isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if len(old.decorator_list) != len(new.decorator_list) or not all(
                _preserved_decorator(a, b) for a, b in zip(old.decorator_list, new.decorator_list)):
            return False
        restored = copy.deepcopy(new)
        restored.decorator_list = copy.deepcopy(old.decorator_list)
        if isinstance(old, ast.ClassDef):
            if not _preserved_body(old.body, new.body, _names(old)):
                return False
            restored.body = copy.deepcopy(old.body)
        return ast.dump(old) == ast.dump(restored)
    if isinstance(old, (ast.Import, ast.ImportFrom)) and getattr(old, 'module', '') != '__future__':
        if getattr(old, 'module', None) != getattr(new, 'module', None) or getattr(old, 'level', None) != getattr(new, 'level', None):
            return False
        aliases = [(item.name, item.asname) for item in old.names]
        remaining = iter((item.name, item.asname) for item in new.names)
        if not all(alias in remaining for alias in aliases):
            return False
        extra = [item for item in new.names if (item.name, item.asname) not in aliases]
        bindings = [item.asname or item.name.split('.')[0] for item in extra]
        return bool(extra) and not (set(bindings) & used or '*' in bindings or len(set(bindings)) != len(bindings))
    return False


def _preserved_body(old, new, used):
    index = 0
    for node in new:
        if index < len(old) and _preserved_statement(old[index], node, used):
            index += 1
        elif not _addition(node, used):
            return False
    return index == len(old)


def _addition(node, used):
    if _bindings(node) & used:
        return False
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import)):
        return True
    if isinstance(node, ast.ImportFrom):
        return node.module != '__future__' and all(item.name != '*' for item in node.names)
    if isinstance(node, ast.Assign):
        return all(isinstance(target, ast.Name) for target in node.targets)
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name)
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def preserves_existing(old, new):
    """All old statements remain, with only appended literal parameter cases."""
    try:
        before, after = ast.parse(old), ast.parse(new)
    except (SyntaxError, ValueError, UnicodeError):
        return False
    return _preserved_body(before.body, after.body, _names(before))
