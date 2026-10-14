"""`make contracts-check`: runs the codegen command in argv, fails if any generated file changed."""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    "apps/web/src/generated",
    "packages/py-clients/src/py_clients/generated",
    "packages/ts-clients/src/generated",
)


def snapshot() -> dict[str, str]:
    """Hash of every generated file; line endings are ignored, since Windows tools write CRLF."""
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for folder in GENERATED
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


before = snapshot()
# The command comes from the Makefile, not from untrusted input.
subprocess.run(sys.argv[1:], cwd=ROOT, check=True)  # noqa: S603
after = snapshot()
stale = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
if stale:
    sys.exit("Generated code was stale; review and commit these files:\n  " + "\n  ".join(stale))
