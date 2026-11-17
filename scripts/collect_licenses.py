"""为受控本地安装收集许可证及 LGPL 依赖的确切源包；不执行下载内容。

缺项一律硬失败：构建用 Python 没带 Tcl/Tk 许可、或依赖在 PyPI 上取不到任何
可校验的分发件时，脚本以非零退出，避免静默产出缺许可的安装包。
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import urllib.request

UA = {'User-Agent': 'DoubaoMouseBridge-build'}
ROOT = Path(__file__).resolve().parents[1]
LICENSE_SOURCE = ROOT / 'packaging' / 'licenses'


def pick_source(urls):
    """优先取 sdist；没有 sdist 时退回 wheel。

    pystray 0.19.5 在 PyPI 上只有 wheel。该包是纯 Python，wheel 内的 .py 文件
    就是随包分发的源码本体，因此 wheel 可作为 LGPLv3 第 4 条要求的对应源码，
    且 PyPI 为其提供了可校验的 sha256。GitHub 生成的 tag 归档哈希不稳定，
    不适合作为可复现来源。
    """
    for kind in ('sdist', 'bdist_wheel'):
        for item in urls:
            if item['packagetype'] == kind:
                return item, kind
    raise RuntimeError('PyPI 上没有 sdist 也没有 wheel，无法提供对应源码')


def fetch_source(name, version):
    request = urllib.request.Request(f'https://pypi.org/pypi/{name}/{version}/json', headers=UA)
    with urllib.request.urlopen(request, timeout=30) as response:
        metadata = json.load(response)
    item, kind = pick_source(metadata['urls'])
    request = urllib.request.Request(item['url'], headers=UA)
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != item['digests']['sha256']:
        raise RuntimeError(f'源包校验失败: {name} {item["filename"]}')
    return item, kind, data


def require_tcl_terms(base):
    """返回可用的 Tcl/Tk 许可正文；一个都没有时硬失败，不静默放过。"""
    base = Path(base)
    terms = [base / 'tcl/tcl8.6/license.terms', base / 'tcl/tk8.6/license.terms']
    terms = [path for path in terms if path.is_file()]
    if not terms:
        raise SystemExit('构建用 Python 未随附 Tcl/Tk 许可，必须先补齐再出包')
    return terms


def collect(repo, target):
    repo, target = Path(repo), Path(target)
    target.mkdir(parents=True, exist_ok=False)
    versions = {}
    for name in ('pynput', 'pystray', 'pillow', 'six'):
        dist = importlib.metadata.distribution(name)
        versions[name] = dist.version
        folder = target / f'{name}-{dist.version}'
        folder.mkdir()
        (folder / 'METADATA.txt').write_text(dist.read_text('METADATA') or '', encoding='utf-8')
        for file in dist.files or []:
            if 'licen' in str(file).lower() or 'copying' in str(file).lower():
                path = dist.locate_file(file)
                if path.is_file():
                    shutil.copy2(path, folder / path.name)
        if name in ('pynput', 'pystray'):
            item, kind, data = fetch_source(name, dist.version)
            download = folder / 'source'
            download.mkdir()
            (download / item['filename']).write_bytes(data)
            (folder / 'source.json').write_text(
                json.dumps({'url': item['url'], 'sha256': item['digests']['sha256'],
                            'source_kind': kind}, indent=2), encoding='utf-8')
    # LGPLv3 是 GPLv3 之上的附加许可，分发时必须同时给出 GPLv3 正文。
    # pynput 的发行版只带 COPYING.LGPL，基础文本由仓库内权威副本补齐。
    gpl = LICENSE_SOURCE / 'GPL-3.0.txt'
    if not gpl.is_file():
        raise SystemExit(f'缺少 GPLv3 正文: {gpl}')
    for name in ('pynput', 'pystray'):
        shutil.copy2(gpl, target / f'{name}-{versions[name]}' / 'COPYING')
    # OpenSSL 3.x 随 _internal/ 分发，Apache-2.0 正文必须随包。
    apache = LICENSE_SOURCE / 'Apache-2.0.txt'
    if not apache.is_file():
        raise SystemExit(f'缺少 Apache-2.0 正文: {apache}')
    openssl = target / 'openssl'
    openssl.mkdir()
    shutil.copy2(apache, openssl / 'LICENSE.txt')
    base = Path(sys.base_prefix)
    python_license = base / 'LICENSE.txt'
    if not python_license.is_file():
        raise SystemExit(f'构建用 Python 未随附 PSF 许可: {python_license}')
    (target / 'python').mkdir()
    shutil.copy2(python_license, target / 'python' / 'LICENSE.txt')
    # Tcl/Tk 许可统一落在 tcl-tk/，不再重复写进 python/ 子目录。
    terms = require_tcl_terms(base)
    tcl_dir = target / 'tcl-tk'
    tcl_dir.mkdir()
    shutil.copy2(terms[0], tcl_dir / 'license.terms')
    (tcl_dir / 'NOTICE.txt').write_text(
        'Tcl/Tk 8.6，取自构建用 CPython 发行版自带 license.terms。\n', encoding='utf-8')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(repo / name, target / name)
    print(json.dumps({'license_directory': str(target),
                      'python_license': str(python_license),
                      'tcl_tk_source': str(terms[0])}, ensure_ascii=False))


if __name__ == '__main__':
    repo, target = map(Path, sys.argv[1:3])
    collect(repo, target)
