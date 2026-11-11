"""Where memhub's configuration comes from, and how it becomes a `Config` and the app's objects.

Layers, last wins:  built-in defaults < preset < `memhub.yaml` < `MEMHUB_*` env < `config=` / `overrides=` in code.
A dict key is merged key by key; a scalar or a list is replaced whole. The one exception is `types:`: a type named
in a higher layer replaces that type whole, `name: null` removes it (a preset's types are then a starting point).

`resolve(...)` returns a `Resolved`: the `Config`, the layer each key came from (`memhub config show`), and the
file read. The files are read *raw*: `${VAR}` references stay in `Config._raw`, so `Config.save` and
`memhub config set` write references back, never the secret.

`Parts` turns a `Config` plus whatever the app passed into the objects memhub needs (`db`, `embedder`, `llm`,
`judge`): the app's own `connect: "module:function"`, else `db:` and `models:` / the older `llm:` and
`embeddings:` blocks. It builds each object when it is first asked for.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml
from pydantic import ValidationError

from memhub.config import KEY_ALIASES, Config, ConfigError, EmbeddingConfig, ModelConfig, canonical_keys

__all__ = [
    "CONFIG_FILE", "Parts", "Resolved", "SECRET_KEYS", "context_path", "default_layer", "discover", "load_raw",
    "mask", "preset_names", "read_context", "resolve", "save_config", "set_key", "write_context", "write_yaml",
]

CONFIG_FILE = "memhub.yaml"
SECRET_KEYS = ("password", "api_key", "token", "secret")  # a key with one of these names holds `${VAR}`, never a value
RESERVED_ENV = {"CONFIG", "CLI_ROLES", "WORKSPACE", "USER"}  # MEMHUB_<name> variables that are not config keys
LAYERS = ("default", "preset", "yaml", "env", "code")

_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_GUARD = re.compile(r"__memhub_ref_([A-Za-z_][A-Za-z0-9_]*)__")
_ONLY_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


# --- reading ----------------------------------------------------------------------------------------------------


def _restore(obj: Any) -> Any:
    if isinstance(obj, str):
        return _GUARD.sub(lambda m: "${" + m.group(1) + "}", obj)
    if isinstance(obj, list):
        return [_restore(v) for v in obj]
    if isinstance(obj, dict):
        return {_restore(k): _restore(v) for k, v in obj.items()}
    return obj


def parse_raw(text: str, where: str = "config") -> dict[str, Any]:
    """The yaml as a dict with every `${VAR}` left as written. A reference is guarded while yaml parses, so it may
    sit anywhere (an unquoted flow mapping included)."""
    try:
        data = yaml.safe_load(_REF.sub(lambda m: f"__memhub_ref_{m.group(1)}__", text))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{where} is not valid yaml: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a mapping of keys, not a {type(data).__name__}")
    return _restore(data)


def _importable(*folders: Path) -> None:
    for folder in folders:  # a project's own type classes, sources and `connect:` function sit next to its yaml
        if str(folder) not in sys.path:
            sys.path.append(str(folder))


def load_raw(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    _importable(path.resolve().parent, Path.cwd())
    return parse_raw(path.read_text(), str(path))


def interpolate(obj: Any, environ: Mapping[str, str], *, strict: bool = True) -> Any:
    """Replace `${VAR}` in every string. `strict`: an unset variable is a `ConfigError`; else it stays as written."""
    if isinstance(obj, str):
        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            if name in environ:
                return environ[name]
            if strict:
                raise ConfigError(f"environment variable {name!r} referenced in config is not set")
            return m.group(0)

        return _REF.sub(repl, obj)
    if isinstance(obj, list):
        return [interpolate(v, environ, strict=strict) for v in obj]
    if isinstance(obj, dict):
        return {k: interpolate(v, environ, strict=strict) for k, v in obj.items()}
    return obj


def discover(environ: Mapping[str, str] | None = None) -> Path | None:
    """`./memhub.yaml`, then the file `$MEMHUB_CONFIG` names, then `~/.config/memhub/memhub.yaml`: the first that exists."""
    environ = os.environ if environ is None else environ
    here = Path.cwd() / CONFIG_FILE
    if here.is_file():
        return here
    named = environ.get("MEMHUB_CONFIG")
    if named:
        path = Path(named).expanduser()
        if not path.is_file():
            raise ConfigError(f"MEMHUB_CONFIG points to {path}, which does not exist")
        return path
    home = Path.home() / ".config" / "memhub" / CONFIG_FILE
    return home if home.is_file() else None


SEARCH_PLACES = f"./{CONFIG_FILE}, $MEMHUB_CONFIG, ~/.config/memhub/{CONFIG_FILE}"


# --- layers -----------------------------------------------------------------------------------------------------


_DEFAULT_TYPES: dict[str, dict[str, Any]] = {
    "fact": {"type_prior": 0.6},
    "preference": {"type_prior": 0.8},
    "profile": {"type_prior": 0.8},
    "episode": {"type_prior": 0.4},
    "case": {"type_prior": 0.7, "extract": False, "on_remember": True},
    "skill": {"retrieval": "index_then_load", "extract": False},
    "area": {"extract": False, "scopes": ["user"]},
    "term": {"scopes": ["workspace"]},
}


def default_layer() -> dict[str, Any]:
    """memhub's behaviour when nothing says otherwise: every built-in type, user and workspace scopes."""
    from memhub.types import BUILTIN_TYPES

    return {
        "project_prefix": "memhub",
        "workspace_default": "default",
        "scopes": ["user", "workspace"],
        "ttl": {"stable": None, "ongoing": "365d", "temporary": "30d"},
        "types": {name: {"class": f"memhub.types:{BUILTIN_TYPES[name].__name__}", **opts} for name, opts in _DEFAULT_TYPES.items()},
    }


