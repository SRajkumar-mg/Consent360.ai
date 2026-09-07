"""Structural guard for the cross-tenant customer-context class of bug.

Three rounds of fixes each closed one call site that looked up a `Customer`
by email or external id with no tenant filter, and each time a sibling
appeared elsewhere within hours - the mistake lives at the call site, not in
any one file. `app/services/tenancy.py::resolve_customer` is the single
choke point every customer resolution in `app/` and `scripts/` now goes
through; it has no default for its tenant scope, so a call site cannot omit
scoping by accident.

This test is the guard that makes that structural fix durable: it scans the
source tree for the raw query shapes `resolve_customer` replaced and fails
if one appears anywhere that is not the choke point's own definition or an
explicitly allow-listed, individually-justified exception. A future call
site that reaches for `Customer.email_search == ...` directly - the exact
mistake every prior round made - trips this test instead of shipping.
"""
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

# \b before "Customer" means these do NOT match "CrmCustomer.email_search"
# (no word boundary between "Crm" and "Customer" - see app/models/entities.py's
# separate, deliberately un-tenanted CrmCustomer directory table) or any other
# *Customer-suffixed identifier; they match only the bare `Customer` model.
PATTERNS = {
    "Customer.email_search ==": re.compile(r"\bCustomer\.email_search\s*=="),
    "Customer.external_id_search ==": re.compile(r"\bCustomer\.external_id_search\s*=="),
    # Any OTHER use of a Customer search-digest column, not just an `==`.
    # The R3-03 HMAC-key-rotation fix replaced the choke point's own
    # `== hmac_digest(...)` comparisons with
    # `find_by_search_digest(query, Customer.email_search, value)` (which
    # matches every retained key's digest and lazily rehashes a hit), so a
    # future call site could now reach around resolve_customer using the
    # new helper and trip none of the `==` patterns above. This one closes
    # that: outside the choke point, naming a Customer *_search column at
    # all - in any spelling - is the mistake.
    "Customer.*_search (any use)": re.compile(r"\bCustomer\.(?:email|external_id)_search\b"),
    "func.lower(Customer.email)": re.compile(r"func\.lower\(Customer\.email\)"),
    "db.get(Customer, ...)": re.compile(r"\bdb\.get\(Customer\b"),
    # Semantically identical to db.get(Customer, ...) - a single-id lookup
    # by primary key spelled as a query+filter instead of Session.get() -
    # but textually invisible to the pattern above. Named by the R3
    # "require a credential and a tenant to purge a customer" review as one
    # of two holes this guard missed.
    "db.query(Customer).filter(Customer.id == ...)": re.compile(
        r"\bdb\.query\(Customer\)\.filter\(\s*Customer\.id\s*=="
    ),
    # A relationship traversal (`<something>.customer.external_id` /
    # `.customer.email`) reads a Customer's identity fields without going
    # through resolve_customer at all - the second textual hole the same
    # review named. The word boundary before "customer" and the required
    # leading "." mean this does NOT match a bare `customer.external_id`
    # where `customer` is itself already a resolve_customer()-produced (or
    # otherwise properly tenant-scoped) local variable - only a two-level
    # chain off some OTHER object (a Consent, a ConsentContext, ...).
    ".customer.external_id / .customer.email": re.compile(r"\.customer\.(external_id|email)\b"),
}

