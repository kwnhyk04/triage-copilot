"""Runs one scenario through the real path and collects what scoring reads.

    uv run python evals/harness/run.py bad-deploy-1 [--run 1]     (make smoke)

The real path: the sim's admin port activates the scenario, its alert goes to the gateway's
webhook as a new tenant, the gateway queues the investigation, the agent runs it, and the report
comes back to the gateway's mirror tables, whose copy scoring reads. Each run gets its own tenant
with an opaque name, so repeated runs never join an earlier run's alert group and nothing the
agent can see names the scenario. eval.py drives whole splits with these functions; this script
runs one scenario, keeping its result in evals/reports/runs/smoke/. A run that parks because the
day's model quota is spent stops with exit code 2; the same command after the quota resets
(midnight Pacific) resumes it with the run's anchor and seed, so the data is the same.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
from sqlalchemy import Engine, create_engine, text

from agent.runbooks import embedding_info
from py_clients.clients import AgentClient, AlertsClient, ApiError, GatewayClient
from py_clients.generated.gateway import AlertmanagerWebhook

REPO = Path(__file__).resolve().parents[2]
SMOKE = REPO / "evals" / "reports" / "runs" / "smoke"
QUOTA_STOP = 2
FINISHED = ("resolved", "awaiting_approval")
Json = dict[str, Any]
REPORT = text("""
    SELECT r.status, r.report, r.recorded_at, r.created_at, g.created_at AS intake
    FROM gateway.investigation_reports r
    JOIN gateway.alert_groups g ON g.investigation_id = r.investigation_id
    WHERE r.investigation_id = :id
""")
STEPS = text("""
    SELECT seq, kind, summary, tool_name, tool_call_id, result, tool_status, tokens_input,
           tokens_output, recorded_at
    FROM gateway.investigation_steps WHERE investigation_id = :id ORDER BY seq
""")
# Approvals and executions of the run's proposals: nothing approves them, so both stay 0.
DECISIONS = text("""
    SELECT (SELECT count(*) FROM gateway.approvals a
              JOIN gateway.proposed_actions p ON p.id = a.action_id
             WHERE p.investigation_id = :id),
           (SELECT count(*) FROM gateway.executions e
              JOIN gateway.proposed_actions p ON p.id = e.action_id
             WHERE p.investigation_id = :id)
""")
INVESTIGATION = text("SELECT status, budget_tokens_used FROM agent.investigations WHERE id = :id")
TOOL_CALLS = text("""
    SELECT id, name, args, error FROM agent.tool_calls
    WHERE investigation_id = :id ORDER BY created_at, id
""")
MODEL_CALLS = text("""
    SELECT role, provider, model, outcome, input_tokens, output_tokens, cached_tokens,
           cache_write_tokens, thought_tokens, input_bound, max_output_tokens, latency_ms, error,
           prompt_sha
    FROM agent.model_calls WHERE investigation_id = :id ORDER BY id
""")


class RunError(Exception):
    """The run cannot go on: a service is down or answered wrongly, or the investigation failed."""


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default
    if value is None:
        raise RunError(f"{name} is required (make passes .env)")
    return value


def engines() -> tuple[Engine, Engine]:
    """The agent's and the gateway's databases, read-only use."""
    gateway = env("GATEWAY_DATABASE_URL").replace("postgres://", "postgresql+psycopg://", 1)
    return create_engine(env("AGENT_DATABASE_URL")), create_engine(gateway)


def activate(scenario: str, seed: int | None = None, anchor: str | None = None) -> Json:
    """Makes the sim serve the scenario, on its admin port, which only the harness knows."""
    admin = env("SIM_ADMIN_URL", "http://127.0.0.1:4231")
    body = {k: v for k, v in (("seed", seed), ("anchor", anchor)) if v is not None}
    try:
        response = httpx2.post(
            f"{admin}/admin/scenarios/{scenario}/activate", json=body, timeout=10
        )
    except httpx2.HTTPError as error:
        raise RunError(f"the sim's admin port at {admin} is unreachable: {error}") from None
    if response.status_code != 200:
        raise RunError(f"activating {scenario} answered {response.status_code}: {response.text}")
    return response.json()


def tenant_token(name: str) -> str:
    """A new tenant's webhook token, from the gateway's CLI."""
    pnpm = shutil.which("pnpm") or "pnpm"
    command = [pnpm, "--silent", "--filter", "@triage/gateway", "tenant:token", name]
    out = subprocess.run(  # noqa: S603 - fixed command, our own CLI
        command, cwd=REPO, capture_output=True, text=True, check=True, timeout=60
    )
    return out.stdout.strip().splitlines()[-1]


def start(scenario: str) -> Json:
    """Activates the scenario, posts its alert as a new tenant, and finds the investigation."""
    body = activate(scenario)
    stamp = f"{scenario}|{time.time_ns()}|{os.getpid()}"
    tenant = "eval-" + hashlib.sha256(stamp.encode()).hexdigest()[:12]
    gateway = env("GATEWAY_URL", "http://127.0.0.1:4200")
    with AlertsClient(gateway, tenant_token(tenant)) as alerts:
        accepted = alerts.post(AlertmanagerWebhook.model_validate(body["alertmanager"]))
    group = str(accepted.group_id)
    with GatewayClient(gateway, env("GATEWAY_API_KEY")) as client:
        deadline = time.monotonic() + 30
        while True:
            page = client.list_investigations(limit=100)
            found = [i for i in page.items if str(i.group_id) == group]
            if found:
                break
            if time.monotonic() > deadline:
                raise RunError(f"no investigation for alert group {group}")
            time.sleep(1)
    return {
        "scenario": scenario,
        "sha256": body["sha256"],
        "seed": body["seed"],
        "anchor": body["anchor"],
        "down": body["down"],
        "tenant": tenant,
        "group_id": group,
        "investigation_id": str(found[0].id),
        "resumed": False,
    }


def resume(state: Json) -> None:
    """Serves the run's world again, same scenario, anchor and seed, before the agent resumes."""
    body = activate(state["scenario"], state["seed"], state["anchor"])
    if body["sha256"] != state["sha256"]:
        raise RunError(f"{state['scenario']} changed since its run started; it cannot resume")


