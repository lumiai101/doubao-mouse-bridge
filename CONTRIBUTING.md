# 贡献说明

## 运行测试

离线单测仅使用Python标准库，不要求安装豆包或pynput，不启动GUI、录音或全局钩子。

```powershell
python -X utf8 -B -I -m unittest discover -s tests -p "test_*.py" -v
python -B -I scripts\check_release.py
```

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
