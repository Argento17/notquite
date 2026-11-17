"""Render diagnostics on the caller's console without losing their meaning.

One command run prints a reader's version: each distinct Note once, lists of
the same kind of finding as one line, and test failures as their short summary
plus the start of the first failure. The complete text goes to a file under the
Git directory; --verbose (WORKORDER_VERBOSE=1 for hooks) prints it all.
"""
import builtins
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
import re
import sys

NAMES = 5          # names shown in a grouped finding
BODY = 20          # lines of the first test failure
FAILED_LINES = 5   # pytest short-summary lines
OTHER_OUTPUT = 40  # lines of other long test output (its end is kept)
SHORT = 30         # pytest output this short is printed whole the first time
LOG = 'workorder/last-output.txt'

_RUN = ContextVar('workorder_output', default=None)


class _Run:
    def __init__(self, root, verbose, hint):
        self.root, self.verbose, self.hint = root, verbose, hint
        self.notes = set()
        self.body_shown = False
        self.log = []
        self.shortened = None  # the stream that received shortened text


def verbose():
    run = _RUN.get()
    return run is None or run.verbose


def _represent(text, stream):
    encoding = getattr(stream, 'encoding', None)
    if encoding:
        # Escapes preserve test IDs and paths that a legacy console cannot
        # represent. Do this before writing so a failure cannot print half a
        # diagnostic or turn successful evidence into a command failure.
        return text.encode(encoding, 'backslashreplace').decode(encoding)
    return text


def print(*values, sep=' ', end='\n', file=None, flush=False, full=None, dedupe=True):
    """Print like builtins.print; `full` is the complete text when `values` shorten it."""
    stream = sys.stdout if file is None else file
    text = (' ' if sep is None else str(sep)).join(str(value) for value in values) + (
        '\n' if end is None else str(end))
    run = _RUN.get()
    if run is not None:
        if dedupe and 'Note: ' in text:
            kept = []
            for line in text.splitlines(keepends=True):
                if line.startswith('Note: '):
                    key = line.rstrip('\r\n')
                    if key in run.notes:
                        continue
                    run.notes.add(key)
                kept.append(line)
            text = ''.join(kept)
            if not text:
                return
        run.log.append(text if full is None else full)
        if full is not None and full != text and run.shortened is None:
            run.shortened = stream
    builtins.print(_represent(text, stream), end='', file=stream, flush=flush)


def log_path(root):
    """The Git directory's copy of a run's full output; never inside the work tree."""
    marker = Path(root) / '.git'
    if marker.is_file():
        text = marker.read_text(encoding='utf-8', errors='replace').strip()
        if not text.startswith('gitdir:'):
            return None
        git_dir = Path(text.removeprefix('gitdir:').strip())
        git_dir = git_dir if git_dir.is_absolute() else Path(root) / git_dir
    elif marker.is_dir():
        git_dir = marker
    else:
        return None
    return git_dir / LOG


@contextmanager
def session(root=None, *, verbose=False, hint='rerun with --verbose to print it all'):
    """One command run; nested runs share the outer one."""
    if _RUN.get() is not None:
        yield _RUN.get()
        return
    run = _Run(root, verbose or os.environ.get('WORKORDER_VERBOSE', '') not in {'', '0'}, hint)
    token = _RUN.set(run)
    try:
        yield run
    finally:
        _RUN.reset(token)
        if run.shortened is not None:
            _save(run)
        else:
            _discard(run)


def _discard(run):
    """A run that shortened nothing leaves no older run's file to be mistaken for its own."""
    path = log_path(run.root) if run.root is not None else None
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _save(run):
    path = log_path(run.root) if run.root is not None else None
    stream = run.shortened
    if path is None:
        builtins.print(_represent(f'Output shortened; {run.hint}', stream), file=stream)
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(run.log), encoding='utf-8', errors='backslashreplace')
    except OSError as error:
        builtins.print(_represent(f'Output shortened; could not save it all ({error}); {run.hint}', stream),
                       file=stream)
        return
    try:
        shown = path.resolve().relative_to(Path(run.root).resolve()).as_posix()
    except ValueError:
        shown = str(path)
    builtins.print(_represent(f'Full output: {shown} ({run.hint})', stream), file=stream)


# Findings about one named test each. Each pattern names the varying test
# (name), the kind shared by a group, and the advice that follows; any text
# before it (a "NOT DONE" or "Pending:" lead) is kept.
_KINDS = (
    (re.compile(r'(?P<lead>.*?)the project test fails on this code: (?P<name>.+?) '
                r'\(passes on (?P<base>[^()]*)\)(?P<rest>.*)', re.S),
     lambda m, n: f'{n} project tests fail on this code that pass on {m["base"]}'),
    (re.compile(r'(?P<lead>.*?)the project test fails on this code: (?P<name>.+?); could not compare this '
                r'failing test with (?P<base>.+?) \((?P<exit>exit [^)]*)\)(?P<rest>.*)', re.S),
     lambda m, n: f'{n} project tests fail on this code; could not compare them with {m["base"]} ({m["exit"]})'),
    (re.compile(r'(?P<lead>.*?)the project test also fails on (?P<base>.+?): (?P<name>.+?)(?P<rest>)', re.S),
     lambda m, n: f'{n} project tests also fail on {m["base"]}'),
)


def _kind(message):
    for index, (pattern, _) in enumerate(_KINDS):
        match = pattern.fullmatch(message)
        if match:
            return index, match
    return None, None


