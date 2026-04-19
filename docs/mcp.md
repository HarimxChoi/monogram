# Monogram — MCP Server Spec

> Expose Monogram's tools to any MCP client: Claude Desktop, Cursor,
> Codex, OpenClaw, and future MCP-compatible agents.

---

## 0. Why This Matters

Telegram is the primary channel — but not the only one.

By exposing Monogram as an MCP server, every MCP client becomes a Monogram client.
A Cursor user can query their scheduler while coding. A Claude Desktop user can
ask "what did I save yesterday" without opening Telegram.

**This is the real distribution play.** Telegram is one client. MCP makes
Monogram infrastructure for every agent.

---

## 1. What MCP Is (briefly)

Standardized protocol for AI applications to connect with external tools.
Donated to Linux Foundation Dec 2025. Adopted by Anthropic, OpenAI, Google,
toolmakers across the ecosystem.

Server exposes tools via JSON schema. Any MCP-compatible client
(Claude Desktop, Cursor, Codex, OpenClaw, etc.) can call them.

Python SDK: `mcp` package. Server implementation ~50 lines.

---

## 2. Exposed Tools (v1)

```
read_project(project: str) -> str
  Read a scheduler project file.
  e.g. read_project("paper-a") → full markdown content

update_project(project: str, note: str) -> bool
  Append a note to a project's log.
  e.g. update_project("paper-a", "Phase 0 experiment started")

list_projects() -> list[str]
  Return all active project names.

search_wiki(query: str, limit: int = 5) -> list[dict]
  Search knowledge base. Returns [{path, title, snippet, confidence}]

read_wiki(path: str) -> str
  Read a specific wiki page.

today_brief() -> str
  Generate today's priorities based on scheduler + deadlines.

recent_activity(hours: int = 24) -> str
  Summary of last N hours: GitHub commits, drops, wiki updates.

add_to_inbox(content: str, source: str = None) -> str
  Drop raw content into wiki/_inbox/ for later classification.

status() -> dict
  Current state: active projects, at-risk, upcoming deadlines.
```

---

## 3. Minimal Implementation

```python
# core/mcp_server.py
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("monogram")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="read_project",
            description="Read a scheduler project file",
            inputSchema={
                "type": "object",
                "properties": {"project": {"type": "string"}},
                "required": ["project"]
            }
        ),
        Tool(
            name="today_brief",
            description="Get today's priorities",
            inputSchema={"type": "object", "properties": {}}
        ),
        # ... other tools
    ]

@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    if name == "read_project":
        content = gh_read(f"scheduler/projects/{args['project']}.md")
        return [TextContent(type="text", text=content)]
    
    if name == "today_brief":
        brief = await generate_today_brief()
        return [TextContent(type="text", text=brief)]
    
    # ... other handlers

if __name__ == "__main__":
    stdio_server(server)
```

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

For `update_project`, `add_to_inbox` — client prompts user before execution.
Same permission model as Telegram bot, just surfaced through different UI.

---

## 6. Distribution

```
pip install mono-gram
monogram init
monogram mcp-serve           # local stdio server
```

That's it. No separate install, no extra config.
One command exposes Monogram to every MCP client on the machine.

For remote use (v2): HTTP transport with OAuth. Not needed for v1.

---

## 7. Why This Is High Leverage

**Before MCP:**
```
Monogram user base = people who want a Telegram-based agent
```

**After MCP:**
```
Monogram user base = people who use ANY agent and want scheduler + wiki
                     (Claude Desktop users, Cursor users, OpenClaw users,
                      Codex users, future MCP clients yet to exist)
```

The addressable market is 10-100x larger.
And the implementation cost is one Python file.

---

## 8. Roadmap

- v0.2 — Basic MCP server: 5 core tools, stdio transport
- v0.3 — All tools exposed, proper schemas
- v0.4 — HTTP transport for remote clients (optional)
- v1.0 — Published `@modelcontextprotocol/server-monogram` npm package
        and included in ClawHub skill registry
