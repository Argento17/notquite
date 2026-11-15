"""Local files and immutable remote reservations. No hosting-service API needed."""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
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
import time
import uuid


class Refused(ValueError):
    pass


_READS = ContextVar("workorder_reads", default=None)


@contextmanager
def read_session():
    """Share reads within one command, never across commands in an embedding process."""
    if _READS.get() is not None:
        yield
        return
    token = _READS.set({})
    try:
        yield
    finally:
        _READS.reset(token)


def invalidate_reads():
    cache = _READS.get()
    if cache is not None:
        cache.clear()


def cached_read(function):
    @wraps(function)
    def read(*args, **kwargs):
        cache = _READS.get()
        if cache is None:
            return function(*args, **kwargs)
        key = (function, args, tuple(sorted(kwargs.items())))
        if key not in cache:
            cache[key] = function(*args, **kwargs)
        return cache[key]
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
    run = (_git_read if args[0] in {"rev-parse", "symbolic-ref", "show", "log", "ls-tree",
                                   "ls-files", "diff", "merge-base", "rev-list", "patch-id",
                                   "check-ref-format"} else _git_run)
    return run(root, *args, data=data, required=required)


@cached_read
def _git_read(root, *args, data=None, required=True):
    return _git_run(root, *args, data=data, required=required)


def copy_environment():
    """A temporary repository must not inherit Git's pointers into the caller's repository."""
    local = {'GIT_INDEX_FILE', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_PREFIX', 'GIT_COMMON_DIR',
             'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_IMPLICIT_WORK_TREE',
             'GIT_GRAFT_FILE', 'GIT_SHALLOW_FILE', 'GIT_REPLACE_REF_BASE', 'GIT_NAMESPACE'}
    return {key: value for key, value in os.environ.items() if key not in local}


def _git_run(root, *args, data=None, required=True, isolated=False):
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
        result = subprocess.run(["git", "show", f":{relative}"], cwd=root,
                                capture_output=True, timeout=30)
        if result.returncode:
            raise Refused(f"missing staged file: {relative}")
        return result.stdout
    return local_path(root, relative).read_bytes()


def read_text(root, relative, staged=False):
    # Git on Windows may check text files out with CRLF; the content is the same.
    if staged and (receipt_task(relative) or relative.startswith("tasks/")):
        text = metadata_texts(root, None, None, True).get(relative)
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
STATUSES = ("IN_PROGRESS", "BLOCKED", "DONE", "CLOSED", "CANCELLED")
CORE_FIELDS = ("id", "title", "owner", "status", "ships", "waits_on", "contract")
# Optional fields. Dependencies and the test add requirements; merge_ref selects delivery's target,
# and test_paths selects files guarded by a base check. Planning fields never stand in for evidence.
EXTENDED_FIELDS = ("business_owner", "priority", "created_at", "mission", "depends_on", "blocks",
                   "next", "summary", "test", "test_paths", "test_timeout", "merge_ref")
EXTENDED_ORDER = ("id", "title", "owner", "business_owner", "priority", "created_at", "mission",
                  "depends_on", "blocks", "next", "summary", "status", "ships", "waits_on", "test",
                  "contract")
TASK_REF = r"[A-Z][A-Z0-9]*-[1-9][0-9]*"
FRONT_MATTER_HELP = ("front matter takes `key: value` lines: plain single-line text, a JSON string, or a"
                     ' list such as ["TASK-1"]; mission, next and summary may use > or | blocks, and'
                     " depends_on, blocks, waits_on and test_paths may use indented - item lines")
# The small YAML subset Workorder reads. Anything else is refused rather than guessed.
BLOCK_TEXT_FIELDS = ("mission", "next", "summary")
LIST_FIELDS = ("depends_on", "blocks", "waits_on", "test_paths")
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
        if not isinstance(item, str) or not item or (key != "test_paths" and not re.fullmatch(PLAIN_ITEM, item)):
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
    if "test_paths" in fields:
        paths = fields["test_paths"]
        if not isinstance(paths, list) or any(not isinstance(p, str) or not p.strip() for p in paths):
            raise Refused(f"{tid}: test_paths must be a list of repository paths")
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


def task_text(tid, data, template):
    if template == "simple":
        return ("---\n" + "".join(f"{k}: {packed(v)}\n" for k, v in data.items())
                + f"---\n\n# {tid}\n\nWrite the original ask here.\n")
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    extended = {**dict(business_owner="", priority="", created_at=today, mission="", depends_on=[],
                       blocks=[], next="", summary="", test=""), **data}
    order = list(EXTENDED_ORDER)
    if extended.get("test_paths"):
        order.insert(order.index("test") + 1, "test_paths")
    return ("---\n" + "".join(f"{k}: {packed(extended[k])}\n" for k in order)
            + f"---\n\n# {tid}\n" + EXTENDED_BODY)


