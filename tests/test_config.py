import os

import pytest
import yaml

from memhub.config import ConfigError, load_config, parse_duration


def test_parse_duration():
    from datetime import timedelta

    assert parse_duration("1h") == timedelta(hours=1)
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("30m") == timedelta(minutes=30)
    with pytest.raises(ConfigError):
        parse_duration("banana")


def _write_yaml(tmp_path, data):
    path = tmp_path / "memhub.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _base_config():
    return {
        "project_prefix": "acme",
        "database_url": "${MEMHUB_DATABASE_URL}",
        "workspace_default": "acme",
        "llm": {
            "extractor": {"provider": "openai", "model": "gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY"},
            "judge": {"provider": "openai", "model": "gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY"},
        },
        "embeddings": {"provider": "openai", "model": "text-embedding-3-small", "api_key_env": "OPENAI_API_KEY", "dims": 1536},
        "entity_types": [],
        "scopes": ["user"],
        "types": {"fact": {"class": "memhub.types:Fact", "type_prior": 0.6}},
        "admission": {"weights": {"utility": 1.0}, "threshold": 0.5},
    }


def test_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://localhost/acme")
    path = _write_yaml(tmp_path, _base_config())
    settings = load_config(path)
    assert settings.database_url == "postgresql://localhost/acme"
    assert settings.project_prefix == "acme"


def test_missing_env_var_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMHUB_DATABASE_URL", raising=False)
    path = _write_yaml(tmp_path, _base_config())
    with pytest.raises(ConfigError):
        load_config(path)


def test_durations_parsed_from_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://localhost/acme")
    data = _base_config()
    data["ingestion"] = {"segment_idle": "2h", "thread_close": "3d"}
    path = _write_yaml(tmp_path, data)
    settings = load_config(path)
    from datetime import timedelta

    assert settings.ingestion.segment_idle == timedelta(hours=2)
    assert settings.ingestion.thread_close == timedelta(days=3)


def test_build_registry_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://localhost/acme")
    path = _write_yaml(tmp_path, _base_config())
    settings = load_config(path)
    registry = settings.build_registry()
    assert "fact" in registry


def test_example_config_loads(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://memhub:memhub@localhost:5433/memhub")
    settings = load_config(Path(__file__).parent.parent / "memhub.example.yaml")
    assert settings.project_prefix == "habitantes"
    assert settings.scopes == ["user", "workspace"]
    assert settings.sources["jsonl"].one_line_per == "turn"
    assert set(settings.build_registry().type_names()) == {"fact", "preference", "profile", "episode", "case", "skill", "area", "term"}


def test_ttl_defaults_and_durations(tmp_path, monkeypatch):
    from datetime import timedelta

    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://localhost/acme")
    data = _base_config()
    assert load_config(_write_yaml(tmp_path, data)).ttl == {
        "stable": None, "ongoing": timedelta(days=365), "temporary": timedelta(days=30),
    }
    data["ttl"] = {"stable": None, "ongoing": "365d", "temporary": "30d"}
    data["types"]["fact"]["ttl"] = "180d"
    settings = load_config(_write_yaml(tmp_path, data))
    assert settings.ttl["temporary"] == timedelta(days=30) and settings.ttl["stable"] is None
    assert settings.types["fact"].ttl == timedelta(days=180)


def test_ttl_rejects_unknown_durability_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://localhost/acme")
    data = _base_config()
    data["ttl"] = {"forever": "1d"}
    with pytest.raises(Exception):
        load_config(_write_yaml(tmp_path, data))


def test_habitantes_config_has_profile_and_preference_slots_episode_capped_and_no_plan(monkeypatch):
    from pathlib import Path

    from memhub.pipeline.extract import make_schema

    monkeypatch.setenv("MEMHUB_DATABASE_URL", "postgresql://memhub:memhub@localhost:5433/memhub")
    for name in ("memhub.yaml", "memhub.example.yaml"):
        settings = load_config(Path(__file__).parent.parent / name)
        assert "plan" not in settings.types and settings.guardrails.allow_inferred is False
        assert settings.types["profile"].keyed is False and "profile" not in settings.keyed_types()
        assert settings.types["preference"].strict_keys is False  # suggestions, like Profile no fixed schema
        assert list(settings.types["preference"].keys) == ["language", "scope", "detail", "format", "emoji", "tone", "sources", "address"]
        assert settings.types["episode"].extract is True
        registry = settings.build_registry()
        assert "profile" in registry and "plan" not in registry
        assert "language" in str(make_schema(["preference"], registry, settings.keyed_types()).model_json_schema())
    assert load_config(Path(__file__).parent.parent / "memhub.yaml").types["episode"].max_active == 10


def test_a_keyed_type_must_list_its_keys():
    from memhub.config import TypeConfig

    with pytest.raises(Exception):
        TypeConfig(**{"class": "memhub.types:Profile"}, keyed=True)
