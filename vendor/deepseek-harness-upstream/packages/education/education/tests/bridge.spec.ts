import { describe, expect, it } from 'vitest'
import { Context } from '@deepseek-ai/cordis'
import { CallId } from '@deepseek-ai/dsh-llm'
import Commands from '@deepseek-ai/dsh-commands'
import SystemPrompt from '@deepseek-ai/dsh-system-prompt'
import ToolRuntime from '@deepseek-ai/dsh-tools'
import { EducationBridge, sanitizeExamOverview, sanitizeStudentProfile } from '../src/bridge.ts'
import * as Education from '../src/index.ts'
import { parseEducationContextInput } from '../src/index.ts'

describe('education bridge sanitization', () => {
  it('keeps only aggregate exam facts', () => {
    expect(sanitizeExamOverview({
      exam_id: 12,
      exam_name: '期中考试',
      full_score: 100,
      present_count: 38,
      absent_count: 2,
      average: 76.5,
      highest: 99,
      lowest: 31,
      student_name: '张三',
    })).toEqual({
      examName: '期中考试',
      fullScore: 100,
      presentCount: 38,
      absentCount: 2,
      average: 76.5,
      highest: 99,
      lowest: 31,
    })
  })

  it('removes student identity while preserving bounded score history', () => {
    expect(sanitizeStudentProfile({
      student: { id: 7, name: '张三', phone: 'secret' },
      exams: [{
        exam_id: 12,
        exam_name: '期中考试',
        score_rate: 76.5,
        total_score: 76.5,
        class_rank: 4,
        tier: 'B',
        student_name: '张三',
      }],
      latest_score: { total_score: 76.5 },
    })).toEqual({
      scoreHistory: [{
        examName: '期中考试',
        scoreRate: 76.5,
        totalScore: 76.5,
        classRank: 4,
        tier: 'B',
      }],
      latestScore: 76.5,
    })
  })
})

describe('education bridge mock mode', () => {
  it('parses only explicit teacher context selections', () => {
    expect(parseEducationContextInput('term=2 exam=8 student=13', {})).toEqual({
      scope: { termId: 2, examId: 8, studentId: 13 },
    })
    expect(parseEducationContextInput('exam=8', {})).toMatchObject({ error: expect.stringContaining('term') })
    expect(parseEducationContextInput('term=2 nope=8', {})).toMatchObject({ error: expect.stringContaining('无法识别') })
    expect(parseEducationContextInput('clear', { termId: 2, examId: 8 })).toEqual({ scope: {} })
  })

  it('returns deterministic data without a live WorkBench server', async () => {
    const bridge = new EducationBridge({
      mode: 'mock',
      baseUrl: 'http://127.0.0.1:8765',
      token: '',
      scope: { termId: 1, examId: 2, studentId: 3 },
      timeoutMs: 100,
      maxResponseBytes: 1024,
    })

    await expect(bridge.readExamOverview(new AbortController().signal)).resolves.toMatchObject({
      examName: '当前考试',
      presentCount: 32,
    })
    await expect(bridge.readStudentProfile(new AbortController().signal)).resolves.toMatchObject({
      latestScore: 74,
    })
  })

  it('mounts exactly the education tool surface', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(Commands)
    await ctx.plugin(Education, { mode: 'mock', scope: { examId: 2, studentId: 3, termId: 1 } })

    expect(ctx.tools.schemas().map(tool => tool.name)).toEqual([
      'education_context',
      'read_exam_overview',
      'read_student_profile',
      'submit_teaching_report',
    ])
    expect((await ctx.systemPrompt.assemble()).sections.map(section => section.name)).toContain('education:policy')
  })

  it('mounts in mock mode without a host-selected scope', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(Commands)
    await ctx.plugin(Education, { mode: 'mock' })

    const result = await ctx.tools.execute({
      signal: new AbortController().signal,
      callId: CallId('education-context'),
      name: 'education_context',
      arguments: {},
    })
    expect(result.value).toEqual({
      mode: 'mock',
      examSelected: false,
      studentSelected: false,
      capabilities: ['exam_analysis', 'student_diagnosis', 'review_plan'],
    })
  })
})

describe('evidence chain binding', () => {
  it('read_exam_overview returns an evidence_id', async () => {
    const bridge = new EducationBridge({
      mode: 'mock',
      baseUrl: 'http://127.0.0.1:8765',
      token: '',
      scope: { termId: 1, examId: 2 },
      timeoutMs: 100,
      maxResponseBytes: 1024,
    })
    const result = await bridge.readExamOverview(new AbortController().signal)
    expect(result.evidenceId).toMatch(/^ev_\d+$/)
    expect(bridge.isEvidenceValid(result.evidenceId)).toBe(true)
  })

  it('read_student_profile returns an evidence_id', async () => {
    const bridge = new EducationBridge({
      mode: 'mock',
      baseUrl: 'http://127.0.0.1:8765',
      token: '',
      scope: { termId: 1, studentId: 3 },
      timeoutMs: 100,
      maxResponseBytes: 1024,
    })
    const result = await bridge.readStudentProfile(new AbortController().signal)
    expect(result.evidenceId).toMatch(/^ev_\d+$/)
    expect(bridge.isEvidenceValid(result.evidenceId)).toBe(true)
  })

  it('rejects report with fabricated evidence_id', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(Commands)
    await ctx.plugin(Education, { mode: 'mock', scope: { examId: 2, studentId: 3, termId: 1 } })

    const result = await ctx.tools.execute({
      signal: new AbortController().signal,
      callId: CallId('submit-report'),
      name: 'submit_teaching_report',
      arguments: {
        reportType: 'exam_analysis',
        title: 'Test',
        summary: 'Test summary',
        findings: [{ label: 'L', detail: 'D', confidence: 0.8 }],
        recommendations: ['R'],
        limitations: ['L'],
        evidence: [{ evidenceId: 'ev_999', source: 'fake', fact: 'fabricated' }],
      },
    })
    expect(result.isError).toBe(true)
    expect(result.error?.message ?? JSON.stringify(result.content)).toMatch(/not registered/)
  })

  it('accepts report with valid evidence_id from prior tool call', async () => {
    const ctx = new Context()
    await ctx.plugin(SystemPrompt)
    await ctx.plugin(ToolRuntime)
    await ctx.plugin(Commands)
    await ctx.plugin(Education, { mode: 'mock', scope: { examId: 2, studentId: 3, termId: 1 } })

    // First, read exam overview to register evidence
    const examResult = await ctx.tools.execute({
      signal: new AbortController().signal,
      callId: CallId('read-exam'),
      name: 'read_exam_overview',
      arguments: {},
    })
    const evidenceId = (examResult.value as { evidenceId: string }).evidenceId

    // Then, submit a report referencing that evidence
    const reportResult = await ctx.tools.execute({
      signal: new AbortController().signal,
      callId: CallId('submit-report-ok'),
      name: 'submit_teaching_report',
      arguments: {
        reportType: 'exam_analysis',
        title: 'Test Report',
        summary: 'Test summary',
        findings: [{ label: 'L', detail: 'D', confidence: 0.8 }],
        recommendations: ['R'],
        limitations: ['L'],
        evidence: [{ evidenceId, source: 'read_exam_overview', fact: 'data' }],
      },
    })
    expect(reportResult.value).toMatchObject({
      status: 'draft',
      requiresTeacherConfirmation: true,
    })
  })
})
