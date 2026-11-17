#!/bin/sh
# Reproduce docs/shortcuts.md with an installed Workorder and pytest.
# Usage: sh examples/shortcuts.sh [PYTHON [OUTPUT_PARENT]]
# Each run creates a new disposable directory; existing directories are never removed.
set -eu
if [ "$#" -gt 2 ]; then
    printf '%s\n' 'usage: shortcuts.sh [PYTHON [OUTPUT_PARENT]]' >&2
    exit 2
fi
PYTHON=${1:-python3}
OUTPUT_PARENT=${2:-${TMPDIR:-/tmp}}
exec "$PYTHON" - "$OUTPUT_PARENT" <<'PY'
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import sysconfig
import venv


python = sys.executable
tool_site = Path(sysconfig.get_path('purelib'))

parent = Path(sys.argv[1]).expanduser().resolve()
parent.mkdir(parents=True, exist_ok=True)
output = Path(tempfile.mkdtemp(prefix="workorder-shortcuts-", dir=parent))
print(f"Disposable repositories and complete logs: {output}", flush=True)

# Resolve the chosen interpreter before changing directories. Give declared tests
# the same interpreter even on systems that provide python3 but no python alias.
bin_dir = output / "bin"
bin_dir.mkdir()
python_alias = bin_dir / "python"
python_alias.write_text("#!/bin/sh\nexec " + shlex.quote(python) + ' "$@"\n', encoding="utf-8")
python_alias.chmod(0o755)
env = os.environ.copy()
for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
             "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
    env.pop(name, None)
env.update(PATH=str(bin_dir) + os.pathsep + env.get("PATH", ""),
           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="", PYTEST_PLUGINS="",
           PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1", TERM="dumb")
empty_hooks = output / "empty-hooks"
empty_hooks.mkdir()
wo = [python, "-m", "workorder"]
test = ["python", "-m", "pytest", "-q", "tests/test_calc.py"]
check = ["check", "--base", "main", "--run-tests", "--all-tests", "--require-task"]


def run(command, cwd, log, expected=0):
    """Save every byte of output and check the real subprocess exit, without a pipe."""
    with log.open("a", encoding="utf-8") as stream:
        stream.write("$ " + shlex.join(command) + "\n")
        stream.flush()
        result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, check=False)
        stream.write(f"[exit {result.returncode}]\n")
    if result.returncode != expected:
        raise RuntimeError(f"Expected exit {expected}, got {result.returncode}: "
                           f"{shlex.join(command)}\n{log.read_text(encoding='utf-8')}")
    return log.read_text(encoding="utf-8")


