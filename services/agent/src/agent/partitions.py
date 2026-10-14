"""Monthly partitions of agent.investigation_steps and its online backfill (docs/migrations.md).

    uv run python -m agent.partitions backfill [--from-start]
    uv run python -m agent.partitions ensure

backfill copies the unpartitioned table into investigation_steps_p in resumable batches. ensure
creates this and next month's partitions, which the agent also does at startup and then daily.
"""

import argparse
import logging
import sys
import threading
import time
from datetime import UTC, date, datetime
from uuid import UUID

from psycopg.errors import LockNotAvailable
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from agent.settings import Settings
from py_config import ConfigError, load_settings
from py_telemetry import configure_logging

log = logging.getLogger(__name__)

BATCH_ROWS = 5000
# Give up on a lock after 3 s instead of queueing: every writer would queue behind the waiter.
LOCK_TIMEOUT = text("SET LOCAL lock_timeout = '3s'")
HOUR_SECONDS = 60 * 60

_PARTITIONED_TABLE = text("""
    SELECT c.relname FROM pg_partitioned_table p JOIN pg_class c ON c.oid = p.partrelid
    WHERE c.relnamespace = 'agent'::regnamespace
      AND c.relname IN ('investigation_steps', 'investigation_steps_p')
""")

_COPY_BATCH = text("""
    WITH batch AS MATERIALIZED (
        SELECT investigation_id, seq, created_at, schema_version, event
        FROM agent.investigation_steps
        WHERE (investigation_id, seq) > (:after_id, :after_seq)
        ORDER BY investigation_id, seq
        LIMIT :batch_rows
    ), copied AS (
        INSERT INTO agent.investigation_steps_p
            (investigation_id, seq, created_at, schema_version, event)
        SELECT * FROM batch
        ON CONFLICT DO NOTHING
    )
    SELECT investigation_id, seq, (SELECT count(*) FROM batch) AS n
    FROM batch ORDER BY investigation_id DESC, seq DESC LIMIT 1
""")
# The row lock also keeps a second backfill from running the same batches.
_READ_CURSOR = text("SELECT last_investigation_id, last_seq FROM agent.steps_backfill FOR UPDATE")
_MOVE_CURSOR = text("UPDATE agent.steps_backfill SET last_investigation_id = :id, last_seq = :seq")


def month_start(day: date, months_later: int = 0) -> date:
    index = day.year * 12 + day.month - 1 + months_later
    return date(index // 12, index % 12 + 1, 1)


def ensure_partitions(conn: Connection, first: date, last: date) -> list[str]:
    """Creates the missing monthly partitions from first's month to last's; returns their names.

    Each starts as a plain table that is then attached. ATTACH locks the parent only in SHARE
    UPDATE EXCLUSIVE mode, where CREATE TABLE ... PARTITION OF takes ACCESS EXCLUSIVE. It still
    locks the default partition and, to clone the foreign key, investigations, so it holds up step
    writes briefly; lock_timeout caps the wait. The attach fails if the default partition holds
    rows of the new month.
    """
    parent = conn.execute(_PARTITIONED_TABLE).scalar_one_or_none()
    if parent is None:  # Before revision 0002 nothing is partitioned.
        return []
    conn.execute(LOCK_TIMEOUT)
    # One agent at a time; the others wait, then find the partitions in place.
    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('agent.ensure_partitions'))"))
    created: list[str] = []
    start = month_start(first)
    while start <= last:
        end = month_start(start, 1)
        name = f"investigation_steps_y{start:%Y}m{start:%m}"
        if conn.execute(text("SELECT to_regclass(:name)"), {"name": f"agent.{name}"}).scalar():
            start = end
            continue
        conn.execute(
            text(
                f"CREATE TABLE agent.{name} "
                f"(LIKE agent.{parent} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
            )
        )
        conn.execute(
            text(
                f"ALTER TABLE agent.{parent} ATTACH PARTITION agent.{name} "
                f"FOR VALUES FROM ('{start}T00:00Z') TO ('{end}T00:00Z')"
            )
        )
        created.append(name)
        start = end
    return created


def ensure_upcoming(conn: Connection) -> list[str]:
    """This month's and next month's partitions."""
    today = datetime.now(UTC).date()
    return ensure_partitions(conn, today, month_start(today, 1))


def maintain_partitions(engine: Engine, stop: threading.Event) -> None:
    """Runs ensure_upcoming now and then daily, hourly after a failure.

    Next month's partition so exists weeks before its first row.
    """
    while not stop.is_set():
        wait = 24 * HOUR_SECONDS
        try:
            with engine.begin() as conn:
                if created := ensure_upcoming(conn):
                    log.info("partitions created", extra={"partitions": created})
        except Exception:
            log.exception("partition maintenance failed; retrying in an hour")
            wait = HOUR_SECONDS
        stop.wait(wait)


def backfill(engine: Engine, *, from_start: bool = False) -> int:
    """Copies investigation_steps into investigation_steps_p in key order; returns the rows read.

    Start it once every agent dual-writes: rows written since are in both tables, and ON CONFLICT
    skips them. Each batch commits together with the cursor, so a rerun resumes after it.
    """
    with engine.begin() as conn:
        # ponytail: full scan for the date range, fine to ~100M rows within the CLI's 60 s
        # statement timeout; read the range from investigations past that.
        first, last = conn.execute(
            text("SELECT min(created_at), max(created_at) FROM agent.investigation_steps")
        ).one()
        if first is not None:
            ensure_partitions(conn, first.astimezone(UTC).date(), last.astimezone(UTC).date())
        if from_start:
            conn.execute(
                text("UPDATE agent.steps_backfill SET last_investigation_id = :id, last_seq = 0"),
                {"id": UUID(int=0)},
            )
    total = 0
    while True:
        try:
            with engine.begin() as conn:
                conn.execute(LOCK_TIMEOUT)
                after_id, after_seq = conn.execute(_READ_CURSOR).one()
                last_row = conn.execute(
                    _COPY_BATCH,
                    {"after_id": after_id, "after_seq": after_seq, "batch_rows": BATCH_ROWS},
                ).one_or_none()
                if last_row is None:
                    return total
                conn.execute(_MOVE_CURSOR, {"id": last_row.investigation_id, "seq": last_row.seq})
                total += last_row.n
        except OperationalError as error:
            if not isinstance(error.orig, LockNotAvailable):
                raise
            log.warning("backfill batch hit lock_timeout; retrying")
            time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="investigation_steps partitioning")
    parser.add_argument("command", choices=["backfill", "ensure"])
    parser.add_argument(
        "--from-start", action="store_true", help="backfill every row again, ignoring the cursor"
    )
    args = parser.parse_args()
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"agent: {error}")
    configure_logging("agent", settings.log_level)
    engine = create_engine(
        settings.agent_database_url,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=60000"},
    )
    if args.command == "backfill":
        log.info("backfill done", extra={"rows": backfill(engine, from_start=args.from_start)})
    else:
        with engine.begin() as conn:
            # Not "created": that LogRecord attribute cannot be overwritten through extra.
            log.info("partitions ensured", extra={"partitions": ensure_upcoming(conn)})
    engine.dispose()


if __name__ == "__main__":
    main()
