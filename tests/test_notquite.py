"""Exercise the public commands against real, disposable Git remotes."""

from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import sysconfig
import threading

import pytest

from notquite import gate, store

PACKAGE = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def git(root, *args):
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=root,
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def cli(root, *args):
    env = dict(os.environ, PYTHONPATH=str(PACKAGE), GIT_TERMINAL_PROMPT="0")
    return subprocess.run([PYTHON, "-m", "notquite", *args], cwd=root, env=env,
                          capture_output=True, encoding="utf-8", timeout=30)


def ok(root, *args):
    result = cli(root, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def task(root, tid="TASK-1", status="IN_PROGRESS", ships="repo", waits=(), contract="none"):
    fields = dict(id=tid, title="Add a list filter", owner="alex", status=status,
                  ships=ships, waits_on=list(waits), contract=contract)
    path = root / "tasks" / f"{tid}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in fields.items())
                    + f"---\n\n# {tid}\n", encoding="utf-8")
    return path


@pytest.fixture
def clones(tmp_path):
    remote, seed = tmp_path / "remote.git", tmp_path / "seed"
    git(tmp_path, "init", "--bare", "-q", "--initial-branch=main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(seed))
    git(seed, "config", "user.name", "alex")
    git(seed, "config", "user.email", "alex@example.com")
    (seed / "app.py").write_text("items = []\n", encoding="utf-8")
    task(seed)
    git(seed, "add", "app.py", "tasks/TASK-1.md")
    git(seed, "commit", "-qm", "Start the sample list app")
    git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")
    result = []
    for name in ("one", "two"):
        path = tmp_path / name
        git(tmp_path, "clone", "-q", str(remote), str(path))
        git(path, "config", "user.name", name)
        git(path, "config", "user.email", f"{name}@example.com")
        result.append(path)
    return result


def implement(root):
    ok(root, "receipt", "implemented", "--task", "TASK-1")
    return store.head(root)


def passed(root):
    return ok(root, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "pass")


def merge(root):
    git(root, "push", "-q", "origin", "HEAD:refs/heads/main")
    ok(root, "receipt", "merged", "--task", "TASK-1")


def readback(root, kind, stamp=None, target="demo"):
    return cli(root, "receipt", kind, "--task", "TASK-1", "--target", target,
               "--", PYTHON, "-c", f"print({(stamp or store.head(root))!r})")


def test_two_clones_reserve_different_ids_without_pushing_records(clones):
    one, two = clones
    assert "TASK-2 opened" in ok(one, "new", "--title", "Store items", "--owner", "alex")
    assert "TASK-3 opened" in ok(two, "new", "--title", "Remove items", "--owner", "bo")
    assert not (two / "tasks/TASK-2.md").exists()


def test_simultaneous_id_allocation_retries_the_losing_push(clones, monkeypatch):
    barrier = threading.Barrier(2)
    original = store.reserve
    first = set()

    def race(root, ref, value):
        if root not in first:
            first.add(root)
            barrier.wait(timeout=10)
        return original(root, ref, value)

    monkeypatch.setattr(store, "reserve", race)
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(lambda root: store.new(root, "Pin an item", "alex"), clones))
    assert set(ids) == {"TASK-2", "TASK-3"}


def test_new_refuses_an_unreachable_remote(clones):
    one, _ = clones
    git(one, "remote", "set-url", "origin", str(one / "missing.git"))
    result = cli(one, "new", "--title", "Pin an item", "--owner", "alex")
    assert result.returncode == 1
    assert not (one / "tasks/TASK-2.md").exists()


def test_second_claim_refused_and_only_holder_releases(clones):
    one, two = clones
    assert "held by one since" in ok(one, "claim", "--task", "TASK-1")
    denied = cli(two, "claim", "--task", "TASK-1")
    assert denied.returncode == 1 and "held by one since" in denied.stderr
    assert cli(two, "release", "--task", "TASK-1").returncode == 1
    assert "TASK-1 held by one since" in ok(two, "claims")
    ok(one, "claim", "--task", "TASK-1")  # same clone can retry safely
    ok(one, "release", "--task", "TASK-1")
    assert ok(two, "claims").strip() == "no active claims"
    ok(two, "claim", "--task", "TASK-1")
    assert "held by two since" in ok(one, "claims")
    assert len([r for r in store.remote_refs(one) if "/claims/" in r]) == 2


def test_simultaneous_claim_has_one_winner(clones, monkeypatch):
    barrier = threading.Barrier(2)
    original = store.reserve

    def race(root, ref, value):
        barrier.wait(timeout=10)
        return original(root, ref, value)

    monkeypatch.setattr(store, "reserve", race)

    def take(root):
        try:
            store.claim(root, "TASK-1")
            return True
        except store.Refused:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(take, clones)) == [False, True]


def test_test_receipt_runs_command_and_keeps_the_real_failure(clones):
    one, _ = clones
    commit = implement(one)
    result = cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "raise SystemExit(7)")
    assert result.returncode == 1 and "exit 7 - recorded" in result.stdout
    receipt = store.entries(one, "TASK-1")[-1]
    assert receipt["exit_code"] == 7 and not receipt["valid"]
    assert receipt["commit"] == commit and receipt["command"][-1] == "raise SystemExit(7)"
    assert receipt["who"] == "one" and receipt["at"]
    task(one, status="DONE")
    assert cli(one, "check").returncode == 1
    assert "computed IN_PROGRESS" in ok(one, "show", "--task", "TASK-1")


def test_done_needs_a_test_on_the_implemented_commit(clones):
    one, _ = clones
    old = implement(one)
    passed(one)
    task(one, status="DONE")
    assert ok(one, "check").strip() == "check: OK"
    (one / "app.py").write_text("items = ['new']\n", encoding="utf-8")
    git(one, "add", "app.py")
    git(one, "commit", "-qm", "Add a starter item", "--", "app.py")
    assert implement(one) != old
    result = cli(one, "check")
    assert result.returncode == 1 and "passing test on" in result.stdout
    passed(one)
    ok(one, "check")


def test_done_with_only_implementation_is_refused(clones):
    one, _ = clones
    commit = implement(one)
    task(one, status="DONE")
    result = cli(one, "check")
    assert result.returncode == 1 and commit[:7] in result.stdout


@pytest.mark.parametrize("mutation", [
    "from pathlib import Path; Path('app.py').write_text('changed')",
    "from pathlib import Path; Path('extra.py').write_text('changed')",
    "import subprocess; subprocess.run(['git','commit','--allow-empty','-qm','another commit'], check=True)",
])
def test_code_or_commit_changed_by_test_cannot_pass(clones, mutation):
    one, _ = clones
    implement(one)
    result = cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", mutation)
    assert result.returncode == 1
    receipt = store.entries(one, "TASK-1")[-1]
    assert receipt["exit_code"] == 0 and not receipt["valid"]
    task(one, status="DONE")
    assert cli(one, "check").returncode == 1


