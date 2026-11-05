"""What Prometheus reads at the agent's GET /metrics (infra/prometheus-alerts.yml; docs/alerts.md),
in its text format: the investigation messages that failed in the last hour, the plans that came
with no model review, and, while live triage's Claude takes no call, why.
"""

from sqlalchemy import Connection, text

from agent.llm import Limiter, stop_code
from agent.settings import Role

# dlq: marked failed as its message moved to the dead-letter queue after 5 receives;
# unreadable: a message that failed validation, kept in dead_letters;
# unreviewed: a plan that no model reviewed, which the gateway refuses to approve.
RECENT = text("""
    SELECT
      (SELECT count(*) FROM agent.investigations
       WHERE status = 'failed' AND updated_at > now() - interval '1 hour') AS dlq,
      (SELECT count(*) FROM agent.dead_letters
       WHERE created_at > now() - interval '1 hour') AS unreadable,
      (SELECT count(*) FROM agent.plans
       WHERE review->>'status' = 'not_reviewed'
         AND planned_at > now() - interval '1 hour') AS unreviewed
""")


def render(conn: Connection, claude: Role | None, limiter: Limiter) -> str:
    """The metrics; claude is live triage's Claude role, None when it has none (no key, AI off)."""
    with conn.begin():
        recent = conn.execute(RECENT).one()
    lines = [
        "# HELP tc_investigations_failed_recent Investigation messages that failed in the last "
        "hour: dlq after 5 receives, unreadable on validation.",
        "# TYPE tc_investigations_failed_recent gauge",
        f'tc_investigations_failed_recent{{kind="dlq"}} {recent.dlq}',
        f'tc_investigations_failed_recent{{kind="unreadable"}} {recent.unreadable}',
        "# HELP tc_plans_unreviewed_recent Plans of the last hour that no model reviewed, which "
        "take no approval.",
        "# TYPE tc_plans_unreviewed_recent gauge",
        f"tc_plans_unreviewed_recent {recent.unreviewed}",
        "# HELP tc_claude_stopped Why live triage's Claude takes no call; no sample while it does.",
        "# TYPE tc_claude_stopped gauge",
    ]
    reason = None if claude is None else stop_code(conn, claude, limiter)
    if reason is not None:
        lines.append(f'tc_claude_stopped{{reason="{reason}"}} 1')
    return "\n".join(lines) + "\n"
