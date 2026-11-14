"""The notquite command."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import sys

from . import __version__, gate
from . import store as s


def parser():
    result = argparse.ArgumentParser(
        prog="notquite", description="Checks whether DONE actually means done.",
        epilog="Pass a test or read-back command after --, for example: "
               "notquite receipt test --task TASK-1 -- python -m pytest")
    result.add_argument("--version", action="version", version=f"notquite {__version__}")
    commands = result.add_subparsers(dest="action", required=True, metavar="command")
    new = commands.add_parser("new", help="reserve a task id on origin and write tasks/<id>.md")
    new.add_argument("--title", required=True)
    new.add_argument("--owner", required=True)
    new.add_argument("--ships", choices=["none", "repo", "live"], default="repo",
                     help="what CLOSED needs beyond DONE (default: repo)")
    new.add_argument("--prefix", default="TASK", help="id prefix (default: TASK)")
    new.add_argument("--template", choices=["simple", "extended"], default="simple",
                     help="extended adds planning fields and Context/Scope/Lanes/Acceptance/Progress"
                          " sections (default: simple)")
    receipt = commands.add_parser("receipt", help="record evidence: implemented, test, merged, deployed, live")
    receipt.add_argument("kind", choices=sorted(s.KINDS))
    receipt.add_argument("--task", required=True)
    receipt.add_argument("--target", help="deployment name, required for deployed and live")
    receipt.add_argument("--ref", default="refs/heads/main",
                         help="branch on origin that merged reads (default: refs/heads/main)")
    helps = {"show": "print a task's recorded and computed status",
             "claim": "take a task with a pushed Git tag", "release": "give up your claim on a task"}
    for name, text in helps.items():
        commands.add_parser(name, help=text).add_argument("--task", required=True)
    commands.add_parser("claims", help="list active claims")
    commands.add_parser("ready", help="list open work: free, held, blocked or awaiting delivery")
    check = commands.add_parser(
        "check", help="refuse DONE/CLOSED claims the evidence does not support")
    check.add_argument("task", nargs="?",
                       help="explain one task, by id (TASK-1) or path (tasks/TASK-1.md)")
    check.add_argument("--staged", action="store_true", help="check the staged files (pre-commit)")
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


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    command = []
    if "--" in args:
        position = args.index("--")
        args, command = args[:position], args[position + 1:]
    options = parser().parse_args(args)
    root = Path.cwd()
    try:
        s.repository(root)
        if command and (options.action != "receipt" or options.kind not in {"test", "deployed", "live"}):
            raise s.Refused("this command does not accept a command after --")
        if options.action == "new":
            shared = s.has_origin(root)
            tid = s.new(root, options.title, options.owner, options.ships, options.prefix,
                        options.template)
            where = ("reserved on origin" if shared
                     else "local id: no origin remote, so other clones could pick the same id")
            print(f"{tid} opened: tasks/{tid}.md ({where})")
        elif options.action == "receipt":
            item = s.receipt(root, options.task, options.kind, command, options.target, options.ref)
            detail = f" exit {item['exit_code']}" if "exit_code" in item else ""
            print(f"{options.task} {options.kind} {item['commit'][:7]}{detail} - recorded")
            if command:
                print("command: " + shlex.join(command))
            if item.get("exit_code") == 0 and not item["valid"]:
                print("not counted: " + invalid_reason(root, item))
            _, done, delivery = gate.verdict(root, s.record(root, options.task))
            print(f"{options.task} still owes: {', '.join(done + delivery) or 'nothing'}")
            return 0 if item.get("exit_code", 0) == 0 and item.get("valid", True) else 1
        elif options.action == "show":
            record = s.record(root, options.task)
            computed, done, delivery = gate.verdict(root, record)
            print(f"{options.task} recorded {record['status']}; computed {computed}")
            print("still owes: " + (", ".join(done + delivery) or "nothing"))
            needs = s.dependencies(record)
            if needs:
                print("waits_on: " + ", ".join(needs))
        elif options.action == "claim":
            held = s.claim(root, options.task)
            print(f"{options.task} held by {held['who']} since {held['at']}")
        elif options.action == "release":
            s.release(root, options.task)
            print(f"{options.task} released")
        elif options.action == "claims":
            held = s.active_claims(root)
            lines = [f"{tid} held by {c['who']} since {c['at']}"
                     for tid, c in sorted(held.items()) if not c["released"]]
            print("\n".join(lines) or "no active claims")
        elif options.action == "ready":
            print("\n".join(gate.ready(root)) or "no open work")
        elif options.task:
            if options.staged:
                raise s.Refused("--staged checks every task; leave out the task")
            lines, refused = gate.explain(root, task_argument(options.task))
            print("\n".join(lines))
            return int(refused)
        else:
            problems = gate.check(root, options.staged)
            print("\n".join(problems) if problems else "check: OK")
            return int(bool(problems))
        return 0
    except (s.Refused, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"notquite: {error}", file=sys.stderr)
        return 1
