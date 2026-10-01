"""Local entity resolution and immutable conversation state, independent of LLMs."""
from difflib import SequenceMatcher
import re
import unicodedata

from sqlalchemy import select

from ...agent.privacy import PrivacyMapper
from ...models import AnalysisRun, Class, Enrollment, Student
from .identity_dict import build_run_identity_dictionary, register_identity_into_mapper


def normalize(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def looks_like_person(query):
    # Only treat likely personal names as unknown people. Topic nouns and pronouns
    # are left to the model instead of blocking free conversation as roster errors.
    surnames = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项董梁杜阮蓝闵席季麻强贾路江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓洪包左石崔吉龚程嵇邢裴陆荣翁荀羊甄曲封芮储靳汲邴糜松井段富巫乌焦巴弓牧山谷车侯全班秋仲伊宫宁仇栾甘厉戎祖武符刘景詹龙叶幸司韶黎乔苍双闻莘翟谭贡劳逄姬申扶堵冉宰郦雍璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷辛阚简饶曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
    topics = {"成绩", "表现", "全班", "整个班", "所有人", "周末", "班级", "学生", "这位", "那位", "这个", "那个", "同学", "教师", "老师"}
    return bool(query not in topics and not query.endswith(("呢", "同学", "老师"))
                and re.fullmatch(r"[\u3400-\u9fff]{2,4}", query) and query[0] in surnames)


def qualified_mentions(rows, name, text):
    matches = [r for r in rows if normalize(r["name"]) == name]
    qualified = []
    ambiguous = []
    for occurrence in re.finditer(re.escape(name), text):
        if any(normalize(r["name"]) != name and normalize(r["name"]).startswith(name)
               and text.startswith(normalize(r["name"]), occurrence.start()) for r in rows):
            continue
        before = text[:occurrence.start()]
        after = text[occurrence.end():]
        local = [r for r in matches if re.search(re.escape(normalize(r["class_name"])) + r"(?:的)?$", before)
                 or re.match(r"[（(]" + re.escape(normalize(r["class_name"])) + r"[）)]", after)]
        # A sole class elsewhere in the sentence still qualifies common teacher phrasing.
        if not local:
            contextual = [r for r in matches if normalize(r["class_name"]) in text]
            local = contextual if len(contextual) == 1 else matches
        if len(local) == 1:
            qualified.extend(local)
        else:
            ambiguous.extend(local)
    return qualified, ambiguous


def roster(db, scope):
    if not scope.get("term_id"):
        return []
    query = (select(Student.id, Student.name, Student.student_no, Class.name.label("class_name"))
             .join(Enrollment, Enrollment.student_id == Student.id)
             .join(Class, Class.id == Enrollment.class_id)
             .where(Enrollment.term_id == scope["term_id"], Enrollment.status == "active"))
    if scope.get("class_id"):
        query = query.where(Enrollment.class_id == scope["class_id"])
    return [dict(id=r.id, name=r.name, student_no=r.student_no, class_name=r.class_name)
            for r in db.execute(query.order_by(Student.id)).unique()]


def previous_state(db, session_id):
    for run in db.scalars(select(AnalysisRun).where(AnalysisRun.session_id == session_id,
        AnalysisRun.status.not_in(["failed", "cancelled"])).order_by(AnalysisRun.id.desc()).limit(20)):
        state = (run.input_summary_json or {}).get("conversation_state")
        if state:
            return dict(state)
    return {}


def run_mapper(db, scope, *, snapshot=None):
    mapper = PrivacyMapper.from_snapshot(snapshot, allow_student_names=
        (snapshot or {}).get("allow_student_names", True))
    identity = build_run_identity_dictionary(db, term_id=scope.get("term_id"),
        class_id=scope.get("class_id"), student_id=None if scope.get("term_id") else scope.get("student_id"))
    register_identity_into_mapper(mapper, identity)
    return mapper


def mapper_for_run(db, scope):
    run = db.get(AnalysisRun, scope["run_id"]) if scope.get("run_id") else None
    snapshot = (run.input_summary_json or {}).get("identity_snapshot") if run else None
    if not snapshot:
        return run_mapper(db, scope, snapshot={"allow_student_names": False} if run else None)
    mapper = PrivacyMapper.from_snapshot(snapshot, allow_student_names=snapshot.get("allow_student_names", True))
    identity = build_run_identity_dictionary(db, term_id=scope.get("term_id"), class_id=scope.get("class_id"))
    # Historical names and refs come from the frozen run; current contact details remain protected.
    mapper.register_school_names(identity.school_names)
    mapper.register_protected_terms(identity.protected_terms + identity.student_nos + identity.phones)
    for sid, number in identity.student_number_records:
        ref = mapper.to_anonymous(sid)
        if ref:
            mapper._student_number_refs[number] = ref
    return mapper


def resolve_student(db, scope, query, mapper, aliases=None):
    """A candidate is never a match; refs are scoped capabilities, not DB IDs."""
    rows = roster(db, scope)
    text = normalize(query)
    aliases = aliases or {}
    exact = [r for r in rows if text in {normalize(r["name"]), normalize(r["student_no"]),
                                       normalize(mapper.to_anonymous(r["id"]) or "")}
             or aliases.get(text) == mapper.to_anonymous(r["id"])]
    if not exact:
        # A complete, unknown name must not match a shorter known name inside it.
        named = [] if looks_like_person(text) else [r for r in rows if normalize(r["name"]) and normalize(r["name"]) in text]
        qualified = [r for r in named if normalize(r["class_name"]) in text]
        exact = qualified or named
    if len(exact) == 1:
        return {"status": "matched", "matches": exact, "candidates": []}
    if exact:
        return {"status": "ambiguous", "matches": [], "candidates": exact[:8]}
    candidates = [r for r in rows if len(text) >= 2 and
                  SequenceMatcher(None, text, normalize(r["name"])).ratio() >= 0.6]
    return {"status": "not_found", "matches": [], "candidates": candidates[:5]}


def prepare_turn(db, session_id, scope, content, selected_refs=None, confirmed_aliases=None, identity_mode=None):
    previous = previous_state(db, session_id)
    # Identity is session-stable; historical rows are retained only for restoration.
    prior_runs = db.scalars(select(AnalysisRun).where(AnalysisRun.session_id == session_id)
                           .order_by(AnalysisRun.id.desc()).limit(20))
    snapshot = next(((r.input_summary_json or {})["identity_snapshot"] for r in prior_runs
                     if (r.input_summary_json or {}).get("identity_snapshot")), None)
    if identity_mode:
        snapshot = {**(snapshot or {}), "allow_student_names": identity_mode == "teacher"}
    mapper = run_mapper(db, scope, snapshot=snapshot)
    rows = roster(db, scope)
    allowed = {r["id"]: r for r in rows}
    text = normalize(content)
    mentions = []
    candidates = []
    for ref in selected_refs or []:
        sid = mapper.to_real(ref)
        if sid not in allowed:
            from fastapi import HTTPException
            raise HTTPException(400, "学生引用不属于当前学期或班级")
        mentions.append(sid)
    aliases = dict(previous.get("aliases", {}))
    for alias, ref in (confirmed_aliases or {}).items():
        key = normalize(alias)
        sid = mapper.to_real(ref)
        if not key or len(key) > 30 or sid not in allowed or any(key == normalize(r["name"]) and r["id"] != sid for r in rows):
            from fastapi import HTTPException
            raise HTTPException(400, "别名为空、冲突或引用越出当前名单")
        aliases[key] = ref
    for alias, ref in aliases.items():
        if len(alias) >= 2 and alias in text and mapper.to_real(ref) in allowed:
            mentions.append(mapper.to_real(ref))
    names = {normalize(r["name"]) for r in rows if r["name"]}
    for name in sorted(names, key=len, reverse=True):
        if name not in text:
            continue
        qualified, ambiguous = qualified_mentions(rows, name, text)
        mentions.extend(r["id"] for r in qualified)
        candidates.extend(ambiguous)
    for row in rows:
        no = normalize(row["student_no"])
        ref = mapper.to_anonymous(row["id"])
        if (no and re.search(r"(?<![a-z0-9])" + re.escape(no) + r"(?![a-z0-9])", text)) or (ref and re.search(r"(?<![a-z0-9_])" + re.escape(ref) + r"(?![a-z0-9_])", text)):
            mentions.append(row["id"])
    # An explicit choice resolves a prior ambiguous name; exact student numbers do too.
    mentions = list(dict.fromkeys(mentions))
    if candidates and mentions:
        candidates = [r for r in candidates if r["id"] not in mentions]
        if any(normalize(allowed[sid]["name"]) == normalize(r["name"])
               for sid in mentions for r in candidates):
            candidates = []
    unknown_queries = []
    person_pattern = r"(?:分析|看看|查看|给|为)([\u3400-\u9fff·0-9a-zA-Z]{2,30}?)(?:的)?(?:成绩|表现|错题|画像|同学|出|设计|安排|生成|准备)"
    followup_pattern = r"^(?:那|再看|看看|分析)?([\u3400-\u9fff]{2,4})(?:呢|怎么样|最近如何)[？?。！!]*$"
    for match in [*re.finditer(person_pattern, normalize(content)), *re.finditer(followup_pattern, normalize(content))]:
        query = match.group(1).removesuffix("的")
        for suffix in ("这次", "本次", "最近", "上次", "当前", "本轮"):
            query = query.removesuffix(suffix)
        for class_name in sorted({normalize(r["class_name"]) for r in rows}, key=len, reverse=True):
            if query.startswith(class_name):
                query = query[len(class_name):].removeprefix("的")
                break
        explicit_student = query.startswith("学生") or match.group(0).endswith("同学")
        query = query.removeprefix("学生")
        if (explicit_student or looks_like_person(query)) and query not in {"这位", "那位", "这个学生", "所有学生", "全班学生", "全班", "整个班", "全体学生", "所有人"}:
            resolved = resolve_student(db, scope, query, mapper, aliases)
            if resolved["status"] == "not_found":
                unknown_queries.append(query)
                candidates.extend(resolved["candidates"])
    clear = bool(re.search(r"全班|整个班|所有学生|全体学生", content))
    prior_focus = [sid for ref in previous.get("focus_refs", [])
                   if (sid := mapper.to_real(ref)) in allowed]
    if clear:
        focus = []
    elif candidates or unknown_queries:
        focus = []
    elif mentions:
        focus = list(dict.fromkeys(prior_focus + mentions)) if len(mentions) == 1 and re.search(r"比较|对比|和.+比|与.+比", content) else mentions
    else:
        focus = prior_focus or ([scope["student_id"]] if scope.get("student_id") in allowed else [])
    status = "not_found" if unknown_queries else "ambiguous" if candidates else "matched" if focus else "unspecified"
    state = {"focus_refs": [mapper.to_anonymous(sid) for sid in focus],
             "resolution_status": status,
             "candidate_refs": [mapper.to_anonymous(r["id"]) for r in candidates[:8]],
             "aliases": dict(list(aliases.items())[-30:]),
             "teacher_corrections": previous.get("teacher_corrections", [])[-3:]}
    if re.search(r"还没教|尚未教|没学过|不是.+原因|判断不对|不要.+作业", content):
        state["teacher_corrections"] = (state["teacher_corrections"] + [content[:500]])[-4:]
    people = [{"student_ref": mapper.to_anonymous(sid), "name": allowed[sid]["name"],
               "class_name": allowed[sid]["class_name"]} for sid in focus]
    choices = [{"student_ref": mapper.to_anonymous(r["id"]), "name": r["name"],
                "class_name": r["class_name"]} for r in candidates[:8]]
    context = {"unresolved_queries": unknown_queries[:3], "people": people, "resolution_status": status, "candidates": choices,
               "teacher_corrections": state["teacher_corrections"],
               "note": "人物为空不等于名单无此人；有歧义请简短确认班级或引用，不查询猜测对象。"}
    return state, mapper.snapshot(), context, focus
