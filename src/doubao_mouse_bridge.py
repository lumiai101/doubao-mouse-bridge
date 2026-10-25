import argparse
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass, field
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sqlite3
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
MENU_MODIFIERS = frozenset((0x5B, 0x5C, 0xA4, 0xA5))
MENU_INPUT_TAG = 0x44424D42


class KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_uint16), ('wScan', ctypes.c_uint16),
                ('dwFlags', ctypes.c_uint32), ('time', ctypes.c_uint32),
                ('dwExtraInfo', ctypes.c_size_t)]


class MouseInput(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_int32), ('dy', ctypes.c_int32),
                ('mouseData', ctypes.c_uint32), ('dwFlags', ctypes.c_uint32),
                ('time', ctypes.c_uint32), ('dwExtraInfo', ctypes.c_size_t)]


class InputUnion(ctypes.Union):
    _fields_ = [('ki', KeyboardInput), ('mi', MouseInput)]


class KeyboardEvent(ctypes.Structure):
    _anonymous_ = ('event',)
    _fields_ = [('type', ctypes.c_uint32), ('event', InputUnion)]


def mask_menu_release(user, vk, scan, extended):
    # 必须把屏蔽键和释放放在同一批；仅在主键处补发会排到原批次的释放之后。
    inputs = (KeyboardEvent * 3)()
    for event, key, flags in zip(inputs, (0xE8, 0xE8, vk), (0, 2, 2 | extended)):
        event.type = 1
        event.ki = KeyboardInput(key, scan if key == vk else 0, flags, 0, MENU_INPUT_TAG)
    return user.SendInput(3, inputs, ctypes.sizeof(KeyboardEvent)) == 3


RPC_SHA256 = 'bc3d6fb5dbfa36161c9a6afddb3bde0c4937ee3a3a08bec1ddde71801710b11e'
ALLOWED_RPC_SHA256 = frozenset({
    'bc3d6fb5dbfa36161c9a6afddb3bde0c4937ee3a3a08bec1ddde71801710b11e',  # v0.9.2.7
    'b1a609d662d807537f366b5c8d33ab20eba2701c0e207f67a0f664ea9a2c42d5',  # v0.9.3.19
})


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
            keys = {0x0D: 'Enter', 0x20: 'Space', 0xBA: ';', 0xBB: '=',
                    0xBC: ',', 0xBD: '-', 0xBE: '.', 0xBF: '/', 0xC0: '`',
                    0xDB: '[', 0xDC: '\\', 0xDD: ']', 0xDE: "'"}
            parts.append(keys.get(self.main, chr(self.main) if 48 <= self.main <= 90 else f'VK{self.main:02X}'))
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


@dataclass(frozen=True)
class SideButton:
    device: str
    shortcut: Shortcut | None
    reason: str = ''

    def label(self):
        return f'{self.device} 下方/后侧键: {self.shortcut.label() if self.shortcut else self.reason}'


def decode_logi_shortcut(keystroke):
    key = keystroke.get('virtualKeyId')
    keys = {'VK_RIGHT_BRACKET': 0xDD, 'VK_LEFT_BRACKET': 0xDB,
            'VK_NUMPAD_ENTER': 13, 'VK_RETURN': 13, 'VK_ENTER': 13,
            'VK_SPACE': 32, 'VK_TAB': 9, 'VK_ESCAPE': 27,
            'VK_BACKSPACE': 8, 'VK_DELETE': 46, 'VK_INSERT': 45,
            'VK_HOME': 36, 'VK_END': 35, 'VK_PAGE_UP': 33, 'VK_PAGE_DOWN': 34,
            'VK_LEFT': 37, 'VK_UP': 38, 'VK_RIGHT': 39, 'VK_DOWN': 40,
            'VK_SEMICOLON': 0xBA, 'VK_EQUAL': 0xBB, 'VK_COMMA': 0xBC,
            'VK_MINUS': 0xBD, 'VK_PERIOD': 0xBE, 'VK_SLASH': 0xBF,
            'VK_GRAVE': 0xC0, 'VK_BACKSLASH': 0xDC, 'VK_QUOTE': 0xDE}
    keys.update({f'VK_{chr(vk)}': vk for vk in (*range(48, 58), *range(65, 91))})
    keys.update({f'VK_F{n}': 111 + n for n in range(1, 25)})
    modifiers = {224: 0xA2, 225: 0xA0, 226: 0xA4, 227: 0x5B,
                 228: 0xA3, 229: 0xA1, 230: 0xA5, 231: 0x5C}
    values = keystroke.get('modifiers', [])
    if not isinstance(values, list) or any(type(value) is not int or value not in modifiers for value in values):
        raise ValueError('尚不支持的罗技修饰键')
    sides = frozenset(modifiers[value] for value in values)
    modifier_keys = {'VK_LCONTROL': 0xA2, 'VK_RCONTROL': 0xA3,
                     'VK_LSHIFT': 0xA0, 'VK_RSHIFT': 0xA1,
                     'VK_LMENU': 0xA4, 'VK_RMENU': 0xA5,
                     'VK_LWIN': 0x5B, 'VK_RWIN': 0x5C}
    if key is None and sides and 'code' not in keystroke:
        return Shortcut(0, sides)
    if isinstance(key, str) and key in modifier_keys:
        return Shortcut(0, sides | {modifier_keys[key]})
    if not isinstance(key, str) or key not in keys:
        raise ValueError('尚不支持的罗技按键格式')
    return Shortcut(keys[key], sides)


