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


def cases(seed, commit_id, project, package_path, output, env, prepared_python=None):
    """Run all six cases against the pinned source. prepared_python is for offline checks."""
    results = []
    package = package_path.split('/')[-1]
    wo = [sys.executable, '-m', 'workorder']
    for case in CASES:
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
        run(wo + ['init'], repo, log, env)
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
            repo, log, env)
        definition = repo / 'tasks/TASK-1.md'
        replace(definition, '## Scope\n', '\n## Scope\n\nProvide clean_label using the project API.\n')
        replace(definition, '## Acceptance\n', '\n## Acceptance\n\n- clean_label removes surrounding whitespace and preserves plain labels.\n')
        commit(repo, log, env, 'Define the task before its implementation')
        base = git(repo, log, env, 'rev-parse', 'HEAD').strip()
        git(repo, log, env, 'update-ref', 'refs/remotes/origin/main', base)
        git(repo, log, env, 'checkout', '-qb', 'workorder-example')
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
        commit(repo, log, env, case)
        honest = case in {'feature', 'bugfix', 'refactor'}
        done = run(wo + ['done', '--task', 'TASK-1', *(['--refactor'] if case == 'refactor' else [])],
                   repo, log, env, expected=0 if honest else 1)
        if honest and 'TASK-1 DONE:' not in done:
            raise RuntimeError(f'{project}/{case} did not reach DONE: {log}')
        if not honest:
            # Exercise a false DONE claim as well as the user-facing refusal.
            run(wo + ['renew', '--task', 'TASK-1'], repo, log, env)
            replace(definition, 'status: "IN_PROGRESS"', 'status: "DONE"')
        checked = run(wo + ['check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task'],
                      repo, log, env, expected=0 if honest else 1)
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
        print('PASS: 12 real-project cases verified; 6 honest changes DONE without labels; 6 shortcuts refused.', flush=True)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'FAIL: {error}\nLogs: {output}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
