"""Derive completion from receipts; a status line never supplies missing proof."""

from __future__ import annotations

import json
import shlex

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
                proved |= any(evidence["ref"] == "receipt:" + entry["hash"]
                              and entry["kind"] == "test" and entry["exit_code"] == 0
                              and entry["valid"] and implemented is not None and not entry["missing"]
                              and entry["code"] == implemented["code"] for entry in history)
        if item["critical"] and not proved:
            debts.append(f"contract {cid}: {item['requires']} evidence")
    return debts


def declared_test(record):
    """The task's own test command as an argument list, or None when the task declares none."""
    value = record.get("test", "")
    return s.split_command(value) if value.strip() else None


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


def verdict(root, record, staged=False):
    tid = record["id"]
    history = s.entries(root, tid, staged)
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    implemented = history[index] if index >= 0 else None
    current = history[index + 1:] if implemented else []
    done = []
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
    if record["ships"] != "none":
        # Any merge receipt that carries the tested code counts; a later one that does not, cannot undo it.
        merges = [i for i, e in enumerate(current) if e["kind"] == "merged" and carries(root, implemented, e)]
        merge_index = merges[-1] if merges else -1
        merged = current[merge_index] if merges else None
        if not merged:
            delivery.append("merge of the tested code")
        if record["ships"] == "live":
            after_merge = current[merge_index + 1:] if merged else []
            deploy_index = max((i for i, e in enumerate(after_merge) if e["kind"] == "deployed"), default=-1)
            deployed = after_merge[deploy_index] if deploy_index >= 0 else None
            if (not deployed or not deployed["valid"] or deployed["exit_code"] != 0
                    or not carries(root, implemented, deployed)):
                delivery.append("deployment read-back of the tested code")
            after_deploy = after_merge[deploy_index + 1:] if deployed else []
            if not deployed or not any(e["kind"] == "live" and e["valid"] and e["exit_code"] == 0
                                       and not e["missing"] and e["commit"] == deployed["commit"]
                                       and e["target"] == deployed["target"] for e in after_deploy):
                delivery.append("live read-back after deployment")
    computed = "IN_PROGRESS" if done else "DONE" if delivery else "CLOSED"
    return computed, done, delivery


def records(root, staged=False):
    """Task records: tasks/<ID>.md. Other files there (a README, notes, subfolders) are not tasks."""
    result = {}
    for path in s.files(root, "tasks", staged):
        name = path.removeprefix("tasks/")
        if name.endswith(".md") and "/" not in name and s.ID.fullmatch(name[:-3]):
            result[name[:-3]] = s.record(root, name[:-3], staged)
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
    if (tid not in all_records or tid in seen or views[tid][0] not in {"DONE", "CLOSED"}
            or all_records[tid]["status"] == "CANCELLED"):
        return False
    return not waits(all_records[tid], all_records, views, seen | {tid})


def fields_at(root, tid, rev="HEAD"):
    """The front matter of tasks/<tid>.md at a revision, or None if it has no readable record there."""
    shown = s.git(root, "show", f"{rev}:tasks/{tid}.md", required=False)
    if shown.returncode:
        return None
    lines = shown.stdout.replace("\r\n", "\n").removeprefix("\ufeff").splitlines()
    try:
        return s.front_matter(tid, lines[1:lines.index("---", 1)])
    except (s.Refused, ValueError):
        return None


def committed_status(root, tid, rev="HEAD"):
    """The status line of tasks/<tid>.md at a revision, or None if it has no readable record there."""
    fields = fields_at(root, tid, rev)
    return fields.get("status") if fields else None


def base_notes(root, base):
    """Changes since base to what a task requires: its test, its contract, its dependencies. These are
    shown for review; they are not refused, because requirements legitimately change."""
    notes = []
    for tid, record in sorted(records(root).items()):
        before = fields_at(root, tid, base)
        if before is None:
            continue
        if before.get("test", "") != record.get("test", ""):
            notes.append(f"{tid}: test changed since {base}: {before.get('test', '') or '(none)'!r}"
                         f" -> {record.get('test', '') or '(none)'!r}")
        if before.get("contract") != record.get("contract"):
            notes.append(f"{tid}: contract changed since {base}: {before.get('contract')} -> {record.get('contract')}")
        try:
            dropped = sorted(set(s.dependencies({"waits_on": before.get("waits_on", []),
                                                 "depends_on": s.task_refs(tid, "depends_on",
                                                                           before.get("depends_on", []))}))
                             - set(s.dependencies(record)))
        except (s.Refused, TypeError):
            dropped = []
        if dropped:
            notes.append(f"{tid}: dependencies removed since {base}: {', '.join(dropped)}")
    return notes


