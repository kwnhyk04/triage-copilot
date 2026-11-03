"""The planner without a database: the plan's shape per app, what a valid plan is, the prompt and
its bound, the check on real targets (P2.3), and the plan's review (Part B), on fixtures of the
apps' catalogs."""

import asyncio
import copy
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CheckConstraint

from agent.db import Base
from agent.model import ModelCall, PlanRequest
from agent.planner import (
    PLAN_INPUT_BYTES,
    PLANNER_PROMPT_SHA,
    PLANNER_SYSTEM,
    SOURCES,
    PlanDraft,
    PlanKind,
    Planner,
    parse_plan,
    plan_problems,
    plan_refusal,
    plan_shape,
    planner_text,
)
from agent.reviewer import REVIEWER_PROMPT_SHA, REVIEWER_SYSTEM, index_refusal, source_index
from agent.switches import DEFAULTS
from py_clients.generated.gateway import AlertContext
from watch.catalog import CATALOGS

FA, FL = CATALOGS["freight-audit"], CATALOGS["filing-lens"]
FIRED = datetime(2026, 10, 26, 9, 55, tzinfo=UTC)
HEALTHY = "Up 3 hours (healthy)"
FA_CONTAINERS = ("api", "worker", "postgres", "redis", "minio", "egress-proxy", "tailscale")


def container(service: str, state: str = "running", status: str = HEALTHY) -> dict[str, Any]:
    return {
        "service": service,
        "name": f"freightaudit-prod-{service}-1",
        "image": f"{service}:1",
        "state": state,
        "status": status,
        "created": "2026-10-26T06:00:00Z",
    }


def snapshot(*changes: tuple[str, str, str], api: int | None = 200) -> dict[str, Any]:
    """FreightAudit's prod stack with everything up, the api's health check answering `api`, and
    each (service, state, status) change applied to its container."""
    states = {service: ("running", HEALTHY) for service in FA_CONTAINERS}
    for service, state, status in changes:
        states[service] = (state, status)
    return {
        "app": "freight-audit",
        "stack": "prod",
        "taken_at": "2026-10-26T09:56:00Z",
        "alert": {"alertname": "HealthCheckFailed", "service": "api", "source": "health"},
        "health": [
            {
                "service": "api",
                "url": "https://[tailnet]/readyz",
                "status": api,
                "ms": 12,
                "error": None if api else "ConnectError",
                "body": "",
            }
        ],
        "containers": [container(s, *states[s]) for s in FA_CONTAINERS],
        "logs": {"api": ['{"level":50,"msg":"readiness check failed","check":"postgres"}']},
        "changes": {"recreated": [], "commits": []},
        "problems": [],
    }


def draft(kind: PlanKind, target: str, files: list[str] | None = None) -> PlanDraft:
    rationale = "The evidence [tc_01JA0SNAP0] shows it; check the health check after."
    return PlanDraft(kind=kind, target=target, files=files or [], rationale=rationale)


def test_the_plan_s_shape_names_only_the_app_s_own_services() -> None:
    shape = plan_shape(FA)
    assert shape.name == "submit_plan"
    properties = shape.parameters["properties"]
    assert properties["target"]["enum"] == [s.name for s in FA.services]
    assert properties["kind"]["enum"] == ["none", "restart_service", "code_fix"]
    assert set(shape.parameters["required"]) == {"kind", "target", "files", "rationale"}
    assert shape.parameters["additionalProperties"] is False
    assert plan_shape(FL).parameters["properties"]["target"]["enum"] == [
        s.name for s in FL.services
    ]


def test_a_valid_plan_holds_a_catalog_target_and_free_text_only_in_its_rationale() -> None:
    problems = plan_problems(FA)
    good: dict[str, Any] = {
        "kind": "none",
        "target": "postgres",
        "files": [],
        "rationale": "A human must look.",
    }
    assert problems(good) is None
    for bad in (
        {**good, "target": "checkout"},
        {**good, "kind": "rollback_deploy"},
        {**good, "rationale": "x" * 2001},
        {**good, "note": "restart everything"},
        {k: v for k, v in good.items() if k != "rationale"},
    ):
        assert problems(bad) is not None
    assert "checkout" in (problems({**good, "target": "checkout"}) or "")
    # Files belong to a code fix only; another kind keeps none of them.
    listed = parse_plan({**good, "files": ["apps/api/src/db.ts"]}, FA)
    assert listed.files == []


