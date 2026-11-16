"""Derive completion from receipts; a status line never supplies missing proof."""

from __future__ import annotations

import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import configparser
import copy
import difflib
import fnmatch
import hashlib
import io
import json
import os
import ntpath
from pathlib import Path, PureWindowsPath
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tokenize
import uuid

from . import store as s
from . import pytest_selection
from . import source


def contract_debt(root, record, history, implemented, staged=False):
    path = record["contract"]
    if path == "none":
        return []
    try:
        contract = json.loads(s.read_text(root, path, staged))
    except json.JSONDecodeError as error:
        raise s.Refused(f"{record['id']}: contract {path} is not valid JSON"
                        f" ({error.msg}, line {error.lineno})") from None
    if not isinstance(contract, dict) or contract.get("task") != record["id"]:
        raise s.Refused("contract must name its task")
    criteria = contract.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise s.Refused("contract needs a nonempty criteria list")
    debts, seen = [], set()
    for item in criteria:
        if not isinstance(item, dict):
            raise s.Refused("criterion must be an object")
        cid = item.get("id")
        if not isinstance(cid, str) or not cid or cid in seen:
            raise s.Refused("criterion ids must be distinct nonempty strings")
        seen.add(cid)
        if (not isinstance(item.get("ask"), str) or not item["ask"].strip()
                or type(item.get("critical")) is not bool
                or item.get("requires") not in {"REAL", "REAL_OR_TEST"}
                or not isinstance(item.get("evidence"), list)):
            raise s.Refused(f"{cid}: need ask, critical, requires and evidence")
        proved = False
        for evidence in item["evidence"]:
            if (not isinstance(evidence, dict)
                    or evidence.get("provenance") not in {"REAL", "TEST", "MOCK", "SYNTHETIC", "UNKNOWN"}
                    or not isinstance(evidence.get("ref"), str) or not evidence["ref"]):
                raise s.Refused(f"{cid}: every evidence item needs provenance and ref")
            provenance = evidence["provenance"]
            if provenance == "REAL":
                try:
                    artifact = s.read_bytes(root, evidence["ref"], staged)
                except (OSError, s.Refused):
                    continue  # a missing evidence file proves nothing; the criterion stays owed
                proved |= s.digest(artifact) == evidence.get("sha256")
            elif provenance == "TEST" and item["requires"] != "REAL":
                proved |= any(evidence["ref"] in {"receipt:" + entry["hash"],
                                                  "receipt:" + entry.get("legacy", {}).get("hash", "")}
                              and entry["kind"] == "test" and entry["exit_code"] == 0
                              and entry["valid"] and implemented is not None and not entry["missing"]
                              and entry["code"] == implemented["code"] for entry in history)
        if item["critical"] and not proved:
            debts.append(f"contract {cid}: {item['requires']} evidence")
    return debts


ANY_TEST = "any"


def declared_test(record):
    """The task's own test command as an argument list, or None when any command counts: no test
    declared (which DONE refuses) or the explicit opt-out test: any."""
    value = record.get("test", "").strip()
    return s.split_command(value) if value and value != ANY_TEST else None


def counting_runs(record, current, implemented):
    """Test runs that can decide: on the implemented code, on a commit in this clone, and the task's
    declared test command when it has one."""
    declared = declared_test(record)
    return [e for e in current if e["kind"] == "test" and not e["missing"]
            and e["code"] == implemented["code"] and (declared is None or e["command"] == declared)]


def carries(root, implemented, item):
    """A merge or deployment carries the tested code when it holds exactly that code, or when its commit
    contains the implemented commit (a merge commit or fast-forward, with other work beside it)."""
    return (implemented is not None and not implemented["missing"] and not item["missing"]
            and (item["code"] == implemented["code"]
                 or s.contains(root, implemented["commit"], item["commit"])))


def code_seen(root, tid, code, staged=False):
    """A commit in this clone that holds exactly this code: HEAD, or a commit that touched the task or
    its receipts (a squash merge does). None when there is none."""
    candidates = ["HEAD"] + s.git(root, "log", "--format=%H", "-n", "50", "HEAD", "--", f"tasks/{tid}.md",
                                  s.ledger_path(tid), f".workorder/receipts/{tid}",
                                  f".notquite/receipts/{tid}.jsonl", required=False).stdout.split()
    for commit in candidates:
        found = s.git(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", required=False)
        if found.returncode == 0 and s.code_digest(root, found.stdout.strip()) == code:
            return found.stdout.strip()
    return None


def recognise_squashed(root, tid, history, staged=False):
    """Receipts whose commits are gone (squash, rebase, amend) count again when a commit in this clone
    holds exactly the code they recorded: the evidence is about that code, and the code is here."""
    implemented = newest_implementation(history)
    if implemented is None or not implemented["missing"]:
        return
    seen = code_seen(root, tid, implemented["code"], staged)
    if not seen:
        target = merge_target(root, s.record(root, tid, staged))
        merged = merge_match(root, implemented, target[1]) if target else None
        if merged and merged["method"] in {"same code", "recorded paths"}:
            seen = merged["commit"]
        elif target and recorded_branch_code(root, implemented, target[1], s.head(root)):
            seen = s.head(root)
    if seen:
        for item in history:
            if item["missing"] and item["code"] == implemented["code"]:
                item["missing"], item["seen_on"] = False, seen


# Limit both sides of a patch comparison. An incomplete search never proves a rebase.
MERGE_SEARCH_LIMIT = 1000


def merge_target(root, record):
    # The pushed branch first: a commit only on local main is not merged for anyone else yet.
    override = record.get("merge_ref")
    refs = [override] if override else ["refs/remotes/origin/main", "refs/remotes/origin/master",
                                      "refs/heads/main", "refs/heads/master"]
    for ref in refs:
        found = s.git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", required=False)
        if found.returncode == 0:
            return ref, found.stdout.strip()
    return None


def patch_ids(root, revision, commits=None):
    """git cherry semantics, restricted to code, in two Git calls for the whole range."""
    selection = (["--no-walk=unsorted", "--diff-merges=first-parent", *commits] if commits is not None
                 else ["--no-merges", f"--max-count={MERGE_SEARCH_LIMIT + 1}", revision])
    if commits == []:
        return {}
    patches = s.git(root, "log", *selection, "--format=commit %H", "-p", "--binary", "--no-renames",
                    "--no-ext-diff", "--no-textconv", "--", *s.CODE_PATHS).stdout
    result = s.git(root, "patch-id", "--stable", data=patches).stdout
    return {commit: patch for patch, commit in (line.split() for line in result.splitlines())}


def merge_match(root, implemented, target):
    """Evidence from immutable commits, cached independently of branch names and HEAD."""
    original = implemented["commit"]
    key = s.digest(s.packed(["merge-v7", implemented.get('task'), implemented.get('hash'),
                            original, s.has_commit(root, original), implemented["code"],
                            implemented.get('implementation_paths'), implemented.get('implementation_base'),
                            target, MERGE_SEARCH_LIMIT]).encode())
    folder = s._cache_dir(root)
    cached = folder / ("merge-" + key + ".json") if folder else None
    try:
        if cached and cached.is_file():
            value = json.loads(cached.read_text(encoding="utf-8"))
            if value is None or (isinstance(value, dict) and value.get("method") in
                                 {"ancestry", "same code", "squash", "rebase", "recorded paths"} and s.has_commit(root, value.get("commit", ""))):
                return value
    except (OSError, ValueError):
        pass
    result = _merge_match(root, implemented, target)
    try:
        if cached:
            folder.mkdir(exist_ok=True)
            # Atomic replacement keeps concurrent checks from reading a partial cache file.
            temporary = cached.with_suffix("." + uuid.uuid4().hex)
            temporary.write_text(s.packed(result), encoding="utf-8")
            temporary.replace(cached)
    except OSError:
        pass
    return result


def _merge_match(root, implemented, target):
    original = implemented["commit"]
    present = s.has_commit(root, original)
    if not present and implemented.get('implementation_paths'):
        tid = implemented['task']
        candidates = s.git(root, 'log', f'--max-count={MERGE_SEARCH_LIMIT}', '--format=%H', target,
                           '--', f'tasks/{tid}.md', f'.workorder/receipts/{tid}').stdout.split()
        for commit in reversed(candidates):
            # The landing must carry this receipt, not merely happen to contain similar files.
            texts = s.metadata_texts(root, commit, tid)
            if not any(implemented['hash'] in text for path, text in texts.items() if s.receipt_task(path) == tid):
                continue
            tree = s.code_tree(root, commit)
            if recorded_paths_match(root, tree, implemented):
                method = 'same code' if s.code_digest(root, commit) == implemented['code'] else 'recorded paths'
                return {'method': method, 'commit': commit}
    if present and s.contains(root, original, target):
        # Name the landing, not a moving tip that may have undone the tested code.
        mainline = s.git(root, "rev-list", "--first-parent", target).stdout.split()
        if original in mainline:
            landing = original
        else:
            descendants = set(s.git(root, "rev-list", "--ancestry-path", f"{original}..{target}").stdout.split())
            landing = next(commit for commit in reversed(mainline) if commit in descendants)
        return {"method": "ancestry", "commit": landing}
    base = s.git(root, "merge-base", original, target, required=False).stdout.strip() if present else ""
    revision = f"{base}..{target}" if base else target
    # Only code-changing commits can supply a same-code or patch match.
    candidates = s.git(root, "rev-list", f"--max-count={MERGE_SEARCH_LIMIT}", revision).stdout.split()
    target_patches = patch_ids(root, revision, candidates)
    for commit in reversed(target_patches):
        if s.code_digest(root, commit) == implemented["code"]:
            return {"method": "same code", "commit": commit}
    if not base:
        return None
    combined = s.git(root, "diff", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv",
                     base, original, "--", *s.CODE_PATHS).stdout
    combined_id = s.git(root, "patch-id", "--stable", data=combined).stdout.split()
    if combined_id:
        for commit, patch in reversed(list(target_patches.items())):
            if patch == combined_id[0]:
                # One replayed code commit also matches the combined diff. Call that a
                # rebase; squash describes a landing that combines several code commits.
                feature = s.git(root, "rev-list", f"--max-count={MERGE_SEARCH_LIMIT + 1}",
                                f"{base}..{original}").stdout.split()
                if len(feature) <= MERGE_SEARCH_LIMIT:
                    feature_patches = patch_ids(root, f"{base}..{original}")
                    merges = s.git(root, "rev-list", "--merges", f"{base}..{original}",
                                   "--", *s.CODE_PATHS).stdout.split()
                    if feature_patches == {original: patch} and not merges:
                        return {"method": "rebase", "commit": commit}
                return {"method": "squash", "commit": commit}
    # Count all commits (including merges and metadata) so truncation cannot look complete.
    feature = s.git(root, "rev-list", f"--max-count={MERGE_SEARCH_LIMIT + 1}", f"{base}..{original}").stdout.split()
    if len(feature) > MERGE_SEARCH_LIMIT:
        return None
    feature_patches = patch_ids(root, f"{base}..{original}")
    # A merge's own resolution is not represented by git cherry; do not silently skip it.
    merges = s.git(root, "rev-list", "--merges", f"{base}..{original}", "--", *s.CODE_PATHS).stdout.split()
    available = set(target_patches.values())
    if not merges and feature_patches and set(feature_patches.values()) <= available:
        representatives = {patch: commit for commit, patch in target_patches.items()
                           if patch in set(feature_patches.values())}
        matched = next(commit for commit in target_patches if commit in set(representatives.values()))
        if not all(s.contains(root, representatives[patch], matched) for patch in feature_patches.values()):
            matched = target  # patches on separate merged branches must all be present in a deployment
        return {"method": "rebase", "commit": matched}
    return None


def computed_merge(root, record, implemented):
    if record["ships"] == "none" or implemented is None:
        return None
    target = merge_target(root, record)
    if target is None:
        return None
    ref, commit = target
    # Always inspect the selected target. HEAD on a feature branch is never a fallback target.
    match = merge_match(root, implemented, commit)
    if match:
        return {**match, "ref": ref, "target": commit}
    return None


def merge_line(merged):
    ref = merged["ref"].removeprefix("refs/heads/").removeprefix("refs/remotes/")
    return f"merged into {ref} by {merged['method']} {merged['commit'][:7]}, computed"


def delivered_code(root, implemented, item, computed=None):
    return carries(root, implemented, item) or bool(
        computed and not item["missing"] and s.contains(root, computed["commit"], item["commit"]))


def verdict(root, record, staged=False, *, allow_no_code_change=False):
    tid = record["id"]
    history = s.entries(root, tid, staged)
    recognise_squashed(root, tid, history, staged)
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    implemented = history[index] if index >= 0 else None
    current = history[index + 1:] if implemented else []
    done = []
    if not record.get("test", "").strip():
        done.append("a declared test")  # the command that proves the task, or test: any
    if not implemented:
        done.append("implementation")
    elif implemented["missing"]:
        done.append(f"implementation (commit {implemented['commit'][:7]} is not in this clone)")
    else:
        # The latest test run on the implemented code decides; a later failure revokes an earlier pass.
        # Code leaves out tasks/ and .workorder/, so committing the receipts does not hide later runs.
        runs = counting_runs(record, current, implemented)
        if runs and runs[-1].get('no_code_change') is True:
            base = runs[-1].get('no_code_change_base') or implemented.get('implementation_base')
            if not base or not s.has_commit(root, base):
                base, _ = implementation_base(root, record)
            problem = no_code_change_problem(root, record, base,
                                             end=implemented.get('seen_on', implemented['commit']))
            if problem and not allow_no_code_change:
                done.append(reviewer_label(ReviewProblem(problem, f'tasks/{tid}.md', label_only=True), tid))
        if runs and runs[-1].get('no_tests_passed'):
            done.append(f"its test ran but no test passed ({runs[-1]['no_tests_passed']})")
        elif not runs or runs[-1]["exit_code"] != 0 or not runs[-1]["valid"]:
            done.append(f"a passing test on {implemented['commit'][:7]}")
    done += contract_debt(root, record, current, implemented, staged)
    delivery = []
    computed = computed_merge(root, record, implemented)
    if record["ships"] != "none":
        # Any merge receipt that carries the tested code counts; a later one that does not, cannot undo it.
        merges = [i for i, e in enumerate(current) if e["kind"] == "merged" and carries(root, implemented, e)]
        merge_index = merges[-1] if merges else -1
        merged = current[merge_index] if merges else computed
        if not merged:
            delivery.append("merge of the tested code")
        if record["ships"] == "live":
            after_merge = current if computed else current[merge_index + 1:] if merged else []
            deploy_index = max((i for i, e in enumerate(after_merge) if e["kind"] == "deployed"), default=-1)
            deployed = after_merge[deploy_index] if deploy_index >= 0 else None
            if (not deployed or not deployed["valid"] or deployed["exit_code"] != 0
                    or not delivered_code(root, implemented, deployed, computed)):
                delivery.append("deployment read-back of the tested code")
            after_deploy = after_merge[deploy_index + 1:] if deployed else []
            if not deployed or not any(e["kind"] == "live" and e["valid"] and e["exit_code"] == 0
                                       and not e["missing"] and e["commit"] == deployed["commit"]
                                       and e["target"] == deployed["target"] for e in after_deploy):
                delivery.append("live read-back after deployment")
    computed = "IN_PROGRESS" if done else "DONE" if delivery else "CLOSED"
    return computed, done, delivery


def task_names(root, staged=False):
    """Ids of the task files: tasks/<ID>.md. Other files there (a README, notes, subfolders) are not tasks."""
    names = []
    for path in s.files(root, "tasks", staged):
        name = path.removeprefix("tasks/")
        if name.endswith(".md") and "/" not in name and s.ID.fullmatch(name[:-3]):
            names.append(name[:-3])
    return names


def records(root, staged=False, errors=None):
    """Task records by id. With errors (a dict), a task file that cannot be read is reported there and
    left out, so one bad file does not stop the others; without it, the first bad file is refused."""
    result = {}
    for tid in task_names(root, staged):
        try:
            result[tid] = s.record(root, tid, staged)
        except s.Refused as error:
            if errors is None:
                raise
            errors[tid] = str(error)
    return result


def waits(record, all_records, views, seen=frozenset()):
    """Open dependencies: person waits, and tasks that are not finished."""
    result = []
    for wait in s.dependencies(record):
        kind, value = wait.split(":", 1)
        if kind == "person" or not finished(value, all_records, views, seen):
            result.append(wait)
    return result


def finished(tid, all_records, views, seen=frozenset()):
    """A dependency is finished when its evidence computes DONE or CLOSED and its own dependencies are
    finished, all the way down. A cycle never counts as finished."""
    # Finished means the task says DONE or CLOSED and its evidence supports that: a reopened task
    # (status set back to IN_PROGRESS) holds back the tasks that depend on it, as does a cancelled one.
    if (tid not in all_records or tid in seen or views[tid][0] not in {"DONE", "CLOSED"}
            or all_records[tid]["status"] not in {"DONE", "CLOSED"}):
        return False
    return not waits(all_records[tid], all_records, views, seen | {tid})


@s.cached_read
def fields_at(root, tid, rev="HEAD"):
    """The front matter of tasks/<tid>.md at a revision, or None if it has no readable record there."""
    text = s.metadata_texts(root, rev, tid if rev != "HEAD" else None, False).get(f"tasks/{tid}.md")
    if text is None:
        return None
    lines = text.splitlines()
    try:
        return s.front_matter(tid, lines[1:lines.index("---", 1)])
    except (s.Refused, ValueError):
        return None


def committed_status(root, tid, rev="HEAD"):
    """The status line of tasks/<tid>.md at a revision, or None if it has no readable record there."""
    fields = fields_at(root, tid, rev)
    return fields.get("status") if fields else None


def newest_implementation(history):
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    return history[index] if index >= 0 else None


def base_problems(root, base, staged=False, retired=frozenset()):
    """Receipts committed on the base revision must still be there, unchanged, as a prefix."""
    found = s.git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", required=False)
    if found.returncode:
        raise s.Refused(f"base {base} is not in this clone; fetch it"
                        " (in GitHub Actions: actions/checkout with fetch-depth: 0)")
    commit = found.stdout.strip()
    problems = []
    tids = {s.receipt_task(p) for p in s.receipt_paths(root, rev=commit)} - {None}
    for tid in sorted(tids):
        if tid in retired:
            continue
        before = s.receipt_texts(root, tid, rev=commit)
        after = s.receipt_texts(root, tid, staged)
        try:
            history = s.ordered_entries(root, tid, after)
        except (s.Refused, ValueError):
            history = None
        if s.receipt_changes(root, tid, before, after, history):
            problems.append(f"{tid}: receipts recorded on {base} were removed or changed")
    # Endpoints miss a receipt added and then hidden, or edited and restored, in this range.
    changed = s.git(root, "log", "--format=", "--name-only", "-z", "--diff-filter=DMT",
                    "--no-renames", "-m", "--full-history", f"{commit}..HEAD", "--",
                    *s.RECEIPT_DIRS).stdout
    for path in sorted(set(changed.split("\0")) - {""}):
        tid = s.receipt_task(path)
        if tid and tid not in retired:
            problems.append(f"{tid}: committed receipt file removed or changed since {base}: {path}")
    return problems


def implementation_at(root, rev, tid):
    """Use the same receipt order for historical claims as for current verdicts."""
    return newest_implementation(s.entries(root, tid, rev=rev))


FINISHED = {"DONE", "CLOSED"}
ACCEPTANCE_NOTE = ("the evidence covers the task's test only, not its Acceptance list;"
                   " finish every Acceptance item before claiming DONE")
UNCLAIMED_OWES = "its Acceptance items, then status DONE (the evidence for its test is in place)"


def owes(record, owed, delivery=()):
    """What a task still owes. A task nobody has claimed finished is never 'owing nothing'."""
    items = list(owed)
    if not items and record["status"] not in FINISHED | {"CANCELLED"}:
        items.append(UNCLAIMED_OWES)
    return ", ".join(items + list(delivery)) or "nothing"


def unfinished_headline(record, done, delivery, open_waits):
    debt = done + delivery + open_waits
    detail = 'owes: ' + ', '.join(debt) if debt else 'its test evidence is in place'
    return f"{record['id']} {record['status']} (not claimed finished); {detail}"


@s.cached_read
def task_history(root, base=None):
    """One history walk for a linear task history. Merge histories keep Git's per-path simplification."""
    log = s.git(root, "log", "--full-history", "--format=commit %H %P", "--raw", "--no-abbrev",
                "--no-renames", f"{base}..HEAD" if base else "HEAD", "--", "tasks").stdout
    changes, commit = {}, None
    for line in log.splitlines():
        if line.startswith("commit "):
            words = line.split()
            if len(words) > 3:
                return None  # a merge needs the same path-specific history used by earlier releases
            commit = words[1]
        elif line.startswith(":") and "\t" in line:
            info, path = line.split("\t", 1)
            tid = path.removeprefix("tasks/").removesuffix(".md")
            if path == f"tasks/{tid}.md" and s.ID.fullmatch(tid):
                oid = info.split()[3]
                changes.setdefault(tid, []).append((commit, oid))
    blobs = s.read_blobs(root, {oid for items in changes.values() for _, oid in items if set(oid) != {"0"}})
    result = {}
    for tid, items in changes.items():
        statuses = []
        for commit, oid in items:
            lines = blobs.get(oid, "").splitlines()
            try:
                fields = s.front_matter(tid, lines[1:lines.index("---", 1)])
            except (ValueError, s.Refused):
                fields = {}
            statuses.append((commit, fields.get("status")))
        result[tid] = statuses
    return result


def inherited_claim(root, record, staged=False, rev=None):
    """An unchanged task and its receipts imported from a target or merge parent add no claim."""
    tid = record['id']
    target = merge_target(root, record)
    sources = [target[1]] if target else []
    if rev:
        parents = s.git(root, 'rev-list', '--parents', '-n', '1', rev).stdout.split()[1:]
        previous = parents[0] if parents else None
        if len(parents) > 1:
            sources += parents
    else:
        previous = s.head(root)
        merge_head = root / s.git(root, 'rev-parse', '--git-path', 'MERGE_HEAD').stdout.strip()
        if merge_head.is_file():
            sources += merge_head.read_text(encoding='ascii').split()
    # Reopening and then restoring an old status is a new claim, even if its
    # metadata equals an earlier landing already in this branch's history.
    sources = [source for source in sources if not previous or not s.contains(root, source, previous)]
    if rev:
        # A target containing this very claim cannot justify the claim itself.
        sources = [source for source in sources if not s.contains(root, rev, source)]
        current = s.metadata_texts(root, rev, tid)
    else:
        current = {f'tasks/{tid}.md': s.read_text(root, f'tasks/{tid}.md', staged),
                   **s.receipt_texts(root, tid, staged)}
    paths = [f'tasks/{tid}.md', *[f'{folder}/{tid}.jsonl' for folder in s.RECEIPT_DIRS],
             *[f'{folder}/{tid}' for folder in s.RECEIPT_DIRS]]
    for source in dict.fromkeys(sources):
        if current != s.metadata_texts(root, source, tid):
            continue
        # A moving target may have new code but still carry an already imported
        # task. Use the task/receipt version's own commit to distinguish a reopen.
        introduced = s.git(root, 'log', '-1', '--format=%H', source, '--', *paths).stdout.strip()
        if introduced and (not previous or not s.contains(root, introduced, previous)):
            return True
    return False


def stale_claim(root, record, staged=False, base=None):
    """A DONE or CLOSED claim must be made on the code of the implementation it relies on.

    An uncommitted claim is compared with the code being committed (the index under --staged, HEAD
    otherwise). A committed claim is found in the task file's history (since base, when given) and
    compared with the code of the commit that made it, unless the evidence was renewed after it.
    """
    tid, status = record["id"], record["status"]
    if status not in FINISHED:
        return None
    current = newest_implementation(s.entries(root, tid, staged))
    if current is None or current["missing"]:
        return None  # the verdict already reports these
    short = current["commit"][:7]
    if committed_status(root, tid) != status:
        if inherited_claim(root, record, staged):
            return None
        dirty = [] if staged else s.dirty_paths(root)
        if dirty:
            return (f"NOT DONE  {tid} is being marked {status} with uncommitted code changes; "
                    + s.describe_dirty(dirty) + f". Commit, record workorder receipt implemented --task {tid}"
                    " and test again.")
        tree = s.git(root, "write-tree").stdout.strip() if staged else s.head(root)
        if claim_matches(root, record, current, s.head(root), tree, staged):
            return None
        where = "in this commit" if staged else "now"
        return (f"NOT DONE  {tid} is being marked {status}, but the code {where} differs from the"
                f" implemented commit {short}. Run workorder renew --task {tid} on the branch before pushing.")
    histories = task_history(root, base)
    if histories is None:
        commits = s.git(root, "log", "--format=%H", f"{base}..HEAD" if base else "HEAD", "--",
                        f"tasks/{tid}.md").stdout.split()
        statuses = ((commit, committed_status(root, tid, commit)) for commit in commits)
    else:
        statuses = histories.get(tid, [])
    claim = None
    for commit, status_at_commit in statuses:  # newest first: find the commit that made the claim
        if status_at_commit != status:
            break
        claim = commit
    else:
        if base and committed_status(root, tid, base) == status:
            claim = None  # claimed before the base
    if claim is None:
        return None
    relied_on = implementation_at(root, claim, tid)
    if relied_on is None or current["hash"] != relied_on.get("hash"):
        return None
    if claim_matches(root, record, current, claim, receipt_rev=claim):
        return None
    if inherited_claim(root, record, rev=claim):
        return None
    return (f"NOT DONE  {tid} was marked {status} in {claim[:7]} on code other than the implemented"
            f" commit {short}. Run workorder renew --task {tid} on the branch before pushing.")


def claim_matches(root, record, implemented, claim, tree=None, staged=False, receipt_rev=None):
    tree = tree or claim
    # This concerns the claim's history, even when the task ships nothing.
    target = merge_target(root, record)
    merged = merge_match(root, implemented, target[1]) if target else None
    if merged and s.contains(root, merged["commit"], claim):
        return unchanged_implementation_paths(root, implemented, merged, tree, staged, receipt_rev)
    # A commit advances its checked-out local target, but never origin/main.
    # Compare against that eventual target tree, independently of SQUASH_MSG.
    target_tree = None
    parents = []
    if staged and target:
        if target[0] == s.git(root, 'symbolic-ref', '-q', 'HEAD', required=False).stdout.strip():
            target_tree = tree
        merge_head = root / s.git(root, 'rev-parse', '--git-path', 'MERGE_HEAD').stdout.strip()
        if merge_head.is_file():
            parents = merge_head.read_text(encoding='ascii').split()
    if target and recorded_branch_code(root, implemented, target[1], claim, tree,
                                       base_tree=target_tree, parents=parents):
        return True
    if s.code_digest(root, tree) == implemented["code"] or same_source_code(root, implemented['commit'], tree):
        return True
    if merged and s.contains(root, claim, merged["commit"]):
        # Landing must not invalidate a claim whose patch matched before it landed.
        before = s.git(root, "rev-parse", "--verify", "--quiet", f"{merged['commit']}^1",
                       required=False).stdout.strip()
        if before and equivalent_claim(root, record, implemented, tree, before):
            return True
    return tree == claim and equivalent_claim(root, record, implemented, claim)


def implementation_paths(root, implemented, base, staged=False, receipt_rev=None):
    """The net changed paths contributed by this task's current implementation period."""
    original, tid = implemented["commit"], implemented["task"]
    paths = set(s.git(root, "diff", "--name-only", "-z", "--no-renames", base, original,
                      "--", *s.CODE_PATHS).stdout.split("\0")) - {""}
    paths = {path for path in paths if not s.generated_path(path)}
    paths = source.changed(root, paths, base, after=original)
    # A committed claim keeps the task boundaries recorded when it was made.
    history = s.entries(root, tid, staged, rev=receipt_rev)
    index = next((i for i, item in enumerate(history) if item["hash"] == implemented["hash"]), len(history))
    previous = next((item["commit"] for item in reversed(history[:index])
                     if item["kind"] == "implemented" and item["commit"] != original
                     and s.contains(root, item["commit"], original)), None)
    others = set()
    owners = {s.receipt_task(p) for p in s.receipt_paths(root, staged, rev=receipt_rev)} - {None, tid}
    for owner in sorted(owners):
        try:
            others.update(item["commit"] for item in s.entries(root, owner, staged, rev=receipt_rev)
                          if item["kind"] == "implemented")
        except s.Refused:
            # An unreadable task's receipts cannot narrow another task's protection.
            continue
    if not previous and not others:
        return paths
    revisions = [f"{base}..{original}"] + ([f"^{previous}"] if previous else [])
    commits = s.git(root, "rev-list", *revisions).stdout.split()
    # A shared implementation commit still belongs to the task being checked.
    commits = [commit for commit in commits if commit == original or commit not in others]
    if not commits:
        return set()
    contributed = s.git(root, "log", "--no-walk=unsorted", "--format=", "--name-only", "-z",
                        "--diff-merges=first-parent", "--no-renames", "--no-ext-diff", "--no-textconv",
                        *commits, "--", *s.CODE_PATHS).stdout
    # Intersect with the net diff to preserve the single-task behaviour for reverted work.
    # Select contributions by commit, not by subtracting another task's path names:
    # two tasks may legitimately change the same file.
    return paths & set(contributed.split("\0"))


def unchanged_implementation_paths(root, implemented, merged, claim, staged=False, receipt_rev=None):
    """Only changes on implementation paths can invalidate a claim after its landing."""
    original, landing = implemented["commit"], merged["commit"]
    if claim == landing:
        return True
    changed = s.git(root, "diff", "--name-only", "-z", "--no-renames", landing, claim,
                    "--", *s.CODE_PATHS).stdout
    if not changed:
        return True
    reference = landing
    if merged["method"] == "ancestry":
        reference = s.git(root, "rev-parse", "--verify", "--quiet", f"{landing}^1",
                          required=False).stdout.strip()
    base = (s.git(root, "merge-base", original, reference, required=False).stdout.strip()
            if reference and s.has_commit(root, original) else "")
    if landing == original:
        # A fast-forward has no merge commit recording the branch boundary. Use the
        # task's current open period, or the implementation's parent when the task
        # was introduced later. This includes earlier commits of a multi-commit task.
        commits = s.git(root, "log", "--format=%H", original, "--", f"tasks/{implemented['task']}.md").stdout.split()
        for commit in commits:
            status = committed_status(root, implemented["task"], commit)
            if status is None or status in FINISHED | {"CANCELLED"}:
                break
            base = commit
    if implemented.get('implementation_paths'):
        paths = set(implemented['implementation_paths'])
    elif base and base != original:
        paths = implementation_paths(root, implemented, base, staged, receipt_rev)
    else:
        # Without a branch boundary (or the original history), the recorded tree is the
        # evidence available. Include deleted paths too, so restoring one cannot escape.
        paths = s.git(root, "ls-tree", "-r", "--name-only", "-z", landing).stdout
        paths = "\0".join(p for p in paths.split("\0")
                          if not p.startswith(("tasks/", ".workorder/", ".notquite/")))
        paths += s.git(root, "diff-tree", "--root", "--no-commit-id", "-r", "--name-only", "-z",
                       "--no-renames", landing, "--", *s.CODE_PATHS).stdout
        paths = set(paths.split("\0")) - {""}
    meaningful = source.changed(root, set(changed.split('\0')) - {''}, landing, after=claim)
    return not ({path for path in paths if not s.generated_path(path)} & meaningful)


def equivalent_claim(root, record, implemented, claim, target_commit=None):
    """A rebase preserves the code patch relative to each commit's own target merge base."""
    if target_commit is None:
        target = merge_target(root, record)
        if target is None:
            return False
        target_commit = target[1]
    patches = []
    for commit in (implemented["commit"], claim):
        base = s.git(root, "merge-base", commit, target_commit, required=False).stdout.strip()
        if not base:
            return False
        diff = s.git(root, "diff", "--binary", "--no-renames", "--no-ext-diff", "--no-textconv",
                     base, commit, "--", *s.CODE_PATHS).stdout
        patch = s.git(root, "patch-id", "--stable", data=diff).stdout.split()
        if not patch:
            return False  # an empty change cannot excuse a claim on different code
        patches.append(patch[0])
    return patches[0] == patches[1]


def unpushed_target(root, record):
    target = merge_target(root, record)
    if target is None or not target[0].startswith("refs/remotes/"):
        return None
    remote, branch = target[0].removeprefix("refs/remotes/").split("/", 1)
    local = s.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}",
                  required=False)
    implemented = newest_implementation(s.entries(root, record["id"]))
    if (local.returncode == 0 and implemented and not merge_match(root, implemented, target[1])
            and merge_match(root, implemented, local.stdout.strip())):
        return f"push {branch} to {remote}: git push {remote} {branch}"
    return None


