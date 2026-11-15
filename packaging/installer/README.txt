豆包鼠标语音助手 · 一键安装程序
================================

安装包：DoubaoMouseBridge-v0.2.0-rc.6-installer.zip
内容：安装脚本 + 已打包的 EXE（无需另装 Python）

使用步骤
--------
1. 把整个 ZIP 解压到一个文件夹（不要在 ZIP 里直接运行）。
2. 双击 install.cmd。
3. 脚本会依次完成：
   - 校验安装包与 EXE 的 SHA-256（checksums.txt）
   - 关闭正在运行的旧实例
   - 把旧安装备份为 DoubaoMouseBridge.backup-<时间戳>
   - 安装到 %LOCALAPPDATA%\Programs\DoubaoMouseBridge
   - 启动程序并显示进程 PID
4. 看到 "Install OK" 即表示安装完成。程序常驻右下角通知区域，
   右键图标选择“打开设置”。

边界
----
- 只写 %LOCALAPPDATA%\Programs\DoubaoMouseBridge，不装驱动、不写注册表启动项。
- 不提供卸载；换版本重跑一次即可，旧目录会以 backup 形式保留。
- 安装失败时不会破坏现有安装：校验不通过会直接中止。

源码版本
--------
同版本另附 doubao-mouse-bridge-v0.2.0-rc.6-source.zip，需要 uv / Python 3.13。
