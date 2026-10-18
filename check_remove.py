"""Offline removal checks: all writes/deletions stay in temporary homes."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import tomlkit

source = Path(__file__).resolve().parent
assert (source / 'remove.py').exists(), 'Toolkit removal is missing'
spec = importlib.util.spec_from_file_location('remove_toolkit', source / 'remove.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

with tempfile.TemporaryDirectory() as tmp:
    result = subprocess.run([sys.executable, '-S', str(source / 'remove.py'), '--dry-run'],
                            env={**os.environ, 'HOME': tmp}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'nothing removed' in result.stdout

with tempfile.TemporaryDirectory(prefix='toolkit remove ') as tmp:
    home = Path(tmp)
    codex = home / '.codex'
    root = codex / 'toolkit'
    root.mkdir(parents=True)
    native = codex / 'packages/standalone/current/bin/codex'
    native.parent.mkdir(parents=True)
    native.write_text('native binary')
    link = home / '.local/bin/codex'
    link.parent.mkdir(parents=True)
    link.symlink_to(native)
    (root / 'install-state.json').write_text(json.dumps({'codex': str(link)}))
    (root / 'mem0-sessions').mkdir()
    (root / 'mem0-sessions/pending.job.json').write_text('{}')
    (codex / 'config.toml').write_text(f'''model = "kept-model"
[features]
hooks = true
[shell_environment_policy.set]
PATH = "{root}/bin:/usr/bin"
[mcp_servers.serena]
command = "{root}/bin/serena"
[mcp_servers.headroom]
command = "{root}/bin/headroom-mcp"
[mcp_servers.mem0]
command = "{root}/bin/mem0-mcp"
[mcp_servers.custom]
command = "/keep/custom"
[mcp_servers.graft]
command = "{root}-custom/bin/graft"
''')
    (codex / 'AGENTS.md').write_text('User notes\n<!-- BEGIN CODEX-TOOLKIT -->\nmanaged\n<!-- END CODEX-TOOLKIT -->\n'
        '<!-- BEGIN PONYTAIL-UPSTREAM -->\nmanaged\n<!-- END PONYTAIL-UPSTREAM -->\n')
    rc_target = home / 'dotfiles/bashrc'
    rc_target.parent.mkdir()
    rc_target.write_text('export USER_SETTING=keep\n# BEGIN CODEX-TOOLKIT\nmanaged\n# END CODEX-TOOLKIT\n')
    (home / '.bashrc').symlink_to(rc_target)
    (codex / 'hooks.json').write_text(json.dumps({'hooks': {event: [{'hooks': [
        {'command': f"'{root}/bin/mem0-session'"}, {'command': '/keep/hook'}]}]
        for event in ('SessionEnd', 'Stop')}}))
    (codex / 'auth.json').write_text('keep authentication')
    (codex / 'mem0.json').write_text('private configuration')
    (codex / 'sessions').mkdir()
    (codex / 'sessions/history.jsonl').write_text('keep conversation')

    with contextlib.redirect_stdout(io.StringIO()):
        r.uninstall(home, dry_run=True)
    assert root.exists() and link.exists()
    assert not (home / '.codex-backups').exists()
    with contextlib.redirect_stdout(io.StringIO()):
        r.uninstall(home)
    assert not root.exists()
    assert not link.is_symlink()
    assert not (codex / 'packages/standalone').exists()
    doc = tomlkit.parse((codex / 'config.toml').read_text())
    assert doc['model'] == 'kept-model'
    assert set(doc['mcp_servers']) == {'custom', 'graft'}
    assert doc['shell_environment_policy']['set']['PATH'] == '/usr/bin'
    assert (codex / 'auth.json').read_text() == 'keep authentication'
    assert (codex / 'sessions/history.jsonl').exists()
    assert (home / '.bashrc').is_symlink()
    assert rc_target.read_text() == 'export USER_SETTING=keep\n'
    assert (codex / 'AGENTS.md').read_text().strip() == 'User notes'
    hooks = json.loads((codex / 'hooks.json').read_text())
    for event in ('SessionEnd', 'Stop'):
        assert hooks['hooks'][event][0]['hooks'] == [{'command': '/keep/hook'}]
    backups = list((home / '.codex-backups').glob('remove-*'))
    assert len(backups) == 1
    assert (backups[0] / 'mem0.json').read_text() == 'private configuration'
    assert (backups[0] / 'mem0-sessions/pending.job.json').exists()
    with contextlib.redirect_stdout(io.StringIO()):
        r.uninstall(home)  # Repeated removal is harmless.

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / 'home'
    home.mkdir()
    outside = Path(tmp) / 'outside'
    outside.mkdir()
    (home / '.codex').symlink_to(outside, target_is_directory=True)
    try:
        r.uninstall(home)
    except RuntimeError:
        pass
    else:
        raise AssertionError('symlinked Codex home must be refused')
    assert outside.exists()

print('PASS: removal dry run, managed components, retained user data, backup, symlinks, repeat removal.')

with tempfile.TemporaryDirectory(prefix='toolkit services % ') as tmp:
    home = Path(tmp)
    codex = home / '.codex'
    root = codex / 'toolkit'
    fake_bin = home / 'commands'
    fake_bin.mkdir()
    native = codex / 'packages/standalone/current/bin/codex'
    native.parent.mkdir(parents=True)
    fake_script = ('#!/bin/sh\n'
        'printf "%s\\n" "$*" >> "$REMOVE_TEST_LOG"\n'
        'if [ "$*" = "plugin list --json" ]; then\n'
        '  printf \'%s\\n\' \'{"installed":[{"name":"superpowers","marketplaceName":"openai-curated-remote","installed":true}]}\'\n'
        '  exit 0\n'
        'fi\n'
        '[ "$1" != plugin ] || [ "${REMOVE_TEST_FAIL:-0}" = 0 ]\n')
    native.write_text(fake_script)
    native.chmod(0o700)
    link = home / '.local/bin/codex'
    link.parent.mkdir(parents=True)
    link.symlink_to(native)
    (root / 'bin').mkdir(parents=True)
    (root / 'bin/headroom').write_text(fake_script)
    (root / 'bin/headroom').chmod(0o700)
    for name in ('code', 'systemctl'):
        path = fake_bin / name
        path.write_text(fake_script)
        path.chmod(0o700)
    (root / 'install-state.json').write_text(json.dumps({
        'codex': str(link), 'vscode_extension_installed': 'true'}))
    (codex / 'config.toml').write_text('model = "kept-model"\n')
    manifest = home / '.headroom/deploy/codex-toolkit/manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{}')
    units = home / '.config/systemd/user'
    units.mkdir(parents=True)
    (units / 'codex-mem0-retry.service').write_text(f'ExecStart={str(root).replace("%", "%%")}/bin/mem0-session --drain\n')
    (units / 'codex-mem0-retry.timer').write_text('timer')
    log = home / 'commands.log'
    with patch.dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ['PATH'],
                    XDG_CONFIG_HOME=str(home / '.config'), ZDOTDIR=str(home),
                    REMOVE_TEST_LOG=str(log), REMOVE_TEST_FAIL='1'):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                r.uninstall(home)
            except r.subprocess.CalledProcessError:
                pass
            else:
                raise AssertionError('failed component uninstall must retain runtimes for retry')
        assert root.exists() and link.exists()
        assert (codex / 'config.toml').read_text() == 'model = "kept-model"\n'
        os.environ['REMOVE_TEST_FAIL'] = '0'
        with contextlib.redirect_stdout(io.StringIO()):
            r.uninstall(home)
    calls = log.read_text()
    assert 'plugin remove superpowers@openai-curated-remote' in calls
    assert '--user disable --now codex-mem0-retry.timer' in calls
    assert 'install remove --profile codex-toolkit' in calls
    assert '--uninstall-extension openai.chatgpt' in calls
    assert not (units / 'codex-mem0-retry.service').exists()
    assert not root.exists()
print('PASS: plugin/service/extension cleanup and failed uninstall retry.')

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    root = home / '.codex/toolkit'
    root.mkdir(parents=True)
    native = home / '.local/bin/codex'
    native.parent.mkdir(parents=True)
    native.write_text('user replacement binary')
    (root / 'install-state.json').write_text(json.dumps({'codex': str(native)}))
    with contextlib.redirect_stdout(io.StringIO()):
        r.uninstall(home)
    assert native.exists(), 'do not delete a user replacement at the old native path'

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    root = home / '.codex/toolkit'
    root.mkdir(parents=True)
    (root / 'install-state.json').write_text('{}')
    (home / '.codex/mem0.json').write_text('configuration')
    unlink = Path.unlink
    def fail_mem0(path, *args, **kwargs):
        if path == home / '.codex/mem0.json':
            raise PermissionError('simulated removal failure')
        return unlink(path, *args, **kwargs)
    with patch.object(Path, 'unlink', fail_mem0), contextlib.redirect_stdout(io.StringIO()):
        try:
            r.uninstall(home)
        except PermissionError:
            pass
        else:
            raise AssertionError('file deletion failure must be reported')
    assert (root / 'install-state.json').exists(), 'retain uninstall state until every other deletion succeeds'
print('PASS: replacement binary preserved; late deletion failure retains uninstall state.')
