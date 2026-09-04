# ADR: Consent360 Consent Manager Architecture

**Date**: 2026-09-03
**Status**: Proposed
**Decision**: Continue as fiduciary-side CMS with full PII visibility (current default)

## Context

Consent360 is considering registering as a Consent Manager under the DPDP Act, which requires specific architectural changes around data blindness and interoperability with ISO/IEC TS 27560. The gap register flags this with a "by 13 Nov 2026" deadline.

Two architectural options are under consideration:

## Option A: Continue as Fiduciary-Side CMS (Current Default)

**Description**: Consent360 continues as a fiduciary-side consent management platform where full PII is visible to the platform. The platform stores and processes personal data, and fiduciaries (CRM systems, enterprise customers) interact with consent records through API keys and context tokens. This is the current implementation.

**Pros**:
- No storage redesign required - existing encrypted storage model continues to work
- Full visibility into consent data for platform features (analytics, audit, reporting)
- No changes needed to data model or encryption infrastructure
- Faster to ship - existing features can be extended without major refactoring

**Cons**:
- Does not meet the data-blind storage requirement for actual Consent Manager registration under DPDP
- Platform has access to PII, which may conflict with certain compliance requirements
- Not interoperable with DEPA/Account Aggregator framework without additional redesign

**Impact**: Minor - existing code continues to work. Future CM registration would require a separate redesign path.

## Option B: Redesign Toward Data-Blind Storage for CM Registration

**Description**: Consent360 redesigns its storage so that the platform itself cannot read the content of consent records. Encryption keys would be managed such that even the platform operators cannot decrypt personal data without explicit customer consent. This would enable registration as a true Consent Manager under DPDP.

**Pros**:
- Meets the data-blind storage requirement for DPDP Consent Manager registration
- Enables interoperability with DEPA/Account Aggregator framework
- Stronger privacy guarantees - platform operators cannot access PII without customer consent
- Aligns with RBI's Account Aggregator framework patterns

**Cons**:
- Significant storage redesign required - existing `EncryptedString`, `EncryptedText`, `EncryptedJSON` TypeDecorators would need to be replaced with KMS-backed encryption where the key never touches the application server
- Would require changes to all data access patterns - queries, audits, analytics would need to be restructured
- Performance impact - additional KMS calls for each read operation
- Higher complexity - key management, rotation, and audit logging become more challenging
- Would delay other R3 features while the redesign is implemented

**Impact**: Major - requires significant refactoring of the data storage layer, encryption module, and potentially the entire data access pattern.

## Decision

**Continue as fiduciary-side CMS with full PII visibility (Option A).**

**Rationale**:
- The current implementation can be extended to meet the interoperability requirements (ISO/IEC TS 27560 consent artefact API, signed events) without a full storage redesign
- The data-blind redesign (Option B) would delay critical R3 features (breach management, processor register, notification service) by several months
- A clear migration path can be documented: the platform can start as a fiduciary-side CMS and evolve toward data-blind storage in a future phase (post-R3), when the CM registration deadline approaches
- The architectural ADR is explicitly documented so the team can revisit this decision when the 13 Nov 2026 deadline approaches

**Next Steps**:
1. Build the versioned consent-artefact API (Option A) - this is the immediate R3-10 deliverable
2. Document the migration path to data-blind storage for future consideration
3. Flag this decision in the project risk register for review at the 13 Nov 2026 deadline
4. Implement governance items (CM-05) as org-level tracking, not backend changes