def test_uncommitted_code_cannot_be_recorded(clones):
    one, _ = clones
    (one / "app.py").write_text("changed\n", encoding="utf-8")
    assert cli(one, "receipt", "implemented", "--task", "TASK-1").returncode == 1
    assert cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "pass").returncode == 1
    assert not store.entries(one, "TASK-1")


def test_missing_test_executable_is_recorded_as_failure(clones):
    one, _ = clones
    implement(one)
    assert cli(one, "receipt", "test", "--task", "TASK-1", "--", "missing-notquite-command").returncode == 1
    assert store.entries(one, "TASK-1")[-1]["exit_code"] == 127


@pytest.mark.parametrize("ships,computed", [("none", "CLOSED"), ("repo", "DONE"), ("live", "DONE")])
def test_delivery_rules_are_computed(clones, ships, computed):
    one, _ = clones
    task(one, ships=ships)
    implement(one)
    passed(one)
    assert f"computed {computed}" in ok(one, "show", "--task", "TASK-1")
    task(one, ships=ships, status="CLOSED")
    assert cli(one, "check").returncode == (0 if ships == "none" else 1)


def test_repo_closes_only_after_merge_of_tested_code(clones):
    one, _ = clones
    implement(one)
    passed(one)
    task(one, status="CLOSED")
    assert cli(one, "check").returncode == 1
    merge(one)
    assert "computed CLOSED" in ok(one, "show", "--task", "TASK-1")
    ok(one, "check")


def test_merge_of_different_code_is_not_delivery(clones):
    one, two = clones
    implement(one)
    passed(one)
    (two / "app.py").write_text("items = ['other']\n", encoding="utf-8")
    git(two, "add", "app.py")
    git(two, "commit", "-qm", "Change the initial list")
    git(two, "push", "-q", "origin", "HEAD:refs/heads/main")
    landed = cli(one, "receipt", "merged", "--task", "TASK-1")
    assert landed.returncode == 1
    assert "recorded, but does not count: refs/heads/main on origin does not hold exactly" in landed.stdout
    task(one, status="CLOSED")
    assert "merge of the tested code" in cli(one, "check").stdout


def test_live_needs_successful_readback_after_deployment(clones):
    one, _ = clones
    task(one, ships="live")
    implement(one)
    passed(one)
    merge(one)
    early = readback(one, "live")  # too early; cannot discharge later delivery
    assert early.returncode == 1 and "no deployment read-back is recorded after the merge" in early.stdout
    assert readback(one, "deployed").returncode == 0
    task(one, status="CLOSED", ships="live")
    denied = cli(one, "check")
    assert denied.returncode == 1 and "live read-back" in denied.stdout
    assert readback(one, "live", stamp="no version here").returncode == 1
    assert cli(one, "check").returncode == 1
    other = readback(one, "live", target="other")
    assert other.returncode == 1 and "does not match the latest deployment read-back" in other.stdout
    assert cli(one, "check").returncode == 1
    assert readback(one, "live").returncode == 0
    ok(one, "check")
    assert "computed CLOSED" in ok(one, "show", "--task", "TASK-1")


def test_failed_or_wrong_stamp_deploy_cannot_close(clones):
    one, _ = clones
    task(one, ships="live")
    implement(one)
    passed(one)
    merge(one)
    result = readback(one, "deployed", stamp=store.head(one)[:7])
    assert result.returncode == 1
    late = readback(one, "live")
    assert late.returncode == 1 and "the latest deployment read-back did not pass" in late.stdout
    task(one, ships="live", status="CLOSED")
    assert "deployment read-back" in cli(one, "check").stdout


def contract(root, provenance="MOCK", requires="REAL", ref="sample-output", critical=True):
    path = root / ".notquite/contract.json"
    path.parent.mkdir(exist_ok=True)
    evidence = [{"provenance": provenance, "ref": ref}]
    if provenance == "REAL":
        artifact = root / ref
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("An observed result.\n", encoding="utf-8")
        evidence[0]["sha256"] = store.digest(artifact.read_bytes())
    value = {"task": "TASK-1", "criteria": [{"id": "C1", "ask": "Show stored items", "critical": critical,
                                          "requires": requires, "evidence": evidence}]}
    path.write_text(json.dumps(value), encoding="utf-8")
    task(root, status="DONE", contract=".notquite/contract.json")
    return path


@pytest.mark.parametrize("provenance", ["MOCK", "SYNTHETIC", "UNKNOWN", "TEST"])
def test_real_requirement_rejects_other_provenance(clones, provenance):
    one, _ = clones
    implement(one)
    passed(one)
    contract(one, provenance)
    result = cli(one, "check")
    assert result.returncode == 1 and "contract C1: REAL evidence" in result.stdout


def test_critical_test_evidence_must_reference_a_passing_receipt(clones):
    one, _ = clones
    implement(one)
    passed(one)
    entry = store.entries(one, "TASK-1")[-1]
    path = contract(one, "TEST", "REAL_OR_TEST", "receipt:" + entry["hash"])
    ok(one, "check")
    value = json.loads(path.read_text())
    value["criteria"][0]["evidence"][0]["ref"] = "receipt:invented"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert cli(one, "check").returncode == 1


def test_real_artifact_is_checked_against_its_hash(clones):
    one, _ = clones
    contract(one, "REAL", ref=".notquite/evidence/observed.txt")
    implement(one)
    passed(one)
    ok(one, "check")
    (one / ".notquite/evidence/observed.txt").write_text("changed", encoding="utf-8")
    assert cli(one, "check").returncode == 1


@pytest.mark.parametrize("value", [{}, {"task": "TASK-1", "criteria": [None]},
                                  {"task": "TASK-1", "criteria": []}])
def test_malformed_contract_is_refused(clones, value):
    one, _ = clones
    path = one / ".notquite/contract.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    task(one, contract=".notquite/contract.json")
    assert cli(one, "check").returncode == 1


def test_ready_reports_free_held_blocked_and_delivery(clones):
    one, _ = clones
    task(one, "TASK-2", waits=["task:TASK-1", "person:bo"], status="BLOCKED")
    task(one, "TASK-3")
    assert "TASK-1 free" in ok(one, "ready")
    assert "TASK-2 blocked: task:TASK-1, person:bo" in ok(one, "ready")
    ok(one, "claim", "--task", "TASK-1")
    assert "TASK-1 held by one since" in ok(one, "ready")
    ok(one, "release", "--task", "TASK-1")
    implement(one)
    passed(one)
    output = ok(one, "ready")
    assert "TASK-1 waiting for delivery" in output
    assert "TASK-2 blocked: person:bo" in output
    assert "TASK-3 free" in output


def test_dependency_cycle_and_missing_task_are_refused(clones):
    one, _ = clones
    task(one, waits=["task:TASK-2"])
    assert "missing dependency TASK-2" in cli(one, "check").stdout
    task(one, "TASK-2", waits=["task:TASK-1"])
    assert "dependency cycle" in cli(one, "check").stderr


def test_finished_record_cannot_owe_a_person(clones):
    one, _ = clones
    implement(one)
    passed(one)
    task(one, status="DONE", waits=["person:bo"])
    assert cli(one, "check").returncode == 1


