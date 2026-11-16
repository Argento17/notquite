"""Run an optional repository test command and compare only its failing tests."""

from __future__ import annotations

from .console import print

import json
from pathlib import Path
import re
import subprocess

from . import gate, project_report, pytest_selection, store as s
from .python_imports import interpreter, pytest_arguments


CONFIG = '.workorder/config.json'
UNSET = ("only the declared tests ran; the project's other tests did not run "
         "(set a project test with workorder init --project-test)")


def _settings(root, staged=False):
    path = s.local_path(root, CONFIG)
    missing = CONFIG not in s.files(root, '.workorder', True) if staged else not path.exists()
    if missing:
        return {}
    try:
        value = json.loads(s.read_text(root, CONFIG, staged))
    except (OSError, ValueError) as error:
        raise s.Refused(f'{CONFIG}: cannot read project configuration: {error}') from None
    if not isinstance(value, dict):
        raise s.Refused(f'{CONFIG}: project configuration must be an object')
    return value


def _words(value):
    if not isinstance(value, str) or not value.strip():
        raise s.Refused(f'{CONFIG}: project_test must be a nonempty command')
    return s.split_command(value, f'{CONFIG}: project_test')


def command(root, staged=False):
    settings = _settings(root, staged)
    return _words(settings['project_test']) if 'project_test' in settings else None


def configure(root, value, *, check=False):
    """Preserve other settings; a check only reports whether this command is set."""
    _words(value)
    settings = _settings(root)
    if check:
        return settings.get('project_test') == value
    if settings.get('project_test') != value:
        settings['project_test'] = value
        path = s.local_path(root, CONFIG)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
        s.invalidate_reads()
    return True


def _pytest(words):
    """Only direct pytest entry points have a known failure-selection protocol."""
    executable = Path(words[0].replace('\\', '/')).name
    if executable in {'pytest', 'py.test', 'pytest.exe', 'py.test.exe'}:
        return True
    return (bool(re.fullmatch(r'(?:python|pypy)(?:\d+(?:\.\d+)*)?(?:\.exe)?', executable))
            and pytest_arguments(words) is not None)


def _options(words, options):
    end = words.index('--') if '--' in words else len(words)
    return words[:end] + options + words[end:]


def _report_source(path):
    """Use pytest's exact node IDs; terminal summaries have ambiguous separators."""
    return project_report.SOURCE + f'\n_workorder_report_path = {str(path)!r}\n'


