"""Live data retention: a live tenant's steps, which hold watch's snapshot and every tool result,
its tool calls (the model's arguments) and its unsent step messages go 30 days after they were
stored. Its investigation row, report, report message and model calls stay: the user reads every
live report, and the dollar caps count the calls. The gateway purges its own copies
(apps/gateway/src/retention.ts)."""

import logging
import threading
from collections.abc import Collection
from datetime import timedelta
from uuid import UUID

from sqlalchemy import Connection, Engine, String, cast, delete, func, select

from agent.db import Investigation, InvestigationStep, Outbox, ToolCall
from agent.outbox import STEP

log = logging.getLogger(__name__)

# Days live data is kept (docs/memory.md, 2026-10-24).
RETENTION_DAYS = 30
HOUR_SECONDS = 3600
# A queued or running investigation replays its steps at its next turn, so it keeps them until done.
DONE = ("awaiting_approval", "resolved", "cancelled", "failed")


def purge(
    conn: Connection, tenants: Collection[UUID], days: int = RETENTION_DAYS
) -> dict[str, int]:
    """Deletes what the tenants' finished investigations stored over `days` ago; returns the rows
    it deleted per table."""
    if not tenants:
        return {"steps": 0, "tool_calls": 0, "outbox": 0}
    i, s, c, o = Investigation, InvestigationStep, ToolCall, Outbox
    finished = (i.tenant_id.in_(list(tenants)), i.status.in_(DONE))
    done = select(i.id).where(*finished)
    cutoff = func.now() - timedelta(days=days)
    steps = conn.execute(delete(s).where(s.investigation_id.in_(done), s.created_at < cutoff))
    calls = conn.execute(delete(c).where(c.investigation_id.in_(done), c.created_at < cutoff))
    # Each message's group is its investigation's id.
    groups = select(cast(i.id, String)).where(*finished)
    messages = conn.execute(
        delete(o).where(o.group_id.in_(groups), o.message_type == STEP, o.created_at < cutoff)
    )
    return {"steps": steps.rowcount, "tool_calls": calls.rowcount, "outbox": messages.rowcount}


def maintain_retention(engine: Engine, tenants: Collection[UUID], stop: threading.Event) -> None:
    """Purges now and then daily, hourly after a failure, as agent.partitions keeps partitions."""
    while not stop.is_set():
        wait = 24 * HOUR_SECONDS
        try:
            with engine.begin() as conn:
                purged = purge(conn, tenants)
            if any(purged.values()):
                log.info("live data purged", extra=purged)
        except Exception:
            log.exception("live data purge failed; retrying in an hour")
            wait = HOUR_SECONDS
        stop.wait(wait)
