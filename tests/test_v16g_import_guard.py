"""A comparison verifies pytest imports in its actual test interpreter."""
import os
from pathlib import Path
import shlex
import subprocess

import pytest

from workorder import gate, python_imports, pytest_selection, store
from test_workorder import PYTHON, git, local_repo


def comparison(root, tmp_path, *, config='[pytest]\npythonpath = .\n', extra=''):
    (root / 'calc.py').write_text('def value():\n    return 3\n')
    (root / 'pytest.ini').write_text(config)
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'tests/test_calc.py').write_text(
        'from calc import value\n\ndef test_value():\n    assert value() == 3\n' + extra)
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'guard fixture')
    destination = tmp_path / 'comparison'
    gate._ref_test_snapshot(root, destination, 'HEAD')
    return destination


def count_python(monkeypatch):
    calls = []
    actual = subprocess.Popen

    def start(argv, *args, **kwargs):
        if Path(argv[0]).name.lower().startswith(('python', 'pytest', 'py.test')):
            calls.append(list(argv))
        return actual(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', start)
    return calls


@pytest.mark.parametrize('runner', ['module', 'console', '-I', '-E', 'selected-I'])
def test_fresh_pytest_comparison_uses_one_interpreter(local_repo, tmp_path, monkeypatch, runner):
    root = local_repo
    destination = comparison(root, tmp_path, extra='\ndef test_other():\n    assert False\n'
                             if runner == 'selected-I' else '')
    if runner == 'console':
        # Windows installs entry points in Scripts, beside the base
        # interpreter only for a virtualenv. Ask the selected interpreter.
        scripts = subprocess.check_output(
            [PYTHON, '-c', 'import sysconfig; print(sysconfig.get_path("scripts"))'], text=True).strip()
    command = ([str(Path(scripts) / ('pytest.exe' if os.name == 'nt' else 'pytest'))]
               if runner == 'console' else
               [PYTHON, *([runner] if runner in {'-I', '-E'} else ['-I'] if runner == 'selected-I' else []),
                '-m', 'pytest']) + ['-q', 'tests/test_calc.py']
    if runner == 'selected-I':
        selected = ['tests/test_calc.py::test_value']
        for relative, (content, _) in pytest_selection.overlay(selected).items():
            (destination / relative).write_bytes(content)
        command = pytest_selection.command(command, selected)
    calls = count_python(monkeypatch)
    run = store.cached_test(command, destination, cache_root=root, fresh=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.args == command
    assert '1 passed' in run.stdout
    if runner == 'selected-I':
        assert '1 deselected' in run.stdout
    assert len(calls) == 1


@pytest.mark.parametrize('flag', ['', '-I', '-E'])
def test_wrong_import_is_refused_before_conftest_or_tests(local_repo, tmp_path, flag):
    root = local_repo
    foreign = tmp_path / 'foreign path'
    foreign.mkdir()
    (foreign / 'calc.py').write_text('def value():\n    return 3\n')
    conftest_ran, test_ran = tmp_path / 'conftest-ran', tmp_path / 'test-ran'
    (root / 'conftest.py').write_text(f'from pathlib import Path\nPath({str(conftest_ran)!r}).touch()\n')
    # Pytest shell-splits this ini setting on Windows too: raw backslashes
    # disappear, and unquoted spaces split a single foreign directory.
    destination = comparison(root, tmp_path, config=f'[pytest]\npythonpath = {shlex.quote(foreign.as_posix())}\n',
                             extra=f'\nfrom pathlib import Path\nPath({str(test_ran)!r}).touch()\n')
    command = [PYTHON, *([flag] if flag else []), '-m', 'pytest', '-q', 'tests/test_calc.py']
    run = store.cached_test(command, destination, cache_root=root, fresh=True)
    assert isinstance(run.returncode, python_imports.ImportFailure)
    assert 'calc' in run.returncode and 'outside the comparison copy' in run.returncode
    assert not conftest_ran.exists() and not test_ran.exists()


def test_cache_hit_rechecks_external_interpreter_imports(local_repo, tmp_path, monkeypatch):
    root = local_repo
    destination = comparison(root, tmp_path, config='[pytest]\n')
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py']
    first = store.cached_test(command, destination, cache_root=root)
    assert first.returncode == 0 and not first.cached, first
    second = store.cached_test(command, destination, cache_root=root)
    assert second.returncode == 0 and second.cached, second
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'calc.py').write_text('def value():\n    return 3\n')
    startup = tmp_path / 'startup'
    startup.mkdir()
    (startup / 'sitecustomize.py').write_text(
        'import importlib.util, sys\n'
        f'spec = importlib.util.spec_from_file_location("calc", {str(foreign / "calc.py")!r})\n'
        'module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n'
        'sys.modules["calc"] = module\n')
    monkeypatch.setenv('PYTHONPATH', str(startup))
    calls = count_python(monkeypatch)
    third = store.cached_test(command, destination, cache_root=root)
    assert isinstance(third.returncode, python_imports.ImportFailure)
    assert 'outside the comparison copy' in third.returncode
    assert not third.cached
    assert len(calls) == 1


def test_integrated_guard_keeps_declared_timeout_124(local_repo, tmp_path):
    destination = comparison(local_repo, tmp_path)
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py']
    run = store.cached_test(command, destination, timeout=0.01, cache_root=local_repo, fresh=True)
    assert run.returncode == 124
    assert 'test timed out' in run.stderr


@pytest.mark.parametrize('timeout', [None, 30])
def test_pytest_startup_without_report_is_refused(local_repo, tmp_path, monkeypatch, timeout):
    destination = comparison(local_repo, tmp_path)
    plugin = tmp_path / 'plugins'
    plugin.mkdir()
    (plugin / 'slow_plugin.py').write_text('import time\ntime.sleep(30)\n')
    monkeypatch.setenv('PYTHONPATH', str(plugin))
    monkeypatch.setenv('PYTEST_PLUGINS', 'slow_plugin')
    monkeypatch.setattr(python_imports, 'GUARD_STARTUP_TIMEOUT', 0.5)
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py']
    run = store.cached_test(command, destination, timeout=timeout, cache_root=local_repo, fresh=True)
    assert isinstance(run.returncode, python_imports.ImportFailure)
    assert 'import guard startup timed out after 0.5 seconds' in run.returncode


def test_script_arguments_do_not_turn_it_into_a_pytest_entrypoint(local_repo, tmp_path):
    (local_repo / 'runner.py').write_text(
        'import sys\nassert sys.argv[1:] == ["-m", "pytest"]\nprint("wrapper ok")\n')
    destination = comparison(local_repo, tmp_path)
    command = [PYTHON, 'runner.py', '-m', 'pytest']
    run = store.cached_test(command, destination, cache_root=local_repo, fresh=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout == 'wrapper ok\n'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX console shebang with interpreter flags')
@pytest.mark.parametrize('name', ['pytest', 'PYTEST'])
def test_isolated_environment_console_keeps_script_path_before_imports(local_repo, tmp_path, name):
    console = tmp_path / 'bin'
    console.mkdir()
    marker = tmp_path / 'console-json-imported'
    (console / 'json.py').write_text(
        'import importlib, pathlib, sys\n'
        f'pathlib.Path({str(marker)!r}).touch()\n'
        'sys.path.pop(0)\nsys.modules.pop("json")\n'
        'module = importlib.import_module("json")\n'
        'sys.modules["json"] = module\nglobals().update(module.__dict__)\n')
    executable = console / name
    executable.write_text(f'#!{PYTHON} -E\nimport json\nfrom pytest import console_main\n'
                          'raise SystemExit(console_main())\n')
    executable.chmod(0o755)
    destination = comparison(local_repo, tmp_path)
    command = [str(executable), '-q', 'tests/test_calc.py']
    run = store.cached_test(command, destination, cache_root=local_repo, fresh=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert marker.exists()


def test_integrated_isolated_selection_keeps_root_unimportable(local_repo, tmp_path):
    root = local_repo
    (root / 'src/sample').mkdir(parents=True)
    (root / 'src/sample/__init__.py').write_text('value = 3\n')
    (root / 'root_only.py').write_text('value = 9\n')
    (root / 'pytest.ini').write_text('[pytest]\npythonpath = src\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_sample.py').write_text(
        'import importlib.util, pathlib, sys\nfrom sample import value\n'
        'def test_value():\n    assert value == 3\n'
        '    assert importlib.util.find_spec("root_only") is None\n'
        '    assert sys.path.count(str(pathlib.Path.cwd() / "src")) == 1\n'
        'def test_other():\n    assert False\n')
    selected = ['tests/test_sample.py::test_value']
    for name, (content, _) in pytest_selection.overlay(selected).items():
        (root / name).write_bytes(content)
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'isolated selection')
    destination = tmp_path / 'comparison'
    gate._ref_test_snapshot(root, destination, 'HEAD')
    command = pytest_selection.command([PYTHON, '-I', '-m', 'pytest', '-q', 'tests/test_sample.py'], selected)
    run = store.cached_test(command, destination, cache_root=root, fresh=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert '1 passed, 1 deselected' in run.stdout


def test_import_guard_artifacts_stay_outside_checkout_and_are_removed(local_repo, tmp_path):
    report = tmp_path / 'guard-files.json'
    destination = comparison(local_repo, tmp_path, extra=(
        '\ndef test_guard_files(pytestconfig):\n'
        '    import json, pathlib, sys\n'
        '    files = [plugin.__file__ for name, plugin in pytestconfig.pluginmanager.list_name_plugin()\n'
        '             if name.startswith("__workorder_import_guard_")]\n'
        f'    pathlib.Path({str(report)!r}).write_text(json.dumps([files, sys.pycache_prefix]))\n'))
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py']
    run = store.cached_test(command, destination, cache_root=local_repo, fresh=True)
    assert run.returncode == 0, run.stdout + run.stderr
    import json
    files, prefix = json.loads(report.read_text())
    assert len(files) == 1
    assert not Path(files[0]).is_relative_to(destination)
    assert not Path(files[0]).is_relative_to(local_repo)
    assert not Path(files[0]).exists()
    assert not list(Path(prefix).rglob('__workorder_import_guard_*'))


@pytest.mark.skipif(os.name == 'nt', reason='POSIX console shebang and symlink')
def test_isolated_environment_console_preserves_symlink_argv(local_repo, tmp_path):
    console = tmp_path / 'console'
    console.mkdir()
    executable = console / 'entrypoint.py'
    link = tmp_path / 'pytest'
    executable.write_text(
        f'#!{PYTHON} -E\nimport sys\n'
        f'assert sys.argv[0] == {str(link)!r}, sys.argv\n'
        f'assert sys.path[0] == {str(console)!r}, sys.path\n'
        'from pytest import console_main\nraise SystemExit(console_main())\n')
    executable.chmod(0o755)
    link.symlink_to(executable)
    destination = comparison(local_repo, tmp_path)
    command = [str(link), '-q', 'tests/test_calc.py']
    run = store.cached_test(command, destination, cache_root=local_repo, fresh=True)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize('flags', [['-S'], ['-I', '-S']])
def test_missing_pytest_retains_dependency_failure_after_import_verification(local_repo, tmp_path, monkeypatch, flags):
    marker = tmp_path / 'conftest-ran'
    (local_repo / 'conftest.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
    destination = comparison(local_repo, tmp_path)
    command = [PYTHON, *flags, '-m', 'pytest', '-q', 'tests/test_calc.py']
    calls = count_python(monkeypatch)
    original_check = python_imports.check

    def standalone(root, source, words, env, *args, **kwargs):
        assert words == command
        assert 'workorder-imports-' not in env.get('PYTHONPATH', '')
        return original_check(root, source, words, env, *args, **kwargs)

    monkeypatch.setattr(python_imports, 'check', standalone)
    run = store.cached_test(command, destination, cache_root=local_repo, fresh=True)
    assert run.returncode == 1, run.stdout + run.stderr
    assert not isinstance(run.returncode, python_imports.ImportFailure)
    assert "No module named 'pytest'" in run.stderr or 'No module named pytest' in run.stderr
    assert not marker.exists()
    assert len(calls) == 2  # failed invocation plus a fresh standalone import probe
    assert not any('__workorder_import_guard_' in word for word in calls[1])


def test_missing_pytest_does_not_hide_foreign_project_imports(local_repo, tmp_path, monkeypatch):
    destination = comparison(local_repo, tmp_path)
    (destination / 'calc.py').unlink()
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'calc.py').write_text('def value():\n    return 3\n')
    monkeypatch.setenv('PYTHONPATH', str(foreign))
    run = store.cached_test([PYTHON, '-S', '-m', 'pytest', '-q'], destination,
                            cache_root=local_repo, fresh=True)
    assert isinstance(run.returncode, python_imports.ImportFailure)
    assert 'calc' in run.returncode and 'outside the comparison copy' in run.returncode


@pytest.mark.skipif(os.name == 'nt', reason='POSIX console shebang')
@pytest.mark.parametrize('spoof_probe_flag', [False, 'plugin', 'startup'])
def test_spoofed_missing_pytest_message_does_not_replace_guard_evidence(local_repo, tmp_path, monkeypatch,
                                                                     spoof_probe_flag):
    console = tmp_path / 'pytest'
    console.write_text(f'#!{PYTHON}\nimport sys\nprint("No module named pytest", file=sys.stderr)\n'
                       'raise SystemExit(1)\n')
    console.chmod(0o755)
    if spoof_probe_flag:
        plugins = tmp_path / 'plugins'
        plugins.mkdir()
        (plugins / ('sitecustomize.py' if spoof_probe_flag == 'startup' else 'report_spoofer.py')).write_text(
            'import os\nos.write(1, b"WORKORDER_PYTEST_AVAILABLE=false\\n")\n'
            'print("WORKORDER_PYTEST_AVAILABLE=false")\n')
        monkeypatch.setenv('PYTHONPATH', str(plugins))
        if spoof_probe_flag == 'plugin':
            monkeypatch.setenv('PYTEST_PLUGINS', 'report_spoofer')
    destination = comparison(local_repo, tmp_path)
    run = store.cached_test([str(console), '-q'], destination, cache_root=local_repo, fresh=True)
    assert isinstance(run.returncode, python_imports.ImportFailure)
    assert 'import guard did not complete' in run.returncode


@pytest.mark.parametrize('exit_code', [0, 1])
def test_early_exit_without_guard_report_is_refused(local_repo, tmp_path, monkeypatch, exit_code):
    destination = comparison(local_repo, tmp_path)
    plugins = tmp_path / 'plugins'
    plugins.mkdir()
    (plugins / 'early_exit.py').write_text(
        'import sys\nprint("No module named pytest", file=sys.stderr)\n'
        f'raise SystemExit({exit_code})\n')
    monkeypatch.setenv('PYTHONPATH', str(plugins))
    monkeypatch.setenv('PYTEST_PLUGINS', 'early_exit')
    calls = count_python(monkeypatch)
    run = store.cached_test([PYTHON, '-m', 'pytest', '-q'], destination,
                            cache_root=local_repo, fresh=True)
    assert isinstance(run.returncode, python_imports.ImportFailure)
    assert 'could not verify project imports' in run.returncode
    assert len(calls) == (1 if exit_code == 0 else 2)
