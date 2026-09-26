import json, tempfile
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import select, event
from backend.tests.test_growth_three_part import env as base_env, _add_exam
from backend.tests.test_growth_profile_integration import env as profile_env
from backend.app.services.growth import rules, events, service, snapshots, exam_events, performance
from backend.app.models import GrowthEvent, ExamScore, ExamPaperVersion, ExamQuestion, StudentItemResult
out={}
r=rules.build_default_rule_version()
d=date(2026,4,1)
def e(i,t='task_completed',day=d,rev=None):
 return dict(id=i,event_type=t,business_date=day,occurred_at=datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc)+timedelta(hours=i),reverses_event_id=rev)
a=rules.compute_awards([e(1),e(2),e(3),e(4,'reversal',rev=1)],rule=r)
out['reversal_cap']={'awards':[x['applied_points'] for x in a],'actual':rules.term_points_from_awards(a),'expected':4}
a=rules.compute_awards([e(1,'weekly_goal'),e(2,'weekly_goal',d+timedelta(days=7)),e(3,'weekly_goal',d+timedelta(days=14)),e(4,'reversal',rev=1)],rule=r)
out['reversed_streak']={'awards':[x['applied_points'] for x in a],'actual':rules.term_points_from_awards(a),'expected':4}
with tempfile.TemporaryDirectory() as tmp:
 env=base_env.__wrapped__(Path(tmp));s=env['session'];tid=env['term'].id;sid=env['strong'].id
 for day,key,score in [('2026-04-01','first',60),('2026-04-08','second',70)]:
  ex=_add_exam(s,term_id=tid,class_id=env['cls'].id,day=day,key=key,scores={sid:score})
 exam_events.sync_exam_growth_events(s,term_id=tid)
 latest=s.scalar(select(GrowthEvent).where(GrowthEvent.source_id==f'{ex.id}:{sid}'))
 score=s.scalar(select(ExamScore).where(ExamScore.exam_id==ex.id));score.total_score=60;s.flush()
 res=exam_events.sync_exam_growth_events(s,term_id=tid)
 out['score_correction']={'sync':res,'stored_progress':latest.payload_json['progress_points'],'expected_progress':0}
 service.reverse_event(s,term_id=tid,event_id=latest.id,reason='校正成绩')
 out['resync_after_reversal']=exam_events.sync_exam_growth_events(s,term_id=tid)
 s.commit()
 sql=[]
 def listen(c,cu,statement,parameters,context,many):sql.append(statement.split()[0].upper())
 event.listen(s.bind,'before_cursor_execute',listen)
 service.forest_rows(s,term_id=tid)
 event.remove(s.bind,'before_cursor_execute',listen)
 out['forest_read_sql']={'students':2,'total_statements':len(sql),'writes':{k:sql.count(k) for k in ['INSERT','UPDATE','DELETE']}}
 s.rollback();s.close()
with tempfile.TemporaryDirectory() as tmp:
 env=profile_env.__wrapped__(Path(tmp));s=env['session'];tid=env['term'].id;sid=env['strong'].id;eid=env['exam'].id
 before=snapshots.build_snapshot(s,student_id=sid,term_id=tid)
 p=ExamPaperVersion(exam_id=eid,version=2,status='draft',full_score=40);s.add(p);s.flush()
 q=ExamQuestion(paper_version_id=p.id,question_no='draft',question_type='阅读理解',max_score=10);s.add(q);s.flush()
 s.add(StudentItemResult(exam_id=eid,student_id=sid,question_id=q.id,score=0,score_rate=0,attendance_status='present'));s.flush()
 after=snapshots.build_snapshot(s,student_id=sid,term_id=tid)
 out['draft_pollution']={'before':before['dimensions']['reading']['value'],'after':after['dimensions']['reading']['value'],'expected_after':before['dimensions']['reading']['value']}
 out['revision_ignores_dimensions']={'same_revision':before['source_revision']==after['source_revision'],'before':before['source_revision'],'after':after['source_revision']}
 s.close()
print(json.dumps(out,ensure_ascii=False,indent=2))
