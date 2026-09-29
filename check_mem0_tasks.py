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
        self.fail_after_update = False

    def get_all(self, filters):
        row = self.rows.get(filters['toolkit_fact_id'])
        return {'results': [row] if row else []}

    def add(self, fact, user_id, infer, metadata):
        assert user_id == 'codex' and infer is False
        self.rows[metadata['toolkit_fact_id']] = {
            'id': metadata['toolkit_fact_id'], 'memory': fact}
        self.metadata[metadata['toolkit_fact_id']] = metadata
        return {'results': [{'id': metadata['toolkit_fact_id']}]}

    def update(self, memory_id, text, metadata=None):
        self.rows[memory_id]['memory'] = text
        self.metadata[memory_id].update(metadata or {})
        self.updates += 1
        if self.fail_after_update:
            raise ConnectionError('Connection lost after update')
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

    def extract(_store, messages, previous=None):
        seen.append(messages)
        return [{'text': f'문제: {messages[0]["content"]} 조치 및 결과: {messages[-1]["content"]}',
                 'continuation': False}]

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

    def extract_continued(_store, messages, previous=None):
        observed_previous.append(previous)
        if previous:
            return [{'text': '문제/증상: 배포 오류\n조치: 태그 오류 조사 및 수정\n결과: 배포 성공 검증\n상태: 성공',
                     'continuation': True}]
        return [{'text': '문제/증상: 배포 오류\n조치: 태그 조사\n결과: 수정 전\n상태: 미해결',
                 'continuation': False}]

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
        store.fail_after_update = True
        try:
            session_import.process_job(job, lambda: store)
            raise AssertionError('Expected lost update acknowledgement')
        except ConnectionError:
            pass
        assert (state / f'{session_id}.cursor.json').read_bytes() == old_cursor
        store.fail_after_update = False
        stats = session_import.process_job(job, lambda: store)
        assert len(store.rows) == 1
        assert store.updates == 2
        assert stats['updated'] == 1
        assert '배포 성공 검증' in next(iter(store.rows.values()))['memory']
        assert observed_previous[0] is None
        assert '배포 오류' in observed_previous[1]['text']

print('PASS: a continued task updates its causal memory instead of duplicating it.')

store = MagicMock()
choice = store.llm.client.with_options.return_value.chat.completions.create.return_value.choices.__getitem__.return_value
choice.finish_reason = 'stop'
choice.message.content = json.dumps({'tasks': [
    {'problem': '배포 실패', 'actions': '로그 확인 후 태그 수정',
     'result': '배포가 정상 상태임을 확인', 'status': 'success', 'continuation': False},
    {'problem': '알림 누락', 'actions': '라우팅 설정 조사',
     'result': '외부 수신자 미설정 확인', 'status': 'unresolved', 'continuation': False},
]})
assert mem0_mcp.extract_task_records(store, [{'role': 'user', 'content': '두 작업'}]) == [
    {'text': '문제/증상: 배포 실패\n조치: 로그 확인 후 태그 수정\n결과: 배포가 정상 상태임을 확인\n상태: 성공',
     'continuation': False},
    {'text': '문제/증상: 알림 누락\n조치: 라우팅 설정 조사\n결과: 외부 수신자 미설정 확인\n상태: 미해결',
     'continuation': False},
]
choice.message.content = json.dumps({'tasks': [{
    'problem': '배포 실패', 'actions': '조사', 'result': '미확인', 'status': 'invented',
    'continuation': False}]})
try:
    mem0_mcp.extract_task_records(store, [])
    raise AssertionError('Invalid status must be rejected')
except ValueError:
    pass
choice.message.content = json.dumps({'tasks': [{
    'problem': '배포 실패', 'actions': '조사', 'result': '미확인', 'status': ['success'],
    'continuation': False}]})
try:
    mem0_mcp.extract_task_records(store, [])
    raise AssertionError('Non-string status must be rejected')
except ValueError:
    pass

with patch.object(mem0_mcp, 'extract_facts', side_effect=lambda store, messages, prompt: [
        '문제와 조치가 이어진다']) as summarize, \
        patch.object(mem0_mcp, 'extract_task_records', return_value=[
            {'text': '완료된 작업', 'continuation': False}]) as finish:
    assert session_import.extract_task_memories(store, [
        {'role': 'user', 'content': '장기 작업 ' * 1800},
        {'role': 'final', 'content': '성공을 검증했다'}]) == [
            {'text': '완료된 작업', 'continuation': False}]
    assert summarize.call_count > 1
    assert '문제와 조치가 이어진다' in finish.call_args.args[1][0]['content']

print('PASS: multiple outcomes per turn, strict status, bounded long-task inference.')

def task_response(problem, actions, result):
    payload = {'tasks': [{'problem': problem, 'actions': actions, 'result': result,
                          'status': 'unresolved', 'continuation': False}]}
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

def growing_summary(_store, messages, _prompt):
    previous = json.loads(messages[0]['content'])['prior_evidence']
    return previous + ['e' * 2500]

with patch.object(mem0_mcp, 'extract_facts', side_effect=growing_summary), \
        patch.object(mem0_mcp, 'extract_task_records', return_value=[
            {'text': '긴 작업 결과', 'continuation': False}]) as finish:
    assert session_import.extract_task_memories(store, [
        {'role': 'user', 'content': '긴 작업 ' * 4500},
        {'role': 'final', 'content': '검증 완료'}]) == [
            {'text': '긴 작업 결과', 'continuation': False}]
    assert len(finish.call_args.args[1][0]['content']) <= session_import.MAX_TEXT // 2

print('PASS: growing evidence remains bounded across a long task.')
