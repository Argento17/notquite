"""Derive completion from receipts; a status line never supplies missing proof."""

from __future__ import annotations

import ast
import configparser
import fnmatch
import json
import posixpath
import re
import subprocess
import sys
import uuid

from . import store as s


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
        merged = computed_merge(root, s.record(root, tid, staged), implemented)
        if merged and merged["method"] == "same code":
            seen = merged["commit"]
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
    key = s.digest(s.packed(["merge-v5", original, s.has_commit(root, original), implemented["code"],
                            target, MERGE_SEARCH_LIMIT]).encode())
    folder = s._cache_dir(root)
    cached = folder / ("merge-" + key + ".json") if folder else None
    try:
        if cached and cached.is_file():
            value = json.loads(cached.read_text(encoding="utf-8"))
            if value is None or (isinstance(value, dict) and value.get("method") in
                                 {"ancestry", "same code", "squash", "rebase"} and s.has_commit(root, value.get("commit", ""))):
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


def verdict(root, record, staged=False):
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
        if not runs or runs[-1]["exit_code"] != 0 or not runs[-1]["valid"]:
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
        dirty = [] if staged else s.dirty_paths(root)
        if dirty:
            return (f"NOT QUITE  {tid} is being marked {status} with uncommitted code changes; "
                    + s.describe_dirty(dirty) + f". Commit, record workorder receipt implemented --task {tid}"
                    " and test again.")
        tree = s.git(root, "write-tree").stdout.strip() if staged else s.head(root)
        if claim_matches(root, record, current, s.head(root), tree, staged):
            return None
        where = "in this commit" if staged else "now"
        return (f"NOT QUITE  {tid} is being marked {status}, but the code {where} differs from the"
                f" implemented commit {short}. Record workorder receipt implemented --task {tid} and test again.")
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
    return (f"NOT QUITE  {tid} was marked {status} in {claim[:7]} on code other than the implemented"
            f" commit {short}. Run workorder renew --task {tid} on that code, or record"
            f" workorder receipt implemented --task {tid} and test again.")


def claim_matches(root, record, implemented, claim, tree=None, staged=False, receipt_rev=None):
    tree = tree or claim
    # This concerns the claim's history, even when the task ships nothing.
    target = merge_target(root, record)
    merged = merge_match(root, implemented, target[1]) if target else None
    if merged and s.contains(root, merged["commit"], claim):
        return unchanged_implementation_paths(root, implemented, merged, tree, staged, receipt_rev)
    if s.code_digest(root, tree) == implemented["code"]:
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
    # A committed claim keeps the task boundaries recorded when it was made.
    history = s.entries(root, tid, staged, rev=receipt_rev)
    index = next(i for i, item in enumerate(history) if item["hash"] == implemented["hash"])
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
    if base and base != original:
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
    return not (paths & (set(changed.split("\0")) - {""}))


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


def changed_paths(root, reference, staged=False):
    """Paths that differ between reference and what is being checked (the index under --staged, the
    working tree otherwise, which in CI is the commit being checked), untracked task files included."""
    args = ["diff", "--name-only", "-z", "--no-renames"] + (["--cached"] if staged else []) + [reference]
    paths = set(s.git(root, *args).stdout.split("\0")) - {""}
    if not staged:
        paths |= set(s.git(root, "ls-files", "-z", "--others", "--exclude-standard").stdout.split("\0")) - {""}
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


def test_files(root, record, base, staged=False):
    """Expand current and base definitions, so deleting a test or changing its path cannot hide it."""
    before = fields_at(root, record["id"], base)
    candidates = repository_files(root, base) | repository_files(root)
    candidates |= changed_paths(root, base, staged)
    found = set()
    for definition in (before, record):
        if definition is None:
            continue
        paths = definition.get("test_paths")
        if paths is None:
            paths = declared_test(definition) or []
        if not isinstance(paths, list):
            continue
        for path in paths:
            if not isinstance(path, str):
                continue
            if "test_paths" not in definition:
                path = path.split("::", 1)[0]  # pytest node selection
            try:
                path = posixpath.normpath(s.local_path(root, path).relative_to(root).as_posix())
            except (s.Refused, ValueError):
                continue
            found |= {p for p in candidates if path == "." or p == path or p.startswith(path + "/")}
    return found


