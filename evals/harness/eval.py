"""The eval: every scenario of a split, RUNS times, through the real path (run.py), scored against
the scenario labels (score.py), checked against thresholds.json, and written to a dated report;
the full test split also writes the README's eval table.

    make eval SPLIT=dev|test RUNS=3 SUBSET=ci|full [ESTIMATE=1] [SET_FLOORS=1] [ALLOW_LOOSER=1]

Exit codes: 0 every floor and gate holds; 1 a floor or a gate broke, which any forbidden proposal
does; 2 the model's quota or credits ran out, so run the same command once they are back (Gemini
after midnight Pacific, Claude with new credits) and it goes on from the parked run; 3 the stack
or the setup is wrong.

Each run is cached in evals/reports/runs/ under a key of the prompt hash, every pinned model id,
the scenario file's sha256 and the run index: a changed prompt, model or scenario runs again, and
an eval stopped for any reason goes on where it stopped. Runs go one at a time, since the sim
serves one scenario, all first runs before any second, GAP seconds apart so each starts with the
per-minute quota free.
"""

import argparse
import dataclasses
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import run
import score

# The agent's own hash: the eval compares it with the one each model call recorded.
from agent.job import prompt_sha
from agent.llm import CALLS_TODAY
from agent.settings import Settings
from sim.scenario import load_scenarios

REPO = Path(__file__).resolve().parents[2]
SET = REPO / "evals" / "scenarios" / "v1"
REPORTS = REPO / "evals" / "reports"
CACHE = REPORTS / "runs"
THRESHOLDS = REPO / "evals" / "thresholds.json"
# .prettierrc.json's printWidth: lint runs Prettier over thresholds.json.
PRINT_WIDTH = 100
PRICING = REPO / "evals" / "pricing.json"
MEMORY = REPO / "docs" / "memory.md"
README = REPO / "README.md"
GAP = 60
WITHIN = 900.0
FAILED, QUOTA, SETUP = 1, 2, 3
ROLES = ("primary", "fallback", "small")
DEFAULTS = {role: str(Settings.model_fields[f"llm_{role}_model"].default) for role in ROLES}
# Calls an investigation makes per role before any run measured it (docs/memory.md, 6a).
GUESS = {"primary": 9.0, "small": 0.5}
RUNBOOKS = {
    "bad-deploy": ["bad-deploy-rollback"],
    "db-pool-exhaustion": ["db-connection-pool-exhaustion"],
    "slow-dependency": ["high-latency-triage"],
    "memory-leak": [],
    "expired-cert": ["certificate-expiry"],
    "bad-config": [],
    "noisy-neighbor": ["high-latency-triage"],
    "dns-partition": [],
}
# Targets of docs/context.md that the eval does not measure, and what proves them.
PROVEN: list[dict[str, str]] = [
    {
        "name": "Unapproved executions",
        "target": "0 (hard gate)",
        "measured": "0 in every eval run; the gate is proven by tests",
        "result": "pass",
        "date": "",
        "evidence": "services/executor/tests/test_executor_run.py, "
        "services/agent/tests/test_executor_int.py, apps/gateway/src/executions.int.test.ts",
    },
    {
        "name": "Webhook ack during a 500-alert storm",
        "target": "p99 200 ms, 0 dropped, 5 or fewer investigations",
        "measured": "p99 86 ms, 0 dropped, 5 investigations",
        "result": "pass",
        "date": "2026-10-15",
        "evidence": "apps/gateway/src/intake.int.test.ts (make test-int)",
    },
]
NOTES = [
    "Time to first hypothesis has no step of its own yet: hypotheses exist only in the final "
    "report, so it is measured as the time from webhook intake to the report, an upper bound "
    "that equals the investigation time (docs/todo.md has the item that adds an early "
    "hypothesis step).",
    "Any forbidden proposal fails the eval (exit 1) whatever the other scores are. Unapproved "
    "executions are not measured here: the executor's tests prove the gate, and no eval run had "
    "an approval or an execution.",
    "Runs that waited out a quota stop are left out of the timing figures and counted below.",
    "Action correctness counts a run without a proposal as right when its scenario has no "
    "acceptable action, so an agent that never proposes scores the do-nothing baseline shown "
    "beside it; the next row counts only scenarios with an acceptable action.",
]
CAVEATS = (
    "What the numbers show, and what they do not: the incidents are simulated, and the same "
    "project wrote the scenarios, the tools and the labels. Prompts were tuned on the dev split "
    "only (evals/prompt-log.md), never on the test scenarios. 25 scenarios run 3 times is a "
    "small sample: the 95% intervals, which resample scenarios, are wide, and per-family figures "
    "rest on 2 to 4 scenarios each."
)
FREE_COST = (
    "Costs are what the paid tier would charge for the same tokens; the free tier billed nothing."
)
CREDITS_COST = (
    "Costs are what the run spent in Anthropic API credits; the $0-billed gate applies to the free "
    "setup only."
)
START, END = "<!-- eval:start -->", "<!-- eval:end -->"
Json = dict[str, Any]


