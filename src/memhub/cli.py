"""The `memhub` CLI. Acts as `actor=cli:<os user>` with roles from
`MEMHUB_CLI_ROLES` (comma-separated, default `workspace_admin`)."""
from __future__ import annotations

import getpass
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import find_dotenv, load_dotenv

from memhub import cli_setup
from memhub.config import Config, ConfigError, EmbeddingConfig, SourceConfig, build_chat_model, build_embeddings
from memhub.service import MemoryService, ServiceError
from memhub.settings import SEARCH_PLACES, Parts, Resolved, embedding_model_id, read_context, resolve
from memhub.sources import build_source
from memhub.storage import open_backend
from memhub.storage.migrations import SCHEMA_VERSION, check as check_schema
from memhub.storage.rules import Actor, StoreError

load_dotenv(find_dotenv(usecwd=True))  # API keys live in a git-ignored .env

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _actor() -> Actor:
    roles = [r.strip() for r in os.environ.get("MEMHUB_CLI_ROLES", "workspace_admin").split(",") if r.strip()]
    return Actor(id=f"cli:{getpass.getuser()}", roles=roles)


def _resolve(config: Optional[Path]) -> Resolved:
    """The config the command runs on: `-c FILE`, else the yaml found in `.`, `$MEMHUB_CONFIG`, `~/.config/memhub`."""
    try:
        resolved = resolve(config)
    except ConfigError as exc:
        _fail(exc)
    if config is None and resolved.path is None:
        typer.secho(f"no memhub.yaml found (looked in {SEARCH_PLACES}): run `memhub init`", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return resolved


def _parts(resolved: Resolved) -> Parts:
    # the builders are looked up when called, so a test (or a project) can replace `build_embeddings` / `build_chat_model` here
    return Parts(resolved, build_chat=lambda cfg: build_chat_model(cfg), build_embed=lambda cfg: build_embeddings(cfg))


def _require_db_and_embedder(resolved: Resolved) -> None:
    """The CLI builds its own connection and models, so it needs what `Memory(db=, llm=, embedder=)` does not."""
    settings, where = resolved.config, resolved.path or "the configuration"
    if not (settings.connect or settings.database_url):
        _fail_missing(where, "database_url", "set `db`, `database_url` or `connect`")
    if not (settings.connect or settings.embeddings or (settings.models and settings.models.embeddings)):
        _fail_missing(where, "embeddings", "set `models.embeddings`, `embeddings` or `connect`")


def _service(config: Optional[Path], *, check_embedding: bool = True) -> MemoryService:
    """Build the service. Every command except `init`/`reembed` first checks that the
    embedding model is the one the ledger was built with."""
    resolved = _resolve(config)
    settings = resolved.config
    parts = _parts(resolved)
    _require_db_and_embedder(resolved)
    try:
        embeddings = parts.embedder
        store = open_backend(parts.db, settings.project_prefix, settings.database_schema)  # Postgres, SQLite or memory
        if settings.embeddings is None:  # `models:` / `connect:`: the dims are what the model answers
            settings.embeddings = EmbeddingConfig(model=embedding_model_id(embeddings, settings), dims=len(embeddings.embed_query("memhub")))
    except ConfigError as exc:
        _fail(exc)
    try:  # an older (or newer) ledger is refused before any command reads or writes it
        version = store.schema_version()
        if version is not None:
            check_schema(version)
    except StoreError as exc:
        _fail(exc)
    if check_embedding:
        try:
            with store.unit_of_work() as uow:
                uow.meta.check_embedding_config(model=settings.embeddings.model, dims=settings.embeddings.dims)
        except StoreError as exc:
            _fail(exc)
    registry = settings.build_registry()
    service = MemoryService(store=store, settings=settings, registry=registry, embeddings=embeddings)
    service.parts = parts  # the models ingest needs, built only if it runs
    service.resolved = resolved
    return service

def _fail_missing(where: Any, key: str, hint: str) -> None:
    typer.secho(f"{where} is missing `{key}`: the CLI needs it to reach the ledger ({hint})", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)

def _ids(workspace: Optional[str], user: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Workspace and user of a command: the flag, else `MEMHUB_WORKSPACE` / `MEMHUB_USER`, else `memhub use`."""
    try:
        context = read_context()
    except ConfigError as exc:
        _fail(exc)
    ws = workspace or os.environ.get("MEMHUB_WORKSPACE")
    who = user or os.environ.get("MEMHUB_USER")
    from_use = [f"{n}={v}" for n, mine, v in (("workspace", ws, context["workspace"]), ("user", who, context["user"]))
                if mine is None and v]
    if from_use:  # one file for every project on this machine: say when it narrows a command, or an empty result puzzles
        typer.secho(f"using {' '.join(from_use)} from `memhub use` (pass --ws/--user to override)", fg=typer.colors.BRIGHT_BLACK, err=True)
    return ws or context["workspace"], who or context["user"]

def _readable(obj: Any) -> Any:
    """A row as a person reads it: the embedding vector (hundreds of floats) is left out."""
    if isinstance(obj, dict):
        return {k: _readable(v) for k, v in obj.items() if k != "embedding"}
    if isinstance(obj, list):
        return [_readable(v) for v in obj]
    return obj


def _print(obj: Any) -> None:
    typer.echo(json.dumps(_readable(obj), default=str, indent=2))


def _fail(exc: Exception) -> None:
    typer.secho(f"{type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


ConfigOpt = typer.Option(None, "--config", "-c", help="Path to memhub.yaml (default: ./memhub.yaml, $MEMHUB_CONFIG, ~/.config/memhub/memhub.yaml)")


@app.command()
def init(
    config: Optional[Path] = ConfigOpt,
    project: Optional[str] = typer.Option(None, "--project", help="Table prefix (a project per prefix can share one database)"),
    db: Optional[str] = typer.Option(None, "--db", help="postgresql://... (a `${VAR}` for the password keeps it out of the file), or sqlite:///memhub.db"),
    chat: Optional[str] = typer.Option(None, "--chat", help="Chat model as provider:model (needed by `ingest`)"),
    embeddings: Optional[str] = typer.Option(None, "--embeddings", help="Embedding model as provider:model"),
    memory: Optional[str] = typer.Option(None, "--memory", help="Your memory file (types, areas, what to keep); default: write a starter memory.md"),
    connect: Optional[str] = typer.Option(None, "--connect", help="module:function returning {db, embedder, llm?, judge?}: your app's own objects"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing memhub.yaml and memory.md"),
    check_model: bool = typer.Option(True, "--check-model/--no-check-model", help="Embed one text to check the model answers with `embeddings.dims`"),
) -> None:
    """Write memhub.yaml and memory.md (from flags, or asking), check the embedding model and create the tables. With
    `-c FILE`, or with no flags and a yaml found, only create the tables from that file."""
    cli_setup.init_command(
        config=config, project=project, db=db, chat=chat, embeddings=embeddings, memory=memory, connect=connect,
        force=force, check_model=check_model,
    )

@app.command()
def migrate(
    config: Optional[Path] = ConfigOpt,
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the pending migrations and change nothing"),
) -> None:
    """Bring the ledger to this memhub's schema version, in one transaction: an older one is refused by every other
    command until this has run."""
    resolved = _resolve(config)
    settings = resolved.config
    if not (settings.connect or settings.database_url):
        _fail_missing(resolved.path or "the configuration", "database_url", "set `db`, `database_url` or `connect`")
    try:
        store = open_backend(_parts(resolved).db, settings.project_prefix, settings.database_schema)
        before = store.schema_version()
        names = store.migrate(dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 - no ledger, a newer one, or a failed statement: nothing was applied
        _fail(exc)
    if not names:
        typer.echo(f"already at schema version {SCHEMA_VERSION}")
        return
    for name in names:
        typer.echo(f"{'pending' if dry_run else 'applied'} {name}")
    if not dry_run:
        typer.echo(f"schema version {before} -> {SCHEMA_VERSION}")


@app.command()
def reembed(config: Optional[Path] = ConfigOpt, batch_size: int = 100) -> None:
    """Recompute every active version's embedding after an embedding-model change."""
    service = _service(config, check_embedding=False)
    try:
        count = service.reembed(_actor(), batch_size=batch_size)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        typer.echo(f"reembedded {count} active memories")


def _build_source(settings: Config, name: str, config: Optional[Path]):
    """`config` is the yaml that was read: a source's relative paths are relative to its folder."""
    cfg = settings.sources.get(name)
    if cfg is None and name == "claude_code":  # Claude Code's transcripts need no yaml entry
        cfg = SourceConfig(kind="claude_code")
    if cfg is None:
        typer.secho(f"unknown source {name!r}; configured: {list(settings.sources)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    try:
        return build_source(cfg, workspace_id=settings.workspace_default,
                            base_dir=config.resolve().parent if config else Path.cwd())
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

@app.command()
def ingest(
    source: str = typer.Option(..., "--source", "-s"),
    reprocess: bool = False,
    dry_run: bool = False,
    thread: Optional[str] = typer.Option(None, "--thread"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Extract memories from a source's traces (idempotent; progress is saved per thread)."""
    from memhub.pipeline.ingest import ingest_source

    service = _service(config)
    settings = service.settings
    try:
        extractor, judge = service.parts.llm, service.parts.judge
    except ConfigError as exc:
        _fail(exc)
    if extractor is None:
        typer.secho(f"{service.resolved.path or 'the configuration'} is missing `llm`: ingest needs an extractor and a judge (set `models.chat`, `llm` or `connect`)", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    summary = ingest_source(
        store=service.store,
        settings=settings,
        registry=service.registry,
        source=_build_source(settings, source, service.resolved.path),
        source_name=source,
        extractor=extractor,
        judge=judge,
        embeddings=service.embeddings,
        reprocess=reprocess,
        dry_run=dry_run,
        thread_id=thread,
    )
    _print(asdict(summary))
    if summary.threads_waiting:
        idle = settings.ingestion.segment_idle
        typer.secho(
            f"{summary.threads_waiting} thread(s) had a message less than {idle} ago and were left for a later run "
            f"(a conversation is read once it has been idle for `ingestion.segment_idle`; "
            f"`memhub config set ingestion.segment_idle 0s` reads it now)", fg=typer.colors.YELLOW, err=True)
    if summary.failed_segments:
        typer.secho(f"{summary.failed_segments} segment(s) failed and will be retried by the next run: "
                    + "; ".join(summary.errors), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command()
def add(
    type: str = typer.Option(..., "--type", "-t"),
    scope: str = typer.Option(..., "--scope", "-s"),
    file: Path = typer.Option(..., "--file", "-f", help="JSON file with the type's fields"),
    workspace: Optional[str] = typer.Option(None, "--ws"),
    user: Optional[str] = typer.Option(None, "--user"),
    reference: Optional[str] = typer.Option(None, "--reference"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Add a memory by hand from a JSON file validated against the type's schema. An optional
    `observed_at` (ISO timestamp) in the file dates the memory; it defaults to now."""
    service = _service(config)
    workspace, user = _ids(workspace, user)
    fields = json.loads(file.read_text())
    observed_at = datetime.fromisoformat(fields.pop("observed_at")) if "observed_at" in fields else None
    try:
        row = service.add(
            _actor(), type=type, scope=scope, fields=fields, workspace_id=workspace, user_id=user,
            reference=reference, observed_at=observed_at,
        )
    except ServiceError as exc:
        _fail(exc)
    else:
        _print(row)


@app.command(name="list")
def list_(
    status: Optional[str] = None,
    type: Optional[str] = None,
    user: Optional[str] = typer.Option(None, "--user"),
    workspace: Optional[str] = typer.Option(None, "--ws"),
    stale: bool = typer.Option(False, "--stale", help="Only memories whose valid_until has passed"),
    area: Optional[str] = typer.Option(None, "--area", help="Only memories linked to this area (key or title)"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """List every memory, each with a `stale` flag (valid_until passed)."""
    service = _service(config)
    workspace, user = _ids(workspace, user)
    rows = service.list(_actor(), status=status, type=type, user_id=user, workspace_id=workspace, stale=stale, area=area)
    _print([{**r, "key": r["payload"].get("key")} for r in rows])  # `key` names the slot of a keyed type


@app.command()
def search(
    query: str,
    user: Optional[str] = typer.Option(None, "--user"),
    workspace: Optional[str] = typer.Option(None, "--ws"),
    type: Optional[str] = None,
    k: int = 10,
    include_stale: bool = typer.Option(False, "--include-stale", help="Also return expired memories"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    service = _service(config)
    workspace, user = _ids(workspace, user)
    try:
        expansion = service.expansion(_actor(), query, workspace_id=workspace, include_stale=include_stale)
        rows = service.search(
            _actor(), query, workspace_id=workspace, user_id=user, k=k, type=type, include_stale=include_stale
        )
    except ServiceError as exc:
        _fail(exc)
    else:
        if expansion.terms:  # stderr, so the JSON on stdout stays parseable
            typer.echo(f"query expanded by {', '.join(expansion.terms)}: {expansion.text}", err=True)
        if not rows and (workspace is None or user is None):  # the commonest "why is it empty?": who was searched
            typer.secho(
                f"nothing found for workspace={workspace or getattr(getattr(service, 'settings', None), 'workspace_default', None)!r} "
                f"user={user!r}: memories belong to a workspace and a user; pass --ws/--user, or set them once with "
                "`memhub use <workspace> --user <user>` (`memhub list` shows the workspace_id and user_id of every memory)",
                fg=typer.colors.YELLOW, err=True)
        _print(rows)


@app.command()
def queue(
    workspace: Optional[str] = typer.Option(None, "--ws"),
    user: Optional[str] = typer.Option(None, "--user", help="Only this user's own (user-scope) candidates"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Candidates awaiting review (workspace and user scope), conflicts first."""
    service = _service(config)
    workspace, _ = _ids(workspace, None)
    _print(service.queue(_actor(), workspace_id=workspace, user_id=user))


@app.command()
def approve(
    id: str,
    resolve: Optional[str] = typer.Option(None, help="keep_old | replace | keep_both, for a conflicting candidate"),
    note: Optional[str] = None,
    config: Optional[Path] = ConfigOpt,
) -> None:
    service = _service(config)
    try:
        row = service.approve(_actor(), uuid.UUID(id), resolve=resolve, note=note)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(row)


@app.command()
def reject(id: str, note: Optional[str] = None, config: Optional[Path] = ConfigOpt) -> None:
    service = _service(config)
    try:
        row = service.reject(_actor(), uuid.UUID(id), note=note)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(row)


@app.command()
def edit(id: str, file: Path = typer.Option(..., "--file", "-f"), config: Optional[Path] = ConfigOpt) -> None:
    """Insert version N+1 of memory `id` with the fields from a JSON file (only the fields given are changed)."""
    service = _service(config)
    fields = json.loads(file.read_text())
    try:
        row = service.edit(_actor(), uuid.UUID(id), fields)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(row)


@app.command()
def history(id: str, config: Optional[Path] = ConfigOpt) -> None:
    """Every version of memory `id`, oldest first: value, observed_at, evidence and who created it."""
    service = _service(config)
    try:
        rows = service.history(_actor(), uuid.UUID(id))
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print([
            {
                "version": r["version"], "status": r["status"], "value": r["content"], "key": r["payload"].get("key"),
                "observed_at": r["observed_at"], "evidence": r["evidence"], "created_by": r["created_by"],
                "created_at": r["created_at"], "verified": r["verified"], "id": r["id"],
            }
            for r in rows
        ])


@app.command()
def archive(id: str, config: Optional[Path] = ConfigOpt) -> None:
    service = _service(config)
    try:
        row = service.archive(_actor(), uuid.UUID(id))
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(row)


@app.command()
def delete(
    id: Optional[str] = typer.Argument(None),
    user: Optional[str] = typer.Option(None, "--user", help="Permanently erase all of this user's data"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    service = _service(config)
    try:
        if user is not None:
            result = service.delete_user(_actor(), user)
            _print(result)
        elif id is not None:
            service.delete(_actor(), uuid.UUID(id))
            typer.echo(f"deleted {id}")
        else:
            typer.secho("pass either an id or --user <id>", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    except (ServiceError, StoreError) as exc:
        _fail(exc)


@app.command()
def promote(id: str, config: Optional[Path] = ConfigOpt) -> None:
    """Copy a user-scope memory into the workspace review queue as a new candidate."""
    service = _service(config)
    try:
        row = service.promote(_actor(), uuid.UUID(id))
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(row)


areas_app = typer.Typer(help="An owner's areas with counts; `areas merge <from> <to>` folds one into another.")
app.add_typer(areas_app, name="areas")


@areas_app.callback(invoke_without_command=True)
def areas(
    ctx: typer.Context,
    user: Optional[str] = typer.Option(None, "--user"),
    workspace: Optional[str] = typer.Option(None, "--ws"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """List the areas (key, title, `proposed` flag, count of active memories) of a user, or of the workspace."""
    if ctx.invoked_subcommand is not None:
        return
    service = _service(config)
    workspace, user = _ids(workspace, user)
    _print(service.areas(_actor(), user_id=user, workspace_id=workspace))


@areas_app.command("merge")
def areas_merge(source: str, target: str, config: Optional[Path] = ConfigOpt) -> None:
    """Move every memory of area `source` (a memory_id) to `target`, as new versions, and archive `source`."""
    service = _service(config)
    try:
        result = service.merge_areas(_actor(), uuid.UUID(source), uuid.UUID(target))
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(result)


@app.command()
def page(
    user: Optional[str] = typer.Option(None, "--user"),
    area: Optional[str] = typer.Option(None, "--area", help="Area key or title; omit for every page of the user"),
    workspace: Optional[str] = typer.Option(None, "--ws"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Print an area page: title, summary (derived text, marked auto-summary), dated details, last updated."""
    service = _service(config)
    workspace, user = _ids(workspace, user)
    try:
        pages = [service.page(user, area, workspace_id=workspace)] if area else service.pages(user, workspace_id=workspace)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        typer.echo("\n\n".join(_render_page(pg) for pg in pages))


def _render_page(pg: dict) -> str:
    lines = [f"# {pg['title']}", f"Summary (auto-summary): {pg['summary'] or '(none yet)'}"]
    lines += [f"- {d['observed_at'].date().isoformat()}  {d['content']}" for d in pg["details"]]
    lines.append(f"Last updated: {pg['last_updated'].isoformat() if pg['last_updated'] else 'never'}")
    return "\n".join(lines)


@app.command()
def runs(last: int = 10, config: Optional[Path] = ConfigOpt) -> None:
    service = _service(config)
    _print(service.runs(_actor(), last=last))


@app.command()
def mcp(
    workspace: Optional[str] = typer.Option(None, "--ws", help="Workspace (default: MEMHUB_WORKSPACE, else this folder as Claude Code names it; `memhub use` is not read)"),
    user: Optional[str] = typer.Option(None, "--user", help="User (default: MEMHUB_USER, else the OS user)"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Serve search_memory and propose_memory over MCP (stdio). In Claude Code: `claude mcp add memhub -- memhub mcp`."""
    from memhub import mcp_server
    from memhub.memory import Memory

    try:
        mcp_server.require_sdk()
    except ImportError as exc:
        _fail(exc)
    resolved = _resolve(config)
    _require_db_and_embedder(resolved)
    # not `memhub use`: that is the CLI's own context, and one shared file would move every Claude Code session in
    # every project to the workspace last picked at the terminal
    workspace_id, user_id = mcp_server.identity(workspace or os.environ.get("MEMHUB_WORKSPACE"),
                                                user or os.environ.get("MEMHUB_USER"))
    try:  # an unusable ledger (no tables, an old schema, another embedder) stops here, before serving
        parts = _parts(resolved)  # the CLI's own model builders, as every other command uses
        memory = Memory(db=parts.db, embedder=parts.embedder, config=resolved)
        server = mcp_server.build_server(memory, workspace_id=workspace_id, user_id=user_id, run_id=mcp_server.new_run_id())
    except Exception as exc:  # noqa: BLE001 - the reason goes to stderr: stdout is the MCP channel
        _fail(exc)
    server.run("stdio")


cli_setup.register(app)

if __name__ == "__main__":
    app()
