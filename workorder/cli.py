"""The workorder command."""

from __future__ import annotations

from .console import print

import argparse
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import subprocess
import sys

from . import __version__, console, gate, timing
from . import store as s


def timeout_argument(value):
    try:
        return s.positive_seconds(value)
    except s.Refused as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def jobs_argument(value):
    try:
        jobs = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('jobs must be a positive integer') from None
    if jobs < 1:
        raise argparse.ArgumentTypeError('jobs must be a positive integer')
    return jobs


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
    init.add_argument("--claude", action="store_true", help="also install Claude Code project hooks")
    init.add_argument("--update-workflow", action="store_true", help="rewrite the workflow and show its diff; --check previews only")
    init.add_argument("--light", action="store_true", help="write a trial workflow with review warnings and no task requirement")
    init.add_argument('--test-first', action='store_true', help='require tests to land before implementation in the workflow')
    init.add_argument('--solo', action='store_true',
                      help='solo mode: your own workorder-allow labels count as owner acceptance (never review) when'
                           ' GitHub confirms you as owner or admin; recorded in .workorder/config.json and the workflow')
    init.add_argument('--require-acceptance', action='store_true',
                      help="refuse DONE tasks in the pull request until a person accepts them (an approving review)")
    project = init.add_mutually_exclusive_group()
    project.add_argument('--project-test', metavar='CMD', help="set the project's test command in .workorder/config.json"
                         " (a first init turns on python -m pytest -q when it finds pytest tests)")
    project.add_argument('--no-project-test', action='store_true',
                         help="turn the project test off, or keep a first init from turning it on")
    hook = commands.add_parser("hook", help="run a local agent session or stop check")
    hook.add_argument("event", choices=["session-start", "stop"])
    done = commands.add_parser("done", help="record implementation and tests, then claim DONE if supported")
    done.add_argument("--task", required=True)
    done.add_argument("--note", help="add a dated Progress line when DONE is written")
    done.add_argument("--needs-review", action="store_true", help="write DONE pending a reviewer label or approval")
    done.add_argument("--refactor", action="store_true", help="declare unchanged tests passing on both base and current code")
    done.add_argument("--no-code-change", action="store_true", help="allow verification tasks with no code change")
    done.add_argument('--require-test-first', action='store_true', help='refuse tests added or changed with the implementation')
    done.add_argument('--verbose', action='store_true', help='print every finding and all test output instead of a summary')
    done.add_argument('--take-over', action='store_true',
                      help="finish a task another clone has claimed; the takeover is added to its Progress notes")
    done.add_argument('--project-test', action='store_true',
                      help="also run the project test (your full suite); by default it runs in the pull-request check")
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
    new.add_argument('--test-first', action='store_true', help='require existing test files and a failing run before opening the task')
    new.add_argument("--test-paths", nargs="+", metavar="PATH",
                     help="test files or folders; required for wrapper commands, even before the files exist")
    new.add_argument("--code-paths", nargs="+", metavar="PATH",
                     help="code files or folders in scope; changes outside them are flagged by --require-task")
    new.add_argument("--acceptance", action="append", default=[], metavar="TEXT",
                     help="an Acceptance item (repeatable); end it with (test: path::name) to tie it to a"
                          " pytest test that done checks")
    new.add_argument("--kind", choices=list(s.TASK_KINDS),
                     help="the task's kind; refactor and verification predeclare those completions"
                          " once the task lands (default: no kind)")
    new.add_argument("--template", choices=["extended", "simple"], default="extended",
                     help="extended: planning fields and Context/Scope/Lanes/Acceptance/Progress sections;"
                          " simple: the seven required fields only, as in 0.1.0 (default: extended)")
    accept = commands.add_parser("accept", help="record that a person confirms a DONE task's Acceptance items"
                                 " (refused for an agent: WORKORDER_AGENT is set)")
    accept.add_argument("--task", required=True)
    accept.add_argument("--note", help="what the person checked (kept in the acceptance receipt)")
    receipt = commands.add_parser("receipt", help="record evidence: implemented, test, merged, deployed, live")
    receipt.add_argument("kind", choices=sorted(s.EVIDENCE_KINDS))
    receipt.add_argument("--task", required=True)
    receipt.add_argument("--target", help="deployment name, required for deployed and live")
    receipt.add_argument("--ref", default="refs/heads/main",
                         help="branch on origin that merged reads (default: refs/heads/main)")
    helps = {"show": "print a task's claim and evidence",
             "claim": "take a task with a pushed Git tag", "release": "give up your claim on a task"}
    for name, text in helps.items():
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--task", required=True)
        if name == "show":
            sub.add_argument('--verbose', action='store_true', help='print every pending finding instead of a summary')
        if name == "release":
            sub.add_argument("--force", action="store_true",
                             help="release a claim whose clone or worktree is gone")
    commands.add_parser("claims", help="list active claims")
    renew = commands.add_parser("renew", help="record implemented and the task's test again on the current"
                                " commit (after a squash merge or rebase); merges are computed")
    renew.add_argument("--task", required=True)
    renewal_kind = renew.add_mutually_exclusive_group()
    renewal_kind.add_argument("--refactor", action="store_true", default=None,
                              help="declare unchanged tests passing before and after (kept by default)")
    renewal_kind.add_argument("--no-refactor", action="store_false", dest="refactor",
                              help="drop the previous refactor declaration")
    note = commands.add_parser("note", help="set the next action or add a dated Progress line")
    note.add_argument("--task", required=True)
    note.add_argument("--next", dest="next_text", help="set the task's next field")
    note.add_argument("text", nargs="?", help="what was done, or what is next")
    retire = commands.add_parser("retire", help="mark a DONE or CLOSED task RETIRED: its record and receipts stay,"
                                 " it no longer owns its test files and its test no longer reruns")
    retire.add_argument("task")
    retire.add_argument("--reason", required=True, help="why the task's protection ends (added to Progress)")
    repair = commands.add_parser("repair", help="convert a verified legacy append conflict to receipt files")
    repair.add_argument("--task", required=True)
    commands.add_parser("ready", help="list open work: free, held, blocked or awaiting delivery")
    check = commands.add_parser(
        "check", help="refuse DONE/CLOSED claims the evidence does not support")
    check.add_argument("task", nargs="?",
                       help="explain one task, by id (TASK-1) or path (tasks/TASK-1.md)")
    check.add_argument("--task", dest="named_task", help="explain one task (also accepts a positional id)")
    check.add_argument("--staged", action="store_true", help="check the staged files (pre-commit)")
    check.add_argument("--verbose", action="store_true",
                       help="print every task note, finding and test output line instead of grouping them")
    check.add_argument("--allow", metavar="TASKS", default="",
                       help="with --base: accept definition/test edits, base-test failures, reduced pytest outcomes or complete removal"
                            " (comma-separated ids; tests for test-file edits, no-task for changes without task work,"
                            " workflow for Workorder workflow changes)")
    check.add_argument('--approving-review', action='store_true',
                       help='an approving review clears every finding except edits to existing tests and Workorder'
                            ' workflow changes, which need labels, as do existing tests that now fail and a task\'s'
                            ' pending review of them (its task label); failed current tests and unsupported claims'
                            ' always refuse; --allow names explicit labels')
    check.add_argument('--owner-accepts', metavar='LABELS', default='',
                       help='with --base in solo mode: labels the repository owner added (same ids as --allow);'
                            ' they clear what --allow would and print as owner acceptance, never as review')
    check.add_argument('--require-test-first', action='store_true', help='with --base: require tests to land before implementation')
    check.add_argument('--require-acceptance', action='store_true',
                       help="refuse DONE/CLOSED tasks a person has not accepted (workorder accept, or with --base"
                            " an approving review); tied tests alone are the author's evidence")
    check.add_argument('--approved-by', metavar='NAMES', default='',
                       help='with --approving-review: the approving reviewers, named in Acceptance lines'
                            ' (the workflow sets this)')
    check.add_argument("--run-tests", action="store_true",
                       help="run selected tasks' tests; with --base, include tasks with changed tests and base comparisons")
    check.add_argument("--strict-tests", action="store_true",
                       help="with --base: require --allow for all test and pytest configuration changes")
    check.add_argument("--all-tests", action="store_true",
                       help="with --run-tests: also test every DONE/CLOSED task, once per code, command and timeout")
    check.add_argument("--require-task", action="store_true",
                       help="with --base: require a new counting receipt on a task with a concrete test command for non-test code changes;"
                            " an unfinished task's hand-recorded receipts need an approving review or its label")
    check.add_argument('--require-task-ignore', action='append', default=[], metavar='GLOB',
                       help='with --require-task: ignore matching repository paths (repeatable)')
    for sub in (done, check):
        sub.add_argument('--jobs', type=jobs_argument, metavar='N',
                         help='run distinct test commands with N workers (default: CPU count)')
    check.add_argument("--review", choices=["warn", "refuse"],
                       help="with --base: refuse test changes until accepted (default), or opt in to warnings")
    check.add_argument("--base", metavar="REF",
                       help="check changes since REF: changed or removed receipts; requirement changes"
                            " (test, contract, evidence, dependencies, Scope and Acceptance lines);"
                            " removed or changed test-file lines; tasks created and claimed finished in"
                            " the same change; and task removal. Use --allow for reviewer acceptance of"
                            " definition changes or complete removal of finished or cancelled tasks;"
                            " use --run-tests to rerun declared tests and compare changed tests with their base versions")
    for sub in (done, receipt, renew, check):
        sub.add_argument("--timeout", type=timeout_argument, metavar="SECONDS",
                         help="test deadline in seconds, overriding the task's test_timeout")
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
    if item.get('no_tests_passed'):
        return f"its test ran but no test passed ({item['no_tests_passed']})"
    if s.head(root) != item["commit"]:
        return "HEAD moved while the command ran"
    paths = s.dirty_paths(root)
    if paths:
        return "files changed while the command ran; " + s.describe_dirty(paths)
    return "the code changed while the command ran"


