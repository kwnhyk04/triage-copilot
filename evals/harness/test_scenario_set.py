"""Scenario set v1: the manifest pins every file, the generator reproduces them, and every
required signal shows in what the agent's own tools return for it, so a correct, grounded
investigation is possible in every scenario.
"""

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient

from agent.tools import ToolContext, Tools
from py_clients.clients import AlertContextClient
from sim.main import Settings, create_apps
from sim.scenario import Action, load_scenarios, seconds

V1 = Path(__file__).resolve().parents[1] / "scenarios" / "v1"
# v1 was frozen on 2026-10-16: the manifest's own digest, so no file of the set can change.
FROZEN = "b81c80f4ba4424272360e435052daf1107e79f533583b3da138247feb260e338"
# A config or flag change, as the line logging it names it.
CHANGE = re.compile(r"feature flag (\S+) changed|(?:config|routes) reloaded \S+ [\w-]+: ([^=\s]+)")
MANIFEST = json.loads((V1 / "manifest.json").read_text(encoding="utf-8"))["scenarios"]
LOADED = load_scenarios(V1)
APPS = create_apps(Settings(sim_scenarios_dir=V1, log_level="warn"))


def test_the_manifest_pins_every_scenario_file() -> None:
    assert hashlib.sha256((V1 / "manifest.json").read_bytes()).hexdigest() == FROZEN
    files = {p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in V1.glob("*.yaml")}
    assert files == {sid: entry["sha256"] for sid, entry in MANIFEST.items()}
    assert {sid: entry["down"] for sid, entry in MANIFEST.items()} == {
        sid: loaded.scenario.down for sid, loaded in LOADED.items()
    }


def test_the_set_is_8_families_of_5_split_15_and_25() -> None:
    families = Counter(e["family"] for e in MANIFEST.values())
    assert len(families) == 8 and set(families.values()) == {5}
    assert Counter(e["split"] for e in MANIFEST.values()) == {"dev": 15, "test": 25}
    ci = [e for e in MANIFEST.values() if e["ci"]]
    assert len(ci) == 8 and {e["split"] for e in ci} == {"dev"}
    assert len({e["family"] for e in ci}) == 8
    degraded = [e for e in MANIFEST.values() if e["down"]]
    assert Counter(e["split"] for e in degraded) == {"dev": 2, "test": 4}
    assert {e["down"] for e in degraded} == {"metrics", "logs", "deploys", "dependencies"}


def test_deploy_timing_does_not_give_the_answer_away() -> None:
    """In a dev and a test scenario of every family, the newest deploy is a harmless one minutes
    before the alert, forbidden to roll back and named as a red herring; causal deploys land
    from under 5 to over 30 minutes before the alert."""
    decoys: set[tuple[str, str]] = set()
    causal: list[int] = []
    for sid, (scenario, _) in LOADED.items():
        labels = scenario.labels
        latest = max(scenario.deploys, key=lambda d: seconds(d.at))
        rollback = Action(kind="rollback_deploy", target=latest.service)
        named = any(latest.version in herring for herring in labels.red_herrings)
        if seconds(latest.at) >= -600 and rollback in labels.forbidden_actions and named:
            decoys.add((MANIFEST[sid]["family"], MANIFEST[sid]["split"]))
        if labels.root_cause_class == "bad_deploy":
            causal += [seconds(d.at) for d in scenario.deploys if d.service == labels.service]
    families = {e["family"] for e in MANIFEST.values()}
    assert decoys == {(family, split) for family in families for split in ("dev", "test")}
    assert min(causal) < -1800 and max(causal) > -300


def test_config_timing_does_not_give_the_answer_away() -> None:
    """In a dev and a test scenario of every family, the newest config or flag change is a
    harmless one minutes before the alert, named as a red herring, and turning its flag off is
    forbidden; causal changes start from under 5 to over 15 minutes before the alert."""
    decoys: set[tuple[str, str]] = set()
    causal: list[int] = []
    for sid, (scenario, _) in LOADED.items():
        labels = scenario.labels
        changes = sorted(
            (seconds(stream.start), stream.labels["service"], found[1] or found[2], bool(found[1]))
            for stream in scenario.logs
            for line in stream.lines
            if stream.start and (found := CHANGE.match(line))
        )
        harmless = [c for c in changes if any(c[2] in h for h in labels.red_herrings)]
        if labels.root_cause_class == "config_change":
            causal += [c[0] for c in changes if c not in harmless]
        if harmless and harmless[-1] == changes[-1]:
            at, service, name, flag = harmless[-1]
            off = Action(kind="disable_feature_flag", target=service, parameters={"flag": name})
            if at >= -600 and (not flag or off in labels.forbidden_actions):
                decoys.add((MANIFEST[sid]["family"], MANIFEST[sid]["split"]))
    families = {e["family"] for e in MANIFEST.values()}
    assert decoys == {(family, split) for family in families for split in ("dev", "test")}
    assert min(causal) < -900 and max(causal) > -300


def test_the_generator_writes_the_same_bytes(tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603 - this interpreter and the repo's own script
        [sys.executable, str(V1 / "generate.py"), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == sorted(p.name for p in V1.iterdir() if p.suffix in (".yaml", ".json"))
    for name in written:
        assert (tmp_path / name).read_bytes() == (V1 / name).read_bytes(), name


@pytest.mark.parametrize("scenario_id", sorted(LOADED))
def test_every_required_signal_shows_in_the_tools_results(scenario_id: str) -> None:
    # As in a live run: the alert fired three minutes ago, so tool windows end now.
    fired = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=3)
    admin = TestClient(APPS.admin)
    activation = admin.post(
        f"/admin/scenarios/{scenario_id}/activate", json={"anchor": fired.isoformat()}
    )
    assert activation.is_success
    began = time.perf_counter()
    # The slowest query there is, on a cold world: every line of every stream.
    every = TestClient(APPS.data).get(
        "/loki/api/v1/query_range", params={"query": '{service=~".+"}'}
    )
    assert every.status_code == (503 if LOADED[scenario_id].scenario.down == "logs" else 200)
    assert time.perf_counter() - began < 1.5

    async def results() -> list[tuple[str, str]]:
        transport = httpx2.ASGITransport(app=APPS.data)
        async with httpx2.AsyncClient(transport=transport, base_url="http://sim") as sim:
            context = AlertContextClient("http://gateway.test", "token")
            tools = Tools(sim, context, None, None).all()
            ctx = ToolContext(uuid.uuid4(), fired, 100_000)
            found: list[tuple[str, str]] = []
            for signal in LOADED[scenario_id].scenario.labels.required_signals:
                out = await tools[signal.tool](ctx, dict(signal.args))
                found.append((signal.pattern, out if isinstance(out, str) else out.text))
            context.close()
            return found

    for pattern, text in asyncio.run(results()):
        assert re.search(pattern, text), f"{pattern!r} not in:\n{text[:2000]}"
