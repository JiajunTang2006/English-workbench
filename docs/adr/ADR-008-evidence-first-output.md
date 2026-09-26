# ADR-008：证据先于结论

**日期：** 2026-08-17  
**状态：** 已接受

## 背景

当前 `HarnessRunAdapter._try_extract_evidence()` 从模型最终输出文本中反向解析证据。这种方式存在根本缺陷：

- 模型可能生成不存在的数字或结论，反向提取的"证据"不可靠；
- 无法保证报告中的数字与本地工具计算结果一致；
- 证据与结论的因果关系不明确，无法审计；
- 模型输出格式变化会导致证据提取失败。

## 决定

**证据在工具完成时创建，先于模型结论。** 流程为：

```text
工具查询 → 本地计算 → 写 analysis_evidence → 返回匿名事实 + evidence_id → 模型引用
```

模型只能引用已有 `evidence_id`，不能自行生成证据。`HarnessRunAdapter._try_extract_evidence()` 在 Harness 切换完成后删除。

## 备选方案

1. **继续从模型输出反向提取证据**：不可靠，无法保证数字一致性。
2. **模型直接访问原始数据**：违反"模型只读"原则，且模型可能暴露学生身份。
3. **不使用证据，只展示模型文本**：无法审计，教师无法验证结论来源。

## 原因

- 本地计算结果是唯一可信事实源，模型只负责解释和推理；
- `evidence_id` 建立了结论与数据的显式因果关系，可审计；
- 匿名事实 + `evidence_id` 的返回方式既保护隐私又保证可追溯；
- 强制结构化报告 Schema 要求 `findings[]` 每项必须有 `evidence_ids`，`recommendations[]` 每项必须有 `supports`。

## 后果

- 工具返回值格式固定为 `{ facts: ..., evidence_id: "..." }`；
- 服务端校验：没有 `evidence_ids` 的 finding 被拒绝；
- 结构化报告 Schema 包含 `answer_type`、`summary`、`findings[]`、`recommendations[]`、`limitations[]`、`scope_snapshot`、`schema_version`；
- 输出验证失败时最多 repair 一次；仍失败则保存可读降级文本，状态为 `degraded`；
- 模型收到的学生身份全部脱敏（匿名编号），模型不能调用白名单以外工具。

## 事实源

| 数据 | 权威事实源 | 说明 |
|---|---|---|
| 考试统计、分数分布、分层 | WorkBench SQLite + 本地计算 | 模型只能通过只读工具访问 |
| 证据 | `analysis_evidence` (SQLite) | 工具完成时写入，模型引用 |
| 教师确认后的评价 | WorkBench SQLite | AI 草稿不能覆盖教师确认文本 |

## 回滚方式

- 证据先于结论是新增逻辑，不修改现有 `analysis_evidence` 表结构；
- 如需回滚，恢复 `_try_extract_evidence()` 并放宽 Schema 校验即可，但不建议作为长期方案。

## 删除时机

- `HarnessRunAdapter._try_extract_evidence()` 在 U3-02 完成后立即删除；
- 删除前确认所有教育工具已返回 `evidence_id`，且报告 Schema 校验已生效。
