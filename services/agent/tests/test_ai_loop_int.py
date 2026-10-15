"""The agent loop against a scratch database and the sim in-process, with fake models.

The hard token cap, the degraded-tool report, log pagination, propose_action with report
delivery through the outbox, a rejected report, and runbook search with task prefixes.
"""

import asyncio
import hashlib
import json
import os
import random
import re
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx2
import pytest
from sqlalchemy import Connection, Engine, create_engine, func, select, text
from test_workers_int import new_request, recorded
from types_boto3_sqs import SQSClient

import agent.job
from agent.db import Investigation, ModelCall, Outbox, ProposedAction
from agent.job import DEGRADED_CEILING, MAX_TOKENS, RESULT_BYTES, Job
from agent.model import (
    BudgetRefused,
    HypothesisDraft,
    ModelTurn,
    Report,
    ReportDraft,
    ToolRequest,
    TurnRequest,
    Usage,
    is_model_turn,
)
from agent.model import ModelCall as Call
from agent.outbox import relay_once, sqs_sender
from agent.runbooks import DOCUMENT, QUERY, FastEmbedder, RunbookSearch, ingest
from agent.steps import StepRecordedV2, TokenUsage
from agent.tools import Tool, ToolContext, ToolResult, Tools
from py_clients.clients import AlertContextClient
from py_clients.generated.messages.report_recorded_v1_schema import ReportRecordedV1
from py_clients.generated.messages.step_recorded_v3_schema import StepRecordedV3
from sim.main import Settings as SimSettings
from sim.main import create_app as sim_app

if TYPE_CHECKING:
    from conftest import ScratchDb

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
SCENARIOS = REPO / "evals" / "scenarios"
Plan = ToolRequest | Callable[[Sequence[StepRecordedV2]], ToolRequest | ReportDraft]


@pytest.fixture
def engine(scratch_db: "ScratchDb") -> Iterator[Engine]:
    scratch_db.migrate_agent("head")
    engine = create_engine(scratch_db.agent_url)
    yield engine
    engine.dispose()


class Planned:
    """Plays its plan in order, like ScriptedModel; a callable entry sees the recorded steps, so
    it can cite their result ids. A forced report plays the plan's last callable. It stays within
    each turn's budget, as a provider would."""

    def __init__(self, plan: Sequence[Plan], tokens: TokenUsage | None = None) -> None:
        self.plan = list(plan)
        self.tokens = tokens or TokenUsage(input=1_000, output=100)

    async def turn(self, request: TurnRequest) -> ModelTurn:
        limits = request.budget.limits("planned")
        if limits is None:
            raise BudgetRefused
        bound, max_output = limits
        done = sum(is_model_turn(step) for step in request.steps)
        entry = self.plan[-1] if request.allowed is not None else self.plan[done]
        action = entry(request.steps) if callable(entry) else entry
        tokens = TokenUsage(
            input=min(self.tokens.input, bound), output=min(self.tokens.output, max_output)
        )
        usage = Usage(input=tokens.input, output=tokens.output)
        call = Call("primary", "planned", "planned", "ok", usage, bound, max_output)
        return ModelTurn(action=action, tokens=tokens, calls=[call], model="planned")

    def available(self, conn: Connection) -> bool:
        return True


def ask(tool: str, **args: Any) -> ToolRequest:
    return ToolRequest(tool=tool, args=args, reason=f"Run {tool}.")


def ids(steps: Sequence[StepRecordedV2], tool: str) -> list[str]:
    """Ids of the successful results of a tool, oldest first."""
    return [
        s.tool_call_id or ""
        for s in steps
        if s.tool_name == tool and not is_model_turn(s) and s.result is not None
    ]


def report(confidence: float, *tools: str) -> Callable[[Sequence[StepRecordedV2]], ReportDraft]:
    def draft(steps: Sequence[StepRecordedV2]) -> ReportDraft:
        cited = [i for tool in tools for i in ids(steps, tool)]
        hypothesis = HypothesisDraft(
            root_cause_class="bad_deploy",
            service="checkout",
            confidence=confidence,
            summary="checkout v1.8.0 broke checkout.",
            evidence_ids=cited,
        )
        return ReportDraft(summary="checkout v1.8.0 broke checkout.", hypotheses=[hypothesis])

    return draft


def alerts_client() -> AlertContextClient:
    context = {
        "investigation_id": str(uuid.uuid4()),
        "group_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "group_key": '{}:{alertname="CheckoutHighErrorRate"}',
        "window_start": "2026-10-14T12:00:05Z",
        "severity": "critical",
        "alert_count": 1,
        "alerts": [
            {
                "fingerprint": "8a0461e5e216f2b5",
                "labels": {"alertname": "CheckoutHighErrorRate", "service": "checkout"},
                "starts_at": "2026-10-14T12:00:05Z",
                "status": "firing",
            }
        ],
    }
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=context))
    return AlertContextClient("http://gateway.test", "token", transport=transport)


