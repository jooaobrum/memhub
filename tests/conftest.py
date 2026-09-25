from __future__ import annotations

import socket
import subprocess
import time
import uuid

import psycopg
import pytest

from memhub.config import (
    AdmissionConfig,
    EmbeddingConfig,
    IngestionConfig,
    LLMConfig,
    ModelConfig,
    ReconcileConfig,
    Settings,
    TypeConfig,
)
from memhub.service import Actor, MemoryService
from memhub.store import MemoryStore
from memhub.types import TypeRegistry
from tests.fakes import FakeChatModel, FakeEmbeddings

TEST_DIMS = 16


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=10)
        return True
    except Exception:
        return False


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(dsn: str, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2):
                return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1)
    raise RuntimeError(f"postgres did not become ready in time: {last_err}")


@pytest.fixture(scope="session")
def pg_dsn():
    if not _docker_available():
        pytest.skip("docker is not available; skipping tests that need a real pgvector Postgres")
    port = _free_port()
    name = f"memhub-test-pg-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name,
            "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=memhub",
            "-p", f"{port}:5432", "pgvector/pgvector:pg16",
        ],
        check=True, capture_output=True,
    )
    dsn = f"postgresql://postgres:postgres@localhost:{port}/memhub"
    try:
        _wait_ready(dsn)
        yield dsn
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


TEST_EMBEDDING_MODEL = "fake-embed"


@pytest.fixture
def store(pg_dsn):
    prefix = f"t{uuid.uuid4().hex[:8]}"
    s = MemoryStore(pg_dsn, prefix)
    s.init(embedding_model=TEST_EMBEDDING_MODEL, dims=TEST_DIMS)
    return s


@pytest.fixture
def settings() -> Settings:
    return Settings(
        project_prefix="test",
        database_url="postgresql://unused",
        workspace_default="ws-default",
        llm=LLMConfig(
            extractor=ModelConfig(provider="openai", model="fake-extractor", api_key_env="UNSET_KEY"),
            judge=ModelConfig(provider="openai", model="fake-judge", api_key_env="UNSET_KEY"),
        ),
        embeddings=EmbeddingConfig(provider="openai", model="fake-embed", api_key_env="UNSET_KEY", dims=TEST_DIMS),
        entity_types=["machine"],
        scopes=["user", "workspace"],
        types={
            "fact": TypeConfig(**{"class": "memhub.types:Fact"}, type_prior=0.6),
            "preference": TypeConfig(
                **{"class": "memhub.types:Preference"}, type_prior=0.8, max_chars=1500, keyed=True,
                keys={k: k for k in ("language", "style", "scope", "detail", "lang", "tone", "answer_style", "theme", "old", "k1", "k2", "k3")},
            ),
            "profile": TypeConfig(
                **{"class": "memhub.types:Profile"}, type_prior=0.8, keyed=True,
                keys={k: k for k in ("nationality", "city", "age", "residence_status", "studies", "work", "family")},
            ),
            "episode": TypeConfig(**{"class": "memhub.types:Episode"}, type_prior=0.4),
            "skill": TypeConfig(**{"class": "memhub.types:Skill"}, extract=False),
        },
        ingestion=IngestionConfig(segment_idle="1h", thread_close="7d", min_user_turns=1, max_candidates=3),
        admission=AdmissionConfig(
            weights={"utility": 0.35, "evidence": 0.30, "novelty": 0.20, "type_prior": 0.10, "signals": 0.05},
            threshold=0.5,
        ),
        reconcile=ReconcileConfig(duplicate=0.92, conflict_band=(0.80, 0.92)),
    )


@pytest.fixture
def registry() -> TypeRegistry:
    return TypeRegistry()


@pytest.fixture
def fake_embeddings() -> FakeEmbeddings:
    return FakeEmbeddings(dims=TEST_DIMS)


@pytest.fixture
def fake_chat_model() -> FakeChatModel:
    return FakeChatModel()


@pytest.fixture
def service(store, settings, registry, fake_embeddings) -> MemoryService:
    return MemoryService(store=store, settings=settings, registry=registry, embeddings=fake_embeddings)


@pytest.fixture
def admin() -> Actor:
    return Actor(id="admin-1", roles=["workspace_admin"])


@pytest.fixture
def alice() -> Actor:
    return Actor(id="alice", roles=[])


@pytest.fixture
def bob() -> Actor:
    return Actor(id="bob", roles=[])
