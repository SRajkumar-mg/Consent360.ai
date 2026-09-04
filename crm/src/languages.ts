export interface LanguageOption {
  code: string
  nameEn: string
  nameNative: string
  enabled: boolean
}

export const LANGUAGES: LanguageOption[] = [
  { code: 'as', nameEn: 'Assamese', nameNative: 'অসমীয়া', enabled: true },
  { code: 'bn', nameEn: 'Bengali', nameNative: 'বাংলা', enabled: true },
  { code: 'brx', nameEn: 'Bodo', nameNative: 'बरʼ', enabled: true },
  { code: 'doi', nameEn: 'Dogri', nameNative: 'डोगरी', enabled: true },
  { code: 'gu', nameEn: 'Gujarati', nameNative: 'ગુજરાતી', enabled: true },
  { code: 'en', nameEn: 'English', nameNative: 'English', enabled: true },
  { code: 'hi', nameEn: 'Hindi', nameNative: 'हिन्दी', enabled: true },
  { code: 'kn', nameEn: 'Kannada', nameNative: 'ಕನ್ನಡ', enabled: true },
  { code: 'ks', nameEn: 'Kashmiri', nameNative: 'कॉशुर', enabled: true },
  { code: 'kok', nameEn: 'Konkani', nameNative: 'कोंकणी', enabled: true },
  { code: 'mai', nameEn: 'Maithili', nameNative: 'मैथिली', enabled: true },
  { code: 'ml', nameEn: 'Malayalam', nameNative: 'മലയാളം', enabled: true },
  { code: 'mni', nameEn: 'Manipuri (Meitei)', nameNative: 'ꯃꯤꯇꯩꯂꯣꯟ', enabled: true },
  { code: 'mr', nameEn: 'Marathi', nameNative: 'मराठी', enabled: true },
  { code: 'ne', nameEn: 'Nepali', nameNative: 'नेपाली', enabled: true },
  { code: 'or', nameEn: 'Odia', nameNative: 'ଓଡ଼ିଆ', enabled: true },
  { code: 'pa', nameEn: 'Punjabi', nameNative: 'ਪੰਜਾਬੀ', enabled: true },
  { code: 'sa', nameEn: 'Sanskrit', nameNative: 'संस्कृतम्', enabled: true },
  { code: 'sat', nameEn: 'Santali', nameNative: 'ᱥᱟᱱᱛᱟᱲᱤ', enabled: true },
  { code: 'sd', nameEn: 'Sindhi', nameNative: 'سنڌي', enabled: true },
  { code: 'ta', nameEn: 'Tamil', nameNative: 'தமிழ்', enabled: true },
  { code: 'te', nameEn: 'Telugu', nameNative: 'తెలుగు', enabled: true },
  { code: 'ur', nameEn: 'Urdu', nameNative: 'اردو', enabled: true },
]

export type BannerCopyKey =
  | 'necessary'
  | 'necessaryDesc'
  | 'functional'
  | 'functionalDesc'
  | 'analytics'
  | 'analyticsDesc'
  | 'advertising'
  | 'advertisingDesc'

export interface CookieCategory {
  id: string
  labelKey: BannerCopyKey
  descKey: BannerCopyKey
  locked?: boolean
}

export const COOKIE_CATEGORIES: CookieCategory[] = [
  { id: 'necessary', labelKey: 'necessary', descKey: 'necessaryDesc', locked: true },
  { id: 'functional', labelKey: 'functional', descKey: 'functionalDesc' },
  { id: 'analytics', labelKey: 'analytics', descKey: 'analyticsDesc' },
  { id: 'advertising', labelKey: 'advertising', descKey: 'advertisingDesc' },
]

export const COOKIE_CONSENT_KEY = 'crm_cookie_consent'
export const CRM_LANG_KEY = 'crm_lang'
