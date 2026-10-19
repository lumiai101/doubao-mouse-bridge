import argparse
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time

MODIFIERS = {
    0x0100: (0xA2, 2), 0x0200: (0xA3, 2),
    0x0400: (0xA4, 1), 0x0800: (0xA5, 1),
    0x1000: (0xA0, 4), 0x2000: (0xA1, 4),
    0x4000: (0x5B, 8), 0x8000: (0x5C, 8),
}
GROUPS = {1: frozenset((0xA4, 0xA5)), 2: frozenset((0xA2, 0xA3)),
          4: frozenset((0xA0, 0xA1)), 8: frozenset((0x5B, 0x5C))}
MOD_VKS = frozenset(vk for vk, _ in MODIFIERS.values())
RPC_SHA256 = 'bc3d6fb5dbfa36161c9a6afddb3bde0c4937ee3a3a08bec1ddde71801710b11e'


@dataclass(frozen=True)
class Shortcut:
    main: int
    sides: frozenset[int]
    generic: tuple[frozenset[int], ...] = ()

    def matches(self, pressed):
        required = self.sides | ({self.main} if self.main else set())
        if not required <= pressed or any(not group & pressed for group in self.generic):
            return False
        permitted = self.sides | frozenset().union(*self.generic)
        return not ((pressed & MOD_VKS) - permitted)

    def required_down(self, pressed):
        required = self.sides | ({self.main} if self.main else set())
        return required <= pressed and all(group & pressed for group in self.generic)

    def label(self):
        names = {0xA2: '左Ctrl', 0xA3: '右Ctrl', 0xA4: '左Alt', 0xA5: '右Alt',
                 0xA0: '左Shift', 0xA1: '右Shift', 0x5B: '左Win', 0x5C: '右Win'}
        parts = [names[vk] for vk in sorted(self.sides)]
        parts += [{1: 'Alt', 2: 'Ctrl', 4: 'Shift', 8: 'Win'}[kind]
                  for kind, group in GROUPS.items() if group in self.generic]
        if self.main:
            parts.append(chr(self.main) if 48 <= self.main <= 90 else f'VK{self.main:02X}')
        return '+'.join(parts) or '禁用'


def decode_shortcut(data):
    if not isinstance(data, dict):
        raise ValueError('快捷键不是对象')
    main, flags = data.get('keyCode'), data.get('modifierFlags')
    if type(main) is not int or type(flags) is not int or not 0 <= main <= 254 or not 0 <= flags <= 65535:
        raise ValueError('快捷键字段无效')
    if main in MOD_VKS:
        raise ValueError('修饰键不应位于主键字段')
    if not main and not flags:
        return None
    sides = frozenset(vk for bit, (vk, _) in MODIFIERS.items() if flags & bit)
    generic = []
    for kind, group in GROUPS.items():
        if sides & group and not flags & kind:
            raise ValueError('左右修饰位缺少种类位')
        if flags & kind and not sides & group:
            generic.append(group)
    if flags & 0xF0:
        raise ValueError('未知修饰位')
    return Shortcut(main, sides, tuple(generic))


@dataclass(frozen=True)
class Bindings:
    hold: Shortcut | None
    toggle: Shortcut | None

    def label(self):
        return f'长按 {self.hold.label() if self.hold else "禁用"}；免按 {self.toggle.label() if self.toggle else "禁用"}'


