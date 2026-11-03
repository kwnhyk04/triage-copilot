"""Plans for real incidents against a scratch Postgres (P2.2): a real verdict's report gets one
plan and its review (Part B), sent as PlanRecorded v2 after the report; a not_real verdict, a
simulated incident and the planner switch off get none; a redelivered message finishes a plan left
unmade and makes no second; a plan the code checks refuse is kept with its review.
"""

import asyncio
import json
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, select
from test_live_int import APPS, LIVE, VERDICT, snapshot_tool, switch
from test_workers_int import TENANT, Tools, make_job, new_request

from agent.db import ModelCall as CallRow
from agent.db import Outbox, Plan
from agent.job import Job
from agent.llm import PLAN_OUTPUT_CAP, REVIEW_OUTPUT_CAP
from agent.model import ModelCall, PlanRequest, ScriptedModel, Usage
from agent.planner import PLANNER_PROMPT_SHA, Planner
from agent.reviewer import REVIEWER_PROMPT_SHA
from py_clients.generated.gateway import AlertContext
from py_clients.generated.messages.investigation_requested_v2_schema import (
    InvestigationRequestedV2,
)
from py_clients.generated.messages.plan_recorded_v2_schema import PlanRecordedV2

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

RESTART_API: dict[str, Any] = {
    "kind": "restart_service",
    "target": "api",
    "files": [],
    "rationale": "The api's readiness check fails while postgres, redis and minio run healthy.",
}
FA_CONTAINERS = ("api", "worker", "postgres", "redis", "minio", "egress-proxy", "tailscale")
SNAPSHOT: dict[str, Any] = {
    "app": "freight-audit",
    "stack": "prod",
    "taken_at": "2026-10-26T09:56:00Z",
    "alert": {"alertname": "HealthCheckFailed", "service": "api", "source": "health"},
    "health": [{"service": "api", "url": "https://[tailnet]/readyz", "status": 503, "ms": 9}],
    "containers": [
        {
            "service": name,
            "name": f"freightaudit-prod-{name}-1",
            "state": "running",
            "status": "Up 3 hours (healthy)",
        }
        for name in FA_CONTAINERS
    ],
    "logs": {},
    "changes": {"recreated": [], "commits": []},
    "problems": [],
}


@pytest.fixture
def engine(scratch_db: "ScratchDb") -> Iterator[Engine]:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    yield engine
    engine.dispose()


@pytest.fixture
def planning(engine: Engine) -> Engine:
    switch(engine, "live", True)
    switch(engine, "planner", True)
    return engine


OK_REVIEW: dict[str, Any] = {"verdict": "ok", "issues": []}


class Plans:
    """The plan chain: answers each request with the next answer (None: every model failed), as
    Opus would; and the reviewer, with its one answer (None: it failed), as Sonnet would. Both
    keep every request."""

    def __init__(
        self, *answers: dict[str, Any] | None, review: dict[str, Any] | None = OK_REVIEW
    ) -> None:
        self.answers = list(answers)
        self.verdict = review
        self.requests: list[PlanRequest] = []
        self.reviews: list[PlanRequest] = []

    async def review(
        self, request: PlanRequest
    ) -> tuple[dict[str, Any] | None, list[ModelCall], str | None]:
        self.reviews.append(request)
        answer = self.verdict
        refused = "Refused: anthropic declined the request (category cyber)"
        call = ModelCall(
            "reviewer",
            "anthropic",
            "claude-sonnet-5-5",
            "ok" if answer else "error",
            Usage(input=14_000, output=300),
            request.bound,
            REVIEW_OUTPUT_CAP,
            2_500,
            None if answer else refused,
            request.prompt_sha,
        )
        return answer, [call], None if answer else refused

    async def plan(self, request: PlanRequest) -> tuple[dict[str, Any] | None, list[ModelCall]]:
        self.requests.append(request)
        answer = self.answers.pop(0)
        refused = "Refused: anthropic declined the request (category cyber)"
        call = ModelCall(
            "planner",
            "anthropic",
            "claude-opus-5-5",
            "ok" if answer else "error",
            Usage(input=12_000, output=900),
            request.bound,
            PLAN_OUTPUT_CAP,
            900,
            None if answer else refused,
            request.prompt_sha,
        )
        return answer, [call]


class Failing(Plans):
    async def plan(self, request: PlanRequest) -> tuple[dict[str, Any] | None, list[ModelCall]]:
        raise RuntimeError("the agent stopped mid-plan")


