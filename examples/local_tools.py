"""Make offline demo/test tools independent of an editable Workorder checkout."""
import atexit
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import venv


def isolated_tools():
    site = Path(sysconfig.get_path('purelib'))
    editable = [dist for dist in importlib.metadata.distributions()
                if dist.metadata['Name'].lower() == 'workorder'
                and json.loads(dist.read_text('direct_url.json') or '{}').get('dir_info', {}).get('editable')]
    if not editable:
        return sys.executable, site
    # The import probe deliberately checks *all* editable distributions. An
    # editable install of this test tool must not contaminate sample projects.
    # Copy locally available tools into a private venv; no pip or network needed.
    temporary = Path(tempfile.mkdtemp(prefix='workorder-test-tools-'))
    atexit.register(shutil.rmtree, temporary, ignore_errors=True)
    venv.EnvBuilder().create(temporary)
    python = temporary / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    destination = Path(subprocess.check_output([str(python), '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    for source in site.iterdir():
        if source.name.startswith(('__editable__', 'workorder', '__pycache__')) or source.suffix == '.pth':
            continue
        if source.is_dir():
            shutil.copytree(source, destination / source.name, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            shutil.copy2(source, destination / source.name)
    shutil.copytree(Path(__file__).resolve().parents[1] / 'workorder', destination / 'workorder',
                    ignore=shutil.ignore_patterns('__pycache__'))
    if os.name == 'nt':
        from pip._vendor.distlib.scripts import ScriptMaker
        maker = ScriptMaker(None, str(python.parent))
        maker.executable = str(python)
        maker.variants = {''}
        maker.make('workorder = workorder.cli:main')
        maker.make('pytest = pytest:console_main')
    for name, module, entry in [('workorder', 'workorder.cli', 'main'), ('pytest', 'pytest', 'console_main')]:
        script = python.parent / name
        script.write_text(f'#!{python}\nfrom {module} import {entry}\nraise SystemExit({entry}())\n')
        script.chmod(0o755)
    return str(python), destination
