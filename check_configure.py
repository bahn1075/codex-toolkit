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


def check_configure(existing, state=None):
    doc = tomlkit.parse(existing)
    with patch.dict(c.os.environ, CT_PREFIX='/tools', CT_CODEX='/native/codex', CT_UV='/uv', CT_NPM='/npm', CT_NODE='/selected/node'), \
            patch.object(c, 'read_state', return_value=state or {}), \
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
    assert restored['mcp_servers']['context7']['command'] == '/selected/node'
    assert restored['tui']['status_line_use_colors'] is True
    assert restored['mcp_servers']['graft']['env']['PATH'].split(c.os.pathsep)[0] == '/selected'
    assert restored['features']['hooks'] is True
    assert 'codex_hooks' not in restored['features']
    assert '--enable-web-dashboard=false' in restored['mcp_servers']['serena']['args']
    assert '--open-web-dashboard=false' in restored['mcp_servers']['serena']['args']
    return restored


doc = check_configure('[features]\ncodex_hooks = true\nother = false\n')
assert doc['features']['other'] is False
assert doc['mcp_servers']['graft']['enabled'] is True
doc = check_configure(tomlkit.dumps(doc))
assert doc['mcp_servers']['graft']['enabled'] is True
doc = check_configure('[mcp_servers.graft]\nenabled = true\ntool_timeout_sec = 42\n')
assert doc['mcp_servers']['graft']['enabled'] is True
assert doc['mcp_servers']['graft']['tool_timeout_sec'] == 42
assert check_configure('[mcp_servers.graft]\nenabled = false\n')['mcp_servers']['graft']['enabled'] is True
assert check_configure('[mcp_servers.graft]\nenabled = false\n',
    {'graft_auto_start': True})['mcp_servers']['graft']['enabled'] is False
assert doc['mcp_servers']['mem0']['enabled'] is True
assert doc['tui']['status_line'] == [
    'model-with-reasoning', 'current-dir', 'thread-name', 'run-state',
    'five-hour-limit', 'weekly-limit', 'used-tokens',
    'estimated-thread-cost', 'task-progress',
]
assert doc['tui']['status_line_use_colors'] is True
assert 'serena_projects.py' in Path(__file__).with_name('toolkit.sh').read_text()
assert 'install_memory' not in Path(__file__).with_name('setup.sh').read_text()

computer_use_doc = tomlkit.parse('''[mcp_servers.node_repl.env]
SKY_CUA_SERVICE_PATH = "/Applications/Codex Computer Use.app"
[mcp_servers.computer-use]
command = "./Codex Computer Use.app/Contents/MacOS/client"
cwd = "."
''')
c.normalize_computer_use_path(computer_use_doc)
assert computer_use_doc['mcp_servers']['computer-use']['command'] == \
    '/Applications/Codex Computer Use.app/Contents/MacOS/client'

headroom_doc = tomlkit.parse('''model_provider = "headroom"
openai_base_url = "http://127.0.0.1:18787/v1"
[model_providers.headroom]
name = "Headroom persistent proxy"
[model_providers.other]
name = "Other provider"
''')
with patch.object(c, 'config', return_value=headroom_doc), patch.object(c, 'save_config'):
    c.direct_provider()
