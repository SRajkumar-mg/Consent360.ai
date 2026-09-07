"""R1-09 (P-01, P-02, P-03, K-09): what counts as a material change, and what
happens when one is published.

`app/models/reconsent.py`'s docstring says why this exists. This module is the
definition and the mechanism.

--------------------------------------------------------------------------
THE DEFINITION, FIELD BY FIELD
--------------------------------------------------------------------------
`FIELD_RULES` below is the whole thing, written down rather than left implied
by a diff function, because "was that change material?" is a question a
regulator asks about a specific edit made two years ago and the answer has to
be the same every time.

The organising principle is **direction**. s.6(1) consent is agreement to a
specified purpose; a change that *widens* what is done, keeps data *longer*,
or changes the *kind* of thing being relied on takes the processing outside
what was agreed, and the agreement no longer covers it. A change that
*narrows* does not: the consent already covered a superset, and forcing
someone to re-consent to strictly less than they already agreed to is
consent fatigue with no legal benefit - it makes re-consent requests routine,
which is precisely how a genuinely material one gets clicked through.

So:

**Always material (a widening or a change in kind).**

* `data_category_ids` - a category ADDED. New personal data is now collected
  that the principal never saw listed. Removal alone is a narrowing.
* `data_items` - an item added, or an existing item's `necessity` changed.
  A-01's itemised list is what the principal actually read; changing whether
  an item is strictly necessary changes the bargain even when the category
  list is untouched.
* `processing_activity_ids` - an activity ADDED. Same data, new thing done to
  it, is exactly the "specified purpose" the section names. Removal is a
  narrowing.
* `retention_period_days` - INCREASED. Holding data longer than the period
  disclosed is processing beyond what was consented to; s.8(7) makes the
  period part of the bargain, not an implementation detail. A decrease is a
  narrowing.
* `legal_basis` - ANY change. Moving off CONSENT means the consent is no
  longer what authorises the processing (and the principal should be told
  what is). Moving onto CONSENT means consent must now actually be obtained.
  Moving between two s.7 gateways changes which statutory conditions apply.
  None of these is ever cosmetic.
* `requires_consent` - false -> true. Consent is now required where it was
  not; there is no existing consent to rely on. (True -> false is a
  narrowing: nothing more is asked of the principal.)
* `child_restricted` - false -> true. s.9's stricter regime now applies -
  verifiable parental consent, no behavioural advertising, no tracking. A
  consent given before that was declared cannot have accounted for it.

None of these can be downgraded by a publisher. A machine can tell an
addition from a removal and an increase from a decrease with certainty, so
there is nothing for human judgement to add - and an override switch on a
widening is exactly the switch that gets flipped under deadline pressure.

**Material by default, downgradeable on the record.**

* `description`, `services_enabled`, `consent_text` - free text. A typo fix
  in consent text is cosmetic; a rewrite of what the data enables (A-02) is a
  scope change wearing the same diff. No algorithm can tell them apart, so
  the default is the safe direction (material), a publisher may downgrade one
  to cosmetic, and the downgrade is recorded on `PolicyChangeLog` with who
  made it and why. The claim is auditable, not invisible.

**Never material.**

* Pure narrowings, as above.
* `name` - the label a purpose is listed under. The operative scope lives in
  the structured fields; renaming "Marketing" to "Marketing and offers"
  changes no processing.
* `translations` - additional language renderings of content already
  classified above. A new translation makes an existing disclosure reachable
  by more people, which is the opposite of a scope change.
* `checklist` - the R2-09 plain-language review record. Process metadata
  about the version, not content of it.

--------------------------------------------------------------------------
WHAT A MATERIAL CHANGE DOES
--------------------------------------------------------------------------
    publish_purpose_change()
      -> classify_change()          the definition above, per field
      -> PolicyChangeLog row        ALWAYS, material or not
      -> if MATERIAL:
           ReConsentCampaign row
           every affected consent:  re_consent_required = True,
                                    re_consent_requested_at = now,
                                    re-pointed at the new purpose version
           PURPOSE_CHANGE_RECONSENT notification per principal

and then `services/decision_engine.py::evaluate_decision` refuses to ALLOW
while the flag is set. The flag is cleared only by `grant_consent` /
`renew_consent` - a fresh affirmative act - never by the platform's own
re-pointing of the consent, which would be assuming the consent it is
supposed to be asking for.

--------------------------------------------------------------------------
P-02: THE SAME DEFINITION, APPLIED TO A POLICY
--------------------------------------------------------------------------
R1-09 built exactly this machinery for a `Purpose`. `CHANGE_ENTITY_TYPES`
always permitted `"POLICY"` too, but nothing ever called `_log_change` or
started a campaign for one - `PUT /policies/{id}` versioned the rule engine
silently. `publish_policy_change` below closes that, reusing the same
classifier engine (`classify_change`, now parameterised on which field
vocabulary to apply), the same `PolicyChangeLog`, and the same
`ReConsentCampaign` - not a second, parallel mechanism.

A `Policy` has none of a Purpose's prose. What it has is `rules` (one entry
per `purpose_code`/`data_category_code`/`processing_activity_code` triple:
`decision` ALLOW|DENY, `requires_active_consent`, `priority`) and
`default_decision` (ALLOW|DENY|REQUIRE_CONSENT for every triple no rule
names). `services/decision_engine.py::evaluate_decision` reads exactly these:
an explicit `DENY` rule blocks the triple outright, before consent is even
looked up; `requires_active_consent=False` lets a rule ALLOW without any live
consent; anything else falls through to the ordinary consent-status check.
So the same organising principle applies - a change that lets MORE happen
with LESS gating is material; a change that gates more is not - just applied
to what this artefact actually contains instead of to prose:

**`rules`, per triple, always material when it widens.** Three tiers, in the
order `evaluate_decision` actually checks them:

  0. `decision="DENY"`                              - blocked outright.
  1. no rule, or `ALLOW` + `requires_active_consent` - the ordinary
     True (the default)                                consent-gated flow.
  2. `ALLOW` + `requires_active_consent=False`       - always permitted,
                                                        consent irrelevant.

A triple moving to a HIGHER tier (a DENY lifted, or a rule newly bypassing
the consent check) is material for exactly the reason `data_category_ids`
widening is: something is now permitted that was not before, and an existing
consent - given against the old, more restrictive rule - does not cover it.
Moving to a LOWER tier is a narrowing: the existing consent already covers
the now-smaller permission. `priority` is not part of the comparison because
`find_applicable_rule` does not consult it - a change nothing evaluates
differently is not a change to what a principal agreed to.

**`default_decision`, ordinally, always material when it widens.** Ranked
DENY (0) < REQUIRE_CONSENT (1) < ALLOW (2) - the same three tiers a triple
with no explicit rule sits across, since REQUIRE_CONSENT's ordinary
consent-gated flow is exactly tier 1 above. Moving right widens what every
un-ruled triple is permitted without a matching rule; moving left narrows it.

**`checklist` is never material** - process metadata, identical reasoning to
the purpose classifier's own `checklist` rule.

**Nothing on a Policy is downgradeable.** Unlike a Purpose's three free-text
fields, nothing on a `PolicyVersion` is prose a machine cannot classify - a
rule's tier and `default_decision`'s rank are both computed facts, not
judgement calls - so `POLICY_OVERRIDABLE_FIELDS` is empty and any attempted
override is refused by `classify_change` itself, the same guard that already
stops a purpose widening being downgraded.

Affected consents are found precisely for a `rules` widening (the exact
triples that widened, joined back to `Consent` by purpose/category/activity
code) and, for a `default_decision` widening, over every live consent whose
triple has no explicit rule in the new version - the only consents actually
governed by the default. Flagging follows the cookie-policy precedent
(`_flag_consents(..., new_pv=None)`: no `PurposeVersion` to re-point, since
none changed) plus a `POLICY_CHANGE_RECONSENT` notification queued directly,
since that path does not go through `services/consent.py::
update_consent_for_purpose_version` (which is purpose-specific and pins a
`consent_text` a `PolicyVersion` does not have).
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, tuple_
from sqlalchemy.orm import Session

from app.models.entities import (
    Consent,
    CrmCustomer,
    Customer,
    DataCategory,
    Policy,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
)
from app.models.reconsent import (
    CAMPAIGN_STATUSES,
    CHANGE_ENTITY_TYPES,
    CookiePolicyVersion,
    PolicyChangeLog,
    ReConsentCampaign,
)
from app.services.audit import log_audit

logger = logging.getLogger("app.reconsent")

#: Consent statuses a re-consent campaign flags. A withdrawn, denied or
#: expired consent is already not a basis for processing, so flagging it would
#: inflate K-09's denominator with people who have nothing to re-consent to.
FLAGGABLE_CONSENT_STATUSES = ("GRANTED", "ACTIVE", "RENEWED", "UPDATED")

#: The three free-text fields a publisher may downgrade from MATERIAL to
#: COSMETIC with a recorded justification. Nothing else is downgradeable -
#: see the module docstring.
OVERRIDABLE_FIELDS = ("description", "services_enabled", "consent_text")


class MaterialityError(ValueError):
    """Raised when a publisher tries to downgrade something that is not
    downgradeable."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ref(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6).upper()}"


