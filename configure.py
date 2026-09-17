#!/usr/bin/env python3
"""Local config editing and read-only MCP protocol checks. Python 3.13 + tomlkit."""
import json
import os
import pathlib
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import tempfile
import time
import urllib.request
from getpass import getpass

import tomlkit

HOME = pathlib.Path.home()
CODEX = HOME / '.codex'
ROOT = CODEX / 'toolkit'
STATE = ROOT / 'install-state.json'
BUNDLE = pathlib.Path(__file__).resolve().parent
CONFIG = CODEX / 'config.toml'
MEM0_CONFIG = CODEX / 'mem0.json'


def write(path, content, mode=0o600):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
    tmp.write_text(content, encoding='utf-8')
    tmp.chmod(mode)
    tmp.replace(path)


def read_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state):
    write(STATE, json.dumps(state, indent=2) + '\n')


def config():
    return tomlkit.parse(CONFIG.read_text()) if CONFIG.exists() else tomlkit.document()


def save_config(doc):
    text = tomlkit.dumps(doc)
    tomlkit.parse(text)
    write(CONFIG, text)


def table(parent, name):
    if name not in parent:
        parent[name] = tomlkit.table()
    return parent[name]


def executable(name):
    value = shutil.which(name)
    if not value:
        raise RuntimeError(f'Missing executable: {name}')
    # Preserve stable symlink paths: resolving Homebrew Cellar breaks after upgrades.
    return os.path.abspath(value)


def safe_path():
    paths = [os.path.dirname(os.environ.get('CT_NODE', '')),
             str(ROOT / 'bin'), str(ROOT / 'npm/node_modules/.bin')]
    paths.extend(os.environ['PATH'].split(os.pathsep))
    return os.pathsep.join(dict.fromkeys(p for p in paths if p.startswith('/')))


def npm_binary(package, expected=None):
    p = ROOT / 'npm/node_modules' / package / 'package.json'
    data = json.loads(p.read_text())
    bins = data.get('bin', {})
    if isinstance(bins, str):
        rel = bins
    elif expected in bins:
        rel = bins[expected]
    elif len(bins) == 1:
        rel = next(iter(bins.values()))
    else:
        raise RuntimeError(f'Cannot select executable for {package}: {list(bins)}')
    target = (p.parent / rel).resolve()
    if not target.is_file() or not target.is_relative_to(p.parent.resolve()):
        raise RuntimeError(f'Invalid npm binary path for {package}')
    return str(target)


def npm_policy():
    """Allow the known native parser builds in the private prefix, including npm 12."""
    path = ROOT / 'npm/package.json'
    data = json.loads(path.read_text()) if path.exists() else {}
    policy = data.setdefault('allowScripts', {})
    for name in (
        'tree-sitter', 'tree-sitter-cli', 'tree-sitter-go', 'tree-sitter-java',
        'tree-sitter-javascript', 'tree-sitter-kotlin', 'tree-sitter-php',
        'tree-sitter-python', '@davisvaughan/tree-sitter-r',
        'tree-sitter-swift', 'tree-sitter-typescript',
    ):
        # npm matches an alias by its real registry name. Preserve explicit denials.
        policy.setdefault(name, True)
    write(path, json.dumps(data, indent=2) + '\n')


def replace_block(text, tag, body):
    begin, end = f'<!-- BEGIN {tag} -->', f'<!-- END {tag} -->'
    if text.count(begin) != text.count(end) or text.count(begin) > 1:
        raise RuntimeError(f'Malformed managed block {tag}; refusing overwrite.')
    block = f'{begin}\n{body.rstrip()}\n{end}'
    if begin in text:
        return re.sub(re.escape(begin) + r'.*?' + re.escape(end), lambda _: block, text, flags=re.S)
    return text.rstrip() + '\n\n' + block + '\n'


