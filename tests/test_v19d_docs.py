"""Lane D2 (0.19): the README tells continuity first, the normal one-branch workflow, and what DONE is not."""

import os
import re
import shutil
import subprocess

import pytest

from test_workorder import PACKAGE, PYTHON, cli, git
from test_v18d_docs import MERMAID


README = (PACKAGE / 'README.md').read_text(encoding='utf-8')
REFERENCE = (PACKAGE / 'docs/reference.md').read_text(encoding='utf-8')


def quick_start():
    return README.split('## Quick start', 1)[1].split('\n## Language support', 1)[0]


def in_order(text, phrases):
    positions = [text.find(phrase) for phrase in phrases]
    return -1 not in positions and positions == sorted(positions), dict(zip(phrases, positions))


def test_readme_leads_with_continuity_then_proof():
    overview = README[:README.index('```mermaid')]
    # Continuity (where it stopped, what remains, recovered by show) comes before the proof of DONE.
    ordered, positions = in_order(overview, ['ends mid-task', 'where it stopped', 'what remains',
                                             'same structure', 'workorder show', 'handoffs', 'DONE'])
    assert ordered, positions
    assert 'DONE' not in overview.split('\n\n')[1], 'the first paragraph is about continuity alone'


def test_flow_chart_tells_interruption_recovery_refusal_proof_done():
    chart = MERMAID.findall(README)[0]
    ordered, positions = in_order(chart, ['ends mid-task', 'A new session picks it up', 'workorder show',
                                          'same structure', 'finishes the work', 'claims DONE', 'Refused',
                                          'adds the proof', 'DONE, with receipts', 'pull request'])
    assert ordered, positions


def test_done_is_a_verified_test_result_not_acceptance():
    top = README.split('## Quick start', 1)[0]
    assert '**A verified test result is not a verified task.**' in top
    assert "whether the task was done right is a person's acceptance" in top
    assert '`workorder accept`' in top


def test_quick_start_is_one_branch_and_one_pull_request():
    text = quick_start()
    ordered, positions = in_order(text, ['git switch -c add-calc', 'workorder new', 'git commit -qm "TASK-1: add calc"',
                                         'workorder done --task TASK-1', 'git add tasks/ .workorder/',
                                         'git push -qu origin add-calc', 'workorder check --base origin/main'])
    assert ordered, positions
    assert 'Land the task on main' not in text
    assert 'an approving review or the label `workorder-allow:TASK-1`' in text
    assert 'solo mode (`workorder init --solo`)' in text
    # Landing the task first stays an option in the reference.
    assert '(docs/reference.md#task-first-development)' in text
    first = REFERENCE.split('## Task-first development', 1)[1].split('\n## ', 1)[0]
    assert 'landing it first avoids that' in first and 'push\n   it to the default branch' in first


def table(text):
    return [line for line in text.splitlines() if line.startswith('| ') and not line.startswith('| Finding')]


def test_language_support_short_in_readme_full_in_reference():
    short = README.split('## Language support', 1)[1].split('\n## ', 1)[0]
    full = REFERENCE.split('## Language support', 1)[1].split('\n## ', 1)[0]
    assert '[Language support](#language-support)' in REFERENCE.split('## ', 1)[0]
    for text in (short, full):
        assert 'Python-only today' in text
        assert table(text)[0].split('|')[2].strip() == 'Python with pytest'
        assert 'Yes, by exit code' in text and '--test-paths' in text
    assert 'Other test commands' in short and 'Any other test command' in full
    assert 5 <= len(table(short)) - 1 < len(table(full)) - 1
    assert '[full table](docs/reference.md#language-support)' in short


def write_task_sections(path, scope, acceptance):
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'(## Scope\n).*?(\n## )', lambda m: m[1] + '\n' + scope + '\n' + m[2], text, flags=re.S)
    text = re.sub(r'(## Acceptance\n).*?(\n## )', lambda m: m[1] + '\n' + acceptance + '\n' + m[2], text, flags=re.S)
    path.write_text(text, encoding='utf-8')