# ---------------------------------------------------------------------------
# The definition
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldRule:
    field: str
    #: "widening" | "increase" | "any" | "false_to_true" | "text" | "never"
    #: | "ordinal" | "policy_rules" - the last two are POLICY_FIELD_RULES'
    #: own kinds, added for P-02; see that constant's own comments.
    kind: str
    why_material: str
    why_narrowing: str = ""
    #: "ordinal" only: {value: rank}, higher rank = more permissive/widening.
    ordinal: Optional[dict] = None


FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "data_category_ids", "widening",
        "a data category was added, so personal data is now collected that the principal "
        "never saw listed (DPDP Act s.6(1): consent is to a specified purpose; s.5 notice "
        "must itemise the personal data)",
        "only removals: the existing consent already covered a superset of this data",
    ),
    FieldRule(
        "processing_activity_ids", "widening",
        "a processing activity was added, so something new is now done with the same data "
        "(DPDP Act s.6(1) 'for the specified purpose')",
        "only removals: the existing consent already covered a superset of these activities",
    ),
    FieldRule(
        "data_items", "widening",
        "the itemised data list changed - an item was added, or an existing item's necessity "
        "was re-stated - and that list is what the principal actually read (A-01)",
        "only removals: the itemised list is a strict subset of what was consented to",
    ),
    FieldRule(
        "retention_period_days", "increase",
        "the retention period was extended, so the data will be held longer than was "
        "disclosed (DPDP Act s.8(7) makes the period part of the bargain)",
        "the retention period was shortened, which asks nothing further of the principal",
    ),
    FieldRule(
        "legal_basis", "any",
        "the lawful basis changed, so what authorises this processing is no longer what the "
        "principal was told (DPDP Act s.4 read with s.6/s.7)",
    ),
    FieldRule(
        "requires_consent", "false_to_true",
        "consent is now required where it previously was not, so there is no prior consent to "
        "rely on",
        "consent is no longer required, which asks nothing further of the principal",
    ),
    FieldRule(
        "child_restricted", "false_to_true",
        "the purpose is now declared as involving children, so DPDP Act s.9's stricter regime "
        "(verifiable parental consent, no behavioural advertising, no tracking) applies and a "
        "consent given before that declaration cannot have accounted for it",
        "the child restriction was lifted, which relaxes rather than widens the processing",
    ),
    FieldRule(
        "description", "text",
        "the purpose description a principal read when consenting was rewritten",
    ),
    FieldRule(
        "services_enabled", "text",
        "the specific description of the goods, services or uses this processing enables was "
        "rewritten (A-02) - which is the substance of what the principal agreed to, not a label",
    ),
    FieldRule(
        "consent_text", "text",
        "the consent statement the principal actually agreed to was rewritten",
    ),
    FieldRule("name", "never", ""),
    FieldRule("translations", "never", ""),
    FieldRule("checklist", "never", ""),
)

def _as_id_set(value) -> set:
    return set(value or [])


def _data_item_signature(items) -> dict:
    """An itemised list reduced to {data_category_id: necessity}, so a
    reordering of the same items is not mistaken for a change and a change of
    necessity on one item is not missed."""
    out = {}
    for item in items or []:
        if isinstance(item, dict):
            out[str(item.get("data_category_id"))] = item.get("necessity")
    return out


def _classify_field(rule: "FieldRule", before, after) -> Optional[dict]:
    """Classify one field's change, or return None when it did not change.

    Takes the `FieldRule` itself rather than a field name looked up from a
    single global table, so `classify_change` can be handed a different
    field vocabulary (`POLICY_FIELD_RULES`) without the two vocabularies
    colliding on field names in one shared dict.
    """
    if rule.kind == "widening":
        if rule.field == "data_items":
            b, a = _data_item_signature(before), _data_item_signature(after)
            if b == a:
                return None
            widened = any(k not in b or b[k] != v for k, v in a.items())
        else:
            b, a = _as_id_set(before), _as_id_set(after)
            if b == a:
                return None
            widened = bool(a - b)
        return {
            "from": before, "to": after,
            "materiality": "MATERIAL" if widened else "NARROWING",
            "why": rule.why_material if widened else rule.why_narrowing,
        }

    if rule.kind == "increase":
        if before == after:
            return None
        increased = (after or 0) > (before or 0)
        return {
            "from": before, "to": after,
            "materiality": "MATERIAL" if increased else "NARROWING",
            "why": rule.why_material if increased else rule.why_narrowing,
        }

    if rule.kind == "any":
        if before == after:
            return None
        return {"from": before, "to": after, "materiality": "MATERIAL", "why": rule.why_material}

    if rule.kind == "false_to_true":
        if bool(before) == bool(after):
            return None
        material = bool(after) and not bool(before)
        return {
            "from": before, "to": after,
            "materiality": "MATERIAL" if material else "NARROWING",
            "why": rule.why_material if material else rule.why_narrowing,
        }

    if rule.kind == "text":
        if (before or "") == (after or ""):
            return None
        return {
            "from": before, "to": after, "materiality": "MATERIAL", "why": rule.why_material,
            "overridable": True,
        }

    if rule.kind == "ordinal":
        if before == after:
            return None
        order = rule.ordinal or {}
        widened = order.get(after, 0) > order.get(before, 0)
        return {
            "from": before, "to": after,
            "materiality": "MATERIAL" if widened else "NARROWING",
            "why": rule.why_material if widened else rule.why_narrowing,
        }

    if rule.kind == "policy_rules":
        return _classify_policy_rules(before or [], after or [])

    # "never"
    if before == after:
        return None
    return {
        "from": before, "to": after, "materiality": "COSMETIC",
        "why": (
            f"'{rule.field}' is presentation or process metadata, not the scope of processing, "
            f"so a change to it does not alter what the principal agreed to"
        ),
    }


