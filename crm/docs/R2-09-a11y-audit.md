# R2-09 — Accessibility & Dark-Pattern Audit Report

Status: implemented (consent-critical path). Applies to the four sites'
cookie/consent banners, the principal consent portal, and the admin UI.

## Scope
- Cookie consent banners (crm / codex / skilllearn / portal)
- Principal consent portal (`/portal/consent`) — consent overview & withdrawal
- Notice publish workflow (backend `notices.py`)

## WCAG 2.1 AA findings & fixes

| Control | WCAG SC | Finding | Fix applied |
| --- | --- | --- | --- |
| Reject-all button | 1.4.3 / 1.4.11 contrast | Reject-all was visually lighter than accept-all (dark-pattern) | Equal visual weight: `btn-primary` + `background: var(--muted), color: #fff` on all sites (R2-01) |
| Consent preference modal | 1.3.1 / 4.1.2 name+role | Modal lacked an accessible name/role | Added `role="dialog"` + `aria-modal="true"` + `aria-label` to `CookiePreferenceModal` |
| Banner modal | 4.1.2 | Already had `role="dialog"` + `aria-label` | Verified/no change |
| Category switches | 4.1.2 | False | Uses native `input type="checkbox"` (inherently keyboard + AT-announced) — no change |
| Language selector | 2.1.1 keyboard | Native `<select>` | No change (keyboard operable) |
| Close button | 4.1.2 name | Icon button | Added `aria-label` close label |

## Keyboard-only flow verified
Banner → language select → accept / reject / more-options → modal category
switches → save — all reachable and activatable via keyboard (native form
controls).

## Plain-language / dark-pattern checklist (enforced at publish)
The notice publish workflow (`backend/app/api/routes/notices.py`) records
`checklist_passed`, `checklist_reviewer`, `checklist_at` when a notice moves to
PUBLISHED. Rubric (yes/no, versioned):

1. Is decline/reject given equal visual weight to accept-all? (yes)
2. Is the language free of double negatives? (yes)
3. Is there any time-pressure or scarcity cue? (no — absent)
4. Is the purpose/consent text in plain language without legal-only jargon? (yes)
5. Is a persistent "manage consent" path available to the principal? (yes —
   portal + banner)

## Remaining / deferred
- Automated axe-core CI assertions (tooling not present in repo; documented).
- Focus trapping on modal (native tab order reaches all controls; a full trap
  loop is a follow-up).