def shell_environment(tmp_path):
    """A `workorder` on PATH that runs this checkout, as pipx would put it there."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    shim = bin_dir / 'workorder'
    shim.write_text(f'#!/bin/sh\nexec "{PYTHON}" -m workorder "$@"\n', encoding='utf-8')
    shim.chmod(0o755)
    return dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'], PYTHONPATH=str(PACKAGE),
                GIT_TERMINAL_PROMPT='0')


def sh(script, cwd, env):
    result = subprocess.run(['sh', '-ec', script], cwd=cwd, env=env, capture_output=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


needs_sh = pytest.mark.skipif(os.name == 'nt' or not shutil.which('sh') or not shutil.which('python3'),
                              reason='runs the README shell lines with sh and python3')


@needs_sh
def test_readme_quick_start_runs_as_written(tmp_path):
    env = shell_environment(tmp_path)
    blocks = re.findall(r'```(sh|markdown)\n(.*?)```', quick_start(), re.S)
    assert [kind for kind, _ in blocks] == ['sh', 'sh', 'markdown', 'sh']
    sh(blocks[0][1] + blocks[1][1], tmp_path, env)
    demo = tmp_path / 'demo'
    scope, acceptance = blocks[2][1].split('## Acceptance')
    write_task_sections(demo / 'tasks/TASK-1.md', scope.replace('## Scope', '').strip(), acceptance.strip())
    output = sh(blocks[3][1], demo, env)
    assert 'DONE  TASK-1' in output
    assert git(demo, 'status', '--porcelain') == ''
    assert git(demo, 'rev-parse', 'HEAD') == git(demo, 'rev-parse', 'origin/add-calc')
    command = re.search(r'`workorder (check --base origin/main[^`]*)`', quick_start())[1].split()
    check = cli(demo, *command)
    assert check.returncode == 1, check.stdout
    assert ('TASK-1 was written and finished in this pull request; an approving review clears this'
            in check.stdout) and 'workorder-allow:TASK-1' in check.stdout
    # The quick start mentions nothing else for the pull request to clear.
    asks = [line for line in check.stdout.splitlines()
            if line.strip() and not line.startswith(('Note:', 'Time:', 'checked ', '('))]
    assert len(asks) == 1 and asks[0].startswith('TASK-1 was written'), check.stdout
    approved = cli(demo, *command, '--approving-review')
    assert approved.returncode == 0, approved.stdout + approved.stderr


@needs_sh
@pytest.mark.parametrize('case', ['passes-on-base', 'imports-only', 'tied-acceptance', 'supported'])
def test_other_test_commands_get_what_the_language_table_says(tmp_path, case):
    """`sh test.sh`: exit code now, the whole command on the base, stubs for Python code, no tied items."""
    env = shell_environment(tmp_path)
    sh('git init -q --bare origin.git && mkdir demo && cd demo && git init -q -b main && '
       'git config user.name You && git config user.email you@example.com && '
       'git remote add origin ../origin.git && workorder init >/dev/null && '
       "printf '__pycache__/\\n' > .gitignore && printf 'echo base\\n' > base.sh && "
       'git add . && git commit -qm setup && git push -qu origin main && git switch -qc work', tmp_path, env)
    demo = tmp_path / 'demo'
    code = 'calc.py' if case == 'imports-only' else 'greet.sh'
    sh(f'workorder new --title Work --owner you --test "sh test.sh" --test-paths test.sh --code-paths {code}',
       demo, env)
    item = '- It works.' + (' (test: test.sh::greets)' if case == 'tied-acceptance' else '')
    write_task_sections(demo / 'tasks/TASK-1.md', 'In: the change.', item)
    if case == 'imports-only':
        (demo / 'calc.py').write_text('def add(a, b):\n    return a + b\n')
        (demo / 'test.sh').write_text('python3 -c "import calc"\n')
    else:
        (demo / 'greet.sh').write_text('echo hello\n')
        test = '[ "$(sh base.sh)" = base ]\n' if case == 'passes-on-base' else '[ "$(sh greet.sh)" = hello ]\n'
        (demo / 'test.sh').write_text(test)
    sh('git add . && git commit -qm work', demo, env)
    done = cli(demo, 'done', '--task', 'TASK-1')
    output = done.stdout + done.stderr
    expected = {'passes-on-base': "its test passes on origin/main's code too",
                'imports-only': 'its test still passes with the changed functions stubbed out (add in calc.py)',
                'tied-acceptance': '(test: test.sh::greets) is not a pytest node id',
                'supported': 'DONE  TASK-1'}[case]
    assert expected in output, output
    assert done.returncode == (0 if case == 'supported' else 1), output