def launcher(name, args, prefix=''):
    body = '#!/usr/bin/env bash\nset -euo pipefail\n'
    body += 'export PATH=' + shlex.quote(safe_path()) + '\n'
    body += prefix
    body += 'exec ' + shlex.join(args) + ' "$@"\n'
    path = ROOT / 'bin' / name
    write(path, body, 0o700)
    return str(path)


def configure():
    state = read_state()
    state['path'] = safe_path()
    state['codex'] = os.environ['CT_PREFIX'] + '/bin/codex'
    state['node'] = os.environ.get('CT_NODE') or executable('node')
    state['uv'] = os.environ['CT_UV']
    state['npm'] = os.environ['CT_NPM']
    graft = npm_binary('@nanonets/graft', 'graft')
    c7 = npm_binary('@upstash/context7-mcp', 'context7-mcp')
    graft_launch = launcher('graft-mcp',
        [sys.executable, str(BUNDLE / 'graft_mcp.py'), state['node'], graft])
    state['graft_binary'] = graft
    migrate_graft = not state.get('graft_auto_start', False)
    doc = config()
    # Keep login independent from any old OS keychain entry.
    doc['cli_auth_credentials_store'] = 'file'
    features = table(doc, 'features')
    features.pop('codex_hooks', None)
    features['hooks'] = True
    table(table(doc, 'shell_environment_policy'), 'set')['PATH'] = state['path']
    servers = table(doc, 'mcp_servers')
    specs = {
        'serena': (str(ROOT / 'bin/serena'), ['start-mcp-server', '--context=codex',
            '--enable-web-dashboard=false', '--open-web-dashboard=false']),
        'graft': (graft_launch, []),
        'kubernetes': (os.environ['CT_PREFIX'] + '/bin/kubernetes-mcp-server', []),
        'context7': (state['node'], [c7]),
        'headroom': (str(ROOT / 'bin/headroom'), ['mcp', 'serve']),
        'mem0': (launcher('mem0-mcp', [sys.executable, str(BUNDLE / 'mem0_mcp.py')]), []),
    }
    for name, (command, args) in specs.items():
        entry = table(servers, name)
        # Preserve later user-added limits, disabled_tools and approval settings.
        entry['command'] = command
        entry['args'] = args
        entry.setdefault('enabled', True)
        if name == 'graft' and migrate_graft:
            entry['enabled'] = True
        entry.setdefault('startup_timeout_sec', 120)
        entry.setdefault('tool_timeout_sec', 180)
        env = table(entry, 'env')
        env['PATH'] = state['path']
        if name == 'graft':
            env['CODEX_GRAFT_BUILD_TIMEOUT'] = str(min(90, max(.1, float(entry['startup_timeout_sec']) - 10)))
        if name == 'kubernetes':
            # Persist only explicitly configured kubeconfig file paths, never its contents.
            if os.environ.get('KUBECONFIG'):
                env['KUBECONFIG'] = os.pathsep.join(str(pathlib.Path(p).expanduser().absolute())
                    for p in os.environ['KUBECONFIG'].split(os.pathsep) if p)
            entry['env_vars'] = list(dict.fromkeys(list(entry.get('env_vars', [])) + ['KUBECONFIG']))
        if name == 'context7' and os.environ.get('CONTEXT7_API_KEY'):
            env['CONTEXT7_API_KEY'] = os.environ['CONTEXT7_API_KEY']
    save_config(doc)
    state['graft_auto_start'] = True
    save_state(state)
    instructions = CODEX / 'AGENTS.md'
    text = instructions.read_text() if instructions.exists() else ''
    text = replace_block(text, 'CODEX-TOOLKIT', (BUNDLE / 'policy.md').read_text())
    pony = (ROOT / 'repos/ponytail/AGENTS.md').read_text()
    text = replace_block(text, 'PONYTAIL-UPSTREAM', pony)
    write(instructions, text)
    # Aliases only select the same real Codex binary; no launch-time wrapping needed.
    init = ROOT / 'shell-init.sh'
    shell_prefix = os.pathsep.join([str(ROOT / 'bin'), str(ROOT / 'npm/node_modules/.bin'),
                                  os.environ['CT_PREFIX'] + '/bin', os.environ['CT_PREFIX'] + '/sbin'])
    init_text = ('# Generated Codex toolkit shell integration\n'
        + 'export PATH=' + shlex.quote(shell_prefix) + ':"$PATH"\n'
        + 'unalias codex 2>/dev/null || true\n'
        + 'alias codex=' + shlex.quote(shlex.quote(state['codex'])) + '\n'
        + 'alias codex-update=' + shlex.quote('bash ' + shlex.quote(str(ROOT / 'bundle/update.sh'))) + '\n'
        + 'alias codex-doctor=' + shlex.quote(shlex.quote(str(ROOT / 'bin/codex-doctor'))) + '\n')
    write(init, init_text)
    zdot = pathlib.Path(os.environ.get('ZDOTDIR') or str(HOME)).expanduser()
    for rc in dict.fromkeys([zdot / '.zshrc', HOME / '.bashrc', HOME / '.bash_profile']):
        current = rc.read_text() if rc.exists() else ''
        line = '[ ! -r ' + shlex.quote(str(init)) + ' ] || . ' + shlex.quote(str(init))
        start, end = '# BEGIN CODEX-TOOLKIT', '# END CODEX-TOOLKIT'
        if start in current and end not in current:
            raise RuntimeError(f'Malformed toolkit block in {rc}')
        updated = re.sub(re.escape(start) + r'.*?' + re.escape(end) + r'\n?', '', current, flags=re.S)
        updated = updated.rstrip() + '\n\n' + start + '\n' + line + '\n' + end + '\n'
        if updated != current:
            if rc.exists():
                backup = rc.with_name(rc.name + '.codex-backup-' + str(time.time_ns()))
                shutil.copy2(rc, backup)
            # Preserve an existing symlink to a dotfiles checkout.
            write(rc.resolve() if rc.is_symlink() else rc, updated,
                  (rc.stat().st_mode & 0o777) if rc.exists() else 0o600)
    launcher('codex-doctor', [sys.executable, str(ROOT / 'bundle/configure.py'), 'doctor'])
    launcher('mem0-import', [sys.executable, str(BUNDLE / 'mem0_import/import_memories.py')])
    launcher('mem0-import-prompt', ['bash', str(BUNDLE / 'mem0_import/import_memories.sh')])