def preset_names() -> list[str]:
    return sorted(p.name[: -len(".yaml")] for p in resources.files("memhub.presets").iterdir() if p.name.endswith(".yaml"))


def load_preset(name: str) -> dict[str, Any]:
    names = preset_names()
    if name not in names:
        raise ConfigError(f"unknown preset {name!r}; available: {', '.join(names)}")
    return parse_raw(resources.files("memhub.presets").joinpath(f"{name}.yaml").read_text(), f"preset {name}")


def env_layer(environ: Mapping[str, str]) -> dict[str, Any]:
    """`MEMHUB_<KEY>[__<SUB>...]`, lower-cased, the value parsed as yaml. A name that is no config key is ignored."""
    known = set(Config.model_fields) | set(KEY_ALIASES)
    out: dict[str, Any] = {}
    for name in sorted(environ):
        if not name.startswith("MEMHUB_") or name[len("MEMHUB_"):] in RESERVED_ENV:
            continue
        path = name[len("MEMHUB_"):].lower().split("__")
        if not all(path) or path[0] not in known:
            continue
        path[0] = KEY_ALIASES.get(path[0], path[0])
        if path[0] == "types":
            raise ConfigError(f"{name}: types are set in memhub.yaml or in `overrides=`, not in the environment")
        try:
            value = yaml.safe_load(environ[name])
        except yaml.YAMLError:
            value = environ[name]
        if not isinstance(value, (list, dict, type(None))):
            value = environ[name]  # a plain value stays text: validation turns it into the field's type ("5" -> 5, "123" stays a str)
        node = out
        for seg in path[:-1]:
            node = node.setdefault(seg, {})
            if not isinstance(node, dict):
                raise ConfigError(f"{name} clashes with another MEMHUB_ variable")
        node[path[-1]] = value
    return out


def _duration(value: timedelta) -> str:
    return f"{int(value.total_seconds())}s"


def _plain(obj: Any) -> Any:
    """A dumped `Config` as yaml-safe data (durations as `<n>s`, tuples as lists)."""
    if isinstance(obj, timedelta):
        return _duration(obj)
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def _set_fields(cfg: Config) -> dict[str, Any]:
    """What a `Config` object adds as a layer: its raw form when it has one, else the fields that were set."""
    if cfg._raw is not None:
        return copy.deepcopy(cfg._raw)
    return _plain(cfg.model_dump(mode="python", by_alias=True, exclude_unset=True))


