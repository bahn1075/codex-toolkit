"""Hardware-specific Headroom installation and real compression checks."""
import asyncio
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile


def gpu_vendors(root=Path('/sys/bus/pci/devices')):
    vendors = set()
    for device in root.glob('*'):
        try:
            if (device / 'class').read_text().strip().startswith('0x03'):
                vendor = (device / 'vendor').read_text().strip()
                if vendor in {'0x1002', '0x8086', '0x10de'}:
                    vendors.add({'0x1002': 'amd', '0x8086': 'intel', '0x10de': 'nvidia'}[vendor])
        except OSError:
            continue
    return vendors


def resolve_torch_backend(uv, python):
    # Reuse uv's driver/device compatibility detection instead of maintaining GPU tables.
    result = subprocess.run([uv, 'pip', 'compile', '--python', python, '--no-deps',
        '--no-header', '--no-annotate', '--torch-backend', 'auto', '-'],
        input='torch>=2.12.1\n', text=True, capture_output=True, timeout=120, check=True)
    match = re.search(r'^torch==[^\s+]+\+([a-z0-9.]+)$', result.stdout, re.MULTILINE)
    return match.group(1) if match else 'cpu'


def plan(system, machine, vendors, detected='cpu'):
    backend, torch = 'onnx_cpu', None
    reason = 'No compatible Headroom GPU runtime; use ONNX CPU.'
    if system == 'Darwin' and machine == 'arm64':
        backend, reason = 'onnx_coreml', 'Apple Silicon: use CoreML without PyTorch.'
    elif system == 'Linux':
        if ('amd' in vendors and detected.startswith('rocm')) or \
                ('nvidia' in vendors and re.fullmatch(r'cu\d+', detected)):
            backend, torch = 'pytorch', detected
            reason = 'GPU runtime selected by uv; validate actual execution before enabling.'
        elif 'intel' in vendors:
            # ponytail: upstream Kompress has no XPU device selection; use CPU until it does.
            reason = 'Intel GPU detected; Headroom has no XPU backend, so use ONNX CPU.'
    return {'system': system, 'machine': machine, 'gpu_vendors': sorted(vendors),
            'backend': backend, 'torch_backend': torch, 'reason': reason}


def install_args(selected, python, uv):
    package = 'headroom-ai[mcp,code,ml]>=0.39.1' if selected['torch_backend'] else \
        'headroom-ai[mcp,code]>=0.39.1'
    args = [uv, 'tool', 'install', '--upgrade', '--reinstall', '--python', python,
            package, '--with', 'onnxruntime>=1.24,<2', '--with', 'transformers>=5.5,<6']
    if selected['torch_backend']:
        args += ['--torch-backend', selected['torch_backend']]
    return args


def install(selected, python, uv, root):
    try:
        subprocess.run(install_args(selected, python, uv), check=True, stdout=sys.stderr)
        subprocess.run([str(root / 'uv-tools/headroom-ai/bin/python'), __file__,
            'verify', selected['backend'], selected['torch_backend'] or 'cpu'],
            check=True, timeout=300, stdout=sys.stderr)
    except (subprocess.SubprocessError, OSError) as exc:
        if selected['backend'] == 'onnx_cpu':
            raise
        print(f'Headroom accelerator setup failed ({exc}); reinstalling ONNX CPU.', file=sys.stderr)
        selected = dict(selected, backend='onnx_cpu', torch_backend=None,
                        reason='Accelerator installation or execution failed; verified ONNX CPU fallback.')
        subprocess.run(install_args(selected, python, uv), check=True, stdout=sys.stderr)
        subprocess.run([str(root / 'uv-tools/headroom-ai/bin/python'), __file__,
            'verify', 'onnx_cpu', 'cpu'], check=True, timeout=300, stdout=sys.stderr)
    return selected


def initialize_runtime():
    # Preserve the CLI's settings precedence and OS/additive certificate trust.
    from headroom import settings_store
    settings_store.apply_to_environ(settings_store.load())
    from headroom.proxy.ssl_context import build_httpx_verify, ensure_process_trust
    ensure_process_trust()
    from huggingface_hub import set_client_factory
    from huggingface_hub.utils import _http
    # HF creates its own TLS context; share Headroom trust without dropping HF's offline hook.
    set_client_factory(lambda: _http.httpx.Client(verify=build_httpx_verify(),
        event_hooks={'request': [_http.hf_request_event_hook]},
        follow_redirects=True, timeout=None))


