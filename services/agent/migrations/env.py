"""Runs agent migrations online as the agent role; versions are tracked in the agent schema."""

from alembic import context
from sqlalchemy import create_engine

from agent.settings import Settings
from py_config import load_settings

engine = create_engine(load_settings(Settings).agent_database_url)
with engine.connect() as connection:
    context.configure(connection=connection, version_table_schema="agent")
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