def finding_tasks(text):
    return set(re.findall(r'\b[A-Z][A-Z0-9]*-[1-9][0-9]*\b', text))


def other_findings(other):
    if other:
        wording = 'task has' if len(other) == 1 else 'tasks have'
        print(f'  Other:    {len(other)} other {wording} findings ({", ".join(sorted(other))}); '
              'workorder check --base <target> --run-tests shows them')


def claim_warning(root, tid, action):
    """Claims are advisory for note and receipt: name another holder and carry on."""
    try:
        held = s.foreign_claim(root, tid)
    except s.Refused:
        return None
    if held:
        print(f"Warning: {tid} is held by {held['who']} since {held['at']}; {action} anyway: "
              f"coordinate with {held['who']}")
    return held


def done_refusal(tid, status, lines, other=(), *, needs_review=False, recordable=True):
    """Show this task's findings once and one next action; summarize other tasks."""
    reasons = list(dict.fromkeys(part.strip() for line in lines if line.startswith('  Missing:')
                                 for part in line.removeprefix('  Missing:').splitlines() if part.strip()))
    own, other = [], set(other)
    for reason in reasons:
        # A test's own name (test_flag[SHOUT-1]) is not a task id.
        name = console.finding_name(reason)
        owners = finding_tasks(reason.replace(name, '', 1) if name else reason)
        if owners and tid not in owners:
            other |= owners
            if reason.startswith('regression:'):
                own.append(reason)
        else:
            own.append(reason)
    if status in gate.FINISHED:
        print(f'{tid} stays {status}; this change was refused:')
    elif status is not None:
        print(f'{tid}: DONE was not written; the status stays {status}')
    console.print_lines(own, prefix='  Missing:  ')
    other_findings(other)
    steps = [line for line in lines if line.startswith('  Next:')]
    if any('test changed:' in reason for reason in own):
        steps = [f'  Next:     commit the test change, then run workorder renew --task {tid}; '
                 f'the pull request needs workorder-allow:{tid} (an approving review is not enough: {gate.TEST_EDIT})']
    elif own and not other and all('asserts nothing that can fail' in reason or
                                  reason.endswith('; ' + gate.label_advice(tid))
                                  for reason in own):
        if any('its test must land on' in reason for reason in own):
            steps = [f'  Next:     land the failing test before the work; to request an exception, '
                     f'run workorder done --task {tid} without --require-test-first; '
                     f'the pull request needs workorder-allow:{tid} or an approving review']
        elif not needs_review:
            steps = [f'  Next:     fix the test, or run workorder done --task {tid} --needs-review; '
                     f'the pull request needs workorder-allow:{tid} or an approving review']
    elif (own and not other and not needs_review and recordable
          and all(gate._accepts_allow_label(reason) for reason in own)):
        # Findings done --needs-review records (an edited or weakened existing test): offer it, naming
        # the one label that then clears the review.
        label_only = [{'label_only': 'an approving review clears this' not in reason} for reason in own]
        steps = [f'  Next:     fix the test, or run workorder done --task {tid} --needs-review; the pull request then '
                 + gate.pending_review_message(tid, label_only).removeprefix('pending review: the pull request ')]
    print(steps[0] if steps else f'  Next:     address the findings, then run workorder done --task {tid} again')


