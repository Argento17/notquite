"""The workorder command."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

from . import __version__, gate
from . import store as s


def parser():
    result = argparse.ArgumentParser(
        prog="workorder", description="The task record for coding agents: every task recorded, a clear history, agents always go back to it.",
        epilog="Pass a test or read-back command after --, for example: "
               "workorder receipt test --task TASK-1 -- python -m pytest")
    result.add_argument("--version", action="version", version=f"workorder {__version__}")
    commands = result.add_subparsers(dest="action", required=True, metavar="command")
    init = commands.add_parser("init", help="add agent instructions, a pre-commit hook and a workflow")
    init.add_argument("--check", action="store_true", help="report missing setup without changing files")
    init.add_argument("--force", action="store_true", help="replace a different pre-commit hook")
    done = commands.add_parser("done", help="record implementation and tests, then claim DONE if supported")
    done.add_argument("--task", required=True)
    done.add_argument("--note", help="add a dated Progress line, including when done refuses")
    new = commands.add_parser("new", help="reserve a task id on origin and write tasks/<id>.md")
    new.add_argument("--title", required=True)
    new.add_argument("--owner", required=True)
    new.add_argument("--ships", choices=["none", "repo", "live"], default="none",
                     help="what CLOSED needs beyond DONE: nothing, a merge, or also deployment"
                          " read-backs (default: none)")
    new.add_argument("--prefix", default="TASK", help="id prefix (default: TASK)")
    new.add_argument("--local", action="store_true",
                     help="number the id locally even with an origin remote (no tag is pushed)")
    new.add_argument("--test", default="", metavar="COMMAND",
                     help="the task's test command; once set, only a run of exactly this command counts")
    new.add_argument("--template", choices=["extended", "simple"], default="extended",
                     help="extended: planning fields and Context/Scope/Lanes/Acceptance/Progress sections;"
                          " simple: the seven required fields only, as in 0.1.0 (default: extended)")
    receipt = commands.add_parser("receipt", help="record evidence: implemented, test, merged, deployed, live")
    receipt.add_argument("kind", choices=sorted(s.KINDS))
    receipt.add_argument("--task", required=True)
    receipt.add_argument("--target", help="deployment name, required for deployed and live")
    receipt.add_argument("--ref", default="refs/heads/main",
                         help="branch on origin that merged reads (default: refs/heads/main)")
    helps = {"show": "print a task's recorded and computed status",
             "claim": "take a task with a pushed Git tag", "release": "give up your claim on a task"}
    for name, text in helps.items():
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--task", required=True)
        if name == "release":
            sub.add_argument("--force", action="store_true",
                             help="release a claim whose clone or worktree is gone")
    commands.add_parser("claims", help="list active claims")
    renew = commands.add_parser("renew", help="record implemented and the task's test again on the current"
                                " commit (after a squash merge or rebase); merges are computed")
    renew.add_argument("--task", required=True)
    note = commands.add_parser("note", help="add a dated line under the task's Progress section")
    note.add_argument("--task", required=True)
    note.add_argument("text", help="what was done, or what is next")
    repair = commands.add_parser("repair", help="convert a verified legacy append conflict to receipt files")
    repair.add_argument("--task", required=True)
    commands.add_parser("ready", help="list open work: free, held, blocked or awaiting delivery")
    check = commands.add_parser(
        "check", help="refuse DONE/CLOSED claims the evidence does not support")
    check.add_argument("task", nargs="?",
                       help="explain one task, by id (TASK-1) or path (tasks/TASK-1.md)")
    check.add_argument("--task", dest="named_task", help="explain one task (also accepts a positional id)")
    check.add_argument("--staged", action="store_true", help="check the staged files (pre-commit)")
    check.add_argument("--allow", metavar="TASKS", default="",
                       help="with --base: accept task definition or test file changes, or complete removal"
                            " (comma-separated ids, for a reviewer to pass)")
    check.add_argument("--run-tests", action="store_true",
                       help="with --base: run the declared test of every task this change claims DONE or CLOSED")
    check.add_argument("--strict-tests", action="store_true",
                       help="with --base: require --allow for all test and pytest configuration changes;"
                            " with --run-tests, refuse pytest runs with no passed tests")
    check.add_argument("--all-tests", action="store_true",
                       help="with --base --run-tests: also test every DONE/CLOSED task, once per command")
    check.add_argument("--require-task", action="store_true",
                       help="with --base: refuse code changes without a changed task file or receipt")
    check.add_argument("--base", metavar="REF",
                       help="check changes since REF: changed or removed receipts; requirement changes"
                            " (test, contract, evidence, dependencies, Scope and Acceptance lines);"
                            " removed or changed test-file lines; tasks created and claimed finished in"
                            " the same change; and task removal. Use --allow for reviewer acceptance of"
                            " definition changes or complete removal of finished or cancelled tasks;"
                            " use --run-tests to rerun declared tests of supported DONE/CLOSED claims")
    return result


def task_argument(value):
    path = Path(value)
    if path.suffix == ".md":
        if path.parent.as_posix() not in {"tasks", "./tasks"}:
            raise s.Refused("task files live in tasks/ at the repository root")
        value = path.stem
    return s.task_id(value)


def invalid_reason(root, item):
    if item["kind"] != "test":
        return f"the output did not include the full commit id {item['commit']}"
    if s.head(root) != item["commit"]:
        return "HEAD moved while the command ran"
    paths = s.dirty_paths(root)
    if paths:
        return "files changed while the command ran; " + s.describe_dirty(paths)
    return "the code changed while the command ran"


def done_refusal(tid, status, lines):
    """Describe the refusal without presenting the proposed DONE as a written claim."""
    if status is not None:
        print(f"{tid}: DONE was not written; the status stays {status}")
    print("\n".join(line for line in lines if line.startswith(("  Missing:", "  Next:"))))


def finish_task(root, tid, note=None):
    """Use the same evidence and claim checks as the individual commands, restoring status on refusal."""
    status = None
    try:
        record = s.record(root, tid)
        status = record['status']
        if note is not None:
            print(f"{tid}: {s.add_note(root, tid, note)}")
        dirty = s.dirty_paths(root)
        if dirty:
            raise s.Refused("commit the code first; " + s.describe_dirty(dirty))
        command = gate.declared_test(record)
        if not command:
            raise s.Refused(f"{tid} needs a declared test command; add test: to tasks/{tid}.md")
        implemented = gate.newest_implementation(s.entries(root, tid))
        if implemented is None or implemented["code"] != s.code_digest(root, s.head(root)):
            item = s.receipt(root, tid, "implemented")
            print(f"{tid} implemented {item['commit'][:7]} - recorded")
        item = s.receipt(root, tid, "test", command)
        print(f"{tid} test {item['commit'][:7]} exit {item['exit_code']} - recorded")
        lines, refused = gate.explain(root, tid, claim="DONE")
        if refused or item["exit_code"] or not item["valid"]:
            done_refusal(tid, status, lines)
            return 1
        print("Acceptance:")
        sections = s.task_body_sections(s.read_text(root, f"tasks/{tid}.md"))
        acceptance = [line for heading, body in sections if heading == "Acceptance"
                      for line in body if line.strip() and not line.strip().startswith("<!--")]
        print("\n".join(acceptance) or "  the Acceptance list is not filled in")
        print("Acceptance is not checked by Workorder: confirm each item is met.", flush=True)
        path = s.local_path(root, f"tasks/{tid}.md")
        before = path.read_bytes()
        # Match only the validated front matter; preserve every other byte, including CRLF and BOM.
        raw_lines = before.splitlines(keepends=True)
        closing = next(i for i in range(1, len(raw_lines)) if raw_lines[i].rstrip(b'\r\n') == b'---')
        end = sum(len(line) for line in raw_lines[:closing])
        front = re.sub(rb"(?m)^status:[^\r\n]*", b"status: DONE", before[:end], count=1)
        supported = False
        try:
            path.write_bytes(front + before[end:])
            if s.record(root, tid)['status'] != 'DONE':
                raise s.Refused('could not set the task status to DONE')
            lines, refused = gate.explain(root, tid)
            supported = not refused
        finally:
            if not supported:
                path.write_bytes(before)
        if refused:
            done_refusal(tid, status, lines)
            return 1
        print("\n".join(lines))
        push = " and push" if s.has_origin(root) else ""
        print(f"{tid} DONE: claim supported; commit tasks/ and .workorder/{push}")
        return 0
    except (s.Refused, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        done_refusal(tid, status, [f"  Missing:  {error}"])
        return 1


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    command = []
    if "--" in args:
        position = args.index("--")
        args, command = args[:position], args[position + 1:]
    options = parser().parse_args(args)
    root = Path.cwd()
    try:
        root = s.repository(root)
        if options.action == "check":
            if options.task and options.named_task:
                raise s.Refused("give the task once, positionally or with --task")
            options.task = options.task or options.named_task
            if (options.allow or options.run_tests or options.strict_tests or options.all_tests
                    or options.require_task) and not options.base:
                raise s.Refused("--allow, --run-tests, --strict-tests, --all-tests and --require-task go with --base")
            if options.all_tests and not options.run_tests:
                raise s.Refused("--all-tests goes with --base --run-tests")
        if command and options.action != "renew" and (options.action != "receipt"
                                                      or options.kind not in {"test", "deployed", "live"}):
            raise s.Refused("this command does not accept a command after --")
        if options.action == "init":
            from .bootstrap import initialize
            return initialize(root, options.check, options.force)
        elif options.action == "done":
            return finish_task(root, s.task_id(options.task), options.note)
        elif options.action == "new":
            shared = s.has_origin(root) and not options.local
            tid = s.new(root, options.title, options.owner, options.ships, options.prefix,
                        options.template, options.test, options.local)
            where = ("reserved on origin" if shared
                     else "local id: --local, not reserved on origin" if options.local
                     else "local id: no origin remote, so other clones could pick the same id")
            print(f"{tid} opened: tasks/{tid}.md ({where})")
        elif options.action == "receipt":
            if options.kind == "test" and not command:
                command = gate.declared_test(s.record(root, options.task)) or []  # the task's own test
            item = s.receipt(root, options.task, options.kind, command, options.target, options.ref)
            detail = f" exit {item['exit_code']}" if "exit_code" in item else ""
            print(f"{options.task} {options.kind} {item['commit'][:7]}{detail} - recorded")
            if command:
                print("command: " + s.join_command(command))
            record = s.record(root, options.task)
            reason = (invalid_reason(root, item) if item.get("exit_code") == 0 and not item["valid"]
                      else gate.uncounted(root, record, s.entries(root, options.task)))
            if reason:
                print("recorded, but does not count: " + reason)
            _, done, delivery = gate.verdict(root, record)
            print(f"{options.task} still owes: {gate.owes(record, done, delivery)}")
            failed = item.get("exit_code", 0) != 0 or not item.get("valid", True)
            return 1 if failed or reason else 0
        elif options.action == "show":
            snapshot = s.fetch_upstream(root)
            if snapshot == {}:
                print("Note: origin could not be reached; showing local task.")
            elif s.newer_work(root, s.task_id(options.task), snapshot):
                print(f"{snapshot['ref']} has newer work on this task: pull before you start")
            print("\n".join(gate.record_lines(root, s.task_id(options.task))))
        elif options.action == "note":
            print(f"{options.task}: {s.add_note(root, options.task, options.text)}")
        elif options.action == "repair":
            print(s.repair(root, options.task))
        elif options.action == "renew":
            record = s.record(root, options.task)
            command = command or gate.declared_test(record)
            if not command:
                raise s.Refused(f"{options.task} declares no test; add test: to tasks/{options.task}.md,"
                                " or put the test command after --")
            failed = False
            # Merges are computed from Git history; renew never records one that might not count.
            for kind in ("implemented", "test"):
                item = s.receipt(root, options.task, kind, command if kind == "test" else ())
                detail = f" exit {item['exit_code']}" if "exit_code" in item else ""
                print(f"{options.task} {kind} {item['commit'][:7]}{detail} - recorded")
                reason = gate.uncounted(root, s.record(root, options.task), s.entries(root, options.task))
                if item.get("exit_code", 0) != 0 or not item.get("valid", True) or reason:
                    print("recorded, but does not count: " + (reason or "the test did not pass"))
                    failed = True
                    break
            record = s.record(root, options.task)
            _, done, delivery = gate.verdict(root, record)
            print(f"{options.task} still owes: {gate.owes(record, done, delivery)}")
            return int(failed)
        elif options.action == "claim":
            held = s.claim(root, options.task)
            print(f"{options.task} claimed: you hold it now, as {held['who']} (since {held['at']})")
        elif options.action == "release":
            forced = s.release(root, options.task, options.force)
            print(f"{options.task} released" + (" (forced: its holder's clone did not release it)" if forced else ""))
        elif options.action == "claims":
            held = s.active_claims(root)
            lines = [f"{tid} held by {c['who']} since {c['at']}"
                     for tid, c in sorted(held.items()) if not c["released"]]
            print("\n".join(lines) or "no active claims")
        elif options.action == "ready":
            print("\n".join(gate.ready(root)) or "no open work")
        elif options.task:
            if options.staged or options.base:
                raise s.Refused("--staged and --base check every task; leave out the task")
            lines, refused = gate.explain(root, task_argument(options.task))
            print("\n".join(lines))
            record = s.record(root, task_argument(options.task))
            claimed = record["status"] in gate.FINISHED
            print(gate.check_summary(dict(tasks=1, claimed=int(claimed),
                                         tests_only=int(claimed and not refused and record['contract'] == 'none')),
                                     single_unclaimed=not claimed))
            return int(refused)
        else:
            notes = []
            counts = {}
            allow = {item.strip() for item in options.allow.split(",") if item.strip()}
            problems = gate.check(root, options.staged, options.base, notes, allow, options.run_tests, counts,
                                  options.strict_tests, options.all_tests, options.require_task)
            if options.staged and not counts["tasks"] and not problems and not notes and not options.run_tests:
                return 0
            print("\n".join(problems) if problems else "check: OK")
            if notes:
                print("\n".join(f"Note: {note}" for note in notes))
            if options.run_tests and counts["tests"] == 0:
                print("no declared test ran in this check")
            print(gate.check_summary(counts, options.run_tests))
            return int(bool(problems))
        return 0
    except (s.Refused, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"workorder: {error}", file=sys.stderr)
        return 1
