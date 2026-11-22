"""`MemhubBackend`: memhub as the reviewed store under a deepagents agent's `/memories/` (issue #41).

    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend, StateBackend

    backend = CompositeBackend(
        default=StateBackend(),
        routes={"/memories/": MemhubBackend(memory, workspace_from=lambda ctx: ctx["team"],
                                            user_from=lambda ctx: ctx["user"])},
    )
    agent = create_deep_agent(model=..., backend=backend)

The agent keeps its usual tools - `ls`, `read_file`, `grep`, `glob`, `write_file`, `edit_file` - over two virtual
folders built from the database on every call, never stored as files: the caller's own memory
(`users/{user_id}/`) and the workspace's (`team/{workspace_id}/`). Each folder holds a `README.md` and one
`<area>.md` per area; a summarized area also has `<area>/<subject>.md`. Writing a page back asks memhub for changes:
the result lists what happened to each line (`saved`, `pending review`, `archived`, `flag`, `refused`).

Only public `memhub` names are used here: the rules live in memhub (`Memory.render`, `Memory.write_page`), and
this file maps the deepagents backend protocol (0.7.x) onto them.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from deepagents.backends.protocol import (
    BackendProtocol, DeleteResult, EditResult, FileDownloadResponse, FileUploadResponse, GlobResult, GrepResult,
    LsResult, ReadResult, WriteResult,
)
from deepagents.backends.utils import (
    _glob_search_files, compile_grep_include_glob, create_file_data, normalize_read_bounds, perform_string_replacement,
)

from memhub import Memory, NotFound, ValidationError

NOT_FOUND = "not found"
NO_NEW_AREA = "areas come from memory.md: edit lines in an existing page"
AGENT_ID = "deepagents"


class _Reported:
    """A write or edit result whose `path` carries the result lines. deepagents 0.7 tells the agent only
    `Updated file {res.path}`, and `CompositeBackend` sets `res.path` to the routed path afterwards: the lines are
    appended again whenever `path` is set, so they reach the agent either way."""

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "path" and value is not None and getattr(self, "_lines", None):
            value = f"{str(value).split(chr(10))[0]}\n" + "\n".join(self._lines)
        object.__setattr__(self, name, value)


class _WriteResult(_Reported, WriteResult):
    pass


class _EditResult(_Reported, EditResult):
    pass


def _reported(cls: type, path: str, lines: list[str], **extra: Any) -> Any:
    res = cls(**extra)
    object.__setattr__(res, "_lines", lines)
    res.path = path
    return res


def _value(resolver: Any, context: Any) -> str | None:
    value = resolver(context) if callable(resolver) else resolver
    return None if value is None else str(value)


def _graph_messages() -> list[dict[str, Any]]:
    """The agent's conversation, read from the graph state the way deepagents' `StateBackend` reads its files."""
    try:
        from langgraph._internal._constants import CONFIG_KEY_READ
        from langgraph.config import get_config

        read = get_config().get("configurable", {}).get(CONFIG_KEY_READ)
    except Exception:  # noqa: BLE001 - outside a graph run there is no conversation
        return []
    if read is None:
        return []
    roles = {"human": "user", "ai": "assistant"}
    out = []
    for m in read("messages", True) or []:
        role = roles.get(getattr(m, "type", None))
        content = getattr(m, "content", None)
        if isinstance(content, list):  # content blocks: keep the text ones
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        if role and isinstance(content, str) and content:
            out.append({"role": role, "content": content})
    return out


class MemhubBackend(BackendProtocol):
    """deepagents' backend protocol over `Memory`. `workspace_from` / `user_from`: a fixed id, or a callable over
    the run's `runtime.context` (as `MemoryMiddleware`). `user_folder` / `team_folder`: where the two folders sit,
    as templates over `{user_id}` and `{workspace_id}`. `messages_from`: where the conversation is read for the
    quote check (default: the graph state)."""

    def __init__(self, memory: Memory, *, workspace_from: Any, user_from: Any, user_folder: str = "users/{user_id}",
                 team_folder: str = "team/{workspace_id}", roles: list[str] | None = None,
                 messages_from: Callable[[], list[dict[str, Any]]] | None = None) -> None:
        self.memory = memory
        self.workspace_from, self.user_from = workspace_from, user_from
        self.user_folder, self.team_folder = user_folder, team_folder
        self.roles = roles
        self.messages_from = messages_from or _graph_messages

    # --- who and where ----------------------------------------------------------------------------------------

    def _ids(self) -> dict[str, Any]:
        try:
            from langgraph.config import get_config
            from langgraph.runtime import get_runtime

            context = get_runtime().context
            configurable = get_config().get("configurable", {})
        except Exception:  # noqa: BLE001 - called outside a graph run (tests, scripts)
            context, configurable = None, {}
        thread = str(configurable.get("thread_id") or "deepagents")
        return dict(workspace_id=_value(self.workspace_from, context), user_id=_value(self.user_from, context),
                    run_id=str(configurable.get("run_id") or thread), agent_id=AGENT_ID,
                    **({"roles": self.roles} if self.roles is not None else {}))

    def _folders(self, ids: dict[str, Any]) -> dict[str, bool]:
        """Each folder path -> personal?; a template whose id would escape it, or two equal paths, are refused."""
        out = {}
        for template, personal in ((self.user_folder, True), (self.team_folder, False)):
            for key in ("user_id", "workspace_id"):
                value = ids.get(key) or ""
                if "/" in value or ".." in value or value.strip() == "":
                    raise ValidationError(f"{key} {value!r} cannot name a folder")
            path = template.format(user_id=ids["user_id"], workspace_id=ids["workspace_id"]).strip("/")
            if not path or path in out:
                raise ValidationError(f"the user and team folders must be two different paths, got {path!r} twice")
            out[path] = personal
        return out

    def _resolve(self, path: str) -> tuple[dict[str, Any], str | None, bool, str]:
        """(ids, the folder path or None, personal?, the rest of the path inside the folder)."""
        ids = self._ids()
        clean = path.strip("/")
        for folder, personal in self._folders(ids).items():
            if clean == folder or clean.startswith(folder + "/"):
                return ids, folder, personal, clean[len(folder):].strip("/")
        return ids, None, False, clean

    def _render(self, ids: dict[str, Any], personal: bool, rest: str) -> str:
        """The page at `rest` inside a folder: README.md, <area>.md or <area>/<subject>.md; NotFound otherwise."""
        if rest == "README.md":
            return self.memory.render(None, personal=personal, **ids)
        m = re.fullmatch(r"([^/]+)\.md", rest) or re.fullmatch(r"([^/]+)/([^/]+)\.md", rest)
        if not m:
            raise NotFound(rest)
        subject = m.group(2) if m.re.groups == 2 else None
        return self.memory.render(m.group(1), personal=personal, subject=subject, **ids)

    def _files(self, ids: dict[str, Any]) -> dict[str, str]:
        """Every page the caller can see, by its path inside the backend ("/users/u1/travel.md")."""
        files = {}
        for folder, personal in self._folders(ids).items():
            readme = self.memory.render(None, personal=personal, **ids)
            files[f"/{folder}/README.md"] = readme
            for key in re.findall(r"^- ([^\s]+)\.md:", readme, re.M):
                text = self.memory.render(key, personal=personal, **ids)
                files[f"/{folder}/{key}.md"] = text
                for target in re.findall(r"→ (\S+\.md)$", text, re.M):
                    area, slug = target[:-3].split("/", 1)
                    files[f"/{folder}/{target}"] = self.memory.render(area, personal=personal, subject=slug, **ids)
        return files

    # --- reads ------------------------------------------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        try:
            ids = self._ids()
            clean = path.strip("/")
            folders = self._folders(ids)
            dirs = sorted({f[len(clean):].strip("/").split("/")[0] for f in folders
                           if (not clean or f.startswith(clean + "/")) and f != clean})
            if dirs:
                prefix = f"/{clean}/" if clean else "/"
                return LsResult(entries=[{"path": f"{prefix}{d}/", "is_dir": True} for d in dirs])
            files = self._files(ids)
            inside = f"/{clean}/"
            names = sorted({p[len(inside):].split("/")[0] + ("/" if "/" in p[len(inside):] else "")
                            for p in files if p.startswith(inside)})
            if not names:
                return LsResult(error=NOT_FOUND)
            return LsResult(entries=[{"path": inside + n, "is_dir": n.endswith("/")} for n in names])
        except (NotFound, ValidationError) as exc:
            return LsResult(error=NOT_FOUND if isinstance(exc, NotFound) else str(exc))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        try:
            ids, folder, personal, rest = self._resolve(file_path)
            if folder is None:
                return ReadResult(error=NOT_FOUND)
            text = self._render(ids, personal, rest)
        except NotFound:
            return ReadResult(error=NOT_FOUND)
        except ValidationError as exc:
            return ReadResult(error=str(exc))
        offset, limit = normalize_read_bounds(offset, limit)
        if limit == 0:
            return ReadResult(no_lines_requested=True)
        lines = text.splitlines()
        window = lines[offset:offset + limit]
        if not window:
            return ReadResult(file_data=create_file_data(""))
        end = offset + len(window)
        return ReadResult(file_data=create_file_data("\n".join(window)), total_lines=len(lines), start_line=offset + 1,
                          end_line=end, next_offset=end if end < len(lines) else None)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *,
             max_count: int | None = None) -> GrepResult:
        try:
            files = self._files(self._ids())
        except ValidationError as exc:
            return GrepResult(error=str(exc))
        root = "/" + (path or "/").strip("/")
        include = compile_grep_include_glob(glob) if glob else None
        matches = []
        for p, text in sorted(files.items()):
            if not (root == "/" or p == root or p.startswith(root.rstrip("/") + "/")):
                continue
            if include is not None and not include(p):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if pattern in line:
                    matches.append({"path": p, "line": n, "text": line})
                    if max_count is not None and len(matches) >= max_count:
                        return GrepResult(matches=matches)
        return GrepResult(matches=matches)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        try:
            files = {p: create_file_data(t) for p, t in self._files(self._ids()).items()}
            found = _glob_search_files(files, pattern, path or "/")
        except Exception as exc:  # noqa: BLE001 - a refused pattern is a result, as in deepagents' own backends
            return GlobResult(error=str(exc))
        if found == "No files found":
            return GlobResult(matches=[])
        return GlobResult(matches=[{"path": p, "is_dir": False} for p in found.split("\n")])

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        out = []
        for p in paths:
            res = self.read(p, 0, 10**9)
            if res.error:
                out.append(FileDownloadResponse(path=p, error="file_not_found"))
            else:
                text = res.file_data["content"] if res.file_data else ""
                out.append(FileDownloadResponse(path=p, content=(text + "\n").encode("utf-8")))
        return out

    # --- writes -----------------------------------------------------------------------------------------------

    def _change(self, file_path: str, new_text: Callable[[str], str | EditResult], *, edit: bool, cls: type) -> Any:
        try:
            ids, folder, personal, rest = self._resolve(file_path)
            m = re.fullmatch(r"([^/]+)\.md", rest) or re.fullmatch(r"([^/]+)/([^/]+)\.md", rest)
            if folder is None or m is None or rest == "README.md":
                return cls(error=NOT_FOUND if folder is None else NO_NEW_AREA)
            subject = m.group(2) if m.re.groups == 2 else None
            try:
                current = self._render(ids, personal, rest)
            except NotFound:
                return cls(error=NO_NEW_AREA if subject is None else NOT_FOUND)
            text = new_text(current)
            if not isinstance(text, str):
                return text
            changes = self.memory.write_page(m.group(1), text, personal=personal, subject=subject, edit=edit,
                                             messages=self.messages_from(), thread_id=ids["run_id"], **ids)
        except ValidationError as exc:
            return cls(error=str(exc))
        extra = {"occurrences": 1} if cls is _EditResult else {}
        return _reported(cls, file_path, [c.line for c in changes], **extra)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._change(file_path, lambda _current: content, edit=False, cls=_WriteResult)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        def apply(current: str) -> str | EditResult:
            done = perform_string_replacement(current, old_string, new_string, replace_all)
            return EditResult(error=done) if isinstance(done, str) else done[0]

        return self._change(file_path, apply, edit=True, cls=_EditResult)

    def delete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=NO_NEW_AREA)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=p, error=NO_NEW_AREA) for p, _ in files]