def classify_change(
    before: dict,
    after: dict,
    *,
    cosmetic_overrides: Optional[dict[str, str]] = None,
    field_rules: tuple["FieldRule", ...] = FIELD_RULES,
    overridable_fields: tuple[str, ...] = OVERRIDABLE_FIELDS,
) -> dict:
    """The definition, applied. Returns
    ``{"materiality", "changed_fields", "basis", "overridden"}``.

    `cosmetic_overrides` maps a field name to the publisher's justification
    for downgrading it. Only the three free-text fields may be downgraded; an
    attempt on anything else raises `MaterialityError` rather than being
    silently ignored, because a publisher who believes a widening is cosmetic
    needs to be told they are wrong, not to have their opinion discarded
    without comment.

    `field_rules`/`overridable_fields` default to the purpose vocabulary
    (`FIELD_RULES`/`OVERRIDABLE_FIELDS`); `publish_policy_change` passes
    `POLICY_FIELD_RULES`/`POLICY_OVERRIDABLE_FIELDS` instead, so the same
    engine classifies a Policy's own, different fields rather than a second
    copy of it being built for them.
    """
    overrides = cosmetic_overrides or {}
    for field in overrides:
        if field not in overridable_fields:
            allowed = ", ".join(overridable_fields) if overridable_fields else "none - nothing here is free text"
            raise MaterialityError(
                f"'{field}' cannot be downgraded to cosmetic. Only {allowed} "
                f"are free text a machine cannot judge; every other field's materiality is "
                f"decided by the direction of the change (a category or activity added, a "
                f"retention period extended, the lawful basis changed), which is not a matter "
                f"of opinion. See app/services/material_change.py."
            )

    changed: dict[str, dict] = {}
    for rule in field_rules:
        entry = _classify_field(rule, before.get(rule.field), after.get(rule.field))
        if entry is None:
            continue
        if rule.field in overrides and entry.get("overridable"):
            entry["materiality"] = "COSMETIC"
            entry["overridden"] = True
            entry["override_justification"] = overrides[rule.field]
            entry["why"] = (
                f"Classified MATERIAL by default ({entry['why']}), downgraded to cosmetic by the "
                f"publisher: {overrides[rule.field]}"
            )
        changed[rule.field] = entry

    if any(e["materiality"] == "MATERIAL" for e in changed.values()):
        materiality = "MATERIAL"
    elif any(e["materiality"] == "NARROWING" for e in changed.values()):
        materiality = "NARROWING"
    else:
        materiality = "COSMETIC"

    if not changed:
        basis = "No classified field changed between these two versions."
    else:
        basis = "; ".join(
            f"{field}: {entry['materiality']}"
            + (f" - {entry['why']}" if entry["why"] else "")
            for field, entry in changed.items()
        )
    return {
        "materiality": materiality,
        "changed_fields": changed,
        "basis": basis,
        "overridden": bool(overrides),
    }


def purpose_version_snapshot(pv: PurposeVersion) -> dict:
    """The classified fields of one PurposeVersion, as a plain dict."""
    return {
        "name": pv.name,
        "description": pv.description,
        "legal_basis": pv.legal_basis,
        "requires_consent": pv.requires_consent,
        "retention_period_days": pv.retention_period_days,
        "data_category_ids": list(pv.data_category_ids or []),
        "processing_activity_ids": list(pv.processing_activity_ids or []),
        "data_items": list(pv.data_items or []),
        "services_enabled": pv.services_enabled,
        "child_restricted": pv.child_restricted,
        "consent_text": pv.consent_text,
        "translations": pv.translations,
        "checklist": pv.checklist,
    }


# ---------------------------------------------------------------------------
# P-02: the same definition, applied to a Policy - see the module docstring's
# "THE SAME DEFINITION, APPLIED TO A POLICY" section for the reasoning.
# ---------------------------------------------------------------------------

#: Ordinal permissiveness of a Policy's `default_decision` - the posture
#: applied to every (purpose, data category, processing activity) triple no
#: rule names. DENY is the most protective a policy can declare, ALLOW the
#: least; REQUIRE_CONSENT is the ordinary consent-gated flow, which is also
#: what a triple with no rule at all already gets (see
#: POLICY_RULE_BASELINE_TIER below) - moving right widens, left narrows.
POLICY_DECISION_ORDER = {"DENY": 0, "REQUIRE_CONSENT": 1, "ALLOW": 2}

#: The tier a (purpose_code, data_category_code, processing_activity_code)
#: triple sits at, mirroring the order services/decision_engine.py::
#: evaluate_decision actually checks a rule in:
#:   0 DENY                               - blocked outright, consent moot.
#:   1 no rule, or ALLOW + requires_active - the ordinary consent-gated flow
#:     _consent=True (the default)          (find_applicable_rule returning
#:                                           nothing behaves identically).
#:   2 ALLOW + requires_active_consent=   - always permitted, no consent
#:     False                                 check reached at all.
#: `priority` plays no part: find_applicable_rule matches a triple and
#: returns the first entry it iterates to without sorting by priority, so a
#: priority-only difference changes nothing this platform evaluates.
POLICY_RULE_BASELINE_TIER = 1


def _policy_rule_tier(rule: Optional[dict]) -> int:
    """Where a rule (or the absence of one, `rule=None`) sits on the
    permissiveness scale `_classify_policy_rules` compares."""
    if not rule:
        return POLICY_RULE_BASELINE_TIER
    if rule.get("decision") == "DENY":
        return 0
    return 2 if rule.get("requires_active_consent", True) is False else 1


def _policy_rule_key(rule: dict) -> tuple:
    return (
        rule.get("purpose_code"), rule.get("data_category_code"),
        rule.get("processing_activity_code"),
    )


