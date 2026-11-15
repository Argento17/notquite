"""Reviewer allowances from GitHub issue events, used by the generated workflow."""

import json
import re
import sys


ALLOW_LABEL = re.compile(r'workorder-allow:([A-Z][A-Z0-9]*-[1-9][0-9]*|tests|no-task)\Z')


def accepted_labels(pull_request, pages):
    """Keep current labels whose latest add event belongs to another account.

    gh api --paginate --slurp supplies pages in event order. A remove event
    revokes an earlier add; missing actors or event history supply no approval.
    GitHub account IDs also survive login renames.
    """
    author = pull_request.get('user') or {}
    latest = {}
    for page in pages:
        for event in page:
            name = (event.get('label') or {}).get('name', '')
            if event.get('event') in {'labeled', 'unlabeled'} and ALLOW_LABEL.fullmatch(name):
                latest[name] = event
    allowed, notices = [], []
    for label in pull_request.get('labels', []):
        name = label.get('name', '')
        match = ALLOW_LABEL.fullmatch(name)
        event = latest.get(name, {})
        actor = event.get('actor') or {}
        if not match or event.get('event') != 'labeled' or not actor.get('login') or not author.get('login'):
            continue
        same_author = (actor['login'].casefold() == author['login'].casefold()
                       or (author.get('id') is not None and actor.get('id') == author['id']))
        if same_author:
            notices.append(f'ignored {name}: added by the pull request author')
        elif match[1] not in allowed:
            allowed.append(match[1])
    return allowed, notices


def main():
    with open(sys.argv[1], encoding='utf-8') as stream:
        pull_request = json.load(stream)['pull_request']
    with open(sys.argv[2], encoding='utf-8') as stream:
        pages = json.load(stream)
    allowed, notices = accepted_labels(pull_request, pages)
    for notice in notices:
        print(notice, file=sys.stderr)
    print(','.join(allowed))


if __name__ == '__main__':
    main()
