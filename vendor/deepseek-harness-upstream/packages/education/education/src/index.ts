/** TeachMate education tools over a bounded WorkBench loopback Bridge. */

import type { Context } from '@deepseek-ai/cordis'
import type { CommandInvocation } from '@deepseek-ai/dsh-commands'
import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type { ToolRunContext } from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-system-prompt'
import { EducationBridge } from './bridge.ts'
import type { EducationScope, TeachingReportDraft } from './types.ts'

export { EducationBridge, EducationBridgeError, sanitizeExamOverview, sanitizeStudentProfile } from './bridge.ts'
export type { EducationBridgeOptions } from './bridge.ts'
export type { EducationScope, ExamOverview, StudentProfile, TeachingReportDraft, TermOption, ExamOption, StudentOption } from './types.ts'
import type { ExamOverview, StudentProfile } from './types.ts'

export const name = 'education'
export const inject = ['tools', 'systemPrompt', 'commands']

/** Host configuration for the education tools. */
export interface Config {
  /** `mock` is deterministic and keyless; `bridge` calls WorkBench over loopback. */
  mode?: 'mock' | 'bridge'
  /** Base URL of the local WorkBench API, without a trailing path. */
  baseUrl?: string
  /** Explicit token for tests; production should use tokenEnv instead. */
  token?: string
  /** Environment variable holding the local WorkBench token. */
  tokenEnv?: string
  /** Server-owned context; no model tool accepts these ids. */
  scope?: EducationScope
  /** Cooperative upper bound for one Bridge request. */
  timeoutMs?: number
  /** Maximum decoded response size retained in the model-visible path. */
  maxResponseBytes?: number
}

export const Config: z<Config> = z.object({
  mode: z.union([z.const('mock'), z.const('bridge')]).default('mock'),
  baseUrl: z.string().default('http://127.0.0.1:8765'),
  token: z.string(),
  tokenEnv: z.string().default('WORKBENCH_TOKEN'),
  scope: z.object({
    termId: z.number().step(1).min(1),
    examId: z.number().step(1).min(1),
    studentId: z.number().step(1).min(1),
  }),
  timeoutMs: z.number().step(1).min(100).default(10_000),
  maxResponseBytes: z.number().step(1).min(1024).default(256 * 1024),
})

const EXAM_OUTPUT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    kind: { type: 'string', const: 'exam_overview', required: true },
    examName: { type: 'string', required: true },
    fullScore: { type: 'number', required: true },
    presentCount: { type: 'integer', required: true },
    absentCount: { type: 'integer', required: true },
    average: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
    highest: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
    lowest: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
    evidenceId: { type: 'string', required: true },
  },
} as const

const STUDENT_OUTPUT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    kind: { type: 'string', const: 'student_profile', required: true },
    scoreHistory: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          examName: { type: 'string', required: true },
          scoreRate: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
          totalScore: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
          classRank: { oneOf: [{ type: 'integer' }, { type: 'null' }], required: true },
          tier: { oneOf: [{ type: 'string' }, { type: 'null' }], required: true },
        },
      },
    },
    latestScore: { oneOf: [{ type: 'number' }, { type: 'null' }], required: true },
    evidenceId: { type: 'string', required: true },
  },
} as const

const REPORT_OUTPUT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    status: { type: 'string', const: 'draft', required: true },
    reportType: { type: 'string', enum: ['exam_analysis', 'student_diagnosis', 'review_plan'], required: true },
    title: { type: 'string', required: true },
    summary: { type: 'string', required: true },
    findings: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          label: { type: 'string', required: true },
          detail: { type: 'string', required: true },
          confidence: { type: 'number', required: true },
        },
      },
    },
    recommendations: { type: 'array', required: true, items: { type: 'string' } },
    limitations: { type: 'array', required: true, items: { type: 'string' } },
    evidence: {
      type: 'array', required: true,
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          evidenceId: { type: 'string', required: true },
          source: { type: 'string', required: true },
          fact: { type: 'string', required: true },
        },
      },
    },
    requiresTeacherConfirmation: { type: 'boolean', const: true, required: true },
  },
} as const

function renderJson(value: unknown): [{ type: 'text'; text: string }] {
  return [{ type: 'text', text: JSON.stringify(value) }]
}

function assertReportConfidence(value: number): void {
  if (!Number.isFinite(value) || value < 0 || value > 1) throw new Error('confidence must be between 0 and 1')
}

