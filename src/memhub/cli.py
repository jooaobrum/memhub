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

from memhub.config import Settings, build_chat_model, build_embeddings, load_config
from memhub.service import MemoryService, ServiceError
from memhub.store import Actor, MemoryStore, StoreError

load_dotenv(find_dotenv(usecwd=True))  # API keys live in a git-ignored .env

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _actor() -> Actor:
    roles = [r.strip() for r in os.environ.get("MEMHUB_CLI_ROLES", "workspace_admin").split(",") if r.strip()]
    return Actor(id=f"cli:{getpass.getuser()}", roles=roles)


def _settings(config: Path) -> Settings:
    if not config.exists():
        typer.secho(f"config file not found: {config}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    return load_config(config)


def _service(config: Path, *, check_embedding: bool = True) -> MemoryService:
    """Build the service. Every command except `init`/`reembed` first checks that the
    configured embedding model/dims still match what `init` saved."""
    settings = _settings(config)
    store = MemoryStore(settings.database_url, settings.project_prefix, settings.database_schema)
    if check_embedding:
        try:
            with store.connect() as conn, conn.cursor() as cur:
                store.check_embedding_config(cur, model=settings.embeddings.model, dims=settings.embeddings.dims)
        except StoreError as exc:
            _fail(exc)
    registry = settings.build_registry()
    embeddings = build_embeddings(settings.embeddings)
    return MemoryService(store=store, settings=settings, registry=registry, embeddings=embeddings)


def _print(obj: Any) -> None:
    typer.echo(json.dumps(obj, default=str, indent=2))


def _fail(exc: Exception) -> None:
    typer.secho(f"{type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


ConfigOpt = typer.Option(Path("memhub.yaml"), "--config", "-c", help="Path to memhub.yaml")


@app.command()
def init(
    config: Path = ConfigOpt,
    check_model: bool = typer.Option(True, "--check-model/--no-check-model", help="Embed one text to check the model answers with `embeddings.dims`"),
) -> None:
    """Create the pgvector extension, both ledger tables and their indexes."""
    settings = _settings(config)
    if check_model:  # a wrong `dims` (or key, or endpoint) shows here, not on the first insert
        try:
            got = len(build_embeddings(settings.embeddings).embed_query("memhub"))
        except Exception as exc:  # noqa: BLE001 - any provider error is a configuration problem here
            typer.secho(f"embedding model {settings.embeddings.model!r} is not reachable: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        if got != settings.embeddings.dims:
            typer.secho(f"the model returns {got} dimensions but embeddings.dims is {settings.embeddings.dims}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    store = MemoryStore(settings.database_url, settings.project_prefix, settings.database_schema)
    store.init(embedding_model=settings.embeddings.model, dims=settings.embeddings.dims)
    typer.echo(f"initialized {settings.project_prefix}_memory / {settings.project_prefix}_memory_runs")


@app.command()
def reembed(config: Path = ConfigOpt, batch_size: int = 100) -> None:
    """Recompute every active version's embedding after an embedding-model change."""
    service = _service(config, check_embedding=False)
    try:
        count = service.reembed(_actor(), batch_size=batch_size)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        typer.echo(f"reembedded {count} active memories")


def _build_source(settings: Settings, name: str, config: Path):
    if name not in settings.sources:
        typer.secho(f"unknown source {name!r}; configured: {list(settings.sources)}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    cfg = settings.sources[name]
    if cfg.kind == "jsonl":
        from memhub.sources.jsonl import JSONLSource

        return JSONLSource(cfg, workspace_id=settings.workspace_default, base_dir=config.resolve().parent)
    if cfg.kind == "mlflow":
        from memhub.sources.mlflow import MLflowSource

        return MLflowSource(cfg, workspace_id=settings.workspace_default)
    if cfg.kind == "sql":
        from memhub.sources.sql import SQLSource

        return SQLSource(cfg, workspace_id=settings.workspace_default)
    if ":" in cfg.kind:  # "package.module:Class": the project's own adapter (see sources/base.py)
        import importlib

        module, _, name = cfg.kind.partition(":")
        return getattr(importlib.import_module(module), name)(cfg, workspace_id=settings.workspace_default)
    typer.secho(f"unsupported source kind {cfg.kind!r}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def ingest(
    source: str = typer.Option(..., "--source", "-s"),
    reprocess: bool = False,
    dry_run: bool = False,
    thread: Optional[str] = typer.Option(None, "--thread"),
    config: Path = ConfigOpt,
) -> None:
    """Extract memories from a source's traces (idempotent; progress is saved per thread)."""
    from memhub.pipeline.ingest import ingest_source

    service = _service(config)
    settings = service.settings
    summary = ingest_source(
        store=service.store,
        settings=settings,
        registry=service.registry,
        source=_build_source(settings, source, config),
        source_name=source,
        extractor=build_chat_model(settings.llm.extractor),
        judge=build_chat_model(settings.llm.judge),
        embeddings=service.embeddings,
        reprocess=reprocess,
        dry_run=dry_run,
        thread_id=thread,
    )
    _print(asdict(summary))


@app.command()
def add(
    type: str = typer.Option(..., "--type", "-t"),
    scope: str = typer.Option(..., "--scope", "-s"),
    file: Path = typer.Option(..., "--file", "-f", help="JSON file with the type's fields"),
    workspace: Optional[str] = typer.Option(None, "--ws"),
    user: Optional[str] = typer.Option(None, "--user"),
    reference: Optional[str] = typer.Option(None, "--reference"),
    config: Path = ConfigOpt,
) -> None:
    """Add a memory by hand from a JSON file validated against the type's schema. An optional
    `observed_at` (ISO timestamp) in the file dates the memory; it defaults to now."""
    service = _service(config)
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
    config: Path = ConfigOpt,
) -> None:
    """List every memory, each with a `stale` flag (valid_until passed)."""
    service = _service(config)
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
    config: Path = ConfigOpt,
) -> None:
    service = _service(config)
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
        _print(rows)


@app.command()
def queue(workspace: Optional[str] = typer.Option(None, "--ws"), config: Path = ConfigOpt) -> None:
    """Workspace candidates awaiting review, conflicts first."""
    service = _service(config)
    _print(service.queue(_actor(), workspace_id=workspace))


@app.command()
def approve(
    id: str,
    resolve: Optional[str] = typer.Option(None, help="keep_old | replace | keep_both, for a conflicting candidate"),
    note: Optional[str] = None,
    config: Path = ConfigOpt,
) -> None:
    service = _service(config)
    try:
        row = service.approve(_actor(), uuid.UUID(id), resolve=resolve, note=note)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(row)


@app.command()
def reject(id: str, note: Optional[str] = None, config: Path = ConfigOpt) -> None:
    service = _service(config)
    try:
        row = service.reject(_actor(), uuid.UUID(id), note=note)
    except (ServiceError, StoreError) as exc:
        _fail(exc)
    else:
        _print(row)


@app.command()
def edit(id: str, file: Path = typer.Option(..., "--file", "-f"), config: Path = ConfigOpt) -> None:
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
def history(id: str, config: Path = ConfigOpt) -> None:
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
def archive(id: str, config: Path = ConfigOpt) -> None:
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
    config: Path = ConfigOpt,
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
def promote(id: str, config: Path = ConfigOpt) -> None:
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
    config: Path = ConfigOpt,
) -> None:
    """List the areas (key, title, `proposed` flag, count of active memories) of a user, or of the workspace."""
    if ctx.invoked_subcommand is not None:
        return
    service = _service(config)
    _print(service.areas(_actor(), user_id=user, workspace_id=workspace))


@areas_app.command("merge")
def areas_merge(source: str, target: str, config: Path = ConfigOpt) -> None:
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
    config: Path = ConfigOpt,
) -> None:
    """Print an area page: title, summary (derived text, marked auto-summary), dated details, last updated."""
    service = _service(config)
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
def runs(last: int = 10, config: Path = ConfigOpt) -> None:
    service = _service(config)
    _print(service.runs(_actor(), last=last))


if __name__ == "__main__":
    app()
