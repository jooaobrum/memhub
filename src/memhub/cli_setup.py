"""The setup commands of the CLI: `init`, `config show|set`, `doctor`, `use`. `cli.py` registers them."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

import psycopg
import typer
import yaml

from memhub.config import ConfigError
from memhub.connection import _is_pool
from memhub.settings import (
    CONFIG_FILE, SEARCH_PLACES, Parts, Resolved, discover, embedding_model_id, load_preset, preset_names, resolve,
    set_key, write_context, write_yaml,
)
from memhub.storage import open_backend
from memhub.storage.postgres import MemoryStore
from memhub.storage.rules import StoreError

# what a database can answer `init` with: a configuration problem to report, not a crash
_INIT_ERRORS = (ConfigError, StoreError, psycopg.Error, sqlite3.Error, NotImplementedError, TypeError)

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to memhub.yaml (default: ./memhub.yaml, $MEMHUB_CONFIG, ~/.config/memhub/memhub.yaml)")
POOL_LIFETIME_LIMIT = 3600  # an Entra token lives about an hour: a pool must recycle connections sooner


def _die(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


# --- init -------------------------------------------------------------------------------------------------------


def _folder_project() -> str:
    name = re.sub(r"[^a-z0-9_]", "_", Path.cwd().name.lower()).strip("_") or "memhub"
    return name if name[0].isalpha() else f"m_{name}"


def init_command(
    *, config: Optional[Path], project: Optional[str], db: Optional[str], chat: Optional[str],
    embeddings: Optional[str], preset: Optional[str], connect: Optional[str], force: bool, check_model: bool,
) -> None:
    from memhub import cli

    given = [name for name, v in (("--project", project), ("--db", db), ("--chat", chat), ("--embeddings", embeddings),
                                  ("--preset", preset), ("--connect", connect)) if v is not None]
    if config is not None:
        if given:
            _die(f"-c FILE creates the tables of that file; it cannot be combined with {', '.join(given)}")
        return _init_tables(cli, config, check_model)
    if not given and discover() is not None:
        return _init_tables(cli, None, check_model)
    _init_write(cli, given=given, project=project, db=db, chat=chat, embeddings=embeddings, preset=preset, connect=connect, force=force)


def _init_tables(cli: Any, config: Optional[Path], check_model: bool) -> None:
    """Today's `init`: create the extension, the tables and the indexes of the yaml in use."""
    resolved = cli._resolve(config)
    cli._require_db_and_embedder(resolved)
    settings, parts = resolved.config, cli._parts(resolved)
    legacy = settings.embeddings is not None  # an `embeddings:` block states the dims; `models:` / `connect:` are probed
    model, dims = (settings.embeddings.model, settings.embeddings.dims) if legacy else (None, None)
    if check_model or not legacy:  # a wrong `dims` (or key, or endpoint) shows here, not on the first insert
        try:
            embedder = parts.embedder
            got = len(embedder.embed_query("memhub"))
        except ConfigError as exc:
            cli._fail(exc)
        except Exception as exc:  # noqa: BLE001 - any provider error is a configuration problem here
            _die(f"embedding model {model or settings.models and settings.models.embeddings!r} is not reachable: {exc}")
        if legacy and got != dims:
            _die(f"the model returns {got} dimensions but embeddings.dims is {dims}")
        if not legacy:
            model, dims = embedding_model_id(embedder, settings), got
    try:
        open_backend(parts.db, settings.project_prefix, settings.database_schema).init(embedding_model=model, dims=dims)
    except _INIT_ERRORS as exc:
        cli._fail(exc)
    typer.echo(f"initialized {settings.project_prefix}_memory / {settings.project_prefix}_memory_runs")


