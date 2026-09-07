import { useState } from 'react'
import { Modal } from './ui'

// R2-09 / gap A-09: a plain-language and dark-pattern review checklist that must
// be completed before any purpose or policy version — i.e. any notice version —
// is published. Client-side gate: the publish button underneath this modal is
// only reachable by ticking every item and naming a reviewer.
//
// The completed record used to be kept in this browser's localStorage only
// (see git history) because PurposeVersion/PolicyVersion had no checklist
// column. That column now exists (backend/app/models/entities.py:
// PurposeVersion.checklist / PolicyVersion.checklist), so this component no
// longer persists anything itself: NoticeChecklistGate hands the completed
// record to its caller's onConfirm, which sends it to the backend as the
// `checklist` field on the create/update/new-version request
// (schemas.ReviewChecklistIn: reviewer, completed_at, items — snake_case,
// mapped from this record's camelCase shape by the caller) so every admin,
// on any device, sees the same durable record instead of only the browser
// that completed it. See pages/Purposes.tsx and pages/Policies.tsx for the
// read side (rendering PurposeVersion.checklist / PolicyVersion.checklist).

export interface NoticeChecklistRecord {
  reviewer: string
  completedAt: string
  items: string[]
}

export const NOTICE_CHECKLIST_ITEMS: { id: string; label: string }[] = [
  {
    id: 'plain-language',
    label: 'Written in plain, everyday language a layperson can understand — no unexplained legal or technical jargon.',
  },
  {
    id: 'specific-not-bundled',
    label: 'Data items, purpose and consequence of refusal are stated specifically for this notice — not vague or bundled with unrelated purposes.',
  },
  {
    id: 'no-confirmshaming',
    label: 'No confirmshaming, guilt-tripping or coercive wording (e.g. framing refusal as "risky" or accepting as the only "smart" choice).',
  },
  {
    id: 'no-preselection',
    label: 'Nothing described in this notice is presented as already selected, or implies opting out requires more effort than opting in.',
  },
  {
    id: 'equal-prominence',
    label: 'Accept and refuse are described with equal neutrality — neither option uses more persuasive or larger/bolder language than the other.',
  },
  {
    id: 'withdrawal-stated',
    label: 'The notice states withdrawal is available and at least as easy as giving consent, with the actual withdrawal path named.',
  },
  {
    id: 'contacts-current',
    label: 'Retention period, DPO contact and grievance / Data Protection Board complaint path shown with this notice are accurate for this version.',
  },
]

interface NoticeChecklistGateProps {
  open: boolean
  entityLabel: string
  versionNumber: number
  onCancel: () => void
  // Receives the completed record so the caller can send it to the backend
  // (mapped to the snake_case ReviewChecklistIn shape) alongside the
  // create/update/new-version request it publishes with.
  onConfirm: (record: NoticeChecklistRecord) => void
}

export function NoticeChecklistGate({ open, entityLabel, versionNumber, onCancel, onConfirm }: NoticeChecklistGateProps) {
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [reviewer, setReviewer] = useState('')

  const allChecked = NOTICE_CHECKLIST_ITEMS.every((item) => checked[item.id])
  const canConfirm = allChecked && reviewer.trim().length > 0

  const reset = () => {
    setChecked({})
    setReviewer('')
  }

  return (
    <Modal
      open={open}
      title={`Plain-language & dark-pattern review — ${entityLabel}`}
      onClose={() => { reset(); onCancel() }}
      wide
      footer={
        <>
          <button className="btn" onClick={() => { reset(); onCancel() }}>Cancel</button>
          <button
            className="btn btn-primary"
            disabled={!canConfirm}
            title={canConfirm ? undefined : 'Tick every item and name a reviewer before publishing'}
            onClick={() => {
              const record: NoticeChecklistRecord = {
                reviewer: reviewer.trim(),
                completedAt: new Date().toISOString(),
                items: NOTICE_CHECKLIST_ITEMS.map((i) => i.id),
              }
              reset()
              onConfirm(record)
            }}
          >
            Confirm review &amp; publish
          </button>
        </>
      }
    >
      <p className="text-sm text-secondary mb">
        Required before this notice version reaches Data Principals (gap A-09). Every item must be checked and a
        reviewer named — this is recorded against v{versionNumber} of {entityLabel}.
      </p>
      <div className="form-group" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {NOTICE_CHECKLIST_ITEMS.map((item) => (
          <label key={item.id} className="flex" style={{ gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={!!checked[item.id]}
              onChange={(e) => setChecked({ ...checked, [item.id]: e.target.checked })}
              style={{ marginTop: 3 }}
            />
            <span className="text-sm">{item.label}</span>
          </label>
        ))}
      </div>
      <div className="form-group" style={{ marginTop: 12 }}>
        <label htmlFor="notice-checklist-reviewer">Reviewed by</label>
        <input
          id="notice-checklist-reviewer"
          className="input"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
          placeholder="Name of the reviewer completing this checklist"
        />
      </div>
    </Modal>
  )
}
