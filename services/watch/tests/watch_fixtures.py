"""Synthetic FreightAudit and FilingLens logs, built from their log fields, and their canaries."""

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
# Each app's fixtures, as `docker logs --timestamps` prints them, per container service.
LOGS = {
    "freight-audit": {"api": "freight-audit-api.log", "worker": "freight-audit-worker.log"},
    "filing-lens": {"api": "filing-lens-api.log", "ingest": "filing-lens-ingest.log"},
}

# Planted in every app's fixtures, one per shape the scrubber must stop; none may survive it.
# They are low-entropy and shorter than real keys, so gitleaks never takes them for secrets.
CANARIES = {
    "anthropic key": "canarycanarycanary01",
    "google key": "Canarycanarycanarycanary02",
    "nvidia key": "canarycanarycanarycanary03",
    "groq key": "canarycanarycanarycanary04",
    "github token": "canarycanarycanary05",
    "github fine-grained token": "canarycanary06",
    "jwt": "eyJjYW5hcnkiOiIx",
    "bearer token": "canarycanarybearer08",
    "url password": "canarycanarypass09",
    "query string": "canarycanaryquery10",
    "email": "canary.person@example.com",
    "tailnet name": "canary-tailnet",
    "traceback frame": "canary_frame_module",
    "unlisted field": "canarycanarycanary14",
    "pem block": "Y2FuYXJ5Y2FuYXJ5cGVtMTU",
    "assigned secret": "canarycanarycanary16",
}


def log_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def log_lines(name: str) -> list[str]:
    """A fixture's lines without docker's timestamps."""
    return [line.partition(" ")[2] for line in log_text(name).splitlines()]


def app_lines(app: str) -> list[str]:
    return [line for name in LOGS[app].values() for line in log_lines(name)]
