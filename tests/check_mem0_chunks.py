"""Run with ~/.codex/toolkit/venv/bin/python tests/check_mem0_chunks.py (no network)."""
from _paths import ROOT
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid
import httpx
from openai import BadRequestError

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
    def test_context_overflow_splits_and_preserves_completed_windows(self):
        accepted = []
        def create(**kwargs):
            events = request_events(kwargs)
            text = ''.join(event['content'] for event in events if event['role'] != 'context')
            if len(text) > 3000:
                response = httpx.Response(400, request=httpx.Request('POST', 'http://model'))
                raise BadRequestError('Error code: 400', response=response, body=
                    'The number of tokens to keep from the initial prompt is greater than the context length. Try to load the model with a larger context length, or provide a shorter input')
            accepted.append(text)
            return reply('확인한 근거 ' + str(len(accepted)))
        messages = [{'role': 'user', 'content': '가' * 16000}]
        with tempfile.TemporaryDirectory() as tmp:
            progress = Path(tmp) / 'summary.json'
            records = s.extract_task_memories(Store(create), messages, progress_path=progress)
            self.assertEqual(''.join(accepted), messages[0]['content'])
            self.assertEqual(json.loads(progress.read_text())['consumed'], 16000)
            self.assertGreater(len(records), 1)

    def test_unrelated_bad_request_is_not_retried_as_context_overflow(self):
        response = httpx.Response(400, request=httpx.Request('POST', 'http://model'))
        error = BadRequestError('Error code: 400', response=response, body={'error': 'invalid schema'})
        def create(**kwargs):
            raise error
        with self.assertRaises(BadRequestError):
            s.extract_task_memories(Store(create), [{'role': 'user', 'content': '가' * 16000}])

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


