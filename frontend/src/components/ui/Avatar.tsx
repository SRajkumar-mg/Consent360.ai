export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase() || '').join('') || '?'
}
export function Avatar({ name, size = 32 }: { name: string; size?: 28 | 32 | 40 | 48 }) {
  return <span className="avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.38) }}>{initials(name)}</span>
}
