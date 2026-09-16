"""Run with ~/.codex/toolkit/venv/bin/python check_configure.py."""
import contextlib
import io
import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import tomlkit
import configure as c


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = Path(__file__).resolve().parent
    (root / 'setup.sh').write_text((source / 'setup.sh').read_text())
    (root / 'toolkit.sh').write_text(
        'source ' + shlex.quote(str(source / 'toolkit.sh')) + '\n'
        'CT_HOME="$SCRIPT_DIR/home"\nCT_ROOT="$CT_HOME/toolkit"\n'
        'preflight() { :; }\nacquire_lock() { :; }\n'
        'reset_codex() { exit 99; }\nbootstrap() { echo RESUMED; }\n'
        + ''.join(name + '() { :; }\n' for name in (
            'install_packages', 'configure_core', 'install_mem0',
            'configure_headroom', 'install_plugins', 'finish')))
    home = root / 'home'
    home.mkdir()
    marker = home / 'previous-home.txt'
    for has_marker in (False, True):
        if has_marker:
            marker.write_text('/previous-backup\n')
        result = subprocess.run(['bash', str(root / 'setup.sh'), '--resume'], capture_output=True, text=True)
        assert (result.returncode == 0) == has_marker
        assert ('RESUMED' in result.stdout) == has_marker
        assert not (home / 'toolkit/venv').exists()


class Saved(Exception):
    pass


def check_configure(existing):
    doc = tomlkit.parse(existing)
    with patch.dict(c.os.environ, CT_PREFIX='/tools', CT_UV='/uv', CT_NPM='/npm'), \
            patch.object(c, 'read_state', return_value={}), \
            patch.object(c, 'save_state'), \
            patch.object(c, 'executable', return_value='/node'), \
            patch.object(c, 'npm_binary', return_value='/package'), \
            patch.object(c, 'launcher', return_value='/launcher'), \
            patch.object(c, 'config', return_value=doc), \
            patch.object(c, 'save_config', side_effect=Saved):
        try:
            c.configure()
        except Saved:
            pass
    restored = tomlkit.parse(tomlkit.dumps(doc))
    assert restored['features']['hooks'] is True
    assert 'codex_hooks' not in restored['features']
    assert '--enable-web-dashboard=false' in restored['mcp_servers']['serena']['args']
    assert '--open-web-dashboard=false' in restored['mcp_servers']['serena']['args']
    return restored


doc = check_configure('[features]\ncodex_hooks = true\nother = false\n')
assert doc['features']['other'] is False
assert doc['mcp_servers']['graft']['enabled'] is False
doc = check_configure(tomlkit.dumps(doc))
assert doc['mcp_servers']['graft']['enabled'] is False
doc = check_configure('[mcp_servers.graft]\nenabled = true\ntool_timeout_sec = 42\n')
assert doc['mcp_servers']['graft']['enabled'] is True
assert doc['mcp_servers']['graft']['tool_timeout_sec'] == 42
assert doc['mcp_servers']['mem0']['enabled'] is True
assert 'install_memory' not in Path(__file__).with_name('setup.sh').read_text()

with tempfile.TemporaryDirectory() as tmp:
    wallet = Path(tmp) / 'wallet'; wallet.mkdir()
    (wallet / 'tnsnames.ora').write_text('demo_medium = (DESCRIPTION=...)\n')
    target = Path(tmp) / 'mem0.json'
    with patch.object(c, 'MEM0_CONFIG', target), \
            patch('builtins.input', side_effect=[str(wallet), 'dbuser', 'demo_medium']), \
            patch.object(c, 'getpass', side_effect=['secret', 'wallet-secret']):
        c.mem0_settings()
    assert json.loads(target.read_text())['tns_alias'] == 'demo_medium'
    assert json.loads(target.read_text())['wallet_password'] == 'wallet-secret'
    assert target.stat().st_mode & 0o777 == 0o600

with patch.object(c.shutil, 'which', return_value='/kubectl'), \
        patch.object(c.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
    for current, expected in [('', False), ('missing', False), ('chosen', True)]:
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({
            'current-context': current, 'contexts': [{'name': 'chosen'}, {'name': 'other'}]}))
        assert c.kubernetes_preflight({'env': {'KUBECONFIG': '/chosen/config'}}) is expected
        assert run.call_args.args[0] == ['/kubectl', 'config', 'view', '-o', 'json']
        assert run.call_args.kwargs['env']['KUBECONFIG'] == '/chosen/config'
    run.return_value = subprocess.CompletedProcess([], 1, '')
    assert c.kubernetes_preflight({}) is False
    run.side_effect = subprocess.TimeoutExpired('kubectl', 10)
    assert c.kubernetes_preflight({}) is False
    run.reset_mock()
    assert c.kubernetes_preflight({'args': ['--config', '/custom']}) is True
    run.assert_not_called()

for plugin_exit in (0, 1):
    result = subprocess.run(['bash', '-c', '''
set -Eeuo pipefail
source "$1/toolkit.sh"
mock_codex() {
  [ "$*" = 'plugin add superpowers@openai-curated-remote' ] || exit 99
  return "$plugin_exit"
}
CT_CODEX=mock_codex
plugin_exit=$2
install_plugins
printf 'CONTINUED'
''', 'check', str(Path(__file__).resolve().parent), str(plugin_exit)],
        capture_output=True, text=True, check=True)
    assert result.stdout.endswith('CONTINUED')
    assert ('Superpowers installation failed' in result.stderr) == bool(plugin_exit)

print('PASS: hooks migration, Graft defaults/preservation, Kubernetes local preflight, Superpowers install handling.')
