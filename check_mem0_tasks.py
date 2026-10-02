"""Run with ~/.codex/toolkit/venv/bin/python check_mem0_tasks.py (no network)."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import mem0_session as session_import
import mem0_mcp


class Store:
    def __init__(self):
        self.rows = {}
        self.metadata = {}
        self.updates = 0
        self.fail_after_insert = False

    def get_all(self, filters):
        row = self.rows.get(filters['toolkit_fact_id'])
        return {'results': [row] if row else []}

    def add(self, fact, user_id, infer, metadata):
        assert user_id == 'codex' and infer is False
        self.rows[metadata['toolkit_fact_id']] = {
            'id': metadata['toolkit_fact_id'], 'memory': fact}
        self.metadata[metadata['toolkit_fact_id']] = metadata
        if self.fail_after_insert:
            raise ConnectionError('Connection lost after insert')
        return {'results': [{'id': metadata['toolkit_fact_id']}]}

    def update(self, memory_id, text, metadata=None):
        self.rows[memory_id]['memory'] = text
        self.metadata[memory_id].update(metadata or {})
        self.updates += 1
        return {'message': 'Memory updated successfully!'}


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    state = root / 'state'
    state.mkdir()
    transcript = root / 'sessions' / 'test.jsonl'
    transcript.parent.mkdir()
    session_id = str(uuid.uuid4())

    def append(kind, payload):
        with transcript.open('a') as out:
            out.write(json.dumps({'type': kind, 'payload': payload}) + '\n')

    def item(kind, **fields):
        append('event_msg', {'type': 'item_completed', 'item': {'type': kind, **fields}})

    def queue():
        with patch.object(session_import.subprocess, 'Popen'):
            session_import.enqueue({'hook_event_name': 'SessionEnd',
                                    'session_id': session_id, 'transcript_path': str(transcript)})
        return next(state.glob('*.job.json'))

    append('session_meta', {'id': session_id})
    item('UserMessage', content=[{'type': 'text', 'text': '배포가 실패한다'}])
    item('AgentMessage', phase='commentary', content=[{'type': 'text', 'text': '로그를 확인한다'}])
    item('CommandExecution', command=['kubectl', 'logs', 'app'], exit_code=0,
         status='completed', aggregated_output='ImagePullBackOff')
    item('FileChange', changes={'deploy.yaml': {'kind': 'update'}},
         status='completed', stdout='', stderr='')
    item('AgentMessage', phase='final_answer', content=[{'type': 'text', 'text': '이미지 태그 오류를 확인했다'}])
    item('UserMessage', content=[{'type': 'text', 'text': '태그를 고쳐줘'}])
    item('CommandExecution', command='git commit -m fix', exit_code=0,
         status='completed', aggregated_output='1 file changed')
    item('AgentMessage', phase='final_answer', content=[{'type': 'text', 'text': '태그 수정 후 배포 성공'}])
    item('UserMessage', content=[{'type': 'text', 'text': '다른 것도 살펴줘'}])

    store = Store()
    seen = []

    def extract(_store, messages, previous=None, **kwargs):
        seen.append(messages)
        return [{'text': f'문제: {messages[0]["content"]} 조치 및 결과: {messages[-1]["content"]}'}]

    with patch.object(session_import, 'STATE', state), patch.object(session_import, 'HOME', root), \
            patch.object(session_import, 'extract_task_memories', side_effect=extract):
        stats = session_import.process_job(queue(), lambda: store)
        assert stats['inserted'] == 2, stats
        assert len(store.rows) == 2
        assert {value['memory_kind'] for value in store.metadata.values()} == {'task_outcome'}
        assert len(seen) == 2
        assert 'ImagePullBackOff' in json.dumps(seen[0], ensure_ascii=False)
        assert 'deploy.yaml' in json.dumps(seen[0], ensure_ascii=False)
        assert 'git commit -m fix' in json.dumps(seen[1], ensure_ascii=False)
        assert '다른 것도 살펴줘' not in json.dumps(seen, ensure_ascii=False)
        assert session_import.process_job(queue(), lambda: store)['inserted'] == 0

print('PASS: two completed tasks produce two causal memories; unfinished work waits.')

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    state = root / 'state'
    state.mkdir()
    transcript = root / 'sessions' / 'steered.jsonl'
    transcript.parent.mkdir()
    session_id = str(uuid.uuid4())
    events = [
        {'type': 'session_meta', 'payload': {'id': session_id}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '첫 요청'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'commentary', 'message': '첫 조사'}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '두 번째 요청'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer', 'message': '두 번째 결과'}},
    ]
    transcript.write_text(''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in events))
    job = state / 'steered.job.json'
    session_import.write_json(job, {'session': session_id, 'path': str(transcript), 'end': transcript.stat().st_size})
    store = Store()
    seen = []

    def extract(_store, messages, previous=None, **kwargs):
        seen.append((messages, previous))
        return [{'text': messages[0]['content'] + ': ' + messages[-1]['content']}]

    with patch.object(session_import, 'extract_task_memories', side_effect=extract):
        stats = session_import.process_job(job, lambda: store, state_dir=state)
    assert stats['inserted'] == 2 and stats['updated'] == 0
    assert len(store.rows) == 2
    assert [row['memory'] for row in store.rows.values()] == ['첫 요청: 첫 조사', '두 번째 요청: 두 번째 결과']
    assert seen[1][1]['text'] == '첫 요청: 첫 조사'

print('PASS: steering within a turn creates a separate vector for each user message.')

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    state = root / 'state'
    state.mkdir()
    transcript = root / 'sessions' / 'continued.jsonl'
    transcript.parent.mkdir()
    session_id = str(uuid.uuid4())
    records = [
        {'type': 'session_meta', 'payload': {'id': session_id}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '배포 오류 원인을 찾아줘'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                                          'message': '이미지 태그 오류를 확인했지만 수정은 아직 안 했다'}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '그 태그를 수정해줘'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                                          'message': '태그 수정 후 배포 성공을 검증했다'}},
    ]
    transcript.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records[:3]))
    store = Store()
    observed_previous = []

    def extract_continued(_store, messages, previous=None, **kwargs):
        observed_previous.append(previous)
        if previous:
            return [{'text': '문제/증상: 태그 수정 요청\n조치: 태그 수정\n결과: 배포 성공 검증'}]
        return [{'text': '문제/증상: 배포 오류\n조치: 태그 조사\n결과: 수정 전'}]

    with patch.object(session_import, 'STATE', state), patch.object(session_import, 'HOME', root), \
            patch.object(session_import, 'extract_task_memories', side_effect=extract_continued):
        with patch.object(session_import.subprocess, 'Popen'):
            session_import.enqueue({'hook_event_name': 'SessionEnd',
                                    'session_id': session_id, 'transcript_path': str(transcript)})
        session_import.process_job(next(state.glob('*.job.json')), lambda: store)
        with transcript.open('a') as out:
            out.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records[3:]))
        with patch.object(session_import.subprocess, 'Popen'):
            session_import.enqueue({'hook_event_name': 'SessionEnd',
                                    'session_id': session_id, 'transcript_path': str(transcript)})
        job = next(state.glob('*.job.json'))
        old_cursor = (state / f'{session_id}.cursor.json').read_bytes()
        store.fail_after_insert = True
        try:
            session_import.process_job(job, lambda: store)
            raise AssertionError('Expected lost insert acknowledgement')
        except ConnectionError:
            pass
        assert (state / f'{session_id}.cursor.json').read_bytes() == old_cursor
        store.fail_after_insert = False
        stats = session_import.process_job(job, lambda: store)
        assert len(store.rows) == 2
        assert store.updates == 0
        assert stats['inserted'] == 0  # Retry confirms the already committed insert.
        assert stats['updated'] == 0
        assert any('수정 전' in row['memory'] for row in store.rows.values())
        assert any('배포 성공 검증' in row['memory'] for row in store.rows.values())
        assert observed_previous[0] is None
        assert '배포 오류' in observed_previous[1]['text']

print('PASS: each user request inserts its own memory; retries do not duplicate it.')

store = MagicMock()
choice = store.llm.client.with_options.return_value.chat.completions.create.return_value.choices.__getitem__.return_value
choice.finish_reason = 'stop'
choice.message.content = json.dumps({'tasks': [
    {'problem': '배포 실패', 'actions': '로그 확인 후 태그 수정',
     'result': '배포가 정상 상태임을 확인'},
    {'problem': '알림 누락', 'actions': '라우팅 설정 조사',
     'result': '외부 수신자 미설정 확인'},
]})
try:
    mem0_mcp.extract_task_records(store, [{'role': 'user', 'content': '한 요청'}])
    raise AssertionError('One user request must produce exactly one memory')
except ValueError:
    pass
choice.message.content = json.dumps({'tasks': [{
    'problem': '배포 실패', 'actions': '로그 확인 후 태그 수정',
    'result': '배포가 정상 상태임을 확인'}]})
assert mem0_mcp.extract_task_records(store, [{'role': 'user', 'content': '두 작업'}]) == [
    {'text': '배포 실패\n로그 확인 후 태그 수정\n배포가 정상 상태임을 확인'},
]
choice.message.content = json.dumps({'tasks': [{
    'problem': '배포 실패', 'actions': '조사', 'result': ''}]})
try:
    mem0_mcp.extract_task_records(store, [])
    raise AssertionError('Empty result must be rejected')
except ValueError:
    pass
choice.message.content = json.dumps({'tasks': [{
    'problem': '배포 실패', 'actions': '조사', 'result': ['미확인']}]})
try:
    mem0_mcp.extract_task_records(store, [])
    raise AssertionError('Non-string result must be rejected')
except ValueError:
    pass

with patch.object(mem0_mcp, 'extract_facts', side_effect=lambda store, messages, prompt, **kwargs: [
        '문제와 조치가 이어진다']) as summarize, \
        patch.object(mem0_mcp, 'extract_task_records', return_value=[
            {'text': '완료된 작업'}]) as finish:
    assert session_import.extract_task_memories(store, [
        {'role': 'user', 'content': '장기 작업 ' * 3000},
        {'role': 'final', 'content': '성공을 검증했다'}]) == [
            {'text': '완료된 작업'}]
    assert summarize.call_count > 1
    assert '문제와 조치가 이어진다' in finish.call_args.args[1][0]['content']

with patch.object(mem0_mcp, 'extract_task_records', return_value=[{'text': '현재 요청 결과'}]) as finish:
    session_import.extract_task_memories(store, [{'role': 'user', 'content': '그 태그를 고쳐줘'}],
                                         {'text': '문제/증상: 이미지 태그 오류\n조치: 로그 조사\n결과: 오타 발견'})
    assert finish.call_args.args[2] == '문제/증상: 이미지 태그 오류'

print('PASS: one outcome per request, observed results without status labels, bounded long-task inference.')

def task_response(problem, actions, result):
    payload = {'tasks': [{'problem': problem, 'actions': actions, 'result': result}]}
    choice = SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False), model_extra={}))
    return SimpleNamespace(choices=[choice])

store = MagicMock()
create = store.llm.client.with_options.return_value.chat.completions.create
create.side_effect = [task_response('网络访问异常', '检查了路由和代理', '原因未确认'),
                      task_response('네트워크 접속 이상', '라우팅과 프록시를 점검했다', '원인을 확인하지 못했다')]
records = mem0_mcp.extract_task_records(store, [{'role': 'user', 'content': '네트워크가 안 돼'}])
assert create.call_count == 2
assert '네트워크 접속 이상' in records[0]['text'] and '网络访问异常' not in records[0]['text']
assert '한국어' in create.call_args.kwargs['messages'][0]['content']

store = MagicMock()
create = store.llm.client.with_options.return_value.chat.completions.create
create.return_value = task_response('网络访问异常', '检查了路由和代理', '原因未确认')
try:
    mem0_mcp.extract_task_records(store, [{'role': 'user', 'content': '네트워크가 안 돼'}])
    raise AssertionError('Chinese task descriptions must not be stored')
except ValueError:
    pass

print('PASS: Chinese task output is retried in Korean and rejected if still untranslated.')

secret = '{"password": "hunter2", "apiKey": "topsecret"}'
assert 'hunter2' not in session_import.redact(secret)
assert 'topsecret' not in session_import.redact(secret)
print('PASS: JSON-shaped credentials are redacted before inference.')

def summary_store():
    store = MagicMock()
    store.llm.config.lmstudio_response_format = {'type': 'json_schema', 'json_schema': {
        'name': 'mem0_facts', 'schema': {'type': 'object', 'properties': {'memory': {
            'type': 'array', 'items': {'type': 'object', 'properties': {'text': {'type': 'string'}}}}}}}}
    return store

store = summary_store()
create = store.llm.client.with_options.return_value.chat.completions.create
create.return_value = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
    message=SimpleNamespace(content=json.dumps({'memory': [{'text': 'x' * 3001}]}),
                            model_extra={}))], usage=None)
try:
    session_import.extract_task_memories(store, [{'role': 'user', 'content': 'a' * 16000}])
    raise AssertionError('Oversized evidence must not be clipped and accepted')
except mem0_mcp.ExtractionLimitError as error:
    assert error.details['reason'] == 'summary_size'
assert create.call_count == 4  # Stop after 12000, 6000, 3000 and 1500 characters.
print('PASS: oversized summaries are rejected with bounded retries instead of silent clipping.')

# Removing the length fallback must leave this real extraction unable to finish.
store = summary_store()
create = store.llm.client.with_options.return_value.chat.completions.create
calls = []
def limited_response(**kwargs):
    data = json.loads(kwargs['messages'][1]['content'])
    events = json.loads(data[0]['content'])['events']
    text = ''.join(event['content'] for event in events)
    calls.append(text)
    if len(text) > 6000:
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='length')],
                               usage=SimpleNamespace(completion_tokens=4095))
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
        message=SimpleNamespace(content=json.dumps({'memory': [{'text': text[:20]}]}),
                                model_extra={}))], usage=None)
create.side_effect = limited_response
with patch.object(mem0_mcp, 'extract_task_records', return_value=[{'text': '복구 완료'}]):
    assert session_import.extract_task_memories(store, [
        {'role': 'user', 'content': 'a' * 12000 + 'b' * 4000}]) == [{'text': '복구 완료'}]
assert ''.join(text for text in calls if len(text) <= 6000) == 'a' * 12000 + 'b' * 4000
print('PASS: truncated inference splits input without dropping or duplicating evidence.')

# A later network failure must preserve successful summaries for the next run.
with tempfile.TemporaryDirectory() as tmp:
    progress = Path(tmp) / 'summary.json'
    store = summary_store()
    create = store.llm.client.with_options.return_value.chat.completions.create
    calls = []
    def resumable_response(**kwargs):
        data = json.loads(kwargs['messages'][1]['content'])
        text = ''.join(event['content'] for event in json.loads(data[0]['content'])['events'])
        calls.append(text)
        if text.startswith('b') and calls.count(text) == 1:
            raise ConnectionError('offline')
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps({'memory': [{'text': '근거 ' + text[0]}]}),
                                    model_extra={}))], usage=None)
    create.side_effect = resumable_response
    messages = [{'role': 'user', 'content': 'a' * 12000 + 'b' * 4000}]
    with patch.object(mem0_mcp, 'extract_task_records', return_value=[{'text': '재개 완료'}]):
        try:
            session_import.extract_task_memories(store, messages, progress_path=progress)
            raise AssertionError('Expected outage')
        except ConnectionError:
            pass
        assert progress.exists() and progress.stat().st_mode & 0o777 == 0o600
        assert session_import.extract_task_memories(store, messages,
                                                   progress_path=progress) == [{'text': '재개 완료'}]
        assert calls.count('a' * 12000) == 1
        try:
            session_import.extract_task_memories(store, [{'role': 'user', 'content': 'changed' * 3000}],
                                               progress_path=progress)
            raise AssertionError('Changed evidence must not reuse a summary')
        except ValueError:
            pass
print('PASS: successful summaries survive outages and reject changed source evidence.')

store = MagicMock()
create = store.llm.client.with_options.return_value.chat.completions.create
create.side_effect = [SimpleNamespace(choices=[SimpleNamespace(finish_reason='length')],
                                     usage=SimpleNamespace(completion_tokens=4095)),
                      task_response('작업 요청', '작업을 수행했다', '검증했다')]
assert mem0_mcp.extract_task_records(store, [{'role': 'user', 'content': '작업'}]) == [
    {'text': '작업 요청\n작업을 수행했다\n검증했다'}]
print('PASS: a truncated final record is regenerated as a shorter complete record.')

store = MagicMock()
create = store.llm.client.with_options.return_value.chat.completions.create
create.side_effect = [task_response('가' * 1001, '작업했다', '검증했다'),
                      task_response('작업 요청', '작업했다', '검증했다')]
assert mem0_mcp.extract_task_records(store, []) == [{'text': '작업 요청\n작업했다\n검증했다'}]
create.side_effect = None
create.return_value = task_response('가' * 1001, '작업했다', '검증했다')
try:
    mem0_mcp.extract_task_records(store, [])
    raise AssertionError('Oversized final records must not be stored')
except mem0_mcp.ExtractionLimitError as error:
    assert error.details['reason'] == 'record_size'
print('PASS: providers ignoring field limits cannot store oversized final records.')

store = summary_store()
store.llm.client.with_options.return_value.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content='{"memory": []}', model_extra={}))], usage=None)
try:
    mem0_mcp.extract_facts(store, [{'role': 'user', 'content': '관찰된 근거'}], max_chars=3000)
    raise AssertionError('Empty rolling summaries must not erase prior evidence')
except ValueError:
    pass
print('PASS: empty bounded summaries cannot erase prior evidence.')

# The constrained response must have room to rewrite actions/results together;
# a capped list otherwise fills with early facts and freezes later evidence out.
store = summary_store()
def single_summary_response(**kwargs):
    schema = kwargs['response_format']['json_schema']['schema']['properties']['memory']
    assert schema['maxItems'] == 1
    assert schema['items']['properties']['text']['maxLength'] == 3000
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content='{"memory": [{"text": "요청과 최신 조치·검증 결과를 함께 갱신했다"}]}',
        model_extra={}))], usage=None)
store.llm.client.with_options.return_value.chat.completions.create.side_effect = single_summary_response
assert mem0_mcp.extract_facts(store, [], max_chars=3000) == ['요청과 최신 조치·검증 결과를 함께 갱신했다']
print('PASS: bounded evidence is one rewritable summary, not a saturated fact list.')

store = summary_store()
store.llm.client.with_options.return_value.chat.completions.create.return_value = SimpleNamespace(
    choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content='{"memory": [{"text": "초기 근거"}, {"text": "최신 근거"}]}', model_extra={}))], usage=None)
try:
    mem0_mcp.extract_facts(store, [], max_chars=3000)
    raise AssertionError('A provider must not restore the saturated-list format')
except ValueError:
    pass
print('PASS: bounded evidence rejects providers ignoring the single-summary schema.')

# Long reductions must also split on output limits and retain later evidence.
store = summary_store()
def reducing_response(**kwargs):
    payload = json.loads(kwargs['messages'][1]['content'])
    events = json.loads(payload[0]['content'])['events']
    chars = sum(len(event['content']) for event in events)
    if chars > 6000:
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='length')], usage=None)
    text = '검증 완료 ' + '근' * 1900
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content=json.dumps({'memory': [{'text': text}]}), model_extra={}))], usage=None)
store.llm.client.with_options.return_value.chat.completions.create.side_effect = reducing_response
with patch.object(mem0_mcp, 'extract_task_records', return_value=[{'text': '최종 검증 완료'}]) as finish:
    assert session_import.extract_task_memories(store, [{'role': 'user', 'content': 'x' * 76000}]) == [
        {'text': '최종 검증 완료'}]
    assert len(finish.call_args.args[1][0]['content']) <= 12000
    assert '검증 완료' in finish.call_args.args[1][0]['content']
print('PASS: long evidence reductions recover from length limits and bound final inference.')

store = summary_store()
def smallest_reduction_response(**kwargs):
    payload = json.loads(kwargs['messages'][1]['content'])
    events = json.loads(payload[0]['content'])['events']
    chars = sum(len(event['content']) for event in events)
    if chars > 1500:
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='length')], usage=None)
    cap = kwargs['response_format']['json_schema']['schema']['properties']['memory']['items']['properties']['text']['maxLength']
    assert cap > 0, 'Reduction must advance through later evidence rather than exhaust its first prefix'
    text = '근' * min(1900, cap)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content=json.dumps({'memory': [{'text': text}]}), model_extra={}))], usage=None)
store.llm.client.with_options.return_value.chat.completions.create.side_effect = smallest_reduction_response
with patch.object(mem0_mcp, 'extract_task_records', return_value=[{'text': '작은 입력도 복구'}]) as finish:
    assert session_import.extract_task_memories(store, [{'role': 'user', 'content': 'x' * 16000}]) == [
        {'text': '작은 입력도 복구'}]
    assert len(finish.call_args.args[1][0]['content']) <= 12000
print('PASS: reductions at the smallest retry size advance through all evidence.')
