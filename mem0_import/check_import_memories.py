"""No-network self-check for the document importer."""
import json
import io
from pathlib import Path
import sys
import tempfile
import uuid

from contextlib import redirect_stdout
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_memories as importer


class Store:
    def __init__(self):
        self.rows = {}

    def get_all(self, filters):
        return {'results': [self.rows[filters['toolkit_fact_id']]]} if filters['toolkit_fact_id'] in self.rows else {'results': []}

    def add(self, fact, user_id, infer, metadata):
        assert user_id == 'codex' and infer is False
        self.rows[metadata['toolkit_fact_id']] = fact
        return {'results': [{'id': metadata['toolkit_fact_id']}]}


class NoisyStore(Store):
    def add(self, fact, user_id, infer, metadata):
        print('HTTP Request: POST http://example "HTTP/1.1 200 OK"')
        print('Inserting 1 vectors into collection "CODEX_MEMORIES"')
        print('WARNING: provider degraded')
        return super().add(fact, user_id, infer, metadata)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / 'nested').mkdir()
    (root / 'note.md').write_text('Oracle\n\nUse Python.')
    (root / 'nested/page.html').write_text('<h1>Title</h1><script>ignore()</script><p>HTML fact.</p>')
    (root / 'data.json').write_text(json.dumps({'fact': 'JSON fact'}, ensure_ascii=False))
    (root / 'events.jsonl').write_text('{"fact":"JSONL fact"}\n')
    (root / 'ignored.bin').write_bytes(b'ignored')
    store = Store()
    with patch.object(importer, 'MAX_CHUNK', 100), patch('mem0_mcp.extract_facts', side_effect=lambda s, messages: [messages[0]['content'].split('Source: ', 1)[1].split('\n\n', 1)[1]]):
        first = importer.import_tree(root, store_factory=lambda: store, show_progress=False)
        second = importer.import_tree(root, store_factory=lambda: store, show_progress=False)
    assert first['files'] == 4 and first['inserted'] == 4 and first['errors'] == 0
    assert second['inserted'] == 0 and second['skipped'] == 4
    assert 'ignore()' not in next(iter(store.rows.values()))

    status = root / '.status'
    issues = root / '.issues'
    assert importer.main([
        str(root), '--dry-run', '--status-file', str(status), '--error-log', str(issues),
    ]) == 0
    events = [json.loads(line) for line in status.read_text().splitlines()]
    assert events[0] == {'event': 'start', 'total': 4}
    assert sum(event['event'] == 'file_complete' for event in events) == 4
    assert events[-1]['event'] == 'complete' and events[-1]['exit_code'] == 0
    rendered = io.StringIO()
    with redirect_stdout(rendered):
        assert importer.follow_status(status) == 0
    assert 'note.md' in rendered.getvalue() and '"errors": 0' in rendered.getvalue()

    noisy = NoisyStore()
    with patch('mem0_mcp.extract_facts', return_value=['fact']):
        importer.import_tree(root, store_factory=lambda: noisy, show_progress=False, error_log=issues)
    assert issues.read_text().splitlines() == ['WARNING: provider degraded'] * 4

print('PASS: recursive common formats, HTML filtering, redaction path, deterministic duplicate prevention.')


