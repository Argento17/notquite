"""Where a command's time went, by phase, printed as one line by done and the pull-request check."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time

PHASES = ('setup', 'new code', 'base code', 'stubbed', 'project test')
_CLOCK = ContextVar('workorder_timing', default=None)


class Clock:
    def __init__(self):
        self.started = time.monotonic()
        self.spent = dict.fromkeys(PHASES, 0.0)
        self.opened = set()
        self.lock = threading.Lock()
        self.local = threading.local()

    def stack(self):
        if not hasattr(self.local, 'stack'):
            self.local.stack = []
        return self.local.stack

    def add(self, name, seconds):
        with self.lock:
            self.spent[name] = self.spent.get(name, 0.0) + seconds


@contextmanager
def session():
    clock = Clock()
    token = _CLOCK.set(clock)
    try:
        yield clock
    finally:
        _CLOCK.reset(token)


@contextmanager
def phase(name, *, default=False):
    """Count wall time to one phase; an inner phase pauses its outer one (per thread).

    A default phase applies only when no other phase is open, so a test process
    started by base or stubbed runs keeps that phase.
    """
    clock = _CLOCK.get()
    if clock is None:
        yield
        return
    stack = clock.stack()
    if default and stack:
        yield
        return
    now = time.monotonic()
    if stack:
        outer = stack[-1]
        clock.add(outer[0], now - outer[1])
    clock.opened.add(name)
    entry = [name, now]
    stack.append(entry)
    try:
        yield
    finally:
        now = time.monotonic()
        clock.add(name, now - entry[1])
        stack.pop()
        if stack:
            stack[-1][1] = now


def _seconds(value):
    return f'{value:.1f}s'


def line(clock):
    """One line: the wall time, each phase, and the rest (startup, Git reads, review)."""
    wall = time.monotonic() - clock.started
    spent = dict(clock.spent)
    parts = []
    for name in PHASES:
        if name != 'setup' and name not in clock.opened:
            parts.append(f'{name} not run')
        else:
            parts.append(f'{name} {_seconds(spent.get(name, 0.0))}')
    counted = sum(spent.values())
    if counted > wall:
        rest = 'runs overlapped'
    else:
        rest = f'other {_seconds(wall - counted)}'
    return f'Time: {_seconds(wall)}: ' + ', '.join(parts) + f', {rest}'
