"""Select currently loaded LM Studio models without triggering model loading."""
import json
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
