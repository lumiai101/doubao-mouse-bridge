"""构建无控制台安装版；不启动应用、不修改系统启动入口。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def build(output, compiler=None, licenses=None, version='0.2.0-rc.7'):
    output = Path(output).resolve()
    if compiler and not licenses:
        raise ValueError('编译安装器前必须提供包含第三方许可与 LGPL 源包的 --licenses 目录')
    if output.exists():
        raise FileExistsError(f'输出目录已存在，拒绝覆盖: {output}')
    output.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location('build_bridge', ROOT / 'src/doubao_mouse_bridge.py')
    bridge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bridge
    spec.loader.exec_module(bridge)
    icon = output / 'bridge.ico'
    bridge.create_tray_icon_image('ready').save(icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    command = [sys.executable, '-X', 'utf8', '-B', '-I', '-m', 'PyInstaller',
               '--noconfirm', '--clean', '--onedir', '--noconsole',
               '--name', 'DoubaoMouseBridge', '--icon', str(icon),
               '--distpath', str(output / 'app'), '--workpath', str(output / 'work'),
               '--specpath', str(output / 'spec'), '--paths', str(ROOT / 'src'),
               '--hidden-import', 'pynput.keyboard._win32', '--hidden-import', 'pynput.mouse._win32',
               '--hidden-import', 'pystray._win32']
    for source in sorted((ROOT / 'src').glob('*.py')):
        command.extend(['--add-data', f'{source}:src'])
    command.append(str(ROOT / 'src/doubao_mouse_bridge.py'))
    subprocess.run(command, cwd=ROOT, check=True)
    application = output / 'app/DoubaoMouseBridge'
    if licenses:
        shutil.copytree(licenses, application / 'licenses')
    hashes = {p.relative_to(application).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(application.rglob('*')) if p.is_file()}
    (output / 'application-sha256.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    if compiler:
        subprocess.run([str(compiler), f'/DAppVersion={version}',
                        f'/DAppSourceDir={application}', f'/DOutputDir={output / "installer"}',
                        f'/DAppIcon={icon}', str(ROOT / 'packaging/windows-installer.iss')], check=True)
    print(json.dumps({'application': str(application), 'files': len(hashes),
                      'installer_compiled': bool(compiler)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--licenses', type=Path)
    parser.add_argument('--version', default='0.2.0-rc.7')
    args = parser.parse_args()
    build(args.output, args.iscc, args.licenses, args.version)
