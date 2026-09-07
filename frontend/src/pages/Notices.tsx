/**
 * R2-08: the admin screen for the notice module (`/notices`).
 *
 * A notice is the s.5 itemised notice a Data Principal is shown before their
 * consent is taken, and a published NoticeVersion is evidentiary: consents and
 * evidence rows pin `notice_version_id`, and the version carries a content
 * hash over its own text. Two consequences shape this screen:
 *
 *  1. Editing never rewrites what is live. The API spawns a *draft* on top of
 *     the published version and leaves it unpublished until POST /publish. The
 *     screen says so in as many words, because "Save" that does not change what
 *     principals see is otherwise indistinguishable from a bug.
 *  2. Language coverage is a first-class figure, not a detail. DPDP s.5(3)
 *     gives the principal the right to the notice in any language in the Eighth
 *     Schedule, so "3 of 23 languages" is a compliance fact this page states
 *     plainly rather than something an operator has to count by clicking.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { categoriesApi, noticesApi, purposesApi, type NoticePayload } from '../api'
import { getErrorMessage } from '../api/client'
import {
  Badge, EmptyState, FooterRow, MetricCard, Modal, PageHeading, Spinner, formatDate, formatDateTime, useToast,
} from '../components/ui'
import { RefusalNotice, ReadOnlyBanner, Tabs } from '../components/GuardedAction'
import { NoticeChecklistGate, type NoticeChecklistRecord } from '../components/NoticeChecklist'
import { IconAlert, IconCheck, IconGlobe, IconInbox, IconPlus, IconPolicy } from '../components/icons'
import { useAuth } from '../context/AuthContext'
import { LANGUAGES } from '../languages'
import type { DataCategory, DataItem, Notice, NoticeLangContent, NoticeVersion, Purpose } from '../types'

type EditorTab = 'content' | 'translations' | 'items' | 'retention'

const LANG_NAME: Record<string, string> = Object.fromEntries(
  LANGUAGES.map((l) => [l.code, l.nameEn]),
)

function latestVersion(n: Notice): NoticeVersion | undefined {
  return [...n.versions].sort((a, b) => b.version_number - a.version_number)[0]
}

function liveVersion(n: Notice): NoticeVersion | undefined {
  return n.versions.find((v) => v.is_current)
}

/** A language counts as covered when it actually carries text, not merely a key. */
function hasContent(c?: NoticeLangContent): boolean {
  return Boolean(c && (c.title?.trim() || c.body?.trim()))
}

function coverage(v?: NoticeVersion): { covered: string[]; missing: string[] } {
  const covered = new Set<string>()
  if (v) {
    if (v.title?.trim() || v.body?.trim()) covered.add(v.language_default || 'en')
    for (const [code, content] of Object.entries(v.translations || {})) {
      if (hasContent(content)) covered.add(code)
    }
  }
  return {
    covered: LANGUAGES.filter((l) => covered.has(l.code)).map((l) => l.code),
    missing: LANGUAGES.filter((l) => !covered.has(l.code)).map((l) => l.code),
  }
}

interface EditorState {
  notice: Notice | null
  purpose_id: number
  language_default: string
  title: string
  body: string
  translations: Record<string, NoticeLangContent>
  data_items: DataItem[]
  services_enabled: string
  retention_period_days: number | null
  retention_note: string
  child_restricted: boolean
}

const BLANK: EditorState = {
  notice: null,
  purpose_id: 0,
  language_default: 'en',
  title: '',
  body: '',
  translations: {},
  data_items: [],
  services_enabled: '',
  retention_period_days: null,
  retention_note: '',
  child_restricted: false,
}

