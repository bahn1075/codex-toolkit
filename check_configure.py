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

model_response = json.dumps({'choices': [{'message': {'content': 'pong'}}],
                             'data': [{'embedding': [0.1] * 1024}]})


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
            patch.object(c, 'launcher', return_value='/launcher'), \
            patch.object(c, 'config', return_value=doc), \
            patch.object(c, 'save_config', side_effect=Saved):
        try:
            c.configure()
        except Saved:
            pass
    restored = tomlkit.parse(tomlkit.dumps(doc))
    assert set(restored['mcp_servers']) == {'serena', 'headroom', 'mem0'}
    assert restored['mcp_servers']['headroom']['env']['HEADROOM_MODE'] == 'token'
    assert restored['mcp_servers']['headroom']['env']['HEADROOM_SAVINGS_PROFILE'] == 'balanced'
    assert restored['mcp_servers']['headroom']['env']['HEADROOM_MCP_READ'] == 'on'
    assert restored['mcp_servers']['headroom']['env']['HEADROOM_KOMPRESS_BACKEND'] == (state or {}).get('headroom_hardware', {}).get('backend', 'onnx_cpu')
    assert restored['tui']['status_line_use_colors'] is True
    assert restored['features']['hooks'] is True
    assert 'codex_hooks' not in restored['features']
    assert '--enable-web-dashboard=false' in restored['mcp_servers']['serena']['args']
    assert '--open-web-dashboard=false' in restored['mcp_servers']['serena']['args']
    return restored


doc = check_configure('[features]\ncodex_hooks = true\nother = false\n')
assert doc['features']['other'] is False
assert 'graft' not in doc['mcp_servers']
doc = check_configure(tomlkit.dumps(doc))
for backend in ('onnx_coreml', 'pytorch'):
    check_configure(tomlkit.dumps(doc), {'headroom_hardware': {'backend': backend}})
assert 'graft' not in doc['mcp_servers']
doc = check_configure('[mcp_servers.graft]\nenabled = true\ntool_timeout_sec = 42\n')
assert 'graft' not in doc['mcp_servers']
legacy_doc = check_configure('''[mcp_servers.kubernetes]
command = "/legacy/kubernetes"

[mcp_servers.context7]
command = "/legacy/context7"
''')
assert 'kubernetes' not in legacy_doc['mcp_servers']
assert 'context7' not in legacy_doc['mcp_servers']

assert doc['mcp_servers']['mem0']['enabled'] is True
assert set(doc['mcp_servers']) == {'serena', 'headroom', 'mem0'}
assert doc['mcp_servers']['headroom']['enabled'] is True
assert doc['mcp_servers']['headroom']['env']['HEADROOM_MODE'] == 'token'
assert doc['mcp_servers']['headroom']['env']['HEADROOM_SAVINGS_PROFILE'] == 'balanced'
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

status_doc = tomlkit.parse('''[mcp_servers.serena]
command = "/tools/serena"

[mcp_servers.graft]
command = "/tools/graft"
enabled = false

[mcp_servers.headroom]
command = "/tools/headroom"

[mcp_servers.headroom.env]
HEADROOM_MODE = "token"
HEADROOM_SAVINGS_PROFILE = "balanced"

[mcp_servers.mem0]
command = "/tools/mem0"
''')
with patch.object(c, 'config', return_value=status_doc), \
        patch.object(c, 'read_state', return_value={'headroom_runtime': 'mcp-only'}), \
        contextlib.redirect_stdout(io.StringIO()) as output:
    c.final_status()
assert output.getvalue().splitlines() == [
    'MCP serena: enabled',
    'MCP headroom: enabled',
    'MCP mem0: enabled',
    'Headroom: MCP-only, token mode, balanced profile; backend=onnx_cpu, read cache enabled.',
    'Excluded MCPs: graft, kubernetes, context7.',
]
with patch.object(c, 'config', return_value=status_doc), \
        patch.object(c, 'read_state', return_value={'headroom_runtime': 'configured'}):
    try:
        c.final_status()
    except RuntimeError as exc:
        assert str(exc) == 'Headroom is not configured for MCP-only operation.'
    else:
        raise AssertionError('final status must reject a Headroom proxy configuration')

