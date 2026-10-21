"""Run with ~/.codex/toolkit/venv/bin/python check_mem0_chunks.py (no network)."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock
import uuid

import mem0_mcp as m
import mem0_session as s


def reply(*results):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
        content=json.dumps({'tasks': [
            {'problem': 'route.md 라우팅 표 보완', 'actions': '근거를 조사했다', 'result': result}
            for result in results]}, ensure_ascii=False), model_extra={}))])


def request_events(kwargs):
    payload = json.loads(kwargs['messages'][1]['content'])
    assert isinstance(payload, dict) and 'events' in payload, 'Extract original evidence, not summaries'
    return payload['events']


class Store:
    def __init__(self, create):
        self.llm = MagicMock()
        self.llm.config.model = 'test'
        self.llm.config.lmstudio_response_format = {'type': 'json_schema', 'json_schema': {
            'name': 'facts', 'schema': {'properties': {'memory': {
                'items': {'properties': {'text': {'type': 'string'}}}}}}}}
        self.llm.client.with_options.return_value.chat.completions.create.side_effect = create
        self.rows = {}
        self.metadata = {}
        self.inserts = []
        self.lose_ack = False

    def get_all(self, filters):
        row = self.rows.get(filters['toolkit_fact_id'])
        return {'results': [row] if row else []}

    def add(self, text, user_id, infer, metadata):
        assert user_id == 'codex' and infer is False
        key = metadata['toolkit_fact_id']
        self.inserts.append(key)
        self.rows[key] = {'id': key, 'memory': text}
        self.metadata[key] = metadata
        if self.lose_ack and len(self.rows) == 2:
            raise ConnectionError('Lost acknowledgement after second commit')
        return {'results': [{'id': key}]}


class SemanticChunks(unittest.TestCase):
    def test_tool_inputs_results_and_middle_of_diff_survive_collection(self):
        patch = 'x' * 1300 + '\n+ Destination | Target\n+ 10.0.0.1/32 | tgw-1\n' + 'y' * 1300
        call = s.task_message({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call', 'name': 'apply_patch', 'call_id': 'edit-1', 'input': patch}})
        self.assertIsNotNone(call, 'Custom tool calls must reach extraction')
        self.assertEqual(json.loads(call['content'])['input'], patch)
        self.assertEqual(call['call_id'], 'edit-1')
        result = s.task_message({'type': 'response_item', 'payload': {
            'type': 'custom_tool_call_output', 'call_id': 'edit-1', 'output': 'Success'}})
        self.assertIn('Success', result['content'])
        for changes in ({'route.md': {'diff': patch}}, [{'path': 'route.md', 'diff': patch}]):
            change = s.task_message({'type': 'event_msg', 'payload': {'type': 'item_completed',
                'item': {'type': 'FileChange', 'changes': changes, 'status': 'completed'}}})
            actual = json.loads(change['content'])['changes']
            self.assertEqual(actual['route.md']['diff'] if isinstance(actual, dict) else actual[0]['diff'], patch)
            self.assertIn('route.md', change['content'])
        self.assertIsNone(s.task_message({'type': 'response_item', 'payload': {
            'type': 'reasoning', 'text': 'Private reasoning is not evidence'}}))

    def test_call_identity_deduplicates_mirrored_results_without_losing_arguments(self):
        session = str(uuid.uuid4())
        events = [
            {'type': 'session_meta', 'payload': {'id': session}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '표를 보완해줘'}},
            {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'read_file',
                'call_id': 'read-1', 'arguments': '{"path":"route.md","password":"hunter2"}'}},
            {'type': 'event_msg', 'payload': {'type': 'mcp_tool_call_end', 'call_id': 'read-1',
                'invocation': {'server': 'files', 'tool': 'read_file', 'arguments': {'path': 'route.md'}},
                'result': {'Ok': {'content': [{'type': 'text', 'text': 'Destination | Target'}]}}}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output',
                'call_id': 'read-1', 'output': 'Destination | Target'}},
            {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                'message': '표를 확인했다'}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'transcript.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            [(messages, cursor)] = list(s.batches(path, session, {}, path.stat().st_size))
        tools = [message for message in messages if message['role'] == 'tool']
        self.assertEqual(len(tools), 2, 'Keep a call and one observed result for the same ID')
        self.assertIn('route.md', tools[0]['content'])
        self.assertIn('Destination | Target', tools[1]['content'])
        self.assertNotIn('hunter2', json.dumps(messages))
        self.assertGreater(cursor['start_offset'], 0)

    def test_nested_json_credentials_are_redacted_before_inference(self):
        arguments = {'payload': json.dumps({'password': 'swordfish'}),
                     'cmd': 'curl -d ' + json.dumps({'api_key': 'nested-secret'}) + ' https://example.test'}
        call = s.task_message({'type': 'response_item', 'payload': {
            'type': 'function_call', 'name': 'command', 'call_id': 'nested-1',
            'arguments': json.dumps(arguments)}})
        self.assertNotIn('swordfish', call['content'])
        self.assertNotIn('nested-secret', call['content'])
        self.assertIn('https://example.test', call['content'])

    def test_many_short_tools_and_escaped_context_fit_serialized_input_budget(self):
        observed = []
        def create(**kwargs):
            payload = kwargs['messages'][1]['content']
            self.assertLessEqual(len(payload), s.MAX_TEXT)
            events = request_events(kwargs)
            observed.extend(event['content'] for event in events if event['role'] != 'context')
            return reply('구간을 확인했다')
        messages = [{'role': 'user', 'content': '작업 요청' + '\x01' * 2000}] + [
            {'role': 'tool', 'content': 'operation ok', 'call_id': 'call_%036d' % index,
             'tool_phase': 'result'} for index in range(1100)
        ] + [{'role': 'final', 'content': '검사 완료'}]
        self.assertTrue(s.extract_task_memories(Store(create), messages))
        self.assertEqual(''.join(observed), ''.join(message['content'] for message in messages))

    def test_long_requests_keep_all_source_windows_and_shared_context(self):
        observed = []
        def create(**kwargs):
            events = request_events(kwargs)
            observed.append(events)
            return reply('공통 확인 결과', '구간 확인 결과 ' + str(len(observed)))
        store = Store(create)
        messages = [
            {'role': 'user', 'content': 'route.md의 Destination Target 표를 보완해줘'},
            {'role': 'tool', 'name': 'read_file', 'call_id': 'read-1',
             'content': ' '.join('step-%04d' % index for index in range(3000)) + '중간 설정 target=tgw-1'},
            {'role': 'final', 'content': '표 형식은 확인했고 실제 장비는 미검증이다'},
        ]
        records = s.extract_task_memories(store, messages)
        self.assertGreater(len(observed), 1)
        self.assertEqual(''.join(event['content'] for events in observed for event in events
            if event['role'] != 'context'), ''.join(message['content'] for message in messages))
        self.assertEqual(sum('공통 확인 결과' in record['text'] for record in records), 1)
        self.assertEqual(len(records), len(observed) + 1)
        for events in observed:
            self.assertLessEqual(sum(len(event['content']) for event in events), s.MAX_TEXT)
            context = next(event['content'] for event in events if event['role'] == 'context')
            self.assertIn('route.md', context)
            self.assertIn('실제 장비는 미검증', context)
            for event in events:
                if event['role'] == 'tool':
                    self.assertEqual(event['call_id'], 'read-1')
        preceding = json.loads(observed[1][0]['content'])['preceding_evidence']
        tail = ''.join(event['content'] for event in observed[0] if event['role'] != 'context')[-100:]
        self.assertIn(tail, ''.join(event['content'] for event in preceding))

    def test_old_lossy_progress_restarts_from_source_and_partial_responses_are_not_accepted(self):
        store = Store(lambda **kwargs: reply('원문에서 재추출했다'))
        with tempfile.TemporaryDirectory() as tmp:
            progress = Path(tmp) / 'summary.json'
            s.write_json(progress, {'source_hash': 'old', 'consumed': 24000, 'summary': ['오래된 요약']})
            records = s.extract_task_memories(store, [{'role': 'user', 'content': '가' * 24000}],
                                             progress_path=progress)
            self.assertTrue(records)
            saved = json.loads(progress.read_text())
            self.assertEqual(saved['consumed'], 24000)
            self.assertNotIn('summary', saved)
        responses = iter([reply('초기 응답', '가' * 1001), reply('재시도한 전체 결과')])
        records = m.extract_task_records(Store(lambda **kwargs: next(responses)), [])
        self.assertEqual(len(records), 1)
        self.assertIn('재시도한 전체 결과', records[0]['text'])
        with self.assertRaises(ValueError):
            m.extract_task_records(Store(lambda **kwargs: reply()), [])

    def test_successful_windows_resume_and_changed_source_is_rejected(self):
        calls = []
        offline = True
        def create(**kwargs):
            nonlocal offline
            events = request_events(kwargs)
            text = ''.join(event['content'] for event in events if event['role'] != 'context')
            calls.append(text)
            if len(calls) == 2 and offline:
                offline = False
                raise ConnectionError('offline')
            return reply('관찰된 구간 ' + text[:1])
        store = Store(create)
        messages = [{'role': 'user', 'content': '가' * 12000 + '나' * 12000}]
        with tempfile.TemporaryDirectory() as tmp:
            progress = Path(tmp) / 'progress.json'
            with self.assertRaises(ConnectionError):
                s.extract_task_memories(store, messages, progress_path=progress)
            self.assertEqual(progress.stat().st_mode & 0o777, 0o600)
            records = s.extract_task_memories(store, messages, progress_path=progress)
            self.assertEqual(calls.count(calls[0]), 1)
            self.assertTrue(any('관찰된 구간 나' in record['text'] for record in records))
            with self.assertRaises(ValueError):
                s.extract_task_memories(store, [{'role': 'user', 'content': 'changed' * 5000}],
                                        progress_path=progress)

    def test_output_limits_split_source_instead_of_compressing_away_details(self):
        accepted = []
        def create(**kwargs):
            events = request_events(kwargs)
            text = ''.join(event['content'] for event in events if event['role'] != 'context')
            if len(text) > 3000:
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason='length')], usage=None)
            accepted.append(text)
            return reply('확인한 근거 ' + str(len(accepted)))
        messages = [{'role': 'user', 'content': '가' * 8000 + '나' * 8000}]
        records = s.extract_task_memories(Store(create), messages)
        self.assertEqual(''.join(accepted), messages[0]['content'])
        self.assertGreater(len(records), 1)

    def test_irrelevant_windows_do_not_force_artificial_memories(self):
        calls = []
        def create(**kwargs):
            calls.append(request_events(kwargs))
            return reply('요청에 대한 유의미한 결과') if len(calls) == 1 else reply()
        records = s.extract_task_memories(Store(create), [
            {'role': 'user', 'content': '문서 내용을 보완해줘'},
            {'role': 'tool', 'content': '관련 없는 도구 지침 ' * 4000}])
        self.assertGreater(len(calls), 1)
        self.assertEqual(len(records), 1)

    def test_all_empty_windows_cannot_advance_a_task_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            progress = Path(tmp) / 'progress.json'
            with self.assertRaises(ValueError):
                s.extract_task_memories(Store(lambda **kwargs: reply()), [
                    {'role': 'user', 'content': '유의미한 요청 ' * 4000}], progress_path=progress)
            self.assertFalse(progress.exists(), 'Retry from original evidence after an all-empty extraction')

    def test_multiple_memories_retry_after_commit_and_keep_source_links(self):
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return reply('라우팅 표를 수정했다', '장비 검증은 수행하지 않았다')
        store = Store(create)
        store.lose_ack = True
        session = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'session.jsonl'
            events = [
                {'type': 'session_meta', 'payload': {'id': session}},
                {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'route.md 표를 보완해줘'}},
                {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                    'message': '표를 수정했고 장비는 미검증이다'}},
            ]
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            job = root / 'request.job.json'
            s.write_json(job, {'session': session, 'path': str(path), 'end': path.stat().st_size})
            with self.assertRaises(ConnectionError):
                s.process_job(job, lambda: store, state_dir=root)
            self.assertEqual(len(store.rows), 2)
            store.lose_ack = False
            s.process_job(job, lambda: store, state_dir=root)
            self.assertEqual(len(store.inserts), 2)
            self.assertEqual(len(calls), 1)
            metadata = list(store.metadata.values())
            self.assertEqual(len({item['codex_batch_id'] for item in metadata}), 1)
            self.assertEqual([item['codex_chunk_index'] for item in metadata], [0, 1])
            self.assertEqual({item['codex_chunk_count'] for item in metadata}, {2})
            self.assertEqual({item['codex_transcript_path'] for item in metadata}, {str(path)})
            self.assertTrue(all(item['codex_first_offset'] > 0 for item in metadata))
            cursor = json.loads((root / f'{session}.cursor.json').read_text())
            self.assertEqual(cursor['last_task']['request'], 'route.md 표를 보완해줘')


if __name__ == '__main__':
    unittest.main()
