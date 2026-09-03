export function maskPhone(phone: string | null | undefined): string {
  if (!phone) return '—'
  const digits = phone.replace(/\D/g, '')
  if (digits.length < 3) return '****'
  const head = digits.slice(0, 2)
  const tail = digits.slice(-1)
  return head + '*'.repeat(digits.length - 3) + tail
}

export function maskEmail(email: string | null | undefined): string {
  if (!email) return '—'
  const at = email.lastIndexOf('@')
  if (at <= 0) return '***'
  const local = email.slice(0, at)
  const domain = email.slice(at)
  if (local.length <= 2) return local[0] + '***' + domain
  return local.slice(0, 2) + '***' + local.slice(-1) + domain
}