# Every entry here is a conscious exception, not an oversight - each one
# must justify itself. Adding a new entry to silence a future test failure
# without a real, written reason defeats the entire point of this guard.
ALLOWED_FILES = {
    "app/services/tenancy.py": (
        "Defines resolve_customer and external_id_owned_by_another_tenant "
        "themselves - this IS the choke point, not a call site of it. Its "
        "own lookups go through app/core/encryption.py's "
        "find_by_search_digest / search_filter (every retained search-digest "
        "key, plus a lazy rehash) rather than `== hmac_digest(...)`, so what "
        "it matches here is the broader `Customer.*_search (any use)` "
        "pattern, not the `==` ones."
    ),
    "scripts/merge_duplicate_customers.py": (
        "A one-off aggregate/reporting maintenance script, not a "
        "single-identifier request-time resolution: it groups ALL customers "
        "by (lower(email), source_app) to find same-tenant duplicates, and "
        "separately reports (never acts on) emails shared across different "
        "tenants. The merge grouping key includes source_app, so a "
        "cross-tenant 'duplicate' group cannot exist structurally, not "
        "merely by convention. Its db.get(Customer, ...) / "
        "db.query(Customer).filter(Customer.id == ...) calls resolve ids "
        "already produced by that tenant-scoped grouping, never a raw "
        "caller-supplied identifier."
    ),
    "app/api/routes/consents.py": (
        "`consent.customer.external_id` (in `_consent_out` and "
        "`get_consent_detail`) only ever READS the external_id off a "
        "Consent row's already-loaded `customer` relationship, to echo it "
        "back in a response/audit-log field. The Consent itself was already "
        "resolved and tenant-checked earlier in the same function (via "
        "`db.get(Consent, consent_id)` plus an explicit `scope` compare, or "
        "via a caller that already scoped its own Consent query) - this is "
        "not a second, unscoped identity resolution."
    ),
    "app/services/consent.py": (
        "`consent.customer.external_id` in `_record_transition`/history "
        "logging reads the external_id off a Consent object the caller "
        "already holds (passed in as a parameter, already fetched via a "
        "tenant-scoped path upstream) purely to populate the audit-log "
        "row - it is not a resolution path a caller-supplied identifier "
        "flows into."
    ),
    "app/services/erasure.py": (
        "R1-06: the three `db.get(Customer, ...)` calls here "
        "(execute_erasure_job, send_pre_erasure_notice, and the re-fetch after "
        "a rolled-back processor fan-out) resolve a PRIMARY KEY that this "
        "service itself wrote onto an `erasure_jobs` row - "
        "`ErasureJob.customer_id`, an FK - never a caller-supplied identifier. "
        "The Customer was resolved and tenant-checked once, upstream, by "
        "whatever raised the job: `services/consent.py` (which already holds a "
        "tenant-scoped Consent), or `routes/erasure.py::_resolve_principal` "
        "(which goes through resolve_customer with the caller's org scope). "
        "Re-resolving here from an identifier would be the second, unscoped "
        "lookup this guard exists to prevent, not a fix for one - the same "
        "reasoning already allow-listed for scripts/merge_duplicate_customers.py's "
        "id-based lookups. This module never accepts an email or external id at "
        "all: every public entry point takes a `Customer` object or an "
        "`ErasureJob`."
    ),
    "app/services/principal_records.py": (
        "R1-11: every `.customer.external_id` / `.customer.email` here reads "
        "off `PrincipalRecord.customer`, which is exactly the Customer object "
        "app/api/routes/portal.py::_resolve_customer_and_context already "
        "resolved and tenant-checked (via get_customer_from_context plus its "
        "own second source_app check) before calling build_principal_record. "
        "This module never itself looks up a Customer from a caller-supplied "
        "identifier - it only echoes fields off an already-scoped object it "
        "was handed, the identical pattern already allow-listed above for "
        "app/api/routes/consents.py and app/services/consent.py."
    ),
}


def _iter_python_files():
    for base in ("app", "scripts"):
        base_dir = BACKEND_DIR / base
        if not base_dir.exists():
            continue
        yield from base_dir.rglob("*.py")


def test_no_raw_customer_lookup_outside_the_resolve_customer_choke_point():
    violations = []
    for path in _iter_python_files():
        rel = path.relative_to(BACKEND_DIR).as_posix()
        if rel in ALLOWED_FILES:
            continue
        text = path.read_text()
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                violations.append(f"{rel}:{line_no}: raw {label} outside resolve_customer")

    assert not violations, (
        "Found a raw, unscoped Customer lookup outside app/services/tenancy.py::resolve_customer "
        "(the required choke point for resolving a Customer from a caller-supplied identifier - "
        "see its docstring). Route the new call site through resolve_customer(), or if this is a "
        "genuinely justified exception, add it to ALLOWED_FILES here with a written reason:\n"
        + "\n".join(violations)
    )


# --------------------------------------------------------------------------- #
# ANY_TENANT reach must be earned, not defaulted into.
#
# The R3 "require a credential and a tenant to purge a customer" fix found
# ANY_TENANT reachable from a route with NO credential at all:
# crm_directory.py's unauthenticated `DELETE /crm/customers/{id}` called
# purge_customer_by_email_core() without passing a scope, and that
# function's *default* for an unpassed/falsy scope was ANY_TENANT - so a
# reach nobody at that call site consciously chose was silently granted
# anyway. The structural fix was making `scope` a required keyword with no
# default (see purge_customer_by_email_core's docstring); this guard makes
# it durable: every actual USE of the ANY_TENANT sentinel as a value (not a
# mention of it in an import or in prose/comments) must live in a file
# listed below with a written reason covering every occurrence in it.
# --------------------------------------------------------------------------- #

# Matches `= ANY_TENANT`, `or ANY_TENANT`, `else ANY_TENANT` - the shapes
# ANY_TENANT is actually assigned/passed as a value in this codebase today
# - but not a bare import (`import ANY_TENANT, resolve_customer`) or prose
# that merely names it (e.g. "not ANY_TENANT", "the explicit ``ANY_TENANT``
# sentinel").
ANY_TENANT_USAGE = re.compile(r"\b(?:=|or|else)\s*ANY_TENANT\b")

