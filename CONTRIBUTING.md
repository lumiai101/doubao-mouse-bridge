# 贡献说明

## 运行测试

离线单测仅使用Python标准库，不要求安装豆包或pynput，不启动GUI、录音或全局钩子。

```powershell
python -B -I tests\test_bridge.py
python -B -I scripts\check_release.py
```

运行应用按README安装依赖。不要把应用运行环境、日志和测试时的真实配置提交到仓库。

## 修改边界

- 保持豆包正常设置是快捷键唯一真源，不加入第二套独立触发键配置。
- 不把非公开接口调用成功等同于识别上屏成功。
- 不自动安装驱动、提升权限、创建服务或自启动。
- 修改DLL兼容逻辑时，先验证调用契约，不只改哈希。
- 涉及触发和上屏行为的改动，按 `docs/compatibility.md` 做实体鼠标回归。CI只覆盖离线逻辑。

## 报告问题

说明系统、豆包、Python和Logi Options+版本，快捷键组合、输入框类型、预期与实际结果。不要附完整豆包配置、原始音频或未脱敏日志。敏感问题遵守 `SECURITY.md`。

## 发布源码

维护者核对 `scripts/check_release.py` 的发布文件清单，再打包源码。安装产生的 `.venv` 可在开发时忽略，但不包含在源码压缩包中。发布ZIP不包含EXE或可移植Python环境，不能称为免安装版本。

本包不含Git仓库历史，也不预设远端地址。上传GitHub时以本目录内容作为仓库根目录，包含 `.github`、`.gitignore` 和 `.gitattributes`。源码ZIP作为下载附件上传Release，不应代替展开的仓库源码。
