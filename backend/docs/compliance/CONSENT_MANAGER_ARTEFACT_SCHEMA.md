# Consent artefact schema — `urn:consent360:artefact-schema:1.0`

**Gap:** CM-01 (interoperable consent-artefact API), B-09 (consent artefact/receipt)
**Served live at:** `GET /consent-manager/artefact-schema` (unauthenticated)
**Implemented in:** `app/services/consent_manager.py::build_artefact_payload`

This document exists because **ISO/IEC TS 27560:2023 clause 6.3.2.1 requires it**:

> *"Where the organization creates its own schema for the implementation of consent
> records, it **shall** publish or reference the schema(s) being used and maintain
> documentation necessary for its correct technical implementation and conformance to the
> requirements specified in this document."*

That is one of the few `shall` clauses in the specification, and it is the one most
implementations quietly skip. `schema_version` in every artefact payload is the URN above;
this file and the `/consent-manager/artefact-schema` endpoint are what it refers to.

---

## 1. Conformance statement — read this before repeating any claim

**This payload is modelled on ISO/IEC TS 27560:2023. It is not certified against it, and
no conformance assessment of any kind has been performed.**

Three facts about the standard make that the only honest phrasing available:

1. **TS 27560 defines an information model, not a JSON encoding.** Every annex is marked
   *informative* — including Annex A ("Examples of consent records and receipts") and
   Annex D ("Consent record encoding structure"). The only normative references in
   Clause 2 are ISO/IEC 29100:2011 and ISO/IEC 29184:2020; no JSON, JSON-LD or JSON
   Schema reference is normative. The Introduction states the document *"does not specify
   an exchange protocol for consent records or consent receipts, nor structures for such
   exchanges"*, and clause 6.3.2.2 NOTE 2 says implementers may organize the fields
   *"according to the implementers' operational needs"*.
2. **There is therefore no official JSON Schema and no validator.** "Passes the TS 27560
   validator" is not a statement that can be true of anything. Conformance means carrying
   the required information elements with the required presence, under a published schema.
3. **Not all of the standard's text is publicly readable.** ISO's own preview covers the
   front matter, Scope, definitions, full Contents and the normative Tables 1 and 2. The
   field tables for the party, event and PII-information sections are beyond it. Section 3
   below marks, field by field, which names come from the ISO text and which come from the
   W3C DPV community group's implementation guide.

**Shelf life.** TS 27560:2023 (first edition, 2023-08-08, ISO/IEC JTC 1/SC 27) has been at
ISO stage **90.92 — "standard to be revised" — since 2025-04-03**, and is being replaced by
**ISO/IEC CD 27560.2**, retitled *"Structure of Personally Identifiable Information (PII)
Processing Records"*, targeted to publish as a full International Standard. This schema
will need to be re-checked against the successor when it publishes.

**Contrast with Kantara CR v1.1.0**, the predecessor most people mean when they say
"consent receipt": Kantara's JSON field names *are* normative, it ships a literal
draft-04 JSON Schema, and it uses camelCase. If machine-validatable JSON conformance is
what a counterparty needs, Kantara is the only one of the two that offers it — and it is
receipt-only and its authoring group is archived. TS 27560 deliberately abandoned that
approach; we follow TS 27560.

## 2. Structure

Clause 6.3.2.2 says the record *should* be organized into six sections: record header,
PII processing, event, purposes, PII information, and party identification. It fixes the
*field* names; it does not fix container names for those sections, and NOTE 2 expressly
leaves the arrangement to the implementer. Ours:

```
{
  "schema_version":   "urn:consent360:artefact-schema:1.0",   # record header
  "record_id":        "CA-...",                                # record header
  "pii_principal_id": "pid_...",                               # record header
  "pii_processing": {                                          # PII processing section
    "privacy_notice": {...},
    "language": "en",
    "purposes": [ {                                            # purposes section
      "purpose": "...", "purpose_type": "...", "lawful_basis": "...",
      "pii_information": [ {...} ],                            # PII information section
      "pii_controllers": ["<party_id>"],
      "collection_method": "...", "processing_method": "...",
      "storage_locations": [], "retention_period": {...},
      "x_consent360": {...}
    } ]
  },
  "parties": [ {...} ],                                        # party identification section
  "event":   {...},                                            # event section
  "x_consent360": {...},                                       # extensions, below
  "x_dpdp":       {...},
  "x_sharing":    {...}
}
```

