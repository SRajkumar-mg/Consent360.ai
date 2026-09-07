import { getTranslations, type PageTranslations } from '../translations'

export function useTranslation<T extends keyof PageTranslations>(section: T, lang: string): PageTranslations[T] {
  const t = getTranslations(lang)
  return t[section]
}
