"""The file view of memory (#41): an area read as a rendered page, and a written page turned into changes.

The page is a view rebuilt from the database, never the source of truth (AD-063). An agent reads it, and changes
memory by writing the page back: `write_page` pairs the written lines with the current page by their `#<id>` and
turns each difference into one change - a new memory, a new version, an archive, or a flag - under the project's
review config (AD-065). Nothing here stores the page itself.

    Folder         whose page: a user's own (`scope="user"`) or the workspace's
    build_page     the area (or one subject of it, or the folder's README) as a `Page`
    write_page     a written page -> `list[PageChange]`, one per changed line
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from memhub import injection
from memhub.config import Settings
from memhub.pipeline.route import AUTO, evidence_entries
from memhub.service import MemoryService, NotFound, ValidationError
from memhub.storage import UnitOfWork
from memhub.storage.rules import Row, area_link, as_vector_list

__all__ = ["Folder", "Page", "PageChange", "HOW_TO_READ", "STALE", "build_page", "write_page"]

STALE = "page changed since you read it: read it again"
ARCHIVE = "_action"  # AD-062: a candidate whose payload carries `"_action": "archive"` proposes archiving the memory
SOURCE = "deepagents"

HOW_TO_READ = """## How to read this
- Each line is one memory: `#<id> [provenance] text (date)`. Quoted text is the user's own words; copy it, never paraphrase.
- Provenance: [said] quoted from the user · [reviewed] approved by a person · [inferred] a guess, check before acting · [added] written by the app, with no quote.
- Lines under "Pending review" are not usable yet.
- This file is read-only. To change a line, edit only that line and keep its `#<id>`; the edit becomes a proposal. To add a fact, append a line with no id. A line needs the user's own words in the conversation, or it is kept as a flag for a reviewer.
- Large areas show summaries such as `#s_<subject>`; open one with `read_file` on the path after the arrow."""

_ID_LINE = re.compile(r"^#([0-9a-f]{8,32})(?=\s|$)\s*(?:\[(\w+)\]\s*)?(.*?)\s*(?:\((\d{4}-\d{2}-\d{2})\))?\s*$")
_STAMP = re.compile(r"\bstamp ([0-9a-f]{12})\b")
_GUTTER = re.compile(r"^\s*\d+(?:\.\d+)?\t")
_WINDOW = re.compile(r"^@@ .* @@$")


@dataclass(frozen=True)
class Folder:
    """Whose pages: `scope="user"` with the user's id, or `scope="workspace"` (`user_id` None)."""

    scope: Literal["user", "workspace"]
    workspace_id: str
    user_id: str | None

    @property
    def label(self) -> str:
        return self.user_id if self.scope == "user" else self.workspace_id

    def owns(self, row: Row) -> bool:
        return row["workspace_id"] == self.workspace_id and row["scope"] == self.scope and (
            self.scope == "workspace" or row["user_id"] == self.user_id)


@dataclass
class Line:
    id: str
    row: Row
    tag: str
    text: str


@dataclass
class Page:
    title: str
    folder: Folder
    lines: list[Line] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    area: Row | None = None
    rows: list[Row] = field(default_factory=list)  # every active line of the area, shown or summarized
    pending_rows: list[Row] = field(default_factory=list)
    text: str = ""
    stamp: str = ""

    @property
    def by_id(self) -> dict[str, Line]:
        return {ln.id: ln for ln in self.lines}


@dataclass
class PageChange:
    """What happened to one line of a written page; `line` is what the agent is told."""

    kind: Literal["saved", "pending", "archived", "flag", "refused", "no change"]
    text: str = ""
    id: str | None = None
    reason: str | None = None

    @property
    def line(self) -> str:
        if self.kind == "saved":
            return f"saved #{self.id}: {self.text}"
        if self.kind == "pending":
            return f"pending review: {self.text}" + (f" ({self.reason})" if self.reason else "")
        if self.kind == "archived":
            return f"archived #{self.id}"
        if self.kind == "flag":
            return f"flag: {self.text} (no supporting quote)"
        if self.kind == "refused":
            return f"refused: {self.reason}"
        return "no change"


# --- reading ------------------------------------------------------------------------------------------------


def area_key(area: Row) -> str:
    from memhub.pipeline.areas import slugify

    return area["payload"].get("key") or slugify(area["payload"]["title"])


def subject_slug(entity: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(entity.get("id") or "").lower()).strip("-") or "subject"


