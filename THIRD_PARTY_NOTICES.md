# 第三方依赖与来源

项目自身代码使用MIT许可证。该许可不替代以下依赖和软件各自的许可。

| 组件 | 用途 | 版本 | 许可证与来源 |
|---|---|---|---|
| pynput | Windows键盘事件监听与单事件过滤 | 1.8.2 | LGPLv3，[PyPI](https://pypi.org/project/pynput/1.8.2/)、[源码](https://github.com/moses-palmer/pynput) |
| six | pynput的兼容性依赖 | 1.17.0 | MIT，[PyPI](https://pypi.org/project/six/1.17.0/) |
| pystray | Windows系统托盘常驻与右键菜单 | 0.19.5 | LGPLv3，[PyPI](https://pypi.org/project/pystray/0.19.5/)、[源码](https://github.com/moses-palmer/pystray) |
| pillow | 托盘状态图标图形生成 | 11.3.0 | MIT-CMU，[PyPI](https://pypi.org/project/pillow/11.3.0/)、[官网](https://python-pillow.github.io) |
| CPython | 运行时，标准库ctypes与tkinter | 实体验收3.13.12 | PSF License等，[许可证说明](https://docs.python.org/3/license.html) |
| uv | 可选安装工具 | 不随项目分发 | Apache-2.0或MIT，[项目](https://github.com/astral-sh/uv) |
| 豆包输入法 | 本机录音、识别和上屏 | 已核验0.9.2.7 | 专有软件，许可不由本项目授予 |
| Logi Options+ | 鼠标快捷键映射 | 用户自行安装 | 专有软件，许可不由本项目授予 |

源码包不捆绑上述依赖、第三方源码、Python运行时或豆包DLL。安装依赖会把依赖自己的许可证装入本机环境；若后续发布带依赖的二进制包，必须重新检查相应再分发义务，不能只附本项目MIT。

## 托盘图标来源

托盘的鼠标轮廓参考免费图标库 [Lucide Mouse](https://lucide.dev/icons/mouse)，采用 ISC 许可证；本项目用已有 Pillow 重新绘制，加上声波和状态角标，没有新增图标库运行依赖。原始图标与许可来源：[Lucide License](https://lucide.dev/license)。

以下声明适用于参考的 Lucide 图标：

```text
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION
OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## 方案研究来源

[VoiceWinBridge](https://github.com/littleWhiteDuck/VoiceWinBridge) 是早期本机接口接通试验和公开用法研究的参考。整理本方案时没有复制它的源码，也不分发其发布文件。初次研究时未确认该项目授予开源再分发许可，不把其可下载等同于可复制授权。

Windows事件过滤按[pynput官方FAQ](https://pynput.readthedocs.io/en/latest/faq.html)使用成熟库。最终代码仅编排已有监听能力和豆包本地接口，不实现录音或识别服务。

以上来源说明不是法律意见。特别是非公开豆包接口的使用与发布，仍需遵守第三方软件条款。无官方授权或厂商兼容性承诺。
