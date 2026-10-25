"""离线调用真实回调，不安装系统钩子或启动麦克风。"""
import ast
import importlib.util
import logging
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'doubao_mouse_bridge.py'
spec = importlib.util.spec_from_file_location('bridge_latency_test', SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def callback(state, lock):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    fn = next(n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == 'event_filter')
    ns = dict(vars(m))
    ns.update(state=state, physical=m.PhysicalHold(state.bindings.hold),
              physical_events=m.queue.SimpleQueue(), closing=threading.Event(),
              native_fault=threading.Event(),
              lock=lock, allowed=True, paused=False, jobs=m.queue.Queue(),
              user=types.SimpleNamespace(GetForegroundWindow=lambda: 100),
              listener=types.SimpleNamespace(suppress_event=lambda: None))
    send = next(n for n in main.body if isinstance(n, ast.FunctionDef) and n.name == 'send')
    factory = ast.parse('def make_callback():\n    target = 0\n').body[0]
    factory.body.extend([send, fn, ast.Return(value=ast.Name(id='event_filter', ctx=ast.Load()))])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[])), str(SOURCE), 'exec'), ns)
    return ns['make_callback']()


class CallbackLatencyTests(unittest.TestCase):
    def test_toggle_command_does_not_write_log_on_hook_thread(self):
        key = m.decode_shortcut({'keyCode': 0, 'modifierFlags': 2049})
        event_filter = callback(m.InputState(m.Bindings(None, key)), threading.RLock())
        event = types.SimpleNamespace(flags=0x10, vkCode=165, scanCode=56)
        with patch.object(logging, 'info', side_effect=lambda *a, **k: time.sleep(0.15)):
            before = time.perf_counter()
            event_filter(0x104, event)
            elapsed = time.perf_counter() - before
        self.assertLess(elapsed, 0.05, f'发送命令同步等待日志 {elapsed:.3f} 秒')

    def test_ui_lock_contention_does_not_delay_physical_key(self):
        lock = threading.RLock()
        acquired = threading.Event()
        release = threading.Event()

        def occupy():
            with lock:
                acquired.set()
                release.wait(2)

        thread = threading.Thread(target=occupy)
        thread.start()
        acquired.wait()
        event_filter = callback(m.InputState(m.Bindings(None, None)), lock)
        event = types.SimpleNamespace(flags=0, vkCode=65, scanCode=30)
        try:
            timer = threading.Timer(0.2, release.set)
            timer.start()
            before = time.perf_counter()
            event_filter(0x100, event)
            elapsed = time.perf_counter() - before
            self.assertLess(elapsed, 0.05, f'键盘回调等待界面锁 {elapsed:.3f} 秒')
        finally:
            release.set()
            thread.join()
            timer.cancel()

    def test_slow_disk_logging_does_not_delay_injected_key(self):
        key = m.decode_shortcut({'keyCode': 0, 'modifierFlags': 2049})
        event_filter = callback(m.InputState(m.Bindings(key, None)), threading.RLock())
        event = types.SimpleNamespace(flags=0x10, vkCode=165, scanCode=56)
        with patch.object(logging, 'info', side_effect=lambda *a, **k: time.sleep(0.15)):
            before = time.perf_counter()
            event_filter(0x104, event)
            elapsed = time.perf_counter() - before
        self.assertLess(elapsed, 0.05, f'键盘回调等待慢日志 {elapsed:.3f} 秒')


if __name__ == '__main__':
    unittest.main(verbosity=2)
