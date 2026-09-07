"""Model package.

`entities.py` registers its sibling modules (`artefacts`, `breach`,
`grievance`) from its own footer, because each of those declares a
relationship that has to resolve against a class defined in `entities.py`
and so cannot be imported before it.

`analytics` has no such dependency - it names `organizations.id` by string
and imports nothing from `entities` - so it is registered here instead, at
the package root. `guardian` (R1-14) is registered here for the same reason
and with one extra consequence worth knowing: because this file runs BEFORE
`entities.py`, nothing in `guardian.py` may import from `entities` at module
scope. It does not - every ForeignKey target is a string, and the two places
`app/services/guardian.py` needs an `entities` class it imports inside the
function. That keeps `Base.metadata` complete for
`alembic/env.py` (and therefore for `alembic check`, which
`tests/test_migrations.py` runs) without a second lane having to edit
`entities.py` at the same time as the lane that already owns it. Moving this
line into `entities.py`'s sibling block later is a safe no-op; a duplicate
import of an already-imported module registers nothing twice.
"""
from app.models import analytics  # noqa: F401  (R2-07: banner_events table)
from app.models import guardian  # noqa: F401  (R1-14: age assurance, guardian consents, Fourth Schedule exemptions)