def test_committed_receipts_cannot_be_rewritten_or_deleted(clones):
    one, _ = clones
    implement(one)
    path = store.ledger_path("TASK-1")
    git(one, "add", path)
    git(one, "commit", "-qm", "Record implementation", "--", path)
    (one / path).write_text("", encoding="utf-8")
    assert "append-only" in cli(one, "check").stderr
    (one / path).unlink()
    assert "append-only" in cli(one, "check").stderr


def test_hand_edit_breaks_receipt_chain(clones):
    one, _ = clones
    implement(one)
    path = one / store.ledger_path("TASK-1")
    value = json.loads(path.read_text())
    value["commit"] = "0" * 40
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    assert "broken receipt chain" in cli(one, "check").stderr


def test_staged_check_uses_index_and_hook_refuses_false_done(clones):
    one, _ = clones
    task(one, status="DONE")
    git(one, "add", "tasks/TASK-1.md")
    task(one)  # working file is valid, staged file is not
    ok(one, "check")
    assert cli(one, "check", "--staged").returncode == 1
    git(one, "config", "core.hooksPath", str(PACKAGE / "hooks"))
    env = dict(os.environ, PYTHONPATH=str(PACKAGE))
    result = subprocess.run(["git", "commit", "-qm", "Try a false completion"], cwd=one,
                            env=env, capture_output=True, encoding="utf-8")
    assert result.returncode != 0 and "says DONE" in result.stdout + result.stderr


@pytest.mark.parametrize("identifier", ["../escape", "TASK-0", "a-1", "TASK-1/other"])
def test_task_ids_cannot_escape_paths(clones, identifier):
    assert cli(clones[0], "show", "--task", identifier).returncode == 1


def test_contract_cannot_read_outside_repository(clones):
    one, _ = clones
    task(one, contract="../outside.json")
    assert "inside this repository" in cli(one, "check").stderr


def test_invented_examples_are_readable_and_false_done_is_explicit(clones):
    one, _ = clones
    for source in (PACKAGE / "examples/tasks").glob("*.md"):
        (one / "tasks" / source.name).write_bytes(source.read_bytes())
    records = gate.records(one)
    assert {records[f"TASK-{n}"]["status"] for n in (1, 2, 3)} == {"IN_PROGRESS", "BLOCKED", "DONE"}
    assert "TASK-3 says DONE" in cli(one, "check").stdout
    assert records["TASK-4"]["depends_on"] == ["TASK-1"] and records["TASK-4"]["priority"] == "P2"


def test_readme_extended_example_is_a_valid_task(clones):
    one, _ = clones
    readme = (PACKAGE / "README.md").read_text(encoding="utf-8")
    block = next(b for b in readme.split("```markdown\n")[1:] if "business_owner" in b).split("```")[0]
    (one / "tasks/TASK-4.md").write_text(block + "\n# TASK-4\n", encoding="utf-8")
    record = store.record(one, "TASK-4")
    assert list(record) == list(store.EXTENDED_ORDER)
    assert "TASK-4 blocked: task:TASK-1" in ok(one, "ready")


def test_workflow_installs_kit_runs_tests_and_checker():
    steps = [line.strip() for line in
             (PACKAGE / ".github/workflows/check.yml").read_text().splitlines()
             if line.strip().startswith("- run:")]
    assert steps == ["- run: python -m pip install -e '.[test]'",
                     "- run: python -m pytest", "- run: notquite check"]


def test_test_before_implementation_does_not_complete_task(clones):
    one, _ = clones
    early = cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "pass")
    assert early.returncode == 1 and "no implementation is recorded yet" in early.stdout
    implement(one)
    task(one, status="DONE")
    assert "passing test on" in cli(one, "check").stdout


def test_recomputed_hash_cannot_hide_wrong_code_digest(clones):
    one, _ = clones
    implement(one)
    path = one / store.ledger_path("TASK-1")
    value = json.loads(path.read_text())
    value.pop("hash")
    value["code"] = "0" * 64
    value["hash"] = store.digest(store.packed(value).encode())
    path.write_text(store.packed(value) + "\n", encoding="utf-8")
    assert "code does not match" in cli(one, "check").stderr


def test_contract_rejects_failed_test_even_when_receipt_exists(clones):
    one, _ = clones
    implement(one)
    cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "raise SystemExit(1)")
    failed = store.entries(one, "TASK-1")[-1]
    passed(one)
    contract(one, "TEST", "REAL_OR_TEST", "receipt:" + failed["hash"])
    assert "contract C1" in cli(one, "check").stdout


def test_critical_criterion_with_no_evidence_is_refused(clones):
    one, _ = clones
    implement(one)
    passed(one)
    path = contract(one)
    value = json.loads(path.read_text())
    value["criteria"][0]["evidence"] = []
    path.write_text(json.dumps(value), encoding="utf-8")
    assert "contract C1" in cli(one, "check").stdout


def test_staged_contract_reads_the_staged_binary_artifact(clones):
    one, _ = clones
    path = contract(one, "REAL", ref=".notquite/evidence/observed.bin")
    artifact = one / ".notquite/evidence/observed.bin"
    artifact.write_bytes(b"\x89\x00\xff")
    value = json.loads(path.read_text())
    value["criteria"][0]["evidence"][0]["sha256"] = store.digest(artifact.read_bytes())
    path.write_text(json.dumps(value), encoding="utf-8")
    implement(one)
    passed(one)
    git(one, "add", "tasks/TASK-1.md", ".notquite")
    artifact.write_bytes(b"changed after staging")
    assert cli(one, "check").returncode == 1
    ok(one, "check", "--staged")


def test_unstaged_passing_receipt_cannot_validate_staged_done(clones):
    one, _ = clones
    implement(one)
    task(one, status="DONE")
    git(one, "add", "tasks/TASK-1.md", ".notquite")
    passed(one)
    ok(one, "check")
    assert "passing test on" in cli(one, "check", "--staged").stdout


def test_check_from_a_subdirectory_cannot_report_false_success(clones):
    one, _ = clones
    task(one, status="DONE")
    directory = one / "nested"
    directory.mkdir()
    result = cli(directory, "check")
    assert result.returncode == 1 and "repository root" in result.stderr


# Regression tests for the NotQuite name, packaging and the per-task explanation.

def test_package_imports_under_its_name_and_versions_agree():
    import notquite
    tomllib = pytest.importorskip("tomllib")  # new in Python 3.11
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "notquite"
    assert project["scripts"] == {"notquite": "notquite.cli:main"}
    assert project["dependencies"] == []
    assert notquite.__version__ == project["version"]


def test_module_and_installed_command_report_the_version(tmp_path):
    import notquite
    result = subprocess.run([PYTHON, "-m", "notquite", "--version"], capture_output=True,
                            encoding="utf-8", cwd=tmp_path)
    assert result.stdout.strip() == f"notquite {notquite.__version__}"
    script = Path(sysconfig.get_path("scripts")) / ("notquite.exe" if os.name == "nt" else "notquite")
    if not script.exists():
        pytest.skip("notquite is not installed next to this Python")
    result = subprocess.run([str(script), "--version"], capture_output=True, encoding="utf-8")
    assert result.returncode == 0 and result.stdout.strip() == f"notquite {notquite.__version__}"