def mem0_settings():
    """Collect the persistent Oracle connection only in an interactive installer."""
    if MEM0_CONFIG.exists():
        data = json.loads(MEM0_CONFIG.read_text())
        if 'wallet_password' not in data:
            data['wallet_password'] = getpass('Oracle wallet password (leave blank for auto-login wallet): ')
            write(MEM0_CONFIG, json.dumps(data, indent=2) + '\n')
        return
    wallet = pathlib.Path(input('Oracle wallet directory: ').strip()).expanduser()
    if not wallet.is_dir() or not (wallet / 'tnsnames.ora').is_file():
        raise RuntimeError('Wallet directory must contain tnsnames.ora.')
    username = input('Oracle database username: ').strip()
    password = getpass('Oracle database password: ')
    alias = input('Oracle TNS alias: ').strip()
    wallet_password = getpass('Oracle wallet password (leave blank for auto-login wallet): ')
    if not username or not password or not re.fullmatch(r'[A-Za-z0-9_.-]+', alias):
        raise RuntimeError('Username, password and a simple TNS alias are required.')
    if not re.search(rf'(?mi)^\s*{re.escape(alias)}\s*=', (wallet / 'tnsnames.ora').read_text()):
        raise RuntimeError(f'TNS alias {alias!r} was not found in tnsnames.ora.')
    write(MEM0_CONFIG, json.dumps({
        'wallet_dir': str(wallet.resolve()), 'username': username,
        'password': password, 'tns_alias': alias, 'wallet_password': wallet_password,
    }, indent=2) + '\n')


