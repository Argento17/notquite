"""Render diagnostics on the caller's console without losing their meaning."""
import builtins
import sys


def print(*values, sep=' ', end='\n', file=None, flush=False):
    stream = sys.stdout if file is None else file
    encoding = getattr(stream, 'encoding', None)
    if encoding:
        # Escapes preserve test IDs and paths that a legacy console cannot
        # represent. Do this before writing so a failure cannot print half a
        # diagnostic or turn successful evidence into a command failure.
        def represent(value):
            return str(value).encode(encoding, 'backslashreplace').decode(encoding)
        values = tuple(represent(value) for value in values)
        sep = represent(sep) if sep is not None else sep
        end = represent(end) if end is not None else end
    builtins.print(*values, sep=sep, end=end, file=stream, flush=flush)
