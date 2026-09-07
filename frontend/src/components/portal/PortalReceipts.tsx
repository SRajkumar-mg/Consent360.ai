import { useEffect, useState } from 'react'
import { Badge, formatDateTime, Spinner } from '../ui'
import { getErrorMessage } from '../../api/client'
import { IconDownload } from '../icons'
import type { PortalStrings } from '../../translations/portal'
import type { ConsentReceipt } from '../../types'
import { portalApi, saveBlobResponse } from './portalApi'

interface Props {
  t: PortalStrings
  token: string
}

type Format = 'json' | 'csv' | 'pdf'

/**
 * Consent receipts and the full-record export.
 *
 * Both produce evidence a principal may hand to the Data Protection Board, so
 * the screen is built around making that usable rather than around the
 * download button:
 *
 *  - `valid` on each receipt is the server RE-DERIVING the signature at read
 *    time, not a stored flag. It is surfaced as a plain verdict with a plain
 *    explanation of what was checked, because "signature: a3f9c2..." tells a
 *    non-specialist nothing about whether the document is trustworthy.
 *  - The payload hash and signature are shown in full and are selectable, so
 *    they can be quoted in a complaint.
 *  - The export explains, before the click, what the file contains, that
 *    downloading changes nothing, and which format keeps the signatures
 *    intact - the difference between a file a regulator can verify and one
 *    they cannot.
 */
export function PortalReceipts({ t, token }: Props) {
  const [receipts, setReceipts] = useState<ConsentReceipt[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [openRef, setOpenRef] = useState<string | null>(null)
  const [exporting, setExporting] = useState<Format | null>(null)
  const [status, setStatus] = useState('')

  useEffect(() => {
    let live = true
    setLoading(true)
    portalApi
      .receipts(token)
      .then((r) => { if (live) { setReceipts(r.data); setError('') } })
      .catch((e) => { if (live) setError(getErrorMessage(e)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [token])

  const exportRecord = async (format: Format) => {
    setExporting(format)
    setStatus('')
    setError('')
    try {
      const res = await portalApi.exportRecord(token, format)
      const name = saveBlobResponse(res, `consent-record.${format}`)
      setStatus(`${t.receipts.exportDone} ${name}`)
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setExporting(null)
    }
  }

  /** The receipt exactly as the server returned it, including the signature
   *  and hash - so a file saved here is the same document the organisation
   *  holds, not a re-rendering of it. */
  const downloadReceipt = (r: ConsentReceipt) => {
    const blob = new Blob([JSON.stringify(r, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `consent-receipt-${r.receipt_ref}.json`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    setStatus(`${t.receipts.exportDone} consent-receipt-${r.receipt_ref}.json`)
  }

  return (
    <section aria-labelledby="pp-receipts-title">
      <h1 id="pp-receipts-title" className="pp-h1">{t.receipts.title}</h1>

      <div className="pp-export card">
        <h2 className="pp-h2 pp-h2-first">{t.receipts.exportTitle}</h2>
        <p className="pp-lede">{t.receipts.exportIntro}</p>
        <p className="pp-hint">{t.receipts.exportContains}</p>
        <div className="pp-export-actions">
          <button className="btn btn-primary" onClick={() => exportRecord('json')} disabled={exporting !== null}>
            <IconDownload size={15} aria-hidden="true" />
            {exporting === 'json' ? t.receipts.exporting : t.receipts.exportJson}
          </button>
          <button className="btn" onClick={() => exportRecord('csv')} disabled={exporting !== null}>
            <IconDownload size={15} aria-hidden="true" />
            {exporting === 'csv' ? t.receipts.exporting : t.receipts.exportCsv}
          </button>
          <button className="btn" onClick={() => exportRecord('pdf')} disabled={exporting !== null}>
            <IconDownload size={15} aria-hidden="true" />
            {exporting === 'pdf' ? t.receipts.exporting : t.receipts.exportPdf}
          </button>
        </div>
        <p className="pp-hint pp-hint-note">{t.receipts.evidenceNote}</p>
      </div>

      <p className="pp-sr-status" role="status">{status}</p>
      {error && <p className="alert alert-error pp-alert" role="alert">{error}</p>}

      <h2 className="pp-h2">{t.receipts.title}</h2>
      <p className="pp-lede pp-lede-sm">{t.receipts.intro}</p>

      {loading && <div className="center-load"><Spinner /></div>}

      {!loading && receipts.length === 0 && <p className="pp-empty">{t.receipts.empty}</p>}

      {!loading && receipts.length > 0 && (
        <ul className="pp-receipt-list">
          {receipts.map((r) => {
            const open = openRef === r.receipt_ref
            const detailId = `pp-receipt-detail-${r.id}`
            return (
              <li key={r.id} className="pp-receipt">
                <div className="pp-receipt-head">
                  <div>
                    <code className="pp-code pp-receipt-ref">{r.receipt_ref}</code>
                    <p className="pp-receipt-meta">
                      {t.receipts.action}: <Badge status={r.action} /> · {t.receipts.version} {r.consent_version}
                      {' · '}{t.receipts.issuedOn} <time dateTime={r.issued_at}>{formatDateTime(r.issued_at)}</time>
                    </p>
                  </div>
                  <span className={`pp-verdict ${r.valid ? 'is-valid' : 'is-invalid'}`}>
                    {r.valid ? t.receipts.signatureValid : t.receipts.signatureInvalid}
                  </span>
                </div>
                <p className="pp-receipt-help">
                  {r.valid ? t.receipts.signatureValidHelp : t.receipts.signatureInvalidHelp}
                </p>
                <div className="pp-receipt-actions">
                  <button
                    className="btn btn-sm"
                    aria-expanded={open}
                    aria-controls={detailId}
                    onClick={() => setOpenRef(open ? null : r.receipt_ref)}
                  >
                    {open ? t.receipts.hideDetail : t.receipts.showDetail}
                  </button>
                  <button className="btn btn-sm" onClick={() => downloadReceipt(r)}>
                    <IconDownload size={14} aria-hidden="true" />
                    {t.receipts.downloadReceipt}
                  </button>
                </div>
                <div id={detailId} hidden={!open} className="pp-receipt-detail">
                  <dl className="pp-kv">
                    <div>
                      <dt>{t.receipts.payloadHash}</dt>
                      <dd><code className="pp-code pp-code-wrap">{r.payload_hash}</code></dd>
                    </div>
                    <div>
                      <dt>{t.receipts.signature}</dt>
                      <dd><code className="pp-code pp-code-wrap">{r.signature}</code></dd>
                    </div>
                  </dl>
                  <pre className="pp-payload">{JSON.stringify(r.payload, null, 2)}</pre>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