async def verify(backend, torch_backend):
    os.environ.update(HEADROOM_KOMPRESS_BACKEND=backend, HEADROOM_MCP_READ='on',
                      HEADROOM_MODE='token', HEADROOM_SAVINGS_PROFILE='balanced')
    initialize_runtime()
    if backend == 'pytorch':
        import torch
        if torch_backend.startswith('rocm'):
            assert torch.version.hip, 'ROCm build required'
        else:
            assert torch.version.cuda, 'CUDA build required'
        assert torch.cuda.is_available(), 'Selected GPU is unavailable'
        x = torch.ones((16, 16), device='cuda')
        assert (x @ x).sum().item() == 4096
        torch.cuda.synchronize()
    from headroom.transforms.kompress_compressor import _load_kompress, KompressCompressor
    model, tokenizer, actual = _load_kompress()
    if backend == 'pytorch':
        assert actual == 'pytorch' and next(model.parameters()).device.type == 'cuda', \
            'Kompress model did not load on the selected GPU'
    if backend == 'onnx_coreml':
        assert 'CoreMLExecutionProvider' in model._session.get_providers(), 'CoreML unavailable'
    # Exercise real model inference as well as the structural compression path.
    prose = ('The server started successfully and accepted incoming connections. '
             'The health check completed and all services are ready. ') * 20
    # A healthy compressor may keep all words. Validate raw model scores directly
    # so its fail-open passthrough cannot conceal an inference failure.
    encoding = tokenizer('The diagnostic server is ready for incoming connections.',
                         return_tensors='np' if actual.startswith('onnx') else 'pt')
    if actual.startswith('onnx'):
        import numpy as np
        scores = model.get_scores(encoding['input_ids'], encoding['attention_mask'])
        assert scores.size and np.isfinite(scores).all(), 'Invalid ONNX inference scores'
    else:
        import torch
        device = next(model.parameters()).device
        with torch.no_grad():
            scores = model.get_scores(encoding['input_ids'].to(device),
                                      encoding['attention_mask'].to(device))
        assert scores.numel() and torch.isfinite(scores).all().item(), 'Invalid GPU inference scores'
    result = KompressCompressor().compress(prose)
    assert result.compressed, 'ML compressor returned no content'
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    content = json.dumps([{'id': i, 'status': 'ok', 'service': 'headroom-check',
        'message': 'Synthetic diagnostic event for compression and retrieval'} for i in range(150)])
    async with stdio_client(StdioServerParameters(command=sys.executable,
            args=[__file__, 'serve'], env=dict(os.environ))) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {'headroom_compress', 'headroom_retrieve', 'headroom_stats', 'headroom_read'} <= names
            compressed = await session.call_tool('headroom_compress', {'content': content})
            data = json.loads(compressed.content[0].text)
            assert data['tokens_saved'] > 0, data
            original = await session.call_tool('headroom_retrieve', {'hash': data['hash']})
            assert json.loads(original.content[0].text)['original_content'] == content
            stats = json.loads((await session.call_tool('headroom_stats', {})).content[0].text)
            assert stats['compressions'] == 1 and stats['retrievals'] == 1
            with tempfile.TemporaryDirectory(prefix='headroom-read-check-') as tmp:
                path = Path(tmp) / 'sample.txt'
                path.write_text('Original diagnostic content\n')
                first = await session.call_tool('headroom_read', {'file_path': str(path)})
                assert 'Original diagnostic content' in first.content[0].text
                cached = await session.call_tool('headroom_read', {'file_path': str(path)})
                assert json.loads(cached.content[0].text)['status'] == 'cached'
                path.write_text('Updated diagnostic content\n')
                fresh = await session.call_tool('headroom_read', {'file_path': str(path), 'fresh': True})
                assert 'Updated diagnostic content' in fresh.content[0].text
            print(json.dumps({'backend': backend, 'model_backend': actual,
                'ml_tokens_saved': result.tokens_saved,
                'tokens_before': data['original_tokens'], 'tokens_after': data['compressed_tokens'],
                'retrieval_matches': True, 'read_cache_verified': True, 'mcp_tools': sorted(names)}))


def main():
    if sys.argv[1] == 'serve':
        initialize_runtime()
        from headroom.ccr.mcp_server import HeadroomMCPServer
        asyncio.run(HeadroomMCPServer(check_proxy=False).run_stdio())
    elif sys.argv[1] == 'verify':
        asyncio.run(verify(*sys.argv[2:]))
    else:
        python, uv, root = sys.argv[2:]
        system, machine, vendors = platform.system(), platform.machine(), gpu_vendors()
        detected = 'cpu'
        if system == 'Linux' and vendors & {'amd', 'nvidia'}:
            try:
                detected = resolve_torch_backend(uv, python)
            except (subprocess.SubprocessError, OSError) as exc:
                print(f'GPU compatibility detection failed ({exc}); using ONNX CPU.', file=sys.stderr)
        selected = plan(system, machine, vendors, detected)
        print('Headroom: ' + selected['reason'], file=sys.stderr)
        print(json.dumps(install(selected, python, uv, Path(root))))


if __name__ == '__main__':
    main()