class Setup(Exception):
    """The eval cannot run as asked: wrong settings, or a service is not ready."""


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def pinned() -> dict[str, str]:
    """The model of each role, from the .env the agent runs with too."""
    if os.environ.get("LLM_FALLBACK_ON_RATE_LIMIT", "true").lower() != "false":
        raise Setup("evals need LLM_FALLBACK_ON_RATE_LIMIT=false, for the agent and here")
    if os.environ.get("AI_ENABLED", "false").lower() != "true":
        raise Setup("evals need AI_ENABLED=true")
    return {role: os.environ.get(f"LLM_{role.upper()}_MODEL") or DEFAULTS[role] for role in ROLES}


def report_name(today: str, scope: str, prompt: str, models: dict[str, str]) -> tuple[str, bool]:
    """The report's name, and whether it is the official one: the full test split on the default
    models, which also writes the README table. Tuning runs of one day keep a report per prompt,
    and runs on other models their own."""
    if scope.startswith("test-full-") and models == DEFAULTS:
        return today, True
    name = f"{today}-{scope}-{prompt[:8]}"
    return (name if models == DEFAULTS else f"{name}-{models['primary']}"), False


def key(prompt: str, models: dict[str, str], scenario_sha: str, index: int) -> str:
    payload = {"prompt": prompt, "models": models, "scenario": scenario_sha, "run": index}
    return sha(json.dumps(payload, sort_keys=True))


@dataclasses.dataclass(frozen=True)
class Job:
    scenario: str
    run: int
    key: str

    @property
    def result(self) -> Path:
        return CACHE / f"{self.scenario}-run{self.run}-{self.key[:16]}.json"

    @property
    def state(self) -> Path:
        return CACHE / f"{self.scenario}-run{self.run}-{self.key[:16]}.state.json"


def plan(
    manifest: Json, split: str, subset: str, runs: int, prompt: str, models: dict[str, str]
) -> list[Job]:
    """All first runs, then all second runs, and so on."""
    chosen = sorted(
        sid
        for sid, entry in manifest["scenarios"].items()
        if entry["split"] == split and (subset == "full" or entry["ci"])
    )
    shas = {sid: manifest["scenarios"][sid]["sha256"] for sid in chosen}
    return [
        Job(sid, index, key(prompt, models, shas[sid], index))
        for index in range(1, runs + 1)
        for sid in chosen
    ]


def load(path: Path) -> Json:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def say(line: str) -> None:
    print(line, flush=True)  # noqa: T201 - CLI output


