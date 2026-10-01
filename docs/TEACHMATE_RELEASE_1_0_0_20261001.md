# English Workbench 1.0.0 发行检查

日期：2026-10-01。

## 文件与平台

macOS 安装包由提交前的本轮源码构建，Apple Silicon arm64，最低 macOS 13.5（内置 Node 24.16.0 的实际系统要求）。Windows ZIP 使用维护者提供的原文件，未重打包，嵌入版本为 1.0.0，数据库结构为 20261001_0035。

## macOS 验证

- 最终 DMG 完整性及实际应用签名一致性检查通过；使用本地 ad-hoc 签名，未获 Apple Developer 签名或公证。
- 从最终 DMG 启动应用，服务返回版本 1.0.0 与结构 20261001_0035；未回退到浏览器，未出现启动异常。
- 新建独立目录首次启动后，students、classes、exams、student_item_results 均为 0。安装包未包含业务数据库或私钥。
- 教学知识库包含 121 条索引、6 类题型路由，专项推题插件随包提供。
- 使用实际应用可执行文件调用考试概览与教学知识查询：返回合法 JSON，合成考试结果正确，证据写入正确。
- 使用包内 Node 与 Harness、实际生产启动参数离线握手成功，关闭后子进程回收；没有发送模型请求。
- 后台全量回归 1458 项通过，1 项跳过；最后的桌面运行时与递归依赖打包相关回归 61 项通过。
- 原生窗口截图因自动审批担忧窗口绑定指向旧实例而未继续；上述安装版服务、独立空库、桥接和引擎检查使用明确的最终 DMG 与测试目录。

## Windows 验证边界

ZIP CRC 完整性通过；确认主 EXE、版本 1.0.0、迁移 0035、专项推题插件存在。未发现随包数据库、私钥、业务附件或日志目录。原包未内置 Harness 运行时或教学知识库，此包使用精简 Python Agent。未在 Windows 做首次启动或 AI 实机测试，不将 macOS 的验收结论推及 Windows。

## 空白版的数据含义

发行文件不预装学生、班级、考试、成绩、原卷或模型密钥。首次使用新目录从空库开始；空白版继续使用已有用户目录时会保留数据，不会替教师清空旧记录。

默认目录为 macOS 的 `~/Library/Application Support/English Workbench Blank/`、Windows 的 `%APPDATA%\English Workbench Blank\`。允许显式设置 WORKBENCH_DATA_DIR；本轮验收使用独立测试目录，不读取原教学数据库。

## 校验值

```text
f0422b26194957fa685995ff21d2962db23026f7f4ab7d4cf0091bbdda598a0f  EnglishWorkBench_macOS_arm64_Blank_1.0.0.dmg
0162e27fd3ca5245ed5ad28602d99c6c097957b195195dd065076ada41c021f2  EnglishWorkBench_Windows_x64_Blank_1.0.0.zip
```
