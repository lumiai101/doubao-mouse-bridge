"""构建可解压运行的 Windows 包：写应用图标、跑 PyInstaller、核对 EXE 内嵌图标、封装 ZIP。

EXE 图标与应用窗口、托盘同源，都来自 src/doubao_mouse_bridge.py 的图标绘制函数。
"""
import argparse
import ctypes
from ctypes import wintypes as w
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_window_icon

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src' / 'doubao_mouse_bridge.py'
NAME = 'DoubaoMouseBridge'
CHECK_SIZE = 256
ICON_SIZES = (16, 32, 48, 256)

user = ctypes.WinDLL('user32', use_last_error=True)
user.PrivateExtractIconsW.argtypes = [w.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.POINTER(w.HANDLE), ctypes.POINTER(ctypes.c_uint), w.UINT, w.UINT]
user.PrivateExtractIconsW.restype = w.UINT
user.DestroyIcon.argtypes = [w.HANDLE]


def load_bridge():
    spec = importlib.util.spec_from_file_location('bridge_icon_packager', SOURCE)
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    return bridge


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def exe_icon_report(exe, bridge):
    """按关键尺寸取 EXE 内嵌图标，逐个和托盘图形比不透明像素，证明打包没有丢图或换图。"""
    report = {}
    for size in ICON_SIZES:
        handle = (w.HANDLE * 1)()
        index = (ctypes.c_uint * 1)()
        if not user.PrivateExtractIconsW(str(exe), 0, size, size, handle, index, 1, 0):
            raise SystemExit(f'EXE 里没有 {size} 像素的图标资源')
        try:
            rendered = verify_window_icon.icon_to_image(handle[0], size)
            difference = verify_window_icon.artwork_difference(
                rendered, bridge.create_tray_icon_image('ready', size))
        finally:
            if handle[0]:
                user.DestroyIcon(handle[0])
        report[str(size)] = difference
    return report


def build(label):
    bridge = load_bridge()
    work = ROOT / 'dist' / 'windows-app-work'
    icon = work / 'app.ico'
    icon.parent.mkdir(parents=True, exist_ok=True)
    bridge.write_app_icon(icon)
    dist = ROOT / 'dist'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
                    '--name', NAME, '--icon', str(icon),
                    '--distpath', str(dist), '--workpath', str(work), '--specpath', str(work),
                    str(SOURCE)],
                   cwd=ROOT, check=True)
    app = dist / NAME
    exe = app / f'{NAME}.exe'
    if not exe.is_file():
        raise SystemExit(f'PyInstaller 没有产出 {exe}')
    internal = app / '_internal'
    if not internal.is_dir():
        raise SystemExit('缺少 _internal 目录，包不是可直接解压运行的形态')
    icons = exe_icon_report(exe, bridge)
    archive = dist / f'{NAME}-{label}.zip'
    if archive.exists():
        raise SystemExit(f'{archive} 已存在，先人工核对再删除')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                bundle.write(path, (Path(NAME) / path.relative_to(app)).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        broken = bundle.testzip()
    if broken or f'{NAME}/{NAME}.exe' not in names:
        raise SystemExit(f'ZIP 自检失败: {broken}')
    report = {
        'label': label,
        'check_size': CHECK_SIZE,
        'icons': icons,
        'exe': str(exe),
        'exe_sha256': sha256(exe),
        'zip': str(archive),
        'zip_sha256': sha256(archive),
        'zip_bytes': archive.stat().st_size,
        'zip_entries': len(names),
        'icon_matches_tray_artwork': all(item['differing_opaque_pixels'] == 0 for item in icons.values()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['icon_matches_tray_artwork']:
        raise SystemExit('EXE 内嵌图标与托盘图形不一致')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='构建 Windows 便携包')
    parser.add_argument('--label', default='FLOW-141', help='ZIP 名称里的标记')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('此构建需要 Windows')
    build(args.label)
