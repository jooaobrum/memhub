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
    CONFIG_FILE, SEARCH_PLACES, Parts, Resolved, discover, embedding_model_id, resolve,
    set_key, write_context, write_yaml,
)
from memhub.storage import open_backend
from memhub.storage.migrations import check as check_schema
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
    embeddings: Optional[str], memory: Optional[str], connect: Optional[str], force: bool, check_model: bool,
) -> None:
    from memhub import cli

    given = [name for name, v in (("--project", project), ("--db", db), ("--chat", chat), ("--embeddings", embeddings),
                                  ("--memory", memory), ("--connect", connect)) if v is not None]
    if config is not None:
        if given:
            _die(f"-c FILE creates the tables of that file; it cannot be combined with {', '.join(given)}")
        return _init_tables(cli, config, check_model)
    if not given and discover() is not None:
        return _init_tables(cli, None, check_model)
    _init_write(cli, given=given, project=project, db=db, chat=chat, embeddings=embeddings, memory=memory, connect=connect, force=force)


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


def _literal_password(url: str) -> bool:
    """Whether a database URL carries a password in clear (a `${VAR}` reference is not one)."""
    from urllib.parse import urlsplit

    try:
        password = urlsplit(url).password
    except ValueError:
        return False
    return bool(password) and not re.fullmatch(r"\$\{\w+\}", password)


MEMORY_FILE = "memory.md"
KEEP_QUESTION = "In a sentence, what should this memory keep?"
DEFAULT_BODY = "Durable facts about the user and their work, what they prefer, and who they are."

# The general rules a starter memory keeps under its purpose line (AD-034): tuned on real conversations, and the
# project's to edit like the rest of the file.
STARTER_RULES = """\
## Kinds

- `profile`: who the user is: identity, role, where they live or work, what they study, their team, lasting
  constraints and goals. One free sentence per statement, only what the user said.
- `fact`: other lasting knowledge about the user's situation or environment: their projects, tools, conventions,
  household, commitments.
- `preference`: how the user wants the assistant to answer or to work for them.

Ask of every candidate: will this help me serve this user better in a month? If not, emit nothing.

## Voice

Write `content` in the third person, in the language the user wrote in, never in the first person and without
"The user": "Prefers pytest over unittest", "Lives in Lisbon", not "I prefer pytest". Do not guess the user's gender.
A statement about someone else (a child, partner, colleague, pet) names that person: "Daughter Alice is lactose
intolerant", never "Is lactose intolerant". Jokes and sarcasm are never memories.

## Keep

What the user stated in a declarative sentence and that is still true and useful in a month: role and employer, team,
projects and the stack they use, coding conventions they want followed, where they live, studies, family, pets, health
constraints and diet, habits, budget, language level, decisions already taken, and dated commitments (a trip, a start
date, a deadline) written with the absolute date and `valid_until` set to that date. One memory per distinct fact (up
to 5 per segment), even when the message also asks a question.

## Never

- Questions, how-to requests, searches or recommendation requests ("how do I configure X?", "looking for a good gym"),
  and "The user asked / wants to know / is looking for ...".
- Something deduced from the topic of a question: asking about Paris rents does not mean the user lives in Paris.
- Facts that only appear in the assistant's answers.
- Vague intentions ("I'm thinking of", "maybe I'll"): keep only a durable fact behind them, if any.
- An acute one-off errand: a lost card, a single bug being fixed right now.
- Something too thin to be useful ("Works", "Has a project"): keep it only with the concrete detail.
- Secrets: passwords, keys, tokens.

If in doubt, emit nothing: an empty list is a good answer.

## Time

Every message shows its date, e.g. [#3 2026-07-21]. Never write relative time ("next year", "recently", "soon",
"now") in `content`: rewrite it with the date of the message that says it ("next year" said on 2026-07-21 -> "in
2027"), or drop it. A fact whose text gives an end gets `valid_until` = the last day of that period.

## Preferences

Store one when the user asks for a way of working or answering ("keep answers short", "always use type hints", "no
emoji", "call me Sam"), or praises a format ("perfect, keep it this short"). A request that only concerns the current
answer ("this time give me details") is not a preference. Set `key` to a short snake_case name (`format`, `language`,
`logging`); one row per key, the latest wins. Write `content` as a complete sentence saying what is wanted. A request
covering two things gives two memories.

## Examples

These only illustrate the rules: never emit a memory from them.

- "I'm the tech lead on the payments API, we deploy on Fly.io. How do I set secrets there?" -> profile "Tech lead on
  the payments API." and fact "The payments API is deployed on Fly.io." The question is dropped.
- "Moved from Lyon to Annecy last month." (dated 2026-05-02) -> profile "Lives in Annecy since around April 2026."
- "Please never use print for logging, we use structlog." -> preference (key `logging`) "Wants structlog for logging,
  never print."
- "What's a good restaurant around here?" -> nothing. "I'm thinking about learning Rust." -> nothing.

Quote verbatim from a user message. Never use `assertion: inferred`. Utility (1-5): 5 = a constraint the assistant
must respect in almost every answer (an allergy, a required convention, a format they asked for, where they live);
4 = another lasting circumstance (job, project, stack, family, dated commitment); 3 = mildly useful (a hobby, a
taste); 1-2 = trivia.
"""


