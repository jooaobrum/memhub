"""``memhub.yaml`` -> :class:`Settings`.

Loading does ``${ENV_VAR}`` interpolation over the raw YAML text, then
validates the result with pydantic. Model entries are turned into LangChain
objects by :func:`build_chat_model` / :func:`build_embeddings` — memhub never
imports a provider SDK directly.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from memhub.types import TypeRegistry

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Raised for a malformed or inconsistent ``memhub.yaml``."""


def _interpolate_env(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ConfigError(f"environment variable {name!r} referenced in config is not set")
        return os.environ[name]

    return _ENV_PATTERN.sub(repl, text)


_DURATION_PATTERN = re.compile(r"^(\d+)\s*([smhdw])$")
_DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_duration(value: str | timedelta) -> timedelta:
    """Parse "1h", "7d", "30m", ... into a :class:`timedelta`."""
    if isinstance(value, timedelta):
        return value
    match = _DURATION_PATTERN.match(value.strip())
    if not match:
        raise ConfigError(f"invalid duration {value!r}, expected e.g. '1h', '7d', '30m'")
    amount, unit = match.groups()
    return timedelta(**{_DURATION_UNITS[unit]: int(amount)})


Durability = Literal["stable", "ongoing", "temporary"]

def _parse_optional_duration(v: Any) -> Any:
    return parse_duration(v) if isinstance(v, str) else v

class ModelConfig(BaseModel):
    provider: str = ""  # a langchain provider name; may stay empty when `factory` builds the model
    model: str
    base_url: str | None = None
    factory: str | None = None  # "package.module:function": builds the model yourself (Azure AD tokens, a wrapper, LiteLLM); gets this config
    api_key_env: str | None = None  # None: no key from the environment (Ollama, Bedrock/ADC credentials, Azure managed identity via `params`)
    params: dict[str, Any] = Field(default_factory=dict)


class EmbeddingConfig(ModelConfig):
    dims: int


class LLMConfig(BaseModel):
    extractor: ModelConfig
    judge: ModelConfig


class TypeConfig(BaseModel):
    class_path: str | None = Field(default=None, alias="class")  # "package.module:Class"; or declare `fields` instead
    fields: dict[str, Any] | None = None  # a type declared in the yaml, no Python: {equipment: str, fix: "str?"}
    retrieval: str = "search"  # search | always | index_then_load
    type_prior: float = 0.5
    max_chars: int | None = None  # `always` types: prompt budget, whole entries only
    max_active: int | None = None  # cap on active rows of this type per owner (user, or workspace)
    extract: bool = True
    review: bool = True  # false: a workspace-scope memory of this type is active at once, not a candidate for an admin
    content_template: str | None = None  # `content` is composed from the fields, e.g. "Symptom: {symptom} Action: {action}"
    on_remember: bool = False  # offered to the extractor when the user orders `/remember`, even if `extract` is false
    ttl: timedelta | None = None  # how long a memory of this type holds; beats the durability ttl
    keyed: bool = False  # one active row per (owner, type, key); `keys` lists the allowed keys
    keys: dict[str, str] = Field(default_factory=dict)  # key -> one-line description
    strict_keys: bool = True  # false: `keys` are only suggestions in the prompt; any short key, or none, is accepted
    immutable_keys: list[str] = Field(default_factory=list)  # keys whose value is never replaced (`mutable: false`)
    area: Literal["required", "optional"] | None = None  # required: a memory with no area is dropped
    scopes: list[str] | None = None  # scopes this kind may take; None = the project's `scopes`

    model_config = {"populate_by_name": True}

    _parse_ttl = field_validator("ttl", mode="before")(_parse_optional_duration)

    @model_validator(mode="before")
    @classmethod
    def _key_options(cls, data: Any) -> Any:
        """A key is `name: "description"` or `name: {description: ..., mutable: false}`."""
        if not isinstance(data, dict) or not any(isinstance(v, dict) for v in (data.get("keys") or {}).values()):
            return data
        keys, immutable = {}, list(data.get("immutable_keys") or [])
        for name, spec in data["keys"].items():
            if isinstance(spec, dict):
                keys[name] = spec.get("description", name)
                if spec.get("mutable", True) is False:
                    immutable.append(name)
            else:
                keys[name] = spec
        return {**data, "keys": keys, "immutable_keys": immutable}

    @model_validator(mode="after")
    def _has_a_schema(self) -> "TypeConfig":
        if not self.class_path and not self.fields:
            raise ValueError("a type needs `class` (package.module:Class) or `fields` (its declared schema)")
        return self

    @model_validator(mode="after")
    def _keyed_needs_keys(self) -> "TypeConfig":
        if self.keyed and not self.keys:
            raise ValueError("a keyed type must list its `keys`")
        return self


class SeedArea(BaseModel):
    key: str
    title: str
    icon: str = ""
    description: str = ""


class AreasConfig(BaseModel):
    seeds: list[SeedArea] = Field(default_factory=list)
    open: bool = True  # false: the model may not propose a new area
    merge_similarity: float = 0.88  # a proposed area this close to an existing one merges into it
    max_per_user: int = 25
    max_per_workspace: int = 40
    page_max_items: int = 15
    page_max_chars: int = 1500  # budget of the page injected into a turn by the middleware
    page_min_similarity: float = 0.5  # a query less similar than this to every area gets no page


class TermsConfig(BaseModel):
    max_per_workspace: int = 300  # only used by projects that enable the `term` type
    max_aliases: int = 5
    min_confidence: float = 0.9  # a term the model defines itself (not confirmed by the user) needs at least this


class SignalsConfig(BaseModel):
    path: str | None = None
    query: str | None = None  # `kind: sql`: a SELECT returning the feedback rows
    join_on: dict[str, str] = Field(default_factory=dict)
    map: dict[str, dict[str, str]] = Field(default_factory=dict)


class SourceConfig(BaseModel):
    model_config = {"extra": "allow"}  # a custom adapter reads its own options from its yaml block

    kind: str  # jsonl | mlflow | sql | "package.module:Class" (your own adapter: Class(cfg, workspace_id=...) with read())
    dsn_env: str | None = None  # `sql`: the environment variable holding the database URL
    query: str | None = None  # `sql`: a SELECT returning one row per turn
    path: str | None = None
    one_line_per: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    signals: SignalsConfig | None = None
    tracking_uri: str | None = None
    experiment: str | None = None


class IngestionConfig(BaseModel):
    segment_idle: timedelta = timedelta(hours=1)
    thread_close: timedelta = timedelta(days=7)
    min_user_turns: int = 1
    max_candidates: int = 3
    remember_context: int = 30  # messages above a `/remember` that the extractor reads with it
    remember_command: str | None = "/remember"  # a user message starting with it is an order to keep what follows
    assistant_chars: int | None = None  # an assistant answer is shown to the extractor cut to this many characters
    verify_claims: bool = False  # the judge model checks the claims that look invented or about someone else against their quote
    repair_relative_time: bool = False  # the judge model dates a claim that says "next year" instead of it being dropped
    verify_episodes: bool = False  # the judge model must confirm that the user told an episode's three parts
    segment_max_user_turns: int | None = 8  # a long thread is extracted in slices of this many user turns (None: whole)
    llm_correction_check: bool = False  # cheap-LLM fallback (judge model) for corrections the regexes miss
    skip_when: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("segment_idle", "thread_close", mode="before")
    @classmethod
    def _parse_duration(cls, v: Any) -> Any:
        return parse_duration(v) if isinstance(v, str) else v


DEFAULT_EXTRACTION_INSTRUCTIONS = """\
A memory is durable knowledge from the conversation that would help the assistant in a FUTURE conversation.
Most segments must yield NO candidates - return an empty list. NEVER store:
- a question, or the fact that someone asked or wondered about something;
- a one-off request, or what is being searched for right now;
- general knowledge, or anything that only appears in the assistant's answers;
- greetings, thanks, small talk, or a summary of the conversation.
Write each memory in the language of the conversation, as a self-contained statement.
Utility (1-5) is how much the memory would help later: 5 = key durable information, 3 = mildly useful,
1-2 = trivia or a one-off need. Be strict.
"""


class ExtractionConfig(BaseModel):
    """`instructions` is the domain policy: what is worth remembering in THIS project (a support bot
    keeps user circumstances and preferences; a maintenance agent keeps machine facts and fixes).
    The mechanics (evidence quotes, type fields, entity rule) stay in code."""

    instructions: str = DEFAULT_EXTRACTION_INSTRUCTIONS
    passes: int = 1  # independent extraction samples per slice, their candidates united (a cheap model misses different facts each time)
    remember_instructions: str | None = None  # what to do with a `/remember` order (None: the built-in text)
    retries: int = 0  # extra attempts when the model returns unparseable output (cheap models truncate the JSON now and then)


class AdmissionConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {"utility": 0.35, "evidence": 0.30, "novelty": 0.20, "type_prior": 0.10, "signals": 0.05}
    )
    threshold: float = 0.5


