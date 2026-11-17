"""Upgrading replaces the agent block an earlier version wrote, instead of adding a second one."""

from pathlib import Path

from workorder.bootstrap import AGENT_BLOCK
from test_workorder import cli, local_repo  # noqa: F401  (fixture)

OLD = (Path(__file__).parent / 'fixtures/agent_block_0_17.md').read_text(encoding='utf-8')


def test_init_replaces_an_earlier_agent_block_and_keeps_other_text(local_repo):
    root = local_repo
    (root / 'AGENTS.md').write_text('# Our rules\n\nBe kind.\n\n' + OLD.rstrip('\n') + '\n\n## Style\n\nUse tabs.\n')
    (root / 'CLAUDE.md').write_text(OLD)
    checked = cli(root, 'init', '--check')
    assert 'AGENTS.md: agent block from an earlier version' in checked.stdout
    result = cli(root, 'init')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'AGENTS.md: replaced the agent block from an earlier version' in result.stdout
    for name in ('AGENTS.md', 'CLAUDE.md'):
        text = (root / name).read_text(encoding='utf-8')
        assert text.count('## Tasks') == 1, text
        assert AGENT_BLOCK.strip() in text
        assert OLD.strip() not in text
    agents = (root / 'AGENTS.md').read_text(encoding='utf-8')
    assert agents.startswith('# Our rules\n\nBe kind.\n\n## Tasks')
    assert agents.endswith('\n\n## Style\n\nUse tabs.\n')
    again = cli(root, 'init')
    assert 'AGENTS.md: agent block already present' in again.stdout