Every key that is **not** from the standard is prefixed `x_`. A reader can therefore always
tell, from the payload alone and without consulting this document, which parts are the
standard's and which are ours.

## 3. Field provenance

### 3.1 Record header — TS 27560 Table 1. All three REQUIRED. **Verified against the ISO text.**

| Field | Clause | Our value |
|---|---|---|
| `schema_version` | 6.3.3.2 | `urn:consent360:artefact-schema:1.0` — the reference this document describes, as 6.3.2.1 requires |
| `record_id` | 6.3.3.3 | `CA-<20 hex>`. The standard's *recommended* encoding is UUID-4; ours is a 20-hex-character slice of one with a readable prefix, which is a deliberate deviation for operator legibility and is recorded as such here |
| `pii_principal_id` | 6.3.3.4 | The pseudonym — see `CONSENT_MANAGER_DATA_BLIND_DECISION.md`. 6.3.3.4 itself recommends pseudonyms |

### 3.2 PII processing — TS 27560 Table 2. **Field names verified against the ISO text.**

| Field | Clause | Presence in TS 27560 | Our value |
|---|---|---|---|
| `privacy_notice` | 6.3.4.2 | Required | The `NoticeVersion` pinned on the consent: id, version number, content hash, language, title. `null` when no notice has been published for the purpose — a stated absence, not a fabricated id |
| `language` | 6.3.4.3 | Required | The language recorded on the consent evidence |
| `purposes` | 6.3.4.4 | Required | One entry per linked consent row |
| `purpose` | 6.3.4.5 | Required | The purpose's name |
| `purpose_type` | 6.3.4.6 | Optional | The purpose's code |
| `lawful_basis` | 6.3.4.7 | Optional | Always populated. TS 27560 makes it optional because it assumes consent; DPDP s.4 requires a named gateway, so this is `CONSENT` (s.6) or one of `S7_A`…`S7_I` |
| `pii_information` | 6.3.4.8 | Required | The data categories covered. Sub-field names are from the DPV guide (§3.4) |
| `pii_controllers` | 6.3.4.9 | Required | A list of `party_id` **references** into the party section, which is what the standard specifies — not inline controller objects |
| `collection_method` | 6.3.4.10 | Optional | `CONSENT_MANAGER`, `PORTAL`, `UI`, … from the consent row |
| `processing_method` | 6.3.4.11 | Optional | The processing activity's code |
| `storage_locations` | 6.3.4.12 | **Required** | **Emitted empty.** This platform does not yet record a per-purpose storage location. Emitting a guess in a required field would be worse than an explicit absence; recorded here as a known deviation |
| `retention_period` | 6.3.4.13 | Required | `{x_days, x_expires_at}` — the sub-field names are ours, the standard does not fix them |

### 3.3 Party identification. **Names from the W3C DPV guide, NOT verified against the ISO text.**

`party_id`, `party_name`, `party_type`, `party_role`, `party_contact`, `party_email`,
`party_phone`, `party_url`, `party_address`.

Two parties appear: the Data Fiduciary (`party_role: "PII_CONTROLLER"`) and, when one
brokered the consent, the Consent Manager. The CM's role is written
`x_dpdp_CONSENT_MANAGER` — prefixed because TS 27560 has no Consent Manager concept at all
and a bare `CONSENT_MANAGER` role would look like a standard value that does not exist.

Contact details are the tenant's own DPO configuration: the business contact a principal
is entitled to under s.8(9) and R.9, published deliberately — not personal data of the DPO
gathered from somewhere else.

### 3.4 PII information and event. **Names from the W3C DPV guide, NOT verified.**

- PII information: `pii_type`, `pii_attribute_id`, `pii_optional`,
  `sensitive_pii_category`, `special_pii_category`.
- Event: `entity_id`, `event_type`, `event_time`, `event_state`, `validity_duration`.

Confidence note: for the 12 processing fields that *could* be checked against the ISO text,
the DPV guide's names and presence flags matched exactly. That is good evidence for these,
not proof. (The DPV guide's *clause numbers* are consistently one off — it labels
`privacy_notice` as 6.3.4.1 where ISO has 6.3.4.2, since 6.3.4.1 is "General" — so the
clause numbers cited in this document come from the ISO text, not the guide.)

