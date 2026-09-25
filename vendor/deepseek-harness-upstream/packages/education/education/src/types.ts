/** JSON-safe education values exchanged with the WorkBench Bridge. */

/** WorkBench scope selected by the host application, not by the model. */
export interface EducationScope {
  readonly termId?: number
  readonly examId?: number
  readonly studentId?: number
}

/** Sanitized exam facts shown to the model. */
export interface ExamOverview {
  readonly examName: string
  readonly fullScore: number
  readonly presentCount: number
  readonly absentCount: number
  readonly average: number | null
  readonly highest: number | null
  readonly lowest: number | null
}

/** Sanitized student facts shown to the model. */
export interface StudentProfile {
  readonly scoreHistory: {
    readonly examName: string
    readonly scoreRate: number | null
    readonly totalScore: number | null
    readonly classRank: number | null
    readonly tier: string | null
  }[]
  readonly latestScore: number | null
}

/** A structured teaching report draft. */
export interface TeachingReportDraft {
  readonly status: 'draft'
  readonly reportType: 'exam_analysis' | 'student_diagnosis' | 'review_plan'
  readonly title: string
  readonly summary: string
  readonly findings: { readonly label: string; readonly detail: string; readonly confidence: number }[]
  readonly recommendations: string[]
  readonly limitations: string[]
  readonly evidence: { readonly evidenceId: string; readonly source: string; readonly fact: string }[]
  readonly requiresTeacherConfirmation: true
}

/** Bridge operation names used for bounded diagnostics. */
export type EducationBridgeOperation = 'exam-overview' | 'student-profile' | 'list-terms' | 'list-exams' | 'search-students'

/** Dropdown option for the context selector. */
export interface TermOption {
  readonly id: number
  readonly name: string
}

/** Dropdown option for the context selector. */
export interface ExamOption {
  readonly id: number
  readonly name: string
}

/** Dropdown option for the context selector. */
export interface StudentOption {
  readonly id: number
  readonly name: string
}
