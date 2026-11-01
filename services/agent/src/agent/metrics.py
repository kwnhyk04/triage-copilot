"""What Prometheus reads at the agent's GET /metrics (infra/prometheus-alerts.yml; docs/alerts.md),
in its text format: the investigation messages that failed in the last hour, and, while live
triage's Claude takes no call, why.
"""

from sqlalchemy import Connection, text

from agent.llm import Limiter, stop_code
from agent.settings import Role

# dlq: marked failed as its message moved to the dead-letter queue after 5 receives;
# unreadable: a message that failed validation, kept in dead_letters.
FAILED = text("""
    SELECT
      (SELECT count(*) FROM agent.investigations
       WHERE status = 'failed' AND updated_at > now() - interval '1 hour') AS dlq,
      (SELECT count(*) FROM agent.dead_letters
       WHERE created_at > now() - interval '1 hour') AS unreadable
""")


def render(conn: Connection, claude: Role | None, limiter: Limiter) -> str:
    """The metrics; claude is live triage's Claude role, None when it has none (no key, AI off)."""
    with conn.begin():
        failed = conn.execute(FAILED).one()
    lines = [
        "# HELP tc_investigations_failed_recent Investigation messages that failed in the last "
        "hour: dlq after 5 receives, unreadable on validation.",
        "# TYPE tc_investigations_failed_recent gauge",
        f'tc_investigations_failed_recent{{kind="dlq"}} {failed.dlq}',
        f'tc_investigations_failed_recent{{kind="unreadable"}} {failed.unreadable}',
        "# HELP tc_claude_stopped Why live triage's Claude takes no call; no sample while it does.",
        "# TYPE tc_claude_stopped gauge",
    ]
    reason = None if claude is None else stop_code(conn, claude, limiter)
    if reason is not None:
        lines.append(f'tc_claude_stopped{{reason="{reason}"}} 1')
    return "\n".join(lines) + "\n"