@s.cached_read
def raw_changed_paths(root, reference, staged=False):
    """Paths that differ between reference and what is being checked (the index under --staged, the
    working tree otherwise, which in CI is the commit being checked), untracked task files included."""
    if reference is None:
        return set(s.files(root, '.', staged))
    args = ["diff", "--name-only", "-z", "--no-renames"] + (["--cached"] if staged else []) + [reference]
    paths = set(s.git(root, *args).stdout.split("\0")) - {""}
    if not staged:
        paths |= set(s.git(root, "ls-files", "-z", "--others", "--exclude-standard").stdout.split("\0")) - {""}
    return paths


def changed_paths(root, reference, staged=False):
    paths = raw_changed_paths(root, reference, staged)
    return source.changed(root, paths, reference, staged=staged) if reference else paths


@s.cached_read
def test_material(root, base, staged=False):
    """Classify tests before excluding documentation or normalizing source code."""
    all_records = records(root, staged, errors={})
    paths = repository_test_files(root, base, all_records, staged)
    for record in all_records.values():
        paths |= strict_test_files(root, record, base, staged)
    return paths


def evidence_paths(root, record, staged=False):
    """The contract file of a task and the REAL evidence files it names."""
    path = record.get("contract", "none")
    if path == "none":
        return set()
    found = {path}
    try:
        contract = json.loads(s.read_text(root, path, staged))
        for item in contract.get("criteria", []) if isinstance(contract, dict) else []:
            for evidence in item.get("evidence", []) if isinstance(item, dict) else []:
                if isinstance(evidence, dict) and evidence.get("provenance") == "REAL":
                    found.add(str(evidence.get("ref", "")))
    except (OSError, ValueError, s.Refused, AttributeError):
        pass
    return found


@s.cached_read
def repository_files(root, rev=None):
    args = ["ls-tree", "-r", "--name-only", "-z", rev] if rev else ["ls-files", "-z"]
    return set(s.git(root, *args).stdout.split("\0")) - {""}


def _repository_test_path(root, path):
    if Path(path).is_absolute():
        path = Path(path).relative_to(root).as_posix()
    return posixpath.normpath(s.local_path(root, path).relative_to(root).as_posix())


def pytest_collection_arguments(record):
    """Positions of collection arguments, retaining node selections."""
    words = declared_test(record) or []
    start = next((i + 1 for i, word in enumerate(words)
                  if posixpath.basename(word.replace('\\', '/')) in
                  {'pytest', 'py.test', 'pytest.exe', 'py.test.exe'}), len(words))
    takes_value = {'-c', '--config-file', '-k', '-m', '--deselect', '--ignore', '--ignore-glob',
                   '--confcutdir', '--rootdir', '--basetemp', '-o', '--override-ini', '-p',
                   '--tb', '--maxfail', '--durations', '--durations-min', '--color', '--capture',
                   '--import-mode', '--junitxml', '--junit-xml', '--junit-prefix', '--junitprefix',
                   '--log-level', '--log-format', '--log-date-format', '--log-cli-level',
                   '--log-cli-format', '--log-cli-date-format', '--log-file', '--log-file-mode',
                   '--log-file-level', '--log-file-format', '--log-file-date-format', '--log-disable',
                   '--verbosity', '-n', '--numprocesses'}
    paths, skip = [], False
    for index, word in enumerate(words[start:], start):
        if skip:
            skip = False
        elif word in takes_value:
            skip = True
        elif not word.startswith('-'):
            paths.append(index)
    return paths


def pytest_collection_paths(record):
    """Collection arguments, including named paths which have not been written yet."""
    words = declared_test(record) or []
    return [words[index].split('::', 1)[0] for index in pytest_collection_arguments(record)]


def default_pytest_files(root, record, base, staged=False):
    """Files selected by a pytest command without explicit collection paths."""
    if base and not s.has_commit(root, base):
        base = None
    configs = tuple(sorted(pytest_configs(root, record)))
    paths = pytest_patterns(root, base, staged, configs, 'testpaths')
    candidates = repository_files(root, base) | repository_files(root) | raw_changed_paths(root, base, staged)
    candidates = {path for path in candidates if not s.generated_path(path)
                  and not path.startswith(('tasks/', '.workorder/', '.notquite/'))}
    patterns = pytest_patterns(root, base, staged, configs)
    found = {path for path in candidates if _repository_test_file(path) or _matches_test_name(path, patterns)}
    if paths:
        found = {path for path in found if any(parent == '.' or any(
            fnmatch.fnmatchcase('/'.join(path.split('/')[:end]), parent)
            for end in range(1, len(path.split('/')) + 1)) for parent in paths)}
    return {path for path in found if posixpath.basename(path) not in PYTEST_CONFIG_NAMES | {'conftest.py'}}


def test_files(root, record, base, staged=False):
    """Expand current and base definitions, so deleting a test or changing its path cannot hide it."""
    before = fields_at(root, record["id"], base)
    candidates = repository_files(root, base) | repository_files(root)
    candidates |= raw_changed_paths(root, base, staged)
    candidates = {path for path in candidates if not s.generated_path(path)}
    found = set()
    for definition in (before, record):
        if definition is None:
            continue
        paths = definition.get("test_paths")
        if paths is None:
            paths = (pytest_collection_paths(definition) if uses_pytest(definition) else
                     [word for word in declared_test(definition) or [] if looks_like_test(word)])
            if not paths and uses_pytest(definition):
                found |= default_pytest_files(root, definition, base, staged)
        if not isinstance(paths, list):
            continue
        for path in paths:
            if not isinstance(path, str):
                continue
            if "test_paths" not in definition:
                path = path.split("::", 1)[0]  # pytest node selection
            try:
                path = _repository_test_path(root, path)
            except (s.Refused, ValueError):
                continue
            # An inferred "." collects the repository; that must not make production code a test.
            if path == '.' and 'test_paths' not in definition:
                continue
            found |= {p for p in candidates if (path == "." or p == path or p.startswith(path + "/"))
                      and ('test_paths' in definition or not p.startswith(('tasks/', '.workorder/', '.notquite/')))}
    return without_declared_code(record, found)


LANGUAGE_TEST_PATTERNS = ('test_*.py', '*_test.py', '*_test.go', '*.test.*', '*.spec.*', '*_spec.*', '*_tests.*',
                          'testing.py', '*_testing.py', 'expected*', 'golden*', 'snapshot*')
TEST_DIR_NAMES = {'test', 'tests', '__tests__', 'spec'}


def test_directory(name):
    name = name.lower()
    return (name in {'test', 'tests', 'testing', '__tests__', 'spec', 'specs', 'fixtures',
                    'testdata', 'test_data', 'testsupport', 'test_support', 'golden', '__snapshots__'}
            or name.startswith('test_') or name.endswith(('_test', '_tests')))


def looks_like_test(path):
    path = path.replace('\\', '/').split('::', 1)[0]
    name = posixpath.basename(path)
    return (any(test_directory(part) for part in path.split('/')[:-1])
            or name in TEST_DIR_NAMES
            or name == 'conftest.py'
            or any(fnmatch.fnmatchcase(name, pattern) for pattern in LANGUAGE_TEST_PATTERNS))


def _language_test_name(path):
    name = posixpath.basename(path)
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in LANGUAGE_TEST_PATTERNS)


def _repository_test_file(path):
    """A test file in any language. Configuration alone does not count."""
    if path.startswith(('tasks/', '.workorder/', '.notquite/')):
        return False
    if posixpath.basename(path) == 'conftest.py':
        return False
    if any(test_directory(part) for part in path.split('/')[:-1]):
        return True
    return _language_test_name(path)


def paths_named_by_command(command, root, include_missing=False):
    """Files and folders a test command names, ignoring flags and the repository root."""
    words = command if isinstance(command, list) else s.split_command(command, "--test")
    if not words:
        return []
    record = {"test": s.join_command(words)}
    if uses_pytest(record):
        named = pytest_collection_paths(record)
    else:
        named = []
        for word in words[1:]:
            if word.startswith('-'):
                continue
            path = word.split('::', 1)[0]
            if path not in {'', '.'} and ('/' in path.replace('\\', '/')
                                          or '.' in posixpath.basename(path)
                                          or looks_like_test(path)):
                named.append(path)
        if looks_like_test(words[0]):
            named.append(words[0])
    found = []
    for path in named:
        path = path.replace('\\', '/').split('::', 1)[0]
        try:
            path = _repository_test_path(root, path)
            target = s.local_path(root, path)
        except (s.Refused, ValueError):
            continue
        if not include_missing and not (target.is_file() or target.is_dir()):
            continue
        if path in {'', '.'} or path in found:
            continue
        found.append(path)
    return found


def uses_pytest(record):
    command = declared_test(record) or []
    return any(posixpath.basename(word.replace('\\', '/')) in {"pytest", "py.test", "pytest.exe", "py.test.exe"}
               for word in command)


PYTEST_CONFIG_NAMES = {'pytest.ini', '.pytest.ini', 'pytest.toml', '.pytest.toml',
                       'tox.ini', 'setup.cfg', 'pyproject.toml'}


def pytest_sections(text, native=False):
    """Keep pytest TOML sections only; unrelated project settings do not require acceptance."""
    found, selected = [], False
    for line in text.splitlines():
        if re.match(r"^\s*\[", line):
            selected = bool(re.match(r'''^\s*\[\[?\s*["']?tool["']?\s*\.\s*["']?pytest''', line))
            if native:
                selected |= bool(re.match(r'''^\s*\[\[?\s*["']?pytest["']?\s*[.\]]''', line))
        if selected:
            found.append(line)
    return "\n".join(found).strip()


@s.cached_read
def pytest_patterns(root, base, staged=False, configs=(), option='python_files'):
    """Conservatively include configured discovery patterns from both snapshots."""
    patterns = {'test_*.py', '*_test.py'} if option == 'python_files' else set()
    candidates = repository_files(root, base) | repository_files(root) | raw_changed_paths(root, base, staged) | set(configs)
    for path in sorted(candidates):
        name = posixpath.basename(path)
        if name not in PYTEST_CONFIG_NAMES and path not in configs:
            continue
        def add(values):
            patterns.update(posixpath.normpath(posixpath.join(posixpath.dirname(path), value))
                            if option == 'testpaths' else value for value in values if isinstance(value, str))
        old = s.git(root, 'show', f'{base}:{path}', required=False).stdout
        try:
            current = s.read_text(root, path, staged)
        except (OSError, s.Refused, UnicodeError):
            current = ''
        for text in (old, current):
            if name.endswith('.toml'):
                # python_files is a string or array of strings in pytest's TOML options.
                lines = pytest_sections(text, native=name != 'pyproject.toml').splitlines()
                for i, line in enumerate(lines):
                    value = re.match(r'^\s*' + option + r'\s*=\s*(.*)', line)
                    if not value:
                        continue
                    expression = value[1]
                    for end in range(i, len(lines)):
                        if end > i:
                            expression += '\n' + lines[end]
                        try:
                            parsed = ast.literal_eval(expression)
                        except (ValueError, SyntaxError):
                            continue  # a multiline array or string may need another line
                        values = parsed.split() if isinstance(parsed, str) else parsed
                        if isinstance(values, list):
                            add(values)
                        break
            else:
                config = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    config.read_string(text)
                    section = 'tool:pytest' if name == 'setup.cfg' else 'pytest'
                    add(config.get(section, option, fallback='').split())
                except configparser.Error:
                    continue
    return patterns


def pytest_configs(root, definition):
    command = declared_test(definition) or []
    found = set()
    for i, word in enumerate(command):
        path = (command[i + 1] if word in {'-c', '--config-file'} and i + 1 < len(command)
                else word.partition('=')[2] if word.startswith('--config-file=')
                else word[2:] if word.startswith('-c') and len(word) > 2 else None)
        if path:
            try:
                found.add(_repository_test_path(root, path))
            except (s.Refused, ValueError):
                continue
    return found


def wrapper_paths(record):
    """Known wrapper commands need explicit test_paths to describe their hidden test invocation."""
    command = declared_test(record) or []
    if not command:
        return None
    if posixpath.basename(command[0].replace('\\', '/')).removesuffix('.exe') == 'env':
        command = command[1:]
        while command and (command[0].startswith('-') or '=' in command[0]):
            command = command[1:]
        if not command:
            return None
    executable = posixpath.basename(command[0].replace('\\', '/'))
    tools = {'npm', 'npx', 'yarn', 'pnpm', 'go', 'cargo', 'tox', 'nox'}
    if (executable.removesuffix('.exe').removesuffix('.cmd') in tools
            or any(command[i:i + 2] in (['-m', 'tox'], ['-m', 'nox'])
                   for i in range(len(command) - 1))):
        return []
    if executable in {'make', 'gmake', 'make.exe'}:
        for i, word in enumerate(command):
            if word in {'-f', '--file', '--makefile'} and i + 1 < len(command):
                return [command[i + 1]]
            if word.startswith(('--file=', '--makefile=')):
                return [word.partition('=')[2]]
        return ['GNUmakefile', 'makefile', 'Makefile']
    scripts = [word for word in command if word.endswith(('.sh', '.bash', '.py', '.js', '.mjs', '.rb', '.pl', '.ps1'))]
    if executable in {'sh', 'bash', 'dash', 'zsh'}:
        return scripts or [word for word in command[1:] if not word.startswith('-')][:1]
    if uses_pytest(record):
        return None
    if any(not looks_like_test(path) for path in scripts):
        return scripts
    if ('/' in command[0].replace('\\', '/') and not looks_like_test(command[0])
            and not re.fullmatch(r'(?:python[\d.]*|pypy[\d.]*|node|ruby|perl)(?:\.exe)?', executable)):
        return [command[0]]
    return None


def wrapper_note(record):
    if 'test_paths' not in record and wrapper_paths(record) is not None:
        return f"{record['id']} runs a wrapper script; set test_paths so changes to its tests are reviewed"
    return None


def _under_test_root(path, roots):
    """A folder named '.' is the repository, not a test folder, so it selects nothing."""
    return any(root and root != '.' and (path == root or path.startswith(root + '/')) for root in roots)


def _named_test_folder(path, candidates):
    """True when a command or testpaths entry names a directory that contains files."""
    return bool(path and path != '.' and any(candidate.startswith(path + '/') for candidate in candidates))


def _natural_test_roots(candidates):
    """Recognised test folders, including their data and helpers."""
    roots = set()
    for path in candidates:
        parts = path.split('/')
        for index, part in enumerate(parts[:-1]):
            if test_directory(part):
                roots.add('/'.join(parts[:index + 1]))
    return roots


def _matches_test_name(path, patterns):
    name = posixpath.basename(path)
    return (name == 'conftest.py'
            or any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns))


def _inferred_roots(candidates, named_paths):
    roots = _natural_test_roots(candidates)
    for path in named_paths:
        if _named_test_folder(path, candidates):
            roots.add(path)
    return roots


def _normalize_named_paths(root, paths):
    found = []
    for path in paths:
        try:
            found.append(_repository_test_path(root, path))
        except (s.Refused, ValueError):
            continue
    return found


def _named_paths(root, definition):
    """Files and folders this definition names. The repository root is not one of them."""
    if not definition:
        return []
    if 'test_paths' in definition:
        raw = [path for path in definition.get('test_paths') or [] if isinstance(path, str)]
    elif uses_pytest(definition):
        raw = pytest_collection_paths(definition)
    else:
        words = declared_test(definition) or []
        raw = []
        for word in words[1:]:
            if word.startswith('-'):
                continue
            path = word.split('::', 1)[0]
            if path not in {'', '.'} and ('/' in path.replace('\\', '/')
                                          or '.' in posixpath.basename(path)
                                          or looks_like_test(path)):
                raw.append(path)
        if words and looks_like_test(words[0]):
            raw.append(words[0])
    return [path for path in _normalize_named_paths(root, raw) if path not in {'', '.'}]


def _configs_on_path(named, candidates):
    """conftest.py and pytest configuration in the named path and its parents."""
    directories = set()
    for path in named:
        nested = any(candidate.startswith(path + '/') for candidate in candidates)
        start = path if nested and path not in candidates else posixpath.dirname(path)
        while True:
            directories.add(start)
            if not start:
                break
            start = posixpath.dirname(start)
    names = PYTEST_CONFIG_NAMES | {'conftest.py'}
    return {candidate for candidate in candidates
            if not candidate.startswith(('tasks/', '.workorder/', '.notquite/'))
            and posixpath.basename(candidate) in names
            and posixpath.dirname(candidate) in directories}


def implicit_test_files(root, record, base, staged, candidates, configs):
    """Pytest configuration on the path to files or folders the task names.

    A named file does not include its siblings. A command that names no path
    does not own every folder called tests. A pytest command with no path still
    owns root pytest configuration, because that is what the command reads.
    A command that does not use pytest does not.
    """
    del base, staged, configs
    named = _named_paths(root, record)
    selected = named if uses_pytest(record) else []
    found = _configs_on_path(selected, candidates)
    if uses_pytest(record) and not named:
        found |= {candidate for candidate in candidates
                  if not candidate.startswith(('tasks/', '.workorder/', '.notquite/'))
                  and posixpath.dirname(candidate) == ''
                  and posixpath.basename(candidate) in PYTEST_CONFIG_NAMES | {'conftest.py'}}
    return found