def _policy_rules_by_key(rules) -> dict:
    """`rules` reduced to ``{(purpose_code, data_category_code,
    processing_activity_code): rule}``, first match wins - mirrors
    services/decision_engine.py::find_applicable_rule, which returns the
    first rule it iterates to for a triple and never sorts by priority."""
    out: dict = {}
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        key = _policy_rule_key(rule)
        if key not in out:
            out[key] = rule
    return out


def _classify_policy_rules(before: list, after: list) -> Optional[dict]:
    """The `rules` field's own widening/narrowing test, one triple at a
    time - the rule engine's equivalent of comparing `data_category_ids`
    item by item rather than as an opaque blob.

    A triple is MATERIAL when it moves to a higher permissiveness tier (a
    DENY lifted, or a rule newly bypassing the active-consent check):
    something is now permitted that an existing consent, given against the
    old and more restrictive rule, does not cover. It is a NARROWING when it
    moves to a lower tier - the existing consent already covers the smaller
    permission. Two rule dicts that differ (e.g. only in `priority`) without
    changing tier are COSMETIC: a change nothing this platform evaluates
    differently is not a change to what a principal agreed to.

    Returns None only when no triple's rule actually changed (an identical
    `rules` list, in any order).
    """
    before_by_key = _policy_rules_by_key(before)
    after_by_key = _policy_rules_by_key(after)
    keys = set(before_by_key) | set(after_by_key)

    per_triple: dict[str, dict] = {}
    widened_triples: list[tuple] = []
    for key in sorted(keys, key=lambda k: tuple(str(part) for part in k)):
        b, a = before_by_key.get(key), after_by_key.get(key)
        if b == a:
            continue
        before_tier, after_tier = _policy_rule_tier(b), _policy_rule_tier(a)
        if after_tier > before_tier:
            bucket = "MATERIAL"
            widened_triples.append(key)
        elif after_tier < before_tier:
            bucket = "NARROWING"
        else:
            bucket = "COSMETIC"
        per_triple["|".join(str(part) for part in key)] = {
            "purpose_code": key[0], "data_category_code": key[1],
            "processing_activity_code": key[2],
            "from": b, "to": a, "materiality": bucket,
        }

    if not per_triple:
        return None

    if any(t["materiality"] == "MATERIAL" for t in per_triple.values()):
        materiality = "MATERIAL"
    elif any(t["materiality"] == "NARROWING" for t in per_triple.values()):
        materiality = "NARROWING"
    else:
        materiality = "COSMETIC"

    if materiality == "MATERIAL":
        why = (
            "rule(s) for "
            + ", ".join(
                f"{t['purpose_code']}/{t['data_category_code']}/{t['processing_activity_code']}"
                for t in per_triple.values() if t["materiality"] == "MATERIAL"
            )
            + " now permit processing more than before (an explicit DENY was lifted, or the "
            "active-consent requirement was dropped) - the existing consent, given against the "
            "old and more restrictive rule, does not cover it"
        )
    elif materiality == "NARROWING":
        why = (
            "rule(s) changed to permit processing less than before (a DENY added, or the "
            "active-consent requirement reinstated), which the existing consent already covers"
        )
    else:
        why = (
            "the rule list changed with no effect on what is permitted (e.g. only `priority`, "
            "which services/decision_engine.py::find_applicable_rule does not consult)"
        )

    return {
        "from": before, "to": after, "materiality": materiality, "why": why,
        "per_triple": per_triple, "widened_triples": widened_triples,
    }


#: Policy's own field vocabulary for `classify_change`. Deliberately not
#: merged into FIELD_RULES: a Policy has no `name`/`description`/prose
#: fields FIELD_RULES's other entries assume, and giving "checklist" (the
#: one field the two entities share) its own tuple here keeps each
#: vocabulary independently readable rather than one list serving two
#: unrelated entities by convention.
POLICY_FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule(
        "rules", "policy_rules",
        "a rule now permits processing more than before at the policy's own grain - see "
        "_classify_policy_rules for the per-triple reasoning actually used",
    ),
    FieldRule(
        "default_decision", "ordinal",
        "the policy's default posture for every triple with no explicit rule moved toward "
        "ALLOW: that processing used to fall through to the ordinary consent-gated flow (or be "
        "blocked outright) and now proceeds without one",
        "the default posture moved toward DENY, which asks nothing further of a principal whose "
        "existing consent already covers the narrower outcome",
        ordinal=POLICY_DECISION_ORDER,
    ),
    FieldRule("checklist", "never", ""),
)

#: Nothing on a PolicyVersion is free text a machine cannot classify - a
#: rule's tier and default_decision's rank are both computed, not judged -
#: so no field may be downgraded. Empty, not absent: `classify_change`
#: refuses any override named against this tuple, the same guard that
#: already stops a purpose widening being downgraded (see the module
#: docstring's "None of these can be downgraded by a publisher").
POLICY_OVERRIDABLE_FIELDS: tuple[str, ...] = ()


def policy_version_snapshot(pv: PolicyVersion) -> dict:
    """The classified fields of one PolicyVersion, as a plain dict."""
    return {
        "rules": [dict(r) for r in (pv.rules or []) if isinstance(r, dict)],
        "default_decision": pv.default_decision,
        "checklist": pv.checklist,
    }


def _policy_affected_consents(
    db: Session, policy: Policy, changed_fields: dict, new_pv: PolicyVersion,
) -> list[Consent]:
    """Every live consent a MATERIAL policy change actually affects.

    Two independent sources, unioned:

    * A `rules` widening affects exactly the triples that widened - found by
      joining Consent back to Purpose/DataCategory/ProcessingActivity on
      their codes, the same join `evaluate_decision` itself does via
      `find_applicable_rule`.
    * A `default_decision` widening affects every live consent whose triple
      has NO explicit rule in the new version - the only consents actually
      governed by the default, computed from the new version's own `rules`
      rather than assumed, so a policy with an explicit rule for every triple
      correctly affects none.

    Not filtered by `policy.tenant_id`: a Policy sits on the platform
    tenant exactly like a Purpose does (both created with
    `tenant_id=platform_tenant_id(db)`), so it is shared across every demo
    tenant that uses it, while a Consent belongs to whichever tenant its own
    `source_app` resolved to. `publish_purpose_change` does not filter by
    tenant either, for the same reason - the purpose/category/activity
    triple already identifies exactly what changed; adding a tenant filter
    would silently exclude every non-platform tenant's consents from a
    platform-wide policy change.
    """
    consents_by_id: dict[int, Consent] = {}
    base = (
        db.query(Consent)
        .join(Purpose, Consent.purpose_id == Purpose.id)
        .join(DataCategory, Consent.data_category_id == DataCategory.id)
        .join(ProcessingActivity, Consent.processing_activity_id == ProcessingActivity.id)
        .filter(
            Consent.status.in_(FLAGGABLE_CONSENT_STATUSES),
            Consent.re_consent_required.is_(False),
        )
    )

    rules_entry = changed_fields.get("rules")
    triples = rules_entry.get("widened_triples") if rules_entry else None
    if triples:
        for consent in base.filter(
            tuple_(Purpose.code, DataCategory.code, ProcessingActivity.code).in_(
                [tuple(t) for t in triples]
            )
        ).all():
            consents_by_id[consent.id] = consent

    default_entry = changed_fields.get("default_decision")
    if default_entry and default_entry.get("materiality") == "MATERIAL":
        covered = {
            (r.get("purpose_code"), r.get("data_category_code"), r.get("processing_activity_code"))
            for r in (new_pv.rules or []) if isinstance(r, dict)
        }
        rows = (
            db.query(Consent, Purpose.code, DataCategory.code, ProcessingActivity.code)
            .select_from(Consent)
            .join(Purpose, Consent.purpose_id == Purpose.id)
            .join(DataCategory, Consent.data_category_id == DataCategory.id)
            .join(ProcessingActivity, Consent.processing_activity_id == ProcessingActivity.id)
            .filter(
                Consent.status.in_(FLAGGABLE_CONSENT_STATUSES),
                Consent.re_consent_required.is_(False),
            )
        )
        for consent, p_code, dc_code, pa_code in rows.all():
            if (p_code, dc_code, pa_code) not in covered:
                consents_by_id[consent.id] = consent

    return list(consents_by_id.values())


