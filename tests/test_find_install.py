import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('find_install_bridge', Path(__file__).resolve().parents[1] / 'src/doubao_mouse_bridge.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class FindInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name) / 'DoubaoIME' / 'versions'
        for name in ('v1.0.0.23', 'v1.1.0.23'):
            (self.base / name).mkdir(parents=True)
            (self.base / name / 'rpc.dll').write_bytes(b'x')
            (self.base / name / 'ImeService.exe').write_bytes(b'x')
        self.env = patch.dict(m.os.environ, {'ProgramFiles': self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_prefers_running_service_over_pending_update(self):
        running = self.base / 'v1.0.0.23' / 'ImeService.exe'
        with patch.object(m, 'running_ime_service', return_value=str(running)):
            self.assertEqual(m.find_install(), self.base / 'v1.0.0.23')

    def test_falls_back_to_newest_when_service_not_found(self):
        with patch.object(m, 'running_ime_service', return_value=None):
            self.assertEqual(m.find_install(), self.base / 'v1.1.0.23')

    def test_ignores_running_service_outside_versions_dir(self):
        for outside in (Path(self.tmp.name) / 'elsewhere' / 'v1.0.0.23',
                        self.base.parent / 'versions_evil' / 'v1.0.0.23',
                        self.base / 'v1.0.0.23' / 'nested'):
            outside.mkdir(parents=True)
            (outside / 'rpc.dll').write_bytes(b'x')
            (outside / 'ImeService.exe').write_bytes(b'x')
            with self.subTest(outside=outside),                     patch.object(m, 'running_ime_service', return_value=str(outside / 'ImeService.exe')):
                self.assertEqual(m.find_install(), self.base / 'v1.1.0.23')

    def test_falls_back_when_running_version_lacks_rpc_dll(self):
        (self.base / 'v1.0.0.23' / 'rpc.dll').unlink()
        running = self.base / 'v1.0.0.23' / 'ImeService.exe'
        with patch.object(m, 'running_ime_service', return_value=str(running)):
            self.assertEqual(m.find_install(), self.base / 'v1.1.0.23')

    def test_running_service_lookup_never_raises(self):
        with patch.object(m.ctypes, 'WinDLL', side_effect=OSError('boom')):
            self.assertIsNone(m.running_ime_service())


if __name__ == '__main__':
    unittest.main()
