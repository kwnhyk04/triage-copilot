# Bad deploy: errors after a release

## Symptoms

- The 5xx ratio or p99 latency of one service rises within minutes after a deploy of that service.
- Error logs name the new version, or show an exception in code the release changed.
- Services that call it show errors too, but start no earlier than it does.

## Checks

1. List the service's deploys and compare their times with the start of the errors.
2. Query `http_error_ratio{service="<service>"}` from an hour before the alert: errors that begin after the deploy, and not before, point at it.
3. Read the error logs for exceptions that mention the new version or the changed code path.
4. Rule out deploys of other services that left their own metrics flat.

## Remediation

Propose `rollback_deploy` on the service with `to_version` set to the version that ran before the errors began. A human approves it; the executor runs it. After the rollback the error ratio should return to its earlier level within a few minutes.