def _notify_policy_change(
    db: Session, consents: list[Consent], policy: Policy, new_pv: PolicyVersion, *,
    actor_username: str, source_app: str, request_id: Optional[str],
) -> int:
    """Queue a POLICY_CHANGE_RECONSENT notification per affected principal.

    A parallel, smaller version of what `services/consent.py::
    update_consent_for_purpose_version` does for a purpose change: that
    function cannot be reused here (it re-points `purpose_version_id` and
    pins a `consent_text` from the new PurposeVersion, neither of which
    applies to a Policy change), so this queues the notification directly
    with `services/notifications.py::queue_notification` - existing
    machinery, just called one level up instead of through that function.
    """
    from app.services.notifications import queue_notification

    queued = 0
    for consent in consents:
        try:
            before = _notification_count(db, consent.customer_id, "POLICY_CHANGE_RECONSENT")
            queue_notification(
                db, customer=consent.customer, event_type="POLICY_CHANGE_RECONSENT",
                source_app=consent.source_app or source_app,
                context={
                    "policy_code": policy.code, "policy_name": policy.name,
                    "new_policy_version": new_pv.version_number,
                },
                request_id=request_id, actor_username=actor_username,
            )
            queued += max(
                _notification_count(db, consent.customer_id, "POLICY_CHANGE_RECONSENT") - before, 0
            )
        except Exception:  # noqa: BLE001 - one undeliverable notice must not stop the campaign
            logger.exception(
                "Failed to queue POLICY_CHANGE_RECONSENT for consent_id=%s", consent.id
            )
    db.commit()
    return queued


def publish_policy_change(
    db: Session,
    policy: Policy,
    old_pv: PolicyVersion,
    new_pv: PolicyVersion,
    *,
    actor_username: str = "system",
    source_app: str = "UI",
    request_id: Optional[str] = None,
    cosmetic_overrides: Optional[dict[str, str]] = None,
) -> dict:
    """Classify a policy version publication, log it, and - when it is
    material - start the re-consent campaign. P-02's fix: this used to not
    exist at all, so `PUT /policies/{id}` versioned the rule engine with no
    change record and no re-consent, however material the change.

    Called from `routes/policies.py` immediately after a new PolicyVersion is
    made current, exactly where `publish_purpose_change` is called from
    `routes/purposes.py`. Returns the same shape that function does, echoed
    back to the publisher on the response to their own edit.
    """
    classification = classify_change(
        policy_version_snapshot(old_pv), policy_version_snapshot(new_pv),
        cosmetic_overrides=cosmetic_overrides,
        field_rules=POLICY_FIELD_RULES, overridable_fields=POLICY_OVERRIDABLE_FIELDS,
    )
    change = _log_change(
        db, entity_type="POLICY", entity_id=policy.id, entity_code=policy.code,
        from_version=old_pv.version_number, to_version=new_pv.version_number,
        classification=classification, tenant_id=policy.tenant_id,
        actor_username=actor_username, source_app=source_app, request_id=request_id,
        cosmetic_overrides=cosmetic_overrides,
    )

    if classification["materiality"] != "MATERIAL":
        db.commit()
        return {
            "change_ref": change.change_ref,
            "materiality": classification["materiality"],
            "materiality_basis": classification["basis"],
            "campaign_ref": None,
            "consents_flagged": 0,
            "notifications_queued": 0,
        }

    campaign = ReConsentCampaign(
        campaign_ref=_ref("RCC"),
        tenant_id=policy.tenant_id,
        entity_type="POLICY",
        entity_id=policy.id,
        entity_code=policy.code,
        from_version=old_pv.version_number,
        to_version=new_pv.version_number,
        status="OPEN",
        reason=classification["basis"],
        started_by=actor_username,
        started_at=utcnow(),
        source_app=source_app,
        request_id=request_id,
        details={"changed": sorted(classification["changed_fields"])},
    )
    db.add(campaign)
    db.flush()
    change.campaign_id = campaign.id

    consents = _policy_affected_consents(db, policy, classification["changed_fields"], new_pv)
    log_audit(
        db, "RE_CONSENT_CAMPAIGN_STARTED", actor_username=actor_username, source_app=source_app,
        tenant_id=policy.tenant_id,
        reason=(
            f"Material change to policy {policy.code} v{old_pv.version_number} -> "
            f"v{new_pv.version_number}: {len(consents)} consent(s) require fresh consent before "
            f"processing may continue"
        ),
        request_id=request_id,
        metadata={"campaign_ref": campaign.campaign_ref, "change_ref": change.change_ref,
                  "consents": len(consents)},
        commit=False,
    )
    db.commit()

    flagged, _ = _flag_consents(
        db, consents, campaign, actor_username=actor_username, request_id=request_id,
        new_pv=None,
    )
    queued = _notify_policy_change(
        db, consents, policy, new_pv, actor_username=actor_username, source_app=source_app,
        request_id=request_id,
    )
    campaign = db.merge(campaign)
    campaign.consents_flagged = flagged
    campaign.notifications_queued = queued
    change = db.merge(change)
    change.affected_consents = flagged
    db.commit()

    return {
        "change_ref": change.change_ref,
        "materiality": "MATERIAL",
        "materiality_basis": classification["basis"],
        "campaign_ref": campaign.campaign_ref,
        "consents_flagged": flagged,
        "notifications_queued": queued,
    }


