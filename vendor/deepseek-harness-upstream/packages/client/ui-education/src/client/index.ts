import type { ClientContext } from '@deepseek-ai/dsh-client-runtime/client'
import type { SessionId } from '@deepseek-ai/dsh-client-runtime/client'
import type {} from '@deepseek-ai/dsh-api-remotes/client'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import { ContextSelector, type EducationContextSelectorInjected } from './ContextSelector.tsx'
import { WelcomePanel } from './WelcomePanel.tsx'
import { en, zh, type EducationKey } from './locales.ts'

export type { EducationKey, EducationContextSelectorInjected }

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    education: EducationKey
  }
}

const NS = 'education'

export const inject = ['locale', 'remote', 'slots']

export function apply(ctx: ClientContext): void {
  ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'ui-education: dictionaries')
  ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
    name: 'conversation.input.dock',
    id: 'teachmate-welcome',
    order: 0,
    locale: NS,
  }, WelcomePanel))
  ctx.slots.inject('conversation.input.dock', () => ctx.slots.register({
    name: 'conversation.input.dock',
    id: 'teachmate-context',
    order: 5,
    locale: NS,
    inject: (_sessionId: SessionId): EducationContextSelectorInjected => ({
      update: async (sessionId, input) => {
        const result = await ctx.remote.commands.execute(sessionId as SessionId, `/teachmate-context ${input}`)
        return result.ok
          ? { ok: true, ...result.value?.result.text === undefined ? {} : { message: result.value.result.text } }
          : { ok: false, message: result.error.message }
      },
      execCommand: async (sessionId, command) => {
        const result = await ctx.remote.commands.execute(sessionId as SessionId, command)
        if (result.ok) {
          return { ok: true, value: result.value?.result }
        }
        return { ok: false, error: result.error.message }
      },
    }),
  }, ContextSelector))
}
