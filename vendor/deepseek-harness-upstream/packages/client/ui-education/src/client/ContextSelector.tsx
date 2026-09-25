import { useState, useEffect, useRef, useCallback } from 'react'
import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { InputZone } from '@deepseek-ai/dsh-client-ui-conversation/client'
import css from './ContextSelector.module.css'

export interface EducationContextSelectorInjected {
  update: (sessionId: string, input: string) => Promise<{ ok: boolean; message?: string }>
  /** Execute a host-side command and return its structured result. */
  execCommand: (sessionId: string, command: string) => Promise<{ ok: boolean; value?: unknown; error?: string }>
}

export type ContextSelectorProps = PropsRuntime<'conversation.input.dock'>
  & PropsLocale<'education'>
  & EducationContextSelectorInjected
  & InputZone

interface TermOption { id: number; name: string }
interface ExamOption { id: number; name: string }
interface StudentOption { id: number; name: string }

export function ContextSelector({ session, t, update, execCommand }: ContextSelectorProps) {
  const [terms, setTerms] = useState<TermOption[]>([])
  const [exams, setExams] = useState<ExamOption[]>([])
  const [students, setStudents] = useState<StudentOption[]>([])
  const [selectedTerm, setSelectedTerm] = useState('')
  const [selectedExam, setSelectedExam] = useState('')
  const [selectedStudent, setSelectedStudent] = useState('')
  const [studentQuery, setStudentQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadingTerms, setLoadingTerms] = useState(false)
  const [loadingExams, setLoadingExams] = useState(false)
  const [loadingStudents, setLoadingStudents] = useState(false)
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load terms on mount
  useEffect(() => {
    setLoadingTerms(true)
    void execCommand(session.sessionId, '/teachmate-list-terms').then(result => {
      setLoadingTerms(false)
      if (result.ok && result.value) {
        const terms = (result.value as { terms?: TermOption[] }).terms ?? []
        setTerms(terms)
      }
    }, () => setLoadingTerms(false))
  }, [session.sessionId, execCommand])

  // Load exams when term changes
  useEffect(() => {
    if (!selectedTerm) {
      setExams([])
      return
    }
    setLoadingExams(true)
    void execCommand(session.sessionId, `/teachmate-list-exams ${selectedTerm}`).then(result => {
      setLoadingExams(false)
      if (result.ok && result.value) {
        const exams = (result.value as { exams?: ExamOption[] }).exams ?? []
        setExams(exams)
      }
    }, () => setLoadingExams(false))
  }, [session.sessionId, selectedTerm, execCommand])

  // Debounced student search
  const searchStudents = useCallback((query: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setLoadingStudents(true)
      void execCommand(session.sessionId, `/teachmate-search-students ${query}`).then(result => {
        setLoadingStudents(false)
        if (result.ok && result.value) {
          const students = (result.value as { students?: StudentOption[] }).students ?? []
          setStudents(students)
        }
      }, () => setLoadingStudents(false))
    }, 250)
  }, [session.sessionId, execCommand])

  useEffect(() => {
    searchStudents(studentQuery)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [studentQuery, searchStudents])

  const apply = (): void => {
    const termId = selectedTerm || undefined
    const examId = selectedExam || undefined
    const studentId = selectedStudent || undefined
    if (termId === undefined || (examId === undefined && studentId === undefined)) {
      setStatus({ ok: false, text: '请填写学期，并至少选择考试或学生。' })
      return
    }
    const parts = [`term=${termId}`]
    if (examId !== undefined) parts.push(`exam=${examId}`)
    if (studentId !== undefined) parts.push(`student=${studentId}`)
    setBusy(true)
    setStatus(null)
    void update(session.sessionId, parts.join(' ')).then((result) => {
      setBusy(false)
      setStatus({ ok: result.ok, text: result.ok ? t('success') : result.message ?? t('error') })
    }, (error: unknown) => {
      setBusy(false)
      setStatus({ ok: false, text: error instanceof Error ? error.message : t('error') })
    })
  }

  const clear = (): void => {
    setBusy(true)
    setStatus(null)
    void update(session.sessionId, 'clear').then((result) => {
      setBusy(false)
      if (result.ok) {
        setSelectedTerm('')
        setSelectedExam('')
        setSelectedStudent('')
        setStudentQuery('')
        setStudents([])
      }
      setStatus({ ok: result.ok, text: result.ok ? t('success') : result.message ?? t('error') })
    }, (error: unknown) => {
      setBusy(false)
      setStatus({ ok: false, text: error instanceof Error ? error.message : t('error') })
    })
  }

  return (
    <section className={css.card} aria-label={t('title')}>
      <div className={css.copy}>
        <strong className={css.title}>{t('title')}</strong>
        <span className={css.hint}>{t('hint')}</span>
      </div>
      <div className={css.fields}>
        <select
          className={css.field}
          aria-label={t('term')}
          value={selectedTerm}
          disabled={loadingTerms}
          onChange={event => { setSelectedTerm(event.target.value); setSelectedExam('') }}
        >
          <option value="">{loadingTerms ? '加载中…' : t('term')}</option>
          {terms.map(term => <option key={term.id} value={String(term.id)}>{term.name}</option>)}
        </select>
        <select
          className={css.field}
          aria-label={t('exam')}
          value={selectedExam}
          disabled={loadingExams || !selectedTerm}
          onChange={event => setSelectedExam(event.target.value)}
        >
          <option value="">{loadingExams ? '加载中…' : t('exam')}</option>
          {exams.map(exam => <option key={exam.id} value={String(exam.id)}>{exam.name}</option>)}
        </select>
        <select
          className={css.field}
          aria-label={t('student')}
          value={selectedStudent}
          disabled={loadingStudents && !studentQuery}
          onChange={event => setSelectedStudent(event.target.value)}
        >
          <option value="">{t('student')}</option>
          {students.map(student => <option key={student.id} value={String(student.id)}>{student.name}</option>)}
        </select>
        <input
          className={css.field}
          type="search"
          placeholder="搜索学生…"
          value={studentQuery}
          onChange={event => { setStudentQuery(event.target.value); setSelectedStudent('') }}
        />
      </div>
      <div className={css.actions}>
        <button className={css.button} type="button" disabled={busy} onClick={clear}>{t('clear')}</button>
        <button className={`${css.button} ${css.primary}`} type="button" disabled={busy} onClick={apply}>{busy ? t('applying') : t('apply')}</button>
      </div>
      {status !== null && <span className={status.ok ? css.status : css.statusError} role="status">{status.text}</span>}
    </section>
  )
}
