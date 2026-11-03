"""Native frontier inference checks with local servers; no paid APIs or Oracle."""
import contextlib
import io
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import configure
import mem0_mcp
from model_api import inference_request
from openai import OpenAI, DefaultHttpxClient, APIStatusError
from mem0.llms.lmstudio import LMStudioLLM
from mem0.embeddings.openai import OpenAIEmbedding
from mem0.configs.embeddings.base import BaseEmbedderConfig

hits = []
down = False
status = 200
finish = 'STOP'
fail_once = False

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global fail_once
        if self.path.startswith('/gemini/v1beta/models/'):
            hits.append((self.path, dict(self.headers), None))
            self.send_response(403 if fail_once else status)
            self.end_headers()
            if fail_once:
                fail_once = False
                response = {'error': {'status': 'PERMISSION_DENIED',
                    'message': 'Key ' + self.headers['x-goog-api-key'] +
                        ' is restricted; check API permissions'}}
            else:
                response = {'name': 'models/' + self.path.rsplit('/', 1)[1],
                            'supportedGenerationMethods': ['generateContent']}
            self.wfile.write(json.dumps(response).encode())
            return
        if self.path.startswith('/local-down'):
            self.connection.close()
            return
        assert self.path == '/bge/api/v1/models'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'models': [{'type': 'embedding',
            'loaded_instances': [{'id': 'bge-m3'}]}]}).encode())

    def do_POST(self):
        global fail_once
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        hits.append((self.path, dict(self.headers), body))
        if fail_once:
            fail_once = False
            self.send_response(403)
            self.end_headers()
            self.wfile.write(json.dumps({'error': {'status': 'PERMISSION_DENIED',
                'message': 'Key ' + self.headers['x-goog-api-key'] +
                    ' is restricted; check API permissions'}}).encode())
            return
        if down and self.path.startswith('/gemini'):
            self.connection.close()
            return
        self.send_response(status)
        self.end_headers()
        if self.path.startswith('/bge'):
            assert body['model'] == 'bge-m3'
            response = {'data': [{'embedding': [0.1] * 1024, 'index': 0}]}
        elif self.path.startswith('/openai'):
            assert self.headers['Authorization'] == 'Bearer openai-secret'
            response = {'choices': [{'index': 0, 'message': {'role': 'assistant',
                'content': '{"memory": []}'}, 'finish_reason': 'stop'}]}
        elif self.path.startswith('/gemini'):
            properties = body['generationConfig'].get('responseJsonSchema', {}).get('properties', {})
            content = {'tasks': [{'problem': '추론 공급자 변경 요청', 'actions': '설정 경로를 수정함',
                                  'result': '로컬 검사를 통과함'}]} if 'tasks' in properties else {'memory': []}
            response = {'candidates': [{'content': {'parts': [{'text': json.dumps(content)}]},
                                        'finishReason': finish}],
                        'usageMetadata': {'candidatesTokenCount': 7}}
        else:
            response = {'content': [{'type': 'text', 'text': '{"memory": []}'}],
                        'stop_reason': 'end_turn', 'usage': {'output_tokens': 7}}
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f'http://127.0.0.1:{server.server_port}'
gemini = {'provider': 'gemini', 'api_url': base + '/gemini/v1beta',
          'model': 'gemini-test', 'api_key': 'gemini-secret'}
anthropic = {'provider': 'anthropic', 'api_url': base + '/anthropic/v1/messages',
             'model': 'claude-test', 'api_key': 'anthropic-secret'}
schema = {'type': 'object', 'properties': {'memory': {'type': 'array', 'items': {'type': 'string'}}},
          'required': ['memory'], 'additionalProperties': False}
