export interface LanguageOption {
  code: string
  nameEn: string
  nameNative: string
}

export const LANGUAGES: LanguageOption[] = [
  { code: 'as', nameEn: 'Assamese', nameNative: 'অসমীয়া' },
  { code: 'bn', nameEn: 'Bengali', nameNative: 'বাংলা' },
  { code: 'brx', nameEn: 'Bodo', nameNative: 'बरʼ' },
  { code: 'doi', nameEn: 'Dogri', nameNative: 'डोगरी' },
  { code: 'gu', nameEn: 'Gujarati', nameNative: 'ગુજરાતી' },
  { code: 'en', nameEn: 'English', nameNative: 'English' },
  { code: 'hi', nameEn: 'Hindi', nameNative: 'हिन्दी' },
  { code: 'kn', nameEn: 'Kannada', nameNative: 'ಕನ್ನಡ' },
  { code: 'ks', nameEn: 'Kashmiri', nameNative: 'कॉशुर' },
  { code: 'kok', nameEn: 'Konkani', nameNative: 'कोंकणी' },
  { code: 'mai', nameEn: 'Maithili', nameNative: 'मैथिली' },
  { code: 'ml', nameEn: 'Malayalam', nameNative: 'മലയാളം' },
  { code: 'mni', nameEn: 'Manipuri (Meitei)', nameNative: 'ꯃꯤꯇꯩꯂꯣꯟ' },
  { code: 'mr', nameEn: 'Marathi', nameNative: 'मराठी' },
  { code: 'ne', nameEn: 'Nepali', nameNative: 'नेपाली' },
  { code: 'or', nameEn: 'Odia', nameNative: 'ଓଡ଼ିଆ' },
  { code: 'pa', nameEn: 'Punjabi', nameNative: 'ਪੰਜਾਬੀ' },
  { code: 'sa', nameEn: 'Sanskrit', nameNative: 'संस्कृतम्' },
  { code: 'sat', nameEn: 'Santali', nameNative: 'ᱥᱟᱱᱛᱟᱲᱤ' },
  { code: 'sd', nameEn: 'Sindhi', nameNative: 'سنڌي' },
  { code: 'ta', nameEn: 'Tamil', nameNative: 'தமிழ்' },
  { code: 'te', nameEn: 'Telugu', nameNative: 'తెలుగు' },
  { code: 'ur', nameEn: 'Urdu', nameNative: 'اردو' },
]

export const LANG_STORAGE_KEY = 'consent360_lang'
