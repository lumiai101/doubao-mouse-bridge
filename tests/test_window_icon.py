"""验证窗口标题栏与任务栏图标取自应用图标，并与托盘图形、EXE 图标素材保持同一张图。"""
import ctypes
from ctypes import wintypes as w
import gc
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

# 与托盘测试同一套隔离：不装载真实全局键盘监听。
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

WM_GETICON = 0x007F
ICON_SMALL, ICON_BIG = 0, 1
GCLP_HICON, GCLP_HICONSM = -14, -34
DI_NORMAL = 3
ICON_SIZE = 256

user = ctypes.WinDLL('user32', use_last_error=True)
gdi = ctypes.WinDLL('gdi32', use_last_error=True)
user.GetAncestor.argtypes = [w.HWND, w.UINT]
user.GetAncestor.restype = w.HWND
user.GetDC.argtypes = [w.HWND]
user.GetDC.restype = w.HDC
user.ReleaseDC.argtypes = [w.HWND, w.HDC]
user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.SendMessageW.restype = w.LPARAM
user.GetClassLongPtrW.argtypes = [w.HWND, ctypes.c_int]
user.GetClassLongPtrW.restype = ctypes.c_ssize_t
user.DrawIconEx.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, w.HANDLE,
                            ctypes.c_int, ctypes.c_int, w.UINT, w.HANDLE, w.UINT]
user.DrawIconEx.restype = w.BOOL
gdi.CreateCompatibleDC.argtypes = [w.HDC]
gdi.CreateCompatibleDC.restype = w.HDC
gdi.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
gdi.CreateCompatibleBitmap.restype = w.HBITMAP
gdi.SelectObject.argtypes = [w.HDC, w.HANDLE]
gdi.SelectObject.restype = w.HANDLE
gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p, ctypes.c_void_p, w.UINT]
gdi.GetDIBits.restype = ctypes.c_int
gdi.DeleteObject.argtypes = [w.HANDLE]
gdi.DeleteDC.argtypes = [w.HDC]


class Header(ctypes.Structure):
    _fields_ = [('size', w.DWORD), ('width', w.LONG), ('height', w.LONG),
                ('planes', w.WORD), ('bits', w.WORD), ('compression', w.DWORD),
                ('image_size', w.DWORD), ('xppm', w.LONG), ('yppm', w.LONG),
                ('used', w.DWORD), ('important', w.DWORD)]


def window_icon_handle(window):
    """返回 Windows 真正会为这个窗口画的图标句柄：窗口图标优先，再回退窗口类图标。

    句柄为 0 表示系统只剩 Tk 的默认羽毛图标可画。
    """
    hwnd = user.GetAncestor(window.winfo_id(), 2)
    for index in (ICON_BIG, ICON_SMALL):
        handle = user.SendMessageW(hwnd, WM_GETICON, index, 0)
        if handle:
            return hwnd, int(handle)
    for index in (GCLP_HICON, GCLP_HICONSM):
        handle = user.GetClassLongPtrW(hwnd, index)
        if handle:
            return hwnd, int(handle)
    return hwnd, 0


def icon_pixels(handle):
    """按系统绘制路径把图标句柄渲染成像素，用来和托盘图形逐像素比对。"""
    from PIL import Image
    screen = user.GetDC(0)
    memory = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, ICON_SIZE, ICON_SIZE)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.DrawIconEx(memory, 0, 0, handle, ICON_SIZE, ICON_SIZE, 0, None, DI_NORMAL):
            raise AssertionError('DrawIconEx 未能绘制窗口图标')
        gdi.SelectObject(memory, previous)
        header = Header(ctypes.sizeof(Header), ICON_SIZE, -ICON_SIZE, 1, 32, 0,
                        ICON_SIZE * ICON_SIZE * 4, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(ICON_SIZE * ICON_SIZE * 4)
        if gdi.GetDIBits(memory, bitmap, 0, ICON_SIZE, buffer, ctypes.byref(header), 0) != ICON_SIZE:
            raise AssertionError('GetDIBits 未取得窗口图标像素')
        return Image.frombuffer('RGB', (ICON_SIZE, ICON_SIZE), buffer.raw, 'raw', 'BGRX', 0, 1).copy()
    finally:
        if previous:
            gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(0, screen)


def opaque_difference(rendered, artwork):
    """透明区域由系统按掩码合成，只比对图标本身的不透明像素。"""
    from PIL import Image
    composed = Image.alpha_composite(
        Image.new('RGBA', artwork.size, (255, 255, 255, 255)), artwork).convert('RGB')
    rendered_data = list(rendered.getdata())
    composed_data = list(composed.getdata())
    return sum(1 for index, alpha in enumerate(artwork.getchannel('A').getdata())
               if alpha == 255 and rendered_data[index] != composed_data[index])


class FakeFunc:
    def __init__(self, fn):
        self.fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *args, **kwargs):
        return self.fn(*args, **kwargs)


