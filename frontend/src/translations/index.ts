import type { PageTranslations } from './types'
import { en } from './en'
import { hi } from './hi'
import { ta } from './ta'
import { kn } from './kn'
import { ml } from './ml'
import { te } from './te'
import { bn } from './bn'
import { gu } from './gu'
import { mr } from './mr'
import { pa } from './pa'
import { or } from './or'
import { as } from './as'
import { ne } from './ne'
import { sd } from './sd'
import { ur } from './ur'
import { doi } from './doi'
import { ks } from './ks'
import { kok } from './kok'
import { mai } from './mai'
import { brx } from './brx'
import { sat } from './sat'
import { mni } from './mni'
import { sa } from './sa'

const translations: Record<string, PageTranslations> = {
  en, hi, ta, kn, ml, te, bn, gu, mr, pa, or, as, ne, sd, ur, doi, ks, kok, mai, brx, sat, mni, sa,
}

export function getTranslations(lang: string): PageTranslations {
  return translations[lang] || en
}

export type { PageTranslations }
