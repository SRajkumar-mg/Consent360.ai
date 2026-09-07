"""Resolve the tenant (Organization) for a legacy source_app value, and the
single choke point for resolving a Customer from a caller-supplied
identifier.

`source_app` remains the compatibility identifier written by every demo
site and integration; `tenant_id` is the real foreign key. A source_app of
"", "SYSTEM" or "UI" (placeholder values written by internal tooling, not a
real client) all resolve to a single PLATFORM tenant, matching how
integration.py's `_ensure_source_consent_matrix` already treats those three
values as placeholders to be adopted by a real source.

Three rounds of cross-tenant customer-context fixes each closed one call
site that looked up a Customer by email/external_id with no tenant filter,
and each time a sibling appeared elsewhere within hours - the pattern lives
at the call site, not in any one file. `resolve_customer` below is the
structural fix: every customer resolution in `app/` and `scripts/` goes
through it, and it has no default for `source_app`, so a call site cannot
omit tenant scoping by accident - the exact way every prior instance
happened. `tests/test_customer_resolution_guard.py` enforces this by
scanning the source tree for the raw query patterns this replaces and
failing if one appears anywhere not explicitly allow-listed.
"""
from sqlalchemy.orm import Session

from app.core.encryption import find_by_search_digest, search_filter
from app.models.entities import Customer, Organization

PLATFORM_TENANT_CODE = "PLATFORM"
_PLACEHOLDER_SOURCE_APPS = ("", "SYSTEM", "UI")


class _AnyTenant:
    """Sentinel for `resolve_customer(source_app=...)`: an explicit,
    grep-able opt-out of tenant scoping for the narrow, legitimate case of
    an unscoped staff role (e.g. a platform admin) that is genuinely allowed
    to see every tenant's customers. `source_app` has no default value, so
    a call site can never fall into "no scope" by omission - reaching for
    ANY_TENANT is a conscious, visible choice, not a silent accident."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "ANY_TENANT"


ANY_TENANT = _AnyTenant()


def resolve_customer(
    db: Session,
    *,
    source_app,
    external_id: str | None = None,
    email: str | None = None,
    customer_pk: int | None = None,
) -> Customer | None:
    """The single choke point for resolving an existing Customer from a
    caller-supplied identifier. Always scoped; never returns a customer
    outside that scope.

    ``source_app`` is a required keyword with NO default - it must be one of:
      - a single tenant string (the common case: an integration key's own
        tenant_code, an org-scoped staff role's scope, a context's own
        source_app, ...);
      - a list/tuple/set of tenant strings (a bounded, named family of
        tenants that are allowed to share one identity by design - e.g. the
        CRM/Codex/SkillLearn shared directory, see crm.py's module comment -
        never an unbounded "everything" set);
      - the ``ANY_TENANT`` sentinel, for the rare, legitimate "no
        restriction" case (an unscoped staff admin). Must be reached for
        explicitly - there is no falsy default that means the same thing.

    Exactly one of ``external_id``, ``email``, ``customer_pk`` must be given.
    Returns ``None`` if there is no match WITHIN the given scope - a
    customer that exists but belongs to a different tenant is indistinguishable
    from one that does not exist at all, by design (see
    create_context_for_customer's "treat as not found" rationale).
    """
    identifiers = [v for v in (external_id, email, customer_pk) if v is not None]
    if len(identifiers) != 1:
        raise ValueError("resolve_customer requires exactly one of external_id, email, or customer_pk")

    query = db.query(Customer)
    if isinstance(source_app, _AnyTenant):
        pass  # explicit, deliberate opt-out - see ANY_TENANT's docstring
    elif isinstance(source_app, (list, tuple, set, frozenset)):
        allowed = list(source_app)
        if not allowed:
            raise ValueError("resolve_customer's source_app list/set must not be empty")
        query = query.filter(Customer.source_app.in_(allowed))
    elif source_app:
        query = query.filter(Customer.source_app == source_app)
    else:
        raise ValueError(
            "resolve_customer requires a tenant scope - a source_app string, a list/set of "
            "allowed source_apps, or the explicit ANY_TENANT sentinel. It cannot be called with "
            "no scope at all: that silent omission is the exact bug this function exists to make "
            "impossible."
        )

    if customer_pk is not None:
        return query.filter(Customer.id == customer_pk).first()
    # find_by_search_digest, not `== hmac_digest(...)`: it matches every
    # search digest the row could currently be stored under (see
    # app/core/encryption.py's _HmacKeyRing) and lazily rehashes a hit
    # found under a retired key. A miss here is read as "new customer"
    # by every upsert path above this one, so an HMAC key change that
    # turned a real customer into a miss did not merely hide them - it
    # fabricated a duplicate identity for them.
    if external_id is not None:
        return find_by_search_digest(query, Customer.external_id_search, external_id)
    return find_by_search_digest(query, Customer.email_search, email)


def external_id_owned_by_another_tenant(db: Session, external_id: str, source_app: str) -> bool:
    """Global existence check used ONLY to detect a naming collision (see
    create_context_for_customer's 409 path for an explicit customer_id
    already claimed by a different tenant). Deliberately outside
    resolve_customer's scoping: it must be able to see across every tenant
    to do its one job, but it returns a bool, never the Customer row itself,
    so it cannot be used to read or act on another tenant's data - the
    same reason ANY_TENANT is a separate, explicit opt-out rather than a
    silent default."""
    clash = (
        db.query(Customer.id)
        .filter(search_filter(Customer.external_id_search, external_id), Customer.source_app != source_app)
        .first()
    )
    return clash is not None


def resolve_tenant_id(db: Session, source_app: str | None) -> int:
    code = PLATFORM_TENANT_CODE if not source_app or source_app in _PLACEHOLDER_SOURCE_APPS else source_app

    # Memoise per Session (not module- or process-wide): `db.info` is a plain
    # dict SQLAlchemy attaches to each Session instance and never shares
    # across Sessions, so repeated lookups within one request/script run hit
    # the cache instead of issuing a fresh SELECT per row (e.g.
    # `_ensure_source_consent_matrix` resolving the same source_app once per
    # purpose x data category x processing activity), while different
    # sessions - and different test databases, each with their own engine
    # and Session - stay fully isolated from each other.
    cache: dict[str, int] = db.info.setdefault("_tenant_ids", {})
    cached = cache.get(code)
    if cached is not None:
        return cached

    org = db.query(Organization).filter(Organization.code == code).first()
    if not org:
        org = Organization(name=code.replace("_", " ").title(), code=code, is_active=True)
        db.add(org)
        db.flush()
    cache[code] = org.id
    return org.id


def platform_tenant_id(db: Session) -> int:
    return resolve_tenant_id(db, None)
