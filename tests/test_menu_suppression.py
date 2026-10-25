"""回放真实 event_filter；只替换 Windows 发送端，不安装 hook 或注入系统按键。"""
import ast
from collections import deque
import ctypes
import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src/doubao_mouse_bridge.py'
spec = importlib.util.spec_from_file_location('bridge_menu_test', SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class Suppressed(Exception):
    pass


class HookReplay:
    """SendInput 新事件排到原批次后；和 pynput 一样用异常停止传播。"""
    def __init__(self, bindings):
        self.state = m.InputState(bindings)
        self.pending = deque()
        self.visible = []
        self.sent = []
        self.send_count = None
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        functions = [n for n in main.body if isinstance(n, ast.FunctionDef)
                     and n.name in ('send', 'event_filter')]
        self.ns = dict(vars(m), state=self.state, physical=m.PhysicalHold(bindings.hold),
                       physical_events=m.queue.SimpleQueue(), closing=threading.Event(),
                       native_fault=threading.Event(), lock=threading.RLock(), allowed=True,
                       paused=False, jobs=m.queue.Queue(), user=self,
                       listener=types.SimpleNamespace(suppress_event=self.suppress))
        factory = ast.parse('def make_callback():\n    target = 0\n').body[0]
        factory.body.extend(functions + [ast.Return(value=ast.Name(id='event_filter', ctx=ast.Load()))])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[])),
                     str(SOURCE), 'exec'), self.ns)
        self.ns['event_filter'] = self.ns['make_callback']()

    @staticmethod
    def GetForegroundWindow():
        return 100

    @staticmethod
    def suppress():
        raise Suppressed()

    def SendInput(self, count, inputs, size):
        self.sent.append([(int(inputs[i].ki.wVk), int(inputs[i].ki.dwFlags),
                           int(inputs[i].ki.dwExtraInfo)) for i in range(count)])
        accepted = count if self.send_count is None else self.send_count
        for i in range(accepted):
            key = inputs[i].ki
            self.pending.append((int(key.wVk), not bool(key.dwFlags & 2),
                                 0x10 | (int(key.dwFlags) & 1),
                                 int(key.wScan), int(key.dwExtraInfo)))
        return accepted

    def replay(self, events, system=False):
        self.pending.extend(events)
        while self.pending:
            vk, down, flags, scan, extra = self.pending.popleft()
            data = types.SimpleNamespace(vkCode=vk, flags=flags, scanCode=scan, dwExtraInfo=extra)
            message = (0x104 if down else 0x105) if system else (0x100 if down else 0x101)
            try:
                self.ns['event_filter'](message, data)
            except Suppressed:
                continue
            if vk == 0x12:
                vk = 0xA5 if flags & 1 else 0xA4
            self.visible.append((vk, down))
        return self.visible

    def drain_jobs(self):
        jobs = []
        while not self.ns['jobs'].empty():
            jobs.append(self.ns['jobs'].get_nowait())
        return jobs


def key(vk, down, flags=0x10, scan=0, extra=0):
    return vk, down, flags, scan, extra


def naked_menu_releases(events):
    """下游只见修饰键独按独放，是开始菜单/Alt 菜单的必要触发形状。"""
    armed, releases = set(), []
    for vk, down in events:
        if down:
            if vk in (0x5B, 0x5C, 0xA4, 0xA5):
                armed.add(vk)
            else:
                armed.clear()
        elif vk in armed:
            releases.append(vk)
            armed.remove(vk)
    return releases


