"""Derive completion from receipts; a status line never supplies missing proof."""

from __future__ import annotations

import json

from . import store as s


def contract_debt(root, record, history, implemented, staged=False):
    path = record["contract"]
    if path == "none":
        return []
    contract = json.loads(s.read_text(root, path, staged))
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
                artifact = s.read_bytes(root, evidence["ref"], staged)
                proved |= s.digest(artifact) == evidence.get("sha256")
            elif provenance == "TEST" and item["requires"] != "REAL":
                proved |= any(evidence["ref"] == "receipt:" + entry["hash"]
                              and entry["kind"] == "test" and entry["exit_code"] == 0
                              and entry["valid"] and implemented is not None
                              and entry["code"] == implemented["code"] for entry in history)
        if item["critical"] and not proved:
            debts.append(f"contract {cid}: {item['requires']} evidence")
    return debts


def verdict(root, record, staged=False):
    tid = record["id"]
    history = s.entries(root, tid, staged)
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    implemented = history[index] if index >= 0 else None
    current = history[index + 1:] if implemented else []
    done = []
    if not implemented:
        done.append("implementation")
    else:
        # The latest test run on the implemented code decides; a later failure revokes an earlier pass.
        # Code leaves out tasks/ and .workorder/, so committing the receipts does not hide later runs.
        runs = [e for e in current if e["kind"] == "test" and e["code"] == implemented["code"]]
        if not runs or runs[-1]["exit_code"] != 0 or not runs[-1]["valid"]:
            done.append(f"a passing test on {implemented['commit'][:7]}")
    done += contract_debt(root, record, current, implemented, staged)
    delivery = []
    if record["ships"] != "none":
        merge_index = max((i for i, e in enumerate(current) if e["kind"] == "merged"), default=-1)
        merged = current[merge_index] if merge_index >= 0 else None
        if not merged or not implemented or merged["code"] != implemented["code"]:
            delivery.append("merge of the tested code")
        if record["ships"] == "live":
            after_merge = current[merge_index + 1:] if merged else []
            deploy_index = max((i for i, e in enumerate(after_merge) if e["kind"] == "deployed"), default=-1)
            deployed = after_merge[deploy_index] if deploy_index >= 0 else None
            if (not deployed or not deployed["valid"] or deployed["exit_code"] != 0
                    or not implemented or deployed["code"] != implemented["code"]):
                delivery.append("deployment read-back of the tested code")
            after_deploy = after_merge[deploy_index + 1:] if deployed else []
            if not deployed or not any(e["kind"] == "live" and e["valid"] and e["exit_code"] == 0
                                       and e["commit"] == deployed["commit"]
                                       and e["target"] == deployed["target"] for e in after_deploy):
                delivery.append("live read-back after deployment")
    computed = "IN_PROGRESS" if done else "DONE" if delivery else "CLOSED"
    return computed, done, delivery


def records(root, staged=False):
    result = {}
    for path in s.files(root, "tasks", staged):
        if path.endswith(".md"):
            tid = path.removeprefix("tasks/").removesuffix(".md")
            result[tid] = s.record(root, tid, staged)
    return result


def waits(record, all_records, views):
    result = []
    for wait in s.dependencies(record):
        kind, value = wait.split(":", 1)
        if kind == "person" or value not in all_records or views[value][0] not in {"DONE", "CLOSED"}:
            result.append(wait)
    return result


def committed_status(root, tid):
    """The status line of tasks/<tid>.md in HEAD, or None if HEAD has no readable record."""
    shown = s.git(root, "show", f"HEAD:tasks/{tid}.md", required=False)
    if shown.returncode:
        return None
    lines = shown.stdout.replace("\r\n", "\n").splitlines()
    try:
        return s.front_matter(tid, lines[1:lines.index("---", 1)]).get("status")
    except (s.Refused, ValueError):
        return None


def newest_implementation(history):
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    return history[index] if index >= 0 else None


def fresh_claim_on_changed_code(root, record):
    """A commit that newly claims DONE or CLOSED must carry exactly the implemented code."""
    tid = record["id"]
    if record["status"] not in {"DONE", "CLOSED"} or committed_status(root, tid) in {"DONE", "CLOSED"}:
        return None
    implemented = newest_implementation(s.entries(root, tid, staged=True))
    if implemented is None or s.index_code(root) == implemented["code"]:
        return None
    return (f"NOT QUITE  {tid} is being marked {record['status']}, but the code in this commit differs"
            f" from the implemented commit {implemented['commit'][:7]}. Record workorder receipt"
            f" implemented --task {tid} and test again.")


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
        before = s.git(root, "show", f"{commit}:{path}").stdout.replace("\r\n", "\n")
        now = s.read_text(root, path, staged) if path in present else None
        if now is None or not now.startswith(before):
            problems.append(f"{tid}: receipts recorded on {base} were removed or changed")
    return problems