def test_no_old_project_names_remain():
    old = ("task" + "kit", "agent-task-" + "kit", "Agent Task " + "Kit")
    for path in PACKAGE.rglob("*"):
        if path.is_file() and not {".git", ".venv", "__pycache__"} & set(path.parts) \
                and ".egg-info" not in str(path):
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not any(name in text for name in old), path


def test_explain_refuses_false_done_and_says_what_to_do(clones):
    one, _ = clones
    task(one, status="DONE")
    result = cli(one, "check", "TASK-1")
    assert result.returncode == 1
    assert result.stdout.startswith("NOT QUITE  TASK-1")
    assert "Claimed:  DONE" in result.stdout and "Missing:  implementation" in result.stdout
    assert "Next:     commit the code, then: notquite receipt implemented --task TASK-1" in result.stdout
    assert "Result: NOT DONE" in result.stdout


def test_explain_rejects_a_test_from_an_older_commit(clones):
    one, _ = clones
    implement(one)
    passed(one)
    old = store.head(one)[:7]
    (one / "app.py").write_text("items = ['new']\n", encoding="utf-8")
    git(one, "commit", "-qam", "Change the list")
    new = implement(one)[:7]
    task(one, status="DONE")
    result = cli(one, "check", "tasks/TASK-1.md")
    assert result.returncode == 1
    assert f"the last test ran on {old}" in result.stdout
    assert f"Missing:  a passing test on {new}" in result.stdout
    assert "Note:" not in result.stdout  # the README and demo output stay exactly as shown


def test_explain_accepts_valid_done_and_shows_the_passing_test(clones):
    one, _ = clones
    commit = implement(one)[:7]
    passed(one)
    task(one, status="DONE")
    result = cli(one, "check", "TASK-1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("DONE  TASK-1")
    assert f"Test:     PASS (exit 0, {commit})" in result.stdout
    assert "Owes:     merge of the tested code" in result.stdout


def test_explain_refuses_closed_without_merge(clones):
    one, _ = clones
    implement(one)
    passed(one)
    task(one, status="CLOSED")
    result = cli(one, "check", "TASK-1")
    assert result.returncode == 1 and "Result: NOT CLOSED" in result.stdout
    assert "Next:     merge the tested commit, then: notquite receipt merged --task TASK-1" in result.stdout
    merge(one)
    assert cli(one, "check", "TASK-1").returncode == 0


def test_explain_reports_a_failed_test(clones):
    one, _ = clones
    commit = implement(one)[:7]
    cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "raise SystemExit(3)")
    task(one, status="DONE")
    assert f"Test:     FAIL (exit 3, {commit})" in cli(one, "check", "TASK-1").stdout


@pytest.mark.parametrize("argument", ["elsewhere/TASK-1.md", "../TASK-1", "TASK-0"])
def test_explain_rejects_bad_task_arguments(clones, argument):
    one, _ = clones
    result = cli(one, "check", argument)
    assert result.returncode == 1 and result.stderr.startswith("notquite: ")


def test_explain_cannot_be_combined_with_staged(clones):
    one, _ = clones
    assert cli(one, "check", "TASK-1", "--staged").returncode == 1


def test_untracked_output_during_a_test_is_named(clones):
    one, _ = clones
    implement(one)
    result = cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c",
                 "open('build.log', 'w').write('x')")
    assert result.returncode == 1
    assert "recorded, but does not count: files changed while the command ran" in result.stdout
    assert "build.log" in result.stdout and ".gitignore" in result.stdout
    refused = cli(one, "receipt", "implemented", "--task", "TASK-1")
    assert refused.returncode == 1 and "build.log" in refused.stderr


@pytest.mark.skipif(os.name == "nt", reason="builds a POSIX PATH; the hook itself is tested above")
def test_hook_works_without_a_python_named_python(clones, tmp_path):
    one, _ = clones
    task(one, status="DONE")
    git(one, "add", "tasks/TASK-1.md")
    git(one, "config", "core.hooksPath", str(PACKAGE / "hooks"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(PYTHON)
    (bin_dir / "git").symlink_to(subprocess.run(["sh", "-c", "command -v git"], capture_output=True,
                                                encoding="utf-8").stdout.strip())
    env = dict(os.environ, PATH=f"{bin_dir}:/bin:/usr/bin", PYTHONPATH=str(PACKAGE))
    env.pop("VIRTUAL_ENV", None)
    result = subprocess.run(["git", "commit", "-qm", "False completion"], cwd=one, env=env,
                            capture_output=True, encoding="utf-8")
    assert result.returncode != 0 and "says DONE" in result.stdout + result.stderr


def test_shallow_clone_explains_missing_history(clones, tmp_path):
    one, _ = clones
    implement(one)
    passed(one)
    task(one, status="DONE")
    git(one, "add", "tasks", ".notquite")
    git(one, "commit", "-qm", "Record the evidence")
    (one / "app.py").write_text("items = ['later']\n", encoding="utf-8")
    git(one, "commit", "-qam", "Later work")
    git(one, "push", "-q", "origin", "HEAD:refs/heads/main")
    remote = git(one, "remote", "get-url", "origin")
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", Path(remote).resolve().as_uri(), str(shallow))
    result = cli(shallow, "check")
    assert result.returncode == 1 and "fetch-depth: 0" in result.stderr


def test_latest_test_run_decides_and_history_is_kept(clones):
    one, _ = clones
    commit = implement(one)[:7]
    task(one, status="DONE")
    fail = [PYTHON, "-c", "raise SystemExit(2)"]
    passed(one)
    assert "computed DONE" in ok(one, "show", "--task", "TASK-1")
    ok(one, "check")
    cli(one, "receipt", "test", "--task", "TASK-1", "--", *fail)
    assert "computed IN_PROGRESS" in ok(one, "show", "--task", "TASK-1")
    refused = cli(one, "check", "TASK-1")
    assert refused.returncode == 1 and "Result: NOT DONE" in refused.stdout
    assert (f"Test:     FAIL (exit 2, {commit}); an earlier run passed, but the latest run decides"
            in refused.stdout)
    assert cli(one, "check").returncode == 1
    passed(one)
    assert "computed DONE" in ok(one, "show", "--task", "TASK-1")
    ok(one, "check")
    kinds = [(e["kind"], e.get("exit_code")) for e in store.entries(one, "TASK-1")]
    assert kinds == [("implemented", None), ("test", 0), ("test", 2), ("test", 0)]


def test_a_later_uncounted_run_also_revokes_done(clones):
    one, _ = clones
    implement(one)
    passed(one)
    cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c",
        "open('build.log', 'w').write('x')")
    assert "computed IN_PROGRESS" in ok(one, "show", "--task", "TASK-1")


def test_a_failure_before_a_new_implementation_does_not_count_against_it(clones):
    one, _ = clones
    implement(one)
    cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "raise SystemExit(1)")
    (one / "app.py").write_text("items = ['fixed']\n", encoding="utf-8")
    git(one, "commit", "-qam", "Fix the list")
    implement(one)
    passed(one)
    assert "computed DONE" in ok(one, "show", "--task", "TASK-1")