def newest_implementation(history):
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    return history[index] if index >= 0 else None


def base_problems(root, base, staged=False):
    """Receipts committed on the base revision must still be there, unchanged, as a prefix."""
    found = s.git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", required=False)
    if found.returncode:
        raise s.Refused(f"base {base} is not in this clone; fetch it"
                        " (in GitHub Actions: actions/checkout with fetch-depth: 0)")
    commit = found.stdout.strip()
    listed = s.git(root, "ls-tree", "-r", "--name-only", commit, "--", ".workorder/receipts").stdout
    present = set(s.files(root, ".workorder/receipts", staged))
    problems = []
    for path in sorted(p for p in listed.splitlines() if p.endswith(".jsonl")):
        tid = path.rsplit("/", 1)[-1].removesuffix(".jsonl")
        if path.count("/") != 2 or not s.ID.fullmatch(tid):
            continue
        before = s.git(root, "show", f"{commit}:{path}").stdout.replace("\r\n", "\n")
        now = s.read_text(root, path, staged) if path in present else None
        if now is None or not now.startswith(before):
            problems.append(f"{tid}: receipts recorded on {base} were removed or changed")
    return problems


def implementation_at(root, rev, tid):
    """The newest implemented receipt in tid's ledger as committed at rev, or None."""
    shown = s.git(root, "show", f"{rev}:{s.ledger_path(tid)}", required=False)
    found = None
    for line in shown.stdout.splitlines() if shown.returncode == 0 else []:
        try:
            item = json.loads(line)
        except ValueError:
            return None
        if isinstance(item, dict) and item.get("kind") == "implemented":
            found = item
    return found


FINISHED = {"DONE", "CLOSED"}


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
    if committed_status(root, tid) not in FINISHED:
        dirty = [] if staged else s.dirty_paths(root)
        if dirty:
            return (f"NOT QUITE  {tid} is being marked {status} with uncommitted code changes; "
                    + s.describe_dirty(dirty) + f". Commit, record workorder receipt implemented --task {tid}"
                    " and test again.")
        if (s.index_code(root) if staged else s.code_digest(root, s.head(root))) == current["code"]:
            return None
        where = "in this commit" if staged else "now"
        return (f"NOT QUITE  {tid} is being marked {status}, but the code {where} differs from the"
                f" implemented commit {short}. Record workorder receipt implemented --task {tid} and test again.")
    commits = s.git(root, "log", "--format=%H", f"{base}..HEAD" if base else "HEAD", "--",
                    f"tasks/{tid}.md").stdout.split()
    claim = None
    for commit in commits:  # newest first: walk back to the commit that made the claim
        if committed_status(root, tid, commit) not in FINISHED:
            break
        claim = commit
    else:
        if base and committed_status(root, tid, base) in FINISHED:
            claim = None  # claimed before the base
    if claim is None:
        return None
    relied_on = implementation_at(root, claim, tid)
    if (relied_on is None or current["hash"] != relied_on.get("hash")
            or s.code_digest(root, claim) == current["code"]):
        return None
    return (f"NOT QUITE  {tid} was marked {status} in {claim[:7]} on code other than the implemented"
            f" commit {short}. Record workorder receipt implemented --task {tid} on that code and test again.")


def unchanged_since(root, base, tid, staged=False):
    """True when the task file and its receipts are byte-for-byte what they were on base."""
    for path in (f"tasks/{tid}.md", s.ledger_path(tid)):
        before = s.git(root, "show", f"{base}:{path}", required=False)
        folder = path.rsplit("/", 1)[0]
        now = s.read_text(root, path, staged) if path in s.files(root, folder, staged) else None
        if before.returncode:
            if now is not None:
                return False
        elif now != before.stdout.replace("\r\n", "\n").removeprefix("\ufeff"):
            return False
    return True


