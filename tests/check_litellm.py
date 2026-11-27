"""Verify Mem0 setup and runtime against one OpenAI-compatible gateway."""
from _paths import ROOT
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
from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.embeddings.openai import OpenAIEmbedding
from mem0.llms.lmstudio import LMStudioLLM

hits = []
key = 'test-virtual-key'
inference = 'groq/qwen/qwen3.8-27b'
embedding = '@cf/baai/bge-m3'


class Gateway(BaseHTTPRequestHandler):
    dimensions = 1024

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        hits.append((self.path, self.headers.get('Authorization'), body['model']))
        if self.path == '/v1/embeddings':
            if 'dimensions' in body:
                self.send_error(400, 'BGE-M3 does not accept dimensions')
                return
            result = {'data': [{'embedding': [0.1] * self.dimensions, 'index': 0}]}
        else:
            result = {'choices': [{'message': {'role': 'assistant', 'content': 'ok'},
                                   'finish_reason': 'stop', 'index': 0}]}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, *args):
        pass


with ThreadingHTTPServer(('127.0.0.1', 0), Gateway) as server, tempfile.TemporaryDirectory() as tmp:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}/v1'
        path = Path(tmp) / 'mem0.json'
        oracle = {'wallet_dir': tmp, 'username': 'test', 'password': 'test',
                  'tns_alias': 'test', 'custom': {'preserve': True}}
        path.write_text(json.dumps({**oracle, 'inference_api_key_secondary': 'old-key',
                                    'embedding_api_url': 'http://old/v1/embeddings'}))
        expected = [('/v1/chat/completions', f'Bearer {key}', inference),
                    ('/v1/embeddings', f'Bearer {key}', embedding)]
        with patch.object(configure, 'MEM0_CONFIG', path), \
                patch('builtins.input', side_effect=[base]) as url_prompt, \
                patch.object(configure, 'getpass', side_effect=[key]) as key_prompt, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            configure.llm_settings()
        assert url_prompt.call_count == key_prompt.call_count == 1
        assert key not in output.getvalue()
        assert hits == expected
        assert json.loads(path.read_text()) == {**oracle, 'litellm': {'base_url': base, 'api_key': key}}
        assert path.stat().st_mode & 0o777 == 0o600

        def create_store(config):
            return SimpleNamespace(llm=LMStudioLLM(config['llm']['config']),
                embedding_model=OpenAIEmbedding(BaseEmbedderConfig(**config['embedder']['config'])))

        with patch.object(mem0_mcp, 'CONFIG', path), \
                patch.object(mem0_mcp.Memory, 'from_config', side_effect=create_store):
            store = mem0_mcp.memory()
        with store.llm.client, store.embedding_model.client:
            result = store.llm.client.chat.completions.create(model=store.llm.config.model,
                messages=[{'role': 'user', 'content': 'ping'}])
            assert result.choices[0].message.content == 'ok'
            assert len(store.embedding_model.embed('ping')) == 1024
        assert hits == expected * 2

        before = path.read_bytes()
        Gateway.dimensions = 768
        with patch.object(configure, 'MEM0_CONFIG', path), \
                patch('builtins.input', side_effect=['']), \
                patch.object(configure, 'getpass', side_effect=['']), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                configure.llm_settings()
            except ValueError as error:
                assert '1024' in str(error)
            else:
                raise AssertionError('An incompatible embedding must fail validation')
        assert path.read_bytes() == before
    finally:
        server.shutdown()
        thread.join()

print('LiteLLM setup, shared authentication, runtime routing and atomic migration passed')
