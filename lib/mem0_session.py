#!/usr/bin/env python3
"""Queue completed-turn/session work; checkpoint only successfully stored transcript ranges."""
import fcntl
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid


HOME = Path.home() / '.codex'
STATE = HOME / 'toolkit/mem0-sessions'
MAX_TEXT = 12000
MIN_CHUNK = 1500


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
    text = re.sub(r'(?i)("(?:password|passwd|api[_-]?key|access[_-]?token|secret|token)"\s*:\s*)"(?:\\.|[^"\\])*"',
                  r'\1"[REDACTED]"', text)
    return re.sub(r'(?i)\b(password|passwd|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+',
                  r'\1=[REDACTED]', text)


def complete_end(path, include_valid_tail=False):
    """Snapshot the byte offset after the last complete JSONL record."""
    size = path.stat().st_size
    end = size
    with path.open('rb') as source:
        while end:
            start = max(0, end - 65536)
            source.seek(start)
            tail = source.read(end - start)
            newline = tail.rfind(b'\n')
            if newline >= 0:
                complete = start + newline + 1
                if include_valid_tail and complete < size:
                    source.seek(complete)
                    try:
                        json.loads(source.read())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        pass
                    else:
                        return size
                return complete
            end = start
    return 0


def enqueue(event):
    if event.get('hook_event_name') not in ('SessionStart', 'Stop', 'SessionEnd'):
        raise ValueError('Expected SessionStart, Stop or SessionEnd')
    if event['hook_event_name'] in ('Stop', 'SessionEnd'):
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        session = str(uuid.UUID(event['session_id']))
        if not event.get('transcript_path'):
            raise ValueError(f"{event['hook_event_name']} has no transcript_path")
        path = Path(event['transcript_path']).resolve(strict=True)
        roots = (HOME / 'sessions', HOME / 'archived_sessions')
        if not any(path.is_relative_to(root.resolve()) for root in roots):
            raise ValueError('Transcript is outside Codex session directories')
        # Only enqueue complete JSONL records; a later Stop/SessionEnd picks up a partial tail.
        end = complete_end(path)
        if not end:
            raise ValueError('Transcript contains no complete records')
        job = STATE / f'{session}-{end:020d}.job.json'
        if not job.exists():
            write_json(job, {'session': session, 'path': str(path), 'end': end})
    if not any(STATE.glob('*.job.json')):
        return
    # No Mem0 imports or network I/O in the three-second hook window.
    with (STATE / 'worker.log').open('ab') as log:
        os.chmod(STATE / 'worker.log', 0o600)
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--drain'],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)


def item_text(item):
    return '\n'.join(part['text'] for part in item.get('content', [])
                     if part.get('type') in ('text', 'Text') and isinstance(part.get('text'), str))


def evidence(text):
    """Redact structured and JSON-encoded values before escaping them for extraction."""
    def clean(value):
        if isinstance(value, dict):
            return {key: '[REDACTED]' if re.fullmatch(
                r'password|passwd|api[_-]?key|access[_-]?token|secret|token', str(key), re.I)
                else clean(part) for key, part in value.items()}
        if isinstance(value, list):
            return [clean(part) for part in value]
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                return redact(value)
            if isinstance(decoded, (dict, list, str)):
                return json.dumps(clean(decoded), ensure_ascii=False)
            return redact(value)
        return value
    value = clean(text)
    return redact(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str))