def check(root, staged=False, base=None, notes=None):
    """Problems that refuse the check. With base, a claim problem on a task whose file and receipts are
    unchanged since base was already there before this change: it goes to notes, not problems."""
    notes = [] if notes is None else notes
    all_records = records(root, staged)
    views = {tid: verdict(root, record, staged) for tid, record in all_records.items()}
    problems = base_problems(root, base, staged) if base else []
    # Also inspect ledgers removed from the current snapshot.
    old = s.git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", ".workorder/receipts", required=False)
    paths = set(old.stdout.splitlines()) | set(s.files(root, ".workorder/receipts", staged))
    for path in sorted(paths):
        tid = path.rsplit("/", 1)[-1].removesuffix(".jsonl")
        if not path.endswith(".jsonl") or path.count("/") != 2 or not s.ID.fullmatch(tid):
            continue  # a .gitkeep or a note in the folder is not a ledger
        s.entries(root, tid, staged)
        if tid not in all_records:
            problems.append(f"{tid}: receipt ledger has no task record")
    visiting, visited = set(), set()

    def walk(tid):
        if tid in visiting:
            raise s.Refused(f"dependency cycle at {tid}")
        if tid in visited:
            return
        visiting.add(tid)
        for wait in s.dependencies(all_records[tid]):
            if wait.startswith("task:"):
                other = wait[5:]
                if other not in all_records:
                    problems.append(f"{tid}: missing dependency {other}")
                else:
                    walk(other)
        visiting.remove(tid)
        visited.add(tid)

    for tid, record in all_records.items():
        walk(tid)
        # blocks is informational: it is checked for consistency but never enforced.
        for other in record.get("blocks", []):
            if other not in all_records:
                problems.append(f"{tid}: blocks unknown task {other}")
            elif tid in all_records[other].get("blocks", []) and tid < other:
                problems.append(f"{tid} and {other} each say they block the other")
        debt = claim_debt(record, all_records, views)
        found = (f"NOT QUITE  {tid} says {record['status']} but owes: {', '.join(debt)}."
                 f" Details: workorder check {tid}") if debt else stale_claim(root, record, staged, base)
        reference = base or ("HEAD" if staged else None)
        if found and reference and unchanged_since(root, reference, tid, staged):
            notes.append(f"{found} (already so on {reference}; this change does not touch {tid})")
        elif found:
            problems.append(found)
    return problems


def claim_debt(record, all_records, views):
    """What a DONE or CLOSED status line claims but the evidence does not yet support."""
    claimed = record["status"]
    if claimed not in {"DONE", "CLOSED"}:
        return []
    _, done, delivery = views[record["id"]]
    return done + (delivery if claimed == "CLOSED" else []) + waits(record, all_records, views)


def next_step(item, record, code_changed=False):
    tid = record["id"]
    command = record.get("test", "").strip() or "<your test command>"
    if item == "implementation":
        return f"commit the code, then: workorder receipt implemented --task {tid}"
    if item.startswith("implementation (commit"):
        return ("fetch the full history; after a squash or rebase merge, record it again on the merged"
                f" code: workorder receipt implemented --task {tid}")
    if item == "evidence for the uncommitted code changes":
        return (f"commit the code, then: workorder receipt implemented --task {tid}, then"
                f" workorder receipt test --task {tid} -- {command}")
    if item.startswith("a passing test on the code marked"):
        return (f"workorder receipt implemented --task {tid}, then"
                f" workorder receipt test --task {tid} -- {command}")
    if item.startswith("a passing test on"):
        if code_changed:
            return (f"workorder receipt implemented --task {tid}, then"
                    f" workorder receipt test --task {tid} -- {command}")
        return f"workorder receipt test --task {tid} -- {command}"
    if item.startswith("contract "):
        return f"add qualifying evidence to {record['contract']}"
    if item == "merge of the tested code":
        return f"merge the tested commit, then: workorder receipt merged --task {tid}"
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
    if kind == "deployed" and not carries(root, implemented, item):
        return f"the deployed commit neither contains the implemented commit {short} nor holds exactly its code"
    current = before[before.index(implemented) + 1:]
    merges = [i for i, e in enumerate(current) if e["kind"] == "merged" and carries(root, implemented, e)]
    if kind == "deployed" and not merges:
        return (f"no merge of the tested code is recorded since the implementation;"
                f" run workorder receipt merged --task {tid} first")
    if kind == "live":
        after_merge = current[merges[-1] + 1:] if merges else []
        deployed = [e for e in after_merge if e["kind"] == "deployed"]
        if not deployed:
            return "no deployment read-back is recorded after the merge"
        if deployed[-1]["exit_code"] != 0 or not deployed[-1]["valid"]:
            return "the latest deployment read-back did not pass"
        if deployed[-1]["commit"] != item["commit"] or deployed[-1]["target"] != item["target"]:
            return (f"it does not match the latest deployment read-back"
                    f" ({deployed[-1]['target']} on {deployed[-1]['commit'][:7]})")
    return None


