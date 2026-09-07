/**
 * Consent purposes.
 *
 * R2-08 extends this screen to everything a purpose version actually carries
 * rather than the five fields it used to edit:
 *
 *  - **Itemised data with a necessity flag** (s.5(1)(i) / B-04). Naming a
 *    category is not itemisation, and "necessary" is not decoration: it is the
 *    flag the minimisation check reads, so it is edited per item rather than
 *    inferred.
 *  - **Translations in all 23 languages.** The shape written here —
 *    `{lang: {name, description, consent_text}}` — is exactly what every client
 *    app's `localize()` reads, so a language left blank falls back to English
 *    at the banner rather than showing nothing. The editor therefore counts
 *    coverage honestly: a language with an empty entry is *not* covered, and an
 *    empty entry is stripped before saving so it can never inflate the count.
 *  - **Child-restricted** (s.9) and the services the processing enables.
 *
 * Every save still passes through the plain-language/dark-pattern checklist
 * (R2-09 / A-09), because every save publishes a purpose version and a purpose
 * version is notice content. After the save the screen reports how R1-09
 * classified the change: a MATERIAL change invalidates existing consents, and
 * an editor who is not told that will not know they have just opened a
 * re-consent campaign.
 */
import { useEffect, useMemo, useState } from 'react'
import { activitiesApi, categoriesApi, purposesApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, FooterRow, MetricCard, Modal, PageHeading, Spinner, useToast, formatDate } from '../components/ui'
import { NoticeChecklistGate, type NoticeChecklistRecord } from '../components/NoticeChecklist'
import { Tabs } from '../components/GuardedAction'
import { IconCheck, IconGlobe, IconHistory, IconPlus, IconShield, IconUsers } from '../components/icons'
import { LanguageSelector, usePersistedLang } from '../components/LanguageSelector'
import { useAuth } from '../context/AuthContext'
import { useTranslation } from '../hooks/useTranslation'
import { LANGUAGES } from '../languages'
import type {
  ChangeClassification, DataCategory, DataItem, ProcessingActivity, Purpose, PurposeLangContent, PurposeVersion,
} from '../types'

type EditorTab = 'details' | 'items' | 'translations'

const LANG_NAME: Record<string, string> = Object.fromEntries(LANGUAGES.map((l) => [l.code, l.nameEn]))

/** A language counts only when it actually carries text. */
function langFilled(c?: PurposeLangContent): boolean {
  return Boolean(c && (c.name?.trim() || c.description?.trim() || c.consent_text?.trim()))
}

function coveredLanguages(v?: PurposeVersion): string[] {
  const covered = new Set<string>(['en'])
  for (const [code, content] of Object.entries(v?.translations || {})) {
    if (langFilled(content)) covered.add(code)
  }
  return LANGUAGES.filter((l) => covered.has(l.code)).map((l) => l.code)
}

interface Form {
  name: string
  code: string
  description: string
  legal_basis: string
  requires_consent: boolean
  retention_period_days: number
  consent_text: string
  data_category_ids: number[]
  processing_activity_ids: number[]
  data_items: DataItem[]
  services_enabled: string
  child_restricted: boolean
  translations: Record<string, PurposeLangContent>
}

const BLANK: Form = {
  name: '', code: '', description: '', legal_basis: 'CONSENT', requires_consent: true,
  retention_period_days: 365, consent_text: '', data_category_ids: [], processing_activity_ids: [],
  data_items: [], services_enabled: '', child_restricted: false, translations: {},
}

