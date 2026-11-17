"""Windows report-publication races must not become failed comparison tests."""
from pathlib import Path

import pytest

from workorder import python_imports, store
from test_v16g_import_guard import comparison
from test_workorder import PYTHON, local_repo


def test_import_report_permission_race_keeps_successful_comparison(local_repo, tmp_path, monkeypatch):
    release = tmp_path / 'continue-test'
    destination = comparison(local_repo, tmp_path, extra=(
        '\ndef test_after_publication():\n'
        '    import pathlib, time\n'
        f'    release = pathlib.Path({str(release)!r})\n'
        '    deadline = time.monotonic() + 10\n'
        '    while not release.exists() and time.monotonic() < deadline:\n'
        '        time.sleep(0.01)\n'
        '    assert release.exists()\n'))
    actual_read = Path.read_text

    def read_report(path, *args, **kwargs):
        content = actual_read(path, *args, **kwargs)
        if path.name == 'report.json' and path.parent.name.startswith('workorder-imports-') and not release.exists():
            # The child has published its valid report. Inject a transient
            # sharing violation at the parent's file-read boundary.
            release.touch()
            raise PermissionError('report publication temporarily denies access')
        return content

    monkeypatch.setattr(Path, 'read_text', read_report)
    run = store.cached_test([PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py'],
                            destination, cache_root=local_repo, fresh=True)
    assert release.exists(), 'the report publication fault was not exercised'
    assert run.returncode == 0, run.stdout + run.stderr
    assert '2 passed' in run.stdout


@pytest.mark.parametrize('failure', ['permission', 'malformed'])
def test_unreadable_import_report_still_refuses_comparison(local_repo, tmp_path, monkeypatch, failure):
    destination = comparison(local_repo, tmp_path)
    actual_read = Path.read_text

    def read_report(path, *args, **kwargs):
        content = actual_read(path, *args, **kwargs)
        if path.name == 'report.json' and path.parent.name.startswith('workorder-imports-'):
            if failure == 'permission':
                raise PermissionError('report remains inaccessible')
            return '{'
        return content

    monkeypatch.setattr(Path, 'read_text', read_report)
    run = store.cached_test([PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py'],
                            destination, cache_root=local_repo, fresh=True)
    assert isinstance(run.returncode, python_imports.ImportFailure), run
    assert 'could not verify project imports' in run.returncode
    assert not run.cached
