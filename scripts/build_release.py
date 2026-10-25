"""按公开文件清单生成源码 ZIP，检查解压内容；不上传、不打包运行数据。"""
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_release


VERSION = '0.2.0-rc.2'
ROOT = Path(__file__).resolve().parents[1]


def inspect(directory):
    original = check_release.ROOT
    try:
        check_release.ROOT = directory
        errors = check_release.inspect_tree()
    finally:
        check_release.ROOT = original
    if errors:
        raise ValueError('\n'.join(errors))


def build():
    name = f'doubao-mouse-bridge-v{VERSION}-source'
    output = ROOT / 'dist'
    archive = output / f'{name}.zip'
    checksum = output / f'{name}.sha256'
    if archive.exists() or checksum.exists():
        raise FileExistsError('发布文件已存在，请先核对已有文件，不自动覆盖')
    with tempfile.TemporaryDirectory(prefix='doubao-release-') as temp:
        staging = Path(temp) / name
        staging.mkdir()
        for relative in check_release.FILES:
            source = ROOT / relative
            if source.is_symlink() or not source.is_file():
                raise ValueError(f'发布源文件无效: {relative}')
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        inspect(staging)
        packed = Path(temp) / archive.name
        with zipfile.ZipFile(packed, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
            for relative in check_release.FILES:
                bundle.write(staging / relative, f'{name}/{relative}')
        with zipfile.ZipFile(packed) as bundle:
            expected = {f'{name}/{relative}' for relative in check_release.FILES}
            if set(bundle.namelist()) != expected or len(bundle.namelist()) != len(expected):
                raise ValueError('ZIP 文件集合与公开清单不一致')
            if bundle.testzip() is not None:
                raise ValueError('ZIP 完整性检查失败')
            unpacked = Path(temp) / 'unpacked'
            bundle.extractall(unpacked)
        inspect(unpacked / name)
        digest = hashlib.sha256(packed.read_bytes()).hexdigest()
        output.mkdir(exist_ok=True)
        with archive.open('xb') as stream:
            stream.write(packed.read_bytes())
        with checksum.open('x', encoding='ascii', newline='\n') as stream:
            stream.write(f'{digest}  {archive.name}\n')
    print(f'PASS: {len(check_release.FILES)} 个公开文件、打包前与解压后检查、ZIP 完整性')
    print(f'ZIP: {archive}')
    print(f'SHA256: {digest}')
    print(f'校验文件: {checksum}')


if __name__ == '__main__':
    build()
