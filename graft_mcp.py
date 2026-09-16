#!/usr/bin/env python3
"""Prepare a local structural index before handing stdio to Graft MCP."""
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def prepare(node, graft, timeout):
    deadline = time.monotonic() + timeout
    result = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True, timeout=5)
    if result.returncode:
        return None
    repo = Path(result.stdout.strip())
    os.chdir(repo)
    graph = repo / 'graft/.graph/wiring.json'
    workspace = repo / 'graft/workspace.json'
    git_path = subprocess.check_output(
        ['git', 'rev-parse', '--git-path', 'codex-graft.lock'], text=True, timeout=5).strip()
    # flock is per repository/worktree and released by the OS even on termination.
    with open(git_path, 'a') as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('waiting for another Graft build')
                time.sleep(.1)
        if graph.is_file() or workspace.is_file():
            return repo
        exclude = Path(subprocess.check_output(
            ['git', 'rev-parse', '--git-path', 'info/exclude'], text=True, timeout=5).strip())
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open('a+') as file:
            file.seek(0)
            if '/graft/' not in file.read().splitlines():
                file.write('\n/graft/\n')
        print(f'Graft: preparing structural index in {repo}', file=sys.stderr, flush=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Graft build startup budget exhausted')
        proc = subprocess.Popen([node, graft, 'build', '--no-gitignore', '--no-ignore'],
            stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr, start_new_session=True)
        def interrupted(signum, frame):
            raise SystemExit(128 + signum)
        previous = signal.signal(signal.SIGTERM, interrupted)
        complete = False
        try:
            code = proc.wait(timeout=remaining)
            if code:
                raise RuntimeError(f'graft build exited with status {code}')
            if not graph.is_file() and not workspace.is_file():
                raise RuntimeError('graft build did not produce an index')
            complete = True
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            signal.signal(signal.SIGTERM, previous)
            if not complete:
                # This attempt started with no graph: do not reuse an incomplete build.
                graph.unlink(missing_ok=True)
    return repo


def main():
    node, graft = sys.argv[1:3]
    reason = 'Graft is idle: the starting directory is not a Git working tree.'
    try:
        timeout = float(os.environ.get('CODEX_GRAFT_BUILD_TIMEOUT', '90'))
        if prepare(node, graft, timeout) is not None:
            os.execv(node, [node, graft, 'mcp', *sys.argv[3:]])
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError, TimeoutError) as exc:
        reason = f'Graft unavailable: {exc}. The next session will retry.'
    print(reason, file=sys.stderr, flush=True)
    # Reuse the installed MCP SDK for valid idle responses, including ping/tools/list.
    from mcp.server.fastmcp import FastMCP
    FastMCP('graft-idle', instructions=reason).run()


if __name__ == '__main__':
    main()
