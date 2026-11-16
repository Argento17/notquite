"""Observable Git configuration dependencies permit safe command-local reuse."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

from workorder import store
from test_workorder import PYTHON, git, local_repo
from test_v16f_git_cache import git_spawns


@pytest.fixture(autouse=True)
def independent_git_environment(tmp_path, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    for name in list(os.environ):
        if name.startswith('GIT_'):
            monkeypatch.delenv(name)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')


def reused(root, query, calls, expected, *, isolated=False, budget=3):
    for _ in range(3):
        result = store._git_run(root, *query, isolated=isolated)
        assert result.stdout == expected
    assert calls.count(query) == 1, calls
    discovery = [args for args in calls if args[0] == 'config' and '--show-origin' in args]
    assert len(discovery) <= 1, calls
    assert len(calls) <= budget, calls


def test_registered_but_unused_lfs_allows_reuse_without_lfs_installed(local_repo, git_spawns):
    root = local_repo
    for key, value in [('clean', 'missing-git-lfs clean -- %f'),
                       ('smudge', 'missing-git-lfs smudge -- %f'),
                       ('process', 'missing-git-lfs filter-process'), ('required', 'true')]:
        git(root, 'config', 'filter.lfs.' + key, value)
    git_spawns.clear()
    with store.read_session():
        reused(root, ('diff', '--name-only', 'HEAD'), git_spawns, '', budget=4)


@pytest.mark.parametrize('location', ['worktree', 'index'])
@pytest.mark.parametrize('rule', ['unmatched', 'unset', 'different', 'higher-priority', 'unused-macro'])
def test_ineffective_filter_rules_allow_reuse(local_repo, git_spawns, location, rule):
    root = local_repo
    git(root, 'config', 'filter.lfs.clean', 'missing-git-lfs clean -- %f')
    rules = {
        'unmatched': '*.psd filter=lfs\n',
        'unset': 'app.py filter=lfs\napp.py -filter\n',
        'different': 'app.py filter=lfs\napp.py filter=unregistered\n',
        'higher-priority': 'app.py filter=lfs\n',
        'unused-macro': '[attr]large filter=lfs\n*.psd large\n',
    }
    attrs = root / '.gitattributes'
    attrs.write_text(rules[rule])
    if rule == 'higher-priority':
        (root / '.git/info/attributes').write_text('app.py -filter\n')
    if location == 'index':
        git(root, 'add', '.gitattributes')
        attrs.unlink()
    git_spawns.clear()
    with store.read_session():
        reused(root, ('diff', '--name-only', 'HEAD'), git_spawns, '', budget=6)
        resolutions = [call for call in git_spawns if call[0] == 'check-attr']
        assert len(resolutions) == 1


def test_override_removal_activates_filter_with_external_inputs(local_repo, git_spawns):
    root = local_repo
    dependency = root.parent / 'filter-data'
    dependency.write_text('items = []\n')
    (root / 'app.py').write_text('worktree input differs\n')
    code = f'import sys; from pathlib import Path; sys.stdin.read(); sys.stdout.write(Path({str(dependency)!r}).read_text())'
    git(root, 'config', 'filter.dynamic.clean', shlex.join([PYTHON, '-c', code]))
    (root / '.gitattributes').write_text('app.py filter=dynamic\n')
    override = root / '.git/info/attributes'
    override.write_text('app.py -filter\n')
    query = ('diff', '--name-only', 'HEAD')
    git_spawns.clear()
    with store.read_session():
        reused(root, query, git_spawns, 'app.py\n', budget=5)
        override.unlink()
        assert store.git(root, *query).stdout == ''
        dependency.write_text('external output changed\n')
        assert store.git(root, *query).stdout == 'app.py\n'


def test_new_index_path_activates_previously_unmatched_filter(local_repo, git_spawns):
    root = local_repo
    dependency = root.parent / 'filter-data'
    dependency.write_text('first filtered output\n')
    code = f'import sys; from pathlib import Path; sys.stdin.read(); sys.stdout.write(Path({str(dependency)!r}).read_text())'
    git(root, 'config', 'filter.dynamic.clean', shlex.join([PYTHON, '-c', code]))
    (root / '.gitattributes').write_text('*.psd filter=dynamic\n')
    query = ('diff', 'HEAD', '--', 'new.psd')
    git_spawns.clear()
    with store.read_session():
        reused(root, query, git_spawns, '', budget=5)
        (root / 'new.psd').write_text('worktree input\n')
        git(root, 'add', '--intent-to-add', 'new.psd')
        assert '+first filtered output\n' in store.git(root, *query).stdout
        dependency.write_text('changed filtered output\n')
        assert '+changed filtered output\n' in store.git(root, *query).stdout


def test_large_index_inventory_uses_fresh_reads(local_repo, git_spawns):
    root = local_repo
    git(root, 'config', 'filter.lfs.clean', 'missing-git-lfs clean -- %f')
    (root / '.gitattributes').write_text('*.psd filter=lfs\n')
    oid = git(root, 'rev-parse', 'HEAD:app.py')
    inventory = ''.join(f'100644 {oid}\tbulk-{number}.txt\n' for number in range(4097))
    result = subprocess.run(['git', 'update-index', '--index-info'], cwd=root,
                            input=inventory, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    query = ('config', '--get', 'user.name')
    git_spawns.clear()
    with store.read_session():
        assert store.git(root, *query).stdout == 'solo\n'
        assert store.git(root, *query).stdout == 'solo\n'
        assert git_spawns.count(query) == 2


@pytest.mark.parametrize('include', ['plain', 'gitdir', 'gitdir/i', 'onbranch', 'hasconfig'])
def test_include_dependencies_reuse_and_refresh(local_repo, git_spawns, include):
    root = local_repo
    settings = root.parent / 'settings'
    settings.write_text('[workorder]\nlabel = first\n')
    if include == 'plain':
        key = 'include.path'
    elif include == 'onbranch':
        key = 'includeIf.onbranch:main.path'
    elif include == 'hasconfig':
        git(root, 'config', 'remote.origin.url', 'https://example.invalid/project')
        key = 'includeIf.hasconfig:remote.*.url:https://example.invalid/**.path'
    else:
        key = f'includeIf.{include}:{root.as_posix()}/.git.path'
    git(root, 'config', key, '../../settings')
    query = ('config', '--get', 'workorder.label')
    git_spawns.clear()
    with store.read_session():
        reused(root, query, git_spawns, 'first\n')
        stamp = settings.stat()
        settings.write_text('[workorder]\nlabel = other\n')
        os.utime(settings, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert store.git(root, *query).stdout == 'other\n'
        assert store.git(root, *query).stdout == 'other\n'
        assert git_spawns.count(query) == 2


@pytest.mark.parametrize('missing', ['include', 'global', 'xdg', 'system'])
def test_creation_of_missing_configuration_invalidates_reuse(local_repo, monkeypatch, git_spawns, missing):
    root = local_repo
    if missing == 'include':
        settings = root.parent / 'new-settings'
        git(root, 'config', 'include.path', str(settings))
    elif missing == 'global':
        settings = Path(os.environ['HOME']) / '.gitconfig'
    elif missing == 'xdg':
        settings = Path(os.environ['XDG_CONFIG_HOME']) / 'git/config'
    else:
        settings = root.parent / 'system-config'
        monkeypatch.delenv('GIT_CONFIG_NOSYSTEM')
        monkeypatch.setenv('GIT_CONFIG_SYSTEM', str(settings))
    query = ('config', '--get', 'workorder.label')
    git_spawns.clear()
    with store.read_session():
        assert store.git(root, *query, required=False).returncode == 1
        assert store.git(root, *query, required=False).returncode == 1
        assert git_spawns.count(query) == 1
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('[workorder]\nlabel = created\n')
        assert store.git(root, *query).stdout == 'created\n'


def test_nested_tilde_include_and_symlink_target_changes(local_repo, git_spawns):
    root = local_repo
    folder = Path(os.environ['HOME']) / 'settings'
    folder.mkdir()
    first = folder / 'first config'
    first.write_text('[include]\npath = "second config"\n')
    target = root.parent / 'target'
    target.write_text('[workorder]\nlabel = first\n')
    try:
        (folder / 'second config').symlink_to(target)
    except OSError:
        pytest.skip('symlinks are unavailable')
    git(root, 'config', 'include.path', '~/settings/first config')
    query = ('config', '--get', 'workorder.label')
    git_spawns.clear()
    with store.read_session():
        reused(root, query, git_spawns, 'first\n')
        target.write_text('[workorder]\nlabel = second\n')
        assert store.git(root, *query).stdout == 'second\n'
        first.write_text('[include]\npath = "third config"\n')
        (folder / 'third config').write_text('[workorder]\nlabel = third\n')
        assert store.git(root, *query).stdout == 'third\n'


def test_conditional_include_activates_after_branch_change(local_repo, git_spawns):
    root = local_repo
    settings = root.parent / 'branch-settings'
    settings.write_text('[workorder]\nlabel = feature\n')
    git(root, 'config', 'includeIf.onbranch:feature.path', str(settings))
    git(root, 'config', 'workorder.label', 'main')
    query = ('config', '--get-all', 'workorder.label')
    git_spawns.clear()
    with store.read_session():
        reused(root, query, git_spawns, 'main\n')
        git(root, 'switch', '-qc', 'feature')
        assert store.git(root, *query).stdout == 'feature\nmain\n'


@pytest.mark.parametrize('kind', ['ignore', 'attributes'])
@pytest.mark.parametrize('location', ['explicit', 'global'])
@pytest.mark.parametrize('initially_missing', [False, True])
def test_external_ignore_and_attributes_dependencies(local_repo, git_spawns, kind, location, initially_missing):
    root = local_repo
    if location == 'explicit':
        settings = Path(os.environ['HOME']) / ('custom-' + kind)
        setting = 'core.excludesFile' if kind == 'ignore' else 'core.attributesFile'
        git(root, 'config', setting, '~/custom-' + kind)
    else:
        settings = Path(os.environ['XDG_CONFIG_HOME']) / 'git' / kind
    settings.parent.mkdir(parents=True, exist_ok=True)
    if not initially_missing:
        settings.write_text('')
    if kind == 'ignore':
        (root / 'hidden').write_text('data')
        query = ('ls-files', '--others', '--exclude-standard')
        before, changed, after = 'hidden\n', 'hidden\n', ''
    else:
        (root / 'app.py').write_bytes(b'items = []\r\n')
        query = ('diff', '--name-only', 'HEAD')
        before, changed, after = 'app.py\n', 'app.py text\n', ''
    git_spawns.clear()
    with store.read_session():
        reused(root, query, git_spawns, before)
        settings.write_text(changed)
        assert store.git(root, *query).stdout == after
        assert store.git(root, *query).stdout == after
        assert git_spawns.count(query) == 2


@pytest.mark.parametrize('injection', ['count', 'parameters'])
@pytest.mark.parametrize('isolated', [False, True])
def test_environment_includes_use_the_effective_environment(local_repo, monkeypatch, git_spawns, injection, isolated):
    root = local_repo
    settings = root.parent / 'environment-settings'
    settings.write_text('[workorder]\nlabel = injected\n')
    if injection == 'count':
        monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
        monkeypatch.setenv('GIT_CONFIG_KEY_0', 'include.path')
        monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(settings))
    else:
        value = ('include.path=' + str(settings)).replace("'", "'\\''")
        monkeypatch.setenv('GIT_CONFIG_PARAMETERS', "'" + value + "'")
    if isolated:
        monkeypatch.setenv('GIT_DIR', str(root.parent / 'nonexistent'))
    query = ('config', '--get', 'workorder.label')
    git_spawns.clear()
    with store.read_session():
        reused(root, query, git_spawns, 'injected\n', isolated=isolated)
        settings.write_text('[workorder]\nlabel = changed\n')
        assert store._git_run(root, *query, isolated=isolated).stdout == 'changed\n'


def test_hook_environment_does_not_disable_reuse(local_repo, monkeypatch, git_spawns):
    monkeypatch.setenv('GIT_EXEC_PATH', subprocess.check_output(['git', '--exec-path'], text=True).strip())
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'Author')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'author@example.invalid')
    monkeypatch.setenv('GIT_AUTHOR_DATE', '2000-01-01T00:00:00Z')
    monkeypatch.setenv('GIT_REFLOG_ACTION', 'commit')
    git_spawns.clear()
    with store.read_session():
        reused(local_repo, ('diff', '--name-only', 'HEAD'), git_spawns, '')


def system_attributes_path(root):
    result = subprocess.run(['git', 'var', 'GIT_ATTR_SYSTEM'], cwd=root,
                            capture_output=True, text=True)
    if result.returncode:
        pytest.skip('this Git version does not report GIT_ATTR_SYSTEM')
    return Path(result.stdout.strip())


@pytest.mark.parametrize('value', ['0', 'false', 'no', 'off', '', '00', '0x0', '0k'])
def test_false_system_attributes_flag_keeps_git_reported_dependency(local_repo, monkeypatch, value):
    from workorder import git_reads
    monkeypatch.setenv('GIT_ATTR_NOSYSTEM', value)
    system = system_attributes_path(local_repo)
    configurations = {}
    assert git_reads.repository_state(local_repo, configurations=configurations) is not None
    configuration = next(item for key, item in configurations.items() if key[0] == 'configuration')
    assert system in configuration['attributes']


@pytest.mark.parametrize('value', ['1', 'true', 'yes', 'on', '2', '-1', '0x1', '1k'])
def test_true_system_attributes_flag_omits_system_dependency(local_repo, monkeypatch, value):
    from workorder import git_reads
    system = system_attributes_path(local_repo)
    monkeypatch.setenv('GIT_ATTR_NOSYSTEM', value)
    configurations = {}
    assert git_reads.repository_state(local_repo, configurations=configurations) is not None
    configuration = next(item for key, item in configurations.items() if key[0] == 'configuration')
    assert system not in configuration['attributes']


@pytest.mark.parametrize('value', ['0', 'false', 'no', 'off', '', '00'])
def test_false_system_config_flag_observes_created_configuration(local_repo, monkeypatch, value):
    settings = local_repo.parent / 'system-config'
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', str(settings))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', value)
    query = ('config', '--get', 'workorder.label')
    with store.read_session():
        assert store.git(local_repo, *query, required=False).returncode == 1
        settings.write_text('[workorder]\nlabel = created\n')
        assert store.git(local_repo, *query).stdout == 'created\n'


@pytest.mark.parametrize('location', ['worktree', 'index', 'info', 'global', 'explicit'])
@pytest.mark.parametrize('driver', ['dynamic', 'dynamic\u00a0filter',
                                  pytest.param('dynamic\u0085filter', id='next-line'),
                                  pytest.param('dynamic\u2028filter', id='line-separator')])
@pytest.mark.parametrize('redirect_config', [False, True])
def test_active_filter_with_unobservable_dependencies_uses_fresh_git(local_repo, location, driver, redirect_config, monkeypatch, git_spawns):
    root = local_repo
    dependency = root.parent / 'filter-data'
    dependency.write_text('items = []\n')
    (root / 'app.py').write_text('worktree input differs\n')
    code = f'import sys; from pathlib import Path; sys.stdin.read(); sys.stdout.write(Path({str(dependency)!r}).read_text())'
    git(root, 'config', f'filter.{driver}.clean', shlex.join([PYTHON, '-c', code]))
    if location in {'worktree', 'index'}:
        attrs = root / '.gitattributes'
    elif location == 'info':
        attrs = root / '.git/info/attributes'
    elif location == 'global':
        attrs = Path(os.environ['XDG_CONFIG_HOME']) / 'git/attributes'
    else:
        attrs = root.parent / 'configured-attributes'
        git(root, 'config', 'core.attributesFile', str(attrs))
    attrs.parent.mkdir(parents=True, exist_ok=True)
    attrs.write_text(f'app.py filter={driver}\n')
    if location == 'index':
        git(root, 'add', '.gitattributes')
        attrs.unlink()
    if redirect_config:
        redirected = root.parent / 'redirected-config'
        redirected.write_text('')
        monkeypatch.setenv('GIT_CONFIG', str(redirected))
    query = ('diff', '--name-only', 'HEAD')
    git_spawns.clear()
    with store.read_session():
        assert store.git(root, *query).stdout == ''
        assert store.git(root, *query).stdout == ''
        assert git_spawns.count(query) == 2
        dependency.write_text('filtered output changed\n')
        assert store.git(root, *query).stdout == 'app.py\n'


def test_overridden_global_attributes_do_not_activate_unused_filter(local_repo, git_spawns):
    root = local_repo
    default = Path(os.environ['XDG_CONFIG_HOME']) / 'git/attributes'
    default.parent.mkdir(parents=True)
    default.write_text('app.py filter=lfs\n')
    explicit = root.parent / 'attributes'
    explicit.write_text('app.py text\n')
    git(root, 'config', 'core.attributesFile', str(explicit))
    git(root, 'config', 'filter.lfs.clean', 'missing-git-lfs clean -- %f')
    git_spawns.clear()
    with store.read_session():
        reused(root, ('diff', '--name-only', 'HEAD'), git_spawns, '', budget=4)


def test_deleted_shared_index_cannot_return_cached_index_entries(local_repo):
    root = local_repo
    git(root, 'update-index', '--split-index')
    shared = list((root / '.git').glob('sharedindex.*'))
    assert shared
    query = ('ls-files', '--stage')
    with store.read_session():
        assert 'app.py' in store.git(root, *query).stdout
        for path in shared:
            path.unlink()
        result = store.git(root, *query, required=False)
        assert result.returncode != 0
        assert 'sharedindex' in result.stderr


def test_gitfile_retarget_discovers_new_configuration_dependencies(local_repo):
    root = local_repo
    first, second = root.parent / 'first.git', root.parent / 'second.git'
    (root / '.git').rename(first)
    shutil.copytree(first, second)
    (root / '.git').write_text(f'gitdir: {first}\n')
    ignore = root.parent / 'external-ignore'
    ignore.write_text('')
    git(root, 'config', '--file', str(second / 'config'), 'core.excludesFile', str(ignore))
    (root / 'hidden').write_text('data')
    query = ('ls-files', '--others', '--exclude-standard')
    with store.read_session():
        assert store.git(root, *query).stdout == 'hidden\n'
        (root / '.git').write_text(f'gitdir: {second}\n')
        assert store.git(root, *query).stdout == 'hidden\n'
        ignore.write_text('hidden\n')
        assert store.git(root, *query).stdout == ''


def test_replacement_of_indexed_attributes_refreshes_helper_detection(local_repo):
    root = local_repo
    attrs = root / '.gitattributes'
    attrs.write_text('app.py text\n')
    git(root, 'add', '.gitattributes')
    original = git(root, 'rev-parse', ':.gitattributes')
    attrs.unlink()
    dependency = root.parent / 'filter-data'
    dependency.write_text('items = []\n')
    (root / 'app.py').write_text('worktree input differs\n')
    code = f'import sys; from pathlib import Path; sys.stdin.read(); sys.stdout.write(Path({str(dependency)!r}).read_text())'
    git(root, 'config', 'filter.dynamic.clean', shlex.join([PYTHON, '-c', code]))
    query = ('diff', '--name-only')
    with store.read_session():
        assert store.git(root, *query).stdout == '.gitattributes\napp.py\n'
        replacement = root.parent / 'replacement-attributes'
        replacement.write_text('app.py filter=dynamic\n')
        oid = git(root, 'hash-object', '-w', str(replacement))
        git(root, 'replace', original, oid)
        assert store.git(root, *query).stdout == '.gitattributes\n'
        dependency.write_text('external helper output changed\n')
        assert store.git(root, *query).stdout == '.gitattributes\napp.py\n'