ANY_TENANT_ALLOWED_FILES = {
    "app/api/routes/crm.py": (
        "purge_customer_by_email's call into purge_customer_by_email_core "
        "passes `scope=resolved_key.tenant_code if resolved_key.tenant_code "
        "else ANY_TENANT` - reached only when the caller authenticated with "
        "the legacy, unbound INTEGRATION_API_KEY (tenant_code is None: it "
        "proved no tenant identity to scope to at all). `require_scope` "
        "(app/api/deps.py) now rejects that same legacy key for "
        "SCOPE_CUSTOMER_PURGE unconditionally, so this branch is "
        "unreachable via the API today; it documents the intended blast "
        "radius as defense in depth rather than leaving an undocumented "
        "default, and would only start mattering again if a future change "
        "granted the legacy key that scope - which would need its own "
        "explicit review."
    ),
    "app/api/routes/customers.py": (
        "GET /customers/{customer_id}: `source_app=scope or ANY_TENANT` "
        "where `scope = get_org_scope(user)`. `scope` is `None` only for a "
        "genuinely unscoped staff role (a full platform admin - see "
        "rbac.py's ORG_SCOPE_MAP and app/api/deps.py::get_org_scope); every "
        "org-scoped role (jobhub_admin, codex_admin, ...) always gets its "
        "own real scope string here, never ANY_TENANT. The route itself is "
        "already gated by require_permission(PERM_CUSTOMER_VIEW)."
    ),
    "app/api/routes/consents.py": (
        "Three call sites (get_consent_by_customer / consent history / "
        "customer_summary), each `source_app=scope or ANY_TENANT` with "
        "`scope = get_org_scope(current_user)` - the identical, "
        "already-reviewed staff-admin-fallback pattern as customers.py "
        "above, each behind its own require_permission(...) dependency."
    ),
    "app/api/routes/guardian.py": (
        "R1-14: `_resolve_principal` is the single entry every route in the "
        "module goes through, and it is `source_app=scope or ANY_TENANT` with "
        "`scope = get_org_scope(user)` - the identical staff-admin-fallback "
        "pattern as customers.py above, behind "
        "require_permission(PERM_CONSENT_MANAGE) or PERM_CONSENT_VIEW on "
        "every caller. `scope` is None only for a genuinely unscoped platform "
        "admin; every org-scoped role (jobhub_admin, codex_admin, "
        "skilllearn_admin) always gets its own real scope string, so a "
        "per-application admin cannot record an age assurance or a parental "
        "consent against another tenant's child."
    ),
    "app/api/routes/receipts.py": (
        "list_receipts (staff): `source_app=scope or ANY_TENANT` with "
        "`scope = get_org_scope(user)` - the identical staff-admin-fallback "
        "pattern as customers.py above, behind require_permission(PERM_CONSENT_VIEW), "
        "used only when a customer_external_id filter is also given."
    ),
    "app/api/routes/sharing_events.py": (
        "create_sharing_event and list_sharing_events (staff): each "
        "`source_app=scope or ANY_TENANT` with `scope = get_org_scope(current_user "
        "/ user)` - the identical staff-admin-fallback pattern as customers.py "
        "above, each behind its own require_permission(...) dependency."
    ),
    "app/api/routes/objections.py": (
        "create_objection and list_objections (staff): each "
        "`source_app=scope or ANY_TENANT` with `scope = get_org_scope(current_user "
        "/ user)` - the identical staff-admin-fallback pattern as customers.py "
        "above, each behind its own require_permission(...) dependency."
    ),
    "app/api/routes/notifications.py": (
        "R3-06: trigger_notification and list_notifications (staff): each "
        "`source_app=scope or ANY_TENANT` (or, in trigger_notification, "
        "`resolve_customer(db, source_app=scope or ANY_TENANT, ...)`) with "
        "`scope = get_org_scope(user)` - the identical staff-admin-fallback "
        "pattern as customers.py above, each behind its own "
        "require_permission(...) dependency."
    ),
    "app/api/routes/erasure.py": (
        "R1-06: one call site, `_resolve_principal`, which every route that "
        "takes a `customer_external_id` goes through: "
        "`resolve_customer(db, source_app=scope if scope else ANY_TENANT, "
        "external_id=...)` with `scope = get_org_scope(user)` - the identical "
        "staff-admin-fallback pattern as customers.py above, behind "
        "require_permission(PERM_ERASURE_VIEW / PERM_ERASURE_MANAGE), which are "
        "granted to admin and dpo only. An org-scoped role (jobhub_admin, "
        "codex_admin, skilllearn_admin) always gets its own real scope string "
        "here, never ANY_TENANT, and a wrong-tenant match is a 404 rather than "
        "a 403 so the fallback cannot be used to probe for the existence of "
        "another tenant's external id. The erasure_jobs rows themselves are "
        "separately scoped regardless of how the customer was resolved: every "
        "job route goes through `_scoped_jobs`, which filters on "
        "`ErasureJob.source_app == scope` whenever a scope exists."
    ),
    "app/api/routes/grievances.py": (
        "R2-06: list_grievances and create_grievance_for_customer (staff): "
        "each `resolve_customer(db, source_app=scope or ANY_TENANT, "
        "external_id=...)` with `scope = get_org_scope(user)` - the identical "
        "staff-admin-fallback pattern as customers.py above, each behind its "
        "own require_permission(PERM_GRIEVANCE_VIEW / PERM_GRIEVANCE_MANAGE) "
        "dependency. Note the grievance rows themselves are separately scoped "
        "in both routes regardless of how the customer was resolved: the list "
        "filters on `Grievance.source_app == scope` whenever a scope exists, "
        "and every single-grievance route goes through `_staff_grievance`, "
        "which 404s a grievance outside the caller's scope. The two "
        "self-service routes in the same file resolve their principal from an "
        "X-Context-Token instead and never reach ANY_TENANT at all."
    ),
    "app/api/routes/rights.py": (
        "R2-05: three staff routes - list_requests, "
        "create_request_for_principal and list_nominations - each "
        "`resolve_customer(db, source_app=scope or ANY_TENANT, "
        "external_id=...)` with `scope = get_org_scope(user)`, the identical "
        "staff-admin-fallback pattern as customers.py above, each behind its "
        "own require_permission(PERM_RIGHTS_VIEW / PERM_RIGHTS_MANAGE) "
        "dependency. As in grievances.py, the register rows themselves are "
        "separately scoped regardless of how the customer was resolved: the "
        "two list routes filter on `RightsRequest.source_app == scope` / "
        "`Nomination.source_app == scope` whenever a scope exists, and every "
        "single-record route goes through `_staff_request` / "
        "`_staff_nomination`, which 404 anything outside the caller's scope. "
        "The self-service routes in the same file resolve their principal "
        "from a VERIFIED X-Context-Token and never reach ANY_TENANT at all; "
        "so does `_customer_for`, which resolves by primary key scoped to the "
        "request's own already-checked source_app rather than by a "
        "caller-supplied identifier."
    ),
}