export function PurposesPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('purpose.manage')
  const [lang, setLang] = usePersistedLang()
  const t = useTranslation('purposes', lang)
  const [purposes, setPurposes] = useState<Purpose[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Purpose | null>(null)
  const [creating, setCreating] = useState(false)
  const [versioning, setVersioning] = useState<Purpose | null>(null)
  const [versionReason, setVersionReason] = useState('')
  const [categories, setCategories] = useState<DataCategory[]>([])
  const [activities, setActivities] = useState<ProcessingActivity[]>([])
  const [tab, setTab] = useState<EditorTab>('details')
  const [editLang, setEditLang] = useState('hi')
  const [classification, setClassification] = useState<ChangeClassification | null>(null)

  const load = async () => {
    const r = await purposesApi.list()
    setPurposes(r.data)
  }

  useEffect(() => {
    setLoading(true)
    Promise.all([purposesApi.list(), categoriesApi.list(), activitiesApi.list()])
      .then(([p, c, a]) => { setPurposes(p.data); setCategories(c.data); setActivities(a.data) })
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [])

  const [form, setForm] = useState<Form>({ ...BLANK })

  const catName = (id: number) => categories.find((c) => c.id === id)?.name || `Category ${id}`

  const openCreate = () => {
    setForm({ ...BLANK })
    setTab('details')
    setEditLang('hi')
    setCreating(true)
    setEditing(null)
  }

  const openEdit = (p: Purpose) => {
    const pv = [...p.versions].sort((a, b) => b.version_number - a.version_number)[0]
    setForm({
      name: p.name, code: p.code, description: p.description, legal_basis: p.legal_basis,
      requires_consent: p.requires_consent, retention_period_days: p.retention_period_days,
      consent_text: pv?.consent_text || '',
      data_category_ids: pv?.data_category_ids || [],
      processing_activity_ids: pv?.processing_activity_ids || [],
      data_items: [...(pv?.data_items || [])],
      services_enabled: pv?.services_enabled || p.services_enabled || '',
      child_restricted: Boolean(pv?.child_restricted ?? p.child_restricted),
      translations: { ...(pv?.translations || {}) },
    })
    setTab('details')
    setEditLang('hi')
    setEditing(p)
    setCreating(false)
  }

  // R2-09 / gap A-09: every purpose version is notice content shown to Data
  // Principals, so every save that publishes one (create = v1, edit = v+1, an
  // explicit new version) is gated behind the plain-language / dark-pattern
  // checklist below rather than calling the API directly.
  const [checklistFor, setChecklistFor] = useState<null | { action: 'save' | 'version'; versionNumber: number; label: string }>(null)

  // Maps the modal's camelCase draft shape onto the backend's ReviewChecklistIn
  // (snake_case) so the completed review travels with the publish request
  // instead of only ever living in this browser's localStorage.
  const toChecklistPayload = (record: NoticeChecklistRecord) => ({
    reviewer: record.reviewer,
    completed_at: record.completedAt,
    items: record.items,
  })

  /** Drop languages the editor opened but left blank — an empty entry would
   *  otherwise count as coverage on every screen that counts languages. */
  const cleanTranslations = (input: Record<string, PurposeLangContent>) => {
    const out: Record<string, PurposeLangContent> = {}
    for (const [code, content] of Object.entries(input)) {
      if (langFilled(content)) {
        out[code] = {
          name: content.name || '',
          description: content.description || '',
          consent_text: content.consent_text || '',
        }
      }
    }
    return out
  }

  const doSave = async (checklist: NoticeChecklistRecord) => {
    try {
      const shared = {
        name: form.name,
        description: form.description,
        legal_basis: form.legal_basis,
        requires_consent: form.requires_consent,
        retention_period_days: form.retention_period_days,
        consent_text: form.consent_text,
        data_category_ids: form.data_category_ids,
        processing_activity_ids: form.processing_activity_ids,
        data_items: form.data_items,
        services_enabled: form.services_enabled,
        child_restricted: form.child_restricted,
        translations: cleanTranslations(form.translations),
        checklist: toChecklistPayload(checklist),
      }
      if (editing) {
        const r = await purposesApi.update(editing.id, shared)
        toast('success', `Purpose updated. Changes versioned to v${editing.current_version + 1}`)
        if (r.data.change) setClassification(r.data.change)
      } else {
        const r = await purposesApi.create({ ...shared, code: form.code })
        toast('success', 'Purpose created')
        if (r.data.change) setClassification(r.data.change)
      }
      setEditing(null); setCreating(false)
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const save = () => {
    const versionNumber = editing ? editing.current_version + 1 : 1
    setChecklistFor({ action: 'save', versionNumber, label: form.name || form.code })
  }

  const doVersion = async (checklist: NoticeChecklistRecord) => {
    if (!versioning) return
    try {
      const r = await purposesApi.addVersion(versioning.id, { reason: versionReason, checklist: toChecklistPayload(checklist) })
      toast('success', `New version created for ${versioning.name}`)
      if (r.data.change) setClassification(r.data.change)
      setVersioning(null); setVersionReason('')
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const requestVersion = () => {
    if (!versioning) return
    setChecklistFor({ action: 'version', versionNumber: versioning.current_version + 1, label: versioning.name })
  }

  const totalActive = useMemo(() => purposes.reduce((n, p) => n + p.versions.filter((v) => v.is_current).reduce((m, v) => m + v.data_category_ids.length, 0), 0), [purposes])

  const languagesInUse = useMemo(() => {
    const set = new Set<string>()
    for (const p of purposes) {
      const current = [...p.versions].sort((a, b) => b.version_number - a.version_number)[0]
      for (const code of coveredLanguages(current)) set.add(code)
    }
    return set.size
  }, [purposes])

  const oldestCreated = useMemo(() => (
    purposes.length
      ? purposes.reduce((min, p) => (new Date(p.created_at) < new Date(min) ? p.created_at : min), purposes[0].created_at)
      : null
  ), [purposes])

  const formCoverage = useMemo(() => {
    const covered = new Set<string>(['en'])
    for (const [code, content] of Object.entries(form.translations)) {
      if (langFilled(content)) covered.add(code)
    }
    return covered
  }, [form.translations])

  if (loading) return <Spinner />

  const entry = form.translations[editLang] || {}

  return (
    <div>
      <PageHeading
        title={t.pageTitle}
        subtitle={t.pageSubtitle}
        actions={
          <>
            <LanguageSelector value={lang} onChange={setLang} />
            {canManage && <button className="btn btn-primary" onClick={openCreate}>{t.newPurpose}</button>}
          </>
        }
      />

      <div className="metric-grid mb">
        <MetricCard label={t.activePurposes} value={purposes.filter((p) => p.is_active).length} icon={<IconShield size={20} />} tone="primary" sub={t.activePurposesSub} />
        <MetricCard label={t.consentRequired} value={purposes.filter((p) => p.requires_consent).length} icon={<IconCheck size={20} />} tone="info" sub={t.consentRequiredSub} />
        <MetricCard label={t.purposeVersions} value={purposes.reduce((n, p) => n + p.versions.length, 0)} icon={<IconHistory size={20} />} tone="purple" sub={t.purposeVersionsSub} />
        <MetricCard label="Languages in use" value={`${languagesInUse} of 23`} icon={<IconGlobe size={20} />} tone="success"
          sub={`${totalActive} category links across current versions`} />
      </div>

      {purposes.map((p) => {
        const current = [...p.versions].sort((a, b) => b.version_number - a.version_number)[0]
        const covered = coveredLanguages(current)
        return (
          <div className="card card-hover mb" key={p.id}>
            <div className="card-header">
              <div>
                <h3>{p.name} <span className="text-xs text-muted mono">({p.code})</span></h3>
              </div>
              <div className="flex">
                <Badge status={p.requires_consent ? 'ACTIVE' : 'REQUESTED'}>{p.requires_consent ? t.consentReqBadge : t.consentNotReqBadge}</Badge>
                <Badge status={p.is_active ? 'ACTIVE' : 'EXPIRED'}>{p.is_active ? t.active : t.inactive}</Badge>
                {(current?.child_restricted ?? p.child_restricted) && <Badge status="DENIED">Child-restricted</Badge>}
                {canManage && <button className="btn btn-sm" onClick={() => openEdit(p)}>{t.edit}</button>}
                {canManage && <button className="btn btn-sm" onClick={() => { setVersioning(p); setVersionReason('') }}>{t.newVersion}</button>}
              </div>
            </div>
            <div className="card-body">
              <div className="meta-row">
                <span className="meta-item">{t.legalBasisLabel}: <b>{p.legal_basis}</b></span>
                <span className="meta-item">Retention: <b>{p.retention_period_days} days</b></span>
                <span className="meta-item">Current version: <b>v{p.current_version}</b></span>
                <span className="meta-item">Languages: <b>{covered.length} of 23</b></span>
                {current?.checklist ? (
                  <span className="meta-item" title={`Reviewed by ${current.checklist.reviewer} on ${formatDate(current.checklist.completed_at)}`}>
                    Plain-language review: <b>recorded</b>
                  </span>
                ) : (
                  <span className="meta-item text-muted">Plain-language review: not recorded</span>
                )}
              </div>
              <p className="text-sm text-secondary">{p.description || t.noDescription}</p>

              <div className="chip-group-title">Itemised data (s.5(1)(i))</div>
              <div className="chip-group">
                {(current?.data_items || []).length === 0
                  ? <span className="text-muted text-sm">Not itemised — only the category links below are declared</span>
                  : (current?.data_items || []).map((d, i) => (
                    <span key={`${d.data_category_id}-${i}`} className={`chip${d.necessity ? ' chip-necessary' : ''}`} title={d.description}>
                      {catName(d.data_category_id)} · {d.necessity ? 'necessary' : 'optional'}
                    </span>
                  ))}
              </div>

              <div className="chip-group-title">{t.dataCategories}</div>
              <div className="chip-group">
                {(current?.data_category_ids || []).length === 0 ? <span className="text-muted text-sm">{t.none}</span> : current?.data_category_ids.map((id) => {
                  const c = categories.find((x) => x.id === id)
                  return c ? <span key={id} className="chip">{c.name}</span> : null
                })}
              </div>

              <div className="chip-group-title">{t.processingActivities}</div>
              <div className="chip-group">
                {(current?.processing_activity_ids || []).length === 0 ? <span className="text-muted text-sm">{t.none}</span> : current?.processing_activity_ids.map((id) => {
                  const a = activities.find((x) => x.id === id)
                  return a ? <span key={id} className="chip">{a.name}</span> : null
                })}
              </div>

              <div className="chip-group-title">Translations</div>
              <div className="chip-group">
                {LANGUAGES.map((l) => (
                  <span key={l.code} className={`chip lang-chip${covered.includes(l.code) ? ' lang-chip-on' : ''}`}
                    title={covered.includes(l.code)
                      ? `${l.nameEn}: translated`
                      : `${l.nameEn}: not translated — clients fall back to the English text`}>
                    {l.code}
                  </span>
                ))}
              </div>

              {current?.consent_text && (
                <div className="quote">&ldquo;{current.consent_text}&rdquo;</div>
              )}

              <div className="card-footnote">
                {t.versions} v{p.current_version} {t.current} · Last modified: {formatDate(current?.effective_from)}
              </div>
            </div>
          </div>
        )
      })}

      <FooterRow left={`Directory created: ${oldestCreated ? formatDate(oldestCreated) : '—'}`} />

      {/* ----------------------------- Editor ----------------------------- */}
      <Modal open={creating || !!editing} title={editing ? `${t.modalEditTitle} — ${editing.name}` : t.modalCreateTitle}
        onClose={() => { setCreating(false); setEditing(null) }} wide>
        <Tabs<EditorTab>
          active={tab}
          onChange={setTab}
          tabs={[
            { id: 'details', label: 'Details' },
            { id: 'items', label: 'Itemised data', count: form.data_items.length },
            { id: 'translations', label: 'Translations', count: formCoverage.size },
          ]}
        />

        {tab === 'details' && (
          <>
            <div className="form-group">
              <label>{t.nameLabel}</label>
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="form-row">
              <div className="form-group"><label>{t.codeLabel}</label><input className="input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} disabled={!!editing} /></div>
              <div className="form-group"><label>{t.legalBasisLabel}</label>
                <select className="select" value={form.legal_basis} onChange={(e) => setForm({ ...form, legal_basis: e.target.value })}>
                  {['CONSENT', 'CONTRACT', 'LEGAL_OBLIGATION', 'LEGITIMATE_INTEREST', 'VITAL_INTEREST', 'PUBLIC_INTEREST'].map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
              </div>
              <div className="form-group"><label>{t.retentionLabel}</label><input className="input" type="number" min={0} value={form.retention_period_days} onChange={(e) => setForm({ ...form, retention_period_days: Number(e.target.value) })} /></div>
            </div>
            <div className="form-group">
              <label>{t.descriptionLabel}</label>
              <textarea className="textarea" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="form-row">
              <div className="form-group">
                <label>{t.dataCategoriesLabel}</label>
                <select className="select" multiple size={4} value={form.data_category_ids.map(String)} onChange={(e) => setForm({ ...form, data_category_ids: [...e.target.selectedOptions].map((o) => Number(o.value)) })}>
                  {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>{t.processingActivitiesLabel}</label>
                <select className="select" multiple size={4} value={form.processing_activity_ids.map(String)} onChange={(e) => setForm({ ...form, processing_activity_ids: [...e.target.selectedOptions].map((o) => Number(o.value)) })}>
                  {activities.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </div>
            </div>
            <div className="form-group">
              <label>{t.consentTextLabel} <span className="text-xs text-muted">({t.consentTextSub})</span></label>
              <textarea className="textarea" value={form.consent_text} onChange={(e) => setForm({ ...form, consent_text: e.target.value })} />
            </div>
            <div className="form-group">
              <label>Services this processing enables</label>
              <textarea className="textarea" rows={2} value={form.services_enabled}
                onChange={(e) => setForm({ ...form, services_enabled: e.target.value })}
                placeholder="What the principal gets in return — shown in the notice" />
            </div>
            <div className="form-group">
              <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={form.requires_consent} onChange={(e) => setForm({ ...form, requires_consent: e.target.checked })} />
                {t.consentRequiredLabel}
              </label>
            </div>
            <div className="form-group">
              <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={form.child_restricted} onChange={(e) => setForm({ ...form, child_restricted: e.target.checked })} />
                <span>Child-restricted (s.9: verifiable parental consent; no tracking or targeted advertising)</span>
              </label>
            </div>
          </>
        )}

        {tab === 'items' && (
          <>
            <p className="text-sm text-secondary">
              s.5(1)(i) requires the notice to itemise the personal data collected, not merely to name categories.
              <b> Necessary</b> means the purpose cannot be delivered without that item; it is the flag the
              minimisation check reads, so marking everything necessary defeats the point of having it.
            </p>
            {form.data_items.length === 0 && (
              <div className="alert alert-info mb">
                Nothing is itemised. The purpose still declares its categories, but the notice generated from it will
                list categories rather than data items.
              </div>
            )}
            {form.data_items.map((item, i) => (
              <div className="form-row data-item-row" key={i}>
                <div className="form-group">
                  <label>Data category</label>
                  <select className="select" value={item.data_category_id} onChange={(e) => {
                    const next = [...form.data_items]
                    next[i] = { ...item, data_category_id: Number(e.target.value) }
                    setForm({ ...form, data_items: next })
                  }}>
                    {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label>Item description shown to the principal</label>
                  <input className="input" value={item.description} onChange={(e) => {
                    const next = [...form.data_items]
                    next[i] = { ...item, description: e.target.value }
                    setForm({ ...form, data_items: next })
                  }} placeholder="e.g. mobile number, used only to send the OTP" />
                </div>
                <div className="form-group" style={{ maxWidth: 170 }}>
                  <label>Necessity</label>
                  <label className="flex" style={{ gap: 8, cursor: 'pointer', paddingTop: 8 }}>
                    <input type="checkbox" checked={item.necessity} onChange={(e) => {
                      const next = [...form.data_items]
                      next[i] = { ...item, necessity: e.target.checked }
                      setForm({ ...form, data_items: next })
                    }} />
                    <span className="text-sm">Necessary</span>
                  </label>
                </div>
                <div className="form-group" style={{ maxWidth: 92 }}>
                  <label>&nbsp;</label>
                  <button type="button" className="btn btn-ghost-danger btn-sm"
                    onClick={() => setForm({ ...form, data_items: form.data_items.filter((_, j) => j !== i) })}>
                    Remove
                  </button>
                </div>
              </div>
            ))}
            <button type="button" className="btn btn-sm" disabled={categories.length === 0}
              onClick={() => setForm({
                ...form,
                data_items: [...form.data_items, { data_category_id: categories[0].id, necessity: true, description: '' }],
              })}>
              <IconPlus size={13} /> Add data item
            </button>
          </>
        )}

        {tab === 'translations' && (
          <>
            <p className="text-sm text-secondary">
              {formCoverage.size} of 23 languages carry text. Everything a principal reads about this purpose — its
              name, its description and the consent sentence itself — is translated here; a language left blank falls
              back to the English above, which is a legitimate state but is <b>not</b> counted as coverage.
            </p>
            <div className="lang-editor">
              <div className="lang-editor-rail" role="tablist" aria-label="Language">
                {LANGUAGES.map((l) => {
                  const isBase = l.code === 'en'
                  const filled = isBase ? true : langFilled(form.translations[l.code])
                  return (
                    <button
                      key={l.code}
                      type="button"
                      role="tab"
                      aria-selected={editLang === l.code}
                      className={`lang-editor-item${editLang === l.code ? ' active' : ''}${filled ? ' filled' : ''}`}
                      onClick={() => setEditLang(l.code)}
                    >
                      <span className="lang-editor-native">{l.nameNative}</span>
                      <span className="lang-editor-en">{l.nameEn}{isBase ? ' · base' : ''}</span>
                      <span className={`lang-editor-dot${filled ? ' on' : ''}`} aria-hidden />
                    </button>
                  )
                })}
              </div>
              <div className="lang-editor-pane">
                {editLang === 'en' ? (
                  <div className="alert alert-info">
                    English is the base text for a purpose. It lives on the <b>Details</b> tab — name, description and
                    consent text — and is what every untranslated language falls back to.
                  </div>
                ) : (
                  <>
                    <div className="form-group">
                      <label>Name — {LANG_NAME[editLang]}</label>
                      <input className="input" value={entry.name || ''} onChange={(e) => setForm({
                        ...form, translations: { ...form.translations, [editLang]: { ...entry, name: e.target.value } },
                      })} placeholder={form.name} />
                    </div>
                    <div className="form-group">
                      <label>Description — {LANG_NAME[editLang]}</label>
                      <textarea className="textarea" rows={4} value={entry.description || ''} onChange={(e) => setForm({
                        ...form, translations: { ...form.translations, [editLang]: { ...entry, description: e.target.value } },
                      })} placeholder={form.description} />
                    </div>
                    <div className="form-group">
                      <label>Consent text — {LANG_NAME[editLang]}</label>
                      <textarea className="textarea" rows={4} value={entry.consent_text || ''} onChange={(e) => setForm({
                        ...form, translations: { ...form.translations, [editLang]: { ...entry, consent_text: e.target.value } },
                      })} placeholder={form.consent_text} />
                    </div>
                    <div className="flex" style={{ gap: 8 }}>
                      <button type="button" className="btn btn-sm" onClick={() => setForm({
                        ...form,
                        translations: {
                          ...form.translations,
                          [editLang]: { name: form.name, description: form.description, consent_text: form.consent_text },
                        },
                      })}>Copy English text here</button>
                      <button type="button" className="btn btn-ghost-danger btn-sm" disabled={!langFilled(form.translations[editLang])}
                        onClick={() => {
                          const next = { ...form.translations }
                          delete next[editLang]
                          setForm({ ...form, translations: next })
                        }}>Clear this language</button>
                    </div>
                    <p className="text-xs text-muted" style={{ marginTop: 10 }}>
                      Copying the English in verbatim marks this language covered while still showing the principal
                      English. Use it to stage a translation, not to claim one.
                    </p>
                  </>
                )}
              </div>
            </div>
          </>
        )}

        <div className="modal-footer" style={{ padding: 0, border: 'none', marginTop: 16 }}>
          <button className="btn" onClick={() => { setCreating(false); setEditing(null) }}>{t.cancel}</button>
          <button className="btn btn-primary" onClick={save}>{editing ? t.saveVersion : t.createPurpose}</button>
        </div>
      </Modal>

      <Modal open={!!versioning} title={`${t.versionModalTitle} — ${versioning?.name}`} onClose={() => setVersioning(null)}
        footer={<><button className="btn" onClick={() => setVersioning(null)}>{t.cancel}</button><button className="btn btn-primary" onClick={requestVersion}>{t.createVersion}</button></>}>
        <p className="text-sm text-secondary mb">{t.versionModalDesc}</p>
        <div className="form-group"><label>{t.reasonLabel}</label><input className="input" value={versionReason} onChange={(e) => setVersionReason(e.target.value)} placeholder={t.reasonPlaceholder} /></div>
      </Modal>

      {/*
        R1-09 tells the editor what their save actually did. A MATERIAL change
        opens a re-consent campaign and blocks the affected consents until the
        principals answer again — that consequence belongs in front of whoever
        caused it, not only on the re-consent screen.
      */}
      <Modal open={!!classification} title="How this change was classified" onClose={() => setClassification(null)}
        footer={<button className="btn btn-primary" onClick={() => setClassification(null)}>Understood</button>}>
        {classification && (
          <>
            <div className={`alert ${classification.materiality === 'MATERIAL' ? 'alert-error' : 'alert-info'} mb`}>
              <b>{classification.materiality}.</b> {classification.materiality_basis}
            </div>
            {classification.materiality === 'MATERIAL' && (
              <ul className="danger-list">
                <li>{classification.consents_flagged ?? 0} existing consent(s) were flagged and will not be relied on until refreshed.</li>
                <li>{classification.notifications_queued ?? 0} re-consent notification(s) were queued.</li>
                {classification.campaign_ref && <li>Campaign <span className="mono">{classification.campaign_ref}</span> is now open on the Re-consent screen.</li>}
              </ul>
            )}
            <p className="text-xs text-muted">Change reference <span className="mono">{classification.change_ref}</span></p>
          </>
        )}
      </Modal>

      {checklistFor && (
        <NoticeChecklistGate
          open
          versionNumber={checklistFor.versionNumber}
          entityLabel={checklistFor.label}
          onCancel={() => setChecklistFor(null)}
          onConfirm={(record) => {
            const action = checklistFor.action
            setChecklistFor(null)
            if (action === 'save') doSave(record)
            else doVersion(record)
          }}
        />
      )}
    </div>
  )
}
