"""Story A: investigation_steps is partitioned online while a writer keeps recording steps.

Runs on 2,000,000 seeded steps (the seed's large mode); PARTITION_TEST_STEPS changes that.
"""

import itertools
import logging
import os
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, insert, text

from agent.db import Investigation
from agent.partitions import backfill, ensure_partitions, month_start
from agent.steps import StepRecordedV2, record_step

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

log = logging.getLogger(__name__)

STEPS = int(os.environ.get("PARTITION_TEST_STEPS", "2000000"))
EVENT = StepRecordedV2(
    kind="hypothesis",
    summary="recorded during the migration",
    tool_name=None,
    tool_call_id=None,
    result=None,
    latency_ms=None,
    tokens=None,
)


class Writer(threading.Thread):
    """Records steps back to back, as the agent loop will, and keeps every error."""

    def __init__(self, engine: Engine, investigations: list[UUID]) -> None:
        super().__init__(daemon=True)
        self.engine, self.investigations = engine, investigations
        self.dual_write, self.stop = threading.Event(), threading.Event()
        self.dual_write.set()
        self.written = 0
        self.slowest = 0.0
        self.errors: list[Exception] = []

    def run(self) -> None:
        for investigation_id in itertools.cycle(self.investigations):
            if self.stop.is_set():
                return
            started = time.monotonic()
            try:
                with self.engine.begin() as conn:
                    record_step(conn, investigation_id, EVENT, dual_write=self.dual_write.is_set())
                self.written += 1
            except Exception as error:
                self.errors.append(error)
            self.slowest = max(self.slowest, time.monotonic() - started)
            time.sleep(0.002)


def test_partition_swap_while_a_writer_runs(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_gateway()
    scratch_db.migrate_agent("0001")
    scratch_db.seed("--large", str(STEPS))
    scratch_db.migrate_agent("0002")

    # The writer gets the agent's statement timeout, so a write stuck behind a lock fails instead
    # of waiting; the backfill and the partition job get the CLI's.
    engine = create_engine(
        scratch_db.agent_url, connect_args={"options": "-c statement_timeout=5000"}
    )
    jobs = create_engine(
        scratch_db.agent_url, connect_args={"options": "-c statement_timeout=60000"}
    )
    investigations = [uuid4() for _ in range(5)]
    with engine.begin() as conn:
        conn.execute(insert(Investigation), [{"id": id_} for id_ in investigations])
        seeded = conn.execute(text("SELECT count(*) FROM agent.investigation_steps")).scalar_one()
    assert seeded >= STEPS

    writer = Writer(engine, investigations)
    written_after: dict[str, int] = {}

    def phase_done(name: str) -> None:
        # Every phase must see writes of its own: wait for one past the previous phase's count.
        before = max(written_after.values(), default=0)
        deadline = time.monotonic() + 10
        while writer.written <= before:
            if time.monotonic() > deadline:
                pytest.fail(
                    f"the writer recorded no step in the {name!r} phase within 10 s "
                    f"({before} before it; last errors: {writer.errors[-3:]})"
                )
            time.sleep(0.01)
        written_after[name] = writer.written

    far = month_start(datetime.now(UTC).date(), 3)
    writer.start()
    try:
        phase_done("dual-write on")
        copied = backfill(jobs)
        phase_done("backfill")
        scratch_db.migrate_agent("0003")  # Checks the month counts, then swaps the names.
        phase_done("swap")
        writer.dual_write.clear()  # The next release turns the flag off.
        phase_done("dual-write off")
        scratch_db.migrate_agent("0004")  # A later release drops the old table.
        phase_done("contract")
        with jobs.begin() as conn:
            created = ensure_partitions(conn, far, far)
        phase_done("partition job")
    finally:
        writer.stop.set()
        writer.join()
        jobs.dispose()
    scratch_db.partitions("ensure")  # The CLI path, which logs what it created.

    log.info("writes after each phase %s, slowest %.3f s", written_after, writer.slowest)
    assert writer.errors == []
    # No write waited out a lock timeout: the swap held its locks only briefly.
    assert writer.slowest < 3
    counts = [0, *written_after.values()]
    assert all(after > before for before, after in itertools.pairwise(counts)), written_after
    assert copied >= seeded
    assert created == [f"investigation_steps_y{far:%Y}m{far:%m}"]
    with engine.begin() as conn:
        assert ensure_partitions(conn, far, far) == []
        assert (
            conn.execute(text("SELECT to_regclass('agent.investigation_steps_old')")).scalar()
            is None
        )
        assert (
            conn.execute(text("SELECT to_regclass('agent.investigation_steps_p')")).scalar() is None
        )
        kind = "SELECT relkind FROM pg_class WHERE oid = 'agent.investigation_steps'::regclass"
        assert conn.execute(text(kind)).scalar() == "p"
        total = conn.execute(text("SELECT count(*) FROM agent.investigation_steps")).scalar()
        assert total == seeded + writer.written
        default = "SELECT count(*) FROM agent.investigation_steps_default"
        assert conn.execute(text(default)).scalar() == 0
        # The writer's steps are numbered 1..n per investigation, with no gap and no duplicate.
        numbering = conn.execute(
            text("""
                SELECT i.step_count, count(s.seq), count(DISTINCT s.seq), max(s.seq)
                FROM agent.investigations i
                JOIN agent.investigation_steps s ON s.investigation_id = i.id
                WHERE i.id = ANY(:ids) GROUP BY i.id, i.step_count
            """),
            {"ids": investigations},
        ).all()
        assert len(numbering) == len(investigations)
        assert all(len(set(row)) == 1 for row in numbering), numbering
    engine.dispose()


def test_contract_refuses_while_old_rows_are_missing(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_agent("0003")
    engine = create_engine(scratch_db.agent_url)
    with engine.begin() as conn:
        investigation_id = uuid4()
        conn.execute(insert(Investigation), {"id": investigation_id})
        # What a writer without dual-write could leave between 0003's count check and its lock.
        conn.execute(
            text(
                "INSERT INTO agent.investigation_steps_old"
                " (investigation_id, seq, schema_version, event) VALUES (:id, 1, 2, '{}')"
            ),
            {"id": investigation_id},
        )

    with pytest.raises(RuntimeError, match=r"not in agent\.investigation_steps"):
        scratch_db.migrate_agent("0004")
    with engine.connect() as conn:
        old = conn.execute(text("SELECT to_regclass('agent.investigation_steps_old')")).scalar()
        assert old is not None
    engine.dispose()


def test_swap_refuses_before_the_backfill_finishes(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_agent("0001")
    engine = create_engine(scratch_db.agent_url)
    with engine.begin() as conn:
        investigation_id = uuid4()
        conn.execute(insert(Investigation), {"id": investigation_id})
        record_step(conn, investigation_id, EVENT, dual_write=False)
    scratch_db.migrate_agent("0002")

    with pytest.raises(RuntimeError, match="missing rows"):
        scratch_db.migrate_agent("0003")
    with engine.connect() as conn:
        kind = "SELECT relkind FROM pg_class WHERE oid = 'agent.investigation_steps'::regclass"
        assert conn.execute(text(kind)).scalar() == "r"
    engine.dispose()