def strict_test_files(root, record, base, staged=False):
    paths = test_files(root, record, base, staged)
    definitions = (record, fields_at(root, record['id'], base) or {})
    candidates = repository_files(root, base) | repository_files(root) | raw_changed_paths(root, base, staged)
    candidates = {path for path in candidates if not s.generated_path(path)}
    configs = set().union(*(pytest_configs(root, definition) for definition in definitions if uses_pytest(definition)))
    on_path = set()
    for definition in definitions:
        if not definition:
            continue
        on_path |= implicit_test_files(root, definition, base, staged, candidates, configs)
        for path in wrapper_paths(definition) or []:
            try:
                paths.add(_repository_test_path(root, path))
            except (s.Refused, ValueError):
                continue
    paths |= {path for path in on_path if not path.endswith('.toml')}
    if not any(uses_pytest(definition) for definition in definitions):
        return without_declared_code(record, paths & candidates)
    paths |= configs
    # Even when passed with -c, only pytest sections of TOML belong to this policy.
    if all('test_paths' not in definition for definition in definitions):
        paths = {p for p in paths if posixpath.basename(p) != 'pyproject.toml'
                 and not (p in configs and p.endswith('.toml'))}
    for path in on_path:
        name = posixpath.basename(path)
        if not (name == 'pyproject.toml' or path.endswith('.toml')):
            continue
        old = s.git(root, 'show', f'{base}:{path}', required=False).stdout
        try:
            current = s.read_text(root, path, staged)
        except UnicodeError:
            paths.add(path)  # pytest cannot read this configuration, so run and report its failure
            continue
        except (OSError, s.Refused):
            current = ""
        if pytest_sections(old, native=name != 'pyproject.toml') != pytest_sections(current, native=name != 'pyproject.toml'):
            paths.add(path)
    return without_declared_code(record, paths)


class _TestSyntax(ast.NodeTransformer):
    """Only the documented harmless differences in a Python test function."""
    def visit_Assert(self, node):
        node.msg = None
        return self.generic_visit(node)

    def visit_Compare(self, node):
        self.generic_visit(node)
        if len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            node.left, node.comparators[0] = sorted((node.left, node.comparators[0]), key=ast.dump)
        return node

    def generic_visit(self, node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
        return super().generic_visit(node)


def _test_functions(tree, prefix=()):
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test'):
            key = (*prefix, node.name)
            # Duplicate definitions cannot be matched unambiguously.
            found[key] = None if key in found else node
        elif isinstance(node, ast.ClassDef):
            found.update(_test_functions(node, (*prefix, node.name)))
    return found


def refactor_test_files(root, record, base, staged=False):
    """Include tests selected by the command even without explicit ownership."""
    inferred = {key: value for key, value in record.items() if key != 'test_paths'}
    paths = strict_test_files(root, record, base, staged)
    paths |= strict_test_files(root, inferred, base, staged)
    return paths


def changed_test_paths(root, base, paths, staged=False):
    """Ignore Python comments and layout when both versions have the same syntax tree."""
    changed = {path for path in paths & changed_paths(root, base, staged) if not s.generated_path(path)}
    for path in list(changed):
        if not path.endswith('.py'):
            continue
        before, after = _test_bytes(root, path, base), _test_bytes(root, path, staged=staged)
        if before is None or after is None:
            continue
        old_mode = s.code_tree(root, base).get(path, '').split(' ', 1)[0]
        if staged:
            info = s.git(root, 'ls-files', '--stage', '--', f':(literal){path}').stdout.split()
            mode = info[0] if info else ''
        else:
            mode = ('120000' if (root / path).is_symlink() else
                    '100755' if (root / path).stat().st_mode & 0o111 else '100644')
        if old_mode != mode or mode not in {'100644', '100755'}:
            continue
        try:
            if ast.dump(ast.parse(before)) == ast.dump(ast.parse(after)):
                changed.remove(path)
        except (SyntaxError, ValueError, UnicodeError):
            pass  # unreadable syntax is still a change
    return changed


def changed_existing_test_paths(root, base, paths, staged=False):
    """Finished tasks keep existing Python tests, without owning later additions."""
    from .python_tests import preserves_existing
    changed = changed_test_paths(root, base, paths, staged)
    old_tree = s.code_tree(root, base)
    current_tree = s.code_tree(root, s.git(root, 'write-tree').stdout.strip()) if staged else None
    for path in list(changed):
        if not path.endswith('.py') or posixpath.basename(path) == 'conftest.py':
            continue
        before, after = _test_bytes(root, path, base), _test_bytes(root, path, staged=staged)
        if before is None:
            changed.remove(path)
            continue
        if after is None:
            continue
        old_mode = old_tree.get(path, '').split(' ', 1)[0]
        mode = (current_tree.get(path, '').split(' ', 1)[0] if staged else
                '120000' if (root / path).is_symlink() else
                '100755' if (root / path).stat().st_mode & 0o111 else '100644')
        if old_mode == mode and mode in {'100644', '100755'} and preserves_existing(before, after):
            changed.remove(path)
    return changed


def selected_test_files(root, record, base, staged=False):
    """Select tests themselves, without mistaking configuration or helpers for old tests."""
    paths = refactor_test_files(root, record, base, staged)
    before = fields_at(root, record['id'], base) or {}
    configs = pytest_configs(root, record) | pytest_configs(root, before)
    paths = {path for path in paths if posixpath.basename(path) not in PYTEST_CONFIG_NAMES | {'conftest.py'}
             and path not in configs}
    if uses_pytest(record) or uses_pytest(before):
        named = _normalize_named_paths(root, [path.split('::', 1)[0] for definition in (before, record)
                                             for path in pytest_collection_paths(definition)])
        patterns = pytest_patterns(root, base, staged, tuple(sorted(configs)))
        paths = {path for path in paths if path in named or _matches_test_name(path, patterns)}
    return paths


def changed_pytest_tests(root, record, base, staged=False):
    """Current node IDs whose function syntax (including decorators) changed."""
    if not uses_pytest(record):
        return []
    from .python_stubs import functions
    patterns = pytest_patterns(root, base, staged, tuple(sorted(pytest_configs(root, record))),
                               'python_functions') or {'test'}
    command = declared_test(record)
    selectors = []
    for index in pytest_collection_arguments(record):
        path, *nodes = command[index].split('::')
        selectors.append((_repository_test_path(root, path), nodes))
    found = []
    for path in sorted(selected_test_files(root, record, base, staged)):
        if not path.endswith('.py'):
            continue
        try:
            before = functions(ast.parse(_test_bytes(root, path, base) or b''))
            after = functions(ast.parse(_test_bytes(root, path, staged=staged) or b''))
        except (SyntaxError, ValueError, UnicodeError):
            continue  # the declared run reports unreadable tests
        for key, node in after.items():
            name, _ = key
            if not any(name[-1].startswith(pattern) or fnmatch.fnmatchcase(name[-1], pattern) for pattern in patterns):
                continue
            if key in before and ast.dump(node) == ast.dump(before[key]):
                continue
            if not selectors:
                found.append('::'.join((path, *name)))
            for selected, nodes in selectors:
                if (selected == '.' or path == selected or path.startswith(selected + '/')) and all(
                        index < len(name) and part.split('[', 1)[0] == name[index]
                        for index, part in enumerate(nodes)):
                    # Keep an explicit parametrized case selection, if the command made one.
                    found.append('::'.join((path, *(nodes if nodes and '[' in nodes[-1] else name))))
    return sorted(set(found))


def selected_pytest_command(record, tests):
    return pytest_selection.command(declared_test(record), tests)


def test_names(tests):
    return ', '.join(dict.fromkeys(test.split('::', 1)[1] for test in tests))


def test_origin(root, record, base, label, staged=False):
    paths = selected_test_files(root, record, base, staged)
    existing = paths & repository_files(root, base)
    unchanged = bool(existing) and not changed_test_paths(root, base, paths, staged)
    command = declared_test(record)
    # An inline test lives in the task's command, rather than in a test file.
    if not paths and command and '-c' in command:
        unchanged = command == declared_test(fields_at(root, record['id'], base) or {})
    tid = record['id']
    if unchanged:
        return True, f"{tid}'s test landed on {label} before this change and is unchanged"
    additions = [test for test in changed_pytest_tests(root, record, base, staged)
                 if test.split('::', 1)[0] in existing]
    if additions and not changed_existing_test_paths(root, base, existing, staged):
        return False, f"{tid}'s new tests were written in this change ({test_names(additions)})"
    return False, f"{tid}'s test was written in this change"


def test_first_problem(tid, label):
    return (f"{tid}: its test must land on {label}, failing, before the work; "
            f"a reviewer accepts it with the label workorder-allow:{tid}")


ASSERTION_PREFIXES = ('assert', 'verify', 'check', 'expect', 'ensure')
ASSERTION_HELPER_DEPTH = 4
PYTEST_ASSERTIONS = {'raises', 'warns', 'deprecated_call', 'fail'}


def self_equality(test):
    return (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Eq, ast.Is))
            and isinstance(test.left, (ast.Name, ast.Attribute, ast.Subscript))
            and ast.dump(test.left) == ast.dump(test.comparators[0]))


class _AssertionScan:
    """Follow local test helpers without importing or executing their modules."""

    def __init__(self, root, material, staged):
        self.root, self.material, self.staged = root, material, staged
        self.modules, self.scopes = {}, {}

    def module_path(self, module, path, level=0):
        stem = module.replace('.', '/')
        parent = posixpath.dirname(path)
        if level:
            for _ in range(level - 1):
                parent = posixpath.dirname(parent)
            stems = [posixpath.join(parent, stem)]
        else:
            stems = [stem, posixpath.join(parent, stem), 'src/' + stem]
        return next((candidate for stem in stems
                     for candidate in (stem + '.py', stem.rstrip('/') + '/__init__.py')
                     if candidate in self.material), None)

    def bindings(self, nodes, path, inherited=None):
        found = dict(inherited or {})
        functions, classes = [], []

        def collect(nodes):
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[node.name] = (path, node)
                    functions.append(node)
                elif isinstance(node, ast.ClassDef):
                    classes.append(node)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        found[alias.asname or alias.name] = (self.module_path(alias.name, path), '')
                        if alias.name == 'pytest':
                            for assertion in PYTEST_ASSERTIONS:
                                found[(alias.asname or alias.name) + '.' + assertion] = True
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        name = alias.asname or alias.name
                        if node.module == 'pytest' and alias.name in PYTEST_ASSERTIONS:
                            found[name] = True
                        else:
                            module = self.module_path(node.module or '', path, node.level)
                            submodule = self.module_path('.'.join(filter(None, (node.module, alias.name))), path, node.level)
                            found[name] = (submodule, '') if submodule else (module, alias.name)
                elif not isinstance(node, ast.Lambda):
                    collect(ast.iter_child_nodes(node))

        collect(nodes)
        class_methods = []
        for cls in classes:
            methods = {node.name: (path, node) for node in cls.body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            found.update({cls.name + '.' + name: value for name, value in methods.items()})
            class_methods.append(methods)
        for methods in class_methods:
            # Bare names in a method resolve in the module, not in the class.
            context = dict(found)
            for receiver in ('self', 'cls'):
                context.update({receiver + '.' + name: value for name, value in methods.items()})
            for _, function in methods.values():
                self.scopes[function] = context
        for function in functions:
            self.scopes[function] = found
        return found

    def module(self, path):
        if path not in self.modules:
            try:
                tree = ast.parse(_test_bytes(self.root, path, staged=self.staged) or b'')
            except (SyntaxError, UnicodeError, ValueError):
                tree = ast.Module(body=[], type_ignores=[])
            self.modules[path] = tree, self.bindings(tree.body, path)
        return self.modules[path]

    def resolve(self, name, bindings, depth):
        if depth <= 0:
            return None
        target = bindings.get(name)
        if target is True:
            return True
        if target is None:
            prefix, dot, member = name.rpartition('.')
            module = bindings.get(prefix)
            if dot and isinstance(module, tuple) and module[0] and module[1] == '':
                return self.resolve(member, self.module(module[0])[1], depth - 1)
            return None
        path, value = target
        if not path:
            return None
        if isinstance(value, str):
            return self.resolve(value, self.module(path)[1], depth - 1) if value else None
        return path, value

    def context(self, path):
        found = {}
        configs = _configs_on_path([path], self.material)
        for config in sorted(configs, key=lambda value: (value.count('/'), value)):
            if posixpath.basename(config) == 'conftest.py':
                found.update(self.module(config)[1])
        found.update(self.module(path)[1])
        return found

    def assertion(self, node, path, bindings, depth=ASSERTION_HELPER_DEPTH):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return False
        if isinstance(node, ast.Assert):
            if self_equality(node.test):
                return False
            return any(isinstance(child, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))
                       for child in ast.walk(node.test))
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ''
            if name.startswith(ASSERTION_PREFIXES):
                return True
            qualified = ast.unparse(func) if isinstance(func, (ast.Name, ast.Attribute)) else ''
            if qualified in {'pytest.' + name for name in PYTEST_ASSERTIONS}:
                return True
            target = self.resolve(qualified, bindings, ASSERTION_HELPER_DEPTH)
            if target is True:
                return True
            if target and depth:
                source, function = target
                context = self.bindings(function.body, source, self.scopes[function])
                if any(self.assertion(child, source, context, depth - 1) for child in function.body):
                    return True
        return any(self.assertion(child, path, bindings, depth) for child in ast.iter_child_nodes(node))


