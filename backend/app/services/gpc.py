"""R2-11/Q-07 - Global Privacy Control as an enforceable objection.

`Sec-GPC: 1` (https://globalprivacycontrol.org/) is a machine-readable
objection sent by the principal's own user agent. This codebase already read
it server-side and made it durable evidence on `ConsentEvidence.gpc_signal`
(see app/api/routes/portal.py and crm.py's `_read_gpc_signal`), but nothing
ever *decided* anything from it: a request carrying `Sec-GPC: 1` together
with `analytics: true` still produced an ACTIVE analytics consent. The
browser-side gate suppressed the optional tags while the server recorded a
grant, so the client and the server disagreed about the same request - and
the server is the record of truth.

WHY IT BLOCKS A GRANT AT ALL. s.6(1) requires consent to be "free, specific,
informed, unconditional and unambiguous with a clear affirmative action". A
single request that carries both an affirmative click and a standing,
machine-readable objection from the same principal is, on its face, not
unambiguous. The Act does not let a fiduciary pick the half of that request
it prefers.

WHAT IS ENFORCED ON. The SERVER-OBSERVED header only - the `gpc_signal`
argument that routes populate from `request.headers`. The client-claimed
value the request *body* may carry (`ClientContext.gpc_signal`, kept in
`ConsentEvidence.details["claimed_gpc_signal"]`) is never read here. That
split is deliberate and pre-existing: a caller can assert anything in a body,
so letting a claimed signal force an outcome would let a client manufacture
denials for a principal - the mirror image of the bug being fixed.

WHAT IS NOT AFFECTED. Only processing that actually rests on s.6 consent can
be objected to by withholding consent. A purpose whose lawful gateway is one
of the s.7(a)-(i) legitimate uses, or that is marked `requires_consent =
False`, is not consent-based; there is no consent for GPC to withhold, and
blocking it would break the service rather than protect the principal. That
is exactly the seeded `strictly_necessary` purpose (legal_basis S7_A,
requires_consent False - login, session security and keeping a record of the
principal's own consent choices), which therefore passes through untouched.
The test suite pins that.

DELIBERATE BOUNDARY, stated so nobody mistakes it for an oversight. This
enforces GPC on the act of RECORDING an affirmative consent - grant and
renew. It does not retroactively act on a consent that was already active
before the objection arrived: `routes/crm.py::_sync_consent_preferences` and
`routes/portal.py::portal_grant` both skip a consent that is already active,
so `grant_consent` is never reached for one. Treating a standing GPC header
as an objection against EXISTING consents is a different decision (does it
withdraw them? when does the objection lapse?) and belongs with the
objections register (R1-08/C-06, `app/api/routes/objections.py`), not in the
grant path. What is closed here is the reproduced defect: an objection
arriving with the grant request itself.
"""
from typing import Optional

from app.models.entities import Consent

#: Audit event written whenever a GPC objection actually changes an outcome.
#: Listed in `AUDIT_EVENTS` so the audit-log filter can offer it.
GPC_ENFORCED_EVENT = "GPC_OBJECTION_ENFORCED"


def purpose_is_consent_based(purpose) -> bool:
    """True when this purpose's lawful gateway is s.6 consent.

    Both conditions are checked rather than either alone: `legal_basis` is the
    gateway the Act cares about, and `requires_consent` is the operational
    flag the decision engine already honours (`purpose.requires_consent is
    False -> ALLOW`). A purpose that disagrees with itself is treated as not
    consent-based, i.e. GPC leaves it alone - the conservative direction for a
    guard that can only ever refuse.
    """
    if purpose is None:
        return False
    if purpose.requires_consent is False:
        return False
    return (purpose.legal_basis or "CONSENT") == "CONSENT"


def objection_blocks_consent(consent: Consent, gpc_signal: Optional[bool]) -> bool:
    """Does a server-observed GPC objection forbid recording this affirmative
    consent?

    `gpc_signal` is tri-state on purpose: None means the header was absent
    (the overwhelming majority of callers, and every staff-console path),
    False means the header was present but not "1". Only an explicit True is
    an objection, so every existing caller is unaffected.
    """
    if gpc_signal is not True:
        return False
    return purpose_is_consent_based(consent.purpose)


def refusal_reason(purpose, action: str) -> str:
    verb = "renewed" if action == "RENEW" else "granted"
    return (
        f"Consent for {purpose.name} was not {verb}: the request carried an active Global "
        "Privacy Control objection (Sec-GPC: 1) from the data principal's own user agent. "
        "DPDP Act s.6(1) requires consent to be unambiguous and given by a clear affirmative "
        "action; a request that simultaneously asserts an objection is not that, so the consent "
        "is recorded as refused rather than given. Strictly necessary processing, which does not "
        "rest on consent, is unaffected."
    )