def parse_logi_buttons(data, metadata):
    devices = data['ever_connected_devices']['devices']
    profiles = [data[key] for key in data['profile_keys']]
    for profile in profiles:
        if not isinstance(profile.get('assignments'), list) or any(
                not isinstance(item, dict) or not isinstance(item.get('slotId'), str)
                for item in profile['assignments']):
            raise ValueError('罗技应用设置结构未确认')
    defaults = [profile for profile in profiles if profile.get('name') == 'PROFILE_NAME_DEFAULT']
    if len(defaults) != 1:
        raise ValueError('无法确定罗技全局按键设置')
    catalog = {device['slotPrefix']: device for device in metadata['devices']}
    results, seen = [], set()
    for device in devices:
        if device.get('deviceType') not in ('MOUSE', 'TRACKBALL'):
            continue
        prefix = device['slotPrefix']
        if prefix in seen:
            continue
        seen.add(prefix)
        info = catalog.get(prefix, {})
        name = info.get('displayName', '未识别型号')
        controls = info.get('capabilities', {}).get('specialKeys', {}).get('programmable', [])
        # HID++ 后侧键为 83；只有本机型号描述也声明该控制键时才使用。
        if 83 not in controls:
            results.append(SideButton(name, None, '本机设备描述无法确认后侧键'))
            continue
        slot = prefix + '_c83'
        assignments = [item for item in defaults[0]['assignments'] if item.get('slotId') == slot]
        if len(assignments) != 1:
            results.append(SideButton(name, None, '没有唯一的后侧键绑定，请在罗技设置检查'))
            continue
        overrides = any(profile.get('name') != 'PROFILE_NAME_DEFAULT' and
                        any(item.get('slotId') == slot for item in profile.get('assignments', []))
                        for profile in profiles)
        try:
            macro = assignments[0]['card']['macro']
            if macro.get('type') != 'KEYSTROKE':
                raise ValueError('后侧键未绑定单个组合键')
            shortcut = decode_logi_shortcut(macro['keystroke'])
            reason = '存在应用专属设置，当前映射未确认' if overrides else ''
            results.append(SideButton(name, shortcut, reason))
        except (KeyError, TypeError, AttributeError, ValueError):
            results.append(SideButton(name, None, '后侧键不是可识别的单个组合键'))
    return results


def read_logi_buttons(db_path=None, metadata_path=None):
    db = Path(db_path) if db_path is not None else Path(os.environ.get('LOCALAPPDATA', '')) / 'LogiOptionsPlus/settings.db'
    metadata = Path(metadata_path) if metadata_path is not None else Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'LogiOptionsPlus/data/devices/devices_initial.json'
    if not db.is_file():
        return []
    try:
        connection = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)
        try:
            rows = connection.execute('SELECT file FROM data LIMIT 2').fetchall()
        finally:
            connection.close()
        if len(rows) != 1:
            raise ValueError('罗技数据库结构变化')
        return parse_logi_buttons(json.loads(rows[0][0]), json.loads(metadata.read_text(encoding='utf-8-sig')))
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError):
        return [SideButton('罗技鼠标', None, '设置暂不可读或格式未确认，请打开 Logi Options+ 检查')]


def side_button_status(buttons, bindings):
    if not buttons:
        return '侧键未确认', '未读取到罗技鼠标下方/后侧键，请打开 Logi Options+ 检查。'
    lines = []
    matched = True
    for button in buttons:
        modes = []
        if button.shortcut:
            pressed = button.shortcut.sides | {button.shortcut.main}
            for name, shortcut in (('长按', bindings.hold), ('免按', bindings.toggle)):
                if shortcut and shortcut.main == button.shortcut.main and shortcut.matches(pressed):
                    modes.append(name)
        matched = matched and bool(modes) and not button.reason
        detail = button.reason or ('匹配豆包' + '/'.join(modes) if modes else '与豆包快捷键不一致')
        lines.append(button.label() + ('；' + detail if button.shortcut else ''))
    return ('按键设置匹配' if matched else '侧键需检查'), '\n'.join(lines)


@dataclass
class InputState:
    bindings: Bindings
    pressed: set[int] = field(default_factory=set)
    physical_modifiers: set[int] = field(default_factory=set)
    swallowed: set[int] = field(default_factory=set)
    menu_modifiers: set[int] = field(default_factory=set)
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

    def key(self, vk, down, injected, allowed, now, voice_enabled=True):
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
        if not voice_enabled:
            # 故障锁存只保留已匹配模拟键的吞键，不建立任何语音会话。
            self.pending = None
            self.active = None
            if allowed and down and not repeat:
                for spec in (self.bindings.hold, self.bindings.toggle):
                    if (spec and spec.matches(self.pressed | self.physical_modifiers)
                            and (vk == spec.main if spec.main else vk in MOD_VKS)):
                        self.swallowed.add(vk)
                        suppress = True
                        break
            return suppress, []
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