def base_code_findings(root, base, record, timeout, results):
    """The base-code finding from the run done started beside the stubbed run, if it showed one."""
    if not base:
        return []
    try:
        with s.base_reuse(), console.capture([]):
            found = gate.local_test_problems(root, base, record, timeout=timeout, notes=[], completed=True,
                                             results=results)
    except (s.Refused, OSError, ValueError, subprocess.SubprocessError):
        return []
    return [str(line) for line in found if "'s code too, so it does not show the change works" in str(line)]


def done_evidence(lines, tid, base, proven, results):
    """One Evidence line naming what done's runs showed, in place of the repeated pass."""
    facts = ['passes now']
    if base and tid in proven:
        facts.append(f"failed on {base}'s code")
    stubbed = [value for key, value in dict.items(results) if isinstance(key, tuple) and key[:2] == ('stub', tid)]
    if any(isinstance(code, int) and code not in {0, 5} for code, _ in stubbed):
        facts.append('failed with the change stubbed out')
    shown = []
    for line in lines:
        if line == '  Evidence: verified: the declared test passed on this code':
            line = '  Evidence: verified: ' + ', '.join(facts)
        elif line.startswith('  Result: the declared test passed on '):
            if shown and not shown[-1]:
                shown.pop()
            continue
        shown.append(line)
    return shown


