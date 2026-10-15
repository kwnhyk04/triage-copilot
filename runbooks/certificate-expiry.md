# TLS certificate expiry

## Symptoms

- Calls to one service fail all at once with handshake errors such as "certificate has expired" or "x509: certificate signed by unknown authority".
- The failures begin at a round time and do not follow a deploy.

## Checks

1. Read the callers' error logs for TLS or x509 messages.
2. Confirm that no deploy or config change happened at the start of the failures.

## Remediation

Renewing the certificate is a manual step outside the allowlisted actions; report it with the certificate's service as the root cause.