def new(root, title, owner, ships="none", prefix="TASK", template="extended", test="", local_only=False,
        test_paths=None):
    repository(root)
    if template not in {"simple", "extended"}:
        raise Refused("template must be simple or extended")
    if not re.fullmatch(r"[A-Z][A-Z0-9]*", prefix) or not title.strip() or not owner.strip():
        raise Refused("use an uppercase prefix, nonempty title and owner")
    if test.strip():
        split_command(test, "--test")
    directory = local_path(root, "tasks")
    directory.mkdir(exist_ok=True)
    # Without an origin remote, ids are numbered locally and not coordinated with other clones.
    # With one, it must answer: never guess offline.
    local = local_only or not has_origin(root)
    for _ in range(30):
        try:
            refs = {} if local else remote_refs(root)
        except Refused as error:
            raise Refused(f"origin could not be reached ({error}); workorder new --local numbers the task"
                          " from the task files without it") from None
        stem = NAMESPACE + f"ids/{prefix}-"
        numbers = [int(ref[len(stem):]) for ref in refs
                   if ref.startswith(stem) and ref[len(stem):].isdigit()]
        numbers += [int(p.stem.split("-")[-1]) for p in directory.glob(f"{prefix}-*.md")
                    if ID.fullmatch(p.stem)]
        tid = f"{prefix}-{max(numbers, default=0) + 1}"
        data = dict(id=tid, title=title, owner=owner, status="IN_PROGRESS", ships=ships,
                    waits_on=[], contract="none", **({"test": test} if test else {}),
                    **({"test_paths": list(test_paths)} if test_paths else {}))
        if local or reserve(root, NAMESPACE + f"ids/{tid}", {**data, "at": now(), "who": who(root)}):
            path = directory / f"{tid}.md"
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(task_text(tid, data, template))
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
            if fields.get("status") in {"DONE", "CLOSED", "CANCELLED"}:
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


def read_blobs(root, oids):
    # Preserve undecodable bytes in the shared batch. read_text/receipt_texts validate only the
    # selected file, so an unrelated task's bad encoding cannot poison every cached metadata read.
    cache = _READS.get()
    blobs = cache.setdefault(("blobs", root), {}) if cache is not None else {}
    wanted = sorted(set(oids) - blobs.keys())
    if wanted:
        result = subprocess.run(["git", "cat-file", "--batch"], cwd=root,
                                input=("\n".join(wanted) + "\n").encode(),
                                capture_output=True, timeout=30, check=True)
        offset = 0
        for oid in wanted:
            end = result.stdout.index(b"\n", offset)
            header = result.stdout[offset:end].split()
            if len(header) != 3 or header[1] != b"blob":
                raise Refused("Git could not read a task or receipt blob")
            size = int(header[2])
            raw = result.stdout[end + 1:end + 1 + size]
            blobs[oid] = raw.decode("utf-8", errors="surrogateescape").removeprefix("\ufeff").replace("\r\n", "\n")
            offset = end + 2 + size
    return blobs


@cached_read
def receipt_paths(root, staged=False, rev=None, tid=None):
    """Receipt files of every task, or of one task (its legacy ledger and its folder) when tid is given."""
    scopes = ([f"{d}/{tid}.jsonl" for d in RECEIPT_DIRS] + [f"{d}/{tid}" for d in RECEIPT_DIRS]
              if tid else list(RECEIPT_DIRS))
    if rev:
        return sorted(p for p in metadata_texts(root, rev, tid if rev != "HEAD" else None, False)
                      if receipt_task(p) and (tid is None or receipt_task(p) == tid))
    if staged:
        return sorted(p for p in metadata_texts(root, None, None, True)
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
            root, rev, tid if rev and rev != "HEAD" else None, staged).items() if receipt_task(p) == tid}
    return {p: read_text(root, p)
            for p in receipt_paths(root, staged, rev, tid) if receipt_task(p) == tid}


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
    if item["kind"] in {"test", "deployed", "live"}:
        if (type(item.get("exit_code")) is not int or type(item.get("valid")) is not bool
                or not isinstance(item.get("command"), list) or not item["command"]
                or any(not isinstance(a, str) for a in item["command"])):
            raise Refused(f"{tid}: command receipt needs its arguments, exit code and validity")
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


def append(root, tid, item):
    history = entries(root, tid)
    followed = {p for e in history for p in e.get("parents", [e.get("previous", "")])}
    heads = sorted(e["hash"] for e in history if e["hash"] not in followed)
    data = {**item, "task": tid, "at": now(), "who": who(root), "parents": heads}
    data["hash"] = digest(packed(data).encode())
    write_receipt(root, tid, data)
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


