; 版本号只有命令行一个来源，不在本文件里留兜底值，避免不带 /DAppVersion 的编译
; 产出落在 0.2.0-rc.x 版本线之外的安装器。
#ifndef AppVersion
  #error AppVersion is required, pass it with /DAppVersion
#endif
#ifndef AppSourceDir
  #error AppSourceDir is required
#endif
#ifndef OutputDir
  #define OutputDir "..\dist\windows"
#endif
; 生产构建使用下面的默认值；只有隔离实装试验才覆盖这两个值，
; 避免试验安装占掉正式 AppId 的卸载登记、或被正在运行的正式实例挡住。
#ifndef AppId
  #define AppId "{{86667029-2D73-490C-A0FD-59830E33C283}"
#endif
#ifndef AppMutex
  #define AppMutex "Local\DoubaoMouseVoiceBridge"
#endif

[Setup]
AppId={#AppId}
AppName=豆包鼠标语音助手
AppVersion={#AppVersion}
AppPublisher=LumiAI
DefaultDirName={localappdata}\Programs\DoubaoMouseBridge
DefaultGroupName=豆包鼠标语音助手
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AppMutex={#AppMutex}
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=DoubaoMouseBridge-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\DoubaoMouseBridge.exe
#ifdef AppIcon
SetupIconFile={#AppIcon}
#endif

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: checkedonce
Name: "startup"; Description: "登录 Windows 后自动启动（只驻留托盘）"; Flags: checkedonce

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\豆包鼠标语音助手"; Filename: "{app}\DoubaoMouseBridge.exe"
Name: "{userdesktop}\豆包鼠标语音助手"; Filename: "{app}\DoubaoMouseBridge.exe"; Tasks: desktopicon
Name: "{userstartup}\豆包鼠标语音助手"; Filename: "{app}\DoubaoMouseBridge.exe"; Parameters: "--background"; Tasks: startup

[Run]
Filename: "{app}\DoubaoMouseBridge.exe"; Description: "打开豆包鼠标语音助手"; Flags: nowait postinstall skipifsilent unchecked
