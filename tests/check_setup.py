from _paths import ROOT
import os
import shlex
import subprocess
import tempfile
from pathlib import Path


def test_setup_preserves_existing_home(*options, script='setup.sh', has_mem0=True):
    source = ROOT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / 'home'
        codex = home / '.codex'
        (codex / 'sessions').mkdir(parents=True)
        (codex / 'sessions' / 'conversation.jsonl').write_text('conversation')
        (codex / 'auth.json').write_text('login')
        (codex / 'config.toml').write_text('user settings')
        mem0 = codex / 'mem0.json'
        stored = b'{"litellm":{"base_url":"https://gateway.example/v1","api_key":"test-key"}}\n'
        if has_mem0:
            mem0.write_bytes(stored)
        if script == 'update.sh':
            (codex / 'toolkit').mkdir()
            (codex / 'toolkit' / 'install-state.json').write_text('{}')
        agy_plugin = home / '.gemini/config/plugins/codex-toolkit'
        agy_plugin.mkdir(parents=True)
        (agy_plugin / 'plugin.json').write_text('{"name":"codex-toolkit"}\n')
        (root / script).write_text((source / script).read_text())
        (root / 'lib').mkdir()
        (root / 'lib/toolkit.sh').write_text(
            'source ' + shlex.quote(str(source / 'lib/toolkit.sh')) + '\n'
            'preflight() { echo "$*" > "$HOME/preflight-args"; }\nacquire_lock() { :; }\n'
            'remove_homebrew_codex() { :; }\nnpm() { return 1; }\n'
            'bootstrap() { mkdir -p "$CT_ROOT"; echo "{}" > "$CT_ROOT/install-state.json"; CT_PY=mock_python; }\n'
            'install_packages() { mkdir -p "$HOME/.local/bin"; echo updated > "$HOME/.local/bin/codex"; echo updated-agy > "$HOME/.local/bin/agy"; }\n'
            'mock_python() { echo "$2" >> "$HOME/mem0-steps"; }\n'
            'configure_core() { echo "$*" > "$HOME/core-args"; }\n'
            + ''.join(name + '() { :; }\n' for name in (
                'configure_headroom', 'install_plugins', 'finish')))
        result = subprocess.run(['bash', str(root / script)] + list(options),
                                env={**os.environ, 'HOME': str(home), 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (codex / 'sessions' / 'conversation.jsonl').read_text() == 'conversation'
        assert (codex / 'auth.json').read_text() == 'login'
        assert (codex / 'config.toml').read_text() == 'user settings'
        backups = list((home / '.codex-backups').glob('update-*/config.toml'))
        assert len(backups) == 1 and backups[0].read_text() == 'user settings'
        agy_backups = list((home / '.codex-backups').glob('update-*/agy-codex-toolkit/plugin.json'))
        assert len(agy_backups) == 1 and '"codex-toolkit"' in agy_backups[0].read_text()
        assert (home / '.local/bin/codex').read_text().strip() == 'updated'
        assert (home / '.local/bin/agy').read_text().strip() == 'updated-agy'
        steps = home / 'mem0-steps'
        updating = '--update' in options or script == 'update.sh'
        if has_mem0:
            assert steps.read_text().splitlines() == ['mem0-hooks', 'status']
            assert mem0.read_bytes() == stored
        elif updating:
            assert not steps.exists() and not mem0.exists()
        else:
            assert steps.read_text().splitlines() == ['mem0-settings', 'mem0-hooks', 'status']
        assert (home / 'core-args').read_text().strip() == ''
        assert (home / 'preflight-args').read_text().strip() == ('' if script == 'update.sh' else '--stop-codex')
        if options == ('--update',) and has_mem0:
            result = subprocess.run(['bash', str(root / script), '--resume'],
                                    capture_output=True, text=True)
            assert result.returncode == 1 and 'Unknown option: --resume' in result.stderr


if __name__ == '__main__':
    test_setup_preserves_existing_home()
    test_setup_preserves_existing_home(has_mem0=False)
    test_setup_preserves_existing_home('--update')
    test_setup_preserves_existing_home('--update', has_mem0=False)
    test_setup_preserves_existing_home('--cli-only')
    test_setup_preserves_existing_home('--update', '--cli-first')
    test_setup_preserves_existing_home(script='update.sh')
    test_setup_preserves_existing_home(script='update.sh', has_mem0=False)