def starter_memory(body: str | None = None) -> str:
    """The `memory.md` that `memhub init` writes (AD-027, AD-034): the starter types, then what to remember: the
    project's purpose (the interactive answer, or a default) and the general rules."""
    return (
        "---\n"
        "# Each type is `builtin`, a template such as \"Saw {species} at {location}[ on {seen_on?}]\", or a mapping.\n"
        "# Add `areas:` and `strict: true` to keep only your own domain. See docs/memory.md.\n"
        "types:\n"
        "  fact: builtin\n"
        "  preference: builtin\n"
        "  profile: builtin\n"
        "---\n"
        "# What to remember\n\n"
        f"{(body or '').strip() or DEFAULT_BODY}\n\n"
        f"{STARTER_RULES}"
    )


def _init_write(cli: Any, *, given: list[str], project, db, chat, embeddings, memory, connect, force) -> None:
    target = Path.cwd() / CONFIG_FILE
    if target.exists() and not force:
        _die(f"{target} already exists: edit it with `memhub config set`, or pass --force to overwrite it")
    if connect is not None and db is not None:
        _die("--connect and --db are two ways to say where the database is: give one")
    if connect is not None and (chat is not None or embeddings is not None):
        _die("with --connect the models come from your function: do not give --chat or --embeddings")
    memory_path = (Path.cwd() / Path(memory).expanduser()) if memory is not None else None
    if memory_path is not None and not memory_path.is_file():
        _die(f"memory file not found: {memory}")
    if project is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", project):
        _die(f"--project {project!r} must start with a letter and use only letters, digits and underscores (max 41)")
    if connect is not None and not re.fullmatch(r"[\w.]+:\w+", connect):
        _die(f"--connect must be 'package.module:function', not {connect!r}")

    # Flags mean a script, a CI job or a coding agent: what was not given takes its default, nothing is asked.
    # Questions are only for a person running a bare `memhub init` in a terminal.
    if given:
        missing = [] if connect is not None else [f for f, v in (("--db", db), ("--embeddings", embeddings)) if v is None]
        if missing:
            _die(f"memhub init needs {' and '.join(missing)} (or --connect), e.g. "
                 "memhub init --db sqlite:///memhub.db --embeddings openai:text-embedding-3-small --chat openai:gpt-4.1-mini")
        project = project or _folder_project()
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
    body = None
    if memory is None and not given:  # a person at a terminal says, in their words, what the memory is for (AD-028)
        body = typer.prompt(KEEP_QUESTION, default="", show_default=False)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", project):
        _die(f"project {project!r} is not valid")

    data: dict[str, Any] = {"project": project}
    if connect is not None:
        data["connect"] = connect
    else:
        data["db"] = db
        models = {k: v for k, v in (("chat", chat), ("embeddings", embeddings)) if v}
        if models:
            data["models"] = models
    data["memory"] = memory if memory is not None else f"./{MEMORY_FILE}"
    if memory is None:
        data["scopes"] = ["user"]  # the starter is one person's memory (AD-035); a project sharing memory says so

    try:  # validate, build the embedder and embed one text, create the tables - and only then write the files
        # the yaml's `memory:` is relative to the yaml, which sits in this folder; the starter file needs no reading
        resolved = resolve({**data, "memory": str(memory_path)} if memory_path is not None else {k: v for k, v in data.items() if k != "memory"})
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
    memory_target = Path.cwd() / MEMORY_FILE
    kept = memory is None and memory_target.exists() and not force
    if memory is None and not kept:
        memory_target.write_text(starter_memory(body))
    if db is not None and _literal_password(db):  # written as given (AD-007), but never silently
        typer.secho(
            "warning: the --db URL holds a literal password, written to memhub.yaml as given. "
            "Put ${MEMHUB_DB_PASSWORD} in its place and export MEMHUB_DB_PASSWORD to keep it out of the file.",
            fg=typer.colors.YELLOW, err=True,
        )
    typer.echo(f"initialized {settings.project_prefix}_memory / {settings.project_prefix}_memory_runs")
    typer.echo(f"wrote {target}")
    if kept:
        typer.echo(f"kept {MEMORY_FILE}", err=True)
    elif memory is None:
        typer.echo(f"wrote {memory_target}")


# --- config show / set ------------------------------------------------------------------------------------------

config_app = typer.Typer(help="Show where each setting comes from, or change one in memhub.yaml.", no_args_is_help=True)


def _text(value: Any) -> str:
    """One line per setting: a text with line breaks is shown escaped."""
    return value if isinstance(value, str) and "\n" not in value else json.dumps(value, default=str, ensure_ascii=False)


@config_app.command("show")
def config_show(all: bool = typer.Option(False, "--all", help="Also the settings left at their defaults"), config: Optional[Path] = ConfigOpt) -> None:
    """One line per setting: KEY, VALUE, SOURCE (default, yaml, the memory file, env, code)."""
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
        for check in ("db", "chat", "embedder", "dims", "schema", "version"):
            say("skip", check, "no usable config")
        return done()
    if config is None and resolved.path is None:
        say("fail", "config", f"no memhub.yaml found (looked in {SEARCH_PLACES}): run `memhub init`")
        for check in ("db", "chat", "embedder", "dims", "schema", "version"):
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
        say("skip", "version", "needs the database")
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
            say("skip", "version", "no ledger yet")
        else:
            say("ok", "schema", f"{settings.project_prefix}_memory, {settings.project_prefix}_memory_runs, embedding {saved.get('model')}")
            try:
                version = store.schema_version()
                check_schema(version)
                say("ok", "version", f"schema version {version}")
            except Exception as exc:  # noqa: BLE001 - an old ledger is the finding
                say("fail", "version", str(exc))

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