def check(root, staged=False, base=None):
    all_records = records(root, staged)
    views = {tid: verdict(root, record, staged) for tid, record in all_records.items()}
    problems = base_problems(root, base, staged) if base else []
    # Also inspect ledgers removed from the current snapshot.
    old = s.git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", ".workorder/receipts", required=False)
    paths = set(old.stdout.splitlines()) | set(s.files(root, ".workorder/receipts", staged))
    for path in paths:
        tid = path.rsplit("/", 1)[-1].removesuffix(".jsonl")
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
        if debt:
            problems.append(f"NOT QUITE  {tid} says {record['status']} but owes: {', '.join(debt)}."
                            f" Details: workorder check {tid}")
        elif staged:
            changed = fresh_claim_on_changed_code(root, record)
            if changed:
                problems.append(changed)
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
    if item == "implementation":
        return f"commit the code, then: workorder receipt implemented --task {tid}"
    if item.startswith("a passing test on"):
        if code_changed:
            return (f"workorder receipt implemented --task {tid}, then"
                    f" workorder receipt test --task {tid} -- <your test command>")
        return f"workorder receipt test --task {tid} -- <your test command>"
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


def test_line(history):
    """Describe the newest test evidence for the newest implementation."""
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    if index < 0:
        return None, "none counted: no implementation recorded"
    implemented = history[index]
    short = implemented["commit"][:7]
    runs = [e for e in history[index + 1:] if e["kind"] == "test" and e["code"] == implemented["code"]]
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
    tests = [e for e in history if e["kind"] == "test"]
    if not tests:
        return short, "no passing test for this commit"
    if tests[-1]["code"] == implemented["code"]:
        return short, "no passing test for this commit; the last test was recorded before the implementation"
    return short, f"no passing test for this commit; the last test ran on {tests[-1]['commit'][:7]}"


def uncounted(record, history):
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
    if kind in {"test", "merged", "deployed"} and item["code"] != implemented["code"]:
        if kind == "test":
            return (f"this code differs from the implemented commit {short}; run"
                    f" workorder receipt implemented --task {tid} first")
        if kind == "merged":
            return (f"{item['ref']} on origin does not hold exactly the code of the implemented commit"
                    f" {short}; other work may have landed there")
        return f"the deployed checkout's code differs from the implemented commit {short}"
    current = before[before.index(implemented) + 1:]
    merges = [i for i, e in enumerate(current) if e["kind"] == "merged"]
    if kind == "deployed" and not merges:
        return f"no merge is recorded since the implementation; run workorder receipt merged --task {tid} first"
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
    commit, test = test_line(history)
    implemented = newest_implementation(history)
    code_changed = implemented is not None and s.code_digest(root, s.head(root)) != implemented["code"]
    claimed = record["status"]
    lines = [f"NOT QUITE  {tid}" if debt else f"{claimed}  {tid}", "",
             f"  Claimed:  {claimed}", f"  Computed: {computed}",
             f"  Commit:   {commit or 'none recorded'}", f"  Test:     {test}"]
    if code_changed:
        lines.append(f"  Note:     the code has changed since {commit}; this verdict is about {commit}")
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
    issues = check(root)
    if issues:
        raise s.Refused("; ".join(issues))
    all_records = records(root)
    views = {tid: verdict(root, record) for tid, record in all_records.items()}
    claims = s.active_claims(root) if s.has_origin(root) else {}
    lines = []
    for tid, record in sorted(all_records.items()):
        if views[tid][0] == "CLOSED":
            continue
        blocked = waits(record, all_records, views)
        held = claims.get(tid)
        if blocked:
            state = "blocked: " + ", ".join(blocked)
        elif held and not held["released"]:
            state = f"held by {held['who']} since {held['at']}"
        elif views[tid][0] == "DONE":
            state = "waiting for delivery: " + ", ".join(views[tid][2])
        else:
            state = "free"
        lines.append(f"{tid} {state}")
    return lines