def problems(record: Json, models: dict[str, str], prompt: str) -> list[str]:
    """Why a finished run does not count: an unpinned model answered, the agent ran another
    prompt than the source's, or the sim lost its world."""
    found = [
        f"a {c['role']} call went to {c['model']}"
        for c in record["model_calls"]
        if c["role"] == "fallback" or c["model"] != models[c["role"]]
    ]
    # Calls from before agent migration 0008 recorded no sha; one at least must name this prompt.
    shas: set[str | None] = {c["prompt_sha"] for c in record["model_calls"]}
    if shas - {prompt, None} or prompt not in shas:
        ran = ", ".join(sorted(s[:16] for s in shas - {prompt} if s)) or "no recorded prompt"
        found.append(f"the agent ran {ran}, not the source's prompt {prompt[:16]}: restart it")
    if any("no scenario is active" in (s["result"] or s["summary"]) for s in record["steps"]):
        found.append("the sim served no scenario during the run (restarted?)")
    return found


def execute(jobs: Sequence[Job], models: dict[str, str], prompt: str) -> int | None:
    """Runs every job without a cached result, a parked one first; QUOTA on a quota stop."""
    pending = [j for j in jobs if not j.result.exists()]
    pending.sort(key=lambda j: not j.state.exists())
    started = False
    for number, job in enumerate(pending, start=1):
        say(f"[{number}/{len(pending)}] {job.scenario} run {job.run}")
        for attempt in (1, 2):
            if started:
                time.sleep(GAP)
            started = True
            state: Json
            if job.state.exists():
                state = load(job.state)
                run.resume(state)
                state["resumed"] = True
            else:
                state = {**run.start(job.scenario), "run": job.run, "key": job.key}
            save(job.state, state)
            if run.wait(UUID(state["investigation_id"]), WITHIN) == "quota":
                say("quota stop: the model's quota or credits are spent; run the same command once")
                say("they are back (Gemini: midnight Pacific; Claude: new credits, or next month")
                say("after a spend limit), and the eval goes on from this run")
                return QUOTA
            run.mirrored(state["investigation_id"])
            record = run.collect(state)
            found = problems(record, models, prompt)
            job.state.unlink()
            if not found:
                save(job.result, record)
                say(f"  {record['status']}, {record['tokens']:,} tokens, {record['seconds']:.0f} s")
                break
            if attempt == 2 or not any("sim" in p for p in found):
                raise Setup(f"{job.scenario} run {job.run} does not count: {'; '.join(found)}")
            say(f"  running it again: {'; '.join(found)}")
    return None


def estimate(jobs: Sequence[Job], models: dict[str, str]) -> None:
    """Calls each model still has to make against its daily quota, and the days that takes."""
    pending = [j for j in jobs if not j.result.exists()]
    cached = [load(p) for p in CACHE.glob("*-run*-*.json") if not p.name.endswith(".state.json")]
    # Only runs whose agent ran on this primary model say how this one behaves.
    done = [
        r
        for r in cached
        if any(c["role"] == "primary" and c["model"] == models["primary"] for c in r["model_calls"])
    ]
    say(
        f"{len(jobs)} investigations planned, {len(pending)} to run "
        f"({len(done)} cached runs on {models['primary']})"
    )
    agent_db, gateway_db = run.engines()
    gateway_db.dispose()
    with agent_db.connect() as conn:
        used = {m: conn.execute(CALLS_TODAY, {"model": m}).scalar_one() for m in models.values()}
    agent_db.dispose()
    for role in ("primary", "small"):
        model = models[role]
        counted = [
            sum(c["role"] == role and c["outcome"] in ("ok", "error") for c in r["model_calls"])
            for r in done
        ]
        each = statistics.fmean(counted) if counted else GUESS[role]
        needed = math.ceil(each * len(pending))
        default = Settings.model_fields[f"llm_{role}_rpd"].default
        rpd = int(os.environ.get(f"LLM_{role.upper()}_RPD") or default)
        left = max(rpd - used[model], 0)
        days = 1 if needed <= left or not rpd else 1 + math.ceil((needed - left) / rpd)
        basis = f"measured over {len(counted)} runs" if counted else "a guess"
        provider = os.environ.get(f"LLM_{role.upper()}_PROVIDER") or "gemini"
        say(
            f"{role} {provider} {model}: about {each:.1f} calls per investigation ({basis}), "
            f"{needed} to make; {rpd} a day and {used[model]} used today: {days} quota day(s), "
            "today included"
        )
    seconds = [r["seconds"] for r in done if not r["resumed"]]
    each_s = statistics.fmean(seconds) if seconds else 120.0
    hours = len(pending) * (each_s + GAP) / 3600
    say(f"wall time: about {hours:.1f} hours of running ({each_s:.0f} s each plus a {GAP} s gap)")


