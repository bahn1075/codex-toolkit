import os
import shlex
import subprocess
import tempfile
from pathlib import Path


def test_setup_preserves_existing_home():
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        home = root / 'home'
        codex = home / '.codex'
        (codex / 'sessions').mkdir(parents=True)
        (codex / 'sessions' / 'conversation.jsonl').write_text('conversation')
        (codex / 'auth.json').write_text('login')
        (codex / 'config.toml').write_text('user settings')
        (root / 'setup.sh').write_text((source / 'setup.sh').read_text())
        (root / 'toolkit.sh').write_text(
            'source ' + shlex.quote(str(source / 'toolkit.sh')) + '\n'
            'preflight() { :; }\nacquire_lock() { :; }\n'
            'remove_homebrew_codex() { :; }\nnpm() { return 1; }\n'
            'bootstrap() { mkdir -p "$CT_ROOT"; echo "{}" > "$CT_ROOT/install-state.json"; }\n'
            'install_packages() { mkdir -p "$HOME/.local/bin"; echo updated > "$HOME/.local/bin/codex"; }\n'
            + ''.join(name + '() { :; }\n' for name in (
                'configure_core', 'install_mem0', 'configure_headroom', 'install_plugins', 'finish')))
        result = subprocess.run(['bash', str(root / 'setup.sh')],
                                env={**os.environ, 'HOME': str(home), 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert (codex / 'sessions' / 'conversation.jsonl').read_text() == 'conversation'
        assert (codex / 'auth.json').read_text() == 'login'
        assert (codex / 'config.toml').read_text() == 'user settings'
        backups = list((home / '.codex-backups').glob('update-*/config.toml'))
        assert len(backups) == 1 and backups[0].read_text() == 'user settings'
        assert (home / '.local/bin/codex').read_text().strip() == 'updated'


if __name__ == '__main__':
    test_setup_preserves_existing_home()
