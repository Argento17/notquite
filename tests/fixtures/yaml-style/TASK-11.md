---
id: TASK-11
title: Share a list by link
owner: alex
business_owner: sam
priority: P1
created_at: 2026-11-14T09:30:00Z

mission: |
  Let a user share a read-only list with someone else.
  Nobody else can edit it.
depends_on:
  - TASK-10
  - "TASK-12"
blocks:
  - TASK-13
next: >-
  Agree how long a shared link stays valid
  before building the share button.
summary: >
  A share button creates a read-only link to the list.

  Links expire, and the owner can revoke them.

status: IN_PROGRESS
ships: live
waits_on:
  - person:sam
contract: none
---

# TASK-11

## Context

Invented example for the Pocket List to-do app.

## Scope

In: read-only links. Out: shared editing.

## Lanes

| Lane | Owner |
|---|---|
| Link service | alex |
| Share button | alex |

## Acceptance

- A shared link opens the list without signing in.
- A revoked link stops working.

## Progress

- 2026-11-14: opened.
