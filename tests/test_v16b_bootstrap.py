"""Execute the generated workflow's refusal output without service calls."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from workorder import bootstrap
from test_workorder import PACKAGE, PYTHON, local_repo
from test_v09_setup import check_steps
from test_v13b import REVIEWS


@pytest.mark.skipif(os.name == 'nt', reason='executes a POSIX workflow shell')
@pytest.mark.parametrize('pull_request', [False, True])
def test_generated_failure_links_reviewer_guide_and_keeps_exit(local_repo, tmp_path, pull_request):
    root = local_repo
    bootstrap.initialize(root)
    workflow = (root / '.github/workflows/workorder.yml').read_text()
    command = next(command for _, command in check_steps(workflow) if ('--base' in command) == pull_request)
    binary = tmp_path / 'bin'
    binary.mkdir()
    gh = binary / 'gh'
    gh.write_text('#!/bin/sh\nprintf "[[]]\\n"\n')
    runner = binary / 'workorder'
    runner.write_text('#!/bin/sh\necho "task check refused"\nexit 9\n')
    for path in (gh, runner):
        path.chmod(0o755)
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': REVIEWS['pull_request']}))
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + str(Path(PYTHON).parent) + os.pathsep + os.environ['PATH'],
           'PYTHONPATH': str(PACKAGE), 'GITHUB_EVENT_PATH': str(event), 'RUNNER_TEMP': str(tmp_path),
           'GH_REPO': 'example/project', 'PR_NUMBER': '7', 'BASE': 'main'}
    result = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 9, result.stdout + result.stderr
    assert 'task check refused' in result.stdout
    assert 'https://github.com/Argento17/workorder/blob/main/docs/reviewer-guide.md' in result.stderr