def _deep(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        out[key] = _deep(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else copy.deepcopy(value)
    return out


def _overlay_types(base: dict[str, Any], over: Any, *, keep_null: bool = False) -> dict[str, Any]:
    """A type in `over` replaces the one of that name whole; `null` removes it (`keep_null`: the removal is kept, so a
    layer stays an instruction to a lower one)."""
    out = dict(base)
    for name, spec in (over or {}).items():
        if spec is None:
            if keep_null:
                out[name] = None
            else:
                out.pop(name, None)
        else:
            out[name] = copy.deepcopy(spec)
    return out


def _merge_layers(low: dict[str, Any], high: dict[str, Any]) -> dict[str, Any]:
    out = _deep({k: v for k, v in low.items() if k != "types"}, {k: v for k, v in high.items() if k != "types"})
    if "types" in low or "types" in high:
        out["types"] = _overlay_types(_overlay_types({}, low.get("types"), keep_null=True), high.get("types"), keep_null=True) if "types" in high else copy.deepcopy(low["types"])
    return out


def _leaves(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, dict) and obj:
        out: dict[str, Any] = {}
        for key, value in obj.items():
            out.update(_leaves(value, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: obj}


# --- resolve ----------------------------------------------------------------------------------------------------


@dataclass
class Resolved:
    """The result of `resolve`: the config, where each key came from, and the file that was read."""

    config: Config
    sources: dict[str, str]
    path: Path | None
    values: dict[str, Any]  # every key after the merge, `${VAR}` interpolated where the variable is set

    def source_of(self, key: str) -> str:
        if key in self.sources:
            return self.sources[key]
        if key.startswith("types."):
            return self.sources.get("types." + key.split(".")[1], "default")
        return "default"

    def rows(self, *, all: bool = False) -> list[tuple[str, Any, str]]:
        """`(key, value, source)` for every leaf key set above the defaults (or every key, with `all`), secrets masked."""
        dumped = _leaves(_plain(self.config.model_dump(mode="python", by_alias=True)))
        set_values = _leaves(self.values)
        out = []
        for key in sorted(dumped):
            source = self.source_of(key)
            if source == "default" and not all:
                continue
            value = set_values[key] if key in set_values else dumped[key]
            shown = {name: alias for alias, name in KEY_ALIASES.items()}.get(key, key)  # the short names `init` writes
            out.append((shown, mask(key, value), source))
        return out


def mask(key: str, value: Any) -> Any:
    """A value fit to print: secret-named keys and the password of a URL become `***`."""
    if key.rsplit(".", 1)[-1] in SECRET_KEYS and value is not None:
        return "***"
    if isinstance(value, str):
        return re.sub(r"(://[^:/\s]+:)(\S*)@", r"\1***@", value)  # up to the last @: a password may hold one
    return value


def _resolve(
    *, yaml_layer: dict[str, Any], path: Path | None, code_layer: dict[str, Any], environ: Mapping[str, str], strict: bool,
) -> Resolved:
    yaml_layer = canonical_keys(yaml_layer, str(path) if path else "config")
    layers: list[tuple[str, dict[str, Any]]] = [("yaml", yaml_layer), ("env", env_layer(environ)), ("code", code_layer)]
    name = None
    for _, layer in layers:
        if layer.get("preset") is not None:
            name = layer["preset"]
    preset = load_preset(name) if name else {}

    merged: dict[str, Any] = {}
    sources: dict[str, str] = {}
    types: dict[str, Any] | None = None
    typed_by: dict[str, str] = {}
    if preset.get("types"):
        types = _overlay_types({}, preset["types"])
        typed_by = {t: "preset" for t in types}
    for label, layer in [("default", default_layer()), ("preset", preset), *layers]:
        if label == "default":
            default_types = layer["types"]
        for key, leaf in _leaves({k: v for k, v in layer.items() if k != "types"}).items():
            sources[key] = label
        merged = _deep(merged, {k: v for k, v in layer.items() if k != "types"})
        if label not in ("default", "preset") and "types" in layer:
            types = _overlay_types(types or {}, layer["types"])
            for t, spec in (layer["types"] or {}).items():
                if spec is None:
                    typed_by.pop(t, None)
                else:
                    typed_by[t] = label
    if types is None:
        types, typed_by = default_types, {t: "default" for t in default_types}
    merged["types"] = types
    sources.update({f"types.{t}": label for t, label in typed_by.items()})

    user_raw: dict[str, Any] = {}
    for _, layer in layers:
        user_raw = _merge_layers(user_raw, layer)
    values = interpolate(merged, environ, strict=strict)
    try:
        config = Config.model_validate(values)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(s) for s in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"invalid configuration: {problems}") from exc
    config._raw = user_raw
    return Resolved(config=config, sources=sources, path=path, values=values)


def resolve(
    config: str | Path | dict | Config | Resolved | None = None,
    overrides: dict[str, Any] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    strict: bool = True,
) -> Resolved:
    """`config`: a path (that file only), None (discover the yaml), a dict or a `Config` (no file is read; env still
    applies, and the dict beats it). `overrides` sits on top of everything."""
    if isinstance(config, Resolved):
        return config
    environ = os.environ if environ is None else environ
    path: Path | None = None
    yaml_layer: dict[str, Any] = {}
    code_layer: dict[str, Any] = {}
    if isinstance(config, (str, Path)):
        path = Path(config)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        yaml_layer = load_raw(path)
    elif config is None:
        path = discover(environ)
        if path is not None:
            yaml_layer = load_raw(path)
    elif isinstance(config, Config):
        code_layer = canonical_keys(_set_fields(config))
    elif isinstance(config, dict):
        code_layer = canonical_keys(copy.deepcopy(config))
    else:
        raise TypeError(f"config must be a path, a dict or a Config, not {type(config).__name__}")
    if overrides:
        code_layer = _merge_layers(code_layer, canonical_keys(copy.deepcopy(overrides), "overrides"))
    return _resolve(yaml_layer=yaml_layer, path=path, code_layer=code_layer, environ=environ, strict=strict)


# --- writing ----------------------------------------------------------------------------------------------------


def _is_ref(value: Any) -> bool:
    return isinstance(value, str) and _ONLY_REF.fullmatch(value) is not None


def check_no_secrets(data: Any, where: str = "") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            here = f"{where}.{key}" if where else str(key)
            if key in SECRET_KEYS and value is not None and not _is_ref(value):
                raise ConfigError(f"`{here}` holds a literal secret: write a ${{VAR}} reference and keep the value in the environment")
            check_no_secrets(value, here)
    elif isinstance(data, list):
        for i, value in enumerate(data):
            check_no_secrets(value, f"{where}[{i}]")


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def save_config(config: Config, path: str | Path) -> None:
    data = copy.deepcopy(config._raw) if config._raw is not None else _set_fields(config)
    check_no_secrets(data)  # before anything is written
    write_yaml(Path(path), data)


def set_key(path: str | Path, key: str, text: str, *, environ: Mapping[str, str] | None = None) -> None:
    """Set a dotted `key` to `text` (read as yaml) in the file, after checking the result is a valid `Config`."""
    path = Path(path)
    segs = key.split(".")
    value = yaml.safe_load(text)
    if segs[-1] in SECRET_KEYS and not _is_ref(value):
        raise ConfigError(f"`{key}` is a secret: pass a ${{VAR}} reference (a single variable), not the value")
    data = load_raw(path)
    head = segs[0]
    for alias, name in KEY_ALIASES.items():  # write to whichever spelling the file already uses
        if head == name and alias in data and name not in data:
            head = alias
        elif head == alias and name in data and alias not in data:
            head = name
    segs[0] = head
    node = data
    for seg in segs[:-1]:
        node = node.setdefault(seg, {})
        if not isinstance(node, dict):
            raise ConfigError(f"`{key}`: `{seg}` holds a value, not a block of keys")
    node[segs[-1]] = value
    try:
        _resolve(yaml_layer=copy.deepcopy(data), path=path, code_layer={}, environ=os.environ if environ is None else environ, strict=False)
    except ConfigError as exc:
        raise ConfigError(f"`{key}` = {text!r} is not valid: {exc}") from exc
    check_no_secrets(data)
    write_yaml(path, data)


# --- the CLI's current context ---------------------------------------------------------------------------------


def context_path() -> Path:
    return Path.home() / ".config" / "memhub" / "context"


def read_context() -> dict[str, str | None]:
    path = context_path()
    if not path.is_file():
        return {"workspace": None, "user": None}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError as exc:
        raise ConfigError(f"{path} is not valid JSON ({exc}): fix it or run `memhub use` again") from exc
    return {"workspace": data.get("workspace"), "user": data.get("user")}


def write_context(workspace: str, user: str | None) -> Path:
    path = context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"workspace": workspace, "user": user}))
    return path


