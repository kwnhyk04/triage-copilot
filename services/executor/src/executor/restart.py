"""The restart effector: `docker compose -p <project> restart <service>`, the only command the
executor runs, with the catalog's names (executor.run.Target).

A fixed argv with no shell, under a timeout, with a minimal environment, so none of TC's settings
or secrets reach docker, and in an empty working directory, so compose finds the project by its
name and reads no compose file or .env of its own.
"""

import os
import shutil
import subprocess
import tempfile

from executor.run import Effector, Target
from watch.readers import safe_argument

# What docker may see of the executor's environment, as watch's readers keep for theirs.
ENV_KEPT = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "LANG",
        "LC_ALL",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
    }
)


def restart_effector(timeout: float = 120.0) -> Effector:
    """Restarts one container through docker found on PATH now; raises when it fails."""
    program = shutil.which("docker")

    def restart(target: Target) -> None:
        argv = target.command()
        if program is None:
            raise RuntimeError("docker is not on PATH")
        if not all(safe_argument(arg) for arg in argv):
            raise RuntimeError(f"refused: {argv!r} holds a character the command may not")
        environ = {k: v for k, v in os.environ.items() if k.upper() in ENV_KEPT}
        with tempfile.TemporaryDirectory() as empty:
            try:
                # A fixed argv of catalog names, checked above, and no shell.
                done = subprocess.run(  # noqa: S603
                    [program, *argv],
                    cwd=empty,
                    env=environ,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"docker compose timed out after {timeout:g} s") from None
        if done.returncode:
            stderr = done.stderr.decode("utf-8", "replace").splitlines()
            last = next((line for line in reversed(stderr) if line.strip()), "")
            raise RuntimeError(f"docker compose exit {done.returncode}: {last[:300]}")

    return restart
