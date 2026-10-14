"""Runs agent migrations online as the agent role; versions are tracked in the agent schema."""

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.schema import SchemaItem

from agent.db import Base
from agent.settings import Settings
from py_config import load_settings


def include_object(
    _obj: SchemaItem, name: str | None, type_: str, reflected: bool, _compare_to: SchemaItem | None
) -> bool:
    """Leaves out the monthly partitions: agent.partitions creates them, no model describes them."""
    return not (type_ == "table" and reflected and (name or "").startswith("investigation_steps_"))


# With search_path=public, reflection sees "agent" by name, as the models do, so `alembic check`
# and `alembic revision --autogenerate` compare like with like. Revisions qualify every name.
engine = create_engine(
    load_settings(Settings).agent_database_url, connect_args={"options": "-c search_path=public"}
)
with engine.connect() as connection:
    context.configure(
        connection=connection,
        version_table_schema="agent",
        # Each revision commits on its own: a run of several never holds one's locks into the next.
        transaction_per_migration=True,
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=lambda name, type_, _parents: type_ != "schema" or name == "agent",
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