/** Parse the explicit teacher context command without accepting arbitrary ids. */
export function parseEducationContextInput(rawInput: string, current: EducationScope):
  | { readonly scope: EducationScope }
  | { readonly error: string } {
  const input = rawInput.trim()
  if (input === 'clear') return { scope: {} }
  if (input.length === 0) return { error: '请指定 term=<id>、exam=<id> 或 student=<id>；也可以使用 clear 清空上下文。' }

  const next: { termId?: number; examId?: number; studentId?: number } = { ...current }
  const seen = new Set<string>()
  for (const token of input.split(/\s+/u)) {
    const match = /^(term|exam|student)=(\d+)$/u.exec(token)
    if (match === null) return { error: '无法识别上下文参数，请使用 term=<id>、exam=<id> 或 student=<id>。' }
    const [, kind, rawId] = match
    if (kind === undefined || rawId === undefined || seen.has(kind)) return { error: '上下文参数重复，请每种类型只指定一次。' }
    const id = Number(rawId)
    if (!Number.isSafeInteger(id) || id < 1) return { error: '上下文 ID 必须是正整数。' }
    seen.add(kind)
    if (kind === 'term') next.termId = id
    if (kind === 'exam') next.examId = id
    if (kind === 'student') next.studentId = id
  }
  if ((next.examId !== undefined || next.studentId !== undefined) && next.termId === undefined) {
    return { error: '选择考试或学生时必须同时指定 term=<id>。' }
  }
  return { scope: next }
}

function createReport(args: {
  reportType: TeachingReportDraft['reportType']
  title: string
  summary: string
  findings: TeachingReportDraft['findings']
  recommendations: string[]
  limitations: string[]
  evidence: TeachingReportDraft['evidence']
  bridge: EducationBridge
}): TeachingReportDraft {
  for (const finding of args.findings) assertReportConfidence(finding.confidence)
  // Validate that every evidence ID was registered by a prior tool call
  for (const ev of args.evidence) {
    if (!args.bridge.isEvidenceValid(ev.evidenceId)) {
      throw new Error(`evidence_id "${ev.evidenceId}" was not registered by a prior tool call — model cannot fabricate evidence sources`)
    }
    // Enrich: use the bridge's stored source/fact if the model didn't provide them
    const registered = args.bridge.getRegisteredEvidence().get(ev.evidenceId)
    if (registered) {
      return {
        status: 'draft',
        reportType: args.reportType,
        title: args.title.trim(),
        summary: args.summary.trim(),
        findings: args.findings,
        recommendations: args.recommendations,
        limitations: args.limitations,
        evidence: args.evidence.map(e => ({
          evidenceId: e.evidenceId,
          source: e.source || registered.source,
          fact: e.fact || registered.fact,
        })),
        requiresTeacherConfirmation: true,
      }
    }
  }
  return {
    status: 'draft',
    reportType: args.reportType,
    title: args.title.trim(),
    summary: args.summary.trim(),
    findings: args.findings,
    recommendations: args.recommendations,
    limitations: args.limitations,
    evidence: args.evidence,
    requiresTeacherConfirmation: true,
  }
}