def _report(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    outcomes = value.get('outcomes')
    if not isinstance(outcomes, dict) or not all(
            isinstance(node, str) and isinstance(outcome, str) and outcome in {'PASSED', 'FAILED'}
            for node, outcome in outcomes.items()):
        return {}
    if not all(isinstance(value.get(key), str) for key in ('summary', 'short_summary')):
        return {}
    if not isinstance(value.get('failures'), list) or not all(
            isinstance(section, str) for section in value['failures']):
        return {}
    return value


def _failure_output(report, output):
    if report.get('short_summary'):
        print(report['short_summary'])
    sections = report.get('failures') or []
    if not sections:
        # Startup failures/timeouts can prevent the plugin from writing a report.
        # Keep their diagnostic text without treating it as outcome evidence.
        lines = re.sub(r'\x1b\[[0-9;]*m', '', output).strip().splitlines()
        if report:
            # A completed session already supplied its diagnostics and counts.
            # Pytest emits usage errors after sessionfinish; retain just those,
            # never replay progress or output from tests that already passed.
            lines = [line for line in lines if line.startswith('ERROR:')]
        if len(lines) > 60:
            print('[project-test output truncated to last 60 lines]')
        if lines:
            print('\n'.join(lines[-60:]))
    for section in sections:
        lines = section.splitlines()
        print('\n'.join(lines[:60]))
        if len(lines) > 60:
            print('[failure section truncated after 60 lines]')
    if report.get('summary'):
        print(report['summary'])


def _reported_command(root, words, report):
    python = interpreter(words, root, s.copy_environment())
    if python is None:
        return words  # An unidentifiable runner cannot supply comparison evidence.
    source = (_report_source(report) + '\nimport pytest, sys\n'
              'raise SystemExit(pytest.main(sys.argv[1:], plugins=[sys.modules[__name__]]))\n')
    return [*python, '-c', source, *_options(pytest_arguments(words), ['-rfE', '--color=no'])]


def _selected_command(words, nodes, report):
    record = {'test': s.join_command(words)}
    positions = set(gate.pytest_collection_arguments(record))
    selected = [word for index, word in enumerate(words) if index not in positions]
    selected = pytest_selection.command(selected + list(nodes), nodes)
    selected = _options(selected, ['-rfE', '--color=no'])
    # Configured addopts can contain more collection paths. Always deselect
    # everything outside the failed nodes, including when no node is present.
    source = (_report_source(report) + '\nimport pytest\n'
              f'WANTED = {nodes!r}\n'
              'def pytest_addoption(parser):\n'
              '    parser.addoption("--workorder-test-node", action="append", default=[])\n'
              '@pytest.hookimpl(trylast=True)\n'
              'def pytest_collection_modifyitems(config, items):\n'
              '    deselected = [item for item in items if item.nodeid not in WANTED]\n'
              '    items[:] = [item for item in items if item.nodeid in WANTED]\n'
              '    if deselected:\n'
              '        config.hook.pytest_deselected(items=deselected)\n')
    return selected, {pytest_selection.MODULE + '.py': (source.encode('utf-8'), None)}


def run(root, *, base=None, label=None, notes=None, counts=None, allow=(), timeout=None):
    """Run current code once, then only failed pytest nodes on the available base.

    Returned failures are ordinary strings: warning mode cannot turn an observed
    current failure into a pass. A PR review or its explicit tests label can.
    """
    notes = [] if notes is None else notes
    try:
        words = command(root)
    except s.Refused as error:
        return [str(error)]
    if words is None:
        notes.append(UNSET)
        return []

    def finding(message):
        if 'tests' in allow:
            notes.append(message + ' (accepted by review)')
            return []
        return [gate.reviewer_label(message, 'tests')]

    pytest = _pytest(words)
    with s.temporary_checkout(prefix='workorder-project-') as temporary, s.test_reuse(False):
        current_report = Path(temporary) / 'current.json'
        current_command = _reported_command(root, words, current_report) if pytest else words
        code, output = gate._run_test(root, current_command, timeout, counts, quiet=pytest)
        report = _report(current_report) if pytest else {}
        if code == 0:
            if pytest:
                summary = f" ({report['summary']})" if report.get('summary') else ''
                notes.append('the project test passed on this code' + summary)
            return []
        if pytest:
            _failure_output(report, output)
        failed = sorted(node for node, outcome in report.get('outcomes', {}).items()
                        if outcome == 'FAILED') if pytest and code == 1 else []
        if not failed:
            return finding(f'the project test fails on this code (exit {code}); '
                           'could not identify failing tests to compare with the base')
        base = base or gate.local_test_base(root)
        if not base:
            return finding('the project test fails on this code: ' + ', '.join(failed)
                           + '; could not compare: no target is available locally')
        display = (label or base).removeprefix('refs/remotes/').removeprefix('refs/heads/')
        base_report = Path(temporary) / 'base.json'
        selected, overlays = _selected_command(words, failed, base_report)
        try:
            revision = s.git(root, 'rev-parse', '--verify', f'{base}^{{commit}}').stdout.strip()
            before, before_output = gate.run_ref_test(
                root, Path(temporary) / 'base', revision, selected, timeout, counts, overlays)
        except (s.Refused, OSError, ValueError, subprocess.SubprocessError) as error:
            return finding(f'the project test fails on this code; could not compare with {display}: {error}')
        report = _report(base_report)
        if report.get('summary'):
            notes.append(f"the project test on {display} ({report['summary']})")
        outcomes = report.get('outcomes', {}) if before in {0, 1} else {}

    problems = []
    for node in failed:
        outcome = outcomes.get(node)
        if outcome == 'FAILED':
            notes.append(f'the project test also fails on {display}: {node}')
        elif outcome == 'PASSED':
            problems += finding(f'the project test fails on this code: {node} (passes on {display})')
        else:
            problems += finding(f'the project test fails on this code: {node}; '
                                f'could not compare this failing test with {display} (exit {before})')
    return problems