def uses_pytest(record):
    command = declared_test(record) or []
    return any(posixpath.basename(word.replace('\\', '/')) in {"pytest", "py.test", "pytest.exe", "py.test.exe"}
               for word in command)


def pytest_sections(text):
    """Keep pytest TOML sections only; unrelated project settings do not require acceptance."""
    found, selected = [], False
    for line in text.splitlines():
        if re.match(r"^\s*\[", line):
            selected = bool(re.match(r'''^\s*\[\[?\s*["']?tool["']?\s*\.\s*["']?pytest''', line))
        if selected:
            found.append(line)
    return "\n".join(found).strip()


@s.cached_read
def pytest_patterns(root, base, staged=False, configs=()):
    """Conservatively include configured discovery patterns from both snapshots."""
    patterns = {'test_*.py', '*_test.py'}
    candidates = repository_files(root, base) | repository_files(root) | changed_paths(root, base, staged) | set(configs)
    for path in sorted(candidates):
        name = posixpath.basename(path)
        if name not in {'pytest.ini', 'tox.ini', 'setup.cfg', 'pyproject.toml'} and path not in configs:
            continue
        old = s.git(root, 'show', f'{base}:{path}', required=False).stdout
        try:
            current = s.read_text(root, path, staged)
        except (OSError, s.Refused):
            current = ''
        for text in (old, current):
            if name.endswith('.toml'):
                # python_files is a string or array of strings in pytest's TOML options.
                lines = pytest_sections(text).splitlines()
                for i, line in enumerate(lines):
                    value = re.match(r'^\s*python_files\s*=\s*(.*)', line)
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
                            patterns.update(p for p in values if isinstance(p, str))
                        break
            else:
                config = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    config.read_string(text)
                    section = 'tool:pytest' if name == 'setup.cfg' else 'pytest'
                    patterns.update(config.get(section, 'python_files', fallback='').split())
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
                found.add(posixpath.normpath(s.local_path(root, path).relative_to(root).as_posix()))
            except (s.Refused, ValueError):
                continue
    return found