def configure_mem0_hooks():
    command = shlex.quote(launcher('mem0-session', [sys.executable, str(BUNDLE / 'mem0_session.py')]))
    path = CODEX / 'hooks.json'
    doc = json.loads(path.read_text()) if path.exists() else {}
    hooks = doc.setdefault('hooks', {})
    for event in ('SessionStart', 'SessionEnd'):
        groups = hooks.setdefault(event, [])
        for group in groups:
            group['hooks'] = [h for h in group['hooks'] if h.get('command') != command]
        groups[:] = [group for group in groups if group['hooks']]
        group = {'hooks': [{'type': 'command', 'command': command, 'timeout': 3}]}
        if event == 'SessionStart':
            group['matcher'] = 'startup|resume'
        groups.append(group)
    write(path, json.dumps(doc, indent=2) + '\n')


def remove_claude_mem():
    """Remove only claude-mem entries managed by this toolkit; retain its data for recovery."""
    doc = config()
    servers = table(doc, 'mcp_servers')
    for name in list(servers):
        entry = servers[name]
        raw = json.dumps(entry.unwrap() if hasattr(entry, 'unwrap') else entry)
        if name == 'claude_mem' or 'claude-mem' in raw:
            del servers[name]
    plugins = doc.get('plugins', {})
    for name in list(plugins):
        if 'claude-mem' in name:
            del plugins[name]
    save_config(doc)


def verify_proxy():
    manifest = HOME / '.headroom/deploy/codex-toolkit/manifest.json'
    data = json.loads(manifest.read_text())
    # A detached process alone does not meet the "after reboot without activation" goal.
    if data.get('supervisor_kind') not in {'service', 'task', 'launchd', 'cron', 'systemd'}:
        raise RuntimeError('Headroom has no supported persistent supervisor on this host.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:18787/health', timeout=10) as response:
        health = json.load(response)
    if health.get('deployment', {}).get('profile') != 'codex-toolkit':
        raise RuntimeError('Port 18787 does not belong to the requested Headroom profile.')
    doc = config()
    if doc.get('model_provider') != 'headroom':
        raise RuntimeError('Headroom installer did not route the Codex provider.')
    provider = doc.get('model_providers', {}).get('headroom')
    if not provider or not provider.get('base_url', '').startswith('http://127.0.0.1:18787/'):
        raise RuntimeError('Unexpected Headroom provider base URL.')
    # User will log in AFTER setup. Upstream cannot auto-detect OAuth yet.
    # Keep both keys INSIDE upstream's managed marker, so uninstall removes them.
    text = CONFIG.read_text()
    pattern = r'(?m)^\[model_providers\.headroom\][^\n]*\n'
    match = re.search(pattern, text)
    if not match:
        raise RuntimeError('Unknown Headroom table layout; refusing an ambiguous edit.')
    tail = text[match.end():]
    next_table = re.search(r'(?m)^\[', tail)
    boundary = next_table.start() if next_table else len(tail)
    section = re.sub(r'(?m)^[ \t]*(requires_openai_auth|wire_api)[ \t]*=.*\n?', '', tail[:boundary])
    updated = text[:match.end()] + 'requires_openai_auth = true\nwire_api = "responses"\n' + section + tail[boundary:]
    tomlkit.parse(updated)
    write(CONFIG, updated)


def direct_provider():
    doc = config()
    if doc.get('model_provider') == 'headroom':
        del doc['model_provider']
    if 'openai_base_url' in doc and '127.0.0.1:18787' in str(doc['openai_base_url']):
        del doc['openai_base_url']
    save_config(doc)