toolkit = Path(__file__).with_name('toolkit.sh').read_text()
assert 'CT_HEADROOM_MODE=${CT_HEADROOM_MODE:-mcp}' in toolkit
assert 'CT_HEADROOM_MODE must be mcp.' in toolkit
assert "state['codex'] = os.environ['CT_CODEX']" in Path(__file__).with_name('configure.py').read_text()

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    toolkit_root = root / 'toolkit'
    repo = toolkit_root / 'repos/ponytail'
    (repo / '.git').mkdir(parents=True)
    (repo / 'AGENTS.md').write_text('Ponytail test instructions\n')
    serena = toolkit_root / 'bin/serena'
    serena.parent.mkdir(parents=True)
    serena.write_text('#!/usr/bin/env bash\nexit 0\n')
    serena.chmod(0o755)
    serena_python = toolkit_root / 'uv-tools/serena-agent/bin/python'
    serena_python.parent.mkdir(parents=True)
    serena_python.write_text('#!/usr/bin/env bash\nexit 0\n')
    serena_python.chmod(0o755)
    npm_log = root / 'npm.log'
    result = subprocess.run(['bash', '-c', r'''
set -Eeuo pipefail
source "$1/toolkit.sh"
mock_brew() { printf 'BREW %s\n' "$*"; }
mock_uv() { printf 'UV %s\n' "$*"; }
mock_npm() { printf 'NPM %s\n' "$*"; printf '%s\n' "$*" >> "$NPM_LOG"; }
git() {
  case "$*" in
    *'status --porcelain'|*'pull --ff-only') : ;;
    *'remote get-url origin') printf '%s\n' 'https://github.com/dietrichgebert/ponytail.git' ;;
    *) : ;;
  esac
}
install_codex() { :; }
SCRIPT_DIR=$1
CT_ROOT=$2
CT_BREW=mock_brew
CT_UV=mock_uv
CT_NPM=mock_npm
CT_PY=:
CT_BOOT_PY=/python
NPM_LOG=$3
install_packages
''', 'check', str(Path(__file__).resolve().parent), str(toolkit_root), str(npm_log)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '@nanonets/graft@latest' not in result.stdout
    assert 'install --prefix' not in result.stdout
    assert 'kubernetes-mcp-server' not in result.stdout
    assert '@upstash/context7-mcp@latest' not in result.stdout

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
curl() {
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
            patch.object(c, 'select_model', side_effect=lambda url, kind: ('http://server/v1', 'served-' + kind)), \
            patch('builtins.input', side_effect=[
                str(wallet), 'dbuser', 'demo_medium',
                'http://inference.example/api/v1/chat/completions',
                'http://secondary.example/v1/chat/completions',
                'http://embedding.example/api/v1/embedding',
                'http://secondary.example/v1/embeddings']), \
            patch.object(c, 'getpass', side_effect=['secret', 'wallet-secret']), \
            patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, model_response, '')) as curl:
        c.mem0_settings()
        curl_calls = curl.call_count
    mem0 = json.loads(target.read_text())
    assert mem0['tns_alias'] == 'demo_medium'
    assert mem0['wallet_password'] == 'wallet-secret'
    assert mem0['inference_api_url'].endswith('/chat/completions')
    assert mem0['embedding_api_url'].endswith('/embedding')
    assert target.stat().st_mode & 0o777 == 0o600
    assert curl_calls == 4

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / 'mem0.json'
    target.write_text(json.dumps({
        'wallet_password': '',
        'inference_api_url': 'http://inference.example/api/v1/chat/completions',
        'embedding_api_url': 'http://embedding.example/api/v1/embedding',
    }))
    with patch.object(c, 'MEM0_CONFIG', target), \
            patch.object(c, 'select_model', side_effect=lambda url, kind: ('http://server/v1', 'served-' + kind)), \
            patch('builtins.input', side_effect=['', 'http://secondary.example/v1/chat/completions',
                'https://new.example/api/v1/embeddings', 'http://secondary.example/v1/embeddings']), \
            patch.object(c.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, model_response, '')) as curl, \
            contextlib.redirect_stdout(io.StringIO()) as output:
        c.mem0_settings()
        curl_calls = curl.call_count
    mem0 = json.loads(target.read_text())
    assert mem0['inference_api_url'] == 'http://inference.example/api/v1/chat/completions'
    assert mem0['embedding_api_url'] == 'https://new.example/api/v1/embeddings'
    assert 'http://HOST:1234/v1/chat/completions' in output.getvalue()
    assert 'http://HOST:1234/v1/embeddings' in output.getvalue()
    assert output.getvalue().count('기존값을 그대로 사용하시겠습니까?') == 2
    assert curl_calls == 4
    assert '입력하신 경로가 정상작동하였습니다. 해당 값으로 확정합니다' in output.getvalue()

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / 'mem0.json'
    target.write_text(json.dumps({
        'wallet_password': '',
        'inference_api_url': 'http://unavailable.example/v1/chat/completions',
        'embedding_api_url': 'http://embedding.example/v1/embedding',
    }))
    failed = subprocess.CompletedProcess([], 7, '', 'curl: (7) Connection refused')
    succeeded = subprocess.CompletedProcess([], 0, model_response, '')
    with patch.object(c, 'MEM0_CONFIG', target), \
            patch.object(c, 'select_model', side_effect=lambda url, kind: ('http://server/v1', 'served-' + kind)), \
            patch('builtins.input', side_effect=['', 'https://working.example/v1/chat/completions',
                'http://secondary.example/v1/chat/completions', '', 'http://secondary.example/v1/embeddings']), \
            patch.object(c.subprocess, 'run', side_effect=[
                failed, succeeded, succeeded, succeeded, succeeded]), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        c.mem0_settings()
    mem0 = json.loads(target.read_text())
    assert mem0['inference_api_url'] == 'https://working.example/v1/chat/completions'
    assert '해당 URL은 동작하지 않습니다. 올바른 URL을 다시 입력해주세요.' in output.getvalue()

