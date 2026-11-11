"""Check bundle copying and direct execution without changing the host installation."""
from _paths import ROOT
import os
from pathlib import Path
import subprocess
import sys
import tempfile


with tempfile.TemporaryDirectory(prefix='toolkit layout ') as tmp:
    home = Path(tmp)
    toolkit = home / '.codex/toolkit'
    interpreter = toolkit / 'venv/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('#!/bin/sh\ntest "$1" = "$HOME/.codex/toolkit/bundle/lib/configure.py" && test -f "$1"\n')
    interpreter.chmod(0o755)
    script = '''
set -Eeuo pipefail
SCRIPT_DIR=$1
source "$SCRIPT_DIR/lib/toolkit.sh"
select_python() { :; }
CT_UV=/usr/bin/true
CT_PREFIX=/usr
bootstrap
'''
    result = subprocess.run(['bash', '-c', script, 'check', str(ROOT)], cwd=home,
                            env={**os.environ, 'HOME': str(home)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    bundle = toolkit / 'bundle'
    expected = [ROOT / name for name in ('setup.sh', 'update.sh', 'remove.sh', 'README.md', 'docs/policy.md')]
    for directory, patterns in [('lib', ('*.py', '*.sh')), ('tests', ('*.py',)),
                                ('tools/mem0_import', ('*.py', '*.sh', '*.sql'))]:
        for pattern in patterns:
            expected.extend((ROOT / directory).glob(pattern))
    for original in expected:
        copied = bundle / original.relative_to(ROOT)
        assert copied.read_bytes() == original.read_bytes(), copied
    # Updating from the installed bundle must skip copying files onto themselves.
    result = subprocess.run(['bash', '-c', script, 'check', str(bundle)], cwd=home,
                            env={**os.environ, 'HOME': str(home)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    interpreter.unlink()
    interpreter.symlink_to(sys.executable)
    # Exercise the generated hook launcher, not only the relocated module.
    configure_hook = '''
import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import configure
with patch.object(configure, 'configure_mem0_retry'):
    configure.configure_mem0_hooks()
'''
    env = {**os.environ, 'HOME': str(home)}
    result = subprocess.run([sys.executable, '-c', configure_hook, str(bundle / 'lib')],
                            cwd=home, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    hook = toolkit / 'bin/mem0-session'
    assert str(bundle / 'lib/mem0_session.py') in hook.read_text()
    result = subprocess.run([str(hook)], input='{"hook_event_name":"SessionStart"}',
                            cwd=home, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for command in ([sys.executable, str(bundle / 'tests/check_model_api.py')],
                    [sys.executable, str(bundle / 'tools/mem0_import/import_memories.py'), '--help'],
                    ['bash', str(bundle / 'remove.sh'), '--dry-run']):
        result = subprocess.run(command, cwd=home, env={**os.environ, 'HOME': str(home)},
                                capture_output=True, text=True)
        assert result.returncode == 0, (command, result.stderr)

print('Bundle layout and copied entrypoints passed.')