def kubernetes_preflight(entry):
    """Read local kubeconfig only; never select a context or contact a cluster."""
    if entry.get('args'):
        print('SKIP Kubernetes preflight: custom server arguments; doctor checks server startup.')
        return True
    env = os.environ.copy()
    env.update(entry.get('env', {}))
    kubectl = shutil.which('kubectl', path=env.get('PATH'))
    if not kubectl:
        print('SKIP Kubernetes preflight: kubectl unavailable; doctor checks server startup.')
        return True
    try:
        result = subprocess.run([kubectl, 'config', 'view', '-o', 'json'],
            env=env, capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError('unable to read kubeconfig')
        doc = json.loads(result.stdout)
        contexts = [item['name'] for item in doc.get('contexts', [])]
        current = doc.get('current-context')
        if not current or current not in contexts:
            print('PENDING Kubernetes: select a valid current-context in the MCP kubeconfig.')
            print('Available contexts: ' + ', '.join(contexts))
            print('Use kubectl config use-context <chosen-context> with the same KUBECONFIG.')
            return False
        print(f'PASS Kubernetes local context: {current} (cluster access not tested).')
        return True
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print('PENDING Kubernetes: cannot inspect local kubeconfig; check its path and syntax.')
        return False


def validate():
    state, doc = read_state(), config()
    for name in ['serena', 'graft', 'kubernetes', 'context7', 'headroom', 'mem0']:
        entry = doc['mcp_servers'][name]
        if not os.path.isabs(entry['command']) or not os.access(entry['command'], os.X_OK):
            raise RuntimeError(f'Missing executable for {name}')
    if (CODEX / 'AGENTS.md').stat().st_size > int(doc.get('project_doc_max_bytes', 32768)):
        raise RuntimeError('Global AGENTS.md exceeds instruction byte budget.')
    print('PASS: TOML, managed MCP executable paths, global instruction size.')
    kube = doc['mcp_servers']['kubernetes']
    if kube.get('enabled', True):
        kubernetes_preflight(kube)
    for key in ['mem0_install', 'headroom_runtime']:
        print(f'{key}: {state.get(key, "not completed")}')
    if not MEM0_CONFIG.exists():
        print('PENDING: Mem0 Oracle connection has not been configured.')
    print('NOT CHECKED here: Codex login, actual inference, Mem0 writes/searches and IDE behavior.')


def mcp_probe(name, entry, workdir, stderr=subprocess.DEVNULL):
    """Initialize + tools/list only: never invoke cluster or memory write tools."""
    q = queue.Queue()
    env = os.environ.copy()
    env.update(entry.get('env', {}))
    proc = subprocess.Popen([entry['command'], *entry.get('args', [])],
        cwd=entry.get('cwd') or workdir, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=stderr, text=True, start_new_session=True)
    def reader():
        for line in proc.stdout:
            try:
                q.put(json.loads(line))
            except json.JSONDecodeError:
                q.put({'protocol_error': True})
        q.put({'eof': True})
    threading.Thread(target=reader, daemon=True).start()
    def send(obj):
        proc.stdin.write(json.dumps(obj) + '\n')
        proc.stdin.flush()
    def wait_response(request_id, seconds=120):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                msg = q.get(timeout=max(.01, end - time.monotonic()))
            except queue.Empty:
                raise TimeoutError('MCP response timeout') from None
            if msg.get('eof') or msg.get('protocol_error'):
                raise RuntimeError('invalid stdio stream or early exit')
            if msg.get('method') and 'id' in msg:
                if msg['method'] == 'roots/list':
                    send({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'roots': [
                        {'uri': pathlib.Path(workdir).as_uri(), 'name': pathlib.Path(workdir).name}]}})
                else:
                    send({'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32601, 'message': 'Not supported by diagnostic client'}})
            elif msg.get('id') == request_id:
                if 'error' in msg:
                    raise RuntimeError('server returned JSON-RPC error')
                return msg['result']
        raise TimeoutError('MCP response timeout')
    try:
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {'roots': {'listChanged': False}},
            'clientInfo': {'name': 'codex-toolkit-doctor', 'version': '1.0'}}})
        wait_response(1, float(entry.get('startup_timeout_sec', 120)))
        send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        result = wait_response(2)
        count = len(result.get('tools', []))
        print(f'PASS {name}: MCP handshake and {count} tool definitions')
        return count
    finally:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        except ProcessLookupError:
            pass