def alerts(snapshot: dict[str, Any] | None) -> Callable[[UUID], AlertContext]:
    """The gateway's alert context: one firing alert, with watch's snapshot when there is one."""

    def read(investigation_id: UUID) -> AlertContext:
        notes = {"summary": "api readiness failing"}
        if snapshot is not None:
            notes["snapshot"] = json.dumps(snapshot)
        alert = {
            "fingerprint": "8a0461e5e216f2b5",
            "labels": {"alertname": "HealthCheckFailed", "app": "freight-audit", "service": "api"},
            "annotations": notes,
            "starts_at": "2026-10-26T09:55:00Z",
            "status": "firing",
        }
        return AlertContext.model_validate(
            {
                "investigation_id": str(investigation_id),
                "group_id": str(uuid4()),
                "tenant_id": str(LIVE),
                "group_key": '{}:{app="freight-audit"}',
                "window_start": "2026-10-26T09:55:00Z",
                "severity": "critical",
                "alert_count": 1,
                "alerts": [alert],
            }
        )

    return read


def planned_job(
    engine: Engine,
    plans: Plans,
    verdict: str = "real",
    snapshot: dict[str, Any] | None = SNAPSHOT,
) -> Job:
    """Live triage of FreightAudit that reports `verdict` at once, with the planner after it."""
    draft = VERDICT.model_copy(update={"verdict": verdict})
    return make_job(
        engine,
        Tools().all(),
        live_tenants=APPS,
        live_tools=snapshot_tool([]),
        live_model=ScriptedModel([draft]),
        planner=Planner(plans, alerts(snapshot), plans, None),
    )


def run(job: Job, request: InvestigationRequestedV2, stopping: asyncio.Event | None = None) -> str:
    return asyncio.run(job.run(request, stopping or asyncio.Event()))


def messages(engine: Engine, inv: UUID) -> list[tuple[str, dict[str, Any]]]:
    query = select(Outbox.message_type, Outbox.body).where(Outbox.group_id == str(inv))
    with engine.connect() as conn:
        return [(row.message_type, row.body) for row in conn.execute(query.order_by(Outbox.id))]


def plan_message(engine: Engine, inv: UUID) -> PlanRecordedV2:
    [body] = [body for kind, body in messages(engine, inv) if kind == "plan-recorded"]
    return PlanRecordedV2.model_validate(body)


def test_a_real_verdict_gets_one_plan_sent_after_its_report(planning: Engine) -> None:
    plans = Plans(RESTART_API)
    request = new_request(LIVE)

    assert run(planned_job(planning, plans), request) == "done"

    inv = request.investigation_id
    kinds = [kind for kind, _ in messages(planning, inv)]
    assert kinds[-2:] == ["report-recorded", "plan-recorded"]
    message = plan_message(planning, inv)
    assert message.investigation_id == inv
    assert message.plan is not None
    assert message.plan.model_dump(mode="json") == RESTART_API
    assert message.problem is None
    assert [m.model_dump(mode="json") for m in message.models] == [
        {
            "part": "plan",
            "provider": "anthropic",
            "model": "claude-opus-5-5",
            "calls": 1,
            "failed": 0,
            "reason": None,
        }
    ]
    # The planner saw the report and the newest snapshot, under its own prompt's sha; the reviewer
    # saw the same, then the plan, under its own.
    [asked], [reviewed] = plans.requests, plans.reviews
    assert asked.prompt_sha == PLANNER_PROMPT_SHA
    assert VERDICT.summary in asked.text
    assert "Health checks:" in asked.text
    assert reviewed.prompt_sha == REVIEWER_PROMPT_SHA
    assert reviewed.text.startswith(asked.text)
    review: dict[str, Any] = {
        "status": "ok",
        "by": "model",
        "model": "claude-sonnet-5-5",
        "issues": [],
        "reason": None,
        "new_files": [],
    }
    assert message.review is not None
    assert message.review.model_dump(mode="json") == review
    with planning.connect() as conn:
        columns = (Plan.id, Plan.plan, Plan.problem, Plan.review, Plan.planned_at)
        row = conn.execute(select(*columns).where(Plan.investigation_id == inv)).one()
        roles = conn.execute(select(CallRow.role).where(CallRow.investigation_id == inv))
        assert sorted(roles.scalars()) == ["planner", "primary", "reviewer"]
    stored = (row.id, row.plan, row.problem, row.review)
    assert stored == (message.plan_id, RESTART_API, None, review)
    assert row.planned_at is not None
    # The report lists triage's models only, and the plan's message the planner's: the review
    # names its own model.
    [report] = [body for kind, body in messages(planning, inv) if kind == "report-recorded"]
    assert [m["part"] for m in report["models"]] == ["triage"]


