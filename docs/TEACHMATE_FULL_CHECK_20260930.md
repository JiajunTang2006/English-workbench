# TeachMate 全量检查 · 2026-09-30

当前源码版本 **1.0.0**，数据库迁移 **20260930_0034**。本次已完成当前源码的全量自动化检查及主要界面截图走查；检查中发现的问题已修复，最终运行无失败用例。

后续已开展配置模型的线上合成课堂测试，包含额外修复和最终回归；最新结果及仍待验收的边界见 [模拟课堂与真实模型测试](TEACHMATE_REALISTIC_TEST_20260930.md)。本文件保留此前检查时点的结果。

## 最终结果

| 检查 | 结果 | 记录 |
| --- | --- | --- |
| 全量后端测试 | 1403 通过，139.61 秒 | [backend.log](validation/teachmate-full-check-20260930/backend.log) |
| 全量前端测试 | 161 通过，20.56 秒 | [frontend.log](validation/teachmate-full-check-20260930/frontend.log) |
| 全量浏览器页面回归 | 51 通过，1 跳过，约 1.9 分钟 | [e2e.log](validation/teachmate-full-check-20260930/e2e.log) |
| JavaScript 语法 | 通过 | [syntax.log](validation/teachmate-full-check-20260930/syntax.log) |
| 版本与迁移 | 前端、锁文件、后端均为 1.0.0；迁移单一 head 为 0034；空库迁移通过 | [integrity.log](validation/teachmate-full-check-20260930/integrity.log) |
| 依赖 | Python 无依赖冲突，关键模块可导入；Node 顶层依赖完整 | [integrity.log](validation/teachmate-full-check-20260930/integrity.log)、[node-dependencies.log](validation/teachmate-full-check-20260930/node-dependencies.log) |
| 插件与 skill | 7 个内置清单校验通过；专项推题 skill 文件及格式有效 | [integrity.log](validation/teachmate-full-check-20260930/integrity.log)、[skill.log](validation/teachmate-full-check-20260930/skill.log) |
| 第三方资源完整性 | 5 项已登记摘要一致 | [integrity.log](validation/teachmate-full-check-20260930/integrity.log) |
| 敏感文件检查 | 通过；未发现符合检查规则的违规文件或内容 | [integrity.log](validation/teachmate-full-check-20260930/integrity.log) |
| 改动空白检查 | 通过 | 最终执行 `git diff --check`，无输出、退出码 0 |

跳过的是窄窗口项目中的名单和成绩写入用例，原设计仅在桌面项目执行一次；桌面同用例通过。这不是失败重试或临时排除。

## 检查覆盖

运行了仓库现有全部后端和前端测试，以及全部 9 个浏览器回归文件。检查包括鉴权与运行时信息、名单和成绩导入、教师对话模块、学生解析与隐私处理、模型与插件控件、教学任务、材料版本恢复、专项练习、作答核对、复测导入和打印流程。第三方 Provider 相关验证使用模拟响应，不进行付费模型调用。

页面回归覆盖 1440 × 900 与 1100 × 800，并在布局用例中检查 780px 窗口。验证了后台重绘与跨工作区切换保留未保存正文、光标和文件选择；练习按题查看、阅读来源鉴权下载及页码定位、预览清理；学生打印含阅读原文且不含答案与解析。已目视检查本轮对话、练习编辑和小窗口截图，无明显横向溢出或控件重叠。

截图：[桌面对话](validation/teachmate-full-check-20260930/conversation-desktop.png)、[较窄窗口练习](validation/teachmate-full-check-20260930/practice-narrow.png)、[780px 对话](validation/teachmate-full-check-20260930/conversation-small.png)。

## 本次发现并修复

1. **快速取消切换仍会跳到另一页面。** 点击 WorkBench 后立即点回当前 TeachMate，旧动画计时器未取消，330ms 后错误切换。调整为每次点击先取消待执行切换，再同步当前导航与滑块；减少动画或缺少动画节点的路径同样取消旧计时器。新回归用例在修复前明确失败，修复后在桌面和较窄窗口均通过。[修复前复现日志](validation/teachmate-full-check-20260930/rapid-switch-before.log)。
2. **两处验收断言过时。** 运行时版本断言仍写死 0.9.0-beta.2，现改为核对版本源；欢迎页断言仍要求旧文案，现与现有界面一致，保留图片加载、提问入口和配置区域隐藏的校验。
3. **敏感文件检查误报小数时间戳。** 视频制作记录的时间戳小数尾数被当作手机号。检查规则不再从小数尾数截取号码，保留原始文档不修改；新增用例验证时间戳可通过、真实号码仍被拒绝且输出不泄露号码。随后重新运行全量后端及敏感文件检查通过。

交互脚本的资源版本已更新，避免页面继续使用旧缓存。软件仍为 1.0.0 源码版。

## 尚需真实环境验收

- **真实模型质量与用量：** 本次未测第三方厂商线上回答自然度、题目教学质量或改造前后实际 Token 节省比例；不能据自动化通过认定这些指标达标。原计划 G01 的模型对照及 G04 的课堂走查仍待完成。
- **桌面客户端 PDF 渲染：** 已验证 PDF 下载、预览地址、页码和清理；无头浏览器检查不能替代实际桌面客户端内置 PDF 阅读器的显示验收。
- **发布与真实数据：** 没有制作安装包、插件 ZIP 或发布清单，没有升级真实教学数据库、提交或推送代码。所有业务测试使用隔离临时数据。D、F 按此前约定不新增。

敏感文件检查是源码交付规则检查，扫描 Git 跟踪及未忽略文件，跳过大文件、压缩脚本和二进制内容；结果不等于完整安全审计或外部依赖漏洞审计。