def read_bindings(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if config.get('schemaVersion') != 12:
        raise ValueError('未验证的豆包配置版本')
    voice = config['voice']
    if voice.get('enableVoiceShortcut') is False:
        return Bindings(None, None)
    if voice.get('hasShortcutConflict'):
        raise ValueError('豆包报告快捷键冲突')
    bindings = []
    for key in ('voiceLongPressShortcut', 'voiceShortcut'):
        if voice.get(key + 'Mode') not in (None, 'null'):
            raise ValueError('旧版快捷键格式尚未验证')
        bindings.append(decode_shortcut(voice[key]))
    return Bindings(*bindings)


@dataclass
class InputState:
    bindings: Bindings
    pressed: set[int] = field(default_factory=set)
    physical_modifiers: set[int] = field(default_factory=set)
    swallowed: set[int] = field(default_factory=set)
    pending: tuple[Shortcut, str, float] | None = None
    active: tuple[Shortcut, str] | None = None
    started_at: float = 0.0

    def reset(self, bindings):
        self.bindings = bindings
        self.pending = None
        was_active = self.active is not None
        self.active = None
        return ['cancel'] if was_active else []

    def tick(self, now):
        if self.active and now - self.started_at >= 180:
            self.active = None
            self.pending = None
            self.pressed.clear()
            return ['stop']
        if not self.pending:
            return []
        spec, mode, at = self.pending
        if not spec.matches(self.pressed | self.physical_modifiers):
            self.pending = None
            return []
        if now - at < 0.2:
            return []
        self.pending = None
        self.active = spec, 'hold' if mode == 'dual' else mode
        self.started_at = now
        return ['start']

    def key(self, vk, down, injected, allowed, now):
        if not injected:
            if vk in MOD_VKS:
                (self.physical_modifiers.add if down else self.physical_modifiers.discard)(vk)
                if self.pending and not self.pending[0].matches(self.pressed | self.physical_modifiers):
                    self.pending = None
            return False, []
        repeat = down and vk in self.pressed
        (self.pressed.add if down else self.pressed.discard)(vk)
        suppress = vk in self.swallowed
        if not down:
            self.swallowed.discard(vk)
        actions = []
        if not allowed:
            self.pending = None
            if self.active:
                self.active = None
                actions.append('cancel')
            return suppress, actions
        if self.pending:
            spec, mode, _ = self.pending
            if not spec.required_down(self.pressed):
                self.pending = None
                if mode == 'dual':
                    self.active = spec, 'toggle'
                    self.started_at = now
                    actions.append('start')
            elif not spec.matches(self.pressed | self.physical_modifiers) or vk not in MOD_VKS | {spec.main}:
                self.pending = None
        if self.active:
            spec, mode = self.active
            if mode == 'hold' and not spec.required_down(self.pressed):
                self.active = None
                actions.append('stop')
            elif mode == 'toggle' and down and not repeat:
                if spec.matches(self.pressed | self.physical_modifiers):
                    self.active = None
                    actions.append('stop')
                    self.swallowed.add(vk)
                    suppress = True
                elif vk not in MOD_VKS:
                    self.active = None
                    actions.append('stop')
            return suppress, actions
        if not down or repeat or self.pending or actions:
            return suppress, actions
        hold, toggle = self.bindings.hold, self.bindings.toggle
        for spec, mode in ((hold, 'dual' if hold and hold == toggle else 'hold'), (toggle, 'toggle')):
            if not spec or not spec.matches(self.pressed | self.physical_modifiers):
                continue
            if spec.main and vk != spec.main:
                continue
            if not spec.main and vk not in MOD_VKS:
                continue
            self.swallowed.add(vk)
            suppress = True
            if mode == 'toggle':
                self.active = spec, mode
                self.started_at = now
                actions.append('start')
            else:
                self.pending = spec, mode, now
            break
        return suppress, actions


class GUID(ctypes.Structure):
    _fields_ = [('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD), ('Data4', ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        import uuid
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


class InputProfile(ctypes.Structure):
    _fields_ = [('kind', w.DWORD), ('lang', w.WORD), ('clsid', GUID), ('profile', GUID), ('category', GUID),
                ('substitute', w.HANDLE), ('caps', w.DWORD), ('layout', w.HANDLE), ('flags', w.DWORD)]


class DoubaoProfile:
    def __init__(self):
        self.ole = ctypes.OleDLL('ole32')
        self.ole.CoInitializeEx(None, 2)
        self.closed = False
        self.pointer = ctypes.c_void_p()
        clsid = GUID.parse('33c53a50-f456-4884-b049-85fd643ecfed')
        iid = GUID.parse('71c6e74c-0f28-11d8-a82a-00065b84435c')
        self.category = GUID.parse('34745c63-b2f0-4784-8b67-5e12c8701a31')
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, w.DWORD,
                                            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        result = self.ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(self.pointer))
        if result != 0 or not self.pointer:
            self.ole.CoUninitialize()
            self.closed = True
            raise RuntimeError('无法查询Windows输入法状态')
        self.vtable = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        self.get_active = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID),
                                             ctypes.POINTER(InputProfile))(self.vtable[10])
        self.doubao = bytes(GUID.parse('9d2b2e2b-3c93-4d2f-9d35-6eeb85f0d2b0'))

    def active(self):
        profile = InputProfile()
        result = self.get_active(self.pointer, ctypes.byref(self.category), ctypes.byref(profile))
        return result == 0 and bytes(profile.clsid) == self.doubao

    def close(self):
        if self.closed:
            return
        self.closed = True
        ctypes.WINFUNCTYPE(w.ULONG, ctypes.c_void_p)(self.vtable[2])(self.pointer)
        self.ole.CoUninitialize()


