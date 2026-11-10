import ctypes
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from test_tray import m, MockKernel32, MockUser32, make_clean_tk


class ConnectionUITests(unittest.TestCase):
    def run_bridge(self, scenario, probe=None, close=None):
        import tkinter as tk
        import pystray
        failures = []
        observed = types.SimpleNamespace(listeners=[], voices=[], commands=[], trays=[], root=None)

        class Listener:
            def __init__(self, win32_event_filter=None):
                self.filter = win32_event_filter
                self.alive = False
                observed.listeners.append(self)
            def start(self): self.alive = True
            def wait(self): pass
            def is_alive(self): return self.alive
            def stop(self): self.alive = False
            def suppress_event(self): pass

        class Voice:
            process = None
            def __init__(self, install):
                self.install = str(install)
                observed.voices.append(self)
            def command(self, action):
                observed.commands.append(action)
                return False
            def probe(self):
                return probe(observed, self) if probe else 0
            def close(self):
                if close:
                    close(observed, self)

        class Profile:
            def active(self): return True
            def close(self): pass

        class Tray:
            def __init__(self, *args, **kwargs):
                self.done = threading.Event()
                self.title = ''
                self.menu = kwargs.get('menu')
                observed.trays.append(self)
            def run(self, setup=None):
                if setup: setup(self)
                self.done.wait(10)
            def stop(self): self.done.set()
            def update_menu(self): pass

        def widgets(parent):
            for child in parent.winfo_children():
                yield child
                yield from widgets(child)

        def button(label):
            return next(widget for widget in widgets(observed.root)
                        if isinstance(widget, tk.Button) and widget.cget('text') == label)

        def status():
            return [widget.cget('text') for widget in widgets(observed.root)
                    if isinstance(widget, tk.Label)]

        def finish(error=None):
            if error:
                failures.append(error)
            button('退出程序').invoke()

        def wait_for(predicate, then, deadline=None):
            deadline = deadline or time.monotonic() + 4
            try:
                if predicate():
                    then()
                elif time.monotonic() > deadline:
                    finish(AssertionError('等待状态超时: ' + repr(status())))
                else:
                    observed.root.after(10, lambda: wait_for(predicate, then, deadline))
            except BaseException as exc:
                finish(exc)

        original = make_clean_tk(tk.Tk)
        def create_root(*args, **kwargs):
            observed.root = root = original(*args, **kwargs)
            def callback_error(kind, error, traceback):
                finish(error)
            root.report_callback_exception = callback_error
            root.after(100, lambda: wait_for(lambda: True,
                       lambda: scenario(observed, button, status, wait_for, finish)))
            return root

        shortcut = m.Shortcut(0, frozenset({0xA5}))
        observed.buttons = [m.SideButton('测试鼠标', shortcut)]
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'settings.json'
            config.write_text(json.dumps({'schemaVersion': 12, 'voice': {
                'enableVoiceShortcut': True, 'hasShortcutConflict': False,
                'voiceLongPressShortcut': {'keyCode': 0, 'modifierFlags': 0x801},
                'voiceShortcut': {'keyCode': 0, 'modifierFlags': 0x801}}}), encoding='utf-8')
            observed.config = config
            fake = types.SimpleNamespace(keyboard=types.SimpleNamespace(Listener=Listener))
            with patch.dict(sys.modules, {'pynput': fake, 'pynput.keyboard': fake.keyboard}), \
                 patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw:
                              MockKernel32() if name == 'kernel32' else MockUser32()), \
                 patch.object(m, 'find_install', return_value=Path(directory)), \
                 patch.object(m, 'VoiceSession', Voice), \
                 patch.object(m, 'DoubaoProfile', Profile), \
                 patch.object(m, 'read_logi_buttons', side_effect=lambda: observed.buttons), \
                 patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
                 patch('tkinter.messagebox.showwarning'), \
                 patch.object(tk, 'Tk', side_effect=create_root), \
                 patch.object(pystray, 'Icon', Tray), \
                 patch.object(sys, 'argv', ['bridge', '--config', str(config)]):
                m.main()
        if failures:
            raise failures[0]
        return observed

    def test_unknown_interface_stays_disconnected_and_recovery_really_replaces_worker(self):
        def probe(observed, voice):
            if len(observed.voices) == 1:
                raise RuntimeError('接口版本未验证，请安装兼容版本')
            return 0

        def scenario(observed, button, status, wait_for, finish):
            self.assertIn('断开', status())
            self.assertNotIn('稳定连接', status())
            recovery = button('一键恢复')
            recovery.invoke()
            recovery.invoke()
            def check():
                self.assertEqual(len(observed.voices), 2)
                self.assertEqual(observed.commands, [])
                self.assertIn('稳定连接', observed.trays[0].title)
                finish()
            wait_for(lambda: '稳定连接' in status(), check)

        self.run_bridge(scenario, probe)

    def test_expired_health_drops_green_and_blocks_start_during_slow_probe(self):
        release = threading.Event()
        def probe(observed, voice):
            if getattr(observed, 'stall', False):
                release.wait(1)
            return 0
        def scenario(observed, button, status, wait_for, finish):
            def ready():
                observed.stall = True
                def check():
                    try:
                        self.assertIn('断开', status())
                        self.assertNotIn('稳定连接', observed.trays[0].title)
                        event = types.SimpleNamespace(flags=0x12, vkCode=0xA5, scanCode=0)
                        observed.listeners[-1].filter(0x100, event)
                        observed.listeners[-1].filter(0x101, event)
                        self.assertEqual(observed.commands, [])
                        release.set()
                        finish()
                    except BaseException as exc:
                        release.set()
                        finish(exc)
                observed.root.after(300, check)
            wait_for(lambda: '稳定连接' in status(), ready)
        with patch.object(m, 'HEALTH_TTL', 0.15), patch.object(m, 'HEALTH_INTERVAL', 0.05):
            self.run_bridge(scenario, probe)

    def test_dead_listener_is_rebuilt_and_late_callback_is_ignored(self):
        def scenario(observed, button, status, wait_for, finish):
            def ready():
                previous = observed.listeners[-1]
                previous.alive = False
                button('一键恢复').invoke()
                def check():
                    self.assertEqual(len(observed.listeners), 2)
                    event = types.SimpleNamespace(flags=0x12, vkCode=0xA5, scanCode=0)
                    previous.filter(0x100, event)
                    previous.filter(0x101, event)
                    self.assertEqual(observed.commands, [])
                    finish()
                wait_for(lambda: len(observed.listeners) == 2 and '稳定连接' in status(), check)
            wait_for(lambda: '稳定连接' in status(), ready)
        self.run_bridge(scenario)

    def test_health_loss_automatically_runs_bounded_recovery(self):
        def probe(observed, voice):
            observed.probes = getattr(observed, 'probes', 0) + 1
            if observed.probes == 1:
                raise RuntimeError('接口暂时不可用')
            return 0
        def scenario(observed, button, status, wait_for, finish):
            def check():
                self.assertEqual(len(observed.voices), 2)
                self.assertEqual(observed.commands, [])
                self.assertIn('稳定连接', status())
                finish()
            wait_for(lambda: len(observed.voices) > 1 and '稳定连接' in status(), check)
        with patch.object(m, 'HEALTH_INTERVAL', 0.05), patch.object(m, 'RECOVERY_BACKOFF', (0.05, 0.1, 0.2)):
            self.run_bridge(scenario, probe)

    def test_missing_device_and_unreadable_config_never_turn_green(self):
        def scenario(observed, button, status, wait_for, finish):
            def ready():
                observed.buttons = []
                def check_device():
                    self.assertNotIn('稳定连接', status())
                    observed.buttons = [m.SideButton('测试鼠标', m.Shortcut(0xDD, frozenset({0xA5})))]
                    observed.config.write_text('{invalid', encoding='utf-8')
                    observed.root.after(100, lambda: wait_for(lambda: '稳定连接' not in status(), finish))
                wait_for(lambda: '稳定连接' not in status(), check_device)
            wait_for(lambda: '稳定连接' in status(), ready)
        self.run_bridge(scenario)

    def test_automatic_recovery_stops_after_three_failed_attempts(self):
        def probe(observed, voice):
            raise RuntimeError('接口版本未验证')
        def scenario(observed, button, status, wait_for, finish):
            def exhausted():
                def check():
                    self.assertEqual(len(observed.voices), 4)
                    self.assertIn('断开', status())
                    self.assertEqual(observed.commands, [])
                    finish()
                observed.root.after(400, lambda: wait_for(lambda: True, check))
            wait_for(lambda: len(observed.voices) >= 4, exhausted)
        with patch.object(m, 'HEALTH_INTERVAL', 0.05), patch.object(m, 'RECOVERY_BACKOFF', (0.05, 0.1, 0.2)):
            self.run_bridge(scenario, probe)

    def test_config_change_and_mismatched_mapping_cannot_stay_green(self):
        def scenario(observed, button, status, wait_for, finish):
            def ready():
                config = json.loads(observed.config.read_text(encoding='utf-8'))
                config['voice']['voiceLongPressShortcut'] = {'keyCode': 75, 'modifierFlags': 0x401}
                config['voice']['voiceShortcut'] = {'keyCode': 75, 'modifierFlags': 0x401}
                observed.config.write_text(json.dumps(config), encoding='utf-8')
                def check():
                    self.assertNotIn('稳定连接', observed.trays[0].title)
                    self.assertTrue(any('未匹配' in label for label in status()))
                    self.assertEqual(observed.commands, [])
                    finish()
                wait_for(lambda: '断开' in status(), check)
            wait_for(lambda: '稳定连接' in status(), ready)
        self.run_bridge(scenario)

    def test_pause_during_recovery_cannot_turn_green(self):
        release = threading.Event()
        def probe(observed, voice):
            if len(observed.voices) > 1:
                release.wait(1)
            return 0
        def scenario(observed, button, status, wait_for, finish):
            def ready():
                button('一键恢复').invoke()
                button('暂停适配').invoke()
                release.set()
                def check():
                    self.assertIn('断开', status())
                    self.assertTrue(any('已暂停' in label for label in status()))
                    self.assertEqual(observed.commands, [])
                    finish()
                observed.root.after(200, lambda: wait_for(lambda: True, check))
            wait_for(lambda: '稳定连接' in status(), ready)
        self.run_bridge(scenario, probe)

    def test_close_during_recovery_does_not_rebuild_listener_or_restart_voice(self):
        release = threading.Event()
        def close(observed, voice):
            if not getattr(observed, 'blocked', False):
                observed.blocked = True
                release.wait(1)
        def scenario(observed, button, status, wait_for, finish):
            def ready():
                observed.listeners[-1].alive = False
                button('一键恢复').invoke()
                def stop():
                    button('退出程序').invoke()
                    release.set()
                wait_for(lambda: getattr(observed, 'blocked', False), stop)
            wait_for(lambda: '稳定连接' in status(), ready)
        observed = self.run_bridge(scenario, close=close)
        self.assertEqual(len(observed.voices), 1)
        self.assertEqual(len(observed.listeners), 1)
        self.assertFalse(observed.listeners[0].is_alive())
        self.assertEqual(observed.commands, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
