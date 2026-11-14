"""Usability regressions, using only disposable local repositories."""

import pytest

from workorder import store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all


def record_implementation(root, tid):
    store.receipt(root, tid, "implemented")
    store.receipt(root, tid, "test", [PYTHON, "-c", "pass"])
    commit_all(root, f"Record {tid} evidence")


def squash(root):
    git(root, "checkout", "-q", "main")
    (root / "unrelated.txt").write_text("Main moved.\n")
    commit_all(root, "Unrelated main work")
    git(root, "merge", "-q", "--squash", "feature")
    return commit_all(root, "Squash the feature")


@pytest.mark.parametrize("mode", ["committed", "working", "staged"])
@pytest.mark.parametrize("protected", ["safe.py", "test_safe.py", "shared.py", "early.py"])
def test_two_tasks_in_one_squash_protect_their_own_paths(local_repo, mode, protected):
    root = local_repo
    for tid in ("TASK-1", "TASK-2"):
        task(root, tid, ships="repo")
    commit_all(root, "Define tasks")
    git(root, "checkout", "-q", "-b", "feature")
    # TASK-2 also has work before TASK-1's implementation, without a receipt yet.
    (root / "early.py").write_text("early = True\n")
    commit_all(root, "Start TASK-2")
    for name in ("undo.py", "test_undo.py", "shared.py"):
        (root / name).write_text("value = 1\n")
    commit_all(root, "Implement TASK-1")
    record_implementation(root, "TASK-1")
    for name in ("safe.py", "test_safe.py", "shared.py"):
        (root / name).write_text("value = 2\n")
    commit_all(root, "Implement TASK-2")
    record_implementation(root, "TASK-2")
    landing = squash(root)
    (root / "undo.py").write_text("value = 3\n")
    task(root, "TASK-1", ships="repo", status="DONE")
    commit_all(root, "Change undo and claim TASK-1")
    (root / "notes.txt").write_text("Ready for acceptance.\n")
    if mode == "working":
        commit_all(root, "Add notes")
    task(root, "TASK-2", ships="repo", status="DONE")
    if mode == "committed":
        commit_all(root, "Claim TASK-2")
    elif mode == "staged":
        git(root, "add", "-A")
    if mode == "staged":
        ok(root, "check", "--staged")
    else:
        assert "claim supported" in ok(root, "check", "TASK-2")
    # A base check still rejects TASK-1's stale claim, but must accept TASK-2.
    result = cli(root, "check", "--base", landing, *(["--staged"] if mode == "staged" else []))
    assert result.returncode == 1 and "TASK-1" in result.stdout
    assert "NOT QUITE  TASK-2" not in result.stdout
    if mode != "committed":
        commit_all(root, "Commit TASK-2 claim")
    # Shared paths and earlier unreceipted work still belong to TASK-2.
    (root / protected).write_text("value = 'untested'\n")
    task(root, "TASK-2", ships="repo", status="CLOSED")
    commit_all(root, "Change TASK-2 code and close")
    refused = cli(root, "check", "TASK-2")
    assert refused.returncode == 1 and "renew --task TASK-2" in refused.stdout


def test_previous_implementation_bounds_the_current_task_paths(local_repo):
    root = local_repo
    task(root, ships="repo")
    commit_all(root, "Define task")
    git(root, "checkout", "-q", "-b", "feature")
    (root / "before.py").write_text("before = True\n")
    commit_all(root, "Earlier work")
    (root / "old.py").write_text("old = True\n")
    commit_all(root, "First implementation")
    record_implementation(root, "TASK-1")
    (root / "current.py").write_text("current = True\n")
    commit_all(root, "Current implementation")
    record_implementation(root, "TASK-1")
    squash(root)
    for name in ("before.py", "old.py"):
        (root / name).write_text("later = True\n")
    task(root, ships="repo", status="DONE")
    commit_all(root, "Claim after earlier paths changed")
    assert "claim supported" in ok(root, "check", "TASK-1")
    (root / "current.py").unlink()
    task(root, ships="repo", status="CLOSED")
    commit_all(root, "Delete current work and close")
    assert cli(root, "check", "TASK-1").returncode == 1


def test_committed_claim_keeps_other_tasks_recorded_boundaries(local_repo):
    root = local_repo
    for tid in ("TASK-1", "TASK-2"):
        task(root, tid, ships="repo")
    commit_all(root, "Define tasks")
    git(root, "checkout", "-q", "-b", "feature")
    for tid, name in (("TASK-1", "undo.py"), ("TASK-2", "safe.py")):
        (root / name).write_text("value = 1\n")
        commit_all(root, f"Implement {tid}")
        record_implementation(root, tid)
    squash(root)
    (root / "undo.py").write_text("value = 2\n")
    task(root, "TASK-2", ships="repo", status="DONE")
    commit_all(root, "Claim TASK-2 after TASK-1 code changed")
    ok(root, "check", "TASK-2")
    # Removing another task later must not rewrite a supported historical claim.
    git(root, "rm", "-r", "tasks/TASK-1.md", ".workorder/receipts/TASK-1")
    commit_all(root, "Remove TASK-1 and its receipts")
    assert "claim supported" in ok(root, "check", "TASK-2")


def test_renew_help_names_only_the_receipts_it_records(local_repo):
    output = " ".join(ok(local_repo, "--help").split())
    renew = output.split("renew ", 1)[1].split("note ", 1)[0]
    assert "implemented" in renew and "test" in renew
    assert "merges are computed" in renew
    assert "the merge if the task ships" not in renew


def test_base_help_names_the_checks_and_options(local_repo):
    output = " ".join(ok(local_repo, "check", "--help").split())
    base = output.split("--base REF", 1)[1]
    for phrase in ("changed or removed receipts", "test, contract, evidence, dependencies",
                   "Scope and Acceptance lines", "test-file lines",
                   "tasks created and claimed finished in the same change", "task removal",
                   "--allow", "--run-tests"):
        assert phrase in base


def test_progress_template_explains_claim_and_computed_support(local_repo):
    ok(local_repo, "new", "--title", "Calc", "--owner", "solo")
    text = (local_repo / "tasks/TASK-1.md").read_text()
    progress = text.split("## Progress", 1)[1]
    assert "status line is your claim" in progress
    assert "workorder show" in progress and "workorder check" in progress
    assert "compute what the receipts support" in progress


@pytest.mark.parametrize("status", ["IN_PROGRESS", "BLOCKED"])
def test_show_gives_next_step_for_unclaimed_acceptance(local_repo, status):
    ok(local_repo, "new", "--title", "Calc", "--owner", "solo", "--test", f"{PYTHON} -c pass")
    path = local_repo / "tasks/TASK-1.md"
    path.write_text(path.read_text().replace('status: "IN_PROGRESS"', f'status: "{status}"'))
    record_implementation(local_repo, "TASK-1")
    before = path.read_bytes()
    shown = ok(local_repo, "show", "--task", "TASK-1")
    assert "still owes: its Acceptance items" in shown
    assert ("next step: finish the Acceptance items, then set status DONE and run "
            "workorder check TASK-1") in shown
    assert "mission:" not in shown and "next:" not in shown
    assert path.read_bytes() == before
    for ended in ("DONE", "CLOSED", "CANCELLED"):
        path.write_text(before.decode().replace(f'status: "{status}"', f'status: "{ended}"'))
        assert "next step:" not in ok(local_repo, "show", "--task", "TASK-1")
