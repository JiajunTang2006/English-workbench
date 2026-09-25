import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { InputZone } from '@deepseek-ai/dsh-client-ui-conversation/client'
import css from './WelcomePanel.module.css'

type WelcomeProps = PropsRuntime<'conversation.input.dock'> & PropsLocale<'education'> & InputZone

interface WelcomeCard {
  key: 'exam' | 'student' | 'report'
  prompt: string
  tone: string | undefined
}

const cards: readonly WelcomeCard[] = [
  { key: 'exam', prompt: '请分析当前考试成绩，找出班级最需要提升的维度。', tone: css.exam },
  { key: 'student', prompt: '请查看当前学生的学习历史，给出个性化学习建议。', tone: css.lesson },
  { key: 'report', prompt: '请根据当前考试数据生成一份教学分析报告草稿。', tone: css.assignment },
]

/** Blank-session welcome cards. Clicking a card only seeds the composer. */
export function WelcomePanel({ session, t, inputActions }: WelcomeProps) {
  if (!session.blank) return null

  return (
    <section className={css.panel} aria-label={t('welcome.title')}>
      <div className={css.heading}>
        <strong className={css.title}>{t('welcome.title')}</strong>
        <span className={css.subtitle}>{t('welcome.subtitle')}</span>
      </div>
      <div className={css.grid}>
        {cards.map(card => (
          <button
            key={card.key}
            type="button"
            className={`${css.card} ${card.tone ?? ''}`}
            onClick={() => { inputActions.setDraft(card.prompt) }}
          >
            <span className={css.cardTitle}>{t(`welcome.${card.key}`)}</span>
            <span className={css.cardDescription}>{t(`welcome.${card.key}.description`)}</span>
          </button>
        ))}
      </div>
    </section>
  )
}