def wait(investigation: UUID, within: float) -> str:
    """The investigation's final status, or "quota" while it waits for tomorrow's quota."""
    agent = AgentClient(env("AGENT_URL", "http://127.0.0.1:4210"), env("AGENT_SERVICE_TOKEN"))
    deadline = time.monotonic() + within
    with agent:
        while time.monotonic() < deadline:
            try:
                state = agent.get_investigation_state(investigation)
            except ApiError as error:
                # The agent knows an investigation once it has received its message.
                if error.status != 404:
                    raise
                time.sleep(2)
                continue
            if state.status in FINISHED:
                return str(state.status)
            if state.status == "failed":
                raise RunError(f"investigation {investigation} failed; make dlq-replay resumes it")
            if state.waiting_for == "budget" and not state.resumable:
                return "quota"
            time.sleep(2)
    raise RunError(f"investigation {investigation} did not finish within {within:.0f} s")


def mirrored(investigation: str, within: float = 120) -> None:
    """Waits for the report to reach the gateway's mirror, behind its steps."""
    _, gateway_db = engines()
    deadline = time.monotonic() + within
    try:
        while time.monotonic() < deadline:
            with gateway_db.connect() as conn:
                if conn.execute(REPORT, {"id": investigation}).one_or_none():
                    return
            time.sleep(1)
    finally:
        gateway_db.dispose()
    raise RunError(f"the report of {investigation} did not reach the gateway in {within:.0f} s")


def collect(state: Json) -> Json:
    """The gateway's copy of the report and steps, and the agent's tool and model calls."""
    inv = {"id": state["investigation_id"]}
    agent_db, gateway_db = engines()
    with agent_db.connect() as conn:
        investigation = conn.execute(INVESTIGATION, inv).one()
        tool_calls = [dict(c) for c in conn.execute(TOOL_CALLS, inv).mappings()]
        model_calls = [dict(c) for c in conn.execute(MODEL_CALLS, inv).mappings()]
    with gateway_db.connect() as conn:
        report = conn.execute(REPORT, inv).one()
        steps = [dict(s) for s in conn.execute(STEPS, inv).mappings()]
        approvals, executions = conn.execute(DECISIONS, inv).one()
    agent_db.dispose()
    gateway_db.dispose()
    cache = os.environ.get("FASTEMBED_CACHE_PATH")
    return {
        **state,
        "status": investigation.status,
        "tokens": investigation.budget_tokens_used,
        "report_status": report.status,
        "report": report.report,
        # Both on the database's clock: the alert group's intake and the report step.
        "seconds": (report.recorded_at - report.intake).total_seconds(),
        "delivered_after_seconds": (report.created_at - report.recorded_at).total_seconds(),
        "steps": steps,
        "tool_calls": tool_calls,
        "model_calls": model_calls,
        "models": sorted({f"{c['role']}={c['model']}" for c in model_calls}),
        "approvals": approvals,
        "executions": executions,
        "embedding": embedding_info(Path(cache) if cache else None),
    }


def show(result: Json) -> None:
    lines = [
        f"{result['scenario']}: {result['status']}, {result['tokens']} tokens, "
        f"{result['seconds']:.0f} s"
    ]
    for step in result["steps"]:
        if step["kind"] != "tool_call":
            continue
        if step["tokens_input"] is not None:
            lines.append(f"  call   {step['tool_name']}: {step['summary']}")
        else:
            shown = (step["result"] or step["summary"]).replace("\n", " | ")[:150]
            lines.append(f"  result {step['tool_name']} ({step['tool_status']}): {shown}")
    for c in result["model_calls"]:
        lines.append(
            f"  model {c['role']} {c['model']} {c['outcome']}: in {c['input_tokens']}"
            f" (bound {c['input_bound']}, cached {c['cached_tokens']}), out {c['output_tokens']}"
            f" (thinking {c['thought_tokens']}), {c['latency_ms']} ms"
        )
    lines.append(json.dumps(result["report"], indent=2))
    lines.append(f"models: {result['models']}; embedding: {result['embedding']}")
    print("\n".join(lines))  # noqa: T201 - CLI output


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals/harness/run.py")
    parser.add_argument("scenario")
    parser.add_argument("--run", type=int, default=1)
    parser.add_argument("--within", type=float, default=900.0, help="seconds to wait")
    args = parser.parse_args()
    SMOKE.mkdir(parents=True, exist_ok=True)
    result_file = SMOKE / f"{args.scenario}-run{args.run}.json"
    state_file = SMOKE / f"{args.scenario}-run{args.run}.state.json"
    if result_file.exists():
        show(json.loads(result_file.read_text(encoding="utf-8")))
        return
    try:
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            resume(state)
            state["resumed"] = True
        else:
            state = start(args.scenario)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if wait(UUID(state["investigation_id"]), args.within) == "quota":
            print("quota stop: today's model quota is spent; run again after it resets")  # noqa: T201
            sys.exit(QUOTA_STOP)
        mirrored(state["investigation_id"])
        result = collect(state)
    except RunError as error:
        sys.exit(f"run: {error}")
    result_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    state_file.unlink()
    show(result)


if __name__ == "__main__":
    main()
