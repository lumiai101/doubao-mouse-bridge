import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import tempfile
import json
import sqlite3
from contextlib import closing

spec = importlib.util.spec_from_file_location('side_button_bridge', Path(__file__).resolve().parents[1] / 'src/doubao_mouse_bridge.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class SideButtonStatusTests(unittest.TestCase):
    def setUp(self):
        self.combo = m.Shortcut(0xDD, frozenset((0xA2, 0xA4)))
        self.button = m.SideButton('测试鼠标', self.combo)

    def test_label_shows_punctuation_not_virtual_key_number(self):
        self.assertEqual(self.combo.label(), '左Ctrl+左Alt+]')

    def test_exact_sides_match_both_modes(self):
        title, detail = m.side_button_status([self.button], m.Bindings(self.combo, self.combo))
        self.assertEqual(title, '按键设置匹配')
        self.assertIn('匹配豆包长按/免按', detail)

    def test_generic_doubao_modifier_accepts_explicit_logi_side(self):
        generic = m.decode_shortcut({'keyCode': 0xDD, 'modifierFlags': 3})
        self.assertEqual(m.side_button_status([self.button], m.Bindings(generic, None))[0], '按键设置匹配')

    def test_wrong_side_or_extra_modifier_does_not_match(self):
        for shortcut in (m.Shortcut(0xDD, frozenset((0xA3, 0xA4))),
                         m.Shortcut(0xDD, frozenset((0xA2,))),
                         m.Shortcut(0xDB, self.combo.sides)):
            self.assertEqual(m.side_button_status([self.button], m.Bindings(shortcut, None))[0], '侧键需检查')

    def test_no_mouse_never_claims_ready(self):
        self.assertEqual(m.side_button_status([], m.Bindings(self.combo, None))[0], '侧键未确认')

    def test_unknown_device_and_profile_override_never_claim_match(self):
        for button in (m.SideButton('未知型号', None, '无法定位后侧键'),
                       m.SideButton('测试鼠标', self.combo, '存在应用专属设置，当前映射未确认')):
            self.assertEqual(m.side_button_status([button], m.Bindings(self.combo, None))[0], '侧键需检查')

    def test_multiple_devices_must_all_match(self):
        other = m.SideButton('另一只鼠标', m.Shortcut(13, frozenset()))
        self.assertEqual(m.side_button_status([self.button, other], m.Bindings(self.combo, None))[0], '侧键需检查')


class LogiReaderTests(unittest.TestCase):
    def setUp(self):
        self.assignment = {'slotId': 'test-mouse_c83', 'card': {'macro': {
            'type': 'KEYSTROKE', 'keystroke': {'virtualKeyId': 'VK_RIGHT_BRACKET',
                                             'code': 48, 'modifiers': [224, 226]}}}}
        self.data = {'ever_connected_devices': {'devices': [
            {'slotPrefix': 'test-mouse', 'deviceType': 'MOUSE'}]},
            'profile_keys': ['default'], 'default': {
                'name': 'PROFILE_NAME_DEFAULT', 'assignments': [self.assignment]}}
        self.metadata = {'devices': [{'slotPrefix': 'test-mouse', 'displayName': '测试鼠标',
                                      'capabilities': {'specialKeys': {'programmable': [82, 83, 86]}}}]}

    def parse(self):
        return m.parse_logi_buttons(self.data, self.metadata)

    def test_uses_metadata_and_only_rear_button_not_front_or_wheel(self):
        for suffix in ('82', '86'):
            self.data['default']['assignments'].append({'slotId': 'test-mouse_c' + suffix})
        self.assertEqual(self.parse(), [m.SideButton('测试鼠标', m.Shortcut(0xDD, frozenset((0xA2, 0xA4))))])

    def test_unknown_model_or_missing_control_fails_closed(self):
        self.metadata['devices'][0]['capabilities']['specialKeys']['programmable'] = [82, 86]
        self.assertIsNone(self.parse()[0].shortcut)
        self.metadata['devices'] = []
        self.assertIsNone(self.parse()[0].shortcut)

    def test_duplicate_historical_device_is_deduplicated(self):
        self.data['ever_connected_devices']['devices'] *= 2
        self.assertEqual(len(self.parse()), 1)

    def test_application_override_never_reports_global_as_current(self):
        self.data['profile_keys'].append('app')
        self.data['app'] = {'name': 'Example app', 'assignments': [self.assignment]}
        self.assertIn('应用专属', self.parse()[0].reason)

    def test_modifier_only_forms_match_doubao(self):
        cases = [({'code': 230, 'modifiers': [], 'virtualKeyId': 'VK_RMENU'}, 2049),
                 ({'modifiers': [228]}, 514)]
        for keystroke, flags in cases:
            self.assignment['card']['macro']['keystroke'] = keystroke
            shortcut = m.decode_shortcut({'keyCode': 0, 'modifierFlags': flags})
            self.assertEqual(self.parse()[0].shortcut, shortcut)
            self.assertEqual(m.side_button_status(self.parse(), m.Bindings(shortcut, None))[0], '按键设置匹配')

    def test_unknown_application_schema_cannot_claim_global_match(self):
        self.data['profile_keys'].append('app')
        for profile in ({'name': 'App', 'deviceProfiles': [{'assignments': [self.assignment]}]},
                        {'name': 'App', 'assignments': {}},
                        {'name': 'App', 'assignments': [{}]}):
            self.data['app'] = profile
            with self.assertRaises(ValueError):
                self.parse()

    def test_duplicate_assignment_or_default_is_not_guessed(self):
        self.data['default']['assignments'] *= 2
        self.assertIsNone(self.parse()[0].shortcut)
        self.data['profile_keys'].append('default')
        with self.assertRaises(ValueError):
            self.parse()

    def test_unknown_key_does_not_treat_hid_code_as_windows_vk(self):
        self.assignment['card']['macro']['keystroke']['virtualKeyId'] = 'UNKNOWN'
        self.assertIsNone(self.parse()[0].shortcut)
        self.assignment['card']['macro']['keystroke'].pop('virtualKeyId')
        self.assertIsNone(self.parse()[0].shortcut)

    def test_all_hid_modifier_sides_and_dynamic_main_key(self):
        for hid, vk in ((224, 162), (225, 160), (226, 164), (227, 91),
                        (228, 163), (229, 161), (230, 165), (231, 92)):
            with self.subTest(hid=hid):
                self.assertEqual(m.decode_logi_shortcut({'virtualKeyId': 'VK_K', 'modifiers': [hid]}),
                                 m.Shortcut(75, frozenset((vk,))))
        for modifiers in ([999], [True], 'ctrl'):
            with self.assertRaises(ValueError):
                m.decode_logi_shortcut({'virtualKeyId': 'VK_K', 'modifiers': modifiers})

    def test_macro_and_missing_mapping_are_not_keystrokes(self):
        self.assignment['card']['macro']['type'] = 'SEQUENCE'
        self.assertIsNone(self.parse()[0].shortcut)
        self.data['default']['assignments'] = []
        self.assertIsNone(self.parse()[0].shortcut)

    def test_read_only_database_reload_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, meta = Path(tmp) / 'settings.db', Path(tmp) / 'devices.json'
            self.assertEqual(m.read_logi_buttons(db, meta), [])
            self.assertFalse(db.exists())
            meta.write_text(json.dumps(self.metadata), encoding='utf-8')
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('CREATE TABLE data(file BLOB)')
                connection.execute('INSERT INTO data VALUES (?)', (json.dumps(self.data),))
            before = db.read_bytes()
            self.assertEqual(m.read_logi_buttons(db, meta), self.parse())
            self.assertEqual(db.read_bytes(), before)
            self.assignment['card']['macro']['keystroke']['virtualKeyId'] = 'VK_K'
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('UPDATE data SET file = ?', (json.dumps(self.data),))
            self.assertEqual(m.read_logi_buttons(db, meta)[0].shortcut.main, 75)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('UPDATE data SET file = ?', ('invalid json',))
            self.assertIsNone(m.read_logi_buttons(db, meta)[0].shortcut)


class SettingsEntryTests(unittest.TestCase):
    def test_known_local_entries_and_missing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            paths = {'doubao': base / 'Doubao/Application/Doubao.exe',
                     'logi': base / 'LogiOptionsPlus/logioptionsplus.exe',
                     'ime': base / 'DoubaoImeSettings.exe'}
            for path in paths.values():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            with patch.dict(m.os.environ, {'LOCALAPPDATA': tmp, 'ProgramFiles': tmp}), patch.object(m, 'find_install', return_value=base):
                for kind, path in paths.items():
                    self.assertEqual(m.settings_target(kind), path)
                paths['logi'].unlink()
                with self.assertRaises(FileNotFoundError):
                    m.settings_target('logi')
                with self.assertRaises(ValueError):
                    m.settings_target('unknown')

    def test_launch_uses_exact_local_file_not_shell_command(self):
        with patch.object(m, 'settings_target', return_value=Path('test.exe')), patch.object(m.os, 'startfile', create=True) as start:
            m.open_settings('doubao')
            start.assert_called_once_with('test.exe')
            start.reset_mock()
            m.open_settings('ime')
            start.assert_called_once_with('test.exe', arguments='--open-tab voice --highlight-item global_voice')


if __name__ == '__main__':
    unittest.main()
