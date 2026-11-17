#!/usr/bin/env bash
# Re-make docs/workorder-demo.gif from real Workorder output.
#
#   bash docs/demo.sh
#
# Runs the README "Quick start" in a throwaway folder and records it with
# asciinema, then renders the GIF with agg. Every line of output in the GIF
# comes from actually running the commands; only typing is simulated.
#
# Needs: `workorder` (the release being shown), git, python3, asciinema (2.x)
# and agg (https://github.com/asciinema/agg) on PATH.
# Settings (environment): DEMO_DIR (default: a new temporary folder),
# OUT (default: docs/workorder-demo.gif).
set -euo pipefail

if [ "${1:-}" = "--session" ]; then
  # Recorded part. Runs inside the throwaway repository.
  type_cmd() {  # print a prompt and type the command, then run it for real
    printf '$ '
    local s="$1" i
    for ((i = 0; i < ${#s}; i++)); do printf '%s' "${s:i:1}"; sleep 0.012; done
    sleep 0.15; printf '\n'
    eval "$1"
    sleep "${2:-0.8}"
  }
  edit_task() {  # the README's hand edit of tasks/TASK-1.md (Scope and Acceptance)
    python3 - <<'PY'
import pathlib, re
p = pathlib.Path("tasks/TASK-1.md")
t = p.read_text()
t = re.sub(r"(## Scope\n\n)<!--.*?-->", lambda m: m.group(1)
           + "In: add two numbers with `add(a, b)` in calc.py.\n"
           + "Out: subtraction and other operations.", t, count=1, flags=re.S)
t = re.sub(r"(## Acceptance\n\n)<!--.*?-->", lambda m: m.group(1)
           + "- `add(2, 2)` returns `4`.", t, count=1, flags=re.S)
p.write_text(t)
PY
  }
  sleep 0.5
  type_cmd 'workorder init' 3.5
  type_cmd "printf '__pycache__/\\n' > .gitignore" 0.4
  type_cmd 'workorder new --title "Add calc" --owner you --test "python3 test_calc.py" --code-paths calc.py' 1.5
  type_cmd '# edit tasks/TASK-1.md: Scope and Acceptance' 0.2
  edit_task
  type_cmd 'git add . && git commit -qm "Workorder setup and TASK-1"' 0.8
  type_cmd 'git switch -c add-calc' 0.4
  type_cmd "printf 'def add(a, b):\\n    return a + b\\n' > calc.py" 0.2
  type_cmd "printf 'from calc import add\\n\\nassert add(2, 2) == 4\\n' > test_calc.py" 0.2
  type_cmd 'git add . && git commit -qm "Add calc"' 0.8
  type_cmd 'workorder done --task TASK-1 --note "add() works; next: subtraction"' 4.5
  type_cmd 'workorder show --task TASK-1' 6.5
  type_cmd 'git add tasks/ .workorder/ && git commit -qm "Record TASK-1 completion"' 1
  exit 0
fi

repo=$(cd "$(dirname "$0")/.." && pwd)
script="$repo/docs/demo.sh"
out=$(realpath -m "${OUT:-$repo/docs/workorder-demo.gif}")
dir=${DEMO_DIR:-$(mktemp -d)}
rm -rf "$dir/demo"
mkdir -p "$dir/demo"
cd "$dir/demo"

# Off camera: the README's setup lines.
git init -q -b main
git config user.name "Your Name"
git config user.email you@example.com

cast="$dir/demo.cast"
PS1='$ ' asciinema rec --overwrite -q --cols 100 --rows 36 -i 8 \
  -c "bash '$script' --session" "$cast"
agg -q --font-family "DejaVu Sans Mono,Liberation Mono" --font-size 16 \
  --theme monokai --idle-time-limit 8 --last-frame-duration 3 \
  "$cast" "$out"
echo "wrote $out"
