import type { ReactNode } from 'react'
export type Tone = 'primary' | 'success' | 'warning' | 'danger' | 'info' | 'purple' | 'slate'
export function MetricCard({ label, value, sub, icon, tone = 'primary' }: { label: string; value: number | string; sub?: string; icon?: ReactNode; tone?: Tone }) {
  return (
    <div className="metric-card">
      <div className="metric-card-body">
        <div className="metric-label">{label}</div>
        <div className="metric-value">{value}</div>
        {sub && <div className="metric-sub">{sub}</div>}
      </div>
      {icon && <div className={`metric-icon tone-${tone}`}>{icon}</div>}
    </div>
  )
}