# --- the app's objects ------------------------------------------------------------------------------------------


def _call_connect(spec: str, folder: Path | None) -> dict[str, Any]:
    module, _, func = spec.partition(":")
    if not module or not func:
        raise ConfigError(f"`connect` must be 'package.module:function', not {spec!r}")
    _importable(*([folder.resolve()] if folder else []), Path.cwd())
    try:
        fn = getattr(importlib.import_module(module), func)
    except ImportError as exc:
        raise ConfigError(f"`connect` {spec!r}: cannot import {module!r}: {exc}") from exc
    except AttributeError as exc:
        raise ConfigError(f"`connect` {spec!r}: module {module!r} has no {func!r}") from exc
    if not callable(fn):
        raise ConfigError(f"`connect` {spec!r} is not callable")
    built = fn()
    if not isinstance(built, dict):
        raise ConfigError(f"`connect` {spec!r} must return a dict with `db` and `embedder`, not {type(built).__name__}")
    missing = [k for k in ("db", "embedder") if built.get(k) is None]
    if missing:
        raise ConfigError(f"`connect` {spec!r} returned no {' or '.join(f'`{k}`' for k in missing)}: it must return {{db, embedder, llm?, judge?}}")
    return built


def embedding_model_id(embedder: Any, config: Config) -> str:
    if config.embeddings is not None:
        return config.embeddings.model
    if config.models is not None and config.models.embeddings:
        return config.models.embeddings
    return str(getattr(embedder, "model", None) or getattr(embedder, "model_name", None) or type(embedder).__name__)


