import { useEffect, useMemo, useState } from 'react'
import { activitiesApi, categoriesApi, purposesApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, Modal, PageHead, Spinner, StatCard, useToast, formatDate } from '../components/ui'
import { IconCheck, IconHistory, IconPlus, IconShield, IconUsers } from '../components/icons'
import { LanguageSelector, usePersistedLang } from '../components/LanguageSelector'
import { useAuth } from '../context/AuthContext'
import { useTranslation } from '../hooks/useTranslation'
import type { DataCategory, ProcessingActivity, Purpose } from '../types'

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

  const [form, setForm] = useState({
    name: '', code: '', description: '', legal_basis: 'CONSENT', requires_consent: true,
    retention_period_days: 365, consent_text: '', data_category_ids: [] as number[], processing_activity_ids: [] as number[],
  })

  const openCreate = () => {
    setForm({ name: '', code: '', description: '', legal_basis: 'CONSENT', requires_consent: true, retention_period_days: 365, consent_text: '', data_category_ids: [], processing_activity_ids: [] })
    setCreating(true)
    setEditing(null)
  }

  const openEdit = (p: Purpose) => {
    const pv = [...p.versions].sort((a, b) => b.version_number - a.version_number)[0]
    setForm({
      name: p.name, code: p.code, description: p.description, legal_basis: p.legal_basis,
      requires_consent: p.requires_consent, retention_period_days: p.retention_period_days,
      consent_text: pv?.consent_text || '', data_category_ids: pv?.data_category_ids || [], processing_activity_ids: pv?.processing_activity_ids || [],
    })
    setEditing(p)
    setCreating(false)
  }

  const save = async () => {
    try {
      if (editing) {
        await purposesApi.update(editing.id, {
          name: form.name, description: form.description, legal_basis: form.legal_basis,
          requires_consent: form.requires_consent, retention_period_days: form.retention_period_days,
          consent_text: form.consent_text, data_category_ids: form.data_category_ids, processing_activity_ids: form.processing_activity_ids,
        })
        toast('success', `Purpose updated. Changes versioned to v${editing.current_version + 1}`)
      } else {
        await purposesApi.create(form)
        toast('success', 'Purpose created')
      }
      setEditing(null); setCreating(false)
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const doVersion = async () => {
    if (!versioning) return
    try {
      await purposesApi.addVersion(versioning.id, { reason: versionReason })
      toast('success', `New version created for ${versioning.name}`)
      setVersioning(null); setVersionReason('')
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const totalActive = useMemo(() => purposes.reduce((n, p) => n + p.versions.filter((v) => v.is_current).reduce((m, v) => m + v.data_category_ids.length, 0), 0), [purposes])

  if (loading) return <Spinner />

  return (
    <div>
      <PageHead
        title={t.pageTitle}
        subtitle={t.pageSubtitle}
        actions={
          <>
            <LanguageSelector value={lang} onChange={setLang} />
            {canManage && (
              <button className="btn-add" onClick={openCreate}>
                <IconPlus size={16} /> {t.newPurpose}
              </button>
            )}
          </>
        }
      />

      <div className="stat-grid mb">
        <StatCard label={t.activePurposes} value={purposes.filter((p) => p.is_active).length} icon={<IconShield size={20} />} tone="primary" sub={t.activePurposesSub} />
        <StatCard label={t.consentRequired} value={purposes.filter((p) => p.requires_consent).length} icon={<IconCheck size={20} />} tone="info" sub={t.consentRequiredSub} />
        <StatCard label={t.purposeVersions} value={purposes.reduce((n, p) => n + p.versions.length, 0)} icon={<IconHistory size={20} />} tone="purple" sub={t.purposeVersionsSub} />
        <StatCard label={t.purposeByCategory} value={totalActive} icon={<IconUsers size={20} />} tone="success" sub={t.purposeByCategorySub} />
      </div>

      {purposes.map((p) => {
        const current = [...p.versions].sort((a, b) => b.version_number - a.version_number)[0]
        return (
          <div className="card card-hover mb" key={p.id}>
            <div className="card-header">
              <div>
                <h3>{p.name} <span className="text-xs text-muted mono">({p.code})</span></h3>
                <div className="text-xs text-muted" style={{ marginTop: 3 }}>
                  Legal basis: <b>{p.legal_basis}</b> · Retention: <b>{p.retention_period_days} days</b> · Current version <b>v{p.current_version}</b>
                </div>
              </div>
              <div className="flex">
                <Badge status={p.requires_consent ? 'ACTIVE' : 'REQUESTED'}>{p.requires_consent ? t.consentReqBadge : t.consentNotReqBadge}</Badge>
                <Badge status={p.is_active ? 'ACTIVE' : 'EXPIRED'}>{p.is_active ? t.active : t.inactive}</Badge>
                {canManage && <button className="btn btn-sm" onClick={() => openEdit(p)}>{t.edit}</button>}
                {canManage && <button className="btn btn-sm" onClick={() => { setVersioning(p); setVersionReason('') }}>{t.newVersion}</button>}
              </div>
            </div>
            <div className="card-body">
              <p className="text-sm text-secondary">{p.description || t.noDescription}</p>
              <div className="detail-grid mt" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))' }}>
                <div className="detail-item"><span className="k">{t.dataCategories}</span>
                  <div className="flex mt-sm" style={{ flexWrap: 'wrap', gap: 6 }}>
                    {(current?.data_category_ids || []).length === 0 ? <span className="text-muted text-sm">{t.none}</span> : current?.data_category_ids.map((id) => {
                      const c = categories.find((x) => x.id === id)
                      return c ? <span key={id} className="badge b-primary">{c.name}</span> : null
                    })}
                  </div>
                </div>
                <div className="detail-item"><span className="k">{t.processingActivities}</span>
                  <div className="flex mt-sm" style={{ flexWrap: 'wrap', gap: 6 }}>
                    {(current?.processing_activity_ids || []).length === 0 ? <span className="text-muted text-sm">{t.none}</span> : current?.processing_activity_ids.map((id) => {
                      const a = activities.find((x) => x.id === id)
                      return a ? <span key={id} className="badge b-purple">{a.name}</span> : null
                    })}
                  </div>
                </div>
              </div>
              {current?.consent_text && (
                <div className="text-sm text-secondary mt" style={{ fontStyle: 'italic' }}>“{current.consent_text}”</div>
              )}
              <div className="text-xs text-muted mt">{t.versions} {[...p.versions].sort((a, b) => a.version_number - b.version_number).map((v) => (
                <span key={v.id}> v{v.version_number}{v.is_current ? ` ${t.current}` : ''}{v.effective_to ? ` → ${formatDate(v.effective_to)}` : ''} ·</span>
              ))}</div>
            </div>
          </div>
        )
      })}

      <Modal open={creating || !!editing} title={editing ? `${t.modalEditTitle} — ${editing.name}` : t.modalCreateTitle} onClose={() => { setCreating(false); setEditing(null) }} wide>
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
          <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" checked={form.requires_consent} onChange={(e) => setForm({ ...form, requires_consent: e.target.checked })} />
            {t.consentRequiredLabel}
          </label>
        </div>
        <div className="modal-footer" style={{ padding: 0, border: 'none' }}>
          <button className="btn" onClick={() => { setCreating(false); setEditing(null) }}>{t.cancel}</button>
          <button className="btn btn-primary" onClick={save}>{editing ? t.saveVersion : t.createPurpose}</button>
        </div>
      </Modal>

      <Modal open={!!versioning} title={`${t.versionModalTitle} — ${versioning?.name}`} onClose={() => setVersioning(null)}
        footer={<><button className="btn" onClick={() => setVersioning(null)}>{t.cancel}</button><button className="btn btn-primary" onClick={doVersion}>{t.createVersion}</button></>}>
        <p className="text-sm text-secondary mb">{t.versionModalDesc}</p>
        <div className="form-group"><label>{t.reasonLabel}</label><input className="input" value={versionReason} onChange={(e) => setVersionReason(e.target.value)} placeholder={t.reasonPlaceholder} /></div>
      </Modal>
    </div>
  )
}
