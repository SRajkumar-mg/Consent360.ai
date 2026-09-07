import type { PartialPortalStrings, PortalStrings } from './types'
import { en } from './en'
import { as } from './as'
import { bn } from './bn'
import { brx } from './brx'
import { doi } from './doi'
import { gu } from './gu'
import { hi } from './hi'
import { kn } from './kn'
import { kok } from './kok'
import { ks } from './ks'
import { mai } from './mai'
import { ml } from './ml'
import { mni } from './mni'
import { mr } from './mr'
import { ne } from './ne'
import { or } from './or'
import { pa } from './pa'
import { sa } from './sa'
import { sat } from './sat'
import { sd } from './sd'
import { ta } from './ta'
import { te } from './te'
import { ur } from './ur'

const catalogues: Record<string, PartialPortalStrings> = {
  as, bn, brx, doi, gu, hi, kn, kok, ks, mai, ml, mni, mr, ne, or, pa, sa, sat, sd, ta, te, ur,
}

/** Languages whose script runs right to left. `dir` is set on the portal root
 *  from this, so Urdu, Sindhi and Kashmiri lay out correctly rather than
 *  rendering right-to-left text inside a left-to-right layout. */
const RTL = new Set(['ur', 'sd', 'ks'])

export function isRtl(lang: string): boolean {
  return RTL.has(lang)
}

/** Recursive merge of a partial catalogue over English.
 *
 *  Per KEY, not per file: an incomplete language still serves everything it
 *  has translated, and a key it is missing appears in English rather than
 *  breaking the build or rendering `undefined`. An empty string counts as
 *  missing - a blank label on a rights screen is worse than an English one. */
function merge<T>(base: T, over: unknown): T {
  if (!over || typeof over !== 'object') return base
  const out = { ...(base as object) } as Record<string, unknown>
  for (const [key, value] of Object.entries(over as Record<string, unknown>)) {
    if (!(key in out)) continue
    const current = out[key]
    if (typeof current === 'object' && current !== null) {
      out[key] = merge(current, value)
    } else if (typeof value === 'string' && value.trim() !== '') {
      out[key] = value
    }
  }
  return out as T
}

const cache = new Map<string, PortalStrings>()

export function getPortalStrings(lang: string): PortalStrings {
  if (lang === 'en' || !catalogues[lang]) return en
  const hit = cache.get(lang)
  if (hit) return hit
  const built = merge(en, catalogues[lang])
  cache.set(lang, built)
  return built
}

/** Substitutes `{name}` placeholders. Kept deliberately dumb - no plural
 *  rules, no date formatting - because a translator only ever has to move the
 *  placeholder, never match a grammar the catalogue cannot express. */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key) =>
    key in values ? String(values[key]) : match,
  )
}

export type { PortalStrings, PartialPortalStrings }