class TaskStore(Store):
    def __init__(self):
        super().__init__()
        self.updates = 0

    def add(self, fact, user_id, infer, metadata):
        assert metadata['memory_kind'] == 'task_outcome'
        key = metadata['toolkit_fact_id']
        self.rows[key] = {'id': key, 'memory': fact, 'metadata': metadata}
        return {'results': [{'id': key}]}

    def update(self, memory_id, text, metadata=None):
        self.rows[memory_id]['memory'] = text
        self.rows[memory_id]['metadata'].update(metadata or {})
        self.updates += 1
        return {'message': 'Memory updated successfully!'}


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / 'archive'
    root.mkdir()
    session_id = str(uuid.uuid4())
    transcript = root / 'session.jsonl'
    events = [
        {'type': 'session_meta', 'payload': {'id': session_id}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '배포가 실패한다'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                                          'message': '이미지 태그 오류를 확인했다'}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '그 태그를 고쳐줘'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                                          'message': '수정 후 배포 성공을 확인했다'}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '알림 설정을 조사해줘'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                                          'message': '수신자가 없는 상태로 확인됐다'}},
    ]
    transcript.write_text(''.join(json.dumps(event, ensure_ascii=False) + '\n' for event in events) + '{')
    store = TaskStore()
    seen = []

    def extract(_store, messages, previous=None):
        seen.append((messages, previous))
        first = messages[0]['content']
        if first == '그 태그를 고쳐줘':
            assert previous and '배포' in previous['text']
            return [{'text': '문제: 배포 실패; 조치: 태그 조사와 수정; 결과: 배포 성공',
                     'continuation': True}]
        return [{'text': f'문제: {first}; 조치와 결과: {messages[-1]["content"]}',
                 'continuation': False}]

    with patch('mem0_session.extract_task_memories', side_effect=extract):
        first = importer.import_tree(root, store_factory=lambda: store, show_progress=False,
                                     state_root=Path(tmp) / 'state')
        second = importer.import_tree(root, store_factory=lambda: store, show_progress=False,
                                      state_root=Path(tmp) / 'state')
    assert first['inserted'] == 2 and first['updated'] == 1 and first['errors'] == 0
    assert len(store.rows) == 2 and store.updates == 1
    assert any('배포 성공' in row['memory'] for row in store.rows.values())
    assert second['inserted'] == 0 and second['updated'] == 0 and second['errors'] == 0
    assert len(seen) == 3

print('PASS: Codex JSONL imports task outcomes, merges continuation, and resumes idempotently.')

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / 'archive'
    root.mkdir()
    session_id = str(uuid.uuid4())
    transcript = root / 'session.jsonl'
    events = [
        {'type': 'session_meta', 'payload': {'id': session_id}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'task one'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer', 'message': 'done one'}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'task two'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer', 'message': 'done two'}},
        {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'task three'}},
        {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer', 'message': 'done three'}},
    ]
    transcript.write_text(''.join(json.dumps(event) + '\n' for event in events))
    store = TaskStore()

    def extract(_store, messages, previous=None):
        task = messages[0]['content']
        return [{'text': f'progress through {task}', 'continuation': task != 'task one'}]

    with patch('mem0_session.extract_task_memories', side_effect=extract):
        importer.import_tree(root, store_factory=lambda: store, show_progress=False,
                             state_root=Path(tmp) / 'full-state')
        latest = next(iter(store.rows.values()))['memory']
        transcript.write_text(''.join(json.dumps(event) + '\n' for event in events[:5]))
        result = importer.import_tree(root, store_factory=lambda: store, show_progress=False,
                                      state_root=Path(tmp) / 'older-state')
    assert result['errors'] == 0 and next(iter(store.rows.values()))['memory'] == latest

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / 'events.jsonl').write_text('{"type":"session_meta","payload":{"id":"batch-42"}}\n'
                                      '{"fact":"keep this durable fact"}\n')
    with patch('mem0_mcp.extract_facts', return_value=['keep this durable fact']):
        result = importer.import_tree(root, store_factory=Store, show_progress=False)
    assert result['inserted'] == 1 and result['errors'] == 0

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    session_id = str(uuid.uuid4())
    (root / 'session.jsonl').write_text(
        json.dumps({'type': 'session_meta', 'payload': {'id': session_id}}) + '\n' +
        json.dumps({'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'task'}}) + '\n' +
        json.dumps({'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                                                  'message': 'done'}}))
    with patch('mem0_session.extract_task_memories', return_value=[{'text': 'task done', 'continuation': False}]):
        result = importer.import_tree(root, store_factory=TaskStore, show_progress=False,
                                      state_root=root / 'state')
    assert result['chunks'] == 1 and result['inserted'] == 1 and result['errors'] == 0

print('PASS: older copies preserve newer outcomes, generic JSONL stays generic, and complete EOF records import.')