def snapshot_report() -> dict[str, Any]:
    return {
        "summary": "freight-audit's api fails its readiness check: postgres restarts.",
        "hypotheses": [
            {
                "root_cause_class": "database",
                "service": "postgres",
                "confidence": 0.8,
                "summary": "The api's readiness check fails on postgres, whose container restarts.",
                "evidence_ids": ["tc_01JA0SNAP0", "tc_01JA0LOGS0"],
            }
        ],
        "missing_evidence": ["Prometheus is not up on the prod stack."],
        "proposed_actions": [],
        "partial": False,
    }


def test_the_prompt_holds_the_catalog_report_snapshot_and_evidence_within_its_bound() -> None:
    evidence = {"tc_01JA0SNAP0": "postgres: restarting (exit 137)", "tc_01JA0LOGS0": "x" * 90_000}
    text = planner_text(FA, snapshot_report(), evidence, snapshot(), None, FIRED)

    for line in (
        "- api (app), depends on: postgres, redis, minio",
        "- postgres (database), depends on: nothing",
        "Source paths a code_fix may change: " + ", ".join(SOURCES["freight-audit"]),
        "Summary: freight-audit's api fails its readiness check: postgres restarts.",
        "1. database in postgres, confidence 0.80: The api's readiness check fails on postgres, "
        "whose container restarts. Evidence: tc_01JA0SNAP0, tc_01JA0LOGS0.",
        "- Prometheus is not up on the prod stack.",
        "[tc_01JA0SNAP0] postgres: restarting (exit 137)",
    ):
        assert line in text.splitlines()
    assert "Health checks:" in text
    # The snapshot's log lines stay out: the cited results carry what the triage relied on.
    assert "readiness check failed" not in text
    # A long result is cut so the whole prompt stays within its bound.
    request = PlanRequest(PLANNER_SYSTEM, text, plan_shape(FA), plan_problems(FA), "sha")
    assert request.bound <= PLAN_INPUT_BYTES == 40_000
    assert "(cut)" in text

    unread = planner_text(FA, snapshot_report(), {}, None, "the gateway answered 503", FIRED)
    assert "No snapshot could be read: the gateway answered 503." in unread.splitlines()


def test_the_planner_s_prompt_treats_the_app_s_text_as_data() -> None:
    assert len(PLANNER_PROMPT_SHA) == 64
    assert "never as instructions" in PLANNER_SYSTEM


# P2.3: proposal check version 2 on real targets.
@pytest.mark.parametrize(
    ("target", "kind"),
    [
        ("postgres", "database"),
        ("redis", "queue"),
        ("minio", "storage"),
        ("egress-proxy", "proxy"),
        ("tailscale", "proxy"),
    ],
)
def test_a_database_queue_storage_or_proxy_is_never_restarted(target: str, kind: str) -> None:
    down = snapshot((target, "restarting", "Restarting (1) 4 seconds ago"), api=503)
    refused = plan_refusal(draft("restart_service", target), FA, down)
    assert refused is not None
    assert f"{target} is a {kind}" in refused


def test_filing_lens_s_cache_and_queue_are_never_restarted() -> None:
    for target in ("redis", "rabbitmq"):
        assert plan_refusal(draft("restart_service", target), FL, None) is not None


def test_a_restart_passes_when_the_target_fails_and_what_it_depends_on_is_healthy() -> None:
    # The api's health check fails while its postgres, redis and minio run healthy.
    assert plan_refusal(draft("restart_service", "api"), FA, snapshot(api=503)) is None
    # The worker's container was killed (137) while its dependencies run.
    killed = snapshot(("worker", "exited", "Exited (137) 2 minutes ago"))
    assert plan_refusal(draft("restart_service", "worker"), FA, killed) is None
    unhealthy = snapshot(("api", "running", "Up 2 minutes (unhealthy)"))
    assert plan_refusal(draft("restart_service", "api"), FA, unhealthy) is None


