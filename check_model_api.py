"""Run with ~/.codex/toolkit/venv/bin/python check_model_api.py."""
import importlib.util
import json
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

assert importlib.util.find_spec('model_api'), 'Missing shared loaded-model selection'
from model_api import select_model, validate_response

models = [
    {'type': 'llm', 'key': 'old', 'loaded_instances': []},
    {'type': 'llm', 'key': 'new', 'loaded_instances': [{'id': 'served-chat'}]},
    {'type': 'embedding', 'key': 'embed', 'loaded_instances': [{'id': 'served-embed'}]},
]
paths = []
payloads = []
legacy = False

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        payloads.append(payload)
        if self.path.endswith('/embeddings'):
            assert payload['model'] == 'served-embed'
            response = {'data': [{'embedding': [0.1] * 1024}]}
        else:
            assert payload['model'] == 'replacement'
            response = {'choices': [{'message': {'content': 'pong'}}]}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def do_GET(self):
        paths.append(self.path)
        if legacy and self.path.endswith('/api/v1/models'):
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        response = {'data': [
            {'type': 'llm', 'id': 'old', 'state': 'not-loaded'},
            {'type': 'llm', 'id': 'legacy-chat', 'state': 'loaded'},
            {'type': 'embeddings', 'id': 'legacy-embed', 'state': 'loaded'},
        ]} if legacy else {'models': models}
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f'http://127.0.0.1:{server.server_port}/proxy'
try:
    assert select_model(base + '/v1/chat/completions', 'llm') == (base + '/v1', 'served-chat')
    assert select_model(base + '/v1/embeddings', 'embedding') == (base + '/v1', 'served-embed')
    models[1]['loaded_instances'] = [{'id': 'replacement'}]
    assert select_model(base + '/v1/chat/completions', 'llm')[1] == 'replacement'
    # Exercise the real setup POST and runtime config boundary, without Oracle access.
    import configure
    import mem0_mcp
    chat_url = base + '/v1/chat/completions'
    embedding_url = base + '/v1/embeddings'
    with patch('builtins.input', return_value=chat_url):
        assert configure.mem0_api_url('Chat', chat_url, {
            'messages': [{'role': 'user', 'content': 'ping'}], 'max_tokens': 64}) == chat_url
    with patch('builtins.input', return_value=embedding_url):
        assert configure.mem0_api_url('Embedding', embedding_url, {'input': 'ping'},
                                      response_kind='embedding') == embedding_url
    assert [payload['model'] for payload in payloads] == ['replacement', 'served-embed']
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / 'mem0.json'
        config.write_text(json.dumps({'inference_api_url': chat_url,
            'embedding_api_url': embedding_url, 'wallet_dir': tmp,
            'username': 'test', 'password': 'test', 'tns_alias': 'test'}))
        with patch.object(mem0_mcp, 'CONFIG', config), \
                patch.object(mem0_mcp.Memory, 'from_config', side_effect=lambda value: value):
            resolved = mem0_mcp.memory()
            assert resolved['llm']['config']['model'] == 'replacement'
            assert resolved['embedder']['config']['model'] == 'served-embed'
            assert resolved['embedder']['config']['embedding_dims'] == 1024
    models[1]['loaded_instances'] = []
    try:
        select_model(base + '/v1/chat/completions', 'llm')
    except ValueError as error:
        assert 'loaded' in str(error)
    else:
        raise AssertionError('An unloaded model must never be selected')
    legacy = True
    assert select_model(base + '/v1/chat/completions', 'llm')[1] == 'legacy-chat'
    assert select_model(base + '/v1/embeddings', 'embedding')[1] == 'legacy-embed'
    assert all(path.endswith('/models') for path in paths)
finally:
    server.shutdown()
    server.server_close()
    thread.join()

validate_response({'choices': [{'message': {'content': 'pong'}}]}, 'llm')
validate_response({'choices': [{'message': {'content': None, 'reasoning_content': 'pong'}}]}, 'llm')
validate_response({'data': [{'embedding': [0.1] * 1024}]}, 'embedding')
for kind, response in [
    ('llm', {}), ('llm', {'choices': [{'message': {}}]}),
    ('embedding', {'data': [{'embedding': []}]}),
    ('embedding', {'data': [{'embedding': [0.1]}]}),
    ('embedding', {'data': [{'embedding': ['bad'] * 1024}]}),
    ('embedding', {'data': [{'embedding': [float('nan')] * 1024}]}),
]:
    try:
        validate_response(response, kind)
    except ValueError:
        pass
    else:
        raise AssertionError(f'Accepted invalid {kind} response')
print('model API checks passed')