export function NoticesPage() {
  const toast = useToast()
  const { hasPermission } = useAuth()
  const canManage = hasPermission('purpose.manage')

  const [notices, setNotices] = useState<Notice[]>([])
  const [purposes, setPurposes] = useState<Purpose[]>([])
  const [categories, setCategories] = useState<DataCategory[]>([])
  const [loading, setLoading] = useState(true)
  const [refusal, setRefusal] = useState('')

  const [editor, setEditor] = useState<EditorState | null>(null)
  const [tab, setTab] = useState<EditorTab>('content')
  const [editLang, setEditLang] = useState('hi')
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState<Notice | null>(null)
  // A-09: publishing is gated behind the plain-language / dark-pattern
  // checklist (same component Purposes.tsx/Policies.tsx use) - opened from
  // the publish-confirmation modal below rather than calling
  // noticesApi.publish directly, since POST /notices/{id}/publish now
  // refuses without a checklist recorded against the draft.
  const [checklistGateOpen, setChecklistGateOpen] = useState(false)
  const [previewing, setPreviewing] = useState<{ notice: Notice; version: NoticeVersion } | null>(null)

  const load = useCallback(async () => {
    const [n, p, c] = await Promise.all([noticesApi.list(), purposesApi.list(), categoriesApi.list()])
    setNotices(n.data)
    setPurposes(p.data)
    setCategories(c.data)
  }, [])

  useEffect(() => {
    setLoading(true)
    load()
      .catch((e) => toast('error', getErrorMessage(e)))
      .finally(() => setLoading(false))
  }, [load])

  const catName = (id: number) => categories.find((c) => c.id === id)?.name || `Category ${id}`

  const uncovered = useMemo(
    () => purposes.filter((p) => p.is_active && !notices.some((n) => n.purpose_id === p.id)),
    [purposes, notices],
  )

  const stats = useMemo(() => {
    const published = notices.filter((n) => n.status === 'ACTIVE').length
    const drafts = notices.filter((n) => {
      const latest = latestVersion(n)
      return latest && !latest.is_current
    }).length
    const langs = new Set<string>()
    for (const n of notices) {
      for (const code of coverage(liveVersion(n) || latestVersion(n)).covered) langs.add(code)
    }
    return { published, drafts, langs: langs.size }
  }, [notices])

  const openCreate = () => {
    setRefusal('')
    setEditor({ ...BLANK, purpose_id: uncovered[0]?.id || 0 })
    setTab('content')
    setEditLang('hi')
  }

  const openEdit = (n: Notice) => {
    const v = latestVersion(n)
    setRefusal('')
    setEditor({
      notice: n,
      purpose_id: n.purpose_id,
      language_default: v?.language_default || 'en',
      title: v?.title || '',
      body: v?.body || '',
      translations: { ...(v?.translations || {}) },
      data_items: [...(v?.data_items || [])],
      services_enabled: v?.services_enabled || '',
      retention_period_days: v?.retention_period_days ?? null,
      retention_note: v?.retention_note || '',
      child_restricted: Boolean(v?.child_restricted),
    })
    setTab('content')
    setEditLang(LANGUAGES.find((l) => l.code !== (v?.language_default || 'en'))?.code || 'hi')
  }

  const save = async () => {
    if (!editor) return
    setSaving(true)
    setRefusal('')
    // Strip languages the operator opened but left blank: an empty entry would
    // otherwise look like coverage on every screen that counts languages.
    const translations: Record<string, NoticeLangContent> = {}
    for (const [code, content] of Object.entries(editor.translations)) {
      if (hasContent(content)) translations[code] = { title: content.title || '', body: content.body || '' }
    }
    const payload: NoticePayload = {
      purpose_id: editor.purpose_id,
      language_default: editor.language_default,
      title: editor.title,
      body: editor.body,
      translations,
      data_items: editor.data_items,
      services_enabled: editor.services_enabled,
      retention_period_days: editor.retention_period_days,
      retention_note: editor.retention_note,
      child_restricted: editor.child_restricted,
    }
    try {
      if (editor.notice) {
        const wasLive = Boolean(liveVersion(editor.notice))
        await noticesApi.update(editor.notice.id, payload)
        toast(
          'success',
          wasLive
            ? 'Saved as a new draft version. The live notice is unchanged until you publish.'
            : 'Draft updated. It is not live until you publish it.',
        )
      } else {
        if (!payload.purpose_id) {
          toast('warning', 'Choose the purpose this notice covers')
          setSaving(false)
          return
        }
        await noticesApi.create(payload)
        toast('success', 'Notice created as draft v1. Publish it to make it live.')
      }
      setEditor(null)
      await load()
    } catch (e) {
      setRefusal(getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  const publish = () => {
    if (!publishing) return
    setChecklistGateOpen(true)
  }

  const publishWithChecklist = async (record: NoticeChecklistRecord) => {
    if (!publishing) return
    setChecklistGateOpen(false)
    setRefusal('')
    try {
      // A-09: attach the completed checklist to the draft this publish
      // targets before publishing it - a checklist-only PUT (no other
      // content field given) never touches title/body/etc., see
      // update_notice()'s other_content_given handling.
      await noticesApi.update(publishing.id, { checklist: toChecklistPayload(record) })
      await noticesApi.publish(publishing.id)
      toast('success', `Notice for ${publishing.purpose_code} published and hashed.`)
      setPublishing(null)
      await load()
    } catch (e) {
      // Publish refuses on an already-published latest version, on missing
      // title/body, and (A-09) on a missing/incomplete checklist. All three
      // name the reason; show it rather than a toast that disappears before
      // it can be acted on.
      setRefusal(getErrorMessage(e))
      setPublishing(null)
    }
  }

  // Maps the modal's camelCase draft shape onto the backend's
  // ReviewChecklistIn (snake_case) - see components/NoticeChecklist.tsx.
  const toChecklistPayload = (record: NoticeChecklistRecord) => ({
    reviewer: record.reviewer,
    completed_at: record.completedAt,
    items: record.items,
  })

  const retire = async (n: Notice) => {
    setRefusal('')
    try {
      const r = await noticesApi.remove(n.id)
      toast('success', r.data.retired ? 'Notice retired (it is referenced by consent evidence).' : 'Notice deleted.')
      await load()
    } catch (e) {
      setRefusal(getErrorMessage(e))
    }
  }

  if (loading) return <Spinner />

  const editorLangEntry = editor?.translations[editLang] || { title: '', body: '' }
  const editorCoverage = editor
    ? coverage({
      language_default: editor.language_default,
      title: editor.title,
      body: editor.body,
      translations: editor.translations,
    } as NoticeVersion)
    : { covered: [], missing: [] }

  return (
    <div>
      <PageHeading
        title="Notices"
        subtitle="The s.5 itemised notice shown before consent is taken — one per purpose, versioned, hashed and published in up to 23 languages"
        actions={canManage && (
          <button className="btn btn-primary" onClick={openCreate} disabled={uncovered.length === 0}
            title={uncovered.length === 0 ? 'Every active purpose already has a notice' : undefined}>
            <IconPlus size={14} /> New notice
          </button>
        )}
      />

      {!canManage && <ReadOnlyBanner permission="purpose.manage" what="Drafting, publishing and retiring a notice" />}

      <RefusalNotice title="The notice API refused this change" detail={refusal} onDismiss={() => setRefusal('')} />

      <div className="metric-grid mb">
        <MetricCard label="Notices" value={notices.length} tone="primary" icon={<IconPolicy size={20} />}
          sub={`${uncovered.length} active purpose${uncovered.length === 1 ? '' : 's'} without one`} />
        <MetricCard label="Published" value={stats.published} tone="success" icon={<IconCheck size={20} />}
          sub="live and pinned by consent evidence" />
        <MetricCard label="Unpublished drafts" value={stats.drafts} tone="warning" icon={<IconAlert size={20} />}
          sub="edited but not yet shown to anyone" />
        <MetricCard label="Languages in use" value={`${stats.langs} of 23`} tone="info" icon={<IconGlobe size={20} />}
          sub="s.5(3): the principal may ask for any of the 23" />
      </div>

      {uncovered.length > 0 && (
        <div className="alert alert-info mb">
          <b>{uncovered.length} active purpose{uncovered.length === 1 ? '' : 's'} with no notice:</b>{' '}
          {uncovered.map((p) => p.code).join(', ')}. Consent taken for a purpose with no published notice has
          no s.5 notice to point at.
        </div>
      )}

      {notices.length === 0 && (
        <EmptyState icon={<IconInbox size={30} />} title="No notices yet"
          message="Create a notice for each active purpose. It goes live only when you publish it." />
      )}

      {notices.map((n) => {
        const live = liveVersion(n)
        const latest = latestVersion(n)
        const draftPending = latest && !latest.is_current
        const cov = coverage(live || latest)
        return (
          <div className="card card-hover mb" key={n.id}>
            <div className="card-header">
              <div>
                <h3>{n.purpose_name} <span className="text-xs text-muted mono">({n.purpose_code})</span></h3>
              </div>
              <div className="flex" style={{ gap: 8, flexWrap: 'wrap' }}>
                <Badge status={n.status === 'ACTIVE' ? 'ACTIVE' : n.status === 'RETIRED' ? 'EXPIRED' : 'PENDING'}>
                  {n.status === 'ACTIVE' ? `Live v${n.current_version}` : n.status === 'RETIRED' ? 'Retired' : 'Draft only'}
                </Badge>
                {draftPending && <Badge status="REQUESTED">Draft v{latest?.version_number} pending</Badge>}
                <button className="btn btn-sm" onClick={() => live && setPreviewing({ notice: n, version: live })}
                  disabled={!live} title={live ? 'Show the published content and its hash' : 'Nothing published yet'}>
                  Preview live
                </button>
                {canManage && <button className="btn btn-sm" onClick={() => openEdit(n)}>Edit</button>}
                {canManage && draftPending && (
                  <button className="btn btn-primary btn-sm" onClick={() => setPublishing(n)}>Publish v{latest?.version_number}</button>
                )}
                {canManage && n.is_active && (
                  <button className="btn btn-ghost-danger btn-sm" onClick={() => retire(n)}>Retire</button>
                )}
              </div>
            </div>
            <div className="card-body">
              <div className="meta-row">
                <span className="meta-item">Default language: <b>{LANG_NAME[live?.language_default || latest?.language_default || 'en']}</b></span>
                <span className="meta-item">Versions: <b>{n.versions.length}</b></span>
                <span className="meta-item">
                  Languages: <b>{cov.covered.length} of 23</b>
                </span>
                {live?.child_restricted && <span className="meta-item text-danger"><b>Child-restricted</b></span>}
                {live?.published_at && <span className="meta-item">Published {formatDateTime(live.published_at)} by {live.published_by}</span>}
                {live && (
                  live.checklist ? (
                    <span className="meta-item" title={`Reviewed by ${live.checklist.reviewer} on ${formatDate(live.checklist.completed_at)}`}>
                      Plain-language review: <b>recorded</b>
                    </span>
                  ) : (
                    // A-09: only a version published before this gate existed
                    // (grandfathered) can be live with no checklist - every
                    // publish from here on requires one.
                    <span className="meta-item text-muted">Plain-language review: not recorded (published before this check existed)</span>
                  )
                )}
              </div>

              <div className="text-sm text-secondary" style={{ marginTop: 6 }}>
                {(live || latest)?.title || <span className="text-muted">No title yet</span>}
              </div>

              <div className="chip-group-title">Language coverage</div>
              <div className="chip-group">
                {LANGUAGES.map((l) => (
                  <span key={l.code} className={`chip lang-chip${cov.covered.includes(l.code) ? ' lang-chip-on' : ''}`}
                    title={cov.covered.includes(l.code) ? `${l.nameEn}: translated` : `${l.nameEn}: not translated — falls back to ${LANG_NAME[live?.language_default || 'en']}`}>
                    {l.code}
                  </span>
                ))}
              </div>

              {(live?.data_items || []).length > 0 && (
                <>
                  <div className="chip-group-title">Itemised data (s.5(1)(i))</div>
                  <div className="chip-group">
                    {(live?.data_items || []).map((d, i) => (
                      <span key={`${d.data_category_id}-${i}`} className="chip" title={d.description}>
                        {catName(d.data_category_id)}{d.necessity ? ' · necessary' : ' · optional'}
                      </span>
                    ))}
                  </div>
                </>
              )}

              {live?.content_hash && (
                <div className="card-footnote">
                  Content hash <span className="mono text-xs">{live.content_hash.slice(0, 24)}…</span> — what a consent
                  row pins when it records that this exact notice was shown.
                </div>
              )}
            </div>
          </div>
        )
      })}

      <FooterRow left={`${notices.length} notice${notices.length === 1 ? '' : 's'} · ${stats.drafts} awaiting publication`} />

      {/* ---------------- Editor ---------------- */}
      <Modal
        open={!!editor}
        wide
        title={editor?.notice ? `Edit notice — ${editor.notice.purpose_name}` : 'New notice'}
        onClose={() => setEditor(null)}
        footer={
          <>
            <button className="btn" onClick={() => setEditor(null)} disabled={saving}>Cancel</button>
            <button className="btn btn-primary" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : editor?.notice ? 'Save draft' : 'Create draft'}
            </button>
          </>
        }
      >
        {editor && (
          <>
            <div className="alert alert-info mb">
              <b>Saving does not publish.</b>{' '}
              {editor.notice && liveVersion(editor.notice)
                ? `Editing the live v${liveVersion(editor.notice)?.version_number} creates a new draft on top of it. Principals keep seeing the live version until you publish.`
                : 'This stays a draft until you publish it. Nothing is shown to any principal in the meantime.'}
            </div>

            <Tabs<EditorTab>
              active={tab}
              onChange={setTab}
              tabs={[
                { id: 'content', label: 'Content' },
                { id: 'translations', label: 'Translations', count: editorCoverage.covered.length },
                { id: 'items', label: 'Itemised data', count: editor.data_items.length },
                { id: 'retention', label: 'Retention & flags' },
              ]}
            />

            {tab === 'content' && (
              <>
                <div className="form-row">
                  <div className="form-group">
                    <label>Purpose</label>
                    <select className="select" value={editor.purpose_id} disabled={!!editor.notice}
                      onChange={(e) => setEditor({ ...editor, purpose_id: Number(e.target.value) })}>
                      {editor.notice
                        ? <option value={editor.purpose_id}>{editor.notice.purpose_name}</option>
                        : <>
                          <option value={0}>Select…</option>
                          {uncovered.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.code})</option>)}
                        </>}
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Default language</label>
                    <select className="select" value={editor.language_default}
                      onChange={(e) => setEditor({ ...editor, language_default: e.target.value })}>
                      {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.nameEn} — {l.nameNative}</option>)}
                    </select>
                    <div className="text-xs text-muted" style={{ marginTop: 4 }}>
                      Served whenever the requested language has no translation.
                    </div>
                  </div>
                </div>
                <div className="form-group">
                  <label>Title ({LANG_NAME[editor.language_default]})</label>
                  <input className="input" value={editor.title} onChange={(e) => setEditor({ ...editor, title: e.target.value })} />
                </div>
                <div className="form-group">
                  <label>Body ({LANG_NAME[editor.language_default]})</label>
                  <textarea className="textarea" rows={8} value={editor.body}
                    onChange={(e) => setEditor({ ...editor, body: e.target.value })} />
                </div>
              </>
            )}

            {tab === 'translations' && (
              <>
                <p className="text-sm text-secondary">
                  {editorCoverage.covered.length} of 23 languages carry content. A language left blank is not an error —
                  a request for it falls back to {LANG_NAME[editor.language_default]} — but it is not coverage either,
                  and this screen counts it as missing.
                </p>
                <div className="lang-editor">
                  <div className="lang-editor-rail" role="tablist" aria-label="Language">
                    {LANGUAGES.map((l) => {
                      const isDefault = l.code === editor.language_default
                      const filled = isDefault
                        ? Boolean(editor.title.trim() || editor.body.trim())
                        : hasContent(editor.translations[l.code])
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
                          <span className="lang-editor-en">{l.nameEn}{isDefault ? ' · default' : ''}</span>
                          <span className={`lang-editor-dot${filled ? ' on' : ''}`} aria-hidden />
                        </button>
                      )
                    })}
                  </div>
                  <div className="lang-editor-pane">
                    {editLang === editor.language_default ? (
                      <div className="alert alert-info">
                        {LANG_NAME[editLang]} is this notice&rsquo;s default language. Its text lives on the
                        <b> Content</b> tab, not here.
                      </div>
                    ) : (
                      <>
                        <div className="form-group">
                          <label>Title — {LANG_NAME[editLang]}</label>
                          <input
                            className="input"
                            value={editorLangEntry.title || ''}
                            onChange={(e) => setEditor({
                              ...editor,
                              translations: { ...editor.translations, [editLang]: { ...editorLangEntry, title: e.target.value } },
                            })}
                          />
                        </div>
                        <div className="form-group">
                          <label>Body — {LANG_NAME[editLang]}</label>
                          <textarea
                            className="textarea"
                            rows={10}
                            value={editorLangEntry.body || ''}
                            onChange={(e) => setEditor({
                              ...editor,
                              translations: { ...editor.translations, [editLang]: { ...editorLangEntry, body: e.target.value } },
                            })}
                          />
                        </div>
                        <div className="flex" style={{ gap: 8 }}>
                          <button
                            type="button" className="btn btn-sm"
                            onClick={() => setEditor({
                              ...editor,
                              translations: { ...editor.translations, [editLang]: { title: editor.title, body: editor.body } },
                            })}
                          >
                            Copy {LANG_NAME[editor.language_default]} text here
                          </button>
                          <button
                            type="button" className="btn btn-ghost-danger btn-sm"
                            disabled={!hasContent(editor.translations[editLang])}
                            onClick={() => {
                              const next = { ...editor.translations }
                              delete next[editLang]
                              setEditor({ ...editor, translations: next })
                            }}
                          >
                            Clear this language
                          </button>
                        </div>
                        <p className="text-xs text-muted" style={{ marginTop: 10 }}>
                          Copying the default text in verbatim marks this language as covered while leaving the
                          principal reading {LANG_NAME[editor.language_default]}. Use it to stage a translation,
                          not to claim one.
                        </p>
                      </>
                    )}
                  </div>
                </div>
              </>
            )}

            {tab === 'items' && (
              <>
                <p className="text-sm text-secondary">
                  s.5(1)(i): the notice must itemise the personal data collected, not merely name categories.
                  <b> Necessary</b> means the purpose cannot be delivered without it — that flag is what the
                  minimisation check reads.
                </p>
                {editor.data_items.length === 0 && (
                  <EmptyState message="No itemised data. On create, the purpose version's own items are copied in if you leave this empty." />
                )}
                {editor.data_items.map((item, i) => (
                  <div className="form-row data-item-row" key={i}>
                    <div className="form-group">
                      <label>Data category</label>
                      <select className="select" value={item.data_category_id}
                        onChange={(e) => {
                          const next = [...editor.data_items]
                          next[i] = { ...item, data_category_id: Number(e.target.value) }
                          setEditor({ ...editor, data_items: next })
                        }}>
                        {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </select>
                    </div>
                    <div className="form-group">
                      <label>Description shown to the principal</label>
                      <input className="input" value={item.description}
                        onChange={(e) => {
                          const next = [...editor.data_items]
                          next[i] = { ...item, description: e.target.value }
                          setEditor({ ...editor, data_items: next })
                        }} />
                    </div>
                    <div className="form-group" style={{ maxWidth: 180 }}>
                      <label>Necessity</label>
                      <label className="flex" style={{ gap: 8, cursor: 'pointer', paddingTop: 8 }}>
                        <input type="checkbox" checked={item.necessity}
                          onChange={(e) => {
                            const next = [...editor.data_items]
                            next[i] = { ...item, necessity: e.target.checked }
                            setEditor({ ...editor, data_items: next })
                          }} />
                        <span className="text-sm">Necessary</span>
                      </label>
                    </div>
                    <div className="form-group" style={{ maxWidth: 90 }}>
                      <label>&nbsp;</label>
                      <button type="button" className="btn btn-ghost-danger btn-sm"
                        onClick={() => setEditor({ ...editor, data_items: editor.data_items.filter((_, j) => j !== i) })}>
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                <button type="button" className="btn btn-sm" disabled={categories.length === 0}
                  onClick={() => setEditor({
                    ...editor,
                    data_items: [...editor.data_items, { data_category_id: categories[0].id, necessity: true, description: '' }],
                  })}>
                  <IconPlus size={13} /> Add data item
                </button>
              </>
            )}

            {tab === 'retention' && (
              <>
                <div className="form-group">
                  <label>Services enabled by this processing</label>
                  <textarea className="textarea" rows={3} value={editor.services_enabled}
                    onChange={(e) => setEditor({ ...editor, services_enabled: e.target.value })}
                    placeholder="What the principal gets in return for this data" />
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label>Retention period (days)</label>
                    <input className="input" type="number" min={0}
                      value={editor.retention_period_days ?? ''}
                      onChange={(e) => setEditor({ ...editor, retention_period_days: e.target.value === '' ? null : Number(e.target.value) })}
                      placeholder="Inherits the purpose's own period" />
                  </div>
                  <div className="form-group">
                    <label>Retention note</label>
                    <input className="input" value={editor.retention_note}
                      onChange={(e) => setEditor({ ...editor, retention_note: e.target.value })} />
                  </div>
                </div>
                <div className="form-group">
                  <label className="flex" style={{ gap: 8, cursor: 'pointer' }}>
                    <input type="checkbox" checked={editor.child_restricted}
                      onChange={(e) => setEditor({ ...editor, child_restricted: e.target.checked })} />
                    <span>Child-restricted (s.9: verifiable parental consent required, no tracking or targeted ads)</span>
                  </label>
                </div>
              </>
            )}
          </>
        )}
      </Modal>

      {/* ---------------- Publish confirmation ---------------- */}
      <Modal
        open={!!publishing && !checklistGateOpen}
        title="Publish this notice"
        onClose={() => setPublishing(null)}
        footer={
          <>
            <button className="btn" onClick={() => setPublishing(null)}>Cancel</button>
            <button className="btn btn-primary" onClick={publish}>Publish</button>
          </>
        }
      >
        {publishing && (() => {
          const latest = latestVersion(publishing)
          const cov = coverage(latest)
          const previous = liveVersion(publishing)
          return (
            <>
              <p>
                Publishing makes <b>v{latest?.version_number}</b> of the notice for{' '}
                <b>{publishing.purpose_name}</b> the version every principal sees, snapshots the tenant&rsquo;s DPO
                contact and rights links into it, and hashes its content so a consent can pin it.
              </p>
              <ul className="danger-list">
                {previous && <li>v{previous.version_number} stops being current and is stamped with an end date. It stays readable as evidence.</li>}
                <li>{cov.covered.length} of 23 languages carry content{cov.missing.length ? `; ${cov.missing.length} will fall back to ${LANG_NAME[latest?.language_default || 'en']}` : ''}.</li>
                <li>Consents already granted against v{previous?.version_number ?? '—'} keep pointing at that version.</li>
              </ul>
              {cov.missing.length > 0 && (
                <div className="alert alert-info">
                  Untranslated: {cov.missing.map((c) => LANG_NAME[c]).join(', ')}.
                </div>
              )}
            </>
          )
        })()}
      </Modal>

      {/* ---------------- A-09 plain-language & dark-pattern review ---------------- */}
      {publishing && (() => {
        const latest = latestVersion(publishing)
        return (
          <NoticeChecklistGate
            open={checklistGateOpen}
            versionNumber={latest?.version_number ?? 1}
            entityLabel={publishing.purpose_name}
            onCancel={() => { setChecklistGateOpen(false); setPublishing(null) }}
            onConfirm={publishWithChecklist}
          />
        )
      })()}

      {/* ---------------- Live preview ---------------- */}
      <Modal open={!!previewing} wide title={previewing ? `Live notice — ${previewing.notice.purpose_name}` : ''}
        onClose={() => setPreviewing(null)}>
        {previewing && (
          <>
            <dl className="detail-grid mb">
              <div className="detail-item"><dt>Version</dt><dd>v{previewing.version.version_number}</dd></div>
              <div className="detail-item"><dt>Default language</dt><dd>{LANG_NAME[previewing.version.language_default]}</dd></div>
              <div className="detail-item"><dt>Effective from</dt><dd>{formatDateTime(previewing.version.effective_from)}</dd></div>
              <div className="detail-item"><dt>Content hash</dt><dd className="mono text-xs">{previewing.version.content_hash || '—'}</dd></div>
            </dl>
            <h4 className="kpi-section-title">{previewing.version.title}</h4>
            <p className="text-sm text-secondary" style={{ whiteSpace: 'pre-wrap' }}>{previewing.version.body}</p>
            {Object.keys(previewing.version.translations || {}).length > 0 && (
              <>
                <h4 className="kpi-section-title">Translations</h4>
                {Object.entries(previewing.version.translations).filter(([, c]) => hasContent(c)).map(([code, c]) => (
                  <div key={code} className="quote">
                    <div className="text-xs text-muted mono">{LANG_NAME[code] || code}</div>
                    <div style={{ fontWeight: 600 }}>{c.title}</div>
                    <div className="text-sm" style={{ whiteSpace: 'pre-wrap' }}>{c.body}</div>
                  </div>
                ))}
              </>
            )}
            <h4 className="kpi-section-title">Snapshotted contact and links</h4>
            <pre className="code-block" style={{ maxHeight: 200, overflow: 'auto' }}>
              {JSON.stringify({ contact: previewing.version.contact_snapshot, links: previewing.version.links }, null, 2)}
            </pre>
          </>
        )}
      </Modal>
    </div>
  )
}
