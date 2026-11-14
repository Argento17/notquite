"""Local files and immutable remote reservations. No hosting-service API needed."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import heapq
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import uuid


class Refused(ValueError):
    pass


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
    result = subprocess.run(
        ["git", *args], cwd=root, input=data, capture_output=True,
        encoding="utf-8", errors="strict", timeout=30,
    )
    if required and result.returncode:
        raise Refused(result.stderr.strip() or "Git command failed")
    return result


def who(root):
    # Only the Git user name: receipts and claims are pushed, and a hostname says more than needed.
    name = git(root, "config", "user.name", required=False).stdout.strip()
    if not name:
        raise Refused("set git user.name first")
    return name


def head(root):
    return git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()


def repository(root):
    top = Path(git(root, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if top != root.resolve():
        raise Refused("run workorder at the Git repository root")


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
    return read_bytes(root, relative, staged).decode("utf-8").removeprefix("\ufeff").replace("\r\n", "\n")


def files(root, directory, staged=False):
    if staged:
        paths = git(root, "ls-files", "-z", "--", directory).stdout.split("\0")
        return sorted(p for p in paths if p)
    base = local_path(root, directory)
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())


# CANCELLED ends a task that will not be done: it needs no evidence and never counts as finished.
STATUSES = ("IN_PROGRESS", "BLOCKED", "DONE", "CLOSED", "CANCELLED")
CORE_FIELDS = ("id", "title", "owner", "status", "ships", "waits_on", "contract")
# Optional fields of the extended template. Only depends_on and test affect a verdict, and only by
# adding requirements (dependencies; the one test command that counts); the rest is information for
# people and never stands in for evidence.
EXTENDED_FIELDS = ("business_owner", "priority", "created_at", "mission", "depends_on", "blocks",
                   "next", "summary", "test", "merge_ref")
EXTENDED_ORDER = ("id", "title", "owner", "business_owner", "priority", "created_at", "mission",
                  "depends_on", "blocks", "next", "summary", "status", "ships", "waits_on", "test",
                  "contract")
TASK_REF = r"[A-Z][A-Z0-9]*-[1-9][0-9]*"
FRONT_MATTER_HELP = ("front matter takes `key: value` lines: plain single-line text, a JSON string, or a"
                     ' list such as ["TASK-1"]; mission, next and summary may use > or | blocks, and'
                     " depends_on, blocks and waits_on may use indented - item lines")
# The small YAML subset Workorder reads. Anything else is refused rather than guessed.
BLOCK_TEXT_FIELDS = ("mission", "next", "summary")
LIST_FIELDS = ("depends_on", "blocks", "waits_on")
BLOCK_INDICATORS = (">", "|", ">-", "|-", ">+", "|+")
PLAIN_ITEM = r"[A-Za-z0-9_.@:-]+"


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
        if not isinstance(item, str) or not re.fullmatch(PLAIN_ITEM, item):
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


def remote_refs(root):
    if not has_origin(root):
        raise Refused("this needs an origin remote: only a shared remote can refuse a second"
                      " holder, so claims are never kept locally")
    output = git(root, "ls-remote", "--refs", "origin", NAMESPACE + "*").stdout
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

<!-- Dated notes: workorder note adds one, workorder show prints them. Status comes from receipts, not from here. -->
"""


def task_text(tid, data, template):
    if template == "simple":
        return ("---\n" + "".join(f"{k}: {packed(v)}\n" for k, v in data.items())
                + f"---\n\n# {tid}\n\nWrite the original ask here.\n")
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    extended = {**dict(business_owner="", priority="", created_at=today, mission="", depends_on=[],
                       blocks=[], next="", summary="", test=""), **data}
    return ("---\n" + "".join(f"{k}: {packed(extended[k])}\n" for k in EXTENDED_ORDER)
            + f"---\n\n# {tid}\n" + EXTENDED_BODY)


