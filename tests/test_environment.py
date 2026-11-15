"""Subprocess tools must not carry the editable checkout into sample projects."""
from pathlib import Path
import os
import runpy

_tools = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/local_tools.py"))
if os.environ.get('WORKORDER_TEST_PYTHON'):
    TEST_PYTHON = os.environ['WORKORDER_TEST_PYTHON']
    TEST_SITE = Path(os.environ['WORKORDER_TEST_SITE'])
else:
    TEST_PYTHON, TEST_SITE = _tools['isolated_tools']()
    os.environ['WORKORDER_TEST_PYTHON'] = TEST_PYTHON
    os.environ['WORKORDER_TEST_SITE'] = str(TEST_SITE)
