"""0.4 regressions, built from explicit content in disposable repositories."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess

import pytest

from workorder import gate, store
from test_workorder import (PACKAGE, PYTHON, cli, clones, commit_receipts, git, implement,
                            local_repo, ok, passed, readback, task)


def raw_receipt(root, kind="implemented", *, parents=None, previous=None, at=0, **extra):
    value = dict(task="TASK-1", kind=kind, who="fixture", at=f"2026-01-01T00:00:{at:02d}+00:00",
                 commit=store.head(root), code=store.code_digest(root, store.head(root)))
    if previous is not None:
        value["previous"] = previous
    else:
        value["parents"] = parents or []
    if kind == "test":
        value.update(command=[PYTHON, "-c", "pass"], exit_code=0, valid=True)
    value.update(extra)
    value["hash"] = store.digest(store.packed(value).encode())
    return value


def receipt_file(root, item):
    stamp = dt.datetime.fromisoformat(item["at"]).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    path = root / ".workorder/receipts/TASK-1" / f"{stamp}-{item['hash'][:12]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(store.packed(item) + "\n", encoding="utf-8", newline="\n")
    return path


def legacy_file(root, items, folder=".workorder"):
    path = root / folder / "receipts/TASK-1.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(store.packed(item) + "\n" for item in items), encoding="utf-8")
    return path


def test_two_clones_merge_receipts_and_join_both_heads(clones):
    one, two = clones
    task(one, ships="none", kind='verification')
    implement(one)
    base = commit_receipts(one)
    git(two, "fetch", "-q", str(one), "main")
    git(two, "merge", "-q", "--ff-only", "FETCH_HEAD")
    passed(one)
    passed(two)
    commit_receipts(one, "First test")
    commit_receipts(two, "Second test")
    git(one, "fetch", "-q", str(two), "main")
    git(one, "merge", "-q", "--no-edit", "FETCH_HEAD")
    history = store.entries(one, "TASK-1")
    assert len(history) == 3
    assert history[1]["parents"] == history[2]["parents"] == [history[0]["hash"]]
    passed(one)
    assert store.entries(one, "TASK-1")[-1]["parents"] == sorted(e["hash"] for e in history[1:])
    task(one, ships="none", status="DONE")
    ok(one, "check")
    ok(one, "check", "--base", base)
    assert not (one / ".workorder/receipts/TASK-1.jsonl").exists()


@pytest.mark.parametrize("mode", ["plain", "staged", "base", "hook"])
@pytest.mark.parametrize("damage", ["delete", "edit"])
def test_committed_receipt_files_are_immutable(local_repo, mode, damage):
    root = local_repo
    task(root, ships="none")
    path = receipt_file(root, raw_receipt(root))
    base = commit_receipts(root)
    if damage == "delete":
        path.unlink()
    else:
        # Even harmless whitespace is a change to a committed object.
        path.write_text(path.read_text().replace('{', '{ ', 1), encoding="utf-8")
    if mode in {"staged", "base", "hook"}:
        git(root, "add", "-u")
    if mode == "base":
        git(root, "commit", "-qm", "Bypass the hook")
    if mode == "hook":
        git(root, "config", "core.hooksPath", str(PACKAGE / "hooks"))
        result = subprocess.run(["git", "commit", "-qm", "Damage"], cwd=root, capture_output=True,
                                encoding="utf-8", env=dict(os.environ, PYTHONPATH=str(PACKAGE)))
    else:
        args = ["--staged"] if mode == "staged" else ["--base", base] if mode == "base" else []
        result = cli(root, "check", *args)
    assert result.returncode != 0
    assert "removed or changed" in result.stdout + result.stderr


@pytest.mark.parametrize("damage,message", [
    ("content", "content does not match its hash"),
    ("name", "file name does not match"),
    ("parent", "a receipt this one follows is missing: removed or not yet merged"),
    ("fields", "parents must be distinct hashes"),
])
def test_receipt_files_refuse_invalid_content(local_repo, damage, message):
    task(local_repo, ships="none")
    item = raw_receipt(local_repo, parents=["a" * 64] if damage == "parent" else None)
    if damage == "fields":
        item = raw_receipt(local_repo, parents=["a" * 64, "a" * 64])
    path = receipt_file(local_repo, item)
    if damage == "content":
        path.write_text(path.read_text().replace('"fixture"', '"edited"'), encoding="utf-8")
    if damage == "name":
        path.rename(path.with_name(path.name.replace(item["hash"][:12], "0" * 12)))
    refused = cli(local_repo, "check")
    assert refused.returncode == 1 and message in refused.stdout + refused.stderr


def test_receipt_order_is_topological_then_time_then_hash(local_repo):
    task(local_repo, ships="none")
    first = raw_receipt(local_repo, at=20)
    a = raw_receipt(local_repo, "test", parents=[first["hash"]], at=10, who="a")
    b = raw_receipt(local_repo, "test", parents=[first["hash"]], at=10, who="b", exit_code=1, valid=False)
    last = raw_receipt(local_repo, "test", parents=[a["hash"], b["hash"]], at=0)
    for entry in (last, b, first, a):
        receipt_file(local_repo, entry)
    history = store.entries(local_repo, "TASK-1")
    assert [e["hash"] for e in history] == [first["hash"], *sorted([a["hash"], b["hash"]]), last["hash"]]
    task(local_repo, ships="none", status="DONE")
    ok(local_repo, "check")
    assert "PASS" in ok(local_repo, "check", "TASK-1")
    assert all("missing" in e and "missing" not in json.loads(p.read_text())
               for e, p in zip(history, sorted((local_repo / ".workorder/receipts/TASK-1").glob("*.json"))))


@pytest.mark.parametrize("folder", [".workorder", ".notquite"])
def test_legacy_only_then_mixed_history_keeps_the_prefix(local_repo, folder):
    task(local_repo, ships="none", status="DONE")
    first = raw_receipt(local_repo, previous="")
    tested = raw_receipt(local_repo, "test", previous=first["hash"], at=1)
    path = legacy_file(local_repo, [first, tested], folder)
    before = path.read_bytes()
    git(local_repo, "add", "tasks", folder)
    git(local_repo, "commit", "-qm", "Legacy evidence")
    ok(local_repo, "check")
    assert len(store.entries(local_repo, "TASK-1")) == 2
    passed(local_repo)
    assert path.read_bytes() == before
    history = store.entries(local_repo, "TASK-1")
    assert history[-1]["parents"] == [tested["hash"]]
    assert history[0]["previous"] == ""
    assert b"\r\n" not in next((local_repo / ".workorder/receipts/TASK-1").glob("*.json")).read_bytes()
    ok(local_repo, "check")


@pytest.mark.parametrize("style", ["merge", "diff3"])
def test_repair_preserves_both_verified_legacy_sides(local_repo, style):
    root = local_repo
    task(root, ships="none")
    first = raw_receipt(root, previous="")
    path = legacy_file(root, [first])
    commit_receipts(root)
    git(root, "checkout", "-q", "-b", "side")
    a = raw_receipt(root, "test", previous=first["hash"], at=1)
    aa = raw_receipt(root, "test", previous=a["hash"], at=2)
    legacy_file(root, [first, a, aa])
    side = commit_receipts(root, "Side tests")
    git(root, "checkout", "-q", "main")
    b = raw_receipt(root, "test", previous=first["hash"], at=3)
    legacy_file(root, [first, b])
    main = commit_receipts(root, "Main test")
    git(root, "config", "merge.conflictStyle", style)
    conflict = subprocess.run(["git", "merge", "side"], cwd=root, capture_output=True)
    assert conflict.returncode == 1 and "<<<<<<<" in path.read_text()
    result = ok(root, "repair", "--task", "TASK-1")
    assert "kept 1 legacy receipts" in result and "3 files" in result
    assert path.read_text() == store.packed(first) + "\n"
    history = store.entries(root, "TASK-1")
    assert len(history) == 4
    converted = {e["legacy"]["hash"]: e for e in history[1:]}
    assert converted[a["hash"]]["parents"] == converted[b["hash"]]["parents"] == [first["hash"]]
    assert converted[aa["hash"]]["parents"] == [converted[a["hash"]]["hash"]]
    ok(root, "check", "--base", main)  # the working repair still preserves HEAD's receipts
    refused = cli(root, "check", "--base", side)
    # main has modified the legacy file since side; --base now audits intervening commits.
    assert refused.returncode == 1 and "removed or changed since" in refused.stdout
    assert ".workorder/receipts/TASK-1.jsonl" in refused.stdout
    git(root, "add", "tasks", ".workorder")
    ok(root, "check", "--staged")
    git(root, "commit", "-qm", "Repair")
    passed(root)
    assert len(store.entries(root, "TASK-1")[-1]["parents"]) == 2


@pytest.mark.parametrize("damage", ["hash", "chain", "markers", "suffix"])
def test_repair_refuses_unverifiable_input_without_writing(local_repo, damage):
    task(local_repo, ships="none")
    first = raw_receipt(local_repo, previous="")
    a = raw_receipt(local_repo, "test", previous="f" * 64 if damage == "chain" else first["hash"], at=1)
    b = raw_receipt(local_repo, "test", previous=first["hash"], at=2)
    if damage == "hash":
        a["valid"] = False
    content = (store.packed(first) + "\n<<<<<<< HEAD\n" + store.packed(a) + "\n=======\n"
               + store.packed(b) + "\n>>>>>>> side\n")
    if damage == "markers":
        content = content.replace("=======", "<<<<<<< extra")
    if damage == "suffix":
        content += store.packed(b) + "\n"
    path = legacy_file(local_repo, [])
    path.write_text(content, encoding="utf-8")
    refused = cli(local_repo, "repair", "--task", "TASK-1")
    assert refused.returncode == 1  # old CLI exits 2 for an unknown command
    assert path.read_text() == content
    assert not (local_repo / ".workorder/receipts/TASK-1").exists()


def feature(root, ships="repo", multi=False):
    task(root, ships=ships)
    git(root, "add", "tasks")
    git(root, "commit", "-qm", "Task")
    base = store.head(root)
    git(root, "checkout", "-q", "-b", "feature")
    (root / "app.py").write_text("items = ['tested']\n", encoding="utf-8")
    git(root, "commit", "-qam", "Filter items")
    if multi:
        (root / "more.py").write_text("answer = 42\n", encoding="utf-8")
        git(root, "add", "more.py")
        git(root, "commit", "-qm", "Second part")
    implement(root)
    passed(root)
    task(root, ships=ships, status="CLOSED")
    commit_receipts(root, "Tested task")
    return base


def move_main(root):
    git(root, "checkout", "-q", "main")
    (root / "other.py").write_text("unrelated = True\n", encoding="utf-8")
    git(root, "add", "other.py")
    git(root, "commit", "-qm", "Unrelated main work")


@pytest.mark.parametrize("method", ["ancestry", "squash", "rebase", "same code"])
def test_computed_merge_closes_without_post_merge_receipts(local_repo, method):
    root = local_repo
    base = feature(root, multi=True)
    before = {p.name: p.read_bytes().replace(b"\r\n", b"\n") for p in (root / ".workorder/receipts/TASK-1").glob("*.json")}
    if method == "same code":
        git(root, "checkout", "-q", "main")
    else:
        move_main(root)
    if method == "ancestry":
        git(root, "merge", "-q", "--no-ff", "--no-edit", "feature")
    elif method == "rebase":
        git(root, "checkout", "-q", "feature")
        git(root, "rebase", "-q", "main")
        git(root, "checkout", "-q", "main")
        git(root, "merge", "-q", "--ff-only", "feature")
    else:
        git(root, "merge", "-q", "--squash", "feature")
        git(root, "commit", "-qm", "Squashed feature")
    shown = ok(root, "show", "--task", "TASK-1")
    assert "computed: CLOSED" in shown and f"merged into main by {method}" in shown
    assert "computed" in ok(root, "check", "TASK-1").split("Merge:")[1]
    assert f"by {method}" in ok(root, "check")
    ok(root, "check", "--base", base)
    # Git may check receipt files out with CRLF on Windows; the content is what must not change.
    assert {p.name: p.read_bytes().replace(b"\r\n", b"\n")
            for p in (root / ".workorder/receipts/TASK-1").glob("*.json")} == before
    assert not any(e["kind"] == "merged" for e in store.entries(root, "TASK-1"))
    assert not git(root, "status", "--porcelain")


def test_squash_with_changed_lines_needs_renew(local_repo):
    root = local_repo
    feature(root)
    git(root, "checkout", "-q", "main")
    (root / "app.py").write_text("items = ['main change']\n", encoding="utf-8")
    git(root, "commit", "-qam", "Main changes the same line")
    result = subprocess.run(["git", "merge", "--squash", "feature"], cwd=root, capture_output=True)
    assert result.returncode == 1
    (root / "app.py").write_text("items = ['main change', 'tested']\n", encoding="utf-8")
    git(root, "add", "app.py")
    git(root, "commit", "-qm", "Resolve squash")
    refused = cli(root, "check", "TASK-1")
    assert refused.returncode == 1 and "Evidence: DONE" in refused.stdout
    assert "merged code differs from the tested code" in refused.stdout
    assert "workorder renew --task TASK-1 on main" in refused.stdout
    assert "workorder receipt merged --task TASK-1" in refused.stdout
    assert "workorder renew --task TASK-1 on main" in ok(root, "show", "--task", "TASK-1")
    ok(root, "renew", "--task", "TASK-1", "--", PYTHON, "-c", "pass")
    assert "computed: CLOSED" in ok(root, "show", "--task", "TASK-1")


def test_target_merge_is_visible_from_the_feature_branch(local_repo):
    root = local_repo
    feature(root)
    assert "Evidence: DONE" in ok(root, "show", "--task", "TASK-1")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "--no-edit", "feature")
    assert "computed: CLOSED" in ok(root, "show", "--task", "TASK-1")
    git(root, "checkout", "-q", "feature")
    assert "computed: CLOSED" in ok(root, "show", "--task", "TASK-1")
    assert "Merge:" in ok(root, "check", "TASK-1")


@pytest.mark.parametrize("target", ["local", "local-master", "remote-main", "remote-master", "override"])
def test_merge_target_selection(local_repo, target):
    root = local_repo
    feature(root)
    if target == "override":
        git(root, "branch", "release", "main")
        path = root / "tasks/TASK-1.md"
        path.write_text(path.read_text().replace('ships: "repo"', 'ships: "repo"\nmerge_ref: refs/heads/release'), encoding="utf-8")
        git(root, "commit", "-qam", "Choose release")
        git(root, "checkout", "-q", "release")
    else:
        git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", "--no-edit", "feature")
    tip = store.head(root)
    if target == "local-master":
        git(root, "branch", "-m", "main", "master")
    if target in {"remote-main", "remote-master"}:
        git(root, "checkout", "-q", "--detach")
        git(root, "branch", "-D", "main")
        ref = "refs/remotes/origin/" + target.removeprefix("remote-")
        git(root, "update-ref", ref, tip)
    shown = ok(root, "show", "--task", "TASK-1")
    expected = {"local": "main", "local-master": "master", "remote-main": "origin/main",
                "remote-master": "origin/master", "override": "release"}[target]
    assert f"merged into {expected} by ancestry" in shown and "computed: CLOSED" in shown


@pytest.mark.parametrize("ref", ["main", "refs/tags/release", "refs/heads/bad..ref"])
def test_merge_ref_requires_a_full_branch_ref(local_repo, ref):
    path = task(local_repo)
    path.write_text(path.read_text().replace('ships: "repo"', f'ships: "repo"\nmerge_ref: "{ref}"'), encoding="utf-8")
    refused = cli(local_repo, "check")
    assert refused.returncode == 1 and "merge_ref must be a full branch ref" in refused.stdout


def test_live_readbacks_follow_a_computed_squash(local_repo):
    root = local_repo
    feature(root, ships="live", multi=True)
    move_main(root)
    git(root, "merge", "-q", "--squash", "feature")
    git(root, "commit", "-qm", "Squashed")
    assert readback(root, "live").returncode == 1
    assert readback(root, "deployed").returncode == 0
    assert readback(root, "live").returncode == 0
    assert "computed: CLOSED" in ok(root, "show", "--task", "TASK-1")
    ok(root, "check")


def test_metadata_patches_do_not_stand_in_for_missing_code(local_repo):
    root = local_repo
    feature(root, multi=True)
    move_main(root)
    # Bring only the task and its receipts to main, without either code change.
    git(root, "checkout", "feature", "--", "tasks", ".workorder")
    git(root, "commit", "-qm", "Metadata only")
    refused = cli(root, "check", "TASK-1")
    assert refused.returncode == 1 and "merge of the tested code is still owed" in refused.stdout
    assert "Merge:" not in refused.stdout
    # One of two patches is also insufficient for a rebase match.
    git(root, "checkout", "feature", "--", "app.py")
    git(root, "commit", "-qm", "Only the first part")
    assert "Merge:" not in cli(root, "check", "TASK-1").stdout


def test_merge_search_is_bounded_and_cached(local_repo, monkeypatch):
    root = local_repo
    feature(root, multi=True)
    move_main(root)
    git(root, "merge", "-q", "--squash", "feature")
    git(root, "commit", "-qm", "Squash")
    for n in range(220):
        (root / "other.py").write_text(f"unrelated = {n}\n", encoding="utf-8")
        git(root, "commit", "-qam", f"Main {n}")
    first = ok(root, "check", "TASK-1")
    assert "by squash" in first
    cached = list((root / ".git/workorder-digests").glob("merge-*.json"))
    assert cached
    original = store.git
    calls = []
    def counted(root, *args, **kwargs):
        calls.append(args)
        return original(root, *args, **kwargs)
    monkeypatch.setattr(store, "git", counted)
    assert gate.verdict(root, store.record(root, "TASK-1"))[0] == "CLOSED"
    assert not any(args[0] in {"diff", "patch-id"} or (args[0] == "log" and "-p" in args) for args in calls)
    # A tiny search window deliberately misses the squash; a different bound gets a different key.
    monkeypatch.setattr(gate, "MERGE_SEARCH_LIMIT", 1)
    assert gate.computed_merge(root, store.record(root, "TASK-1"), store.entries(root, "TASK-1")[0]) is None


@pytest.mark.parametrize("folder", [".workorder", ".notquite"])
@pytest.mark.parametrize("mode", ["plain", "staged", "base"])
def test_mixed_history_keeps_committed_legacy_prefix(local_repo, folder, mode):
    root = local_repo
    task(root, ships="none")
    first = raw_receipt(root, previous="")
    path = legacy_file(root, [first], folder)
    receipt_file(root, raw_receipt(root, "test", parents=[first["hash"]], at=1))
    git(root, "add", "tasks", ".workorder", folder)
    git(root, "commit", "-qm", "Mixed evidence")
    base = store.head(root)
    assert len(store.entries(root, "TASK-1")) == 2
    path.write_text("", encoding="utf-8")
    if mode == "base":
        git(root, "commit", "-qam", "Remove prefix")
    if mode == "staged":
        git(root, "add", "-u")
    args = ["--base", base] if mode == "base" else ["--staged"] if mode == "staged" else []
    refused = cli(root, "check", *args)
    assert refused.returncode == 1
    assert "missing" in refused.stdout or "removed or changed" in refused.stdout


def test_staged_file_receipt_failure_selects_its_task(local_repo):
    root = local_repo
    task(root, ships="none", status="DONE")
    first = raw_receipt(root)
    tested = raw_receipt(root, "test", parents=[first["hash"]], at=1)
    for entry in (first, tested):
        receipt_file(root, entry)
    commit_receipts(root)
    ok(root, "check")
    failure = raw_receipt(root, "test", parents=[tested["hash"]], at=2, exit_code=9, valid=False)
    path = receipt_file(root, failure)
    git(root, "add", path.relative_to(root).as_posix())
    path.unlink()  # index content must decide, not the checkout's passing test
    refused = cli(root, "check", "--staged")
    assert refused.returncode == 1 and "passing test on" in refused.stdout


def test_base_finds_removed_files_even_when_task_is_removed(local_repo):
    root = local_repo
    task(root, ships="none")
    receipt = receipt_file(root, raw_receipt(root))
    base = commit_receipts(root)
    receipt.unlink()
    (root / "tasks/TASK-1.md").unlink()
    git(root, "commit", "-qam", "Remove task and evidence")
    refused = cli(root, "check", "--base", base)
    assert refused.returncode == 1 and "receipts recorded on" in refused.stdout


def test_repair_preserves_test_contract_references(local_repo):
    root = local_repo
    task(root, ships="none", status="DONE", contract=".workorder/contract.json")
    first = raw_receipt(root, previous="")
    a = raw_receipt(root, "test", previous=first["hash"], at=1)
    b = raw_receipt(root, "test", previous=first["hash"], at=2)
    path = legacy_file(root, [first])
    contract = root / ".workorder/contract.json"
    contract.write_text(json.dumps({"task": "TASK-1", "criteria": [{"id": "C1", "ask": "Works",
        "critical": True, "requires": "REAL_OR_TEST", "evidence": [{"provenance": "TEST",
        "ref": "receipt:" + a["hash"]}]}]}), encoding="utf-8")
    path.write_text(path.read_text() + "<<<<<<< HEAD\n" + store.packed(a) + "\n=======\n"
                    + store.packed(b) + "\n>>>>>>> side\n", encoding="utf-8")
    ok(root, "repair", "--task", "TASK-1")
    ok(root, "check")
    assert len(store.entries(root, "TASK-1")) == 3


def test_repair_copy_cannot_hide_a_legacy_edit(local_repo):
    root = local_repo
    task(root, ships="none")
    first = raw_receipt(root, previous="")
    old_test = raw_receipt(root, "test", previous=first["hash"], at=1)
    legacy_file(root, [first, old_test])
    base = commit_receipts(root)
    legacy_file(root, [first])
    changed = raw_receipt(root, "test", parents=[first["hash"]], at=1, legacy=old_test,
                          exit_code=4, valid=False)
    receipt_file(root, changed)
    refused = cli(root, "check", "--base", base)
    assert refused.returncode == 1 and "differs from its legacy object" in refused.stdout


def test_same_code_is_recognised_with_missing_original_commits(local_repo, tmp_path):
    root = local_repo
    feature(root)
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--squash", "feature")
    git(root, "commit", "-qm", "Same code squash")
    git(root, "branch", "-D", "feature")
    fresh = tmp_path / "fresh"
    git(tmp_path, "clone", "-q", "--no-local", str(root), str(fresh))
    assert all(e["missing"] for e in store.entries(fresh, "TASK-1"))
    shown = ok(fresh, "show", "--task", "TASK-1")
    assert "computed: CLOSED" in shown and "by same code" in shown
    assert "Test:     PASS" in ok(fresh, "check", "TASK-1")


def test_pushed_main_precedes_local_main_and_target_movement_invalidates_cache(local_repo):
    root = local_repo
    feature(root)
    git(root, "checkout", "-q", "main")
    git(root, "update-ref", "refs/remotes/origin/main", "main")
    # Local main has the merge; the pushed branch does not have it yet.
    git(root, "merge", "-q", "--no-ff", "--no-edit", "feature")
    shown = ok(root, "show", "--task", "TASK-1")
    assert "merge of the tested code" in shown and "merged into" not in shown
    # Pushing moves the local copy of the remote branch; the cached "not merged" must not stick.
    git(root, "update-ref", "refs/remotes/origin/main", "main")
    shown = ok(root, "show", "--task", "TASK-1")
    assert "merged into origin/main by ancestry" in shown and "computed: CLOSED" in shown


def test_receipt_fields_and_code_are_verified_in_new_files(local_repo):
    root = local_repo
    task(root, ships="none")
    path = receipt_file(root, raw_receipt(root, code="f" * 64))
    refused = cli(root, "check")
    assert refused.returncode == 1 and "code does not match its commit" in refused.stdout
    path.unlink()
    receipt_file(root, raw_receipt(root, "test", command=[]))
    refused = cli(root, "check")
    assert refused.returncode == 1 and "command receipt needs its arguments" in refused.stdout


def test_computed_merge_keeps_readbacks_before_a_later_merge_receipt(local_repo):
    root = local_repo
    feature(root, ships="live")
    move_main(root)
    git(root, "merge", "-q", "--no-ff", "--no-edit", "feature")
    assert readback(root, "deployed").returncode == 0
    assert readback(root, "live").returncode == 0
    store.append(root, "TASK-1", dict(kind="merged", commit=store.head(root),
                 code=store.code_digest(root, store.head(root)), ref="refs/heads/main"))
    assert "computed: CLOSED" in ok(root, "show", "--task", "TASK-1")
    ok(root, "check")
