"""Lane D (0.18): docs a team can read, and the one review rule for project tests."""

import re

from workorder import bootstrap
from test_workorder import PACKAGE, cli, local_repo
from test_v16b_project import FAILURE, project


MERMAID = re.compile(r'^```mermaid\n.*?^```\n', re.M | re.S)


def prose(text):
    # Image alt text (the demo GIF) and Mermaid flow charts are pictures; the limits are for prose.
    return [line for line in MERMAID.sub('', text).splitlines() if not line.startswith('![')]


def words(text):
    return len(' '.join(prose(text)).split())


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
    # 1,300 (was 1,200): the README now also carries the continuity story, the language support table
    # and the line that DONE is not acceptance; the review table and agent block are fixed text.
    assert words(readme) <= 1300
    order = ['Every task is a Markdown file', '`workorder done` refuses', '## Quick start', '## The review rule',
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
    assert words(reference) < 14000  # 1.0 adds solo mode, acceptance and language support
    contents = reference.split('## ', 1)[0]
    anchors = re.findall(r'\]\(#([a-z0-9-]+)\)', contents)
    headings = {re.sub(r'[^a-z0-9 -]', '', h.lower()).replace(' ', '-')
                for h in re.findall(r'^## (.+)$', reference, re.M)}
    assert len(anchors) >= 8 and set(anchors) <= headings


def test_public_docs_avoid_banned_wording():
    banned = ('trust layer', 'revolutionary', 'tamper-proof', 'production-grade', 'ai-powered',
              'ci gate', 'branch protection', 'macos')
    for name in ('README.md', 'docs/reference.md', 'docs/reviewer-guide.md', 'docs/how-it-works.md'):
        text = (PACKAGE / name).read_text(encoding='utf-8').lower()
        for word in banned:
            assert word not in text, (name, word)
        assert not re.search(r'\bsigned\b', text), name


def test_readme_opens_with_an_overview_then_one_flow_chart():
    readme = (PACKAGE / 'README.md').read_text(encoding='utf-8')
    charts = MERMAID.findall(readme)
    assert len(charts) == 1
    chart, gif = readme.index('```mermaid'), readme.index('![')
    # What and why come first; file details come after the chart and the GIF.
    overview = readme[:chart]
    for detail in ('Markdown', 'tasks/', 'receipt'):
        assert detail not in overview, detail
    assert 'same structure' in overview and 'workorder show' in overview
    assert chart < gif < readme.index('Every task is a Markdown file')
    boxes = re.findall(r'^\s+[A-Z]+[\[{(]', charts[0], re.M) + re.findall(r'--> [A-Z]+[\[{(]', charts[0])
    assert 8 <= len(boxes) <= 12, boxes
    for command in ('workorder show', 'workorder done', 'workorder check', 'tasks/TASK-1.md'):
        assert command in charts[0], command
    assert '[How it works](docs/how-it-works.md)' in readme.split('## ', 1)[0]


def test_how_it_works_has_three_short_charts():
    text = (PACKAGE / 'docs/how-it-works.md').read_text(encoding='utf-8')
    assert len(MERMAID.findall(text)) == 3
    first = text.split('```mermaid', 1)[0]
    assert first.index('same structure') < first.index('tasks/')
    assert words(text) <= 450
    # Each box quotes the code's own wording.
    for phrase in ('commit the code first', 'does not exercise the change', 'does not show the change works',
                   'workorder done --needs-review', 'workorder retire', 'no review or label clears it'):
        assert phrase in text, phrase