def _quote(row: Row) -> str | None:
    """The user's own words behind a row: its first quote that is not the assistant's."""
    for e in row.get("evidence") or []:
        if isinstance(e, dict) and e.get("quote") and e.get("claim_source") in (None, "user"):
            return str(e["quote"])
    return None


def tag(row: Row) -> str:
    """AD-063: a person approved it > the model inferred it > the user said it > the app added it."""
    if row.get("reviewed_by") not in (None, "", AUTO):
        return "reviewed"
    if row.get("assertion") == "inferred":
        return "inferred"
    return "said" if _quote(row) else "added"


def _text(row: Row, tg: str) -> str:
    quote = _quote(row) if tg == "said" else None
    return f'"{" ".join(quote.split())}"' if quote else " ".join(str(row["content"]).split())


def _day(value: Any) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else str(value or "")[:10]


def short_ids(memory_ids: list[Any]) -> dict[str, str]:
    """Each memory id's shortest hex prefix, at least 8, that no other id on the page shares (AD-063)."""
    hexes = {str(m): uuid.UUID(str(m)).hex for m in memory_ids}
    out = {}
    for key, h in hexes.items():
        n = 8
        while n < 32 and any(o != h and o[:n] == h[:n] for o in hexes.values()):
            n += 1
        out[key] = h[:n]
    return out


def _subjects(rows: list[Row]) -> dict[str, dict]:
    """slug -> entity, for the first subject of each row; a slug two subject types share gets `<type>-` first."""
    entities = {}
    for r in rows:
        if r["entities"]:
            e = r["entities"][0]
            entities[(e["type"], e["id"])] = e
    clash: dict[str, int] = {}
    for e in entities.values():
        clash[subject_slug(e)] = clash.get(subject_slug(e), 0) + 1
    return {(subject_slug(e) if clash[subject_slug(e)] == 1 else f"{e['type']}-{subject_slug(e)}"): e
            for e in entities.values()}


def _about(row: Row, entity: dict) -> bool:
    return bool(row["entities"]) and (row["entities"][0]["type"], row["entities"][0]["id"]) == (entity["type"], entity["id"])


def _pending(uow: UnitOfWork, folder: Folder, area: Row, viewer: str) -> list[Row]:
    """The viewer's own candidates and flags in the area, newest first."""
    link = area_link(area["memory_id"])
    rows = [
        r for status in ("candidate", "flag")
        for r in uow.memories.list_memories(status=status, workspace_id=folder.workspace_id)
        if folder.owns(r) and r["created_by"] == viewer and link in (r["links"] or [])
    ]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


def _pending_line(row: Row, ids: dict[str, str]) -> str:
    text = " ".join(str(row["content"]).split()).rstrip(".")
    if row["status"] == "flag":
        return f"- [flag] {text}. Noted {_day(row['created_at'])}."
    if row["payload"].get(ARCHIVE) == "archive":
        text = f"archive #{ids.get(str(row['memory_id'])) or uuid.UUID(str(row['memory_id'])).hex[:8]}: {text}"
    return f"- [pending] {text}. Proposed {_day(row['created_at'])}."


def find_area(uow: UnitOfWork, folder: Folder, key: str) -> Row:
    for row in uow.memories.owner_areas(scope=folder.scope, workspace_id=folder.workspace_id, user_id=folder.user_id):
        if area_key(row) == key:
            return row
    raise NotFound(f"no area {key!r}")


def areas(uow: UnitOfWork, folder: Folder, now: datetime) -> list[tuple[Row, int]]:
    """The folder's areas with their count of active, non-stale lines; areas with none are left out."""
    out = []
    for row in uow.memories.owner_areas(scope=folder.scope, workspace_id=folder.workspace_id, user_id=folder.user_id):
        n = sum(1 for r in uow.memories.linked_rows(row["memory_id"], stale_at=now) if folder.owns(r))
        if n:
            out.append((row, n))
    return out


def _header(title: str, now: datetime, stamp: str) -> list[str]:
    return [f"# {title}",
            f"<!-- memhub · rendered {now.strftime('%Y-%m-%dT%H:%M:%SZ')} · stamp {stamp} · "
            "read-only view, rebuilt from the database -->", "", HOW_TO_READ, ""]


def _stamp(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]