def test_a_restart_is_refused_when_its_target_is_up() -> None:
    refused = plan_refusal(draft("restart_service", "api"), FA, snapshot())
    assert refused is not None
    assert "shows api up" in refused


def test_a_restart_is_refused_while_a_dependency_fails() -> None:
    down = snapshot(("postgres", "restarting", "Restarting (1) 4 seconds ago"), api=503)
    refused = plan_refusal(draft("restart_service", "api"), FA, down)
    assert refused is not None
    assert "postgres" in refused
    gone = snapshot(api=503)
    gone["containers"] = [c for c in gone["containers"] if c["service"] != "redis"]
    assert "redis" in (plan_refusal(draft("restart_service", "api"), FA, gone) or "")


def test_a_restart_is_refused_without_a_snapshot_that_shows_the_containers() -> None:
    assert plan_refusal(draft("restart_service", "api"), FA, None) is not None
    unread = snapshot(api=503)
    unread["containers"] = []
    unread["problems"] = ["docker ps: docker timed out"]
    # The dependencies cannot be shown healthy.
    assert plan_refusal(draft("restart_service", "api"), FA, unread) is not None


def test_none_always_passes_and_a_code_fix_needs_allowlisted_source_files() -> None:
    assert plan_refusal(draft("none", "postgres"), FA, None) is None
    ok = ["apps/worker/src/extract.ts", "packages/llm/src/claude-first.ts"]
    assert plan_refusal(draft("code_fix", "worker", ok), FA, None) is None
    fl_ok = ["services/api/src/app/main.py"]
    assert plan_refusal(draft("code_fix", "api", fl_ok), FL, None) is None
    for files in (
        [],
        [".github/workflows/ci.yml"],
        ["apps/api/.env"],
        ["apps/api/src/../../../.env"],
        ["/etc/passwd"],
        ["C:/Windows/system32/x.ts"],
        ["apps\\api\\src\\x.ts"],
        ["pnpm-lock.yaml"],
        ["apps/web/next.config.ts"],
        ["apps/api/drizzle/0001.ts"],
        ["apps/api/src/AGENTS.md"],
        ["infra/docker-compose.yml"],
    ):
        assert plan_refusal(draft("code_fix", "api", files), FA, None) is not None, files
    # A code fix changes an app's or a worker's own code, never a database's.
    assert plan_refusal(draft("code_fix", "postgres", ok), FA, None) is not None


def test_a_code_fix_names_files_never_patterns() -> None:
    # High's memory-leak-1 named services/orders/**/*.py, which the allowlist matched (2026-11-03).
    for path in ("services/api/**/*.py", "services/api/src/*.py", "services/api/src/app?.py"):
        refused = plan_refusal(draft("code_fix", "api", [path]), FL, None)
        assert refused == f"{path} is a pattern, not a file: name each file it changes", path


def test_the_check_reads_the_snapshot_without_changing_it() -> None:
    down = snapshot(api=503)
    before = copy.deepcopy(down)
    plan_refusal(draft("restart_service", "api"), FA, down)
    assert json.dumps(down) == json.dumps(before)


# Part B: the review of every plan a model gave, the code checks first.
COMMIT = "4f1c2a9e0b7d5c3a1e9f7b5d3c1a9e7f5b3d1c9a"
NEW = "services/api/src/app/retry.py"
FIX: dict[str, Any] = {
    "kind": "code_fix",
    "target": "api",
    "files": ["services/api/src/app/main.py", NEW],
    "rationale": "Errors began with commit 9c1d2e3 [tc_01JA0GIT01]. Guard the lookup, then retry.",
}
OK: dict[str, Any] = {"verdict": "ok", "issues": []}
WARNING = {
    "kind": "fallback_step",
    "quote": "If errors persist, turn retry off.",
    "reason": "No cited result ties the retry flag to the errors.",
}
FORBIDDEN = {
    "kind": "forbidden_step",
    "quote": "Turn search-results-badges off to rule it out.",
    "reason": "Nothing the triage cites shows the flag caused the errors.",
}
Answer = dict[str, Any] | None