def _init_write(cli: Any, *, given: list[str], project, db, chat, embeddings, preset, connect, force) -> None:
    target = Path.cwd() / CONFIG_FILE
    if target.exists() and not force:
        _die(f"{target} already exists: edit it with `memhub config set`, or pass --force to overwrite it")
    if connect is not None and db is not None:
        _die("--connect and --db are two ways to say where the database is: give one")
    if connect is not None and (chat is not None or embeddings is not None):
        _die("with --connect the models come from your function: do not give --chat or --embeddings")
    if preset is not None and preset not in preset_names():
        _die(f"unknown preset {preset!r}; available: {', '.join(preset_names())}")
    if project is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", project):
        _die(f"--project {project!r} must start with a letter and use only letters, digits and underscores (max 41)")
    if connect is not None and not re.fullmatch(r"[\w.]+:\w+", connect):
        _die(f"--connect must be 'package.module:function', not {connect!r}")

    if project is None:
        project = typer.prompt("Project (table prefix)", default=_folder_project())
    if connect is None and db is None:
        connect = typer.prompt("Connect function (module:function), empty to give a database URL instead", default="", show_default=False) or None
        if connect is None:
            db = typer.prompt("Database URL (use ${VAR} for the password)")
    if connect is None and embeddings is None:
        embeddings = typer.prompt("Embedding model (provider:model)")
    if connect is None and chat is None and not given:  # optional: only a person answering every question is asked
        chat = typer.prompt("Chat model (provider:model, empty to skip)", default="", show_default=False) or None
    if preset is None:
        preset = typer.prompt(f"Preset ({', '.join(preset_names())})", default="assistant")
    for name, value, check in (("project", project, r"[A-Za-z][A-Za-z0-9_]{0,40}"), ("preset", preset, "|".join(preset_names()))):
        if not re.fullmatch(check, value):
            _die(f"{name} {value!r} is not valid" + (f"; available: {', '.join(preset_names())}" if name == "preset" else ""))

    data: dict[str, Any] = {"project": project}
    if connect is not None:
        data["connect"] = connect
    else:
        data["db"] = db
        models = {k: v for k, v in (("chat", chat), ("embeddings", embeddings)) if v}
        if models:
            data["models"] = models
    data["preset"] = preset
    data["scopes"] = load_preset(preset).get("scopes", ["user"])

    try:  # validate, build the embedder and embed one text, create the tables - and only then write the file
        resolved = resolve(data)
        cli._require_db_and_embedder(resolved)
        parts = cli._parts(resolved)
        embedder = parts.embedder
        try:
            dims = len(embedder.embed_query("memhub"))
        except Exception as exc:  # noqa: BLE001 - any provider error is a configuration problem here
            _die(f"embedding model {embeddings or 'from connect'!r} is not reachable: {exc}")
        settings = resolved.config
        open_backend(parts.db, settings.project_prefix, settings.database_schema).init(
            embedding_model=embedding_model_id(embedder, settings), dims=dims)
    except _INIT_ERRORS as exc:
        cli._fail(exc)
    write_yaml(target, data)
    typer.echo(f"initialized {settings.project_prefix}_memory / {settings.project_prefix}_memory_runs")
    typer.echo(f"wrote {target}")


# --- config show / set ------------------------------------------------------------------------------------------

config_app = typer.Typer(help="Show where each setting comes from, or change one in memhub.yaml.", no_args_is_help=True)


def _text(value: Any) -> str:
    """One line per setting: a text with line breaks is shown escaped."""
    return value if isinstance(value, str) and "\n" not in value else json.dumps(value, default=str, ensure_ascii=False)


@config_app.command("show")
def config_show(all: bool = typer.Option(False, "--all", help="Also the settings left at their defaults"), config: Optional[Path] = ConfigOpt) -> None:
    """One line per setting: KEY, VALUE, SOURCE (default, preset, yaml, env, code)."""
    from memhub import cli

    try:
        resolved = resolve(config, strict=False)
    except ConfigError as exc:
        cli._fail(exc)
    if config is None and resolved.path is None:
        _die(f"no memhub.yaml found (looked in {SEARCH_PLACES}): run `memhub init`")
    for key, value, source in resolved.rows(all=all):
        typer.echo(f"{key}  {_text(value)}  {source}")


@config_app.command("set")
def config_set(key: str, value: str, config: Optional[Path] = ConfigOpt) -> None:
    """Set KEY (dotted, e.g. `ingestion.max_candidates`) to VALUE (yaml) in memhub.yaml, after validating it."""
    path = config if config is not None else discover()
    if path is None or not Path(path).is_file():
        _die(f"no memhub.yaml found (looked in {SEARCH_PLACES}): run `memhub init`")
    try:
        set_key(path, key, value)
    except ConfigError as exc:
        _die(str(exc))
    typer.echo(f"{key} = {value}")


# --- doctor -----------------------------------------------------------------------------------------------------


