# English WorkBench Windows x64 构建与使用说明

macOS 不能用 PyInstaller 直接生成 Windows EXE。本目录是一份不含用户数据的 Windows 构建源码包，需要在 Windows 10/11 x64 电脑上完成一次本机构建。

## 一、Windows 电脑准备

首次构建需要联网下载 Python 依赖。请先安装：

1. Python 3.11（x64），建议从 python.org 安装，并勾选 `Add Python to PATH`。项目正式构建固定使用 3.11，避免较新解释器与桌面依赖尚未适配。
2. Microsoft Edge WebView2 Runtime。Windows 10/11 通常已经自带；若应用窗口无法创建，再从微软官网下载运行时。

默认稳定版不需要 Node.js。只有在完整开发源码中显式使用 `build_windows.ps1 -WithHarness` 构建实验性 Harness 版本时，才需要 Node.js 22.19 或更高版本；精简 Windows 构建包不包含这套 Node 开发源码。

建议把源码解压到较短的本地路径，例如 `C:\TeachMateBuild`。不要直接在 ZIP 内运行，也尽量避免放在 OneDrive 同步目录。

## 二、一键生成 EXE

1. 双击项目根目录的 `build_windows.bat`。
2. 脚本会创建独立的 `.venv-windows`，不会修改系统 Python 环境。
3. 脚本会安装固定版本的 Python 依赖和 PyInstaller，并使用已验证的 Python Agent 运行时；不需要安装或构建 Node/Harness。智谱、DeepSeek、自定义 OpenAI 兼容模型、普通聊天和内置教学插件都可使用这条稳定路径。
4. 首次构建通常需要 10—30 分钟，具体取决于网络和电脑性能。请保持窗口打开直至显示 `Build completed successfully`。

完成后会生成：

- `dist\EnglishWorkBench\EnglishWorkBench.exe`：本机直接运行入口。
- `release\EnglishWorkBench_Windows_x64.zip`：可复制到另一台 Windows x64 电脑的完整绿色软件包。
- `release\EnglishWorkBench_Windows_x64.zip.sha256`：ZIP 完整性校验值。

不能只复制单独一个 EXE。PyInstaller 使用的是 `onedir` 模式，必须保留 `dist\EnglishWorkBench` 整个目录，或直接分发生成的 ZIP。

## 三、软件运行规则

- 双击 `EnglishWorkBench.exe` 打开独立桌面窗口。
- 点击窗口右上角关闭按钮只会隐藏窗口，TeachMate 和本地任务继续在后台运行。
- 再次双击 EXE，或双击系统托盘图标，会恢复并置前已有窗口，不会启动第二套数据库服务。
- 右键系统托盘图标，选择“完全退出”，才会关闭窗口并停止本地后端。
- 软件只监听 `127.0.0.1:8765`，不会向局域网开放服务。调用用户配置的第三方模型 API 时需要互联网。

## 四、空白数据与升级

构建产物不包含数据库、学生信息、考试文件、对话、API Key 或导入记录。首次启动后，数据保存在：

```text
%APPDATA%\English Workbench Blank\
```

升级前建议在旧版的“班级与设置 → 数据安全”中导出备份。新版解压到新目录运行后，再从界面导入备份。不要把 `%APPDATA%` 数据目录直接塞进发布 ZIP。

## 五、常见问题

### Windows SmartScreen 提示未知发布者

本地生成的 EXE没有商业代码签名证书。可以选择“更多信息 → 仍要运行”。如果将软件公开分发，应使用可信 Windows 代码签名证书给 EXE和安装包签名。

### 提示 Python 找不到或版本不符

确认安装的是 Python 3.11 x64，而不是 3.12、3.13 或 Microsoft Store 占位版本。关闭构建窗口后重新打开 `build_windows.bat`；在命令提示符中运行 `py -3.11 --version` 应能显示 `Python 3.11.x`。

### 提示构建包不完整或文件无法识别

请先在资源管理器中右键 ZIP 选择“全部解压”，再进入解压后的文件夹运行 `build_windows.bat`，不要直接在压缩包预览窗口里运行。建议解压到 `C:\TeachMateBuild`；如果安全软件隔离了文件，请重新下载并对照随包提供的 SHA-256 校验值。

### 构建阶段下载失败

检查网络、代理和安全软件后重新运行脚本。`.venv-windows`、`.windows-tools` 和已下载依赖会被复用。

### 关闭窗口后找不到软件

查看任务栏右下角的系统托盘；双击图标可恢复窗口，右键选择“完全退出”可结束后台。也可以再次双击 `EnglishWorkBench.exe` 唤醒已有窗口。

### 8765 端口被占用

先从系统托盘“完全退出”旧实例。如果旧进程异常卡住，可在任务管理器中结束 `EnglishWorkBench.exe`，再重新启动。

### 页面能打开但模型不能回复

在设置中检查第三方 API 地址、模型名称和密钥。软件本地功能不要求公网，但第三方模型调用取决于对应 API 是否可访问。