@dataclass
class Parts:
    """`db`, `embedder`, `llm`, `judge`, each built when first asked for. What the app passed wins; then what `connect:`
    returned; then the config's own blocks."""

    resolved: Resolved
    passed: dict[str, Any] = field(default_factory=dict)
    build_chat: Callable[[ModelConfig], Any] | None = None
    build_embed: Callable[[EmbeddingConfig], Any] | None = None
    _connected: dict[str, Any] | None = None
    _built: dict[str, Any] = field(default_factory=dict)

    @property
    def config(self) -> Config:
        return self.resolved.config

    def _where(self) -> str:
        return "" if self.resolved.path else f" (no memhub.yaml found; looked in {SEARCH_PLACES})"

    def _connect(self) -> dict[str, Any] | None:
        if not self.config.connect:
            return None
        if self._connected is None:
            self._connected = _call_connect(self.config.connect, self.resolved.path.parent if self.resolved.path else None)
        return self._connected

    def _get(self, name: str, build: Callable[[], Any]) -> Any:
        if self.passed.get(name) is not None:
            return self.passed[name]
        if name not in self._built:
            self._built[name] = build()
        return self._built[name]

    @property
    def db(self) -> Any:
        def build() -> Any:
            connected = self._connect()
            if connected is not None:
                return connected["db"]
            if self.config.database_url:
                return self.config.database_url
            raise ConfigError("no database: set `connect`, `db` (or `database_url`) in memhub.yaml, or MEMHUB_DB" + self._where())

        return self._get("db", build)

    @property
    def embedder(self) -> Any:
        def build() -> Any:
            connected = self._connect()
            if connected is not None:
                return connected["embedder"]
            cfg = self.config
            if cfg.embeddings is not None:
                from memhub import config as config_module

                return (self.build_embed or config_module.build_embeddings)(cfg.embeddings)
            if cfg.models is not None and cfg.models.embeddings:
                from langchain.embeddings import init_embeddings

                return init_embeddings(cfg.models.embeddings)
            raise ConfigError("no embedding model: set `connect`, `models.embeddings` or `embeddings` in memhub.yaml" + self._where())

        return self._get("embedder", build)

    @property
    def llm(self) -> Any | None:
        def build() -> Any:
            connected = self._connect()
            if connected is not None and connected.get("llm") is not None:
                return connected["llm"]
            cfg = self.config
            if cfg.llm is not None:
                from memhub import config as config_module

                return (self.build_chat or config_module.build_chat_model)(cfg.llm.extractor)
            if cfg.models is not None and cfg.models.chat:
                from langchain.chat_models import init_chat_model

                return init_chat_model(cfg.models.chat)
            return None

        return self._get("llm", build)

    @property
    def judge(self) -> Any | None:
        def build() -> Any:
            if self.passed.get("llm") is not None:  # the app gave a model and no judge: its model judges too
                return self.passed["llm"]
            connected = self._connect()
            if connected is not None and connected.get("judge") is not None:
                return connected["judge"]
            cfg = self.config
            if cfg.llm is not None:
                from memhub import config as config_module

                return (self.build_chat or config_module.build_chat_model)(cfg.llm.judge)
            if cfg.models is not None and cfg.models.judge:
                from langchain.chat_models import init_chat_model

                return init_chat_model(cfg.models.judge)
            return self.llm

        return self._get("judge", build)
