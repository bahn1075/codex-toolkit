#!/usr/bin/env python3
"""Queue SessionEnd work quickly; checkpoint only successfully stored transcript ranges."""
import fcntl
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


HOME = Path.home() / '.codex'
STATE = HOME / 'toolkit/mem0-sessions'
MAX_TEXT = 12000


def write_json(path, value):
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with tmp.open('w', encoding='utf-8') as out:
        os.chmod(tmp, 0o600)
        json.dump(value, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)


def redact(text):
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----',
                  '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'\b(?:ctx7sk-|github_pat_|gh[pousr]_|sk-)[A-Za-z0-9_-]{16,}',
                  '[REDACTED TOKEN]', text)
    return re.sub(r'(?i)\b(password|passwd|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+',
                  r'\1=[REDACTED]', text)


def enqueue(event):
    if event.get('hook_event_name') not in ('SessionStart', 'SessionEnd'):
        raise ValueError('Expected SessionStart or SessionEnd')
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if event['hook_event_name'] == 'SessionEnd':
        session = str(uuid.UUID(event['session_id']))
        if not event.get('transcript_path'):
            raise ValueError('SessionEnd has no transcript_path')
        path = Path(event['transcript_path']).resolve(strict=True)
        roots = (HOME / 'sessions', HOME / 'archived_sessions')
        if not any(path.is_relative_to(root.resolve()) for root in roots):
            raise ValueError('Transcript is outside Codex session directories')
        end = path.stat().st_size
        # Only enqueue complete JSONL records; a later end event picks up a partial tail.
        with path.open('rb') as source:
            while end:
                start = max(0, end - 65536)
                source.seek(start)
                tail = source.read(end - start)
                newline = tail.rfind(b'\n')
                if newline >= 0:
                    end = start + newline + 1
                    break
                end = start
        if not end:
            raise ValueError('Transcript contains no complete records')
        job = STATE / f'{session}-{end:020d}.job.json'
        if not job.exists():
            write_json(job, {'session': session, 'path': str(path), 'end': end})
    # No Mem0 imports or network I/O in the three-second hook window.
    with (STATE / 'worker.log').open('ab') as log:
        os.chmod(STATE / 'worker.log', 0o600)
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--drain'],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)


def batches(path, session, checkpoint, end):
    """Read native Codex JSONL events once, excluding tools, reasoning and injected instructions."""
    offset = checkpoint.get('offset', 0)
    digest = hashlib.sha256()
    with path.open('rb') as transcript:
        meta = json.loads(transcript.readline())
        if meta.get('type') != 'session_meta' or (
                meta['payload'].get('session_id') or meta['payload'].get('id')) != session:
            raise ValueError('Transcript session identity mismatch')
        transcript.seek(0)
        remaining = offset
        while remaining:
            chunk = transcript.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError('Transcript was truncated; checkpoint retained')
            digest.update(chunk)
            remaining -= len(chunk)
        if offset and digest.hexdigest() != checkpoint['sha256']:
            raise ValueError('Transcript prefix changed; checkpoint retained')
        messages, size = [], 0
        while transcript.tell() < end:
            raw = transcript.readline()
            if not raw or not raw.endswith(b'\n') or transcript.tell() > end:
                raise ValueError('Incomplete transcript record; retry after the next SessionEnd')
            record = json.loads(raw)
            digest.update(raw)
            payload = record.get('payload', {})
            if record.get('type') == 'event_msg':
                if payload.get('type') == 'item_completed':
                    item = payload.get('item', {})
                    if item.get('type') in ('UserMessage', 'AgentMessage'):
                        payload = {
                            'type': 'user_message' if item['type'] == 'UserMessage' else 'agent_message',
                            'phase': item.get('phase'),
                            'message': '\n'.join(part['text'] for part in item.get('content', [])
                                                 if part.get('type') in ('text', 'Text') and isinstance(part.get('text'), str)),
                        }
                kind = payload.get('type')
                if kind == 'user_message' or (kind == 'agent_message' and payload.get('phase') in ('final', 'final_answer')):
                    text = redact(payload['message'])
                    if text.strip():
                        messages.append({'role': 'user' if kind == 'user_message' else 'assistant',
                                         'content': text})
                        size += len(text)
            # ponytail: a single message can exceed this soft batch limit; split oversized
            # messages upstream if the local model's context window becomes a constraint.
            if size >= MAX_TEXT:
                yield messages, {'offset': transcript.tell(), 'sha256': digest.hexdigest()}
                messages, size = [], 0
        if transcript.tell() > offset:
            yield messages, {'offset': transcript.tell(), 'sha256': digest.hexdigest()}


