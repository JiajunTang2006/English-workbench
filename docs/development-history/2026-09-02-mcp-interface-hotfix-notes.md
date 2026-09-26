# 2026-09-02 MCP / MONI 接口补丁记录

> 用途：供下一次 macOS / Windows 补丁构建前复查。本文只记录脱敏的接口现象、
> 根因、修复位置和验证要求，不包含学生数据、API Key 或学校端返回内容。

## 1. TeachMate 请求了不存在的 Core API 路径

### 现象

- WorkBench 中已经存在班级和学生，但 TeachMate 无法读取班级列表或学生范围；
- 前端请求失败后只显示通用提示，容易被误判为 MONI 没有同步成功；
- 重启不会修复，因为每次都会请求同一个错误路径。

### 根因

TeachMate API 层曾请求：

- `/api/v1/core/classes`
- `/api/v1/core/students`

FastAPI Core Router 的真实公开路径是：

- `/api/v1/classes`
- `/api/v1/students`

`core.py` 是代码文件名，不是 URL 中的 `/core` 前缀。

### 修复与护栏

- `workbench-assets/teachmate-api.js`
  - `listClasses()` 改为请求 `/api/v1/classes`；
  - `listStudents()` 改为请求 `/api/v1/students`。
- `tests/test_p17_context_resolution.js`
  - 增加学生作用域必须访问已注册 Core 路由的行为测试；
  - 班级上下文解析测试统一使用真实 `/api/v1/classes` 路径。

## 2. MONI 同步后仍要点击“保存设置”才能在 TeachMate 选择班级

### 现象

- MONI 同步已经完成，WorkBench 也能显示班级和学生；
- WorkBench 的“班级列表”中显示 `711,712`；
- TeachMate 选择班级后提示无法读取所选班级；
- 在“班级与设置”中额外点击一次“保存设置”后暂时恢复。

### 根因

MONI 在规范化数据库中保留学校端正式班级名称，例如 `七年级11班`；WorkBench
为了简洁展示，会用 `normalizeClassName()` 将其归一为 `711`。TeachMate 原来的
`findClassIdByName()` 只去掉尾部“班”和空格，因此无法把 `711` 与
`七年级11班` 识别为同一班级。

点击“保存设置”会再次用 WorkBench 的缩写名称写入兼容状态，因而掩盖了名称
匹配缺陷。MONI 同步本身已经落库，不应该要求教师再次确认保存。

### 修复与护栏

- `workbench-assets/teachmate-api.js`
  - `findClassIdByName()` 复用 WorkBench 的 `normalizeClassName()`；
  - 归一后仍采用完整值精确相等，不使用包含匹配，避免 `711`、`712` 等相近班级
    误匹配；
  - MONI 同步后可直接取得真实数字 `class_id`，不再依赖“保存设置”。
- `tests/test_p17_context_resolution.js`
  - 新增 `七年级11班 → 711 → class_id=71`、
    `七年级12班 → 712 → class_id=72` 的回归测试；
  - 同时保留正式名称直接查询的兼容测试。
- `workbench.html`
  - 已提升 `teachmate-api.js` 的资源版本，避免补丁安装后继续命中旧缓存。

## 本次验证

- `node --test tests/test_p17_context_resolution.js`：7/7 通过；
- `node tools/check_syntax.js`：通过；
- `npm test`：139/139 通过；
- `git diff --check`：相关文件通过。

## 下次补丁构建清单

1. 用当前源码重新构建 macOS App 和 Windows EXE；旧打包产物不会自动获得修复。
2. 确认 `workbench.html` 中 `teachmate-api.js?v=...` 使用本次或更新的资源版本。
3. 使用一份全新的空白数据目录配置 MONI，只执行“测试并同步学生数据”。
4. 不点击“保存设置”，直接切换到 TeachMate。
5. 验证班级菜单可列出 MONI 班级，并能创建带正确数字 `class_id` 的班级分析会话。
6. 验证学生作用域查询访问 `/api/v1/students`，班级查询访问 `/api/v1/classes`，
   不得重新出现 `/api/v1/core/*`。
7. Windows 补丁需重新运行 `build_windows.bat`；不能只替换或分发旧 EXE。