def index_code(root):
    """The code fingerprint of what the next commit would contain (the index)."""
    return code_digest(root, git(root, "write-tree").stdout.strip())


def dirty_paths(root):
    """Uncommitted or untracked code files; task records and receipts do not count."""
    status = git(root, "status", "--porcelain", "--untracked-files=all", "--", *CODE_PATHS).stdout
    return [line[3:] for line in status.splitlines()]


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


def run_test(command, root, timeout=None, capture_output=True, *, isolated=False):
    """Run a test without a shell; a deadline is recorded as a failed run, exit 124."""
    # Keep the ordinary subprocess path when there is no deadline. With a deadline,
    # use a process group so child test runners cannot keep captured pipes open.
    if timeout is None:
        return subprocess.run(list(command), cwd=root, capture_output=capture_output,
                              encoding="utf-8", errors="replace",
                              env=copy_environment() if isolated else None)
    with subprocess.Popen(list(command), cwd=root, stdout=subprocess.PIPE if capture_output else None,
                          stderr=subprocess.PIPE if capture_output else None,
                          encoding="utf-8", errors="replace", start_new_session=os.name != 'nt',
                          env=copy_environment() if isolated else None) as process:
        try:
            output, errors = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(command, process.returncode, output, errors)
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            output, errors = process.communicate()
            message = f"test timed out after {timeout:g} seconds (exit 124)\n"
            if capture_output:
                errors = (errors or "") + message
            else:
                print(message, end='', file=sys.stderr, flush=True)
            return subprocess.CompletedProcess(command, 124, output, errors)


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


def cached_test(command, root, timeout=None, *, cache_root=None, logical_command=None, fresh=False):
    """Reuse a stable test result within this clone; CI and explicit reruns run fresh.

    Snapshot callers name their original repository and command so a temporary
    path does not defeat sharing. Results remain local in Git's private directory,
    never in receipts or cloned files. A fresh run replaces any earlier result.
    """
    owner = cache_root if cache_root is not None else root
    isolated = owner != root
    logical = list(command if logical_command is None else logical_command)
    timeout = positive_seconds(timeout) if timeout is not None else None
    ci = any(os.environ.get(name, '').lower() not in {'', '0', 'false', 'no'}
             for name in ('CI', 'GITHUB_ACTIONS'))
    cached_path = None
    before = owner_head = None
    if not ci:
        try:
            before = _test_state(root, isolated)
            owner_head = (_git_run(owner, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.strip()
                          if owner != root else before[0] if before else None)
            if before:
                folder = owner / git(owner, 'rev-parse', '--git-path', 'workorder-tests').stdout.strip()
                key = digest(packed([1, before[1], logical, timeout]).encode('ascii'))
                cached_path = folder / (key + '.json')
                if not fresh:
                    value = json.loads(cached_path.read_text(encoding='utf-8'))
                    if (isinstance(value, dict) and type(value.get('returncode')) is int
                            and isinstance(value.get('stdout'), str) and isinstance(value.get('stderr'), str)):
                        run = subprocess.CompletedProcess(command, value['returncode'], value['stdout'], value['stderr'])
                        run.cached = True
                        return run
        except (OSError, Refused, ValueError):
            pass  # an absent, malformed or unwritable cache must never prevent a run
    if cached_path is not None:
        try:
            cached_path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        run = run_test(command, root, timeout, capture_output=True, isolated=isolated)
    finally:
        invalidate_reads()
    run.cached = False
    if cached_path is not None:
        temporary = cached_path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            if (_test_state(root, isolated) == before
                    and _git_run(owner, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.strip() == owner_head):
                cached_path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(packed(dict(returncode=run.returncode, stdout=run.stdout or '',
                                                 stderr=run.stderr or '')), encoding='utf-8')
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


def receipt(root, tid, kind, command=(), target=None, ref="refs/heads/main", timeout=None, *, use_cache=False):
    repository(root)
    definition = record(root, tid)
    commit = head(root)
    if kind not in KINDS:
        raise Refused("unknown receipt kind")
    if kind in {"implemented", "test"} and not clean_code(root):
        raise Refused("commit the code before recording implementation or tests; "
                      + describe_dirty(dirty_paths(root)))
    data = {"kind": kind, "commit": commit, "code": code_digest(root, commit)}
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
                run = cached_test(command, root, test_timeout(definition, timeout), fresh=not use_cache)
                print(run.stdout or '', end='')
                print(run.stderr or '', end='', file=sys.stderr)
            else:
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
        else:
            valid = valid and bool(re.search(rf"(?<![0-9a-f]){commit}(?![0-9a-f])", output))
        data.update(command=list(command), exit_code=exit_code, valid=valid)
        if kind != "test":
            data.update(target=target, output_sha256=digest(output.encode()))
    with ledger_lock(root, tid):
        return append(root, tid, data)