def git(repo, log, *args):
    return run(["git", "-c", "core.hooksPath=" + str(empty_hooks),
                "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], repo, log)


def commit(repo, log, title):
    git(repo, log, "add", "-A")
    git(repo, log, "commit", "-qm", title)


def write(repo, name, content):
    (repo / name).write_text(content, encoding="utf-8")


def replace(repo, name, old, new):
    path = repo / name
    content = path.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise RuntimeError(f"Expected exactly one occurrence of {old!r} in {path}")
    write(repo, name, content.replace(old, new))


def task(repo, log, title, scope, acceptance, tid, command=None):
    run(wo + ["new", "--title", title, "--owner", "dev", "--test", shlex.join(command or test),
              "--ships", "none", "--local"], repo, log)
    replace(repo, f"tasks/{tid}.md", "## Scope\n", f"## Scope\n\n{scope}\n")
    replace(repo, f"tasks/{tid}.md", "## Acceptance\n",
            f"## Acceptance\n\n- {acceptance}\n")


good_code = "def mul(a, b):\n    return a * b\n"
broken_code = "def mul(a, b):\n    if a < 0:\n        return a + b\n    return a * b\n"
negative_test = "\n\ndef test_mul_negative():\n    assert mul(-3, 4) == -12\n"
test_code = ("import pytest\nfrom calc import mul\n\n\ndef test_mul():\n"
             "    assert mul(3, 4) == 12\n" + negative_test)


def base(number, honest=False, split_tests=False, complete=True, code=None, kind=None):
    folder = output / f"{number:02d}"
    folder.mkdir()
    repo = folder / "repo"
    repo.mkdir()
    log = folder / "setup.txt"
    git(repo, log, "init", "-q")
    git(repo, log, "symbolic-ref", "HEAD", "refs/heads/main")
    git(repo, log, "config", "user.name", "Shortcut demo")
    git(repo, log, "config", "user.email", "demo@example.invalid")
    (repo / "tests").mkdir()
    write(repo, ".gitignore", "__pycache__/\n.pytest_cache/\n")
    write(repo, "calc.py", good_code if code is None else code)
    if split_tests:
        (repo / "tests/mul").mkdir()
    test_path = "tests/mul/test_calc.py" if split_tests else "tests/test_calc.py"
    command = ["python", "-m", "pytest", "-q", "tests/mul"] if split_tests else test
    write(repo, test_path, test_code)
    write(repo, "pytest.ini", "[pytest]\npythonpath = .\n")
    commit(repo, log, "Working multiplication and two tests")
    task(repo, log, "Multiply", "mul in calc.py.",
         "mul(a, b) returns a * b, also for negative numbers.", "TASK-1", command=command)
    if kind:
        replace(repo, "tasks/TASK-1.md", "\ncontract:", f"\nkind: {kind}\ncontract:")
    commit(repo, log, "Define multiplication task")
    if complete:
        # This setup verifies multiplication already present before the task.
        run(wo + ["done", "--task", "TASK-1", "--no-code-change"], repo, log)
        commit(repo, log, "Record TASK-1 completion")
    if honest:
        # Define the ask at the base: this example tests additions, not acceptance
        # of a definition first introduced in the same change as its DONE claim.
        task(repo, log, "Divide", "Add div in calc.py and its test; preserve mul.",
             "div(6, 3) returns 2 and multiplication still passes.", "TASK-2",
             command=["python", "-m", "pytest", "-q", "tests/div"] if split_tests else test)
        commit(repo, log, "Define division before implementation")
    git(repo, log, "checkout", "-qb", "change")
    return repo, log


def finish(repo, log, title, tid="TASK-1"):
    commit(repo, log, title)
    run(wo + ["renew", "--task", tid], repo, log)
    commit(repo, log, "Record current implementation and passing test")


def summary(text):
    matches = [line for line in text.splitlines()
               if re.search(r"\b\d+ (?:passed|failed|skipped|deselected).*\bin \d", line)]
    if not matches:
        raise RuntimeError("pytest did not print a result summary:\n" + text)
    return matches[-1].strip("= ")


results = []


def report(repo, title, command=test, plain_exit=0, check_exit=1, reasons=()):
    plain = run(command, repo, repo.parent / "pytest.txt", expected=plain_exit)
    # Review findings refuse by default. Warnings are optional; false claims still refuse.
    warn_exit = int(any('this change edits code' in reason or 'asserts nothing that can fail' in reason
                        for reason in reasons))
    checked = run(wo + check, repo, repo.parent / "check.txt", expected=check_exit)
    warned = run(wo + check + ["--review", "warn"], repo,
                 repo.parent / "check-warn.txt", expected=warn_exit)
    if reasons:
        if "Warning:" not in warned or "Review:" not in warned:
            raise RuntimeError("Shortcut did not produce an opt-in review warning:\n" + warned)
    elif any("Warning:" in result or "Review:" in result for result in (checked, warned)):
        raise RuntimeError("Honest change produced a warning:\n" + checked + warned)
    messages = []
    for reason in reasons:
        matching = [line for line in checked.splitlines() if reason in line]
        if not matching:
            raise RuntimeError(f"Missing expected diagnostic {reason!r}:\n{checked}")
        messages.append(matching[0])
    if not reasons:
        if "check: OK" not in checked:
            raise RuntimeError("Successful check did not print check: OK:\n" + checked)
        messages.append("check: OK")
    result = dict(title=title, command=shlex.join(command), pytest=summary(plain),
                  pytest_exit=plain_exit, workorder_exit=check_exit, warn_exit=warn_exit, messages=messages)
    results.append(result)
    print(f"\n{len(results)}. {title}", flush=True)
    print(f"$ {result['command']}\n{result['pytest']} (exit {plain_exit})", flush=True)
    print("$ workorder " + shlex.join(check), flush=True)
    print("\n".join(messages) + f"\n(default exit {check_exit}; --review warn exit {warn_exit})", flush=True)


try:
    version_log = output / "environment.txt"
    version_output = run(wo + ["--version"], output, version_log)
    version = next(line for line in version_output.splitlines() if line.startswith("workorder "))
    run([sys.executable, "-m", "pytest", "--version"], output, version_log)
    run(["git", "--version"], output, version_log)
    print(version, flush=True)

    repo, log = base(1, complete=False)
    write(repo, "calc.py", broken_code)
    replace(repo, "tests/test_calc.py", "assert mul(-3, 4) == -12", "assert True")
    finish(repo, log, "Replace failing assertion with assert True")
    report(repo, "Replace a failing assertion with assert True",
           reasons=("asserts nothing that can fail", "its test files lost or changed", "its tests from main fail"))

    repo, log = base(2, complete=False)
    write(repo, "calc.py", broken_code)
    replace(repo, "tests/test_calc.py", "def test_mul_negative():",
            '@pytest.mark.skip(reason="flaky")\ndef test_mul_negative():')
    finish(repo, log, "Mark failing test skipped")
    report(repo, "Mark the failing test skipped", reasons=("new skip or xfail behavior",))

    repo, log = base(3, complete=False)
    write(repo, "calc.py", broken_code)
    replace(repo, "tests/test_calc.py", "    assert mul(-3, 4) == -12",
            '    pytest.skip("later")\n    assert mul(-3, 4) == -12')
    finish(repo, log, "Skip from inside failing test")
    report(repo, "Call pytest.skip() inside the failing test", reasons=("new skip or xfail behavior",))

    repo, log = base(4, complete=False)
    write(repo, "calc.py", broken_code)
    write(repo, "conftest.py", 'import pytest\n\n\ndef pytest_collection_modifyitems(items):\n'
          '    for item in items:\n        if "negative" in item.name:\n'
          '            item.add_marker(pytest.mark.skip(reason="env"))\n')
    finish(repo, log, "Skip failing test through conftest")
    report(repo, "Add a conftest.py that skips the failing test",
           reasons=("pytest configuration changed",))

    repo, log = base(5, complete=False)
    write(repo, "calc.py", broken_code)
    replace(repo, "tests/test_calc.py", negative_test, "\n")
    finish(repo, log, "Delete failing test")
    report(repo, "Delete the failing test",
           reasons=("its test files lost or changed", "its tests from main fail"))

    repo, log = base(6, complete=False)
    write(repo, "calc.py", broken_code)
    replace(repo, "tests/test_calc.py", "assert mul(-3, 4) == -12", "assert mul(-3, 4) == 1")
    finish(repo, log, "Change expected value to match bug")
    report(repo, "Change the expected value to match the bug",
           reasons=("its test files lost or changed", "its tests from main fail"))

    repo, log = base(7, complete=False)
    write(repo, "calc.py", broken_code)
    narrowed = test + ["-k", "not negative"]
    path = repo / "tasks/TASK-1.md"
    text = path.read_text(encoding="utf-8")
    text, replaced = re.subn(r"(?m)^test: .*", lambda _: "test: " + json.dumps(shlex.join(narrowed)), text)
    if replaced != 1:
        raise RuntimeError("Expected one task test field")
    path.write_text(text, encoding="utf-8")
    finish(repo, log, "Narrow task command to exclude failing test")
    report(repo, "Narrow the task's test command with -k", command=narrowed,
           reasons=("pytest selection changed", "test changed:"))

    repo, log = base(8)
    write(repo, "calc.py", good_code + "\n\ndef div(a, b):\n    return a * b\n")
    replace(repo, "tests/test_calc.py", "assert mul(3, 4) == 12", "assert True")
    commit(repo, log, "Add broken division and weaken a test without recording task work")
    report(repo, "Add broken code without touching a task",
           reasons=("this change edits code",))

    repo, log = base(9, honest=True)
    write(repo, "calc.py", good_code + "\n\ndef div(a, b):\n    return a / b\n")
    write(repo, "tests/test_calc.py", test_code +
          "\n\ndef test_div():\n    from calc import div\n    assert div(6, 3) == 2\n")
    commit(repo, log, "Add division and its test")
    run(wo + ["done", "--task", "TASK-2"], repo, log)
    commit(repo, log, "Record TASK-2 completion")
    report(repo, "Honest change: add a feature and a new test", check_exit=0)

    repo, log = base(10)
    write(repo, "calc.py", broken_code)
    replace(repo, "tests/test_calc.py", "assert mul(-3, 4) == -12", "assert mul(-3, 4) == 1")
    write(repo, "tests/test_unrelated.py", "def test_unrelated():\n    assert 2 + 2 == 4\n")
    task(repo, log, "Unrelated work", "An unrelated check.", "Two plus two is four.",
         "TASK-2", command=["any"])
    commit(repo, log, "Weaken multiplication and add unrelated task")
    run(wo + ["receipt", "test", "--task", "TASK-2", "--", "python", "-c", "pass"],
        repo, log, expected=1)
    commit(repo, log, "Record a non-counting test receipt on the unrelated task")
    report(repo, "Weaken a test and add an unrelated non-counting receipt",
           command=["python", "-m", "pytest", "-q", "tests"],
           reasons=("its test files lost or changed", "its tests from main fail", "this change edits code"))

    repo, log = base(11, honest=True, split_tests=True)
    retained = {str(path.relative_to(repo)): path.read_bytes()
                for path in [repo / "tasks/TASK-1.md",
                             *(repo / ".workorder/receipts/TASK-1").glob("*.json")]}
    write(repo, "calc.py", broken_code + "\n\ndef div(a, b):\n    return a / b\n")
    replace(repo, "tests/mul/test_calc.py", "assert mul(-3, 4) == -12", "assert mul(-3, 4) == 1")
    (repo / "tests/div").mkdir()
    write(repo, "tests/div/test_div.py", "from calc import div\n\n\ndef test_div():\n"
          "    assert div(6, 3) == 2\n")
    commit(repo, log, "Implement division while weakening multiplication")
    run(wo + ["done", "--task", "TASK-2"], repo, log, expected=1)
    # Pending review writes the claim and evidence without editing status by hand.
    run(wo + ["done", "--task", "TASK-2", "--needs-review"], repo, log)
    commit(repo, log, "Record division completion pending review")
    if any((repo / name).read_bytes() != content for name, content in retained.items()):
        raise RuntimeError("The unrelated multiplication task or receipts changed")
    report(repo, "Work on division while weakening multiplication tests",
           command=["python", "-m", "pytest", "-q", "tests"],
           reasons=("TASK-1: its test files lost or changed", "TASK-1: its tests from main fail"))

    repo, log = base(12, complete=False, kind="refactor")
    git(repo, log, "checkout", "-q", "main")
    write(repo, "test_probe.py", "def test_probe():\n    assert True\n")
    (repo / "src").mkdir()
    write(repo, "src/__init__.py", "")
    write(repo, "src/app.py", "def shape(text):\n    return text.strip()\n")
    write(repo, "tests/test_app.py", "from src.app import shape\n\n\ndef test_shape():\n"
          "    assert shape(' ab ') == 'ab'\n")
    commit(repo, log, "Add src/app.py and a root-level test file")
    git(repo, log, "checkout", "-q", "change")
    git(repo, log, "merge", "-q", "main")
    replace(repo, "src/app.py", "return text.strip()", "return ''.join(text.split())")
    finish(repo, log, "Refactor shape without changing tests")
    run(wo + ["done", "--task", "TASK-1", "--refactor"], repo, log)
    commit(repo, log, "Record declared refactor")
    report(repo, "Refactor production code; tests and the root probe stay unchanged",
           command=["python", "-m", "pytest", "-q", "tests"], check_exit=0)

    repo, log = base(13, complete=False, code=broken_code, kind="verification")
    replace(repo, "tests/test_calc.py", "def test_mul_negative():\n",
            "def test_mul_negative():\n    return\n")
    commit(repo, log, "Return before the failing assertion")
    run(wo + ["renew", "--task", "TASK-1"], repo, log)
    replace(repo, "tasks/TASK-1.md", 'status: "IN_PROGRESS"', 'status: "DONE"')
    commit(repo, log, "Claim TASK-1 done after adding return")
    report(repo, "Return at the top of a test that failed on main",
           reasons=("so any change needs review", "its tests from main fail"))

    repo, log = base(14, complete=False, code=broken_code)
    write(repo, "tests/test_calc.py", test_code + "\n\ndef test_mul_negative():\n    assert True\n")
    commit(repo, log, "Shadow the failing test with a second function")
    run(wo + ["renew", "--task", "TASK-1"], repo, log)
    replace(repo, "tasks/TASK-1.md", 'status: "IN_PROGRESS"', 'status: "DONE"')
    commit(repo, log, "Claim TASK-1 done after shadowing the test")
    report(repo, "Add a second test_mul_negative that shadows the failing one",
           reasons=("asserts nothing that can fail", "so any change needs review", "its tests from main fail"))

    repo, log = base(15, honest=True)
    git(repo, log, "checkout", "-q", "main")
    (repo / "src/calc").mkdir(parents=True)
    (repo / "calc.py").rename(repo / "src/calc/__init__.py")
    write(repo, "pyproject.toml", '[build-system]\nrequires = []\nbuild-backend = "build_backend"\nbackend-path = ["."]\n')
    write(repo, "build_backend.py", "import pathlib, zipfile\n\ndef build_editable(wheel_directory, config_settings=None, metadata_directory=None):\n    filename = 'wo_sample-1.0-py3-none-any.whl'\n    with zipfile.ZipFile(pathlib.Path(wheel_directory) / filename, 'w') as z:\n        z.writestr('wo_sample.pth', str(pathlib.Path('src').resolve()) + '\\n')\n        z.writestr('wo_sample-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\\nName: wo-sample\\nVersion: 1.0\\n')\n        z.writestr('wo_sample-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\\nGenerator: local-test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')\n        z.writestr('wo_sample-1.0.dist-info/RECORD', '')\n    return filename\n")
    commit(repo, log, "Move calc into an editable src package")
    git(repo, log, "checkout", "-q", "change")
    git(repo, log, "merge", "-q", "main")
    environment = repo.parent / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = subprocess.check_output([str(executable), "-c", 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    (Path(site) / "test_tools.pth").write_text(str(tool_site) + "\n")
    run([str(executable), "-m", "pip", "install", "--no-index", "--no-build-isolation", "--no-deps", "-e", "."], repo, log)
    command = [str(executable), "-m", "pytest", "-q", "tests/test_calc.py"]
    # Set the interpreter in the task before implementation, on main.
    git(repo, log, "checkout", "-q", "main")
    for tid in ("TASK-1", "TASK-2"):
        path = repo / f"tasks/{tid}.md"
        path.write_text(re.sub(r"(?m)^test: .*", lambda _: "test: " + json.dumps(shlex.join(command)), path.read_text()))
    commit(repo, log, "Use the editable environment for tests")
    run(wo + ["renew", "--task", "TASK-1"], repo, log)
    commit(repo, log, "Renew the existing task in its editable environment")
    git(repo, log, "checkout", "-q", "change")
    git(repo, log, "merge", "-q", "main")
    write(repo, "src/calc/__init__.py", good_code + "\n\ndef div(a, b):\n    return a / b\n")
    write(repo, "tests/test_calc.py", test_code + "\n\ndef test_div():\n    from calc import div\n    assert div(6, 3) == 2\n")
    commit(repo, log, "Implement division in the installed src package")
    run(wo + ["done", "--task", "TASK-2"], repo, log)
    commit(repo, log, "Record editable src completion")
    report(repo, "Honest src-layout feature with an editable install", command=command, check_exit=0)

    repo, log = base(16, complete=False, code="")
    definition = repo / "tasks/TASK-1.md"
    definition.write_text(re.sub(r'(?m)^contract: .*',
        lambda match: match[0] + '\ncode_paths: ["calc.py"]', definition.read_text()))
    write(repo, "calc.py", good_code)
    commit(repo, log, "Implement multiplication on its branch")
    run(wo + ["done", "--task", "TASK-1"], repo, log)
    commit(repo, log, "Record multiplication completion")
    git(repo, log, "checkout", "-q", "main")
    git(repo, log, "merge", "--ff-only", "change")
    division = ["python", "-m", "pytest", "-q", "tests/test_div.py"]
    task(repo, log, "Divide in the same file", "Add div in calc.py; preserve mul.",
         "div(6, 3) returns 2.", "TASK-2", command=division)
    definition = repo / "tasks/TASK-2.md"
    definition.write_text(re.sub(r'(?m)^contract: .*',
        lambda match: match[0] + '\ncode_paths: ["calc.py"]', definition.read_text()))
    commit(repo, log, "Land the second task before its implementation")
    git(repo, log, "checkout", "-qb", "second-task")
    write(repo, "calc.py", good_code + "\n\ndef div(a, b):\n    return a / b\n")
    write(repo, "tests/test_div.py", "from calc import div\n\ndef test_div():\n    assert div(6, 3) == 2\n")
    commit(repo, log, "Implement division alongside finished multiplication")
    done = run(wo + ["done", "--task", "TASK-2"], repo, repo.parent / "done.txt")
    if "Other:" in done or "TASK-2 DONE:" not in done:
        raise RuntimeError("The second honest task did not finish cleanly:\n" + done)
    commit(repo, log, "Record the second task completion")
    report(repo, "Second honest task in a finished task's file", command=division, check_exit=0)
    checked = (repo.parent / "check.txt").read_text()
    if "workorder-allow:TASK-1" in checked:
        raise RuntimeError("The unchanged finished task was blamed for division:\n" + checked)

    (output / "results.json").write_text(json.dumps(dict(version=version, cases=results), indent=2)
                                       + "\n", encoding="utf-8")
    print(f"\nVerified results: {output / 'results.json'}", flush=True)
    print("PASS: 16 cases verified; 12 shortcuts refused by default; 4 still refused with --review warn; honest changes pass with no warning.", flush=True)
except (OSError, RuntimeError, StopIteration) as error:
    print(f"FAIL: {error}\nComplete logs remain in {output}", file=sys.stderr)
    sys.exit(1)
PY
