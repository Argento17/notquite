"""Claude Code hook commands: local task context and completion checks."""

import json
import re
import sys

from . import gate
from . import store as s


def refresh_claim_refs(root):
    """Re-read claim and release tags so a force-release from another clone is visible."""
    if not s.has_origin(root):
        return
    spec = s.NAMESPACE + '*'
    s.git(root, 'fetch', '--no-tags', '--refmap=', 'origin', f'+{spec}:{spec}', required=False)
    s.invalidate_reads()


def local_claims(root):
    """A private token must match the newest available claim tag, with no known release."""
    listing = s.git(root, 'for-each-ref', '--format=%(objectname) %(refname)', s.NAMESPACE).stdout
    refs = {ref: oid for oid, ref in (line.split() for line in listing.splitlines())}
    latest = {}
    stem = s.NAMESPACE + 'claims/'
    for ref, oid in refs.items():
        if not ref.startswith(stem):
            continue
        parts = ref[len(stem):].split('/')
        if len(parts) != 2 or not s.ID.fullmatch(parts[0]) or not parts[1].isdigit():
            continue
        tid, generation = parts
        if int(generation) > latest.get(tid, (0, ''))[0]:
            latest[tid] = int(generation), oid
    claims, owned = {}, []
    for tid, (generation, oid) in latest.items():
        try:
            value = json.loads(s.git(root, 'cat-file', 'blob', oid).stdout)
        except (s.Refused, ValueError):
            continue
        if not isinstance(value, dict) or not all(isinstance(value.get(key), str) for key in ('who', 'at')):
            continue
        claims[tid] = {**value, 'generation': generation, 'oid': oid,
                       'released': s.NAMESPACE + f'releases/{tid}/{oid}' in refs}
    folder = s.claim_token_path(root, 'TASK-1').parent
    for path in sorted(folder.glob('*')):
        tid = path.name
        if not s.ID.fullmatch(tid) or not path.is_file():
            continue
        held = claims.get(tid)
        if not held or held['released'] or not s.holds(held, path.read_text(encoding='utf-8')):
            continue
        owned.append(tid)
    return claims, owned


def completion_claims(root, names):
    """Keep a malformed task's DONE/CLOSED claim visible when its other fields cannot be read."""
    claimed = set()
    for tid in names:
        # A bad byte elsewhere in the task must not hide an otherwise readable finished claim.
        lines = s.read_bytes(root, f'tasks/{tid}.md').decode(
            'utf-8', errors='surrogateescape').removeprefix('\ufeff').splitlines()
        if not lines or lines[0] != '---' or '---' not in lines[1:]:
            continue
        for line in lines[1:lines.index('---', 1)]:
            key, separator, value = line.partition(':')
            if key == 'status' and separator:
                try:
                    status = s.front_value(tid, key, value)
                except s.Refused:
                    continue
                if isinstance(status, str) and status in gate.FINISHED:
                    claimed.add(tid)
    return claimed


def relevant_tasks(root):
    """Tasks held here, or whose task file differs from its locally available target branch."""
    _, owned = local_claims(root)
    names = gate.task_names(root)
    selected = set(owned) & set(names)
    for tid in names:
        if tid in selected:
            continue
        path = f'tasks/{tid}.md'
        current = s.read_bytes(root, path).decode('utf-8', errors='surrogateescape')
        current = current.removeprefix('\ufeff').replace('\r\n', '\n')
        lines = current.splitlines()
        try:
            record = s.front_matter(tid, lines[1:lines.index('---', 1)]) if lines[0] == '---' else {}
        except (s.Refused, ValueError, IndexError):
            record = {}
        override = record.get('merge_ref')
        if not isinstance(override, str) or not override.startswith(('refs/heads/', 'refs/remotes/')):
            record.pop('merge_ref', None)
        target = gate.merge_target(root, record)
        revision = gate.comparison_base(root, target[0])[0] if target else 'HEAD'
        before = s.metadata_texts(root, revision).get(path)
        if before != current:
            selected.add(tid)
    return selected


@s.read_session()
@s.test_reuse()
@s.test_output_session()
def run(root, event):
    if event == 'session-start':
        refresh_claim_refs(root)
        claims, owned = local_claims(root)
        print('Run workorder ready to see available work.')
        for tid in owned:
            held = claims[tid]
            print(f"{tid} held by {held['who']} since {held['at']}")
            print(f'\nworkorder show --task {tid}')
            try:
                print('\n'.join(gate.record_lines(root, tid)))
            except s.Refused as error:
                print(f'{tid}: {error}')
        return 0
    if event != 'stop':
        raise s.Refused(f'unknown hook event: {event}')
    try:
        payload = json.load(sys.stdin)
    except ValueError as error:
        raise s.Refused(f'Stop hook input must be JSON: {error}') from None
    if not isinstance(payload, dict):
        raise s.Refused('Stop hook input must be a JSON object')
    if payload.get('stop_hook_active') is True:
        return 0
    relevant = relevant_tasks(root)
    claimed = completion_claims(root, relevant)
    problems = []
    if claimed:
        for line in gate.check(root):
            owner = re.match(r'(?:NOT QUITE\s+)?([A-Z][A-Z0-9]*-[1-9][0-9]*)\b', line)
            if owner and owner[1] in claimed:
                problems.append(line)
    _, owned = local_claims(root)
    notes, results = [], {}
    for tid in sorted(relevant):
        try:
            record = s.record(root, tid)
        except s.Refused:
            continue  # readable completion claims already received their parse error above
        base = gate.local_test_base(root, record)
        if record['status'] == 'CANCELLED' and tid in owned:
            target = gate.merge_target(root, record)
            reference = base or (target[0] if target else None)
            revision = gate.comparison_base(root, reference)[0] if reference else 'HEAD'
            previous = gate.committed_status(root, tid, revision)
            if previous != 'CANCELLED':
                problems.append(gate.reviewer_label(
                    f'{tid}: status changed: {previous or "new task"} -> CANCELLED; '
                    f'a reviewer accepts closing a task without finishing it with --allow {tid}', tid))
        if tid in claimed and base:
            problems += gate.local_test_problems(root, base, record, notes=notes, results=results,
                                                 pending_review=True)
        else:
            revision = gate.comparison_base(root, base)[0] if base else 'HEAD'
            problems += gate.local_command_problems(root, revision, record)
    for note in gate.summarize_notes(notes):
        print(f'Note: {note}')
    if problems:
        print('\n'.join(line if line.startswith('NOT QUITE') else 'NOT QUITE  ' + line
                        for line in problems), file=sys.stderr)
        return 2
    return 0