def test_no_plan_for_a_not_real_verdict_a_simulated_incident_or_the_planner_off(
    planning: Engine,
) -> None:
    plans = Plans()
    not_real, sim, off = new_request(LIVE), new_request(TENANT), new_request(LIVE)

    assert run(planned_job(planning, plans, verdict="not_real"), not_real) == "done"
    assert run(planned_job(planning, plans), sim) == "done"
    switch(planning, "planner", False)
    assert run(planned_job(planning, plans), off) == "done"

    assert plans.requests == []
    for request in (not_real, sim, off):
        inv = request.investigation_id
        assert "plan-recorded" not in [kind for kind, _ in messages(planning, inv)]
        with planning.connect() as conn:
            assert conn.execute(select(Plan).where(Plan.investigation_id == inv)).first() is None


def test_a_redelivery_makes_no_second_plan_and_finishes_one_left_unmade(planning: Engine) -> None:
    plans = Plans(RESTART_API, RESTART_API)
    request = new_request(LIVE)
    assert run(planned_job(planning, plans), request) == "done"
    assert run(planned_job(planning, plans), request) == "done"
    assert len(plans.requests) == 1

    crashed = new_request(LIVE)
    with pytest.raises(RuntimeError):
        run(planned_job(planning, Failing()), crashed)
    inv = crashed.investigation_id
    assert "plan-recorded" not in [kind for kind, _ in messages(planning, inv)]
    # Shutting down leaves the plan due, and keeps the message for the next agent.
    stopping = asyncio.Event()
    stopping.set()
    assert run(planned_job(planning, plans), crashed, stopping) == "stopped"
    assert run(planned_job(planning, plans), crashed) == "done"
    assert plan_message(planning, inv).plan is not None
    assert len(plans.requests) == 2


def test_no_plan_says_why_and_a_plan_the_code_checks_refuse_is_kept_with_its_review(
    planning: Engine,
) -> None:
    failed, refused = new_request(LIVE), new_request(LIVE)
    restart_db = {**RESTART_API, "target": "postgres"}
    refusing = Plans(restart_db)

    assert run(planned_job(planning, Plans(None)), failed) == "done"
    assert run(planned_job(planning, refusing), refused) == "done"

    nothing = plan_message(planning, failed.investigation_id)
    assert (nothing.plan, nothing.review) == (None, None)
    assert (nothing.problem or "").startswith("No model gave a plan: claude-opus-5-5: Refused")
    assert [(m.calls, m.failed) for m in nothing.models] == [(0, 1)]
    check = plan_message(planning, refused.investigation_id)
    assert check.plan is not None
    assert (check.plan.model_dump(mode="json"), check.problem) == (restart_db, None)
    assert check.review is not None
    review = check.review.model_dump(mode="json")
    assert (review["status"], review["by"], review["model"]) == ("refuse", "check", None)
    [issue] = review["issues"]
    assert (issue["kind"], issue["quote"]) == ("code_check", "restart_service of postgres")
    assert issue["reason"].startswith("postgres is a database")
    # The plan was answered and billed; the code checks refused it, and no model reviewed it.
    assert [(m.calls, m.failed) for m in check.models] == [(1, 0)]
    assert refusing.reviews == []


def test_a_plan_is_not_reviewed_while_the_reviewer_is_switched_off(planning: Engine) -> None:
    switch(planning, "reviewer", False)
    plans = Plans(RESTART_API)
    request = new_request(LIVE)

    assert run(planned_job(planning, plans), request) == "done"

    message = plan_message(planning, request.investigation_id)
    assert message.plan is not None
    assert message.review is not None
    review = message.review.model_dump(mode="json")
    assert (review["status"], review["reason"]) == ("not_reviewed", "the reviewer is switched off")
    assert plans.reviews == []


def test_a_plan_is_still_made_without_a_snapshot(planning: Engine) -> None:
    plans = Plans({**RESTART_API, "kind": "none", "target": "postgres"})
    request = new_request(LIVE)

    assert run(planned_job(planning, plans, snapshot=None), request) == "done"

    assert plan_message(planning, request.investigation_id).plan is not None
    assert "No snapshot could be read" in plans.requests[0].text
