"""Cloudflare embedding setup and transport checks without external services."""
import contextlib
import io
import json
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import configure
import mem0_mcp
from model_api import embedding_connection, resolve_embedding
from openai import OpenAI, DefaultHttpxClient
from mem0.embeddings.openai import OpenAIEmbedding
from mem0.configs.embeddings.base import BaseEmbedderConfig

hits = []

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'models': [{'type': 'embedding',
            'loaded_instances': [{'id': 'bge-m3'}]}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        hits.append((self.path, self.headers.get('Authorization'), body))
        if self.path == '/down/embeddings':
            self.connection.close()
            return
        cloud = self.path.startswith('/cloud/')
        assert body['model'] == ('@cf/baai/bge-m3' if cloud else 'bge-m3')
        assert self.headers['Authorization'] == ('Bearer test-token' if cloud else 'Bearer local-no-key')
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'data': [{'index': 0, 'embedding': [0.1]*1024}]}).encode())

    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{server.server_port}'
cloud = dict(provider='cloudflare', api_url=base+'/cloud/embeddings', model='@cf/baai/bge-m3', api_key='test-token')
local = dict(provider='local', api_url=base+'/local/embeddings')
try:
    # Rate-limited frontier checks must still save all four user-entered connections.
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / 'mem0.json'
        config.write_text(json.dumps({'unrelated': 'preserved'}))
        answers = ['frontier', 'gemini', 'custom-model', '', 'inference-secret'] * 2
        answers += ['frontier', 'cloudflare', 'a'*32, 'embedding-secret'] * 2
        with patch.object(configure, 'MEM0_CONFIG', config), \
                patch('builtins.input', side_effect=answers), \
                patch.object(configure.urllib.request.OpenerDirector, 'open',
                    side_effect=configure.urllib.error.HTTPError('https://example.com', 429, 'Too Many Requests', {}, None)), \
                patch.object(configure.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 22, '\n429', 'curl: (22) HTTP 429')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            configure.llm_settings()
        saved = json.loads(config.read_text())
        assert saved['unrelated'] == 'preserved'
        for suffix in ('', '_secondary'):
            assert saved['inference_provider'+suffix] == 'gemini'
            assert saved['inference_model'+suffix] == 'custom-model'
            assert saved['inference_api_key'+suffix] == 'inference-secret'
            assert saved['inference_api_url'+suffix] == 'https://generativelanguage.googleapis.com/v1beta'
            assert saved['embedding_provider'+suffix] == 'cloudflare'
            assert saved['embedding_api_key'+suffix] == 'embedding-secret'
            assert saved['embedding_model'+suffix] == '@cf/baai/bge-m3'
            assert saved['embedding_api_url'+suffix] == 'https://api.cloudflare.com/client/v4/accounts/'+'a'*32+'/ai/v1/embeddings'
        assert '429' in output.getvalue()
        assert 'inference-secret' not in output.getvalue() and 'embedding-secret' not in output.getvalue()

    # After a failed Cloudflare check, entering local at the retry prompt must switch providers.
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / 'mem0.json'
        config.write_text(json.dumps({'embedding_provider': 'cloudflare',
            'embedding_api_url': 'https://api.cloudflare.com/client/v4/accounts/' + 'a'*32 + '/ai/v1/embeddings',
            'embedding_model': '@cf/baai/bge-m3', 'embedding_api_key': 'old-secret'}))
        def local_url(prompt, example, payload, existing, **kwargs):
            return base + ('/local/embeddings' if kwargs.get('response_kind') == 'embedding' else '/chat/completions')
        with patch.object(configure, 'MEM0_CONFIG', config), \
                patch('builtins.input', side_effect=['local', 'local', 'frontier', 'cloudflare',
                    'a'*32, 'new-secret', 'local', 'local']), \
                patch.object(configure, 'mem0_api_url', side_effect=local_url), \
                patch.object(configure.subprocess, 'run', return_value=subprocess.CompletedProcess([], 22, '', 'HTTP 403')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            configure.llm_settings()
        saved = json.loads(config.read_text())
        assert saved['embedding_api_url'] == base + '/local/embeddings'
        assert saved['embedding_api_url_secondary'] == base + '/local/embeddings'
        assert 'embedding_api_key' not in saved and 'embedding_provider' not in saved
        assert 'new-secret' not in output.getvalue()

    assert embedding_connection({'embedding_api_url': local['api_url']})['provider'] == 'local'
    assert resolve_embedding(cloud) == (base+'/cloud', '@cf/baai/bge-m3')
    for primary, secondary in ((cloud, local), (local, cloud), ({**cloud, 'api_url':base+'/down/embeddings'}, local),
                               ({**local, 'api_url':base+'/down/embeddings'}, cloud)):
        with OpenAI(api_key='unused', base_url=base, max_retries=0,
                    http_client=DefaultHttpxClient(transport=mem0_mcp.ModelFailoverTransport(primary, secondary, 'embedding'))) as client:
            assert len(client.embeddings.create(model='unused', input='ping').data[0].embedding) == 1024

    # Every type prompt defaults to frontier, even when resuming legacy local settings.
    choices = []
    def choose(prompt, options, default):
        choices.append((prompt, default))
        return 'frontier'
    with patch.object(configure, 'model_choice', side_effect=choose), \
            patch.object(configure, 'frontier_inference', return_value={'provider':'gemini','api_url':'https://example.com'}), \
            patch.object(configure, 'frontier_embedding', return_value=cloud):
        data = configure.mem0_model_settings({'embedding_api_url':local['api_url']})
    assert len(choices) == 4 and all(default=='frontier' for _,default in choices)
    assert data['embedding_provider_secondary'] == 'cloudflare'
    with patch('builtins.input', return_value=''):
        assert configure.model_choice('type', ('local','frontier'), 'frontier') == 'frontier'

    response = subprocess.CompletedProcess([],0,json.dumps({'data':[{'embedding':[0.1]*1024}]})+'\n200','')
    with patch.object(configure.subprocess, 'run', return_value=response) as opened, \
            patch('builtins.input', side_effect=['', 'a'*32, 'test-token']), contextlib.redirect_stdout(io.StringIO()):
        selected = configure.frontier_embedding({}, 'primary')
    command = opened.call_args.args[0]
    assert 'https://api.cloudflare.com/client/v4/accounts/'+'a'*32+'/ai/v1/embeddings' in command
    assert 'Authorization: Bearer test-token' in opened.call_args.kwargs['input']
    assert not any('test-token' in arg for arg in command)
    assert '-k' not in command and '--insecure' not in command
    assert selected['model'] == '@cf/baai/bge-m3'
    assert opened.call_args.kwargs['timeout'] == 15 and command[command.index('--max-time')+1]=='10'

    # Startup must pass provider credentials to the actual Mem0 embedder, including without a secondary.
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp)/'mem0.json'
        config.write_text(json.dumps({'embedding_'+k:v for k,v in cloud.items()} | {
            'inference_provider':'openai','inference_api_url':base+'/chat/completions',
            'inference_model':'test','inference_api_key':'test',
            'wallet_dir':tmp,'username':'test','password':'test','tns_alias':'test'}))
        def create_store(value):
            return SimpleNamespace(llm=None, embedding_model=OpenAIEmbedding(BaseEmbedderConfig(**value['embedder']['config'])))
        # Use a local inference config to avoid wrapping the irrelevant LLM client.
        values=json.loads(config.read_text());values.pop('inference_provider');config.write_text(json.dumps(values))
        with patch.object(mem0_mcp,'CONFIG',config), \
                patch.object(mem0_mcp,'resolve_inference',return_value=(base,'test')), \
                patch.object(mem0_mcp.Memory,'from_config',side_effect=create_store):
            store=mem0_mcp.memory()
        assert len(store.embedding_model.embed('ping'))==1024
        assert hits[-1][1]=='Bearer test-token'
        store.embedding_model.client.close()

    # Wrong dimensions fail validation; retries retain the unsaved account and secret.
    responses=[subprocess.CompletedProcess([],0,json.dumps({'data':[{'embedding':[0.1]*n}]})+'\n200','') for n in (768,1024)]
    with patch.object(configure.subprocess,'run',side_effect=responses), \
            patch('builtins.input',side_effect=['','a'*32,'test-token','','','']), \
            contextlib.redirect_stdout(io.StringIO()) as output:
        assert configure.frontier_embedding({},'secondary')['api_key']=='test-token'
    assert '1024 dimensions' in output.getvalue() and 'test-token' not in output.getvalue()

    # Selecting local clears frontier credentials, including the secondary.
    with patch.object(configure,'model_choice',return_value='local'), \
            patch.object(configure,'mem0_api_url',return_value=local['api_url']):
        data = configure.mem0_model_settings(data)
    assert not any(k.startswith('embedding_') and ('provider' in k or 'api_key' in k or 'model' in k) for k in data)
    print('PASS Cloudflare: frontier defaults, authenticated setup, legacy config and mixed-provider transport/failover')
finally:
    server.shutdown()