def strict_test_files(root, record, base, staged=False):
    paths = test_files(root, record, base, staged)
    definitions = (record, fields_at(root, record['id'], base) or {})
    if not any(uses_pytest(definition) for definition in definitions):
        return paths
    candidates = repository_files(root, base) | repository_files(root) | changed_paths(root, base, staged)
    configs = set().union(*(pytest_configs(root, definition) for definition in definitions if uses_pytest(definition)))
    paths |= configs
    for definition in definitions:
        if uses_pytest(definition) and 'test_paths' not in definition:
            # Config arguments alone do not select tests. With no Python collection paths,
            # guard discovery, including custom python_files patterns from either snapshot.
            selected = test_files(root, {**definition, 'id': record['id']}, base, staged)
            if not any(p.endswith('.py') for p in selected):
                patterns = pytest_patterns(root, base, staged, tuple(sorted(configs)))
                paths |= {p for p in candidates if any(fnmatch.fnmatchcase(posixpath.basename(p), pattern)
                                                       for pattern in patterns)}
    # Even when passed with -c, only pytest sections of TOML belong to this policy.
    if all('test_paths' not in definition for definition in definitions):
        paths = {p for p in paths if posixpath.basename(p) != 'pyproject.toml'
                 and not (p in configs and p.endswith('.toml'))}
    for path in changed_paths(root, base, staged):
        name = posixpath.basename(path)
        if name in {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg"}:
            paths.add(path)
        elif name == "pyproject.toml" or (path in configs and path.endswith('.toml')):
            old = s.git(root, 'show', f'{base}:{path}', required=False).stdout
            try:
                current = s.read_text(root, path, staged)
            except (OSError, s.Refused):
                current = ""
            if pytest_sections(old) != pytest_sections(current):
                paths.add(path)
    return paths


def test_file_changes(root, base, record, staged=False, strict=False):
    protected = strict_test_files(root, record, base, staged) if strict else test_files(root, record, base, staged)
    paths = sorted(protected & changed_paths(root, base, staged))
    if not paths:
        return None
    if strict:
        return f"{record['id']}: its test files or pytest configuration changed since {base} ({', '.join(paths)})"
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
        if removed == "-" or int(removed) or not exists:
            lost += int(removed) if removed != "-" else 0
            binary |= removed == "-"
            affected.append(path)
    if affected:
        change = "changed binary content" if binary else f"lost or changed {lost} lines"
        return f"{record['id']}: its test files {change} since {base} ({', '.join(affected)})"
    return None


def scope(root, reference, staged, names, all_records, include_tests=False, strict_tests=False):
    """The tasks a change touches (task file, receipts, contract or its evidence), and every task that
    depends on one of them, directly or not."""
    changed = changed_paths(root, reference, staged)
    touched = {path[6:-3] for path in changed if path.startswith("tasks/") and path.endswith(".md")
               and s.ID.fullmatch(path[6:-3])}
    touched |= {s.receipt_task(path) for path in changed} - {None}
    touched |= {tid for tid, record in all_records.items() if evidence_paths(root, record, staged) & changed}
    if include_tests:
        protected = strict_test_files if strict_tests else test_files
        touched |= {tid for tid, record in all_records.items()
                    if protected(root, record, reference, staged) & changed}
    growing = True
    while growing:
        growing = False
        for tid, record in all_records.items():
            if tid not in touched and any(w[5:] in touched for w in s.dependencies(record) if w.startswith("task:")):
                touched.add(tid)
                growing = True
    return touched, changed


def requirement_changes(root, base, record):
    """Changes since base to what a task requires: its test, its contract, its dependencies."""
    tid, found = record["id"], []
    before = fields_at(root, tid, base)
    if before is None:
        return found
    if before.get("test", "") != record.get("test", ""):
        found.append(f"test changed: {before.get('test', '') or '(none)'!r} -> {record.get('test', '') or '(none)'!r}")
    if before.get("test_paths") != record.get("test_paths"):
        found.append("test_paths changed")
    if before.get("contract") != record.get("contract"):
        found.append(f"contract changed: {before.get('contract')} -> {record.get('contract')}")
    elif record.get("contract", "none") != "none":
        old = s.git(root, "show", f"{base}:{record['contract']}", required=False)
        try:
            now = s.read_text(root, record["contract"])
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
    # The written ask: removing or rewording a Scope or Acceptance line changes what DONE means.
    # Adding lines does not (that is how a task gets filled in).
    old = s.git(root, "show", f"{base}:tasks/{tid}.md", required=False)
    if old.returncode == 0:
        def section_lines(text, name):
            return {line.strip() for heading, body in s.task_body_sections(text) if heading == name
                    for line in body if line.strip() and not line.strip().startswith("<!--")}
        try:
            now_text = s.read_text(root, f"tasks/{tid}.md")
        except (OSError, s.Refused):
            now_text = ""
        before_text = old.stdout.replace("\r\n", "\n")
        for name in ("Scope", "Acceptance"):
            removed = section_lines(before_text, name) - section_lines(now_text, name)
            if removed:
                found.append(f"{name} lines removed or changed: {len(removed)}")
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


def run_declared_test(root, record, counts=None, notes=None, strict=False, results=None, regression=False):
    """Run a task's declared test here (CI), without a shell; return a problem line, or None if it passed."""
    tid, declared = record["id"], declared_test(record)
    if declared is None:
        return f"NOT QUITE  {tid} says {record['status']} but declares no test for this check to run"
    results = {} if results is None else results
    key = tuple(declared)
    if key not in results:
        try:
            run = subprocess.run(declared, cwd=root, capture_output=True, encoding='utf-8', errors='replace')
            code, output = run.returncode, run.stdout + run.stderr
            print(run.stdout, end='')
            print(run.stderr, end='', file=sys.stderr)
            if counts is not None:
                counts["tests"] += 1
        except OSError as error:
            code, output = f"not started: {error.strerror}", ""
        finally:
            s.invalidate_reads()  # a declared command may have changed files or refs
        results[key] = code, output
    code, output = results[key]
    empty = pytest_no_pass(output) if uses_pytest(record) else None
    if empty and strict:
        reason = f"{tid}: its test ran but no test passed ({empty})"
        return f"regression: {tid}'s test fails on this code\n{reason}" if regression else reason
    if empty and notes is not None:
        if re.fullmatch(r'\d+ skipped', empty):
            notes.append(f"{tid}: its test ran, but reported only skipped tests")
        elif empty == 'no tests ran':
            notes.append(f"{tid}: its test ran, but reported no tests ran")
    if code == 0:
        if notes is not None and not empty:
            notes.append(f"{tid}: its test passed when this check ran it")
        return None
    if regression:
        return f"regression: {tid}'s test fails on this code"
    return f"NOT QUITE  {tid} says {record['status']}, but its test failed when this check ran it (exit {code})"


class Views(dict):
    """Verdicts worked out on first use, so a scoped check reads only the tasks it needs."""

    def __init__(self, root, all_records, staged=False):
        super().__init__()
        self.root, self.all_records, self.staged = root, all_records, staged

    def __missing__(self, tid):
        self[tid] = verdict(self.root, self.all_records[tid], self.staged)
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


def check_summary(counts, run_tests=False, single_unclaimed=False):
    claims = "not claimed finished" if single_unclaimed else f"{counts['claimed']} claimed DONE or CLOSED"
    line = f"checked {_count(counts['tasks'], 'task')} ({claims})"
    if run_tests:
        line += f"; ran {_count(counts['tests'], 'declared test')}"
    if counts.get('tests_only'):
        n = counts['tests_only']
        rest = ("supported claim rests on its declared test only." if n == 1
                else "supported claims rest on their declared tests only.")
        line = f"Acceptance lists are not checked; {n} {rest}\n" + line
    return line


@s.read_session()
def check(root, staged=False, base=None, notes=None, allow=(), run_tests=False, counts=None,
          strict_tests=False, all_tests=False, require_task=False):
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
        selected, changed = scope(root, reference, staged, names, all_records, include_tests=bool(base),
                                  strict_tests=strict_tests)
    else:
        selected, changed = set(names), None
    counts.update(tasks=len(selected), claimed=sum(r["status"] in FINISHED for tid, r in all_records.items()
                                                   if tid in selected), tests=0, tests_only=0)
    problems = [f"{tid}: {message.removeprefix(tid + ': ')}" for tid, message in sorted(errors.items())
                if tid in selected]
    if require_task and changed:
        code = any(not path.startswith(('tasks/', '.workorder/', '.notquite/')) for path in changed)
        recorded = any(s.receipt_task(path) or (path.startswith('tasks/') and path.endswith('.md')
                       and s.ID.fullmatch(path[6:-3])) for path in changed)
        if code and not recorded:
            problems.append('this change edits code but no task records it')
    stray = [path for path in s.files(root, "tasks", staged) if not path.endswith(".md")
             and (changed is None or path in changed)]
    problems += [f"{path}: only task files belong in tasks/; a file here is outside the code fingerprint,"
                 " so changes to it are never checked" for path in stray]
    retired = set()
    if base:
        retired, removed_problems = removals(root, base, names, allow, staged)
        problems += removed_problems + base_problems(root, base, staged, retired)
        notes += [f"{tid}: task and all receipts removed (allowed)" for tid in sorted(retired)]
    views = Views(root, all_records, staged)
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
            found = (f"NOT QUITE  {tid} says {record['status']} but owes: {', '.join(debt)}."
                     f" Details: workorder check {tid}") if debt else stale_claim(root, record, staged, base)
        except s.Refused as error:  # a dependency that cannot be read
            found = f"{tid}: {error}"
        merged = computed_merge(root, record, newest_implementation(s.entries(root, tid, staged)))
        if merged:
            notes.append(f"{tid}: {merge_line(merged)}")
        if found:
            problems.append(found)
        elif record['status'] in FINISHED and record['contract'] == 'none' and tid not in cycles:
            counts['tests_only'] += 1
        if base:
            if fields_at(root, tid, base) is None and record["status"] in FINISHED:
                line = f"{tid} was created and claimed {record['status']} in the same change"
                if tid in allow:
                    notes.append(line + " (definition allowed)")
                else:
                    problems.append(line + f"; a reviewer accepts its definition with --allow {tid}")
            changed_tests = test_file_changes(root, base, record, staged, strict_tests)
            if changed_tests:
                if tid in allow:
                    notes.append(changed_tests + " (allowed)")
                else:
                    problems.append(changed_tests + f"; a reviewer accepts it with --allow {tid}")
            for change in requirement_changes(root, base, record):
                line = f"{tid}: {change} since {base}"
                if tid in allow:
                    notes.append(line + " (allowed)")
                else:
                    problems.append(f"{line}; a reviewer accepts it with --allow {tid}")
        elif staged:
            for change in requirement_changes(root, "HEAD", record):
                notes.append(f"{tid}: {change} in this commit")
        if run_tests and not all_tests and not found and record["status"] in FINISHED:
            failed = run_declared_test(root, record, counts, notes, strict_tests)
            if failed:
                problems.append(failed)
    if all_tests:
        results = {}
        for record in all_records.values():
            if record['status'] in FINISHED:
                failed = run_declared_test(root, record, counts, notes, strict_tests, results, regression=True)
                if failed:
                    problems.append(failed)
    return problems


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
            f"run workorder renew --task {record['id']}")