class MenuSuppressionTests(unittest.TestCase):
    def test_matched_win_combo_does_not_become_bare_win_in_one_batch(self):
        replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5B}))))
        visible = replay.replay([key(0x5B, True), key(0xDD, True),
                                 key(0xDD, False), key(0x5B, False)])
        self.assertEqual(replay.drain_jobs(), ['start'])
        self.assertEqual(naked_menu_releases(visible), [], f'Windows 下游收到裸 Win: {visible}')
        self.assertEqual([event for event in visible if event[0] == 0x5B],
                         [(0x5B, True), (0x5B, False)], 'Win 按下/释放必须成对')
        self.assertNotIn((0xDD, True), visible)

    def test_matched_alt_hold_masks_release_without_stopping_pending(self):
        for vk, raw_vk, flags in ((0xA4, 0x12, 0x10), (0xA5, 0x12, 0x11)):
            with self.subTest(vk=vk):
                replay = HookReplay(m.Bindings(m.Shortcut(75, frozenset({vk})), None))
                replay.replay([key(raw_vk, True, flags, 56), key(75, True)])
                self.assertIsNotNone(replay.state.pending)
                self.assertEqual(replay.state.tick(m.time.monotonic() + 1), ['start'])
                visible = replay.replay([key(75, False), key(raw_vk, False, flags, 56)])
                self.assertEqual(naked_menu_releases(visible), [], f'Windows 下游收到裸 Alt: {visible}')
                self.assertEqual(replay.drain_jobs(), ['stop'])
                self.assertEqual([e for e in visible if e[0] in (raw_vk, vk)],
                                 [(vk, True), (vk, False)])
                self.assertFalse(replay.state.pressed)

    def test_system_key_messages_and_extra_info_do_not_skip_logi_events(self):
        replay = HookReplay(m.Bindings(None, m.Shortcut(75, frozenset({0xA4}))))
        visible = replay.replay([key(0x12, True, 0x30, 56, 123), key(75, True, extra=123),
                                 key(75, False, extra=123), key(0x12, False, 0x30, 56, 123)],
                                system=True)
        self.assertEqual(naked_menu_releases(visible), [])
        self.assertEqual(replay.drain_jobs(), ['start'])
        self.assertEqual(len(replay.sent), 1)

    def test_repeated_combo_rearms_mask_without_stopping_from_own_events(self):
        replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5B}))))
        cycle = [key(0x5B, True), key(0xDD, True), key(0xDD, False), key(0x5B, False)]
        self.assertEqual(naked_menu_releases(replay.replay(cycle)), [])
        self.assertEqual(naked_menu_releases(replay.replay(cycle)), [])
        self.assertEqual(replay.drain_jobs(), ['start', 'stop'])
        self.assertEqual(len(replay.sent), 2)
        self.assertIsNone(replay.state.active)

    def test_mask_is_tagged_and_uses_windows_input_abi(self):
        self.assertEqual(ctypes.sizeof(m.KeyboardInput), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
        self.assertEqual(ctypes.sizeof(m.KeyboardEvent), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5C}))))
        replay.replay([key(0x5C, True, 0x11, 92), key(0xDD, True),
                       key(0xDD, False), key(0x5C, False, 0x11, 92)])
        self.assertEqual(replay.sent, [[(0xE8, 0, m.MENU_INPUT_TAG),
                                       (0xE8, 2, m.MENU_INPUT_TAG),
                                       (0x5C, 3, m.MENU_INPUT_TAG)]])
        self.assertEqual(replay.drain_jobs(), ['start'])
        self.assertIsNotNone(replay.state.active, '自身屏蔽键不能触发免按停止')
        self.assertFalse(replay.state.pressed)
        self.assertFalse(replay.state.menu_modifiers)

    def test_release_before_main_and_paused_or_reloaded_still_masks(self):
        for change in ('pause', 'reload', 'fault', 'timeout'):
            with self.subTest(change=change):
                replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5B}))))
                replay.replay([key(0x5B, True), key(0xDD, True)])
                if change == 'pause':
                    replay.ns['paused'] = True
                elif change == 'reload':
                    replay.state.reset(m.Bindings(None, None))
                elif change == 'fault':
                    replay.ns['native_fault'].set()
                else:
                    replay.state.tick(m.time.monotonic() + 181)
                visible = replay.replay([key(0x5B, False), key(0xDD, False)])
                self.assertEqual(naked_menu_releases(visible), [])
                self.assertEqual([e for e in visible if e[0] == 0x5B],
                                 [(0x5B, True), (0x5B, False)])
                self.assertNotIn((0xDD, False), visible)

    def test_two_menu_modifiers_are_each_released_once(self):
        replay = HookReplay(m.Bindings(None, m.Shortcut(75, frozenset({0x5B, 0xA4}))))
        visible = replay.replay([key(0x5B, True), key(0xA4, True), key(75, True),
                                 key(75, False), key(0xA4, False), key(0x5B, False)])
        self.assertEqual(naked_menu_releases(visible), [])
        self.assertEqual([e for e in visible if e[0] in (0x5B, 0xA4)],
                         [(0x5B, True), (0xA4, True), (0xA4, False), (0x5B, False)])
        self.assertEqual(len(replay.sent), 2)
        self.assertEqual(replay.drain_jobs(), ['start'])

    def test_failed_or_partial_send_never_swallows_original_release(self):
        for count in (0, 1, 2):
            with self.subTest(count=count):
                replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5B}))))
                replay.send_count = count
                visible = replay.replay([key(0x5B, True), key(0xDD, True),
                                         key(0xDD, False), key(0x5B, False)])
                self.assertEqual([e for e in visible if e[0] == 0x5B],
                                 [(0x5B, True), (0x5B, False)])
                self.assertFalse(replay.state.pressed)

    def test_lower_integrity_injected_events_still_bridge(self):
        replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5B}))))
        visible = replay.replay([key(0x5B, True, 0x02), key(0xDD, True, 0x02),
                                 key(0xDD, False, 0x02), key(0x5B, False, 0x02)])
        self.assertEqual(naked_menu_releases(visible), [])
        self.assertEqual(replay.drain_jobs(), ['start'])

    def test_unmatched_win_combo_and_bare_win_are_unchanged(self):
        for events in ([key(0x5B, True), key(65, True), key(65, False), key(0x5B, False)],
                       [key(0x5B, True), key(0x5B, False)]):
            replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5B}))))
            self.assertEqual(replay.replay(events), [(e[0], e[1]) for e in events])
            self.assertFalse(replay.sent)

    def test_physical_combo_and_unpaired_release_are_unchanged(self):
        for events in ([key(0x5B, True, 0), key(0xDD, True, 0),
                        key(0xDD, False, 0), key(0x5B, False, 0)],
                       [key(0x5B, False)]):
            replay = HookReplay(m.Bindings(None, m.Shortcut(0xDD, frozenset({0x5B}))))
            self.assertEqual(replay.replay(events), [(e[0], e[1]) for e in events])
            self.assertFalse(replay.sent)

    def test_modifier_only_binding_already_swallows_balanced_pair(self):
        replay = HookReplay(m.Bindings(None, m.Shortcut(0, frozenset({0xA5}))))
        self.assertEqual(replay.replay([key(0xA5, True), key(0xA5, False)]), [])
        self.assertFalse(replay.sent)


if __name__ == '__main__':
    unittest.main(verbosity=2)