try:
    # A failed first attempt must explain the server error and retain unsaved inputs.
    auth_key = 'AQ.' + 'new-authorization-key-' * 16
    fail_once = True
    with patch('builtins.input', side_effect=[
            'gemini', 'gemini-3.8-flash', base + '/gemini/v1beta', auth_key, '', '', '', '']), \
            patch.object(configure, 'getpass', side_effect=AssertionError('API key must use visible input')), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        try:
            retried = configure.frontier_inference({}, 'primary')
        except (StopIteration, EOFError):
            raise AssertionError('Retry discarded the just-entered model, URL or API key') from None
    assert retried['model'] == 'gemini-3.8-flash'
    assert retried['api_key'] == auth_key
    assert {key.lower(): value for key, value in hits[-1][1].items()}['x-goog-api-key'] == auth_key
    assert 'HTTP 403' in output.getvalue()
    assert 'PERMISSION_DENIED' in output.getvalue()
    assert 'check API permissions' in output.getvalue()
    assert auth_key not in output.getvalue()
    assert hits[-1][0] == '/gemini/v1beta/models/gemini-3.8-flash'
    assert hits[-1][2] is None, 'Key validation must not generate an answer'

    # A read timeout retains visible inputs without treating the key as invalid.
    real_open = configure.urllib.request.OpenerDirector.open
    waits = []
    def delayed_open(opener, request, *, timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise TimeoutError('The read operation timed out')
        return real_open(opener, request, timeout=timeout)
    with patch.object(configure.urllib.request.OpenerDirector, 'open', new=delayed_open), \
            patch('builtins.input', side_effect=[
                'gemini', 'gemini-3.8-flash', base + '/gemini/v1beta', auth_key, '', '', '', '']), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        assert configure.frontier_inference({}, 'primary')['api_key'] == auth_key
    assert waits == [10, 10]
    assert '키의 유효성은 아직 확인되지 않았습니다' in output.getvalue()
    assert auth_key not in output.getvalue()
    # Native Anthropic schemas cannot carry our locally enforced length constraints.
    constrained = {**schema, 'properties': {'memory': {'type': 'array', 'minItems': 1,
        'maxItems': 1, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}}}
    _, _, native = inference_request(anthropic, {
        'messages': [{'role': 'user', 'content': 'hello'}],
        'response_format': {'type': 'json_schema', 'json_schema': {'schema': constrained}}})
    converted = native['output_config']['format']['schema']['properties']['memory']
    assert 'maxItems' not in converted
    assert 'maxLength' not in converted['items']
    assert constrained['properties']['memory']['maxItems'] == 1
    transport = mem0_mcp.ModelFailoverTransport(gemini, anthropic, 'llm')
    with OpenAI(api_key='unused', base_url=base + '/unused', max_retries=0,
                http_client=DefaultHttpxClient(transport=transport)) as client:
        def complete():
            return client.chat.completions.create(model='placeholder', max_tokens=1024,
                messages=[{'role': 'system', 'content': 'Extract facts'},
                          {'role': 'user', 'content': 'hello'}],
                response_format={'type': 'json_schema', 'json_schema': {'name': 'facts', 'schema': schema}})
        response = complete()
        assert response.choices[0].message.content == '{"memory": []}'
        assert response.choices[0].finish_reason == 'stop'
        path, headers, body = hits[-1]
        assert path == '/gemini/v1beta/models/gemini-test:generateContent'
        assert headers['x-goog-api-key'] == 'gemini-secret'
        assert 'authorization' not in {k.lower() for k in headers}
        assert body['generationConfig']['responseJsonSchema'] == schema
        assert body['systemInstruction']['parts'][0]['text'] == 'Extract facts'
        assert body['contents'][0]['parts'][0]['text'] == 'hello'
        finish = 'MAX_TOKENS'
        assert complete().choices[0].finish_reason == 'length'
        finish = 'STOP'
        down = True
        response = complete()
        assert response.choices[0].finish_reason == 'stop'
        path, headers, body = hits[-1]
        assert path == '/anthropic/v1/messages'
        assert headers['x-api-key'] == 'anthropic-secret'
        assert 'x-goog-api-key' not in headers
        assert body['output_config']['format']['schema'] == schema
        assert body['system'] == 'Extract facts'
        down = False
        status = 401
        before = len(hits)
        try:
            complete()
        except APIStatusError as error:
            assert error.status_code == 401
        else:
            raise AssertionError('HTTP authentication errors must not trigger failover')
        assert len(hits) == before + 1
        status = 200

    openai_connection = {'provider': 'openai', 'api_url': base + '/openai/v1/chat/completions',
                         'model': 'openai-test', 'api_key': 'openai-secret'}
    with OpenAI(api_key='unused', base_url=base + '/unused', max_retries=0,
                http_client=DefaultHttpxClient(transport=mem0_mcp.ModelFailoverTransport(
                    openai_connection, None, 'llm'))) as client:
        assert client.chat.completions.create(model='placeholder', messages=[
            {'role': 'user', 'content': 'hello'}]).choices[0].finish_reason == 'stop'
        assert hits[-1][2]['model'] == 'openai-test'

    with OpenAI(api_key='unused', base_url=base + '/unused', max_retries=0,
                http_client=DefaultHttpxClient(transport=mem0_mcp.ModelFailoverTransport(
                    base + '/local-down/v1/chat/completions', gemini, 'llm'))) as client:
        assert client.chat.completions.create(model='placeholder', messages=[
            {'role': 'user', 'content': 'hello'}]).choices[0].finish_reason == 'stop'
        assert hits[-1][0].startswith('/gemini')

    # Real frontier validation and atomic persistence; local embeddings remain bge-m3 URLs.
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / 'mem0.json'
        original = {'wallet_password': '', 'password': 'oracle-secret',
                    'username': 'test', 'tns_alias': 'test', 'wallet_dir': tmp,
                    'embedding_api_url': base + '/bge/v1/embeddings',
                    'embedding_api_url_secondary': base + '/bge/v1/embeddings'}
        target.write_text(json.dumps(original))
        answers = ['frontier', 'gemini', 'gemini-test', base + '/gemini/v1beta', 'gemini-secret',
                   'frontier', 'anthropic', 'claude-test', base + '/anthropic/v1/messages', 'anthropic-secret',
                   '', '']
        with patch.object(configure, 'MEM0_CONFIG', target), \
                patch('builtins.input', side_effect=answers), \
                patch.object(configure, 'getpass', side_effect=AssertionError('API key must use visible input')), \
                patch.object(configure, 'mem0_api_url', side_effect=lambda p, e, pl, existing, **kw: existing), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            configure.mem0_settings()
        data = json.loads(target.read_text())
        assert data['inference_provider'] == 'gemini'
        assert data['inference_provider_secondary'] == 'anthropic'
        assert data['inference_api_key'] == 'gemini-secret'
        assert data['embedding_api_url'] == original['embedding_api_url']
        assert data['password'] == original['password']
        assert target.stat().st_mode & 0o777 == 0o600
        assert 'gemini-secret' not in output.getvalue()
        assert 'anthropic-secret' not in output.getvalue()

        # Startup and fact extraction use Gemini; the real embedder still calls bge-m3.
        def create_store(value):
            return SimpleNamespace(llm=LMStudioLLM(value['llm']['config']),
                embedding_model=OpenAIEmbedding(BaseEmbedderConfig(**value['embedder']['config'])))
        with patch.object(mem0_mcp, 'CONFIG', target), \
                patch.object(mem0_mcp.Memory, 'from_config', side_effect=create_store):
            store = mem0_mcp.memory()
            try:
                assert mem0_mcp.extract_facts(store, [{'role': 'user', 'content': 'test'}]) == []
                finish = 'MAX_TOKENS'
                try:
                    mem0_mcp.extract_facts(store, [])
                except mem0_mcp.ExtractionLimitError as error:
                    assert error.details['reason'] == 'length'
                else:
                    raise AssertionError('Truncated native output must not become facts')
                finish = 'SAFETY'
                try:
                    mem0_mcp.extract_facts(store, [])
                except ValueError as error:
                    assert 'did not finish' in str(error)
                else:
                    raise AssertionError('Refused native output must not become facts')
                finish = 'STOP'
                assert mem0_mcp.extract_task_records(store, [{'role': 'user', 'content': 'test'}]) == [
                    {'text': '추론 공급자 변경 요청\n설정 경로를 수정함\n로컬 검사를 통과함'}]
                assert len(store.embedding_model.embed('test')) == 1024
                assert store.embedding_model.config.model == 'bge-m3'
                assert store.llm.config.model == 'gemini-test'
            finally:
                store.llm.client.close()
                store.embedding_model.client.close()

            mixed = {**data, 'inference_api_url': base + '/local-down/v1/chat/completions',
                     'inference_provider': 'local', 'inference_provider_secondary': 'gemini',
                     'inference_api_url_secondary': gemini['api_url'],
                     'inference_model_secondary': gemini['model'],
                     'inference_api_key_secondary': gemini['api_key']}
            target.write_text(json.dumps(mixed))
            store = mem0_mcp.memory()
            try:
                assert store.llm.config.model == 'gemini-test'
                assert mem0_mcp.extract_facts(store, []) == []
            finally:
                store.llm.client.close()
                store.embedding_model.client.close()
                target.write_text(json.dumps(data))

        # Invalid native validation must not partly save provider/key changes.
        before = target.read_bytes()
        status = 401
        with patch.object(configure, 'MEM0_CONFIG', target), \
                patch('builtins.input', side_effect=['', '', '', '', '', EOFError('cancel')]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                configure.llm_settings()
            except EOFError:
                pass
            else:
                raise AssertionError('Failed API validation must ask again')
        assert target.read_bytes() == before
        assert 'gemini-secret' not in output.getvalue()
        status = 200

        # Returning both inference roles to local removes stale frontier credentials.
        with patch.object(configure, 'MEM0_CONFIG', target), \
                patch.object(configure, 'model_choice', return_value='local'), \
                patch.object(configure, 'mem0_api_url', return_value=base + '/local/v1/chat/completions'), \
                contextlib.redirect_stdout(io.StringIO()):
            configure.llm_settings()
        local = json.loads(target.read_text())
        assert not any('api_key' in key or 'provider' in key or 'model' in key for key in local)
finally:
    server.shutdown()
    server.server_close()
    thread.join()
print('Frontier inference checks passed')
