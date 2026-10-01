"""Merge task-based reading without changing question IDs or score facts."""
from alembic import op
import sqlalchemy as sa

revision = "20261001_0035"
down_revision = "20260930_0034"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for column in ("question_type", "section_name"):
        connection.execute(sa.text(
            f"UPDATE exam_questions SET {column} = replace({column}, '任务型阅读', '阅读理解') "
            f"WHERE {column} LIKE '%任务型阅读%'"
        ))
    # Freeze this migration's mapping rather than importing mutable app code.
    settings = sa.table("app_settings", sa.column("key", sa.String), sa.column("value_json", sa.JSON))
    for key, value in connection.execute(sa.select(settings.c.key, settings.c.value_json)
                                         .where(settings.c.key.like("paper_distribution:%"))):
        if value.get("subject_key", "english") != "english":
            continue
        rows = value.get("rows", [])
        if not any(r.get("question_type") == "任务型阅读" for r in rows):
            continue
        merged = {}
        for row in rows:
            name = "阅读理解" if row["question_type"] == "任务型阅读" else row["question_type"]
            numbers = row.get("question_numbers", "").strip()
            if name in merged:
                merged[name]["question_numbers"] = "、".join(
                    v for v in (merged[name]["question_numbers"], numbers) if v)
            else:
                merged[name] = {**row, "question_type": name, "question_numbers": numbers}
        order = ("听力理解", "阅读理解", "完形填空", "词汇运用", "语法填空", "书面表达")
        value = {**value, "rows": [merged[t] for t in order if t in merged],
                 "revision": value.get("revision", 0) + 1}
        connection.execute(settings.update().where(settings.c.key == key).values(value_json=value))

    workspace = sa.table("workspace_states", sa.column("term_id", sa.Integer),
                         sa.column("state_json", sa.JSON), sa.column("revision", sa.Integer))
    for term_id, state, version in connection.execute(sa.select(workspace)):
        changed = False
        for row in state.get("errors", []):
            if isinstance(row, dict) and isinstance(row.get("type"), str) and "任务型阅读" in row["type"]:
                row["type"] = row["type"].replace("任务型阅读", "阅读理解")
                changed = True
        if changed:
            connection.execute(workspace.update().where(workspace.c.term_id == term_id)
                               .values(state_json=state, revision=version + 1))

    # Older school imports may have stored section totals as well as item facts.
    # Consolidate those totals without touching the underlying item results.
    dimensions = sa.table("score_dimensions", sa.column("id", sa.Integer), sa.column("exam_id", sa.Integer),
                          sa.column("name", sa.String), sa.column("max_score", sa.Float))
    parts = sa.table("exam_dimension_scores", sa.column("id", sa.Integer),
                     sa.column("exam_score_id", sa.Integer), sa.column("dimension_id", sa.Integer),
                     sa.column("score", sa.Float))
    legacy = list(connection.execute(sa.select(dimensions).where(dimensions.c.name.like("%任务型阅读%"))).mappings())
    for old in legacy:
        name = old["name"].replace("任务型阅读", "阅读理解")
        target = connection.execute(sa.select(dimensions).where(
            dimensions.c.exam_id == old["exam_id"], dimensions.c.name == name)).mappings().first()
        if target is None:
            connection.execute(dimensions.update().where(dimensions.c.id == old["id"]).values(name=name))
            continue
        existing = {r["exam_score_id"]: r for r in connection.execute(sa.select(parts)
                    .where(parts.c.dimension_id == target["id"])).mappings()}
        for part in list(connection.execute(sa.select(parts).where(parts.c.dimension_id == old["id"])).mappings()):
            previous = existing.get(part["exam_score_id"])
            if previous:
                connection.execute(parts.update().where(parts.c.id == previous["id"])
                                   .values(score=previous["score"] + part["score"]))
                connection.execute(parts.delete().where(parts.c.id == part["id"]))
            else:
                connection.execute(parts.update().where(parts.c.id == part["id"])
                                   .values(dimension_id=target["id"]))
        connection.execute(dimensions.update().where(dimensions.c.id == target["id"])
                           .values(max_score=target["max_score"] + old["max_score"]))
        connection.execute(dimensions.delete().where(dimensions.c.id == old["id"]))


def downgrade():
    # Scores and identities remain valid; merged categories cannot be split
    # reliably without the original teacher's classification.
    pass
