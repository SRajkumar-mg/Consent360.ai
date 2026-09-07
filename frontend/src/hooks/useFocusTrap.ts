import { useEffect, useRef } from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

/**
 * WCAG 2.1 AA keyboard support for modal dialogs (2.4.3 focus order, 2.1.2 no
 * keyboard trap escape, 2.4.7 focus visible via the initial-focus move).
 *
 * While `active`, moves focus into the dialog, cycles Tab/Shift+Tab between its
 * focusable elements so focus never leaks to the page behind the overlay, and
 * restores focus to whatever had it beforehand when the dialog closes.
 * `onEscape` is optional and should only be wired up for dialogs the user may
 * dismiss without recording a decision (a details/preferences sub-view) — never
 * the primary consent banner, where dismissal must never stand in for a choice.
 */
export function useFocusTrap<T extends HTMLElement>(active: boolean, onEscape?: () => void) {
  const ref = useRef<T | null>(null)

  useEffect(() => {
    if (!active) return
    const container = ref.current
    if (!container) return
    const previouslyFocused = document.activeElement as HTMLElement | null

    const focusables = () =>
      Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((el) => el.offsetParent !== null)

    if (!container.hasAttribute('tabindex')) container.setAttribute('tabindex', '-1')
    const initial = focusables()[0] ?? container
    initial.focus()

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (!onEscape) return
        e.stopPropagation()
        onEscape()
        return
      }
      if (e.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }

    container.addEventListener('keydown', onKeyDown)
    return () => {
      container.removeEventListener('keydown', onKeyDown)
      previouslyFocused?.focus?.()
    }
  }, [active, onEscape])

  return ref
}