assert 'model_provider' not in headroom_doc
assert 'openai_base_url' not in headroom_doc
assert 'headroom' not in headroom_doc['model_providers']
assert headroom_doc['model_providers']['other']['name'] == 'Other provider'
toolkit = Path(__file__).with_name('toolkit.sh').read_text()
assert 'CT_HEADROOM_MODE=${CT_HEADROOM_MODE:-mcp}' in toolkit
assert 'CT_HEADROOM_MODE must be mcp.' in toolkit
assert "state['codex'] = os.environ['CT_CODEX']" in Path(__file__).with_name('configure.py').read_text()

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / 'home'
    home.mkdir()
    result = subprocess.run(['bash', '-c', r'''
set -Eeuo pipefail
source "$1/toolkit.sh"
mock_brew() {
  case "$*" in
    'list --formula codex'|'list --cask codex') return 0 ;;
    'uninstall --formula codex'|'uninstall --cask codex') printf '%s\n' "$*" ;;
    *) exit 99 ;;
  esac
}
mock_curl() {
  printf '%s\n' 'mkdir -p "$HOME/.local/bin"'
  printf '%s\n' 'printf "#!/bin/sh\\necho native-codex\\n" > "$HOME/.local/bin/codex"'
  printf '%s\n' 'chmod +x "$HOME/.local/bin/codex"'
}
CT_BREW=mock_brew
install_codex
printf 'CODEX=%s\n' "$CT_CODEX"
''', 'check', str(Path(__file__).resolve().parent)], env={**__import__('os').environ, 'HOME': str(home)},
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'uninstall --formula codex' in result.stdout
    assert 'uninstall --cask codex' in result.stdout
    assert f'CODEX={home}/.local/bin/codex' in result.stdout

with tempfile.TemporaryDirectory() as tmp:
    wallet = Path(tmp) / 'wallet'; wallet.mkdir()
    (wallet / 'tnsnames.ora').write_text('demo_medium = (DESCRIPTION=...)\n')
    target = Path(tmp) / 'mem0.json'
    with patch.object(c, 'MEM0_CONFIG', target), \
            patch('builtins.input', side_effect=[
                str(wallet), 'dbuser', 'demo_medium',
                'http://inference.example/api/v1/chat/completions',
                'http://embedding.example/api/v1/embedding']), \
            patch.object(c, 'getpass', side_effect=['secret', 'wallet-secret']), \
            patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as curl:
        c.mem0_settings()
        curl_calls = curl.call_count
    mem0 = json.loads(target.read_text())
    assert mem0['tns_alias'] == 'demo_medium'
    assert mem0['wallet_password'] == 'wallet-secret'
    assert mem0['inference_api_url'].endswith('/chat/completions')
    assert mem0['embedding_api_url'].endswith('/embedding')
    assert target.stat().st_mode & 0o777 == 0o600
    assert curl_calls == 2

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / 'mem0.json'
    target.write_text(json.dumps({
        'wallet_password': '',
        'inference_api_url': 'http://inference.example/api/v1/chat/completions',
        'embedding_api_url': 'http://embedding.example/api/v1/embedding',
    }))
    with patch.object(c, 'MEM0_CONFIG', target), \
            patch('builtins.input', side_effect=['', 'https://new.example/api/v1/embedding']), \
            patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as curl, \
            contextlib.redirect_stdout(io.StringIO()) as output:
        c.mem0_settings()
        curl_calls = curl.call_count
    mem0 = json.loads(target.read_text())
    assert mem0['inference_api_url'] == 'http://inference.example/api/v1/chat/completions'
    assert mem0['embedding_api_url'] == 'https://new.example/api/v1/embedding'
    assert 'http://inference.example/api/v1/chat/completions' in output.getvalue()
    assert 'http://embedding.example/api/v1/embedding' in output.getvalue()
    assert output.getvalue().count('기존값을 그대로 사용하시겠습니까?') == 2
    assert curl_calls == 2
    assert '입력하신 경로가 정상작동하였습니다. 해당 값으로 확정합니다' in output.getvalue()

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / 'mem0.json'
    target.write_text(json.dumps({
        'wallet_password': '',
        'inference_api_url': 'http://unavailable.example/v1/chat/completions',
        'embedding_api_url': 'http://embedding.example/v1/embedding',
    }))
    failed = subprocess.CompletedProcess([], 7, '', 'curl: (7) Connection refused')
    succeeded = subprocess.CompletedProcess([], 0, '{}', '')
    with patch.object(c, 'MEM0_CONFIG', target), \
            patch('builtins.input', side_effect=['', 'https://working.example/v1/chat/completions', '']), \
            patch.object(c.subprocess, 'run', side_effect=[failed, succeeded, succeeded]), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        c.mem0_settings()
    mem0 = json.loads(target.read_text())
    assert mem0['inference_api_url'] == 'https://working.example/v1/chat/completions'
    assert '해당 URL은 동작하지 않습니다. 올바른 URL을 다시 입력해주세요.' in output.getvalue()

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

with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'ROOT', Path(tmp)):
    c.npm_policy()
    manifest = Path(tmp) / 'npm/package.json'
    data = json.loads(manifest.read_text())
    assert data['allowScripts']['tree-sitter-kotlin'] is True
    assert data['allowScripts']['@davisvaughan/tree-sitter-r'] is True
    assert '*' not in data['allowScripts']
    data.update(dependencies={'example': '1.0.0'})
    data['allowScripts']['tree-sitter-kotlin'] = False
    manifest.write_text(json.dumps(data))
    c.npm_policy()
    assert json.loads(manifest.read_text()) == data

# Exercise the real stdio probe, including stderr and process cleanup, in a fresh Git repo.
with tempfile.TemporaryDirectory() as tmp:
    server = Path(tmp) / 'server.py'
    server.write_text('''import json, os, subprocess, sys, time
assert subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip() == os.getcwd()
mode = sys.argv[1]
if mode == 'crash':
    print('No native build was found: tree-sitter-kotlin', file=sys.stderr)
    sys.exit(1)
if mode == 'timeout':
    time.sleep(30)
for line in sys.stdin:
    message = json.loads(line)
    if message.get('method') == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {}, 'serverInfo': {'name': 'test', 'version': '1'}}
    elif message.get('method') == 'tools/list':
        result = {'tools': [] if mode == 'idle' else [{'name': 'test', 'inputSchema': {'type': 'object'}}]}
    else:
        continue
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
''')
    for mode in ('ok', 'crash', 'timeout', 'idle'):
        entry = {'command': c.sys.executable, 'args': [str(server), mode],
                 'enabled': False, 'cwd': '/nonexistent', 'startup_timeout_sec': 0.5}
        with patch.object(c, 'config', return_value={'mcp_servers': {'graft': entry}}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                c.verify_graft()
            except RuntimeError as exc:
                assert mode != 'ok'
                assert 'Graft MCP verification failed' in str(exc)
                if mode == 'crash':
                    assert 'No native build was found: tree-sitter-kotlin' in str(exc)
            else:
                assert mode == 'ok'
                assert 'MCP handshake and 1 tool definitions' in output.getvalue()

# Both entrypoints share finish(): a failed Graft probe must prevent the success message.
for failed in ('', 'verify-graft'):
    result = subprocess.run(['bash', '-c', '''
set -Eeuo pipefail
source "$1/toolkit.sh"
CT_PY=mock_python
SCRIPT_DIR=$1
failed=$2
mock_python() { [ "$2" != "$failed" ]; }
finish
''', 'check', str(Path(__file__).resolve().parent), failed], capture_output=True, text=True)
    assert (result.returncode == 0) == (not failed)
    assert ('Install/configuration pass finished' in result.stdout) == (not failed)

print('PASS: configuration, Graft build policy/runtime probe/failure gate, Kubernetes preflight, Superpowers handling.')