def test_any_tenant_usage_is_allow_listed_with_a_reason():
    violations = []
    for path in _iter_python_files():
        rel = path.relative_to(BACKEND_DIR).as_posix()
        if rel == "app/services/tenancy.py" or rel in ANY_TENANT_ALLOWED_FILES:
            continue
        text = path.read_text()
        for match in ANY_TENANT_USAGE.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            violations.append(f"{rel}:{line_no}: unreviewed ANY_TENANT usage")

    assert not violations, (
        "Found a use of the ANY_TENANT sentinel outside the reviewed, allow-listed call "
        "sites. ANY_TENANT must never be a silent default (see resolve_customer's and "
        "purge_customer_by_email_core's docstrings) - either scope this call site to a real "
        "tenant/family instead, or add it to ANY_TENANT_ALLOWED_FILES here with a written "
        "reason covering every ANY_TENANT use in that file:\n" + "\n".join(violations)
    )


def test_allowed_files_still_exist_and_still_contain_the_patterns_they_were_allow_listed_for():
    """Keeps ALLOWED_FILES honest: an entry that no longer matches anything
    (the file was rewritten to no longer need the exception, or renamed) is
    stale and should be removed, not left as unused, unverifiable scope."""
    for rel in ALLOWED_FILES:
        path = BACKEND_DIR / rel
        assert path.exists(), f"Allow-listed file {rel} no longer exists - remove its ALLOWED_FILES entry"
        text = path.read_text()
        assert any(p.search(text) for p in PATTERNS.values()), (
            f"Allow-listed file {rel} no longer contains any of the guarded patterns - "
            f"remove its now-unused ALLOWED_FILES entry"
        )


def test_any_tenant_allowed_files_still_exist_and_still_use_any_tenant():
    """Keeps ANY_TENANT_ALLOWED_FILES honest the same way the check above
    keeps ALLOWED_FILES honest."""
    for rel in ANY_TENANT_ALLOWED_FILES:
        path = BACKEND_DIR / rel
        assert path.exists(), f"Allow-listed file {rel} no longer exists - remove its ANY_TENANT_ALLOWED_FILES entry"
        text = path.read_text()
        assert ANY_TENANT_USAGE.search(text), (
            f"Allow-listed file {rel} no longer uses ANY_TENANT as a value - "
            f"remove its now-unused ANY_TENANT_ALLOWED_FILES entry"
        )