class MockKernel32:
    """隔离真实单实例互斥体与事件，只让 m.main() 走完启动与退出。"""
    def __init__(self):
        self.CreateMutexW = FakeFunc(lambda sec, owner, name: 1001)
        self.CreateEventW = FakeFunc(lambda sec, manual, initial, name: 2001)
        self.OpenEventW = FakeFunc(lambda access, inherit, name: 3001)
        self.SetEvent = FakeFunc(lambda handle: 1)
        self.WaitForSingleObject = FakeFunc(lambda handle, ms: 1)
        self.CloseHandle = FakeFunc(lambda handle: 1)
        self.OpenProcess = FakeFunc(lambda *a: 0)
        self.QueryFullProcessImageNameW = FakeFunc(lambda *a: 0)

    def __getattr__(self, name):
        func = FakeFunc(lambda *a, **kw: 0)
        setattr(self, name, func)
        return func


class MockUser32:
    def __init__(self):
        self.GetForegroundWindow = FakeFunc(lambda: 100)
        self.GetWindowThreadProcessId = FakeFunc(lambda hwnd, ptr: 1)
        self.GetAsyncKeyState = FakeFunc(lambda vk: 0)
        self.MessageBoxW = FakeFunc(lambda *a: 1)

    def __getattr__(self, name):
        func = FakeFunc(lambda *a, **kw: 0)
        setattr(self, name, func)
        return func


def make_clean_tk(orig_tk):
    """销毁真实 Tk 窗口前先撤销 after 句柄，避免退出时报 Tcl 错。"""
    def create_wrapped(*args, **kwargs):
        window = orig_tk(*args, **kwargs)
        scheduled = []
        real_after = window.after

        def safe_after(delay, callback=None):
            handle = real_after(delay, callback)
            scheduled.append(handle)
            return handle

        window.after = safe_after
        real_destroy = window.destroy

        def clean_destroy():
            for handle in scheduled:
                try:
                    window.after_cancel(handle)
                except Exception:
                    pass
            real_destroy()

        window.destroy = clean_destroy
        return window
    return create_wrapped


def walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


