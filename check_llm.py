"""Runnable URL-only setup and real OpenAI client failover checks; no Oracle."""
import contextlib
import io
import json
import subprocess
import tempfile
import threading
import time
from openai import APIConnectionError, APIStatusError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import configure
import mem0_mcp
from mem0.llms.lmstudio import LMStudioLLM
from mem0.embeddings.openai import OpenAIEmbedding
from mem0.configs.embeddings.base import BaseEmbedderConfig

# Missing secondary prompts or writes outside the URL fields must fail this check.
with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / 'mem0.json'
    original = {'username': 'keep', 'password': 'keep', 'custom': {'keep': True},
                'inference_api_url': 'http://old/v1/chat/completions'}
    config.write_text(json.dumps(original))
    urls = ['http://primary/v1/chat/completions', 'http://secondary/v1/chat/completions',
            'http://primary/v1/embeddings', 'http://secondary/v1/embeddings']
    prompts = []
    def validate(prompt, *args, **kwargs):
        prompts.append(prompt)
        return urls[len(prompts) - 1]
    with patch.object(configure, 'MEM0_CONFIG', config), \
            patch.object(configure, 'mem0_api_url', side_effect=validate):
        # Execute the real URL-only command, preserving Oracle and other settings.
        with patch('sys.argv', ['configure.py', 'llm-settings']):
            configure.main()
    saved = json.loads(config.read_text())
    assert len(prompts) == 4
    assert saved == {**original, 'inference_api_url': urls[0],
        'inference_api_url_secondary': urls[1], 'embedding_api_url': urls[2],
        'embedding_api_url_secondary': urls[3]}
    assert config.stat().st_mode & 0o777 == 0o600
    before = config.read_bytes()
    with patch.object(configure, 'MEM0_CONFIG', config), \
            patch.object(configure, 'mem0_api_url', side_effect=[urls[0], ValueError('invalid')]):
        try:
            with patch('sys.argv', ['configure.py', 'llm-settings']):
                configure.main()
        except ValueError:
            pass
        else:
            raise AssertionError('Partial validation should fail')
    assert config.read_bytes() == before

# --llm must exit before any install/reset path, including normal preflight.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = Path(__file__).resolve().parent
    (root / 'setup.sh').write_text((source / 'setup.sh').read_text())
    (root / 'toolkit.sh').write_text('configure_llm() { echo URL_ONLY; }\n'
        'usage() { :; }\ndie() { exit 1; }\npreflight() { exit 99; }\n')
    result = subprocess.run(['bash', str(root / 'setup.sh'), '--llm'], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == 'URL_ONLY', result

hits = []
primary_down = False
models_down = False
secondary_down = False
primary_slow = False
primary_status = 200
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.server.label == 'primary' and models_down:
            self.connection.close()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'models': [
            {'type': 'llm', 'loaded_instances': [{'id': self.server.label + '-chat'}]},
            {'type': 'embedding', 'loaded_instances': [{'id': self.server.label + '-embed'}]},
        ]}).encode())
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        hits.append((self.server.label, self.path, body['model']))
        if self.server.label == 'primary' and primary_slow:
            time.sleep(0.15)
        if self.server.label == 'primary' and primary_status != 200:
            self.send_error(primary_status)
            return
        if ((self.server.label == 'primary' and primary_down)
                or (self.server.label == 'secondary' and secondary_down)):
            self.connection.close()
            return
        if self.path.endswith('/embeddings'):
            assert body['model'] == self.server.label + '-embed'
            response = {'data': [{'embedding': [0.1] * 1024, 'index': 0}], 'model': body['model']}
        else:
            assert body['model'] == self.server.label + '-chat'
            response = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop', 'index': 0}]}
        self.send_response(200)
        self.end_headers()
        try:
            self.wfile.write(json.dumps(response).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
    def log_message(self, *args):
        pass
servers = []
for label in ('primary', 'secondary'):
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.label = label
    threading.Thread(target=server.serve_forever, daemon=True).start()
    servers.append(server)
try:
    bases = [f'http://127.0.0.1:{s.server_port}/v1' for s in servers]
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / 'mem0.json'
        data = {'wallet_dir': tmp, 'username': 'test', 'password': 'test', 'tns_alias': 'test',
                'inference_api_url': bases[0] + '/chat/completions',
                'inference_api_url_secondary': bases[1] + '/chat/completions',
                'embedding_api_url': bases[0] + '/embeddings',
                'embedding_api_url_secondary': bases[1] + '/embeddings'}
        config.write_text(json.dumps(data))
        def create_store(value):
            return SimpleNamespace(llm=LMStudioLLM(value['llm']['config']),
                embedding_model=OpenAIEmbedding(BaseEmbedderConfig(**value['embedder']['config'])))
        with patch.object(mem0_mcp, 'CONFIG', config), \
                patch.object(mem0_mcp.Memory, 'from_config', side_effect=create_store):
            store = mem0_mcp.memory()
            def request(timeout=1):
                assert store.llm.client.with_options(timeout=timeout, max_retries=0).chat.completions.create(
                    model=store.llm.config.model, messages=[{'role': 'user', 'content': 'ping'}]).choices[0].message.content == 'ok'
                store.embedding_model.client = store.embedding_model.client.with_options(timeout=timeout)
                assert len(store.embedding_model.embed('ping')) == 1024
            request()
            assert [h[0] for h in hits] == ['primary', 'primary']
            primary_down = True
            hits.clear()
            request()
            assert [h[0] for h in hits] == ['primary', 'secondary', 'primary', 'secondary']
            primary_down = False
            hits.clear()
            request()
            assert [h[0] for h in hits] == ['primary', 'primary']
            primary_slow = True
            hits.clear()
            request(timeout=0.05)
            assert [h[0] for h in hits] == ['primary', 'secondary', 'primary', 'secondary']
            primary_slow = False
            primary_status = 401
            hits.clear()
            try:
                request()
            except APIStatusError as error:
                assert error.status_code == 401
            else:
                raise AssertionError('HTTP errors must not fail over')
            assert [h[0] for h in hits] == ['primary']
            primary_status = 200
            primary_down = secondary_down = True
            hits.clear()
            try:
                request()
            except APIConnectionError:
                pass
            else:
                raise AssertionError('Both endpoints down must report failure')
            assert [h[0] for h in hits] == ['primary', 'secondary']
            primary_down = secondary_down = False
            store.llm.client.close()
            store.embedding_model.client.close()
            models_down = True
            store = mem0_mcp.memory()
            hits.clear()
            request()
            assert [h[0] for h in hits] == ['secondary', 'secondary']
            store.llm.client.close()
            store.embedding_model.client.close()
            models_down = False
            # Exercise all four real validation POSTs, including secondary re-entry.
            original = config.read_bytes()
            with patch.object(configure, 'MEM0_CONFIG', config), \
                    patch('builtins.input', side_effect=[data['inference_api_url'],
                        'invalid-url', data['inference_api_url_secondary'],
                        data['embedding_api_url'], 'invalid-url', data['embedding_api_url_secondary']]), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                configure.llm_settings()
            assert output.getvalue().count('해당 URL은 동작하지 않습니다.') == 2
            assert json.loads(config.read_text()) == json.loads(original)
finally:
    for server in servers:
        server.shutdown()
        server.server_close()
print('URL-only settings, setup isolation and request failover checks passed')
