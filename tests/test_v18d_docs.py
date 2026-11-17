"""Lane D (0.18): docs a team can read, and the one review rule for project tests."""

import re

from workorder import bootstrap
from test_workorder import PACKAGE, cli, local_repo
from test_v16b_project import FAILURE, project


def words(text):
    return len(text.split())


def section(text, heading):
    return text.split(heading, 1)[1].split('\n## ', 1)[0]


def test_project_regression_needs_the_tests_label_not_approval(local_repo):
    # One review rule: existing tests that now fail, including project tests, need a label.
    root = local_repo
    project(root)
    approved = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--approving-review')
    output = approved.stdout + approved.stderr
    assert approved.returncode == 1, output
    assert FAILURE in output
    assert 'an approving review is not enough: it changes what existing tests expect' in output
    labelled = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--allow', 'tests')
    assert labelled.returncode == 0, labelled.stdout + labelled.stderr


def test_readme_is_short_and_in_order():
    readme = (PACKAGE / 'README.md').read_text(encoding='utf-8')
    assert words(readme) <= 1100
    order = ['Every task is a Markdown file', 'refuses', '## Quick start', '## The review rule',
             '## What it does not do']
    positions = [readme.find(phrase) for phrase in order]
    positions.append(readme.rfind('docs/reference.md'))
    assert -1 not in positions and positions == sorted(positions), positions


def review_table(text):
    rows = [line for line in text.split('| Finding |', 1)[1].split('\n\n', 1)[0].splitlines()[2:]]
    return [line for line in rows if line.startswith('|')]


def test_review_table_is_one_screen_and_shared():
    readme = (PACKAGE / 'README.md').read_text(encoding='utf-8')
    guide = (PACKAGE / 'docs/reviewer-guide.md').read_text(encoding='utf-8')
    rows = review_table(readme)
    assert 5 <= len(rows) <= 15
    assert rows == review_table(guide)
    # The table is at the top of the guide, before the first refusal family.
    assert guide.index('| Finding |') < guide.index('<!-- refusal-family')
    labels = {row.split('|')[1].strip(): row.split('|')[2] for row in rows}
    for finding, clears in labels.items():
        if 'existing test' in finding.lower() or 'workflow' in finding:
            assert 'approving review' not in clears.replace('not an approving review', ''), finding


def test_agent_block_is_short_and_names_the_essentials():
    block = bootstrap.AGENT_BLOCK
    assert words(block) <= 170
    for phrase in ('workorder show', 'workorder claim', 'fails without', 'workorder done --task',
                   'workorder note', '--next', 'by hand', 'receipts',
                   'workorder check --base origin/main --run-tests --all-tests --require-task',
                   '.git/workorder/last-output.txt'):
        assert phrase in block, phrase
    assert block.strip() in (PACKAGE / 'README.md').read_text(encoding='utf-8')


def test_reference_has_contents_and_is_shorter():
    reference = (PACKAGE / 'docs/reference.md').read_text(encoding='utf-8')
    assert words(reference) < 13000
    contents = reference.split('## ', 1)[0]
    anchors = re.findall(r'\]\(#([a-z0-9-]+)\)', contents)
    headings = {re.sub(r'[^a-z0-9 -]', '', h.lower()).replace(' ', '-')
                for h in re.findall(r'^## (.+)$', reference, re.M)}
    assert len(anchors) >= 8 and set(anchors) <= headings


def test_public_docs_avoid_banned_wording():
    banned = ('trust layer', 'revolutionary', 'tamper-proof', 'production-grade', 'ai-powered',
              'ci gate', 'branch protection', 'macos')
    for name in ('README.md', 'docs/reference.md', 'docs/reviewer-guide.md'):
        text = (PACKAGE / name).read_text(encoding='utf-8').lower()
        for word in banned:
            assert word not in text, (name, word)
        assert not re.search(r'\bsigned\b', text), name
