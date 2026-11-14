"""Claude Code hook commands: local task context and completion checks, without fetching."""

import json
import re
import sys

from . import gate
from . import store as s


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


def completion_claims(root):
    """Keep a malformed task's DONE/CLOSED claim visible when its other fields cannot be read."""
    claimed = set()
    for tid in gate.task_names(root):
        lines = s.read_text(root, f'tasks/{tid}.md').splitlines()
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


def run(root, event):
    if event == 'session-start':
        claims, owned = local_claims(root)
        print('workorder ready (local view; no fetch)')
        print('\n'.join(gate.ready(root, local_claims=claims)) or 'no open work')
        for tid in owned:
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
    claimed = completion_claims(root)
    problems = []
    for line in gate.check(root):
        owner = re.match(r'(?:NOT QUITE\s+)?([A-Z][A-Z0-9]*-[1-9][0-9]*)\b', line)
        if owner and owner[1] in claimed:
            problems.append(line)
    if problems:
        print('\n'.join(line if line.startswith('NOT QUITE') else 'NOT QUITE  ' + line
                        for line in problems), file=sys.stderr)
        return 2
    return 0
