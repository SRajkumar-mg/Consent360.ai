// Unauthenticated /public/* lookups used to render the Rule 3 notice elements
// (itemised data items, purposes, services, retention, consent text, DPO
// contact and the rights links) directly on the consent banner - the same
// fields the notice modal shows, just visible before a decision is made.
export interface PublicPurpose {
  purpose_version_id: number
  code: string
  name: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  consent_text: string
  data_categories: string[]
  processing_activities: string[]
  translations: Record<string, Record<string, string>>
}

export interface PublicPrivacyContact {
  tenant_code: string
  tenant_name: string
  dpo_name: string
  dpo_email: string
  dpo_phone: string
}

export interface PublicRights {
  tenant_code: string
  tenant_name: string
  rights_url: string
  withdraw_url: string
  grievance_url: string
  board_complaint_url: string
  grievance_response_days: number
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

export const publicApi = {
  purposes: (tenantCode: string) => getJson<PublicPurpose[]>(`/public/${tenantCode}/purposes`),
  privacyContact: (tenantCode: string) => getJson<PublicPrivacyContact>(`/public/${tenantCode}/privacy-contact`),
  rights: (tenantCode: string) => getJson<PublicRights>(`/public/${tenantCode}/rights`),
}

// Purpose text falls back to the base (English) fields whenever the selected
// language has no translation entry, or is missing individual keys within it.
export function localize(p: PublicPurpose, lang: string | undefined) {
  const t = (lang && p.translations[lang]) || {}
  return {
    name: t.name || p.name,
    description: t.description || p.description,
    consent_text: t.consent_text || p.consent_text,
  }
}