# ---------------------------------------------------------------------------
# Publishing a change
# ---------------------------------------------------------------------------
def _log_change(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    entity_code: str,
    from_version: Optional[int],
    to_version: int,
    classification: dict,
    tenant_id: Optional[int],
    actor_username: str,
    source_app: str = "",
    request_id: Optional[str] = None,
    cosmetic_overrides: Optional[dict[str, str]] = None,
    purpose_id: Optional[int] = None,
    purpose_code: Optional[str] = None,
) -> PolicyChangeLog:
    if entity_type not in CHANGE_ENTITY_TYPES:
        raise ValueError(f"Unknown change entity type: {entity_type}")
    overrides = cosmetic_overrides or {}
    row = PolicyChangeLog(
        change_ref=_ref("PCL"),
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_code=entity_code,
        from_version=from_version,
        to_version=to_version,
        materiality=classification["materiality"],
        changed_fields=classification["changed_fields"],
        materiality_basis=classification["basis"],
        overridden_by=actor_username if overrides else None,
        override_justification="; ".join(f"{k}: {v}" for k, v in overrides.items()),
        actor_username=actor_username,
        source_app=source_app,
        request_id=request_id,
    )
    db.add(row)
    db.flush()
    log_audit(
        db, "POLICY_CHANGE_LOGGED", actor_username=actor_username, source_app=source_app,
        tenant_id=tenant_id,
        # Carried on the audit row for a PURPOSE change so "show me everything
        # that happened to this purpose" is one query - the same fields the
        # PURPOSE_UPDATED row routes/purposes.py writes already sets.
        purpose_id=purpose_id, purpose_code=purpose_code,
        reason=(
            f"{entity_type} {entity_code} v{from_version} -> v{to_version} classified "
            f"{classification['materiality']}"
        ),
        request_id=request_id,
        metadata={
            "change_ref": row.change_ref, "entity_type": entity_type, "entity_code": entity_code,
            "from_version": from_version, "to_version": to_version,
            "materiality": classification["materiality"],
            "changed": sorted(classification["changed_fields"]),
            "basis": classification["basis"][:900],
        },
        commit=False,
    )
    return row


def _flag_consents(
    db: Session,
    consents: list[Consent],
    campaign: ReConsentCampaign,
    *,
    actor_username: str,
    request_id: Optional[str],
    new_pv: Optional[PurposeVersion] = None,
) -> tuple[int, int]:
    """Flag every affected consent and tell its principal. Returns
    ``(flagged, notifications_queued)``.

    The consent is re-pointed at the new version through
    `services/consent.py::update_consent_for_purpose_version`, which writes
    the ConsentHistory row and queues the PURPOSE_CHANGE_RECONSENT
    notification - the function P-01 notes has existed and been unused since
    it was written. Re-pointing alone would be the "consent cannot be assumed"
    failure, so the flag is set in the same unit of work and is what actually
    blocks processing.
    """
    from app.services.consent import update_consent_for_purpose_version

    now = utcnow()
    flagged = 0
    queued = 0
    for consent in consents:
        consent.re_consent_required = True
        consent.re_consent_requested_at = now
        consent.re_consent_campaign_id = campaign.id
        flagged += 1
        db.flush()
        if new_pv is not None:
            try:
                before = _notification_count(db, consent.customer_id)
                update_consent_for_purpose_version(
                    db, consent, new_pv,
                    reason=(
                        f"Material change published (campaign {campaign.campaign_ref}): fresh "
                        f"consent required before processing continues"
                    ),
                    actor_username=actor_username, source_app=consent.source_app,
                    request_id=request_id,
                )
                queued += max(_notification_count(db, consent.customer_id) - before, 0)
            except Exception:  # noqa: BLE001 - one principal's notification must not stop the campaign
                db.rollback()
                logger.exception(
                    "Failed to re-point and notify consent_id=%s for campaign %s; the flag is "
                    "re-applied below so processing stays blocked either way",
                    consent.id, campaign.campaign_ref,
                )
                consent = db.merge(consent)
                consent.re_consent_required = True
                consent.re_consent_requested_at = now
                consent.re_consent_campaign_id = campaign.id
                db.flush()
        log_audit(
            db, "RE_CONSENT_REQUIRED", actor_username=actor_username,
            source_app=consent.source_app, tenant_id=consent.tenant_id,
            customer_id=consent.customer_id, consent_id=consent.id,
            purpose_id=consent.purpose_id,
            reason=(
                f"Processing blocked pending fresh consent: campaign {campaign.campaign_ref}"
            ),
            request_id=request_id,
            metadata={"campaign_ref": campaign.campaign_ref, "consent_id": consent.id},
            commit=False,
        )
    db.commit()
    return flagged, queued


def _notification_count(
    db: Session, customer_id: int, event_type: str = "PURPOSE_CHANGE_RECONSENT",
) -> int:
    from app.models.entities import Notification

    return (
        db.query(func.count(Notification.id))
        .filter(
            Notification.customer_id == customer_id,
            Notification.event_type == event_type,
        )
        .scalar()
        or 0
    )


def publish_purpose_change(
    db: Session,
    purpose: Purpose,
    old_pv: PurposeVersion,
    new_pv: PurposeVersion,
    *,
    actor_username: str = "system",
    source_app: str = "UI",
    request_id: Optional[str] = None,
    cosmetic_overrides: Optional[dict[str, str]] = None,
) -> dict:
    """Classify a purpose version publication, log it, and - when it is
    material - start the re-consent campaign.

    Called from `routes/purposes.py` immediately after a new PurposeVersion is
    made current. Returns a summary the route echoes back so a publisher sees,
    in the response to their own edit, that they have just required N
    principals to re-consent.
    """
    classification = classify_change(
        purpose_version_snapshot(old_pv), purpose_version_snapshot(new_pv),
        cosmetic_overrides=cosmetic_overrides,
    )
    change = _log_change(
        db, entity_type="PURPOSE", entity_id=purpose.id, entity_code=purpose.code,
        from_version=old_pv.version_number, to_version=new_pv.version_number,
        classification=classification, tenant_id=purpose.tenant_id,
        actor_username=actor_username, source_app=source_app, request_id=request_id,
        cosmetic_overrides=cosmetic_overrides,
        purpose_id=purpose.id, purpose_code=purpose.code,
    )

    if classification["materiality"] != "MATERIAL":
        db.commit()
        return {
            "change_ref": change.change_ref,
            "materiality": classification["materiality"],
            "materiality_basis": classification["basis"],
            "campaign_ref": None,
            "consents_flagged": 0,
            "notifications_queued": 0,
        }

    campaign = ReConsentCampaign(
        campaign_ref=_ref("RCC"),
        tenant_id=purpose.tenant_id,
        entity_type="PURPOSE",
        entity_id=purpose.id,
        entity_code=purpose.code,
        from_version=old_pv.version_number,
        to_version=new_pv.version_number,
        status="OPEN",
        reason=classification["basis"],
        started_by=actor_username,
        started_at=utcnow(),
        source_app=source_app,
        request_id=request_id,
        details={"changed": sorted(classification["changed_fields"])},
    )
    db.add(campaign)
    db.flush()
    change.campaign_id = campaign.id

    consents = (
        db.query(Consent)
        .filter(
            Consent.purpose_id == purpose.id,
            Consent.status.in_(FLAGGABLE_CONSENT_STATUSES),
            Consent.re_consent_required.is_(False),
        )
        .all()
    )
    log_audit(
        db, "RE_CONSENT_CAMPAIGN_STARTED", actor_username=actor_username, source_app=source_app,
        tenant_id=purpose.tenant_id, purpose_id=purpose.id, purpose_code=purpose.code,
        reason=(
            f"Material change to purpose {purpose.code} v{old_pv.version_number} -> "
            f"v{new_pv.version_number}: {len(consents)} consent(s) require fresh consent before "
            f"processing may continue"
        ),
        request_id=request_id,
        metadata={"campaign_ref": campaign.campaign_ref, "change_ref": change.change_ref,
                  "consents": len(consents)},
        commit=False,
    )
    db.commit()

    flagged, queued = _flag_consents(
        db, consents, campaign, actor_username=actor_username, request_id=request_id,
        new_pv=new_pv,
    )
    campaign = db.merge(campaign)
    campaign.consents_flagged = flagged
    campaign.notifications_queued = queued
    change = db.merge(change)
    change.affected_consents = flagged
    db.commit()

    return {
        "change_ref": change.change_ref,
        "materiality": "MATERIAL",
        "materiality_basis": classification["basis"],
        "campaign_ref": campaign.campaign_ref,
        "consents_flagged": flagged,
        "notifications_queued": queued,
    }


