---
name: analyze-exam
description: 分析某次考试的确定性统计、数据质量与证据，输出事实、解释、局限与行动建议。所有关键结论必须关联 evidence ID。
---

# analyze-exam

用于把一次考试转化为可审计的教学分析。

## 工作流

1. **确认作用域**：调用 `list_teaching_scopes` 确认 `term_id`、班级与 `exam_id`。
2. **获取快照**：调用 `get_exam_snapshot`（传入 `exam_id` 与 `term_id`）拿到实考/缺考/缺分、均分、最高/最低与各班年级排名，以及 `data_quality`。
3. **补充事实**：对关键数字调用 `get_review_plan_facts` 取得题型均分、共性错题维度与薄弱知识点。
4. **展开证据**：对支撑结论调用 `get_evidence` 取得计算来源（公式、分子/分母、来源文件/页码/题号）。

## 输出纪律

- 只陈述确定性事实；对缺考/缺分等不完整数据显式标注局限。
- 每个关键结论后附 `evidence_id`，不得编造未提供的数字。
- 不修改任何成绩或学生档案（插件为只读）。
