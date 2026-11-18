"""Reviewer allowances from GitHub issue events, used by the generated workflow."""

from .console import print

import json
import re
import sys


ALLOW_LABEL = re.compile(r'workorder-allow:((?:scope:)?[A-Z][A-Z0-9]*-[1-9][0-9]*|tests|no-task|workflow)\Z')
OWNER_ACCEPTANCE = 'accepted by the owner (solo mode; no independent review)'
UNVERIFIED = 'whose owner or admin permission could not be verified'


def _latest_adds(pull_request, pages):
    """Current allowance labels whose latest add has an actor: (name, item, same author, actor).

    gh api --paginate --slurp supplies pages in event order. A remove event
    revokes an earlier add; missing actors or event history supply nothing.
    GitHub account IDs also survive login renames.
    """
    author = pull_request.get('user') or {}
    latest = {}
    for page in pages:
        for event in page:
            name = (event.get('label') or {}).get('name', '')
            if event.get('event') in {'labeled', 'unlabeled'} and ALLOW_LABEL.fullmatch(name):
                latest[name] = event
    for label in pull_request.get('labels', []):
        name = label.get('name', '')
        match = ALLOW_LABEL.fullmatch(name)
        event = latest.get(name, {})
        actor = event.get('actor') or {}
        if not match or event.get('event') != 'labeled' or not actor.get('login') or not author.get('login'):
            continue
        same_author = (actor['login'].casefold() == author['login'].casefold()
                       or (author.get('id') is not None and actor.get('id') == author['id']))
        yield name, match[1], same_author, actor


def accepted_labels(pull_request, pages):
    """Keep current labels whose latest add event belongs to another account."""
    allowed, notices = [], []
    for name, item, same_author, _ in _latest_adds(pull_request, pages):
        if same_author:
            notices.append(f'ignored {name}: added by the pull request author')
        elif item not in allowed:
            allowed.append(item)
    return allowed, notices


def _same_account(account, author):
    """Account IDs decide when both are known; a login alone is compared only without IDs."""
    if not isinstance(account, dict) or not isinstance(account.get('login'), str):
        return False
    if account.get('id') is not None and author.get('id') is not None:
        return account['id'] == author['id']
    return account['login'].casefold() == author['login'].casefold()


def verify_owner(event, permission):
    """(confirmed, who, permission): is the pull request's author the repository owner or an admin?

    The owner comes from the event GitHub wrote; admin permission comes from the answer to
    GET repos/OWNER/REPO/collaborators/AUTHOR/permission. A missing, failed, unreadable or
    mismatched answer is unverifiable and accepts nothing.
    """
    author = (event.get('pull_request') or {}).get('user') or {}
    if not isinstance(author.get('login'), str) or not author['login']:
        return False, UNVERIFIED, None
    owner = ((event.get('repository') or {}).get('owner')) or {}
    if _same_account(owner, author):
        return True, 'the repository owner', None
    if (not isinstance(permission, dict) or not isinstance(permission.get('permission'), str)
            or not _same_account(permission.get('user'), author)):
        return False, UNVERIFIED, None
    if permission['permission'] == 'admin':
        return True, 'an admin of the repository', None
    return False, 'who is not the repository owner or an admin', permission['permission']


def solo_labels(pull_request, pages, owner):
    """Solo mode: the author's own labels are owner acceptance when verify_owner confirmed the author.

    Labels added by anyone else count as before. Owner acceptance is returned separately so
    the check can name it; it is never reported as review.
    """
    verified, who, permission = owner
    author = pull_request.get('user') or {}
    allowed, accepted, notices = [], [], []
    for name, item, same_author, actor in _latest_adds(pull_request, pages):
        if not same_author:
            if item not in allowed:
                allowed.append(item)
        elif not verified or not _same_account(actor, author):
            # The actor must be the confirmed account itself, not a login it once had.
            reason = (who, permission) if verified is False else (UNVERIFIED, None)
            detail = f' (permission: {reason[1]}; solo mode)' if reason[1] else ' (solo mode)'
            notices.append(f'ignored {name}: added by the pull request author, {reason[0]}{detail}')
        elif item not in accepted:
            accepted.append(item)
            notices.append(f'{OWNER_ACCEPTANCE}: {name} added by the pull request author, {who}')
    return allowed, accepted, notices


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


def _answer(path):
    """A recorded service answer, or None when the lookup failed or wrote something unreadable."""
    try:
        with open(path, encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def main():
    arguments = sys.argv[1:]
    permission = None
    solo = arguments[:1] == ['--solo']
    if solo:
        permission, arguments = _answer(arguments[1]), arguments[2:]
    with open(arguments[0], encoding='utf-8') as stream:
        event = json.load(stream)
    pull_request = event['pull_request']
    with open(arguments[1], encoding='utf-8') as stream:
        pages = json.load(stream)
    if solo:
        allowed, owned, notices = solo_labels(pull_request, pages, verify_owner(event, permission))
    else:
        (allowed, notices), owned = accepted_labels(pull_request, pages), []
    for notice in notices:
        print(notice, file=sys.stderr)
    if len(arguments) == 2:
        print(','.join(allowed))
        return
    with open(arguments[2], encoding='utf-8') as stream:
        approvers = approving_reviewers(pull_request, json.load(stream))
    words = ['--allow', ','.join(allowed)] if allowed else []
    if owned:
        words += ['--owner-accepts', ','.join(owned)]
    if approvers:
        words.append('--approving-review')
        # Logins name the reviewers in Acceptance lines; only plain GitHub logins reach the shell.
        named = [login for login in approvers if LOGIN.fullmatch(login)]
        if named:
            words += ['--approved-by', ','.join(named)]
    print(' '.join(words))  # all words are fixed flags or validated allowance IDs


if __name__ == '__main__':
    main()