def process_job(job_path, store_factory=None):
    from mem0_mcp import extract_facts, memory
    job = json.loads(job_path.read_text())
    stats = {'messages': 0, 'llm_calls': 0, 'inserted': 0}
    session = job['session']
    cursor_path = STATE / f'{session}.cursor.json'
    cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
    pending_path = STATE / f'{session}.pending.json'
    if pending_path.exists() and cursor:
        committed = f'{session}:{cursor["offset"]}:{cursor["sha256"]}'
        if json.loads(pending_path.read_text())['batch'] == committed:
            pending_path.unlink()
    path = Path(job['path'])
    if not path.exists():
        path = HOME / 'archived_sessions' / path.name
    if job['end'] <= cursor.get('offset', 0):
        job_path.unlink()
        return stats
    store = None
    for messages, next_cursor in batches(path, session, cursor, job['end']):
        if messages:
            stats['messages'] += len(messages)
            if store is None:
                store = (store_factory or memory)()
            # Persist extracted facts before inserts so retries never re-extract this batch.
            batch_id = f'{session}:{next_cursor["offset"]}:{next_cursor["sha256"]}'
            if pending_path.exists():
                pending = json.loads(pending_path.read_text())
                if pending['batch'] != batch_id:
                    raise ValueError('Pending batch mismatch; refusing to skip unsaved facts')
            else:
                pending = {'batch': batch_id, 'facts': extract_facts(store, messages)}
                stats['llm_calls'] += 1
                write_json(pending_path, pending)
            for index, fact in enumerate(pending['facts']):
                key = hashlib.sha256(f'{batch_id}:{index}'.encode()).hexdigest()
                filters = {'user_id': 'codex', 'toolkit_fact_id': key}
                # Handles a crash after Oracle commit but before local checkpoint commit.
                if not store.get_all(filters=filters)['results']:
                    result = store.add(fact, user_id='codex', infer=False,
                                       metadata={'toolkit_fact_id': key, 'codex_session_id': session})
                    if not result.get('results') or not store.get_all(filters=filters)['results']:
                        raise RuntimeError('Mem0 insert was not confirmed in Oracle')
                    stats['inserted'] += len(result['results'])
            write_json(cursor_path, next_cursor)
            pending_path.unlink()
        else:
            write_json(cursor_path, next_cursor)
    job_path.unlink()
    return stats


def drain():
    # Provider background threads must not retain redirected/closed log streams.
    logging.disable(logging.CRITICAL)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ponytail: one worker processes all sessions serially to avoid duplicate inserts;
    # use per-session locks if queue throughput becomes a bottleneck.
    with (STATE / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        failed = set()
        for job_path in sorted(STATE.glob('*.job.json')):
            session = job_path.name[:36]
            if session in failed:
                continue
            try:
                with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    stats = process_job(job_path)
                print(f'OK {job_path.name} {json.dumps(stats)}', flush=True)
            except Exception as error:
                failed.add(session)
                # Keep provider errors and conversation text out of the persistent log.
                print(f'FAILED {job_path.name}: {type(error).__name__}; pending for retry', flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    if sys.argv[1:] == ['--drain']:
        drain()
    elif not sys.argv[1:]:
        enqueue(json.load(sys.stdin))
    else:
        raise SystemExit('Usage: mem0_session.py [--drain]')