class NativeVoice:
    def __init__(self, install):
        self.directory = Path(install)
        if hashlib.sha256((self.directory / 'rpc.dll').read_bytes()).hexdigest() != RPC_SHA256:
            raise RuntimeError('豆包原生接口版本已变化，未启用')
        self.search = os.add_dll_directory(str(self.directory))
        self.dll = ctypes.CDLL(str(self.directory / 'rpc.dll'))
        self.pipe = rb'\\.\pipe\ObricIme\oime-server'
        self.dll.RpcPipe_SimpleMessage.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.RpcPipe_SimpleMessage.restype = ctypes.c_int
        self.dll.RpcPipe_FocusIn.argtypes = [ctypes.c_char_p]
        self.dll.RpcPipe_FocusIn.restype = None
        self.dll.RpcPipe_RegisterTsfNotifySink.argtypes = [ctypes.c_char_p, w.HWND, ctypes.c_uint64]
        self.dll.RpcPipe_RegisterTsfNotifySink.restype = None
        self.dll.RpcPipe_UnregisterTsfNotifySink.argtypes = [ctypes.c_char_p, w.HWND, ctypes.c_uint64]
        self.dll.RpcPipe_UnregisterTsfNotifySink.restype = None
        self.dll.RpcPipe_PeekVoiceCommitUtf8.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint64), ctypes.c_void_p, ctypes.c_int]
        self.dll.RpcPipe_PeekVoiceCommitUtf8.restype = ctypes.c_int
        self.sink = None
        self.owned = False

    def attach(self, hwnd):
        self.sink = hwnd
        self.dll.RpcPipe_RegisterTsfNotifySink(self.pipe, hwnd, 1)
        logging.info('已注册本程序TSF会话通知窗口')

    def detach(self):
        if self.sink:
            self.dll.RpcPipe_UnregisterTsfNotifySink(self.pipe, self.sink, 1)
            self.sink = None

    def call(self, code, a=0):
        result = self.dll.RpcPipe_SimpleMessage(self.pipe, code, a, 0)
        logging.info('RPC code=%s result=%s', code, result)
        return result

    def command(self, action):
        if action == 'start':
            current = self.call(1006)
            if current != 0:
                raise RuntimeError('已有语音会话，不接管')
            self.owned = False
            self.dll.RpcPipe_FocusIn(self.pipe)
            if self.call(1004):
                raise RuntimeError('豆包语音激活失败')
            self.owned = True
            try:
                if self.call(1007) or self.call(1012, 1):
                    raise RuntimeError('豆包语音启动失败')
            except Exception:
                self.command('cancel')
                raise
        elif action in ('stop', 'cancel') and self.owned:
            if action == 'cancel':
                if self.call(1013):
                    raise RuntimeError('豆包语音取消失败')
                self.owned = False
                return
            if self.call(1008):
                self.command('cancel')
                raise RuntimeError('豆包语音结束失败')
            for _ in range(150):
                current = self.call(1006)
                if current == 0:
                    self.owned = False
                    return
                time.sleep(0.1)
            logging.warning('结果仍由豆包处理，不丢弃、不重复启动')