@dataclass
class PhysicalHold:
    shortcut: Shortcut | None = None
    pressed: set[int] = field(default_factory=set)
    pending_at: float | None = None
    active: bool = False
    blocked: bool = False

    def reset(self, shortcut):
        self.shortcut = shortcut
        self.pending_at = None
        self.blocked = False
        was_active, self.active = self.active, False
        return ['stop-fallback'] if was_active else []

    def key(self, vk, down, allowed, now):
        repeat = down and vk in self.pressed
        (self.pressed.add if down else self.pressed.discard)(vk)
        if not self.pressed:
            self.blocked = False
        if self.shortcut and down and vk not in MOD_VKS | {self.shortcut.main}:
            self.blocked = True
        if self.blocked or not allowed or not self.shortcut or not self.shortcut.matches(self.pressed):
            self.pending_at = None
            if self.active:
                self.active = False
                return ['stop-fallback']
        elif down and not repeat and not self.active and self.pending_at is None:
            self.pending_at = now
        return []

    def tick(self, now):
        if self.pending_at is not None and now - self.pending_at >= 0.6:
            self.pending_at = None
            self.active = True
            return ['start-fallback']
        return []


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
        if hashlib.sha256((self.directory / 'rpc.dll').read_bytes()).hexdigest() not in ALLOWED_RPC_SHA256:
            raise RuntimeError('豆包原生接口版本已变化，未启用')
        self.search = os.add_dll_directory(str(self.directory))
        self.dll = ctypes.CDLL(str(self.directory / 'rpc.dll'))
        self.pipe = rb'\\.\pipe\ObricIme\oime-server'
        self.dll.RpcPipe_SimpleMessage.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.RpcPipe_SimpleMessage.restype = ctypes.c_int
        self.dll.RpcPipe_FocusIn.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.dll.RpcPipe_FocusIn.restype = None
        self.dll.RpcPipe_FocusOut.argtypes = [ctypes.c_char_p]
        self.dll.RpcPipe_FocusOut.restype = None
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
            self.dll.RpcPipe_FocusOut(self.pipe)
            self.dll.RpcPipe_UnregisterTsfNotifySink(self.pipe, self.sink, 1)
            self.sink = None
            logging.info('已释放本程序原生焦点与TSF通知')

    def call(self, code, a=0):
        result = self.dll.RpcPipe_SimpleMessage(self.pipe, code, a, 0)
        logging.info('RPC code=%s result=%s', code, result)
        return result

    def command(self, action):
        if action == 'check-idle':
            if self.call(1006) == 0:
                self.owned = False
        elif action == 'start':
            current = self.call(1006)
            if current != 0:
                raise RuntimeError('已有语音会话，不接管')
            self.owned = False
            self.dll.RpcPipe_FocusIn(self.pipe, None)
            if self.call(1004):
                raise RuntimeError('豆包语音激活失败')
            self.owned = True
            try:
                if self.call(1007) or self.call(1012, 1):
                    raise RuntimeError('豆包语音启动失败')
                deadline = time.monotonic() + 3
                while True:
                    current = self.call(1006)
                    if current == 3:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'豆包启动后未确认进入录音状态 (state={current})；请核对豆包与系统麦克风提示，禁止自动重试')
                    time.sleep(0.1)
            except Exception as exc:
                try:
                    self.command('cancel')
                except Exception as cleanup:
                    raise RuntimeError(f'{exc}；取消失败: {cleanup}；请手动核对麦克风状态') from exc
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