def sim(directory: Path = SCENARIOS) -> httpx2.AsyncClient:
    app = sim_app(SimSettings(sim_scenarios_dir=directory))
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://sim")


async def activate(client: httpx2.AsyncClient, scenario: str, anchor: datetime) -> None:
    response = await client.post(
        f"/admin/scenarios/{scenario}/activate", json={"anchor": anchor.isoformat()}
    )
    assert response.status_code == 200, response.text


def job_for(engine: Engine, model: Planned, tools: dict[str, Tool]) -> Job:
    return Job(engine, model, tools, ai_enabled=True, daily_token_budget=10**9, tenant_cap=5)


def final(engine: Engine, inv: UUID) -> tuple[str, Report, int]:
    query = select(
        Investigation.status, Investigation.final_report, Investigation.budget_tokens_used
    )
    with engine.connect() as conn:
        status, body, used = conn.execute(query.where(Investigation.id == inv)).one()
    return status, Report.model_validate(body), used


def test_no_investigation_records_more_than_the_token_cap(engine: Engine) -> None:
    """Models that always fill their input bound and their output allowance, with tool results
    of random sizes, never take an investigation past MAX_TOKENS."""

    class Greedy(Planned):
        def __init__(self, seed: int) -> None:
            super().__init__([])
            self.random = random.Random(seed)  # noqa: S311 - test data

        async def turn(self, request: TurnRequest) -> ModelTurn:
            limits = request.budget.limits("greedy")
            if limits is None:
                raise BudgetRefused
            bound, max_output = limits
            more = request.allowed is None and self.random.random() > 0.1
            action = ask("query_metrics", query="up") if more else report(0.5)(request.steps)
            usage = Usage(input=bound, output=max_output)
            call = Call("primary", "greedy", "greedy", "ok", usage, bound, max_output)
            tokens = TokenUsage(input=bound, output=max_output)
            return ModelTurn(action=action, tokens=tokens, calls=[call], model="greedy")

    sizes = random.Random(7)  # noqa: S311 - test data

    async def big(ctx: ToolContext, args: dict[str, Any]) -> str:
        return "x" * sizes.randint(100, 2 * RESULT_BYTES)

    for seed in range(30):
        request = new_request()
        job = job_for(engine, Greedy(seed), {"query_metrics": big})
        assert asyncio.run(job.run(request, asyncio.Event())) == "done"
        _, _, used = final(engine, request.investigation_id)
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    ModelCall.input_tokens, ModelCall.output_tokens, ModelCall.input_bound
                ).where(ModelCall.investigation_id == request.investigation_id)
            ).all()
        assert used == sum(i + o for i, o, _ in rows) <= MAX_TOKENS, seed
        assert all(i <= (b or 0) for i, _, b in rows), seed


def test_a_turn_that_cannot_fit_ends_with_the_jobs_partial_report(engine: Engine) -> None:
    request = new_request()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO agent.investigations (id, budget_tokens_used) VALUES (:id, :used)"),
            {"id": request.investigation_id, "used": MAX_TOKENS - 2_000},
        )
    model = Planned([ask("query_metrics", query="up")])

    assert asyncio.run(job_for(engine, model, {}).run(request, asyncio.Event())) == "done"

    status, done, used = final(engine, request.investigation_id)
    assert (status, done.partial, done.hypotheses) == ("resolved", True, [])
    assert used == MAX_TOKENS - 2_000


