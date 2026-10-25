"""隔离运行真实 Tk/pystray 主入口；不安装键鼠钩子，不调用豆包接口。"""
import ctypes
from ctypes import wintypes as w
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import uuid
from unittest.mock import patch


def verify():
    if sys.platform != 'win32':
        raise SystemExit('此探针需要 Windows 桌面会话')
    import tkinter as tk
    import pystray

    source = Path(__file__).resolve().parents[1] / 'src' / 'doubao_mouse_bridge.py'
    spec = importlib.util.spec_from_file_location('bridge_desktop_probe', source)
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    original_dll = ctypes.WinDLL
    kernel = original_dll('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [w.LPVOID, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
    kernel.CreateEventW.restype = w.HANDLE
    kernel.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenEventW.restype = w.HANDLE
    kernel.SetEvent.argtypes = [w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    prefix = 'DoubaoTrayProbe-' + uuid.uuid4().hex

    class NamedCall:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args[:-1], args[-1].replace('DoubaoMouseVoiceBridge', prefix))

    class KernelProxy:
        CreateMutexW = NamedCall(kernel.CreateMutexW)
        CreateEventW = NamedCall(kernel.CreateEventW)
        OpenEventW = NamedCall(kernel.OpenEventW)

        def __getattr__(self, name):
            return getattr(kernel, name)

    proxy = KernelProxy()
    native_calls = []
    listener_instances = []

    class Listener:
        def __init__(self, **kwargs):
            self.alive = False
            listener_instances.append(self)

        def start(self):
            self.alive = True

        def wait(self):
            pass

        def is_alive(self):
            return self.alive

        def stop(self):
            self.alive = False

    class Voice:
        process = None

        def __init__(self, install):
            pass

        def command(self, action):
            native_calls.append(action)
            return False

        def close(self):
            pass

    class Profile:
        def active(self):
            return False

        def close(self):
            pass

    original_tk = tk.Tk
    original_icon = pystray.Icon
    observed = {}
    failures = []
    icons = []
    started = time.monotonic()

    def make_icon(*args, **kwargs):
        icon = original_icon(*args, **kwargs)
        icons.append(icon)
        return icon

    def make_tk(*args, **kwargs):
        root = original_tk(*args, **kwargs)
        observed['root'] = root
        root.after(200, lambda: exercise(root, 0))
        return root

    def widgets(root):
        for child in root.winfo_children():
            yield child
            yield from widgets(child)

    def button(root, label):
        return next(x for x in widgets(root) if isinstance(x, tk.Button) and x.cget('text') == label)

    def exercise(root, stage):
        try:
            if stage == 0:
                if not icons or not icons[0].visible:
                    if time.monotonic() - started > 5:
                        raise AssertionError('真实托盘图标未就绪')
                    root.after(100, lambda: exercise(root, stage))
                    return
                assert root.state() == 'withdrawn', root.state()
                observed['default_hidden'] = True
                icons[0].menu.items[1](icons[0])
            elif stage == 1:
                assert root.state() == 'normal'
                observed['menu_opens_window'] = True
                root.tk.call(root.protocol('WM_DELETE_WINDOW'))
                assert root.state() == 'withdrawn'
                observed['window_close_hides'] = True
                handle = kernel.OpenEventW(2, False, 'Local\\' + prefix + 'ShowUI')
                assert handle
                try:
                    assert kernel.SetEvent(handle)
                finally:
                    kernel.CloseHandle(handle)
            elif stage == 2:
                assert root.state() == 'normal'
                observed['show_event_opens_window'] = True
                button(root, '暂停适配').invoke()
                assert '已暂停' in icons[0].menu.items[0].text
                observed['pause_state'] = True
                button(root, '恢复适配').invoke()
                button(root, '重新检查').invoke()
            elif stage == 3:
                assert '监听已就绪' in icons[0].menu.items[0].text
                assert '侧键未确认' in icons[0].menu.items[0].text
                assert not native_calls, native_calls
                observed['recheck_without_voice'] = True
                with patch.object(bridge, 'open_settings') as launch:
                    for label in ('打开豆包', '豆包输入法设置', '罗技按键设置'):
                        button(root, label).invoke()
                    assert [call.args[0] for call in launch.call_args_list] == ['doubao', 'ime', 'logi']
                observed['settings_buttons_dispatch'] = True
                observed['unknown_side_button_not_ready'] = True
                icons[0].menu.items[4](icons[0])
                return
            root.after(250, lambda: exercise(root, stage + 1))
        except Exception as exc:
            failures.append(repr(exc))
            button(root, '退出程序').invoke()

    with tempfile.TemporaryDirectory(prefix='doubao-tray-probe-') as temp:
        config = Path(temp) / 'config.json'
        config.write_text(json.dumps({'schemaVersion': 12, 'voice': {
            'voiceLongPressShortcut': {'keyCode': 0, 'modifierFlags': 0x801},
            'voiceShortcut': {'keyCode': 0, 'modifierFlags': 0}}}), encoding='utf-8')
        pynput = types.ModuleType('pynput')
        pynput.keyboard = types.SimpleNamespace(Listener=Listener)
        with patch.dict(sys.modules, {'pynput': pynput}), \
             patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw: proxy if name == 'kernel32' else original_dll(name, **kw)), \
             patch.object(bridge, 'find_install', return_value=Path(temp)), \
             patch.object(bridge, 'VoiceSession', Voice), \
             patch.object(bridge, 'read_logi_buttons', return_value=[]), \
             patch.object(bridge, 'DoubaoProfile', Profile), \
             patch.object(bridge, 'RotatingFileHandler', return_value=logging.NullHandler()), \
             patch.object(tk, 'Tk', side_effect=make_tk), \
             patch.object(pystray, 'Icon', side_effect=make_icon), \
             patch.object(sys, 'argv', [str(source), '--config', str(config)]):
            bridge.main()
    for icon in icons:
        thread = getattr(icon, '_thread', None)
        if thread:
            thread.join(2)
            assert not thread.is_alive(), '托盘线程未退出'
    assert listener_instances and not any(x.alive for x in listener_instances)
    assert native_calls == ['cancel'], native_calls
    assert not failures, failures
    observed.pop('root', None)
    observed['clean_shutdown'] = True
    print(json.dumps(observed, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    verify()
