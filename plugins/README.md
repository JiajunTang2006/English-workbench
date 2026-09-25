# TeachMate 插件目录

TeachMate 将插件分为两类：

- `bundled/`：随软件发布的内置插件，走同一套 Plugin Manager，但不可卸载；
- `installed/`：用户导入的第三方插件，安装后可启用、停用、健康检查和卸载。

插件包至少需要包含以下任意一个清单：

```text
.teachmate-plugin/plugin.json
.codex-plugin/plugin.json
```

MCP 插件使用 `runtime.transport=stdio`，TeachMate 会先执行 `tools/list` 健康检查，再只允许调用 manifest 中声明的工具。插件不得直接读取 TeachMate SQLite；需要数据时应通过稳定 API 或由 TeachMate 作为宿主代理。

当前本地安装 API：

```text
POST /api/v1/plugins/install
GET  /api/v1/plugins
POST /api/v1/plugins/{id}/enable
POST /api/v1/plugins/{id}/disable
GET  /api/v1/plugins/{id}/health
POST /api/v1/plugins/{id}/call
```

## 目录说明

- `packages/`：可分发、可安装的标准插件包（zip 根目录即插件根），附 `.sha256` 校验值，用于 `POST /api/v1/plugins/install`。
- `installed/`：已安装插件的源码树副本，便于比对与二次分发。运行时真正加载的是数据目录（`~/Library/Application Support/English Workbench Blank/plugins/installed`）中的那份，两边需保持同步。

当前内容：

- `packages/teachmate-codex-plugin-0.1.3.zip`：只读 MCP 插件，安装后 id 为 `teachmate_codex_plugin`。