def test_a_degraded_metrics_backend_is_missing_evidence_and_caps_confidence(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_wait(attempt: int) -> float:
        return 0.0

    monkeypatch.setattr(agent.job, "backoff", no_wait)
    fired = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    request = new_request().model_copy(update={"requested_at": fired})
    plan: list[Plan] = [
        ask("query_metrics", query='http_error_ratio{service="checkout"}'),
        ask("query_logs", query='{service="checkout", level="error"}'),
        report(0.9, "query_logs"),
    ]

    async def main() -> None:
        async with sim() as client:
            await activate(client, "bad-deploy", fired)
            await client.put("/admin/down", json={"tool": "metrics"})
            tools = Tools(client, alerts_client(), None, None).all()
            outcome = await job_for(engine, Planned(plan), tools).run(request, asyncio.Event())
            assert outcome == "done"

    asyncio.run(main())

    status, done, _ = final(engine, request.investigation_id)
    missing = " ".join(m.root for m in done.missing_evidence)
    assert status == "resolved"
    assert done.missing_evidence[0].root.startswith("query_metrics failed")
    assert "metrics backend is down" in missing
    assert done.hypotheses[0].confidence == DEGRADED_CEILING < 0.9
    steps = [s for _, s in recorded(engine, request.investigation_id)]
    assert ids(steps, "query_metrics") == []


def flood(tmp_path: Path) -> Path:
    """The bad-deploy scenario with one log stream of 5,000 lines in the 50 minutes before."""
    scenario = json.loads((SCENARIOS / "bad-deploy.json").read_text(encoding="utf-8"))
    scenario["id"] = "log-flood"
    scenario["logs"] = [
        {
            "labels": {"service": "checkout", "level": "info"},
            "per_minute": 100,
            "from": "-50m",
            "to": "+0m",
            "lines": ["GET /cart 200 {ms}ms request_id={rid}"],
        }
    ]
    (tmp_path / "log-flood.json").write_text(json.dumps(scenario), encoding="utf-8")
    return tmp_path


def test_log_pages_cover_5000_lines_at_most_200_each(tmp_path: Path) -> None:
    fired = datetime.now(UTC).replace(microsecond=0)
    pages: list[int] = []

    async def summarize(page: str, allowance: int) -> tuple[str | None, list[Call]]:
        pages.append(len(page.splitlines()))
        return f"{pages[-1]} GET /cart lines", []

    async def main() -> None:
        async with sim(flood(tmp_path)) as client:
            await activate(client, "log-flood", fired)
            tools = Tools(client, alerts_client(), None, summarize)
            ctx = ToolContext(uuid.uuid4(), fired, 100_000)
            args: dict[str, Any] = {"query": '{service="checkout"}', "minutes_before": 60}
            first = await tools.query_logs(ctx, args)
            assert isinstance(first, str)
            pages.append(int(first.split(" lines:", 1)[0]))
            cursor = re.search(r"next_cursor: (\S+)", first)
            while cursor and cursor.group(1) != "none":
                out = await tools.query_logs(ctx, args | {"cursor": cursor.group(1)})
                assert isinstance(out, ToolResult)
                assert out.text.startswith("Summary of")
                cursor = re.search(r"next_cursor: (\S+)", out.text)

    asyncio.run(main())

    assert sum(pages) == 5_000
    assert pages[0] == 100
    assert max(pages) <= 200


def test_paging_through_logs_keeps_the_context_within_budget(
    engine: Engine, tmp_path: Path
) -> None:
    fired = datetime.now(UTC).replace(microsecond=0)
    request = new_request().model_copy(update={"requested_at": fired})
    query: dict[str, Any] = {"query": '{service="checkout"}', "minutes_before": 60}

    def next_page(steps: Sequence[StepRecordedV2]) -> ToolRequest:
        last = next(s for s in reversed(steps) if s.result is not None)
        found = re.search(r"next_cursor: (\S+)", last.result or "")
        return ask("query_logs", **query, cursor=found.group(1) if found else "")

    async def summarize(page: str, allowance: int) -> tuple[str | None, list[Call]]:
        size = len(page.encode())
        usage = Usage(input=size // 4, output=40)
        call = Call("small", "fake", "small-model", "ok", usage, size, 400)
        return f"Checkout served {len(page.splitlines())} GET /cart requests.", [call]

    plan: list[Plan] = [ask("query_logs", **query), *[next_page] * 20, report(0.5, "query_logs")]

    async def main() -> None:
        async with sim(flood(tmp_path)) as client:
            await activate(client, "log-flood", fired)
            tools = Tools(client, alerts_client(), None, summarize).all()
            outcome = await job_for(engine, Planned(plan), tools).run(request, asyncio.Event())
            assert outcome == "done"

    asyncio.run(main())

    steps = [s for _, s in recorded(engine, request.investigation_id)]
    pages = [s.result or "" for s in steps if s.tool_name == "query_logs" and not is_model_turn(s)]
    assert len(pages) == 15
    assert pages[0].startswith("100 lines:")
    assert all(page.startswith("Summary of 200 lines") for page in pages[1:])
    # Fifteen raw pages would fill about 300 KB of context; the summaries keep it small.
    assert sum(len(page.encode()) for page in pages) < 40_000
    with engine.connect() as conn:
        small = conn.scalar(
            select(func.count()).where(
                ModelCall.investigation_id == request.investigation_id, ModelCall.role == "small"
            )
        )
    _, done, used = final(engine, request.investigation_id)
    assert (small, done.partial) == (14, True)
    assert used <= MAX_TOKENS


def test_a_proposal_waits_for_a_human_and_the_report_reaches_the_events_queue(
    engine: Engine, sqs: SQSClient, queues: tuple[str, str]
) -> None:
    fired = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    request = new_request().model_copy(update={"requested_at": fired})
    inv = request.investigation_id

    def propose(steps: Sequence[StepRecordedV2]) -> ToolRequest:
        return ask(
            "propose_action",
            kind="rollback_deploy",
            target="checkout",
            params={"to_version": "v1.7.3"},
            rationale="Errors began right after v1.8.0.",
            evidence_ids=ids(steps, "query_metrics"),
        )

    plan: list[Plan] = [
        ask("query_metrics", query='http_error_ratio{service="checkout"}'),
        propose,
        report(0.8, "query_metrics"),
    ]

    async def main() -> None:
        async with sim() as client:
            await activate(client, "bad-deploy", fired)
            tools = Tools(client, alerts_client(), None, None).all()
            outcome = await job_for(engine, Planned(plan), tools).run(request, asyncio.Event())
            assert outcome == "done"

    asyncio.run(main())

    status, done, _ = final(engine, inv)
    a = ProposedAction
    query = select(a.id, a.status, a.kind, a.evidence_ids).where(a.investigation_id == inv)
    with engine.connect() as conn:
        action = conn.execute(query).one()
        events = conn.scalar(select(func.count()).select_from(Outbox)) or 0
    assert status == "awaiting_approval"
    assert (action.status, action.kind) == ("pending", "rollback_deploy")
    assert [str(p.action_id) for p in done.proposed_actions] == [str(action.id)]
    assert action.evidence_ids == ids([s for _, s in recorded(engine, inv)], "query_metrics")

    queue_url, _ = queues
    send = sqs_sender(sqs, queue_url.rsplit("/", 1)[1])
    while relay_once(engine, send):
        pass
    received: list[tuple[str, dict[str, Any]]] = []
    while len(received) < events:
        batch = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
            MessageAttributeNames=["message_type"],
        ).get("Messages", [])
        for message in batch:
            kind = message.get("MessageAttributes", {})["message_type"].get("StringValue", "")
            received.append((kind, json.loads(message.get("Body", "{}"))))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message.get("ReceiptHandle", ""))
    kinds = [kind for kind, _ in received]
    assert kinds == ["step-recorded"] * (events - 1) + ["report-recorded"]
    seqs = [StepRecordedV3.model_validate(body).seq for _, body in received[:-1]]
    assert seqs == sorted(seqs)
    assert ReportRecordedV1.model_validate(received[-1][1]).status.value == "awaiting_approval"


