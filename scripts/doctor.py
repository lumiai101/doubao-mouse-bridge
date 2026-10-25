"""一键健康自检：把仓库现有验证与运行实例状态串成一条命令。

只读：不修改配置、不重启程序、不改启动项、不安装依赖。
每项检查输出文本结果，最后打印汇总并据失败数返回退出码。
托盘探针仅检查隔离实例；安装哈希不证明 RPC 在线，不触发录音。
Startup 仅只读核对快捷方式，不验证真实登录。

用法：
    python -X utf8 -B -I scripts/doctor.py            # 默认跳过登录启动项
    python -X utf8 -B -I scripts/doctor.py --quick    # 跳过 unittest 和托盘探针
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src' / 'doubao_mouse_bridge.py'
LOG = SRC.with_suffix('.log')
VENV_PYW = ROOT / '.venv' / 'Scripts' / 'pythonw.exe'

RESULTS = []


def report(name, status, detail, evidence=None):
    """status: pass / warn / fail / skip。evidence 是给用户核对的具体位置或命令。"""
    RESULTS.append({
        'check': name,
        'status': status,
        'detail': detail,
        'evidence': evidence or '',
    })


def run(cmd, timeout=120, cwd=ROOT):
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def check_unittest():
    rc, out, err = run([sys.executable, '-X', 'utf8', '-B', '-I',
                        '-m', 'unittest', 'discover', '-s', 'tests', '-q'], timeout=180)
    tail = (err + out).strip().splitlines()[-3:]
    if rc == 0:
        m = re.search(r'Ran (\d+) tests', err + out)
        count = m.group(1) if m else '?'
        report('unittest', 'pass', f'{count} 项测试通过',
               '在仓库目录运行: python -m unittest discover -s tests -v')
    else:
        report('unittest', 'fail', f'退出码 {rc}: {" | ".join(tail)}',
               '在仓库目录运行: python -m unittest discover -s tests -v')


def check_verify_tray():
    script = ROOT / 'scripts' / 'verify_tray.py'
    if not script.is_file():
        report('verify_tray', 'fail', f'脚本不存在: {script}')
        return
    rc, out, err = run([sys.executable, '-X', 'utf8', '-B', '-I', str(script)],
                       timeout=60)
    if rc == 0:
        report('verify_tray', 'pass', '真实 Tk/pystray 隔离探针通过',
               f'运行: python -X utf8 -B -I {script.relative_to(ROOT)}')
    else:
        tail = ((err or '') + (out or '')).strip().splitlines()[-3:]
        report('verify_tray', 'fail', f'退出码 {rc}: {" | ".join(tail)}')


def check_side_button():
    script = ROOT / 'scripts' / 'verify_side_button.py'
    if not script.is_file():
        report('verify_side_button', 'fail', f'脚本不存在: {script}')
        return
    rc, out, err = run([sys.executable, '-X', 'utf8', '-B', '-I', str(script)],
                       timeout=30)
    if rc != 0:
        report('verify_side_button', 'fail',
               f'退出码 {rc}: {(err or out).strip()[:200]}')
        return
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        report('verify_side_button', 'fail', f'输出非 JSON: {exc}')
        return
    status = data.get('side_button_status', 'unknown')
    detail_text = (data.get('side_button') or '').replace('\n', ' | ')
    if status == '按键设置匹配':
        report('verify_side_button', 'pass',
               f'侧键状态 {status}: {detail_text}',
               '只读核对，不代表实体按键已测试')
    elif status in ('侧键未确认', '侧键需检查'):
        # 不修改用户设置；警告不表示绑定正确或实体侧键验收通过。
        report('verify_side_button', 'warn',
               f'侧键状态 {status}: {detail_text}',
               '需用户在 Logi Options+ 与豆包设置中核对绑定是否一致')
    else:
        report('verify_side_button', 'fail',
               f'侧键状态 {status}: {detail_text}')


def check_release_gate():
    script = ROOT / 'scripts' / 'check_release.py'
    rc, out, err = run([sys.executable, '-X', 'utf8', '-B', '-I', str(script)],
                       timeout=60)
    if rc == 0:
        report('check_release', 'pass', '发布门禁通过',
               f'运行: python -X utf8 -B -I {script.relative_to(ROOT)}')
    else:
        tail = ((err or '') + (out or '')).strip().splitlines()[-3:]
        report('check_release', 'fail', f'退出码 {rc}: {" | ".join(tail)}')


def _enum_doubao_processes():
    """返回 [(pid, name, cmdline, exe), ...]，失败时返回 None。"""
    ps = (
        "$ErrorActionPreference = 'Stop'; "
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        "Where-Object { $_.CommandLine -like '*doubao_mouse_bridge*' } | "
        "Select-Object ProcessId, Name, CommandLine, ExecutablePath | "
        "ConvertTo-Json -Compress"
    )
    rc, out, err = run(['powershell', '-NoProfile', '-Command', ps], timeout=15)
    if rc != 0:
        return None
    if not out.strip():
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    return [(p['ProcessId'], p['Name'], p.get('CommandLine') or '',
             p.get('ExecutablePath') or '') for p in data]


def check_process():
    procs = _enum_doubao_processes()
    if procs is None:
        report('process', 'warn', '无法枚举进程（PowerShell 调用失败）',
               '手工: Get-CimInstance Win32_Process | '
               'Where-Object {$_.CommandLine -like "*doubao_mouse_bridge*"}')
        return
    if not procs:
        report('process', 'fail', '未发现运行中的适配进程',
               f'预期从 {VENV_PYW} 启动')
        return
    expected_root = str(ROOT).lower()
    good, bad = [], []
    for pid, name, cmd, exe in procs:
        if expected_root in cmd.lower() or expected_root in exe.lower():
            good.append(f'PID {pid} ({name})')
        else:
            bad.append(f'PID {pid} ({name}) 路径不匹配: {cmd[:120]}')
    if bad:
        report('process', 'fail',
               f'{len(good)} 个进程指向当前仓库; {len(bad)} 个不匹配: {"; ".join(bad)}',
               '应全部从新仓库启动')
    else:
        report('process', 'pass',
               f'{len(good)} 个进程指向当前仓库: {", ".join(good)}',
               '任务管理器→详细信息→python*.exe→命令行')


def check_log_health():
    if not LOG.is_file():
        report('log_health', 'warn', f'日志文件不存在: {LOG.name}',
               '首次启动程序后才会生成')
        return
    try:
        lines = LOG.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError as exc:
        report('log_health', 'warn', f'读取日志失败: {exc}')
        return
    if not lines:
        report('log_health', 'warn', '日志为空')
        return
    last_line = lines[-1]
    m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', last_line)
    freshness = '未知'
    if m:
        try:
            from datetime import datetime
            last_ts = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
            age = (datetime.now() - last_ts).total_seconds()
            freshness = f'{int(age)} 秒前'
        except ValueError:
            pass
    errors = [ln for ln in lines[-200:]
              if ' ERROR ' in ln or 'Traceback' in ln or '故障锁存' in ln]
    if errors:
        report('log_health', 'fail',
               f'最近 200 行内有 {len(errors)} 条错误/锁存: {errors[-1][:120]}',
               f'打开 {LOG} 查看')
    else:
        report('log_health', 'pass',
               f'最近 200 行无错误；最后事件 {freshness}',
               f'打开 {LOG}')


def check_doubao_install():
    sys.path.insert(0, str(ROOT / 'src'))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('bridge_probe', SRC)
        bridge = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = bridge
        spec.loader.exec_module(bridge)
    except Exception as exc:
        report('doubao_install', 'warn', f'加载 bridge 模块失败: {exc}')
        return
    finally:
        sys.path.pop(0)
    try:
        install = bridge.find_install()
    except Exception as exc:
        report('doubao_install', 'fail', f'未找到豆包输入法安装: {exc}')
        return
    rpc = install / 'rpc.dll'
    if not rpc.is_file():
        report('doubao_install', 'fail', f'rpc.dll 不存在于 {install}')
        return
    import hashlib
    digest = hashlib.sha256(rpc.read_bytes()).hexdigest()
    if digest in bridge.ALLOWED_RPC_SHA256:
        report('doubao_install', 'pass',
               f'豆包安装 {install.name}，RPC 哈希在白名单',
               f'路径: {install}')
    else:
        report('doubao_install', 'fail',
               f'RPC 哈希 {digest[:16]}... 不在白名单（豆包可能升级）',
               f'路径: {install}')


def check_startup_shortcut():
    """检查 Startup 中真正属于本适配的快捷方式。

    判定依据：快捷方式的 Target 或 Arguments 指向 doubao_mouse_bridge.py。
    名字含「豆包/鼠标」但目标不是适配程序的（如 Logi Options+）不算。
    """
    ps = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        "$startup = [Environment]::GetFolderPath('Startup'); "
        "Get-ChildItem -Path $startup -Filter *.lnk -ErrorAction SilentlyContinue | "
        "ForEach-Object { "
        "  $sh = New-Object -ComObject WScript.Shell; "
        "  $lnk = $sh.CreateShortcut($_.FullName); "
        "  [PSCustomObject]@{ Name = $_.Name; Target = $lnk.TargetPath; Args = $lnk.Arguments } "
        "} | ConvertTo-Json -Compress"
    )
    rc, out, err = run(['powershell', '-NoProfile', '-Command', ps], timeout=15)
    if rc != 0:
        report('startup_shortcut', 'warn', f'读取 Startup 失败: {err.strip()[:120]}',
               'Win+R 输 shell:startup 查看')
        return
    if not out.strip():
        report('startup_shortcut', 'warn', 'Startup 目录无快捷方式',
               'Win+R 输 shell:startup 查看')
        return
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    expected_root = str(ROOT).lower()
    adapter_links = []
    for entry in data:
        target = (entry.get('Target') or '').lower()
        args = (entry.get('Args') or '').lower()
        if 'doubao_mouse_bridge' in target or 'doubao_mouse_bridge' in args:
            adapter_links.append((entry['Name'], entry.get('Target', ''),
                                  entry.get('Args', '')))
    evidence = 'Win+R 输 shell:startup，右键快捷方式→属性'
    if not adapter_links:
        report('startup_shortcut', 'warn',
               'Startup 目录无指向本适配的快捷方式（不排除其他自启动机制）', evidence)
        return
    bad = [n for n, t, a in adapter_links
           if expected_root not in (t + ' ' + a).lower()]
    if bad:
        report('startup_shortcut', 'warn',
               f'以下适配启动项未指向当前仓库: {", ".join(bad)}', evidence)
    else:
        names = ', '.join(n for n, _, _ in adapter_links)
        has_bg = any('--background' in a for _, _, a in adapter_links)
        suffix = '，带 --background' if has_bg else '，未带 --background（登录后会弹设置窗）'
        report('startup_shortcut', 'pass',
               f'{len(adapter_links)} 个启动项指向当前仓库 ({names}){suffix}',
               evidence)


def main():
    parser = argparse.ArgumentParser(description='豆包鼠标桥一键健康自检')
    parser.add_argument('--quick', action='store_true',
                        help='跳过 unittest 与 verify_tray（仅做运行实例和只读配置核对）')
    parser.add_argument('--check-startup', action='store_true',
                        help='额外只读核对 Startup 快捷方式，不验证真实登录')
    args = parser.parse_args()

    RESULTS.clear()
    started = time.monotonic()
    print(f'# 豆包鼠标桥自检  root={ROOT}  quick={args.quick}\n')

    checks = [
        ('unittest', check_unittest),
        ('verify_tray', check_verify_tray),
        ('verify_side_button', check_side_button),
        ('check_release', check_release_gate),
        ('process', check_process),
        ('log_health', check_log_health),
        ('doubao_install', check_doubao_install),
        ('startup_shortcut', check_startup_shortcut),
    ]
    for name, check in checks:
        if name == 'startup_shortcut' and not args.check_startup:
            report(name, 'skip', '默认不检查登录启动项；可用 --check-startup 只读核对')
            continue
        if args.quick and name in ('unittest', 'verify_tray'):
            report(name, 'skip', '--quick 已跳过')
            continue
        try:
            check()
        except Exception as exc:
            report(name, 'fail', f'检查未完成: {type(exc).__name__}: {exc}')

    counts = {'pass': 0, 'warn': 0, 'fail': 0, 'skip': 0}
    for r in RESULTS:
        counts[r['status']] += 1
        icon = {'pass': '✅', 'warn': '⚠️', 'fail': '❌', 'skip': '⏭'}[r['status']]
        print(f"{icon} [{r['check']:18s}] {r['detail']}")
        if r['evidence']:
            print(f"   核对: {r['evidence']}")

    elapsed = time.monotonic() - started
    print(f"\n# 汇总  pass={counts['pass']}  warn={counts['warn']}  "
          f"fail={counts['fail']}  skip={counts['skip']}  用时 {elapsed:.1f}s")

    if counts['fail']:
        print('\n存在失败项，请勿据此提交新版本。')
        return 1
    if counts['warn']:
        print('\n存在警告项，建议人工核对后再决定。')
        return 0
    print('\n所选自动检查通过；不代表实体侧键、真实语音上屏或登录验收通过。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
