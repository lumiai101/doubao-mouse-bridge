"""检查公开源码文件，不访问用户配置或运行桌面。"""
import ast
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    '.gitattributes', '.gitignore', '.github/workflows/test.yml',
    'CHANGELOG.md', 'CONTRIBUTING.md', 'LICENSE', 'README.md', 'SECURITY.md',
    'THIRD_PARTY_NOTICES.md', 'docs/architecture.md', 'docs/compatibility.md',
    'docs/troubleshooting.md', 'requirements.txt', 'setup.cmd', 'start.cmd',
    'src/doubao_mouse_bridge.py', 'tests/test_bridge.py', 'scripts/check_release.py',
    'tests/test_callback_latency.py', 'tests/test_physical_fallback.py',
    'tests/test_native_lifecycle.py', 'tests/test_tray.py',
    'scripts/verify_tray.py', 'docs/tray-verification.md',
    'tests/test_side_button.py', 'scripts/verify_side_button.py',
    'tests/test_menu_suppression.py', 'scripts/build_release.py',
    'docs/releases/v0.2.0-rc.1.md', 'docs/releases/v0.2.0-rc.2.md',
)
# 按 LF 计算发布源码指纹，避免 Git 换行转换造成误报；不代表实体验收通过。
SOURCE_SHA256 = '0a281f2a94045cf482c9c56d7fa662130a3a69bafe51e9612080e520a797dfef'
IGNORED_DIRS = {'.git', '.venv', '__pycache__'}
PATTERNS = {
    '本机用户绝对路径': re.compile(r'(?:[A-Za-z]:[\\/]Users[\\/]|/home/)[A-Za-z0-9_.-]+[\\/]', re.I),
    '私钥': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'GitHub令牌': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    '常见服务密钥': re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b'),
}


def inspect_tree():
    errors = []
    actual = set()
    for path in ROOT.rglob('*'):
        relative = path.relative_to(ROOT)
        if set(relative.parts) & IGNORED_DIRS:
            continue
        if path.is_symlink():
            errors.append(f'不接受符号链接: {relative.as_posix()}')
        elif path.is_file():
            if path.suffix == '.log':
                continue
            actual.add(relative.as_posix())
    expected = set(FILES)
    errors.extend(f'缺少文件: {name}' for name in sorted(expected - actual))
    errors.extend(f'未审阅文件: {name}' for name in sorted(actual - expected))
    for name in sorted(expected & actual):
        path = ROOT / name
        text = path.read_text(encoding='utf-8-sig')
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                errors.append(f'{label}: {name}')
        if path.suffix == '.py':
            ast.parse(text, filename=name)
        if path.suffix == '.md':
            for target in re.findall(r'\]\(([^)]+)\)', text):
                if '://' in target or target.startswith('#'):
                    continue
                if not (path.parent / target.split('#')[0]).is_file():
                    errors.append(f'文档链接缺失: {name} -> {target}')
    source = ROOT / 'src/doubao_mouse_bridge.py'
    if source.is_file() and hashlib.sha256(source.read_text(encoding='utf-8').encode('utf-8')).hexdigest() != SOURCE_SHA256:
        errors.append('源码与发布指纹不同，请审阅变更、运行测试并更新指纹；指纹不证明实体验收')
    return errors


if __name__ == '__main__':
    errors = inspect_tree()
    for message in errors:
        print(f'FAIL: {message}')
    if errors:
        sys.exit(1)
    print(f'PASS: {len(FILES)}个公开文件、Python语法、相对链接、常见隐私模式及源码哈希')
    print('本检查不扫描忽略目录或运行日志。打包必须严格使用FILES清单，不打包整个工作目录。')