with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / 'mem0.json'
    target.write_text(json.dumps({
        'wallet_password': '',
        'inference_api_url': 'http://inference.example/v1/chat/completions',
        'embedding_api_url': 'http://embedding.example/v1/chat/embedding',
    }))
    with patch.object(c, 'MEM0_CONFIG', target), \
            patch.object(c, 'select_model', side_effect=lambda url, kind: ('http://server/v1', 'served-' + kind)), \
            patch('builtins.input', side_effect=[
                '', 'http://secondary.example/v1/chat/completions',
                'http://embedding.example/v1/chat/embedding',
                'http://embedding.example/v1/embeddings', 'http://secondary.example/v1/embeddings']), \
            patch.object(c.subprocess, 'run', side_effect=[
                subprocess.CompletedProcess([], 0, model_response, ''),
                subprocess.CompletedProcess([], 0, model_response, ''),
                subprocess.CompletedProcess([], 0, model_response, ''),
                subprocess.CompletedProcess([], 0, model_response, '')]):
        c.mem0_settings()
    mem0 = json.loads(target.read_text())
    assert mem0['embedding_api_url'] == 'http://embedding.example/v1/embeddings'

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

# Both entrypoints share finish(): failed diagnostics must prevent the success message.
for failed in ('', 'doctor'):
    result = subprocess.run(['bash', '-c', '''
set -Eeuo pipefail
source "$1/toolkit.sh"
CT_PY=mock_python
CT_CODEX=mock_codex
SCRIPT_DIR=$1
failed=$2
mock_python() { [ "$2" != "$failed" ]; }
mock_codex() { [ "$*" = 'mcp list' ]; }
finish
''', 'check', str(Path(__file__).resolve().parent), failed], capture_output=True, text=True)
    assert (result.returncode == 0) == (not failed)
    assert ('Install/configuration pass finished' in result.stdout) == (not failed)

print('PASS: configuration, excluded Graft, Headroom read cache, diagnostics gate, Superpowers handling.')

with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    codex = home / '.codex'
    root = codex / 'toolkit'
    pony = root / 'repos/ponytail/AGENTS.md'
    pony.parent.mkdir(parents=True)
    pony.write_text('Ponytail instructions\n')
    (root / 'npm').mkdir()
    (root / 'npm/old-graft').write_text('obsolete private package')
    (root / 'bin').mkdir()
    (root / 'bin/graft-mcp').write_text('obsolete launcher')
    with patch.object(c, 'HOME', home), patch.object(c, 'CODEX', codex), \
            patch.object(c, 'ROOT', root), patch.object(c, 'STATE', root / 'install-state.json'), \
            patch.object(c, 'CONFIG', codex / 'config.toml'), \
            patch.dict(c.os.environ, CT_PREFIX='/tools', CT_CODEX='/native/codex', CT_UV='/uv', ZDOTDIR=str(home)):
        c.write(c.CONFIG, '[mcp_servers.graft]\ncommand = "/old/graft"\n')
        c.configure()
        assert 'graft' not in c.config()['mcp_servers']
        assert not (root / 'npm').exists() and not (root / 'bin/graft-mcp').exists()
        assert 'headroom_read' in (codex / 'AGENTS.md').read_text()
        doc = c.config()
        doc['mcp_servers']['headroom']['env']['HEADROOM_MCP_READ'] = 'off'
        c.save_config(doc)
        with patch.object(c.os, 'access', return_value=True), contextlib.redirect_stdout(io.StringIO()):
            try:
                c.validate()
            except RuntimeError as exc:
                assert 'read cache' in str(exc)
            else:
                raise AssertionError('installation must reject a disabled Headroom read cache')
print('PASS: real config migration removes legacy Graft and enforces Headroom read cache.')