# ---------------------------------------------------------------------------
# Clearing the flag: only a fresh, affirmative act
# ---------------------------------------------------------------------------
def clear_re_consent(
    db: Session, consent: Consent, *, actor_username: str = "system",
    request_id: Optional[str] = None,
) -> bool:
    """Record that fresh consent has been given for a flagged consent.

    Called from `grant_consent` and `renew_consent` only - the two transitions
    that represent the principal actually agreeing again. Re-pointing a
    consent at a new purpose version does NOT clear it, and neither does a
    staff user editing the row: that would be assuming the very consent this
    machinery exists to obtain (BRD 4.1.3, "consent cannot be assumed").

    Increments the campaign's `fresh_consents` counter, which is K-09's
    numerator.
    """
    if not consent.re_consent_required:
        return False
    campaign_id = consent.re_consent_campaign_id
    consent.re_consent_required = False
    db.flush()
    if campaign_id:
        campaign = db.get(ReConsentCampaign, campaign_id)
        if campaign is not None:
            campaign.fresh_consents = (campaign.fresh_consents or 0) + 1
            db.flush()
    log_audit(
        db, "RE_CONSENT_RECEIVED", actor_username=actor_username, source_app=consent.source_app,
        tenant_id=consent.tenant_id, customer_id=consent.customer_id, consent_id=consent.id,
        purpose_id=consent.purpose_id,
        reason="Fresh consent received after a material change; processing may resume",
        request_id=request_id,
        metadata={"consent_id": consent.id, "campaign_id": campaign_id},
        commit=False,
    )
    return True