def new(root, title, owner, ships="none", prefix="TASK", template="extended", test="", local_only=False):
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
                    waits_on=[], contract="none", **({"test": test} if test else {}))
        if local or reserve(root, NAMESPACE + f"ids/{tid}", {**data, "at": now(), "who": who(root)}):
            path = directory / f"{tid}.md"
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(task_text(tid, data, template))
            return tid
    raise Refused("too much allocation contention; retry")


def active_claims(root):
    refs = remote_refs(root)
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
    for start in range(0, len(wanted), 200):
        git(root, "fetch", "--no-tags", "origin", *wanted[start:start + 200], required=False)
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


def claim(root, tid):
    repository(root)
    record(root, tid)
    held = active_claims(root).get(tid)
    token_path = claim_token_path(root, tid)
    token = token_path.read_text(encoding="utf-8") if token_path.exists() else uuid.uuid4().hex
    if held and not held["released"]:
        if holds(held, token):
            return held
        raise Refused(f"{tid} is held by {held['who']} since {held['at']} - not claimed")
    generation = held["generation"] + 1 if held else 1
    # Only the hash is pushed; the token itself stays in this clone's .git directory.
    value = {"task": tid, "who": who(root), "at": now(), "token_sha256": token_hash(token)}
    # Save before pushing: a crash can leave a recoverable claim, never a lost key.
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8", newline="\n")
    if not reserve(root, NAMESPACE + f"claims/{tid}/{generation}", value):
        winner = active_claims(root).get(tid)
        raise Refused(f"{tid} was claimed by {winner['who']} first - not claimed")
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
    reserve(root, NAMESPACE + f"releases/{tid}/{held['oid']}", value)
    return not holder


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


def add_note(root, tid, text):
    """Add "- YYYY-MM-DD: text" at the end of the task's Progress section (creating it if needed).
    This is the one change Workorder makes to a task file after new; every other byte is kept."""
    record(root, tid)
    text = " ".join(text.split())
    if not text:
        raise Refused("a note needs some text")
    path = local_path(root, f"tasks/{tid}.md")
    lines = read_text(root, f"tasks/{tid}.md").split("\n")
    line = f"- {dt.datetime.now(dt.timezone.utc).date().isoformat()}: {text}"
    starts = [i for i, value in enumerate(lines) if value.strip() == "## Progress"]
    if starts:
        end = next((i for i in range(starts[-1] + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        while end > starts[-1] + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = [line] if lines[end - 1].strip() and not lines[end - 1].startswith("## ") else ["", line]
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", "## Progress", "", line]
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
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


def receipt_paths(root, staged=False, rev=None, tid=None):
    """Receipt files of every task, or of one task (its legacy ledger and its folder) when tid is given."""
    scopes = ([f"{d}/{tid}.jsonl" for d in RECEIPT_DIRS] + [f"{d}/{tid}" for d in RECEIPT_DIRS]
              if tid else list(RECEIPT_DIRS))
    if rev:
        return git(root, "ls-tree", "-r", "--name-only", rev, "--", *scopes,
                   required=False).stdout.splitlines()
    if staged:
        return sorted(p for p in git(root, "ls-files", "-z", "--", *scopes).stdout.split("\0") if p)
    found = []
    for scope in scopes:
        path = local_path(root, scope)
        if path.is_file():
            found.append(scope)
        elif path.is_dir():
            found += files(root, scope)
    return sorted(found)


def receipt_texts(root, tid, staged=False, rev=None):
    return {p: (git(root, "show", f"{rev}:{p}").stdout.replace("\r\n", "\n") if rev
                else read_text(root, p, staged))
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
            except FileExistsError:
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
    return digest("\0".join(code).encode())


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


def receipt(root, tid, kind, command=(), target=None, ref="refs/heads/main"):
    repository(root)
    record(root, tid)
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
            run = subprocess.run(list(command), cwd=root, capture_output=kind != "test",
                                 encoding="utf-8", errors="replace")
            exit_code = run.returncode
            output = run.stdout or ""
        except OSError as error:
            exit_code, output = 127, str(error)
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
