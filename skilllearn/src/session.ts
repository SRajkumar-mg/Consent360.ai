// A per-visit session identifier, stable for as long as this browser tab stays open
// (sessionStorage, not localStorage) and sent as ClientContext.session_id on every
// consent decision so evidence rows can be correlated to a single browsing session.
const SESSION_KEY = 'skilllearn_session_id'

function randomId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function getSessionId(): string {
  try {
    let id = sessionStorage.getItem(SESSION_KEY)
    if (!id) {
      id = randomId()
      sessionStorage.setItem(SESSION_KEY, id)
    }
    return id
  } catch {
    return randomId()
  }
}