def task_message(record):
    payload = record.get('payload', {})
    kind = payload.get('type')
    if record.get('type') == 'response_item':
        if kind in ('function_call', 'custom_tool_call'):
            return {'role': 'tool', 'call_id': payload.get('call_id'), 'tool_phase': 'call',
                    'name': payload.get('name'), 'content': evidence({
                        'name': payload.get('name'), 'arguments': payload.get('arguments'),
                        'input': payload.get('input'), 'status': payload.get('status')})}
        if kind in ('function_call_output', 'custom_tool_call_output'):
            return {'role': 'tool', 'call_id': payload.get('call_id'), 'tool_phase': 'result',
                    'content': evidence(payload.get('output', ''))}
        return None  # User/agent messages are mirrored in event_msg; reasoning is not evidence.
    if record.get('type') != 'event_msg':
        return None
    if kind == 'mcp_tool_call_end':
        return {'role': 'tool', 'call_id': payload.get('call_id'), 'tool_phase': 'result',
                'content': evidence({'invocation': payload.get('invocation'),
                                     'result': payload.get('result'), 'error': payload.get('error')})}
    if kind == 'item_completed':
        item = payload.get('item', {})
        kind = item.get('type')
        if kind == 'UserMessage':
            return {'role': 'user', 'content': redact(item_text(item))}
        if kind == 'AgentMessage' and item.get('phase') in ('commentary', 'final', 'final_answer'):
            return {'role': 'final' if item.get('phase') in ('final', 'final_answer') else 'assistant',
                    'content': redact(item_text(item))}
        if kind == 'CommandExecution':
            command = item.get('command') or ''
            if isinstance(command, list):
                command = shlex.join(command)
            return {'role': 'tool', 'call_id': item.get('id'), 'tool_phase': 'result',
                    'content': evidence({'command': command, 'exit_code': item.get('exit_code'),
                        'status': item.get('status'),
                        'output': item.get('aggregated_output') or item.get('formatted_output') or ''})}
        if kind == 'McpToolCall':
            return {'role': 'tool', 'call_id': item.get('id'), 'tool_phase': 'result',
                    'content': evidence({'server': item.get('server'), 'tool': item.get('tool'),
                        'arguments': item.get('arguments'), 'status': item.get('status'),
                        'result': item.get('result'), 'error': item.get('error')})}
        if kind == 'FileChange':
            return {'role': 'tool', 'call_id': item.get('id'), 'tool_phase': 'result',
                    'content': evidence({'changes': item.get('changes'), 'status': item.get('status'),
                        'stdout': item.get('stdout'), 'stderr': item.get('stderr')})}
        return None
    if kind == 'user_message':
        return {'role': 'user', 'content': redact(payload.get('message', ''))}
    if kind == 'agent_message' and payload.get('phase') in ('commentary', 'final', 'final_answer'):
        return {'role': 'final' if payload.get('phase') in ('final', 'final_answer') else 'assistant',
                'content': redact(payload.get('message', ''))}
    return None


def message_chunks(messages, limit, skip=0):
    """Yield source slices bounded by serialized size, retaining event identity."""
    current, size = [], 2  # JSON array brackets.
    for message in messages:
        content = message['content']
        if skip >= len(content):
            skip -= len(content)
            continue
        content, skip = content[skip:], 0
        while content:
            available = limit - size - (2 if current else 0)
            overhead = len(json.dumps({**message, 'content': ''}, ensure_ascii=False))
            low, high = 0, min(len(content), max(0, available - overhead))
            while low < high:
                middle = (low + high + 1) // 2
                part = {**message, 'content': content[:middle]}
                if len(json.dumps(part, ensure_ascii=False)) <= available:
                    low = middle
                else:
                    high = middle - 1
            if not low:
                if current:
                    yield current
                    current, size = [], 2
                    continue
                raise ValueError('Event metadata exceeds extraction input budget; checkpoint retained')
            part = {**message, 'content': content[:low]}
            size += len(json.dumps(part, ensure_ascii=False)) + (2 if current else 0)
            current.append(part)
            content = content[low:]
            if content:
                yield current
                current, size = [], 2
    if current:
        yield current


