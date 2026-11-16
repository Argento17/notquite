"""Standalone pytest plugin source, loaded in the project's own interpreter."""

# The runner supplies _workorder_report_path. Keep this independent of an
# installed Workorder so isolated Python and disposable base copies can use it.
SOURCE = '''import io
import json
from pathlib import Path

import pytest
from _pytest._io import TerminalWriter

_workorder_outcomes = {}
_workorder_failures = {}
_workorder_errors = []


def pytest_runtest_logreport(report):
    if report.failed:
        _workorder_outcomes[report.nodeid] = "FAILED"
        _workorder_failures.setdefault(report.nodeid, []).append(report)
    elif report.when == "call" and report.passed:
        _workorder_outcomes.setdefault(report.nodeid, "PASSED")


def pytest_collectreport(report):
    if report.failed:
        _workorder_failures.setdefault(report.nodeid, []).append(report)


def _workorder_session_error(excinfo):
    _workorder_errors.append(("ERROR: pytest session - " + excinfo.exconly(),
                              str(excinfo.getrepr(style="short"))))


def pytest_keyboard_interrupt(excinfo):
    _workorder_session_error(excinfo)


def pytest_internalerror(excrepr, excinfo):
    _workorder_session_error(excinfo)


def _workorder_capture(terminal, render):
    stream = io.StringIO()
    previous = terminal._tw
    terminal._tw = TerminalWriter(stream)
    terminal._tw.hasmarkup = False
    try:
        render()
        return stream.getvalue().strip()
    finally:
        terminal._tw = previous


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    yield
    # Workers also load the plugin, but only the controller has the complete
    # reports. Do not race its JSON file with per-worker results.
    if hasattr(session.config, "workerinput"):
        return
    data = {"outcomes": _workorder_outcomes, "summary": "",
            "short_summary": "\\n".join(error for error, _ in _workorder_errors),
            "failures": [detail for _, detail in _workorder_errors]}
    terminal = session.config.pluginmanager.getplugin("terminalreporter")
    if terminal is not None:
        verbose, reportchars = session.config.option.verbose, terminal.reportchars
        try:
            # Ask pytest itself for its counts, even with -qq or --no-summary.
            session.config.option.verbose = -1
            terminal.reportchars = "fE"
            data["summary"] = _workorder_capture(terminal, terminal.summary_stats)
            short = _workorder_capture(terminal, terminal.short_test_summary)
            data["short_summary"] = "\\n".join(filter(None, [short, data["short_summary"]]))
            for node, reports in _workorder_failures.items():
                def render():
                    terminal.write_sep("_", node)
                    for report in reports:
                        terminal._outrep_summary(report)
                data["failures"].append(_workorder_capture(terminal, render))
        finally:
            session.config.option.verbose = verbose
            terminal.reportchars = reportchars
    else:
        data["failures"] += ["\\n".join(report.longreprtext for report in reports)
                             for reports in _workorder_failures.values()]
    Path(_workorder_report_path).write_text(json.dumps(data), encoding="utf-8")
'''