def scored(jobs: Sequence[Job], manifest: Json) -> tuple[list[score.Scored], list[Json]]:
    loaded = load_scenarios(SET)
    prices: dict[str, Json] = load(PRICING)["models"]
    runs: list[score.Scored] = []
    records: list[Json] = []
    for job in jobs:
        record = load(job.result)
        entry = manifest["scenarios"][job.scenario]
        labels = loaded[job.scenario].scenario.labels
        runs.append(score.score(record, labels, entry["family"], entry["down"], prices))
        records.append(record)
    return runs, records


def ungated(metric: score.Metric, free: bool) -> bool:
    """The $0-billed gate holds the free setup only; a run on credits reports what it spent."""
    return metric.key == "cost_billed" and not free


def check(summary: dict[str, Json], floors: dict[str, float], free: bool) -> list[str]:
    """What broke: a floor, when the scope has floors, and the gates, always."""
    broke: list[str] = []
    for metric in score.METRICS:
        if ungated(metric, free):
            continue
        value = summary[metric.key]["value"]
        gate = metric.kind == "gate"
        limit = metric.target if gate else floors.get(metric.key)
        if limit is not None and score.passes(metric, value, limit) is False:
            broke.append(
                f"{metric.name}: {value:g} against {'gate' if gate else 'floor'} {limit:g}"
            )
    return broke


def set_floors(scope: str, summary: dict[str, Json], allow_looser: bool) -> dict[str, float]:
    """This run's floors; a floor only tightens unless loosening is allowed and written down."""
    thresholds = load(THRESHOLDS)
    old: dict[str, Json] = thresholds["scopes"].get(scope, {}).get("floors", {})
    reasons = [line for line in MEMORY.read_text(encoding="utf-8").splitlines() if "floor" in line]
    floors: dict[str, Json] = {}
    for metric in score.METRICS:
        value, ci = summary[metric.key]["value"], summary[metric.key]["ci"]
        if value is None or ci is None:
            continue
        new = score.floor(metric, value, ci)
        before = old.get(metric.key)
        if before is not None and score.looser(metric, new, before["floor"]):
            written = any(metric.key in line for line in reasons)
            if not (allow_looser and written):
                say(
                    f"kept the {metric.key} floor at {before['floor']:g}, not {new:g}: loosening "
                    "takes ALLOW_LOOSER=1 and a docs/memory.md line naming the metric and why"
                )
                floors[metric.key] = before
                continue
        floors[metric.key] = {"floor": new, "measured": value, "ci": ci, "set": str(date.today())}
    thresholds["scopes"][scope] = {"set": "v1", "floors": floors}
    THRESHOLDS.write_text(prettier_json(thresholds), encoding="utf-8", newline="\n")
    return {k: v["floor"] for k, v in floors.items()}


def prettier_json(value: object) -> str:
    """JSON as the repo's Prettier prints it, so lint passes on the thresholds.json set_floors
    writes (with LF line ends): an object one key per line, an array on one line when that line
    fits PRINT_WIDTH and one item per line otherwise, exponents as 1e-5 rather than 1e-05."""
    return "\n".join(_json_lines(value, "", "", "")) + "\n"


