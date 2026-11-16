"""A small pytest plugin copied only into disposable comparison checkouts."""

import json
import re


MODULE = '__workorder_test_selection'
MARKER = 'workorder selected tests: '
SOURCE = '''import json
import pytest

def pytest_addoption(parser):
    parser.addoption('--workorder-test-node', action='append', default=[])

@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    wanted = config.getoption('--workorder-test-node')
    selected = [item for item in items if any(
        item.nodeid == node or item.nodeid.startswith(node + '[') for node in wanted)]
    config._workorder_selected = sorted({node for node in wanted if any(
        item.nodeid == node or item.nodeid.startswith(node + '[') for item in selected)})
    if selected:
        deselected = [item for item in items if item not in selected]
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)

def pytest_terminal_summary(terminalreporter):
    selected = getattr(terminalreporter.config, '_workorder_selected', None)
    if selected is not None:
        terminalreporter.write_line('workorder selected tests: ' + json.dumps(selected))
'''.encode('utf-8')


def command(words, tests):
    if not tests:
        return words
    options = ['-p', MODULE, '-rA']
    for test in tests:
        options += ['--workorder-test-node', test]
    end = words.index('--') if '--' in words else len(words)
    return words[:end] + options + words[end:]


def overlay(tests):
    return {MODULE + '.py': (SOURCE, None)} if tests else {}


def selected(output, fallback):
    reports = re.findall('^' + re.escape(MARKER) + r'(.*)$', output, re.M)
    if reports:
        try:
            value = json.loads(reports[-1])
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return value
        except ValueError:
            pass
    return fallback  # import errors may stop collection before the selection hook