@pytest.fixture
def local_repo(tmp_path):
    root = tmp_path / "local"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "solo")
    git(root, "config", "user.email", "solo@example.com")
    (root / "app.py").write_text("items = []\n", encoding="utf-8")
    git(root, "add", "app.py")
    git(root, "commit", "-qm", "Start")
    return root


def test_without_a_remote_tasks_are_local_and_checks_work(local_repo):
    opened = ok(local_repo, "new", "--title", "Solo work", "--owner", "solo", "--ships", "none")
    assert "TASK-1 opened" in opened and "local id: no origin remote" in opened
    assert "TASK-2 opened" in ok(local_repo, "new", "--title", "More", "--owner", "solo")
    ok(local_repo, "receipt", "implemented", "--task", "TASK-1")
    ok(local_repo, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "pass")
    assert "computed CLOSED" in ok(local_repo, "show", "--task", "TASK-1")
    assert ok(local_repo, "check").strip() == "check: OK"
    assert "TASK-2 free" in ok(local_repo, "ready")


@pytest.mark.parametrize("args", [("claim", "--task", "TASK-1"), ("release", "--task", "TASK-1"),
                                  ("claims",), ("receipt", "merged", "--task", "TASK-1")])
def test_without_a_remote_claims_and_merges_are_refused(local_repo, args):
    ok(local_repo, "new", "--title", "Solo work", "--owner", "solo")
    result = cli(local_repo, *args)
    assert result.returncode == 1 and "origin remote" in result.stderr
    assert not list(local_repo.joinpath(".git").glob("notquite-claims/*"))


def test_missing_git_user_name_gets_a_clear_message(clones):
    one, _ = clones
    git(one, "config", "--unset", "user.name")
    env = dict(os.environ, PYTHONPATH=str(PACKAGE), HOME=str(one), XDG_CONFIG_HOME=str(one),
               GIT_CONFIG_NOSYSTEM="1")
    result = subprocess.run([PYTHON, "-m", "notquite", "claim", "--task", "TASK-1"], cwd=one,
                            env=env, capture_output=True, encoding="utf-8")
    assert result.returncode == 1 and "set git user.name first" in result.stderr


def test_records_and_receipts_are_written_with_lf_on_every_platform(clones):
    one, _ = clones
    ok(one, "new", "--title", "Line endings", "--owner", "alex")
    implement(one)
    passed(one)
    for path in ("tasks/TASK-2.md", store.ledger_path("TASK-1")):
        assert b"\r\n" not in (one / path).read_bytes(), path


def test_crlf_checkout_of_committed_records_still_verifies(clones):
    """What Git on Windows with core.autocrlf does to committed text files."""
    one, _ = clones
    implement(one)
    passed(one)
    task(one, status="DONE")
    git(one, "add", "tasks", ".notquite")
    git(one, "commit", "-qm", "Record the evidence")
    for path in ("tasks/TASK-1.md", store.ledger_path("TASK-1")):
        data = (one / path).read_bytes()
        (one / path).write_bytes(data.replace(b"\n", b"\r\n"))
    assert ok(one, "check").strip() == "check: OK"
    passed(one)
    assert "computed DONE" in ok(one, "show", "--task", "TASK-1")


def claim_tag_value(root, tid="TASK-1", generation=1):
    ref = f"{store.NAMESPACE}claims/{tid}/{generation}"
    return store.remote_value(root, ref, store.remote_refs(root)[ref])


