# Personal Data Breach Response Runbook (R3-08)

**Status: workflow, clocks, notices and reports are enforced in code; the two regulator *transports*
are not.** Every deadline below is computed and tracked by `app/services/breach.py` and reportable
through `/breaches/*`. Delivery to a data principal is real (it goes through the platform's own
notification service). Delivery to the Data Protection Board and to CERT-In is **not** — neither
regulator exposes an ingestion API, so this platform generates the filing, hashes it, tracks its
deadline, and then either emails it to an address the tenant has configured or holds it for an operator
to file out of band and record the reference. Each section says which of the two it is.

Companion documents: [`RETENTION_SCHEDULE.md`](RETENTION_SCHEDULE.md) (the breach register is a section
of the regulator evidence pack), [`AUDIT_RECORD_TEMPLATE.md`](AUDIT_RECORD_TEMPLATE.md),
[`SECURITY_SCAN_FINDINGS.md`](SECURITY_SCAN_FINDINGS.md), [`BACKUP_POLICY.md`](BACKUP_POLICY.md)
(a ransomware or prolonged-outage event is *both* a restore problem and a breach — see §1).

---

## 1. What counts as a personal data breach

DPDP Act **s.2(u)**: any unauthorised processing of personal data, or accidental disclosure,
acquisition, sharing, use, alteration, destruction **or loss of access** to personal data, that
compromises its confidentiality, integrity or availability.

The last clause is the one teams miss. **Loss of access is a breach.** Ransomware, a destructive
deletion, a prolonged outage that denies the fiduciary access to personal data it holds — all of these
go in this register, notified on the same clocks, even though nothing leaked. If the incident channel
is arguing about whether "data left the building", the argument is already off-point.

