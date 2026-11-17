"""为受控本地安装收集许可证及 LGPL 依赖的确切源包；不执行下载内容。"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import urllib.request

UA = {'User-Agent': 'DoubaoMouseBridge-build'}


def collect(repo, target):
    repo, target = Path(repo), Path(target)
    target.mkdir(parents=True, exist_ok=False)
    for name in ('pynput', 'pystray', 'pillow', 'six'):
        dist = importlib.metadata.distribution(name)
        folder = target / f'{name}-{dist.version}'
        folder.mkdir()
        (folder / 'METADATA.txt').write_text(dist.read_text('METADATA') or '', encoding='utf-8')
        for file in dist.files or []:
            if 'licen' in str(file).lower() or 'copying' in str(file).lower():
                path = dist.locate_file(file)
                if path.is_file():
                    shutil.copy2(path, folder / path.name)
        if name in ('pynput', 'pystray'):
            request = urllib.request.Request(f'https://pypi.org/pypi/{name}/{dist.version}/json', headers=UA)
            with urllib.request.urlopen(request, timeout=30) as response:
                metadata = json.load(response)
            item = next(x for x in metadata['urls'] if x['packagetype'] == 'sdist')
            with urllib.request.urlopen(item['url'], timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != item['digests']['sha256']:
                raise RuntimeError(f'源包校验失败: {name}')
            download = folder / 'source'
            download.mkdir()
            (download / item['filename']).write_bytes(data)
            (folder / 'source.json').write_text(
                json.dumps({'url': item['url'], 'sha256': item['digests']['sha256']}, indent=2), encoding='utf-8')
    base = Path(sys.base_prefix)
    found = []
    for name in ('LICENSE.txt', 'tcl/tcl8.6/license.terms', 'tcl/tk8.6/license.terms'):
        path = base / name
        if path.is_file():
            destination = target / 'python' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            found.append(name)
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(repo / name, target / name)
    missing = [name for name in ('tcl/tcl8.6/license.terms', 'tcl/tk8.6/license.terms') if name not in found]
    if missing:
        print(json.dumps({'warning': '构建用 Python 未随附 Tcl/Tk 许可，须另外补齐后再出包', 'missing': missing},
                         ensure_ascii=False), file=sys.stderr)
    print(json.dumps({'license_directory': str(target), 'python_license_files': found}, ensure_ascii=False))


if __name__ == '__main__':
    repo, target = map(Path, sys.argv[1:3])
    collect(repo, target)