class TranscriptRewrites(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'sessions/transcript.jsonl'
        self.path.parent.mkdir()
        self.session = str(uuid.uuid4())
        self.events = [
            {'type': 'session_meta', 'payload': {'id': self.session, 'note': 'x' * 2000}},
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '첫 요청'}},
            {'type': 'response_item', 'payload': {'type': 'function_call_output',
                'call_id': 'read-1', 'output': '확인한 근거'}},
            {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                'message': '첫 결과'}},
        ]
        self.write()
        self.store = Store(lambda **kwargs: reply('첫 기록'))
        self.job = self.queue(self.path.stat().st_size)
        self.run_job(self.job)
        self.cursor_path = self.root / f'{self.session}.cursor.json'

    def write(self, compact=False):
        separators = (',', ':') if compact else None
        self.path.write_text(''.join(json.dumps(event, ensure_ascii=False, sort_keys=compact,
            separators=separators) + '\n' for event in self.events))

    def queue(self, end):
        job = self.root / f'{self.session}-{end:020d}.job.json'
        if end == self.path.stat().st_size:
            with patch.object(s, 'STATE', self.root), patch.object(s, 'HOME', self.root), \
                    patch.object(s.subprocess, 'Popen'):
                s.enqueue({'hook_event_name': 'Stop', 'session_id': self.session,
                           'transcript_path': str(self.path)})
        else:
            s.write_json(job, {'session': self.session, 'path': str(self.path), 'end': end})
        return job

    def run_job(self, job):
        return s.process_job(job, lambda: self.store, state_dir=self.root)

    def append_turn(self):
        self.events.extend([
            {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': '다음 요청'}},
            {'type': 'event_msg', 'payload': {'type': 'agent_message', 'phase': 'final_answer',
                'message': '다음 결과'}},
        ])

    def test_reserialization_relocates_cursor_and_refreshes_all_stale_jobs(self):
        old_end = self.path.stat().st_size
        first, second = self.queue(old_end + 100), self.queue(old_end + 300)
        other = self.root / f'{uuid.uuid4()}-00000000000000000001.job.json'
        other.write_text('{}')
        self.events[0]['payload']['note'] = 'new metadata'
        self.append_turn()
        self.write(compact=True)
        self.assertLess(self.path.stat().st_size, old_end)
        self.run_job(first)
        self.assertEqual(len(self.store.inserts), 2)
        self.assertEqual(json.loads(self.cursor_path.read_text())['last_task']['request'], '다음 요청')
        self.assertEqual(json.loads(second.read_text())['end'], self.path.stat().st_size)
        self.run_job(second)
        self.assertEqual(len(self.store.inserts), 2)
        self.assertTrue(other.exists())

    def test_expanded_serialization_keeps_the_first_unstored_turn(self):
        self.events[0]['payload']['note'] = 'metadata expanded' * 400
        self.append_turn()
        self.write()
        self.run_job(self.queue(self.path.stat().st_size))
        self.assertEqual(len(self.store.inserts), 2)
        self.assertEqual(json.loads(self.cursor_path.read_text())['last_task']['request'], '다음 요청')

    def test_structured_tool_evidence_survives_json_object_key_reordering(self):
        self.events[2]['payload']['output'] = {'z': {'second': 2, 'first': 1}, 'a': 0}
        self.write()
        self.cursor_path.unlink()
        self.store = Store(lambda **kwargs: reply('구조화된 근거'))
        self.run_job(self.queue(self.path.stat().st_size))
        self.append_turn()
        self.write(compact=True)
        self.run_job(self.queue(self.path.stat().st_size))
        self.assertEqual(len(self.store.inserts), 2)
        self.assertEqual(json.loads(self.cursor_path.read_text())['last_task']['request'], '다음 요청')

    def test_legacy_extraction_cache_is_backed_up_before_canonical_replay(self):
        progress = self.root / 'request.summary.json'
        saved = {'version': 2, 'source_hash': 'legacy-order', 'consumed': 6000,
                 'chunk_size': 6000, 'facts': [{'text': '이전 순서의 캐시'}]}
        s.write_json(progress, saved)
        source = '검증할 원본 ' * 2000
        records = s.extract_task_memories(self.store, [{'role': 'user', 'content': source}],
                                          progress_path=progress)
        backups = list(self.root.glob('request.summary.json*.v2-backup'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), saved)
        current = json.loads(progress.read_text())
        self.assertEqual(current['consumed'], len(source))
        self.assertEqual(current['version'], 3)
        self.assertNotIn('이전 순서의 캐시', [record['text'] for record in records])

    def test_suffix_only_expansion_does_not_drop_an_unstored_turn(self):
        self.append_turn()
        self.write()
        job = self.queue(self.path.stat().st_size)
        self.events[-2]['timestamp'] = 'x' * 1000
        self.write()
        self.run_job(job)
        self.assertFalse(job.exists())
        self.assertEqual(len(self.store.inserts), 2)
        self.assertEqual(json.loads(self.cursor_path.read_text())['last_task']['request'], '다음 요청')

    def test_shortened_rewrite_ignores_a_partial_tail_after_complete_new_turns(self):
        self.events[0]['payload']['note'] = ''
        self.append_turn()
        self.write(compact=True)
        job = self.queue(self.path.stat().st_size)
        with self.path.open('ab') as out:
            out.write(b'{"type":')
        self.run_job(job)
        self.assertEqual(len(self.store.inserts), 2)
        self.assertFalse(job.exists())
        self.assertEqual(json.loads(self.cursor_path.read_text())['last_task']['request'], '다음 요청')

    def test_suffix_only_shrink_does_not_leave_an_oversized_job_retrying_at_eof(self):
        self.append_turn()
        self.events[-2]['timestamp'] = 'x' * 1000
        self.write()
        job = self.queue(self.path.stat().st_size)
        self.events[-2].pop('timestamp')
        self.write()
        self.run_job(job)
        self.assertFalse(job.exists())
        self.assertEqual(len(self.store.inserts), 2)

    def test_first_batch_rewrite_after_commit_reuses_pending_facts_without_a_cursor(self):
        self.cursor_path.unlink()
        self.store = Store(lambda **kwargs: reply('첫 기록', '둘째 기록'))
        self.store.lose_ack = True
        job = self.queue(self.path.stat().st_size)
        with self.assertRaises(ConnectionError):
            self.run_job(job)
        self.events[1]['timestamp'] = 'x' * 1000
        self.write()
        self.store.lose_ack = False
        self.run_job(job)
        self.assertEqual(len(self.store.inserts), 2)
        self.assertFalse(job.exists())
        self.assertFalse(list(self.root.glob('*.pending.json')))

    def test_changed_or_deleted_evidence_keeps_checkpoint_and_job(self):
        old_cursor = self.cursor_path.read_bytes()
        for change in ('edit', 'delete'):
            with self.subTest(change=change):
                original = json.loads(json.dumps(self.events))
                if change == 'edit':
                    self.events[2]['payload']['output'] = '달라진 근거'
                else:
                    self.events.pop(2)
                self.append_turn()
                self.write(compact=True)
                job = self.queue(self.path.stat().st_size)
                with self.assertRaises(ValueError):
                    self.run_job(job)
                self.assertTrue(job.exists())
                self.assertEqual(self.cursor_path.read_bytes(), old_cursor)
                self.assertEqual(len(self.store.inserts), 1)
                job.unlink()
                self.events = original

    def test_different_session_is_rejected_even_for_an_already_consumed_job(self):
        self.events[0]['payload']['id'] = str(uuid.uuid4())
        self.write()
        job = self.queue(1)
        with self.assertRaises(ValueError):
            self.run_job(job)
        self.assertTrue(job.exists())

    def test_legacy_prefix_mismatch_still_requires_verified_manual_recovery(self):
        cursor = json.loads(self.cursor_path.read_text())
        for key in ('evidence_count', 'evidence_sha256'):
            cursor.pop(key, None)
        s.write_json(self.cursor_path, cursor)
        self.events[0]['payload']['note'] = 'changed'
        self.append_turn()
        self.write(compact=True)
        with self.assertRaises(ValueError):
            self.run_job(self.queue(self.path.stat().st_size))
        self.assertEqual(len(self.store.inserts), 1)

    def test_rewrite_after_oracle_commit_reuses_pending_facts_and_insert_identity(self):
        self.append_turn()
        self.write()
        job = self.queue(self.path.stat().st_size)
        self.store.lose_ack = True
        with self.assertRaises(ConnectionError):
            self.run_job(job)
        self.events[0]['payload']['note'] = 'new metadata'
        self.write(compact=True)
        self.store.lose_ack = False
        self.run_job(job)
        self.assertEqual(len(self.store.inserts), 2)
        self.assertFalse(list(self.root.glob('*.pending.json')))
        self.assertEqual(json.loads(self.cursor_path.read_text())['last_task']['request'], '다음 요청')


if __name__ == '__main__':
    unittest.main()
