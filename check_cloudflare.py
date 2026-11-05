"""Cloudflare embedding setup and transport checks without external services."""
import contextlib
import io
import json
import threading
import tempfile
from pathlib import Path
from types import SimpleNamespace
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

    response = io.BytesIO(json.dumps({'data':[{'embedding':[0.1]*1024}]}).encode())
    with patch.object(configure.urllib.request.OpenerDirector, 'open', return_value=response) as opened, \
            patch('builtins.input', side_effect=['', 'a'*32, 'test-token']), contextlib.redirect_stdout(io.StringIO()):
        selected = configure.frontier_embedding({}, 'primary')
    req = opened.call_args.args[0]
    assert req.full_url == 'https://api.cloudflare.com/client/v4/accounts/'+'a'*32+'/ai/v1/embeddings'
    assert req.get_header('Authorization') == 'Bearer test-token'
    assert selected['model'] == '@cf/baai/bge-m3'
    assert opened.call_args.kwargs['timeout'] == 10

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
    responses=[io.BytesIO(json.dumps({'data':[{'embedding':[0.1]*768}]}).encode()),
               io.BytesIO(json.dumps({'data':[{'embedding':[0.1]*1024}]}).encode())]
    with patch.object(configure.urllib.request.OpenerDirector,'open',side_effect=responses), \
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