def test_claim_tag_holds_only_a_hash_of_the_private_token(clones):
    one, _ = clones
    ok(one, "claim", "--task", "TASK-1")
    token = store.claim_token_path(one, "TASK-1").read_text(encoding="utf-8")
    value = claim_tag_value(one)
    assert "token" not in value
    assert value["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    ref = f"{store.NAMESPACE}claims/TASK-1/1"
    raw = git(one, "cat-file", "blob", store.remote_refs(one)[ref])
    assert token not in raw
    path = store.claim_token_path(one, "TASK-1").resolve()
    assert path.is_relative_to((one / ".git").resolve())  # inside .git: never tracked or pushed


def test_another_clone_cannot_release_by_copying_what_the_tag_shows(clones):
    one, two = clones
    ok(one, "claim", "--task", "TASK-1")
    public = claim_tag_value(two)["token_sha256"]
    forged = store.claim_token_path(two, "TASK-1")
    forged.parent.mkdir(parents=True, exist_ok=True)
    for guess in (public, "0" * 32, ""):
        forged.write_text(guess, encoding="utf-8")
        refused = cli(two, "release", "--task", "TASK-1")
        assert refused.returncode == 1 and "only its holder can release it" in refused.stderr
        denied = cli(two, "claim", "--task", "TASK-1")
        assert denied.returncode == 1 and "held by one since" in denied.stderr
    assert "TASK-1 held by one since" in ok(two, "claims")


def test_holder_releases_once_and_a_lost_token_cannot_release(clones):
    one, two = clones
    ok(one, "claim", "--task", "TASK-1")
    token_path = store.claim_token_path(one, "TASK-1")
    token = token_path.read_text(encoding="utf-8")
    token_path.unlink()
    lost = cli(one, "release", "--task", "TASK-1")
    assert lost.returncode == 1 and "only its holder can release it" in lost.stderr
    token_path.write_text(token, encoding="utf-8")
    assert "TASK-1 released" in ok(one, "release", "--task", "TASK-1")
    again = cli(one, "release", "--task", "TASK-1")
    assert again.returncode == 1 and "no active claim" in again.stderr
    ok(two, "claim", "--task", "TASK-1")
    assert claim_tag_value(one, generation=2)["token_sha256"] != claim_tag_value(one)["token_sha256"]


# v0.2.0: the optional extended task format and one dependency model.

V010_SIMPLE = """---
id: "TASK-1"
title: "Solo work"
owner: "solo"
status: "IN_PROGRESS"
ships: "repo"
waits_on: []
contract: "none"
---

# TASK-1

Write the original ask here.
"""


def extended(root, tid="TASK-1", status="IN_PROGRESS", ships="repo", body=None, **extra):
    fields = {"id": tid, "title": "Add a list filter", "owner": "alex", "business_owner": "sam",
              "priority": "P1", "created_at": "2026-11-14", "mission": "Make long lists easier to scan",
              "depends_on": [], "blocks": [], "next": "Write the filter", "summary": "A filter button",
              "status": status, "ships": ships, "waits_on": [], "contract": "none"}
    fields.update(extra)
    text = ("---\n" + "".join(f"{k}: {json.dumps(v)}\n" for k, v in fields.items()) + f"---\n\n# {tid}\n"
            + (body if body is not None else
               "\n## Context\n\nInvented example.\n\n## Progress\n\n- 2026-11-14: marked DONE in notes.\n"))
    path = root / "tasks" / f"{tid}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_default_new_output_is_byte_identical_to_v010(local_repo):
    ok(local_repo, "new", "--title", "Solo work", "--owner", "solo")
    assert (local_repo / "tasks/TASK-1.md").read_text(encoding="utf-8") == V010_SIMPLE


def test_v010_plain_scalar_records_still_read(clones):
    one, _ = clones
    (one / "tasks/TASK-1.md").write_text(
        "---\nid: TASK-1\ntitle: Add a filter\nowner: alex\nstatus: DONE\nships: none\n"
        "waits_on: []\ncontract: none\n---\n\n# TASK-1\n", encoding="utf-8")
    implement(one)
    passed(one)
    assert ok(one, "check").strip() == "check: OK"


def test_extended_template_writes_every_field_and_section(local_repo):
    ok(local_repo, "new", "--title", "Plan the filter", "--owner", "solo", "--template", "extended")
    text = (local_repo / "tasks/TASK-1.md").read_text(encoding="utf-8")
    keys = [line.split(":", 1)[0] for line in text.split("---\n")[1].splitlines()]
    assert keys == list(store.EXTENDED_ORDER)
    sections = [line for line in text.splitlines() if line.startswith("## ")]
    assert sections == ["## Context", "## Scope", "## Lanes", "## Acceptance", "## Progress"]
    record = store.record(local_repo, "TASK-1")
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    assert record["created_at"] == today and record["depends_on"] == [] and record["status"] == "IN_PROGRESS"
    assert ok(local_repo, "check").strip() == "check: OK"


def test_extended_fields_never_stand_in_for_evidence(clones):
    one, _ = clones
    extended(one, status="DONE", ships="none", priority="P0", summary="Done and shipped",
             next="nothing left", business_owner="the whole company")
    refused = cli(one, "check", "TASK-1")
    assert refused.returncode == 1 and "Missing:  implementation" in refused.stdout
    implement(one)
    passed(one)
    assert ok(one, "check").strip() == "check: OK"
    extended(one, status="CLOSED", ships="repo", priority="P0")
    assert "merge of the tested code" in cli(one, "check").stdout


def test_receipts_recorded_on_a_simple_task_still_count_after_switching_to_extended(clones):
    one, _ = clones
    commit = implement(one)
    passed(one)
    git(one, "add", "tasks", ".notquite")
    git(one, "commit", "-qm", "Record the evidence")
    extended(one, status="DONE", ships="none")
    assert ok(one, "check").strip() == "check: OK"
    assert store.entries(one, "TASK-1")[0]["commit"] == commit


def test_reading_never_rewrites_task_files(clones):
    one, _ = clones
    path = extended(one, ships="none", body="\n\n## Context\n\n<!-- kept -->\n\n\n## Progress\n\n  odd   spacing  \n")
    raw = path.read_text(encoding="utf-8").replace('priority: "P1"', "priority:    P1")
    path.write_bytes(raw.encode("utf-8"))
    before = path.read_bytes()
    implement(one)
    passed(one)
    for args in (("check",), ("check", "TASK-1"), ("show", "--task", "TASK-1"), ("ready",),
                 ("claim", "--task", "TASK-1"), ("release", "--task", "TASK-1")):
        ok(one, *args)
    assert path.read_bytes() == before
    assert store.record(one, "TASK-1")["priority"] == "P1"


def test_simple_yaml_style_lists_are_read(clones):
    one, _ = clones
    task(one, "TASK-2")
    task(one, "TASK-3")
    path = extended(one, "TASK-4")
    text = path.read_text(encoding="utf-8")
    text = text.replace('depends_on: []', 'depends_on: [TASK-2, task:TASK-3]')
    text = text.replace('waits_on: []', 'waits_on: [person:alex]')
    path.write_text(text, encoding="utf-8")
    record = store.record(one, "TASK-4")
    assert record["depends_on"] == ["TASK-2", "TASK-3"] and record["waits_on"] == ["person:alex"]
    assert store.dependencies(record) == ["person:alex", "task:TASK-2", "task:TASK-3"]


@pytest.mark.parametrize("change,message", [
    (('priority: "P1"', "priority: |\n  P1"), "priority cannot be a > or | block"),
    (('summary: "A filter button"', "summary: A filter\n  button"), "indented lines belong only under"),
    (("depends_on: []", "depends_on:\n  - id: TASK-2"), 'list lines look like "  - TASK-1"'),
    (('priority: "P1"', '# note: urgent\npriority: "P1"'), "line 6:"),
    (("depends_on: []", "depends_on: &deps [TASK-2]"), "depends_on must be a list of task ids"),
    (('summary: "A filter button"', "summary: |2\n    text"), "indented lines belong only under"),
    (('summary: "A filter button"', 'summary: "A filter\n  button"'), "indented lines belong only under"),
    (('priority: "P1"', 'priorty: "P1"'), "unknown priorty"),
    (('created_at: "2026-11-14"', 'created_at: "14/11/2026"'), "ISO date"),
    (("depends_on: []", 'depends_on: ["person:alex"]'), "people waits go in waits_on"),
    (("depends_on: []", 'depends_on: ["TASK-1"]'), "cannot name the task itself"),
    (("depends_on: []", 'depends_on: [TASK-2]\nblocks: [TASK-2]'), "duplicate field blocks"),
    (("blocks: []", 'blocks: ["TASK-2"]\nwaits_on_x: 1'), "unknown waits_on_x"),
    (('mission: "Make long lists easier to scan"', "mission: [unclosed"), "cannot read mission"),
])
def test_unsupported_front_matter_is_refused_with_a_reason(clones, change, message):
    one, _ = clones
    task(one, "TASK-2")
    path = extended(one)
    text = path.read_text(encoding="utf-8")
    assert change[0] in text
    path.write_text(text.replace(change[0], change[1]), encoding="utf-8")
    result = cli(one, "check")
    assert result.returncode == 1 and message in result.stderr, result.stderr


def test_waiting_for_and_blocking_the_same_task_is_contradictory(clones):
    one, _ = clones
    task(one, "TASK-2")
    extended(one, depends_on=["TASK-2"], blocks=["TASK-2"])
    assert "cannot both wait for and block TASK-2" in cli(one, "check").stderr
    extended(one, waits_on=["task:TASK-2"], blocks=["TASK-2"])
    assert "cannot both wait for and block TASK-2" in cli(one, "check").stderr


def test_depends_on_and_waits_on_form_one_dependency_list(clones):
    one, _ = clones
    task(one, "TASK-1")
    extended(one, "TASK-2", status="DONE", ships="none", depends_on=["TASK-1"], waits_on=["task:TASK-1"])
    assert "waits_on: task:TASK-1\n" in ok(one, "show", "--task", "TASK-2")
    ok(one, "receipt", "implemented", "--task", "TASK-2")
    ok(one, "receipt", "test", "--task", "TASK-2", "--", PYTHON, "-c", "pass")
    refused = cli(one, "check", "TASK-2")
    assert refused.returncode == 1 and "Missing:  task:TASK-1" in refused.stdout
    assert "finish TASK-1 first, or remove it from depends_on" in refused.stdout
    implement(one)
    passed(one)
    assert cli(one, "check", "TASK-2").returncode == 0
    assert ok(one, "check").strip() == "check: OK"


def test_ready_reports_depends_on_like_waits_on(clones):
    one, _ = clones
    task(one, "TASK-1")
    extended(one, "TASK-2", depends_on=["TASK-1"])
    assert "TASK-2 blocked: task:TASK-1" in ok(one, "ready")


def test_cycles_and_missing_tasks_are_found_across_both_fields(clones):
    one, _ = clones
    extended(one, "TASK-1", depends_on=["TASK-2"])
    task(one, "TASK-2", waits=["task:TASK-1"])
    assert "dependency cycle" in cli(one, "check").stderr
    extended(one, "TASK-1", depends_on=["TASK-9"])
    task(one, "TASK-2")
    assert "TASK-1: missing dependency TASK-9" in cli(one, "check").stdout


def test_blocks_is_informational_but_checked(clones):
    one, _ = clones
    extended(one, "TASK-2", blocks=["TASK-1"])
    task(one, "TASK-1", status="DONE", ships="none")
    implement(one)
    passed(one)
    assert ok(one, "check").strip() == "check: OK"  # TASK-2 is unfinished, yet TASK-1 may be DONE
    extended(one, "TASK-2", blocks=["TASK-7"])
    assert "TASK-2: blocks unknown task TASK-7" in cli(one, "check").stdout
    extended(one, "TASK-2", blocks=["TASK-3"])
    extended(one, "TASK-3", blocks=["TASK-2"])
    assert "TASK-2 and TASK-3 each say they block the other" in cli(one, "check").stdout


# The YAML subset: > and | blocks for mission/next/summary, indented lists for list fields.

FIXTURES = PACKAGE / "tests/fixtures/yaml-style"


def load_fixtures(root):
    for source in FIXTURES.glob("*.md"):
        (root / "tasks" / source.name).write_bytes(source.read_bytes())


def test_yaml_style_fixtures_read_to_the_expected_values(clones):
    one, _ = clones
    load_fixtures(one)
    records = gate.records(one)
    full = records["TASK-11"]
    assert list(full) == list(store.EXTENDED_ORDER)
    assert full["mission"] == "Let a user share a read-only list with someone else.\nNobody else can edit it.\n"
    assert full["next"] == "Agree how long a shared link stays valid before building the share button."
    assert full["summary"] == ("A share button creates a read-only link to the list.\n"
                               "Links expire, and the owner can revoke them.\n")
    assert full["depends_on"] == ["TASK-10", "TASK-12"] and full["blocks"] == ["TASK-13"]
    assert full["waits_on"] == ["person:sam"] and full["created_at"] == "2026-11-14T09:30:00Z"
    assert store.dependencies(full) == ["person:sam", "task:TASK-10", "task:TASK-12"]
    partial = records["TASK-12"]
    assert partial["depends_on"] == [] and partial["blocks"] == ["TASK-11"]
    assert partial["summary"] == "A list view without edit controls."
    assert records["TASK-10"]["waits_on"] == [] and "depends_on" not in records["TASK-10"]
    assert ok(one, "check").strip() == "check: OK"
    output = ok(one, "ready")
    assert "TASK-11 blocked: person:sam, task:TASK-10, task:TASK-12" in output
    assert "TASK-10 free" in output and "TASK-12 free" in output


def test_yaml_style_files_are_never_rewritten(clones):
    one, _ = clones
    load_fixtures(one)
    before = {p.name: p.read_bytes() for p in (one / "tasks").glob("TASK-1?.md")}
    ok(one, "receipt", "implemented", "--task", "TASK-11")
    ok(one, "receipt", "test", "--task", "TASK-11", "--", PYTHON, "-c", "pass")
    for args in (("check",), ("check", "TASK-11"), ("show", "--task", "TASK-11"), ("ready",),
                 ("claim", "--task", "TASK-11"), ("release", "--task", "TASK-11")):
        ok(one, *args)
    assert {p.name: p.read_bytes() for p in (one / "tasks").glob("TASK-1?.md")} == before


def test_yaml_style_dependencies_hold_back_a_done_claim(clones):
    one, _ = clones
    load_fixtures(one)
    path = one / "tasks/TASK-11.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("status: IN_PROGRESS", "status: DONE").replace("ships: live", "ships: none")
                    .replace("waits_on:\n  - person:sam\n", "waits_on: []\n"), encoding="utf-8")
    ok(one, "receipt", "implemented", "--task", "TASK-11")
    ok(one, "receipt", "test", "--task", "TASK-11", "--", PYTHON, "-c", "pass")
    refused = cli(one, "check", "TASK-11")
    assert refused.returncode == 1
    assert "Missing:  task:TASK-10" in refused.stdout and "Missing:  task:TASK-12" in refused.stdout