def explain(root, tid):
    """Lines describing one task's claim and evidence, and whether the claim stands."""
    all_records = records(root)
    if tid not in all_records:
        raise s.Refused(f"no task record tasks/{tid}.md")
    views = {key: verdict(root, record) for key, record in all_records.items()}
    record = all_records[tid]
    computed, done, delivery = views[tid]
    debt = claim_debt(record, all_records, views)
    history = s.entries(root, tid)
    commit, test = test_line(record, history)
    implemented = newest_implementation(history)
    runs = (counting_runs(record, history[history.index(implemented) + 1:], implemented)
            if implemented and not implemented["missing"] else [])
    stale = None if debt else stale_claim(root, record)
    if stale and "uncommitted code changes" in stale:
        debt = ["evidence for the uncommitted code changes"]
    elif stale:
        debt = [f"a passing test on the code marked {record['status']} (it differs from {commit})"]
    code_changed = implemented is not None and s.code_digest(root, s.head(root)) != implemented["code"]
    claimed = record["status"]
    if computed in FINISHED and waits(record, all_records, views):
        computed = "BLOCKED"  # the evidence is in, but a dependency is still open
    if s.dirty_paths(root):
        code_note = "uncommitted code changes are not covered by any receipt"
    else:
        code_note = None
    lines = [f"NOT QUITE  {tid}" if debt else f"{claimed}  {tid}", "",
             f"  Claimed:  {claimed}", f"  Computed: {computed}",
             f"  Commit:   {commit or 'none recorded'}", f"  Test:     {test}"]
    if runs:
        lines.append(f"  Command:  {shlex.join(runs[-1]['command'])}")
    if code_changed:
        lines.append(f"  Note:     the code has changed since {commit}; this verdict is about {commit}")
    if code_note:
        lines.append(f"  Note:     {code_note}")
    if debt:
        lines += [f"  Missing:  {item}" for item in debt]
        lines += [f"  Next:     {next_step(item, record, code_changed)}" for item in debt]
        result = "NOT DONE" if done or claimed == "DONE" else "NOT CLOSED"
        lines += ["", f"  Result: {result}. The status says {claimed}; the evidence above does not support it."]
    else:
        owed = done + delivery
        if owed:
            lines.append(f"  Owes:     {', '.join(owed)}")
        lines += ["", f"  Result: {'claim supported' if claimed in {'DONE', 'CLOSED'} else 'no completion claimed'}"]
    return lines, bool(debt)


def ready(root):
    # A refused DONE or CLOSED claim is shown on its own task; it does not hide the rest of the work.
    # Problems with the task set itself (cycles, missing tasks, stray ledgers) still stop the listing.
    issues = check(root)
    refused = {issue.split()[2] for issue in issues if issue.startswith("NOT QUITE  ")}
    structural = [issue for issue in issues if not issue.startswith("NOT QUITE  ")]
    if structural:
        raise s.Refused("; ".join(structural))
    all_records = records(root)
    views = {tid: verdict(root, record) for tid, record in all_records.items()}
    lines = []
    try:
        claims = s.active_claims(root) if s.has_origin(root) else {}
    except s.Refused:
        claims = {}
        lines.append("(claims not shown: origin could not be reached)")
    for tid, record in sorted(all_records.items()):
        if record["status"] == "CANCELLED":
            continue
        blocked = waits(record, all_records, views)
        if record["status"] == "BLOCKED":
            blocked = ["status BLOCKED"] + blocked
        if views[tid][0] == "CLOSED" and not blocked and tid not in refused:
            continue
        held = claims.get(tid)
        if tid in refused:
            state = f"says {record['status']}, which the evidence does not support: workorder check {tid}"
        elif blocked:
            state = "blocked: " + ", ".join(blocked)
        elif held and not held["released"]:
            state = f"held by {held['who']} since {held['at']}"
        elif views[tid][0] == "DONE":
            state = "waiting for delivery: " + ", ".join(views[tid][2])
        else:
            state = "free"
        lines.append(f"{tid} {state}")
    return lines
