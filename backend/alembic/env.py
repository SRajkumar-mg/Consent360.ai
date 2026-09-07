from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.database import Base
from app.models import entities  # noqa: F401

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    # disable_existing_loggers=False is load-bearing, not tidiness.
    # fileConfig's default (True) permanently DISABLES every logger that was
    # created before it runs. alembic/env.py imports app.core.config and
    # app.models.entities above, which pull in modules that create
    # module-level loggers (app.core.access_log, app.core.alerting,
    # app.services.notifications, ...) - and conftest.py runs `alembic upgrade
    # head` IN PROCESS at session start, so with the default those loggers are
    # dead for the entire test session.
    #
    # That is worse than a missing log line: a test asserting "the access log
    # redacted this identifier" would pass against an empty record list, i.e.
    # a false green on a security control. tests/test_observability.py and
    # tests/test_webhooks.py both carry fixtures that re-enable their logger
    # by hand to work around exactly this.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