def finding_name(message):
    """The test a per-test finding names, or None."""
    return _kind(message)[1]['name'] if _kind(message)[1] else None


def group(messages):
    """One line per kind of per-test finding, with a count and the first names.

    Other messages keep their order and text. With --verbose every message is kept.
    """
    messages = list(messages)
    if verbose():
        return messages
    keyed, groups = [], {}
    for message in messages:
        index, match = _kind(message)
        if match is None:
            keyed.append((None, message))
            continue
        key = (index, *(value for name, value in match.groupdict().items() if name != 'name'))
        groups.setdefault(key, []).append(match)
        keyed.append((key, None))
    result, done = [], set()
    for key, message in keyed:
        if key is None:
            result.append(message)
            continue
        if key in done:
            continue
        done.add(key)
        matches = groups[key]
        names = list(dict.fromkeys(match['name'] for match in matches))
        if len(names) == 1:
            result.append(matches[0].string)
            continue
        shown = ', '.join(names[:NAMES]) + (f', ... (+{len(names) - NAMES} more)' if len(names) > NAMES else '')
        rest = matches[0]['rest'].replace('accepts it with', 'accepts them with', 1)
        result.append(f'{matches[0]["lead"]}{_KINDS[key[0]][1](matches[0], len(names))}: {shown}{rest}')
    return result


def print_lines(lines, *, prefix='', file=None):
    """Print grouped messages, keeping every message in the full output."""
    lines = list(lines)
    shown = group(lines)
    if not shown:
        return
    print('\n'.join(prefix + line for line in shown), file=file,
          full='\n'.join(prefix + line for line in lines) + '\n')


_HEADER = re.compile(r'=+ (?:FAILURES|ERRORS) =+')
_SUMMARY_HEADER = re.compile(r'=+ short test summary info =+')
_SECTION = re.compile(r'_{3,} .* _{3,}|_{3,} \S.*')
_RESULT = re.compile(r'=*\s*(?:\d+ (?:failed|passed|errors?|skipped|deselected|xfailed|xpassed|warnings?)'
                     r'(?:, )?)+.* in [\d.]+s.*')


def _failed_lines(lines):
    failed = [line for line in lines if line.startswith(('FAILED ', 'ERROR '))]
    shown = failed[:FAILED_LINES]
    if len(failed) > FAILED_LINES:
        shown.append(f'... (+{len(failed) - FAILED_LINES} more failed tests in the full output)')
    return shown


def short_failures(short_summary, sections, summary, heading=None):
    """A pytest report as its FAILED lines and the start of its first failure, once per run."""
    run = _RUN.get()
    out = []
    if sections and run is not None and not run.body_shown:
        body = sections[0].splitlines()
        out += body[:BODY]
        if len(body) > BODY:
            out.append(f'[first failure: {BODY} of {len(body)} lines shown]')
    if sections and run is not None:
        run.body_shown = True
    failed = _failed_lines(short_summary.splitlines())
    out += ([heading] if heading and failed else []) + failed
    if summary:
        out.append(summary)
    return '\n'.join(out) + '\n' if out else ''


def _short_output(output):
    lines = output.splitlines()
    header = next((index for index, line in enumerate(lines) if _HEADER.fullmatch(line.strip())), None)
    summary_at = next((index for index, line in enumerate(lines) if _SUMMARY_HEADER.fullmatch(line.strip())), None)
    if header is None and summary_at is None:
        if len(lines) <= OTHER_OUTPUT:
            return output
        return (f'[{len(lines) - OTHER_OUTPUT} earlier lines in the full output]\n'
                + '\n'.join(lines[-OTHER_OUTPUT:]) + '\n')
    run = _RUN.get()
    if len(lines) <= SHORT and run is not None and not run.body_shown:
        run.body_shown = header is not None
        return output
    start = header if header is not None else summary_at
    before = lines[:start]
    out = (before if len(before) <= 3 else []) + [lines[start]]
    end = summary_at if summary_at is not None else len(lines)
    if summary_at is None:
        # Without -r, pytest's result line ends the last failure section.
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        if end > start + 1 and _RESULT.fullmatch(lines[end - 1].strip()):
            end -= 1
    sections, current = [], None
    for line in lines[start + 1 if header is not None else end:end]:
        if _SECTION.fullmatch(line):
            current = [line]
            sections.append(current)
        elif current is not None:
            current.append(line)
        else:
            current = [line]
            sections.append(current)
    tail = lines[summary_at + 1:] if summary_at is not None else lines[end:]
    result = [line for line in tail if _RESULT.fullmatch(line.strip())]
    short = short_failures('\n'.join(tail), ['\n'.join(section) for section in sections],
                           result[-1] if result else '',
                           heading=lines[summary_at] if summary_at is not None and header is not None else None)
    return '\n'.join(out) + ('\n' if out else '') + short


def test_output(output, errors=''):
    """A test command's output, shortened unless --verbose; the full text is kept."""
    run = _RUN.get()
    if run is None or run.verbose:
        print(output, end='', dedupe=False)
        print(errors, end='', file=sys.stderr, dedupe=False)
        return
    print(_short_output(output) if output else '', end='', full=output, dedupe=False)
    if errors:
        lines = errors.splitlines()
        short = errors if len(lines) <= BODY else (
            f'[{len(lines) - BODY} earlier lines in the full output]\n' + '\n'.join(lines[-BODY:]) + '\n')
        print(short, end='', file=sys.stderr, full=errors, dedupe=False)
