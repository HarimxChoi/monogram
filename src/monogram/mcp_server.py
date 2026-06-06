"""MCP server; write tools (add_wiki_entry, set_llm_config) are gated by Telegram /approve_<token>."""
from __future__ import annotations

import asyncio
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import github_store
from .llm import complete
from .safe_read import safe_read

server: Server = Server("monogram")

TOOLS: list[Tool] = [
    Tool(
        name="read_project",
        description="Read a project file by name (e.g. 'paper-a').",
        inputSchema={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project filename without extension",
                }
            },
            "required": ["project"],
        },
    ),
    Tool(
        name="list_projects",
        description="List active projects.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="update_project",
        description="Append a timestamped note line to a project log.",
        inputSchema={
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["project", "note"],
        },
    ),
    Tool(
        name="today_brief",
        description="Generate today's priority brief from vault state.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="recent_activity",
        description="Summary of recent drops and GitHub activity.",
        inputSchema={
            "type": "object",
            "properties": {"hours": {"type": "integer", "default": 24}},
        },
    ),
    Tool(
        name="search_wiki",
        description="Search wiki/index.md entries by substring over slug/summary/tags.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="semantic_search",
        description="Meaning-based HYBRID search over the whole vault (dense "
        "embeddings + lexical BM25). Returns the most relevant notes/chunks. Run "
        "`monogram reindex` first to build the index.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 8},
                "kind": {
                    "type": "string",
                    "description": "Optional area filter: wiki/projects/life/daily/identity",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="graph_search",
        description="Graph-aware search: semantic seed → PageRank over the event "
        "graph → most relevant notes WITH their connected neighborhood "
        "(motivated_by / documents / …). Run `monogram reindex` + `monogram graph` first.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="query_life",
        description=(
            "Return recent entries from life/<area>.md within the last N days. "
            "Credentials area is unconditionally blocked."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "area": {"type": "string"},
                "days": {"type": "integer", "default": 7},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["area"],
        },
    ),
    Tool(
        name="get_morning_brief",
        description=(
            "Return daily/<date>/report.md. Defaults to yesterday if date omitted."
        ),
        inputSchema={
            "type": "object",
            "properties": {"date": {"type": "string"}},
        },
    ),
    Tool(
        name="current_project_state",
        description="Return projects/<slug>.md (frontmatter + body). Falls back to archive.",
        inputSchema={
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
        },
    ),
    Tool(
        name="get_board",
        description="Return the current board.md contents.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="add_wiki_entry",
        description=(
            "Create a new wiki/<slug>.md entry. Requires Telegram approval. "
            "Will NOT overwrite an existing entry."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["slug", "title", "body"],
        },
    ),
    Tool(
        name="get_llm_config",
        description="Get the current LLM configuration from mono/config.md.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="set_llm_config",
        description=(
            "Update LLM configuration in mono/config.md. Requires user "
            "approval via Telegram. All fields optional — pass only what "
            "you want to change."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "mode": {"type": "string"},
                "models": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "base_url": {"type": "string"},
            },
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


async def _read_project(project: str) -> str:
    # safe_read blocks life/credentials/ even for legacy callers.
    content = safe_read(f"projects/{project}.md")
    return content or f"Project '{project}' not found."


async def _list_projects() -> str:
    readme = safe_read("README.md")
    return readme or "vault README is empty."


async def _update_project(project: str, note: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    line = f"- {timestamp} — {note}"
    ok = github_store.append(
        f"projects/{project}.md",
        line,
        f"update: {project} — {note[:40]}",
    )
    return "Updated" if ok else "Update failed"


async def _today_brief() -> str:
    context = safe_read("README.md")
    return await complete(
        "Generate today's priority brief from this vault state. "
        "Keep under 150 words. Use emojis for status.\n\n" + context
    )


async def _recent_activity(hours: int = 24) -> str:
    return f"Recent activity (last {hours}h): github_digest not yet built."


async def _semantic_search(query: str, k: int = 8, kind: str | None = None) -> str:
    from .semantic_index import semantic_search

    hits = await semantic_search(query, k=k, areas=[kind] if kind else None)
    if not hits:
        return f"No semantic hits for: {query}  (run `monogram reindex` if the index is empty)"
    lines = []
    for h in hits:
        head = f" — {h['heading']}" if h.get("heading") else ""
        lines.append(f"{h['path']}{head}\n    {h.get('excerpt', '')}")
    return "\n".join(lines)


async def _graph_search(query: str, k: int = 8) -> str:
    from .graph_search import graph_search

    results = await graph_search(query, k=k)
    if not results:
        return f"No graph hits for: {query}  (run `monogram reindex` + `monogram graph` first)"
    lines = []
    for r in results:
        lbl = f" — {r['label']}" if r.get("label") else ""
        lines.append(f"{r['path']}{lbl}")
        for nb in r.get("neighborhood", []):
            lines.append(f"    ─{nb['predicate']}→ {nb['node']}")
    return "\n".join(lines)


async def _get_llm_config() -> str:
    import json
    from .vault_config import load_vault_config
    vc = load_vault_config()
    return json.dumps({
        "provider": vc.llm_provider,
        "mode": vc.llm_mode,
        "models": vc.llm_models,
        "base_url": vc.llm_base_url,
    }, indent=2)


async def _set_llm_config(
    provider: str = "",
    mode: str = "",
    models: dict | None = None,
    base_url: str | None = None,
) -> str:
    from .bot_notify import push_to_telegram
    from .mcp_pending import new_pending

    changes: dict = {}
    if provider:
        changes["llm_provider"] = provider
    if mode:
        changes["llm_mode"] = mode
    if models:
        changes["llm_models"] = models
    if base_url is not None:
        changes["llm_base_url"] = base_url

    if not changes:
        return "No fields provided — nothing to change."

    preview = "\n".join(f"{k}: {v}" for k, v in changes.items())
    entry = new_pending("set_llm_config", changes, preview)
    await push_to_telegram(
        f"🔌 MCP client wants to update LLM config:\n\n"
        f"{preview}\n\n"
        f"/approve_{entry.token} or /deny_{entry.token} (expires 5 min)"
    )
    return f"Pending approval — check Telegram. Token: {entry.token}"


async def _add_wiki_entry(
    slug: str, title: str, body: str, tags: list | None = None
) -> str:
    from .mcp_writes import add_wiki_entry_pending
    return await add_wiki_entry_pending(slug, title, body, tags or [])


_DISPATCH = {
    "read_project": lambda a: _read_project(a["project"]),
    "list_projects": lambda a: _list_projects(),
    "update_project": lambda a: _update_project(a["project"], a["note"]),
    "today_brief": lambda a: _today_brief(),
    "recent_activity": lambda a: _recent_activity(a.get("hours", 24)),
    "semantic_search": lambda a: _semantic_search(a["query"], a.get("k", 8), a.get("kind")),
    "graph_search": lambda a: _graph_search(a["query"], a.get("k", 8)),
    "search_wiki": lambda a: (
        __import__("monogram.mcp_reads", fromlist=["search_wiki"]).search_wiki(
            a["query"], a.get("limit", 10)
        )
    ),
    "query_life": lambda a: (
        __import__("monogram.mcp_reads", fromlist=["query_life"]).query_life(
            a["area"], a.get("days", 7), a.get("limit", 20)
        )
    ),
    "get_morning_brief": lambda a: (
        __import__("monogram.mcp_reads", fromlist=["get_morning_brief"]).get_morning_brief(
            a.get("date", "")
        )
    ),
    "current_project_state": lambda a: (
        __import__("monogram.mcp_reads", fromlist=["current_project_state"]).current_project_state(
            a["slug"]
        )
    ),
    "get_board": lambda a: (
        __import__("monogram.mcp_reads", fromlist=["get_board"]).get_board()
    ),
    "add_wiki_entry": lambda a: _add_wiki_entry(**a),
    "get_llm_config": lambda a: _get_llm_config(),
    "set_llm_config": lambda a: _set_llm_config(**a),
}


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    handler = _DISPATCH.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        text = await handler(args)
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
    return [TextContent(type="text", text=text)]


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