def assertion_problems(root, base, record, staged=False, paths=None, material=None):
    """Find assertion-free functions, including calls through nearby test helpers."""
    if paths is None:
        paths = strict_test_files(root, record, base, staged)
    if material is None:
        material = repository_test_files(root, base, records(root, staged, errors={}), staged, owner=record) | paths
    scanner = _AssertionScan(root, material, staged)
    problems = []

    def functions(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith('test'):
                yield node
            return
        if not isinstance(node, ast.Lambda):
            for child in ast.iter_child_nodes(node):
                yield from functions(child)

    for path in sorted(paths & changed_paths(root, base, staged)):
        if not path.endswith('.py'):
            continue
        tree, _ = scanner.module(path)
        try:
            previous = ast.parse(_test_bytes(root, path, base) or b'')
        except (SyntaxError, UnicodeError, ValueError):
            previous = ast.Module(body=[], type_ignores=[])
        # Match syntax, not line numbers: comments and edits to neighbouring tests
        # do not make old smoke tests part of this change. Consume each occurrence
        # so a newly duplicated function is still inspected.
        from collections import Counter
        unchanged = Counter(ast.dump(function) for function in functions(previous))
        # Script tests can assert at module scope rather than defining test_ functions.
        # A self-comparison there supplies no evidence either.
        top = [node for node in tree.body
               if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        tautologies = [node for node in top if isinstance(node, ast.Assert) and self_equality(node.test)]
        context = scanner.context(path)
        old_top = [node for node in previous.body
                   if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        if ([ast.dump(node) for node in top] != [ast.dump(node) for node in old_top]
                and tautologies and not any(scanner.assertion(node, path, context) for node in top)):
            problems.append(AssertionProblem(
                f"{record['id']}: {path} asserts nothing that can fail", path, tautologies[0].lineno, True))
        for function in functions(tree):
            syntax = ast.dump(function)
            if unchanged[syntax]:
                unchanged[syntax] -= 1
                continue
            context = scanner.context(path)
            context.update(scanner.scopes.get(function, {}))
            bindings = scanner.bindings(function.body, path, context)
            if not any(scanner.assertion(node, path, bindings) for node in function.body):
                problems.append(AssertionProblem(
                    f"{record['id']}: {function.name} in {path} asserts nothing that can fail",
                    path, function.lineno, label_only=True))
    return problems


def _without_python_comments(lines):
    data = b''.join(lines)
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    text = data.decode(encoding)
    lines = text.splitlines(keepends=True)
    multiline = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.start[0] < token.end[0]:
            multiline.update(range(token.start[0], token.end[0] + 1))
        if token.type == tokenize.COMMENT:
            lines[token.start[0] - 1] = lines[token.start[0] - 1][:token.start[1]].rstrip() + '\n'
    # Blank lines inside string literals are content, even when they look like comments.
    return [line for number, line in enumerate(lines, 1) if line.strip() or number in multiline]


def _removed_lines_after_import_additions(old, new):
    """Discount extended imports and test functions with the same normalized syntax."""
    old, new = old.replace(b'\r\n', b'\n'), new.replace(b'\r\n', b'\n')
    try:
        before, after = ast.parse(old), ast.parse(new)
    except (SyntaxError, UnicodeError, ValueError):
        return None
    old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
    imports = (ast.Import, ast.ImportFrom)
    # A new binding must not replace a name the old tests already use or define.
    used = {node.id for node in ast.walk(before) if isinstance(node, ast.Name)}
    used |= {node.name for node in ast.walk(before)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    used |= {node.asname or node.name.split('.')[0] for node in ast.walk(before)
             if isinstance(node, ast.alias)}

    def signature(node):
        return type(node), getattr(node, 'module', None), getattr(node, 'level', None)

    def standalone(node, lines):
        # Do not discount a semicolon followed by another statement on the same line.
        try:
            return len(ast.parse(b''.join(lines[node.lineno - 1:node.end_lineno])).body) == 1
        except (SyntaxError, UnicodeError, ValueError):
            return False

    replacements, consumed = [], set()
    previous_functions = _test_functions(before)
    for name, current in _test_functions(after).items():
        previous = previous_functions.get(name)
        if previous is None or current is None:
            continue
        normalized = lambda node: ast.dump(_TestSyntax().visit(copy.deepcopy(node)))
        if normalized(previous) == normalized(current):
            start = lambda node: min([node.lineno] + [item.lineno for item in node.decorator_list]) - 1
            replacements.append((start(current), current.end_lineno, old_lines[start(previous):previous.end_lineno]))
    for previous in before.body:
        if not isinstance(previous, imports) or getattr(previous, 'module', None) == '__future__':
            continue
        aliases = [(item.name, item.asname) for item in previous.names]
        for current in after.body:
            if (not isinstance(current, imports) or current.lineno in consumed
                    or signature(previous) != signature(current)):
                continue
            added = [(item.name, item.asname) for item in current.names]
            remaining = iter(added)
            if len(added) <= len(aliases) or not all(alias in remaining for alias in aliases):
                continue
            extra = [item for item in added if item not in aliases]
            bindings = [alias or (name.split('.')[0] if isinstance(current, ast.Import) else name)
                        for name, alias in extra]
            if (not extra or any(name == '*' for name, _ in aliases + added)
                    or len(set(bindings)) != len(bindings) or used.intersection(bindings)
                    or not standalone(previous, old_lines) or not standalone(current, new_lines)):
                continue
            replacements.append((current.lineno - 1, current.end_lineno,
                                 old_lines[previous.lineno - 1:previous.end_lineno]))
            consumed.add(current.lineno)
            break
    if not replacements:
        return None
    for start, end, lines in sorted(replacements, reverse=True):
        new_lines[start:end] = lines
    try:
        old_lines, new_lines = _without_python_comments(old_lines), _without_python_comments(new_lines)
    except (SyntaxError, UnicodeError, ValueError, tokenize.TokenError):
        return None
    return sum(end - start for tag, start, end, _, _ in
               difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
               if tag in {'delete', 'replace'})


def test_file_changes(root, base, record, staged=False, strict=False, protected=None, existing_only=False):
    if protected is None:
        protected = strict_test_files(root, record, base, staged)
    compare = changed_existing_test_paths if existing_only else changed_test_paths
    paths = sorted(compare(root, base, protected, staged))
    if not paths:
        return None
    if strict:
        return f"{record['id']}: its test files or pytest configuration changed since {base} ({', '.join(paths)})"
    if existing_only:
        python = [path for path in paths if path.endswith('.py') and posixpath.basename(path) != 'conftest.py']
        if python:
            return f"{record['id']}: its test files lost or changed existing Python statements since {base} ({', '.join(python)})"
    # Disabling rename detection makes a rename remove the old file's lines, even when identical.
    args = ["diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv"]
    if staged:
        args.append("--cached")
    delta = s.git(root, *args, base, "--", *(f":(literal){p}" for p in paths)).stdout
    lost, affected, binary = 0, [], False
    for entry in delta.split("\0"):
        if not entry:
            continue
        added, removed, path = entry.split("\t", 2)
        if path not in repository_files(root, base):
            continue  # new files, including binary fixtures, are pure additions
        exists = path in repository_files(root) if staged else s.local_path(root, path).is_file()
        if exists and removed != '-' and int(removed) and path.endswith('.py'):
            remaining = _removed_lines_after_import_additions(
                _test_bytes(root, path, base) or b'', _test_bytes(root, path, staged=staged) or b'')
            if remaining is not None:
                removed = str(remaining)
        if removed == "-" or int(removed) or not exists:
            lost += int(removed) if removed != "-" else 0
            binary |= removed == "-"
            affected.append(path)
    if affected:
        change = "changed binary content" if binary else f"lost or changed {lost} lines"
        return f"{record['id']}: its test files {change} since {base} ({', '.join(affected)})"
    return None


def repository_test_files(root, base, all_records, staged=False, *, owner=None):
    """Find test material, excluding declared code for this task or unowned review."""
    candidates = repository_files(root, base) | repository_files(root) | raw_changed_paths(root, base, staged)
    candidates = {p for p in candidates if not s.generated_path(p)
                  and not p.startswith(('tasks/', '.workorder/', '.notquite/'))}
    configs = set()
    named = []
    for record in all_records.values():
        for definition in (record, fields_at(root, record['id'], base) or {}):
            if uses_pytest(definition):
                configs |= pytest_configs(root, definition)
                named.extend(pytest_collection_paths(definition))
    patterns = pytest_patterns(root, base, staged, tuple(sorted(configs)))
    testpaths = pytest_patterns(root, base, staged, tuple(sorted(configs)), 'testpaths')
    roots = _inferred_roots(candidates, _normalize_named_paths(root, list(named) + list(testpaths)))
    found = set()
    for path in candidates:
        parts = path.split('/')
        if (_under_test_root(path, roots) or _matches_test_name(path, patterns)
                or _language_test_name(path)):
            found.add(path)
        if parts[-1] in PYTEST_CONFIG_NAMES or path in configs:
            if parts[-1] in {'pyproject.toml', 'tox.ini', 'setup.cfg'} or path.endswith('.toml'):
                old = (_test_bytes(root, path, base) or b'').decode('utf-8', 'replace')
                new = (_test_bytes(root, path, staged=staged) or b'').decode('utf-8', 'replace')
                if path.endswith('.toml'):
                    relevant = pytest_sections(old, native=parts[-1] != 'pyproject.toml') != pytest_sections(
                        new, native=parts[-1] != 'pyproject.toml')
                else:
                    relevant = _pytest_ini_sections(old) != _pytest_ini_sections(new)
                if relevant:
                    found.add(path)
            else:
                found.add(path)
    found |= {p for p in candidates if _under_test_root(p, roots)}
    for record in (owner,) if owner is not None else all_records.values():
        found = without_declared_code(record, found)
    return found


def _pytest_ini_sections(text):
    found, selected = [], False
    for line in text.splitlines():
        if line.lstrip().startswith('['):
            selected = line.strip() in {'[pytest]', '[tool:pytest]'}
        if selected:
            found.append(line)
    return '\n'.join(found)


def scope(root, reference, staged, names, all_records, include_tests=False, strict_tests=False, errors=None):
    """The tasks a change touches (task file, receipts, contract or its evidence), and every task that
    depends on one of them, directly or not."""
    changed = changed_paths(root, reference, staged)
    touched = {path[6:-3] for path in changed if path.startswith("tasks/") and path.endswith(".md")
               and s.ID.fullmatch(path[6:-3])}
    touched |= {s.receipt_task(path) for path in changed} - {None}
    touched |= {tid for tid, record in all_records.items() if evidence_paths(root, record, staged) & changed}
    if include_tests:
        for tid, record in all_records.items():
            try:
                if (strict_test_files(root, record, reference, staged) & changed
                        or record['status'] in FINISHED and task_code_paths(root, record, reference, staged) & changed):
                    touched.add(tid)
            except (s.Refused, OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
                if errors is None:
                    raise
                touched.add(tid)
                errors[tid] = f'could not determine its changed tests: {error}'
    growing = True
    while growing:
        growing = False
        for tid, record in all_records.items():
            if tid not in touched and any(w[5:] in touched for w in s.dependencies(record) if w.startswith("task:")):
                touched.add(tid)
                growing = True
    return touched, changed


def requirement_changes(root, base, record, staged=False):
    """Changes since base to what a task requires: its test, its contract, its dependencies."""
    tid, found = record["id"], []
    before = fields_at(root, tid, base)
    if before is None:
        return found
    old_status, status = before.get('status'), record.get('status')
    if old_status != status and status == 'CANCELLED':
        found.append(f'status changed: {old_status} -> {status}')
    if before.get("test", "") != record.get("test", ""):
        found.append(f"test changed: {before.get('test', '') or '(none)'!r} -> {record.get('test', '') or '(none)'!r}")
    if before.get("test_paths") != record.get("test_paths"):
        found.append("test_paths changed")
    if before.get("contract") != record.get("contract"):
        found.append(f"contract changed: {before.get('contract')} -> {record.get('contract')}")
    elif record.get("contract", "none") != "none":
        old = s.git(root, "show", f"{base}:{record['contract']}", required=False)
        try:
            now = s.read_text(root, record["contract"], staged)
        except (OSError, s.Refused):
            now = None
        if old.returncode == 0 and now is not None and now != old.stdout.replace("\r\n", "\n"):
            found.append(f"contract {record['contract']} edited")
    try:
        dropped = sorted(set(s.dependencies({"waits_on": before.get("waits_on", []),
                                             "depends_on": s.task_refs(tid, "depends_on", before.get("depends_on", []))}))
                         - set(s.dependencies(record)))
    except (s.Refused, TypeError):
        dropped = []
    if dropped:
        found.append(f"dependencies removed: {', '.join(dropped)}")
    # Filling an open task is ordinary work; extending a finished ask needs review too.
    old = s.git(root, "show", f"{base}:tasks/{tid}.md", required=False)
    if old.returncode == 0:
        def section_lines(text, name):
            return {line.strip() for heading, body in s.task_body_sections(text) if heading == name
                    for line in body if line.strip() and not line.strip().startswith("<!--")}
        try:
            now_text = s.read_text(root, f"tasks/{tid}.md", staged)
        except (OSError, s.Refused):
            now_text = ""
        before_text = old.stdout.replace("\r\n", "\n")
        for name in ("Scope", "Acceptance"):
            removed = section_lines(before_text, name) - section_lines(now_text, name)
            if removed:
                found.append(f"{name} lines removed or changed: {len(removed)}")
            added = section_lines(now_text, name) - section_lines(before_text, name)
            if added and before.get('status') in FINISHED:
                found.append(f"{name} lines added to a finished task: {len(added)}")
    return found


def pytest_no_pass(output):
    """Recognise pytest's final summary, not incidental test output containing the word skipped."""
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    for line in reversed(output.splitlines()):
        summary = re.fullmatch(r"\s*(?:=+\s*)?(.*?)\s+in\s+\d+(?:\.\d+)?s(?:\s+\([^)]*\))?\s*(?:=+)?\s*", line)
        if not summary:
            continue
        value = summary[1]
        if re.search(r"\b[1-9]\d* passed\b", value):
            return None
        if re.search(r"\b0 passed\b", value):
            return value
        if "no tests ran" in value:
            return "no tests ran"
        skipped = re.search(r"\b(\d+) skipped\b", value)
        if skipped and not re.search(r'\b\d+ (?:failed|error|xfailed|xpassed|deselected)', value):
            return f"{skipped[1]} skipped"
        if re.fullmatch(r'\d+ (?:failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)(?:, \d+ (?:failed|errors?|skipped|xfailed|xpassed|deselected|warnings?))*', value):
            return value
        return None
    # -qq suppresses the summary, but pytest still prints progress outcomes.
    progress = [m[1] for line in output.splitlines()
                if (m := re.fullmatch(r'\s*([.sxFEfXP]+)(?:\s+\[\s*\d+(?:%|/\d+)\])?\s*', line))]
    outcomes = ''.join(progress)
    if outcomes and set(outcomes) <= {'s', 'x'}:
        return ', '.join(f'{outcomes.count(symbol)} {name}' for symbol, name in (('s', 'skipped'), ('x', 'xfailed'))
                         if symbol in outcomes)
    return None


def pytest_counts(output):
    """Read pytest's final outcomes, including the progress-only output of -qq."""
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    names = {'passed', 'failed', 'error', 'errors', 'skipped', 'xfailed', 'xpassed', 'deselected'}
    for line in reversed(output.splitlines()):
        match = re.fullmatch(r"\s*(?:=+\s*)?(.*?)\s+in\s+\d+(?:\.\d+)?s(?:\s+\([^)]*\))?\s*(?:=+)?\s*", line)
        if match:
            pairs = re.findall(r'\b(\d+) ([a-z]+)\b', match[1])
            if pairs or 'no tests ran' in match[1]:
                return Counter({name: int(number) for number, name in pairs if name in names})
    outcomes = ''.join(m[1] for line in output.splitlines()
                       if (m := re.fullmatch(r'\s*([.sxFEfXP]+)(?:\s+\[\s*\d+(?:%|/\d+)\])?\s*', line)))
    if outcomes:
        found = Counter()
        for symbol, name in (('.', 'passed'), ('s', 'skipped'), ('x', 'xfailed'), ('F', 'failed'),
                             ('E', 'errors'), ('X', 'xpassed'), ('P', 'passed')):
            found[name] += outcomes.count(symbol)
        return found
    return None


def _run_test(root, command, timeout, counts, *, quiet=False, cache_root=None, logical_command=None,
              failures_only=False):
    try:
        run = s.cached_test(command, root, timeout=timeout, cache_root=cache_root,
                            logical_command=logical_command, fresh=not s._REUSE_TESTS.get())
        return test_result(run, counts, quiet=quiet, failures_only=failures_only)
    except FileNotFoundError as error:
        return 127, str(error)  # Windows and POSIX use different error text for the same missing tool.
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return f'not started: {getattr(error, "strerror", None) or str(error)}', ''
    finally:
        s.invalidate_reads()  # a declared command may have changed files or refs


def test_result(run, counts, *, quiet=False, failures_only=False):
    if run.cached:
        s.print_once(f"(reused from {run.reused_at} UTC)")
    if not quiet and isinstance(run.returncode, int) and (not failures_only or run.returncode):
        s.print_test_output(run.stdout or '', run.stderr or '', failed=run.returncode != 0)
    if counts is not None:
        counts['tests'] = counts.get('tests', 0) + 1
        if run.cached:
            counts['cached'] = counts.get('cached', 0) + 1
    return run.returncode, (run.stdout or '') + (run.stderr or '')


def _test_key(record, timeout):
    return tuple(declared_test(record) or []), s.test_timeout(record, timeout)


def prefetch_tests(root, records, results, counts=None, timeout=None, staged=False, jobs=None, snapshot=False,
                   protected=()):
    """Run distinct commands concurrently, then print their output in task order."""
    pending = {}
    for record in sorted(records, key=lambda value: value['id']):
        key = _test_key(record, timeout)
        if declared_test(record) and key not in results:
            pending.setdefault(key, record)
    if not pending:
        return

    def run(key):
        # Context variables carry the reuse policy, but workers must not mutate
        # the main thread's read cache while another worker is using it.
        token = s._READS.set(None)
        command, deadline = key
        try:
            if staged or snapshot:
                with tempfile.TemporaryDirectory(prefix='workorder-parallel-') as temporary:
                    destination = Path(temporary) / 'code'
                    _test_snapshot(root, destination, staged=staged, protected=protected)
                    return s.cached_test(_snapshot_command(command, root, destination), destination, deadline,
                                         cache_root=root, logical_command=command, fresh=not s._REUSE_TESTS.get())
            return s.cached_test(command, root, deadline, fresh=not s._REUSE_TESTS.get())
        except (OSError, ValueError, s.Refused, subprocess.SubprocessError) as error:
            run = subprocess.CompletedProcess(command, 127, '', str(error))
            run.not_started = True
            return run
        finally:
            s._READS.reset(token)

    with ThreadPoolExecutor(max_workers=jobs or os.cpu_count() or 1) as pool:
        futures = {key: pool.submit(copy_context().run, run, key) for key in pending}
        for key, future in futures.items():
            run = future.result()
            if getattr(run, 'not_started', False):
                results[key] = 127, run.stderr
                continue
            if not hasattr(run, 'cached'):
                run.cached = False
            results[key] = test_result(run, counts, failures_only=staged)
    s.invalidate_reads()


def run_declared_test(root, record, counts=None, notes=None, strict=False, results=None, regression=False,
                      timeout=None):
    """Run a task's declared test here (CI), without a shell; return a problem line, or None if it passed."""
    tid, declared = record["id"], declared_test(record)
    if declared is None:
        return f"NOT DONE  {tid} says {record['status']} but declares no test for this check to run"
    results = {} if results is None else results
    key = _test_key(record, timeout)
    if key not in results:
        results[key] = _run_test(root, declared, s.test_timeout(record, timeout), counts)
    code, output = results[key]
    if counts is not None and isinstance(code, int) and code != 127:
        counts.setdefault('tested_tasks', set()).add(tid)
    from .python_imports import ImportFailure
    if isinstance(code, ImportFailure):
        ref = local_test_base(root, record) or 'the target'
        finding = ComparisonProblem(f'{tid}: could not compare with {ref}: {code}',
                                    f'tasks/{tid}.md', label_only=True)
        return ComparisonProblem(reviewer_label(finding, tid), finding.path, label_only=True)
    missing = s.missing_test_dependency(declared, code, output, root=root)
    if missing:
        advice = '' if missing.startswith('cannot import') else '; install the test dependencies and run the check again'
        return f'{tid}: {missing}{advice}'
    empty = pytest_no_pass(output)
    if empty:
        reason = f"{tid}: its test ran but no test passed ({empty})"
        return f"regression: {tid}'s test fails on this code\n{reason}" if regression else reason
    if code == 0:
        if notes is not None and not empty and not test_update_noted(notes, tid):
            note = f"{tid}: its test passed when this check ran it"
            if note not in notes:
                notes.append(note)
        return None
    if regression:
        return f"regression: {tid}'s test fails on this code"
    return f"NOT DONE  {tid} says {record['status']}, but its test failed when this check ran it (exit {code})"


def _test_bytes(root, path, base=None, staged=False):
    if base is not None:
        result = subprocess.run(['git', 'show', f'{base}:{path}'], cwd=root, capture_output=True, timeout=30)
        return result.stdout if result.returncode == 0 else None
    try:
        return s.read_bytes(root, path, staged)
    except (OSError, s.Refused):
        return None


def _pytest_disabling(text):
    """Count syntax that adds skipping without mistaking comments and strings for code."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, UnicodeError, ValueError):
        return Counter()
    aliases = {'pytest': 'pytest'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == 'pytest':
                    aliases[alias.asname or 'pytest'] = 'pytest'
        elif isinstance(node, ast.ImportFrom) and node.module == 'pytest':
            for alias in node.names:
                aliases[alias.asname or alias.name] = 'pytest.' + alias.name

    def name(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name(node.value) + '.' + node.attr
        return ''

    found = Counter()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            value = name(node)
            if value in {'pytest.mark.skip', 'pytest.mark.skipif', 'pytest.mark.xfail'}:
                found[value] += 1
        if isinstance(node, ast.Call) and name(node.func) in {'pytest.skip', 'pytest.xfail'}:
            found[name(node.func)] += 1
    return found


def _conftest_existing_tests(path, tests):
    folder = posixpath.dirname(path)
    return {test for test in tests if not folder or test.startswith(folder + '/')}


def _pytest_policy_changes(root, base, record, paths, staged, *, existing_tests=None):
    before = fields_at(root, record['id'], base) or {}
    pytest_command = uses_pytest(record) or uses_pytest(before)
    found = []
    configs = pytest_configs(root, record) | pytest_configs(root, before)
    for path in paths:
        name = posixpath.basename(path)
        old = _test_bytes(root, path, base) or b''
        new = _test_bytes(root, path, staged=staged) or b''
        if pytest_command and (name in PYTEST_CONFIG_NAMES | {'conftest.py'} or path in configs):
            new_conftest = name == 'conftest.py' and path not in repository_files(root, base)
            if new_conftest:
                selected = (selected_test_files(root, record, base, staged) & repository_files(root, base)
                            if existing_tests is None else existing_tests)
            if (name.endswith('.toml') and name not in {'pytest.toml', '.pytest.toml'}
                    and pytest_sections(old.decode('utf-8', 'replace'), native=name != 'pyproject.toml')
                    == pytest_sections(new.decode('utf-8', 'replace'), native=name != 'pyproject.toml')):
                continue
            if not new_conftest or _conftest_existing_tests(path, selected):
                found.append(f'pytest configuration changed since {base} ({path})')
    old_command, command = declared_test(before) or [], declared_test(record) or []

    def filters(words):
        return [word + (' ' + words[i + 1] if word in {'-k', '--deselect'} and i + 1 < len(words) else '')
                for i, word in enumerate(words)
                if word in {'-k', '--deselect'} or word.startswith('--deselect=') or (word.startswith('-k') and len(word) > 2)]

    if pytest_command and Counter(filters(command)) - Counter(filters(old_command)):
        found.append(f'pytest selection changed with -k or --deselect since {base}')
    return [f"{record['id']}: {line}" for line in found]


def pytest_skip_problems(root, base, record, paths, staged=False, *, timeout=None, counts=None):
    """An added marker matters only when the changed tests actually skip or xfail."""
    if not uses_pytest(record):
        return []
    added = {path: _pytest_disabling(_test_bytes(root, path, staged=staged) or b'')
                   - _pytest_disabling(_test_bytes(root, path, base) or b'')
             for path in paths if path.endswith('.py')}
    added = {path: markers for path, markers in added.items() if markers}
    if not added:
        return []
    tests = changed_pytest_tests(root, record, base, staged)
    command = selected_pytest_command(record, tests)
    with tempfile.TemporaryDirectory(prefix='workorder-skips-') as temporary:
        code, output = run_snapshot_test(root, Path(temporary) / 'code', command,
            s.test_timeout(record, timeout), counts, staged=staged,
            protected=repository_files(root, base), overlays=pytest_selection.overlay(tests), quiet=True)
    outcomes = pytest_counts(output) if isinstance(code, int) else None
    if outcomes is None or not (outcomes['skipped'] or outcomes['xfailed']):
        return []  # Execution failures have their own diagnostic.
    return [f"{record['id']}: new skip or xfail behavior since {base} ({path}: {', '.join(sorted(markers))})"
            for path, markers in sorted(added.items())]


def _internal_link_relative(link, root):
    """Normalize Windows extended paths before deciding whether a link stays inside root."""
    link = str(link)
    if link.startswith('\\\\?\\UNC\\'):
        link = '\\\\' + link[8:]
    elif link.startswith('\\\\?\\'):
        link = link[4:]
    # PureWindowsPath also lets the Windows spelling be verified on other systems.
    if PureWindowsPath(str(root)).is_absolute():
        if not PureWindowsPath(link).is_absolute():
            return None
        resolved_root = root.resolve() if isinstance(root, Path) else root
        resolved_link = Path(link).resolve() if os.name == 'nt' else link
        parent = PureWindowsPath(ntpath.normcase(ntpath.normpath(str(resolved_root))))
        target = PureWindowsPath(ntpath.normcase(ntpath.normpath(str(resolved_link))))
    else:
        target = Path(link)
        if not target.is_absolute():
            return None
        parent, target = root.resolve(), target.resolve()
    try:
        return Path(*target.relative_to(parent).parts)
    except ValueError:
        return None


def _snapshot_link(link, root, destination, parent):
    relative = _internal_link_relative(link, root)
    return os.path.relpath(destination / relative, parent) if relative is not None else link


def _snapshot_write(destination, path, content, mode=None, source_root=None):
    target = s.local_path(destination, path)
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    if content is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode is not None and mode & 0o170000 == 0o120000:
            link = os.fsdecode(content)
            if source_root is not None:
                link = _snapshot_link(link, source_root, destination, target.parent)
            target.symlink_to(link)
            return
        target.write_bytes(content)
        if mode is not None:
            target.chmod(mode & 0o777)


def _copy_submodule_files(root, destination, git_root):
    destination.mkdir(parents=True, exist_ok=True)
    if not (root / '.git').exists():
        return  # an uninitialized submodule has no working files to copy
    _copy_selected_files(root, destination, isolated=True, git_root=git_root)
    tracked = set(s._git_run(root, 'ls-files', '-z', isolated=True).stdout.split('\0')) - {''}
    tracked |= set(s._git_run(root, 'ls-tree', '-r', '--name-only', '-z', 'HEAD',
                              isolated=True).stdout.split('\0')) - {''}
    _link_ignored_dependencies(root, destination, tracked, isolated=True, git_root=git_root)


def _copy_selected_files(root, destination, staged=False, *, isolated=False, git_root=None):
    """Copy the index or working files Git knows, without walking ignored folders."""
    git_root = destination if git_root is None else git_root
    if staged:
        listing = s.git(root, 'ls-files', '--stage', '-z').stdout
        regular = []
        for entry in listing.split('\0'):
            if not entry:
                continue
            info, path = entry.split('\t', 1)
            mode, oid, stage = info.split()
            if stage != '0':
                raise s.Refused(f'unmerged staged test copy: {path}')
            if mode == '160000':
                _copy_submodule_files(root / path, destination / path, git_root)
            elif mode == '120000':
                _snapshot_write(destination, path, s.read_bytes(root, path, True), int(mode, 8), root)
            else:
                regular.append((path, oid, mode))
        for path, mode, content in s.checkout_contents(root, regular):
            _snapshot_write(destination, path, content, int(mode, 8), root)
        return
    args = ('ls-files', '-z', '--cached', '--others', '--exclude-standard')
    paths = (s._git_run(root, *args, isolated=True) if isolated else s.git(root, *args)).stdout
    for path in sorted(set(paths.split('\0')) - {''}):
        source, target = root / path, destination / path
        if source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            link = _snapshot_link(os.readlink(source), root, destination, target.parent)
            target.symlink_to(link, target_is_directory=source.is_dir())
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif source.is_dir():
            # Git lists a submodule as one path; its working files still come from Git.
            _copy_submodule_files(source, target, git_root)


def _link_ignored_dependencies(root, destination, protected, *, isolated=False, git_root=None):
    """Link ignored paths, collapsing whole folders and preserving tracked code on either side."""
    git_root = destination if git_root is None else git_root
    args = ('ls-files', '--others', '--ignored', '--exclude-standard', '--directory', '-z')
    ignored = (s._git_run(root, *args, isolated=True) if isolated else s.git(root, *args)).stdout
    parents = {parent.as_posix() for path in protected for parent in Path(path).parents}
    linked = []
    copied = False

    def link(path):
        nonlocal copied
        if (path in protected or any(part in {'.git', '__pycache__', '.pytest_cache'}
                                    for part in Path(path).parts)):
            return
        source, target = root / path, destination / path
        if path in parents:
            if source.is_dir() and not source.is_symlink():
                for child in source.iterdir():
                    link(child.relative_to(root).as_posix())
            return
        if target.exists() or target.is_symlink():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
        except OSError:
            if not copied:
                s.print_once('Note: dependency links are unavailable; copying ignored dependencies into the test copy')
                copied = True
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        linked.append(target.relative_to(git_root).as_posix())

    for path in ignored.split('\0'):
        if path:
            link(path.rstrip('/'))
    if linked:
        # A directory-only ignore rule does not match a symlink. Keep these shared
        # dependencies out of the copy's code fingerprint just as in the source.
        exclude = git_root / '.git/info/exclude'
        with exclude.open('a', encoding='utf-8') as stream:
            for path in linked:
                stream.write('\n/' + re.sub(r'([\\*?\[\] ])', r'\\\1', path) + '\n')


def _test_snapshot(root, destination, staged=False, protected=()):
    """Copy selected current files, with an independent Git directory and shared dependencies."""
    cloned = subprocess.run(['git', 'clone', '--quiet', '--no-checkout', '--no-hardlinks', str(root), str(destination)],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                            env=s.copy_environment())
    if cloned.returncode:
        raise s.Refused('could not create a temporary test copy: ' + cloned.stderr.strip())

    _copy_selected_files(root, destination, staged)
    s._git_run(destination, 'read-tree', 'HEAD', isolated=True)
    _link_ignored_dependencies(root, destination,
                               set(protected) | repository_files(root) | repository_files(root, 'HEAD'))


def snapshot_fingerprint(root, staged=False, overlays=None):
    """Identify the bytes a copy would execute, without creating that copy.

    Links and submodules fall back to the actual snapshot, whose rebasing and
    nested repositories cannot be described by a single file's bytes.
    """
    entries = {}
    if staged:
        listing = s.git(root, 'ls-files', '--stage', '-z', '--', *s.CODE_PATHS).stdout
        regular = []
        for entry in listing.split('\0'):
            if not entry:
                continue
            info, path = entry.split('\t', 1)
            mode, oid, stage = info.split()
            if stage != '0' or mode not in {'100644', '100755'}:
                return None
            regular.append((path, oid, mode))
        for path, mode, content in s.checkout_contents(root, regular):
            entries[path] = ('executable' if mode == '100755' and os.name != 'nt' else 'file',
                             s.digest(content))
    else:
        listing = s.git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                        '--', *s.CODE_PATHS).stdout
        for path in sorted(set(listing.split('\0')) - {''}):
            source = root / path
            try:
                mode = source.lstat().st_mode
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(mode):
                return None
            entries[path] = ('executable' if mode & 0o111 else 'file', s.digest(source.read_bytes()))
    for path, (content, mode) in (overlays or {}).items():
        if content is None:
            entries.pop(path, None)
        elif mode is None or mode & 0o170000 != 0o100000:
            return None
        else:
            entries[path] = ('executable' if mode & 0o111 and os.name != 'nt' else 'file', s.digest(content))
    fingerprint = hashlib.sha256()
    for path, (kind, content_hash) in sorted(entries.items()):
        fingerprint.update(s.packed([path, kind, content_hash]).encode('ascii') + b'\n')
    return fingerprint.hexdigest()


def needs_import_probe(root, paths=(), command=()):
    from .python_imports import interpreter, package_names, packages
    # Editable distributions live in the test interpreter, not necessarily in
    # repository discovery. Every identifiable Python command must probe even
    # when a cached result exists and the tree contains no discovered package.
    return bool(packages(root) or package_names(paths)
                or command and interpreter(command, root, s.copy_environment()))


def run_snapshot_test(root, destination, command, timeout, counts, *, staged=False,
                      protected=(), overlays=None, quiet=False):
    selected = set(protected) | set(overlays or {})
    if staged:
        selected |= repository_files(root)
    if s._REUSE_TESTS.get() and not s.ci_environment() and not needs_import_probe(root, selected, command):
        fingerprint = snapshot_fingerprint(root, staged, overlays)
        if fingerprint:
            run = s.read_test_cache(s.test_cache_path(root, fingerprint, command, timeout), command)
            if run is not None:
                return test_result(run, counts, quiet=quiet, failures_only=staged)
    _test_snapshot(root, destination, staged, protected)
    for path, (content, mode) in (overlays or {}).items():
        _snapshot_write(destination, path, content, mode, root)
    return _run_test(destination, _snapshot_command(command, root, destination), timeout, counts,
                     cache_root=root, logical_command=command, quiet=quiet, failures_only=staged)


def _snapshot_command(command, root, destination):
    """An absolute in-repository script or test path must also use the temporary copy."""
    path_type = PureWindowsPath if isinstance(root, PureWindowsPath) else Path
    result = []
    for word in command:
        option, separator, value = word.partition('=')
        path = value if separator and option.startswith('-') else word
        filename, node, selection = path.partition('::')
        try:
            relative = path_type(filename).relative_to(root)
        except ValueError:
            pass
        else:
            path = str(destination / relative) + node + selection
        result.append(option + separator + path if separator and option.startswith('-') else path)
    return result


def local_test_base(root, record=None):
    """Use only refs already available in this clone; never fetch for a local check."""
    override = (record or {}).get('merge_ref')
    refs = ([override.replace('refs/heads/', 'refs/remotes/origin/', 1)] if override
            else ['refs/remotes/origin/main', 'refs/remotes/origin/master'])
    for ref in refs:
        if not s.git(root, 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}', required=False).returncode:
            return ref.removeprefix('refs/remotes/')
    target = merge_target(root, record or {})
    branch = s.git(root, 'symbolic-ref', '--quiet', 'HEAD', required=False).stdout.strip()
    if target and target[0] != branch:
        return target[0].removeprefix('refs/heads/').removeprefix('refs/remotes/')
    return None


def comparison_base(root, ref):
    """Resolve a target once, retaining its name for explanations."""
    if s.git(root, 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}', required=False).returncode:
        raise s.Refused(f'base {ref} is not in this clone; fetch it'
                        ' (in GitHub Actions: actions/checkout with fetch-depth: 0)')
    parents = ['HEAD']
    merge_head = root / s.git(root, 'rev-parse', '--git-path', 'MERGE_HEAD').stdout.strip()
    if merge_head.is_file():
        parents += merge_head.read_text(encoding='ascii').split()
    # During a merge the index already contains the incoming parent. Git's
    # merge-base with several commits compares REF with their hypothetical merge.
    base = s.git(root, 'merge-base', ref, *parents, required=False).stdout.strip()
    if not base:
        raise s.Refused(f'HEAD and {ref} have no merge base; fetch the full history')
    short = s.git(root, 'rev-parse', '--short', base).stdout.strip()
    return base, f'{ref} (merge base {short})'


def implementation_base(root, record):
    ref = local_test_base(root, record)
    if not ref:
        target = merge_target(root, record)
        branch = s.git(root, 'symbolic-ref', '--quiet', 'HEAD', required=False).stdout.strip()
        if target and target[0] != branch:
            ref = target[0].removeprefix('refs/heads/').removeprefix('refs/remotes/')
    if ref:
        return comparison_base(root, ref)[0], ref
    created = s.git(root, 'log', '--diff-filter=A', '--format=%H', 'HEAD', '--',
                    f"tasks/{record['id']}.md").stdout.split()
    commit = created[0] if created else s.head(root)
    return commit, s.git(root, 'rev-parse', '--short', commit).stdout.strip()


def declared_code_paths(record, paths):
    scopes = {posixpath.normpath(path) for path in record['code_paths']}
    return {path for path in paths if any(scope == '.' or path == scope or path.startswith(scope + '/')
                                         for scope in scopes)}


def without_declared_code(record, paths):
    """A task's explicit code scope takes precedence over test-folder detection."""
    return paths - declared_code_paths(record, paths) if 'code_paths' in record else paths


def other_recorded_paths(root, record, base, changed, staged=False, end=None):
    """Only other tasks' new receipts covering these exact file versions narrow verification."""
    covered = set()
    current = s.code_tree(root, end or (s.index_tree(root) if staged else s.head(root)))
    original = s.code_tree(root, base)
    unrecorded = changed_paths(root, 'HEAD', staged) if end is None else set()
    for tid, other in records(root, staged, errors={}).items():
        if tid == record['id']:
            continue
        try:
            old = {entry['hash'] for entry in s.entries(root, tid, rev=base)}
            entries = s.entries(root, tid, staged)
        except s.Refused:
            continue  # unreadable receipts cannot narrow this task's protection
        for entry in entries:
            if (entry['hash'] in old or entry['kind'] not in {'implemented', 'test'} or entry['missing']
                    or not s.contains(root, entry['commit'], end or s.head(root))):
                continue
            if entry['kind'] == 'test' and (not entry['valid'] or entry['exit_code']):
                continue
            recorded = s.code_tree(root, entry['commit'])
            paths = {path for path in changed - unrecorded
                     if recorded.get(path) != original.get(path) and recorded.get(path) == current.get(path)}
            if 'implementation_paths' in entry:
                paths &= entry['implementation_paths'].keys()
            if 'code_paths' in other:
                paths &= declared_code_paths(other, paths) | strict_test_files(root, other, base, staged)
            covered |= paths
    return covered


def no_code_change_problem(root, record, base, staged=False, end=None):
    if end is None:
        changed = changed_paths(root, base, staged)
    else:
        changed = set(s.git(root, 'diff', '--name-only', '-z', '--no-renames', base, end).stdout.split('\0'))
    code = {path for path in changed if path and needs_task_receipt(path)}
    if code:
        tests = strict_test_files(root, record, base, staged)
        if 'code_paths' in record:
            code &= declared_code_paths(record, code) | tests
        else:
            code -= other_recorded_paths(root, record, base, code, staged, end) - tests
    if code:
        return (f"{record['id']}: code changed ({', '.join(sorted(code))}); "
                '--no-code-change is for verification tasks with no code change')
    return None


class ReviewProblem(str):
    """A test review finding, separate from a false completion claim."""
    def __new__(cls, text, path, line=1, label_only=False):
        value = super().__new__(cls, text)
        value.path, value.line = path, line
        value.label_only = label_only
        return value


class ComparisonProblem(ReviewProblem):
    """An import comparison could not run; only the task label accepts it."""


class AssertionProblem(ReviewProblem):
    """Unusual assertion syntax needs an explicit label, even in warning mode."""


class TestFirstProblem(ReviewProblem):
    """An explicit test-first rule is not downgraded by --review warn."""


def review_location(root, base, path, staged=False):
    args = ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--unified=0']
    if staged:
        args.append('--cached')
    delta = s.git(root, *args, base, '--', f':(literal){path}').stdout
    found = re.search(r'^@@ -[^ ]+ \+(\d+)', delta, re.M)
    return max(1, int(found[1])) if found else 1


def review_problem(root, base, text, path, staged=False, label_only=False):
    return ReviewProblem(text, path, review_location(root, base, path, staged), label_only)


def comparison_message(line, revision, ref, label):
    text = re.sub(r'(since )?' + re.escape(revision),
                  lambda match: 'since ' + label if match[1] else ref, line)
    return type(line)(text, line.path, line.line, line.label_only) if isinstance(line, ReviewProblem) else text


def reviewer_label(problem, tid):
    approval = '' if getattr(problem, 'label_only', False) else ' (or an approving review)'
    return problem.split('; a reviewer ', 1)[0] + (
        f'; a reviewer accepts it with the label workorder-allow:{tid}' + approval)


def _accepts_allow_label(problem):
    """A failing current test is refused outright; no label accepts it."""
    return '; a reviewer accepts ' in problem


def local_command_problems(root, base, record, staged=False, *, allow_finished=False,
                           notes=None, results=None, counts=None, timeout=None):
    pending = {entry['hash'] for entry in (s.proposed_receipts(root, record['id']) or [])}
    if (record['status'] not in FINISHED and committed_status(root, record['id'], base) not in FINISHED
            and not s.entries(root, record['id'], rev=base)
            and not [entry for entry in s.entries(root, record['id'], staged) if entry['hash'] not in pending]):
        return []
    problems = [reviewer_label(f"{record['id']}: {change} since {base}", record['id'])
                for change in requirement_changes(root, base, record, staged)
                if change.startswith('test changed:')]
    if (not problems or not allow_finished or record['status'] not in FINISHED
            or (committed_status(root, record['id'], base) not in FINISHED
                and not any(entry['kind'] == 'test' for entry in s.entries(root, record['id'], staged)))):
        return problems
    results = {} if results is None else results
    key = _test_key(record, timeout)
    command = declared_test(record)
    if command and key not in results and staged:
        with tempfile.TemporaryDirectory(prefix='workorder-command-') as temporary:
            results[key] = run_snapshot_test(root, Path(temporary) / 'code', command,
                                             s.test_timeout(record, timeout), counts, staged=True)
    failed = run_declared_test(root, record, counts, notes, results=results, timeout=timeout)
    if failed:
        return problems + [failed]
    if notes is not None:
        notes.append(test_update_note(record['id'], 'test command',
                                      renewed=current_test_receipt(root, record, staged)))
    return []


def test_update_note(tid, what, refactor=False, renewed=False):
    repair = (f'it is a declared refactor, so after committing run workorder renew --task {tid} --no-refactor '
              f'(or workorder done --task {tid} --needs-review)' if refactor
              else f'after committing run workorder renew --task {tid}')
    return (f'{tid}: {what} changed and passes on this code; ' + ('' if renewed else repair + '; ') +
            f'the pull request needs workorder-allow:{tid}')


def current_test_receipt(root, record, staged=False):
    """Renewal already covers the snapshot being checked, including its command."""
    run = latest_test_run(root, record, staged)
    code = s.index_code(root) if staged else s.code_digest(root, s.head(root))
    return bool(run and run.get('valid') and run.get('exit_code') == 0 and run['code'] == code
                and (staged or s.clean_code(root)))


def test_update_noted(notes, tid):
    return any(note.startswith(f'{tid}: test ') and 'changed and passes on this code;' in note
               for note in notes)


def unfinished_test_missing(root, record, staged=False):
    """Recognise a declared path that has not been written in this snapshot yet."""
    if record['status'] in FINISHED:
        return None
    command = declared_test(record) or []
    # Inline commands and Python module names are not test-file arguments.
    if not uses_pytest(record) and any(flag in command for flag in ('-c', '-m')):
        return None
    named = paths_named_by_command(command, root, include_missing=True)
    files = set(s.files(root, '.', staged))
    if any(not any(p == path or p.startswith(path + '/') for p in files) for path in named):
        return f"{record['id']}: its test file is not there yet; an unfinished task may land"
    return None


def local_test_update(root, record, staged=False, *, counts=None, timeout=None):
    """An existing finished task may commit passing test edits before renewing."""
    tid = record['id']
    before = fields_at(root, tid, 'HEAD')
    if record['status'] not in FINISHED or not before or before.get('status') not in FINISHED:
        return None, None
    command_changed = declared_test(record) != declared_test(before)
    paths = strict_test_files(root, record, 'HEAD', staged)
    # Configuration has its own shared finding; it does not need a per-task note.
    paths -= pytest_configs(root, record) | pytest_configs(root, before)
    paths = {path for path in paths if posixpath.basename(path) not in PYTEST_CONFIG_NAMES | {'conftest.py'}}
    changed = changed_paths(root, 'HEAD', staged)
    if not staged:
        changed |= changed_paths(root, 'HEAD', True)
    # Stop also checks the index when the edit is staged, so an unstaged repair
    # cannot stand in for the code about to be committed.
    test_staged = staged or bool((paths | {f'tasks/{tid}.md'}) & changed_paths(root, 'HEAD', True))
    if test_staged:
        merge_head = root / s.git(root, 'rev-parse', '--git-path', 'MERGE_HEAD').stdout.strip()
        if merge_head.is_file():
            for parent in merge_head.read_text(encoding='ascii').split():
                # Content brought in by the other parent is not a branch test edit.
                paths = {path for path in paths if _test_bytes(root, path, staged=True)
                         != _test_bytes(root, path, parent)}
    meaningful = changed_existing_test_paths(root, 'HEAD', paths, test_staged)
    if not command_changed and not meaningful:
        return None, None
    history = s.entries(root, tid, rev='HEAD')
    implemented = newest_implementation(history)
    runs = counting_runs(before, history[history.index(implemented) + 1:], implemented) if implemented else []
    if not runs or not runs[-1]['valid'] or runs[-1]['exit_code']:
        return None, None
    command = declared_test(record)
    if not command:
        return None, None
    if test_staged and declared_test(s.record(root, tid, True)) != command:
        return None, None
    with tempfile.TemporaryDirectory(prefix='workorder-update-') as temporary:
        result = run_snapshot_test(root, Path(temporary) / 'code', command,
                                   s.test_timeout(record, timeout), counts, staged=test_staged)
    failure = run_declared_test(root, record, counts, results={_test_key(record, timeout): result}, timeout=timeout)
    if failure:
        return None, failure
    what = 'test command' if command_changed else 'test files'
    return test_update_note(tid, what, latest_test_run(root, record, test_staged).get('refactor') is True,
                            current_test_receipt(root, record, test_staged)), None


def local_test_problems(root, base, record, *, pending_review=False, command_notes=False, **kwargs):
    ref = base
    base, label = comparison_base(root, base)
    kwargs.setdefault('base_label', ref)
    notes = kwargs.setdefault('notes', [])
    start = len(notes)
    kwargs.setdefault('results', {})
    staged = kwargs.get('staged', False)
    labeled = local_command_problems(root, base, record, staged,
                                    allow_finished=command_notes, notes=notes, results=kwargs['results'],
                                    counts=kwargs.get('counts'), timeout=kwargs.get('timeout'))
    if staged and record['status'] not in FINISHED and not kwargs.get('completed'):
        missing = unfinished_test_missing(root, record, staged=True)
        if missing:
            notes.append(missing)
            return labeled
    # Shared configuration belongs to one repository finding, even when several
    # task comparisons read it. The individual comparisons still run as usual.
    shared = repository_review_problems(root, base, records(root, kwargs.get('staged', False), errors={}),
        staged=kwargs.get('staged', False), config_only=True)
    compared = test_change_problems(root, base, record, review_configs=False, **kwargs)
    refactor_edit = reviewer_label(f"{record['id']}: its test files changed; use --needs-review", record['id'])
    result = kwargs['results'].get(_test_key(record, kwargs.get('timeout')))
    run = latest_test_run(root, record, staged)
    if (command_notes and refactor_edit in compared and record['status'] in FINISHED
            and run.get('valid') and run.get('exit_code') == 0 and result and result[0] == 0
            and not pytest_no_pass(result[1]) and not test_update_noted(notes, record['id'])):
        notes.append(test_update_note(record['id'], 'test files', refactor=True))
    repaired = command_notes and test_update_noted(notes, record['id'])
    for problem in shared + compared:
        if repaired and problem == refactor_edit:
            continue  # a passing local edit can commit before dropping the refactor declaration
        if repaired and str(problem).startswith(f"{record['id']}: its test files lost or changed"):
            continue  # the passing repair note already explains renewal and review
        if problem == wrapper_note(record) and not kwargs.get('completed') and record['status'] not in FINISHED:
            if problem not in notes:
                notes.append(problem)
        elif _accepts_allow_label(problem):
            line = str(problem) if problem in shared else reviewer_label(problem, record['id'])
            if pending_review:
                notes.append(line)
            else:
                labeled.append(line)
        else:
            labeled.append(problem)
    notes[start:] = [comparison_message(line, base, ref, label) for line in notes[start:]]
    if repaired:
        notes[:] = [line for line in notes if line != f"{record['id']}: its test passed when this check ran it"]
    return [comparison_message(line, base, ref, label) for line in labeled]


def latest_test_run(root, record, staged=False):
    """Only the latest counting run can carry completion flags."""
    history = s.entries(root, record['id'], staged)
    implemented = newest_implementation(history)
    if implemented is None:
        return {}
    runs = counting_runs(record, history[history.index(implemented) + 1:], implemented)
    return runs[-1] if runs else {}


def verification_task(root, record, staged=False):
    return latest_test_run(root, record, staged).get('no_code_change') is True


def pending_review_message(tid, findings=()):
    approval = '' if any(item['label_only'] for item in findings) else ' or an approving review'
    return f'pending review: the pull request needs workorder-allow:{tid}' + approval


def pending_findings(root, record, base=None, staged=False):
    if base is None:
        target = merge_target(root, record)
        base = target[1] if target else None
    carried = {entry['hash'] for entry in s.entries(root, record['id'], rev=base)} if base else set()
    found = []
    for entry in s.entries(root, record['id'], staged):
        if entry.get('pending_review') is True and entry['hash'] not in carried:
            for item in entry.get('pending_findings', []):
                if item not in found:
                    found.append(item)
    return found


def pending_review(root, record, base=None, staged=False):
    """New evidence does not withdraw an unmerged request for review."""
    if record['status'] not in FINISHED:
        return False
    carried = {entry['hash'] for entry in s.entries(root, record['id'], rev=base)} if base else set()
    return any(entry.get('pending_review') is True and entry.get('kind') == 'test'
               and entry['hash'] not in carried for entry in s.entries(root, record['id'], staged))


def covered_pending_findings(root, record, base):
    """Findings declared in this change by a completion covering the current tests.

    None means no such completion; an empty set still declares its own test edits.
    Other code may have moved since the declaration, so compare test file versions,
    not the whole repository digest. New comparison findings need their own declaration.
    """
    if record['status'] not in FINISHED:
        return None
    carried = {entry['hash'] for entry in s.entries(root, record['id'], rev=base)}
    protected = strict_test_files(root, record, base)
    current = s.code_tree(root, s.head(root))
    findings, implemented = None, None
    for entry in s.entries(root, record['id']):
        if entry['kind'] == 'implemented':
            implemented = entry
        if (entry['kind'] != 'test' or entry.get('pending_review') is not True
                or entry['hash'] in carried or not entry['valid'] or entry['exit_code']
                or entry.get('no_tests_passed') or implemented is None
                or not counting_runs(record, [entry], implemented)
                or not s.contains(root, entry['commit'], s.head(root))):
            continue
        recorded = s.code_tree(root, entry['commit'])
        if any(recorded.get(path) != current.get(path) for path in protected):
            continue
        if findings is None:
            findings = set()
        findings.update(item['message'] for item in entry.get('pending_findings', []))
    return findings


def waiting_for_review(root, record, staged=False):
    target = merge_target(root, record)
    # The declaration records a review request, not the review service's state.
    # Once the implementation has landed, the request no longer needs displaying.
    return pending_review(root, record, target[1] if target else None, staged)


def task_test_paths(root, record, base, staged=False):
    return {path for path in strict_test_files(root, record, base, staged)
            if posixpath.basename(path) not in PYTEST_CONFIG_NAMES | {'conftest.py'}
            and path not in pytest_configs(root, record)}


def task_code_paths(root, record, base, staged=False):
    changed = changed_paths(root, base, staged)
    if 'code_paths' in record:
        return declared_code_paths(record, {path for path in changed if not s.generated_path(path)})
    implemented = newest_implementation(s.entries(root, record['id'], staged))
    if implemented:
        paths = implemented.get('implementation_paths')
        if paths or paths is not None and verification_task(root, record, staged):
            return {path for path in paths if not s.generated_path(path)}
        if paths == {}:
            commit = implemented['commit'] if not implemented['missing'] else base
            return set(source_tree(root, commit))
        # Old receipts do not retain their original implementation boundary.
        # The current merge base may be newer than their commit, so diffing it
        # against that commit can erase ownership of already landed code.
        commit = implemented['commit'] if not implemented['missing'] else base
        return set(source_tree(root, commit))
    material = repository_test_files(root, base, records(root, staged, errors={}), staged)
    return {path for path in changed - material if needs_task_receipt(path)}


def task_change_touched(root, base, record, staged=False):
    material = repository_test_files(root, base, records(root, staged, errors={}), staged, owner=record)
    code = task_code_paths(root, record, base, staged) - material
    return bool(changed_paths(root, base, staged) & code or
                changed_test_paths(root, base, task_test_paths(root, record, base, staged), staged))


def completion_changed(root, base, record, staged=False):
    """A new or reopened claim owes implementation proof; a landed renewal does not."""
    tid, status = record['id'], record['status']
    if status not in FINISHED:
        return False
    before = fields_at(root, tid, base) or {}
    if before.get('status') != status:
        return True
    histories = task_history(root, base)
    if histories is None:
        commits = s.git(root, 'log', '--format=%H', f'{base}..HEAD', '--', f'tasks/{tid}.md').stdout.split()
        statuses = [committed_status(root, tid, commit) for commit in commits]
    else:
        statuses = [value for _, value in histories.get(tid, [])]
    if any(value != status for value in statuses):
        return True  # reopening and restoring the status makes a fresh claim
    return False


def finished_code_change(root, base, record, staged=False):
    """New completion evidence must describe the code a squash would retain."""
    if not completion_changed(root, base, record, staged):
        return None
    implemented = newest_implementation(s.entries(root, record['id'], staged))
    if not implemented or implemented['missing']:
        return None  # receipt validation already names the missing evidence
    commit = implemented['commit']
    if inherited_claim(root, record, staged) or inherited_claim(root, record, rev=s.head(root)):
        return None
    if not s.has_commit(root, commit):
        # Existing squash/rebase recovery has already matched the recorded path map.
        return None
    paths = task_code_paths(root, record, commit, staged)
    changed = sorted(paths & changed_paths(root, commit, staged))
    if not changed:
        return None
    tid = record['id']
    return (f"{tid}: its code changed after it was finished ({', '.join(changed)}); "
            f"run workorder renew --task {tid} and commit the receipts, "
            'so its evidence matches this code and survives a squash merge')


def stub_problem(root, base, record, *, staged=False, timeout=None, counts=None, results=None, notes=None):
    """A passing test must fail when the task's changed Python functions are unavailable."""
    run = latest_test_run(root, record, staged)
    if run.get('refactor') or run.get('no_code_change'):
        return None
    command = declared_test(record)
    if not command:
        return None
    tests = changed_pytest_tests(root, record, base, staged)
    command = selected_pytest_command(record, tests)
    from .python_stubs import changed_functions
    all_records = records(root, staged, errors={})
    material = repository_test_files(root, base, all_records, staged, owner=record)
    material |= strict_test_files(root, record, base, staged)
    if 'code_paths' in record:
        candidates = task_code_paths(root, record, base, staged)
    else:
        candidates = changed_paths(root, base, staged)
        for tid, other in all_records.items():
            if tid != record['id'] and 'code_paths' in other:
                candidates -= task_code_paths(root, other, base, staged)
    overlays, descriptions = {}, []
    for path in sorted(candidates - material):
        if not path.endswith('.py') or not needs_task_receipt(path):
            continue
        current = _test_bytes(root, path, staged=staged)
        if current is None:
            continue
        try:
            content, names = changed_functions(_test_bytes(root, path, base), current)
        except (SyntaxError, ValueError, UnicodeError) as error:
            return f"{record['id']}: could not stub changed functions in {path}: {error}"
        if content is not None:
            overlays[path] = content, None
            descriptions.append(f'{", ".join(names)} in {path}')
    if not overlays:
        return None
    overlays.update(pytest_selection.overlay(tests))
    results = {} if results is None else results
    key = ('stub', record['id'], base, staged, _test_key(record, timeout))
    if key not in results:
        with tempfile.TemporaryDirectory(prefix='workorder-stub-') as temporary:
            results[key] = run_snapshot_test(root, Path(temporary) / 'code', command,
                s.test_timeout(record, timeout), counts, staged=staged, overlays=overlays, quiet=True)
    code, output = results[key]
    tests = pytest_selection.selected(output, tests)
    from .python_imports import ImportFailure
    if isinstance(code, ImportFailure):
        ref = local_test_base(root, record) or base
        finding = ComparisonProblem(f"{record['id']}: could not compare with {ref}: {code}",
                                    f"tasks/{record['id']}.md", label_only=True)
        return ComparisonProblem(reviewer_label(finding, record['id']), finding.path, label_only=True)
    if isinstance(code, str) or code in {5, 124, 127} or uses_pytest(record) and code in {3, 4}:
        return f"{record['id']}: could not run its test with changed functions stubbed out: {code}"
    if code == 0:
        subject = 'its new or changed tests still pass' if tests else 'its test still passes'
        names = test_names(tests) if tests else '; '.join(descriptions)
        text = (f"{record['id']}: {subject} with the changed functions stubbed out "
                f"({names}); it does not exercise the change")
        if tests:
            text += f"; changed functions: {'; '.join(descriptions)}"
        finding = AssertionProblem(text, next(iter(overlays)), label_only=True)
        return AssertionProblem(reviewer_label(finding, record['id']), finding.path, label_only=True)
    if tests and notes is not None:
        passed = re.findall(r'^PASSED (.+)$', output, re.M)
        passing = [test for test in tests if any(node == test or node.startswith(test + '[') for node in passed)]
        if passing:
            note = (f"{record['id']}: some new or changed tests still pass with the changed functions "
                    f"stubbed out ({test_names(passing)})")
            if note not in notes:
                notes.append(note)
    return None


def local_regression_problems(root, base, *, exclude=(), staged=False, timeout=None,
                              notes=None, results=None, counts=None, pending_review=False, jobs=None):
    """Run finished tasks whose recorded code or selected tests the change touches."""
    revision, _ = comparison_base(root, base)
    changed = changed_paths(root, revision, staged)
    results = {} if results is None else results
    problems, selected = [], []
    for tid, record in sorted(records(root, staged, errors={}).items()):
        if tid in exclude or record['status'] not in FINISHED:
            continue
        try:
            advice = finished_code_change(root, revision, record, staged)
            if advice and notes is not None and advice not in notes:
                notes.append(advice)
            protected = strict_test_files(root, record, revision, staged)
            protected |= task_code_paths(root, record, revision, staged)
            if protected & changed:
                selected.append(record)
        except (s.Refused, OSError, ValueError, subprocess.SubprocessError) as error:
            problems.append(f'{tid}: could not select its current test: {error}')
    prefetch_tests(root, selected, results, counts, timeout, staged, jobs)
    for record in selected:
        tid = record['id']
        try:
            if strict_test_files(root, record, revision, staged) & changed:
                problems += local_test_problems(root, base, record, staged=staged, timeout=timeout,
                    notes=notes if notes is not None else [], results=results, counts=counts,
                    pending_review=staged or pending_review, command_notes=staged)
            failed = run_declared_test(root, record, counts, notes, results=results,
                                       regression=True, timeout=timeout)
            if failed:
                problems.append(failed)
        except (s.Refused, OSError, ValueError, subprocess.SubprocessError) as error:
            problems.append(f'{tid}: could not check its current test: {error}')
    return problems


def _ref_test_snapshot(root, destination, commit):
    cloned = subprocess.run(['git', 'clone', '--quiet', '--no-checkout', '--no-hardlinks', str(root), str(destination)],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=30,
                            env=s.copy_environment())
    if cloned.returncode:
        raise s.Refused('could not create a temporary REF copy: ' + cloned.stderr.strip())
    for option in ('core.autocrlf', 'core.eol'):
        configured = s.git(root, 'config', '--get', option, required=False)
        if configured.returncode == 0:
            s._git_run(destination, 'config', option, configured.stdout.strip(), isolated=True)
    s._git_run(destination, 'checkout', '--quiet', '--detach', commit, isolated=True)
    tracked = repository_files(root, commit)
    for relative in tracked:
        path = destination / relative
        if path.is_symlink():
            link = os.readlink(path)
            rebased = _snapshot_link(link, root, destination, path.parent)
            if rebased != link:
                directory = path.is_dir()
                path.unlink()
                path.symlink_to(rebased, target_is_directory=directory)
    _link_ignored_dependencies(root, destination, tracked)


def run_ref_test(root, destination, revision, command, timeout, counts, overlays=None):
    # A REF checkout is immutable. Remember the stable result under its revision
    # and checkout settings as well as its byte fingerprint, avoiding a copy just
    # to look up that fingerprint next time. Never save a run that changed files.
    alias = None
    selected = repository_files(root, revision) | set(overlays or {})
    if not s.ci_environment() and not needs_import_probe(root, selected, command):
        settings = s.git(root, 'config', '--list').stdout
        contents = [(path, s.digest(content) if content is not None else None, mode)
                    for path, (content, mode) in sorted((overlays or {}).items())]
        key = s.digest(s.packed(['ref-v4', revision, command, timeout, settings, contents]).encode('ascii'))
        folder = root / s.git(root, 'rev-parse', '--git-path', 'workorder-tests').stdout.strip()
        alias = folder / ('ref-' + key + '.json')
        if s._REUSE_TESTS.get():
            run = s.read_test_cache(alias, command)
            if run is not None:
                return test_result(run, counts, quiet=True)
        try:
            alias.unlink(missing_ok=True)
        except OSError:
            pass
    _ref_test_snapshot(root, destination, revision)
    for path, (content, mode) in (overlays or {}).items():
        _snapshot_write(destination, path, content, mode, root)
    from .python_imports import missing_base_import
    copied_command = _snapshot_command(command, root, destination)
    missing = missing_base_import(destination, root, copied_command)
    if missing:
        return 2, f"ModuleNotFoundError: No module named '{missing}' (absent from comparison code)\n"
    before = s._test_state(destination, isolated=True) if alias else None
    result = _run_test(destination, copied_command, timeout, counts,
                       quiet=True, cache_root=root, logical_command=command)
    if before and s._test_state(destination, isolated=True) == before:
        cached = s.test_cache_path(root, before[1], command, timeout)
        temporary = alias.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            # cached_test writes this only after its own stability checks pass.
            temporary.write_bytes(cached.read_bytes())
            os.replace(temporary, alias)
        except OSError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    return result


def test_change_problems(root, base, record, timeout=None, *, staged=False, strict=False, allow=False,
                         allow_lines=False, run_tests=True, counts=None, notes=None, results=None,
                         completed=False, review_files=True, proven=None, prove_change=False, base_label=None,
                         allow_failure=None, review_configs=True):
    """Keep comparison errors attached to their task so other tasks are still checked."""
    try:
        return _test_change_problems(root, base, record, timeout, staged=staged, strict=strict, allow=allow,
                                     allow_lines=allow_lines, run_tests=run_tests, counts=counts, notes=notes,
                                     results=results, completed=completed, review_files=review_files,
                                     proven=proven, prove_change=prove_change, base_label=base_label,
                                     allow_failure=allow_failure, review_configs=review_configs)
    except (OSError, s.Refused, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as error:
        if notes is not None:
            notes.append(f"{record['id']}: REF comparison skipped against {base}: {error}")
        return [f"{record['id']}: could not compare its changed tests with {base}: {error}"]


def _test_change_problems(root, base, record, timeout=None, *, staged=False, strict=False, allow=False,
                          allow_lines=False, run_tests=True, counts=None, notes=None, results=None,
                          completed=False, review_files=True, proven=None, prove_change=False, base_label=None,
                          allow_failure=None, review_configs=True):
    """Review changed tests and compare both versions on current code without writing receipts.

    Allow accepts reviewed edits, REF failures and outcome changes, never a current-test failure.
    allow_lines accepts every changed test line, including lines owned by a task.
    """
    notes = [] if notes is None else notes
    results = {} if results is None else results
    tid = record['id']
    base_label = base_label or base
    protected = strict_test_files(root, record, base, staged)
    paths = sorted(changed_test_paths(root, base, protected, staged))
    before = fields_at(root, tid, base)
    wrapper = wrapper_note(record)
    candidates = repository_files(root, base) | repository_files(root) | raw_changed_paths(root, base, staged)
    for definition in (before, record):
        if definition is None:
            continue
        named = definition.get('test_paths', pytest_collection_paths(definition) if uses_pytest(definition) else [])
        for path in named:
            normalized = _repository_test_path(root, path)
            if not any(p == normalized or p.startswith(normalized + '/') for p in candidates):
                note = f'{tid}: {path} does not exist in either version; no test files selected for that path'
                if note not in notes:
                    notes.append(note)
    configs = pytest_configs(root, record) | pytest_configs(root, before or {})
    configs |= {path for path in protected if posixpath.basename(path) in PYTEST_CONFIG_NAMES | {'conftest.py'}}
    existing_only = bool(before and before.get('status') in FINISHED)
    changed = test_file_changes(root, base, record, staged, strict,
                                protected=protected if review_configs else protected - configs,
                                existing_only=existing_only)
    policy = ([changed] if changed else []) if review_files else []
    policy += [line for line in _pytest_policy_changes(root, base, record, paths, staged)
               if (review_files or 'pytest selection changed' in line)
               and (review_configs or 'pytest configuration changed' not in line)]
    assertions = assertion_problems(root, base, record, staged)
    problems = [wrapper] if wrapper else []
    carried = {entry['hash'] for entry in s.entries(root, tid, rev=base)}
    run = latest_test_run(root, record, staged)
    identities = {run.get('hash'), run.get('legacy', {}).get('hash')}
    if run.get('no_code_change') is True and not identities & carried:
        exemption_problem = no_code_change_problem(root, record, base, staged)
        if exemption_problem:
            if allow if allow_failure is None else allow_failure:
                notes.append(exemption_problem + ' (allowed by workorder-allow:' + tid + ')')
            else:
                problems.append(reviewer_label(ReviewProblem(exemption_problem, f'tasks/{tid}.md', label_only=True), tid))
    for problem in assertions if review_files else []:
        if allow if allow_failure is None else allow_failure:
            notes.append(str(problem) + ' (allowed by workorder-allow:' + tid + ')')
        else:
            problems.append(AssertionProblem(reviewer_label(problem, tid), problem.path, problem.line, True))
    refactor = latest_test_run(root, record, staged).get('refactor') is True
    compare = changed_existing_test_paths if existing_only else changed_test_paths
    refactor_changes = refactor and compare(root, base, refactor_test_files(root, record, base, staged), staged)

    def test_failure(line):
        if completed or record['status'] in FINISHED:
            problems.append(line)
        else:
            notes.append(line.removeprefix('NOT DONE  ') + '; task is not claimed finished')

    finishing = completed or record['status'] in FINISHED
    new_completion = completed or completion_changed(root, base, record, staged)

    def review(line, what='it', lines=False, label_only=False):
        accepted = (allow if allow_failure is None else allow_failure) if label_only else allow or (allow_lines and lines)
        if accepted:
            notes.append(line + ' (allowed)')
            notes.append(f'accepted: {tid} ({what if what != "it" else "test review"})')
        else:
            path = next(iter(paths or sorted(protected)), f'tasks/{tid}.md')
            finding = review_problem(root, base, line, path, staged, label_only)
            problems.append(ReviewProblem(reviewer_label(finding, tid), finding.path, finding.line, label_only))

    def unavailable(code):
        from .python_imports import ImportFailure
        if not isinstance(code, ImportFailure):
            return False
        if allow if allow_failure is None else allow_failure:
            notes.append(f'{tid}: could not compare with {base}: {code} (allowed)')
        else:
            finding = ComparisonProblem(f'{tid}: could not compare with {base}: {code}',
                                        f'tasks/{tid}.md', label_only=True)
            problems.append(ComparisonProblem(reviewer_label(finding, tid), finding.path, label_only=True))
        return True

    def addition_rule():
        if not existing_only and task_test_paths(root, record, base, staged) & set(paths) and not changed:
            review(f"{tid}: its test files changed since {base} ({', '.join(paths)}); "
                   f"its tests already fail on {base}, so any change needs review")

    for line in policy:
        review(line, lines='pytest selection changed' not in line)
    if refactor_changes:
        review(f'{tid}: its test files changed; use --needs-review', lines=True)
    if not run_tests:
        notes.append(f'{tid}: REF comparison skipped against {base}: use --run-tests to run it')
        return problems
    command = declared_test(record)
    if not command:
        notes.append(f'{tid}: REF comparison skipped against {base}: no concrete current test command')
        if paths:
            test_failure(f'{tid}: its test files changed but it declares no test for this check to run')
        return problems
    try:
        with tempfile.TemporaryDirectory(prefix='workorder-tests-') as temporary:
            snapshot = Path(temporary) / 'code'
            current_root = Path(temporary) / 'current'
            # Share the logical command key with --all-tests even when the staged run uses a copy.
            key = _test_key(record, timeout)
            if key not in results:
                results[key] = run_snapshot_test(root, current_root, command, s.test_timeout(record, timeout), counts,
                                                 staged=staged, protected=repository_files(root, base),
                                                 quiet=staged and not finishing)
            if unavailable(results[key][0]):
                return problems
            failed = run_declared_test(root, record, counts, notes, strict, results, timeout=timeout,
                                       regression=finishing and not new_completion)
            if failed:
                test_failure(failed)
            for line in pytest_skip_problems(root, base, record, paths, staged, timeout=timeout, counts=counts):
                review(line, lines=True)
            base_command = declared_test(before) if before else None

            def ref_code_result(ref_command, overlays=None):
                revision = s.git(root, 'rev-parse', f'{base}^{{commit}}').stdout.strip()
                contents = tuple((path, s.digest(content) if content is not None else None, mode)
                                 for path, (content, mode) in sorted((overlays or {}).items()))
                own_key = ('ref-code', revision, tuple(ref_command), s.test_timeout(record, timeout), contents)
                if own_key not in results:
                    own_root = Path(temporary) / ('ref-' + str(len(results)))
                    results[own_key] = run_ref_test(root, own_root, revision, ref_command,
                                                   s.test_timeout(record, timeout), counts, overlays)
                return results[own_key]

            if new_completion and not failed:
                stub = stub_problem(root, base, record, staged=staged, timeout=timeout, counts=counts,
                                    results=results, notes=notes)
                if stub:
                    if allow if allow_failure is None else allow_failure:
                        notes.append(str(stub) + ' (allowed by workorder-allow:' + tid + ')')
                    else:
                        problems.append(stub)
            if ((new_completion or prove_change) and task_change_touched(root, base, record, staged)
                    and not failed and (refactor or not verification_task(root, record, staged))):
                overlays = {}
                # Shared helpers and fixtures must be current too: an import of
                # a new helper from an old file is not evidence about base code.
                material = protected | repository_test_files(root, base, records(root, staged, errors={}),
                                                              staged, owner=record)
                for path in sorted(material & changed_paths(root, base, staged)):
                    content = _test_bytes(root, path, staged=staged)
                    mode = None
                    if content is not None:
                        if staged:
                            info = s.git(root, 'ls-files', '--stage', '--', f':(literal){path}').stdout.split()
                            mode = int(info[0], 8) if info else None
                            if mode and stat.S_ISREG(mode):
                                content = s.checkout_bytes(root, path)
                        else:
                            mode = (root / path).lstat().st_mode
                            if stat.S_ISLNK(mode):
                                content = os.fsencode(os.readlink(root / path))
                    overlays[path] = content, mode
                tests = [] if refactor else changed_pytest_tests(root, record, base, staged)
                own_command = selected_pytest_command(record, tests)
                overlays.update(pytest_selection.overlay(tests))
                own_code, own_output = ref_code_result(own_command, overlays)
                tests = pytest_selection.selected(own_output, tests)
                if unavailable(own_code):
                    return problems
                missing = s.missing_test_dependency(command, own_code, own_output, root=root)
                # A new repository module is expected to be absent before implementation.
                module = re.search(r"No module named\s+['\"]?([\w.]+)", own_output)
                new_module = False
                if module:
                    for prefix in ('', 'src/'):
                        stem = prefix + module[1].replace('.', '/')
                        matches = {path for path in candidates if path == stem + '.py'
                                   or path.startswith(stem + '/') and path.endswith('.py')}
                        new_module |= bool(matches) and not bool(matches & repository_files(root, base))
                if (isinstance(own_code, str) or own_code == 124
                        or uses_pytest(record) and own_code in {3, 4} or missing and not new_module):
                    test_failure(f'{tid}: could not run its current test on {base}: {missing or own_code}')
                elif refactor:
                    if own_code != 0 or pytest_no_pass(own_output):
                        test_failure(f'{tid}: a declared refactor needs its test to pass on {base}')
                    elif not refactor_changes:
                        notes.append(f'{tid} is a declared refactor: tests unchanged, passing before and after')
                elif own_code == 0:
                    subject = (f'its new or changed tests ({test_names(tests)}) pass' if tests else 'its test passes')
                    review(f"{tid}: {subject} on {base}'s code too, so it does not show the change works; "
                           'make the test check the new behaviour', 'this refactor or verification task')
                elif not assertions and own_code != 5 and proven is not None:
                    proven.add(tid)
            # There is nothing to rerun when all selected test files are new.
            selected = selected_test_files(root, record, base, staged)
            existing = selected & repository_files(root, base)
            if not existing or not base_command:
                reason = 'no selected test files at REF' if not existing else 'no concrete test command at REF'
                # New tests have no old version to compare. Their origin note
                # already explains this; missing selections still need a reason.
                if existing or not selected:
                    notes.append(f'{tid}: REF comparison skipped against {base}: {reason}')
                return problems
            base_key = ('base', base, tuple(paths), tuple(base_command), s.test_timeout(record, timeout), staged)
            if not paths and base_command == command:
                results[base_key] = results[key]
            if base_key not in results:
                overlays = {}
                for path in paths:
                    content = _test_bytes(root, path, base)
                    mode = None
                    if content is not None:
                        info = s.git(root, 'ls-tree', base, '--', f':(literal){path}').stdout.split()
                        mode = int(info[0], 8) if info else None
                    overlays[path] = content, mode
                results[base_key] = run_snapshot_test(root, snapshot, base_command,
                    s.test_timeout(record, timeout), counts, staged=staged,
                    protected=repository_files(root, base), overlays=overlays, quiet=True)
            base_code, base_output = results[base_key]
            if unavailable(base_code):
                return problems
            if isinstance(base_code, str):
                notes.append(f'{tid}: REF comparison skipped against {base}: {base_code}')
                missing = s.missing_test_dependency(base_command, base_code, base_output, root=root)
                test_failure(f'{tid}: could not run its tests from {base}: {missing or base_code}')
                return problems
            if base_code != 0:
                # An unfinished task may have acceptance tests before the implementation.
                # Attribute a failure only after proving those tests pass on their own code.
                own_code, own_output = ref_code_result(base_command)
                if unavailable(own_code):
                    return problems
                missing = s.missing_test_dependency(base_command, own_code, own_output, root=root)
                if missing or isinstance(own_code, str):
                    if own_output and (finishing or not staged):
                        s.print_test_output(own_output, failed=True)
                    test_failure(f'{tid}: could not run its tests on {base}: {missing or own_code}')
                    return problems
                if own_code != 0:
                    addition_rule()
                    if finishing:
                        s.print_test_output(base_output, failed=True, label=f'Tests from {base_label} on this code:')
                        review(f'{tid}: its tests from {base} fail on this code',
                               'this intended behavior change or test rename', label_only=True)
                    else:
                        notes.append(f'{tid}: its tests already fail on {base} code; this change is not blamed for that failure')
                    return problems
                if finishing or not staged:
                    s.print_test_output(base_output, failed=True, label=f'Tests from {base_label} on this code:')
                if paths or finishing:
                    review(f'{tid}: its tests from {base} fail on this code',
                           'this intended behavior change or test rename', label_only=True)
                else:
                    test_failure(f'{tid}: its tests from {base} fail on this code')
                return problems
            if paths and not changed:
                own_code, own_output = ref_code_result(base_command)
                if unavailable(own_code):
                    return problems
                if not isinstance(own_code, str) and own_code != 0:
                    addition_rule()
            if failed and record['status'] not in FINISHED:
                return problems
            if uses_pytest(record) and uses_pytest(before):
                old_counts = pytest_counts(base_output)
                new_counts = pytest_counts(results[key][1])
                if old_counts is None or new_counts is None:
                    problems.append(f'{tid}: cannot compare test outcomes with {base}; pytest did not report its outcomes')
                else:
                    if new_counts['passed'] < old_counts['passed']:
                        review(f"{tid}: fewer tests pass than at {base} ({old_counts['passed']} -> {new_counts['passed']})",
                               'this reduced pass count')
                    for outcome in ('skipped', 'xfailed', 'deselected'):
                        if new_counts[outcome] > old_counts[outcome]:
                            review(f'{tid}: more tests are {outcome} than at {base} ({old_counts[outcome]} -> {new_counts[outcome]})',
                                   f'this increase in {outcome} tests')
    except (OSError, s.Refused, ValueError, subprocess.SubprocessError) as error:
        notes.append(f'{tid}: REF comparison skipped against {base}: {error}')
        problems.append(f'{tid}: could not compare its changed tests with {base}: {error}')
    return problems


class Views(dict):
    """Verdicts worked out on first use, so a scoped check reads only the tasks it needs."""

    def __init__(self, root, all_records, staged=False, *, allow_no_code_change=()):
        super().__init__()
        self.root, self.all_records, self.staged = root, all_records, staged
        self.allow_no_code_change = allow_no_code_change

    def __missing__(self, tid):
        self[tid] = verdict(self.root, self.all_records[tid], self.staged,
                            allow_no_code_change=tid in self.allow_no_code_change)
        return self[tid]


def dependency_cycles(all_records):
    """Strongly connected components: only participants get a cycle problem."""
    indices, low, stack, active, cycles = {}, {}, [], set(), {}

    def visit(tid):
        indices[tid] = low[tid] = len(indices)
        stack.append(tid)
        active.add(tid)
        children = [w[5:] for w in s.dependencies(all_records[tid])
                    if w.startswith("task:") and w[5:] in all_records]
        for other in children:
            if other not in indices:
                visit(other)
                low[tid] = min(low[tid], low[other])
            elif other in active:
                low[tid] = min(low[tid], indices[other])
        if low[tid] == indices[tid]:
            group = []
            while True:
                other = stack.pop()
                active.remove(other)
                group.append(other)
                if other == tid:
                    break
            if len(group) > 1 or tid in children:
                for other in group:
                    cycles[other] = "dependency cycle: " + ", ".join(sorted(group))

    for tid in all_records:
        if tid not in indices:
            visit(tid)
    return cycles


def removals(root, base, names, allow, staged=False):
    before = s.metadata_texts(root, base, None, False)
    old_tasks = {p[6:-3] for p in before if p.startswith("tasks/") and p.endswith(".md")
                 and s.ID.fullmatch(p[6:-3])}
    retired, problems = set(), []
    for tid in sorted(old_tasks - set(names)):
        if (tid in allow and committed_status(root, tid, base) in FINISHED | {"CANCELLED"}
                and not s.receipt_paths(root, staged, tid=tid)):
            retired.add(tid)
        else:
            problems.append(f"{tid}: task file removed since {base}; removing a DONE, CLOSED or CANCELLED"
                            f" task requires all its receipts removed and --allow {tid}")
    return retired, problems


def _count(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def worked_tasks(changed):
    """Tasks with changed metadata, also eligible for a test run when their files are unchanged."""
    return ({p[6:-3] for p in changed if p.startswith('tasks/') and p.endswith('.md')
             and s.ID.fullmatch(p[6:-3])}
            | {s.receipt_task(p) for p in changed} - {None})


LOCKFILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'Pipfile.lock',
             'uv.lock', 'Cargo.lock', 'go.sum', 'Gemfile.lock', 'composer.lock'}


def needs_task_receipt(path):
    return (not s.generated_path(path) and not source.documentation(path) and posixpath.basename(path) not in LOCKFILES
            and not path.startswith(('tasks/', '.workorder/', '.notquite/', 'docs/', '.github/'))
            and not path.endswith(('.md', '.rst', '.txt', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf'))
            and posixpath.basename(path) not in {'.gitignore', '.gitattributes', '.gitmodules', '.editorconfig'}
            and not posixpath.basename(path).startswith('LICENSE'))


def source_tree(root, revision, protected=None):
    """Compare source without changing the historical fingerprint stored in receipts."""
    return source.entries(root, s.code_tree(root, revision), protected=protected)


def recorded_paths_match(root, tree, implemented):
    paths = implemented['implementation_paths']
    protected = test_material(root, 'HEAD')
    if s.has_commit(root, implemented['commit']):
        protected = protected | test_material(root, implemented['commit'])
    if all(tree.get(path) == entry for path, entry in paths.items()
           if path in protected or not source.documentation(path)):
        return True
    current = source.entries(root, tree, protected=protected)
    recorded = source.entries(root, paths, implemented.get('implementation_syntax'),
                              missing_ok=True, protected=protected)
    return all(current.get(path) == entry for path, entry in recorded.items())


def same_source_code(root, before, after):
    if not s.has_commit(root, before):
        return False
    protected = test_material(root, before) | test_material(root, after)
    return source_tree(root, before, protected) == source_tree(root, after, protected)


def recorded_branch_code(root, implemented, base, commit, tree=None, *, base_tree=None, parents=()):
    """A replay keeps recorded bytes and modes; everything else must be target code."""
    paths = implemented.get('implementation_paths')
    # The staged tree will also descend from its pending merge parents.
    if not paths or not any(s.contains(root, base, parent) for parent in (commit, *parents)):
        return False
    protected = test_material(root, base_tree or base) | test_material(root, tree or commit)
    if s.has_commit(root, implemented['commit']):
        protected = protected | test_material(root, implemented['commit'])
    recorded = source.entries(root, paths, implemented.get('implementation_syntax'),
                              missing_ok=True, protected=protected)
    if not recorded:
        return False
    current = source_tree(root, tree or commit, protected)
    target = source_tree(root, base_tree or base, protected)
    return (all(current.get(path) == entry for path, entry in recorded.items())
            and all(current.get(path) == target.get(path)
                    for path in (current.keys() | target.keys()) - recorded.keys()))


def receipt_covers_change(root, implemented, base, staged=False):
    """Accept the recorded tree plus an exact, clean merge of target-branch work.

    Comparing the resulting trees preserves every byte and mode, including whitespace.
    An ancestor alone would also accept later, untested edits on the task branch.
    """
    code = s.index_code(root) if staged else s.code_digest(root, s.head(root))
    if implemented['code'] == code:
        return True
    original = implemented['commit']
    tree = s.git(root, 'write-tree').stdout.strip() if staged else s.head(root)
    if not implemented['missing'] and same_source_code(root, original, tree):
        return True
    if recorded_branch_code(root, implemented, base, s.head(root), tree):
        return True
    if implemented['missing'] or not s.contains(root, original, s.head(root)):
        return False
    merged = s.git(root, 'merge-tree', '--write-tree', original, base, required=False)
    tree = merged.stdout.splitlines()[0] if merged.stdout else ''
    return (merged.returncode == 0 and bool(re.fullmatch(r'[0-9a-f]{40,64}', tree))
            and s.code_digest(root, tree) == code)


def renewal_advice(root, base, changed, staged=False):
    for tid in sorted(worked_tasks(changed)):
        try:
            implemented = newest_implementation(s.entries(root, tid, staged))
            if (implemented and not implemented['missing']
                    and not s.contains(root, base, implemented['commit'])):
                return f'main moved since {tid} was tested: merge it and run workorder renew --task {tid}'
        except s.Refused:
            continue
    return None


def has_work_receipt(root, base, changed, staged=False, allow=(), new_tasks=None):
    for tid in sorted({s.receipt_task(path) for path in changed} - {None}):
        try:
            before = fields_at(root, tid, base)
            if ((before is None and new_tasks is None) or before and before.get('status') in FINISHED) and tid not in allow:
                continue
            record = s.record(root, tid, staged)
            command = declared_test(record)
            if not command:
                continue
            old = {entry['hash'] for entry in s.entries(root, tid, rev=base)}
            history = s.entries(root, tid, staged)
            recognise_squashed(root, tid, history, staged)
            implemented = newest_implementation(history)
            if (not implemented or implemented['missing']
                    or not receipt_covers_change(root, implemented, base, staged)):
                continue
            if implemented['hash'] not in old:
                if before is None and tid not in allow and new_tasks is not None:
                    new_tasks.add(tid)
                    continue
                return True
            runs = counting_runs(record, history[history.index(implemented) + 1:], implemented)
            if runs and runs[-1]['hash'] not in old and runs[-1]['valid'] and runs[-1]['exit_code'] == 0:
                return True
        except (s.Refused, ValueError):
            continue  # invalid receipts are reported by the task's ordinary checks
    return False


def check_summary(counts, run_tests=False, single_unclaimed=False, unchanged_all_tests=False,
                  unchanged_base=False):
    claims = "not claimed finished" if single_unclaimed else f"{counts['claimed']} claimed DONE or CLOSED"
    line = f"checked {_count(counts['tasks'], 'task')} ({claims})"
    if unchanged_base:
        line = 'no task changed in this change'
    if run_tests and not (unchanged_base and not counts.get('tests') and not counts.get('unowned_tests')):
        ran = counts.get('tests', 0)
        cached = counts.get('cached', 0)
        tasks = len(counts.get('tested_tasks', ()))
        if unchanged_all_tests:
            line = 'no task changed in this change'
        line += f"; ran {tasks} " + ('finished ' if unchanged_all_tests else '')
        line += "task's declared test" if tasks == 1 else "tasks' declared tests"
        if cached and cached == ran:
            line += " (cached)"
        elif cached:
            line += " (some comparisons cached)"
        if counts.get('unowned_tests'):
            line += f"; compared {_count(counts['unowned_tests'], 'unowned test file')}"
    if counts.get('tests_only'):
        n = counts['tests_only']
        rest = ("supported claim rests on its declared test only." if n == 1
                else "supported claims rest on their declared tests only.")
        line = f"Acceptance lists are not checked; {n} {rest}\n" + line
    return line


def summarize_notes(notes, verbose=False):
    """Group identical kinds of task notes, retaining the full lines with --verbose."""
    notes = list(dict.fromkeys(notes))
    # A command repair already gives the same renewal and review advice as a file edit.
    file_repairs = {test_update_note(note.split(':', 1)[0], 'test files') for note in notes
                    if ': test command changed and passes on this code;' in note}
    notes = [note for note in notes if note not in file_repairs]
    if verbose:
        return notes
    groups = {}
    for note in notes:
        match = re.match(r'([A-Z][A-Z0-9]*-[1-9][0-9]*)(?:: | )(.*)', note)
        exemption = any(text in note for text in ('finished with --no-code-change', 'is a declared refactor',
                                                   'DONE pending review', "'s test was written in this change"))
        key = match[2] if match and not exemption else note
        groups.setdefault(key, []).append(note)
    result = []
    for kind, lines in groups.items():
        if len(lines) == 1:
            result.extend(lines)
        elif kind == 'its test passed when this check ran it':
            result.append(f"{len(lines)} tasks' tests passed when this check ran them")
        elif kind == 'its test file is not there yet; an unfinished task may land':
            result.append(f'{len(lines)} tasks: their test files are not there yet; an unfinished task may land')
        elif not re.match(r'[A-Z][A-Z0-9]*-[1-9][0-9]*\b', lines[0]):
            result.extend(lines)
        else:
            result.append(f'{len(lines)} tasks: {kind}')
    return result


def unowned_test_failure(root, base, path, material, staged, timeout, counts):
    """Compare an unowned Python test using its base files on the current code."""
    if not path.endswith('.py') or posixpath.basename(path) == 'conftest.py':
        return None
    if path not in repository_files(root, base):
        return None
    command = [sys.executable, '-m', 'pytest', '-q', path]
    overlays = {}
    for changed in material & changed_paths(root, base, staged):
        content = _test_bytes(root, changed, base)
        info = s.git(root, 'ls-tree', base, '--', f':(literal){changed}').stdout.split()
        overlays[changed] = content, int(info[0], 8) if info else None
    with tempfile.TemporaryDirectory(prefix='workorder-unowned-') as temporary:
        code, output = run_snapshot_test(root, Path(temporary) / 'current', command, timeout, counts,
                                        staged=staged, protected=repository_files(root, base),
                                        overlays=overlays, quiet=True)
        if counts is not None:
            counts['unowned_tests'] = counts.get('unowned_tests', 0) + 1
        if code == 0:
            return None
        before, before_output = run_ref_test(root, Path(temporary) / 'base', base, command, timeout, counts)
        missing = s.missing_test_dependency(command, before, before_output, root=root)
        if missing or isinstance(before, str) or before in {5, 124, 127}:
            raise s.Refused(f'could not compare {path}: tests on {base}: {missing or before}')
        if isinstance(code, str) or code in {5, 124, 127}:
            raise s.Refused(f'could not compare {path}: tests from {base} on this code: {code}')
        if before != 0:
            return None
        return f'{path}: its version from {base} fails on this code (behaviour changed)'


def repository_review_problems(root, base, all_records, staged=False, allow=(), strict_tests=False, errors=(), notes=None,
                               run_tests=False, timeout=None, counts=None, explicit_allow=None, config_only=False):
    """Review each changed test file once, including material without a task owner."""
    problems = []
    changed = changed_paths(root, base, staged)
    owners = {}
    for tid, record in all_records.items():
        if tid not in errors:
            for path in strict_test_files(root, record, base, staged):
                owners.setdefault(path, set()).add(tid)
    material = repository_test_files(root, base, all_records, staged) | set(owners)
    for path in sorted(changed_test_paths(root, base, material & changed, staged)):
        named = sorted(owners.get(path, ()))
        label = ', '.join(named) if named else 'tests'
        tests_record = {'id': label, 'test': 'pytest', 'test_paths': [path]}
        config_named = named
        existing_tests = None
        if posixpath.basename(path) == 'conftest.py' and path not in repository_files(root, base):
            selected = {tid: selected_test_files(root, record, base, staged) & repository_files(root, base)
                        for tid, record in all_records.items() if tid not in errors}
            config_named = [tid for tid in named if _conftest_existing_tests(path, selected[tid])]
            patterns = pytest_patterns(root, base, staged)
            existing_tests = {test for test in material & repository_files(root, base)
                              if posixpath.basename(test) not in PYTEST_CONFIG_NAMES | {'conftest.py'}
                              and _matches_test_name(test, patterns)}
            existing_tests.update(test for tests in selected.values() for test in tests)
        config_lines = _pytest_policy_changes(root, base, tests_record, [path], staged,
                                              existing_tests=existing_tests)
        if run_tests and not named:
            unowned = {**tests_record, 'test': s.join_command([sys.executable, '-m', 'pytest', '-q', path])}
            config_lines += pytest_skip_problems(root, base, unowned, [path], staged,
                                                 timeout=timeout, counts=counts)
        if config_only and not any('pytest configuration changed' in line for line in config_lines):
            continue
        labels = set(allow if explicit_allow is None else explicit_allow)
        accepted_assertion = bool(set(named) & labels) if named else 'tests' in labels
        for problem in assertion_problems(root, base, tests_record, staged, paths={path}, material=material):
            if accepted_assertion:
                if notes is not None:
                    notes.append(str(problem) + ' (allowed by explicit label)')
            else:
                problems.append(AssertionProblem(reviewer_label(problem, named[0] if named else 'tests'),
                                                 path, problem.line, True))
        is_config = any('pytest configuration changed' in line for line in config_lines)
        finished = any((fields_at(root, tid, base) or {}).get('status') in FINISHED for tid in named)
        edits = None if is_config else test_file_changes(root, base, tests_record, staged, strict_tests,
                                                         protected={path}, existing_only=finished)
        if run_tests and not named:
            try:
                failure = unowned_test_failure(root, base, path, material, staged, timeout, counts)
            except (s.Refused, OSError, ValueError, subprocess.SubprocessError) as error:
                problems.append(str(error))
                failure = None
            if failure:
                labels = allow if explicit_allow is None else explicit_allow
                if 'tests' not in labels:
                    finding = review_problem(root, base, failure, path, staged, label_only=True)
                    problems.append(ReviewProblem(reviewer_label(finding, 'tests'), path, finding.line, True))
                elif notes is not None:
                    notes.append(f'{failure} (allowed by workorder-allow:tests)')
                edits = None
        lines = ([edits] if edits else []) + config_lines
        for line in lines:
            line_owners = config_named if line in config_lines and 'pytest configuration changed' in line else named
            if line_owners != named:
                line = (', '.join(line_owners) or 'tests') + line[len(label):]
            if 'tests' not in allow and not set(line_owners) & set(allow):
                problems.append(review_problem(root, base,
                    reviewer_label(line, line_owners[0] if line_owners else 'tests'), path, staged))
            elif notes is not None:
                notes.append(f'accepted: {path} (changed lines)')
    return problems


def code_path_problems(root, base, changed, code, all_records, staged, allow, notes):
    """Optional scopes apply only when every task adding receipts declares one."""
    contributors = []
    for tid in sorted({s.receipt_task(path) for path in changed} - {None}):
        if tid not in all_records:
            continue
        try:
            old = {entry['hash'] for entry in s.entries(root, tid, rev=base)}
            if any(entry['hash'] not in old for entry in s.entries(root, tid, staged)):
                contributors.append(tid)
        except s.Refused:
            continue  # ordinary receipt checks report invalid evidence
    if not contributors or any('code_paths' not in all_records[tid] for tid in contributors):
        return []
    scopes = {posixpath.normpath(path) for tid in contributors for path in all_records[tid]['code_paths']}
    outside = sorted(path for path in code
                     if not any(scope == '.' or path == scope or path.startswith(scope + '/') for scope in scopes))
    problems = []
    for path in outside:
        if {'scope:' + tid for tid in contributors} & set(allow):
            notes.append(f'accepted: {path} (outside code paths of {", ".join(contributors)})')
        else:
            problems.append(reviewer_label(f'{path} is outside the code paths of {", ".join(contributors)}',
                                           "scope:" + contributors[0]))
    return problems


@s.read_session()
@s.test_output_session()
def check(root, staged=False, base=None, notes=None, allow=(), run_tests=False, counts=None,
          strict_tests=False, all_tests=False, require_task=False, timeout=None, review="refuse",
          approving_review=False, require_test_first=False, jobs=None, require_task_ignore=(), local_hook=False):
    counts = {} if counts is None else counts
    notes = [] if notes is None else notes
    revision, label = comparison_base(root, base) if base else (None, None)
    start = len(notes)
    explicit_allow = set(allow)
    if approving_review and base:
        paths = s.metadata_texts(root, revision)
        allow = explicit_allow | set(task_names(root, staged)) | {'tests', 'no-task'}
        allow |= {'scope:' + tid for tid in task_names(root, staged)}
        allow |= {Path(path).stem for path in paths if path.startswith('tasks/') and s.ID.fullmatch(Path(path).stem)}
        allow |= {s.receipt_task(path) for path in paths} - {None}
        allow |= {s.receipt_task(path) for path in s.receipt_paths(root, staged)} - {None}
    with s.test_reuse(staged and not run_tests):
        problems = _check(root, staged, revision, notes, allow, run_tests, counts,
                          strict_tests, all_tests, require_task, timeout, base_label=base,
                          explicit_allow=explicit_allow, require_test_first=require_test_first,
                          jobs=jobs, require_task_ignore=require_task_ignore, local_hook=local_hook)
    if base:
        notes[start:] = [comparison_message(line, revision, base, label) for line in notes[start:]]
        problems = [comparison_message(line, revision, base, label) for line in problems]
    pending_messages = {comparison_message(line, revision, base, label)
                        for line in counts.pop('_pending_findings', ())} if base else set()
    problems = [problem for problem in problems
                if not (isinstance(problem, ReviewProblem) and str(problem) in pending_messages)]
    if approving_review:
        notes += ['approval did not cover: ' + str(problem) for problem in problems
                  if getattr(problem, 'label_only', False)]
    problems = [problem for problem in problems
                if not (isinstance(problem, ComparisonProblem) and problem.split(':', 1)[0] in explicit_allow)]
    warnings = [problem for problem in problems if isinstance(problem, ReviewProblem)
                and not isinstance(problem, (TestFirstProblem, AssertionProblem, ComparisonProblem))] if base and review == 'warn' else []
    counts['warnings'] = warnings
    problems = [problem for problem in problems if problem not in warnings]
    refused = set(re.findall(r'\b[A-Z][A-Z0-9]*-[1-9][0-9]*\b', '\n'.join(problems)))
    counts['tests_only'] = len(counts.pop('_supported', set()) - refused)
    accepted = sorted({note.removeprefix('accepted: ') for note in notes if note.startswith('accepted: ')})
    notes[:] = [note for note in notes if not note.startswith('accepted: ')]
    if approving_review and accepted:
        notes.append('accepted by an approving review: ' + ', '.join(accepted))
    return list(dict.fromkeys(problems))


def _check(root, staged=False, base=None, notes=None, allow=(), run_tests=False, counts=None,
           strict_tests=False, all_tests=False, require_task=False, timeout=None, base_label=None,
           explicit_allow=(), require_test_first=False, jobs=None, require_task_ignore=(), local_hook=False):
    """Problems that refuse the check.

    Plain check reads every task. With --staged (the pre-commit hook) or --base REF (CI) it judges the
    change: the tasks it touches and the tasks that depend on them. A task file that cannot be read is
    a problem for that task only. With base, a change to what a task requires (its test, contract or
    dependencies) fails unless the task is in allow; with run_tests, CI runs each claimed task's test.
    """
    notes = [] if notes is None else notes
    counts = {} if counts is None else counts
    errors = {}
    all_records = records(root, staged, errors)
    names = task_names(root, staged)
    reference = base or ("HEAD" if staged else None)
    if reference and s.git(root, "rev-parse", "--verify", "--quiet", f"{reference}^{{commit}}",
                           required=False).returncode:
        if base:
            raise s.Refused(f"base {base} is not in this clone; fetch it"
                            " (in GitHub Actions: actions/checkout with fetch-depth: 0)")
        reference = None  # the first commit: nothing to compare with, check everything
    if reference:
        selected, changed = scope(root, reference, staged, names, all_records,
                                  include_tests=bool(base), strict_tests=strict_tests, errors=errors)
    else:
        selected, changed = set(names), None
    counts.update(tasks=len(selected), claimed=sum(r["status"] in FINISHED for tid, r in all_records.items()
                                                   if tid in selected), tests=0, cached=0, tests_only=0)
    problems = [f"{tid}: {message.removeprefix(tid + ': ')}" for tid, message in sorted(errors.items())
                if tid in selected]
    updated_tests = set()
    if not base and (staged or local_hook):
        for tid, record in sorted(all_records.items()):
            if tid in errors:
                continue
            target = local_test_base(root, record)
            if target:
                revision, _ = comparison_base(root, target)
                advice = finished_code_change(root, revision, record, staged)
                if advice:
                    notes.append(advice)
            note, failure = local_test_update(root, record, staged, counts=counts, timeout=timeout)
            if note:
                notes.append(note)
                updated_tests.add(tid)
            if failure:
                problems.append(failure)
    new_tasks = set()
    if base:
        for path in sorted(changed or ()):
            if not path.startswith('.github/workflows/'):
                continue
            previous = _test_bytes(root, path, base) or b''
            current = _test_bytes(root, path, staged=staged) or b''
            if (path == '.github/workflows/workorder.yml'
                    or b'python -m workorder.review_labels' in previous + current):
                text = f'{path}: Workorder workflow changed; a reviewer accepts it with the label workorder-allow:workflow'
                if 'workflow' in explicit_allow:
                    notes.append(text + ' (allowed)')
                else:
                    problems.append(AssertionProblem(text, path, label_only=True))
    if require_task and changed:
        test_material = repository_test_files(root, base, all_records, staged)
        for record in all_records.values():
            test_material |= strict_test_files(root, record, base, staged)
        code = {path for path in changed - test_material if needs_task_receipt(path)
                and not any(fnmatch.fnmatchcase(path, pattern) for pattern in require_task_ignore)}
        recorded = has_work_receipt(root, base, changed, staged, allow, new_tasks)
        if not code or recorded:
            new_tasks.clear()
        if 'no-task' in allow:
            if code and not recorded:
                notes.append('accepted: no-task (code without task work)')
            new_tasks.clear()
        elif code and not recorded and not new_tasks:
            problems.append('this change edits code but no task records it with a new implemented or test receipt; '
                            'run workorder receipt implemented --task ID or workorder receipt test --task ID')
            advice = renewal_advice(root, base, changed, staged)
            if advice:
                problems.append(advice)
        if code:
            problems += code_path_problems(root, base, changed, code, all_records, staged, allow, notes)
    stray = [path for path in s.files(root, "tasks", staged) if not path.endswith(".md")
             and (changed is None or path in changed)]
    problems += [f"{path}: only task files belong in tasks/; a file here is outside the code fingerprint,"
                 " so changes to it are never checked" for path in stray]
    retired = set()
    if base:
        retired, removed_problems = removals(root, base, names, allow, staged)
        problems += removed_problems + base_problems(root, base, staged, retired)
        notes += [f"{tid}: task and all receipts removed (allowed)" for tid in sorted(retired)]
        notes += [f'accepted: {tid} (removed task and receipts)' for tid in sorted(retired)]
        problems += [message for tid, record in all_records.items()
                     if tid in selected and (message := wrapper_note(record))]
        for tid, record in all_records.items():
            if tid not in selected or tid in errors or record.get('status') not in FINISHED:
                continue
            before = fields_at(root, tid, base) or {}
            if declared_test(record) is None and declared_test(before) is None:
                continue
            mapped = {path for path in strict_test_files(root, record, base, staged)
                      if (path in repository_files(root, base)
                          or (path in repository_files(root) if staged else s.local_path(root, path).is_file()))
                      and posixpath.basename(path) not in PYTEST_CONFIG_NAMES | {'conftest.py'}}
            if mapped or not any(_repository_test_file(path) for path in repository_files(root, base)):
                continue
            problems.append(f'{tid}: no test files are known for its test; set test_paths')
        problems += repository_review_problems(root, base, all_records, staged, allow, strict_tests, errors, notes,
                                               run_tests, timeout, counts, explicit_allow)
    views = Views(root, all_records, staged, allow_no_code_change=explicit_allow if base else ())
    # Include removed receipts and ledgers whose task file was also removed.
    paths = set(s.receipt_paths(root, rev="HEAD")) | set(s.receipt_paths(root, staged))
    tids = {s.receipt_task(path) for path in paths
            if changed is None or path in changed or s.receipt_task(path) in selected} - {None}
    for tid in sorted(tids):
        if tid in retired:
            continue
        try:
            s.entries(root, tid, staged)
        except s.Refused as error:
            problems.append(f"{tid}: {str(error).removeprefix(tid + ': ')}")
            errors.setdefault(tid, str(error))
            continue
        if tid not in all_records and tid not in errors:
            problems.append(f"{tid}: receipt ledger has no task record")
    visiting, visited = set(), set()
    cycles = dependency_cycles(all_records)
    problems += [f"{tid}: {message}" for tid, message in sorted(cycles.items()) if tid in selected]
    results = {}
    proven = set()
    supported = set()
    worked = worked_tasks(changed) if changed is not None else set(names)
    reopened = {tid for tid, record in all_records.items()
                if base and record['status'] in {'IN_PROGRESS', 'BLOCKED'}
                and committed_status(root, tid, base) in FINISHED}
    selected |= reopened
    runnable = []
    for tid, record in sorted(all_records.items()):
        if tid in errors:
            continue
        comparison = bool(base and tid in selected and
                          (tid in worked or strict_test_files(root, record, base, staged) & changed
                           or record['status'] in FINISHED and task_change_touched(root, base, record, staged)))
        if (tid in reopened or run_tests and (comparison or record['status'] in FINISHED
                                             and (all_tests or tid in worked))):
            runnable.append(record)
    prefetch_tests(root, runnable, results, counts, timeout, staged, jobs, snapshot=bool(base),
                   protected=repository_files(root, base) if base else ())

    def walk(tid):
        if tid in visiting:
            return
        if tid in visited:
            return
        visiting.add(tid)
        for wait in s.dependencies(all_records[tid]):
            if wait.startswith("task:"):
                other = wait[5:]
                if other not in all_records:
                    if other not in errors:
                        problems.append(f"{tid}: missing dependency {other}")
                else:
                    walk(other)
        visiting.remove(tid)
        visited.add(tid)

    for tid, record in sorted(all_records.items()):
        if tid not in selected or tid in errors:
            continue
        note = wrapper_note(record)
        if note and not base and note not in notes:
            notes.append(note)
        try:
            views[tid]  # read the task's receipts and contract now, so their errors belong to this task
        except s.Refused as error:
            problems.append(f"{tid}: {str(error).removeprefix(tid + ': ')}")
            continue
        walk(tid)
        # blocks is informational: it is checked for consistency but never enforced.
        for other in record.get("blocks", []):
            if other not in all_records and other not in errors:
                problems.append(f"{tid}: blocks unknown task {other}")
            elif other in all_records and tid in all_records[other].get("blocks", []) and tid < other:
                problems.append(f"{tid} and {other} each say they block the other")
        try:
            debt = claim_debt(record, all_records, views)
            if tid in updated_tests:
                debt = [item for item in debt if not item.startswith('a passing test on')]
            found = (f"NOT DONE  {tid} says {record['status']} but owes: {', '.join(debt)}."
                     f" Details: workorder check {tid}. If work is unfinished, set status back to"
                     " IN_PROGRESS in the same commit as the failing test receipt.") if debt else stale_claim(root, record, staged, base)
        except s.Refused as error:  # a dependency that cannot be read
            found = f"{tid}: {error}"
        merged = computed_merge(root, record, newest_implementation(s.entries(root, tid, staged)))
        if merged:
            notes.append(f"{tid}: {merge_line(merged)}")
        if found:
            problems.append(found)
        elif record['status'] in FINISHED and record['contract'] == 'none' and tid not in cycles:
            supported.add(tid)
        if base:
            advice = finished_code_change(root, base, record, staged)
            if advice:
                problems.append(advice)
            if tid in reopened:
                failure = (run_declared_test(root, record, counts, notes, results=results, timeout=timeout)
                           if declared_test(record) else None)
                if failure:
                    text = (f'{tid} was reopened while its test fails on this code; '
                            f'a reviewer accepts it with the label workorder-allow:{tid}')
                    if tid in explicit_allow:
                        notes.append(text + ' (allowed)')
                    else:
                        problems.append(AssertionProblem(text, f'tasks/{tid}.md', label_only=True))
            if record['status'] in FINISHED:
                unchanged, origin = test_origin(root, record, base, base_label or base, staged)
                notes.append(origin)
                if require_test_first and not unchanged and tid not in explicit_allow:
                    problems.append(TestFirstProblem(test_first_problem(tid, base_label or base),
                                                     f'tasks/{tid}.md', label_only=True))
            if (tid in worked or strict_test_files(root, record, base, staged) & changed
                    or record['status'] in FINISHED and task_change_touched(root, base, record, staged)):
                problems += test_change_problems(root, base, record, timeout, staged=staged,
                                                 strict=strict_tests, allow=tid in allow,
                                                 allow_lines='tests' in allow, run_tests=run_tests,
                                                 counts=counts, notes=notes, results=results, review_files=False,
                                                 proven=proven, prove_change=tid in new_tasks, base_label=base_label,
                                                 allow_failure=tid in explicit_allow)
            if fields_at(root, tid, base) is None and record["status"] in FINISHED:
                line = (f'{tid} was written and finished in this pull request; a reviewer reads its definition '
                        f'and accepts it with an approving review or workorder-allow:{tid}')
                if tid in allow:
                    notes.append(line + " (definition allowed)")
                    notes.append(f'accepted: {tid} (new task)')
                else:
                    problems.append(line)
            elif fields_at(root, tid, base) is None and tid in new_tasks:
                # Keep the existing receipt rule for unfinished tasks. Only a
                # new finished claim always needs definition acceptance.
                line = (f'{tid} is new in this pull request: a reviewer accepts its definition with the label '
                        f'workorder-allow:{tid} (or an approving review)')
                if tid in proven:
                    notes.append(f'{tid} is new in this pull request; its test fails on {base} and passes now')
                elif tid in allow:
                    notes.append(line + ' (definition allowed)')
                    notes.append(f'accepted: {tid} (new task)')
                else:
                    problems.append(line)
            if pending_review(root, record, base, staged):
                findings = pending_findings(root, record, base, staged)
                label_only = any(item['label_only'] for item in findings)
                line = f'{tid}: ' + pending_review_message(tid, findings)
                line += ''.join('\n  Pending: ' + item['message'] for item in findings)
                if tid in (explicit_allow if label_only else allow):
                    notes.append(f'accepted: {tid} (pending review)')
                else:
                    counts.setdefault('_pending_findings', []).extend(item['message'] for item in findings)
                    if label_only:
                        problems.append(AssertionProblem(line, f'tasks/{tid}.md', label_only=True))
                    else:
                        problems.append(review_problem(root, base, line, f'tasks/{tid}.md', staged))
            for change in requirement_changes(root, base, record, staged):
                line = f"{tid}: {change} since {base}"
                if tid in allow:
                    notes.append(line + " (allowed)")
                    notes.append(f'accepted: {tid} ({change})')
                else:
                    problem = reviewer_label(line, tid)
                    if change.startswith(('test changed:', 'test_paths changed')):
                        problem = review_problem(root, base, problem, f'tasks/{tid}.md', staged)
                    problems.append(problem)
        elif staged:
            if waiting_for_review(root, record, staged):
                notes.append(f'{tid}: ' + pending_review_message(tid, pending_findings(root, record, staged=staged)))
            local_base = local_test_base(root, record)
            revision, label = comparison_base(root, local_base) if local_base else ('HEAD', None)
            for change in requirement_changes(root, revision, record, staged):
                if not change.startswith('test changed:'):
                    notes.append(f"{tid}: {change} " + (f'since {label}' if label else 'in this commit'))
            if not local_base:
                problems += local_command_problems(root, revision, record, staged=True, allow_finished=True,
                    notes=notes, results=results, counts=counts, timeout=timeout)
                missing = unfinished_test_missing(root, record, staged=True)
                if missing:
                    notes.append(missing)
            owns_change = False
            if local_base and changed:
                try:
                    owns_change = bool(strict_test_files(root, record, revision, True) & changed)
                except (OSError, s.Refused, ValueError, TypeError, KeyError, AttributeError,
                        subprocess.SubprocessError):
                    owns_change = False
            if local_base and (tid in worked or owns_change):
                problems += local_test_problems(root, local_base, record, timeout=timeout, staged=True,
                                                counts=counts, notes=notes, results=results, pending_review=True,
                                                command_notes=True)
        if run_tests and tid in worked and not all_tests and not found and record["status"] in FINISHED:
            if staged and declared_test(record) and _test_key(record, timeout) not in results:
                with tempfile.TemporaryDirectory(prefix='workorder-current-') as temporary:
                    results[_test_key(record, timeout)] = run_snapshot_test(
                        root, Path(temporary) / 'code', declared_test(record),
                        s.test_timeout(record, timeout), counts, staged=True)
            failed = run_declared_test(root, record, counts, notes, strict_tests, results, timeout=timeout)
            if failed:
                problems.append(failed)
    if all_tests:
        for record in all_records.values():
            if record['status'] in FINISHED:
                if staged and declared_test(record) and _test_key(record, timeout) not in results:
                    with tempfile.TemporaryDirectory(prefix='workorder-current-') as temporary:
                        results[_test_key(record, timeout)] = run_snapshot_test(
                            root, Path(temporary) / 'code', declared_test(record),
                            s.test_timeout(record, timeout), counts, staged=True)
                failed = run_declared_test(root, record, counts, notes, strict_tests, results,
                                           regression=True, timeout=timeout)
                if failed:
                    problems.append(failed)
    elif staged and not base:
        targets = {local_test_base(root, record) for record in all_records.values()}
        for target in sorted(targets - {None}):
            problems += local_regression_problems(root, target, staged=True, timeout=timeout,
                                                  notes=notes, results=results, counts=counts, jobs=jobs)
    if base:
        for tid, record in sorted(all_records.items()):
            if record['status'] not in FINISHED:
                continue
            run = latest_test_run(root, record, staged)
            if run.get('no_code_change'):
                notes.append(f'{tid} finished with --no-code-change')
            if run.get('refactor') and not any(note.startswith(f'{tid} is a declared refactor') for note in notes):
                notes.append(f'{tid} is a declared refactor')
            if pending_review(root, record, base, staged):
                notes.append(f'{tid} DONE pending review')
    if not staged:
        from . import project_tests
        if run_tests:
            problems += project_tests.run(root, base=base, label=base_label, notes=notes, counts=counts,
                                           allow=allow, timeout=timeout)
        elif base and project_tests.command(root) is None:
            notes.append(project_tests.UNSET)
    refused = set(re.findall(r'\b[A-Z][A-Z0-9]*-[1-9][0-9]*\b', '\n'.join(problems)))
    counts['_supported'] = supported
    counts['tests_only'] = len(supported - refused)
    if supported - refused and not run_tests and not counts.get('tests'):
        notes.append('Evidence: ' + RECORDED_EVIDENCE)
    return list(dict.fromkeys(problems))


RECORDED_EVIDENCE = 'recorded receipts support the claim (not re-run here; `check --run-tests` re-runs it)'


def evidence_status(record, computed, debt, rerun=False):
    if record['status'] in FINISHED and computed in FINISHED and not debt:
        if rerun:
            return 'the declared test passed on this code'
        return RECORDED_EVIDENCE + f'; computed: {computed}'
    return 'claim not supported on this code' if debt and computed == 'CLOSED' else computed


def claim_debt(record, all_records, views):
    """What a DONE or CLOSED status line claims but the evidence does not yet support."""
    claimed = record["status"]
    if claimed not in {"DONE", "CLOSED"}:
        return []
    _, done, delivery = views[record["id"]]
    return done + (delivery if claimed == "CLOSED" else []) + waits(record, all_records, views)


def claim_details(root, record, all_records, views):
    """Keep claim problems separate from the recorded evidence verdict in both views."""
    debt = claim_debt(record, all_records, views)
    stale = None if debt else stale_claim(root, record)
    if stale and "uncommitted code changes" in stale:
        debt = ["evidence for the uncommitted code changes"]
    elif stale:
        implemented = newest_implementation(s.entries(root, record['id']))
        debt = [f"a passing test on the code marked {record['status']} (it differs from {implemented['commit'][:7]})"]
    return debt, stale


def stale_result(record):
    return (f"the {record['status']} claim is on code other than the tested code; "
            f"run workorder renew --task {record['id']} on the branch before pushing")


def done_step(record):
    prerequisite = (f"set a concrete test: command in tasks/{record['id']}.md and "
                    if record.get('test', '').strip() == ANY_TEST else "")
    return prerequisite + f"run workorder done --task {record['id']}"


def next_step(item, record, code_changed=False, root=None, finish=False):
    tid = record["id"]
    command = record.get("test", "").strip() or "<your test command>"
    if '--no-code-change is for verification tasks' in item:
        return f'record the code change with workorder renew --task {tid}, without --no-code-change'
    if item.startswith('its test ran but no test passed'):
        return 'the test must report at least one passing test; fix it, then run workorder done again'
    if root is not None and item.startswith('a passing test on'):
        if record['status'] in FINISHED and s.dirty_paths(root):
            return f'commit the code and test changes, then run workorder renew --task {tid}'
        history = s.entries(root, tid)
        implemented = newest_implementation(history)
        runs = counting_runs(record, history[history.index(implemented) + 1:], implemented) if implemented else []
        if runs and runs[-1]['exit_code'] != 0:
            return 'fix the code or the test, then run workorder done again'
    if finish:
        if item == "a declared test":
            return (f'add the command that proves it to tasks/{tid}.md, e.g. test: "python -m pytest",'
                    f' then run workorder done --task {tid}')
        if (item == "implementation" or item.startswith("a passing test on")
                or item == "evidence for the uncommitted code changes"):
            return "on a branch, finish the Acceptance items and commit the code, then " + done_step(record)
    if item == "implementation":
        return f"commit the code, then: workorder receipt implemented --task {tid}"
    if item == "a declared test":
        return (f'add the command that proves it to tasks/{tid}.md, e.g. test: "python -m pytest",'
                ' or test: any to accept any recorded command')
    if item.startswith("implementation (commit"):
        return ("fetch the full history; after a squash or rebase merge, record it again on the merged"
                f" code: workorder receipt implemented --task {tid}")
    if item == "evidence for the uncommitted code changes":
        return (f"commit the code, then: workorder receipt implemented --task {tid}, then"
                f" workorder receipt test --task {tid} -- {command}")
    if item.startswith("a passing test on the code marked"):
        return (f"workorder receipt implemented --task {tid}, then"
                f" workorder receipt test --task {tid} -- {command}; or"
                f" workorder renew --task {tid} on the branch before pushing")
    if item.startswith("a passing test on"):
        if code_changed:
            return (f"workorder receipt implemented --task {tid}, then"
                    f" workorder receipt test --task {tid} -- {command}")
        return f"workorder receipt test --task {tid} -- {command}"
    if item.startswith("contract "):
        return f"add qualifying evidence to {record['contract']}"
    if item == "merge of the tested code":
        push = unpushed_target(root, record) if root else None
        if push:
            return push
        if root and target_touched_implementation(root, record):
            return ("the merged code differs from the tested code or no matching merge was found; "
                    f"run workorder renew --task {tid} on main, or record workorder receipt merged --task {tid}")
        return f"merge of the tested code is still owed; merge it into the target branch, then run workorder check {tid}"
    if item.startswith("deployment read-back"):
        return (f"workorder receipt deployed --task {tid} --target <name>"
                " -- <command that prints the deployed commit>")
    if item.startswith("live read-back"):
        return (f"workorder receipt live --task {tid} --target <name>"
                " -- <command that prints the commit the live system reports>")
    if item.startswith("task:"):
        field = "depends_on" if item[5:] in record.get("depends_on", []) else "waits_on"
        return f"finish {item[5:]} first, or remove it from {field}"
    return f"remove {item} from waits_on once it is answered"


def target_touched_implementation(root, record):
    implemented = newest_implementation(s.entries(root, record['id']))
    target = merge_target(root, record)
    if implemented is None or not target or not s.has_commit(root, implemented['commit']):
        return False
    base = s.git(root, 'merge-base', implemented['commit'], target[1], required=False).stdout.strip()
    if not base:
        return False
    paths = [set(s.git(root, 'diff', '--name-only', '-z', '--no-renames', base, commit,
                       '--', *s.CODE_PATHS).stdout.split('\0')) - {''}
             for commit in (implemented['commit'], target[1])]
    return bool(paths[0] & paths[1])


def test_line(record, history):
    """Describe the newest test evidence for the newest implementation."""
    implemented = newest_implementation(history)
    if implemented is None:
        return None, "none counted: no implementation recorded"
    short = implemented["commit"][:7]
    if implemented["missing"]:
        return short, f"none counted: commit {short} is not in this clone"
    after = history[history.index(implemented) + 1:]
    runs = counting_runs(record, after, implemented)
    if runs:
        newest = runs[-1]
        where = newest["commit"][:7]
        if newest["commit"] != implemented["commit"]:
            where += f", same code as {short}"
        if newest["exit_code"] != 0:
            timed_out = 'timed out, ' if newest['exit_code'] == 124 else ''
            latest = f"FAIL ({timed_out}exit {newest['exit_code']}, {where})"
        elif not newest["valid"]:
            latest = f"not counted: files or HEAD changed while it ran ({where})"
        else:
            return short, f"PASS (exit 0, {where})"
        if any(e["exit_code"] == 0 and e["valid"] for e in runs):
            return short, f"{latest}; an earlier run passed, but the latest run decides"
        return short, latest
    if declared_test(record) and any(e["kind"] == "test" and e["code"] == implemented["code"] for e in after):
        return short, f"no run of the task's test for this commit (test: {record['test'].strip()})"
    tests = [e for e in history if e["kind"] == "test"]
    if not tests:
        return short, "no passing test for this commit"
    if tests[-1]["code"] == implemented["code"]:
        return short, "no passing test for this commit; the last test was recorded before the implementation"
    return short, f"no passing test for this commit; the last test ran on {tests[-1]['commit'][:7]}"


def uncounted(root, record, history):
    """Why the newest receipt, though recorded, cannot count toward the verdict; None if it can.

    Failed or invalid runs are reported by their exit code and are not repeated here.
    """
    item, before = history[-1], history[:-1]
    kind = item["kind"]
    if kind == "implemented" or (kind in {"test", "deployed", "live"}
                                 and (item["exit_code"] != 0 or not item["valid"])):
        return None
    tid = record["id"]
    implemented = newest_implementation(before)
    if kind in {"merged", "deployed", "live"}:
        needed = {"merged": {"repo", "live"}, "deployed": {"live"}, "live": {"live"}}[kind]
        if record["ships"] not in needed:
            return f"{tid} has ships: {record['ships']}, so a {kind} receipt is not part of its evidence"
    if implemented is None:
        return f"no implementation is recorded yet; run workorder receipt implemented --task {tid} first"
    short = implemented["commit"][:7]
    if implemented["missing"]:
        return (f"the implementation names commit {short}, which is not in this clone; run"
                f" workorder receipt implemented --task {tid} again")
    if kind == "test":
        if item["code"] != implemented["code"]:
            return (f"this code differs from the implemented commit {short}; run"
                    f" workorder receipt implemented --task {tid} first")
        declared = declared_test(record)
        if declared is not None and item["command"] != declared:
            return f"{tid} declares its test as: {record['test'].strip()}; only that command counts"
        return None
    if kind == "merged" and not carries(root, implemented, item):
        return (f"{item['ref']} on origin neither contains the implemented commit {short} nor holds exactly its"
                f" code; after a squash merge, record implemented and test again on the merged code")
    computed = computed_merge(root, record, implemented)
    if kind == "deployed" and not delivered_code(root, implemented, item, computed):
        return f"the deployed commit neither contains the implemented commit {short} nor holds exactly its code"
    current = before[before.index(implemented) + 1:]
    merges = [i for i, e in enumerate(current) if e["kind"] == "merged" and carries(root, implemented, e)]
    if kind == "deployed" and not merges and not computed:
        return (f"no merge of the tested code is recorded since the implementation;"
                f" run workorder receipt merged --task {tid} first")
    if kind == "live":
        after_merge = current if computed else current[merges[-1] + 1:] if merges else []
        deployed = [e for e in after_merge if e["kind"] == "deployed"]
        if not deployed:
            return "no deployment read-back is recorded after the merge"
        if deployed[-1]["exit_code"] != 0 or not deployed[-1]["valid"]:
            return "the latest deployment read-back did not pass"
        if deployed[-1]["commit"] != item["commit"] or deployed[-1]["target"] != item["target"]:
            return (f"it does not match the latest deployment read-back"
                    f" ({deployed[-1]['target']} on {deployed[-1]['commit'][:7]})")
    return None


def timeline(root, record, history):
    """One line per receipt, oldest first: when, what, by whom."""
    lines = []
    implemented = newest_implementation(history)
    after = history[history.index(implemented) + 1:] if implemented else []
    runs = counting_runs(record, after, implemented) if implemented else []
    for index, item in enumerate(history):
        when = item["at"][:16].replace("T", " ")
        commit = item["commit"][:7]
        kind = item["kind"]
        if kind == "implemented":
            what = f"implemented {commit}"
        elif kind == "test":
            timed_out = 'timed out, ' if item['exit_code'] == 124 else ''
            result = "PASS" if item["exit_code"] == 0 else f"FAIL ({timed_out}exit {item['exit_code']})"
            what = f"test {result}: {s.join_command(item['command'])} on {commit}"
            reason = None
            if not item['valid']:
                reason = 'the command failed' if item['exit_code'] else 'files or HEAD changed while it ran'
            elif item['missing']:
                reason = 'commit not in this clone'
            elif not implemented or index < history.index(implemented):
                reason = 'no implementation recorded before this run' if not any(
                    e['kind'] == 'implemented' for e in history[:index]) else 'a newer implementation was recorded'
            elif item['code'] != implemented['code']:
                reason = 'this code differs from the implemented code'
            elif declared_test(record) is not None and item['command'] != declared_test(record):
                reason = 'only the declared test command counts'
            elif runs and item != runs[-1]:
                reason = 'a later test run decides'
            if reason:
                what += f" (does not count: {reason})"
        elif kind == "merged":
            what = f"merged: {item.get('ref', '')} at {commit}"
        else:
            what = f"{kind} read-back {item.get('target', '')}: {'ok' if item['valid'] else 'not valid'} ({commit})"
        if item["missing"]:
            what += " (commit not in this clone)"
        lines.append(f"  {when}  {what}  ({item['who']})")
    computed = computed_merge(root, record, newest_implementation(history))
    if computed:
        lines.append("  " + merge_line(computed))
    return lines


@s.read_session()
def record_lines(root, tid):
    """The task as a record to come back to: what it is, where it stands, what happened, what is next."""
    errors = {}
    all_records = records(root, errors=errors)
    if tid in errors:
        raise s.Refused(errors[tid])
    if tid not in all_records:
        raise s.Refused(f"no task record tasks/{tid}.md")
    record = all_records[tid]
    views = Views(root, all_records)
    computed, done, delivery = views[tid]
    open_waits = waits(record, all_records, views)
    if computed in FINISHED and open_waits:
        computed = "BLOCKED"
    history = s.entries(root, tid)
    recognise_squashed(root, tid, history)
    debt, stale = claim_details(root, record, all_records, views)
    stale_debt = debt if stale else []
    if record['status'] in FINISHED:
        headline = f"{tid}\n  Claimed:  {record['status']}\n  Evidence: {evidence_status(record, computed, debt)}"
    else:
        headline = unfinished_headline(record, done, delivery, open_waits)
    lines = [headline,
             "still owes: " + owes(record, stale_debt + done + open_waits, delivery)]
    pending = waiting_for_review(root, record)
    if pending:
        findings = pending_findings(root, record)
        lines.append(pending_review_message(tid, findings).replace('pending review:', 'waiting for review:', 1))
        lines += ['  Pending: ' + item['message'] for item in findings]
    if stale:
        lines.append("Result: " + stale_result(record))
    if record["status"] not in FINISHED and computed in FINISHED:
        # Found in the handoff trial: "computed CLOSED, owes nothing" on a half-done task misleads.
        lines.append("note:     " + ACCEPTANCE_NOTE)
    if s.dependencies(record):
        lines.append("waits_on: " + ", ".join(s.dependencies(record)))
    lines += ["", f"title:    {record['title']}", f"owner:    {record['owner']}"]
    for key in ("mission", "summary", "next"):
        if record.get(key, "").strip():
            lines.append(f"{key + ':':<9} {' '.join(record[key].split())}")
    lines.append(f"test:     {record.get('test', '').strip() or '(none declared)'}")
    # The task's own words: every section of the body except Progress, which follows the history.
    sections = s.task_body_sections(s.read_text(root, f"tasks/{tid}.md"))
    for heading, body in sections:
        if heading in (None, "Progress"):
            continue
        text = [line.rstrip() for line in body if not line.strip().startswith("<!--")]
        while text and not text[0].strip():
            text.pop(0)
        while text and not text[-1].strip():
            text.pop()
        lines += ["", f"{heading.lower()}:"] + ([f"  {line}" if line.strip() else "" for line in text]
                                               or ["  (empty)"])
    lines += ["", "history:"] + (timeline(root, record, history) or ["  (no receipts yet)"])
    progress = [line.strip() for heading, body in sections
                if heading == "Progress" for line in body if line.strip() and not line.strip().startswith("<!--")]
    lines += ["", "progress:"] + ([f"  {line}" for line in progress] or ["  (no notes yet)"])
    owed = stale_debt + done + delivery + open_waits
    if stale:
        lines += ["", f"next step: workorder renew --task {tid} on the branch before pushing"]
    elif owed:
        lines += ["", f"next step: {next_step(owed[0], record, root=root, finish=True)}"]
    elif pending:
        lines += ['', 'next step: waiting for review']
    elif record["status"] not in FINISHED | {"CANCELLED"}:
        lines += ["", "next step: finish the Acceptance items, then " + done_step(record)]
    return lines


@s.read_session()
def explain(root, tid, claim=None, rerun=False):
    """Lines describing one task's claim and evidence, and whether the claim stands."""
    errors = {}
    all_records = records(root, errors=errors)
    if tid in errors:
        raise s.Refused(errors[tid])
    if tid not in all_records:
        raise s.Refused(f"no task record tasks/{tid}.md")
    if claim is not None:
        all_records[tid] = {**all_records[tid], 'status': claim}
    views = Views(root, all_records)
    record = all_records[tid]
    computed, done, delivery = views[tid]
    debt, stale = claim_details(root, record, all_records, views)
    history = s.entries(root, tid)
    recognise_squashed(root, tid, history)
    commit, test = test_line(record, history)
    implemented = newest_implementation(history)
    runs = (counting_runs(record, history[history.index(implemented) + 1:], implemented)
            if implemented and not implemented["missing"] else [])
    snapshot_changed = implemented is not None and s.code_digest(root, s.head(root)) != implemented["code"]
    code_changed = snapshot_changed and (implemented['missing'] or
        task_change_touched(root, implemented.get('seen_on', implemented['commit']), record))
    claimed = record["status"]
    open_waits = waits(record, all_records, views)
    if computed in FINISHED and open_waits:
        computed = "BLOCKED"  # the evidence is in, but a dependency is still open
    if s.dirty_paths(root):
        code_note = "uncommitted code changes are not covered by any receipt"
    else:
        code_note = None
    if claimed in FINISHED:
        lines = [f"NOT DONE  {tid}" if debt else f"{claimed}  {tid}", "",
                 f"  Claimed:  {claimed}", f"  Evidence: {evidence_status(record, computed, debt, rerun)}"]
    else:
        lines = [unfinished_headline(record, done, delivery, open_waits), ""]
    lines += [f"  Commit:   {commit or 'none recorded'}", f"  Test:     {test}"]
    merged = computed_merge(root, record, implemented)
    if merged:
        lines.append("  Merge:    " + merge_line(merged))
    if runs:
        lines.append(f"  Command:  {s.join_command(runs[-1]['command'])}")
    if waiting_for_review(root, record):
        findings = pending_findings(root, record)
        lines.append('  Review:   ' + pending_review_message(tid, findings))
        lines += ['  Pending:  ' + item['message'] for item in findings]
    if code_changed:
        lines.append(f"  Note:     the code has changed since {commit}; this verdict is about {commit}")
    elif snapshot_changed:
        lines.append(f"  Note:     {tid}'s files are unchanged since {commit}")
    if code_note:
        lines.append(f"  Note:     {code_note}")
    if not debt and claimed not in FINISHED and computed in FINISHED:
        lines.append(f"  Note:     {ACCEPTANCE_NOTE}")
    if debt:
        lines += [f"  Missing:  {item}" for item in debt]
        lines += [f"  Next:     {next_step(item, record, code_changed, root)}" for item in debt]
        if stale:
            result = stale_result(record)
        else:
            state = "NOT DONE" if done or claimed == "DONE" else "NOT CLOSED"
            result = f"{state}. The status says {claimed}; the evidence above does not support it."
        lines += ["", f"  Result: {result}"]
    else:
        owed = done + delivery
        if owed:
            lines.append(f"  Owes:     {', '.join(owed)}")
        result = ('the declared test passed on ' + ('the implemented code' if snapshot_changed or code_note else 'this code')
                  if claimed in FINISHED else 'no completion claimed')
        lines += ["", f"  Result: {result}"]
    return lines, bool(debt)


@s.read_session()
def ready(root, local_claims=None):
    # A refused DONE or CLOSED claim is shown on its own task; it does not hide the rest of the work.
    # Cycles belong to their participants; unrelated work remains visible.
    issues = check(root)
    unreadable = {}
    records(root, errors=unreadable)
    refused = {issue.split()[2] for issue in issues if issue.startswith("NOT DONE  ")}
    structural = [issue for issue in issues if not issue.startswith("NOT DONE  ")
                  and issue.split(":")[0] not in unreadable and not issue.startswith("tasks/")
                  and ": dependency cycle:" not in issue]
    if structural:
        raise s.Refused("; ".join(structural))
    errors = {}
    all_records = records(root, errors=errors)
    cycles = dependency_cycles(all_records)
    views = Views(root, all_records)
    lines = [f"{tid} cannot be read: {message.removeprefix(tid + ': ')}" for tid, message in sorted(errors.items())]
    upstream = {}
    try:
        claims = (local_claims if local_claims is not None
                  else s.active_claims(root, upstream) if s.has_origin(root) else {})
    except s.Refused:
        claims = {}
        upstream = {}
        lines.append("(claims not shown: origin could not be reached)")
    for tid, record in sorted(all_records.items()):
        if record["status"] == "CANCELLED":
            continue
        blocked = waits(record, all_records, views)
        if record["status"] == "BLOCKED":
            blocked = ["status BLOCKED"] + blocked
        claimed = record["status"] in FINISHED
        if claimed and views[tid][0] == "CLOSED" and not blocked and tid not in refused:
            continue
        held = claims.get(tid)
        if tid in upstream and record["status"] != upstream[tid]["status"]:
            state = f"finished on {upstream[tid]['ref']}; pull to update"
        elif tid in cycles:
            state = cycles[tid]
        elif tid in refused:
            state = f"says {record['status']}, which the evidence does not support: workorder check {tid}"
        elif blocked:
            state = "blocked: " + ", ".join(blocked)
        elif held and not held["released"]:
            state = f"held by {held['who']} since {held['at']}"
        elif claimed and views[tid][0] == "DONE":
            state = "waiting for delivery: " + ", ".join(views[tid][2])
        else:
            state = "free"
        lines.append(f"{tid} {state}")
    return lines
