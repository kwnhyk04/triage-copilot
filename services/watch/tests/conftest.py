from collections.abc import Iterator
from pathlib import Path

import pytest
from watch_fakes import FakeServer, FakeTools


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    """The fake docker and git as all of PATH, so the real ones can never run."""
    fake = FakeTools(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(fake.folder))
    return fake


@pytest.fixture
def server() -> Iterator[FakeServer]:
    fake = FakeServer()
    yield fake
    fake.close()
