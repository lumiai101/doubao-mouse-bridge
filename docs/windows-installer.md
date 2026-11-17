# Windows 安装版（本机受控候选，非正式稳定版）

安装版把 Python 运行环境与助手一同封装，使用者不需要另外安装 Python 或 uv。2026-10-31 已取得新版豆包一次启停证据及用户的本机安装、旧入口迁移许可；仅按该许可继续本机复验，不把构建或登记成功当作运行通过。其他设备或未获相应许可时，不替换日常入口。真实出字、登录和长期稳定必须分别记录，未执行即未验证。

## 使用约定

| 操作 | 行为 |
|---|---|
| 手动打开桌面或开始菜单快捷方式 | 打开设置；已经运行时呼出已有设置，不创建第二份监听 |
| 开机并登录 Windows | 若安装时选择自启，则只进入通知区域托盘 |
| 设置窗口右上角 X | 收起到托盘，继续运行 |
| 托盘“退出”或“退出程序” | 结束本次运行，不取消下次登录自启 |
| 再次使用 | 双击桌面快捷方式 |

安装器默认只安装给当前用户，不要求管理员权限，不创建服务或后台守护程序。用户可选择桌面快捷方式和登录自启；安装结束后的“打开助手”默认不勾选，静默安装不会自动启动。正在运行时应先从托盘正常退出，不强杀进程。

## 开发者构建

Windows x64、Python 3.13，先安装 `requirements.txt` 和 `requirements-build.txt`。Inno Setup 7.1.0 编译器须来自官方发行且签名有效。准备包含 Python、Tcl/Tk、Pillow、six、pynput、pystray 许可的目录；LGPL 组件同时提供对应版本源码及替换说明。构建不会执行所收集的依赖源码。

许可目录可用仓库脚本生成，生成后再按实际运行时补齐 Tcl/Tk 许可：

```powershell
.venv\Scripts\python.exe -B -I scripts\collect_licenses.py . 许可证与源码目录
```

脚本会按 PyPI 记录的 SHA-256 校验 pynput 与 pystray 的源码包，并把 `packaging/licenses/` 里的 GPLv3 与 Apache-2.0 正文分别补进 `pynput-*/COPYING`、`pystray-*/COPYING` 与 `openssl/LICENSE.txt`。构建用 Python 一份 Tcl/Tk 许可正文都没有、缺少 GPLv3/Apache-2.0 正文、或依赖在 PyPI 上取不到任何可校验的分发件时，一律硬失败、非零退出，不会打警告后继续出包。

Tcl 与 Tk 的许可正文并不相同，脚本按组件分别落为 `tcl-tk/tcl-license.terms` 与 `tcl-tk/tk-license.terms`。构建用 CPython 只随附其中一份时会向 stderr 打一条警告，并把实际带的是哪一份写进 `tcl-tk/NOTICE.txt` 与脚本的 JSON 输出，不静默略过。

pystray 0.19.5 在 PyPI 上只有 wheel、没有 sdist。该包是纯 Python，wheel 内的 `.py` 就是随包分发的源码本体，因此脚本退回 wheel 作为对应源码，并校验 PyPI 记录的 sha256；不使用 GitHub 生成的 tag 归档，因为它的字节哈希不稳定。

```powershell
.venv\Scripts\python.exe -X utf8 -B -I scripts\build_windows.py --output "新的输出目录" --iscc "ISCC.exe的完整路径" --licenses "许可证与源码目录"
```

输出目录必须不存在。`app/DoubaoMouseBridge/` 是单目录应用；`installer/` 是 EXE 安装器；`application-sha256.json` 是应用文件摘要。不要分发只含主 EXE 而缺少 `_internal/` 的目录。

`packaging/windows-installer.iss` 不在文件里保留版本号兜底值，直接调 ISCC 时必须显式传入 `AppVersion`，否则编译会以 `AppVersion is required` 中止：

```powershell
ISCC.exe /DAppVersion=0.2.0-rc.7 /DAppSourceDir="app\DoubaoMouseBridge" packaging\windows-installer.iss
```

构建只复制 `src/` 下的公开 Python 文件，不打包日志、用户配置或虚拟环境。安装版保留原有兼容检查、风险记录和父子构建身份核查；没有通过的检查不会因打包自动解除。

## 安全自检与验收边界

```powershell
DoubaoMouseBridge.exe --verify-package "证据JSON完整路径"
```

此入口导入打包依赖、创建并关闭隐藏 Tk 窗口、派生一个不调用语音的工作子进程，检查父子构建摘要及控制台句柄，写入 JSON。它不启动按键监听，不连接豆包、不触发录音、不修改自启。这只证明打包依赖与派生进程可用，**不证明真实语音、托盘全生命周期或登录自启成功**。

托盘交互另用 `scripts/verify_tray.py` 在隔离命名空间中验证；主入口和语音仍遵守 `runtime-verification.md`。不得把隔离探针当作已加载安装版的现场验收。

## 升级、卸载与回退

安装目录固定在当前用户的本地应用目录 `Programs/DoubaoMouseBridge`。登录入口由安装任务在当前用户 Startup 创建，不增加第二个 Run 登记。升级保留安装任务选择。退出本次运行不删除该入口。

旧源码入口只能在核对目标、参数及归属并备份后迁移；不根据名称模糊删除。兼容检查未通过，不改现用 Startup。卸载仅移除本安装器拥有的文件和快捷方式，不清空 `DoubaoMouseBridge` 的日志、身份或恢复状态目录，不动豆包/罗技数据。回退前分别确认新实例退出和豆包录音结束；不知道录音是否结束就停止切换。

未进行代码签名的 EXE 可能受到 Windows SmartScreen 或安全软件提示。不得导入根证书、关闭系统保护或承诺不会提示。当前候选不公开发布；二进制公开分发前还须完成全部第三方许可与源包对应审查。
