"""Run with toolkit/venv/bin/python -m unittest check_headroom."""
import pathlib
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
    def test_runtime_initializes_persisted_settings_and_system_tls_trust(self):
        self.assertTrue(callable(getattr(h, 'initialize_runtime', None)), 'Runtime bootstrap is missing')
        calls = []
        settings = SimpleNamespace(load=lambda: {'setting': 'value'},
            apply_to_environ=lambda values: calls.append(('settings', values)))
        tls = SimpleNamespace(ensure_process_trust=lambda: calls.append(('tls',)))
        with patch.dict(sys.modules, {'headroom': SimpleNamespace(settings_store=settings),
                                     'headroom.proxy.ssl_context': tls}):
            h.initialize_runtime()
        self.assertEqual(calls, [('settings', {'setting': 'value'}), ('tls',)])

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
                args = h.install_args(plan, '/python', '/uv')
                self.assertNotIn('headroom-ai[all]', args)
                self.assertEqual('--torch-backend' in args, torch is not None)
                self.assertEqual(any('[mcp,code,ml]' in x for x in args), torch is not None)

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
        installs = [x for x in calls if 'install' in x]
        self.assertEqual(len(installs), 2)
        self.assertIn('--reinstall', installs[1])
        self.assertNotIn('--torch-backend', installs[1])
        self.assertFalse(any('[mcp,code,ml]' in x for x in installs[1]))


if __name__ == '__main__':
    unittest.main()
