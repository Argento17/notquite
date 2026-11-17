"""Local files and immutable remote reservations. No hosting-service API needed."""

from __future__ import annotations

from .console import print

import datetime as dt
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from functools import partial, wraps
import hashlib
import hmac
import heapq
import json
import math
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from .git_reads import ReadCache, Batch, uses_diff_helper


class Refused(ValueError):
    pass


_TEST_OUTPUTS = ContextVar("workorder_test_outputs", default=None)


@contextmanager
def test_output_session():
    if _TEST_OUTPUTS.get() is not None:
        yield
        return
    token = _TEST_OUTPUTS.set(set())
    try:
        yield
    finally:
        _TEST_OUTPUTS.reset(token)


def print_once(message):
    seen = _TEST_OUTPUTS.get()
    if seen is None or message not in seen:
        print(message)
        if seen is not None:
            seen.add(message)


def print_test_output(output, errors='', failed=False, label=None):
    # The same output can be used for evidence, current, REF and all-task checks.
    key = ('test-output', failed, re.sub(r' in \d+(?:\.\d+)?s', ' in <time>s', output + errors))
    seen = _TEST_OUTPUTS.get()
    if seen is not None:
        if key in seen:
            if label:
                print_once(label + ' (same output as above)')
            return
        seen.add(key)
    if label:
        print(label)
    elif failed:
        print('Failure:')
    print(output, end='')
    print(errors, end='', file=sys.stderr)


_REUSE_TESTS = ContextVar("workorder_reuse_tests", default=False)


@contextmanager
def test_reuse(enabled=True):
    token = _REUSE_TESTS.set(enabled)
    try:
        yield
    finally:
        _REUSE_TESTS.reset(token)


_READS = ContextVar("workorder_reads", default=None)


@contextmanager
def read_session(*, seed=None):
    """Share reads within one command, never across commands in an embedding process."""
    if _READS.get() is not None:
        yield
        return
    cache = seed if seed is not None else ReadCache()
    token = _READS.set(cache)
    try:
        yield
    finally:
        try:
            cache.close()
        finally:
            _READS.reset(token)


