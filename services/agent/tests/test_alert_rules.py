"""Production's alert rules (infra/prometheus-alerts.yml): the ones the user asked for, plus one for
blind monitoring, each with a severity, a summary and a link to its own section of docs/alerts.md
in the public repo. Their conditions are tested by promtool (test_monitoring_int.py).
"""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
RULES = ROOT / "infra" / "prometheus-alerts.yml"
RUNBOOKS = ROOT / "docs" / "alerts.md"
# A message links its runbook on main, never Alertmanager, Prometheus or a silence.
LINK = "https://github.com/kwnhyk04/triage-copilot/blob/main/docs/alerts.md#"


def rules() -> list[dict[str, Any]]:
    groups = yaml.safe_load(RULES.read_text("utf-8"))["groups"]
    return [rule for group in groups for rule in group["rules"]]


def anchors(markdown: str) -> set[str]:
    """GitHub's anchors for a page's headings: lower case, punctuation dropped, spaces hyphens."""
    headings = re.findall(r"^#{1,6} (.+)$", markdown, re.MULTILINE)
    return {
        re.sub(r"[^\w\- ]", "", heading).strip().lower().replace(" ", "-") for heading in headings
    }


def test_the_rules_cover_what_production_must_report() -> None:
    assert [rule["alert"] for rule in rules()] == [
        "GatewayNotReady",
        "AgentNotReady",
        "WatchNotReady",
        "InvestigationsFailing",
        "ClaudeOff",
        "ClaudeDailyCapReached",
        "PlansUnreviewed",
        "EgressDenied",
        "MonitoringBlind",
        "AlertDeliveryFailing",
        "InvestigationVerdict",
        "PlanWaiting",
    ]


def test_each_rule_links_its_own_runbook_section() -> None:
    sections = anchors(RUNBOOKS.read_text("utf-8"))
    for rule in rules():
        name = rule["alert"]
        assert (name, rule["annotations"]["runbook_url"]) == (name, LINK + name.lower())
        assert (name, name.lower() in sections) == (name, True)
        assert (name, rule["labels"]["severity"] in {"critical", "warning"}) == (name, True)
        # The rules Alertmanager sends to Discord alone, each linking its report.
        report = rule["labels"].get("notify") == "report"
        expected = {"summary", "runbook_url"} | ({"report_url"} if report else set[str]())
        assert (name, set(rule["annotations"])) == (name, expected)


def test_a_message_carries_no_text_a_model_wrote() -> None:
    # A log line steers the model, so nothing it wrote reaches Discord: the metrics name only an
    # app, an id, a kind and a closed list of causes, never a service, a summary or a rationale.
    for rule in rules():
        if rule["labels"].get("notify") == "report":
            summary = rule["annotations"]["summary"]
            used = set(re.findall(r"\$labels\.(\w+)", summary))
            assert (rule["alert"], used <= {"app", "verdict", "cause"}) == (rule["alert"], True)