@pytest.mark.parametrize("indicator,expected", [
    ("|", "one\n  two\n\nthree\n"),
    ("|-", "one\n  two\n\nthree"),
    ("|+", "one\n  two\n\nthree\n\n"),
    (">", "one\n  two\n\nthree\n"),
    (">-", "one\n  two\n\nthree"),
    (">+", "one\n  two\n\nthree\n\n"),
])
def test_block_text_follows_yaml_for_the_supported_forms(indicator, expected):
    block = ["    one", "      two", "", "    three", ""]
    assert store.block_text("TASK-1", "summary", indicator, block) == expected


def test_folded_block_joins_ordinary_lines_and_keeps_paragraphs():
    block = ["  A share button", "  creates a link.", "", "  Links expire."]
    assert store.block_text("TASK-1", "summary", ">", block) == "A share button creates a link.\nLinks expire.\n"


def test_block_text_edge_cases():
    assert store.block_text("TASK-1", "summary", ">", []) == ""
    assert store.block_text("TASK-1", "summary", "|", ["", ""]) == ""
    with pytest.raises(store.Refused, match="keep the indentation"):
        store.block_text("TASK-1", "summary", "|", ["    one", "  two"])


def test_switching_a_simple_task_to_yaml_style_extended_keeps_its_receipts(clones, tmp_path):
    one, _ = clones
    task(one, status="IN_PROGRESS", ships="none")
    commit = implement(one)
    passed(one)
    git(one, "add", "tasks", ".notquite")
    git(one, "commit", "-qm", "Record the evidence")
    (one / "tasks/TASK-1.md").write_text(
        "---\nid: TASK-1\ntitle: Add a list filter\nowner: alex\nbusiness_owner: sam\npriority: P1\n"
        "created_at: 2026-11-14\nsummary: >\n  A filter button that hides\n  completed items.\n"
        "depends_on:\nblocks:\n  - TASK-2\nstatus: DONE\nships: none\nwaits_on: []\ncontract: none\n"
        "---\n\n# TASK-1\n\n## Progress\n\n- 2026-11-14: switched to the extended format.\n", encoding="utf-8")
    task(one, "TASK-2")
    git(one, "add", "tasks")
    git(one, "commit", "-qm", "Switch TASK-1 to the extended format")
    git(one, "push", "-q", "origin", "HEAD:refs/heads/main")
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", str(Path(git(one, "remote", "get-url", "origin"))), str(fresh))
    assert ok(fresh, "check").strip() == "check: OK"
    explained = ok(fresh, "check", "TASK-1")
    assert explained.startswith("DONE  TASK-1") and f"PASS (exit 0, {commit[:7]})" in explained
    assert store.entries(fresh, "TASK-1")[0]["commit"] == commit


