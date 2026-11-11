"""Layers, discovery, aliases, `types:` and `save`: where memhub's configuration comes from."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from memhub import Config, Memory
from memhub.config import ConfigError, Settings
from memhub.settings import discover, resolve
from tests.fakes import FakeEmbeddings

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
    return path


def _here(data: dict) -> Path:
    return _write(Path.cwd() / "memhub.yaml", data)


def _home(data: dict) -> Path:
    return _write(Path.home() / ".config" / "memhub" / "memhub.yaml", data)


def test_layers_last_wins_over_each_lower_layer():
    assert resolve().config.reconcile.duplicate == 0.92  # default
    _here({"reconcile": {"duplicate": 0.9}})
    assert resolve().config.reconcile.duplicate == 0.9  # yaml
    env = {"MEMHUB_RECONCILE__DUPLICATE": "0.95"}
    assert resolve(environ=env).config.reconcile.duplicate == 0.95  # env
    r = resolve(overrides={"reconcile": {"duplicate": 0.97}}, environ=env)
    assert r.config.reconcile.duplicate == 0.97  # code
    # the preset sits under the yaml: `support` sets ingestion.max_candidates 5, the yaml says 2
    _here({"preset": "support", "ingestion": {"max_candidates": 2}})
    assert resolve().config.ingestion.max_candidates == 2
    _here({"preset": "support"})
    assert resolve().config.ingestion.max_candidates == 5


def test_blocks_merge_per_key_scalars_and_lists_replace():
    _here({"ttl": {"temporary": "7d"}, "preset": "support", "scopes": ["user"], "types": {"account": None, "ticket": None, "fact": None}})
    cfg = resolve().config
    assert cfg.ttl["temporary"] == timedelta(days=7) and cfg.ttl["ongoing"] == timedelta(days=365)
    assert cfg.scopes == ["user"]  # the preset's [user, workspace] is replaced whole


def test_discovery_takes_first_existing_place(tmp_path):
    env_file = _write(tmp_path / "elsewhere.yaml", {"project": "from_env_file"})
    env = {"MEMHUB_CONFIG": str(env_file)}
    _home({"project": "from_home"})
    assert resolve(environ=env).config.project_prefix == "from_env_file"  # env one beats home
    _here({"project": "from_here"})
    r = resolve(environ=env)
    assert r.config.project_prefix == "from_here" and r.path == Path.cwd() / "memhub.yaml"  # ./ beats both
    (Path.cwd() / "memhub.yaml").unlink()
    (Path.home() / ".config" / "memhub" / "memhub.yaml").write_text("project: from_home\n")
    assert resolve().config.project_prefix == "from_home"  # only home


def test_no_yaml_anywhere_gives_defaults():
    assert discover({}) is None
    cfg = resolve().config
    assert cfg.project_prefix == "memhub" and set(cfg.types) == {"fact", "preference", "profile", "episode", "case", "skill", "area", "term"}
    m = Memory(db="postgresql://nobody@127.0.0.1:1/none", embedder=FakeEmbeddings(16))
    assert m.settings.project_prefix == "memhub" and len(m.settings.types) == 8


def test_memhub_config_env_pointing_nowhere_is_error(tmp_path):
    _home({"project": "from_home"})
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="nope.yaml"):
        resolve(environ={"MEMHUB_CONFIG": str(missing)})


def test_config_path_dict_and_object_skip_discovery(tmp_path):
    _here({"project": "from_here"})
    other = _write(tmp_path / "other.yaml", {"project": "from_path"})
    assert resolve(other).config.project_prefix == "from_path"
    with pytest.raises(ConfigError, match="gone.yaml"):
        resolve(tmp_path / "gone.yaml")
    assert resolve({"project": "a"}).config.project_prefix == "a"  # the file in ./ is not read
    assert resolve(Config(project_prefix="obj", workspace_default="w", types={})).config.project_prefix == "obj"
    assert resolve({"project": "a"}, environ={"MEMHUB_PROJECT": "b"}).config.project_prefix == "a"  # code beats env
    assert resolve({"workspace_default": "w2"}, environ={"MEMHUB_PROJECT": "b"}).config.project_prefix == "b"  # env still applies


def test_env_keys_map_to_nested_yaml_values():
    env = {
        "MEMHUB_PRESET": "support",
        "MEMHUB_DB": "postgresql://u@h/d",
        "MEMHUB_INGESTION__MAX_CANDIDATES": "9",
        "MEMHUB_SCOPES": "[user, workspace]",
    }
    r = resolve(environ=env)
    cfg = r.config
    assert cfg.preset == "support" and cfg.database_url == "postgresql://u@h/d"
    assert cfg.ingestion.max_candidates == 9 and isinstance(cfg.ingestion.max_candidates, int)
    assert cfg.scopes == ["user", "workspace"]
    assert r.source_of("ingestion.max_candidates") == "env"


def test_reserved_and_unknown_env_vars_are_not_config(tmp_path):
    named = _write(tmp_path / "x.yaml", {})
    base = resolve(environ={}).values
    env = {"MEMHUB_CONFIG": str(named), "MEMHUB_CLI_ROLES": "a", "MEMHUB_WORKSPACE": "w", "MEMHUB_USER": "u", "MEMHUB_FOO": "1"}
    assert resolve(environ=env).values == base
    assert "config" not in resolve(environ=env).values


def test_env_var_on_types_is_error():
    with pytest.raises(ConfigError, match="MEMHUB_TYPES__FACT__TYPE_PRIOR"):
        resolve(environ={"MEMHUB_TYPES__FACT__TYPE_PRIOR": "0.9"})


def test_project_and_db_aliases():
    _here({"database_url": "A"})
    assert resolve(environ={"MEMHUB_DB": "B"}).config.database_url == "B"
    _here({"project": "x", "project_prefix": "y"})
    with pytest.raises(ConfigError, match="project.*project_prefix"):
        resolve()
    assert Config.model_validate({"project": "z", "workspace_default": "w", "types": {}}).project_prefix == "z"


def test_examples_still_load(monkeypatch):
    for var in ("MEMHUB_DATABASE_URL", "MAINT_DATABASE_URL", "CRM_URL", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.setenv(var, "postgresql://u@h/d")
    expected = {
        "examples/habitantes/memhub.yaml": ("habitantes", {"fact", "profile", "preference", "episode", "case", "term", "area", "skill"}, {"jsonl"}),
        "examples/maintenance/memhub.yaml": ("maint", {"incident"}, {"cmms"}),
        "examples/support/memhub.yaml": ("support", {"account", "preference", "ticket", "fact"}, {"crm"}),
        "memhub.example.yaml": ("habitantes", {"fact", "profile", "preference", "episode", "case", "term", "skill"}, {"jsonl"}),
    }
    sys_path = list(__import__("sys").path)
    try:
        for rel, (prefix, types, sources) in expected.items():
            cfg = resolve(ROOT / rel).config
            assert (cfg.project_prefix, set(cfg.types), set(cfg.sources)) == (prefix, types, sources), rel
    finally:
        __import__("sys").path[:] = sys_path
    assert Settings is Config
    import memhub

    assert memhub.Config is Config


def test_optional_keys_may_be_absent():
    _here({"project": "p", "preset": "assistant"})
    cfg = resolve().config
    assert cfg.database_url is None and cfg.llm is None and cfg.embeddings is None and cfg.project_prefix == "p"


def test_preset_layer_and_unknown_preset(tmp_path):
    _here({"preset": "maintenance"})
    assert set(resolve().config.types) == {"incident"}
    assert set(resolve(environ={"MEMHUB_PRESET": "support"}).config.types) == {"account", "preference", "ticket", "fact"}  # env beats yaml
    assert set(resolve(overrides={"preset": "assistant"}, environ={"MEMHUB_PRESET": "support"}).config.scopes) == {"user"}  # code beats env
    _here({"preset": "nope"})
    with pytest.raises(ConfigError, match="assistant, maintenance, support"):
        resolve()


def test_types_block_replaces_adds_removes():
    base = {"preset": "support"}
    over = resolve({**base, "types": {"ticket": {"fields": {"symptom": "str"}}}}).config
    assert set(over.types["ticket"].fields) == {"symptom"} and "account" in over.types  # replaced whole, others kept
    assert "account" not in resolve({**base, "types": {"account": None}}).config.types
    added = resolve({**base, "types": {"faq": {"fields": {"q": "str"}}}}).config
    assert "faq" in added.types and {"account", "ticket"} <= set(added.types)
    # without a preset the types given are the types (as in PR 1), not added to the built-in eight
    assert set(resolve({"types": {"faq": {"fields": {"q": "str"}}}, "scopes": ["user"]}).config.types) == {"faq"}


def test_save_round_trips_and_keeps_var_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("K", "sk-real")
    _here({"project": "p", "preset": "support", "llm": {
        "extractor": {"provider": "openai", "model": "x", "params": {"api_key": "${K}"}},
        "judge": {"provider": "openai", "model": "y"}}, "ttl": {"temporary": "7d"}})
    cfg = resolve().config
    assert cfg.llm.extractor.params["api_key"] == "sk-real"
    out = tmp_path / "saved.yaml"
    cfg.save(out)
    text = out.read_text()
    assert "${K}" in text and "sk-real" not in text
    assert resolve(out).config.model_dump() == cfg.model_dump()
    # a Config built by hand round-trips too
    built = Config(project_prefix="b", workspace_default="w", scopes=["user"], types={"fact": {"class": "memhub.types:Fact"}}, ttl={"temporary": "2d"})
    built.save(tmp_path / "b.yaml")
    again = resolve(tmp_path / "b.yaml").config
    assert (again.project_prefix, set(again.types), again.ttl["temporary"]) == ("b", {"fact"}, timedelta(days=2))


@pytest.mark.parametrize("key", ["password", "api_key", "token", "secret"])
def test_save_refuses_literal_secret_and_writes_nothing(key, tmp_path):
    target = tmp_path / "out.yaml"
    cfg = Config(project_prefix="p", workspace_default="w", types={"fact": {"class": "memhub.types:Fact"}},
                 llm={"extractor": {"provider": "openai", "model": "x", "params": {key: "sk-1"}}, "judge": {"provider": "openai", "model": "y"}})
    with pytest.raises(ConfigError, match=key):
        cfg.save(target)
    assert not target.exists() and not list(tmp_path.glob("*.tmp"))


def test_a_value_the_field_cannot_hold_is_a_config_error_not_a_traceback():
    with pytest.raises(ConfigError, match="project_prefix"):  # "123" is read as text, then refused as a table prefix
        resolve(environ={"MEMHUB_PROJECT": "123"})
    assert resolve(environ={"MEMHUB_INGESTION__MAX_CANDIDATES": "6"}).config.ingestion.max_candidates == 6
    with pytest.raises(ConfigError, match="ingestion.max_candidates"):
        resolve(environ={"MEMHUB_INGESTION__MAX_CANDIDATES": "many"})