# ---------------------------------------------------------------------------
# P-03: cookie policy versioning
# ---------------------------------------------------------------------------
def cookie_policy_hash(categories: list, summary: str) -> str:
    canonical = json.dumps(
        {"categories": categories, "summary": summary},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_cookie_policy(db: Session, tenant_id: int) -> Optional[CookiePolicyVersion]:
    return (
        db.query(CookiePolicyVersion)
        .filter(
            CookiePolicyVersion.tenant_id == tenant_id,
            CookiePolicyVersion.is_current.is_(True),
        )
        .first()
    )


def _cookie_snapshot(version: Optional[CookiePolicyVersion]) -> dict:
    """A cookie policy reduced to the classified-field vocabulary.

    The itemised category list maps onto `data_items` (an itemised disclosure
    whose additions are a widening) and the prose onto `description`, so a
    cookie policy is classified by exactly the same rules as a purpose rather
    than by a second, parallel definition of "material" that could drift from
    it.
    """
    if version is None:
        return {"data_items": [], "description": ""}
    items = []
    for c in version.categories or []:
        if isinstance(c, dict):
            items.append({
                "data_category_id": c.get("key") or c.get("purpose_code"),
                "necessity": json.dumps(
                    {k: v for k, v in sorted(c.items()) if k != "label"},
                    sort_keys=True, default=str,
                ),
            })
    return {"data_items": items, "description": version.summary or ""}


def publish_cookie_policy(
    db: Session,
    *,
    tenant_id: int,
    categories: list,
    summary: str = "",
    actor_username: str = "system",
    source_app: str = "UI",
    request_id: Optional[str] = None,
    cosmetic_overrides: Optional[dict[str, str]] = None,
) -> dict:
    """P-03: publish a new cookie policy version, and - when the change is
    material - invalidate every stored preference it described.

    Invalidation is done server-side by clearing
    `crm_customers.consent_preferences`, so a banner has nothing to read back
    and re-asks. That deliberately requires no cooperation from the four demo
    frontends: a client that never got the memo still re-asks, because the
    server no longer remembers. Clearing the banner's memory alone would not
    be enough, though - the platform's own consent rows would still say
    GRANTED - so the mirrored consents are flagged in the same act and the
    decision engine blocks processing until a fresh choice is recorded.
    """
    previous = current_cookie_policy(db, tenant_id)
    classification = classify_change(
        _cookie_snapshot(previous), _cookie_snapshot_from(categories, summary),
        cosmetic_overrides=cosmetic_overrides,
    )
    next_version = (previous.version_number + 1) if previous else 1

    if previous is not None:
        previous.is_current = False
    version = CookiePolicyVersion(
        tenant_id=tenant_id,
        version_number=next_version,
        categories=categories,
        summary=summary,
        content_hash=cookie_policy_hash(categories, summary),
        is_current=True,
        published_by=actor_username,
        published_at=utcnow(),
    )
    db.add(version)
    db.flush()

    change = _log_change(
        db, entity_type="COOKIE_POLICY", entity_id=version.id,
        entity_code=f"cookie-policy:{tenant_id}",
        from_version=previous.version_number if previous else None,
        to_version=next_version, classification=classification, tenant_id=tenant_id,
        actor_username=actor_username, source_app=source_app, request_id=request_id,
        cosmetic_overrides=cosmetic_overrides,
    )
    version.change_log_id = change.id
    log_audit(
        db, "COOKIE_POLICY_PUBLISHED", actor_username=actor_username, source_app=source_app,
        tenant_id=tenant_id,
        reason=f"Cookie policy v{next_version} published ({classification['materiality']})",
        request_id=request_id,
        metadata={"version": next_version, "content_hash": version.content_hash,
                  "materiality": classification["materiality"], "change_ref": change.change_ref},
        commit=False,
    )
    db.commit()

    result = {
        "version_number": next_version,
        "content_hash": version.content_hash,
        "change_ref": change.change_ref,
        "materiality": classification["materiality"],
        "materiality_basis": classification["basis"],
        "campaign_ref": None,
        "preferences_invalidated": 0,
        "consents_flagged": 0,
    }
    if classification["materiality"] != "MATERIAL":
        return result

    campaign = ReConsentCampaign(
        campaign_ref=_ref("RCC"),
        tenant_id=tenant_id,
        entity_type="COOKIE_POLICY",
        entity_id=version.id,
        entity_code=f"cookie-policy:{tenant_id}",
        from_version=previous.version_number if previous else None,
        to_version=next_version,
        status="OPEN",
        reason=classification["basis"],
        started_by=actor_username,
        started_at=utcnow(),
        source_app=source_app,
        request_id=request_id,
    )
    db.add(campaign)
    db.flush()
    change = db.merge(change)
    change.campaign_id = campaign.id

    purpose_codes = [
        c.get("purpose_code") for c in (categories or [])
        if isinstance(c, dict) and c.get("purpose_code")
    ]
    consents = []
    if purpose_codes:
        consents = (
            db.query(Consent)
            .join(Purpose, Consent.purpose_id == Purpose.id)
            .filter(
                Purpose.code.in_(purpose_codes),
                Consent.tenant_id == tenant_id,
                Consent.status.in_(FLAGGABLE_CONSENT_STATUSES),
                Consent.re_consent_required.is_(False),
            )
            .all()
        )
    db.commit()

    # Invalidate the stored banner preferences for exactly the principals in
    # this tenant, resolved through the Customer rows rather than by clearing
    # the whole shared directory: `crm_customers` is one row per email across
    # all the demo sites (see routes/crm.py), so a blanket clear would reset
    # an unrelated tenant's visitors too.
    invalidated = _invalidate_stored_preferences(db, tenant_id)

    flagged, _ = _flag_consents(
        db, consents, campaign, actor_username=actor_username, request_id=request_id,
        new_pv=None,
    )
    campaign = db.merge(campaign)
    campaign.consents_flagged = flagged
    version = db.merge(version)
    version.preferences_invalidated = invalidated
    change = db.merge(change)
    change.affected_consents = flagged
    db.commit()

    result.update({
        "campaign_ref": campaign.campaign_ref,
        "preferences_invalidated": invalidated,
        "consents_flagged": flagged,
    })
    return result


def _cookie_snapshot_from(categories: list, summary: str) -> dict:
    stub = CookiePolicyVersion(tenant_id=0, version_number=1, categories=categories, summary=summary)
    return _cookie_snapshot(stub)


def _invalidate_stored_preferences(db: Session, tenant_id: int) -> int:
    """Clear `crm_customers.consent_preferences` for this tenant's principals.

    Matched by the HMAC search digest of the Customer row's email, which is
    the same link `routes/crm.py::_find_linked_customer` uses in the other
    direction. A principal with no email on file has no directory row to
    clear, which is correct rather than a gap: the banner stores preferences
    against a logged-in CRM customer, and there is no such row without one.
    """
    from app.core.encryption import hmac_digest

    digests = set()
    for customer in db.query(Customer).filter(Customer.tenant_id == tenant_id).all():
        email = (customer.email or "").strip().lower()
        if email:
            digests.add(hmac_digest(email))
    if not digests:
        return 0
    rows = (
        db.query(CrmCustomer)
        .filter(CrmCustomer.email_search.in_(digests))
        .all()
    )
    cleared = 0
    for row in rows:
        if row.consent_preferences:
            row.consent_preferences = {}
            cleared += 1
    if cleared:
        db.flush()
        log_audit(
            db, "COOKIE_PREFERENCES_INVALIDATED", actor_username="system", tenant_id=tenant_id,
            reason=(
                f"{cleared} stored cookie-preference record(s) invalidated by a material cookie "
                f"policy change; the banner will re-ask (BRD 4.2)"
            ),
            metadata={"cleared": cleared}, commit=False,
        )
        db.commit()
    return cleared


# ---------------------------------------------------------------------------
# Campaign lifecycle and K-09
# ---------------------------------------------------------------------------
def close_campaign(
    db: Session, campaign: ReConsentCampaign, *, status: str = "COMPLETED",
    actor_username: str = "system", reason: str = "", request_id: Optional[str] = None,
) -> ReConsentCampaign:
    if status not in CAMPAIGN_STATUSES or status == "OPEN":
        raise ValueError(f"A campaign can only be closed as COMPLETED or CANCELLED, not {status}")
    now = utcnow()
    campaign.status = status
    if status == "COMPLETED":
        campaign.completed_at = now
    else:
        campaign.cancelled_at = now
        campaign.cancel_reason = reason
        # Cancelling means the change was rolled back or superseded, so the
        # block it imposed must be lifted - leaving principals unable to be
        # processed for a change that no longer exists would be the mirror
        # image of the failure this module fixes.
        for consent in (
            db.query(Consent)
            .filter(Consent.re_consent_campaign_id == campaign.id,
                    Consent.re_consent_required.is_(True))
            .all()
        ):
            consent.re_consent_required = False
    db.flush()
    log_audit(
        db, "RE_CONSENT_CAMPAIGN_CLOSED", actor_username=actor_username,
        source_app=campaign.source_app, tenant_id=campaign.tenant_id,
        reason=f"Campaign {campaign.campaign_ref} closed as {status}. {reason}".strip(),
        request_id=request_id,
        metadata={"campaign_ref": campaign.campaign_ref, "status": status,
                  "consents_flagged": campaign.consents_flagged,
                  "fresh_consents": campaign.fresh_consents},
        commit=False,
    )
    db.commit()
    db.refresh(campaign)
    return campaign


def re_consent_metrics(db: Session) -> dict:
    """K-09: fresh consents divided by consents flagged 're-consent required',
    plus the outstanding block count."""
    campaigns = db.query(ReConsentCampaign).all()
    flagged = sum(c.consents_flagged or 0 for c in campaigns)
    fresh = sum(c.fresh_consents or 0 for c in campaigns)
    outstanding = (
        db.query(func.count(Consent.id)).filter(Consent.re_consent_required.is_(True)).scalar() or 0
    )
    return {
        "generated_at": utcnow(),
        "campaigns": len(campaigns),
        "open_campaigns": sum(1 for c in campaigns if c.status == "OPEN"),
        "consents_flagged": flagged,
        "fresh_consents": fresh,
        # K-09.
        "re_consent_rate_pct": round(fresh / flagged * 100, 2) if flagged else None,
        "consents_blocked_now": outstanding,
        "material_changes": (
            db.query(func.count(PolicyChangeLog.id))
            .filter(PolicyChangeLog.materiality == "MATERIAL").scalar() or 0
        ),
        "changes_logged": db.query(func.count(PolicyChangeLog.id)).scalar() or 0,
    }
