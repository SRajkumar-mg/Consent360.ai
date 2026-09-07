import { useEffect, useMemo, useState } from 'react'
import { activitiesApi, categoriesApi, policiesApi, purposesApi } from '../api'
import { getErrorMessage } from '../api/client'
import { Badge, ConfirmDialog, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, useToast, formatDate } from '../components/ui'
import { NoticeChecklistGate, type NoticeChecklistRecord } from '../components/NoticeChecklist'
import { IconCheck, IconPlus, IconShield, IconTrash, IconUsers } from '../components/icons'
import { LanguageSelector, usePersistedLang } from '../components/LanguageSelector'
import { useAuth } from '../context/AuthContext'
import { useTranslation } from '../hooks/useTranslation'
import type { DataCategory, Policy, PolicyRule, PolicyRulePayload, ProcessingActivity, Purpose } from '../types'

interface RuleDraft {
  purpose_code: string
  data_category_code: string
  processing_activity_code: string
  decision: 'ALLOW' | 'DENY'
  requires_active_consent: boolean
  priority: number
}

interface PolicyForm {
  name: string
  code: string
  description: string
  default_decision: string
  rules: RuleDraft[]
}

const EMPTY_FORM: PolicyForm = { name: '', code: '', description: '', default_decision: 'REQUIRE_CONSENT', rules: [] }

function draftFromRules(rules: PolicyRule[]): RuleDraft[] {
  return rules.map((r) => ({
    purpose_code: r.purpose_code,
    data_category_code: r.data_category_code,
    processing_activity_code: r.processing_activity_code,
    decision: r.decision,
    requires_active_consent: r.requires_active_consent,
    priority: r.priority,
  }))
}

