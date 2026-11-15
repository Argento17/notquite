"""Setup summaries, local reservations, and reviewer workflow allowances."""

import os
import shlex

import pytest

from workorder import bootstrap, review_labels
from test_v09_setup import check_steps, execute
from test_workorder import cli, git, local_repo, task


@pytest.mark.parametrize('claude', [False, True])
def test_init_lists_written_files_and_prints_a_working_commit_command(local_repo, claude):
    result = cli(local_repo, 'init', *(['--claude'] if claude else []))
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    expected = ['AGENTS.md', 'CLAUDE.md', '.git/hooks/pre-commit',
                '.github/workflows/workorder.yml']
    if claude:
        expected.append('.claude/settings.json')
    assert 'Files written:' in lines, result.stdout
    assert [line.strip() for line in lines[lines.index('Files written:') + 1:-1]] == expected
    command = shlex.split(lines[-1])
    separator = command.index('&&')
    assert command[:2] == ['git', 'add']
    assert command[separator + 1:separator + 4] == ['git', 'commit', '-m']
    tracked = [name for name in expected if name != '.git/hooks/pre-commit']
    assert command[2:separator] == tracked
    # The same Git arguments printed to the user must stage and commit every file.
    git(local_repo, *command[1:separator])
    git(local_repo, *command[separator + 2:])
    assert git(local_repo, 'show', '--format=', '--name-only', 'HEAD').splitlines() == sorted(tracked)
    assert git(local_repo, 'status', '--porcelain') == ''


def test_init_reports_only_files_written_in_this_run(local_repo, capsys):
    bootstrap.initialize(local_repo)
    capsys.readouterr()
    bootstrap.initialize(local_repo, claude=True)
    output = capsys.readouterr().out
    assert 'Files written:\n' in output
    assert output.split('Files written:\n')[1].splitlines() == [
        '  .claude/settings.json',
        "git add .claude/settings.json && git commit -m 'Set up Workorder'",
    ]


def test_init_with_nothing_to_write_has_no_commit_command(local_repo, capsys):
    bootstrap.initialize(local_repo, claude=True)
    capsys.readouterr()
    bootstrap.initialize(local_repo, claude=True)
    output = capsys.readouterr().out
    assert output.splitlines()[-1] == 'No files written.'
    assert 'git add ' not in output


@pytest.mark.parametrize('explicit_local', [False, True])
def test_local_new_uses_reserved_ids_already_in_local_tags(local_repo, explicit_local):
    if explicit_local:
        git(local_repo, 'remote', 'add', 'origin', str(local_repo / 'missing.git'))
    task(local_repo, 'TASK-12')
    for ref in ('refs/tags/workorder/ids/TASK-40',
                'refs/tags/workorder/ids/OTHER-900',
                'refs/tags/workorder/ids/TASK-800-extra',
                'refs/tags/TASK-700'):
        git(local_repo, 'update-ref', ref, 'HEAD')
    result = cli(local_repo, 'new', '--title', 'Use the next known ID', '--owner', 'solo',
                 *(['--local'] if explicit_local else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'TASK-41 opened' in result.stdout
    assert (local_repo / 'tasks/TASK-41.md').is_file()
    assert not git(local_repo, 'for-each-ref', '--format=%(refname)',
                   'refs/tags/workorder/ids/TASK-41')


@pytest.mark.skipif(os.name == 'nt', reason='executes the generated POSIX workflow')
def test_generated_workflow_has_a_repeatable_ignore_example(local_repo, tmp_path):
    bootstrap.initialize(local_repo)
    generated = (local_repo / '.github/workflows/workorder.yml').read_text()
    examples = [line.strip().removeprefix('#').strip() for line in generated.splitlines()
                if line.strip().startswith('# --require-task-ignore ')]
    assert len(examples) == 1, 'setup must show a commented example of repeated path exceptions'
    assert shlex.split(examples[0]) == [
        '--require-task-ignore', 'docs/**', '--require-task-ignore', '*.md']
    command = next(command for step, command in check_steps(generated) if '--base' in command)
    arguments = execute(command + ' ' + examples[0], local_repo, tmp_path, [])
    assert arguments == ['check', '--base', 'origin/main', '--run-tests', '--all-tests',
                         '--require-task', '--require-task-ignore', 'docs/**',
                         '--require-task-ignore', '*.md']


@pytest.mark.parametrize('actor,accepted,notice', [
    ({'id': 2, 'login': 'reviewer'}, ['workflow'], []),
    ({'id': 1, 'login': 'Author'}, [],
     ['ignored workorder-allow:workflow: added by the pull request author']),
    ({'id': 1, 'login': 'renamed-author'}, [],
     ['ignored workorder-allow:workflow: added by the pull request author']),
    ({'id': 9, 'login': 'AUTHOR'}, [],
     ['ignored workorder-allow:workflow: added by the pull request author']),
])
def test_workflow_allowance_retains_author_filtering(actor, accepted, notice):
    request = {'user': {'id': 1, 'login': 'author'},
               'labels': [{'name': 'workorder-allow:workflow'}]}
    events = [[{'event': 'labeled', 'label': {'name': 'workorder-allow:workflow'},
                'actor': actor}]]
    assert review_labels.accepted_labels(request, events) == (accepted, notice)


@pytest.mark.skipif(os.name == 'nt', reason='executes the generated POSIX workflow')
def test_generated_workflow_passes_the_reviewers_workflow_allowance(local_repo, tmp_path):
    bootstrap.initialize(local_repo)
    generated = (local_repo / '.github/workflows/workorder.yml').read_text()
    command = next(command for step, command in check_steps(generated) if '--base' in command)
    arguments = execute(command, local_repo, tmp_path, ['workorder-allow:workflow'])
    assert arguments[-2:] == ['--allow', 'workflow']
