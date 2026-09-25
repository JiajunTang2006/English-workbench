---
name: diagnose-student
description: 在明确学生与考试范围后，给出匿名化趋势、薄弱项与待教师确认项；区分事实、推断与待确认项。
---

# diagnose-student

针对单个学生的学情诊断，默认匿名。

## 工作流

1. **明确范围**：从 `list_teaching_scopes` 取得 `term_id` 与 `student_id`、`exam_id`。
2. **获取档案**：调用 `get_student_learning_profile`，默认 `identify=false`（匿名 `student_anon_id`）。
   - 仅在教师明确要求且场景下，才传 `identify=true` 取得可识别姓名/学号。
3. **关联事实**：结合 `get_review_plan_facts`（班级/考试层面共性）区分「该生个体薄弱」与「全班共性」。

## 输出纪律

- 默认使用匿名标识，不在结论中暴露学生姓名/学号。
- 明确区分：事实（来自插件数据）/ 推断（模型判断）/ 待教师确认项。
- 不把单次考试结论扩大为长期能力判断；本只读插件不直接修改档案。
- 在 Workbench 内置的“学生诊断”能力中，可将有证据的画像变化提交为待确认草稿；必须由教师在 Workbench 确认后才会写入正式画像。
