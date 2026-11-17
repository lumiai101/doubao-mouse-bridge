"""在真实 Windows 会话取证窗口图标：标题栏截图、系统实际采用图标，以及它与托盘图形的像素比对。

每个模式在独立子进程里跑，因为 Tk 的窗口类图标是按进程设置的。会短暂显示一个与应用同标题的窗口。
"""
import argparse
import ctypes
from ctypes import wintypes as w
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'doubao_mouse_bridge.py'
WINDOW_TITLE = '豆包鼠标语音适配'
WM_GETICON = 0x007F
ICON_SMALL, ICON_BIG = 0, 1
GCLP_HICON, GCLP_HICONSM = -14, -34
PW_RENDERFULLCONTENT = 2
DI_NORMAL = 3
ICON_SIZE = 256

user = ctypes.WinDLL('user32', use_last_error=True)
gdi = ctypes.WinDLL('gdi32', use_last_error=True)
user.GetAncestor.argtypes = [w.HWND, w.UINT]
user.GetAncestor.restype = w.HWND
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user.GetWindowDC.argtypes = [w.HWND]
user.GetWindowDC.restype = w.HDC
user.GetDC.argtypes = [w.HWND]
user.GetDC.restype = w.HDC
user.ReleaseDC.argtypes = [w.HWND, w.HDC]
user.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
user.PrintWindow.restype = w.BOOL
user.DrawIconEx.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, w.HANDLE,
                            ctypes.c_int, ctypes.c_int, w.UINT, w.HANDLE, w.UINT]
user.DrawIconEx.restype = w.BOOL
user.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user.SendMessageW.restype = w.LPARAM
user.GetClassLongPtrW.argtypes = [w.HWND, ctypes.c_int]
user.GetClassLongPtrW.restype = ctypes.c_ssize_t
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


def load_bridge():
    spec = importlib.util.spec_from_file_location('bridge_icon_probe', SOURCE)
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    return bridge


def root_window(window):
    return user.GetAncestor(window.winfo_id(), 2)


def effective_icon(hwnd):
    """按 Windows 取值顺序返回系统真正会画的图标：窗口图标优先，再回退窗口类图标。"""
    for index, source in ((ICON_BIG, 'window-big'), (ICON_SMALL, 'window-small')):
        handle = user.SendMessageW(hwnd, WM_GETICON, index, 0)
        if handle:
            return int(handle), source
    for index, source in ((GCLP_HICON, 'window-class-big'), (GCLP_HICONSM, 'window-class-small')):
        handle = user.GetClassLongPtrW(hwnd, index)
        if handle:
            return int(handle), source
    return 0, 'none'


def draw_to_image(draw, size=ICON_SIZE):
    screen = user.GetDC(0)
    memory = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, size, size)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not draw(memory):
            raise RuntimeError('图标绘制失败')
        gdi.SelectObject(memory, previous)
        header = Header(ctypes.sizeof(Header), size, -size, 1, 32, 0, size * size * 4, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(size * size * 4)
        if gdi.GetDIBits(memory, bitmap, 0, size, buffer, ctypes.byref(header), 0) != size:
            raise RuntimeError('图标像素读取失败')
        from PIL import Image
        return Image.frombuffer('RGB', (size, size), buffer.raw, 'raw', 'BGRX', 0, 1).copy()
    finally:
        if previous:
            gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(0, screen)


def icon_to_image(handle, size=ICON_SIZE):
    return draw_to_image(lambda memory: user.DrawIconEx(
        memory, 0, 0, handle, size, size, 0, None, DI_NORMAL), size)


def capture_window(hwnd, target):
    rect = w.RECT()
    if not user.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if not (1 <= width <= 4000 and 1 <= height <= 4000):
        raise RuntimeError(f'窗口尺寸不适合取证: {width}x{height}')
    screen = user.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, PW_RENDERFULLCONTENT):
            raise RuntimeError('PrintWindow 未成功')
        gdi.SelectObject(memory, previous)
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        if gdi.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0) != height:
            raise RuntimeError('窗口像素读取失败')
        from PIL import Image
        Image.frombuffer('RGB', (width, height), buffer.raw, 'raw', 'BGRX', 0, 1).copy().save(target)
    finally:
        if previous:
            gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, screen)
    return {'width': width, 'height': height}


def artwork_difference(rendered, artwork):
    """只比较不透明像素：透明区域由系统按掩码合成，不进图标本身。"""
    from PIL import Image
    composed = Image.alpha_composite(
        Image.new('RGBA', artwork.size, (255, 255, 255, 255)), artwork).convert('RGB')
    rendered_pixels = list(rendered.getdata())
    composed_pixels = list(composed.getdata())
    alpha = list(artwork.getchannel('A').getdata())
    opaque = [index for index, value in enumerate(alpha) if value == 255]
    differing = [index for index in opaque if rendered_pixels[index] != composed_pixels[index]]
    return {'opaque_pixels': len(opaque), 'differing_opaque_pixels': len(differing)}


def probe(mode, output):
    import tkinter as tk
    bridge = load_bridge()
    output.mkdir(parents=True, exist_ok=True)
    window = tk.Tk()
    window.title(WINDOW_TITLE)
    window.geometry('520x140+80+40')
    tk.Label(window, text=f'窗口图标取证：{mode}', font=('Segoe UI', 14)).pack(expand=True)
    if mode == 'after':
        bridge.apply_window_icon(window)
    window.update()
    hwnd = root_window(window)
    report = {'mode': mode, 'window_title': window.title(), 'hwnd': hwnd}
    report['title_bar_capture'] = str(output / f'title-bar-{mode}.png')
    report['window_size'] = capture_window(hwnd, output / f'title-bar-{mode}.png')
    handle, source = effective_icon(hwnd)
    report['icon_handle'] = handle
    report['icon_source'] = source
    if handle:
        image = icon_to_image(handle)
        image.save(output / f'window-icon-{mode}.png')
        report['window_icon_png'] = str(output / f'window-icon-{mode}.png')
        report['artwork'] = artwork_difference(image, bridge.create_app_icon_image())
    window.destroy()
    return report


def run(labels, output):
    reports = {}
    for mode in labels:
        command = [sys.executable, str(Path(__file__).resolve()), '--mode', mode, '--output', str(output)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        if result.returncode != 0:
            raise SystemExit(f'{mode} 取证失败: {result.stdout}{result.stderr}')
        reports[mode] = json.loads(result.stdout.strip().splitlines()[-1])
    before, after = reports['before'], reports['after']
    checks = {
        'after_icon_matches_artwork': after.get('artwork', {}).get('differing_opaque_pixels', -1) == 0,
        'before_icon_is_not_artwork': before.get('artwork', {}).get('differing_opaque_pixels', 0) > 0,
        'icon_changed_between_before_and_after': before.get('icon_handle') != after.get('icon_handle'),
        'after_icon_handle_present': after.get('icon_handle', 0) != 0,
    }
    summary = {'output': str(output), 'checks': checks, 'before': before, 'after': after}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit('窗口图标取证未通过: ' + ', '.join(k for k, v in checks.items() if not v))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='窗口标题栏与任务栏图标的真实会话取证')
    parser.add_argument('--mode', choices=('before', 'after'), help='只跑单个模式并打印该模式报告')
    parser.add_argument('--output', default=str(ROOT / 'dist' / 'icon-evidence'))
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('此取证需要 Windows 会话')
    if args.mode:
        print(json.dumps(probe(args.mode, Path(args.output)), ensure_ascii=False))
    else:
        run(('before', 'after'), Path(args.output))
