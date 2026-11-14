---
id: "TASK-4"
title: "Sort items by due date"
owner: "alex"
business_owner: "sam"
priority: "P2"
created_at: "2026-11-14"
mission: "Show what is due first"
depends_on: ["TASK-1"]
blocks: []
next: "Agree the order for items without a date"
summary: "A sort option on the list view"
status: "IN_PROGRESS"
ships: "repo"
waits_on: []
contract: "none"
---

# TASK-4

## Context

Pocket List users with long lists miss items that are due soon. TASK-1's filter
has to land first, because the sort applies to the filtered list.

## Scope

In: a "Sort by due date" option on the list view. Out: reminders.

## Lanes

- Interface: the sort control (alex).
- Data: due dates on existing items (alex).

## Acceptance

- Items with a due date appear earliest first.
- Items without a due date appear last.

## Progress

- 2026-11-14: opened.
