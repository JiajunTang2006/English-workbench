"""学科注册表：把「学科」从散落的 ``if 学科 == "英语"`` 变成一份集中声明。

学科配置集中声明四类行为：

1. **换词表**——成绩列名、入学成绩、分数趋势、排名、能力免责说明按学科改词；
2. **裁剪模块**——默写 / 背诵 / 写作 三个模块默认只对语文、英语开启，
   其余学科进来就看不到这三个入口（要开只需改本文件对应行的 ``modules``）；
3. **换题型与分析维度**——成绩录入、学生画像和成长表现按学科题型归类；
   Agent 使用对应的诊断维度与错误归因候选。
4. **限制知识库适用范围**——目前内置教学知识库只覆盖英语，仅英语会读取这套路由和课标依据。

**旧数据层键名一律保留**：``scores.英语``、``entrance_english`` 列、
``exam_type=english_total`` 继续兼容旧数据。学科配置为安装级单学科选择，
切换时不会迁移或重写历史成绩；英语列名只作为英语导入的历史别名。

新增学科时在 ``SUBJECTS`` 声明展示词、题型、Agent 诊断维度与成长题型映射；
英语专用知识库仅对英语启用，其他学科走通用证据框架。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from ..question_types import ENGLISH_PAPER_QUESTION_TYPES

# 未显式选择学科时的默认学科。历史上本产品是英语教学工作台，因此默认英语。
DEFAULT_SUBJECT_KEY = "english"

# The teacher's paper distribution uses the same types and order as the
# existing English ability chart; granular question-entry aliases stay below.

# 导航模块 key，与前端 MODULES 一一对应。
# CORE_MODULE_KEYS：所有学科都保留的模块。
CORE_MODULE_KEYS: tuple[str, ...] = (
    "dash",
    "stu",
    "growth",
    "score",
    "homework",
    "errors",
    "todo",
    "settings",
)
# LANGUAGE_MODULE_KEYS：可被学科裁剪的三个模块。
# 默写与写作是语言学科的强需求（英语单词默写 / 语文古诗文默写与作文），
# 背诵则同属记忆型训练。默认只对语文、英语开启；要给其它学科开启，
# 把对应学科的 modules 从 _CORE_ONLY 改成 _FULL_MODULES 即可。
LANGUAGE_MODULE_KEYS: tuple[str, ...] = ("dictation", "recite", "writing")

_FULL_MODULES: tuple[str, ...] = CORE_MODULE_KEYS + LANGUAGE_MODULE_KEYS
_CORE_ONLY: tuple[str, ...] = CORE_MODULE_KEYS
_GENERAL_ERROR_CAUSES = ("审题偏差", "知识点掌握", "方法与步骤", "运算或操作", "证据与推理", "表达与规范")
_GENERAL_ANALYSIS_DIMENSIONS = ("知识理解", "方法运用", "综合应用", "表达规范")

# 每个学科必须提供的文案键。前端按这些键取词，缺键会导致界面回退到通用文案。
LABEL_KEYS: tuple[str, ...] = (
    "score_total",        # 成绩管理里的「总分」项名、考试类型选项
    "entrance_score",     # 学生档案与名册里的入学成绩
    "score_column",       # 成绩表列名、编辑单元格的可读标签
    "score_short",        # 明细表头与趋势图系列名（比 score_column 短）
    "score_single",       # 数据源未提供单科成绩时的提示用语
    "score_trend",        # 趋势图标题
    "score_ranking",      # 排名文案（导入导出列名）
    "exam_default",       # 新建考试时的默认名称
    "ability_disclaimer", # 成长森林的能力免责说明
)


def _labels(label: str, **overrides: str) -> dict[str, str]:
    """按学科名生成默认词表；措辞需要特例时用 overrides 覆盖。"""
    values = {
        "score_total": f"{label}总分",
        "entrance_score": f"入学{label}",
        "score_column": f"{label}成绩",
        "score_short": f"{label}分数",
        "score_single": f"{label}单科成绩",
        "score_trend": f"{label}分数趋势",
        "score_ranking": f"{label}排名",
        "exam_default": f"{label}考试",
        "ability_disclaimer": f"不代表{label}水平",
    }
    values.update(overrides)
    return values


@dataclass(frozen=True)
class Subject:
    """一个学科的完整声明。字段只读，请勿在运行期改写注册表。"""

    key: str
    label: str
    # 老师学科字段（设置页可自由编辑）的默认值，用于顶栏「初中语文工作台」这类标题。
    teacher_subject_default: str
    # 该学科保留的导航模块（前端按自己的 MODULES 顺序过滤，这里只作白名单集合）。
    modules: tuple[str, ...]
    # 模块标题覆盖，如语文把「默写成绩」显示为「古诗文默写」；未覆盖的用前端原名。
    module_labels: dict[str, str]
    # 成绩录入的题型下拉清单。
    question_types: tuple[str, ...]
    # AI 诊断可使用的学科错误分析维度。只在证据支持时选用。
    error_causes: tuple[str, ...]
    # 前端与 Agent 共用的学科分析维度名称。
    analysis_dimensions: tuple[str, ...]
    # 将题型映射到成长表现维度，避免成长报告固定按英语题型统计。
    question_dimension_map: dict[str, str]
    # 界面文案词表，键见 LABEL_KEYS。
    labels: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "teacher_subject_default": self.teacher_subject_default,
            "modules": list(self.modules),
            "module_labels": dict(self.module_labels),
            "question_types": list(self.question_types),
            "error_causes": list(self.error_causes),
            "analysis_dimensions": list(self.analysis_dimensions),
            "question_dimension_map": dict(self.question_dimension_map),
            "labels": dict(self.labels),
        }


SUBJECTS: tuple[Subject, ...] = (
    Subject(
        key="chinese",
        label="语文",
        teacher_subject_default="初中语文",
        modules=_FULL_MODULES,
        module_labels={"dictation": "古诗文默写", "recite": "课文背诵", "writing": "作文训练"},
        question_types=("基础知识", "古诗文默写", "文言文阅读", "现代文阅读", "名著阅读", "作文"),
        error_causes=("审题偏差", "字词基础", "古诗文积累", "阅读理解", "证据与推理", "表达与规范"),
        analysis_dimensions=("基础积累", "阅读鉴赏", "思维推理", "语言表达"),
        question_dimension_map={"基础知识": "基础积累", "古诗文默写": "基础积累", "文言文阅读": "阅读鉴赏", "现代文阅读": "阅读鉴赏", "名著阅读": "阅读鉴赏", "作文": "语言表达"},
        labels=_labels("语文"),
    ),
    Subject(
        key="math",
        label="数学",
        teacher_subject_default="初中数学",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "填空题", "计算题", "解答题", "应用题"),
        error_causes=("审题偏差", "概念理解", "公式与方法", "运算失误", "推理过程", "书写规范"),
        analysis_dimensions=("概念理解", "运算能力", "推理论证", "实际应用"),
        question_dimension_map={"选择题": "概念理解", "填空题": "概念理解", "计算题": "运算能力", "解答题": "推理论证", "应用题": "实际应用"},
        labels=_labels("数学"),
    ),
    Subject(
        key="english",
        label="英语",
        teacher_subject_default="初中英语",
        modules=_FULL_MODULES,
        module_labels={},
        question_types=(
            "听力",
            "阅读理解",
            "完形填空",
            "选词填空",
            "单词拼写",
            "语法填空",
            "作文",
        ),
        error_causes=("审题", "词汇", "语法", "定位", "推断", "表达"),
        analysis_dimensions=("听力理解", "阅读理解", "词汇运用", "语法运用", "书面表达"),
        question_dimension_map={"听力": "听力理解", "阅读理解": "阅读理解", "完形填空": "阅读理解", "选词填空": "词汇运用", "单词拼写": "词汇运用", "语法填空": "语法运用", "作文": "书面表达"},
        labels=_labels("英语"),
    ),
    Subject(
        key="physics",
        label="物理",
        teacher_subject_default="初中物理",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "填空题", "实验探究题", "计算题", "综合应用题"),
        error_causes=("审题偏差", "概念理解", "规律与模型", "实验方法", "推理与计算", "表达与单位"),
        analysis_dimensions=("概念理解", "规律推理", "实验探究", "综合应用"),
        question_dimension_map={"选择题": "概念理解", "填空题": "概念理解", "实验探究题": "实验探究", "计算题": "规律推理", "综合应用题": "综合应用"},
        labels=_labels("物理"),
    ),
    Subject(
        key="chemistry",
        label="化学",
        teacher_subject_default="初中化学",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "填空题", "实验探究题", "推断题", "计算题"),
        error_causes=("审题偏差", "概念与性质", "反应规律", "实验方法", "推理与计算", "表达与规范"),
        analysis_dimensions=("物质性质", "反应规律", "实验探究", "综合应用"),
        question_dimension_map={"选择题": "物质性质", "填空题": "物质性质", "实验探究题": "实验探究", "推断题": "反应规律", "计算题": "综合应用"},
        labels=_labels("化学"),
    ),
    Subject(
        key="biology",
        label="生物",
        teacher_subject_default="初中生物",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "填空题", "识图作答题", "实验探究题", "简答题"),
        error_causes=("审题偏差", "概念理解", "结构与功能", "图表识读", "实验推理", "表达与术语"),
        analysis_dimensions=("生命观念", "科学思维", "实验探究", "图表与表达"),
        question_dimension_map={"选择题": "生命观念", "填空题": "生命观念", "识图作答题": "图表与表达", "实验探究题": "实验探究", "简答题": "科学思维"},
        labels=_labels("生物"),
    ),
    Subject(
        key="politics",
        label="道德与法治",
        teacher_subject_default="初中道德与法治",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "判断题", "简答题", "材料分析题", "实践探究题"),
        error_causes=("审题偏差", "概念理解", "材料提取", "观点辨析", "论据与推理", "表达与规范"),
        analysis_dimensions=("核心概念", "材料解读", "价值辨析", "论证表达"),
        question_dimension_map={"选择题": "核心概念", "判断题": "核心概念", "简答题": "论证表达", "材料分析题": "材料解读", "实践探究题": "价值辨析"},
        labels=_labels("道德与法治"),
    ),
    Subject(
        key="history",
        label="历史",
        teacher_subject_default="初中历史",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "填空题", "材料解析题", "简答题", "论述题"),
        error_causes=("审题偏差", "史实掌握", "时序与因果", "材料提取", "史论结合", "表达与规范"),
        analysis_dimensions=("时空观念", "史料实证", "历史解释", "综合表达"),
        question_dimension_map={"选择题": "时空观念", "填空题": "时空观念", "材料解析题": "史料实证", "简答题": "历史解释", "论述题": "综合表达"},
        labels=_labels("历史"),
    ),
    Subject(
        key="geography",
        label="地理",
        teacher_subject_default="初中地理",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "填空题", "读图分析题", "简答题"),
        error_causes=("审题偏差", "区域认知", "图表识读", "空间定位", "综合推理", "表达与术语"),
        analysis_dimensions=("区域认知", "地图技能", "综合思维", "人地协调"),
        question_dimension_map={"选择题": "区域认知", "填空题": "区域认知", "读图分析题": "地图技能", "简答题": "综合思维"},
        labels=_labels("地理"),
    ),
    Subject(
        key="it",
        label="信息技术",
        teacher_subject_default="初中信息技术",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "判断题", "操作题", "编程题"),
        error_causes=("审题偏差", "概念理解", "操作流程", "算法与逻辑", "调试排错", "表达与规范"),
        analysis_dimensions=("信息意识", "计算思维", "数字化实践", "创新与责任"),
        question_dimension_map={"选择题": "信息意识", "判断题": "信息意识", "操作题": "数字化实践", "编程题": "计算思维"},
        labels=_labels("信息技术"),
    ),
    Subject(
        key="other",
        label="其他",
        teacher_subject_default="初中",
        modules=_CORE_ONLY,
        module_labels={},
        question_types=("选择题", "填空题", "判断题", "简答题", "综合题"),
        error_causes=_GENERAL_ERROR_CAUSES,
        analysis_dimensions=_GENERAL_ANALYSIS_DIMENSIONS,
        question_dimension_map={"选择题": "知识理解", "填空题": "知识理解", "判断题": "知识理解", "简答题": "表达规范", "综合题": "综合应用"},
        # 「其他」不带学科名，词表退回不带学科前缀的通用措辞，避免出现「其他总分」。
        labels=_labels(
            "其他",
            score_total="总分",
            entrance_score="入学成绩",
            score_column="成绩",
            score_short="分数",
            score_single="单科成绩",
            score_trend="分数趋势",
            score_ranking="排名",
            exam_default="考试",
            ability_disclaimer="不代表学科水平",
        ),
    ),
)

SUBJECT_KEYS: tuple[str, ...] = tuple(item.key for item in SUBJECTS)

_SUBJECT_INDEX: dict[str, Subject] = {item.key: item for item in SUBJECTS}
# 允许老师直接填学科名（「语文」「初中语文」）时也能识别，用于读路径容错。
_SUBJECT_ALIAS_INDEX: dict[str, str] = {}
for _item in SUBJECTS:
    _SUBJECT_ALIAS_INDEX[_item.label.strip().lower()] = _item.key
    _SUBJECT_ALIAS_INDEX[_item.teacher_subject_default.strip().lower()] = _item.key
del _item


def is_valid_subject_key(value: Any) -> bool:
    """严格校验：只有注册表里的 key 才算合法（写路径用）。"""
    return str(value or "").strip().lower() in _SUBJECT_INDEX


def normalize_subject_key(value: Any) -> str:
    """容错归一：无法识别时回退默认学科（读路径用，避免脏值把界面打空）。"""
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_SUBJECT_KEY
    if text in _SUBJECT_INDEX:
        return text
    return _SUBJECT_ALIAS_INDEX.get(text, DEFAULT_SUBJECT_KEY)


def get_subject(value: Any) -> Subject:
    """按 key（或学科名）取学科声明，永远返回一个可用对象。"""
    return _SUBJECT_INDEX[normalize_subject_key(value)]


_ABILITY_ALIASES: dict[str, dict[str, str]] = {
    "math": {"数学运算": "运算能力"},
}


def canonical_ability_label(subject: Subject, value: Any) -> str:
    """将明确同义的能力标签统一到该学科注册表；未知标签保留供教师核对。"""
    label = str(value or "").strip()
    normalized = label.casefold().replace(" ", "")
    for known in subject.analysis_dimensions:
        if known.casefold().replace(" ", "") == normalized:
            return known
    for alias, known in _ABILITY_ALIASES.get(subject.key, {}).items():
        if alias.casefold().replace(" ", "") == normalized:
            return known
    return label


def get_selected_subject(session) -> Subject:
    """读取当前安装级学科配置；用于 Agent、导入和分析流程共享同一学科语境。"""
    if session is None:
        return get_subject(DEFAULT_SUBJECT_KEY)
    from ..models import AppSetting

    stored = session.get(AppSetting, "subject_key")
    return get_subject(stored.value_json if stored is not None else DEFAULT_SUBJECT_KEY)


def subject_switch_blocker(session) -> str | None:
    """This installation stores academic facts per term, not per subject.

    Until that schema is partitioned, a populated installation must keep its
    original subject. Check every term, including archived terms and old UI
    snapshots, so changing the active term cannot bypass this boundary.
    """
    from sqlalchemy import or_, select
    from ..models import Exam, Student, WorkspaceState
    from ..models.agent_entities import AgentMessage, AnalysisRun, StudentLongitudinalProfile, StudentProfile
    from ..models.entities import Enrollment
    from ..models.growth_entities import GrowthEvent

    from ..models.teaching_entities import TeachingTask
    if session.scalar(select(TeachingTask.id).limit(1)) is not None:
        return "已有教学任务与学习记录"
    if session.scalar(select(Exam.id).limit(1)) is not None:
        return "已有考试或成绩"
    if session.scalar(select(AnalysisRun.id).limit(1)) is not None or session.scalar(
        select(AgentMessage.id).limit(1)
    ) is not None:
        return "已有 TeachMate 对话或分析记录"
    if session.scalar(select(StudentProfile.id).limit(1)) is not None or session.scalar(
        select(StudentLongitudinalProfile.id).limit(1)
    ) is not None:
        return "已有学生画像"
    if session.scalar(select(Enrollment.id).where(or_(
        Enrollment.entrance_english.is_not(None), Enrollment.target_score.is_not(None),
        Enrollment.weak_tags.is_not(None),
    )).limit(1)) is not None or session.scalar(select(Student.id).where(or_(
        Student.entrance_english.is_not(None), Student.target_score.is_not(None),
        Student.weak_tags.is_not(None),
    )).limit(1)) is not None:
        return "已有学生学科记录"
    if session.scalar(select(GrowthEvent.id).limit(1)) is not None:
        return "已有学生成长记录"
    subject_fields = (
        "exams", "archivedExams", "recitations", "writings", "errors",
        "paperDocuments", "archivedDocuments", "dictationNames",
        "dictationRanges", "homeworkTasks", "homeworkRecords", "dictation",
    )
    for state in session.scalars(select(WorkspaceState.state_json)):
        if isinstance(state, dict) and any(state.get(field) for field in subject_fields):
            return "已有学科教学记录"
        if isinstance(state, dict) and any(
            isinstance(student, dict) and any(student.get(key) not in (None, "")
                                              for key in ("english", "target", "weakness", "weakTags"))
            for student in (state.get("students") or [])
        ):
            return "已有学生学科记录"
    return None


def subject_options_text() -> str:
    """可执行错误提示用的学科清单。"""
    return "、".join(f"{item.label}（{item.key}）" for item in SUBJECTS)


def list_subjects() -> list[dict[str, Any]]:
    return [item.to_dict() for item in SUBJECTS]
