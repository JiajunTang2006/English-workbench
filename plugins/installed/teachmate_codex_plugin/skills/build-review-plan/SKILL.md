---
name: build-review-plan
description: 基于考试共同错误与知识点事实，生成带优先级、目标、课时、材料与复查指标的复习计划；每个计划项有事实依据与可验证结果。
---

# build-review-plan

把考试事实转化为可执行的复习计划。

## 工作流

1. **取事实**：`get_review_plan_facts`（`exam_id`，可选 `class_id`）得到共性错题维度与薄弱知识点、覆盖统计。
2. **可选补充**：`get_exam_snapshot` 看数据质量；`list_formal_materials` / `read_formal_material` 引用教师已确认资料作为复习材料。
3. **生成计划**：按 `common_error_dimensions` 与 `weak_knowledge_points` 排序优先级，输出目标、建议课时、材料与复查指标。

## 输出纪律

- 每个计划项必须标注事实依据（来自 `get_review_plan_facts` 的维度/知识点）。
- 给出可验证结果（如复查测验目标正确率），便于教师核对。
- 仅引用已确认正式资料；不暴露未确认附件正文。