def build_page(uow: UnitOfWork, settings: Settings, folder: Folder, key: str | None, *, subject: str | None = None,
               viewer: str, now: datetime | None = None) -> Page:
    """The README (`key` None), an area page, or one subject of an area, as the agent reads it."""
    now = now or datetime.now(timezone.utc)
    if key is None:
        listed = [f"- {area_key(a)}.md: {a['payload']['title']} ({n} memories)" for a, n in areas(uow, folder, now)]
        body = ["## Areas", *(listed or ["<!-- no areas yet -->"])]
        page = Page(title=f"Memory ({folder.label})", folder=folder, stamp=_stamp(body))
        page.text = "\n".join([*_header(page.title, now, page.stamp), *body]) + "\n"
        return page
    area = find_area(uow, folder, key)
    rows = [r for r in uow.memories.linked_rows(area["memory_id"], stale_at=now) if folder.owns(r)]
    title = area["payload"]["title"]
    shown, summaries = rows, []
    if subject is not None:
        entity = _subjects(rows).get(subject)
        if entity is None:
            raise NotFound(f"no subject {subject!r} in area {key!r}")
        shown = [r for r in rows if _about(r, entity)]
        title = f"{title} / {entity['id']}"
    elif len(rows) > settings.render.summarize_over:
        groups = _subjects(rows)
        shown = [r for r in rows if not r["entities"]]
        for slug, entity in groups.items():
            members = [r for r in rows if _about(r, entity)]
            gist = "; ".join(" ".join(str(r["content"]).split())[:40] for r in members[:3])
            summaries.append(f"#s_{slug} ({len(members)} memories) {entity['id']}: {gist} → {key}/{slug}.md")
    pending_rows = _pending(uow, folder, area, viewer) if subject is None else []
    ids = short_ids([r["memory_id"] for r in [*rows, *pending_rows]])
    page = Page(title=f"Memory: {title} ({folder.label})", folder=folder, area=area, rows=rows, pending_rows=pending_rows,
                summaries=summaries)
    for r in shown:
        tg = tag(r)
        page.lines.append(Line(id=ids[str(r["memory_id"])], row=r, tag=tg, text=_text(r, tg)))
    page.pending = [_pending_line(r, ids) for r in pending_rows]
    memory = [f"#{ln.id} [{ln.tag}] {ln.text} ({_day(ln.row['observed_at'])})" for ln in page.lines] + summaries
    body = ["## Memory", *(memory or ["<!-- no memories yet -->"])]
    if page.pending:
        body += ["", "## Pending review (yours, not usable yet)", *page.pending]
    page.stamp = _stamp(body)
    page.text = "\n".join([*_header(page.title, now, page.stamp), *body]) + "\n"
    return page


# --- writing ------------------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _unquote(text: str) -> str:
    t = text.strip()
    return t[1:-1].strip() if len(t) >= 2 and t[0] == t[-1] == '"' else t


@dataclass
class _Op:
    kind: Literal["new", "edit", "remove", "refused"]
    text: str = ""
    line: Line | None = None
    reason: str | None = None


def diff(page: Page, written: str) -> list[_Op]:
    """The written page against the current one, in written order, then the removed lines in page order. The
    header, the how-to-read text, summary lines and the pending section are not memory: they never change it.

    An id names one line. An id the page does not show, or one written on two lines, refuses every line that holds
    it, and nothing else happens to those memories. A line is removed only when both its id and its text are gone."""
    parsed, in_howto = [], False
    for raw in written.splitlines():
        # what a file tool adds around a page it shows (deepagents: `@@ lines 1-12 of 12 @@`, a line-number gutter)
        line = _GUTTER.sub("", raw).strip()
        if _WINDOW.match(line):
            continue
        if line.startswith("## "):
            in_howto = line == "## How to read this"
            continue
        if not line or in_howto or line.startswith("# ") or line == "#" or line.startswith("<!--") \
                or re.match(r"^- \[(pending|flag)\]", line) or line.startswith("#s_"):
            continue
        m = _ID_LINE.match(line)
        parsed.append((m.group(1), m.group(3)) if m else (None, re.sub(r"^[-*]\s+", "", line)))
    counts = Counter(i for i, _ in parsed if i is not None)
    written_texts = {_norm(_unquote(t)) for i, t in parsed if i is not None}
    ops, kept = [], set()
    for line_id, text in parsed:
        if line_id is None:
            ops.append(_Op("new", text=text))
        elif counts[line_id] > 1 or line_id not in page.by_id:
            ops.append(_Op("refused", reason=f"line #{line_id} id changed"))
        else:
            kept.add(line_id)
            if _norm(text) != _norm(page.by_id[line_id].text):
                ops.append(_Op("edit", text=_unquote(text), line=page.by_id[line_id]))
    for ln in page.lines:
        if ln.id in kept or counts[ln.id] > 1 or _norm(_unquote(ln.text)) in written_texts:
            continue  # still on the page, or its line was refused: nothing happens to this memory
        ops.append(_Op("remove", line=ln))
    return ops


