"""Keep reviewer remedies tied to the executable diagnostic sources."""

import ast
import hashlib
import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / 'docs/reviewer-guide.md'
FAMILIES = {
    'records': ('malformed', 'dependency', 'workorder check ID'),
    'evidence': ('NOT DONE', 'receipt', 'renew --task ID'),
    'current-tests': ('no test passed', 'fix', 'no label'),
    'definitions': ('written and finished in this pull request', 'is new in this pull request',
                    'workorder-allow:ID', 'approving review'),
    'test-edits': ('test_file_patterns', 'test_directory_patterns', 'src/click/testing.py',
                   'test changed', 'workorder-allow:tests', 'approving review'),
    'old-tests': ('tests from', 'workorder-allow:ID', 'approval alone'),
    'weak-tests': ('kind: refactor', 'kind: verification', 'completion needs review',
                   'asserts nothing', 'stubbed out', 'explicit'),
    'test-first': ('--require-test-first', 'workorder-allow:ID'),
    'scope': ('workorder-allow:scope:ID', 'approving review'),
    'code-receipts': ('--require-task', 'workorder-allow:no-task'),
    'imports': ('no tests were collected', 'could not compare', 'pythonpath', 'workorder-allow:ID'),
    'workflow': ('workorder-allow:workflow', 'approval alone'),
    'project-test': ('project test', '--project-test', 'no label'),
    'renewal': ('its code changed after it was finished', 'renew --task ID'),
    'removal': ('removed', 'receipts', 'unfinished'),
    'pending': ('pending review', 'workorder-allow:ID'),
}
MARKER = re.compile(r'<!-- refusal-inventory (\S+) ([0-9a-f]{64}) ([a-z,-]+) -->')


class Executable(ast.NodeTransformer):
    def generic_visit(self, node):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) is not None:
                node.body = node.body[1:]
        return super().generic_visit(node)


def syntax_value(value):
    """Keep fingerprints stable across supported Python AST versions."""
    if isinstance(value, ast.AST):
        # New optional fields, such as type_params, must not change old syntax.
        fields = {key: syntax_value(item) for key, item in ast.iter_fields(value)
                  if item is not None and item != []}
        return [type(value).__name__, fields]
    if isinstance(value, list):
        return [syntax_value(item) for item in value]
    if isinstance(value, (bytes, complex)) or value is Ellipsis:
        return [type(value).__name__, repr(value)]
    return value


def diagnostic_inventory(text):
    """Overinclude fragments and result sinks instead of guessing English messages.

    A returned variable, a newly appended finding, and a new exception all count
    even when they reuse existing wording. All package modules are checked, so
    moving a refusal to a new helper cannot bypass the inventory. Docstrings and
    comments do not affect executable diagnostics. Some unrelated source edits
    also require a human to review the guide; this is deliberately conservative.
    """
    tree = Executable().visit(ast.parse(text))
    fragments = []
    for node in ast.walk(tree):
        literal = isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))
        sink = isinstance(node, (ast.Return, ast.Raise, ast.Assert, ast.AugAssign, ast.If, ast.IfExp))
        if isinstance(node, ast.Call):
            name = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
            sink |= name in {'append', 'extend', 'print', 'Refused'} or name.endswith(('Problem', 'Failure'))
        if literal or isinstance(node, ast.JoinedStr) or sink:
            fragments.append(json.dumps(syntax_value(node), sort_keys=True, separators=(',', ':')))
    return sorted(fragments)


def fingerprint(text):
    payload = json.dumps(diagnostic_inventory(text), ensure_ascii=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def guide_sources(guide):
    entries = MARKER.findall(guide)
    assert len({entry[0] for entry in entries}) == len(entries), 'duplicate refusal inventory module'
    return {name: (digest, set(families.split(','))) for name, digest, families in entries}


def test_reviewer_guide_covers_refusals_and_remedies():
    assert GUIDE.is_file(), 'write docs/reviewer-guide.md with meanings and clearing actions'
    guide = GUIDE.read_text(encoding='utf-8')
    assert len(guide.splitlines()) <= 150
    sections = dict(re.findall(r'<!-- refusal-family ([a-z-]+) -->\n(.*?)(?=\n<!-- refusal-family|\n<!-- refusal-inventory|\Z)',
                               guide, re.S))
    assert set(sections) == set(FAMILIES)
    for family, required in FAMILIES.items():
        assert 'Meaning:' in sections[family], family
        assert 'Clear:' in sections[family], family
        for phrase in required:
            assert phrase in sections[family], (family, phrase)


def test_reviewer_guide_inventory_matches_executable_diagnostics():
    assert GUIDE.is_file(), 'write the reviewer guide before accepting diagnostic inventory'
    documented = guide_sources(GUIDE.read_text(encoding='utf-8'))
    sources = {path.relative_to(ROOT).as_posix(): path for path in (ROOT / 'workorder').rglob('*.py')}
    assert set(documented) == set(sources), 'new or removed diagnostic module needs guide review'
    assert set().union(*(families for _, families in documented.values())) == set(FAMILIES)
    changed = [name for name, path in sources.items()
               if fingerprint(path.read_text(encoding='utf-8')) != documented[name][0]]
    assert not changed, ('Review each changed diagnostic and its meaning/remedy in docs/reviewer-guide.md, '
                         'then update the reviewed fingerprint: ' + ', '.join(sorted(changed)))


def test_readme_links_short_reviewer_guide():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert len(readme.splitlines()) <= 130
    assert '[Reviewer guide](docs/reviewer-guide.md)' in readme


@pytest.mark.parametrize('changed', [
    'def check():\n    return ["new refusal"]\n',
    'def check():\n    problems = ["old refusal"]\n    problems.append(reason)\n    return problems\n',
    'def check():\n    return ["old refusal"]\n\ndef helper():\n    raise Refused(message)\n',
])
def test_inventory_detects_new_wording_or_dynamic_refusal(changed):
    assert fingerprint(changed) != fingerprint('def check():\n    return ["old refusal"]\n')


def test_inventory_detects_new_condition_using_existing_message():
    original = 'def check():\n    if broken:\n        return reason\n'
    changed = 'def check():\n    if broken or new_rule:\n        return reason\n'
    assert fingerprint(original) != fingerprint(changed)


def test_inventory_ignores_comment_and_docstring_only_edits():
    original = 'def check():\n    """Old explanation."""\n    return ["old refusal"]\n'
    changed = '# More context.\ndef check():\n    """New explanation."""\n    return ["old refusal"]\n'
    assert fingerprint(original) == fingerprint(changed)