@contextmanager
def temporary_checkout(*, prefix):
    """Release cached Git processes before removing temporary checkouts.

    A command's read session can outlive many comparison directories. Windows
    cannot remove a process's cwd, so reap every batch beneath this directory
    even when snapshot preparation or test execution raises or times out.
    Keep the source repository's batches available to the rest of the command.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        try:
            yield temporary
        finally:
            cache = _READS.get()
            if cache is not None:
                cache.close(root=temporary)


def invalidate_reads(*, metadata_only=False):
    cache = _READS.get()
    if cache is not None:
        # Virtual receipts and other derived values always need rebuilding.
        # Git queries can survive an external command that changed no observed
        # input: checking validates state before reuse, even in a nested read
        # when this generation changes. Unobservable helpers never reuse reads.
        cache.clear(queries=False)


@contextmanager
def changing_repository():
    """Commands may mutate even when they fail or cannot be started."""
    invalidate_reads()
    try:
        yield
    finally:
        invalidate_reads()


def _read_key(value):
    """Key compound read inputs by value, including a task's nested fields."""
    if isinstance(value, dict):
        return (dict, frozenset((key, _read_key(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return (type(value), tuple(_read_key(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return (type(value), frozenset(_read_key(item) for item in value))
    return value


@contextmanager
def read_scope(root):
    """Validate one compound read without caching its caller's side effects.

    Read-only preparation can share one observation. A scope spanning test
    execution or writes must use the mutation guards: nested reads revalidate
    whenever invalidate_reads changes the generation.
    """
    cache = _READS.get()
    if cache is None or os.environ.get('WORKORDER_GIT_CACHE') == '0':
        yield
    else:
        with cache.checking(root):
            yield


def cached_read(function, *, git_query=False, diff_query=False, refresh_after=False):
    @wraps(function)
    def read(*args, **kwargs):
        cache = _READS.get()
        if cache is None or os.environ.get('WORKORDER_GIT_CACHE') == '0':
            return function(*args, **kwargs)
        with cache.checking(args[0], isolated=kwargs.get('isolated', False)) as reusable:
            if not reusable:
                return function(*args, **kwargs)
            state = cache.states[(args[0], kwargs.get('isolated', False))]
            if diff_query and args[1] in {'diff', 'status'} and (
                    state[3].is_file() or os.name == 'nt' and state[3].with_suffix('.exe').is_file()):
                # Index refresh can run external code even with no diff driver.
                # Bypass the helper probe too: its diff can execute this hook.
                with changing_repository():
                    return function(*args, **kwargs)
            if diff_query and uses_diff_helper(args[0], args[1:],
                    state[2],
                    isolated=kwargs.get('isolated', False)):
                # A derived read enclosing this query must not retain helper
                # output either. Ordinary plumbing reads remain reusable.
                cache.clear(queries=False)
                return function(*args, **kwargs)
            entries = cache.queries if git_query else cache
            key = (function, tuple(_read_key(arg) for arg in args),
                   tuple((key, _read_key(value)) for key, value in sorted(kwargs.items())))
            if key not in entries:
                try:
                    generation = cache.generation
                    result = function(*args, **kwargs)
                    if generation == cache.generation:
                        entries[key] = result if git_query else deepcopy(result)
                    return result
                finally:
                    if refresh_after:
                        # write-tree may write objects and refresh its copy.
                        # Keep the query under its PRE-command state, then force
                        # a full observation before any later reuse. A changed
                        # state evicts it; unchanged inputs can reuse the result.
                        cache.clear(queries=False)
            # A caller may annotate a receipt or task. Reuse must have the same
            # value semantics as a fresh read, including nested mutable fields.
            return entries[key] if git_query else deepcopy(entries[key])
    return read


ID = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*\Z")
KINDS = {"implemented", "test", "merged", "deployed", "live"}
NAMESPACE = "refs/tags/workorder/"


def task_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Refused("expected an id such as TASK-1")
    return value


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def git(root, *args, data=None, required=True):
    return _git_run(root, *args, data=data, required=required)


def _git_run(root, *args, data=None, required=True, isolated=False):
    readonly = (args[0] in {"rev-parse", "show", "log", "ls-tree", "ls-files", "diff",
                           "merge-base", "rev-list", "patch-id", "check-ref-format",
                           "cat-file", "for-each-ref", "status"}
                or args[0] == "symbolic-ref" and len([a for a in args[1:] if not a.startswith('-')]) == 1
                or args[:2] == ("remote", "get-url")
                or args[0] == "config" and (args[1:2] in {("--get",), ("--get-all",), ("--list",)}
                                            or len(args) == 2 and not args[1].startswith('-')))
    if readonly:
        isolated = isolated and copy_environment() != dict(os.environ)
        result = _git_read(root, *args, data=data, isolated=isolated)
        if required and result.returncode:
            raise Refused(result.stderr.strip() or "Git command failed")
        return result
    with changing_repository():
        return _git_execute(root, *args, data=data, required=required, isolated=isolated)


@partial(cached_read, git_query=True, diff_query=True)
def _git_read(root, *args, data=None, isolated=False):
    if (not isolated and _READS.get() is not None
            and args == ('rev-parse', '--verify', 'HEAD^{commit}')):
        item = _batch_object(root, 'HEAD^{commit}')
        if item is not None:
            return subprocess.CompletedProcess(['git', *args], 0, item[0] + '\n', '')
    if not isolated and len(args) == 2 and args[0] == 'show' and ':' in args[1]:
        raw = blob_bytes(root, args[1])
        if raw is not None:
            return subprocess.CompletedProcess(['git', *args], 0,
                raw.decode('utf-8', 'surrogateescape').replace('\r\n', '\n').replace('\r', '\n'), '')
    return _git_execute(root, *args, data=data, required=False, isolated=isolated)


def copy_environment():
    """A temporary repository must not inherit Git's pointers into the caller's repository."""
    local = {'GIT_INDEX_FILE', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_PREFIX', 'GIT_COMMON_DIR',
             'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_IMPLICIT_WORK_TREE',
             'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE', 'GIT_REPLACE_REF_BASE', 'GIT_NAMESPACE'}
    return {key: value for key, value in os.environ.items() if key not in local}


def _git_execute(root, *args, data=None, required=True, isolated=False):
    result = subprocess.run(
        ["git", *args], cwd=root, input=data, capture_output=True,
        encoding="utf-8", errors="surrogateescape", timeout=30,
        env=copy_environment() if isolated else None,
    )
    if required and result.returncode:
        raise Refused(result.stderr.strip() or "Git command failed")
    return result


def who(root):
    # An agent can distinguish itself from others sharing the same Git identity.
    name = os.environ.get("WORKORDER_AGENT", "").strip()
    name = name or git(root, "config", "user.name", required=False).stdout.strip()
    if not name:
        raise Refused("set git user.name first")
    return name


@cached_read
def head(root):
    return git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()


def repository(root):
    return Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def local_path(root, relative):
    if not isinstance(relative, str) or not relative:
        raise Refused("expected a relative file path")
    result = root / relative
    if Path(relative).is_absolute() or not result.resolve().is_relative_to(root.resolve()):
        raise Refused("file path must stay inside this repository")
    return result


def read_bytes(root, relative, staged=False):
    local_path(root, relative)
    if staged:
        content = blob_bytes(root, f":{relative}")
        if content is None:
            raise Refused(f"missing staged file: {relative}")
        return content
    return local_path(root, relative).read_bytes()


def read_text(root, relative, staged=False):
    # Git on Windows may check text files out with CRLF; the content is the same.
    if staged and (receipt_task(relative) or relative.startswith("tasks/")):
        if metadata_scope(relative) is None:
            text = metadata_texts(root, None, None, True).get(relative)
        else:
            # Read the index without resolving a possibly different worktree
            # symlink, exactly as the shared metadata inventory does.
            raw = blob_bytes(root, f':{relative}')
            text = (raw.decode('utf-8', errors='surrogateescape').removeprefix('\ufeff').replace('\r\n', '\n')
                    if raw is not None else None)
        if text is None:
            raise Refused(f"missing staged file: {relative}")
        return checked_text(text, relative)
    text = read_bytes(root, relative, staged).decode("utf-8", errors="surrogateescape")
    return checked_text(text, relative).removeprefix("\ufeff").replace("\r\n", "\n")


def checked_text(text, relative):
    """Reject undecodable bytes only when their owning file is consumed."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused(f"{relative}: cannot be read as UTF-8") from None
    return text


@cached_read
def files(root, directory, staged=False):
    if staged:
        if directory == "tasks":
            return sorted(p for p in git(root, "ls-files", "-z", "--", "tasks").stdout.split("\0") if p)
        paths = git(root, "ls-files", "-z", "--", directory).stdout.split("\0")
        return sorted(p for p in paths if p)
    base = local_path(root, directory)
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())


# CANCELLED ends a task that will not be done: it needs no evidence and never counts as finished.
# RETIRED ends a finished task's protection (workorder retire): its record, notes and receipts
# stay, but it owns no test files and its test no longer reruns.
STATUSES = ("IN_PROGRESS", "BLOCKED", "DONE", "CLOSED", "CANCELLED", "RETIRED")
CORE_FIELDS = ("id", "title", "owner", "status", "ships", "waits_on", "contract")
# Optional fields. Dependencies and the test add requirements; merge_ref selects delivery's target,
# and test_paths selects files guarded by a base check. Planning fields never stand in for evidence.
EXTENDED_FIELDS = ("business_owner", "priority", "created_at", "mission", "depends_on", "blocks",
                   "next", "summary", "test", "test_paths", "code_paths", "test_timeout", "merge_ref", "kind")
EXTENDED_ORDER = ("id", "title", "owner", "business_owner", "priority", "created_at", "mission",
                  "depends_on", "blocks", "next", "summary", "status", "ships", "waits_on", "test",
                  "contract")
TASK_REF = r"[A-Z][A-Z0-9]*-[1-9][0-9]*"
FRONT_MATTER_HELP = ("front matter takes `key: value` lines: plain single-line text, a JSON string, or a"
                     ' list such as ["TASK-1"]; mission, next and summary may use > or | blocks, and'
                     " depends_on, blocks, waits_on, test_paths and code_paths may use indented - item lines")
# The small YAML subset Workorder reads. Anything else is refused rather than guessed.
BLOCK_TEXT_FIELDS = ("mission", "next", "summary")
LIST_FIELDS = ("depends_on", "blocks", "waits_on", "test_paths", "code_paths")
BLOCK_INDICATORS = (">", "|", ">-", "|-", ">+", "|+")
PLAIN_ITEM = r"[A-Za-z0-9_./@:-]+"


def front_value(tid, key, raw):
    """Parse one value: plain text, a JSON string or list, or a simple [a, b] list."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")  # a single-quoted YAML scalar
    if not raw.startswith(('"', '[')):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        pass
    inner = raw[1:-1].strip() if raw.startswith("[") and raw.endswith("]") else None
    raw_items = [item.strip() for item in inner.split(",")] if inner else []
    quoted = [len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'" for item in raw_items]
    items = [item[1:-1] if q else item for item, q in zip(raw_items, quoted)]
    if inner is None or any(not (item if q else re.fullmatch(PLAIN_ITEM, item))
                            for item, q in zip(items, quoted)):
        raise Refused(f"{tid}: cannot read {key}; {FRONT_MATTER_HELP}")
    return items


def block_text(tid, key, indicator, block):
    """A YAML > (folded) or | (literal) block, with optional - (strip) or + (keep) chomping."""
    filled = [line for line in block if line.strip()]
    if not filled:
        return ""
    indent = len(filled[0]) - len(filled[0].lstrip(" "))
    if any(len(line) - len(line.lstrip(" ")) < indent for line in filled):
        raise Refused(f"{tid}: {key}: block lines must keep the indentation of the first line")
    content = [line[indent:] if line.strip() else "" for line in block]
    trailing = len(content) - len("\n".join(content).rstrip("\n").split("\n"))
    content = content[:len(content) - trailing]
    if indicator[0] == "|":
        text = "\n".join(content)
    else:
        # Folding joins two ordinary lines with a space; empty lines become line breaks. Lines
        # indented further than the block keep their own line breaks, as in YAML.
        text, previous, empty = "", None, 0
        for line in content:
            if not line:
                empty += 1
                continue
            if previous is None:
                text += "\n" * empty
            elif previous.startswith(" ") or line.startswith(" "):
                text += "\n" * (empty + 1)
            else:
                text += "\n" * empty if empty else " "
            text += line
            previous, empty = line, 0
    if indicator.endswith("-"):
        return text
    if indicator.endswith("+"):
        return text + "\n" * (1 + trailing)
    return text + "\n"


def block_list(tid, key, block):
    """Indented "- item" lines: plain ids or JSON strings, nothing nested."""
    items = []
    for line in block:
        if not line.strip():
            continue
        match = re.fullmatch(r" *- +(.+?)\s*", line)
        item = match.group(1) if match else None
        if item and item.startswith('"'):
            try:
                item = json.loads(item)
            except ValueError:
                item = None
        if not isinstance(item, str) or not item or (key not in {"test_paths", "code_paths"} and not re.fullmatch(PLAIN_ITEM, item)):
            raise Refused(f"{tid}: {key}: list lines look like \"  - TASK-1\"")
        items.append(item)
    return items


def front_matter(tid, lines):
    """Fields from front matter lines: key: value, plus the block forms above. Line 1 is ---."""
    fields, index = {}, 0
    while index < len(lines):
        line, number = lines[index], index + 2
        end = index + 1
        # A list field may also carry its "- item" lines at the left margin, as YAML allows.
        list_head = line.partition(":")[0] in LIST_FIELDS and not line.partition(":")[2].strip()
        while end < len(lines) and (not lines[end].strip() or lines[end].startswith(" ")
                                    or (list_head and lines[end].startswith("- "))):
            end += 1
        block = lines[index + 1:end]
        index = end
        if not line.strip():
            continue
        if line.startswith("#") and not any(item.strip() for item in block):
            continue  # a YAML comment line
        key, separator, raw = line.partition(":")
        if not separator or not key or key != key.strip() or line.startswith("#"):
            raise Refused(f"{tid}: line {number}: {FRONT_MATTER_HELP}")
        if key in fields:
            raise Refused(f"{tid}: duplicate field {key}")
        raw = raw.strip()
        nested = any(item.strip() for item in block)
        if raw in BLOCK_INDICATORS:
            if key not in BLOCK_TEXT_FIELDS:
                raise Refused(f"{tid}: {key} cannot be a > or | block; only {', '.join(BLOCK_TEXT_FIELDS)} can")
            fields[key] = block_text(tid, key, raw, block)
        elif not raw and key in LIST_FIELDS:
            fields[key] = block_list(tid, key, block)
        elif nested:
            raise Refused(f"{tid}: line {number + 1}: indented lines belong only under a > or | block or a"
                          f" list field; {FRONT_MATTER_HELP}")
        else:
            fields[key] = front_value(tid, key, raw)
    return fields


def task_refs(tid, key, value):
    """A list of task ids, written as TASK-1 or task:TASK-1, returned as plain ids."""
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise Refused(f"{tid}: {key} must be a list of task ids")
    ids = [v[5:] if v.startswith("task:") else v for v in value]
    if any(not re.fullmatch(TASK_REF, i) for i in ids):
        raise Refused(f"{tid}: {key} lists task ids such as TASK-2; people waits go in waits_on")
    if tid in ids:
        raise Refused(f"{tid}: {key} cannot name the task itself")
    return list(dict.fromkeys(ids))


@cached_read
def record(root, tid, staged=False):
    task_id(tid)
    if not staged and not local_path(root, f"tasks/{tid}.md").is_file():
        raise Refused(f"no task record tasks/{tid}.md in this checkout; pull the commit that added it")
    text = read_text(root, f"tasks/{tid}.md", staged)
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise Refused(f"{tid}: expected fenced front matter")
    fields = front_matter(tid, lines[1:lines.index("---", 1)])
    missing = [k for k in CORE_FIELDS if k not in fields]
    unknown = sorted(k for k in set(fields) - set(CORE_FIELDS) - set(EXTENDED_FIELDS) if not k.startswith("x-"))
    if missing or unknown:
        raise Refused(f"{tid}: " + "; ".join(
            ([f"missing {', '.join(missing)}"] if missing else [])
            + ([f"unknown {', '.join(unknown)} (known optional fields: {', '.join(EXTENDED_FIELDS)};"
                 " your own fields start with x-, such as x-issue)"]
               if unknown else [])))
    if fields["id"] != tid:
        raise Refused(f"{tid}: id must match the file name")
    if any(not isinstance(fields[k], str) or not fields[k].strip()
           for k in set(CORE_FIELDS) - {"waits_on"}):
        raise Refused(f"{tid}: text fields must be nonempty strings")
    if fields["status"] not in STATUSES:
        raise Refused(f"{tid}: unknown status {fields['status']!r}; use {', '.join(STATUSES)}")
    if fields["ships"] not in {"none", "repo", "live"}:
        raise Refused(f"{tid}: ships must be none, repo or live")
    if "kind" in fields and fields["kind"] not in ("refactor", "verification"):
        raise Refused(f"{tid}: kind must be refactor or verification")
    if "merge_ref" in fields:
        ref = fields["merge_ref"]
        if (not isinstance(ref, str) or not ref.startswith(("refs/heads/", "refs/remotes/"))
                or (ref.startswith("refs/remotes/") and len(ref.split("/")) < 4)
                or git(root, "check-ref-format", ref, required=False).returncode):
            raise Refused(f"{tid}: merge_ref must be a full branch ref")
    waits = fields["waits_on"]
    if not isinstance(waits, list) or any(
        not isinstance(w, str) or not re.fullmatch(rf"(?:task:{TASK_REF}|person:[^:\s][^:]*)", w)
        or w != w.strip()
        for w in waits
    ):
        raise Refused(f"{tid}: waits_on needs task:<id> or person:<name> items")
    for key in ("business_owner", "priority", "created_at", "mission", "next", "summary", "test"):
        if key in fields and not isinstance(fields[key], str):
            raise Refused(f"{tid}: {key} must be text")
    if fields.get("test", "").strip():
        split_command(fields["test"], f"{tid}: test")
    if "test_timeout" in fields:
        fields["test_timeout"] = positive_seconds(fields["test_timeout"], f"{tid}: test_timeout")
    for key in ("test_paths", "code_paths"):
        if key not in fields:
            continue
        paths = fields[key]
        if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() for p in paths):
            raise Refused(f"{tid}: {key} must be a list of repository paths")
        for path in paths:
            local_path(root, path)
    if fields.get("created_at"):
        try:
            dt.datetime.fromisoformat(fields["created_at"].replace("Z", "+00:00"))
        except ValueError:
            raise Refused(f"{tid}: created_at must be an ISO date such as 2026-11-14") from None
    for key in ("depends_on", "blocks"):
        if key in fields:
            fields[key] = task_refs(tid, key, fields[key])
    needs = {w[5:] for w in waits if w.startswith("task:")} | set(fields.get("depends_on", []))
    both = sorted(needs & set(fields.get("blocks", [])))
    if both:
        raise Refused(f"{tid}: cannot both wait for and block {', '.join(both)}")
    return fields


def dependencies(record):
    """One dependency list: waits_on items plus depends_on ids as task: items, without repeats."""
    return list(dict.fromkeys([*record["waits_on"], *(f"task:{i}" for i in record.get("depends_on", []))]))


def has_origin(root):
    return git(root, "remote", "get-url", "origin", required=False).returncode == 0


def remote_refs(root, upstream=False):
    if not has_origin(root):
        raise Refused("this needs an origin remote: only a shared remote can refuse a second"
                      " holder, so claims are never kept locally")
    branches = ["refs/heads/main", "refs/heads/master"] if upstream else []
    output = git(root, "ls-remote", "--refs", "origin", NAMESPACE + "*", *branches).stdout
    return {ref: oid for oid, ref in (line.split() for line in output.splitlines())}


def reserve(root, ref, value):
    value = {**value, "nonce": uuid.uuid4().hex}
    oid = git(root, "hash-object", "-w", "--stdin", data=packed(value)).stdout.strip()
    # Tags reject replacement even if one object descends from another. No force option.
    result = git(root, "push", "origin", f"{oid}:{ref}", required=False)
    if result.returncode:
        if ref in remote_refs(root):
            return None
        raise Refused(result.stderr.strip() or "remote reservation failed")
    return oid


def remote_value(root, ref, oid):
    git(root, "fetch", "--no-tags", "origin", ref)
    return json.loads(git(root, "cat-file", "blob", oid).stdout)


EXTENDED_BODY = """
## Context

<!-- Why this task exists and what it touches. -->

## Scope

<!-- What is in scope, and what is explicitly out. -->

## Lanes

<!-- Parallel streams of work and who or what owns each. -->

## Acceptance

<!-- The original ask, criterion by criterion. Evidence Workorder should check belongs in a contract. -->

## Progress

<!-- Dated notes: workorder note adds one, workorder show prints them. The status line is your claim; workorder show and workorder check compute what the receipts support. workorder done sets DONE only when its check supports the claim. -->
"""


# An Acceptance item may end with "(test: path::name)", a pytest node id that done checks.
ACCEPTANCE_TEST = re.compile(r'\(test:\s*([^()\s]*)\s*\)\s*$')


def acceptance_node_problem(node):
    """A tied test must be a pytest node id inside the repository: tests/test_x.py::test_name."""
    path, separator, name = node.partition('::')
    parts = path.replace('\\', '/').split('/')
    if (not separator or not name or not path.endswith('.py') or path.startswith('/')
            or re.match(r'^[A-Za-z]:', path) or '..' in parts or not all(part for part in name.split('::'))):
        return (f'(test: {node}) is not a pytest node id in this repository, '
                'such as tests/test_utils.py::test_trailing_slash')
    return None


def acceptance_items(text):
    """The Acceptance section's items, each with its tied pytest node id or None."""
    items = []
    for heading, body in task_body_sections(text):
        if heading != 'Acceptance':
            continue
        body = re.sub(r'<!--.*?-->', '', '\n'.join(body), flags=re.S)
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            match = ACCEPTANCE_TEST.search(line)
            items.append((line, match[1] if match else None))
    return items


def acceptance_lines(items):
    """Turn --acceptance texts into list items, checking any tied node ids."""
    lines = []
    for item in items:
        item = ' '.join(item.split())
        if not item:
            raise Refused('an acceptance item needs some text')
        match = ACCEPTANCE_TEST.search(item)
        if match and acceptance_node_problem(match[1]):
            raise Refused(acceptance_node_problem(match[1]))
        lines.append(item if item.startswith(('- ', '* ')) else '- ' + item)
    return lines


def task_text(tid, data, template, acceptance=()):
    if template == "simple":
        return ("---\n" + "".join(f"{k}: {packed(v)}\n" for k, v in data.items())
                + f"---\n\n# {tid}\n\nWrite the original ask here.\n"
                + ("\n## Acceptance\n\n" + "".join(line + "\n" for line in acceptance) if acceptance else ""))
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    extended = {**dict(business_owner="", priority="", created_at=today, mission="", depends_on=[],
                       blocks=[], next="", summary="", test=""), **data}
    order = list(EXTENDED_ORDER)
    if extended.get("test_paths"):
        order.insert(order.index("test") + 1, "test_paths")
    if 'code_paths' in extended:
        order.insert(order.index('contract'), 'code_paths')
    body = EXTENDED_BODY
    if acceptance:
        marker = "Evidence Workorder should check belongs in a contract. -->\n"
        body = body.replace(marker, marker + "\n" + "".join(line + "\n" for line in acceptance), 1)
    return ("---\n" + "".join(f"{k}: {packed(extended[k])}\n" for k in order)
            + f"---\n\n# {tid}\n" + body)


def new(root, title, owner, ships="none", prefix="TASK", template="extended", test="", local_only=False,
        test_paths=None, code_paths=None, acceptance=()):
    repository(root)
    acceptance = acceptance_lines(acceptance or ())
    if template not in {"simple", "extended"}:
        raise Refused("template must be simple or extended")
    if not re.fullmatch(r"[A-Z][A-Z0-9]*", prefix) or not title.strip() or not owner.strip():
        raise Refused("use an uppercase prefix, nonempty title and owner")
    if test.strip():
        split_command(test, "--test")
    for path in code_paths or []:
        local_path(root, path)
    directory = local_path(root, "tasks")
    directory.mkdir(exist_ok=True)
    # Without an origin remote, ids are numbered locally and not coordinated with other clones.
    # With one, it must answer: never guess offline.
    local = local_only or not has_origin(root)
    for _ in range(30):
        try:
            refs = (git(root, 'for-each-ref', '--format=%(refname)',
                        NAMESPACE + 'ids/').stdout.splitlines() if local else remote_refs(root))
        except Refused as error:
            raise Refused(f"origin could not be reached ({error}); workorder new --local numbers the task"
                          " from the task files and locally known reservations without it") from None
        stem = NAMESPACE + f"ids/{prefix}-"
        numbers = [int(ref[len(stem):]) for ref in refs
                   if ref.startswith(stem) and ref[len(stem):].isdigit()]
        numbers += [int(p.stem.split("-")[-1]) for p in directory.glob(f"{prefix}-*.md")
                    if ID.fullmatch(p.stem)]
        tid = f"{prefix}-{max(numbers, default=0) + 1}"
        data = dict(id=tid, title=title, owner=owner, status="IN_PROGRESS", ships=ships,
                    waits_on=[], contract="none", **({"test": test} if test else {}),
                    **({"test_paths": list(test_paths)} if test_paths else {}),
                    **({'code_paths': list(code_paths)} if code_paths is not None else {}))
        if local or reserve(root, NAMESPACE + f"ids/{tid}", {**data, "at": now(), "who": who(root)}):
            path = directory / f"{tid}.md"
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(task_text(tid, data, template, acceptance))
            return tid
    raise Refused("too much allocation contention; retry")


def active_claims(root, upstream=None, snapshot=None):
    refs = remote_refs(root, upstream=upstream is not None)
    latest = {}
    stem = NAMESPACE + "claims/"
    for ref, oid in refs.items():
        if ref.startswith(stem):
            parts = ref[len(stem):].split("/")
            if len(parts) != 2 or not ID.fullmatch(parts[0]) or not parts[1].isdigit():
                continue  # not a claim this tool wrote; ignore it rather than fail for everyone
            tid, generation = parts
            if int(generation) > latest.get(tid, (0, "", ""))[0]:
                latest[tid] = (int(generation), ref, oid)
    result = {}
    # One fetch for every claim that matters, instead of one network round trip per task.
    wanted = [ref for _, ref, oid in latest.values()
              if git(root, "cat-file", "-e", oid, required=False).returncode]
    branch = next((ref for ref in ("refs/heads/main", "refs/heads/master") if ref in refs), None)
    if upstream is not None and branch:
        # Refresh even when every claim tag is cached. Do not move remote-tracking branches:
        # ready/claim must not make the local task files appear to have been pulled.
        wanted.insert(0, f"+{branch}:refs/workorder/upstream")
    for start in range(0, len(wanted), 200):
        git(root, "fetch", "--no-tags", "--refmap=", "origin", *wanted[start:start + 200],
            required=upstream is not None)
    if upstream is not None and branch:
        invalidate_reads()
        commit = git(root, "rev-parse", "refs/workorder/upstream^{commit}").stdout.strip()
        if snapshot is not None:
            snapshot.update(commit=commit, ref="origin/" + branch[11:])
        for path, text in metadata_texts(root, commit).items():
            if not path.startswith("tasks/") or not path.endswith(".md"):
                continue
            tid = path[6:-3]
            if not ID.fullmatch(tid):
                continue
            try:
                lines = checked_text(text, path).splitlines()
                fields = front_matter(tid, lines[1:lines.index("---", 1)])
            except (Refused, ValueError):
                continue
            if fields.get("status") in {"DONE", "CLOSED", "CANCELLED", "RETIRED"}:
                upstream[tid] = {"status": fields["status"], "ref": "origin/" + branch[11:],
                                 "commit": commit}
    for tid, (generation, ref, oid) in latest.items():
        released = NAMESPACE + f"releases/{tid}/{oid}" in refs
        try:
            value = json.loads(git(root, "cat-file", "blob", oid).stdout)
        except (Refused, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        result[tid] = {**value, "generation": generation, "oid": oid, "released": released}
    return result


def claim_token_path(root, tid):
    relative = git(root, "rev-parse", "--git-path", f"workorder-claims/{tid}").stdout.strip()
    return root / relative


def token_hash(token):
    return digest(token.encode("utf-8"))


def holds(held, token):
    """True when this clone's private token matches the hash stored in the claim tag."""
    expected = held.get("token_sha256")
    return isinstance(expected, str) and hmac.compare_digest(expected, token_hash(token))


def cache_reservation(root, ref, oid):
    """Keep successfully pushed claim/release tags available to hooks that never fetch."""
    if oid:
        git(root, "update-ref", ref, oid, required=False)


def claim(root, tid):
    repository(root)
    record(root, tid)
    upstream, snapshot = {}, {}
    held = active_claims(root, upstream, snapshot).get(tid)
    newer = newer_work(root, tid, snapshot)
    if newer:
        raise Refused(f"{snapshot['ref']} has newer work on {tid} ({newer}); pull first")
    if tid in upstream:
        finished = upstream[tid]
        author = git(root, "log", "-1", "--format=%an", finished["commit"], "--", f"tasks/{tid}.md").stdout.strip()
        raise Refused(f"{tid} is already {finished['status']} on {finished['ref']} (by {author}); pull to update")
    token_path = claim_token_path(root, tid)
    token = token_path.read_text(encoding="utf-8") if token_path.exists() else uuid.uuid4().hex
    if held and not held["released"]:
        if holds(held, token):
            cache_reservation(root, NAMESPACE + f"claims/{tid}/{held['generation']}", held["oid"])
            return held
        raise Refused(f"{tid} is held by {held['who']} since {held['at']} - not claimed")
    generation = held["generation"] + 1 if held else 1
    # Only the hash is pushed; the token itself stays in this clone's .git directory.
    value = {"task": tid, "who": who(root), "at": now(), "token_sha256": token_hash(token)}
    # Save before pushing: a crash can leave a recoverable claim, never a lost key.
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8", newline="\n")
    ref = NAMESPACE + f"claims/{tid}/{generation}"
    oid = reserve(root, ref, value)
    if not oid:
        winner = active_claims(root).get(tid)
        raise Refused(f"{tid} was claimed by {winner['who']} first - not claimed")
    cache_reservation(root, ref, oid)
    return value


def release(root, tid, force=False):
    """Give up a claim. Only the holding clone can, unless force: for a claim whose clone or worktree is
    gone. Anyone with push access could push the release tag by hand; force does it openly and says so."""
    repository(root)
    task_id(tid)
    held = active_claims(root).get(tid)
    path = claim_token_path(root, tid)
    if not held or held["released"]:
        raise Refused(f"{tid} has no active claim")
    holder = path.exists() and holds(held, path.read_text(encoding="utf-8"))
    if not holder and not force:
        raise Refused(f"{tid}: only its holder can release it; if that clone or worktree is gone, run"
                      f" workorder release --task {tid} --force")
    value = {"at": now(), "who": who(root), **({} if holder else {"forced": True, "holder": held["who"]})}
    ref = NAMESPACE + f"releases/{tid}/{held['oid']}"
    cache_reservation(root, ref, reserve(root, ref, value))
    return not holder


@changing_repository()
def fetch_upstream(root):
    """Best-effort show refresh, with one short deadline and no stale-ref fallback."""
    if not has_origin(root):
        return None
    deadline = time.monotonic() + 3
    for branch in ("main", "master"):
        try:
            result = subprocess.run(
                ["git", "fetch", "--no-tags", "--refmap=", "origin",
                 f"+refs/heads/{branch}:refs/workorder/upstream"], cwd=root,
                capture_output=True, timeout=max(.01, deadline - time.monotonic()),
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode == 0:
            invalidate_reads()
            return {"ref": f"origin/{branch}",
                    "commit": git(root, "rev-parse", "refs/workorder/upstream^{commit}").stdout.strip()}
        if time.monotonic() >= deadline:
            break
    return {}


def newer_work(root, tid, snapshot):
    """Upstream task work not present here; local additions alone are not stale work."""
    if not snapshot:
        return None
    commit = snapshot["commit"]
    remote = metadata_texts(root, commit, tid)
    task_path = f"tasks/{tid}.md"
    if task_path not in remote:
        return None
    base = git(root, "merge-base", "HEAD", commit, required=False).stdout.strip()
    before = metadata_texts(root, base, tid) if base else {}
    local = receipt_texts(root, tid)
    try:
        local[task_path] = read_text(root, task_path)
    except (OSError, Refused):
        pass
    different = [p for p in remote.keys() | before.keys()
                 if remote.get(p) != local.get(p) and remote.get(p) != before.get(p)]
    if not different:
        return None
    receipts = len(entries(root, tid, rev=commit))
    notes = sum(line.lstrip().startswith("- ") for heading, body in task_body_sections(remote[task_path])
                if heading == "Progress" for line in body)
    author = git(root, "log", "-1", "--format=%an", commit, "--", task_path,
                 *(p for p in remote if receipt_task(p) == tid)).stdout.strip()
    return (f"{receipts} receipt{'s' if receipts != 1 else ''}, "
            f"{notes} note{'s' if notes != 1 else ''}, by {author}")


def task_body_sections(text):
    """The Markdown body after the front matter, as (heading, lines) pairs in order."""
    lines = text.splitlines()
    end = lines.index("---", 1) if lines and lines[0] == "---" and "---" in lines[1:] else -1
    sections, heading, current = [], None, []
    for line in lines[end + 1:]:
        if line.startswith("## "):
            sections.append((heading, current))
            heading, current = line[3:].strip(), []
        else:
            current.append(line)
    sections.append((heading, current))
    return sections


def set_task_field(root, tid, key, value):
    """Replace status or next in validated front matter, keeping other bytes and scalar quotes."""
    record(root, tid)
    if key not in {"status", "next"}:
        raise Refused("only status and next can be updated here")
    path = local_path(root, f"tasks/{tid}.md")
    lines = path.read_bytes().splitlines(keepends=True)
    closing = next(i for i in range(1, len(lines)) if lines[i].rstrip(b'\r\n') == b'---')
    start = next((i for i in range(1, closing) if lines[i].startswith(key.encode() + b':')), closing)
    newline = b'\r\n' if lines[0].endswith(b'\r\n') else b'\n'
    raw = lines[start].rstrip(b'\r\n').partition(b':')[2].strip() if start < closing else b''
    if raw.startswith(b"'") and raw.endswith(b"'") and not any(c in value for c in '\r\n'):
        encoded = ("'" + value.replace("'", "''") + "'").encode('utf-8')
    elif key == "status" and not raw.startswith(b'"'):
        encoded = value.encode('utf-8')
    else:
        encoded = packed(value).encode('utf-8')
    end = start + 1 if start < closing else start
    if raw.decode('utf-8') in BLOCK_INDICATORS:
        while end < closing and (not lines[end].strip() or lines[end].startswith(b' ')):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
    prefix = lines[start].partition(b':')[0] + b': ' if start < closing else key.encode() + b': '
    lines[start:end] = [prefix + encoded + newline]
    path.write_bytes(b''.join(lines))
    invalidate_reads()


def retire(root, tid, reason):
    """Mark a DONE or CLOSED task RETIRED, keeping its record, notes and receipts."""
    current = record(root, tid)
    reason = " ".join((reason or "").split())
    if not reason:
        raise Refused("retiring a task needs a reason: --reason TEXT")
    if current["status"] not in {"DONE", "CLOSED"}:
        raise Refused(f"{tid} is {current['status']}; only a DONE or CLOSED task can be retired")
    path = local_path(root, f"tasks/{tid}.md")
    before = path.read_bytes()
    try:
        set_task_field(root, tid, "status", "RETIRED")
        line = add_note(root, tid, f"Retired (was {current['status']}): {reason}")
    except BaseException:
        path.write_bytes(before)
        invalidate_reads()
        raise
    return current["status"], line


def add_note(root, tid, text):
    """Add "- YYYY-MM-DD: text" at the end of Progress, creating the section if needed."""
    record(root, tid)
    text = " ".join(text.split())
    if not text:
        raise Refused("a note needs some text")
    path = local_path(root, f"tasks/{tid}.md")
    lines = path.read_bytes().splitlines(keepends=True)
    newline = b'\r\n' if lines[0].endswith(b'\r\n') else b'\n'
    line = f"- {dt.datetime.now(dt.timezone.utc).date().isoformat()}: {text}"
    encoded = line.encode('utf-8') + newline
    starts = [i for i, value in enumerate(lines) if value.strip() == b"## Progress"]
    if starts:
        end = next((i for i in range(starts[-1] + 1, len(lines)) if lines[i].startswith(b"## ")), len(lines))
        while end > starts[-1] + 1 and not lines[end - 1].strip():
            end -= 1
        if not lines[end - 1].endswith(b'\n'):
            lines[end - 1] += newline
        lines[end:end] = ([encoded] if lines[end - 1].strip() and not lines[end - 1].startswith(b"## ")
                          else [newline, encoded])
    else:
        if not lines[-1].endswith(b'\n'):
            lines[-1] += newline
        if lines[-1].strip():
            lines.append(newline)
        lines += [b"## Progress" + newline, newline, encoded]
    path.write_bytes(b''.join(lines))
    invalidate_reads()
    return line


def ledger_path(tid):
    return f".workorder/receipts/{task_id(tid)}.jsonl"


RECEIPT_DIRS = (".workorder/receipts", ".notquite/receipts")


def receipt_task(path):
    """The task owning either receipt layout; ignore notes beside the ledgers."""
    parts = path.split("/")
    if len(parts) not in (3, 4) or "/".join(parts[:2]) not in RECEIPT_DIRS:
        return None
    tid = parts[2].removesuffix(".jsonl") if len(parts) == 3 else parts[2]
    if ID.fullmatch(tid) and path.endswith(".jsonl" if len(parts) == 3 else ".json"):
        return tid
    return None


def metadata_scope(tid):
    """Share a full metadata inventory only when subsequent reads can reuse it."""
    cache = _READS.get()
    fresh = (cache is None or os.environ.get('WORKORDER_GIT_CACHE') == '0'
             or cache.stack and not cache.stack[-1][1])
    return tid if fresh else None


@cached_read
def metadata_texts(root, rev=None, tid=None, staged=False):
    """Read task and receipt blobs in two Git calls, preserving byte-sized batch framing.

    HEAD and the index are shared across tasks. Historical reads select one task;
    immutable blobs already read in this session need not be read again.
    """
    scopes = ([f"tasks/{tid}.md", *[f"{d}/{tid}.jsonl" for d in RECEIPT_DIRS],
               *[f"{d}/{tid}" for d in RECEIPT_DIRS]] if tid else ["tasks", *RECEIPT_DIRS])
    args = ["ls-files", "--stage", "-z"] if staged else ["ls-tree", "-r", "-z", rev or "HEAD"]
    listing = git(root, *args, "--", *scopes, required=False)
    objects = {}
    for entry in listing.stdout.split("\0"):
        if not entry:
            continue
        info, path = entry.split("\t", 1)
        mode, kind, oid = info.split()
        if staged:
            oid, stage = kind, oid
            if stage != "0":
                continue  # unresolved paths are reported as missing staged files
        elif kind != "blob":
            continue
        if receipt_task(path) or (path.startswith("tasks/") and path.endswith(".md")):
            objects[path] = oid
    blobs = read_blobs(root, objects.values())
    return {path: blobs[oid] for path, oid in objects.items()}


@partial(cached_read, git_query=True)
def blob_bytes(root, spec):
    """Read a blob by object ID or revision:path, preserving its exact bytes."""
    # The batch protocol is line delimited. Unusual Git paths still use argv.
    if '\n' in spec or '\r' in spec:
        result = subprocess.run(['git', 'show', spec], cwd=root, capture_output=True, timeout=30)
        return result.stdout if result.returncode == 0 else None
    item = _batch_object(root, spec)
    return item[2] if item is not None and item[1] == b'blob' else None


def _batch_object(root, spec):
    cache = _READS.get()
    if (cache is None or os.environ.get('WORKORDER_GIT_CACHE') == '0'
            or cache.stack and not cache.stack[-1][1]):
        batch = Batch(root)
        try:
            return batch.read(spec)
        except (ValueError, OSError) as error:
            raise Refused(str(error)) from error
        finally:
            batch.close()
    key = (root, tuple(sorted(os.environ.items())))
    if key not in cache.batches:
        cache.batches[key] = Batch(root)
    try:
        return cache.batches[key].read(spec)
    except (ValueError, OSError) as error:
        cache.batches.pop(key).close()
        raise Refused(str(error)) from error


def read_blobs(root, oids):
    if _READS.get() is None or os.environ.get('WORKORDER_GIT_CACHE') == '0':
        return _read_blobs(root, oids, fresh=True)
    with _READS.get().checking(root) as reusable:
        return _read_blobs(root, oids, fresh=not reusable)


def _read_blobs(root, oids, *, fresh):
    # One Git invocation can read a group of immutable IDs without retaining
    # its replacement-ref/index snapshot across separate fresh read calls.
    oids = set(oids)
    batch = Batch(root) if fresh and oids else None
    blobs = {}
    try:
        for oid in oids:
            if batch is not None and '\n' not in oid and '\r' not in oid:
                item = batch.read(oid)
                raw = item[2] if item is not None and item[1] == b'blob' else None
            else:
                raw = blob_bytes(root, oid)
            if raw is None:
                raise Refused("Git could not read a task or receipt blob")
            blobs[oid] = raw.decode("utf-8", errors="surrogateescape").removeprefix("\ufeff").replace("\r\n", "\n")
    except (OSError, ValueError) as error:
        raise Refused(str(error)) from error
    finally:
        if batch is not None:
            batch.close()
    return blobs


@cached_read
def receipt_paths(root, staged=False, rev=None, tid=None):
    """Receipt files of every task, or of one task (its legacy ledger and its folder) when tid is given."""
    scopes = ([f"{d}/{tid}.jsonl" for d in RECEIPT_DIRS] + [f"{d}/{tid}" for d in RECEIPT_DIRS]
              if tid else list(RECEIPT_DIRS))
    if rev:
        return sorted(p for p in metadata_texts(root, rev, tid if rev != "HEAD" else metadata_scope(tid), False)
                      if receipt_task(p) and (tid is None or receipt_task(p) == tid))
    if staged:
        return sorted(p for p in metadata_texts(root, None, metadata_scope(tid), True)
                      if receipt_task(p) and (tid is None or receipt_task(p) == tid))
    found = []
    for scope in scopes:
        path = local_path(root, scope)
        if path.is_file():
            found.append(scope)
        elif path.is_dir():
            found += files(root, scope)
    return sorted(found)


@cached_read
def receipt_texts(root, tid, staged=False, rev=None):
    if rev or staged:
        return {p: checked_text(text, p) for p, text in metadata_texts(
            root, rev, tid if rev and rev != "HEAD" else metadata_scope(tid), staged).items() if receipt_task(p) == tid}
    texts = {p: read_text(root, p)
             for p in receipt_paths(root, staged, rev, tid) if receipt_task(p) == tid}
    pending = proposed_receipts(root, tid)
    if pending is not None:
        texts.update({f'.workorder/receipts/{tid}/{receipt_name(item)}': packed(item) + '\n'
                      for item in pending})
    return texts


def verify_receipt(root, tid, value):
    if not isinstance(value, dict):
        raise Refused(f"{tid}: receipt must be an object")
    item = dict(value)
    checksum = item.pop("hash", None)
    if checksum != digest(packed(item).encode()):
        raise Refused(f"{tid}: receipt content does not match its hash")
    if (item.get("task") != tid or item.get("kind") not in KINDS
            or not isinstance(item.get("who"), str) or not item["who"].strip()
            or not isinstance(item.get("at"), str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(item.get("commit", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("code", "")))):
        raise Refused(f"{tid}: invalid receipt fields")
    dt.datetime.fromisoformat(item["at"])
    present = has_commit(root, item["commit"])
    if present and code_digest(root, item["commit"]) != item["code"]:
        raise Refused(f"{tid}: receipt code does not match its commit")
    if 'implementation_paths' in item:
        paths = item['implementation_paths']
        if (item['kind'] != 'implemented' or not isinstance(paths, dict)
                or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(item.get('implementation_base', '')))):
            raise Refused(f'{tid}: invalid implementation paths')
        for path, entry in paths.items():
            if (not isinstance(path, str) or not path or path.startswith(('/', 'tasks/', '.workorder/', '.notquite/'))
                    or any(part in {'', '.', '..'} for part in path.split('/'))
                    or (entry is not None and (not isinstance(entry, str) or not re.fullmatch(
                        r'(?:100644 blob|100755 blob|120000 blob|160000 commit) [0-9a-f]{40}(?:[0-9a-f]{24})?', entry)))):
                raise Refused(f'{tid}: invalid implementation path entry')
        if present:
            tree = code_tree(root, item['commit'])
            if any(tree.get(path) != entry for path, entry in paths.items()):
                raise Refused(f'{tid}: implementation paths do not match its commit')
    if 'implementation_syntax' in item:
        syntax = item['implementation_syntax']
        paths = item.get('implementation_paths', {})
        if (item['kind'] != 'implemented' or not isinstance(syntax, dict)
                or any(path not in paths or not path.endswith('.py') or not isinstance(entry, str)
                       or not re.fullmatch(r'100(?:644|755) blob [0-9a-f]{64}', entry)
                       for path, entry in syntax.items())):
            raise Refused(f'{tid}: invalid implementation syntax')
        if present:
            from . import source
            if source.syntax_map(root, paths) != syntax:
                raise Refused(f'{tid}: implementation syntax does not match its commit')
    if item["kind"] in {"test", "deployed", "live"}:
        if (type(item.get("exit_code")) is not int or type(item.get("valid")) is not bool
                or not isinstance(item.get("command"), list) or not item["command"]
                or any(not isinstance(a, str) for a in item["command"])):
            raise Refused(f"{tid}: command receipt needs its arguments, exit code and validity")
    if 'no_code_change_base' in item:
        if (item['kind'] != 'test' or item.get('no_code_change') is not True
                or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', str(item['no_code_change_base']))):
            raise Refused(f'{tid}: invalid no-code-change base')
    return {**item, "hash": checksum, "missing": not present}


def legacy_entries(root, tid, text):
    result, previous = [], ""
    for line in text.splitlines():
        try:
            item = verify_receipt(root, tid, json.loads(line))
        except (ValueError, TypeError) as error:
            raise Refused(f"{tid}: broken receipt chain ({error}); conflict markers need"
                          f" workorder repair --task {tid}") from None
        if item.get("previous") != previous:
            raise Refused(f"{tid}: broken receipt chain (receipts edited or not in order)")
        result.append(item)
        previous = item["hash"]
    return result


def receipt_name(item):
    stamp = dt.datetime.fromisoformat(item["at"])
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=dt.timezone.utc)  # old receipts allowed naive ISO dates
    stamp = stamp.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{item['hash'][:12]}.json"


def ordered_entries(root, tid, texts):
    legacy = []
    for path, text in sorted(texts.items()):
        if path.endswith(".jsonl"):
            chain = legacy_entries(root, tid, text)
            overlap = min(len(legacy), len(chain))
            if [e["hash"] for e in legacy[:overlap]] != [e["hash"] for e in chain[:overlap]]:
                raise Refused(f"{tid}: legacy receipt chains disagree")
            if len(chain) > len(legacy):
                legacy = chain
    items = {}
    for path, text in sorted(texts.items()):
        if not path.endswith(".json"):
            continue
        try:
            item = verify_receipt(root, tid, json.loads(text))
        except (ValueError, TypeError) as error:
            raise Refused(f"{tid}: {path}: {error}") from None
        if Path(path).name != receipt_name(item):
            raise Refused(f"{tid}: receipt file name does not match its hash or UTC time: {path}")
        parents = item.get("parents")
        if (not isinstance(parents, list) or any(not isinstance(p, str) or not re.fullmatch(r"[0-9a-f]{64}", p)
                                                for p in parents)
                or len(set(parents)) != len(parents) or "previous" in item):
            raise Refused(f"{tid}: receipt parents must be distinct hashes")
        if item["hash"] in items:
            raise Refused(f"{tid}: duplicate receipt {item['hash']}")
        items[item["hash"]] = item
    seen = {e["hash"] for e in legacy}
    all_hashes = seen | items.keys()
    converted = {}
    for item in items.values():
        if "legacy" in item:
            original = verify_receipt(root, tid, item["legacy"])
            if ("previous" not in original or "parents" in original
                    or any(item.get(k) != v for k, v in original.items()
                           if k not in {"hash", "previous", "missing"})):
                raise Refused(f"{tid}: repaired receipt differs from its legacy object")
            converted[original["hash"]] = item["hash"]
    for item in items.values():
        if "legacy" in item:
            previous = item["legacy"]["previous"]
            parent = previous if previous in seen else converted.get(previous)
            if (previous and parent is None) or item["parents"] != ([parent] if previous else []):
                raise Refused(f"{tid}: repaired receipt does not follow its legacy parent")
    children, degrees, ready = {}, {}, []
    for checksum, item in items.items():
        if any(parent not in all_hashes for parent in item["parents"]):
            raise Refused(f"{tid}: a receipt this one follows is missing: removed or not yet merged")
        pending = [parent for parent in item["parents"] if parent not in seen]
        degrees[checksum] = len(pending)
        for parent in pending:
            children.setdefault(parent, []).append(checksum)
        if not pending:
            heapq.heappush(ready, (item["at"], checksum))
    result = list(legacy)
    while ready:
        _, checksum = heapq.heappop(ready)
        result.append(items[checksum])
        for child in children.get(checksum, []):
            degrees[child] -= 1
            if not degrees[child]:
                heapq.heappush(ready, (items[child]["at"], child))
    if len(result) != len(legacy) + len(items):
        raise Refused(f"{tid}: receipt parents form a cycle")
    return result


def repaired_prefix(root, tid, before, after, history):
    """A repair may move a legacy tail only with an exact, verified copy of every old object.

    The original objects stay inside the new receipts so HEAD and --base can verify the move.
    No other legacy rewrite is allowed.
    """
    if not before.startswith(after):
        return False
    old = legacy_entries(root, tid, before)
    prefix = legacy_entries(root, tid, after)
    mapping = {e["hash"]: e["hash"] for e in prefix}
    converted = {e["legacy"]["hash"]: e for e in history
                 if isinstance(e.get("legacy"), dict) and "hash" in e["legacy"]}
    for entry in old[len(prefix):]:
        original = {k: v for k, v in entry.items() if k != "missing"}
        new = converted.get(entry["hash"])
        parent = mapping.get(entry["previous"]) if entry["previous"] else None
        if (new is None or new["legacy"] != original
                or (entry["previous"] and parent is None)
                or new["parents"] != ([parent] if parent else [])
                or any(new.get(k) != v for k, v in original.items() if k not in {"hash", "previous"})):
            return False
        mapping[entry["hash"]] = new["hash"]
    return True


def receipt_changes(root, tid, before, after, history=None):
    changed = []
    for path, old in before.items():
        new = after.get(path)
        if new is not None and (new.startswith(old) if path.endswith(".jsonl") else new == old):
            continue
        if path.endswith(".jsonl") and new is not None and history is not None:
            if repaired_prefix(root, tid, old, new, history):
                continue
        changed.append(path)
    return changed


@cached_read
def entries(root, tid, staged=False, rev=None):
    task_id(tid)
    texts = receipt_texts(root, tid, staged, rev)
    old = {} if rev else receipt_texts(root, tid, rev="HEAD")
    # Report immutable files before trying to parse a damaged object.
    changed = receipt_changes(root, tid, old, texts)
    if any(p.endswith(".json") for p in changed):
        raise Refused(f"{tid}: committed receipt file removed or changed: {changed[0]}")
    result = ordered_entries(root, tid, texts)
    if receipt_changes(root, tid, old, texts, result):
        raise Refused(f"{tid}: ledger is append-only; committed receipts were removed or changed")
    return result


def write_receipt(root, tid, data):
    path = local_path(root, f".workorder/receipts/{task_id(tid)}/{receipt_name(data)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(packed(data) + "\n")
    invalidate_reads()
    return path


_PROPOSED_RECEIPTS = ContextVar('workorder_proposed_receipts', default=None)


def proposed_receipts(root, tid):
    transaction = _PROPOSED_RECEIPTS.get()
    return transaction[2] if transaction and transaction[:2] == (root, tid) else None


def annotate_pending_review(root, tid, findings):
    """Attach review details to the unpublished test receipt in a completion transaction."""
    pending = proposed_receipts(root, tid)
    if not pending or pending[-1]['kind'] != 'test' or not pending[-1].get('pending_review'):
        raise Refused('pending findings need a proposed test receipt')
    item = pending[-1]
    item['pending_findings'] = [
        {'message': str(line), 'label_only': '(or an approving review)' not in line}
        for line in dict.fromkeys(findings)]
    item.pop('hash')
    item['hash'] = digest(packed(item).encode())
    invalidate_reads(metadata_only=True)


def completion_transaction(function):
    """Check proposed receipts in memory; publish them only with a supported DONE."""
    @wraps(function)
    def complete(root, tid, *args, **kwargs):
        path = local_path(root, f'tasks/{task_id(tid)}.md')
        before = path.read_bytes() if path.is_file() else None
        pending, written = [], []
        succeeded = False
        with ledger_lock(root, tid):
            token = _PROPOSED_RECEIPTS.set((root, tid, pending))
            try:
                result = function(root, tid, *args, **kwargs)
                if result == 0:
                    for item in pending:
                        written.append(write_receipt(root, tid, item))
                    succeeded = True
                return result
            finally:
                if not succeeded:
                    for receipt_path in written:
                        receipt_path.unlink()
                    if before is not None and path.read_bytes() != before:
                        path.write_bytes(before)
                _PROPOSED_RECEIPTS.reset(token)
                invalidate_reads()
    return complete


def append(root, tid, item):
    history = entries(root, tid)
    followed = {p for e in history for p in e.get("parents", [e.get("previous", "")])}
    heads = sorted(e["hash"] for e in history if e["hash"] not in followed)
    data = {**item, "task": tid, "at": now(), "who": who(root), "parents": heads}
    data["hash"] = digest(packed(data).encode())
    pending = proposed_receipts(root, tid)
    if pending is None:
        write_receipt(root, tid, data)
    else:
        pending.append(data)
        invalidate_reads(metadata_only=True)
    return data


def repair(root, tid):
    """Split a verified, two-sided append conflict. Refuse ambiguous or damaged input."""
    record(root, tid)
    with ledger_lock(root, tid):
        texts = receipt_texts(root, tid)
        conflicts = [p for p, text in texts.items() if p.endswith(".jsonl") and "<<<<<<<" in text]
        if len(conflicts) != 1:
            raise Refused(f"{tid}: repair needs one legacy ledger with one append conflict")
        path = conflicts[0]
        lines = texts[path].splitlines(keepends=True)
        markers = [(i, line) for i, line in enumerate(lines)
                   if line.startswith(("<<<<<<<", "|||||||", "=======", ">>>>>>>"))]
        kinds = [line[:7] for _, line in markers]
        if kinds not in (["<<<<<<<", "=======", ">>>>>>>"],
                          ["<<<<<<<", "|||||||", "=======", ">>>>>>>"]):
            raise Refused(f"{tid}: repair needs one complete, two-sided append conflict")
        start, end = markers[0][0], markers[-1][0]
        split = next(i for i, line in markers if line.startswith("======="))
        base_start = next((i for i, line in markers if line.startswith("|||||||")), split)
        if any(line.strip() for line in lines[end + 1:]) or any(line.strip() for line in lines[base_start + 1:split]
                                                              if base_start != split):
            raise Refused(f"{tid}: repair only accepts appended lines after a common prefix")
        prefix = "".join(lines[:start])
        common = legacy_entries(root, tid, prefix)
        sides = [legacy_entries(root, tid, prefix + "".join(side))[len(common):]
                 for side in (lines[start + 1:base_start], lines[split + 1:end])]
        converted = {}
        for side in sides:
            parent = common[-1]["hash"] if common else None
            for entry in side:
                original = {k: v for k, v in entry.items() if k != "missing"}
                data = {k: v for k, v in original.items() if k not in {"hash", "previous"}}
                data.update(parents=[parent] if parent else [], legacy=original)
                data["hash"] = digest(packed(data).encode())
                converted[data["hash"]] = data
                parent = data["hash"]
        proposed = {**texts, path: prefix}
        for data in converted.values():
            proposed[f".workorder/receipts/{tid}/{receipt_name(data)}"] = packed(data) + "\n"
        history = ordered_entries(root, tid, proposed)
        if receipt_changes(root, tid, receipt_texts(root, tid, rev="HEAD"), proposed, history):
            raise Refused(f"{tid}: repair would lose committed receipts")
        for data in converted.values():
            name = f".workorder/receipts/{tid}/{receipt_name(data)}"
            if name in texts and texts[name] != proposed[name]:
                raise Refused(f"{tid}: repair destination already exists: {name}")
        for data in converted.values():
            if f".workorder/receipts/{tid}/{receipt_name(data)}" not in texts:
                write_receipt(root, tid, data)
        local_path(root, path).write_text(prefix, encoding="utf-8", newline="\n")
        return (f"{tid}: kept {len(common)} legacy receipts in {path}; converted {len(sides[0])} and"
                f" {len(sides[1])} branch receipts into {len(converted)} files; stage the repaired files")


def ledger_lock(root, tid):
    """Serialise receipt writers for one task in this clone: each writer must see all current heads."""
    import contextlib
    import time

    @contextlib.contextmanager
    def held():
        folder = root / git(root, "rev-parse", "--git-path", "workorder-locks").stdout.strip()
        folder.mkdir(parents=True, exist_ok=True)
        lock = folder / f"{task_id(tid)}.lock"
        deadline = time.monotonic() + 120
        while True:
            try:
                handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError) as error:
                # On Windows a lock file that another writer is deleting refuses a new create with
                # "permission denied" instead of "exists"; both mean the lock is busy. Elsewhere a
                # permission error is real and is raised.
                if isinstance(error, PermissionError) and os.name != "nt":
                    raise
                if time.monotonic() > deadline:
                    raise Refused(f"{tid}: another receipt is being written; if no workorder command is"
                                  f" running, remove {lock}") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(handle)
            lock.unlink(missing_ok=True)
    return held()


# Releases up to 0.2.0 (named NotQuite) kept receipts in .notquite/. It stays outside the code
# fingerprint, so a repository that moved its receipts to .workorder/ still verifies old receipts.
LEGACY_DIR = ".notquite/"
CODE_PATHS = (".", ":(exclude)tasks", ":(exclude).workorder", ":(exclude).notquite")


def generated_path(path):
    """Build metadata and Python caches do not belong to a task's code scope."""
    return (path.endswith('.pyc') or any(part in {'__pycache__', '.pytest_cache'}
            or part.endswith(('.egg-info', '.dist-info')) for part in path.split('/')[:-1]))


# Commits never change, so their fingerprints are worked out once per run.
_DIGESTS = {}
_PRESENT = {}


SHELL_OPERATORS = {"&&", "||", ";", "|", "&", ">", ">>", "<", "2>", "2>&1"}


def split_command(value, what="test"):
    """A test command line as arguments: split on spaces, quotes group words, backslashes are kept as
    written (so Windows paths work), and no shell runs it, so shell operators are refused."""
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split, lexer.escape, lexer.commenters = True, "", ""
    try:
        words = list(lexer)
    except ValueError as error:
        raise Refused(f"{what} is not a valid command line ({error})") from None
    if words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        raise Refused(f"{what} starts with an environment setting ({words[0]}), which needs a shell;"
                      " use env, as in: env " + " ".join(words))
    operators = sorted(set(words) & SHELL_OPERATORS)
    if operators:
        raise Refused(f"{what} runs without a shell, so {' '.join(operators)} would be passed as an argument;"
                      " put the steps in a script, or use sh -c '...'")
    return words


def join_command(words):
    """Show arguments the way split_command reads them: plain words as they are, others quoted."""
    shown = []
    for word in words:
        if word and not any(ch.isspace() or ch in "\"'" for ch in word):
            shown.append(word)
        else:
            shown.append(f"'{word}'" if '"' in word else f'"{word}"')
    return " ".join(shown)


def has_commit(root, commit):
    key = (str(root), commit)
    if key not in _PRESENT:
        _PRESENT[key] = git(root, "cat-file", "-e", f"{commit}^{{commit}}", required=False).returncode == 0
    return _PRESENT[key]


def contains(root, ancestor, commit):
    """True when commit's history includes ancestor (both must be in this clone)."""
    return (has_commit(root, ancestor) and has_commit(root, commit)
            and git(root, "merge-base", "--is-ancestor", ancestor, commit, required=False).returncode == 0)


def _cache_dir(root):
    key = ("cache", str(root))
    if key not in _DIGESTS:
        found = git(root, "rev-parse", "--git-path", "workorder-digests", required=False)
        _DIGESTS[key] = (root / found.stdout.strip()) if found.returncode == 0 else None
    return _DIGESTS[key]


def code_digest(root, commit):
    """The code fingerprint of a commit (or tree). Commits never change, so the result is kept for the
    run and in .git/workorder-digests/ for later runs; it is a cache, never evidence."""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        return _code_digest(root, commit)
    key = (str(root), commit)
    if key in _DIGESTS:
        return _DIGESTS[key]
    folder = _cache_dir(root)
    cached = folder / commit if folder else None
    try:
        value = cached.read_text(encoding="ascii").strip() if cached and cached.is_file() else ""
    except OSError:
        value = ""
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        value = _code_digest(root, commit)
        try:
            if cached:
                folder.mkdir(exist_ok=True)
                cached.write_text(value, encoding="ascii")
        except OSError:
            pass
    _DIGESTS[key] = value
    return value


def _code_digest(root, commit):
    listing = git(root, "ls-tree", "-r", "-z", commit, required=False)
    if listing.returncode:
        raise Refused(f"commit {commit[:7]} is not in this clone; fetch the full history"
                      " (in GitHub Actions: actions/checkout with fetch-depth: 0)")
    tree = listing.stdout
    code = [entry for entry in tree.split("\0") if entry
            and not entry.partition("\t")[2].startswith(("tasks/", ".workorder/", LEGACY_DIR))]
    return digest("\0".join(code).encode("utf-8", errors="surrogateescape"))


@cached_read
def code_tree(root, revision):
    """Git mode, object type and object ID for each code path, independent of checkout endings."""
    return {path: entry for value in git(root, 'ls-tree', '-r', '-z', revision).stdout.split('\0') if value
            for entry, path in [value.split('\t', 1)]
            if not path.startswith(('tasks/', '.workorder/', LEGACY_DIR))}


def index_code(root):
    """The code fingerprint of what the next commit would contain (the index)."""
    return code_digest(root, index_tree(root))


def dirty_paths(root):
    """Uncommitted or untracked code files; task records and receipts do not count."""
    generated = [f':(exclude,glob)**/{pattern}' for pattern in
                 ('*.egg-info/**', '*.dist-info/**', '__pycache__/**', '*.pyc', '.pytest_cache/**')]
    status = git(root, "status", "--porcelain", "--untracked-files=all", "--", *CODE_PATHS, *generated).stdout
    paths = [line[3:] for line in status.splitlines()]
    from . import source
    if not paths:
        return []
    meaningful = source.changed(root, paths, 'HEAD') | source.changed(root, paths, 'HEAD', staged=True)
    return [path for path in paths if path in meaningful]


def clean_code(root):
    return not dirty_paths(root)


def describe_dirty(paths):
    shown = ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")
    return f"uncommitted or untracked files: {shown} (commit them, or add build output to .gitignore)"


def positive_seconds(value, what="timeout"):
    try:
        seconds = float(value) if not isinstance(value, bool) else 0
    except (ValueError, TypeError, OverflowError):
        seconds = 0
    if not math.isfinite(seconds) or seconds <= 0:
        raise Refused(f"{what} must be a positive number of seconds")
    return seconds


def test_timeout(record, override=None):
    value = override if override is not None else record.get("test_timeout")
    return positive_seconds(value) if value is not None else None


@changing_repository()
def run_test(command, root, timeout=None, capture_output=True, *, isolated=False, cache_root=None,
             verify_imports=None):
    """Run a test without a shell; a deadline is recorded as a failed run, exit 124."""
    from .python_imports import ImportFailure, bytecode_cache, environment, pytest_guard
    # pytest parses this environment option with shlex even on Windows. Each
    # subprocess owns its pytest cache; neither cache affects our result key.
    env = environment(root, copy_environment() if isolated else os.environ.copy())
    with tempfile.TemporaryDirectory(prefix="workorder-pytest-", ignore_cleanup_errors=True) as cache:
        env["PYTEST_ADDOPTS"] = (env.get("PYTEST_ADDOPTS", "")
                                 + " -o cache_dir=" + shlex.quote(cache))
        with pytest_guard(command, root, verify_imports, env) as guard, \
                bytecode_cache(guard.command if guard else command, root, guard.env if guard else env,
                               cache_root=cache_root) as (execution, env):
            if guard:
                guard.pycache_prefix = env.get('PYTHONPYCACHEPREFIX')
            # Bound import verification during pytest startup even without a
            # test timeout. An earlier declared test deadline always wins.
            if timeout is None and guard is None:
                run = subprocess.run(execution, cwd=root, capture_output=capture_output,
                                     encoding="utf-8", errors="replace", env=env)
                run.args = command
                return run
            started = time.monotonic()
            deadline = started + timeout if timeout is not None else None
            probe_deadline = started + guard.timeout if guard else None
            with subprocess.Popen(execution, cwd=root, stdout=subprocess.PIPE if capture_output else None,
                                  stderr=subprocess.PIPE if capture_output else None,
                                  encoding="utf-8", errors="replace", start_new_session=os.name != 'nt',
                                  env=env) as process:
                def stop():
                    if os.name != 'nt':
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    return process.communicate()

                while True:
                    now = time.monotonic()
                    if deadline is not None and now >= deadline:
                        output, errors = stop()
                        message = f"test timed out after {timeout:g} seconds (exit 124)\n"
                        if capture_output:
                            errors = (errors or "") + message
                        else:
                            print(message, end='', file=sys.stderr, flush=True)
                        return subprocess.CompletedProcess(command, 124, output, errors)
                    if probe_deadline is not None:
                        report = guard.status()
                        if report is not None:
                            if not report.get('ok'):
                                output, errors = stop()
                                return guard.finish(subprocess.CompletedProcess(command, 4, output, errors))
                            probe_deadline = None
                        elif now >= probe_deadline:
                            output, errors = stop()
                            failure = ImportFailure('could not verify project imports: '
                                                    f'import guard startup timed out after {guard.timeout:g} seconds')
                            return subprocess.CompletedProcess(command, failure, output, errors)
                    wait = max(0, deadline - now) if deadline is not None else None
                    if probe_deadline is not None:
                        wait = min(wait if wait is not None else 0.05, 0.05, max(0, probe_deadline - now))
                    try:
                        output, errors = process.communicate(timeout=wait)
                    except subprocess.TimeoutExpired:
                        continue
                    run = subprocess.CompletedProcess(command, process.returncode, output, errors)
                    return guard.finish(run) if guard else run


def _test_state(root, isolated=False):
    """Fingerprint the files actually executed, including dirty snapshot overlays.

    HEAD alone cannot identify a staged copy or current code with REF tests overlaid.
    Ignore only Git-ignored files and Workorder metadata, as with ordinary code checks.
    Read Git afresh because a declared command can change files or refs in this process.
    """
    commit = _git_run(root, 'rev-parse', '--verify', 'HEAD^{commit}', isolated=isolated).stdout.strip()
    listing = _git_run(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard',
                       '--', *CODE_PATHS, isolated=isolated).stdout
    fingerprint = hashlib.sha256()
    for relative in sorted(set(listing.split('\0')) - {''}):
        path = root / relative
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue  # tracked deletion in the working tree or a snapshot overlay
        if stat.S_ISLNK(mode):
            content, kind = os.fsencode(os.readlink(path)), 'symlink'
        elif stat.S_ISREG(mode):
            content, kind = path.read_bytes(), 'executable' if mode & 0o111 else 'file'
        else:
            # A gitlink or special file needs more than its own bytes to identify
            # executable code. Run fresh rather than reuse an incomplete fingerprint.
            return None
        fingerprint.update(packed([relative, kind, digest(content)]).encode('ascii') + b'\n')
    return commit, fingerprint.hexdigest()


def ci_environment():
    return any(os.environ.get(name, '').lower() not in {'', '0', 'false', 'no'}
               for name in ('CI', 'GITHUB_ACTIONS'))


def test_cache_path(root, fingerprint, command, timeout):
    folder = root / git(root, 'rev-parse', '--git-path', 'workorder-tests').stdout.strip()
    key = digest(packed([4, fingerprint, list(command), timeout]).encode('ascii'))
    return folder / (key + '.json')


def read_test_cache(path, command):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if (isinstance(value, dict) and type(value.get('returncode')) is int
                and isinstance(value.get('stdout'), str) and isinstance(value.get('stderr'), str)
                and re.fullmatch(r'\d{2}:\d{2}', value.get('at', ''))):
            run = subprocess.CompletedProcess(command, value['returncode'], value['stdout'], value['stderr'])
            run.cached = True
            run.reused_at = value['at']
            return run
    except (OSError, ValueError):
        pass
    return None


def checkout_bytes(root, path):
    """Index contents with Git's checkout conversion, as a user would run them."""
    # Attribute rules must come from the index too, not unstaged .gitattributes.
    tree = index_tree(root)
    with changing_repository():
        result = subprocess.run(['git', 'cat-file', '--filters', f':{path}'], cwd=root,
                                capture_output=True, timeout=30,
                                env={**os.environ, 'GIT_ATTR_SOURCE': tree})
    if result.returncode:
        raise Refused(f'could not read staged checkout content: {path}')
    return result.stdout


def checkout_contents(root, entries):
    """Materialize index files in one Git call with the index's checkout rules.

    cat-file --batch --filters reports the unfiltered size even when conversion
    changes the byte count. checkout-index provides unambiguous file boundaries,
    including binary data and newline paths, without keeping every blob in memory.
    """
    if not entries:
        return
    requests = b''.join(os.fsencode(path) + b'\0' for path, _, _ in entries)
    with tempfile.TemporaryDirectory(prefix='workorder-checkout-') as temporary:
        destination = Path(temporary)
        with changing_repository():
            result = subprocess.run(['git', 'checkout-index', '--stdin', '-z', '--ignore-skip-worktree-bits',
                                     '--prefix=' + destination.as_posix() + '/'], cwd=root,
                                    input=requests, capture_output=True, timeout=30,
                                    env={**os.environ, 'GIT_ATTR_SOURCE': index_tree(root)})
        if result.returncode:
            raise Refused('could not read staged checkout content: ' + result.stderr.decode('utf-8', 'replace').strip())
        for path, _, mode in entries:
            yield path, mode, (destination / path).read_bytes()


@partial(cached_read, git_query=True, refresh_after=True)
def index_tree(root):
    # write-tree updates the index's tree cache. Preserve the caller's index,
    # including an alternate index supplied by Git while running a hook.
    source = Path(git(root, 'rev-parse', '--git-path', 'index').stdout.strip())
    if not source.is_absolute():
        source = root / source
    with tempfile.TemporaryDirectory(prefix='workorder-index-') as temporary:
        index = Path(temporary) / 'index'
        try:
            raw = source.read_bytes()
        except FileNotFoundError:
            pass  # Git interprets an absent index as an empty staged tree.
        else:
            index.write_bytes(raw)
        result = subprocess.run(['git', 'write-tree'], cwd=root, capture_output=True,
                                encoding='utf-8', errors='surrogateescape', timeout=30,
                                env={**os.environ, 'GIT_INDEX_FILE': str(index)})
        if result.returncode:
            raise Refused(result.stderr.strip() or 'could not read the staged tree')
        return result.stdout.strip()


def cached_test(command, root, timeout=None, *, cache_root=None, logical_command=None, fresh=False):
    """Reuse a stable test result within this clone; CI and explicit reruns run fresh.

    Snapshot callers name their original repository and command so a temporary
    path does not defeat sharing. Results remain local in Git's private directory,
    never in receipts or cloned files. A fresh run replaces any earlier result.
    """
    owner = cache_root if cache_root is not None else root
    isolated = owner != root
    integrate = False
    if isolated:
        from .python_imports import ImportFailure, check, environment, integrated_pytest
        integrate = integrated_pytest(command, root, environment(root, copy_environment()))
        if not integrate:
            failure = check(root, owner, command, environment(root, copy_environment()), timeout)
            if failure:
                run = subprocess.CompletedProcess(command, failure, '', '')
                run.cached = False
                return run
    logical = list(command if logical_command is None else logical_command)
    timeout = positive_seconds(timeout) if timeout is not None else None
    ci = ci_environment()
    cached_path = None
    before = owner_head = None
    if not ci:
        try:
            before = _test_state(root, isolated)
            owner_head = (_git_run(owner, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.strip()
                          if owner != root else before[0] if before else None)
            if before:
                cached_path = test_cache_path(owner, before[1], logical, timeout)
                if not fresh:
                    run = read_test_cache(cached_path, command)
                    if run is not None and integrate:
                        # Cache hits still verify the interpreter's external
                        # installations. A probe can itself change the checkout,
                        # so choose the cache again from its resulting state.
                        failure = check(root, owner, command, environment(root, copy_environment()), timeout)
                        if failure:
                            run = subprocess.CompletedProcess(command, failure, '', '')
                            run.cached = False
                            return run
                        cached_path = None
                        before = _test_state(root, isolated)
                        owner_head = _git_run(owner, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.strip()
                        if before:
                            cached_path = test_cache_path(owner, before[1], logical, timeout)
                        run = read_test_cache(cached_path, command) if cached_path is not None else None
                    if run is not None:
                        return run
        except (OSError, Refused, ValueError):
            pass  # an absent, malformed or unwritable cache must never prevent a run
    if cached_path is not None:
        try:
            cached_path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        run = run_test(command, root, timeout, capture_output=True, isolated=isolated, cache_root=owner,
                       verify_imports=owner if integrate else None)
        if integrate and getattr(run, 'import_guard_missing_pytest', None) is not None:
            failure = check(root, owner, command, environment(root, copy_environment()), timeout,
                            require_missing_pytest=True)
            if failure:
                run.returncode = failure
            else:
                run.returncode = run.import_guard_missing_pytest
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if not integrate:
            raise
        run = subprocess.CompletedProcess(command, ImportFailure(f'could not verify project imports: {error}'), '', '')
    finally:
        invalidate_reads()
    # Pytest can report a failure even when application shutdown code forces exit 0.
    from .gate import pytest_counts
    outcomes = pytest_counts((run.stdout or '') + (run.stderr or '')) or {}
    if run.returncode == 0 and any(outcomes.get(name, 0) for name in ('failed', 'error', 'errors')):
        run.returncode = 1
    run.cached = False
    if cached_path is not None and isinstance(run.returncode, int):
        temporary = cached_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            if (_test_state(root, isolated) == before
                    and _git_run(owner, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.strip() == owner_head):
                cached_path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(packed(dict(returncode=run.returncode, stdout=run.stdout or '',
                                                 stderr=run.stderr or '', at=dt.datetime.now(dt.timezone.utc).strftime('%H:%M'))), encoding='utf-8')
                os.replace(temporary, cached_path)
        except (OSError, Refused, ValueError):
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return run


def missing_test_dependency(command, code, output, root=None):
    """Name a missing executable or Python module without attributing it to the code."""
    if code == 0:
        return None
    module = re.search(r"No module named\s+['\"]?([\w.]+)", output)
    if module:
        if root is not None:
            name = module[1].split('.')[0]
            for base in (root, root / 'src'):
                package = base / name
                if ((base / (name + '.py')).is_file()
                        or package.is_dir() and any(package.rglob('*.py'))):
                    return (f"cannot import this repository's own module '{module[1]}'; "
                            'check the working directory and PYTHONPATH')
        return f"{module[1]} is not installed in this environment"
    if code == 127 or (isinstance(code, str) and 'No such file or directory' in code):
        name = Path(command[0]).name
        missing = re.search(r'(?:^|\n).*?:\s*(?:\d+:\s*)?([\w./-]+): (?:command )?not found', output)
        if missing:
            name = missing[1]
        return f"{name} is not installed in this environment"
    return None


def receipt(root, tid, kind, command=(), target=None, ref="refs/heads/main", timeout=None,
            no_code_change=False, refactor=False, pending_review=False):
    repository(root)
    definition = record(root, tid)
    commit = head(root)
    if kind not in KINDS:
        raise Refused("unknown receipt kind")
    if kind in {"implemented", "test"} and not clean_code(root):
        raise Refused("commit the code before recording implementation or tests; "
                      + describe_dirty(dirty_paths(root)))
    data = {"kind": kind, "commit": commit, "code": code_digest(root, commit)}
    if kind == 'test' and no_code_change:
        from . import gate
        base, _ = gate.implementation_base(root, definition)
        problem = gate.no_code_change_problem(root, definition, base)
        if problem:
            raise Refused(problem)
        data['no_code_change'] = True
        data['no_code_change_base'] = base
    if kind == 'test' and refactor:
        data['refactor'] = True
    if kind == 'test' and pending_review:
        data['pending_review'] = True
    if kind == 'implemented':
        from . import gate
        base, _ = gate.implementation_base(root, definition)
        paths = gate.implementation_paths(root, {**data, 'task': tid, 'hash': None}, base)
        tree = code_tree(root, commit)
        data.update(implementation_base=base,
                    implementation_paths={path: tree.get(path) for path in sorted(paths)})
        from . import source
        data['implementation_syntax'] = source.syntax_map(root, data['implementation_paths'])
    if kind == "merged":
        if not ref.startswith("refs/heads/"):
            raise Refused("merge ref must be a full branch ref")
        if not has_origin(root):
            raise Refused("receipt merged reads the branch from origin; add an origin remote")
        git(root, "fetch", "--no-tags", "origin", ref)
        commit = git(root, "rev-parse", "FETCH_HEAD^{commit}").stdout.strip()
        data.update(commit=commit, code=code_digest(root, commit), ref=ref)
    if kind in {"test", "deployed", "live"}:
        if not command:
            raise Refused("supply the command after --")
        if kind in {"deployed", "live"} and not target:
            raise Refused("read-backs need --target")
        try:
            if kind == "test":
                run = cached_test(command, root, test_timeout(definition, timeout), fresh=True)
                print_test_output(run.stdout or '', run.stderr or '', failed=run.returncode != 0)
            else:
                with changing_repository():
                    run = subprocess.run(list(command), cwd=root, capture_output=True,
                                         encoding="utf-8", errors="replace")
            exit_code = run.returncode
            output = run.stdout or ""
        except OSError as error:
            exit_code, output = 127, str(error)
        if kind == 'test':
            missing = missing_test_dependency(command, exit_code,
                                              output + (run.stderr or '') if exit_code != 127 else output,
                                              root=root)
            if missing:
                print(f'{tid}: {missing}')
        valid = exit_code == 0
        if kind == "test":
            valid = valid and clean_code(root) and head(root) == commit
            from .gate import pytest_no_pass
            empty = pytest_no_pass(output + (run.stderr or '')) if exit_code != 127 else None
            if empty:
                valid = False
                # Optional detail; 0.12 readers already refuse valid: false.
                data['no_tests_passed'] = empty
                print(f'{tid}: its test ran but no test passed ({empty})')
        else:
            valid = valid and bool(re.search(rf"(?<![0-9a-f]){commit}(?![0-9a-f])", output))
        data.update(command=list(command), exit_code=exit_code, valid=valid)
        if kind != "test":
            data.update(target=target, output_sha256=digest(output.encode()))
    with ledger_lock(root, tid) if proposed_receipts(root, tid) is None else nullcontext():
        return append(root, tid, data)