@dataclass
class Writer:
    """The context every change of one written page shares."""

    service: MemoryService
    folder: Folder
    viewer: str
    run_id: str
    agent_id: str | None
    thread_id: str
    messages: list[dict[str, Any]]
    check: Callable[..., Any]  # (line, types, area) -> (Proposal | None, drop reason | None, Segment)

    @property
    def settings(self) -> Settings:
        return self.service.settings

    def _flag(self, text: str, type_name: str, area: Row) -> PageChange:
        with self.service.store.unit_of_work() as uow:
            uow.memories.add_memory(
                type=type_name, schema_version=self.service.registry.latest_version(type_name), scope=self.folder.scope,
                workspace_id=self.folder.workspace_id, user_id=self.folder.user_id, content=text,
                payload={"content": text}, entities=[], embedding=self.service._embed(text),
                evidence=[{"kind": "trace", "thread_id": self.thread_id, "run_id": self.run_id}], status="flag",
                verified=False, created_by=self.viewer, links=[area_link(area["memory_id"])], run_id=self.run_id,
                agent_id=self.agent_id,
            )
        return PageChange("flag", text=text)

    def _slot_holder(self, uow: UnitOfWork, type_name: str, memory: Any, own: Any = None) -> Row | None:
        if not self.settings.is_slot(type_name, memory):
            return None
        for r in uow.memories.list_memories(status="active", type=type_name, workspace_id=self.folder.workspace_id):
            if self.folder.owns(r) and r["payload"].get("key") == memory.key and r["memory_id"] != own:
                return r
        return None

    def _wait_reason(self, type_name: str, holder: Row | None) -> str | None:
        if holder is not None:
            return f"#{uuid.UUID(str(holder['memory_id'])).hex[:8]} holds an active value"
        # a workspace line waiting for review is the usual path (AD-065); a personal one is said why
        return "needs review" if self.folder.scope == "user" else None

    def new(self, text: str, page: Page) -> PageChange:
        types = [t for t in self._types() if self.folder.scope in self.settings.type_scopes(t)]
        if not types:
            return PageChange("refused", reason=f"no memory type may be written in the {self.folder.scope} folder")
        found = self._checked(text, types, page)
        if isinstance(found, PageChange):
            return found
        p, segment = found
        try:
            self.service._check_scope(self.folder.scope)
            memory = self.service._validate(p.candidate.type, p.memory.model_dump(mode="json"))
        except Exception as exc:  # noqa: BLE001 - a validation or injection message is the refusal reason
            return PageChange("refused", reason=str(exc))
        embedding = self.service._embed(memory.content)
        evidence = evidence_entries(p, segment, SOURCE)
        with self.service.store.unit_of_work() as uow:
            refused = self._rejected_repeat(uow, p.candidate.type, embedding)
            if refused:
                return refused
            holder = self._slot_holder(uow, p.candidate.type, memory)
            waits = holder is not None or self.settings.needs_review(p.candidate.type, self.folder.scope)
            row = uow.memories.add_memory(
                type=p.candidate.type, schema_version=self.service.registry.latest_version(p.candidate.type),
                scope=self.folder.scope, workspace_id=self.folder.workspace_id, user_id=self.folder.user_id,
                content=memory.content, payload=memory.model_dump(mode="json"),
                entities=[e.model_dump() for e in memory.entities], embedding=embedding, evidence=evidence,
                status="candidate" if waits else "active", verified=False, created_by=self.viewer,
                assertion=p.candidate.assertion, links=[area_link(page.area["memory_id"])], run_id=self.run_id,
                agent_id=self.agent_id, reviewed_by=None if waits else AUTO,
            )
        if waits:
            return PageChange("pending", text=row["content"], reason=self._wait_reason(row["type"], holder))
        return PageChange("saved", text=row["content"], id=uuid.UUID(str(row["memory_id"])).hex[:8])

    def edit(self, text: str, line: Line, page: Page) -> PageChange:
        active = line.row
        found = self._checked(text, [active["type"]], page)
        if isinstance(found, PageChange):
            return found
        p, segment = found
        fields = {**active["payload"], **{k: v for k, v in p.memory.model_dump(mode="json").items() if k != "key"}}
        try:
            memory = self.service._validate(active["type"], fields, active["schema_version"])
        except Exception as exc:  # noqa: BLE001
            return PageChange("refused", reason=str(exc))
        embedding = self.service._embed(memory.content)
        with self.service.store.unit_of_work() as uow:
            refused = self._rejected_repeat(uow, active["type"], embedding)
            if refused:
                return refused
            if uow.memories.get_active(active["memory_id"]) is None:
                return PageChange("refused", reason=STALE)
            waits = self.settings.needs_review(active["type"], self.folder.scope)
            row = uow.memories.edit_memory(
                active["memory_id"], content=memory.content, payload=memory.model_dump(mode="json"),
                entities=[e.model_dump() for e in memory.entities], embedding=embedding, verified=False,
                created_by=self.viewer, as_candidate=waits, evidence=evidence_entries(p, segment, SOURCE),
                times={"observed_at": datetime.now(timezone.utc), "assertion": p.candidate.assertion},
                run_id=self.run_id, agent_id=self.agent_id, reviewed_by=None if waits else AUTO,
            )
        if waits:
            return PageChange("pending", text=row["content"], reason=self._wait_reason(row["type"], None))
        return PageChange("saved", text=row["content"], id=line.id)

    def remove(self, line: Line) -> PageChange:
        active = line.row
        with self.service.store.unit_of_work() as uow:
            if uow.memories.get_active(active["memory_id"]) is None:
                return PageChange("refused", reason=STALE)
            if not self.settings.needs_review(active["type"], self.folder.scope):
                uow.memories.archive(active["memory_id"])
                return PageChange("archived", id=line.id)
            uow.memories.edit_memory(
                active["memory_id"], content=active["content"], payload={**active["payload"], ARCHIVE: "archive"},
                entities=active["entities"], embedding=as_vector_list(active["embedding"]), verified=False,
                created_by=self.viewer, as_candidate=True, run_id=self.run_id, agent_id=self.agent_id,
            )
        return PageChange("pending", text=f"archive #{line.id}")

    def _types(self) -> list[str]:
        from memhub.pipeline.remember import remember_types

        return remember_types(self.settings)

    def _rejected_repeat(self, uow: UnitOfWork, type_name: str, embedding: list[float]) -> PageChange | None:
        try:
            self.service._refuse_rejected_repeat(uow, type=type_name, scope=self.folder.scope,
                                                 workspace_id=self.folder.workspace_id, user_id=self.folder.user_id,
                                                 embedding=embedding)
        except ValidationError:
            return PageChange("refused", reason="a reviewer rejected this before")
        return None

    def _checked(self, text: str, types: list[str], page: Page) -> tuple[Any, Any] | PageChange:
        """The line checked against the user's own words: a grounded proposal, a refusal, or a flag."""
        if injection.scan_all([text]):
            return PageChange("refused", reason="a field matches an injection pattern")
        try:
            proposal, reason, segment = self.check(text, types, page.area)
        except Exception:  # noqa: BLE001 - a model or network failure saves nothing for this line
            return PageChange("refused", reason="memory check failed, try again")
        if proposal is not None:
            return proposal, segment
        if reason == "injection":
            return PageChange("refused", reason="a field matches an injection pattern")
        if reason in (None, "ungrounded", "assistant_only", "inferred"):
            flag_type = "fact" if "fact" in self.settings.types else types[0]
            return self._flag(text, flag_type, page.area)
        return PageChange("refused", reason=reason)


def write_page(writer: Writer, page: Page, written: str, *, whole: bool) -> list[PageChange]:
    """Turn a written page into changes. `whole`: a whole-file write, refused (`ValidationError`) when its stamp is
    not the current page's; an edit is applied to the current page by the caller and is never stale."""
    if whole:
        m = _STAMP.search(written)
        if m is None or m.group(1) != page.stamp:
            raise ValidationError(STALE)
    if page.area is None:
        raise ValidationError("the README lists areas: edit lines in an area page")
    known = {_norm(ln.text) for ln in page.lines} | {_norm(_unquote(ln.text)) for ln in page.lines} \
        | {_norm(r["content"]) for r in [*page.rows, *page.pending_rows]}
    out = []
    for op in diff(page, written):
        if op.kind == "refused":
            out.append(PageChange("refused", reason=op.reason))
        elif op.kind == "new":
            if _norm(op.text) in known:
                continue  # already on the page or waiting: writing it again changes nothing
            known.add(_norm(op.text))
            out.append(writer.new(op.text, page))
        elif op.kind == "edit":
            out.append(writer.edit(op.text, op.line, page))
        else:
            out.append(writer.remove(op.line))
    return out or [PageChange("no change")]