class Chain:
    """The plan's chain and the reviewer's Claude, each with its one answer (None: it failed), and
    what each was asked."""

    def __init__(self, plan: Answer, review: Answer = None, why: str | None = None) -> None:
        self.answer, self.verdict, self.why = plan, review, why
        self.plans: list[PlanRequest] = []
        self.reviews: list[PlanRequest] = []

    async def plan(self, request: PlanRequest) -> tuple[Answer, list[ModelCall]]:
        self.plans.append(request)
        outcome = "ok" if self.answer else "error"
        return self.answer, [ModelCall("planner", "anthropic", "claude-opus-5-5", outcome)]

    async def review(self, request: PlanRequest) -> tuple[Answer, list[ModelCall], str | None]:
        self.reviews.append(request)
        outcome = "ok" if self.verdict else "error"
        call = ModelCall("reviewer", "anthropic", "claude-sonnet-5-5", outcome)
        return self.verdict, [call], self.why


def unread(inv: UUID) -> AlertContext:
    raise ValueError("no gateway in these tests")


def index(folder: Path) -> Path:
    """watch's index of FilingLens at origin/main: main.py is there, and retry.py would be new."""
    files = ["services/api/src/app/main.py", "services/api/src/app/db.py", "README.md"]
    written = {"app": "filing-lens", "commit": COMMIT, "files": files}
    (folder / "filing-lens.json").write_text(json.dumps(written), encoding="utf-8")
    return folder


Planned = tuple[PlanDraft | None, str | None, dict[str, Any] | None, Sequence[ModelCall]]


def make(
    chain: Chain, sources: Path | None, reviewing: Callable[[], bool] = lambda: True
) -> Planned:
    """FilingLens's plan, with no snapshot, and its review, in the job's steps."""
    planner = Planner(chain, unread, chain, sources)

    async def steps() -> Planned:
        incident = await planner.incident(uuid4(), "filing-lens", snapshot_report(), {}, FIRED)
        plan, problem, calls = await planner.draft(incident)
        if plan is None:
            return None, problem, None, calls
        review, reviewed = await planner.review(incident, plan, reviewing)
        return plan, None, review, [*calls, *reviewed]

    return asyncio.run(steps())


def test_the_reviewer_switch_is_on_by_default_and_the_table_takes_every_switch() -> None:
    assert DEFAULTS["reviewer"] is True
    table = Base.metadata.tables["agent.switches"]
    [check] = [c for c in table.constraints if isinstance(c, CheckConstraint)]
    assert [name for name in DEFAULTS if f"'{name}'" not in str(check.sqltext)] == []


@pytest.mark.parametrize(
    ("answer", "indexed", "refused"),
    [
        (
            {**FIX, "target": "postgres"},
            True,
            "postgres is a database: a plan never restarts or changes a database, queue, cache, "
            "storage or proxy; plan none, and name the step a human must take",
        ),
        (
            {**FIX, "files": ["services/billing/app.py"]},
            True,
            f"services/billing/app.py is not in filing-lens's repo at origin/main ({COMMIT[:12]}), "
            "nor is its folder",
        ),
        # No index folder, or none watch wrote: no file can be checked, so the fix is refused.
        (
            FIX,
            False,
            "no source index of filing-lens at origin/main could be read, so no file can be "
            "checked",
        ),
        (
            {**FIX, "kind": "restart_service", "files": []},
            False,
            "no snapshot of filing-lens could be read, so nothing shows api failing: restart "
            "nothing",
        ),
    ],
)
def test_a_plan_the_code_checks_refuse_is_kept_with_their_reason_and_no_model_is_asked(
    tmp_path: Path, answer: dict[str, Any], indexed: bool, refused: str
) -> None:
    chain = Chain(answer, OK)
    plan, problem, review, calls = make(chain, index(tmp_path) if indexed else None)
    assert plan is not None
    assert (plan.model_dump(), problem) == (answer, None)
    assert review == {
        "status": "refuse",
        "by": "check",
        "model": None,
        "issues": [
            {"kind": "code_check", "quote": f"{plan.kind} of {plan.target}", "reason": refused}
        ],
        "reason": None,
        "new_files": [],
    }
    assert chain.reviews == []
    assert [call.role for call in calls] == ["planner"]