def test_a_report_citing_unknown_results_goes_back_to_the_model(engine: Engine) -> None:
    async def metrics(ctx: ToolContext, args: dict[str, Any]) -> str:
        return "http_error_ratio rose to 0.14"

    def invented(steps: Sequence[StepRecordedV2]) -> ReportDraft:
        draft = report(0.8, "query_metrics")(steps)
        hypothesis = draft.hypotheses[0].model_copy(update={"evidence_ids": ["tc_ZZZZZZZZZZ"]})
        return draft.model_copy(update={"hypotheses": [hypothesis]})

    request = new_request()
    plan: list[Plan] = [ask("query_metrics", query="up"), invented, report(0.8, "query_metrics")]

    job = job_for(engine, Planned(plan), {"query_metrics": metrics})
    assert asyncio.run(job.run(request, asyncio.Event())) == "done"

    steps = [s for _, s in recorded(engine, request.investigation_id)]
    rejected = [s for s in steps if s.tool_name == "submit_report" and not is_model_turn(s)]
    assert "evidence_ids name no successful tool result: tc_ZZZZZZZZZZ" in rejected[0].summary
    _, done, _ = final(engine, request.investigation_id)
    assert [e.root for e in done.hypotheses[0].evidence_ids] == ids(steps, "query_metrics")


class Recording:
    """A deterministic embedder that keeps every text it was given."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.texts.extend(texts)
        return [[b / 255 for b in hashlib.sha512(t.encode()).digest()] * 12 for t in texts]


def test_runbook_search_applies_the_nomic_task_prefixes(engine: Engine) -> None:
    embedder = Recording()

    chunks = ingest(engine, embedder, REPO / "runbooks")
    found = asyncio.run(RunbookSearch(engine, embedder).search("connection pool exhausted", 3))

    assert chunks == len(embedder.texts) - 1 > 0
    assert all(t.startswith(DOCUMENT) for t in embedder.texts[:-1])
    assert embedder.texts[-1] == QUERY + "connection pool exhausted"
    assert len(found) == 3


def test_the_cached_embedding_model_ranks_the_right_runbook_first(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = os.environ.get("FASTEMBED_CACHE_PATH")
    if not cache:
        pytest.skip("FASTEMBED_CACHE_PATH is unset: run make embed-model and set it in .env")
    # Never download in tests: the model loads from the cache only.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    embedder = FastEmbedder(Path(cache))

    ingest(engine, embedder, REPO / "runbooks")
    question = "timeout acquiring connection from pool while the database waits on locks"
    found = asyncio.run(RunbookSearch(engine, embedder).search(question, 1))

    assert len(embedder.embed([QUERY + "ping"])[0]) == 768
    assert found[0][0] == "db-connection-pool-exhaustion.md"
