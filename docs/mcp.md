# Monogram — MCP Server Spec

> Expose Monogram's tools to any MCP client: Claude Desktop, Cursor,
> Codex, OpenClaw, and future MCP-compatible agents.

---

## 0. Scope

Telegram is the primary input. MCP is a read/query surface over the same
vault, so non-Telegram clients (Claude Desktop, Cursor) can reach it.

Reads are unrestricted; writes are gated by a Telegram approval token.

---

## 1. What MCP Is (briefly)

Standardized protocol for AI applications to connect with external tools.
Donated to Linux Foundation Dec 2025. Adopted by Anthropic, OpenAI, Google,
toolmakers across the ecosystem.

Server exposes tools via JSON schema. Any MCP-compatible client
(Claude Desktop, Cursor, Codex) can call them.

Python SDK: `mcp` package. Implementation lives in `src/monogram/mcp_server.py`.

---

## 2. Exposed Tools

Source of truth: `src/monogram/mcp_server.py`. 13 tools across three
groups — reads, one gated write, and LLM config.

### Reads (no approval needed)

```
read_project(project: str) -> str
  Read a project file by name (e.g. "paper-a") from projects/<slug>.md.

list_projects() -> str
  Return the vault README contents (which lists active projects).

today_brief() -> str
  Generate today's priority brief from vault state via LLM.

recent_activity(hours: int = 24) -> str
  Summary of recent drops and GitHub activity over the last N hours.

search_wiki(query: str, limit: int = 10) -> str
  Search wiki/index.md entries by substring over slug/summary/tags.

query_life(area: str, days: int = 7, limit: int = 20) -> str
  Recent entries from life/<area>.md. Credentials area is hard-blocked.

get_morning_brief(date: str = "") -> str
  Return daily/<date>/report.md (defaults to yesterday).

current_project_state(slug: str) -> str
  Return projects/<slug>.md. Falls back to projects/archive/.

get_board() -> str
  Return current board.md contents.
```

### Gated write (requires Telegram /approve_<token>)

```
add_wiki_entry(slug: str, title: str, body: str, tags: list[str] = [])
  Create a new wiki/<slug>.md entry. Will NOT overwrite existing.
```

### LLM config

```
get_llm_config() -> str
  Get provider/mode/models/base_url from mono/config.md.

set_llm_config(provider?, mode?, models?, base_url?) -> str
  Update LLM config. Requires /approve_<token> via Telegram.

update_project(project: str, note: str) -> str
  (Legacy) append a timestamped note line to projects/<slug>.md.
```

---

## 3. Implementation shape

```python
# src/monogram/mcp_server.py (excerpt)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("monogram")

TOOLS: list[Tool] = [
    Tool(
        name="read_project",
        description="Read a project file by name (e.g. 'paper-a').",
        inputSchema={
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
        },
    ),
    # ... 12 more
]

@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS

@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    if name == "read_project":
        content = safe_read(f"projects/{args['project']}.md")
        return [TextContent(type="text", text=content or "not found")]
    # ... other handlers
```

Reads go through `safe_read`, which enforces `never_read_paths`
(credentials are blocked unconditionally). Writes route through the
Telegram approval queue before touching the vault.

Run:
```bash
monogram mcp-serve
# exposes via stdio — any MCP client can connect
```

---

## 4. Client Configuration Examples

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "monogram": {
      "command": "monogram",
      "args": ["mcp-serve"]
    }
  }
}
```

After restart, Claude Desktop has access to all Monogram tools.
User can ask: "Claude, what's on my agenda today?"

### Cursor

`~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "monogram": {
      "command": "monogram",
      "args": ["mcp-serve"]
    }
  }
}
```

Cursor now has Monogram as a tool source while coding.

### OpenClaw

Via ClawHub skill install — Monogram is registered as an MCP server
in the user's OpenClaw SOUL.md.

---

## 5. Security Model

MCP runs locally. Uses stdio transport by default — no network exposure.

```
Tools that READ    → safe, auto-allow
Tools that WRITE   → require explicit user confirmation per call
                     (most MCP clients have this built in)
```

Writes (`add_wiki_entry`, `set_llm_config`, `update_project`) require a
Telegram `/approve_<token>` before the vault is touched. Same permission
model as the Telegram bot, surfaced through a different UI.

---

## 6. Distribution

```
pip install mono-gram
monogram init
monogram mcp-serve           # local stdio server
```

Remote / networked use (HTTP transport + OAuth) is out of scope for v1.0.

---

## 7. Roadmap

- **v0.8 (current)** — 13 tools, stdio transport, Telegram-gated writes
- **v1.0** — tool-level audit log, richer `recent_activity` (GitHub digest)
- **post-v1.0** — HTTP transport with OAuth for remote clients
