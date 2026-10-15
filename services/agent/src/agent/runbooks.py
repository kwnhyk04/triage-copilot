"""Runbook search: markdown runbooks cut into sections, embedded locally with fastembed's
nomic-embed-text-v1.5 (768 dimensions), and ranked by cosine distance in pgvector.

The model needs task prefixes: search_document for stored sections, search_query for questions.
It loads only from FASTEMBED_CACHE_PATH; `download` is the one command that fetches it.

CLI: uv run python -m agent.runbooks download|ingest|info
"""

import argparse
import asyncio
import json
import sys
import threading
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Connection, Engine, create_engine, text

MODEL = "nomic-ai/nomic-embed-text-v1.5"
DOCUMENT = "search_document: "
QUERY = "search_query: "
DIMENSIONS = 768
SECTION_CHARS = 4000
UPSERT = text("""
    INSERT INTO agent.runbook_chunks (runbook, chunk, content, embedding)
    VALUES (:runbook, :chunk, :content, CAST(:embedding AS vector))
    ON CONFLICT (runbook, chunk)
    DO UPDATE SET content = excluded.content, embedding = excluded.embedding
""")
NEAREST = text("""
    SELECT runbook, chunk, content, embedding <=> CAST(:embedding AS vector) AS distance
    FROM agent.runbook_chunks ORDER BY distance LIMIT :k
""")


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Loads the model on first use, from the cache only unless `download` is set."""

    def __init__(self, cache_dir: Path, *, download: bool = False) -> None:
        self.cache_dir = cache_dir
        self.download = download
        self._model: Any = None
        self._lock = threading.Lock()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            if self._model is None:
                # Imported here: loading onnxruntime takes a moment the rest of the agent skips.
                from fastembed import TextEmbedding

                self._model = TextEmbedding(
                    MODEL,
                    cache_dir=str(self.cache_dir),
                    threads=2,
                    local_files_only=not self.download,
                )
            vectors = self._model.embed(list(texts))
            return [[float(x) for x in vector] for vector in vectors]


def sections(path: Path) -> list[str]:
    """One chunk per "## " section, each led by the runbook's title so it stands alone."""
    lines = path.read_text(encoding="utf-8").splitlines()
    title = next((line for line in lines if line.startswith("# ")), f"# {path.stem}")
    chunks: list[list[str]] = [[]]
    for line in lines:
        if line == title:
            continue
        if line.startswith("## ") and chunks[-1]:
            chunks.append([])
        chunks[-1].append(line)
    bodies = ("\n".join(chunk).strip() for chunk in chunks)
    return [f"{title}\n\n{body}"[:SECTION_CHARS] for body in bodies if body]


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def ingest(engine: Engine, embedder: Embedder, directory: Path) -> int:
    """Embeds every runbook in `directory` and drops the chunks of removed or shortened ones."""
    paths = sorted(p for p in directory.glob("*.md") if p.name != "README.md")
    count = 0
    with engine.begin() as conn:
        for path in paths:
            parts = sections(path)
            vectors = embedder.embed([DOCUMENT + part for part in parts])
            for chunk, (part, vector) in enumerate(zip(parts, vectors, strict=True)):
                row = {"runbook": path.name, "chunk": chunk, "content": part}
                conn.execute(UPSERT, row | {"embedding": vector_literal(vector)})
            conn.execute(
                text("DELETE FROM agent.runbook_chunks WHERE runbook = :r AND chunk >= :n"),
                {"r": path.name, "n": len(parts)},
            )
            count += len(parts)
        conn.execute(
            text("DELETE FROM agent.runbook_chunks WHERE NOT (runbook = ANY(:names))"),
            {"names": [p.name for p in paths]},
        )
    return count


def search(
    conn: Connection, embedder: Embedder, query: str, k: int
) -> list[tuple[str, int, str, float]]:
    vector = embedder.embed([QUERY + query])[0]
    with conn.begin():
        rows = conn.execute(NEAREST, {"embedding": vector_literal(vector), "k": k})
        return [(r.runbook, r.chunk, r.content, float(r.distance)) for r in rows]


class RunbookSearch:
    """search_runbooks' backend: embedding and the query run off the event loop."""

    def __init__(self, engine: Engine, embedder: Embedder) -> None:
        self.engine = engine
        self.embedder = embedder

    async def search(self, query: str, k: int) -> list[tuple[str, int, str, float]]:
        return await asyncio.to_thread(self._search, query, k)

    def _search(self, query: str, k: int) -> list[tuple[str, int, str, float]]:
        with self.engine.connect() as conn:
            return search(conn, self.embedder, query, k)


def embedding_info(cache_dir: Path | None) -> dict[str, str | None]:
    """The model, the fastembed version and the cached model revision, for eval reports."""
    snapshots = (cache_dir or Path()) / f"models--{MODEL.replace('/', '--')}" / "snapshots"
    revisions = sorted(p.name for p in snapshots.iterdir()) if snapshots.is_dir() else []
    return {
        "model": MODEL,
        "fastembed": version("fastembed"),
        "revision": revisions[-1] if revisions else None,
    }


def main() -> None:
    # Imported here: only the CLI reads the environment.
    from agent.settings import Settings
    from py_config import ConfigError, load_settings

    parser = argparse.ArgumentParser(prog="python -m agent.runbooks")
    parser.add_argument("command", choices=["download", "ingest", "info"])
    command = parser.parse_args().command
    try:
        settings = load_settings(Settings)
    except ConfigError as error:
        sys.exit(f"runbooks: {error}")
    cache = settings.fastembed_cache_path
    if cache is None:
        sys.exit("runbooks: FASTEMBED_CACHE_PATH is required")
    if command == "info":
        print(json.dumps(embedding_info(cache)))  # noqa: T201 - CLI output
        return
    embedder = FastEmbedder(cache, download=command == "download")
    if command == "download":
        dimensions = len(embedder.embed([QUERY + "ping"])[0])
        print(f"{MODEL}: {dimensions} dimensions in {cache}")  # noqa: T201 - CLI output
        return
    engine = create_engine(settings.agent_database_url, connect_args={"connect_timeout": 5})
    count = ingest(engine, embedder, settings.runbooks_dir)
    print(f"ingested {count} chunks from {settings.runbooks_dir}")  # noqa: T201 - CLI output


if __name__ == "__main__":
    main()
