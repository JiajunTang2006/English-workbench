/** Loopback WorkBench data Bridge and deterministic development fallback. */

import type { EducationBridgeOperation, EducationScope, ExamOverview, StudentProfile, TermOption, ExamOption, StudentOption } from './types.ts'

/** Options resolved once when the education plugin is mounted. */
export interface EducationBridgeOptions {
  readonly mode: 'mock' | 'bridge'
  readonly baseUrl: string
  readonly token: string
  readonly scope: EducationScope
  readonly timeoutMs: number
  readonly maxResponseBytes: number
}

/** Error raised for a failed or malformed loopback response. */
export class EducationBridgeError extends Error {
  constructor(
    readonly operation: EducationBridgeOperation,
    message: string,
    options?: ErrorOptions,
  ) {
    super(`education bridge ${operation} failed: ${message}`, options)
    this.name = 'EducationBridgeError'
  }
}

function finiteNumber(value: unknown, fallback: number | null = null): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function nonNegativeInteger(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : fallback
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

/** Remove identifiers and personal fields from an exam response. */
export function sanitizeExamOverview(payload: unknown): ExamOverview {
  const source = record(payload)
  return {
    examName: text(source.exam_name),
    fullScore: finiteNumber(source.full_score, 0) ?? 0,
    presentCount: nonNegativeInteger(source.present_count),
    absentCount: nonNegativeInteger(source.absent_count),
    average: finiteNumber(source.average),
    highest: finiteNumber(source.highest),
    lowest: finiteNumber(source.lowest),
  }
}

/** Remove identifiers and personal fields from a student profile response. */
export function sanitizeStudentProfile(payload: unknown): StudentProfile {
  const source = record(payload)
  const points = Array.isArray(source.exams) ? source.exams : []
  const scoreHistory = points.slice(0, 50).map((item): StudentProfile['scoreHistory'][number] => {
    const point = record(item)
    return {
      examName: text(point.exam_name),
      scoreRate: finiteNumber(point.score_rate),
      totalScore: finiteNumber(point.total_score),
      classRank: nonNegativeInteger(point.class_rank, 0) || null,
      tier: typeof point.tier === 'string' ? point.tier : null,
    }
  })
  const latest = record(source.latest_score)
  return {
    scoreHistory,
    latestScore: finiteNumber(latest.total_score),
  }
}

function mockExam(scope: EducationScope): ExamOverview {
  const hasExam = scope.examId !== undefined
  return {
    examName: hasExam ? '当前考试' : '未选择考试',
    fullScore: 100,
    presentCount: hasExam ? 32 : 0,
    absentCount: hasExam ? 1 : 0,
    average: hasExam ? 76.4 : null,
    highest: hasExam ? 98 : null,
    lowest: hasExam ? 42 : null,
  }
}

function mockStudent(scope: EducationScope): StudentProfile {
  if (scope.studentId === undefined) return { scoreHistory: [], latestScore: null }
  return {
    scoreHistory: [
      { examName: '上次阶段测评', scoreRate: 68, totalScore: 68, classRank: 18, tier: 'B' },
      { examName: '本次阶段测评', scoreRate: 74, totalScore: 74, classRank: 12, tier: 'B' },
    ],
    latestScore: 74,
  }
}

/** Read-only WorkBench data access used by model-facing education tools. */
export class EducationBridge {
  private currentScope: EducationScope
  private readonly registeredEvidence: Map<string, { source: string; fact: string }> = new Map()
  private evidenceCounter = 0

  constructor(private readonly options: EducationBridgeOptions) {
    this.currentScope = Object.freeze({ ...options.scope })
  }

  /** Current immutable scope snapshot. */
  get scope(): EducationScope { return this.currentScope }

  /** Replace the host-owned context after an explicit teacher action. */
  setScope(scope: EducationScope): void {
    this.currentScope = Object.freeze({ ...scope })
    // Clear evidence when scope changes — old evidence is no longer valid
    this.registeredEvidence.clear()
    this.evidenceCounter = 0
  }

  /** Whether this Bridge is returning deterministic development data. */
  get mode(): EducationBridgeOptions['mode'] { return this.options.mode }

  /** Register a piece of evidence and return its stable ID. */
  registerEvidence(source: string, fact: string): string {
    this.evidenceCounter += 1
    const id = `ev_${this.evidenceCounter}`
    this.registeredEvidence.set(id, { source, fact })
    return id
  }

  /** Check if an evidence ID was registered by a prior tool call. */
  isEvidenceValid(evidenceId: string): boolean {
    return this.registeredEvidence.has(evidenceId)
  }

  /** Get all registered evidence for report construction. */
  getRegisteredEvidence(): ReadonlyMap<string, { source: string; fact: string }> {
    return this.registeredEvidence
  }

  /** Fetch one bounded JSON response through loopback with cooperative cancellation. */
  private async get(operation: EducationBridgeOperation, path: string, signal: AbortSignal): Promise<unknown> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.options.timeoutMs)
    const abort = (): void => { controller.abort(signal.reason) }
    signal.addEventListener('abort', abort, { once: true })
    try {
      const response = await fetch(new URL(path, this.options.baseUrl), {
        method: 'GET',
        headers: {
          accept: 'application/json',
          authorization: `Bearer ${this.options.token}`,
        },
        signal: controller.signal,
      })
      const body = await response.text()
      if (new TextEncoder().encode(body).byteLength > this.options.maxResponseBytes) {
        throw new EducationBridgeError(operation, 'response exceeded the configured byte limit')
      }
      let parsed: unknown
      try {
        parsed = JSON.parse(body)
      } catch (error) {
        throw new EducationBridgeError(operation, 'response was not valid JSON', { cause: error })
      }
      if (!response.ok) {
        const detail = record(parsed).detail
        throw new EducationBridgeError(operation, typeof detail === 'string' ? detail : `HTTP ${response.status}`)
      }
      return parsed
    } catch (error) {
      if (error instanceof EducationBridgeError) throw error
      if (signal.aborted) throw error
      throw new EducationBridgeError(operation, error instanceof Error ? error.message : String(error), { cause: error })
    } finally {
      clearTimeout(timer)
      signal.removeEventListener('abort', abort)
    }
  }

  /** Read a sanitized exam overview from WorkBench or the mock provider. */
  async readExamOverview(signal: AbortSignal): Promise<ExamOverview & { evidenceId: string }> {
    if (this.options.mode === 'mock') {
      const data = mockExam(this.currentScope)
      const evidenceId = this.registerEvidence('read_exam_overview', JSON.stringify(data))
      return { ...data, evidenceId }
    }
    const { termId, examId } = this.currentScope
    if (termId === undefined || examId === undefined) {
      throw new EducationBridgeError('exam-overview', 'the host has not selected a term and exam')
    }
    const payload = await this.get('exam-overview', `/api/v1/exams/${String(examId)}/summary?term_id=${String(termId)}`, signal)
    const data = sanitizeExamOverview(payload)
    const evidenceId = this.registerEvidence('read_exam_overview', JSON.stringify(data))
    return { ...data, evidenceId }
  }

  /** Read a sanitized student profile from WorkBench or the mock provider. */
  async readStudentProfile(signal: AbortSignal): Promise<StudentProfile & { evidenceId: string }> {
    if (this.options.mode === 'mock') {
      const data = mockStudent(this.currentScope)
      const evidenceId = this.registerEvidence('read_student_profile', JSON.stringify(data))
      return { ...data, evidenceId }
    }
    const { termId, studentId } = this.currentScope
    if (termId === undefined || studentId === undefined) {
      throw new EducationBridgeError('student-profile', 'the host has not selected a term and student')
    }
    const payload = await this.get('student-profile', `/api/v1/students/${String(studentId)}/profile?term_id=${String(termId)}`, signal)
    const data = sanitizeStudentProfile(payload)
    const evidenceId = this.registerEvidence('read_student_profile', JSON.stringify(data))
    return { ...data, evidenceId }
  }

  /** List all terms for the context selector dropdown. */
  async listTerms(signal: AbortSignal): Promise<TermOption[]> {
    if (this.options.mode === 'mock') {
      return [
        { id: 1, name: '2025 秋季学期' },
        { id: 2, name: '2026 春季学期' },
      ]
    }
    const payload = await this.get('list-terms', '/api/v1/terms?include_archived=false', signal)
    const items = Array.isArray(payload) ? payload : []
    return items.map((item: unknown): TermOption => {
      const record = item as Record<string, unknown>
      return {
        id: nonNegativeInteger(record.id, 0) || 0,
        name: text(record.name, `学期 ${String(record.id ?? '?')}`),
      }
    }).filter(item => item.id > 0)
  }

  /** List exams for a given term, for the context selector dropdown. */
  async listExams(termId: number, signal: AbortSignal): Promise<ExamOption[]> {
    if (this.options.mode === 'mock') {
      return [
        { id: 1, name: '期中考试' },
        { id: 2, name: '期末考试' },
      ]
    }
    const payload = await this.get('list-exams', `/api/v1/exams?term_id=${String(termId)}&include_archived=false`, signal)
    const items = Array.isArray(payload) ? payload : []
    return items.map((item: unknown): ExamOption => {
      const record = item as Record<string, unknown>
      return {
        id: nonNegativeInteger(record.id, 0) || 0,
        name: text(record.name, `考试 ${String(record.id ?? '?')}`),
      }
    }).filter(item => item.id > 0)
  }

  /** Search students by name for the context selector dropdown. */
  async searchStudents(query: string, signal: AbortSignal): Promise<StudentOption[]> {
    if (this.options.mode === 'mock') {
      const mockStudents = [
        { id: 1, name: '张三' },
        { id: 2, name: '李四' },
        { id: 3, name: '王五' },
      ]
      if (!query) return mockStudents
      return mockStudents.filter(s => s.name.includes(query))
    }
    const path = `/api/v1/core/students?search=${encodeURIComponent(query)}&include_archived=false`
    const payload = await this.get('search-students', path, signal)
    const items = Array.isArray(payload) ? payload : []
    return items.map((item: unknown): StudentOption => {
      const record = item as Record<string, unknown>
      return {
        id: nonNegativeInteger(record.id, 0) || 0,
        name: text(record.name, `学生 ${String(record.id ?? '?')}`),
      }
    }).filter(item => item.id > 0)
  }
}
