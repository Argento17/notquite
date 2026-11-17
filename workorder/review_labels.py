"""Reviewer allowances from GitHub issue events, used by the generated workflow."""

from .console import print

import json
import re
import sys


ALLOW_LABEL = re.compile(r'workorder-allow:((?:scope:)?[A-Z][A-Z0-9]*-[1-9][0-9]*|tests|no-task|workflow)\Z')


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


LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z')


def accepted_review(pull_request, pages):
    """An undismissed approval of this head by someone other than its author."""
    return bool(approving_reviewers(pull_request, pages))


def approving_reviewers(pull_request, pages):
    """The logins with an undismissed approval of this head, other than its author."""
    author = pull_request.get('user') or {}
    head = (pull_request.get('head') or {}).get('sha')
    if not head or not author.get('login'):
        return []
    latest = {}
    for page in pages:
        for review in page:
            actor = review.get('user') or {}
            if not actor.get('login') or actor['login'].casefold() == author['login'].casefold():
                continue
            if author.get('id') is not None and actor.get('id') == author['id']:
                continue
            if review.get('state') in {'APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'}:
                latest[actor.get('id') or actor['login'].casefold()] = review
    return sorted({review['user']['login'] for review in latest.values()
                   if review.get('state') == 'APPROVED' and review.get('commit_id') == head})


def main():
    with open(sys.argv[1], encoding='utf-8') as stream:
        pull_request = json.load(stream)['pull_request']
    with open(sys.argv[2], encoding='utf-8') as stream:
        pages = json.load(stream)
    allowed, notices = accepted_labels(pull_request, pages)
    for notice in notices:
        print(notice, file=sys.stderr)
    if len(sys.argv) == 3:
        print(','.join(allowed))
        return
    with open(sys.argv[3], encoding='utf-8') as stream:
        approvers = approving_reviewers(pull_request, json.load(stream))
    # Keep explicit labels separate: an approval cannot accept an old-test failure.
    arguments = ['--allow', ','.join(allowed)] if allowed else []
    if approvers:
        arguments.append('--approving-review')
        # Logins name the reviewers in Acceptance lines; only plain GitHub logins reach the shell.
        named = [login for login in approvers if LOGIN.fullmatch(login)]
        if named:
            arguments += ['--approved-by', ','.join(named)]
    print(' '.join(arguments))  # all words are fixed flags or validated allowance IDs


if __name__ == '__main__':
    main()
