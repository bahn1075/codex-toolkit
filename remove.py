#!/usr/bin/env python3
"""Uninstall the private toolkit, retaining user data and unrelated integrations."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


def strip_block(text, start, end):
    if text.count(start) != text.count(end) or text.count(start) > 1:
        raise RuntimeError(f'Malformed managed block: {start}')
    return re.sub(re.escape(start) + r'.*?' + re.escape(end) + r'\n?', '', text, flags=re.S)


def uninstall(home, dry_run=False):
    home = Path(home).absolute()
    codex = home / '.codex'
    root = codex / 'toolkit'
    if home == Path('/') or codex.is_symlink() or root.is_symlink():
        raise RuntimeError('Refusing root HOME or a symlinked Codex/toolkit directory')
    state_path = root / 'install-state.json'
    if not state_path.exists():
        print('No toolkit installation state; nothing removed.')
        return
    import tomlkit

    state = json.loads(state_path.read_text())
    config_path = codex / 'config.toml'
    doc = tomlkit.parse(config_path.read_text()) if config_path.exists() else tomlkit.document()
    updates = []
    commands = []
    deletions = []
    backup_files = [state_path]
    # All edits are prepared before stopping services or deleting any files.
    servers = doc.get('mcp_servers', {})
    for name in ('serena', 'headroom', 'mem0', 'graft', 'context7', 'kubernetes'):
        entry = servers.get(name, {})
        if any(Path(os.path.normpath(str(value))).is_relative_to(root)
               for value in [entry.get('command', ''), *entry.get('args', [])]
               if os.path.isabs(str(value))):
            del servers[name]
    env = doc.get('shell_environment_policy', {}).get('set', {})
    if 'PATH' in env:
        env['PATH'] = os.pathsep.join(p for p in str(env['PATH']).split(os.pathsep)
                                    if p != str(root) and not p.startswith(str(root) + os.sep))
    providers = doc.get('model_providers', {})
    provider = providers.get('headroom', {})
    if '127.0.0.1:18787' in str(provider.get('base_url', '')):
        del providers['headroom']
        if doc.get('model_provider') == 'headroom':
            del doc['model_provider']
    if '127.0.0.1:18787' in str(doc.get('openai_base_url', '')):
        del doc['openai_base_url']
    native_link = home / '.local/bin/codex'
    native_packages = codex / 'packages/standalone'
    native_owned = state.get('codex') == str(native_link) and (
        (native_link.is_symlink() and native_link.resolve().is_relative_to(native_packages.resolve()))
        or (not native_link.is_symlink() and not native_link.exists()))
    plugin = 'superpowers@openai-curated-remote'
    plugin_installed = plugin in doc.get('plugins', {})
    if not plugin_installed and native_owned and os.access(native_link, os.X_OK):
        listing = subprocess.run([str(native_link), 'plugin', 'list', '--json'],
                                 check=True, capture_output=True, text=True, timeout=30)
        plugin_installed = any(p.get('name') == 'superpowers'
                               and p.get('marketplaceName') == 'openai-curated-remote'
                               and p.get('installed')
                               for p in json.loads(listing.stdout).get('installed', []))
    if plugin_installed:
        if not native_owned or not native_link.exists():
            raise RuntimeError('Installed Superpowers requires the managed Codex binary for removal')
        commands.append([str(native_link), 'plugin', 'remove', plugin])
        if plugin in doc.get('plugins', {}):
            del doc['plugins'][plugin]
    if config_path.exists():
        updates.append((config_path, tomlkit.dumps(doc)))
    agents = codex / 'AGENTS.md'
    if agents.exists():
        text = agents.read_text()
        for tag in ('CODEX-TOOLKIT', 'PONYTAIL-UPSTREAM'):
            text = strip_block(text, f'<!-- BEGIN {tag} -->', f'<!-- END {tag} -->')
        updates.append((agents, text))
    hooks_path = codex / 'hooks.json'
    if hooks_path.exists():
        hooks = json.loads(hooks_path.read_text())
        owned_hooks = {str(root / 'bin/mem0-session'), str(root / 'bundle/mem0_session.py')}
        for event, groups in hooks.get('hooks', {}).items():
            for group in groups:
                group['hooks'] = [h for h in group.get('hooks', [])
                                  if not owned_hooks.intersection(shlex.split(h.get('command', '')))]
            groups[:] = [g for g in groups if g['hooks']]
        updates.append((hooks_path, json.dumps(hooks, indent=2) + '\n'))
    zdot = Path(os.environ.get('ZDOTDIR') or home).expanduser()
    for rc in dict.fromkeys((zdot / '.zshrc', home / '.bashrc', home / '.bash_profile')):
        if rc.exists():
            updates.append((rc, strip_block(rc.read_text(), '# BEGIN CODEX-TOOLKIT', '# END CODEX-TOOLKIT')))
    units = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config') / 'systemd/user'
    service = units / 'codex-mem0-retry.service'
    if service.exists() and str(root / 'bin/mem0-session') in service.read_text().replace('%%', '%'):
        if shutil.which('systemctl'):
            probe = subprocess.run(['systemctl', '--user', 'show-environment'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if probe.returncode == 0:
                commands.append(['systemctl', '--user', 'disable', '--now', 'codex-mem0-retry.timer'])
                commands.append(['systemctl', '--user', 'stop', 'codex-mem0-retry.service'])
        for name in ('codex-mem0-retry.service', 'codex-mem0-retry.timer',
                     'timers.target.wants/codex-mem0-retry.timer'):
            deletions.append(units / name)
    manifest = home / '.headroom/deploy/codex-toolkit/manifest.json'
    if manifest.exists():
        headroom = root / 'bin/headroom'
        if not headroom.exists():
            raise RuntimeError('Headroom deployment exists but its removal command is missing')
        commands.append([str(headroom), 'install', 'remove', '--profile', 'codex-toolkit'])
    if state.get('vscode_extension_installed') and shutil.which('code'):
        commands.append(['code', '--uninstall-extension', 'openai.chatgpt'])
    if native_owned:
        if not native_packages.resolve().is_relative_to(codex.resolve()):
            raise RuntimeError('Native Codex package directory escapes the Codex home')
        deletions.extend((native_link, native_packages))
    elif state.get('codex'):
        print('Preserving Codex binary outside the managed native installation.')
    for path in (codex / 'mem0.json', codex / 'previous-home.txt', root / 'mem0-sessions'):
        if path.exists():
            backup_files.append(path)
        if path.parent == codex:
            deletions.append(path)
    for path, _ in updates:
        backup_files.append(path)
    # Keep the interpreter and installation ledger available until other removals succeed.
    deletions.append(root)
    print('Remove managed software:', ', '.join(str(p) for p in deletions))
    for cmd in commands:
        print('Run:', shlex.join(cmd))
    if dry_run:
        print('Dry run: no files changed or components removed.')
        return
    backup_root = home / '.codex-backups'
    if backup_root.is_symlink():
        raise RuntimeError('Refusing a symlinked backup directory')
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='remove-', dir=backup_root))
    for index, path in enumerate(backup_files):
        if not path.exists():
            continue
        name = path.name if path.parent in (codex, root) else f'{index}-{path.name}'
        target = backup / name
        if path.is_dir():
            shutil.copytree(path, target, symlinks=True)
        else:
            shutil.copy2(path, target)
            target.chmod(0o600)
    # Failures stop here with the private runtimes still available for a retry.
    for cmd in commands:
        subprocess.run(cmd, check=True)
    for path, content in updates:
        target = path.resolve() if path.is_symlink() else path
        mode = target.stat().st_mode & 0o777
        tmp = target.with_name(target.name + f'.remove-{os.getpid()}')
        tmp.write_text(content)
        tmp.chmod(mode)
        tmp.replace(target)
    for path in deletions:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    if service in deletions and shutil.which('systemctl'):
        subprocess.run(['systemctl', '--user', 'daemon-reload'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f'Removed toolkit software and integration. Configuration/queued work backup: {backup}')
    print('Preserved: login/history, other plugins, shared prerequisites, model caches and Oracle memories.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    uninstall(Path.home(), args.dry_run)
