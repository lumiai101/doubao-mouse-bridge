"""验证长按兜底状态机，不安装钩子或调用豆包接口。"""
import importlib.util
from pathlib import Path
import sys
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'doubao_mouse_bridge.py'
spec = importlib.util.spec_from_file_location('bridge_physical_test', SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class PhysicalTests(unittest.TestCase):
    def test_hold_start_release_stop(self):
        hold = m.PhysicalHold(m.Shortcut(0, frozenset({165})))
        self.assertEqual(hold.key(165, True, True, 0), [])
        self.assertEqual(hold.tick(.59), [])
        self.assertEqual(hold.tick(.61), ['start-fallback'])
        self.assertEqual(hold.key(165, False, True, 1), ['stop-fallback'])

    def test_quick_tap_not_started(self):
        hold = m.PhysicalHold(m.Shortcut(0, frozenset({165})))
        hold.key(165, True, True, 0)
        hold.key(165, False, True, .1)
        self.assertEqual(hold.tick(1), [])

    def test_dynamic_rebind(self):
        hold = m.PhysicalHold(m.Shortcut(0, frozenset({165})))
        hold.reset(m.Shortcut(75, frozenset({162})))
        hold.key(165, True, True, 0)
        self.assertEqual(hold.tick(1), [])
        hold.key(165, False, True, 1.1)
        hold.key(162, True, True, 2)
        hold.key(75, True, True, 2.1)
        self.assertEqual(hold.tick(3), ['start-fallback'])
        self.assertEqual(hold.key(75, False, True, 4), ['stop-fallback'])

    def test_alt_tab_never_starts_until_full_release(self):
        hold = m.PhysicalHold(m.Shortcut(0, frozenset({165})))
        hold.key(165, True, True, 0)
        hold.key(9, True, True, .1)
        hold.key(9, False, True, .2)
        self.assertEqual(hold.tick(1), [])
        hold.key(165, False, True, 1.1)
        hold.key(165, True, True, 2)
        self.assertEqual(hold.tick(3), ['start-fallback'])

    def test_disallowed_never_starts(self):
        hold = m.PhysicalHold(m.Shortcut(0, frozenset({165})))
        hold.key(165, True, False, 0)
        self.assertEqual(hold.tick(1), [])

    def test_reset_emits_stop_only_when_fallback_active(self):
        hold = m.PhysicalHold(m.Shortcut(0, frozenset({165})))
        hold.key(165, True, True, 0)
        hold.tick(1)
        self.assertEqual(hold.reset(None), ['stop-fallback'])
        self.assertEqual(hold.reset(None), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
