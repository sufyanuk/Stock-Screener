from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.models import Base

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=get_settings().sqlalchemy_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # app.migrate passes its own (advisory-locked) connection; the CLI creates one.
    conn = context.config.attributes.get("connection")
    if conn is not None:
        context.configure(connection=conn, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    eng = create_engine(get_settings().sqlalchemy_url)
    with eng.connect() as c:
        context.configure(connection=c, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
