"""Run with ~/.codex/toolkit/venv/bin/python check_graft.py."""
import contextlib
import fcntl
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import configure as c

source = Path(__file__).resolve().parent
assert (source / 'graft_mcp.py').exists(), 'Graft automatic startup is missing'
spec = importlib.util.spec_from_file_location('graft_mcp', source / 'graft_mcp.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

with tempfile.TemporaryDirectory(prefix='graft check ') as tmp:
    root = Path(tmp)
    fake = root / 'fake_graft.py'
    fake.write_text('''import os, sys, time
from pathlib import Path
if sys.argv[1] == 'build':
    assert sys.argv[2:] == ['--no-gitignore', '--no-ignore']
    with open('builds', 'a') as f: f.write('build\\n')
    print('build output must not reach MCP stdout', flush=True)
    mode = os.environ.get('GRAFT_TEST_MODE', '')
    if mode:
        p = Path('graft/.graph/wiring.json')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('partial')
        Path('build.pid').write_text(str(os.getpid()))
    if mode == 'timeout': time.sleep(30)
    if mode == 'fail': sys.exit(9)
    time.sleep(.2)
    p = Path('graft/.graph/wiring.json')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{}')
elif sys.argv[1] == 'mcp':
    assert Path('graft/.graph/wiring.json').exists()
    from mcp.server.fastmcp import FastMCP
    server = FastMCP('fake-graft')
    @server.tool()
    def graph_ready() -> bool: return True
    server.run()
else: sys.exit(8)
''')

    def probe(cwd, mode='', timeout=5):
        entry = {'command': sys.executable,
                 'args': [str(source / 'graft_mcp.py'), sys.executable, str(fake)],
                 'env': {'GRAFT_TEST_MODE': mode, 'CODEX_GRAFT_BUILD_TIMEOUT': str(timeout)},
                 'startup_timeout_sec': 10}
        with contextlib.redirect_stdout(io.StringIO()), tempfile.TemporaryFile(mode='w+t') as log:
            count = c.mcp_probe('graft', entry, str(cwd), stderr=log)
            log.seek(0)
            return count, log.read()

    # A non-repository still completes the protocol without indexing its contents.
    assert probe(root)[0] == 0
    assert not (root / 'graft').exists()

    repo = root / 'repo with spaces'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'src').mkdir()
    (repo / '.gitignore').write_text('existing\n')
    assert probe(repo / 'src')[0] == 1
    assert (repo / 'builds').read_text() == 'build\n'
    assert (repo / '.gitignore').read_text() == 'existing\n'
    assert not (repo / '.ignore').exists()
    assert subprocess.run(['git', '-C', str(repo), 'check-ignore', '-q', 'graft/']).returncode == 0
    assert probe(repo)[0] == 1
    assert (repo / 'builds').read_text() == 'build\n', 'existing index was rebuilt'

    # Concurrent first starts share one build; subprocesses have independent flock owners.
    (repo / 'graft/.graph/wiring.json').unlink()
    command = [sys.executable, '-c',
        'import graft_mcp as g,sys; g.prepare(sys.argv[1],sys.argv[2],5); '
        'assert g.Path("graft/.graph/wiring.json").read_text() == "{}"', sys.executable, str(fake)]
    env = dict(os.environ, PYTHONPATH=str(source), GRAFT_TEST_MODE='slow')
    children = [subprocess.Popen(command, cwd=repo, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)]
    deadline = time.monotonic() + 5
    while not (repo / 'graft/.graph/wiring.json').exists():
        assert time.monotonic() < deadline
        time.sleep(.005)
    children.append(subprocess.Popen(command, cwd=repo, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    for child in children:
        out, err = child.communicate(timeout=10)
        assert child.returncode == 0, err
        assert not out
    assert (repo / 'builds').read_text() == 'build\nbuild\n'

    # Failures/timeouts degrade to a valid idle MCP; the next session retries.
    (repo / 'graft/.graph/wiring.json').unlink()
    for mode in ('fail', 'timeout'):
        count, log = probe(repo, mode, .5)
        assert count == 0 and 'Graft unavailable' in log, (count, log)
        assert not (repo / 'graft/.graph/wiring.json').exists()
    with (repo / '.git/codex-graft.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        count, log = probe(repo, timeout=.5)
        assert count == 0 and 'waiting for another Graft build' in log

    # Cancelling the MCP wrapper must stop its builder and release its lock.
    (repo / 'build.pid').unlink()
    child = subprocess.Popen([sys.executable, str(source / 'graft_mcp.py'), sys.executable, str(fake)],
        cwd=repo, env=dict(os.environ, GRAFT_TEST_MODE='timeout'),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not (repo / 'build.pid').exists():
            assert time.monotonic() < deadline, 'builder did not start'
            time.sleep(.02)
        pid = int((repo / 'build.pid').read_text())
        child.terminate()
        child.communicate(timeout=5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError('cancelled startup left its builder running')
        assert not (repo / 'graft/.graph/wiring.json').exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    assert probe(repo)[0] == 1

print('PASS: Graft non-repository, first start, reuse, concurrent build, failure and timeout retry.')