def _json_lines(value: object, indent: str, head: str, tail: str) -> list[str]:
    if not value or not isinstance(value, dict | list | tuple):
        return [f"{head}{_json_scalar(value)}{tail}"]
    inner = indent + "  "
    if isinstance(value, dict):
        pairs = list(cast("dict[str, object]", value).items())
        body = [
            line
            for i, (key, item) in enumerate(pairs, start=1)
            for line in _json_lines(
                item, inner, f"{inner}{json.dumps(key)}: ", "," if i < len(pairs) else ""
            )
        ]
        return [f"{head}{{", *body, f"{indent}}}{tail}"]
    items = list(cast("Sequence[object]", value))
    # ponytail: scalar arrays only; Prettier also inlines [[1, 2]] and fills long number arrays.
    if not any(isinstance(item, dict | list | tuple) for item in items):
        flat = f"{head}[{', '.join(map(_json_scalar, items))}]{tail}"
        if len(flat) <= PRINT_WIDTH:
            return [flat]
    body = [
        line
        for i, item in enumerate(items, start=1)
        for line in _json_lines(item, inner, inner, "," if i < len(items) else "")
    ]
    return [f"{head}[", *body, f"{indent}]{tail}"]


def _json_scalar(value: object) -> str:
    text = json.dumps(value)
    # Python writes 1e-05 and 1e+16 where Prettier prints 1e-5 and 1e16.
    return re.sub(r"e\+?(-?)0*(?=\d)", r"e\1", text) if isinstance(value, float) else text


def fmt(metric: score.Metric, value: float | None) -> str:
    if value is None:
        return "n/a"
    match metric.unit:
        case "USD":
            return f"${value:.4f}"
        case "s":
            return f"{value:.0f} s"
        case "tokens":
            return f"{value:,.0f}"
        case "calls" | "proposals":
            return f"{value:g}"
        case _:
            return f"{value:.2f}"


def target_text(metric: score.Metric) -> str:
    if metric.kind != "gate":
        return fmt(metric, metric.target)
    return f"{fmt(metric, metric.target)} ({'hard gate' if metric.target == 0 else 'hard cap'})"


def interval_text(metric: score.Metric, ci: Sequence[float] | None) -> str:
    return "n/a" if ci is None else f"{fmt(metric, ci[0])} to {fmt(metric, ci[1])}"


def measured(metric: score.Metric, row: Json) -> str:
    """The value, and beside it what an agent that never proposes would score, if it applies."""
    found = fmt(metric, row["value"])
    if row.get("do_nothing") is None:
        return found
    return f"{found} (do-nothing baseline {fmt(metric, row['do_nothing'])})"


def build_report(
    runs: list[score.Scored], records: list[Json], floors: dict[str, float], meta: Json
) -> Json:
    summary = score.summarize(runs)
    free = meta["billing"] == "free"
    metrics: dict[str, Json] = {}
    for metric in score.METRICS:
        value = summary[metric.key]["value"]
        off = ungated(metric, free)
        result = None if off else score.passes(metric, value, metric.target)
        floor = metric.target if metric.kind == "gate" else floors.get(metric.key)
        metrics[metric.key] = {
            "name": metric.name,
            "target": metric.target,
            "kind": metric.kind,
            "value": value,
            "ci": summary[metric.key]["ci"],
            "do_nothing": summary[metric.key]["do_nothing"],
            "floor": None if off else floor,
            "result": "n/a" if result is None else "pass" if result else "fail",
        }
    families: dict[str, Json] = {}
    for family in sorted({r.family for r in runs}):
        mine = [r for r in runs if r.family == family]
        families[family] = {
            "runbooks": RUNBOOKS[family],
            "scenarios": len({r.scenario for r in mine}),
            "runs": len(mine),
            "metrics": score.summarize(mine),
        }
    return {
        **meta,
        "metrics": metrics,
        "proven_elsewhere": PROVEN,
        "notes": [*NOTES, f"{CAVEATS} {FREE_COST if free else CREDITS_COST}"],
        "approvals_during_eval": sum(r["approvals"] for r in records),
        "executions_during_eval": sum(r["executions"] for r in records),
        "resumed_runs": sum(bool(r["resumed"]) for r in records),
        "families": families,
        "runs": [r.json() for r in runs],
    }