class WindowIconTests(unittest.TestCase):
    def tearDown(self):
        gc.collect()

    def make_window(self):
        import tkinter as tk
        window = tk.Tk()
        window.title('豆包鼠标语音适配')
        window.geometry('320x80+60+60')
        self.addCleanup(self.destroy_window, window)
        window.update()
        return window

    def destroy_window(self, window):
        try:
            window.destroy()
        except Exception:
            pass

    def test_window_icon_matches_tray_artwork_on_real_window(self):
        """应用图标必须交给真实窗口，且系统实际会画的像素等于托盘图形。"""
        window = self.make_window()
        m.apply_window_icon(window)

        _, handle = window_icon_handle(window)
        self.assertNotEqual(0, handle, '窗口没有把任何图标交给系统')
        self.assertEqual(0, opaque_difference(icon_pixels(handle), m.create_app_icon_image()),
                         '窗口图标像素与托盘图形不一致')

    def test_app_icon_matches_tray_artwork_at_both_sizes(self):
        """应用图标不是第二套设计：它来自托盘图形，缩小后与托盘 64 像素图逐像素一致。"""
        from PIL import Image

        app_icon = m.create_app_icon_image()
        self.assertEqual((256, 256), app_icon.size)
        self.assertEqual(
            list(m.create_tray_icon_image('ready').convert('RGBA').getdata()),
            list(app_icon.resize((64, 64), Image.Resampling.LANCZOS).convert('RGBA').getdata()))
        self.assertEqual(m.create_tray_icon_image('attention').size, (64, 64))

    def test_icon_file_carries_windows_sizes_and_tray_artwork(self):
        """打包用的 .ico 必须含任务栏与资源管理器需要的尺寸，像素等于应用图标。"""
        from PIL import Image

        buffer = io.BytesIO()
        m.write_app_icon(buffer)
        buffer.seek(0)
        with Image.open(buffer) as image:
            sizes = set(image.info['sizes'])
            pixels = list(image.convert('RGBA').getdata())
        for size in ((16, 16), (32, 32), (48, 48), (256, 256)):
            self.assertIn(size, sizes)
        self.assertEqual(list(m.create_app_icon_image().convert('RGBA').getdata()), pixels)

    def test_build_script_writes_ico_with_256(self):
        """打包脚本的 .ico 必须含 256px；退回旧写法会让这个用例变红。"""
        from PIL import Image

        script = Path(__file__).resolve().parents[1] / 'scripts' / 'build_windows.py'
        source = script.read_text(encoding='utf-8')
        self.assertNotIn('create_tray_icon_image', source)
        self.assertIn('bridge.write_app_icon', source)

        spec = importlib.util.spec_from_file_location('build_windows', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        directory = Path(__file__).resolve().parents[1] / f'.ico-probe-{id(self)}'
        directory.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(directory, True))

        icon = module.write_icon(directory, m)
        with Image.open(icon) as image:
            sizes = set(image.info['sizes'])

        for size in m.APP_ICON_SIZES:
            self.assertIn((size, size), sizes)

    def test_main_applies_icon_to_its_own_window(self):
        """真正跑一遍 m.main()，确认它给自建窗口设了图标，而不是只有辅助函数可用。"""
        import tkinter as tk

        class SafeVoice:
            process = None
            def __init__(self, install): self.install = str(install)
            def probe(self): return 0
            def command(self, action): return False
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
            def run(self, setup=None): pass
            def stop(self): pass
            def update_menu(self): pass

        import pystray
        mock_kernel = MockKernel32()
        mock_user = MockUser32()
        recorded = {}
        real_apply = m.apply_window_icon

        def spy_apply(window):
            image = real_apply(window)
            recorded['title'] = window.title()
            recorded['mapped_before_icon'] = window.winfo_ismapped()
            return image

        orig_tk = make_clean_tk(tk.Tk)

        def tk_spy(*args, **kwargs):
            window = orig_tk(*args, **kwargs)

            def bounded_mainloop(*loop_args, **loop_kwargs):
                """进程里可能还有别的 Tk 窗口，真实 mainloop 不会因本窗口销毁而返回。"""
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        if not window.winfo_exists():
                            return
                        window.update()
                    except tk.TclError:
                        return
                    time.sleep(0.02)

            window.mainloop = bounded_mainloop

            def exit_when_ready():
                for widget in walk(window):
                    if isinstance(widget, tk.Button) and widget.cget('text') == '退出程序':
                        widget.invoke()
                        return
                window.after(50, exit_when_ready)

            window.after(150, exit_when_ready)
            return window

        with patch.object(m, 'apply_window_icon', side_effect=spy_apply), \
             patch.object(pystray, 'Icon', MockTrayIcon), \
             patch.object(ctypes, 'WinDLL',
                          side_effect=lambda name, **kw: mock_kernel if name == 'kernel32' else mock_user), \
             patch.object(m, 'find_install', return_value=Path('C:/nonexistent-doubao')), \
             patch.object(m, 'VoiceSession', SafeVoice), \
             patch.object(m, 'DoubaoProfile', SafeProfile), \
             patch.object(m, 'read_logi_buttons', return_value=[]), \
             patch.object(m, 'read_bindings', return_value=m.Bindings(None, None)), \
             patch.object(m, 'RotatingFileHandler', return_value=__import__('logging').NullHandler()), \
             patch.object(tk, 'Tk', side_effect=tk_spy), \
             patch.object(sys, 'argv', ['doubao_bridge', '--config', tempfile.gettempdir()]):
            m.main()

        self.assertEqual('豆包鼠标语音适配', recorded.get('title'))
        self.assertEqual(0, recorded.get('mapped_before_icon'), '窗口显示之后才设图标会先闪一次默认图标')


if __name__ == '__main__':
    unittest.main()