def verify_graft():
    """Probe even when disabled, without requiring the installer to be a Git checkout."""
    entry = dict(config()['mcp_servers']['graft'])
    # Check the installed runtime in isolation; doctor checks the user's actual workspace.
    entry.pop('cwd', None)
    with tempfile.TemporaryDirectory(prefix='codex-graft-') as workdir, \
            tempfile.TemporaryFile(mode='w+t') as log:
        subprocess.run(['git', 'init', '--quiet', workdir], check=True)
        try:
            if not mcp_probe('graft', entry, workdir, stderr=log):
                raise RuntimeError('Graft advertised no tools after automatic index preparation')
        except Exception as exc:
            log.seek(0)
            details = log.read()[-8000:].strip()
            raise RuntimeError(
                f'Graft MCP verification failed: {type(exc).__name__}: {exc}\n{details}\n'
                'Check the native build output and Node/npm toolchain, then rerun setup.sh --resume.'
            ) from exc


def doctor():
    validate()
    failures = 0
    for name, entry in config().get('mcp_servers', {}).items():
        if not entry.get('command') or not entry.get('enabled', True):
            continue
        if name == 'kubernetes' and not kubernetes_preflight(entry):
            failures += 1
            continue
        print(f'Checking {name}...', flush=True)
        try:
            mcp_probe(name, entry, os.getcwd())
        except Exception as exc:
            print(f'FAIL {name}: {type(exc).__name__}: {exc}')
            failures += 1
    print('Plugin-bundled MCP/hook status must also be checked with Codex /mcp and /hooks.')
    if read_state().get('headroom_runtime') == 'configured':
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open('http://127.0.0.1:18787/health', timeout=5) as r:
                assert json.load(r).get('deployment', {}).get('profile') == 'codex-toolkit'
            print('PASS Headroom profile health (inference/token savings not tested).')
        except Exception:
            print('FAIL Headroom health'); failures += 1
    return 1 if failures else 0


def main():
    cmd, *args = sys.argv[1:]
    if cmd == 'state-get':
        print(read_state().get(args[0], ''))
    elif cmd == 'state-init':
        state = read_state()
        state.update(headroom_mode=os.environ['CT_HEADROOM_MODE'])
        save_state(state)
    elif cmd == 'record-packages':
        state = read_state()
        state['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        state['ponytail_commit'] = subprocess.check_output(['git', '-C', str(ROOT / 'repos/ponytail'), 'rev-parse', 'HEAD'], text=True).strip()
        state['npm_versions'] = {p: json.loads((ROOT / 'npm/node_modules' / p / 'package.json').read_text())['version']
            for p in ['@nanonets/graft', '@upstash/context7-mcp']}
        save_state(state)
    elif cmd == 'status':
        state = read_state(); state[args[0]] = args[1]; save_state(state)
    elif cmd == 'npm-policy': npm_policy()
    elif cmd == 'verify-graft': verify_graft()
    elif cmd == 'configure': configure()
    elif cmd == 'mem0-settings': mem0_settings()
    elif cmd == 'mem0-hooks': configure_mem0_hooks()
    elif cmd == 'remove-claude-mem': remove_claude_mem()
    elif cmd == 'verify-proxy': verify_proxy()
    elif cmd == 'direct-provider': direct_provider()
    elif cmd == 'validate': validate()
    elif cmd == 'doctor': return doctor()
    else: raise RuntimeError(f'Unknown command {cmd}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