def test_a_hash_after_a_value_stays_part_of_the_value(clones):
    one, _ = clones
    path = extended(one)
    path.write_text(path.read_text(encoding="utf-8").replace('priority: "P1"', "priority: P1 # soon"),
                    encoding="utf-8")
    assert store.record(one, "TASK-1")["priority"] == "P1 # soon"


# 0.2.1: the independent review's reproductions, kept as regression tests.

def commit_receipts(root, message="Record receipts"):
    git(root, "add", "tasks", ".notquite")
    git(root, "commit", "-qm", message)
    return store.head(root)


def test_a_failing_run_after_the_receipts_are_committed_revokes_done(clones):
    one, _ = clones
    implement(one)
    passed(one)
    task(one, status="DONE")
    commit_receipts(one)
    ok(one, "check")
    failed = cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "raise SystemExit(1)")
    assert failed.returncode == 1 and "still owes: a passing test on" in failed.stdout
    refused = cli(one, "check")
    assert refused.returncode == 1 and "says DONE but owes: a passing test on" in refused.stdout
    passed(one)
    ok(one, "check")


def test_a_passing_run_on_the_same_code_after_the_receipts_commit_counts(clones):
    one, _ = clones
    old = implement(one)[:7]
    new = commit_receipts(one)[:7]
    result = cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "pass")
    assert result.returncode == 0 and "does not count" not in result.stdout
    task(one, status="DONE")
    explained = cli(one, "check", "TASK-1")
    assert explained.returncode == 0, explained.stdout
    assert f"Test:     PASS (exit 0, {new}, same code as {old})" in explained.stdout


def test_a_test_on_changed_code_does_not_count_and_says_what_to_do(clones):
    one, _ = clones
    old = implement(one)[:7]
    (one / "app.py").write_text("items = ['changed']\n", encoding="utf-8")
    git(one, "commit", "-qam", "Change the list")
    result = cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "pass")
    assert result.returncode == 1
    assert f"recorded, but does not count: this code differs from the implemented commit {old}" in result.stdout
    task(one, status="DONE")
    explained = cli(one, "check", "TASK-1")
    assert explained.returncode == 1
    assert f"Note:     the code has changed since {old}; this verdict is about {old}" in explained.stdout
    assert ("Next:     notquite receipt implemented --task TASK-1, then notquite receipt test"
            in explained.stdout)


def test_the_hook_refuses_a_fresh_done_on_changed_code(clones):
    one, _ = clones
    implement(one)
    passed(one)
    commit_receipts(one)
    (one / "app.py").write_text("items = ['changed after testing']\n", encoding="utf-8")
    task(one, status="DONE")
    git(one, "add", "app.py", "tasks/TASK-1.md")
    staged = cli(one, "check", "--staged")
    assert staged.returncode == 1
    assert ("TASK-1 is being marked DONE, but the code in this commit differs from the implemented"
            in staged.stdout)
    git(one, "config", "core.hooksPath", str(PACKAGE / "hooks"))
    env = dict(os.environ, PYTHONPATH=str(PACKAGE))
    hooked = subprocess.run(["git", "commit", "-qm", "Done, with an untested change"], cwd=one,
                            env=env, capture_output=True, encoding="utf-8")
    assert hooked.returncode != 0 and "is being marked DONE" in hooked.stdout + hooked.stderr
    git(one, "reset", "-q", "app.py")
    (one / "app.py").write_text("items = []\n", encoding="utf-8")  # back to the tested code
    ok(one, "check", "--staged")


def test_an_earlier_done_is_kept_when_later_code_lands_and_explain_notes_it(clones):
    one, _ = clones
    old = implement(one)[:7]
    passed(one)
    task(one, status="DONE")
    commit_receipts(one)
    (one / "other.py").write_text("x = 1\n", encoding="utf-8")
    git(one, "add", "other.py")
    ok(one, "check", "--staged")  # only a commit that newly claims DONE is held to its code
    git(one, "commit", "-qm", "Other work")
    ok(one, "check")
    explained = cli(one, "check", "TASK-1")
    assert explained.returncode == 0
    assert f"Note:     the code has changed since {old}; this verdict is about {old}" in explained.stdout


def test_check_base_finds_receipts_dropped_in_a_later_commit(clones):
    one, _ = clones
    implement(one)
    cli(one, "receipt", "test", "--task", "TASK-1", "--", PYTHON, "-c", "raise SystemExit(1)")
    base = commit_receipts(one)
    ok(one, "check", "--base", base)
    ledger = one / store.ledger_path("TASK-1")
    ledger.write_text(ledger.read_text(encoding="utf-8").splitlines(keepends=True)[0], encoding="utf-8")
    git(one, "commit", "-qam", "Drop the failing run")  # the helper skips hooks, like --no-verify
    ok(one, "check")  # without a base, the commit is compared only with itself
    refused = cli(one, "check", "--base", base)
    assert refused.returncode == 1
    assert f"TASK-1: receipts recorded on {base} were removed or changed" in refused.stdout
    ledger.unlink()
    git(one, "commit", "-qam", "Drop the ledger")
    assert f"TASK-1: receipts recorded on {base} were removed or changed" in cli(one, "check", "--base", base).stdout
    missing = cli(one, "check", "--base", "origin/no-such-branch")
    assert missing.returncode == 1 and "is not in this clone" in missing.stderr
    assert cli(one, "check", "TASK-1", "--base", base).returncode == 1


def test_receipts_and_claims_no_longer_carry_the_hostname(clones):
    one, _ = clones
    implement(one)
    ok(one, "claim", "--task", "TASK-1")
    host = socket.gethostname()
    assert store.entries(one, "TASK-1")[-1]["who"] == "one"
    assert host not in (one / store.ledger_path("TASK-1")).read_text(encoding="utf-8")
    assert claim_tag_value(one)["who"] == "one"


@pytest.mark.parametrize("ships, kind, message", [
    ("none", "merged", "has ships: none, so a merged receipt is not part of its evidence"),
    ("repo", "deployed", "has ships: repo, so a deployed receipt is not part of its evidence"),
    ("live", "deployed", "no merge is recorded since the implementation"),
])
def test_delivery_receipts_that_cannot_count_exit_1_with_a_reason(clones, ships, kind, message):
    one, _ = clones
    task(one, ships=ships)
    implement(one)
    passed(one)
    if kind == "merged":
        git(one, "push", "-q", "origin", "HEAD:refs/heads/main")
        result = cli(one, "receipt", "merged", "--task", "TASK-1")
    else:
        result = readback(one, kind)
    assert result.returncode == 1
    assert "recorded, but does not count: " in result.stdout and message in result.stdout