ACCURACY = (
    "top1",
    "top3",
    "consistency",
    "grounding",
    "action_correctness",
    "action_correctness_actionable",
    "tool_call_validity",
    "degraded_correct",
    "degraded_missing_evidence",
)
EFFICIENCY = (
    "forbidden_actions",
    "tool_calls_median",
    "tool_calls_max",
    "tokens_median",
    "tokens_max",
    "investigation_time_p95",
    "cost_paid",
    "cost_billed",
)


def family_tables(report: Json) -> list[str]:
    by_key = {m.key: m for m in score.METRICS}
    lines: list[str] = []
    for title, keys in (("Accuracy", ACCURACY), ("Efficiency and gates", EFFICIENCY)):
        names = " | ".join(by_key[k].name for k in keys)
        lines += [f"### {title} by family", "", f"| Family | Runbook | Scenarios | {names} |"]
        lines.append("| --- " * (len(keys) + 3) + "|")
        for family, part in report["families"].items():
            found = part["metrics"]
            cells = " | ".join(
                f"{fmt(by_key[k], found[k]['value'])} ({interval_text(by_key[k], found[k]['ci'])})"
                for k in keys
            )
            runbooks = ", ".join(part["runbooks"]) or "none"
            lines.append(f"| {family} | {runbooks} | {part['scenarios']} | {cells} |")
        lines.append("")
    return lines


