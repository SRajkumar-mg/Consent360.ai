export interface LanguageOption {
  code: string
  nameEn: string
  nameNative: string
  enabled: boolean
}

export const LANGUAGES: LanguageOption[] = [
  { code: 'as', nameEn: 'Assamese', nameNative: 'অসমীয়া', enabled: false },
  { code: 'bn', nameEn: 'Bengali', nameNative: 'বাংলা', enabled: true },
  { code: 'brx', nameEn: 'Bodo', nameNative: 'बरʼ', enabled: false },
  { code: 'doi', nameEn: 'Dogri', nameNative: 'डोगरी', enabled: false },
  { code: 'gu', nameEn: 'Gujarati', nameNative: 'ગુજરાતી', enabled: true },
  { code: 'en', nameEn: 'English', nameNative: 'English', enabled: true },
  { code: 'hi', nameEn: 'Hindi', nameNative: 'हिन्दी', enabled: true },
  { code: 'kn', nameEn: 'Kannada', nameNative: 'ಕನ್ನಡ', enabled: true },
  { code: 'ks', nameEn: 'Kashmiri', nameNative: 'कॉशुर', enabled: false },
  { code: 'kok', nameEn: 'Konkani', nameNative: 'कोंकणी', enabled: false },
  { code: 'mai', nameEn: 'Maithili', nameNative: 'मैथिली', enabled: false },
  { code: 'ml', nameEn: 'Malayalam', nameNative: 'മലയാളം', enabled: true },
  { code: 'mni', nameEn: 'Manipuri (Meitei)', nameNative: 'ꯃꯤꯇꯩꯂꯣꯟ', enabled: false },
  { code: 'mr', nameEn: 'Marathi', nameNative: 'मराठी', enabled: true },
  { code: 'ne', nameEn: 'Nepali', nameNative: 'नेपाली', enabled: false },
  { code: 'or', nameEn: 'Odia', nameNative: 'ଓଡ଼ିଆ', enabled: false },
  { code: 'pa', nameEn: 'Punjabi', nameNative: 'ਪੰਜਾਬੀ', enabled: false },
  { code: 'sa', nameEn: 'Sanskrit', nameNative: 'संस्कृतम्', enabled: false },
  { code: 'sat', nameEn: 'Santali', nameNative: 'ᱥᱟᱱᱛᱟᱲᱤ', enabled: false },
  { code: 'sd', nameEn: 'Sindhi', nameNative: 'سنڌي', enabled: false },
  { code: 'ta', nameEn: 'Tamil', nameNative: 'தமிழ்', enabled: true },
  { code: 'te', nameEn: 'Telugu', nameNative: 'తెలుగు', enabled: true },
  { code: 'ur', nameEn: 'Urdu', nameNative: 'اردو', enabled: false },
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
