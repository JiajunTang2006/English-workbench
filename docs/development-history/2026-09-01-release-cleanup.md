# 2026-09-01 发布后目录清理

项目完成发布前测试和双平台空白版打包后，对工作目录进行了可恢复清理。

## 保留内容

- 当前前端、后端、插件、数据库迁移和第三方 Harness 源码；
- 自动化测试、匿名测试数据、构建脚本和依赖锁文件；
- 当前产品、架构、数据生命周期、构建与精选开发记录；
- macOS ARM64 与 Windows x64 的 `0.9.0-beta.2` 空白版交付包；
- 双平台内容指纹和后续差分更新基线。

## 已清理内容

- `.venv`、根目录 `node_modules`、Harness `node_modules` 和 PyInstaller 缓存；
- `build`、`dist`、未压缩 `.app`、旧版本安装包与重复安装包；
- UI 验证截图、一次性复现脚本、旧归档、编辑器状态和会话缓存；
- 旧数据交付包及与项目源码无关的课程资料。

上述内容统一移动到 macOS 废纸篓中的：

```text
EnglishWorkBench_cleanup_20260901_1900
```

清理后项目目录约为 427 MB，废纸篓备份约为 3.2 GB。清空废纸篓前仍可恢复。

## 重新建立开发环境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
npm ci
```

如需重新构建包含 Harness 的 macOS 包，先在 `vendor/deepseek-harness-upstream` 恢复 pnpm 依赖，再运行：

```bash
python3 tools/build_teachmate_runtime.py
./build_macos_app.sh
```
