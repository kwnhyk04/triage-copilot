"""Both migration chains apply to an empty database, and the seed fills them consistently."""

import os
from typing import TYPE_CHECKING
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, insert, text

from agent.db import Investigation
from agent.steps import StepRecordedV2, record_step, replay

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

STEP = StepRecordedV2(
    kind="report",
    summary="carried through the downgrades",
    tool_name=None,
    tool_call_id=None,
    result=None,
    latency_ms=None,
    tokens=None,
)


def test_record_step_stores_nothing_that_breaks_a_v3_limit(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    investigation_id = uuid4()
    with engine.begin() as agent:
        agent.execute(insert(Investigation), {"id": investigation_id})
    # model_copy(update=...) skips validation, as a producer bug might.
    too_long = STEP.model_copy(update={"summary": "x" * 2001})
    with pytest.raises(ValidationError), engine.begin() as agent:
        record_step(agent, investigation_id, too_long, dual_write=False)
    with engine.connect() as agent:
        stored = agent.execute(
            text("""
                SELECT step_count, (SELECT count(*) FROM agent.investigation_steps
                                    WHERE investigation_id = :id)
                FROM agent.investigations WHERE id = :id
            """),
            {"id": investigation_id},
        ).one()
    assert tuple(stored) == (0, 0)
    engine.dispose()


def test_both_chains_apply_from_empty_and_the_seed_fills_them(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_gateway()
    scratch_db.migrate_agent("head")
    scratch_db.check_agent_models()
    scratch_db.seed()

    with psycopg.connect(os.environ["GATEWAY_DATABASE_URL"]) as gateway:
        counts = gateway.execute("""
            SELECT (SELECT count(*) FROM gateway.tenants), (SELECT count(*) FROM gateway.services),
                   (SELECT count(*) FROM gateway.alerts)
        """).fetchone()
        investigated = {
            row[0]
            for row in gateway.execute(
                "SELECT investigation_id FROM gateway.alert_groups"
                " WHERE investigation_id IS NOT NULL"
            )
        }
    assert counts == (5, 50, 10_000)

    engine = create_engine(scratch_db.agent_url)
    with engine.connect() as agent:
        assert {row[0] for row in agent.execute(text("SELECT id FROM agent.investigations"))} == (
            investigated
        )
        assert len(investigated) == 2_000
        by_version = text(
            "SELECT schema_version, count(*) FROM agent.investigation_steps GROUP BY 1"
        )
        versions = {version: count for version, count in agent.execute(by_version)}
        assert set(versions) == {1, 2}
        steps = agent.execute(text("SELECT sum(step_count) FROM agent.investigations")).scalar()
        assert steps == sum(versions.values())
        default = agent.execute(text("SELECT count(*) FROM agent.investigation_steps_default"))
        assert default.scalar() == 0

        # Story B on seeded rows: a v1 investigation replays as v2, and its rows stay v1.
        stored_query = text("""
            SELECT investigation_id, array_agg(event ORDER BY seq) FROM agent.investigation_steps
            WHERE schema_version = 1 GROUP BY 1 ORDER BY 1 LIMIT 1
        """)
        investigation_id, stored = agent.execute(stored_query).one()
        replayed = replay(agent, investigation_id)
        assert [step.result for step in replayed] == [event["observation"] for event in stored]
        assert agent.execute(stored_query).one() == (investigation_id, stored)
    engine.dispose()


def test_agent_chain_downgrades_to_empty_and_back(scratch_db: "ScratchDb") -> None:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    # A stored step, so the downgrades of 0004 and 0003 have rows to carry back.
    with engine.begin() as agent:
        investigation_id = uuid4()
        agent.execute(insert(Investigation), {"id": investigation_id})
        record_step(agent, investigation_id, STEP, dual_write=False)
    scratch_db.downgrade_agent("base")
    with engine.connect() as agent:
        left = agent.execute(
            text("SELECT relname FROM pg_class WHERE relnamespace = 'agent'::regnamespace")
        )
        assert {row[0] for row in left} <= {"alembic_version", "alembic_version_pkc"}
    engine.dispose()
    scratch_db.migrate_agent("head")
    scratch_db.check_agent_models()
