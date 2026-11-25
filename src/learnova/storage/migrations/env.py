"""Alembic entry point. Uses the connection handed over by learnova.storage.database.migrate
when the app runs it, or opens the configured database when run from the alembic CLI."""

from logging.config import fileConfig

from alembic import context

from learnova.storage.tables import Base, UTCDateTime

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _render_item(type_, obj, autogen_context):
    """Write UTCDateTime as a plain DateTime(timezone=True) so migrations don't import app code."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # lets ALTER TABLE work on SQLite
        render_item=_render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    from learnova.config import load_settings
    from learnova.storage.database import make_engine

    engine = make_engine(load_settings().database_url)
    with engine.connect() as conn:
        _run(conn)
