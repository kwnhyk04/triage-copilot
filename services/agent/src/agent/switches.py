"""Kill switches the operator flips without a restart: `live` lets live tenants' investigations
run (default off), and `claude` lets any Claude model be called (default on). The job reads `live`
before every model turn and the limiter reads `claude` before every Claude request, so a flip takes
effect at the next one.

    uv run python -m agent.switches show
    uv run python -m agent.switches set live on
"""

import argparse
import sys
from collections.abc import Sequence
from typing import Literal, cast, get_args

from sqlalchemy import Connection, Engine, create_engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from agent.db import Switch as Row
from agent.settings import Settings
from py_config import ConfigError, load_settings

Switch = Literal["live", "claude"]
# A switch with no row is at its default.
DEFAULTS: dict[Switch, bool] = {"live": False, "claude": True}


def is_on(conn: Connection, name: Switch) -> bool:
    with conn.begin():
        found = conn.execute(select(Row.enabled).where(Row.name == name)).scalar_one_or_none()
    return DEFAULTS[name] if found is None else found


def set_switch(conn: Connection, name: Switch, on: bool) -> None:
    """Sets the switch in the caller's transaction."""
    upsert = pg_insert(Row).values(name=name, enabled=on)
    changed = {"enabled": on, "updated_at": func.now()}
    conn.execute(upsert.on_conflict_do_update(index_elements=[Row.name], set_=changed))


def run(engine: Engine, argv: Sequence[str]) -> str:
    """The CLI: `show`, or `set <name> on|off`; returns what it prints."""
    parser = argparse.ArgumentParser(prog="python -m agent.switches")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show")
    flip = commands.add_parser("set")
    flip.add_argument("name", choices=get_args(Switch))
    flip.add_argument("state", choices=["on", "off"])
    args = parser.parse_args(argv)
    if args.command == "set":
        with engine.begin() as conn:
            set_switch(conn, cast("Switch", args.name), args.state == "on")
        return f"{args.name}: {args.state}"
    with engine.connect() as conn:
        return "\n".join(f"{name}: {'on' if is_on(conn, name) else 'off'}" for name in DEFAULTS)


def main() -> None:
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"agent: {error}")
    engine = create_engine(settings.agent_database_url)
    try:
        print(run(engine, sys.argv[1:]))  # noqa: T201 - CLI output
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
