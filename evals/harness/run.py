"""Runs scenarios against the running stack (gateway, agent, sim) and keeps each run's result.

    uv run python evals/harness/run.py bad-deploy [--run 1]

Each run gets its own tenant, so repeated runs of a scenario never join an earlier run's alert
group. Results go to evals/reports/runs/<scenario>-run<n>.json, and a finished run is skipped
next time. A run that parks because the day's model quota is spent stops with exit code 3; run
the same command after the quota resets (midnight Pacific) and it resumes that investigation:
the sim gets the run's anchor and seed again, so the data is the same.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
from sqlalchemy import create_engine, text

from agent.runbooks import embedding_info
from py_clients.clients import AgentClient, AlertsClient, ApiError, GatewayClient
from py_clients.generated.gateway import AlertmanagerWebhook

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "evals" / "reports" / "runs"
QUOTA_STOP = 3
FINISHED = ("resolved", "awaiting_approval")
REPORT = text(
    "SELECT status, final_report, budget_tokens_used FROM agent.investigations WHERE id = :id"
)
STEPS = text(
    "SELECT seq, event FROM agent.investigation_steps WHERE investigation_id = :id ORDER BY seq"
)
CALLS = text("""
    SELECT role, provider, model, outcome, input_tokens, output_tokens, cached_tokens,
           thought_tokens, input_bound, max_output_tokens, latency_ms, error
    FROM agent.model_calls WHERE investigation_id = :id ORDER BY id
""")
MIRRORED = text(
    "SELECT status, recorded_at FROM gateway.investigation_reports WHERE investigation_id = :id"
)
MIRRORED_STEPS = text(
    "SELECT count(*) FROM gateway.investigation_steps WHERE investigation_id = :id"
)


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default
    if value is None:
        sys.exit(f"run: {name} is required (make passes .env)")
    return value


def tenant_token(name: str) -> str:
    """A new tenant's webhook token, from the gateway's CLI."""
    pnpm = shutil.which("pnpm") or "pnpm"
    command = [pnpm, "--silent", "--filter", "@triage/gateway", "tenant:token", name]
    out = subprocess.run(  # noqa: S603 - fixed command, our own CLI
        command, cwd=REPO, capture_output=True, text=True, check=True, timeout=60
    )
    return out.stdout.strip().splitlines()[-1]


def start(scenario: str, state_file: Path) -> dict[str, Any]:
    """Activates the scenario, posts its alert as a new tenant, and finds the investigation."""
    sim = env("SIM_URL", "http://127.0.0.1:4230")
    activation = httpx2.post(f"{sim}/admin/scenarios/{scenario}/activate", json={}, timeout=10)
    activation.raise_for_status()
    body = activation.json()
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    token = tenant_token(f"eval-{scenario}-{stamp}")
    gateway = env("GATEWAY_URL", "http://127.0.0.1:4200")
    with AlertsClient(gateway, token) as alerts:
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
                sys.exit(f"run: no investigation for alert group {group}")
            time.sleep(1)
    state = {
        "scenario": scenario,
        "seed": body["seed"],
        "anchor": body["anchor"],
        "group_id": group,
        "investigation_id": str(found[0].id),
    }
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def resume(state: dict[str, Any]) -> None:
    """Serves the run's data again: same scenario, anchor and seed."""
    sim = env("SIM_URL", "http://127.0.0.1:4230")
    body = {"seed": state["seed"], "anchor": state["anchor"]}
    url = f"{sim}/admin/scenarios/{state['scenario']}/activate"
    httpx2.post(url, json=body, timeout=10).raise_for_status()


def wait(investigation: UUID, within: float) -> str:
    """The investigation's final status, or "quota" when it waits for tomorrow's quota."""
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
            if state.waiting_for == "budget" and not state.resumable:
                return "quota"
            time.sleep(2)
    sys.exit(f"run: investigation {investigation} did not finish within {within:.0f} s")


def collect(state: dict[str, Any]) -> dict[str, Any]:
    """The report, each tool call and model turn (agent), and the gateway's mirror rows."""
    inv = {"id": state["investigation_id"]}
    agent_db = create_engine(env("AGENT_DATABASE_URL"))
    gateway_url = env("GATEWAY_DATABASE_URL").replace("postgres://", "postgresql+psycopg://", 1)
    gateway_db = create_engine(gateway_url)
    with agent_db.connect() as conn:
        report = conn.execute(REPORT, inv).one()
        steps = conn.execute(STEPS, inv).all()
        calls = conn.execute(CALLS, inv).mappings().all()
    with gateway_db.connect() as conn:
        mirrored = conn.execute(MIRRORED, inv).one_or_none()
        mirrored_steps = conn.scalar(MIRRORED_STEPS, inv)
    agent_db.dispose()
    gateway_db.dispose()
    cache = os.environ.get("FASTEMBED_CACHE_PATH")
    return {
        **state,
        "status": report.status,
        "tokens": report.budget_tokens_used,
        "report": report.final_report,
        "steps": [{"seq": seq, **event} for seq, event in steps],
        "model_calls": [dict(c) for c in calls],
        "models": sorted({f"{c['role']}={c['model']}" for c in calls}),
        "embedding": embedding_info(Path(cache) if cache else None),
        "gateway": {
            "report_status": mirrored.status if mirrored else None,
            "report_recorded_at": str(mirrored.recorded_at) if mirrored else None,
            "steps": mirrored_steps,
        },
    }


def show(result: dict[str, Any]) -> None:
    lines = [f"{result['scenario']}: {result['status']}, {result['tokens']} tokens"]
    for step in result["steps"]:
        if step["kind"] != "tool_call":
            continue
        if step.get("tokens"):
            lines.append(f"  call   {step['tool_name']}: {step['summary']}")
        else:
            shown = (step.get("result") or step["summary"]).replace("\n", " | ")[:150]
            lines.append(f"  result {step['tool_name']} ({step['latency_ms']} ms): {shown}")
    for c in result["model_calls"]:
        lines.append(
            f"  model {c['role']} {c['model']} {c['outcome']}: in {c['input_tokens']}"
            f" (bound {c['input_bound']}, cached {c['cached_tokens']}), out {c['output_tokens']}"
            f" (thinking {c['thought_tokens']}), {c['latency_ms']} ms"
        )
    lines.append(json.dumps(result["report"], indent=2))
    lines.append(f"gateway: {result['gateway']}")
    lines.append(f"models: {result['models']}; embedding: {result['embedding']}")
    print("\n".join(lines))  # noqa: T201 - CLI output


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals/harness/run.py")
    parser.add_argument("scenario")
    parser.add_argument("--run", type=int, default=1)
    parser.add_argument("--within", type=float, default=600.0, help="seconds to wait")
    args = parser.parse_args()
    RUNS.mkdir(parents=True, exist_ok=True)
    result_file = RUNS / f"{args.scenario}-run{args.run}.json"
    state_file = RUNS / f"{args.scenario}-run{args.run}.state.json"
    if result_file.exists():
        show(json.loads(result_file.read_text(encoding="utf-8")))
        return
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        resume(state)
    else:
        state = start(args.scenario, state_file)
    if wait(UUID(state["investigation_id"]), args.within) == "quota":
        print("quota stop: today's model quota is spent; run again after it resets")  # noqa: T201
        sys.exit(QUOTA_STOP)
    result = collect(state)
    result_file.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    state_file.unlink()
    show(result)


if __name__ == "__main__":
    main()
