export interface LanguageOption {
  code: string
  nameEn: string
  nameNative: string
}

// Same curated language set already offered by the other demo sites (SkillLearn).
export const LANGUAGES: LanguageOption[] = [
  { code: 'en', nameEn: 'English', nameNative: 'English' },
  { code: 'hi', nameEn: 'Hindi', nameNative: 'हिन्दी' },
  { code: 'ta', nameEn: 'Tamil', nameNative: 'தமிழ்' },
  { code: 'te', nameEn: 'Telugu', nameNative: 'తెలుగు' },
  { code: 'kn', nameEn: 'Kannada', nameNative: 'ಕನ್ನಡ' },
  { code: 'ml', nameEn: 'Malayalam', nameNative: 'മലയാളം' },
  { code: 'bn', nameEn: 'Bengali', nameNative: 'বাংলা' },
  { code: 'mr', nameEn: 'Marathi', nameNative: 'मराठी' },
  { code: 'gu', nameEn: 'Gujarati', nameNative: 'ગુજરાતી' },
  { code: 'pa', nameEn: 'Punjabi', nameNative: 'ਪੰਜਾਬੀ' },
]

export const CODEX_LANG_KEY = 'codex_lang'
// R2-10: local cache of the visitor's decision so the consent gate (consentGate.ts)
// can enforce it synchronously on load without waiting on a network round trip.
export const COOKIE_CONSENT_KEY = 'codex_cookie_consent'
