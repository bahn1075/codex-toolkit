"""Native frontier inference checks with local servers; no paid APIs or Oracle."""
from _paths import ROOT
import contextlib
import io
import json
import ssl
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
primary_status = 200
finish = 'STOP'
fail_once = False
exaone_bad_response = False
exaone_status = 200

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
        self.send_response(exaone_status if self.path.startswith('/exaone') and exaone_status != 200
                           else primary_status if self.path.startswith(('/gemini', '/groq')) and primary_status != 200
                           else status)
        if status == 302:
            self.send_header('Location', base + '/groq/v1/chat/completions')
        self.end_headers()
        if self.path.startswith('/bge'):
            assert body['model'] == 'bge-m3'
            response = {'data': [{'embedding': [0.1] * 1024, 'index': 0}]}
        elif self.path.startswith(('/openai', '/groq', '/cloudflare', '/exaone')):
            expected = {'openai': 'openai-secret', 'groq': 'groq-secret', 'cloudflare': 'cloudflare-secret',
                        'exaone': 'exaone-secret'}
            assert self.headers['Authorization'] == 'Bearer ' + expected[self.path.split('/')[1]]
            properties = body.get('response_format', {}).get('json_schema', {}).get('schema', {}).get('properties', {})
            content = {'tasks': [{'problem': '추론 공급자 변경 요청', 'actions': '설정 경로를 수정함',
                                  'result': '로컬 검사를 통과함'}]} if 'tasks' in properties else {'memory': []}
            if 'ok' in properties:
                content = {'ok': True}
            response = {'choices': [{'index': 0, 'message': {'role': 'assistant',
                'content': 'not JSON' if self.path.startswith('/exaone') and exaone_bad_response
                else json.dumps(content)}, 'finish_reason': 'stop'}]}
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
    # EXAONE setup and runtime must use its actual model, structured output, and supported reasoning setting.
    with patch('builtins.input', side_effect=['exaone', '', base + '/exaone/v1', 'exaone-secret']), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        exaone = configure.frontier_inference({}, 'primary')
    assert exaone['model'] == 'k-exaone_v2.5'
    assert hits[-1][0] == '/exaone/v1/chat/completions'
    assert hits[-1][2]['reasoning_effort'] == 'low'
    assert hits[-1][2]['response_format']['type'] == 'json_schema'
    assert 'exaone-secret' not in output.getvalue()
    with OpenAI(api_key='unused', base_url=base, max_retries=0,
                http_client=DefaultHttpxClient(transport=mem0_mcp.ModelFailoverTransport(exaone, None, 'llm'))) as client:
        result = client.chat.completions.create(model='placeholder',
            messages=[{'role': 'user', 'content': 'hello'}], reasoning_effort='none',
            response_format={'type': 'json_schema', 'json_schema': {'name': 'facts', 'schema': schema}})
    assert result.choices[0].finish_reason == 'stop'
    assert hits[-1][2]['model'] == 'k-exaone_v2.5' and hits[-1][2]['reasoning_effort'] == 'low'

    # A provider that returns non-JSON must not pass setup as if Mem0 could use it.
    exaone_bad_response = True
    with patch('builtins.input', side_effect=['exaone', '', base + '/exaone/v1', 'exaone-secret', EOFError()]), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            configure.frontier_inference({}, 'primary')
        except EOFError:
            pass
        else:
            raise AssertionError('Non-JSON output must not validate')
    exaone_bad_response = False

    # An OS-trusted TLS chain rejected by Python can be checked by curl without exposing the key.
    with patch('builtins.input', side_effect=['groq', 'openai/gpt-oss-20b',
            base + '/groq/v1', 'groq-secret']), \
            patch.object(configure.urllib.request.OpenerDirector, 'open',
                         side_effect=configure.urllib.error.URLError(
                             ssl.SSLCertVerificationError('CA constraints rejected'))), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        connection = configure.frontier_inference({}, 'primary')
    assert connection['provider'] == 'groq'
    assert hits[-1][0] == '/groq/v1/chat/completions'
    assert hits[-1][1]['Authorization'] == 'Bearer groq-secret'
    assert hits[-1][2]['model'] == 'openai/gpt-oss-20b'
    assert 'groq-secret' not in output.getvalue()

    # curl must neither follow redirects nor turn HTTP errors into successful validation.
    for failure_status in (302, 403):
        status = failure_status
        before = len(hits)
        with patch('builtins.input', side_effect=['groq', 'openai/gpt-oss-20b',
                base + '/groq/v1', 'groq-secret', EOFError('cancel')]), \
                patch.object(configure.urllib.request.OpenerDirector, 'open',
                             side_effect=configure.urllib.error.URLError(
                                 ssl.SSLCertVerificationError('CA constraints rejected'))), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                configure.frontier_inference({}, 'primary')
            except EOFError:
                pass
            else:
                raise AssertionError('Redirect or rejected authentication must not validate')
        assert len(hits) == before + 1
        assert f'HTTP {failure_status}' in output.getvalue()
        assert 'groq-secret' not in output.getvalue()
    status = 200

    # Both Python and TLS-fallback curl checks preserve rate-limited inference inputs.
    status = 429
    for fallback in (False, True):
        with contextlib.ExitStack() as stack:
            if fallback:
                stack.enter_context(patch.object(configure.urllib.request.OpenerDirector, 'open',
                    side_effect=configure.urllib.error.URLError(ssl.SSLCertVerificationError('CA constraints rejected'))))
            stack.enter_context(patch('builtins.input', side_effect=[
                'groq', 'custom-model', base + '/groq/v1', 'groq-secret']))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            selected = configure.frontier_inference({}, 'secondary')
        assert selected == {'provider': 'groq', 'model': 'custom-model',
                            'api_url': base + '/groq/v1', 'api_key': 'groq-secret'}
        assert '429' in output.getvalue() and 'groq-secret' not in output.getvalue()
    status = 200

    # OpenAI-compatible providers must validate, preserve inputs and route actual requests.
    for provider, secret, model in (('groq', 'groq-secret', 'openai/gpt-oss-20b'),
                                    ('cloudflare', 'cloudflare-secret', '@cf/openai/gpt-oss-20b')):
        answers = [provider]
        if provider == 'cloudflare':
            answers.append('a' * 32)
        answers += [model, base + '/' + provider + '/v1', secret]
        with patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
            connection = configure.frontier_inference({}, 'primary')
        assert connection == {'provider': provider, 'api_url': base + '/' + provider + '/v1',
                              'model': model, 'api_key': secret}
        assert hits[-1][0] == '/' + provider + '/v1/chat/completions'
        assert hits[-1][2]['model'] == model
        assert hits[-1][1]['User-Agent'] == 'codex-toolkit/1.0'
        transport = mem0_mcp.ModelFailoverTransport(connection, None, 'llm')
        with OpenAI(api_key='unused', base_url=base, max_retries=0,
                    http_client=DefaultHttpxClient(transport=transport)) as client:
            result = client.chat.completions.create(model='placeholder',
                messages=[{'role': 'user', 'content': 'hello'}], reasoning_effort='none')
        assert result.choices[0].finish_reason == 'stop'
        assert 'reasoning_effort' not in hits[-1][2]

    # Provider defaults, invalid Cloudflare IDs, and existing values are handled safely.
    response = json.dumps({'choices': [{'message': {'content': '{"ok": true}'},
                                         'finish_reason': 'stop'}]}).encode()
    for provider, answers, expected in (
        ('groq', ['groq', 'openai/gpt-oss-20b', '', 'groq-secret'],
         'https://api.groq.com/openai/v1/chat/completions'),
        ('cloudflare', ['cloudflare', 'bad-id', 'cloudflare', 'a' * 32,
                        '@cf/openai/gpt-oss-20b', '', 'cloudflare-secret'],
         'https://api.cloudflare.com/client/v4/accounts/' + 'a' * 32 + '/ai/v1/chat/completions')):
        with patch('builtins.input', side_effect=answers), \
                patch.object(configure.urllib.request.OpenerDirector, 'open',
                             side_effect=lambda *a, **kw: io.BytesIO(response)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            selected = configure.frontier_inference({}, 'primary')
        assert selected['api_url'] == expected
        assert selected['api_key'] not in output.getvalue()
        if provider == 'cloudflare':
            with patch('builtins.input', side_effect=['', '', '', '', '']), \
                    patch.object(configure.urllib.request.OpenerDirector, 'open',
                                 side_effect=lambda *a, **kw: io.BytesIO(response)), \
                    contextlib.redirect_stdout(io.StringIO()):
                assert configure.frontier_inference(selected, 'secondary') == selected

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
        for code in (429, 500, 502, 503, 504):
            primary_status = code
            before = len(hits)
            assert complete().choices[0].finish_reason == 'stop'
            assert len(hits) == before + 2
            assert hits[-2][0].startswith('/gemini') and hits[-1][0].startswith('/anthropic')
            primary_status = 200
            assert complete().choices[0].finish_reason == 'stop'
            assert hits[-1][0].startswith('/gemini')
        # Secondary errors propagate after exactly two HTTP calls.
        primary_status, status = 503, 502
        before = len(hits)
        try:
            complete()
        except APIStatusError as error:
            assert error.status_code == 502
        else:
            raise AssertionError('Secondary error must propagate')
        assert len(hits) == before + 2
        primary_status, status = 200, 200
        for status in (400, 401, 403, 404):
            before = len(hits)
            try:
                complete()
            except APIStatusError as error:
                assert error.status_code == status
            else:
                raise AssertionError('Input/authentication errors must not trigger failover')
            assert len(hits) == before + 1
        status = 200

    openai_connection = {'provider': 'openai', 'api_url': base + '/openai/v1/chat/completions',
                         'model': 'openai-test', 'api_key': 'openai-secret'}
    # Both Mem0 extraction paths must disable thinking on the OpenAI-compatible wire.
    with OpenAI(api_key='openai-secret', base_url=base + '/openai/v1', max_retries=0) as client:
        store = SimpleNamespace(llm=SimpleNamespace(client=client, config=SimpleNamespace(
            model='gemma4-test', lmstudio_response_format={
                'type': 'json_schema', 'json_schema': {'name': 'facts', 'schema': schema}})))
        assert mem0_mcp.extract_facts(store, []) == []
        assert hits[-1][2].get('reasoning_effort') == 'none', 'Fact extraction must disable thinking'
        assert mem0_mcp.extract_task_records(store, []) == [
            {'text': '추론 공급자 변경 요청\n설정 경로를 수정함\n로컬 검사를 통과함'}]
        assert hits[-1][2].get('reasoning_effort') == 'none', 'Task extraction must disable thinking'

    with OpenAI(api_key='unused', base_url=base + '/unused', max_retries=0,
                http_client=DefaultHttpxClient(transport=mem0_mcp.ModelFailoverTransport(
                    openai_connection, None, 'llm'))) as client:
        assert client.chat.completions.create(model='placeholder', messages=[
            {'role': 'user', 'content': 'hello'}]).choices[0].finish_reason == 'stop'
        assert hits[-1][2]['model'] == 'openai-test'
        status = 503
        before = len(hits)
        try:
            client.chat.completions.create(model='placeholder', messages=[{'role':'user','content':'hello'}])
        except APIStatusError as error:
            assert error.status_code == 503
        else:
            raise AssertionError('Without secondary, primary error must propagate')
        assert len(hits) == before + 1
        status = 200

    # HTTP status failover applies only to inference, even with a secondary embedding URL.
    with OpenAI(api_key='unused',base_url=base,max_retries=0,
                http_client=DefaultHttpxClient(transport=mem0_mcp.ModelFailoverTransport(
                    base+'/bge/v1/embeddings',base+'/bge/v1/embeddings','embedding'))) as client:
        status = 503
        before = len(hits)
        try:
            client.embeddings.create(model='placeholder',input='ping')
        except APIStatusError as error:
            assert error.status_code == 503
        else:
            raise AssertionError('Embedding HTTP errors must propagate')
        assert len(hits) == before + 1
        status = 200

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
                   'local', 'local']
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
                assert 'reasoning_effort' not in hits[-1][2]
                assert 'thinkingConfig' not in hits[-1][2]['generationConfig']
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
                assert 'reasoning_effort' not in hits[-1][2]
                assert 'thinkingConfig' not in hits[-1][2]['generationConfig']
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

        # Both providers persist independently and Mem0 fails over without sharing keys.
        answers = ['frontier', 'groq', 'openai/gpt-oss-20b', base + '/groq/v1', 'groq-secret',
                   'frontier', 'cloudflare', 'a' * 32, '@cf/openai/gpt-oss-20b',
                   base + '/cloudflare/v1', 'cloudflare-secret', 'local', 'local']
        with patch.object(configure, 'MEM0_CONFIG', target), \
                patch('builtins.input', side_effect=answers), \
                patch.object(configure, 'mem0_api_url', side_effect=lambda p, e, pl, existing, **kw: existing), \
                contextlib.redirect_stdout(io.StringIO()):
            configure.llm_settings()
        new_data = json.loads(target.read_text())
        assert (new_data['inference_provider'], new_data['inference_provider_secondary']) == ('groq', 'cloudflare')
        assert (new_data['inference_api_key'], new_data['inference_api_key_secondary']) == ('groq-secret', 'cloudflare-secret')
        assert target.stat().st_mode & 0o777 == 0o600
        with patch.object(mem0_mcp, 'CONFIG', target), \
                patch.object(mem0_mcp.Memory, 'from_config', side_effect=create_store):
            store = mem0_mcp.memory()
            try:
                primary_status = 503
                before = len(hits)
                assert mem0_mcp.extract_facts(store, []) == []
                assert [hit[0] for hit in hits[before:]] == ['/groq/v1/chat/completions', '/cloudflare/v1/chat/completions']
                assert hits[-2][1]['Authorization'] == 'Bearer groq-secret'
                assert hits[-1][1]['Authorization'] == 'Bearer cloudflare-secret'
            finally:
                primary_status = 200
                store.llm.client.close()
                store.embedding_model.client.close()

        # EXAONE settings are persisted and can fail over to another provider without sharing keys.
        answers = ['frontier', 'exaone', '', base + '/exaone/v1', 'exaone-secret',
                   'frontier', 'groq', 'openai/gpt-oss-20b', base + '/groq/v1', 'groq-secret',
                   'local', 'local']
        with patch.object(configure, 'MEM0_CONFIG', target), \
                patch('builtins.input', side_effect=answers), \
                patch.object(configure, 'mem0_api_url', side_effect=lambda p, e, pl, existing, **kw: existing), \
                contextlib.redirect_stdout(io.StringIO()):
            configure.llm_settings()
        new_data = json.loads(target.read_text())
        assert (new_data['inference_provider'], new_data['inference_provider_secondary']) == ('exaone', 'groq')
        assert new_data['inference_model'] == 'k-exaone_v2.5'
        assert target.stat().st_mode & 0o777 == 0o600
        with patch.object(mem0_mcp, 'CONFIG', target), \
                patch.object(mem0_mcp.Memory, 'from_config', side_effect=create_store):
            store = mem0_mcp.memory()
            try:
                exaone_status = 503
                before = len(hits)
                assert mem0_mcp.extract_facts(store, []) == []
                assert [hit[0] for hit in hits[before:]] == ['/exaone/v1/chat/completions', '/groq/v1/chat/completions']
                assert hits[-2][1]['Authorization'] == 'Bearer exaone-secret'
                assert hits[-1][1]['Authorization'] == 'Bearer groq-secret'
            finally:
                exaone_status = 200
                store.llm.client.close()
                store.embedding_model.client.close()

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
