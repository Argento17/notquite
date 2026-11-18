"""The agent block names the full-output file only for shortened output: the file exists only then."""

from pathlib import Path

from workorder.bootstrap import AGENT_BLOCK
from test_workorder import cli, local_repo  # noqa: F401  (fixture)

BLOCK_1_0 = (Path(__file__).parent / 'fixtures/agent_block_1_0.md').read_text(encoding='utf-8')


def test_agent_block_does_not_promise_a_file_for_every_run():
    assert 'Output is shortened;' not in AGENT_BLOCK
    assert '- Shortened output is saved in full to `.git/workorder/last-output.txt`.' in AGENT_BLOCK


def test_init_replaces_the_1_0_block(local_repo):
    root = local_repo
    (root / 'AGENTS.md').write_text(BLOCK_1_0)
    result = cli(root, 'init')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'AGENTS.md: replaced the agent block from an earlier version' in result.stdout
    text = (root / 'AGENTS.md').read_text(encoding='utf-8')
    assert text.count('## Tasks') == 1 and AGENT_BLOCK.strip() in text and 'Output is shortened;' not in text
