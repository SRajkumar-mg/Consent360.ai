# Data-blind design decision — Consent Manager interoperability

**Gap:** CM-02 (and CM-01, which the decision constrains)
**Provision:** DPDP Rules 2025, First Schedule Part B 2 — a Consent Manager shall
"ensure that the personal data whose sharing it enables is not readable by it".
**Decided:** 4 September 2026, as part of R3-10.
**Status:** Decided and implemented. Reversal requires a new decision record, not a config change.
**Implemented in:** `app/services/consent_manager.py`, `app/api/routes/consent_manager.py`
**Tested by:** `tests/test_consent_manager_artefacts.py::test_consent_manager_payload_is_data_blind`

---

## 1. The question this record answers

CM-02 in the gap register said only "Architectural decision required (fiduciary-side
CMS vs registered CM)". That is a decision, not a gap to be closed by code, and leaving
it implied by whichever fields a serialiser happened to emit is how a compliance posture
becomes an accident. So it is written down here, explicitly, with the reasoning.

The question is really two questions, and conflating them is the usual mistake:

1. **Is Consent360 a Consent Manager?** (Does Part B apply to *us*?)
2. **When a Consent Manager talks to Consent360, what can that Consent Manager see?**

## 2. The decision

### 2.1 Consent360 is a fiduciary-side consent platform, not a registered Consent Manager

Consent360 is deployed by Data Fiduciaries, holds their principals' personal data on
their instructions, and is a **Data Processor** to each of them. It has not applied for
registration with the Data Protection Board under s.6(9)/R.4, and building the CM-facing
API in R3-10 does not change that: an interface a Consent Manager can talk to is not the
same thing as being one.

Consequence: First Schedule Part B does **not** bind Consent360 as an obligation. The
7-year record retention, the transparency disclosures and the data-blindness requirement
are implemented here as *readiness* — so that a customer who does register as a CM
inherits a platform that already behaves correctly — and because Part B 2's underlying
principle is good design regardless of whether it is legally compelled.

If Consent360 ever does register as a CM in its own right, this record is void and Part B 2
becomes a much harder engineering problem: the personal data itself would have to be opaque
to the platform (end-to-end encrypted between fiduciaries, with the CM holding no key),
which is a different system, not a configuration of this one. Section 5 says what that would take.

### 2.2 Towards a Consent Manager, Consent360 is data-blind

This is the operative half. When a registered Consent Manager calls the artefact API,
it receives **consent metadata and nothing else**:

| The CM receives | The CM never receives |
|---|---|
| The artefact reference, its version, its state and its signed history | The principal's name, email, phone, IP address or user agent |
| Which purposes the consent covers, and each purpose's lawful basis, notice version and notice hash | The fiduciary's own external id for the principal |
| Which **categories** of personal data each purpose covers | Any **value** of any field in those categories |
| Which processing operations each purpose authorises | Any consent-evidence content beyond the reference the CM itself supplied |
| The fiduciary's published business contact (DPO name/email/phone, rights, withdrawal, grievance and Board-complaint links) — the contact a principal is entitled to under s.8(9)/R.9 | Anything about any other principal, any other fiduciary, or any other Consent Manager's artefacts |
| A pseudonymous `pii_principal_id` | Any means of reversing that pseudonym |

There is no endpoint on this surface that returns a personal-data value at all. That is
the strongest form of the control: data-blindness here is not a filter applied to a
richer response, it is the absence of any route that could return the data.

### 2.3 Why data categories are named but values are not

A consent that does not say what it covers is not "specific" under s.6(1), and a CM that
cannot tell the principal what they are consenting to cannot let them "give, manage,
review and withdraw" it meaningfully under s.6(7). So the artefact names the *categories*
(`email_address`, `transaction_history`, ...) and the processing operations. Part B 2 is
about the data whose sharing the CM enables — its contents — not about the description of
what is covered. Naming the category is the minimum that makes the consent real; naming a
value would be exactly what Part B 2 forbids.

### 2.4 The pseudonym

`pii_principal_id` is `"pid_" + HMAC-SHA256(server key, "principal-pseudonym|<cm_ref>|<customer id>")`,
truncated to 32 hex characters (`principal_pseudonym` in `app/services/consent_manager.py`).
Four properties, each of which is the reason for one part of the construction:

- **Keyed, not hashed.** An attacker who obtains the reference, or even the whole
  `consent_artefacts` table, cannot brute-force it back to a customer id without the
  server's HMAC key. An unkeyed hash of a small integer id would be trivially reversible.
- **Salted with the CM's own reference.** The same principal is a *different* reference to
  every Consent Manager. Two CMs comparing their subject lists learn nothing — they cannot
  discover that they share a principal, which they otherwise could from an identical id.
