"""Exercise Workorder with installed upstream projects in disposable clones.

The network setup is deliberately separate from the case runner, so local
mirrors and an already prepared interpreter can exercise the same cases offline.
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
import venv


PROJECTS = {
    'click': ('https://github.com/pallets/click.git', 'e38188e9e205005d5af5f9e59df51904c984586a', 'src/click'),
    # attrs 25.3.0. ATTRS_COMMIT can supply another reviewed full SHA without editing the runner.
    'attrs': ('https://github.com/python-attrs/attrs.git', os.environ.get('ATTRS_COMMIT', '3dca08ce5bbe673d7df25f44f3dda92505d1043d'), 'src/attr'),
}
CASES = ('feature', 'bugfix', 'refactor', 'assert-true', 'expected-value', 'skip')
CLICK_CASES = ('shared-file-callable', 'shared-file-honest', 'shared-folder-second-task')


def run(command, repo, log, env, expected=0):
    result = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
    with log.open('a', encoding='utf-8') as stream:
        stream.write('$ ' + shlex.join(map(str, command)) + '\n')
        stream.write(result.stdout + result.stderr + f'\n[exit {result.returncode}]\n')
    if result.returncode != expected:
        raise RuntimeError(f'expected exit {expected}, got {result.returncode}: {shlex.join(map(str, command))}\n'
                           f'{result.stdout}{result.stderr}\nLog: {log}')
    return result.stdout


def git(repo, log, env, *args):
    return run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false', *args], repo, log, env)


def commit(repo, log, env, title):
    git(repo, log, env, 'add', '-A')
    git(repo, log, env, 'commit', '-qm', title)


def replace(path, old, new):
    text = path.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise RuntimeError(f'expected one occurrence of {old!r} in {path}')
    path.write_text(text.replace(old, new), encoding='utf-8')


def click_shared_case(repo, log, env, python, case, commit_id):
    """Append tests to Click's own shared file, retaining all its existing tests."""
    wo = [sys.executable, '-m', 'workorder']
    source = repo / 'src/click/types.py'
    tests = repo / 'tests/test_basic.py'
    original_tests = tests.read_text(encoding='utf-8')
    test_command = [python, '-m', 'pytest', '-q', 'tests/test_basic.py']

    def define_task(task_id, command, title, scope, acceptance):
        run(wo + ['new', '--local', '--title', title, '--owner', 'example', '--ships', 'none',
                  '--test', shlex.join(command), '--code-paths', 'src/click/types.py'], repo, log, env)
        definition = repo / f'tasks/{task_id}.md'
        replace(definition, '## Scope\n', f'\n## Scope\n\n{scope}\n')
        replace(definition, '## Acceptance\n', f'\n## Acceptance\n\n- {acceptance}\n')
        return definition

    def start_branch(name):
        git(repo, log, env, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        git(repo, log, env, 'checkout', '-qb', name)

    task_id = 'TASK-1'
    if case == 'shared-folder-second-task':
        # The finished task owns the folder, including the shared file that
        # TASK-2 will extend. Its existing tests and definition stay intact.
        first = define_task('TASK-1', [python, '-m', 'pytest', '-q', 'tests', '-k', 'boolean'],
                            'click: simplify boolean normalization',
                            'Name the normalized boolean input before looking up its value.',
                            'Existing boolean options keep their behavior.')
        # A refactor is planned as one: its kind lands with the definition.
        replace(first, '\ncontract:', '\nkind: refactor\ncontract:')
        commit(repo, log, env, 'Define the first task before its implementation')
        start_branch('first-task')
        replace(source, '        return BoolParamType.bool_states.get(value.strip().lower())',
                '        normalized = value.strip().lower()\n'
                '        return BoolParamType.bool_states.get(normalized)')
        commit(repo, log, env, 'Name the normalized boolean input')
        finished = run(wo + ['done', '--task', 'TASK-1', '--refactor'], repo, log, env)
        if 'TASK-1 DONE:' not in finished:
            raise RuntimeError(f'click/{case} did not finish the first task: {log}')
        commit(repo, log, env, 'Record the finished first task')
        git(repo, log, env, 'checkout', '-q', 'main')
        git(repo, log, env, 'merge', '--ff-only', 'first-task')
        task_id = 'TASK-2'

    definition = define_task(task_id, test_command, f'click: {case}',
                             'Accept enable as a true value for a boolean option.',
                             'A boolean option accepts enable, ignoring case and surrounding whitespace.')
    # Definitions land first: writing and finishing a task in one pull request
    # requires reviewer acceptance, which these cases do not request.
    commit(repo, log, env, 'Define the task before adding its boolean option behavior')
    start_branch('workorder-example')
    replace(source, 'value.strip().lower()', '_normalize_bool_word(value)')
    replace(source, '\n\nclass BoolParamType(',
            '\n\ndef _normalize_bool_word(value: str) -> str:\n'
            '    return value.strip().lower()\n\n\nclass BoolParamType(')
    replace(source, '        "on": True,\n', '        "on": True,\n        "enable": True,\n')
    honest = case != 'shared-file-callable'
    if honest:
        added_test = ('\n\ndef test_workorder_enable_boolean(runner):\n'
                      '    @click.command()\n'
                      '    @click.option("--flag", type=bool)\n'
                      '    def cli(flag):\n'
                      '        click.echo(flag, nl=False)\n\n'
                      '    result = runner.invoke(cli, ["--flag", " ENABLE "])\n'
                      '    assert result.exit_code == 0\n'
                      '    assert result.output == "True"\n')
    else:
        added_test = ('\n\ndef test_workorder_boolean_callable():\n'
                      '    from click.types import _normalize_bool_word\n\n'
                      '    assert callable(_normalize_bool_word)\n')
    tests.write_text(original_tests + added_test, encoding='utf-8')
    commit(repo, log, env, case)
    done = run(wo + ['done', '--task', task_id], repo, log, env, expected=0 if honest else 1)
    if honest and f'{task_id} DONE:' not in done:
        raise RuntimeError(f'click/{case} did not reach DONE: {log}')
    reason = 'its new or changed tests still pass with the changed functions stubbed out'
    if not honest:
        if reason not in done or 'test_workorder_boolean_callable' not in done:
            raise RuntimeError(f'click/{case} did not refuse the callable-only test: {log}')
        if 'status: "IN_PROGRESS"' not in definition.read_text(encoding='utf-8'):
            raise RuntimeError(f'click/{case} changed status after refusing DONE: {log}')
        run(wo + ['renew', '--task', task_id], repo, log, env)
        replace(definition, 'status: "IN_PROGRESS"', 'status: "DONE"')
    commit(repo, log, env, 'Record the completion claim for the pull-request check')
    checked = run(wo + ['check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task'],
                  repo, log, env, expected=0 if honest else 1)
    if not honest and (reason not in checked or 'test_workorder_boolean_callable' not in checked):
        raise RuntimeError(f'click/{case} did not refuse the callable-only completion claim: {log}')
    if honest and 'workorder-allow:' in done + checked:
        raise RuntimeError(f'click/{case} unexpectedly asked for a reviewer label: {log}')
    print(f'click/{case}: ' + ('DONE' if honest else 'refused'), flush=True)
    return dict(project='click', case=case, commit=commit_id, done=honest, log=str(log))


def cases(seed, commit_id, project, package_path, output, env, prepared_python=None):
    """Run the project's cases against pinned source; prepared_python allows offline checks."""
    results = []
    package = package_path.split('/')[-1]
    wo = [sys.executable, '-m', 'workorder']
    for case in CASES + (CLICK_CASES if project == 'click' else ()):
        folder = output / f'{project}-{case}'
        folder.mkdir()
        log = folder / 'commands.log'
        repo = folder / 'repo'
        git(output, log, env, 'clone', '--quiet', '--no-hardlinks', str(seed), str(repo))
        git(repo, log, env, 'checkout', '--quiet', '-B', 'main', commit_id)
        git(repo, log, env, 'config', 'user.name', 'Workorder examples')
        git(repo, log, env, 'config', 'user.email', 'examples@example.invalid')
        # No task reservation or show command should contact upstream.
        git(repo, log, env, 'remote', 'remove', 'origin')
        if prepared_python:
            python = str(prepared_python)
        else:
            environment = folder / 'venv'
            venv.EnvBuilder(with_pip=True).create(environment)
            python = str(environment / 'bin/python')
            run([python, '-m', 'pip', 'install', '--upgrade', 'pip>=25.1'], repo, log, env)
            data = tomllib.loads((repo / 'pyproject.toml').read_text())
            groups = data.get('dependency-groups', {})
            extras = data.get('project', {}).get('optional-dependencies', {})
            group = next((name for name in ('test', 'tests') if name in groups), None)
            extra = next((name for name in ('test', 'tests') if name in extras), None)
            install = [python, '-m', 'pip', 'install', '-e', f'.[{extra}]' if extra else '.', 'pytest']
            if group:
                install += ['--group', group]
            run(install, repo, log, env)
        # Verify the README-style editable install before Workorder adjusts paths.
        probe_env = {key: value for key, value in env.items() if key != 'PYTHONPATH'}
        location = run([python, '-c', f'import {package}; print({package}.__file__)'], folder, log, probe_env).strip()
        if not prepared_python and not Path(location).resolve().is_relative_to(repo.resolve()):
            raise RuntimeError(f'{project} editable install resolves outside its clone: {location}')
        # As in an activated virtual environment: the detected project test's plain
        # `python -m pytest -q` runs with this project's interpreter.
        case_env = {**env, 'PATH': str(Path(python).parent) + os.pathsep + env.get('PATH', '')}
        run(wo + ['init'], repo, log, case_env)
        if case in CLICK_CASES:
            results.append(click_shared_case(repo, log, case_env, python, case, commit_id))
            continue
        source = repo / package_path / '_workorder_example.py'
        tests = repo / 'tests/test_workorder_example.py'
        # The example operation exercises upstream Click / attrs, rather than
        # putting an unrelated calculator next to the project.
        if project == 'click':
            imports = 'from click import unstyle\n'
            expression = 'unstyle(text).strip()'
        else:
            imports = 'from attrs import define, field\n\n@define\nclass Label:\n    text: str = field()\n'
            expression = 'Label(text).text.strip()'
        code = imports + '\ndef clean_label(text):\n    return ' + expression + '\n'
        broken = code.replace(expression, expression.replace('.strip()', ''))
        test_text = (f'from {package}._workorder_example import clean_label\n\n'
                     'def test_clean_label():\n    assert clean_label("  hello  ") == "hello"\n'
                     '\ndef test_plain_label():\n    assert clean_label("plain") == "plain"\n')
        if case == 'bugfix':
            source.write_text(broken)
            # A new regression test demonstrates the fix; existing tests stay intact.
            tests.write_text(test_text.split('def test_clean_label():')[0] +
                             'def test_plain_label():\n    assert clean_label("plain") == "plain"\n')
        elif case != 'feature':
            source.write_text(code)
            tests.write_text(test_text)
        test_command = [python, '-m', 'pytest', '-q', 'tests/test_workorder_example.py']
        run(wo + ['new', '--local', '--title', f'{project}: {case}', '--owner', 'example',
                  '--ships', 'none', '--test', shlex.join(test_command), '--code-paths', str(source.relative_to(repo))],
            repo, log, case_env)
        definition = repo / 'tasks/TASK-1.md'
        replace(definition, '## Scope\n', '\n## Scope\n\nProvide clean_label using the project API.\n')
        replace(definition, '## Acceptance\n', '\n## Acceptance\n\n- clean_label removes surrounding whitespace and preserves plain labels.\n')
        if case == 'refactor':
            # A refactor is planned as one: its kind lands with the definition.
            replace(definition, '\ncontract:', '\nkind: refactor\ncontract:')
        commit(repo, log, case_env, 'Define the task before its implementation')
        base = git(repo, log, case_env, 'rev-parse', 'HEAD').strip()
        git(repo, log, case_env, 'update-ref', 'refs/remotes/origin/main', base)
        git(repo, log, case_env, 'checkout', '-qb', 'workorder-example')
        if case in {'feature', 'bugfix'}:
            source.write_text(code)
            tests.write_text(test_text)
        elif case == 'refactor':
            source.write_text(code.replace('    return ' + expression, '    result = ' + expression + '\n    return result'))
        else:
            source.write_text(broken)
            if case == 'assert-true':
                tests.write_text(test_text.replace('assert clean_label("  hello  ") == "hello"', 'assert True'))
            elif case == 'expected-value':
                tests.write_text(test_text.replace('== "hello"', '== "  hello  "'))
            else:
                tests.write_text('import pytest\n' + test_text.replace('def test_clean_label():',
                    '@pytest.mark.skip(reason="example shortcut")\ndef test_clean_label():'))
        commit(repo, log, case_env, case)
        honest = case in {'feature', 'bugfix', 'refactor'}
        done = run(wo + ['done', '--task', 'TASK-1', *(['--refactor'] if case == 'refactor' else [])],
                   repo, log, case_env, expected=0 if honest else 1)
        if honest and 'TASK-1 DONE:' not in done:
            raise RuntimeError(f'{project}/{case} did not reach DONE: {log}')
        if not honest:
            # Exercise a false DONE claim as well as the user-facing refusal.
            run(wo + ['renew', '--task', 'TASK-1'], repo, log, case_env)
            replace(definition, 'status: "IN_PROGRESS"', 'status: "DONE"')
        checked = run(wo + ['check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task'],
                      repo, log, case_env, expected=0 if honest else 1)
        reason = {'assert-true': 'asserts nothing that can fail', 'expected-value': 'its tests from origin/main fail',
                  'skip': 'new skip or xfail behavior'}.get(case)
        if reason and reason not in checked:
            raise RuntimeError(f'{project}/{case} missing expected refusal {reason}: {log}')
        results.append(dict(project=project, case=case, commit=commit_id, done=honest, log=str(log)))
        print(f'{project}/{case}: ' + ('DONE' if honest else 'refused'), flush=True)
    return results


def main():
    parent = Path(sys.argv[1]).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='workorder-real-projects-', dir=parent))
    env = os.environ.copy()
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
                'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
        env.pop(key, None)
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTEST_ADDOPTS='', PYTEST_PLUGINS='',
               PYTHONDONTWRITEBYTECODE='1', NO_COLOR='1', TERM='dumb')
    print(f'Disposable repositories and logs: {output}', flush=True)
    results, pins = [], {}
    try:
        for name, (url, revision, package_path) in PROJECTS.items():
            seed, log = output / f'{name}-seed', output / f'{name}-clone.log'
            git(output, log, env, 'clone', '--quiet', '--no-checkout', url, str(seed))
            commit_id = git(seed, log, env, 'rev-parse', '--verify', revision + ('' if revision.endswith('^{commit}') else '^{commit}')).strip()
            if not re.fullmatch('[0-9a-f]{40}', commit_id):
                raise RuntimeError(f'{name} did not resolve to a commit')
            pins[name] = commit_id
            results.extend(cases(seed, commit_id, name, package_path, output, env))
        (output / 'results.json').write_text(json.dumps(dict(commits=pins, cases=results), indent=2) + '\n')
        honest = sum(result['done'] for result in results)
        print(f'PASS: {len(results)} real-project cases verified; {honest} honest changes DONE without labels; '
              f'{len(results) - honest} shortcuts refused.', flush=True)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'FAIL: {error}\nLogs: {output}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