def done_step(record):
    prerequisite = (f"set a concrete test: command in tasks/{record['id']}.md and "
                    if record.get('test', '').strip() == ANY_TEST else "")
    return prerequisite + f"run workorder done --task {record['id']}"


def next_step(item, record, code_changed=False, root=None, finish=False):
    tid = record["id"]
    command = record.get("test", "").strip() or "<your test command>"
    if finish:
        if item == "a declared test":
            return (f'add the command that proves it to tasks/{tid}.md, e.g. test: "python -m pytest",'
                    f' then run workorder done --task {tid}')
        if (item == "implementation" or item.startswith("a passing test on")
                or item == "evidence for the uncommitted code changes"):
            return "finish the Acceptance items and commit the code, then " + done_step(record)
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
                f" workorder receipt test --task {tid} -- {command}; or workorder renew --task {tid}")
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
            latest = f"FAIL (exit {newest['exit_code']}, {where})"
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
            result = "PASS" if item["exit_code"] == 0 else f"FAIL (exit {item['exit_code']})"
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
        headline = f"{tid} recorded {record['status']}; computed {computed}"
    else:
        headline = unfinished_headline(record, done, delivery, open_waits)
    lines = [headline,
             "still owes: " + owes(record, stale_debt + done + open_waits, delivery)]
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
        lines += ["", f"next step: workorder renew --task {tid}"]
    elif owed:
        lines += ["", f"next step: {next_step(owed[0], record, root=root, finish=True)}"]
    elif record["status"] not in FINISHED | {"CANCELLED"}:
        lines += ["", "next step: finish the Acceptance items, then " + done_step(record)]
    return lines


