# 贡献说明

## 运行测试

测试环境先安装 `requirements.txt`。离线测试不要求安装豆包，不打开真实窗口、不录音、不安装全局输入监听；图标测试使用 Pillow，菜单测试使用 pystray。

```powershell
python -m pip install -r requirements.txt
python -X utf8 -B -I -m unittest discover -s tests -p "test_*.py" -v
python -B -I scripts\check_release.py
```

需要真实 Windows 桌面时，另运行 `python -X utf8 -B -I scripts\verify_tray.py`。它运行真实 Tk/pystray，替换语音和输入监听，不抢占现用实例、不打开麦克风。

运行应用按README安装依赖。不要把应用运行环境、日志和测试时的真实配置提交到仓库。

## 修改边界

- 保持豆包正常设置是快捷键唯一真源，不加入第二套独立触发键配置。
- 不把非公开接口调用成功等同于识别上屏成功。
- 不自动安装驱动、提升权限、创建服务或自启动。用户自行添加的登录启动项属于本机设置，不随源码提交。
- 修改DLL兼容逻辑时，先验证调用契约，不只改哈希。
- 涉及触发和上屏行为的改动，按 `docs/compatibility.md` 做实体鼠标回归。CI只覆盖离线逻辑。

## 报告问题

说明系统、豆包、Python和Logi Options+版本，快捷键组合、输入框类型、预期与实际结果。不要附完整豆包配置、原始音频或未脱敏日志。敏感问题遵守 `SECURITY.md`。

## 发布源码

维护者核对 `scripts/check_release.py` 的发布文件清单，再打包源码。安装产生的 `.venv` 可在开发时忽略，但不包含在源码压缩包中。发布ZIP不包含EXE或可移植Python环境，不能称为免安装版本。

发布源码 ZIP 不含 Git 仓库历史和运行数据。以 `scripts/check_release.py` 的 `FILES` 清单打包，保留 `.github`、`.gitignore` 和 `.gitattributes`。源码 ZIP 可作为 Release 下载附件，不代替展开的仓库源码。

`SOURCE_SHA256` 是按 LF 换行计算的发布指纹，不是实体语音验收证明。修改运行源后先审阅差异、运行测试并更新兼容性记录，再更新指纹。

在仓库目录运行：

```powershell
python -X utf8 -B -I scripts\build_release.py
```

脚本只复制 `FILES` 清单，先在隔离目录执行发布检查，再写入 `dist/doubao-mouse-bridge-v0.2.0-rc.1-source.zip` 与同名 `.sha256` 文件。它拒绝覆盖已有产物，不收录日志、配置、`.venv` 或 `.claude` 工作副本；最后将 ZIP 解压到另一隔离目录，再次核对清单和发布检查。它不运行实体语音，也不上传 GitHub。

原目录因本地额外文件被检查拦下时，不要删除其他工作副本或放宽检查。使用此脚本检查实际将公开的文件，并在提交前核对 `git diff --cached`。