Register it even when you are not yet sure. Registering costs nothing; `aware_at` stays NULL until you
decide, and while it is NULL **no clock is running** (§3). What you must not do is delay registering in
order to delay a clock — the register keeps `detected_at` separately, and the gap between detection and
awareness is itself reported (K-39's triage lag), precisely so it cannot be used that way.

---

## 2. The obligations, with citations

| # | Obligation | Clock starts | Window | Provision |
|---|---|---|---|---|
| 1 | Report the incident to **CERT-In** | on noticing | **6 hours** | CERT-In Directions No. 20(3)/2022-CERT-In, 28 Apr 2022, under s.70B(6) IT Act (Annexure I lists "data breach" and "data leak") |
| 2 | Intimate **each affected Data Principal** | on becoming aware | **without delay** | DPDP Rules 2025, R.7(1)(a)–(e) |
| 3 | Intimate the **Board** — description of the breach | on becoming aware | **without delay** | R.7(2)(a) |
| 4 | File the **detailed report** with the Board | on becoming aware | **72 hours**, or a longer period the Board allows on a written request | R.7(2)(b)(i)–(vi) |

Underlying duty: DPDP Act **s.8(6)** — "in the event of a personal data breach, the Data Fiduciary
shall give the Board and each affected Data Principal, intimation of such breach in such form and
manner as may be prescribed." Penalty head: up to **₹200 crore** (Schedule to the Act).

Rule numbers are the notified Gazette text (DPDP Rules, 2025, G.S.R. 846(E), 13 Nov 2025). The study
PDF and many law-firm summaries number the rules differently — see
`DPDP_COMPLIANCE_GAP_ANALYSIS.md` §1.2 before citing a rule number from any secondary source.

### "Without delay" is not a number

R.7(1) and R.7(2)(a) state a standard, not a period. This platform therefore records **no statutory
deadline** for those two obligations (`deadline_at` is NULL, `deadline_kind` is
`WITHOUT_DELAY_NO_FIXED_PERIOD`) and applies a separate, clearly-labelled **internal operational
target** instead — 24 hours by default, per tenant via
`Organization.settings["breach_without_delay_target_hours"]`. Every clock reports which of the two it
measured against (`measured_against`: `STATUTORY_DEADLINE` or `INTERNAL_TARGET`). Do not quote the
internal target to a regulator as if it were the law, and do not treat hitting it as compliance:
"without delay" means as soon as you reasonably can, which on most incidents is far inside 24 hours.

---

## 3. `detected_at` vs `aware_at` — read this before touching the register

These are two different columns and the difference is legally load-bearing.

- **`detected_at`** — when the incident was first *detected*: a SIEM alert fired, a customer
  complained, a researcher emailed.
- **`aware_at`** — when the fiduciary *became aware* that this is a personal data breach. This is the
  moment R.7 and the CERT-In direction both name ("on becoming aware"; "within 6 hours of noticing
  such incidents or being brought to notice about such incidents").

**Every clock in this platform starts at `aware_at`.** A 02:00 alert triaged at 09:00 gives you a
CERT-In window closing at 15:00, not 08:00. Starting the clock at detection would overstate lateness;
starting it later than genuine awareness would understate a deadline that has already passed, which is
far worse.

Guard rails already in the code, so nobody has to remember these:

- A database CHECK constraint refuses `aware_at < detected_at`.
- `POST /breaches/{ref}/aware` refuses a second call — awareness starts statutory clocks and cannot be
  quietly restated. A genuine correction is a recorded amendment, not a moved clock.
- The detection-to-awareness gap is reported as `detect_to_aware_hours` in K-39, separately from the
  notification figures, so a slow triage cannot hide inside a flattering "hours to notify".

---

## 4. Roles

| Role | Platform permission | Does |
|---|---|---|
| Incident commander (on-call) | — | Runs containment. Escalates to the DPO or an admin to register — see below. |
| **DPO** (s.10(2)(a) contact person) | `breach.view` + `breach.manage` | Decides awareness, decides notifiability, scopes the affected principals, approves and files every regulator report, signs off closure. Named on every principal notice under R.7(1)(e). |
| Admin | `breach.view` + `breach.manage` | Same platform capability as the DPO. Break-glass when the DPO is unavailable; record why. |
| Auditor, operator, viewer, consent_manager | `breach.view` | Read the register, clocks and metrics. Cannot change anything. |
| `jobhub_admin` / `codex_admin` / `skilllearn_admin` | `breach.view` | Same, restricted to their own tenant by `ORG_SCOPE_MAP`. |
| Auditor / DPO / admin | `audit.export` | Read the *content* of a filing (a principal notice quotes that principal's own data). |

`breach.manage` is deliberately **not** `policy.manage`. Filing — or failing to file — a statutory
notification with the Board is a regulator-facing act of a wholly different character from editing a
policy document, and anyone who can do the second should not thereby be able to do the first. It is a
heavy bundle (register, record awareness, scope, notify, file, extend, close) and is granted to two
roles only.

> **A re-seed is required before these permissions grant anything.** `app/core/rbac.py`'s
> `ROLE_PERMISSIONS` is copied into the `roles` table by `seed.py`, and re-synced at startup by
> `rbac.sync_roles()`. On an existing deployment, run `python seed.py` (or simply restart the app,
> which calls `sync_roles`) after deploying this change, or every `/breaches/*` route will 403 for
> every role including the DPO's.

**Operational consequence to plan for:** an on-call engineer with `operator` cannot register a breach.
That is intentional — registration is inseparable from recording awareness, which starts three
statutory clocks — but it means the escalation path to a DPO or admin has to be fast and documented in
your on-call rota. If your organisation genuinely wants first responders to be able to open the
register, grant `breach.manage` to `operator` in `rbac.py` as a conscious decision and re-seed; do not
work around it by sharing a DPO account.

---

## 5. The runbook

Times below are elapsed from **awareness (T0)**, not from detection.

### T0 − ∞ · Before anything happens (one-time setup)

Set these on the tenant's `Organization` row, or the notices have nowhere to go and nobody to name:

| Field | Why | Provision |
|---|---|---|
| `dpo_name`, `dpo_email`, `dpo_phone` | Copied onto every breach as the R.7(1)(e) contact. A notice with nobody able to respond is not a compliant notice. | R.7(1)(e), R.9 |
| `settings["board_notification_email"]` | Where a Board intimation is emailed. Optional — see §6. | R.7(2) |
| `settings["cert_in_notification_email"]` | Where a CERT-In report is emailed. Optional — see §6. | CERT-In 2022 |
| `settings["breach_without_delay_target_hours"]` | Internal target for the two "without delay" obligations. Default 24. | — (internal) |

Also confirm CERT-In's adjacent standing obligations are actually true of the deployment, because they
are checked when you report an incident, not before: ICT logs retained **180 days** within India (see
`RETENTION_SCHEDULE.md` §4), system clocks synchronised to **NIC/NPL NTP**, and a designated point of
contact registered with CERT-In. Gap item **H-12** tracks these.

### T0 − (detection) · Register the breach

```
POST /breaches
{ "title": ..., "source_app": "<tenant code>",
  "detected_at": "<when detection happened>", "occurred_at": "<best known, may be omitted>",
  "severity": "LOW|MEDIUM|HIGH|CRITICAL",
  "nature": ..., "extent": ..., "location": ..., "likely_impact": ...,
  "likely_consequences": ..., "mitigation_measures": ..., "safety_measures": ..., "cause": ... }
```

Do this the moment there is a candidate incident. Status becomes `DETECTED`; no clock is running.
Narrative fields can all be filled in later with `PATCH /breaches/{ref}`.

### T0 · Record awareness — **the decision that starts every clock**

```
POST /breaches/{ref}/aware   { "aware_at": "<optional; defaults to now>" }
```

Made by the DPO. Status moves to `CLASSIFIED`. From this instant:

- CERT-In due at **T0 + 6h**
- Board detailed report due at **T0 + 72h**
- Principal notices and the Board's initial intimation due **without delay**

`GET /breaches/{ref}/clocks` returns all four with `deadline_at`, `internal_target_at`,
`remaining_hours` and a status of `NOT_STARTED` / `OPEN` / `MET` / `MET_LATE` / `OVERDUE` /
`NOT_APPLICABLE`.

### T0 → T0+6h · CERT-In

```
GET  /breaches/{ref}/reports/cert-in      # preview
POST /breaches/{ref}/notify/cert-in       # generate, hash, dispatch or hold
```

Report on the information you have. The direction is explicit that where full information is not
available within 6 hours, an entity may report what it has and supplement it — an incomplete report on
time beats a complete one at hour seven.

If the incident is genuinely outside Annexure I, set `cert_in_reportable=false` **with**
`cert_in_not_reportable_reason`. The clock then reports `NOT_APPLICABLE` **with that reason attached** —
it does not silently disappear, and the decision is on the record for whoever reviews it later.

### T0 → without delay · Principal notices (R.7(1))

1. Scope who is affected:
   ```
   POST /breaches/{ref}/affected  { "external_ids": [...], "data_involved": "..." }
   ```
   External ids are resolved tenant-scoped: an id belonging to a different tenant comes back in
   `unresolved`, never silently added. Call this repeatedly as the investigation widens the list.

2. Assert the list is complete:
   ```
   POST /breaches/{ref}/affected/finalise
   ```
   Until you do, the principal clock reports `scope_finalised: false` and will **not** claim `MET`,
   because "every affected principal has been notified" is not knowable while the list is still moving.
   This is deliberate: a green clock over an unfinished list is the worst possible output.

3. Issue the notices:
   ```
   POST /breaches/{ref}/notify/principals   { "language": "<optional>" }
   ```
   One notice per affected principal, carrying all five mandated contents, queued through the platform's
   own notification service (`app/services/notifications.py`) — the same queue, retries, backoff and
   K-44/K-45 delivery metrics as every other principal communication, delivered through the account and
   the channels the principal is already reachable on, per R.7(1)'s "through her user account or any
   mode of communication registered by her".

   Generation is **refused** if any of the five is empty, naming the missing clause. Fill the field in
   and retry; do not work around it.

| Notice section | Field on the breach | Provision |
|---|---|---|
| 1. What happened — nature, extent, timing | `nature`, `extent`, `occurred_at`/`detected_at`/`aware_at`, plus the principal's own `data_involved` | R.7(1)(a) |
| 2. Likely consequences for you | `likely_consequences` | R.7(1)(b) |
| 3. What we have done and are doing | `mitigation_measures` | R.7(1)(c) |
| 4. Steps you can take | `safety_measures` | R.7(1)(d) |
| 5. Who to contact | `contact_name` / `contact_email` / `contact_phone` (defaulted from the tenant DPO) | R.7(1)(e) |

### T0 → without delay · Board initial intimation (R.7(2)(a))

```
GET  /breaches/{ref}/reports/board-initial
POST /breaches/{ref}/notify/board-initial
```

Five items: nature, extent, timing of occurrence, **location of occurrence**, likely impact. Location is
required here and absent from the principal notice — the two content lists genuinely differ, which is
why they are generated separately rather than from one template.

### T0 → T0+72h · Board detailed report (R.7(2)(b))

```
GET  /breaches/{ref}/reports/board-detailed    # check `missing_mandated_sections` first
POST /breaches/{ref}/notify/board-detailed
```

Six sections, in the Rules' order:

| § | Content | Source |
|---|---|---|
| (i) | Updated and detailed information in respect of clause (a) | `nature`, `extent`, `location`, `likely_impact`, the timestamps, affected count |
| (ii) | Broad facts: events, circumstances and reasons leading to the breach | `cause` |
| (iii) | Measures implemented and being implemented to mitigate risk | `mitigation_measures` |
| (iv) | Findings regarding the person who caused the breach | `findings_on_actor` |
| (v) | Remedial measures taken to prevent recurrence | `remedial_measures` |
| (vi) | Report on the intimations given to affected Data Principals | **generated**, never typed |

Filing is **refused** while any of (i)–(v) is empty, and the refusal names the missing clause numbers.
Section (vi) is computed from the notification rows themselves — counts generated/sent/delivered/failed,
channels, first and last send time, hours from awareness, and every principal notice's content hash.
The point of (vi) is that it is an account of what actually happened, so it is not a field anyone can
fill in.

### If you cannot make 72 hours — the extension log (R.7(2)(b) proviso)

The Rule allows "such longer period as the Board may allow on a request made in writing in this behalf".

```
POST /breaches/{ref}/extensions
{ "requested_until": "...", "reason": "...", "written_request_ref": "LETTER/..." }

POST /breaches/{ref}/extensions/{id}/decision
{ "status": "GRANTED|REFUSED|WITHDRAWN", "granted_until": "...", "board_reference": "DPB/..." }
```

**Requesting an extension does not move the deadline. Only the Board granting one does.** The platform
enforces this: `effective_board_deadline` is unchanged while a request is `REQUESTED`, and moves only on
`GRANTED` with a `granted_until`. The clock keeps reporting the original statutory deadline alongside
the effective one (`statutory_deadline_at`), so an extension is visible as an extension rather than
rewriting history. Send the written request early; do not wait for hour 71.

### Closing

```
POST /breaches/{ref}/status  { "status": "CONTAINED" }     # DETECTED → CLASSIFIED → CONTAINED
POST /breaches/{ref}/status  { "status": "NOTIFIED" }
POST /breaches/{ref}/status  { "status": "CLOSED", "note": "..." }
```

Closure is **refused** while any obligation is outstanding, and the refusal lists them: awareness not
recorded, scope not finalised, a principal without a notice, a notice not sent, the Board initial or
detailed report not filed, the CERT-In report not filed. `GET /breaches/{ref}` reports the same list as
`outstanding_obligations` at any time, so the gap is visible long before anyone tries to close.

---

## 6. What "sent" means for each recipient

| Recipient | Transport | What the platform does |
|---|---|---|
| Data principal | The platform's own notification service — email/SMS/in-app, with retries and backoff | **Real delivery.** `sent_at`/`delivered_at` are synced back from the `notifications` row on every read. |
| Data Protection Board | None (the Board operates a portal) | Generates and hashes the filing. Emails it if `settings["board_notification_email"]` is set; otherwise holds it `PENDING` with the reason on the row. |
| CERT-In | None (incident form / `incident@cert-in.org.in`) | Same. |

For a regulator filing made out of band, record it:

```
POST /breaches/{ref}/notifications/{id}/record-filing
{ "filing_reference": "DPB/2026/000123", "filed_at": "...", "delivered": true }
```

`filing_reference` is mandatory — it is the evidence the filing happened. A principal notice can **not**
be marked filed by hand: its timestamps come from the dispatcher, which is the only thing that actually
knows whether it went out.

---

## 7. Content hashes — proving a filing unaltered

Every filing stores a structured `payload` plus a `content_hash`: SHA-256 over the canonical JSON of
that payload, using **exactly** the canonicalisation the audit ledger uses
(`app/core/audit_chain.py`: `json.dumps(..., sort_keys=True, default=str)` with datetimes normalised to
UTC). One hashing scheme for the platform, not two — key order and timezone representation cannot change
a hash.

```
GET /breaches/{ref}/notifications/{id}/verify
→ { "recorded_hash": ..., "recomputed_hash": ..., "matches": true|false, "algorithm": ... }
```

Related guarantees:

- A filing already `SENT`/`DELIVERED` cannot be regenerated over. Regenerating would destroy the record
  of what was actually filed; log a corrective filing instead.
- Every step writes to the append-only, hash-chained `audit_logs` (`BREACH_REGISTERED`,
  `BREACH_AWARENESS_RECORDED`, `BREACH_SCOPE_UPDATED`, `BREACH_SCOPE_FINALISED`,
  `BREACH_PRINCIPAL_NOTICES_ISSUED`, `BREACH_BOARD_NOTIFIED`, `BREACH_CERT_IN_NOTIFIED`,
  `BREACH_FILING_RECORDED`, `BREACH_EXTENSION_REQUESTED`, `BREACH_EXTENSION_DECIDED`,
  `BREACH_STATUS_CHANGED`). A database trigger blocks UPDATE and DELETE on that table.
- Audit metadata records **counts** of affected principals, never their external ids. The register knows
  who is affected; the ledger does not need to restate it.

---

## 8. Metrics (K-39, K-40, K-41)

`GET /breaches/metrics[?source_app=...]`

| KPI | What it reports | Target |
|---|---|---|
| **K-39** | Mean/max time-to-detect (occurrence → detection), mean detect→aware triage lag, mean hours from awareness to the first principal notice, max hours to the last | "without delay" (law) |
| **K-40** | Mean/max hours to the Board initial intimation; mean hours to the detailed report; count filed; count within the deadline in force; % within deadline | **100% within 72h** (R.7(2)(b)), or within a granted extension |
| **K-41** | Affected principals in total and per breach; notices generated / sent / delivered; delivery rate | **100% delivered** |
| *(H-12)* | CERT-In: reportable breaches, reports filed, reports within 6 hours, % within, mean hours | 100% within 6h |

`per_breach` carries the same figures one row at a time, which is what a Board query about a specific
incident actually needs. `GET /breaches/register` is the register extract for the regulator evidence
pack: every breach with its four clocks, its filings, their deadlines and their content hashes.

---

## 9. What this platform does **not** do

Stated plainly, because a runbook that implies coverage it does not have is worse than no runbook.

1. **It does not detect breaches.** Nothing here watches for anomalous access or exfiltration. The
   register is fed by humans and by whatever monitoring the operator runs.
2. **It does not file with the Board or CERT-In.** No API exists to file with. It generates, hashes,
   deadlines and — where an address is configured — emails; the filing itself remains an operator act
   whose reference must be recorded (§6).
3. **It does not decide notifiability.** Whether an incident is a personal data breach, and whether it
   falls in CERT-In's Annexure I, are DPO judgements. The platform records the decision and its reason;
   it does not make it.
4. **It does not chase a deadline at you.** Clocks are computed on read. There is no scheduled job
   escalating an approaching CERT-In window to a pager — the on-call process has to look. Adding one is
   a small `app/jobs/` entry and a sensible follow-up; it is not built.
5. **It does not translate notices.** `POST /breaches/{ref}/notify/principals` accepts a `language` and
   the notification service will use a template in that language where one exists, but the breach
   narrative fields themselves are stored in whatever language the DPO typed them in. R.7(1)'s "concise,
   clear and plain manner" and the Act's Eighth Schedule language expectations are, for now, on the
   person writing the text.
