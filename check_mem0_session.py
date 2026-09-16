"""Run with ~/.codex/toolkit/venv/bin/python check_mem0_session.py (no network)."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

import mem0_session as s
import mem0_mcp as m
import configure as c


class Store:
    def __init__(self):
        self.rows = {}
        self.fail = False
        self.fail_after_insert = False
        self.calls = 0

    def get_all(self, filters):
        return {'results': [self.rows[filters['toolkit_fact_id']]] if filters['toolkit_fact_id'] in self.rows else []}

    def add(self, fact, user_id, infer, metadata):
        assert user_id == 'codex' and infer is False
        self.calls += 1
        if self.fail:
            raise ConnectionError('Offline')
        self.rows[metadata['toolkit_fact_id']] = fact
        if self.fail_after_insert:
            raise ConnectionError('Connection lost after commit')
        return {'results': [{'id': 'stored'}]}


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp)
    state = home / 'state'
    state.mkdir()
    transcript = home / 'sessions/test.jsonl'
    transcript.parent.mkdir()
    session = str(uuid.uuid4())
    store = Store()

    def append(kind, payload):
        with transcript.open('a') as out:
            out.write(json.dumps({'type': kind, 'payload': payload}) + '\n')

    def event(kind, text, **kwargs):
        append('event_msg', {'type': kind, 'message': text, **kwargs})

    def queue():
        with patch.object(s.subprocess, 'Popen') as spawn:
            s.enqueue({'hook_event_name': 'SessionEnd', 'session_id': session,
                       'transcript_path': str(transcript)})
            assert spawn.call_args.kwargs['start_new_session'] is True
        return next(state.glob('*.job.json'))

    append('session_meta', {'id': session})
    event('user_message', 'Project uses Oracle.')
    append('response_item', {'type': 'message', 'role': 'user', 'content': 'duplicate'})
    event('agent_message', 'Hidden progress', phase='commentary')
    event('agent_message', 'Confirmed Oracle.', phase='final_answer')
    with patch.object(s, 'STATE', state), patch.object(s, 'HOME', home), \
            patch.object(m, 'extract_facts', side_effect=lambda store, messages: [v['content'] for v in messages]) as extract:
        s.process_job(queue(), lambda: store)
        assert list(store.rows.values()) == ['Project uses Oracle.', 'Confirmed Oracle.']
        assert (state / f'{session}.cursor.json').stat().st_mode & 0o777 == 0o600
        s.process_job(queue(), lambda: store)
        assert store.calls == 2  # Same session end does not resubmit anything.

        event('user_message', 'Resumed project uses Python.')
        s.process_job(queue(), lambda: store)
        assert extract.call_args.args[1] == [{'role': 'user', 'content': 'Resumed project uses Python.'}]
        assert store.calls == 3

        event('user_message', 'Retry this fact.')
        job = queue()
        old_cursor = (state / f'{session}.cursor.json').read_bytes()
        store.fail = True
        try:
            s.process_job(job, lambda: store)
            raise AssertionError('Expected outage')
        except ConnectionError:
            pass
        assert job.exists() and (state / f'{session}.cursor.json').read_bytes() == old_cursor
        attempts = extract.call_count
        store.fail = False
        store.fail_after_insert = True
        try:
            s.process_job(job, lambda: store)
            raise AssertionError('Expected lost acknowledgement')
        except ConnectionError:
            pass
        calls = store.calls
        store.fail_after_insert = False
        s.process_job(job, lambda: store)
        assert store.calls == calls and extract.call_count == attempts
        assert not job.exists() and not list(state.glob('*.pending.json'))

        append('event_msg', {'type': 'item_completed', 'item': {
            'type': 'UserMessage', 'content': [{'type': 'text', 'text': 'New wire format user.'},
                                             {'type': 'image', 'image_url': 'ignored'}]}})
        append('event_msg', {'type': 'item_completed', 'item': {
            'type': 'AgentMessage', 'phase': 'final_answer',
            'content': [{'type': 'Text', 'text': 'New wire format answer.'}]}})
        append('event_msg', {'type': 'item_completed', 'item': {
            'type': 'AgentMessage', 'phase': 'commentary',
            'content': [{'type': 'Text', 'text': 'Excluded progress.'}]}})
        stats = s.process_job(queue(), lambda: store)
        assert stats == {'messages': 2, 'llm_calls': 1, 'inserted': 2}
        assert [x['content'] for x in extract.call_args.args[1]] == [
            'New wire format user.', 'New wire format answer.']
        assert s.process_job(queue(), lambda: store) == {'messages': 0, 'llm_calls': 0, 'inserted': 0}

        event('user_message', 'Malformed tail must not advance checkpoint.')
        with transcript.open('a') as out:
            out.write('{')
        job = queue()
        # The hook excludes an unfinished tail from the queued snapshot.
        queued = json.loads(job.read_text())
        assert queued['end'] == transcript.stat().st_size - 1
        queued['end'] = transcript.stat().st_size
        s.write_json(job, queued)  # Exercise worker refusal of a malformed snapshot too.
        old_cursor = (state / f'{session}.cursor.json').read_bytes()
        try:
            s.process_job(job, lambda: store)
            raise AssertionError('Expected malformed JSONL')
        except ValueError:
            pass
        assert (state / f'{session}.cursor.json').read_bytes() == old_cursor

    hooks = home / 'hooks.json'
    hooks.write_text(json.dumps({'hooks': {'SessionEnd': [{'hooks': [{'type': 'command', 'command': 'other'}]}]}}))
    with patch.object(c, 'CODEX', home), patch.object(c, 'launcher', return_value='/tools/mem0-session'):
        c.configure_mem0_hooks()
        first = hooks.read_text()
        c.configure_mem0_hooks()
        assert hooks.read_text() == first
        assert json.loads(first)['hooks']['SessionEnd'][0]['hooks'][0]['command'] == 'other'

assert 'ctx7sk-' not in s.redact('ctx7sk-' + 'a' * 36)
assert 'hunter2' not in s.redact('password=hunter2')
with patch.object(m, 'memory') as factory:
    response = factory.return_value.llm.client.with_options.return_value.chat.completions.create.return_value
    choice = response.choices.__getitem__.return_value
    choice.finish_reason = 'stop'
    choice.message.content = '{broken'
    try:
        m.save_memory('fact')
        raise AssertionError('Invalid model JSON must fail')
    except json.JSONDecodeError:
        pass
    factory.return_value.add.assert_not_called()
    choice.message.content = ''
    choice.message.model_extra = {'reasoning_content': '{"memory": [{"text": "Oracle"}]}'}
    assert m.extract_facts(factory.return_value, []) == ['Oracle']
    choice.finish_reason = 'length'
    try:
        m.extract_facts(factory.return_value, [])
        raise AssertionError('Truncated model output must fail')
    except ValueError:
        pass

print('PASS: new/resumed sessions, duplicate events, outage retry, commit recovery, malformed transcript, hook merge, extraction validation.')