class ReconcileConfig(BaseModel):
    duplicate: float = 0.92
    conflict_band: tuple[float, float] = (0.80, 0.92)  # a slot-less candidate this similar is compared for sure
    compare_floor: float = 0.30  # below the band, the nearest rows at least this similar are still put to the judge
    compare_max: int = 4  # rows shown to the judge in the one call it makes for a candidate
    extends: bool = True  # false: two complementary statements stay two rows (an `extends` verdict counts as `unrelated`)
    compare_across: list[list[str]] = Field(default_factory=list)  # types whose rows are compared with each other's


class RolesConfig(BaseModel):
    approve_workspace: list[str] = Field(default_factory=lambda: ["workspace_admin"])


class GuardrailsConfig(BaseModel):
    """Conservative defaults: memhub stores what the user or a tool actually said, nothing deduced."""

    allow_inferred: bool = False  # false: a candidate with assertion=inferred is dropped as `inferred`


class RetentionConfig(BaseModel):
    dropped_days: int = 90


class Settings(BaseModel):
    project_prefix: str  # table prefix: several projects can share one database
    database_url: str
    database_schema: str | None = None  # Postgres schema for the tables (created if missing); default: the search_path
    workspace_default: str
    llm: LLMConfig
    embeddings: EmbeddingConfig
    entity_types: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=lambda: ["user"])
    types: dict[str, TypeConfig]
    # Default lifetime by durability, used when neither the text nor the type sets `valid_until`; null = never.
    ttl: dict[Durability, timedelta | None] = Field(
        default_factory=lambda: {"stable": None, "ongoing": timedelta(days=365), "temporary": timedelta(days=30)}
    )
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    admission: AdmissionConfig = Field(default_factory=AdmissionConfig)
    reconcile: ReconcileConfig = Field(default_factory=ReconcileConfig)
    roles: RolesConfig = Field(default_factory=RolesConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    areas: AreasConfig = Field(default_factory=AreasConfig)
    terms: TermsConfig = Field(default_factory=TermsConfig)

    @field_validator("project_prefix", "database_schema")
    @classmethod
    def _identifier(cls, v: str | None) -> str | None:
        """The prefix and the schema go into SQL identifiers: letters, digits and underscores only."""
        if v is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", v):
            raise ValueError(f"{v!r} must start with a letter and use only letters, digits and underscores (max 41)")
        return v.lower() if v is not None else v

    @model_validator(mode="after")
    def _type_scopes_are_enabled(self) -> "Settings":
        for name, cfg in self.types.items():
            if cfg.scopes is not None and (not cfg.scopes or not set(cfg.scopes) <= set(self.scopes)):
                raise ValueError(f"type {name!r} scopes {cfg.scopes} must be a non-empty subset of scopes {self.scopes}")
        return self

    def type_scopes(self, type_name: str) -> list[str]:
        """The scopes a candidate of this type may take: its own `scopes`, else the project's."""
        return self.types[type_name].scopes or self.scopes

    def compare_types(self, type_name: str) -> list[str]:
        """The type itself, then the types a candidate of it is also reconciled with (`reconcile.compare_across`)."""
        others = [t for group in self.reconcile.compare_across if type_name in group for t in group if t != type_name]
        return [type_name, *dict.fromkeys(others)]

    def is_slot(self, type_name: str, memory: object) -> bool:
        """True when the type is keyed and the memory carries a key: only then does it replace by slot."""
        cfg = self.types.get(type_name)
        return bool(cfg is not None and cfg.keyed and getattr(memory, "key", None))

    def open_key_types(self) -> set[str]:
        """Keyed types whose keys are suggestions, not a closed list."""
        return {n for n, c in self.types.items() if c.keyed and not c.strict_keys}

    def keyed_types(self) -> dict[str, dict[str, str]]:
        """Type name -> {key: description} for every type marked `keyed`."""
        return {n: c.keys for n, c in self.types.items() if c.keyed}

    def area_types(self) -> dict[str, str]:
        """Type name -> `required` | `optional` for every type that takes areas."""
        return {n: c.area for n, c in self.types.items() if c.area}

    def is_mutable(self, type_name: str, key: str | None) -> bool:
        return key not in self.types[type_name].immutable_keys

    def type_prior(self, type_name: str) -> float:
        return self.types[type_name].type_prior

    @field_validator("ttl", mode="before")
    @classmethod
    def _parse_ttl(cls, v: Any) -> Any:
        return {k: _parse_optional_duration(d) for k, d in v.items()} if isinstance(v, dict) else v

    def default_valid_until(self, type_name: str, durability: str | None, observed_at: datetime) -> datetime | None:
        """Expiry when the text gave none: `observed_at` + the type's ttl, else + the durability's ttl, else None."""
        ttl = self.types[type_name].ttl or self.ttl.get(durability)  # type: ignore[arg-type]
        return observed_at + ttl if ttl is not None else None

    def build_registry(self) -> TypeRegistry:
        registry = TypeRegistry()
        from memhub.types import model_from_fields

        for type_name, type_cfg in self.types.items():
            if type_cfg.class_path:
                registry.register_path(type_name, type_cfg.class_path)
            else:
                registry.register(type_name, model_from_fields(type_name, type_cfg.fields or {}))
        return registry


def load_config(path: str | Path) -> Settings:
    raw = Path(path).read_text()
    for folder in (Path(path).resolve().parent, Path.cwd()):  # a project's own type classes and source adapters sit next to its yaml
        if str(folder) not in sys.path:
            sys.path.append(str(folder))
    interpolated = _interpolate_env(raw)
    data = yaml.safe_load(interpolated)
    return Settings.model_validate(data)


# --- Model construction -----------------------------------------------------


def _from_factory(cfg: ModelConfig):
    import importlib

    module, _, name = cfg.factory.partition(":")
    return getattr(importlib.import_module(module), name)(cfg)


def build_chat_model(cfg: ModelConfig):  # pragma: no cover - exercised via fakes in tests
    """Build a LangChain chat model from a :class:`ModelConfig`.

    memhub never imports a provider SDK directly; the provider's LangChain
    integration package must be installed (see the ``memhub[openai]`` /
    ``memhub[anthropic]`` extras).
    """
    if cfg.factory:
        return _from_factory(cfg)
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {"model_provider": cfg.provider, **cfg.params}
    if cfg.base_url:  # an OpenAI-compatible gateway (`base_url`), or an Azure resource (`azure_endpoint`)
        kwargs["azure_endpoint" if cfg.provider.startswith("azure") and "azure_endpoint" not in kwargs else "base_url"] = cfg.base_url
    api_key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
    if api_key:
        kwargs["api_key"] = api_key
    model = init_chat_model(cfg.model, **kwargs)
    from langchain_core.language_models.chat_models import BaseChatModel

    # Every BaseChatModel has `with_structured_output`; it only works when the subclass
    # implements it (or `bind_tools`, which the base version relies on).
    cls = type(model)
    if cls.with_structured_output is BaseChatModel.with_structured_output and cls.bind_tools is BaseChatModel.bind_tools:
        raise ConfigError(f"model {cfg.model!r} ({cfg.provider}) does not support structured output")
    return model


def build_embeddings(cfg: EmbeddingConfig):  # pragma: no cover - exercised via fakes in tests
    if cfg.factory:
        return _from_factory(cfg)
    from langchain.embeddings import init_embeddings

    kwargs: dict[str, Any] = {**cfg.params}
    if cfg.base_url:  # an OpenAI-compatible gateway, or an Azure resource
        kwargs["azure_endpoint" if cfg.provider.startswith("azure") and "azure_endpoint" not in kwargs else "base_url"] = cfg.base_url
    api_key = os.environ.get(cfg.api_key_env) if cfg.api_key_env else None
    if api_key:
        kwargs["api_key"] = api_key
    return init_embeddings(f"{cfg.provider}:{cfg.model}", **kwargs)
