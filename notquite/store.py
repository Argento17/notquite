"""Local files and immutable remote reservations. No hosting-service API needed."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess
import uuid


class Refused(ValueError):
    pass


ID = re.compile(r"[A-Z][A-Z0-9]*-[1-9][0-9]*\Z")
KINDS = {"implemented", "test", "merged", "deployed", "live"}
NAMESPACE = "refs/tags/notquite/"


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
        raise Refused("run notquite at the Git repository root")


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
    return read_bytes(root, relative, staged).decode("utf-8").replace("\r\n", "\n")


def files(root, directory, staged=False):
    if staged:
        paths = git(root, "ls-files", "-z", "--", directory).stdout.split("\0")
        return sorted(p for p in paths if p)
    base = local_path(root, directory)
    return sorted(p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file())


CORE_FIELDS = ("id", "title", "owner", "status", "ships", "waits_on", "contract")
# Optional fields of the extended template. Only depends_on affects a verdict, and only by adding
# dependencies; the rest is information for people and never stands in for evidence.
EXTENDED_FIELDS = ("business_owner", "priority", "created_at", "mission", "depends_on", "blocks",
                   "next", "summary")
EXTENDED_ORDER = ("id", "title", "owner", "business_owner", "priority", "created_at", "mission",
                  "depends_on", "blocks", "next", "summary", "status", "ships", "waits_on", "contract")
TASK_REF = r"[A-Z][A-Z0-9]*-[1-9][0-9]*"
FRONT_MATTER_HELP = ("front matter takes `key: value` lines: plain single-line text, a JSON string, or a"
                     ' list such as ["TASK-1"]; mission, next and summary may use > or | blocks, and'
                     " depends_on, blocks and waits_on may use indented - item lines")
# The small YAML subset NotQuite reads. Anything else is refused rather than guessed.
BLOCK_TEXT_FIELDS = ("mission", "next", "summary")
LIST_FIELDS = ("depends_on", "blocks", "waits_on")
BLOCK_INDICATORS = (">", "|", ">-", "|-", ">+", "|+")
PLAIN_ITEM = r"[A-Za-z0-9_.@:-]+"


def front_value(tid, key, raw):
    """Parse one value: plain text, a JSON string or list, or a simple [a, b] list."""
    raw = raw.strip()
    if not raw.startswith(('"', '[')):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        pass
    inner = raw[1:-1].strip() if raw.startswith("[") and raw.endswith("]") else None
    items = [item.strip() for item in inner.split(",")] if inner else []
    if inner is None or any(not re.fullmatch(PLAIN_ITEM, item) for item in items):
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
        match = re.fullmatch(r" +- +(.+?)\s*", line)
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
        while end < len(lines) and (not lines[end].strip() or lines[end].startswith(" ")):
            end += 1
        block = lines[index + 1:end]
        index = end
        if not line.strip():
            continue
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
    text = read_text(root, f"tasks/{tid}.md", staged)
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise Refused(f"{tid}: expected fenced front matter")
    fields = front_matter(tid, lines[1:lines.index("---", 1)])
    missing = [k for k in CORE_FIELDS if k not in fields]
    unknown = sorted(set(fields) - set(CORE_FIELDS) - set(EXTENDED_FIELDS))
    if missing or unknown:
        raise Refused(f"{tid}: " + "; ".join(
            ([f"missing {', '.join(missing)}"] if missing else [])
            + ([f"unknown {', '.join(unknown)} (known optional fields: {', '.join(EXTENDED_FIELDS)})"]
               if unknown else [])))
    if fields["id"] != tid:
        raise Refused(f"{tid}: id must match the file name")
    if any(not isinstance(fields[k], str) or not fields[k].strip()
           for k in set(CORE_FIELDS) - {"waits_on"}):
        raise Refused(f"{tid}: text fields must be nonempty strings")
    if fields["status"] not in {"IN_PROGRESS", "BLOCKED", "DONE", "CLOSED"}:
        raise Refused(f"{tid}: unknown status")
    if fields["ships"] not in {"none", "repo", "live"}:
        raise Refused(f"{tid}: ships must be none, repo or live")
    waits = fields["waits_on"]
    if not isinstance(waits, list) or any(
        not isinstance(w, str) or not re.fullmatch(rf"(?:task:{TASK_REF}|person:[^\s:]+)", w)
        for w in waits
    ):
        raise Refused(f"{tid}: waits_on needs task:<id> or person:<name> items")
    for key in ("business_owner", "priority", "created_at", "mission", "next", "summary"):
        if key in fields and not isinstance(fields[key], str):
            raise Refused(f"{tid}: {key} must be text")
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

<!-- The original ask, criterion by criterion. Evidence NotQuite should check belongs in a contract. -->

## Progress

<!-- Dated notes. NotQuite does not read this section: status comes from receipts. -->
"""


def task_text(tid, data, template):
    if template == "simple":
        return ("---\n" + "".join(f"{k}: {packed(v)}\n" for k, v in data.items())
                + f"---\n\n# {tid}\n\nWrite the original ask here.\n")
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    extended = dict(business_owner="", priority="", created_at=today, mission="", depends_on=[],
                    blocks=[], next="", summary="", **data)
    return ("---\n" + "".join(f"{k}: {packed(extended[k])}\n" for k in EXTENDED_ORDER)
            + f"---\n\n# {tid}\n" + EXTENDED_BODY)


