"""Bounded run-local read cache, invalidated when source facts change."""
import hashlib
import json
from sqlalchemy import func, select
from ...models import AnalysisRun, ExamScore, StudentItemResult, ExamPaperVersion, Enrollment, Attachment, Student


def key_for(db, scope, tool, args):
    revisions = []
    for model in (ExamScore, StudentItemResult, ExamPaperVersion, Enrollment, Attachment, Student):
        query = select(func.count(model.id), func.max(model.updated_at if hasattr(model, 'updated_at') else model.id))
        if hasattr(model, 'exam_id') and scope.get('exam_id'): query = query.where(model.exam_id == scope['exam_id'])
        if hasattr(model, 'term_id') and scope.get('term_id'): query = query.where(model.term_id == scope['term_id'])
        revisions.append([str(v) for v in db.execute(query).one()])
    value = [scope.get(k) for k in ('term_id','class_id','exam_id','student_id')] + [tool, args, revisions]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def begin(db, scope, tool, args):
    run = db.get(AnalysisRun, scope.get('run_id')) if scope.get('run_id') else None
    if run is None or tool in {'submit_report', 'get_practice_context', 'get_student_learning_evidence', 'get_original_question'}: return None, None, None
    db.refresh(run)
    summary = dict(run.input_summary_json or {})
    stats = dict(summary.get('query_stats') or {})
    stats['attempts'] = stats.get('attempts', 0) + 1
    key = key_for(db, scope, tool, args)
    cached = (summary.get('read_cache') or {}).get(key)
    if cached: stats['cache_hits'] = stats.get('cache_hits', 0) + 1
    summary['query_stats'] = stats
    run.input_summary_json = summary; db.commit()
    if stats['attempts'] > 20:
        from fastapi import HTTPException
        raise HTTPException(429, '本轮只读查询已达到上限，请依据已有事实回答或开始下一轮')
    return run, key, cached


def remember(db, run, key, result):
    if run is None: return
    payload = {k: v for k, v in result.items() if k not in {'anon', 'privacy_mapper'}}
    if len(json.dumps(payload, ensure_ascii=False, default=str)) > 18000: return
    db.refresh(run)
    summary = dict(run.input_summary_json or {})
    cache = dict(summary.get('read_cache') or {})
    cache[key] = payload
    summary['read_cache'] = dict(list(cache.items())[-8:])
    run.input_summary_json = summary; db.commit()