def doctor(config: Optional[Path] = ConfigOpt) -> None:
    """Check the config, the database, the models, the embedding dims and the ledger tables."""
    from memhub import cli

    lines: list[tuple[str, str, str]] = []

    def say(status: str, check: str, detail: str) -> None:
        lines.append((status, check, detail))
        typer.echo(f"{status}  {check}  {detail}")

    def done() -> None:
        raise typer.Exit(1 if any(s == "fail" for s, _, _ in lines) else 0)

    try:
        resolved: Resolved = resolve(config)
    except ConfigError as exc:
        say("fail", "config", str(exc))
        for check in ("db", "chat", "embedder", "dims", "schema"):
            say("skip", check, "no usable config")
        return done()
    if config is None and resolved.path is None:
        say("fail", "config", f"no memhub.yaml found (looked in {SEARCH_PLACES}): run `memhub init`")
        for check in ("db", "chat", "embedder", "dims", "schema"):
            say("skip", check, "no usable config")
        return done()
    settings, parts = resolved.config, cli._parts(resolved)
    say("ok", "config", str(resolved.path))

    store = None
    try:
        store = open_backend(parts.db, settings.project_prefix, settings.database_schema)
        store = store.resolve() if getattr(store, "deferred", False) else store
        if isinstance(store, MemoryStore):
            with store.connect(vector=False, schema=False) as conn:
                conn.execute("SELECT 1")
        elif store.name == "sqlite":
            with store.source.connection() as conn:
                conn.execute("SELECT 1")
        say("ok", "db", f"{store.name} answers SELECT 1")
        if store.name == "sqlite":
            hint = "" if store.vector_path == "sqlite-vec" else " (Python cosine: pip install memhub[sqlite] for search in SQL)"
            say("ok", "vector", f"{store.vector_path}{hint}")
    except ConfigError as exc:
        store = None
        say("fail", "db", str(exc))
    except Exception as exc:  # noqa: BLE001 - an unreachable database is the finding
        store = None
        say("fail", "db", f"{type(exc).__name__}: {exc}")

    try:
        llm = parts.llm
        if llm is None:
            say("skip", "chat", "none configured (ingest needs one: set `models.chat`, `llm` or `connect`)")
        else:
            llm.invoke("ping")
            say("ok", "chat", "answers")
    except Exception as exc:  # noqa: BLE001
        say("fail", "chat", f"{type(exc).__name__}: {exc}")

    dims = None
    try:
        embedder = parts.embedder
        dims = len(embedder.embed_query("memhub"))
        say("ok", "embedder", f"{embedding_model_id(embedder, settings)} answers with {dims} dimensions")
    except Exception as exc:  # noqa: BLE001
        say("fail", "embedder", f"{type(exc).__name__}: {exc}")

    saved, missing = None, None
    if store is None:
        say("skip", "dims", "needs the database")
        say("skip", "schema", "needs the database")
    else:
        try:
            missing, saved = _ledger_state(store, settings.project_prefix)
        except Exception as exc:  # noqa: BLE001
            missing = [f"{type(exc).__name__}: {exc}"]
        if dims is None:
            say("skip", "dims", "needs the embedder")
        elif saved is None:
            say("skip", "dims", "no ledger yet")
        elif saved.get("dims") != dims:
            say("fail", "dims", f"the model answers with {dims} dimensions but the ledger was built with {saved.get('dims')}: run `memhub reembed`")
        else:
            say("ok", "dims", f"{dims}, as the ledger")
        if missing or saved is None:
            say("fail", "schema", f"missing {', '.join(missing) if missing else 'the embedding meta'}: run `memhub init`")
        else:
            say("ok", "schema", f"{settings.project_prefix}_memory, {settings.project_prefix}_memory_runs, embedding {saved.get('model')}")

    db = parts._connected["db"] if parts._connected is not None else None  # a pool the app's `connect:` built
    if db is not None and _is_pool(db):
        life = getattr(db, "max_lifetime", None)
        if not life or life >= POOL_LIFETIME_LIMIT:
            say("warn", "pool", f"max_lifetime is {life}: set it below {POOL_LIFETIME_LIMIT} s so connections are renewed before an Entra token expires")
    done()


def _ledger_state(store: Any, prefix: str) -> tuple[list[str], Any]:
    """The ledger tables missing from the database, and the embedding meta when none is."""
    tables = (f"{prefix}_memory", f"{prefix}_memory_runs", f"{prefix}_memhub_meta")
    if isinstance(store, MemoryStore):
        with store.connect(vector=False) as conn, conn.cursor() as cur:
            missing = [t for t in tables if cur.execute("SELECT to_regclass(%s) AS t", (t,)).fetchone()["t"] is None]
            return missing, (store.get_meta(cur, "embedding") if not missing else None)
    if store.name == "sqlite":
        with store.source.connection() as conn:
            present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [t for t in tables if t not in present]
        if missing:
            return missing, None
    with store.unit_of_work() as uow:  # in memory, or SQLite with every table
        return [], uow.meta.get_meta("embedding")


# --- use --------------------------------------------------------------------------------------------------------


def use(workspace: str, user: Optional[str] = typer.Option(None, "--user", help="The user the CLI acts for")) -> None:
    """Remember a workspace (and user) for the next commands: `~/.config/memhub/context`."""
    write_context(workspace, user)
    typer.echo(f"workspace={workspace} user={user or ''}")


def register(app: typer.Typer) -> None:
    app.add_typer(config_app, name="config")
    app.command()(doctor)
    app.command()(use)
