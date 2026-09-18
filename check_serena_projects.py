"""Run with ~/.codex/toolkit/venv/bin/python check_serena_projects.py."""
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERENA = Path.home() / '.codex/toolkit/bin/serena'
SERENA_PYTHON = Path.home() / '.codex/toolkit/uv-tools/serena-agent/bin/python'


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / 'home'
    project = Path(tmp) / 'python-project'
    home.mkdir()
    project.mkdir()
    (project / 'main.py').write_text('def answer() -> int:\n    return 42\n')
    env = os.environ | {'HOME': str(home)}
    subprocess.run([SERENA, 'init'], env=env, check=True, capture_output=True, text=True)
    subprocess.run([SERENA, 'project', 'create', '--ls', 'bash', str(project)],
                   env=env, check=True, capture_output=True, text=True)
    config = project / '.serena/project.yml'
    config.write_text(config.read_text().replace('read_only: false', 'read_only: true'))
    local = project / '.serena/project.local.yml'
    local.write_text('language_servers:\n- bash\n')

    result = subprocess.run([SERENA_PYTHON, ROOT / 'serena_projects.py'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    text = config.read_text()
    assert 'language_servers:\n- python\n' in text
    assert 'read_only: true' in text
    assert 'language_servers' not in local.read_text()


print('PASS: registered Serena projects refresh language servers.')
