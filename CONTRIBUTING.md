# Contributing

Small, focused changes are easiest to review. Please open an issue first for
anything that changes what counts as evidence.

```sh
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -n auto   # in parallel; plain `python -m pytest` also works
```

- Keep the runtime on Python's standard library.
- A change to the proof rules needs a test that fails without it.
- To run this repository's own pre-commit hook: `git config core.hooksPath hooks`.
