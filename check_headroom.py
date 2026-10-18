"""Run with toolkit/venv/bin/python -m unittest check_headroom."""
import pathlib
import ssl
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import headroom_setup as h
except ImportError:
    h = None


class HeadroomTests(unittest.TestCase):
    def test_amd_ids_uses_readable_system_file_and_preserves_user_override(self):
        key = 'AMDGPU_ASIC_ID_TABLE_PATH'
        dirs_key = 'AMDGPU_ASIC_ID_TABLE_PATHS'
        observed = []
        def load_settings():
            observed.append({k: h.os.environ[k] for k in (key, dirs_key) if k in h.os.environ})
            return {}
        settings = SimpleNamespace(load=load_settings, apply_to_environ=lambda data: None)
        tls = SimpleNamespace(ensure_process_trust=lambda: None,
                              build_httpx_verify=lambda: ssl.create_default_context())
        for backend, override, readable, expected in (
                ('pytorch', {}, True, {key: '/usr/share/libdrm/amdgpu.ids', dirs_key: '/usr/share/libdrm'}),
                ('pytorch', {key: '/custom/amdgpu.ids'}, True, {key: '/custom/amdgpu.ids'}),
                ('pytorch', {dirs_key: '/custom/a:/custom/b'}, True, {dirs_key: '/custom/a:/custom/b'}),
                ('pytorch', {}, False, {}), ('onnx_cpu', {}, True, {})):
            with self.subTest(backend=backend, override=override, readable=readable):
                env = {'HEADROOM_KOMPRESS_BACKEND': backend, **override}
                with patch.dict(h.os.environ, env, clear=True), patch.object(h.sys, 'platform', 'linux'), \
                        patch.object(h.Path, 'is_file', return_value=True), \
                        patch.object(h.os, 'access', return_value=readable), \
                        patch.dict(sys.modules, {'headroom': SimpleNamespace(settings_store=settings),
                            'headroom.proxy.ssl_context': tls,
                            'huggingface_hub': SimpleNamespace(set_client_factory=lambda factory: None),
                            'huggingface_hub.utils': SimpleNamespace(_http=None)}):
                    h.initialize_runtime()
                    actual = {k: h.os.environ[k] for k in (key, dirs_key) if k in h.os.environ}
                    self.assertEqual(actual, expected)
                    self.assertEqual(observed[-1], expected, 'Configure libdrm before importing Headroom settings')

    def test_runtime_initializes_persisted_settings_and_system_tls_trust(self):
        self.assertTrue(callable(getattr(h, 'initialize_runtime', None)), 'Runtime bootstrap is missing')
        calls = []
        settings = SimpleNamespace(load=lambda: {'setting': 'value'},
            apply_to_environ=lambda values: calls.append(('settings', values)))
        factories = []
        context = ssl.create_default_context()
        tls = SimpleNamespace(ensure_process_trust=lambda: calls.append(('tls',)),
                              build_httpx_verify=lambda: context)
        import httpx
        hook = lambda request: None
        hf_http = SimpleNamespace(httpx=httpx, hf_request_event_hook=hook)
        with patch.dict(sys.modules, {'headroom': SimpleNamespace(settings_store=settings),
                'headroom.proxy.ssl_context': tls,
                'huggingface_hub': SimpleNamespace(set_client_factory=factories.append),
                'huggingface_hub.utils': SimpleNamespace(_http=hf_http)}):
            h.initialize_runtime()
            self.assertEqual(len(factories), 1, 'Model downloads did not receive Headroom TLS settings')
            with factories[0]() as client:
                self.assertIs(client._transport._pool._ssl_context, context)
                self.assertTrue(client.follow_redirects)
                self.assertEqual(client.event_hooks['request'], [hook])
                self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(context.check_hostname)
        self.assertEqual(calls, [('settings', {'setting': 'value'}), ('tls',)])

    def test_runtime_supports_headroom_039_tls_bootstrap(self):
        calls = []
        tls = SimpleNamespace(apply_global_tls_relaxation=lambda: calls.append('tls'),
                              build_httpx_verify=lambda: ssl.create_default_context())
        settings = SimpleNamespace(load=lambda: {}, apply_to_environ=lambda data: None)
        with patch.dict(sys.modules, {'headroom': SimpleNamespace(settings_store=settings),
                'headroom.proxy.ssl_context': tls,
                'huggingface_hub': SimpleNamespace(set_client_factory=lambda factory: None),
                'huggingface_hub.utils': SimpleNamespace(_http=None)}):
            h.initialize_runtime()
        self.assertEqual(calls, ['tls'])

    def test_hardware_plans_only_install_usable_accelerators(self):
        self.assertIsNotNone(h, 'Headroom hardware configuration is missing')
        cases = [
            ('Darwin', 'arm64', set(), '', 'onnx_coreml', None),
            ('Darwin', 'x86_64', {'intel'}, '', 'onnx_cpu', None),
            ('Linux', 'aarch64', set(), '', 'onnx_cpu', None),
            ('Linux', 'x86_64', {'intel'}, 'xpu', 'onnx_cpu', None),
            ('Linux', 'x86_64', {'amd'}, 'rocm7.2', 'pytorch', 'rocm7.2'),
            ('Linux', 'x86_64', {'nvidia'}, 'cu130', 'pytorch', 'cu130'),
            ('Linux', 'x86_64', {'amd'}, 'cu130', 'onnx_cpu', None),
            ('Linux', 'x86_64', {'nvidia'}, 'cpu', 'onnx_cpu', None),
            ('Linux', 'x86_64', set(), 'cu130', 'onnx_cpu', None),
        ]
        for system, machine, vendors, detected, backend, torch in cases:
            with self.subTest(system=system, vendors=vendors, detected=detected):
                plan = h.plan(system, machine, vendors, detected)
                self.assertEqual(plan['backend'], backend)
                self.assertEqual(plan['torch_backend'], torch)
                args = h.install_args('/python', '/uv')
                self.assertNotIn('headroom-ai[all]', args)
                self.assertNotIn('--torch-backend', args)
                self.assertFalse(any('[mcp,code,ml]' in x for x in args))

    def test_pci_detection_excludes_cpu_and_non_display_devices(self):
        self.assertIsNotNone(h)
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for i, (vendor, cls) in enumerate([('0x1002', '0x030000'),
                    ('0x8086', '0x038000'), ('0x10de', '0x030200'),
                    ('0x10de', '0x040300'), ('0x1022', '0x060000')]):
                p = root / str(i)
                p.mkdir()
                (p / 'vendor').write_text(vendor)
                (p / 'class').write_text(cls)
            (root / 'unreadable').mkdir()
            self.assertEqual(h.gpu_vendors(root), {'amd', 'intel', 'nvidia'})

    def test_native_backend_resolution_rejects_ambiguous_and_cpu_versions(self):
        self.assertIsNotNone(h)
        for output, expected in [('torch==2.14.1+rocm7.2\n', 'rocm7.2'),
                ('torch==2.14.1+cu130\n', 'cu130'), ('torch==2.14.1+cpu\n', 'cpu'),
                ('torch==2.14.1\n', 'cpu'), ('other==1\n', 'cpu')]:
            with patch.object(h.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, output, '')):
                self.assertEqual(h.resolve_torch_backend('/uv', '/python'), expected)

    def test_failed_gpu_validation_reinstalls_without_gpu_dependencies(self):
        self.assertIsNotNone(h)
        gpu = h.plan('Linux', 'x86_64', {'amd'}, 'rocm7.2')
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if 'verify' in args and len([x for x in calls if 'verify' in x]) == 1:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0)
        with patch.object(h.subprocess, 'run', side_effect=run):
            final = h.install(gpu, '/python', '/uv', pathlib.Path('/toolkit'))
        self.assertEqual(final['backend'], 'onnx_cpu')
        installs = [x for x in calls if x[1:3] == ['tool', 'install']]
        self.assertEqual(len(installs), 2)
        gpu_installs = [x for x in calls if x[1:3] == ['pip', 'install']]
        self.assertEqual(len(gpu_installs), 1)
        self.assertIn('--torch-backend', gpu_installs[0])
        self.assertIn('rocm7.2', gpu_installs[0])
        self.assertIn('/toolkit/uv-tools/headroom-ai/bin/python', gpu_installs[0])
        self.assertIn('--reinstall', installs[1])
        self.assertNotIn('--torch-backend', installs[1])
        self.assertFalse(any('[mcp,code,ml]' in x for x in installs[1]))


if __name__ == '__main__':
    unittest.main()
