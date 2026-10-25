import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('doubao_mouse_bridge', Path(__file__).resolve().parents[1] / 'src' / 'doubao_mouse_bridge.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.alt = m.decode_shortcut({'keyCode': 0, 'modifierFlags': 2049})
        self.ctrl_k = m.decode_shortcut({'keyCode': 75, 'modifierFlags': 258})

    def test_real_side_encoding(self):
        for flags, vk in ((258, 162), (514, 163), (1025, 164), (2049, 165),
                          (4100, 160), (8196, 161), (16392, 91), (32776, 92)):
            with self.subTest(flags=flags):
                self.assertEqual(m.decode_shortcut({'keyCode': 0, 'modifierFlags': flags}).sides, {vk})

    def test_unknown_flags_fail_closed(self):
        for data in ({'keyCode': 75, 'modifierFlags': 0x10}, {'keyCode': 0, 'modifierFlags': 2048},
                     {'keyCode': True, 'modifierFlags': 1}):
            with self.assertRaises(ValueError):
                m.decode_shortcut(data)

    def test_disabled_binding(self):
        self.assertIsNone(m.decode_shortcut({'keyCode': 0, 'modifierFlags': 0}))

    def test_right_ctrl_and_unrelated_keys_pass(self):
        state = m.InputState(m.Bindings(self.alt, self.alt))
        for vk in (0xA3, 65, 13):
            self.assertEqual(state.key(vk, True, True, True, 0), (False, []))
            self.assertEqual(state.key(vk, False, True, True, 1), (False, []))

    def test_physical_keys_always_pass(self):
        state = m.InputState(m.Bindings(self.alt, self.alt))
        for down in (True, False):
            self.assertEqual(state.key(0xA5, down, False, True, 0), (False, []))
        self.assertEqual(state.pressed, set())

    def test_physical_modifier_blocks_conflicting_mouse_combo(self):
        state = m.InputState(m.Bindings(self.ctrl_k, None))
        self.assertEqual(state.key(164, True, False, True, 0), (False, []))
        state.key(162, True, True, True, 0.1)
        self.assertEqual(state.key(75, True, True, True, 0.2), (False, []))
        self.assertEqual(state.tick(0.5), [])
        state.key(75, False, True, True, 0.6)
        state.key(164, False, False, True, 0.7)
        self.assertEqual(state.key(75, True, True, True, 0.8), (True, []))
        self.assertEqual(state.tick(1.1), ['start'])

    def test_physical_modifier_cancels_pending_without_swallowing(self):
        state = m.InputState(m.Bindings(self.ctrl_k, None))
        state.key(162, True, True, True, 0)
        state.key(75, True, True, True, 0.1)
        self.assertEqual(state.key(164, True, False, True, 0.15), (False, []))
        self.assertEqual(state.tick(0.4), [])
        self.assertEqual(state.key(75, False, True, True, 0.5), (True, []))

    def test_hold_start_stop(self):
        state = m.InputState(m.Bindings(self.alt, None))
        self.assertEqual(state.key(165, True, True, True, 0), (True, []))
        self.assertEqual(state.tick(0.21), ['start'])
        self.assertEqual(state.key(165, False, True, True, 1), (True, ['stop']))
        self.assertIsNone(state.active)

    def test_dual_tap_then_tap_stops(self):
        state = m.InputState(m.Bindings(self.alt, self.alt))
        state.key(165, True, True, True, 0)
        self.assertEqual(state.key(165, False, True, True, 0.05), (True, ['start']))
        self.assertEqual(state.key(165, True, True, True, 1), (True, ['stop']))
        self.assertEqual(state.key(165, False, True, True, 1.1), (True, []))

    def test_dual_hold_not_toggle(self):
        state = m.InputState(m.Bindings(self.alt, self.alt))
        state.key(165, True, True, True, 0)
        self.assertEqual(state.tick(0.21), ['start'])
        self.assertEqual(state.key(165, False, True, True, 1), (True, ['stop']))

    def test_repeat_no_double_start(self):
        state = m.InputState(m.Bindings(self.alt, None))
        state.key(165, True, True, True, 0)
        state.tick(0.3)
        self.assertEqual(state.key(165, True, True, True, 1), (True, []))

    def test_side_specific_combination(self):
        state = m.InputState(m.Bindings(self.ctrl_k, None))
        state.key(163, True, True, True, 0)
        self.assertEqual(state.key(75, True, True, True, 0.1), (False, []))

    def test_combo_stops_on_modifier_release(self):
        state = m.InputState(m.Bindings(self.ctrl_k, None))
        state.key(162, True, True, True, 0)
        self.assertEqual(state.key(75, True, True, True, 0.1), (True, []))
        self.assertEqual(state.tick(0.31), ['start'])
        self.assertEqual(state.key(162, False, True, True, 1), (False, ['stop']))
        self.assertEqual(state.key(75, False, True, True, 1.1), (True, []))

    def test_toggle_combination_stops_once(self):
        state = m.InputState(m.Bindings(None, self.ctrl_k))
        state.key(162, True, True, True, 0)
        self.assertEqual(state.key(75, True, True, True, 0.1), (True, ['start']))
        state.key(75, False, True, True, 0.2)
        state.key(162, False, True, True, 0.3)
        self.assertEqual(state.key(162, True, True, True, 1), (False, []))
        self.assertEqual(state.key(75, True, True, True, 1.1), (True, ['stop']))
        state.key(75, False, True, True, 1.2)
        state.key(162, False, True, True, 1.3)
        self.assertIsNone(state.active)

    def test_toggle_unrelated_modifier_does_not_restart(self):
        state = m.InputState(m.Bindings(None, self.ctrl_k))
        state.key(162, True, True, True, 0)
        state.key(75, True, True, True, 0.1)
        state.key(75, False, True, True, 0.2)
        state.key(162, False, True, True, 0.3)
        self.assertEqual(state.key(163, True, True, True, 1), (False, []))

    def test_settings_window_passes(self):
        state = m.InputState(m.Bindings(self.alt, None))
        self.assertEqual(state.key(165, True, True, False, 0), (False, []))
        self.assertEqual(state.tick(1), [])

    def test_reload_old_key_disabled_without_editing_program(self):
        state = m.InputState(m.Bindings(self.alt, None))
        state.reset(m.Bindings(self.ctrl_k, None))
        self.assertEqual(state.key(165, True, True, True, 0), (False, []))
        state.key(165, False, True, True, 0.1)
        state.key(162, True, True, True, 1)
        self.assertEqual(state.key(75, True, True, True, 1.1), (True, []))
        self.assertEqual(state.tick(1.31), ['start'])

    def test_reload_keeps_balanced_release(self):
        state = m.InputState(m.Bindings(self.alt, None))
        state.key(165, True, True, True, 0)
        state.tick(0.3)
        self.assertEqual(state.reset(m.Bindings(self.ctrl_k, None)), ['cancel'])
        self.assertEqual(state.key(165, False, True, True, 1), (True, []))

    def test_lost_release_has_bounded_session(self):
        state = m.InputState(m.Bindings(self.alt, None))
        state.key(165, True, True, True, 0)
        state.tick(0.21)
        self.assertEqual(state.tick(181), ['stop'])
        self.assertIsNone(state.active)
        self.assertEqual(state.pressed, set())

    def test_disabled_voice_setting_stops_binding(self):
        config = {'schemaVersion': 12, 'voice': {'enableVoiceShortcut': False}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            self.assertEqual(m.read_bindings(path), m.Bindings(None, None))

    def test_config_read_never_writes(self):
        config = {'schemaVersion': 12, 'voice': {
            'voiceLongPressShortcut': {'keyCode': 0, 'modifierFlags': 2049},
            'voiceShortcut': {'keyCode': 75, 'modifierFlags': 258}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config), encoding='utf-8')
            before = path.read_bytes()
            bindings = m.read_bindings(path)
            self.assertEqual(bindings, m.Bindings(self.alt, self.ctrl_k))
            self.assertEqual(path.read_bytes(), before)


class NativeVoiceTests(unittest.TestCase):
    def client(self, states):
        client = object.__new__(m.NativeVoice)
        client.owned = True
        values = iter(states)
        client.calls = []
        def call(code, a=0):
            client.calls.append(code)
            return next(values) if code == 1006 else 0
        client.call = call
        return client

    def test_pending_state_is_not_discarded(self):
        client = self.client([8, 8, 0])
        with patch.object(m.time, 'sleep'):
            client.command('stop')
        self.assertNotIn(1011, client.calls)
        self.assertNotIn(1013, client.calls)
        self.assertFalse(client.owned)

    def test_slow_result_remains_owned(self):
        client = self.client([8] * 150)
        with patch.object(m.time, 'sleep'):
            client.command('stop')
        self.assertTrue(client.owned)
        self.assertNotIn(1011, client.calls)

    def test_slow_result_can_be_rechecked_without_cancelling(self):
        client = self.client([8] * 150 + [8, 0])
        with patch.object(m.time, 'sleep'):
            client.command('stop')
        client.command('check-idle')
        self.assertTrue(client.owned)
        client.command('check-idle')
        self.assertFalse(client.owned)
        self.assertNotIn(1013, client.calls)
        self.assertEqual(client.calls.count(1008), 1)

    def test_idle_reply_reaps_previous_child(self):
        from unittest.mock import MagicMock
        session = m.VoiceSession('unused')
        process = session.process = MagicMock()
        process.is_alive.return_value = False
        connection = session.connection = MagicMock()
        connection.poll.return_value = True
        connection.recv.return_value = (True, True, '')
        self.assertFalse(session.command('check-idle'))
        connection.send.assert_called_once_with('check-idle')
        process.join.assert_called_once_with(3)
        process.terminate.assert_not_called()
        connection.close.assert_called_once_with()
        self.assertIsNone(session.process)

    def test_explicit_cancel_is_finite(self):
        client = self.client([])
        client.command('cancel')
        self.assertEqual(client.calls, [1013])
        self.assertFalse(client.owned)


if __name__ == '__main__':
    unittest.main(verbosity=2)