- **Stable within one CM.** "Review my consents" works: every artefact that CM brokered
  for that principal carries the same reference, so it can group them without ever holding
  an identifier.
- **The same primitive already used elsewhere.** `hmac_signature` is what signs
  `ConsentEvidence` and `ConsentReceipt`. No second key, no second scheme.

This is also what the standard itself asks for. ISO/IEC TS 27560:2023 clause 6.3.3.4, on
`pii_principal_id`: *"organizations should consider using measures to prevent
identification of the PII principal through using mechanisms such as pseudonyms"*.

### 2.5 A fiduciary reading its own tenant is not data-blind, and should not be

`BrokerContext.data_blind` is defined as *exactly* "the caller is a Consent Manager". A
Data Fiduciary using the same API for its own tenant already holds its own customers'
personal data — it is the controller of it. Redacting its own external id out of its own
response would be security theatre: it adds no protection and makes the API unusable for
the direct-integration case. So that caller additionally sees its own `external_id` and a
masked email, and nothing more.

### 2.6 There is deliberately no per-CM override

`data_blind` is a computed property, not a column and not a setting. An obligation an
operator can switch off in an admin screen is not an obligation, and a "trusted CM"
exception is precisely the shape of the exception that gets granted once and then
inherited by everyone. If a future requirement genuinely needs a CM to see more, it needs
a new decision record and a new code path — not a boolean.

## 3. What was rejected, and why

**Give the CM the fiduciary's external id "because it needs to identify the principal".**
Rejected: it does not. The CM identifies the principal on first contact using an identifier
it *already holds* from its own relationship with them (it has an account with them — that
is what a Consent Manager is), and thereafter uses the pseudonym we return. Echoing the
fiduciary's internal identifier back would create a durable cross-tenant correlation key
inside a party that has no need for one.

**Let the CM create principals.** Rejected. A CM brokers consent for people who already
have a relationship with the fiduciary. Letting it create a `Customer` would hand it a
write primitive over a tenant's customer base — the ability to plant identities in a tenant
it does not own — which nothing in s.6(7) or Part B contemplates. `resolve_principal`
returns 404 rather than creating; see `test_a_consent_manager_cannot_create_a_principal`.

**Store the artefact payload encrypted so even Consent360 cannot read it.** Rejected for
this task, and the reasoning matters: it would be *necessary* if Consent360 were the
registered CM (see 2.1), and it is *not sufficient on its own* even then, because the
platform would still hold the underlying personal data in the fiduciary's own tables.
Doing half of it — encrypting the artefact while the same database holds the customer row
in the clear — would buy nothing but the appearance of the control.

**Make data-blindness a per-CM setting.** Rejected; see 2.6.

## 4. How the decision is enforced, and how you would notice it breaking

| Enforcement point | File |
|---|---|
| `BrokerContext.data_blind` is a property, not a stored flag | `app/services/consent_manager.py` |
| The payload's principal block is pseudonym-only when data-blind | `build_artefact_payload` |
| The API response omits `principal_external_id` when data-blind | `_artefact_out`, `app/api/routes/consent_manager.py` |
| A CM may not even *filter* by the fiduciary's customer id | `list_artefacts`, same file |
| A CM sees only artefacts it brokered, for the one fiduciary it named | `artefact_query` |
| A CM cannot create a principal | `resolve_principal` |

The test does not check a field list — it serialises the whole response to JSON and asserts
that the principal's external id, email and name appear **nowhere in it**. A future field
that leaks any of the three fails the test wherever it is added, including inside the
signed payload.

## 5. If Consent360 ever registers as a Consent Manager

This record is void, and the following would be required before registration:

1. **End-to-end encryption of brokered content** between fiduciaries, with Consent360
   holding no decryption key — the only construction that makes Part B 2 true of the
   platform rather than of one API boundary.
2. **Separation from the fiduciary-side deployment.** The same installation cannot both
   hold a fiduciary's personal data as its processor and be data-blind to it.
3. Part B 6–13: no sub-contracting of obligations, conflict-of-interest controls,
   fiduciary duty to principals, Board audits, and independent certification against the
   Board's standards — which, as of September 2026, have not been published (CM-05).
4. The transparency disclosures at `GET /consent-manager/disclosures` would have to be
   populated for Consent360 itself (J-04), not only for third-party CMs registered in it.

## 6. Review triggers

Re-open this decision if any of these happen:

- Consent360 applies to register as a Consent Manager.
- The Board publishes the assurance framework or certification standards under
  First Schedule Part A 9 (tracked as CM-05).
- A Consent Manager requests any personal-data content through this API — the request
  itself is the signal, and the answer is in section 2.2, but the reasoning should be
  re-read rather than the answer repeated.
- The First Schedule is amended.
