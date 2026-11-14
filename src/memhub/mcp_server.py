"""`memhub mcp`: memhub's `search_memory` and `propose_memory` tools over MCP (stdio), for Claude Code and any MCP
client.

    claude mcp add memhub -- memhub mcp

The tools are `MemoryToolkit`'s: the same views and the same `{"id", "event", "status"}` answer to a proposal, which
is always a candidate waiting in `memhub queue`. One server process is one session: it runs as `run_id = mcp-<hex>`
and `agent_id = "mcp"`, for the workspace and user `identity` picks. Needs `pip install memhub[mcp]`.
"""
import getpass
import re
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

AGENT_ID = "mcp"
INSTRUCTIONS = (
    "Long-term memory of this project and user. Call search_memory before answering when past decisions, "
    "preferences or fixes may matter. Call propose_memory for something durable worth keeping: it waits for a "
    "person's review before it is ever used."
)


def require_sdk() -> Any:
    """The MCP SDK's server class; `ImportError` naming the extra when it is not installed."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise ImportError("memhub mcp needs the MCP SDK: pip install memhub[mcp]") from exc
    return MCPServer


def folder_workspace(folder: Path) -> str:
    """A folder as Claude Code names its project folder: every character outside A-Za-z0-9 becomes `-`."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(folder))


def identity(workspace: Optional[str], user: Optional[str], *, cwd: Optional[Path] = None) -> tuple[str, str]:
    """Workspace and user of the server: the ones given (flag, `MEMHUB_*`, `memhub use`), else the working folder
    named the way the `claude_code` source names it, and the OS user."""
    return workspace or folder_workspace(cwd or Path.cwd()), user or getpass.getuser()


def new_run_id() -> str:
    return f"mcp-{uuid.uuid4().hex}"


def build_server(memory: Any, *, workspace_id: str, user_id: str, run_id: str) -> Any:
    """An MCP server whose two tools run on `memory` for this workspace and user. Raises before serving when the
    ledger cannot be used (no tables, an old schema, another embedder)."""
    server_class = require_sdk()
    kit = memory.tools(workspace_id=workspace_id, user_id=user_id, run_id=run_id, agent_id=AGENT_ID)
    types = tuple(memory.settings.types)
    scopes = tuple(memory.settings.scopes)
    server = server_class("memhub", instructions=INSTRUCTIONS)

    def search_memory(query: str, type: Optional[str] = None) -> str:
        return kit.call("search_memory", {"query": query, **({"type": type} if type else {})})

    def propose_memory(type: str, fields: dict, evidence: Optional[list[dict]] = None, scope: str = "user",
                       memory_id: Optional[str] = None, user_requested: bool = False) -> str:
        return kit.call("propose_memory", {"type": type, "fields": fields, "evidence": evidence or [], "scope": scope,
                                           "memory_id": memory_id, "user_requested": user_requested})

    # the configured types and scopes, as enums the model can see
    search_memory.__annotations__["type"] = Optional[Literal[types]]  # type: ignore[valid-type]
    propose_memory.__annotations__["type"] = Literal[types]  # type: ignore[valid-type]
    propose_memory.__annotations__["scope"] = Literal[scopes]  # type: ignore[valid-type]
    specs = {s["function"]["name"]: s["function"]["description"] for s in kit.tool_specs()}
    server.tool(description=specs["search_memory"])(search_memory)
    server.tool(description=specs["propose_memory"])(propose_memory)
    return server


def serve(memory: Any, *, workspace_id: str, user_id: str) -> None:
    build_server(memory, workspace_id=workspace_id, user_id=user_id, run_id=new_run_id()).run("stdio")
