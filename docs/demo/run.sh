#!/bin/bash
# Real Workorder 0.17.0 run behind every terminal line in the comic.
# Needs workorder==0.17.0 on PATH (e.g. pip install workorder==0.17.0 in a venv).
# Writes docs/demo/transcript.txt; the comic's terminal lines are copied from it.
set -u
OUT="$(cd "$(dirname "$0")" && pwd)/transcript.txt"
WORK="$(mktemp -d)" && cd "$WORK"
: > "$OUT"
echo "# $(workorder --version)  $(git --version)  $(python3 --version)  dir: $WORK  date: $(date '+%F %H:%M %Z')" >> "$OUT"
run(){ echo "\$ $1" >> "$OUT"; bash -c "$1" >> "$OUT" 2>&1; echo "[exit $?]" >> "$OUT"; echo >> "$OUT"; }
run 'git init -q -b main'
run 'git config user.name "Your Name"'
run 'git config user.email you@example.com'
run 'workorder init'
run "printf '__pycache__/\n' > .gitignore"
run 'workorder new --title "Add calc" --owner you --test "python3 test_calc.py" --code-paths calc.py'
run "python3 - <<'PY'
import pathlib,re
p=pathlib.Path('tasks/TASK-1.md'); s=p.read_text()
s=re.sub(r'(## Scope\n)(.*?)(\n## )', lambda m: m.group(1)+'\nIn: add two numbers with \`add(a, b)\` in calc.py.\nOut: subtraction and other operations.\n'+m.group(3), s, count=1, flags=re.S)
s=re.sub(r'(## Acceptance\n)(.*?)(\n## |\Z)', lambda m: m.group(1)+'\n- \`add(2, 2)\` returns \`4\`.\n'+m.group(3), s, count=1, flags=re.S)
p.write_text(s)
PY"
run 'cat tasks/TASK-1.md'
run 'git add . && git commit -qm "Workorder setup and TASK-1"'
run 'git switch -c add-calc'
run "printf 'def add(a, b):\n    return a - b\n' > calc.py"
run "printf 'from calc import add\n\nassert add(2, 2) == 4\n' > test_calc.py"
run 'git add . && git commit -qm "Add calc"'
run 'workorder done --task TASK-1 --note "add() works; next: subtraction"'
run "printf 'def add(a, b):\n    return a + b\n' > calc.py"
run 'git add . && git commit -qm "Fix add"'
run 'workorder done --task TASK-1 --note "add() works; next: subtraction"'
run 'workorder show --task TASK-1'
run 'git add tasks/ .workorder/ && git commit -qm "Record TASK-1 completion"'
run 'cat tasks/TASK-1.md'
