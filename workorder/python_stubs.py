"""Replace changed Python functions in a disposable test copy."""

import ast


def functions(tree):
    found = {}

    class Collect(ast.NodeVisitor):
        def __init__(self):
            self.scope = ()
            self.occurrences = {}

        def visit_definition(self, node):
            name = (*self.scope, node.name)
            occurrence = self.occurrences.get(name, 0)
            self.occurrences[name] = occurrence + 1
            if not isinstance(node, ast.ClassDef):
                found[(name, occurrence)] = node
            previous = self.scope
            self.scope = name
            self.generic_visit(node)
            self.scope = previous

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = visit_definition

    Collect().visit(tree)
    return found


def changed_functions(old, new):
    """Return stubbed source and qualified names; whitespace alone changes nothing."""
    before = functions(ast.parse(old or b''))
    tree = ast.parse(new)
    after = functions(tree)
    changed = {node for key, node in after.items()
               if key not in before or ast.dump(node) != ast.dump(before[key])}
    if not changed:
        return None, []
    names = ['.'.join(key[0]) for key, node in after.items() if node in changed]

    class Stub(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node in changed:
                node.body = [ast.Raise(exc=ast.Call(func=ast.Name(id='NotImplementedError', ctx=ast.Load()),
                                                   args=[ast.Constant(value='workorder stub')], keywords=[]),
                                       cause=None)]
                return node
            return self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    return (ast.unparse(ast.fix_missing_locations(Stub().visit(tree))) + '\n').encode('utf-8'), names