def extract_task_memories(store, messages, previous=None, progress_path=None):
    """Extract semantic records per bounded source window without a lossy final reduction."""
    from mem0_mcp import ExtractionLimitError, extract_task_records
    def excerpt(text, budget=MIN_CHUNK):
        if len(json.dumps(text, ensure_ascii=False)) <= budget:
            return text
        width = min(len(text) // 2, budget // 2)
        while width:
            part = text[:width] + '\n[…context excerpt…]\n' + text[-width:]
            if len(json.dumps(part, ensure_ascii=False)) <= budget:
                return part
            width //= 2
        return '[context omitted]'

    previous_problem = (previous.get('request') or previous['text'].splitlines()[0]) if previous else None
    if previous_problem:
        previous_problem = excerpt(previous_problem)
    source_hash = hashlib.sha256(json.dumps(
        [messages, previous_problem, 2], ensure_ascii=False).encode()).hexdigest()
    progress = {'version': 2, 'source_hash': source_hash, 'consumed': 0,
                'chunk_size': MAX_TEXT, 'facts': []}
    if progress_path is not None and progress_path.exists():
        saved = json.loads(progress_path.read_text())
        # Old summaries cannot supply missing detail; re-read their uncommitted source.
        if saved.get('version') == 2:
            if saved['source_hash'] != source_hash:
                raise ValueError('Extraction source changed; checkpoint retained')
            progress = saved
    total = sum(len(message['content']) for message in messages)
    if len(json.dumps({'previous_task': previous_problem, 'events': messages},
                      ensure_ascii=False)) <= MAX_TEXT and progress['consumed'] == 0:
        try:
            return extract_task_records(store, messages, previous_problem)
        except ExtractionLimitError:
            progress['chunk_size'] = MAX_TEXT // 2
    context = {
        'request': next((message['content'] for message in messages if message['role'] == 'user'), ''),
        'latest_result': next((message['content'] for message in reversed(messages)
                               if message['role'] == 'final'), ''),
    }
    seen = {fact['text'] for fact in progress['facts']}
    while progress['consumed'] < total:
        budget = MIN_CHUNK
        while True:
            preceding = progress.get('preceding')
            reference = {'role': 'context', 'content': json.dumps({
                **{key: excerpt(text, budget) for key, text in context.items()},
                'preceding_evidence': [{'role': preceding['role'],
                                       'content': excerpt(preceding['content'], budget)}]
                if preceding else []}, ensure_ascii=False)}
            if len(json.dumps(reference, ensure_ascii=False)) <= MAX_TEXT // 3:
                break
            budget //= 2
        limit = min(progress['chunk_size'], MAX_TEXT - len(json.dumps(
            {'previous_task': previous_problem, 'events': [reference]}, ensure_ascii=False)))
        chunk = next(message_chunks(messages, limit, progress['consumed']))
        try:
            facts = extract_task_records(store, [reference, *chunk], previous_problem, allow_empty=True)
        except ExtractionLimitError:
            if progress['chunk_size'] <= MIN_CHUNK:
                raise
            progress['chunk_size'] = max(MIN_CHUNK, progress['chunk_size'] // 2)
            continue
        end = progress['consumed'] + sum(len(message['content']) for message in chunk)
        for fact in facts:
            text = redact(fact['text'])
            if text not in seen:
                progress['facts'].append({'text': text, 'source_start': progress['consumed'],
                                          'source_end': end})
                seen.add(text)
        progress['consumed'] = end
        progress['preceding'] = {'role': chunk[-1]['role'], 'content': chunk[-1]['content'][-MIN_CHUNK:]}
        if progress_path is not None:
            write_json(progress_path, progress)
    if not progress['facts']:
        if progress_path is not None:
            progress_path.unlink(missing_ok=True)
        raise ValueError('No task evidence extracted; checkpoint retained')
    return progress['facts']


def batches(path, session, checkpoint, end, include_valid_tail=False):
    """Yield each user request with its observed work once a new request or final arrives."""
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
        messages = []
        seen_tools = set()
        start_offset = offset
        while transcript.tell() < end:
            before = {'offset': transcript.tell(), 'sha256': digest.hexdigest()}
            raw = transcript.readline()
            if not raw or transcript.tell() > end or (
                    not raw.endswith(b'\n') and not (include_valid_tail and transcript.tell() == end)):
                raise ValueError('Incomplete transcript record; retry after the next Stop or SessionEnd')
            record = json.loads(raw)
            digest.update(raw)
            message = task_message(record)
            if message and message['content'].strip():
                if message['role'] == 'user' and messages:
                    if any(part['role'] == 'user' for part in messages):
                        before['start_offset'] = start_offset
                        yield messages, before
                    messages = []
                    seen_tools = set()
                if not messages:
                    start_offset = before['offset']
                if message['role'] == 'tool' and message.get('call_id'):
                    identity = (message['call_id'], message['tool_phase'])
                    if identity in seen_tools:
                        continue
                    seen_tools.add(identity)
                messages.append(message)
                if message['role'] == 'final':
                    if any(part['role'] == 'user' for part in messages):
                        yield messages, {'offset': transcript.tell(), 'sha256': digest.hexdigest(),
                                         'start_offset': start_offset}
                    messages = []
                    seen_tools = set()


def process_job(job_path, store_factory=None, user_id='codex', state_dir=None, metadata=None):
    from mem0_mcp import memory
    job = json.loads(job_path.read_text())
    stats = {'messages': 0, 'llm_calls': 0, 'inserted': 0, 'updated': 0}
    session = job['session']
    state_dir = Path(state_dir) if state_dir is not None else STATE
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cursor_path = state_dir / f'{session}.cursor.json'
    cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
    pending_path = state_dir / f'{session}.pending.json'
    progress_path = state_dir / f'{session}.summary.json'
    if pending_path.exists() and cursor:
        committed = f'{session}:{cursor["offset"]}:{cursor["sha256"]}'
        if json.loads(pending_path.read_text())['batch'] == committed:
            pending_path.unlink()
            progress_path.unlink(missing_ok=True)
    path = Path(job['path'])
    if not path.exists():
        path = HOME / 'archived_sessions' / path.name
    if job['end'] <= cursor.get('offset', 0):
        job_path.unlink()
        return stats
    store = None
    for messages, next_cursor in batches(path, session, cursor, job['end'],
                                         job.get('include_valid_tail', False)):
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
                pending = {'batch': batch_id, 'facts': [
                    {**fact, 'text': redact(fact['text'])}
                    for fact in extract_task_memories(store, messages, cursor.get('last_task'),
                                                      progress_path=progress_path)]}
                stats['llm_calls'] += 1
                write_json(pending_path, pending)
            # Once facts are durable, insert retries use pending rather than the summary.
            progress_path.unlink(missing_ok=True)
            last_task = cursor.get('last_task')
            for index, fact in enumerate(pending['facts']):
                key = hashlib.sha256(f'{batch_id}:{index}'.encode()).hexdigest()
                filters = {'user_id': user_id, 'toolkit_fact_id': key}
                # Handles a crash after Oracle commit but before local checkpoint commit.
                rows = store.get_all(filters=filters)['results']
                if not rows:
                    result = store.add(fact['text'], user_id=user_id, infer=False,
                                       metadata={**(metadata or {}), 'toolkit_fact_id': key,
                                                 'codex_session_id': session, 'memory_kind': 'task_outcome',
                                                 'codex_batch_id': batch_id,
                                                 'codex_chunk_index': index,
                                                 'codex_chunk_count': len(pending['facts']),
                                                 'codex_transcript_path': str(path),
                                                 'codex_first_offset': next_cursor.get('start_offset', cursor.get('offset', 0)),
                                                 **({'codex_evidence_start': fact['source_start'],
                                                     'codex_evidence_end': fact['source_end']}
                                                    if 'source_start' in fact else {}),
                                                 'codex_last_offset': next_cursor['offset']})
                    rows = store.get_all(filters=filters)['results']
                    if not result.get('results') or not rows:
                        raise RuntimeError('Mem0 insert was not confirmed in Oracle')
                    stats['inserted'] += len(result['results'])
                last_task = {'id': rows[0]['id'], 'key': key, 'text': rows[0]['memory'],
                             'request': next(message['content'] for message in messages
                                             if message['role'] == 'user')}
            next_cursor['last_task'] = last_task
            write_json(cursor_path, next_cursor)
            pending_path.unlink()
            cursor = next_cursor
        else:
            next_cursor['last_task'] = cursor.get('last_task')
            write_json(cursor_path, next_cursor)
            cursor = next_cursor
    job_path.unlink()
    return stats


def drain():
    from mem0_mcp import ExtractionLimitError
    # Provider background threads must not retain redirected/closed log streams.
    logging.disable(logging.CRITICAL)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    # ponytail: one worker processes all sessions serially to avoid duplicate inserts;
    # use per-session locks if queue throughput becomes a bottleneck.
    with (STATE / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # The active worker owns the queue; the timer will check again.
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
                details = f' {error}' if isinstance(error, ExtractionLimitError) else ''
                print(f'FAILED {job_path.name}: {type(error).__name__}{details}; pending for retry', flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    if sys.argv[1:] == ['--drain']:
        drain()
    elif not sys.argv[1:]:
        event = json.load(sys.stdin)
        enqueue(event)
        if event['hook_event_name'] == 'Stop':
            print('{}')
    else:
        raise SystemExit('Usage: mem0_session.py [--drain]')
