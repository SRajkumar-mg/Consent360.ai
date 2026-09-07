import type { ReactNode } from 'react'
export function FooterRow({ left, right = 'Consent360 Management Platform v1.2' }: { left?: ReactNode; right?: ReactNode }) {
  return (
    <div className="footer-row">
      <span>{left}</span>
      <span>{right}</span>
    </div>
  )
}