def native_session(connection, install):
    logging.basicConfig(handlers=[RotatingFileHandler(Path(__file__).with_suffix('.log'),
                        maxBytes=1048576, backupCount=2, encoding='utf-8')], level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
    user.CreateWindowExW.restype = w.HWND
    user.DestroyWindow.argtypes = [w.HWND]
    user.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
    user.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
    hwnd = user.CreateWindowExW(0, 'STATIC', 'DoubaoVoiceSession', 0, 0, 0, 0, 0,
                                w.HWND(-3), None, None, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    native = NativeVoice(install)
    finished = threading.Event()

    def commands():
        try:
            while True:
                action = connection.recv()
                try:
                    if action == 'start-fallback':
                        if native.call(1006) != 0:
                            connection.send((True, True, ''))
                            return
                        action = 'start'
                    if action == 'start' and not native.sink:
                        native.attach(hwnd)
                    native.command(action)
                    ending = action in ('stop', 'cancel', 'check-idle') and not native.owned
                    connection.send((True, ending, ''))
                    if ending:
                        return
                except Exception as exc:
                    try:
                        native.command('cancel')
                    finally:
                        connection.send((False, True, str(exc)))
                    return
        except EOFError:
            if native.owned:
                native.command('cancel')
        finally:
            finished.set()

    threading.Thread(target=commands, daemon=True).start()

    message = w.MSG()
    try:
        while not finished.wait(0.01):
            while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
    finally:
        native.detach()
        user.DestroyWindow(hwnd)
        connection.close()


class VoiceSession:
    def __init__(self, install):
        self.install = str(install)
        self.process = None
        self.connection = None

    def command(self, action):
        import multiprocessing
        if self.process is None:
            if action not in ('start', 'start-fallback'):
                return False
            context = multiprocessing.get_context('spawn')
            self.connection, child = context.Pipe()
            self.process = context.Process(target=native_session, args=(child, self.install), daemon=True)
            self.process.start()
            child.close()
        self.connection.send(action)
        if not self.connection.poll(25):
            self.process.terminate()
            self.process.join(2)
            self.connection.close()
            self.process = None
            raise RuntimeError('豆包接口响应超时，语音子进程已结束；请检查豆包是否仍在录音')
        success, ending, error = self.connection.recv()
        if ending:
            self.process.join(3)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(2)
            self.connection.close()
            self.process = None
            logging.info('本次语音子进程已退出，原生连接已释放')
        if not success:
            raise RuntimeError(error)
        return not ending

    def close(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(2)
            if self.process.is_alive():
                raise RuntimeError('本程序语音子进程未退出，请检查豆包麦克风')
            self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None


def find_install():
    base = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'DoubaoIME' / 'versions'
    candidates = sorted(base.glob('v*'), key=lambda p: tuple(int(n) for n in p.name[1:].split('.')), reverse=True)
    for item in candidates:
        if (item / 'rpc.dll').is_file() and (item / 'ImeService.exe').is_file():
            return item
    raise RuntimeError('没有找到已安装的豆包输入法')


def settings_target(kind):
    if kind == 'doubao':
        candidates = [Path(os.environ.get('LOCALAPPDATA', '')) / 'Doubao/Application/Doubao.exe']
    elif kind == 'ime':
        candidates = [find_install() / 'DoubaoImeSettings.exe']
    elif kind == 'logi':
        candidates = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'LogiOptionsPlus/logioptionsplus.exe']
    else:
        raise ValueError('未知设置入口')
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError('未找到软件入口，请从开始菜单打开对应软件。')


def open_settings(kind):
    path = str(settings_target(kind))
    if kind == 'ime':
        os.startfile(path, arguments='--open-tab voice --highlight-item global_voice')
    else:
        os.startfile(path)


def create_tray_icon_image(state='ready'):
    from PIL import Image, ImageDraw
    # 先按四倍尺寸绘制，再缩小，让托盘里的轮廓和声波保持平滑。
    img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink, white = '#16435B', '#FFFFFF'
    d.rounded_rectangle((24, 16, 168, 240), radius=72, fill=ink, outline=white, width=10)
    d.rounded_rectangle((86, 48, 106, 92), radius=10, fill=white)
    d.arc((130, 64, 206, 156), start=305, end=55, fill=ink, width=22)
    d.arc((130, 64, 206, 156), start=305, end=55, fill=white, width=10)
    d.arc((130, 40, 242, 180), start=305, end=55, fill=ink, width=22)
    d.arc((130, 40, 242, 180), start=305, end=55, fill=white, width=10)

    if state != 'ready':
        colors = {'paused': '#DB961B', 'attention': '#E9AC32', 'unconfirmed': '#7957A8'}
        d.ellipse((152, 156, 252, 256), fill=white)
        d.ellipse((160, 164, 244, 248), fill=colors.get(state, '#667780'))
        if state == 'paused':
            d.rounded_rectangle((182, 184, 194, 228), radius=3, fill=ink)
            d.rounded_rectangle((210, 184, 222, 228), radius=3, fill=ink)
        elif state == 'attention':
            d.ellipse((190, 194, 214, 218), fill=ink)
        else:
            d.ellipse((184, 188, 220, 224), outline=white, width=8)
    return img.resize((64, 64), Image.Resampling.LANCZOS)


def build_tray_menu(action_handler, get_status_label, get_pause_label):
    import pystray
    return pystray.Menu(
        pystray.MenuItem(lambda item: get_status_label(), lambda icon, item: None, enabled=False),
        pystray.MenuItem('打开设置', lambda icon, item: action_handler('show_window'), default=True),
        pystray.MenuItem(lambda item: get_pause_label(), lambda icon, item: action_handler('toggle_pause')),
        pystray.MenuItem('重新检查', lambda icon, item: action_handler('recheck')),
        pystray.MenuItem('退出', lambda icon, item: action_handler('exit'))
    )


def main():
    import tkinter as tk
    from tkinter import messagebox
    from pynput import keyboard
    parser = argparse.ArgumentParser(description='只读跟随豆包快捷键的罗技模拟键适配')
    parser.add_argument('--config', default=str(Path(os.environ['APPDATA']) / 'DoubaoIme/conf/config.json'))
    parser.add_argument('--background', action='store_true', help='后台运行，不显示控制窗口')
    parser.add_argument('--stop', action='store_true', help='请求正在运行的适配正常退出')
    args = parser.parse_args()
    logging.basicConfig(handlers=[RotatingFileHandler(Path(__file__).with_suffix('.log'),
                        maxBytes=1048576, backupCount=2, encoding='utf-8')], level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [w.LPVOID, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CreateEventW.argtypes = [w.LPVOID, w.BOOL, w.BOOL, w.LPCWSTR]
    kernel.CreateEventW.restype = w.HANDLE
    kernel.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    kernel.OpenEventW.restype = w.HANDLE
    kernel.SetEvent.argtypes = [w.HANDLE]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    stop_name = r'Local\DoubaoMouseVoiceBridgeStop'
    show_name = r'Local\DoubaoMouseVoiceBridgeShowUI'
    if args.stop:
        handle = kernel.OpenEventW(2, False, stop_name)
        if handle:
            kernel.SetEvent(handle)
            kernel.CloseHandle(handle)
        return
    instance = kernel.CreateMutexW(None, False, r'Local\DoubaoMouseVoiceBridge')
    instance_error = ctypes.get_last_error()
    if not instance:
        raise ctypes.WinError(instance_error)
    if instance_error == 183:
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle(instance)
        handle = kernel.OpenEventW(2, False, show_name)
        if handle:
            kernel.SetEvent(handle)
            kernel.CloseHandle(handle)
            logging.info('适配已在运行，已请求显示现有设置窗口')
        else:
            logging.warning('适配已在运行（旧版无显示事件），未安装重复监听')
            ctypes.windll.user32.MessageBoxW(
                None,
                '豆包鼠标语音适配已经在运行中。\n\n由于旧版本未支持唤醒显示事件，未重复安装监听。\n如需重启或更新，请使用 --stop 退出旧实例。',
                '豆包鼠标语音适配',
                0x30
            )
        return
    stop_event = kernel.CreateEventW(None, True, False, stop_name)
    if not stop_event:
        raise ctypes.WinError(ctypes.get_last_error())
    show_event = kernel.CreateEventW(None, False, False, show_name)
    if not show_event:
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    logging.info('启动 pid=%s', os.getpid())
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = w.HWND
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user.SendInput.argtypes = [w.UINT, ctypes.POINTER(KeyboardEvent), ctypes.c_int]
    user.SendInput.restype = w.UINT
    native = VoiceSession(find_install())
    profile = DoubaoProfile()
    state = InputState(Bindings(None, None))
    physical = PhysicalHold()
    lock = threading.RLock()
    physical_events = queue.SimpleQueue()
    jobs = queue.Queue()
    errors = queue.Queue()
    closing = threading.Event()
    allowed = False
    paused = False
    target = 0
    fingerprint = None
    failure = None
    native_fault = threading.Event()
    tray_failed = False
    tray_icon = None
    tray_thread = None
    tray_ready = threading.Event()
    tray_started_at = time.monotonic()
    tray_queue = queue.SimpleQueue()
    tray_status_label = '状态: 初始化中'
    side_results = queue.SimpleQueue()
    side_buttons = []

    def watch_side_button():
        while not closing.is_set():
            side_results.put(read_logi_buttons())
            closing.wait(2)

    ui = tk.Tk()
    ui.title('豆包鼠标语音适配')
    ui.geometry('660x490')
    ui.resizable(True, True)
    ui.withdraw()
    ui.update_idletasks()

    status_var = tk.StringVar(value='适配状态: 正在初始化...')
    hold_var = tk.StringVar(value='长按模式: 读取中...')
    toggle_var = tk.StringVar(value='免按模式: 读取中...')
    diag_var = tk.StringVar(value='只接管匹配设置的模拟快捷键。实体键盘透传。退出立即停用。')

    tk.Label(ui, textvariable=status_var, font=('Segoe UI', 10, 'bold'), anchor='w').pack(fill='x', padx=15, pady=(10, 2))
    tk.Label(ui, text='监听就绪 ≠ 侧键匹配 ≠ 语音成功；下方分别显示检查结果。', fg='#666666', font=('Segoe UI', 8), anchor='w').pack(fill='x', padx=15, pady=(0, 6))

    kf = tk.LabelFrame(ui, text='豆包快捷键 (只读跟随)', padx=10, pady=6)
    kf.pack(fill='x', padx=15, pady=4)
    tk.Label(kf, textvariable=hold_var, anchor='w').pack(fill='x')
    tk.Label(kf, textvariable=toggle_var, anchor='w').pack(fill='x')
    tk.Label(kf, text='修改快捷键请在豆包与 Logi Options+ 设置中调整，本程序自动同步，不做第二套编辑器。', fg='#666666', font=('Segoe UI', 8), anchor='w').pack(fill='x', pady=(4, 0))

    side_var = tk.StringVar(value='正在读取罗技下方/后侧键…')
    sf = tk.LabelFrame(ui, text='罗技下方/后侧键（历史设备设置，不代表当前已连接）', padx=10, pady=6)
    sf.pack(fill='x', padx=15, pady=4)
    tk.Label(sf, textvariable=side_var, wraplength=600, justify='left', anchor='w').pack(fill='x')

    def launch_settings(kind):
        try:
            open_settings(kind)
        except (OSError, RuntimeError) as exc:
            messagebox.showwarning('无法打开', str(exc), parent=ui)

    links = tk.Frame(ui)
    links.pack(fill='x', padx=15, pady=4)
    for label, kind in (('打开豆包', 'doubao'), ('豆包输入法设置', 'ime'), ('罗技按键设置', 'logi')):
        tk.Button(links, text=label, command=lambda kind=kind: launch_settings(kind)).pack(side='left', padx=(0, 8))
    tk.Label(ui, text='设置入口打开后：豆包输入法 → 语音输入；Logi Options+ → 鼠标 → 下方/后侧键。',
             fg='#666666', font=('Segoe UI', 8), anchor='w').pack(fill='x', padx=15)

    df = tk.LabelFrame(ui, text='运行诊断与故障提示', padx=10, pady=6)
    df.pack(fill='x', padx=15, pady=4)
    tk.Label(df, textvariable=diag_var, wraplength=550, justify='left', anchor='w').pack(fill='x')

    buttons = tk.Frame(ui)
    buttons.pack(pady=10)
    pause_button = tk.Button(buttons, text='暂停适配', width=10)
    pause_button.pack(side='left', padx=6)
    recheck_button = tk.Button(buttons, text='重新检查', width=10)
    recheck_button.pack(side='left', padx=6)
    hide_button = tk.Button(buttons, text='隐藏到托盘', width=10)
    hide_button.pack(side='left', padx=6)
    exit_button = tk.Button(buttons, text='退出程序', width=10)
    exit_button.pack(side='left', padx=6)

    def show_window():
        ui.deiconify()
        ui.lift()
        ui.attributes('-topmost', True)
        ui.after(50, lambda: ui.attributes('-topmost', False))

    def hide_window():
        if tray_failed or not tray_ready.is_set():
            logging.info('托盘未就绪，保持可见控制窗口不隐藏')
            return
        ui.withdraw()

    def get_pause_label():
        return '恢复适配' if paused else '暂停适配'

    def update_tray_state(status_label, state_name):
        nonlocal tray_status_label
        tray_status_label = f'状态: {status_label}'
        if tray_icon and tray_ready.is_set() and not tray_failed:
            try:
                tray_icon.title = f'豆包鼠标语音适配 - {status_label}'
                tray_icon.icon = create_tray_icon_image(state_name)
                tray_icon.update_menu()
            except Exception:
                logging.exception('更新托盘图标失败')
                tray_queue.put('tray_failed')

    def tray_setup(icon):
        try:
            if closing.is_set() or tray_failed:
                icon.stop()
                return
            icon.visible = True
            tray_ready.set()
            tray_queue.put('tray_ready')
        except Exception:
            logging.exception('显示托盘图标失败')
            tray_queue.put('tray_failed')

    def run_tray():
        try:
            tray_icon.run(setup=tray_setup)
        except Exception:
            logging.exception('托盘消息循环失败')
        finally:
            if not closing.is_set():
                tray_queue.put('tray_failed')

    def tray_fallback():
        nonlocal tray_failed
        tray_failed = True
        hide_button.configure(state='disabled')
        show_window()
        diag_var.set('托盘不可用，已保留可见控制窗；请使用窗口内按钮管理或退出。')

    try:
        import pystray
        tray_menu = build_tray_menu(
            lambda act: tray_queue.put(act),
            lambda: tray_status_label,
            get_pause_label
        )
        tray_icon = pystray.Icon(
            'DoubaoVoiceBridge',
            create_tray_icon_image('attention'),
            '豆包鼠标语音适配',
            menu=tray_menu
        )
        tray_thread = threading.Thread(target=run_tray, name='DoubaoTray', daemon=True)
        tray_thread.start()
    except Exception as exc:
        logging.warning('启动系统托盘失败，回退到可见控制窗口: %s', exc)
        tray_fallback()

    def send(actions):
        if not native_fault.is_set():
            for action in actions:
                jobs.put_nowait(action)

    def worker():
        fallback_owned = False
        while True:
            action = jobs.get()
            try:
                if action == 'exit':
                    if not native_fault.is_set():
                        try:
                            native.command('cancel')
                        except Exception as exc:
                            native_fault.set()
                            errors.put(str(exc))
                            logging.exception('退出时语音取消失败')
                    try:
                        native.close()
                    except Exception as exc:
                        native_fault.set()
                        errors.put(str(exc))
                        logging.exception('语音子进程清理失败')
                    return
                if closing.is_set() or native_fault.is_set():
                    continue
                logging.info('会话命令 %s', action)
                if action in ('start', 'start-fallback') and native.process is not None:
                    if native.command('check-idle'):
                        raise RuntimeError('豆包仍在处理上次语音，本次未启动；请核对豆包状态后退出并重新启动适配')
                    fallback_owned = False
                if action == 'start-fallback':
                    if native.process is None:
                        fallback_owned = native.command(action)
                elif action == 'stop-fallback':
                    if fallback_owned:
                        native.command('stop')
                        fallback_owned = False
                else:
                    native.command(action)
                    if action in ('stop', 'cancel'):
                        fallback_owned = False
            except Exception as exc:
                native_fault.set()
                logging.exception('语音命令失败')
                errors.put(str(exc))
            finally:
                jobs.task_done()

    threading.Thread(target=worker, name='DoubaoNativeVoice', daemon=True).start()

    def event_filter(msg, data):
        nonlocal target
        if msg not in (0x100, 0x101, 0x104, 0x105):
            return False
        injected = bool(data.flags & 0x12)
        if injected and getattr(data, 'dwExtraInfo', 0) == MENU_INPUT_TAG:
            return False
        vk = int(data.vkCode)
        if vk == 0x10:
            vk = 0xA1 if data.scanCode == 0x36 else 0xA0
        elif vk in (0x11, 0x12):
            vk = (0xA3 if vk == 0x11 else 0xA5) if data.flags & 1 else (0xA2 if vk == 0x11 else 0xA4)
        if not injected:
            if vk not in MOD_VKS and (not physical.shortcut or vk != physical.shortcut.main):
                physical_events.put((vk, msg in (0x100, 0x104)))
                return False
            with lock:
                if closing.is_set():
                    return False
                send(physical.key(vk, msg in (0x100, 0x104), allowed and not paused and not native_fault.is_set(), time.monotonic()))
            if vk not in MOD_VKS:
                return False
        with lock:
            if closing.is_set():
                return False
            down = msg in (0x100, 0x104)
            mask_release = injected and not down and vk in state.menu_modifiers
            foreground = user.GetForegroundWindow()
            foreground_changed = bool(target and foreground != target)
            if foreground_changed:
                send(state.reset(state.bindings))
            suppressed, actions = state.key(vk, down, injected, allowed and not foreground_changed and not paused,
                                            time.monotonic(), voice_enabled=not native_fault.is_set())
            if injected and down and suppressed:
                # 只记已经因本次匹配而隐藏主键的修饰键；未匹配组合仍完整透传。
                state.menu_modifiers.update((state.pressed & MENU_MODIFIERS) - state.swallowed)
            if mask_release:
                state.menu_modifiers.discard(vk)
            if state.pending or state.active:
                target = target or user.GetForegroundWindow()
            send(actions)
        if mask_release and not suppressed:
            # 原按下已透传，不能只吞释放；回放带标记的成对释放，避免裸 Win/Alt 菜单。
            suppressed = mask_menu_release(user, vk, int(data.scanCode), int(data.flags) & 1)
        if suppressed:
            listener.suppress_event()
        return False

    listener = keyboard.Listener(win32_event_filter=event_filter)
    try:
        listener.start()
        listener.wait()
    except Exception:
        closing.set()
        listener.stop()
        jobs.put_nowait('exit')
        if tray_icon:
            tray_icon.stop()
        profile.close()
        for handle in (stop_event, show_event, instance):
            kernel.CloseHandle(handle)
        ui.destroy()
        raise

    def reset(bindings):
        with lock:
            send(state.reset(bindings))
            send(physical.reset(bindings.hold))

    def toggle_pause():
        nonlocal paused
        paused = not paused
        reset(state.bindings)
        if not paused:
            fingerprint_reset()
        pause_button.configure(text=get_pause_label())
        if not failure and not native_fault.is_set():
            status_var.set('适配状态: 已暂停' if paused else '适配状态: 正在重新检查')
            update_tray_state('已暂停' if paused else '正在重新检查', 'paused' if paused else 'attention')

    def manual_recheck():
        nonlocal fingerprint, failure
        if native_fault.is_set():
            if jobs.unfinished_tasks or native.process is not None:
                messagebox.showwarning('仍需处理', '语音工作单元尚未退出，请先在豆包结束录音，再退出本程序后重新启动。', parent=ui)
                return
            if not messagebox.askyesno('确认麦克风状态', '请先在豆包和系统中确认录音已停止。\n是否已检查并允许恢复适配？', parent=ui):
                return
            native_fault.clear()
        logging.info('用户请求重新检查配置与状态')
        fingerprint = None
        failure = None
        status_var.set('适配状态: 正在重新检查')
        update_tray_state('正在重新检查', 'attention')

    pause_button.configure(command=toggle_pause)
    recheck_button.configure(command=manual_recheck)
    hide_button.configure(command=hide_window)
    exit_button.configure(command=lambda: shutdown())
    ui.protocol('WM_DELETE_WINDOW', hide_window)

    def fingerprint_reset():
        nonlocal fingerprint
        fingerprint = None

    def shutdown():
        nonlocal allowed
        if closing.is_set():
            return
        with lock:
            allowed = False
            closing.set()
            jobs.put_nowait('exit')
        listener.stop()
        show_window()
        status_var.set('适配状态: 正在结束语音会话并卸载监听')
        diag_var.set('正在退出，原生停止失败时须核对豆包麦克风状态。')
        poll_exit()

    def poll_exit():
        if jobs.unfinished_tasks == 0:
            if native_fault.is_set():
                messagebox.showwarning('豆包录音状态未确认', '语音调用或收尾失败，本程序无法确认麦克风已关闭。请在豆包中手动检查并结束录音。', parent=ui)
            if tray_icon:
                try:
                    tray_icon.stop()
                except Exception:
                    logging.exception('托盘停止异常')
            logging.info('适配已退出；原生停止失败时须核对豆包麦克风状态')
            profile.close()
            kernel.CloseHandle(stop_event)
            kernel.CloseHandle(show_event)
            kernel.CloseHandle(instance)
            ui.destroy()
        else:
            ui.after(50, poll_exit)

    side_reader_thread = threading.Thread(target=watch_side_button, name='LogiSettingsReader', daemon=True)
    side_reader_thread.start()

    def tick():
        nonlocal fingerprint, allowed, target, profile, failure, side_buttons
        if closing.is_set():
            return
        if kernel.WaitForSingleObject(stop_event, 0) == 0:
            shutdown()
            return
        if kernel.WaitForSingleObject(show_event, 0) == 0:
            show_window()

        while not tray_queue.empty():
            act = tray_queue.get_nowait()
            if act == 'tray_failed':
                tray_fallback()
            elif act == 'tray_ready':
                fingerprint = None
            elif act == 'show_window':
                show_window()
            elif act == 'toggle_pause':
                toggle_pause()
            elif act == 'recheck':
                manual_recheck()
            elif act == 'exit':
                shutdown()
                return

        if not tray_failed and not tray_ready.is_set() and time.monotonic() - tray_started_at > 3:
            tray_fallback()
        voice_fault_only = False
        try:
            if not listener.is_alive():
                raise RuntimeError('输入监听已停止')
            while not side_results.empty():
                latest_buttons = side_results.get_nowait()
                if latest_buttons != side_buttons:
                    side_buttons = latest_buttons
                    fingerprint = None
            stat = Path(args.config).stat()
            current = stat.st_mtime_ns, stat.st_size
            if current != fingerprint or (failure is not None and not native_fault.is_set()):
                bindings = read_bindings(args.config)
                if bindings != state.bindings:
                    reset(bindings)
                fingerprint = current
                failure = None
                hold_var.set(f'长按模式: {bindings.hold.label() if bindings.hold else "禁用"}')
                toggle_var.set(f'免按模式: {bindings.toggle.label() if bindings.toggle else "禁用"}')
                side_status, side_detail = side_button_status(side_buttons, bindings)
                side_var.set(side_detail)
                curr_status = '已暂停' if paused else f'监听已就绪；{side_status}'
                status_var.set(f'适配状态: {curr_status}')
                diag_var.set('豆包配置可读；按键匹配不代表已完成实体语音验证。' + ('托盘不可用，请使用此窗口管理。' if tray_failed else '') + '更换快捷键只需在豆包与 Logi Options+ 设置中修改。')
                update_tray_state(curr_status, 'paused' if paused else ('ready' if side_status == '按键设置匹配' else 'attention'))
                logging.info('设置已重载 %s', bindings.label())
            hwnd = user.GetForegroundWindow()
            pid = w.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if getattr(tick, 'foreground_pid', None) != pid.value:
                image = ''
                process = kernel.OpenProcess(0x1000, False, pid.value)
                if process:
                    try:
                        buffer = ctypes.create_unicode_buffer(32768)
                        size = w.DWORD(len(buffer))
                        if kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                            image = Path(buffer.value).name.lower()
                    finally:
                        kernel.CloseHandle(process)
                tick.foreground_pid = pid.value
                tick.settings_foreground = image == 'doubaoimesettings.exe'
            if getattr(tick, 'profile_foreground', None) != hwnd:
                replacement = DoubaoProfile()
                profile.close()
                profile = replacement
                tick.profile_foreground = hwnd
            is_doubao = profile.active()
            # 前台门控与录音许可分离：故障后仍只在豆包前台吞已配置模拟组合。
            allowed = bool(hwnd) and not tick.settings_foreground and pid.value != os.getpid() and is_doubao
            gate = (pid.value, is_doubao, allowed)
            if getattr(tick, 'gate', None) != gate:
                logging.info('输入门控 pid=%s doubao=%s allowed=%s', *gate)
                tick.gate = gate
            if native_fault.is_set():
                voice_fault_only = True
                if not errors.empty():
                    failure = errors.get_nowait()
                raise RuntimeError(failure or '豆包录音状态未确认，请先核对麦克风')
            with lock:
                while not physical_events.empty():
                    vk, down = physical_events.get_nowait()
                    send(physical.key(vk, down, allowed and not paused and not native_fault.is_set(), time.monotonic()))
                if target and hwnd != target and (state.active or state.pending):
                    send(state.reset(state.bindings))
                if target and hwnd != target and (physical.active or physical.pending_at is not None):
                    send(physical.reset(physical.shortcut))
                if state.pending or state.active or physical.active or physical.pending_at is not None:
                    target = target or hwnd
                else:
                    target = 0
                if not allowed or paused:
                    send(state.reset(state.bindings))
                    send(physical.reset(physical.shortcut))
                else:
                    send(state.tick(time.monotonic()))
                    send(physical.tick(time.monotonic()))
            if not listener.is_alive():
                raise RuntimeError('输入监听已停止')
            if native_fault.is_set():
                raise RuntimeError(errors.get_nowait() if not errors.empty() else '豆包录音状态未确认')
        except Exception as exc:
            if voice_fault_only:
                reset(state.bindings)
                target = 0
            else:
                # 配置损坏、监听停止等错误不能沿用旧快捷键继续吞键。
                allowed = False
                fingerprint = None
                reset(Bindings(None, None))
            curr_exc = str(exc)
            side_var.set('当前适配检查失败，侧键匹配状态暂未确认；请先查看下方故障提示。')
            if native_fault.is_set():
                status_var.set('适配状态: 录音状态未确认 (需要处理)')
                guard_note = '豆包前台仍屏蔽已配置的模拟语音组合；暂停或切换输入法后透传。' if voice_fault_only else '当前未拦截新按键。'
                diag_var.set(f'故障警示: {curr_exc}\n已停用语音命令并禁止自动重试。{guard_note}请在豆包核对麦克风状态后，点击“重新检查”确认恢复。')
                update_tray_state('录音状态未确认', 'unconfirmed')
            else:
                status_var.set('适配状态: 需要处理')
                diag_var.set(f'已停用，未继续拦截按键: {curr_exc}')
                update_tray_state('需要处理', 'attention')
            if failure != curr_exc:
                logging.error('停用 %s', exc)
            failure = curr_exc
        ui.after(1000 if failure else 50, tick)

    ui.after(0, tick)
    try:
        ui.mainloop()
    finally:
        if not closing.is_set():
            closing.set()
            listener.stop()
            jobs.put('exit')
        side_reader_thread.join(timeout=0.5)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        logging.exception('启动或运行失败')
        ctypes.windll.user32.MessageBoxW(None, '豆包鼠标适配未启用。' + str(exc), '豆包鼠标语音适配', 0x10)
