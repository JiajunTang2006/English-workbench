"""成长树规则（方案 §3.1）。

以下数值是可上线试点的产品初值，不是从现有学生数据训练出来的最优参数：
先模拟历史活动频率，再冻结一个学期的规则版本。规则版本不可变，教师调整
参数时新增版本，既有记录仍可按旧规则复算。

计分三段式（growth-v4）::

    本次拟奖励 r = 基础分 + 进步分 + 里程碑分
    进步分      = clamp(floor((本次得分率 - 同分支上次可比得分率) / step), 0, max)
    里程碑分    = clamp(floor((连续达成周数 - 1) / every), 0, max)
    本次实际奖励 = min(r, 当日该类别剩余额度, 当日总剩余额度, 当周总剩余额度)
    学期营养     = max(0, 本学期有效奖励合计 + 误录更正的净调整)

基础分人人相同，保证「做了就有」；进步分只相对学生自己的同类测评基线，
相同百分点提升得到相同奖励；里程碑分奖励连续达成，让持续投入的
学生更快。三者都不看绝对分数，退步不扣分，缺失不补零。

存储整数点数，并保留「原本可得、实际入账、封顶原因」。补录或改分影响封顶
分配时，按稳定的发生时间与事件 ID 顺序重算受影响日期/周。

带 teacher_confirmed_v1 标记的新教师补录按确认分值完整计入，不占用自动奖励
额度；既有未标记记录沿用原规则，撤销仍以原事件为依据。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from ...models.growth_entities import GrowthRuleVersion


DEFAULT_RULE_CODE = "growth-v4"
DEFAULT_TIMEZONE = "Asia/Shanghai"
# growth-v4 延续三段计分，改进可比测评的进步基线。
DAILY_TOTAL_CAP = 10
WEEKLY_TOTAL_CAP = 40

# 试点阶段阈值：随封顶同比放大，保持原节奏感（约每周 30 点）。
# 假设普通参与每周约 30 点，首次发芽约一周，最后阶段约 18.7 周。
DEFAULT_STAGES: list[dict[str, Any]] = [
    {"min": 0, "name": "种子", "icon": "🌰"},
    {"min": 30, "name": "发芽", "icon": "🌱"},
    {"min": 80, "name": "小树苗", "icon": "🌿"},
    {"min": 160, "name": "茁壮成长", "icon": "🪴"},
    {"min": 270, "name": "开花", "icon": "🌸"},
    {"min": 400, "name": "结果", "icon": "🍎"},
    {"min": 560, "name": "森林之星", "icon": "🌳"},
]

# 事件类型 -> 规则。daily_awards/weekly_awards 是「每天/每周最多几次」，
# daily_cap 是「每天该类别最多几点」。
DEFAULT_EVENT_RULES: dict[str, dict[str, Any]] = {
    "task_completed": {
        "label": "完成学习任务", "points": 2,
        "daily_awards": 2, "daily_cap": 4,
    },
    "correction_verified": {
        "label": "完成订正并确认", "points": 2,
        "daily_awards": 1, "daily_cap": 2,
    },
    "spaced_review": {
        "label": "间隔复习/达标复测", "points": 3,
        "daily_awards": 1, "daily_cap": 3,
    },
    "teacher_observation": {
        "label": "课堂/阅读表现", "points": 1,
        "daily_awards": 2, "daily_cap": 2,
    },
    "teacher_bonus": {
        "label": "教师手工加分", "points": 1,
        "daily_awards": 1, "daily_cap": 3,
    },
    "weekly_goal": {
        "label": "达成个人周目标", "points": 2,
        # 里程碑分：连续达成周数每 every 周加 1 分，封顶 max。
        "milestone": {"every": 2, "max": 3},
        "weekly_awards": 1, "daily_cap": 5,
    },
    # 自动事件：考试生效且学生实际参加（present、有有效分数）时生成。
    # 基础分人人相同；进步分相对该生近期同类测评的较高成绩。
    "exam_completed": {
        "label": "参加可比测评", "points": 2,
        "progress": {"step": 0.05, "max": 2},
        "daily_awards": 1, "daily_cap": 4,
    },
    # 历史手工记录：保留为历史依据，不进入新学期营养。
    "legacy_manual": {"label": "历史手工记录", "points": 0, "legacy": True},
    # 撤销/更正：引用原事件，产生净调整，不参与封顶。
    "reversal": {"label": "撤销/更正", "points": 0, "reversal": True},
}

# 手工类事件必须有事由；这里列出允许手工录入的事件类型。
MANUAL_EVENT_TYPES = frozenset({
    "task_completed", "correction_verified", "spaced_review",
    "teacher_observation", "teacher_bonus", "weekly_goal",
})

# 学业表现分支。默写只能支撑词汇/拼写，不能自动支撑阅读、听力。
DIMENSION_KEYS = ("vocabulary", "grammar", "reading", "listening", "writing")
DIMENSION_LABELS = {
    "vocabulary": "词汇", "grammar": "语法", "reading": "阅读",
    "listening": "听力", "writing": "写作",
}
# 指数平滑系数（待试点调整）。
SMOOTHING_ALPHA = 0.3
# 少于三个不同日期的可比测评时只显示记录与「样本较少」。
MIN_COMPARABLE_OBSERVATIONS = 3


def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        # 缺少 tzdata 时退回中国标准时间，保证额度按当地业务日计算。
        return timezone(timedelta(hours=8))


def as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def business_date_for(occurred_at: datetime, timezone_name: str = DEFAULT_TIMEZONE) -> date:
    """按学校时区把发生时间落到当地业务日期。"""
    return as_aware(occurred_at).astimezone(_tz(timezone_name)).date()


def week_key_for(day: date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def previous_week_key(week_key: str) -> str | None:
    """ISO 周键的上一个自然周；格式非法时返回 ``None``。"""
    try:
        year, week = str(week_key).split("-W")
        start = date.fromisocalendar(int(year), int(week), 1)
    except (ValueError, AttributeError):
        return None
    iso = (start - timedelta(days=7)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def compute_progress_bonus(*, score_rate: float | None, baseline_rate: float | None,
                           step: float, cap: int) -> int:
    """进步分：相对学生自己上一次可比得分率的提升。

    ``score_rate`` / ``baseline_rate`` 缺失（``None``）或没有提升时返回 0，
    绝不把缺失当成进步，也不因退步扣分。每提升 ``step`` 记 1 分，封顶 ``cap``。
    """
    if score_rate is None or baseline_rate is None:
        return 0
    try:
        step_value = float(step)
    except (TypeError, ValueError):
        return 0
    if step_value <= 0:
        return 0
    delta = round(float(score_rate) - float(baseline_rate), 6)
    if delta <= 0:
        return 0
    # 加极小量抵消浮点误差，避免 0.15/0.05 被截成 2。
    return max(0, min(int(cap), int((delta + 1e-9) / step_value)))


def compute_milestone_bonus(*, consecutive_weeks: int | None, every: int, cap: int) -> int:
    """里程碑分：连续达成周数每 ``every`` 周记 1 分，封顶 ``cap``。

    连续 1~``every`` 周为 0 分（里程碑是额外奖励，不替代基础分）。
    """
    if consecutive_weeks is None:
        return 0
    try:
        span = int(every)
    except (TypeError, ValueError):
        return 0
    if span <= 0:
        return 0
    return max(0, min(int(cap), (int(consecutive_weeks) - 1) // span))


def stage_for(points: int, stages: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    thresholds = list(stages or DEFAULT_STAGES)
    current = thresholds[0]
    for stage in thresholds:
        if points >= int(stage.get("min", 0)):
            current = stage
    return current


def stage_index_for(points: int, stages: Iterable[dict[str, Any]] | None = None) -> int:
    thresholds = list(stages or DEFAULT_STAGES)
    index = 0
    for position, stage in enumerate(thresholds):
        if points >= int(stage.get("min", 0)):
            index = position
    return index


def next_stage_for(points: int, stages: Iterable[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    for stage in list(stages or DEFAULT_STAGES):
        if int(stage.get("min", 0)) > points:
            return stage
    return None


def stage_progress(points: int, stages: Iterable[dict[str, Any]] | None = None) -> float:
    """阶段内进度 (当前营养 - 当前阶段起点) / (下一阶段起点 - 当前阶段起点)。"""
    thresholds = list(stages or DEFAULT_STAGES)
    current = stage_for(points, thresholds)
    upcoming = next_stage_for(points, thresholds)
    if upcoming is None:
        return 1.0
    start = int(current.get("min", 0))
    end = int(upcoming.get("min", 0))
    if end <= start:
        return 1.0
    return max(0.0, min(1.0, (points - start) / (end - start)))


def build_default_rule_version() -> GrowthRuleVersion:
    """构造内置默认规则版本（未持久化）。"""
    return GrowthRuleVersion(
        code=DEFAULT_RULE_CODE,
        status="active",
        timezone=DEFAULT_TIMEZONE,
        stage_thresholds_json=[dict(stage) for stage in DEFAULT_STAGES],
        event_rules_json={key: dict(value) for key, value in DEFAULT_EVENT_RULES.items()},
        daily_total_cap=DAILY_TOTAL_CAP,
        weekly_total_cap=WEEKLY_TOTAL_CAP,
    )


def compute_awards(events: list[dict[str, Any]], *, rule: GrowthRuleVersion) -> list[dict[str, Any]]:
    """按发生时间/事件 ID 顺序重算奖励。

    ``events`` 每项需含：``id``、``event_type``、``occurred_at``（aware）、
    ``business_date``、可选 ``proposed_points``、可选 ``progress_points``、
    可选 ``reverses_event_id``。返回与输入同序的奖励明细，含 ``proposed_points``
    / ``applied_points`` / ``cap_reason`` / ``week_key`` / ``business_date``。

    拟奖励按三段合成：``基础分 + 进步分 + 里程碑分``。显式传入 ``proposed_points``
    时以它为准（教师手工覆盖点数的类型）；否则由规则点数加进步分（事件载荷携带、
    由服务端按可比测评算好）与里程碑分（本函数按连续达成周数现场计算）合成。
    """
    stages = rule.stage_thresholds_json or DEFAULT_STAGES
    event_rules = rule.event_rules_json or DEFAULT_EVENT_RULES
    daily_cap = int(rule.daily_total_cap or DAILY_TOTAL_CAP)
    weekly_cap = int(rule.weekly_total_cap or WEEKLY_TOTAL_CAP)

    ordered = sorted(
        events,
        key=lambda item: (
            item["business_date"],
            as_aware(item["occurred_at"]),
            item.get("id") or 0,
        ),
    )
    daily_total: dict[date, int] = defaultdict(int)
    weekly_total: dict[str, int] = defaultdict(int)
    daily_category: dict[tuple[date, str], int] = defaultdict(int)
    daily_category_awards: dict[tuple[date, str], int] = defaultdict(int)
    weekly_category_awards: dict[tuple[str, str], int] = defaultdict(int)
    # Corrections change the effective event set. Exclude their targets before
    # allocating caps, so a previously capped valid activity can take the slot.
    reversed_ids = {event.get("reverses_event_id") for event in ordered
                    if event.get("reverses_event_id") is not None}
    # 里程碑分：本函数一次只处理一名学生，按已出现的达成周现场计算连续周数。
    goal_weeks: set[str] = set()

    results_by_event: dict[int, dict[str, Any]] = {}
    for event in ordered:
        etype = event["event_type"]
        day = event["business_date"]
        week = week_key_for(day)
        explicit = event.get("proposed_points")
        proposed = int(explicit) if explicit is not None else 0
        progress_bonus = int(event.get("progress_points") or 0)
        milestone_bonus = 0
        rule_def = event_rules.get(etype) or {}
        applied = 0
        reason = "none"

        if event.get("id") in reversed_ids and not rule_def.get("reversal"):
            applied, reason = 0, "reversed"
        elif rule_def.get("legacy"):
            applied, reason = 0, "legacy"
        elif rule_def.get("reversal") or event.get("reverses_event_id"):
            applied, reason = 0, "reversal"
        elif event.get("teacher_confirmed") and etype in MANUAL_EVENT_TYPES:
            # New teacher-confirmed records bypass caps and do not consume the
            # automatic/legacy allowance. Reversal handling above still applies.
            proposed = max(0, proposed)
            applied = proposed
            progress_bonus = 0
        else:
            if rule_def.get("milestone"):
                goal_weeks.add(week)
                streak = 0
                cursor: str | None = week
                while cursor is not None and cursor in goal_weeks:
                    streak += 1
                    cursor = previous_week_key(cursor)
                cfg = rule_def["milestone"]
                milestone_bonus = compute_milestone_bonus(
                    consecutive_weeks=streak,
                    every=int(cfg.get("every") or 1),
                    cap=int(cfg.get("max") or 0),
                )
            if proposed <= 0:
                proposed = (int(rule_def.get("points") or 0)
                            + max(0, progress_bonus) + milestone_bonus)
            daily_awards_limit = rule_def.get("daily_awards")
            weekly_awards_limit = rule_def.get("weekly_awards")
            if daily_awards_limit is not None and daily_category_awards[(day, etype)] >= int(daily_awards_limit):
                applied, reason = 0, "category_daily_awards"
            elif weekly_awards_limit is not None and weekly_category_awards[(week, etype)] >= int(weekly_awards_limit):
                applied, reason = 0, "category_weekly_awards"
            else:
                remaining = [daily_cap - daily_total[day], weekly_cap - weekly_total[week]]
                if rule_def.get("daily_cap") is not None:
                    remaining.append(int(rule_def["daily_cap"]) - daily_category[(day, etype)])
                applied = max(0, min([proposed] + remaining))
                if applied < proposed:
                    reason = "capped"
                daily_total[day] += applied
                weekly_total[week] += applied
                daily_category[(day, etype)] += applied
                daily_category_awards[(day, etype)] += 1
                weekly_category_awards[(week, etype)] += 1

        event_id = event.get("id")
        detail = {
            "event_id": event_id,
            "event_type": etype,
            "business_date": day,
            "week_key": week,
            "proposed_points": proposed,
            "applied_points": applied,
            "cap_reason": reason,
            "progress_points": max(0, progress_bonus),
            "milestone_points": milestone_bonus,
        }
        if event_id is not None:
            results_by_event[event_id] = detail
    # 保持调用方传入顺序，便于与事件列表对齐。
    return [results_by_event.get(event.get("id"), {
        "event_id": event.get("id"),
        "event_type": event["event_type"],
        "business_date": event["business_date"],
        "week_key": week_key_for(event["business_date"]),
        "proposed_points": int(event.get("proposed_points") or 0),
        "applied_points": 0,
        "cap_reason": "unprocessed",
        "progress_points": int(event.get("progress_points") or 0),
        "milestone_points": 0,
    }) for event in events]


def term_points_from_awards(awards: Iterable[dict[str, Any]]) -> int:
    """学期营养 = max(0, 有效奖励合计 + 误录更正净调整)。"""
    total = sum(int(item["applied_points"]) for item in awards
                if item["cap_reason"] != "legacy")
    return max(0, total)
