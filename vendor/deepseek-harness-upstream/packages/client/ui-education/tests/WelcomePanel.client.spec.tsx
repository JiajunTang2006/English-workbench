// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type {} from '../src/client/index.ts'
import { WelcomePanel } from '../src/client/WelcomePanel.tsx'

afterEach(() => { cleanup() })

describe('TeachMate welcome panel', () => {
  it('seeds a reviewable prompt without submitting it', () => {
    const setDraft = vi.fn()
    const t = (key: string): string => key
    render(<WelcomePanel
      session={{ blank: true } as never}
      input={{} as never}
      inputActions={{ setDraft } as never}
      t={t as never}
      useSession={(() => ({})) as never}
      useProjection={(() => ({})) as never}
      useInput={(() => ({})) as never}
      useSessions={(() => ({})) as never}
      useWorkspaces={(() => ({})) as never}
      sessionId={'session' as never}
    />)

    fireEvent.click(screen.getByRole('button', { name: /welcome\.exam/ }))
    expect(setDraft).toHaveBeenCalledWith('请分析当前考试成绩，找出班级最需要提升的维度。')
  })

  it('does not render after the first message is accepted', () => {
    render(<WelcomePanel
      session={{ blank: false } as never}
      input={{} as never}
      inputActions={{ setDraft: vi.fn() } as never}
      t={((key: string) => key) as never}
      useSession={(() => ({})) as never}
      useProjection={(() => ({})) as never}
      useInput={(() => ({})) as never}
      useSessions={(() => ({})) as never}
      useWorkspaces={(() => ({})) as never}
      sessionId={'session' as never}
    />)
    expect(screen.queryByRole('button', { name: /welcome\.exam/ })).toBeNull()
  })
})