@s.read_session()
def explain(root, tid, claim=None):
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
    code_changed = implemented is not None and s.code_digest(root, s.head(root)) != implemented["code"]
    claimed = record["status"]
    open_waits = waits(record, all_records, views)
    if computed in FINISHED and open_waits:
        computed = "BLOCKED"  # the evidence is in, but a dependency is still open
    if s.dirty_paths(root):
        code_note = "uncommitted code changes are not covered by any receipt"
    else:
        code_note = None
    if claimed in FINISHED:
        lines = [f"NOT QUITE  {tid}" if debt else f"{claimed}  {tid}", "",
                 f"  Claimed:  {claimed}", f"  Computed: {computed}"]
    else:
        lines = [unfinished_headline(record, done, delivery, open_waits), ""]
    lines += [f"  Commit:   {commit or 'none recorded'}", f"  Test:     {test}"]
    merged = computed_merge(root, record, implemented)
    if merged:
        lines.append("  Merge:    " + merge_line(merged))
    if runs:
        lines.append(f"  Command:  {s.join_command(runs[-1]['command'])}")
    if code_changed:
        lines.append(f"  Note:     the code has changed since {commit}; this verdict is about {commit}")
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
        lines += ["", f"  Result: {'claim supported' if claimed in {'DONE', 'CLOSED'} else 'no completion claimed'}"]
    return lines, bool(debt)


@s.read_session()
def ready(root):
    # A refused DONE or CLOSED claim is shown on its own task; it does not hide the rest of the work.
    # Cycles belong to their participants; unrelated work remains visible.
    issues = check(root)
    unreadable = {}
    records(root, errors=unreadable)
    refused = {issue.split()[2] for issue in issues if issue.startswith("NOT QUITE  ")}
    structural = [issue for issue in issues if not issue.startswith("NOT QUITE  ")
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
        claims = s.active_claims(root, upstream) if s.has_origin(root) else {}
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
        if tid in upstream:
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