def new(root, title, owner, ships="repo", prefix="TASK", template="simple"):
    repository(root)
    if template not in {"simple", "extended"}:
        raise Refused("template must be simple or extended")
    if not re.fullmatch(r"[A-Z][A-Z0-9]*", prefix) or not title.strip() or not owner.strip():
        raise Refused("use an uppercase prefix, nonempty title and owner")
    directory = local_path(root, "tasks")
    directory.mkdir(exist_ok=True)
    # Without an origin remote, ids are numbered locally and not coordinated with other clones.
    # With one, it must answer: never guess offline.
    local = not has_origin(root)
    for _ in range(30):
        refs = {} if local else remote_refs(root)
        stem = NAMESPACE + f"ids/{prefix}-"
        numbers = [int(ref[len(stem):]) for ref in refs
                   if ref.startswith(stem) and ref[len(stem):].isdigit()]
        numbers += [int(p.stem.split("-")[-1]) for p in directory.glob(f"{prefix}-*.md")
                    if ID.fullmatch(p.stem)]
        tid = f"{prefix}-{max(numbers, default=0) + 1}"
        data = dict(id=tid, title=title, owner=owner, status="IN_PROGRESS", ships=ships,
                    waits_on=[], contract="none")
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
            tid, generation = ref[len(stem):].split("/")
            task_id(tid)
            if int(generation) > latest.get(tid, (0, "", ""))[0]:
                latest[tid] = (int(generation), ref, oid)
    result = {}
    for tid, (generation, ref, oid) in latest.items():
        released = NAMESPACE + f"releases/{tid}/{oid}" in refs
        result[tid] = {**remote_value(root, ref, oid), "generation": generation,
                       "oid": oid, "released": released}
    return result


def claim_token_path(root, tid):
    relative = git(root, "rev-parse", "--git-path", f"notquite-claims/{tid}").stdout.strip()
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


def release(root, tid):
    repository(root)
    task_id(tid)
    held = active_claims(root).get(tid)
    path = claim_token_path(root, tid)
    if not held or held["released"]:
        raise Refused(f"{tid} has no active claim")
    if not path.exists() or not holds(held, path.read_text(encoding="utf-8")):
        raise Refused(f"{tid}: only its holder can release it")
    reserve(root, NAMESPACE + f"releases/{tid}/{held['oid']}", {"at": now(), "who": who(root)})


def ledger_path(tid):
    return f".notquite/receipts/{task_id(tid)}.jsonl"


def entries(root, tid, staged=False):
    path = ledger_path(tid)
    available = files(root, ".notquite/receipts", staged)
    text = read_text(root, path, staged) if path in available else ""
    # Existing committed receipts are an immutable prefix, including during staging.
    old = git(root, "show", f"HEAD:{path}", required=False)
    if old.returncode == 0 and not text.startswith(old.stdout):
        raise Refused(f"{tid}: ledger is append-only")
    result, previous = [], ""
    for line in text.splitlines():
        item = json.loads(line)
        if not isinstance(item, dict):
            raise Refused(f"{tid}: receipt must be an object")
        checksum = item.pop("hash", None)
        if checksum != digest(packed(item).encode()) or item.get("previous") != previous:
            raise Refused(f"{tid}: broken receipt chain")
        if (item.get("task") != tid or item.get("kind") not in KINDS
                or not isinstance(item.get("who"), str) or not item["who"].strip()
                or not isinstance(item.get("at"), str)
                or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(item.get("commit", "")))
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("code", "")))):
            raise Refused(f"{tid}: invalid receipt fields")
        dt.datetime.fromisoformat(item["at"])
        if code_digest(root, item["commit"]) != item["code"]:
            raise Refused(f"{tid}: receipt code does not match its commit")
        if item["kind"] in {"test", "deployed", "live"}:
            if (type(item.get("exit_code")) is not int or type(item.get("valid")) is not bool
                    or not isinstance(item.get("command"), list) or not item["command"]
                    or any(not isinstance(a, str) for a in item["command"])):
                raise Refused(f"{tid}: command receipt needs its arguments, exit code and validity")
        item["hash"] = checksum
        result.append(item)
        previous = checksum
    return result


def append(root, tid, item):
    history = entries(root, tid)
    data = {"task": tid, "at": now(), "who": who(root),
            "previous": history[-1]["hash"] if history else "", **item}
    data["hash"] = digest(packed(data).encode())
    path = local_path(root, ledger_path(tid))
    path.parent.mkdir(parents=True, exist_ok=True)
    # One O_APPEND write per receipt. A same-task concurrent writer is refused by the chain checker.
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(packed(data) + "\n")
    return data


CODE_PATHS = (".", ":(exclude)tasks", ":(exclude).notquite")


def code_digest(root, commit):
    listing = git(root, "ls-tree", "-r", "-z", commit, required=False)
    if listing.returncode:
        raise Refused(f"commit {commit[:7]} is not in this clone; fetch the full history"
                      " (in GitHub Actions: actions/checkout with fetch-depth: 0)")
    tree = listing.stdout
    code = [entry for entry in tree.split("\0") if entry
            and not entry.partition("\t")[2].startswith(("tasks/", ".notquite/"))]
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
    return append(root, tid, data)