def markdown(report: Json) -> str:
    by_key = {m.key: m for m in score.METRICS}
    scope = report["scope"]
    lines = [
        f"# Eval report {report['date']}",
        "",
        f"{scope['split']} split, {scope['subset']} subset: {scope['scenarios']} scenarios x "
        f"{scope['runs']} runs = {scope['investigations']} investigations, scenario set "
        f"{report['set']['name']} (manifest sha256 {report['set']['manifest_sha256'][:16]}).",
        f"Models, pinned: {report['models_text']}. Prompt sha256 {report['prompt_sha256'][:16]}. "
        f"Embedding: {report['embedding']}.",
        "",
        "| Metric | Target | Measured | 95% CI | Floor | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, row in report["metrics"].items():
        metric = by_key[name]
        floor = "not set" if row["floor"] is None else fmt(metric, row["floor"])
        lines.append(
            f"| {metric.name} | {target_text(metric)} | {measured(metric, row)} | "
            f"{interval_text(metric, row['ci'])} | {floor} | {row['result']} |"
        )
    for row in report["proven_elsewhere"]:
        lines.append(
            f"| {row['name']} | {row['target']} | {row['measured']} | n/a | n/a | "
            f"{row['result']}: {row['evidence']} |"
        )
    lines += ["", "## Notes", "", *(f"- {note}" for note in report["notes"])]
    lines += [
        f"- Approvals and executions during the eval: {report['approvals_during_eval']} and "
        f"{report['executions_during_eval']}. Runs resumed after a quota stop: "
        f"{report['resumed_runs']}.",
        "",
        "## By failure family",
        "",
        "Runbooks: bad-deploy, db-pool-exhaustion and expired-cert have their own; slow-dependency "
        "and noisy-neighbor only the general high-latency-triage; memory-leak, bad-config and "
        "dns-partition none.",
        "",
        *family_tables(report),
        "## Runs",
        "",
        "| Scenario | Run | Top-1 | Top-3 | Grounded | Action | Forbidden | Calls | Tokens "
        "| Time |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in report["runs"]:
        seconds = "resumed" if r["seconds"] is None else f"{r['seconds']:.0f} s"
        lines.append(
            f"| {r['scenario']} | {r['run']} | {r['top1']} | {r['top3']} | {r['grounded']} | "
            f"{r['action_correct']} | {r['forbidden']} | {r['tool_calls']} | {r['tokens']:,} | "
            f"{seconds} |"
        )
    return "\n".join(lines) + "\n"


def readme_block(report: Json) -> str:
    by_key = {m.key: m for m in score.METRICS}
    scope = report["scope"]
    lines = [
        START,
        "## Eval results",
        "",
        f"Test split of scenario set v1, {scope['scenarios']} scenarios x {scope['runs']} runs, "
        f"from `make eval SPLIT=test RUNS=3`; full report in `evals/reports/{report['date']}.md`, "
        "with every metric per failure family.",
        "",
        "| Metric | Target | Measured | 95% CI | Result | Date | Models |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, row in report["metrics"].items():
        metric = by_key[name]
        lines.append(
            f"| {metric.name} | {target_text(metric)} | {measured(metric, row)} | "
            f"{interval_text(metric, row['ci'])} | {row['result']} | {report['date']} | "
            f"{report['models_text']} |"
        )
    for row in report["proven_elsewhere"]:
        lines.append(
            f"| {row['name']} | {row['target']} | {row['measured']} | n/a | {row['result']} "
            f"(tests) | {row['date'] or report['date']} | n/a |"
        )
    # Only the default models, all free, write this table.
    lines += ["", f"{CAVEATS} {FREE_COST}", "", NOTES[0], END]
    return "\n".join(lines)


def update_readme(report: Json) -> None:
    text = README.read_text(encoding="utf-8")
    block = readme_block(report)
    if START in text:
        head, rest = text.split(START, 1)
        text = head + block + rest.split(END, 1)[1]
    else:
        title, rest = text.split("\n", 1)
        text = f"{title}\n\n{block}\n{rest}"
    README.write_text(text, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make eval")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--subset", choices=("ci", "full"), default="full")
    parser.add_argument("--estimate", action="store_true")
    parser.add_argument("--set-floors", action="store_true")
    parser.add_argument("--allow-looser", action="store_true")
    args = parser.parse_args(argv)
    raw = (SET / "manifest.json").read_bytes()
    manifest: Json = json.loads(raw)
    try:
        if args.subset == "ci" and args.split != "dev":
            raise Setup("the ci subset holds dev scenarios only")
        models = pinned()
        prompt = prompt_sha()
        jobs = plan(manifest, args.split, args.subset, args.runs, prompt, models)
        if args.estimate:
            estimate(jobs, models)
            return 0
        stopped = execute(jobs, models, prompt)
        if stopped is not None:
            return stopped
    except Exception as error:  # Exit code 3 is any failure to run, whatever raised it.
        say(f"eval: {type(error).__name__}: {error}")
        return SETUP
    runs, records = scored(jobs, manifest)
    free = score.free([models["primary"], models["small"]], load(PRICING)["models"])
    scope = f"{args.split}-{args.subset}-{args.runs}"
    summary = score.summarize(runs)
    if args.set_floors:
        floors = set_floors(scope, summary, args.allow_looser)
    else:
        saved: dict[str, Json] = load(THRESHOLDS)["scopes"].get(scope, {}).get("floors", {})
        floors = {k: v["floor"] for k, v in saved.items()}
    today = str(date.today())
    meta: Json = {
        "date": today,
        "scope": {
            "split": args.split,
            "subset": args.subset,
            "runs": args.runs,
            "scenarios": len({j.scenario for j in jobs}),
            "investigations": len(jobs),
            "key": scope,
        },
        "set": {"name": "v1", "manifest_sha256": sha(raw)},
        "models": models,
        "models_text": f"{models['primary']} (agent), {models['small']} (log summaries), "
        "no fallback",
        "billing": "free" if free else "credits",
        "prompt_sha256": prompt,
        "embedding": records[0]["embedding"] if records else None,
    }
    report = build_report(runs, records, floors, meta)
    name, official = report_name(today, scope, prompt, models)
    save(REPORTS / f"{name}.json", report)
    (REPORTS / f"{name}.md").write_text(markdown(report), encoding="utf-8")
    if official:
        update_readme(report)
    broke = check(summary, floors, free)
    for line in broke:
        say(f"broke: {line}")
    say(f"report: evals/reports/{name}.md")
    return FAILED if broke else 0


if __name__ == "__main__":
    sys.exit(main())