def find_install():
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'DoubaoIME' / 'versions'
    candidates = sorted(base.glob('v*'), key=lambda p: tuple(int(n) for n in p.name[1:].split('.')), reverse=True)
    for item in candidates:
        if (item / 'rpc.dll').is_file() and (item / 'ImeService.exe').is_file():
            return item
    raise RuntimeError('没有找到已安装的豆包输入法')


def main():
    import tkinter as tk
    from pynput import keyboard
    parser = argparse.ArgumentParser(description='只读跟随豆包快捷键的罗技模拟键适配')
    parser.add_argument('--config', default=str(Path(os.environ['APPDATA']) / 'DoubaoIme/conf/config.json'))
    args = parser.parse_args()
    logging.basicConfig(filename=Path(__file__).with_suffix('.log'), level=logging.INFO,
                        encoding='utf-8', format='%(asctime)s %(levelname)s %(message)s')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [w.LPVOID, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    instance = kernel.CreateMutexW(None, False, r'Local\DoubaoMouseVoiceBridge')
    instance_error = ctypes.get_last_error()
    if not instance:
        raise ctypes.WinError(instance_error)
    if instance_error == 183:
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle(instance)
        logging.info('适配已经运行，未重复安装监听')
        return
    logging.info('启动 pid=%s', os.getpid())
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = w.HWND
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    native = NativeVoice(find_install())
    profile = DoubaoProfile()
    state = InputState(Bindings(None, None))
    lock = threading.RLock()
    jobs = queue.Queue()
    errors = queue.Queue()
    closing = threading.Event()
    allowed = False
    paused = False
    target = 0
    fingerprint = None
    failure = None
    ui = tk.Tk()
    ui.title('豆包鼠标语音适配')
    ui.geometry('570x175')
    ui.update_idletasks()
    native.attach(ui.winfo_id())
    text = tk.StringVar(value='正在读取豆包设置')
    tk.Label(ui, textvariable=text, wraplength=540, justify='left', padx=12, pady=12).pack(fill='x')
    tk.Label(ui, text='只接管匹配设置的模拟快捷键。实体键盘透传。退出立即停用。').pack()

    def send(actions):
        for action in actions:
            logging.info('会话命令 %s', action)
            jobs.put(action)

    def worker():
        while True:
            action = jobs.get()
            try:
                if action == 'exit':
                    try:
                        native.command('cancel')
                    except Exception:
                        logging.exception('退出时语音取消失败')
                    return
                native.command(action)
            except Exception as exc:
                logging.exception('语音命令失败')
                errors.put(str(exc))
            finally:
                jobs.task_done()

    threading.Thread(target=worker, name='DoubaoNativeVoice', daemon=True).start()

    def event_filter(msg, data):
        if msg not in (0x100, 0x101, 0x104, 0x105):
            return False
        injected = bool(data.flags & 0x12)
        vk = int(data.vkCode)
        if vk == 0x10:
            vk = 0xA1 if data.scanCode == 0x36 else 0xA0
        elif vk in (0x11, 0x12):
            vk = (0xA3 if vk == 0x11 else 0xA5) if data.flags & 1 else (0xA2 if vk == 0x11 else 0xA4)
        with lock:
            suppressed, actions = state.key(vk, msg in (0x100, 0x104), injected, allowed and not paused, time.monotonic())
            if injected and any(spec and (vk == spec.main or not spec.main and vk in spec.sides) for spec in (state.bindings.hold, state.bindings.toggle)):
                logging.info('匹配候选 vk=%s down=%s allowed=%s paused=%s suppress=%s', vk, msg in (0x100, 0x104), allowed, paused, suppressed)
            send(actions)
        if suppressed:
            listener.suppress_event()
        return False

    listener = keyboard.Listener(win32_event_filter=event_filter)
    listener.start()
    listener.wait()

    def reset(bindings):
        with lock:
            send(state.reset(bindings))

    def toggle_pause():
        nonlocal paused
        paused = not paused
        reset(state.bindings)
        if not paused:
            fingerprint_reset()
        pause_button.configure(text='恢复' if paused else '暂停')

    def fingerprint_reset():
        nonlocal fingerprint
        fingerprint = None

    def shutdown():
        nonlocal allowed
        if closing.is_set():
            return
        allowed = False
        closing.set()
        listener.stop()
        jobs.put('exit')
        text.set('正在结束本程序的语音会话并卸载监听')
        poll_exit()

    def poll_exit():
        if jobs.unfinished_tasks == 0:
            logging.info('正常退出')
            native.detach()
            profile.close()
            ui.destroy()
        else:
            ui.after(50, poll_exit)

    buttons = tk.Frame(ui)
    buttons.pack(pady=10)
    pause_button = tk.Button(buttons, text='暂停', command=toggle_pause, width=12)
    pause_button.pack(side='left', padx=8)
    tk.Button(buttons, text='退出', command=shutdown, width=12).pack(side='left', padx=8)
    ui.protocol('WM_DELETE_WINDOW', shutdown)

    def tick():
        nonlocal fingerprint, allowed, target, profile, failure
        if closing.is_set():
            return
        try:
            stat = Path(args.config).stat()
            current = stat.st_mtime_ns, stat.st_size
            if current != fingerprint or failure is not None:
                bindings = read_bindings(args.config)
                reset(bindings)
                fingerprint = current
                failure = None
                text.set(bindings.label() + '\n更换时只需修改豆包与 Logi Options+ 设置。')
                logging.info('设置已重载 %s', bindings.label())
            hwnd = user.GetForegroundWindow()
            pid = w.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            import subprocess
            setting_pids = getattr(tick, 'setting_pids', set())
            if time.monotonic() - getattr(tick, 'last_pid_check', 0) > 2:
                output = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq DoubaoImeSettings.exe', '/FO', 'CSV', '/NH'],
                                        capture_output=True, creationflags=0x08000000, check=True).stdout
                import csv
                setting_pids = {int(row[1]) for row in csv.reader(output.decode('mbcs').splitlines())
                                if len(row) > 1 and row[1].isdigit()}
                tick.setting_pids, tick.last_pid_check = setting_pids, time.monotonic()
            if getattr(tick, 'profile_foreground', None) != hwnd:
                replacement = DoubaoProfile()
                profile.close()
                profile = replacement
                tick.profile_foreground = hwnd
            is_doubao = profile.active()
            allowed = bool(hwnd) and pid.value not in setting_pids and pid.value != os.getpid() and is_doubao
            gate = (pid.value, is_doubao, allowed)
            if getattr(tick, 'gate', None) != gate:
                logging.info('输入门控 pid=%s doubao=%s allowed=%s', *gate)
                tick.gate = gate
            with lock:
                if target and hwnd != target and state.active:
                    send(state.reset(state.bindings))
                if state.pending or state.active:
                    target = target or hwnd
                else:
                    target = 0
                if not allowed or paused:
                    send(state.reset(state.bindings))
                else:
                    send(state.tick(time.monotonic()))
            if not listener.is_alive():
                raise RuntimeError('输入监听已停止')
            if not errors.empty():
                raise RuntimeError(errors.get_nowait())
        except Exception as exc:
            allowed = False
            reset(Bindings(None, None))
            text.set('已停用，未继续拦截按键。' + str(exc))
            if failure != str(exc):
                logging.error('停用 %s', exc)
            failure = str(exc)
        ui.after(1000 if failure else 50, tick)

    ui.after(0, tick)
    try:
        ui.mainloop()
    finally:
        if not closing.is_set():
            listener.stop()
            jobs.put('exit')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('启动或运行失败')
        ctypes.windll.user32.MessageBoxW(None, '豆包鼠标适配未启用。' + str(exc), '豆包鼠标语音适配', 0x10)
