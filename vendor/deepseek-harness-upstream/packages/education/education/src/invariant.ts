/** Package-owned invariant companion for dsh-education. */

import type { Context } from '@deepseek-ai/cordis'
import type { InvariantInstaller } from '@deepseek-ai/dsh-invariants'

/** Cordis plugin name. */
export const name = 'education-invariant'
/** Service required before registering the companion. */
export const inject = ['invariants']

/**
 * No runtime invariant: the plugin's observable obligations are enforced at
 * the tool executor and HTTP response boundaries; its Bridge has no durable
 * event stream or shared mutable registry to audit.
 */
const install: InvariantInstaller = () => {}

/** Register the package invariant companion. */
export const apply = (ctx: Context): Promise<() => void> =>
  Promise.resolve(ctx.invariants.register('@deepseek-ai/dsh-education', install))
