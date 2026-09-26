# WorkBench 桌面软件构建说明

WorkBench 现在可以作为独立桌面窗口运行：启动器仍然在本机启动 FastAPI、SQLite、TeachMate 和插件运行时，但通过 `pywebview` 将 `/workbench` 页面嵌入原生窗口，不再依赖 Safari/Chrome 标签页。前端、数据库和 API 地址保持不变，因此现有数据和插件无需迁移。

## 运行方式

- 已安装 `pywebview`：打开一个可调整大小的 WorkBench 原生窗口；关闭窗口只隐藏，后台服务和已确认任务继续运行。
- macOS 的 WorkBench 应用菜单、Windows 的系统托盘都提供“打开/显示窗口”和“完全退出”。只有“完全退出”才停止本地服务。
- macOS 点击红色关闭按钮后窗口会隐藏；再次点击 Dock/Finder 中的应用会恢复并置前。`Command + Q`、应用菜单“退出”及“完全退出”都会关闭窗口并停止后台服务。
- 未安装 `pywebview`：自动回退到默认浏览器，开发模式仍可正常使用。
- 启动入口统一打开 WorkBench；TeachMate 页面仍可在 WorkBench 内切换，避免启动时被 URL 参数强制带到 TeachMate。
- 同一空白数据目录只允许一个实例；重复启动会提示已有 WorkBench 正在运行。

## 开发环境安装

```bash
python -m pip install -e 'backend[desktop]'
```

也可以只安装桌面运行库：

```bash
python -m pip install 'pywebview>=5.3,<7'
```

## macOS 打包

先准备 Python 依赖和随包的 TeachMate 运行时，然后执行：

```bash
python3 tools/build_teachmate_runtime.py
./build_macos_app.sh
```

构建脚本先生成 `release/EnglishWorkBench.app` 和 `release/EnglishWorkBench.app.zip`；正式归档使用带平台、架构和版本号的空白版 ZIP，并配套保存内容指纹基线。脚本使用 `blank_launcher.py`，只把程序资源打进包，不携带用户数据库；用户数据首次运行时写入系统的 `English Workbench Blank` 目录。脚本会在 PyInstaller 前检查 `pywebview`；缺少时会直接提示安装命令，不会生成一个只能打开浏览器的“假桌面版”。

## Windows 打包

在 Windows 10/11 x64 上安装 Python 3.11 x64，然后双击：

```text
build_windows.bat
```

脚本会使用独立的 `.venv-windows`，默认安装稳定 Python Agent 所需依赖，并用 PyInstaller 生成 `dist\EnglishWorkBench\EnglishWorkBench.exe` 和 `release\EnglishWorkBench_Windows_x64.zip`；默认不需要 Node.js。关闭窗口后应用进入系统托盘；再次双击 EXE或托盘图标会恢复已有窗口，托盘“完全退出”会停止本地服务。完整步骤见 [WINDOWS_GUIDE.md](../WINDOWS_GUIDE.md)。PyInstaller 不支持从 macOS 交叉生成 Windows EXE，因此最终构建必须在 Windows 电脑上执行。

## 排查

如果桌面窗口启动失败，启动器会回退到浏览器并在控制台记录原因。可设置 `WORKBENCH_WEBVIEW_DEBUG=1` 查看 WebView 调试日志。首次发布给其他电脑时仍建议使用代码签名/公证（macOS）和安装包签名（Windows），这属于发行流程，不影响本地构建。