/** Install the model-facing TeachMate tools in the current agent scope. */
export function apply(ctx: Context, config: Config): void {
  const bridge = new EducationBridge({
    mode: config.mode ?? 'mock',
    baseUrl: config.baseUrl ?? 'http://127.0.0.1:8765',
    token: config.token ?? process.env[config.tokenEnv ?? 'WORKBENCH_TOKEN'] ?? '',
    scope: config.scope ?? {},
    timeoutMs: config.timeoutMs ?? 10_000,
    maxResponseBytes: config.maxResponseBytes ?? 256 * 1024,
  })

  ctx.systemPrompt.section({
    name: 'education:policy',
    order: 90,
    text: 'You are TeachMate, an AI teaching assistant. Use education tools to ground claims in the selected context. Never request or expose student identifiers. Treat every report as a draft: state evidence, uncertainty, limitations, and ask the teacher to confirm before it becomes an evaluation.',
  })

  ctx.commands.register({
    name: 'teachmate-context',
    description: '设置 TeachMate 当前会话使用的学期、考试和学生上下文',
    input: { hint: 'clear | term=<id> exam=<id> student=<id>' },
    // Context ids are host-owned state and must not be copied into the model history.
    recordInput: false,
    handler: async (invocation: CommandInvocation) => {
      const parsed = parseEducationContextInput(invocation.rawInput, bridge.scope)
      if ('error' in parsed) return { kind: 'error', text: parsed.error }
      bridge.setScope(parsed.scope)
      return { kind: 'success', text: 'TeachMate 上下文已更新。' }
    },
  })

  // ─── Context selector dropdown data ───────────────────────────
  // These commands are host-side and fetch from the WorkBench loopback.
  // They are never model-visible (recordInput: false, not registered as tools).

  ctx.commands.register({
    name: 'teachmate-list-terms',
    description: '列出可选学期（仅供上下文选择器下拉使用）',
    recordInput: false,
    handler: async () => {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), config.timeoutMs ?? 10_000)
      try {
        const terms = await bridge.listTerms(controller.signal)
        return { kind: 'success' as const, text: '', result: { terms } }
      } catch (error) {
        return { kind: 'error' as const, text: error instanceof Error ? error.message : String(error) }
      } finally {
        clearTimeout(timer)
      }
    },
  })

  ctx.commands.register({
    name: 'teachmate-list-exams',
    description: '列出指定学期的考试（仅供上下文选择器下拉使用）',
    input: { hint: '<term_id>' },
    recordInput: false,
    handler: async (invocation: CommandInvocation) => {
      const termId = Number(invocation.rawInput.trim())
      if (!Number.isSafeInteger(termId) || termId < 1) {
        return { kind: 'error' as const, text: '请提供有效的学期 ID。' }
      }
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), config.timeoutMs ?? 10_000)
      try {
        const exams = await bridge.listExams(termId, controller.signal)
        return { kind: 'success' as const, text: '', result: { exams } }
      } catch (error) {
        return { kind: 'error' as const, text: error instanceof Error ? error.message : String(error) }
      } finally {
        clearTimeout(timer)
      }
    },
  })

  ctx.commands.register({
    name: 'teachmate-search-students',
    description: '按姓名搜索学生（仅供上下文选择器下拉使用）',
    input: { hint: '<query>' },
    recordInput: false,
    handler: async (invocation: CommandInvocation) => {
      const query = invocation.rawInput.trim()
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), config.timeoutMs ?? 10_000)
      try {
        const students = await bridge.searchStudents(query, controller.signal)
        return { kind: 'success' as const, text: '', result: { students } }
      } catch (error) {
        return { kind: 'error' as const, text: error instanceof Error ? error.message : String(error) }
      } finally {
        clearTimeout(timer)
      }
    },
  })

  ctx.tools.register(defineTool({
    name: 'education_context',
    description: 'Read the teacher-selected education context and available capabilities. The context is host-owned; do not invent or submit identifiers.',
    parameters: {},
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: {
          mode: { type: 'string', enum: ['mock', 'bridge'], required: true },
          examSelected: { type: 'boolean', required: true },
          studentSelected: { type: 'boolean', required: true },
          capabilities: { type: 'array', required: true, items: { type: 'string' } },
        },
      },
      render: (_args, value) => renderJson(value),
    },
    async execute() {
      return {
        mode: bridge.mode,
        examSelected: bridge.scope.examId !== undefined,
        studentSelected: bridge.scope.studentId !== undefined,
        capabilities: ['exam_analysis', 'student_diagnosis', 'review_plan'],
      }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'read_exam_overview',
    description: 'Read aggregate facts for the teacher-selected exam. Use this before making score or distribution claims.',
    parameters: {},
    output: { schema: EXAM_OUTPUT_SCHEMA, render: (_args, value: ExamOverview & { kind: 'exam_overview' }) => renderJson(value) },
    timeoutMs: config.timeoutMs ?? 10_000,
    async execute(_args, exec: ToolRunContext) {
      return { kind: 'exam_overview' as const, ...await bridge.readExamOverview(exec.signal) }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'read_student_profile',
    description: 'Read sanitized score history for the teacher-selected student. Names, ids, contact details, and file paths are never returned.',
    parameters: {},
    output: { schema: STUDENT_OUTPUT_SCHEMA, render: (_args, value: StudentProfile & { kind: 'student_profile' }) => renderJson(value) },
    timeoutMs: config.timeoutMs ?? 10_000,
    async execute(_args, exec: ToolRunContext) {
      return { kind: 'student_profile' as const, ...await bridge.readStudentProfile(exec.signal) }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'submit_teaching_report',
    description: 'Create a structured teaching report draft from observed evidence. Every evidence entry must reference an evidence_id returned by read_exam_overview or read_student_profile. Fabricated evidence will be rejected.',
    parameters: {
      reportType: { type: 'string', enum: ['exam_analysis', 'student_diagnosis', 'review_plan'], required: true },
      title: { type: 'string', required: true },
      summary: { type: 'string', required: true },
      findings: {
        type: 'array', required: true,
        items: {
          type: 'object', additionalProperties: false,
          properties: {
            label: { type: 'string', required: true },
            detail: { type: 'string', required: true },
            confidence: { type: 'number', required: true },
          },
        },
      },
      recommendations: { type: 'array', required: true, items: { type: 'string' } },
      limitations: { type: 'array', required: true, items: { type: 'string' } },
      evidence: {
        type: 'array', required: true,
        items: {
          type: 'object', additionalProperties: false,
          properties: {
            evidenceId: { type: 'string', required: true },
            source: { type: 'string', required: true },
            fact: { type: 'string', required: true },
          },
        },
      },
    },
    output: { schema: REPORT_OUTPUT_SCHEMA, render: (_args, value) => renderJson(value) },
    execute: async (args) => createReport({ ...args, bridge }),
  }))
}