A further caution: the DPV guide's own JSON is JSON-LD using `dpv:` IRIs and its figure
caption states *"the field names have been modified for alignment with DPV concepts"*. It
deliberately deviates from ISO's model in places (replacing ISO's `purposes` array with
`dpv:Process`). **We follow the ISO field names where we can read them, and use the DPV
guide only as a source for names we cannot.** This payload is not DPV JSON-LD and does not
claim to be.

### 3.5 Extensions — entirely ours

| Block | Contents |
|---|---|
| `x_consent360` | Schema reference, conformance statement, record version, artefact status, `source_app`, consent manager ref, `data_blind`, timestamps, collection method. For a fiduciary reading its own tenant only: `pii_principal_external_id`, `pii_principal_email_masked` |
| `x_dpdp` | Act and Rules citations, s.6 / s.6(7), the 7-year retention basis (First Schedule Part B 3, 4(c)) |
| `x_sharing` | `revocable`, `data_life_days`, `frequency`, `access_mode`, `notification_url` — machine-to-machine semantics from MeitY BRD §4.1.1 and the DEPA / RBI Account Aggregator artefact. None exists in TS 27560 |

DPDP has no representation in DPV either: the DPV India extension
(`https://w3id.org/dpv/legal/in#`) contains exactly two concepts — the Act itself and the
Data Protection Board — and neither TS 27560 nor the DPV 27560 guide mentions DPDP at all.
Every DPDP-specific element here therefore *has* to be an extension; there is no standard
slot being ignored.

## 4. Integrity and signatures

**TS 27560 has no signature field.** What it has:

- 6.2.2.1 (`shall`): records *"shall be kept in a format and manner that provide assurances
  that the records' integrity is maintained over time."*
- 6.2.3.3 (`may`): a consent receipt *"may include information integrity controls to hinder
  modification."*
- 6.2.3.6 (`shall`): receipts shall be *"accurate, traceable, and verifiable."*
- 6.2.2.6 (`shall`): unique references to the *specific version* of information expected to
  change over time — the NOTE names privacy notices explicitly.

Each artefact version is stored as an immutable `consent_artefact_events` row carrying
`payload_hash` (SHA-256 over the canonical, sorted-key JSON) and `signature`
(HMAC-SHA256 over that hash, via `app.core.encryption.hmac_signature` — the same keyed
primitive that already signs `ConsentEvidence` and `ConsentReceipt`; no second scheme).
Verification uses `hmac_signature_matches`, which accepts a signature made under any key in
the search-digest key ring, so rotating the HMAC key does not turn every historical
artefact into a false tamper alarm. `signature_valid` is recomputed on every read.

This satisfies 6.2.2.1 and 6.2.3.6 — it does **not** implement a named standard field,
because there is none. 6.2.2.6 is satisfied by pinning `purpose_version_id`,
`policy_version_id` and `notice_version_id` on every consent, which the platform already did.

## 5. Versioning

Two independent version numbers, deliberately not conflated:

- **`schema_version`** identifies the *shape* of the payload. It changes when this document
  changes. Bumping it is a schema release, and old artefacts keep their old value forever —
  a reader always knows which structure it is holding.
- **`x_consent360.record_version`** (and `artefact_version` on each event) identifies the
  revision of *this particular record*. Version 1 is `CREATED`; every subsequent give,
  manage or withdraw appends a new signed version. Nothing is ever overwritten, so an
  auditor can reconstruct what the artefact said at any past moment and verify the
  signature that was made at the time.

## 6. Known deviations, in one place

1. `record_id` is not a bare UUID-4 (recommended, not required) — a readable `CA-` prefix
   plus 20 hex characters of one.
2. `storage_locations` is required by the standard and emitted empty, because the platform
   does not yet record per-purpose storage locations.
3. Section container names (`pii_processing`, `parties`, `event`) are ours; the standard
   names the sections but not the containers, and 6.3.2.2 NOTE 2 permits this.
4. `retention_period`'s internal shape (`x_days`, `x_expires_at`) is ours.
5. Party, event and PII-information field names are unverified against the ISO text.
6. No conformance assessment has been carried out by anyone.
