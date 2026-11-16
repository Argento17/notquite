"""Use the invoking test environment, including an editable Workorder install."""
from pathlib import Path
import os
import sys
import sysconfig

if os.environ.get('WORKORDER_TEST_PYTHON'):
    TEST_PYTHON = os.environ['WORKORDER_TEST_PYTHON']
    TEST_SITE = Path(os.environ['WORKORDER_TEST_SITE'])
else:
    TEST_PYTHON = sys.executable
    TEST_SITE = Path(sysconfig.get_path('purelib'))
    os.environ['WORKORDER_TEST_PYTHON'] = TEST_PYTHON
    os.environ['WORKORDER_TEST_SITE'] = str(TEST_SITE)
