from app.models.entities import DataCategory, Organization, ProcessingActivity, Purpose, PurposeVersion


def test_public_purposes_returns_itemised_notice_data(db, client):
    org = Organization(name="Notice Test", code="NOTICE_TEST", is_active=True)
    db.add(org)
    category = DataCategory(name="Notice Category", code="notice_cat")
    activity = ProcessingActivity(name="Notice Activity", code="notice_act")
    db.add_all([category, activity])
    db.flush()
    purpose = Purpose(name="Notice Purpose", code="notice_purpose", requires_consent=True, retention_period_days=90)
    db.add(purpose)
    db.flush()
    pv = PurposeVersion(
        purpose_id=purpose.id, version_number=1, name=purpose.name,
        description="Notice purpose description", data_category_ids=[category.id],
        processing_activity_ids=[activity.id], consent_text="I consent to notice purpose.",
        is_current=True, created_by="test",
    )
    db.add(pv)
    db.commit()

    resp = client.get("/public/NOTICE_TEST/purposes")
    assert resp.status_code == 200
    body = resp.json()
    row = next(r for r in body if r["code"] == "notice_purpose")
    assert row["purpose_version_id"] == pv.id
    assert row["data_categories"] == ["Notice Category"]
    assert row["processing_activities"] == ["Notice Activity"]
    assert row["retention_period_days"] == 90
    assert row["consent_text"] == "I consent to notice purpose."


def test_public_purposes_404_for_unknown_tenant(client):
    resp = client.get("/public/NO_SUCH_TENANT_XYZ/purposes")
    assert resp.status_code == 404


def test_public_purposes_uses_a_bounded_number_of_queries_regardless_of_purpose_count(db, client):
    """Previously this issued roughly 1 + 3N queries for N purposes (a lazy
    load of the current PurposeVersion, one DataCategory query and one
    ProcessingActivity query - each per purpose). Prove the query count no
    longer grows with N by exercising 5 purposes and bounding the total."""
    from sqlalchemy import event

    from app.core.database import engine

    org = Organization(name="Query Count Test", code="QUERY_COUNT_TEST", is_active=True)
    db.add(org)
    for i in range(5):
        category = DataCategory(name=f"qc-cat-{i}", code=f"qc_cat_{i}")
        activity = ProcessingActivity(name=f"qc-act-{i}", code=f"qc_act_{i}")
        db.add_all([category, activity])
        db.flush()
        purpose = Purpose(name=f"QC Purpose {i}", code=f"qc_purpose_{i}", requires_consent=True)
        db.add(purpose)
        db.flush()
        db.add(PurposeVersion(
            purpose_id=purpose.id, version_number=1, name=purpose.name,
            data_category_ids=[category.id], processing_activity_ids=[activity.id],
            consent_text="consent", is_current=True, created_by="test",
        ))
    db.commit()

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        resp = client.get("/public/QUERY_COUNT_TEST/purposes")
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert resp.status_code == 200
    assert len(resp.json()) >= 5
    select_statements = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(select_statements) <= 6, (
        f"expected a query count independent of purpose count, got {len(select_statements)}: {select_statements}"
    )


def test_public_purposes_is_rate_limited(client, monkeypatch):
    import app.api.routes.public as public_module
    from app.core.utils import RateLimiter

    # A fresh limiter instance, not the shared module-level singleton, so
    # this test cannot be polluted by (or pollute) any other test's calls
    # to /public/*.
    monkeypatch.setattr(public_module, "public_limiter", RateLimiter(limit=2, window_seconds=60))

    first = client.get("/public/NO_SUCH_TENANT_RATE_LIMIT/purposes")
    second = client.get("/public/NO_SUCH_TENANT_RATE_LIMIT/purposes")
    third = client.get("/public/NO_SUCH_TENANT_RATE_LIMIT/purposes")

    assert first.status_code == 404
    assert second.status_code == 404
    assert third.status_code == 429
