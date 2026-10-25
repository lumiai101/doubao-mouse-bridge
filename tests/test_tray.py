"""验证托盘组件、图标生成、菜单契约、真实 main 入口生命周期、错误锁存与 IPC 事件。"""
import argparse
import gc
import ctypes
from ctypes import wintypes as w
import importlib.util
import json
import logging
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

# 确保在缺少 pynput 依赖的环境中能安全加载模块
if 'pynput' not in sys.modules:
    _pynput = types.ModuleType('pynput')
    class _MockListener:
        def __init__(self, win32_event_filter=None, **kwargs):
            self.filter = win32_event_filter
            self._alive = True
        def start(self): pass
        def wait(self): pass
        def is_alive(self): return self._alive
        def stop(self): self._alive = False
        def suppress_event(self): pass
    _pynput.keyboard = types.SimpleNamespace(Listener=_MockListener)
    sys.modules['pynput'] = _pynput
    sys.modules['pynput.keyboard'] = _pynput.keyboard

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'doubao_mouse_bridge.py'
spec = importlib.util.spec_from_file_location('doubao_mouse_bridge', SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class FakeFunc:
    """包装 Windows API 模拟函数，支持动态赋予 argtypes / restype 属性。"""
    def __init__(self, fn):
        self.fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *args, **kwargs):
        return self.fn(*args, **kwargs)


class MockKernel32:
    """提供 Windows kernel32 调用的内存隔离桩，不碰系统现用单实例互斥体与事件。"""
    def __init__(self, real=None):
        self.real = real
        self.mutex_code = 0
        self.CreateMutexW = FakeFunc(lambda sec, owner, name: 1001)
        self.CreateEventW = FakeFunc(lambda sec, manual, initial, name: 2001)
        self.OpenEventW = FakeFunc(lambda access, inherit, name: 3001)
        self.SetEvent = FakeFunc(lambda handle: 1)
        self.WaitForSingleObject = FakeFunc(lambda handle, ms: 1)  # 默认未触发
        self.CloseHandle = FakeFunc(lambda handle: 1)
        self.OpenProcess = FakeFunc(lambda *a: 0)
        self.QueryFullProcessImageNameW = FakeFunc(lambda *a: 0)

    def __getattr__(self, name):
        if self.real and hasattr(self.real, name):
            return getattr(self.real, name)
        func = FakeFunc(lambda *a, **kw: 0)
        setattr(self, name, func)
        return func


class MockUser32:
    """提供 Windows user32 调用的安全模拟桩。"""
    def __init__(self, real=None):
        self.real = real
        self.fg_hwnd = 100
        self.GetForegroundWindow = FakeFunc(lambda: self.fg_hwnd)
        self.GetWindowThreadProcessId = FakeFunc(self._get_thread_pid)
        self.GetAsyncKeyState = FakeFunc(lambda vk: 0)
        self.MessageBoxW = FakeFunc(lambda *a: 1)

    def _get_thread_pid(self, hwnd, ptr):
        try:
            ctypes.cast(ptr, ctypes.POINTER(w.DWORD)).contents.value = 1234
        except Exception:
            pass
        return 1

    def __getattr__(self, name):
        if self.real and hasattr(self.real, name):
            return getattr(self.real, name)
        func = FakeFunc(lambda *a, **kw: 0)
        setattr(self, name, func)
        return func


def make_clean_tk(orig_tk):
    """包装 tk.Tk，记录所有的 after 句柄并在 destroy 时主动 after_cancel，防止退出时 Tcl 报错。"""
    def create_wrapped(*args, **kwargs):
        root = orig_tk(*args, **kwargs)
        scheduled_afters = []
        real_after = root.after

        def safe_after(delay, callback=None):
            aid = real_after(delay, callback)
            scheduled_afters.append(aid)
            return aid

        root.after = safe_after

        real_destroy = root.destroy
        def clean_destroy():
            for aid in scheduled_afters:
                try:
                    root.after_cancel(aid)
                except Exception:
                    pass
            real_destroy()

        root.destroy = clean_destroy
        return root
    return create_wrapped


class TrayAndLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / 'config.json'
        self.config_data = {
            'schemaVersion': 12,
            'voice': {
                'enableVoiceShortcut': True,
                'hasShortcutConflict': False,
                'voiceLongPressShortcut': {'keyCode': 0, 'modifierFlags': 0x801},  # 右Alt
                'voiceShortcut': {'keyCode': 0, 'modifierFlags': 0x801}            # 右Alt
            }
        }
        self.config_path.write_text(json.dumps(self.config_data), encoding='utf-8')
        reader = patch.object(m, 'read_logi_buttons', return_value=[])
        reader.start()
        self.addCleanup(reader.stop)

    def tearDown(self):
        # 多个真实 Tk 测试的循环引用须在主线程释放。
        gc.collect()
        self.temp_dir.cleanup()

    def test_tray_icon_images_all_states(self):
        """测试所有状态下的图标生成：尺寸为 64x64，且 RGBA 模式。"""
        states = ('ready', 'paused', 'attention', 'unconfirmed')
        for state in states:
            with self.subTest(state=state):
                img = m.create_tray_icon_image(state)
                self.assertEqual(img.size, (64, 64))
                self.assertEqual(img.mode, 'RGBA')

    def test_build_tray_menu_structure_and_action_dispatch(self):
        """测试托盘菜单结构包含 5 项及动作派发。"""
        actions = []
        status_label = '状态: 适配已就绪'
        pause_label = '暂停适配'

        menu = m.build_tray_menu(
            lambda act: actions.append(act),
            lambda: status_label,
            lambda: pause_label
        )
        self.assertEqual(len(menu.items), 5)
        # item 0: status (disabled)
        self.assertFalse(menu.items[0].enabled)
        self.assertEqual(menu.items[0].text, '状态: 适配已就绪')

        # item 1: show_window (default)
        self.assertTrue(menu.items[1].default)
        menu.items[1](None)
        self.assertIn('show_window', actions)

        # item 2: toggle_pause
        self.assertEqual(menu.items[2].text, '暂停适配')
        menu.items[2](None)
        self.assertIn('toggle_pause', actions)

        # item 3: recheck
        menu.items[3](None)
        self.assertIn('recheck', actions)

        # item 4: exit
        menu.items[4](None)
        self.assertIn('exit', actions)

    def test_main_lifecycle_default_hidden_open_close_pause_recheck_exit(self):
        """从真正 m.main() 执行到 mainloop，驱动真实按钮/WM_DELETE_WINDOW/托盘回调与单一tick链。"""
        import tkinter as tk

        class SafeVoice:
            process = None
            def __init__(self, install): pass
            def command(self, act): return False
            def close(self): pass

        class SafeProfile:
            def active(self): return True
            def close(self): pass

        class MockTrayIcon:
            def __init__(self, *args, **kwargs):
                self.visible = False
                self.title = ''
                self.icon = None
                self.menu = kwargs.get('menu')
                self._stop_event = threading.Event()
            def run(self, setup=None):
                if setup:
                    setup(self)
                self._stop_event.wait()
            def stop(self):
                self.visible = False
                self._stop_event.set()
            def update_menu(self):
                pass

        mock_kernel = MockKernel32()
        mock_user = MockUser32()
        recorded = {}
        after_idle_calls = []

        orig_tk = make_clean_tk(tk.Tk)
        def tk_spy(*args, **kwargs):
            root = orig_tk(*args, **kwargs)
            orig_after_idle = root.after_idle
            def spy_after_idle(fn, *a, **kw):
                after_idle_calls.append(fn)
                return orig_after_idle(fn, *a, **kw)
            root.after_idle = spy_after_idle

            def step_lifecycle():
                # 1. 验证默认隐藏（withdrawn）
                recorded['initial_state'] = root.state()

                # 提取控件引用
                def get_all_widgets(parent):
                    res = []
                    for child in parent.winfo_children():
                        res.append(child)
                        res.extend(get_all_widgets(child))
                    return res

                widgets = get_all_widgets(root)
                buttons = {w.cget('text'): w for w in widgets if isinstance(w, tk.Button)}
                recorded['buttons_available'] = set(buttons.keys())
                with patch.object(m, 'open_settings') as launch:
                    for label in ('打开豆包', '豆包输入法设置', '罗技按键设置'):
                        buttons[label].invoke()
                    recorded['settings_targets'] = [call.args[0] for call in launch.call_args_list]

                # 2. 打开窗口并验证状态
                root.deiconify()
                recorded['opened_state'] = root.state()

                # 3. 触发 WM_DELETE_WINDOW 协议，验证关闭窗口回退到隐藏而不退出
                wm_func = root.protocol('WM_DELETE_WINDOW')
                root.tk.call(wm_func)
                recorded['closed_state'] = root.state()

                # 4. 暂停与恢复：验证 UI 按钮文案与状态
                pause_btn = buttons['暂停适配']
                pause_btn.invoke()
                recorded['pause_label'] = pause_btn.cget('text')
                pause_btn.invoke()
                recorded['resumed_label'] = pause_btn.cget('text')

                # 5. 重新检查：验证不会新增 tick 链（单一tick链无多余 after_idle）
                idle_count_before = len(after_idle_calls)
                buttons['重新检查'].invoke()
                recorded['recheck_new_tick_chains'] = len(after_idle_calls) - idle_count_before

                # 6. 正常退出清理：点击退出按钮
                buttons['退出程序'].invoke()

            # 延迟 150ms 验证默认隐藏及驱动生命周期
            root.after(150, step_lifecycle)
            return root

        import pystray
        with patch.object(pystray, 'Icon', MockTrayIcon), \
             patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: mock_kernel if name == 'kernel32' else mock_user), \
             patch.object(m, 'find_install', return_value=Path(self.temp_dir.name)), \
             patch.object(m, 'VoiceSession', SafeVoice), \
             patch.object(m, 'DoubaoProfile', SafeProfile), \
             patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(tk, 'Tk', side_effect=tk_spy), \
             patch.object(sys, 'argv', ['doubao_bridge', '--config', str(self.config_path)]):
            m.main()

        self.assertEqual(recorded.get('initial_state'), 'withdrawn')
        self.assertEqual(recorded.get('opened_state'), 'normal')
        self.assertEqual(recorded.get('closed_state'), 'withdrawn')
        self.assertEqual(recorded.get('pause_label'), '恢复适配')
        self.assertEqual(recorded.get('resumed_label'), '暂停适配')
        self.assertEqual(recorded.get('recheck_new_tick_chains'), 0)
        self.assertIn('退出程序', recorded.get('buttons_available', set()))
        self.assertEqual(recorded['settings_targets'], ['doubao', 'ime', 'logi'])

    def test_tray_fallback_when_dependency_or_init_fails(self):
        """测试托盘不可用时回退为可见控制窗口，并禁用隐藏按钮与 WM_DELETE_WINDOW 隐藏。"""
        import tkinter as tk

        class SafeVoice:
            process = None
            def __init__(self, install): pass
            def command(self, act): return False
            def close(self): pass

        class SafeProfile:
            def active(self): return True
            def close(self): pass

        mock_kernel = MockKernel32()
        mock_user = MockUser32()
        recorded = {}

        orig_tk = make_clean_tk(tk.Tk)
        def tk_spy(*args, **kwargs):
            root = orig_tk(*args, **kwargs)

            def step_fallback():
                def get_all_widgets(parent):
                    res = []
                    for child in parent.winfo_children():
                        res.append(child)
                        res.extend(get_all_widgets(child))
                    return res

                widgets = get_all_widgets(root)
                buttons = {w.cget('text'): w for w in widgets if isinstance(w, tk.Button)}

                recorded['window_state'] = root.state()
                recorded['hide_btn_disabled'] = (str(buttons['隐藏到托盘'].cget('state')) == 'disabled')

                # 点击隐藏按钮不应隐藏
                buttons['隐藏到托盘'].invoke()
                recorded['state_after_hide_click'] = root.state()

                # 点击关闭协议不应隐藏
                wm_func = root.protocol('WM_DELETE_WINDOW')
                root.tk.call(wm_func)
                recorded['state_after_wm_close'] = root.state()

                # 正常退出
                buttons['退出程序'].invoke()

            root.after(150, step_fallback)
            return root

        with patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: mock_kernel if name == 'kernel32' else mock_user), \
             patch.object(m, 'find_install', return_value=Path(self.temp_dir.name)), \
             patch.object(m, 'VoiceSession', SafeVoice), \
             patch.object(m, 'DoubaoProfile', SafeProfile), \
             patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(m, 'create_tray_icon_image', side_effect=RuntimeError('模拟托盘依赖故障')), \
             patch.object(tk, 'Tk', side_effect=tk_spy), \
             patch.object(sys, 'argv', ['doubao_bridge', '--config', str(self.config_path)]):
            m.main()

        self.assertEqual(recorded.get('window_state'), 'normal')
        self.assertTrue(recorded.get('hide_btn_disabled'))
        self.assertEqual(recorded.get('state_after_hide_click'), 'normal')
        self.assertEqual(recorded.get('state_after_wm_close'), 'normal')

    def test_native_fault_latch_and_recheck_confirmation(self):
        """测试原生语音错误触发 threading.Event 锁存，必须经 askyesno 确认才允许恢复。"""
        import tkinter as tk
        from tkinter import messagebox

        captured_listener = {}
        class CapturingListener:
            def __init__(self, win32_event_filter=None, **kw):
                captured_listener['filter'] = win32_event_filter
                self._alive = True
            def start(self): pass
            def wait(self): pass
            def is_alive(self): return self._alive
            def stop(self): self._alive = False
            def suppress_event(self): pass

        class FaultyVoice:
            process = None
            def __init__(self, install):
                self.call_count = 0
            def command(self, act):
                if act in ('start', 'start-fallback'):
                    self.call_count += 1
                    if self.call_count == 1:
                        raise RuntimeError('豆包录音异常: 响应超时')
                return False
            def close(self): pass

        class SafeProfile:
            def active(self): return True
            def close(self): pass

        mock_kernel = MockKernel32()
        mock_user = MockUser32()
        dialog_history = {'askyesno': [], 'showwarning': [], 'answer': False}
        recorded_states = {}

        orig_tk = make_clean_tk(tk.Tk)
        def tk_spy(*args, **kwargs):
            root = orig_tk(*args, **kwargs)

            def step_init():
                root.after(100, step_trigger_fault)

            def step_trigger_fault():
                # 注入快捷键事件触发 start
                class EvData:
                    flags = 0x12  # injected
                    vkCode = 0xA5  # 右Alt
                    scanCode = 0

                ev_filter = captured_listener['filter']
                ev_filter(0x100, EvData())
                ev_filter(0x101, EvData())
                root.after(300, step_check_latched)

            def step_check_latched():
                def get_all_widgets(parent):
                    res = []
                    for child in parent.winfo_children():
                        res.append(child)
                        res.extend(get_all_widgets(child))
                    return res

                widgets = get_all_widgets(root)
                labels = [w.cget('text') for w in widgets if isinstance(w, tk.Label)]
                buttons = {w.cget('text'): w for w in widgets if isinstance(w, tk.Button)}

                recorded_states['fault_labels'] = [t for t in labels if '录音状态未确认' in t or '需要处理' in t]

                # 用户拒绝恢复确认
                dialog_history['answer'] = False
                buttons['重新检查'].invoke()

                # 用户同意恢复确认
                dialog_history['answer'] = True
                buttons['重新检查'].invoke()

                # 等待下一次 tick 执行完毕 (failure 期间 tick 间隔为 1000ms)
                root.after(1150, step_finish)

            def step_finish():
                def get_all_widgets(parent):
                    res = []
                    for child in parent.winfo_children():
                        res.append(child)
                        res.extend(get_all_widgets(child))
                    return res

                widgets = get_all_widgets(root)
                labels = [w.cget('text') for w in widgets if isinstance(w, tk.Label)]
                buttons = {w.cget('text'): w for w in widgets if isinstance(w, tk.Button)}

                recorded_states['recovered_labels'] = [t for t in labels if '适配状态' in t]
                buttons['退出程序'].invoke()

            root.after(100, step_init)
            return root

        def mock_askyesno(title, msg, **kw):
            dialog_history['askyesno'].append((title, msg))
            return dialog_history['answer']

        mock_pynput = types.ModuleType('pynput')
        mock_pynput.keyboard = types.SimpleNamespace(Listener=CapturingListener)

        with patch.dict(sys.modules, {'pynput': mock_pynput, 'pynput.keyboard': mock_pynput.keyboard}), \
             patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: mock_kernel if name == 'kernel32' else mock_user), \
             patch.object(m, 'find_install', return_value=Path(self.temp_dir.name)), \
             patch.object(m, 'VoiceSession', FaultyVoice), \
             patch.object(m, 'DoubaoProfile', SafeProfile), \
             patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(messagebox, 'askyesno', side_effect=mock_askyesno), \
             patch.object(messagebox, 'showwarning', side_effect=lambda t, m, **kw: dialog_history['showwarning'].append((t, m))), \
             patch.object(tk, 'Tk', side_effect=tk_spy), \
             patch.object(sys, 'argv', ['doubao_bridge', '--config', str(self.config_path)]):
            m.main()

        # 验证错误已锁存，并必须通过 askyesno 确认
        self.assertTrue(any('录音状态未确认' in t for t in recorded_states.get('fault_labels', [])))
        self.assertEqual(len(dialog_history['askyesno']), 2)
        self.assertTrue(any('监听已就绪' in t for t in recorded_states.get('recovered_labels', [])))

    def test_pending_hold_is_cancelled_when_foreground_changes(self):
        import tkinter as tk

        for switch_delay, dual in ((0, False), (100, False), (0, True)):
            self.config_data['voice']['voiceShortcut'] = {'keyCode': 0, 'modifierFlags': 0x801 if dual else 0}
            self.config_path.write_text(json.dumps(self.config_data), encoding='utf-8')
            with self.subTest(switch_delay=switch_delay, dual=dual):
                commands = []
                captured = {}
                failures = []
                user = MockUser32()

                class Listener:
                    def __init__(self, win32_event_filter=None, **kw):
                        captured['filter'] = win32_event_filter
                        self.alive = True
                    def start(self): pass
                    def wait(self): pass
                    def is_alive(self): return self.alive
                    def stop(self): self.alive = False
                    def suppress_event(self): pass

                class Voice:
                    process = None
                    def __init__(self, install): pass
                    def command(self, action): commands.append(action); return False
                    def close(self): pass

                class Profile:
                    def active(self): return True
                    def close(self): pass

                class Tray:
                    def __init__(self, *a, **kw): self.done = threading.Event()
                    def run(self, setup=None):
                        if setup: setup(self)
                        self.done.wait()
                    def stop(self): self.done.set()
                    def update_menu(self): pass

                original = make_clean_tk(tk.Tk)
                def create_root(*a, **kw):
                    root = original(*a, **kw)
                    def finish():
                        try:
                            self.assertNotIn('start', commands)
                            event = types.SimpleNamespace(flags=0x12, vkCode=0xA5, scanCode=0)
                            captured['filter'](0x101, event)
                        except BaseException as exc:
                            failures.append(exc)
                        finally:
                            def exit_button(parent):
                                for child in parent.winfo_children():
                                    if isinstance(child, tk.Button) and child.cget('text') == '退出程序':
                                        child.invoke()
                                        return True
                                    if exit_button(child):
                                        return True
                            exit_button(root)
                    def press():
                        event = types.SimpleNamespace(flags=0x12, vkCode=0xA5, scanCode=0)
                        captured['filter'](0x100, event)
                        if switch_delay:
                            root.after(switch_delay, lambda: setattr(user, 'fg_hwnd', 200))
                        else:
                            user.fg_hwnd = 200
                        if dual:
                            captured['filter'](0x101, event)
                        root.after(350, finish)
                    root.after(100, press)
                    return root

                fake = types.SimpleNamespace(keyboard=types.SimpleNamespace(Listener=Listener))
                import pystray
                with patch.dict(sys.modules, {'pynput': fake, 'pynput.keyboard': fake.keyboard}), \
                     patch.object(pystray, 'Icon', Tray), \
                     patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: MockKernel32() if name == 'kernel32' else user), \
                     patch.object(m, 'find_install', return_value=Path(self.temp_dir.name)), \
                     patch.object(m, 'VoiceSession', Voice), \
                     patch.object(m, 'DoubaoProfile', Profile), \
                     patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
                     patch.object(tk, 'Tk', side_effect=create_root), \
                     patch.object(sys, 'argv', ['bridge', '--config', str(self.config_path)]):
                    m.main()
                if failures:
                    raise failures[0]

    def test_slow_result_is_checked_before_next_start(self):
        import tkinter as tk
        from tkinter import messagebox

        for busy in (False, True):
            with self.subTest(busy=busy):
                commands = []
                captured = {}
                failures = []
                observed = {}

                class Listener:
                    def __init__(self, win32_event_filter=None, **kw):
                        captured['filter'] = win32_event_filter
                        self.alive = True
                    def start(self): pass
                    def wait(self): pass
                    def is_alive(self): return self.alive
                    def stop(self): self.alive = False
                    def suppress_event(self): pass

                class SlowVoice:
                    process = None
                    def __init__(self, install): pass
                    def command(self, action):
                        commands.append(action)
                        if action == 'start': self.process = object()
                        if action == 'check-idle' and not busy: self.process = None
                        if action == 'cancel': self.process = None
                        return self.process is not None
                    def close(self): self.process = None

                class Profile:
                    def active(self): return True
                    def close(self): pass

                class Tray:
                    def __init__(self, *a, **kw): self.done = threading.Event()
                    def run(self, setup=None):
                        if setup: setup(self)
                        self.done.wait()
                    def stop(self): self.done.set()
                    def update_menu(self): pass

                original = make_clean_tk(tk.Tk)
                def create_root(*a, **kw):
                    root = original(*a, **kw)
                    def widgets(parent):
                        for child in parent.winfo_children():
                            yield child
                            yield from widgets(child)
                    def exit_test():
                        next(w for w in widgets(root) if isinstance(w, tk.Button) and w.cget('text') == '退出程序').invoke()
                    def tap():
                        event = types.SimpleNamespace(flags=0x12, vkCode=0xA5, scanCode=0)
                        captured['filter'](0x100, event)
                        captured['filter'](0x101, event)
                    def wait_for(predicate, then, deadline=None):
                        deadline = deadline or time.monotonic() + 3
                        if predicate():
                            then()
                        elif time.monotonic() > deadline:
                            failures.append(AssertionError('工作线程未按预期执行: ' + repr(commands)))
                            exit_test()
                        else:
                            root.after(10, lambda: wait_for(predicate, then, deadline))
                    def finish():
                        try:
                            expected = ['start', 'stop', 'check-idle'] + ([] if busy else ['start'])
                            self.assertEqual(commands, expected)
                            if busy:
                                labels = [w.cget('text') for w in widgets(root) if isinstance(w, tk.Label)]
                                self.assertTrue(any('录音状态未确认' in text for text in labels))
                            observed['checked'] = True
                        except BaseException as exc:
                            failures.append(exc)
                        finally:
                            exit_test()
                    def restart():
                        tap()
                        wait_for(lambda: 'check-idle' in commands and (busy or commands.count('start') == 2),
                                 lambda: root.after(1100 if busy else 50, finish))
                    def stop():
                        tap()
                        wait_for(lambda: 'stop' in commands, restart)
                    def start():
                        tap()
                        wait_for(lambda: 'start' in commands, stop)
                    root.after(100, start)
                    return root

                fake = types.SimpleNamespace(keyboard=types.SimpleNamespace(Listener=Listener))
                import pystray
                with patch.dict(sys.modules, {'pynput': fake, 'pynput.keyboard': fake.keyboard}), \
                     patch.object(pystray, 'Icon', Tray), \
                     patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: MockKernel32() if name == 'kernel32' else MockUser32()), \
                     patch.object(m, 'find_install', return_value=Path(self.temp_dir.name)), \
                     patch.object(m, 'VoiceSession', SlowVoice), \
                     patch.object(m, 'DoubaoProfile', Profile), \
                     patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
                     patch.object(messagebox, 'showwarning'), \
                     patch.object(tk, 'Tk', side_effect=create_root), \
                     patch.object(sys, 'argv', ['bridge', '--config', str(self.config_path)]):
                    m.main()
                if failures:
                    raise failures[0]
                self.assertTrue(observed.get('checked'))

    def test_secondary_launch_collision_branches(self):
        """测试单实例互斥体碰撞（错误码183）时的唤醒现有窗口与旧版兼容弹窗。"""
        mock_kernel = MockKernel32()
        fake_user32 = MagicMock()
        windll_mock = types.SimpleNamespace(user32=fake_user32)

        # 分支 A: 支持 ShowUI 事件，触发 OpenEventW -> SetEvent -> 退出
        mock_kernel.OpenEventW = FakeFunc(lambda access, inherit, name: 5001)
        with patch.object(ctypes, 'WinDLL', return_value=mock_kernel), \
             patch.object(ctypes, 'get_last_error', return_value=183), \
             patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(sys, 'argv', ['doubao_bridge']):
            m.main()

        # 分支 B: 不支持 ShowUI 事件，调用 MessageBoxW 提示并不重复安装
        mock_kernel.OpenEventW = FakeFunc(lambda access, inherit, name: None)
        with patch.object(ctypes, 'WinDLL', return_value=mock_kernel), \
             patch.object(ctypes, 'windll', windll_mock, create=True), \
             patch.object(ctypes, 'get_last_error', return_value=183), \
             patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(sys, 'argv', ['doubao_bridge']):
            m.main()

        self.assertTrue(fake_user32.MessageBoxW.called)

    def test_stop_argument_invokes_stop_event(self):
        """测试带 --stop 参数请求运行中的适配正常退出。"""
        mock_kernel = MockKernel32()
        opened_handles = []
        set_events = []

        mock_kernel.OpenEventW = FakeFunc(lambda access, inherit, name: 7777)
        mock_kernel.SetEvent = FakeFunc(lambda h: set_events.append(h) or 1)

        with patch.object(ctypes, 'WinDLL', return_value=mock_kernel), \
             patch.object(m, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(sys, 'argv', ['doubao_bridge', '--stop']):
            m.main()

        self.assertIn(7777, set_events)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows 真实命名事件测试仅在 Windows 支持')
    def test_secondary_launch_show_event_ipc(self):
        """测试基于 Windows Named Event 的二次启动呼出事件机制（使用独立隔离名称）。"""
        import uuid
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
        kernel.CreateEventW.restype = w.HANDLE
        kernel.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
        kernel.OpenEventW.restype = w.HANDLE
        kernel.SetEvent.argtypes = [w.HANDLE]
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.CloseHandle.argtypes = [w.HANDLE]

        test_show_event_name = r'Local\DoubaoBridgeTestShowUI_' + uuid.uuid4().hex
        h_evt = kernel.CreateEventW(None, False, False, test_show_event_name)
        self.assertTrue(bool(h_evt))
        try:
            # 初始未触发
            self.assertNotEqual(kernel.WaitForSingleObject(h_evt, 0), 0)

            # 二次启动端打开并设置事件
            h_open = kernel.OpenEventW(2, False, test_show_event_name)
            self.assertTrue(bool(h_open))
            kernel.SetEvent(h_open)
            kernel.CloseHandle(h_open)

            # 主实例等待并捕获事件
            self.assertEqual(kernel.WaitForSingleObject(h_evt, 0), 0)
            # 自动重置后再次检查为未触发
            self.assertNotEqual(kernel.WaitForSingleObject(h_evt, 0), 0)
        finally:
            kernel.CloseHandle(h_evt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
