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
                              and entry["commit"] == implemented["commit"]
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
        # The latest test run on the implemented commit decides; a later failure revokes an earlier pass.
        runs = [e for e in current if e["kind"] == "test" and e["commit"] == implemented["commit"]
                and e["code"] == implemented["code"]]
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
    for wait in record["waits_on"]:
        kind, value = wait.split(":", 1)
        if kind == "person" or value not in all_records or views[value][0] not in {"DONE", "CLOSED"}:
            result.append(wait)
    return result


def check(root, staged=False):
    all_records = records(root, staged)
    views = {tid: verdict(root, record, staged) for tid, record in all_records.items()}
    problems = []
    # Also inspect ledgers removed from the current snapshot.
    old = s.git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", ".notquite/receipts", required=False)
    paths = set(old.stdout.splitlines()) | set(s.files(root, ".notquite/receipts", staged))
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
        for wait in all_records[tid]["waits_on"]:
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
        debt = claim_debt(record, all_records, views)
        if debt:
            problems.append(f"NOT QUITE  {tid} says {record['status']} but owes: {', '.join(debt)}."
                            f" Details: notquite check {tid}")
    return problems


def claim_debt(record, all_records, views):
    """What a DONE or CLOSED status line claims but the evidence does not yet support."""
    claimed = record["status"]
    if claimed not in {"DONE", "CLOSED"}:
        return []
    _, done, delivery = views[record["id"]]
    return done + (delivery if claimed == "CLOSED" else []) + waits(record, all_records, views)


def next_step(item, record):
    tid = record["id"]
    if item == "implementation":
        return f"commit the code, then: notquite receipt implemented --task {tid}"
    if item.startswith("a passing test on"):
        return f"notquite receipt test --task {tid} -- <your test command>"
    if item.startswith("contract "):
        return f"add qualifying evidence to {record['contract']}"
    if item == "merge of the tested code":
        return f"merge the tested commit, then: notquite receipt merged --task {tid}"
    if item.startswith("deployment read-back"):
        return (f"notquite receipt deployed --task {tid} --target <name>"
                " -- <command that prints the deployed commit>")
    if item.startswith("live read-back"):
        return (f"notquite receipt live --task {tid} --target <name>"
                " -- <command that prints the commit the live system reports>")
    if item.startswith("task:"):
        return f"finish {item[5:]} first, or remove {item} from waits_on"
    return f"remove {item} from waits_on once it is answered"


def test_line(history):
    """Describe the newest test evidence for the newest implementation."""
    index = max((i for i, e in enumerate(history) if e["kind"] == "implemented"), default=-1)
    if index < 0:
        return None, "none counted: no implementation recorded"
    implemented = history[index]
    short = implemented["commit"][:7]
    runs = [e for e in history[index + 1:] if e["kind"] == "test"
            and e["commit"] == implemented["commit"] and e["code"] == implemented["code"]]
    if runs:
        newest = runs[-1]
        if newest["exit_code"] != 0:
            latest = f"FAIL (exit {newest['exit_code']}, {short})"
        elif not newest["valid"]:
            latest = f"not counted: files or HEAD changed while it ran ({short})"
        else:
            return short, f"PASS (exit 0, {short})"
        if any(e["exit_code"] == 0 and e["valid"] for e in runs):
            return short, f"{latest}; an earlier run passed, but the latest run decides"
        return short, latest
    tests = [e for e in history if e["kind"] == "test"]
    if not tests:
        return short, "no passing test for this commit"
    if tests[-1]["commit"] == implemented["commit"]:
        return short, "no passing test for this commit; the last test was recorded before the implementation"
    return short, f"no passing test for this commit; the last test ran on {tests[-1]['commit'][:7]}"


def explain(root, tid):
    """Lines describing one task's claim and evidence, and whether the claim stands."""
    all_records = records(root)
    if tid not in all_records:
        raise s.Refused(f"no task record tasks/{tid}.md")
    views = {key: verdict(root, record) for key, record in all_records.items()}
    record = all_records[tid]
    computed, done, delivery = views[tid]
    debt = claim_debt(record, all_records, views)
    commit, test = test_line(s.entries(root, tid))
    claimed = record["status"]
    lines = [f"NOT QUITE  {tid}" if debt else f"{claimed}  {tid}", "",
             f"  Claimed:  {claimed}", f"  Computed: {computed}",
             f"  Commit:   {commit or 'none recorded'}", f"  Test:     {test}"]
    if debt:
        lines += [f"  Missing:  {item}" for item in debt]
        lines += [f"  Next:     {next_step(item, record)}" for item in debt]
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