@s.read_session()
@s.test_output_session()
@s.completion_transaction
def finish_task(root, tid, note=None, timeout=None, needs_review=False, no_code_change=False, refactor=False,
                require_test_first=False, jobs=None, take_over=False, project_test=False):
    """Use the same evidence and claim checks as the individual commands, restoring status on refusal."""
    status = None
    pool = None
    try:
        record = s.record(root, tid)
        status = record['status']
        if status == 'RETIRED':
            raise s.Refused(f'{tid} is RETIRED; it no longer claims completion. To reopen it, set status: '
                            f'IN_PROGRESS in tasks/{tid}.md, then run workorder done again')
        if note is not None and not note.strip():
            raise s.Refused('a note needs some text')
        try:
            held = s.foreign_claim(root, tid)
        except s.Refused:
            held = None
            print('Note: claims were not checked: origin could not be reached')
        if held:
            print(f"Warning: {tid} is held by {held['who']} since {held['at']}; this clone does not hold its claim")
            if not take_over:
                done_refusal(tid, status, [
                    f"  Missing:  {tid} is claimed by {held['who']} (since {held['at']})",
                    f"  Next:     ask {held['who']} to release it, or run workorder done --task {tid} --take-over "
                    'to finish it and record the takeover in its Progress notes'])
                return 1
        if refactor and (needs_review or no_code_change):
            raise s.Refused('--refactor cannot be combined with --needs-review or --no-code-change')
        dirty = s.dirty_paths(root)
        if dirty:
            setup = {'AGENTS.md', 'CLAUDE.md', '.claude/settings.json', '.github/workflows/workorder.yml'}
            advice = 'commit the files written by workorder init first; ' if setup & set(dirty) else 'commit the code first; '
            raise s.Refused(advice + s.describe_dirty(dirty))
        command = gate.declared_test(record)
        if not command:
            raise s.Refused(f"{tid} needs a declared test command; add test: to tasks/{tid}.md")
        wrapper = gate.wrapper_note(record)
        if wrapper:
            print(f'Note: {wrapper}')
        sections = s.task_body_sections(s.read_text(root, f"tasks/{tid}.md"))
        filled = {heading: re.sub(r'<!--.*?-->', '', '\n'.join(body), flags=re.S).strip()
                  for heading, body in sections}
        if not all(filled.get(name) for name in ("Scope", "Acceptance")):
            raise s.Refused("fill in Scope and Acceptance first")
        revision, reference = gate.implementation_base(root, record)
        if no_code_change:
            problem = gate.no_code_change_problem(root, record, revision)
            if problem:
                raise s.Refused(problem)
        base = gate.local_test_base(root, record)
        if base:
            unchanged, origin = gate.test_origin(root, record, revision, base)
            print(origin)
            if require_test_first and not unchanged:
                raise s.Refused(gate.test_first_problem(tid, base))
        else:
            print(f"{tid}'s test origin is unknown: no target is available locally")
            if require_test_first:
                raise s.Refused('test-first needs a locally available target')
        if refactor and not base:
            raise s.Refused('a declared refactor needs a locally available target; fetch origin first')
        if refactor and gate.changed_test_paths(root, revision, gate.refactor_test_files(root, record, revision)):
            raise s.Refused(f'{tid}: its test files changed; use --needs-review')
        material = gate.repository_test_files(root, revision, gate.records(root, errors={}))
        material |= gate.strict_test_files(root, record, revision)
        assertion_problems = gate.assertion_problems(root, revision, record,
            paths=gate.refactor_test_files(root, record, revision), material=material)
        pending_findings = [gate.reviewer_label(problem, tid) for problem in assertion_problems]
        if assertion_problems and not needs_review:
            raise s.Refused('\n'.join(assertion_problems))
        unchanged_finished = status in gate.FINISHED and not gate.task_change_touched(root, revision, record)
        if not no_code_change and not unchanged_finished and not any(gate.needs_task_receipt(path)
                                          for path in gate.changed_paths(root, revision)):
            raise s.Refused(f'{tid}: no code changed since {reference}; nothing was implemented; '
                            'land the task on main first, then do the work on a branch')
        if 'code_paths' not in record and not no_code_change:
            code = sorted(path for path in gate.changed_paths(root, revision) - material
                          if gate.needs_task_receipt(path))
            if code:
                print(f'{tid} changed: {", ".join(code)}; set code_paths to have changes outside them flagged')
        hook = root / s.git(root, 'rev-parse', '--git-path', 'hooks/pre-commit').stdout.strip()
        if not hook.is_file():
            print("Warning: pre-commit hook is missing in this clone; run workorder init")
        implemented = gate.newest_implementation(s.entries(root, tid))
        if implemented is None or implemented["code"] != s.code_digest(root, s.head(root)):
            item = s.receipt(root, tid, "implemented")
        item = s.receipt(root, tid, "test", command, timeout=timeout, no_code_change=no_code_change,
                         refactor=refactor, pending_review=needs_review, completion=True)
        if item['exit_code'] == 0 and not item['valid']:
            print('test does not count: ' + invalid_reason(root, item))
            if s.dirty_paths(root):
                print('Add generated files such as __pycache__/ to .gitignore; commit the change and run done again.')
        lines, refused = gate.explain(root, tid, claim="DONE", rerun=True)
        if refused or item["exit_code"] or not item["valid"]:
            if item["exit_code"]:
                lines = [line for line in lines if not line.startswith('  Next:')]
                lines.append('  Next:     fix the code or the test, then run workorder done again')
            done_refusal(tid, status, lines, needs_review=needs_review,
                         recordable=item['valid'] and not item['exit_code'])
            return 1
        from . import project_tests
        if project_test:
            project_notes = []
            project_problems = project_tests.run(root, base=revision if base else None, label=base,
                                                 notes=project_notes, timeout=timeout)
            console.print_lines(project_notes, prefix='Note: ')
            if project_problems:
                # done --needs-review does not record project-test failures: no offer of it here.
                done_refusal(tid, status, [f'  Missing:  {problem}' for problem in project_problems],
                             recordable=False)
                return 1
        else:
            print('Note: ' + project_tests.deferred_line(root))
        print("Acceptance:")
        print(filled["Acceptance"])
        exempt = refactor or no_code_change or record.get('kind') in ('refactor', 'verification')
        tied_problems, tied = gate.acceptance_check(root, record, base, exempt=exempt, timeout=timeout)
        if tied_problems:
            done_refusal(tid, status, [f'  Missing:  {problem}' for problem in tied_problems]
                         + ['  Next:     fix the test or the code, or correct the item\'s (test: path::name), '
                            f'then run workorder done --task {tid} again'], recordable=False)
            return 1
        print(tied or "Acceptance is not checked by Workorder: confirm each item is met.", flush=True)
        if tied:
            s.annotate_acceptance_tests(root, tid, [node for _, node in s.acceptance_items(
                s.read_text(root, f"tasks/{tid}.md")) if node is not None])
        results = gate.ConcurrentResults()
        notes = []
        proven = set()  # done's comparison showed the task's tests fail on the base code
        if base and (jobs or 3) > 1:
            # The new-code copy, base-code and stubbed runs are independent: run them at the same time.
            # Their output is printed in the usual order when each result is read.
            pool = ThreadPoolExecutor(max_workers=min(jobs or 3, 3) - 1)
            gate.prefetch_comparison(root, gate.comparison_base(root, base)[0], record, results, pool,
                                     timeout=timeout)
        stub = gate.stub_problem(root, revision, record, timeout=timeout, results=results, notes=notes)
        if stub and needs_review and gate._accepts_allow_label(stub):
            pending_findings.append(stub)
        elif stub:
            # The base-code run ran beside the stubbed one: name both facts when both are known.
            done_refusal(tid, status, [f'  Missing:  {line}' for line in
                                       base_code_findings(root, base, record, timeout, results) + [stub]],
                         needs_review=needs_review)
            return 1
        if base:
            revision, label = gate.comparison_base(root, base)
            records = gate.records(root, errors={})
            declared = {}
            for other, definition in records.items():
                if other != tid and gate.pending_review(root, definition, revision):
                    notes.append(f'{other}: ' + gate.pending_review_message(other))
                    findings = gate.covered_pending_findings(root, definition, revision)
                    if findings is not None:
                        declared[other] = findings
            for problem in gate.repository_review_problems(root, revision, records, run_tests=True,
                                                           timeout=timeout):
                problem = gate.comparison_message(problem, revision, base, label)
                owner = problem.split(':', 1)[0]
                owner = owner.split(', ', 1)[0]
                # A test file no task owns is accepted with workorder-allow:tests, as in the pull request.
                line = gate.reviewer_label(problem, owner) if s.ID.fullmatch(owner) else problem
                if needs_review:
                    notes.append(line)
                else:
                    # File findings for this task are supplied by its comparison below.
                    if owner != tid:
                        notes.append(line)
            with s.base_reuse():
                # The base code does not change while someone works: an unchanged base run is reused.
                problems = gate.local_test_problems(root, base, record, timeout=timeout, notes=notes,
                                                    completed=True, pending_review=needs_review, results=results,
                                                    proven=proven)
            problems += gate.local_regression_problems(root, base, exclude={tid}, timeout=timeout,
                                                       notes=notes, results=results, pending_review=needs_review, jobs=jobs)
            if not needs_review:
                problems += [note for note in notes if gate._accepts_allow_label(note)]
            blocking = []
            for problem in dict.fromkeys(problems):
                owners = finding_tasks(problem)
                if (gate._accepts_allow_label(problem) and owners and tid not in owners
                        and all(owner in declared and (problem in declared[owner]
                                or problem.startswith(f'{owner}: its test files ')) for owner in owners)):
                    notes.append(problem)
                else:
                    blocking.append(problem)
            problems = blocking
            other_notes = set()
            for review_note in dict.fromkeys(notes):
                owners = finding_tasks(review_note)
                if review_note in problems:
                    continue
                if owners and tid not in owners:
                    if 'its code changed after it was finished' in review_note:
                        print(f'Note: {review_note}')
                    elif not review_note.endswith((': ' + gate.PASSED_NOTE, ': ' + gate.REUSED_NOTE)):
                        other_notes |= owners
                elif review_note != wrapper:
                    if needs_review and gate._accepts_allow_label(review_note):
                        pending_findings.append(review_note)
                    else:
                        print(f"Note: {review_note.replace(gate.PASSED_NOTE, 'its test passed when done ran it')}")
            if problems:
                done_refusal(tid, status, [f'  Missing:  {problem}' for problem in problems], other_notes,
                             needs_review=needs_review)
                return 1
            other_findings(other_notes)
        else:
            print('Note: could not compare with the target: fetch origin first')
            changed_command = gate.local_command_problems(root, 'HEAD', record)
            if changed_command:
                done_refusal(tid, status, [f'  Missing:  {problem}' for problem in changed_command],
                             needs_review=needs_review)
                return 1
        if pool is not None:
            # No background run may still be reading the work tree while the task file changes.
            pool.shutdown(wait=True)
        if needs_review:
            s.annotate_pending_review(root, tid, pending_findings)
        path = s.local_path(root, f"tasks/{tid}.md")
        before = path.read_bytes()
        supported = False
        try:
            s.set_task_field(root, tid, "status", "DONE")
            if record.get("next"):
                s.set_task_field(root, tid, "next", "")
            if s.record(root, tid)['status'] != 'DONE':
                raise s.Refused('could not set the task status to DONE')
            lines, refused = gate.explain(root, tid, rerun=True)
            supported = not refused
        finally:
            if not supported:
                path.write_bytes(before)
        if refused:
            done_refusal(tid, status, lines, needs_review=needs_review)
            return 1
        if held:
            print(f"{tid}: " + s.add_note(root, tid, f"taken over from {held['who']} (claim held since "
                                               f"{held['at']}) by {s.who(root)}"))
        if note is not None:
            print(f"{tid}: {s.add_note(root, tid, note)}")
        if base and (refactor or no_code_change):
            exemption = gate.completion_kind_note(root, revision, s.record(root, tid))
            if exemption:
                print(f'Note: {exemption}')
        pending_now = gate.pending_findings(root, s.record(root, tid)) if needs_review else []
        if base and gate.fields_at(root, tid, revision) is None:
            if any(item['label_only'] for item in pending_now):
                # Approval cannot clear an existing test's change: the task's label clears both.
                lines.append(f'  Next:     the pull-request check will ask for the label workorder-allow:{tid}: '
                             'it accepts this new task\'s definition and its pending review (an approving review '
                             'is not enough: it changes existing tests); a second pull request is not needed')
            else:
                lines.append('  Next:     the pull-request check will ask for an approving review: this task is new '
                             f'in this change (the reviewer reads its definition; or the label workorder-allow:{tid}); '
                             'a second pull request is not needed')
        print("\n".join(done_evidence(lines, tid, base, proven, results)))
        _, owed, delivery = gate.verdict(root, s.record(root, tid))
        debt = f"; still owes: {', '.join(owed + delivery)}" if owed or delivery else ""
        if needs_review or gate.waiting_for_review(root, s.record(root, tid)):
            findings = gate.pending_findings(root, s.record(root, tid))
            print(f'{tid} DONE, ' + gate.pending_review_message(tid, findings) + debt)
        else:
            print(f"{tid} DONE: evidence verified{debt}")
        push = " and push" if s.has_origin(root) else ""
        print(f"commit tasks/ and .workorder/{push}")
        return 0
    except (s.Refused, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        done_refusal(tid, status, [f"  Missing:  {error}"], needs_review=needs_review)
        return 1
    finally:
        if pool is not None:
            # A run that a refusal made unnecessary finishes in its own copy and is ignored.
            pool.shutdown(wait=True, cancel_futures=True)


@s.read_session()
def accept_task(root, tid, note=None):
    """A person confirms a DONE task's Acceptance items on the tested code; an agent cannot."""
    if 'WORKORDER_AGENT' in os.environ:
        raise s.Refused(f"{tid}: an agent cannot accept a task (WORKORDER_AGENT is set); acceptance is a person"
                        f" confirming the Acceptance items: ask a person to run workorder accept --task {tid}"
                        " or to approve the pull request")
    if note is not None and not note.strip():
        raise s.Refused('a note needs some text')
    record = s.record(root, tid)
    if record['status'] not in gate.FINISHED:
        raise s.Refused(f"{tid} is {record['status']}; acceptance needs it DONE with supported evidence:"
                        f" run workorder done --task {tid} first")
    dirty = s.dirty_paths(root)
    if dirty:
        raise s.Refused('commit the code first; ' + s.describe_dirty(dirty))
    lines, refused = gate.explain(root, tid)
    if refused:
        print("\n".join(lines))
        raise s.Refused(f"{tid}: its evidence does not support {record['status']}; acceptance needs supported evidence")
    history = s.entries(root, tid)
    gate.recognise_squashed(root, tid, history)
    implemented = gate.newest_implementation(history)
    if implemented is None or implemented['missing']:
        raise s.Refused(f'{tid}: its implementation is not in this clone; run workorder renew --task {tid} first')
    if (s.code_digest(root, s.head(root)) != implemented['code']
            and gate.task_change_touched(root, implemented.get('seen_on', implemented['commit']), record)):
        raise s.Refused(f"{tid}: its code changed after the tested commit {implemented['commit'][:7]};"
                        f" run workorder renew --task {tid} on this commit first")
    item = s.acceptance_receipt(root, tid, implemented['hash'], note)
    print(f"{tid} accepted by {item['who']} on {item['commit'][:7]}"
          + (f": {item['note']}" if item.get('note') else ''))
    state = gate.acceptance_state(root, s.record(root, tid))
    print(f"  Evidence: verified\n  Acceptance: {state}")
    push = " and push" if s.has_origin(root) else ""
    print(f"commit .workorder/{push}")
    return 0


@s.read_session()
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    command = []
    if "--" in args:
        position = args.index("--")
        args, command = args[:position], args[position + 1:]
    options = parser().parse_args(args)
    hint = ('set WORKORDER_VERBOSE=1 to print it all' if options.action == 'hook'
            else 'rerun with --verbose to print it all')
    timed = options.action == 'done' or options.action == 'check' and options.run_tests
    with console.session(verbose=getattr(options, 'verbose', False), hint=hint) as run, \
            timing.session() as clock:
        code = run_command(options, command, run)
        if timed and clock.opened:  # a refusal before any run has no time to show
            # On stderr: a diagnostic about this run, not part of its findings.
            print(timing.line(clock), file=sys.stderr)
        return code


def run_command(options, command, run):
    root = Path.cwd()
    try:
        root = s.repository(root)
        run.root = root
        if options.action == "check":
            if options.task and options.named_task:
                raise s.Refused("give the task once, positionally or with --task")
            options.task = options.task or options.named_task
            if (options.allow or options.strict_tests or options.require_task or options.require_test_first
                    or options.approving_review or options.owner_accepts) and not options.base:
                raise s.Refused("--allow, --approving-review, --owner-accepts, --strict-tests, --require-task"
                                " and --require-test-first go with --base")
            if options.review and not options.base:
                raise s.Refused("--review goes with --base")
            if options.approved_by and not options.approving_review:
                raise s.Refused("--approved-by goes with --approving-review")
            if options.all_tests and not options.run_tests:
                raise s.Refused("--all-tests goes with --run-tests")
            if options.task and options.run_tests:
                raise s.Refused('--run-tests does not run with a task name; '
                                'use workorder check --run-tests without a task')
            if options.timeout is not None and not options.run_tests:
                raise s.Refused("--timeout goes with --run-tests")
        if options.action == "receipt" and options.timeout is not None and options.kind != "test":
            raise s.Refused("--timeout applies to receipt test")
        if command and options.action != "renew" and (options.action != "receipt"
                                                      or options.kind not in {"test", "deployed", "live"}):
            raise s.Refused("this command does not accept a command after --")
        if options.action == "init":
            from .bootstrap import initialize
            return initialize(root, options.check, options.force, claude=options.claude,
                              update_workflow=options.update_workflow, light=options.light, test_first=options.test_first,
                              require_acceptance=options.require_acceptance,
                              project_test=options.project_test, no_project_test=options.no_project_test,
                              solo=options.solo)
        elif options.action == "hook":
            from .hooks import run
            return run(root, options.event)
        elif options.action == "done":
            return finish_task(root, s.task_id(options.task), options.note, options.timeout,
                               options.needs_review, options.no_code_change, options.refactor, options.require_test_first, options.jobs,
                               take_over=options.take_over, project_test=options.project_test)
        elif options.action == "new":
            shared = s.has_origin(root) and not options.local
            if options.test_paths is None and gate.wrapper_paths({'test': options.test}) is not None:
                raise s.Refused('this test command runs a wrapper; supply --test-paths PATH [PATH ...] '
                                'so changes to its tests are reviewed')
            named = (options.test_paths if options.test_paths is not None else
                     gate.paths_named_by_command(options.test, root) if options.test else None)
            definition = {'id': options.prefix + '-1', 'test': options.test}
            if (options.test_paths is None and gate.uses_pytest(definition)
                    and not gate.pytest_collection_paths(definition)):
                inferred = gate.default_pytest_files(root, definition, 'HEAD')
                if not inferred:
                    raise s.Refused('no test files are known for this pytest command; pass --test-paths PATH [PATH ...]')
                if options.test_first:
                    named = sorted(inferred)
            requested = (options.test_paths if options.test_paths is not None else
                         gate.paths_named_by_command(options.test, root, include_missing=True) if options.test else [])
            for path in requested:
                if not s.local_path(root, path).exists():
                    print(f'Warning: {path} does not exist; create it in this change or correct --test.')
            for path in [*(named or []), *(options.code_paths or [])]:
                s.local_path(root, path)
            if options.test_first:
                definition = {'id': options.prefix + '-1', 'test': options.test, 'test_paths': named or []}
                command = gate.declared_test(definition)
                if not command or not named:
                    raise s.Refused('test-first needs a concrete test command and declared test files')
                for path in named:
                    if not s.local_path(root, path).exists():
                        raise s.Refused(f'{path} does not exist; write the failing test before opening the task')
                code, output = gate._run_test(root, command, None, None, quiet=True)
                if code == 0:
                    s.print_test_output(output)
                    raise s.Refused('the declared test already passes; write a failing test before opening the task')
                missing = s.missing_test_dependency(command, code, output, root=root)
                if isinstance(code, str) or code in {5, 124, 127} or missing:
                    s.print_test_output(output, failed=True)
                    raise s.Refused('could not run the declared test: ' + str(missing or code))
                s.print_test_output(output, label='The declared test fails as required.')
            tid = s.new(root, options.title, options.owner, options.ships, options.prefix,
                        options.template, options.test, options.local, test_paths=named, code_paths=options.code_paths,
                        acceptance=options.acceptance, kind=options.kind)
            where = ("reserved on origin" if shared
                     else "local id: local numbering requested, not reserved on origin" if options.local
                     else "local id: no origin remote, so other clones could pick the same id")
            print(f"{tid} opened: tasks/{tid}.md ({where})")
        elif options.action == "receipt":
            s.record(root, options.task)
            claim_warning(root, options.task, 'recording evidence')
            if options.kind == "test" and not command:
                command = gate.declared_test(s.record(root, options.task)) or []  # the task's own test
            item = s.receipt(root, options.task, options.kind, command, options.target, options.ref,
                             timeout=options.timeout)
            detail = f" exit {gate.test_exit_meaning(item['exit_code'], command)}" if "exit_code" in item else ""
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
        elif options.action == "accept":
            return accept_task(root, s.task_id(options.task), options.note)
        elif options.action == "show":
            snapshot = s.fetch_upstream(root)
            if snapshot == {}:
                print("Note: origin could not be reached; showing local task.")
            elif s.newer_work(root, s.task_id(options.task), snapshot):
                print(f"{snapshot['ref']} has newer work on this task: pull before you start")
            console.print_lines(gate.record_lines(root, s.task_id(options.task)))
        elif options.action == "note":
            if options.text is None and options.next_text is None:
                raise s.Refused("give a note or --next TEXT")
            if options.text is not None and not options.text.strip():
                raise s.Refused("a note needs some text")
            s.record(root, options.task)
            claim_warning(root, options.task, 'adding the note')
            if options.next_text is not None:
                s.set_task_field(root, options.task, 'next', options.next_text)
                print(f"{options.task}: next: {options.next_text}")
            if options.text is not None:
                print(f"{options.task}: {s.add_note(root, options.task, options.text)}")
        elif options.action == "retire":
            tid = s.task_id(options.task)
            previous, line = s.retire(root, tid, options.reason)
            print(f"{tid} RETIRED (was {previous}): {line.removeprefix('- ')}")
            print(f"{tid}'s record, notes and receipts stay; it no longer owns its test files, "
                  "and its test no longer reruns in later checks")
            print(f"the pull request needs an approving review or workorder-allow:{tid}; "
                  "retiring removes a protection")
            push = " and push" if s.has_origin(root) else ""
            print(f"commit tasks/{tid}.md{push}")
        elif options.action == "repair":
            print(s.repair(root, options.task))
        elif options.action == "renew":
            record = s.record(root, options.task)
            command = command or gate.declared_test(record)
            if not command:
                raise s.Refused(f"{options.task} declares no test; add test: to tasks/{options.task}.md,"
                                " or put the test command after --")
            previous = next((item for item in reversed(s.entries(root, options.task)) if item['kind'] == 'test'), {})
            refactor = previous.get('refactor', False) if options.refactor is None else options.refactor
            if refactor:
                base = gate.local_test_base(root, record)
                if not base:
                    raise s.Refused('a declared refactor needs a locally available target; fetch origin first')
                revision, _ = gate.comparison_base(root, base)
                if gate.changed_test_paths(root, revision, gate.refactor_test_files(root, record, revision)):
                    raise s.Refused(f'{options.task}: its test files changed; use renew --no-refactor to drop the declaration')
                with s.temporary_checkout(prefix='workorder-refactor-') as temporary:
                    code, output = gate.run_ref_test(root, Path(temporary) / 'base', revision, command,
                                                     s.test_timeout(record, options.timeout), None)
                if isinstance(code, str):
                    raise s.Refused(f'{options.task}: could not compare with {base}: {code}')
                if code or gate.pytest_no_pass(output):
                    raise s.Refused(f'{options.task}: a declared refactor needs its test to pass on {base}')
            failed = False
            # Merges are computed from Git history; renew never records one that might not count.
            for kind in ("implemented", "test"):
                item = s.receipt(root, options.task, kind, command if kind == "test" else (),
                                 timeout=options.timeout, refactor=refactor if kind == "test" else False)
                detail = f" exit {gate.test_exit_meaning(item['exit_code'], command)}" if "exit_code" in item else ""
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
            record = s.record(root, task_argument(options.task))
            claimed = record["status"] in gate.FINISHED
            tally = {}
            if claimed and not refused:
                state = gate.acceptance_state(root, record)
                tally = {'stale' if state.stale else 'confirmed' if state.confirmed else 'awaiting': 1}
                if options.require_acceptance and not state.confirmed:
                    refused = True
                    lines += [f"  Missing:  {gate.acceptance_problem(record['id'], state)}"]
            print("\n".join(lines))
            print(gate.check_summary(dict(tasks=1, claimed=int(claimed), acceptance=tally,
                                         tests_only=int(claimed and not refused and record['contract'] == 'none')),
                                     single_unclaimed=not claimed))
            return int(refused)
        else:
            notes = []
            counts = {}
            allow = {item.strip() for item in options.allow.split(",") if item.strip()}
            owner = {item.strip() for item in options.owner_accepts.split(",") if item.strip()}
            from . import project_tests
            # Solo mode holds only when the base records it too: a change cannot turn it on for itself.
            solo = bool(options.base) and project_tests.solo(root) and project_tests.solo(root, options.base)
            if owner and not solo:
                notes.append(f'owner labels not used ({", ".join(sorted(owner))}): solo mode is not recorded in'
                             f' .workorder/config.json both on {options.base} and in this change'
                             ' (workorder init --update-workflow --solo records it)')
                owner = set()
            if (options.staged and not options.base and not options.run_tests
                    and not options.all_tests and gate.staged_change_unchecked(root)):
                return 0  # the quiet result of a commit that touches no task, receipt, test or code
            # The owner's labels clear what the same labels from a reviewer would; a reviewer's label stays review.
            problems = gate.check(root, options.staged, options.base, notes, allow | owner, options.run_tests, counts,
                                  options.strict_tests, options.all_tests, options.require_task,
                                  timeout=options.timeout, review=options.review or "refuse",
                                  approving_review=options.approving_review, require_test_first=options.require_test_first,
                                  jobs=options.jobs, require_task_ignore=options.require_task_ignore,
                                  require_acceptance=options.require_acceptance,
                                  approvers=[name.strip() for name in options.approved_by.split(',') if name.strip()],
                                  owner_accepts=owner - allow)
            if (options.staged and not counts["tasks"] and not problems and not notes
                    and not counts.get("warnings") and not options.run_tests):
                return 0
            if problems:
                console.print_lines(problems)
                if options.staged and not options.base and any(gate._accepts_allow_label(p) for p in problems):
                    print(gate.LOCAL_REVIEW_HINT)
            for warning in counts.get('warnings', []):
                print(f'Warning: {warning}')
                if os.environ.get('GITHUB_ACTIONS'):
                    def escape(value, prop=False):
                        value = str(value).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                        return value.replace(':', '%3A').replace(',', '%2C') if prop else value
                    print(f'::warning file={escape(warning.path, True)},line={warning.line}::{escape(warning)}')
            if counts.get('warnings'):
                print(f"Review: {len(counts['warnings'])} test changes need a reviewer's look")
            if not problems:
                print("check: OK")
            if notes:
                console.print_lines(gate.summarize_notes(notes, options.verbose), prefix='Note: ')
            if options.run_tests and counts["tests"] == 0 and not (options.base and not counts['tasks']):
                print("no declared test ran in this check")
            print(gate.check_summary(counts, options.run_tests or bool(counts.get('tests')),
                                    unchanged_base=bool(options.base and not counts['tasks']),
                                    unchanged_all_tests=bool(options.base and options.all_tests
                                                             and not counts['tasks']
                                                             and counts.get('tested_tasks')),
                                    solo=solo))
            if problems:
                count = len(problems)
                print(f"check: NOT OK ({count} finding{'s' if count != 1 else ''})")
            return int(bool(problems))
        return 0
    except (s.Refused, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        print(f"workorder: {error}", file=sys.stderr)
        return 1
