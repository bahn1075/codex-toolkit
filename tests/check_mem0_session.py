"""Run with ~/.codex/toolkit/venv/bin/python tests/check_mem0_session.py (no network)."""
from _paths import ROOT
import contextlib
import io
import hashlib
import json
import fcntl
from pathlib import Path
import runpy
import sys
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
        self.rows[metadata['toolkit_fact_id']] = {
            'id': metadata['toolkit_fact_id'], 'memory': fact}
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

    def queue(hook_event='SessionEnd'):
        with patch.object(s.subprocess, 'Popen') as spawn:
            s.enqueue({'hook_event_name': hook_event, 'session_id': session,
                       'transcript_path': str(transcript)})
            assert spawn.call_args.kwargs['start_new_session'] is True
        return next(state.glob('*.job.json'))

    # Startup resumes queued work without model/network calls in the hook.
    with patch.object(s, 'STATE', state), patch.object(s.subprocess, 'Popen') as spawn:
        s.enqueue({'hook_event_name': 'SessionStart'})
        spawn.assert_not_called()  # No queue yet.
        (state / 'retry.job.json').write_text('{}')
        s.enqueue({'hook_event_name': 'SessionStart'})
        assert spawn.called, 'SessionStart must restart pending work'
        assert spawn.call_args.args[0][-1] == '--drain'
        assert spawn.call_args.kwargs['start_new_session'] is True
        (state / 'retry.job.json').unlink()

    # A completed turn under the advertised budget needs one model extraction.
    with patch.object(m, 'extract_task_records', return_value=[{'text': 'stored', 'continuation': False}]) as records, \
            patch.object(m, 'extract_facts', side_effect=AssertionError('Unnecessary summary')):
        assert s.extract_task_memories(store, [{'role': 'user', 'content': 'x' * 8900}]) == [
            {'text': 'stored', 'continuation': False}]
        assert records.call_count == 1

    append('session_meta', {'id': session})
    event('user_message', 'Project uses Oracle.')
    append('response_item', {'type': 'message', 'role': 'user', 'content': 'duplicate'})
    event('agent_message', 'Hidden progress', phase='commentary')
    event('agent_message', 'Confirmed Oracle.', phase='final_answer')
    with patch.object(s, 'STATE', state), patch.object(s, 'HOME', home), \
            patch.object(s, 'extract_task_memories', side_effect=lambda store, messages, previous=None, **kwargs: [{
                'text': messages[0]['content'] + ' ' + messages[-1]['content'],
                'continuation': False}]) as extract:
        s.process_job(queue('Stop'), lambda: store)
        assert [row['memory'] for row in store.rows.values()] == ['Project uses Oracle. Confirmed Oracle.']
        assert (state / f'{session}.cursor.json').stat().st_mode & 0o777 == 0o600
        s.process_job(queue('Stop'), lambda: store)
        assert store.calls == 1  # Repeated Stop does not resubmit anything.

        event('user_message', 'Resumed project uses Python.')
        event('agent_message', 'Python check passed.', phase='final_answer')
        s.process_job(queue('Stop'), lambda: store)
        assert extract.call_args.args[1] == [
            {'role': 'user', 'content': 'Resumed project uses Python.'},
            {'role': 'final', 'content': 'Python check passed.'}]
        assert store.calls == 2
        s.process_job(queue(), lambda: store)
        assert store.calls == 2  # SessionEnd after Stop does not duplicate the requests.

        # The command hook must return JSON without asking Codex to continue the turn.
        cli_transcript = home / '.codex/sessions/test.jsonl'
        cli_transcript.parent.mkdir(parents=True)
        cli_transcript.write_bytes(transcript.read_bytes())
        output = io.StringIO()
        with patch.object(Path, 'home', return_value=home), \
                patch.object(sys, 'argv', ['mem0_session.py']), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps({
                    'hook_event_name': 'Stop', 'session_id': session,
                    'transcript_path': str(cli_transcript)}))), \
                patch.object(s.subprocess, 'Popen'), contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(s.__file__)), run_name='__main__')
        assert json.loads(output.getvalue()) == {}

        event('user_message', 'Retry this fact.')
        event('agent_message', 'Retry completed.', phase='final_answer')
        job = queue('Stop')
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
            'type': 'AgentMessage', 'phase': 'commentary',
            'content': [{'type': 'Text', 'text': 'Checked the evidence.'}]}})
        append('event_msg', {'type': 'item_completed', 'item': {
            'type': 'AgentMessage', 'phase': 'final_answer',
            'content': [{'type': 'Text', 'text': 'New wire format answer.'}]}})
        stats = s.process_job(queue(), lambda: store)
        assert stats == {'messages': 3, 'llm_calls': 1, 'inserted': 1, 'updated': 0}
        assert [x['content'] for x in extract.call_args.args[1]] == [
            'New wire format user.', 'Checked the evidence.', 'New wire format answer.']
        assert s.process_job(queue(), lambda: store) == {
            'messages': 0, 'llm_calls': 0, 'inserted': 0, 'updated': 0}

        event('user_message', 'Malformed tail must not advance checkpoint.')
        with transcript.open('a') as out:
            out.write('{')
        job = queue()
        # The hook excludes an unfinished tail from the queued snapshot.
        queued = json.loads(job.read_text())
        assert queued['end'] == transcript.stat().st_size - 1
        queued['end'] = transcript.stat().st_size
        queued['sha256'] = hashlib.sha256(transcript.read_bytes()).hexdigest()
        s.write_json(job, queued)  # Exercise worker refusal of a malformed snapshot too.
        old_cursor = (state / f'{session}.cursor.json').read_bytes()
        try:
            s.process_job(job, lambda: store)
            raise AssertionError('Expected malformed JSONL')
        except ValueError:
            pass
        assert (state / f'{session}.cursor.json').read_bytes() == old_cursor

    # Timer/hook overlap must exit immediately rather than build a waiting worker queue.
    with (state / 'worker.lock').open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with patch.object(s, 'STATE', state), \
                patch.object(s, 'process_job', side_effect=AssertionError('Concurrent worker')):
            s.drain()

    hooks = home / 'hooks.json'
    hooks.write_text(json.dumps({'hooks': {event: [{'hooks': [{'type': 'command', 'command': 'other'}]}]
                                         for event in ('SessionEnd', 'Stop')}}))
    with patch.object(c, 'CODEX', home), patch.object(c, 'HOME', home), patch.object(c, 'ROOT', home / 'toolkit'), \
            patch.dict(c.os.environ, XDG_CONFIG_HOME=''), \
            patch.object(c.sys, 'platform', 'linux'), \
            patch.object(c.shutil, 'which', return_value='/usr/bin/systemctl'), \
            patch.object(c.subprocess, 'run') as systemctl, \
            patch.object(c, 'launcher', return_value='/tools/mem0-session'):
        systemctl.return_value.returncode = 0
        c.configure_mem0_hooks()
        first = hooks.read_text()
        c.configure_mem0_hooks()
        assert hooks.read_text() == first
        configured = json.loads(first)['hooks']
        for hook_event in ('SessionStart', 'Stop', 'SessionEnd'):
            managed = [hook for group in configured[hook_event] for hook in group['hooks']
                       if hook['command'] == '/tools/mem0-session']
            assert len(managed) == 1 and managed[0]['timeout'] == 3
        for hook_event in ('SessionEnd', 'Stop'):
            assert configured[hook_event][0]['hooks'][0]['command'] == 'other'
        units = home / '.config/systemd/user'
        assert (units / 'codex-mem0-retry.timer').exists(), 'Install a reboot-safe retry timer'
        assert 'OnUnitInactiveSec=2min' in (units / 'codex-mem0-retry.timer').read_text()
        assert 'ExecStart=/tools/mem0-session --drain' in (units / 'codex-mem0-retry.service').read_text()
        assert systemctl.call_args.args[0][-3:] == ['enable', '--now', 'codex-mem0-retry.timer']
        custom_config = home / 'custom-config'
        with patch.dict(c.os.environ, XDG_CONFIG_HOME=str(custom_config)):
            c.configure_mem0_hooks()
            assert (custom_config / 'systemd/user/codex-mem0-retry.timer').exists()
        units_home = home / 'no-user-manager'
        with patch.object(c, 'HOME', units_home):
            systemctl.reset_mock()
            systemctl.return_value.returncode = 1
            c.configure_mem0_hooks()
            assert systemctl.call_count == 1, 'Only probe when the user manager is unavailable'
            assert not (units_home / '.config/systemd/user').exists()

        agy_home = home / 'agy-user'
        (agy_home / '.gemini/antigravity').mkdir(parents=True)
        with patch.object(c, 'HOME', agy_home), patch.object(c, 'CODEX', agy_home / '.codex'), \
                patch.object(c, 'ROOT', agy_home / '.codex/toolkit'):
            systemctl.return_value.returncode = 1
            c.configure_mem0_hooks()
            agy_hooks_path = agy_home / '.gemini/config/plugins/codex-toolkit/hooks.json'
            first_agy = agy_hooks_path.read_text()
            c.configure_mem0_hooks()
            assert agy_hooks_path.read_text() == first_agy
            agy_configured = json.loads(first_agy)['codex-toolkit-mem0']
            for hook_event in ('SessionStart', 'Stop'):
                assert agy_configured[hook_event] == [
                    {'type': 'command', 'command': '/tools/mem0-session', 'timeout': 3}]

    # Exercise Antigravity hook contract and full-transcript task extraction.
    agy_root = home / 'agy-env'
    agy_codex = agy_root / '.codex'
    agy_state = agy_codex / 'toolkit/mem0-sessions'
    agy_session = str(uuid.uuid4())
    agy_logs = agy_root / '.gemini/antigravity/brain' / agy_session / '.system_generated/logs'
    agy_logs.mkdir(parents=True)
    agy_compact = agy_logs / 'transcript.jsonl'
    agy_full = agy_logs / 'transcript_full.jsonl'
    agy_records = [
        {'step_index': 0, 'source': 'USER_EXPLICIT', 'type': 'USER_INPUT', 'status': 'DONE',
         'content': '<USER_REQUEST>\nAGY hook 설정 검토해줘\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nmeta\n</ADDITIONAL_METADATA>'},
        {'step_index': 1, 'source': 'MODEL', 'type': 'PLANNER_RESPONSE', 'status': 'DONE',
         'thinking': 'private thought',
         'tool_calls': [{'name': 'read_file', 'args': {'Path': '/app/setup.sh', 'secret': 's3cr3t'}}]},
        {'step_index': 2, 'source': 'MODEL', 'type': 'GENERIC', 'status': 'DONE',
         'content': 'Showing setup.sh\n\nThe following is a <SYSTEM_MESSAGE>reminder</SYSTEM_MESSAGE>'},
        {'step_index': 3, 'source': 'MODEL', 'type': 'PLANNER_RESPONSE', 'status': 'DONE',
         'content': 'AGY 플러그인과 hook 연동이 가능합니다.'},
    ]
    agy_compact.write_text(json.dumps({**agy_records[0], 'content': 'truncated'}) + '\n')
    agy_full.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in agy_records))
    agy_store = Store()
    agy_seen = []
    with patch.object(s, 'HOME', agy_codex), patch.object(s, 'STATE', agy_state), \
            patch.object(s.subprocess, 'Popen') as spawn, \
            patch.object(s, 'extract_task_memories', side_effect=lambda st, msgs, previous=None, **kw: (
                agy_seen.append(msgs) or [{'text': msgs[0]['content'] + ' -> ' + msgs[-1]['content']}])):
        s.enqueue({'hookEventName': 'Stop', 'conversationId': agy_session,
                   'transcriptPath': str(agy_compact)})
        assert spawn.called
        agy_job = next(agy_state.glob('*.job.json'))
        assert json.loads(agy_job.read_text())['path'] == str(agy_full.resolve())
        agy_stats = s.process_job(agy_job, lambda: agy_store, state_dir=agy_state)
        assert agy_stats['inserted'] == 1
        assert agy_seen[0][0] == {'role': 'user', 'content': 'AGY hook 설정 검토해줘'}
        assert agy_seen[0][-1] == {'role': 'final', 'content': 'AGY 플러그인과 hook 연동이 가능합니다.'}
        assert 's3cr3t' not in json.dumps(agy_seen[0], ensure_ascii=False)
        assert 'SYSTEM_MESSAGE' not in json.dumps(agy_seen[0], ensure_ascii=False)
        assert 'private thought' not in json.dumps(agy_seen[0], ensure_ascii=False)
        # Repeated Stop must be a no-op.
        s.enqueue({'hookEventName': 'Stop', 'conversationId': agy_session,
                   'transcriptPath': str(agy_compact)})
        assert s.process_job(next(agy_state.glob('*.job.json')), lambda: agy_store, state_dir=agy_state)['inserted'] == 0
        # Reject transcripts outside Antigravity directories.
        outside = home / 'outside.jsonl'
        outside.write_text('{}\n')
        try:
            s.enqueue({'hookEventName': 'Stop', 'conversationId': agy_session,
                       'transcriptPath': str(outside)})
            raise AssertionError('Expected outside Antigravity path rejection')
        except ValueError:
            pass

    for event_name, expected_out in (('SessionStart', {}), ('Stop', {'decision': 'allow'})):
        agy_out = io.StringIO()
        with patch.object(Path, 'home', return_value=agy_root), \
                patch.object(sys, 'argv', ['mem0_session.py']), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps({
                    'hookEventName': event_name, 'conversationId': agy_session,
                    'transcriptPath': str(agy_compact)}))), \
                patch.object(s.subprocess, 'Popen'), contextlib.redirect_stdout(agy_out):
            runpy.run_path(str(Path(s.__file__)), run_name='__main__')
        assert json.loads(agy_out.getvalue()) == expected_out

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
