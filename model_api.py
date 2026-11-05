"""Resolve local models and adapt native frontier inference APIs."""
import json
from http.client import RemoteDisconnected
import math
import re
from urllib.error import HTTPError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


def api_base_url(api_url, endpoint):
    """Convert an OpenAI-compatible endpoint URL to its client base URL."""
    parsed = urlparse(api_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('Mem0 model API URLs must be absolute HTTP(S) URLs without query parameters')
    path = parsed.path.rstrip('/')
    for suffix in endpoint:
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunparse((parsed.scheme, parsed.netloc, path or '/', '', '', '')).rstrip('/')


def select_model(api_url, kind):
    """Return the client base and first loaded instance of the requested type."""
    suffixes = ('/embeddings', '/embedding') if kind == 'embedding' else (
        '/chat/completions', '/chat/completion', '/inference')
    base = api_base_url(api_url, suffixes)
    root = re.sub(r'/(?:api/)?v[01]$', '', base)
    for version in ('v1', 'v0'):
        request = Request(root + '/api/' + version + '/models',
                          headers={'Authorization': 'Bearer local-no-key'})
        try:
            with urlopen(request, timeout=10) as response:
                listing = json.load(response)
            break
        except HTTPError as error:
            if version == 'v1' and error.code == 404:
                continue
            raise
    if not isinstance(listing, dict):
        raise ValueError('Invalid LM Studio model list')
    models = listing.get('models' if version == 'v1' else 'data')
    if not isinstance(models, list):
        raise ValueError('Invalid LM Studio model list')
    for model in models:
        if not isinstance(model, dict) or model.get('type') not in (
                ('embedding', 'embeddings') if kind == 'embedding' else ('llm', 'vlm')):
            continue
        if version == 'v1':
            instances = model.get('loaded_instances', [])
            if not isinstance(instances, list):
                continue
            identifiers = [instance.get('id') for instance in instances if isinstance(instance, dict)]
        else:
            identifiers = [model.get('id')] if model.get('state') == 'loaded' else []
        for identifier in identifiers:
            if isinstance(identifier, str) and identifier.strip():
                return base, identifier
    raise ValueError(f'No loaded {kind} model found. Load one in LM Studio first.')


def select_available_model(primary, secondary, kind):
    """Fall back only when the primary cannot return a model-list response."""
    try:
        return select_model(primary, kind)
    except HTTPError:
        raise
    except (OSError, RemoteDisconnected):
        if not secondary:
            raise
        return select_model(secondary, kind)


def validate_response(response, kind):
    """Require a usable chat result or a vector compatible with existing memories."""
    try:
        if kind == 'embedding':
            vector = response['data'][0]['embedding']
            if not isinstance(vector, list) or not vector or any(
                    type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise ValueError('Invalid embeddings result')
            if len(vector) != 1024:
                raise ValueError('Embedding must have 1024 dimensions for existing CODEX_MEMORIES')
        else:
            message = response['choices'][0]['message']
            if not any(isinstance(message.get(key), str) and message[key].strip()
                       for key in ('content', 'reasoning_content')):
                raise ValueError('Invalid chat completion result')
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        raise ValueError(f'Invalid {kind} response') from error


def embedding_connection(data, secondary=False):
    """Legacy URL-only embedding settings remain local."""
    suffix = '_secondary' if secondary else ''
    return {key: data.get('embedding_' + key + suffix, default) for key, default in (
        ('provider', 'local'), ('api_url', None), ('model', None), ('api_key', None))}


def resolve_embedding(connection):
    if connection['provider'] == 'local':
        return select_model(connection['api_url'], 'embedding')
    if connection['provider'] != 'cloudflare':
        raise ValueError('Unsupported embedding provider')
    if connection.get('model') != '@cf/baai/bge-m3' or not connection.get('api_key'):
        raise ValueError('Cloudflare BGE-M3 model and API token are required')
    parsed = urlparse(connection['api_url'])
    if (parsed.username is not None or parsed.password is not None or
            (parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
        raise ValueError('Cloudflare requires HTTPS')
    return api_base_url(connection['api_url'], ('/embeddings',)), connection['model']


def embedding_request(connection, payload):
    _, model = resolve_embedding(connection)
    return (connection['api_url'], {'Authorization': 'Bearer ' + (connection.get('api_key') or 'local-no-key'),
            'Content-Type': 'application/json'}, {**payload, 'model': model, 'encoding_format': 'float'})


def inference_connection(data, secondary=False):
    """Read new provider settings without changing legacy URL-only configurations."""
    suffix = '_secondary' if secondary else ''
    return {key: data.get('inference_' + key + suffix, default) for key, default in (
        ('provider', 'local'), ('api_url', None), ('model', None), ('api_key', None))}


def resolve_inference(connection):
    if connection['provider'] == 'local':
        return select_model(connection['api_url'], 'llm')
    if connection['provider'] not in ('openai', 'gemini', 'anthropic'):
        raise ValueError('Unsupported inference provider')
    if not connection.get('model') or not connection.get('api_key'):
        raise ValueError('Frontier model and API key are required')
    base = api_base_url(connection['api_url'], ('/chat/completions', '/messages'))
    return base, connection['model']



def anthropic_schema(schema):
    """Remove unsupported wire constraints; extraction still validates them locally."""
    result = dict(schema)
    for key in ('minLength', 'maxLength', 'maxItems'):
        result.pop(key, None)
    if 'properties' in result:
        result['properties'] = {key: anthropic_schema(value)
                                for key, value in result['properties'].items()}
    if 'items' in result:
        result['items'] = anthropic_schema(result['items'])
    return result


def inference_request(connection, payload):
    """Build provider-specific, non-streaming text inference without sharing credentials."""
    base, model = resolve_inference(connection)
    provider = connection['provider']
    headers = {'Content-Type': 'application/json'}
    if provider in ('local', 'openai'):
        headers['Authorization'] = 'Bearer ' + (connection.get('api_key') or 'local-no-key')
        url = connection['api_url'] if provider == 'local' else base + '/chat/completions'
        return url, headers, {**payload, 'model': model}
    if payload.get('tools') or payload.get('stream'):
        raise ValueError('Native inference supports non-streaming text and JSON extraction only')
    messages = payload['messages']
    system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
    conversation = [m for m in messages if m['role'] != 'system']
    if any(m['role'] not in ('user', 'assistant') or not isinstance(m['content'], str)
           for m in conversation):
        raise ValueError('Native inference requires user/assistant text messages')
    schema = payload.get('response_format', {}).get('json_schema', {}).get('schema')
    max_tokens = payload.get('max_completion_tokens', payload.get('max_tokens', 4096))
    if provider == 'gemini':
        from urllib.parse import quote
        model_path = quote(model.removeprefix('models/'), safe='')
        headers['x-goog-api-key'] = connection['api_key']
        config = {'maxOutputTokens': max_tokens}
        if schema:
            config.update(responseMimeType='application/json', responseJsonSchema=schema)
        elif payload.get('response_format', {}).get('type') == 'json_object':
            config['responseMimeType'] = 'application/json'
        body = {'contents': [{'role': 'model' if m['role'] == 'assistant' else 'user',
                             'parts': [{'text': m['content']}]} for m in conversation],
                'generationConfig': config}
        if system:
            body['systemInstruction'] = {'parts': [{'text': system}]}
        return base + '/models/' + model_path + ':generateContent', headers, body
    headers.update({'x-api-key': connection['api_key'], 'anthropic-version': '2023-06-01'})
    body = {'model': model, 'max_tokens': max_tokens, 'messages': conversation}
    if system:
        body['system'] = system
    if schema:
        body['output_config'] = {'format': {'type': 'json_schema', 'schema': anthropic_schema(schema)}}
    return base + '/messages', headers, body


def inference_response(provider, response):
    """Preserve truncation/refusal signals for the existing extraction guards."""
    if provider in ('local', 'openai'):
        return response
    if provider == 'gemini':
        candidate = response.get('candidates', [{}])[0]
        parts = candidate.get('content', {}).get('parts', [])
        content = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
        reason = {'STOP': 'stop', 'MAX_TOKENS': 'length'}.get(
            candidate.get('finishReason'), 'content_filter')
        tokens = response.get('usageMetadata', {}).get('candidatesTokenCount', 0)
    elif provider == 'anthropic':
        content = ''.join(p.get('text', '') for p in response.get('content', [])
                          if p.get('type') == 'text')
        reason = {'end_turn': 'stop', 'max_tokens': 'length'}.get(
            response.get('stop_reason'), 'content_filter')
        tokens = response.get('usage', {}).get('output_tokens', 0)
    else:
        raise ValueError('Unsupported inference provider')
    return {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content},
                         'finish_reason': reason}],
            'usage': {'completion_tokens': tokens}}