def test_square_brackets_and_braces_are_path_characters_and_the_index_decides() -> None:
    # Next.js routes such as FreightAudit's invoices/[id] page are real files (2026-11-03).
    page = "apps/web/app/invoices/[id]/page.tsx"
    for path in (page, "apps/web/app/[...slug]/page.tsx", "apps/api/src/routes/{invoice}.ts"):
        assert plan_refusal(draft("code_fix", "api", [path]), FA, None) is None, path
    # The index matches exactly: [id] names a folder, never a class of the letters i and d.
    index = source_index("freight-audit", COMMIT, [page, "apps/web/app/invoices/i/page.tsx"])
    assert index_refusal([page], "freight-audit", index) == (None, ())
    edit = "apps/web/app/invoices/[id]/edit.tsx"
    assert index_refusal([edit], "freight-audit", index) == (None, (edit,))
    other = "apps/web/app/invoices/[slug]/page.tsx"
    assert index_refusal([other], "freight-audit", index) == (
        f"{other} is not in freight-audit's repo at origin/main ({COMMIT[:12]}), nor is its folder",
        (),
    )


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        (OK, "ok"),
        ({"verdict": "flag", "issues": [WARNING]}, "flag"),
        # The issues decide: any forbidden step refuses, whatever verdict the model gave.
        ({"verdict": "flag", "issues": [WARNING, FORBIDDEN]}, "refuse"),
    ],
)
def test_the_reviewer_reads_what_the_planner_read_and_its_issues_decide_the_verdict(
    tmp_path: Path, answer: dict[str, Any], status: str
) -> None:
    chain = Chain(FIX, answer)
    plan, problem, review, calls = make(chain, index(tmp_path))
    assert (plan is not None, problem) == (True, None)
    assert review == {
        "status": status,
        "by": "model",
        "model": "claude-sonnet-5-5",
        "issues": answer["issues"],
        "reason": None,
        "new_files": [NEW],
    }
    [planned], [asked] = chain.plans, chain.reviews
    assert asked.text.startswith(planned.text)
    assert (asked.system, asked.prompt_sha) == (REVIEWER_SYSTEM, REVIEWER_PROMPT_SHA)
    assert f"- {NEW} (a new file; its folder exists at origin/main)" in asked.text.splitlines()
    assert [call.role for call in calls] == ["planner", "reviewer"]


def test_only_a_code_fix_needs_the_source_index() -> None:
    chain = Chain({**FIX, "kind": "none", "target": "postgres"}, OK)
    _, _, review, _ = make(chain, None)
    assert review is not None
    assert (review["status"], review["by"], review["new_files"]) == ("ok", "model", [])


def test_a_plan_is_not_reviewed_while_the_reviewer_is_switched_off(tmp_path: Path) -> None:
    chain = Chain(FIX, OK)
    plan, problem, review, calls = make(chain, index(tmp_path), lambda: False)
    assert (plan is not None, problem) == (True, None)
    assert review == {
        "status": "not_reviewed",
        "by": None,
        "model": None,
        "issues": [],
        "reason": "the reviewer is switched off",
        "new_files": [NEW],
    }
    assert chain.reviews == []
    assert [call.role for call in calls] == ["planner"]


def test_a_plan_is_not_reviewed_when_the_reviewer_cannot_answer(tmp_path: Path) -> None:
    chain = Chain(FIX, None, "its credits ended on 2026-11-10 (UTC)")
    plan, problem, review, calls = make(chain, index(tmp_path))
    # The plan stands, for a human to decide unreviewed.
    assert (plan is not None, problem) == (True, None)
    assert review == {
        "status": "not_reviewed",
        "by": None,
        "model": "claude-sonnet-5-5",
        "issues": [],
        "reason": "its credits ended on 2026-11-10 (UTC)",
        "new_files": [NEW],
    }
    outcomes = [(call.role, call.outcome) for call in calls]
    assert outcomes == [("planner", "ok"), ("reviewer", "error")]


def test_a_plan_no_model_gave_has_no_review() -> None:
    chain = Chain(None, OK)
    plan, problem, review, _ = make(chain, None)
    assert (plan, review) == (None, None)
    assert (problem or "").startswith("No model gave a plan: claude-opus-5-5")
    assert chain.reviews == []