export function PoliciesPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('policy.manage')
  const [lang, setLang] = usePersistedLang()
  const t = useTranslation('policies', lang)
  const [policies, setPolicies] = useState<Policy[]>([])
  const [purposes, setPurposes] = useState<Purpose[]>([])
  const [categories, setCategories] = useState<DataCategory[]>([])
  const [activities, setActivities] = useState<ProcessingActivity[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Policy | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<PolicyForm>(EMPTY_FORM)
  const [confirmDelete, setConfirmDelete] = useState<Policy | null>(null)

  const load = async () => {
    const r = await policiesApi.list()
    setPolicies(r.data)
  }

  useEffect(() => {
    setLoading(true)
    Promise.all([policiesApi.list(), purposesApi.list(), categoriesApi.list(), activitiesApi.list()])
      .then(([p, pr, c, a]) => { setPolicies(p.data); setPurposes(pr.data); setCategories(c.data); setActivities(a.data) })
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [])

  const openCreate = () => {
    setForm(EMPTY_FORM)
    setCreating(true)
    setEditing(null)
  }

  const openEdit = (p: Policy) => {
    const current = [...p.versions].sort((a, b) => b.version_number - a.version_number)[0]
    setForm({
      name: p.name,
      code: p.code,
      description: p.description,
      default_decision: current?.default_decision || 'REQUIRE_CONSENT',
      rules: draftFromRules(current?.rules || []),
    })
    setEditing(p)
    setCreating(false)
  }

  const addRule = () => {
    const purpose = purposes[0]
    const category = categories[0]
    const activity = activities[0]
    setForm({
      ...form,
      rules: [
        ...form.rules,
        {
          purpose_code: purpose?.code || '',
          data_category_code: category?.code || '',
          processing_activity_code: activity?.code || '',
          decision: 'ALLOW',
          requires_active_consent: true,
          priority: 10,
        },
      ],
    })
  }

  const updateRule = (index: number, patch: Partial<RuleDraft>) => {
    setForm({ ...form, rules: form.rules.map((r, i) => (i === index ? { ...r, ...patch } : r)) })
  }

  const removeRule = (index: number) => {
    setForm({ ...form, rules: form.rules.filter((_, i) => i !== index) })
  }

  // R2-09 / gap A-09: every policy version is notice content in force for
  // consent decisions, so publishing one is gated behind the plain-language /
  // dark-pattern checklist rather than calling the API directly.
  const [checklistFor, setChecklistFor] = useState<null | { versionNumber: number; label: string }>(null)

  const doSave = async (checklist: NoticeChecklistRecord) => {
    const payload = {
      name: form.name.trim(),
      code: form.code.trim().toLowerCase(),
      description: form.description,
      default_decision: form.default_decision,
      rules: form.rules.filter((r) => r.purpose_code && r.data_category_code && r.processing_activity_code) as PolicyRulePayload[],
      // Maps the modal's camelCase draft shape onto the backend's
      // ReviewChecklistIn (snake_case) so the completed review travels with
      // the publish request instead of only ever living in localStorage.
      checklist: { reviewer: checklist.reviewer, completed_at: checklist.completedAt, items: checklist.items },
    }
    try {
      if (editing) {
        await policiesApi.update(editing.id, payload)
        toast('success', `Policy updated${payload.rules.length ? ` (versioned to v${editing.current_version + 1})` : ''}`)
      } else {
        await policiesApi.create(payload)
        toast('success', 'Policy created')
      }
      setCreating(false)
      setEditing(null)
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const save = () => {
    const versionNumber = editing ? editing.current_version + 1 : 1
    setChecklistFor({ versionNumber, label: form.name || form.code })
  }

  const doDelete = async () => {
    if (!confirmDelete) return
    try {
      const res = await policiesApi.remove(confirmDelete.id)
      if (res.data.retired) {
        toast('info', res.data.reason || 'Policy retired because consents reference it')
      } else {
        toast('success', `Policy "${confirmDelete.name}" deleted`)
      }
      setConfirmDelete(null)
      load()
    } catch (e) {
      toast('error', getErrorMessage(e))
    }
  }

  const totalRules = useMemo(() => policies.reduce((n, p) => n + (p.versions[0]?.rules.length || 0), 0), [policies])

  const oldestCreated = useMemo(() => (
    policies.length
      ? policies.reduce((min, p) => (new Date(p.created_at) < new Date(min) ? p.created_at : min), policies[0].created_at)
      : null
  ), [policies])

  if (loading) return <Spinner />

  return (
    <div>
      <PageHeading
        title={t.pageTitle}
        subtitle={t.pageSubtitle}
        actions={
          <>
            <LanguageSelector value={lang} onChange={setLang} />
            {canManage && (
              <button className="btn btn-primary" onClick={openCreate}>
                <IconPlus size={16} /> {t.newPolicy}
              </button>
            )}
          </>
        }
      />

      <div className="metric-grid mb">
        <MetricCard label={t.totalPolicies} value={policies.length} icon={<IconShield size={20} />} tone="primary" sub={t.totalPoliciesSub} />
        <MetricCard label={t.activePolicies} value={policies.filter((p) => p.is_active).length} icon={<IconCheck size={20} />} tone="success" sub={t.activePoliciesSub} />
        <MetricCard label={t.retiredPolicies} value={policies.filter((p) => p.status === 'RETIRED').length} icon={<IconTrash size={20} />} tone="danger" sub={t.retiredPoliciesSub} />
        <MetricCard label={t.policyRules} value={totalRules} icon={<IconUsers size={20} />} tone="purple" sub={t.policyRulesSub} />
      </div>

      {policies.length === 0 && (
        <div className="card mb">
          <EmptyState title={t.noPoliciesTitle} message={t.noPoliciesMsg} />
          {canManage && (
            <div style={{ textAlign: 'center', paddingBottom: 28 }}>
              <button className="btn btn-primary" onClick={openCreate}>Create your first policy</button>
            </div>
          )}
        </div>
      )}

      {policies.map((p) => {
        const current = [...p.versions].sort((a, b) => b.version_number - a.version_number)[0]
        const rules = current?.rules || []
        return (
          <div className="card card-hover mb" key={p.id}>
            <div className="card-header">
              <div>
                <h3>{p.name} <span className="text-xs text-muted mono">({p.code})</span></h3>
              </div>
              <div className="flex">
                <Badge status={p.is_active ? 'ACTIVE' : 'EXPIRED'}>{p.is_active ? t.active : t.inactive}</Badge>
                {p.status === 'RETIRED' && <Badge status="DENIED">{t.retired}</Badge>}
                {canManage && <button className="btn btn-sm" onClick={() => openEdit(p)}>{t.edit}</button>}
                {canManage && (
                  <button className="btn btn-sm btn-danger" onClick={() => setConfirmDelete(p)}>
                    <IconTrash size={14} />
                  </button>
                )}
              </div>
            </div>
            <div className="card-body">
              <div className="meta-row">
                <span className="meta-item">{t.defaultDecision} <b>{current?.default_decision}</b></span>
                <span className="meta-item">{t.currentVersion}: <b>v{p.current_version}</b></span>
                <span className="meta-item"><b>{rules.length}</b> {t.rules}{rules.length === 1 ? '' : 's'}</span>
                {current?.checklist ? (
                  <span className="meta-item" title={`Reviewed by ${current.checklist.reviewer} on ${formatDate(current.checklist.completed_at)}`}>
                    Plain-language review: <b>recorded</b>
                  </span>
                ) : (
                  <span className="meta-item text-muted">Plain-language review: not recorded</span>
                )}
              </div>
              <p className="text-sm text-secondary">{p.description || t.noDescription}</p>
              {rules.length > 0 ? (
                <div className="table-wrap mt">
                  <table className="table">
                    <thead>
                      <tr><th>{t.tablePurpose}</th><th>{t.tableDataCategory}</th><th>{t.tableActivity}</th><th>{t.tableDecision}</th><th>{t.tableActiveConsent}</th><th>{t.tablePriority}</th></tr>
                    </thead>
                    <tbody>
                      {rules.map((r, i) => (
                        <tr key={i}>
                          <td>{r.purpose_name || r.purpose_code}</td>
                          <td>{r.data_category_name || r.data_category_code}</td>
                          <td>{r.processing_activity_name || r.processing_activity_code}</td>
                          <td><Badge status={r.decision === 'ALLOW' ? 'ACTIVE' : 'DENIED'}>{r.decision}</Badge></td>
                          <td>{r.requires_active_consent ? t.required : t.notRequired}</td>
                          <td>{r.priority}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-muted text-sm mt">{t.noRulesYet}</div>
              )}
              <div className="card-footnote">
                {t.versions} v{p.current_version} {t.current} · {formatDate(current?.effective_from)}
              </div>
            </div>
          </div>
        )
      })}

      <FooterRow left={`Directory created: ${oldestCreated ? formatDate(oldestCreated) : '—'}`} />

      <Modal open={creating || !!editing} title={editing ? `${t.modalEditTitle} — ${editing.name}` : t.modalCreateTitle} onClose={() => { setCreating(false); setEditing(null) }} wide>
        <div className="form-group">
          <label>{t.nameLabel}</label>
          <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="form-row">
          <div className="form-group"><label>{t.codeLabel}</label><input className="input" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} disabled={!!editing} /></div>
          <div className="form-group"><label>{t.defaultDecisionLabel}</label>
            <select className="select" value={form.default_decision} onChange={(e) => setForm({ ...form, default_decision: e.target.value })}>
              {['REQUIRE_CONSENT', 'ALLOW', 'DENY'].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>
        </div>
        <div className="form-group">
          <label>{t.descriptionLabel}</label>
          <textarea className="textarea" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </div>

        <div className="form-group">
          <label>{t.rulesLabel} <span className="text-xs text-muted">({t.rulesSub})</span></label>
          {form.rules.length === 0 && <div className="text-muted text-sm mb">{t.noRulesYet}</div>}
          {form.rules.map((r, i) => (
            <div key={i} className="policy-rule-row">
              <select className="select" value={r.purpose_code} onChange={(e) => updateRule(i, { purpose_code: e.target.value })}>
                <option value="">{t.purposePlaceholder}</option>
                {purposes.map((p) => <option key={p.id} value={p.code}>{p.name}</option>)}
              </select>
              <select className="select" value={r.data_category_code} onChange={(e) => updateRule(i, { data_category_code: e.target.value })}>
                <option value="">{t.categoryPlaceholder}</option>
                {categories.map((c) => <option key={c.id} value={c.code}>{c.name}</option>)}
              </select>
              <select className="select" value={r.processing_activity_code} onChange={(e) => updateRule(i, { processing_activity_code: e.target.value })}>
                <option value="">{t.activityPlaceholder}</option>
                {activities.map((a) => <option key={a.id} value={a.code}>{a.name}</option>)}
              </select>
              <select className="select" style={{ width: 110 }} value={r.decision} onChange={(e) => updateRule(i, { decision: e.target.value as 'ALLOW' | 'DENY' })}>
                <option value="ALLOW">ALLOW</option>
                <option value="DENY">DENY</option>
              </select>
              <input className="input" style={{ width: 80 }} type="number" min={1} value={r.priority} onChange={(e) => updateRule(i, { priority: Number(e.target.value) })} title={t.tablePriority} />
              <label className="flex" style={{ gap: 6, whiteSpace: 'nowrap', fontSize: 13 }}>
                <input type="checkbox" checked={r.requires_active_consent} onChange={(e) => updateRule(i, { requires_active_consent: e.target.checked })} />
                {t.activeConsent}
              </label>
              <button className="btn btn-sm btn-danger" onClick={() => removeRule(i)}>
                <IconTrash size={13} />
              </button>
            </div>
          ))}
          <button className="btn btn-sm" onClick={addRule} disabled={purposes.length === 0}>
            <IconPlus size={13} /> {t.addRule}
          </button>
        </div>

        <div className="modal-footer" style={{ padding: 0, border: 'none' }}>
          <button className="btn" onClick={() => { setCreating(false); setEditing(null) }}>{t.cancel}</button>
          <button className="btn btn-primary" onClick={save} disabled={!form.name.trim() || !form.code.trim()}>
            {editing ? t.savePolicy : t.createPolicy}
          </button>
        </div>
      </Modal>

      <ConfirmDialog
        open={!!confirmDelete}
        title={t.deleteModalTitle}
        message={`${t.deleteModalMsg} "${confirmDelete?.name}"?`}
        confirmLabel={t.deleteBtn}
        danger
        onClose={() => setConfirmDelete(null)}
        onConfirm={doDelete}
      />

      {checklistFor && (
        <NoticeChecklistGate
          open
          versionNumber={checklistFor.versionNumber}
          entityLabel={checklistFor.label}
          onCancel={() => setChecklistFor(null)}
          onConfirm={(record) => {
            setChecklistFor(null)
            doSave(record)
          }}
        />
      )}
    </div>
  )
}