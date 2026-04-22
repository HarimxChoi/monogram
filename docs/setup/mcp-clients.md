# Monogram MCP Setup — Claude Desktop, Cursor, OpenClaw

## 1. Install `monogram`

Prereqs: **Python 3.10+** and a filled `.env` next to your working dir (see
`.env.example`). You'll need `GEMINI_API_KEY`, `GITHUB_PAT`, `GITHUB_REPO`
for the MCP tools to actually do anything — the server *starts* without
them, but tool calls fail.

### Recommended: pipx (isolated, global command)

```bash
# macOS / Linux
brew install pipx || python3 -m pip install --user pipx
pipx ensurepath

pipx install mono-gram
# or, from this repo's root while under development:
pipx install --editable .
```

```powershell
# Windows (PowerShell)
py -m pip install --user pipx
py -m pipx ensurepath
# restart the shell so PATH picks up ~\.local\bin

pipx install mono-gram
# or, in-repo dev install:
pipx install --editable .
```

pipx puts the binary at:

| OS | Path |
|---|---|
| macOS / Linux | `~/.local/bin/monogram` |
| Windows | `%USERPROFILE%\.local\bin\monogram.exe` |

### Fallback: venv (no pipx available)

```bash
# macOS / Linux
python3 -m venv ~/.monogram
~/.monogram/bin/pip install --upgrade pip
~/.monogram/bin/pip install -e /path/to/monogram

# Binary at: ~/.monogram/bin/monogram
```

```powershell
# Windows
py -m venv $HOME\.monogram
$HOME\.monogram\Scripts\pip install --upgrade pip
$HOME\.monogram\Scripts\pip install -e C:\path\to\monogram

# Binary at: $HOME\.monogram\Scripts\monogram.exe
```

With venv, the binary is **not** on PATH. You must point MCP configs at
the absolute path below.

### Verify the install

```bash
monogram --version           # → monogram, version 0.8.0.dev0
monogram --help              # shows init / mcp-serve / run
monogram mcp-serve           # starts stdio server; Ctrl-C to exit
```

If `monogram: command not found`, see **Troubleshooting** below.

---

## 2. Configure your MCP client

The JSON shape is the same for all clients. Only the **config file
location** and the **binary path** change. Get the binary path once:

```bash
# macOS / Linux
which monogram
# e.g. /Users/<you>/.local/bin/monogram
```

```powershell
# Windows
where.exe monogram
# e.g. C:\Users\<you>\.local\bin\monogram.exe
```

Use that exact path in the configs below. Even on pipx, **absolute paths
are more reliable than bare `monogram`** — Claude Desktop and Cursor spawn
subprocesses with a minimal PATH.

### 2.1 Claude Desktop

Config file (create if missing):

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Contents:

```json
{
  "mcpServers": {
    "monogram": {
      "command": "/Users/<you>/.local/bin/monogram",
      "args": ["mcp-serve"]
    }
  }
}
```

Windows equivalent:

```json
{
  "mcpServers": {
    "monogram": {
      "command": "C:\\Users\\<you>\\.local\\bin\\monogram.exe",
      "args": ["mcp-serve"]
    }
  }
}
```

If your `.env` isn't in the cwd Claude Desktop spawns the subprocess from
(hint: it's usually `$HOME`), also pass env inline:

```json
{
  "mcpServers": {
    "monogram": {
      "command": "/Users/<you>/.local/bin/monogram",
      "args": ["mcp-serve"],
      "env": {
        "GEMINI_API_KEY": "…",
        "GITHUB_PAT": "…",
        "GITHUB_REPO": "<owner>/mono"
      }
    }
  }
}
```

Restart Claude Desktop (fully quit + reopen — not just close the window).

### 2.2 Cursor

Cursor reads MCP config from either:
- GUI: **Settings → Cursor Settings → MCP → Add new MCP server**
- File: `~/.cursor/mcp.json` (or project-scoped `./.cursor/mcp.json`)

Same shape as Claude Desktop:

```json
{
  "mcpServers": {
    "monogram": {
      "command": "/Users/<you>/.local/bin/monogram",
      "args": ["mcp-serve"]
    }
  }
}
```

Cursor refreshes MCP servers per chat; toggle the server off/on in the
MCP panel if it doesn't pick up edits.

### 2.3 OpenClaw

OpenClaw consumes standard MCP servers via its config. While the full
v1.0 packaging (`SOUL.md` + `SKILL.md` + ClawHub) lands later, today you
can register Monogram as a plain MCP server:

```json
{
  "mcpServers": {
    "monogram": {
      "command": "/Users/<you>/.local/bin/monogram",
      "args": ["mcp-serve"]
    }
  }
}
```

Config location varies by OpenClaw install — check your install's docs
for the exact file; the JSON shape is identical to the above. When we
ship the SOUL/SKILL bundle in v1.0, installation will reduce to
`claw install monogram`.

---

## 3. Verify in the client

1. Restart the client completely.
2. Open a new chat.
3. Find the tools panel / MCP indicator — it should show **`monogram`** with
   **13 tools** across three groups: reads, a gated write, and LLM config.
   Authoritative list: [docs/mcp.md §2](../mcp.md).
4. Ask a natural question that should trigger one of them, e.g. *"List my
   projects."* → the client should call `list_projects` and return the
   README contents.
5. Ask *"Generate today's brief."* → `today_brief` runs, LLM produces a
   priority brief.

If any step fails, jump to **Troubleshooting**.

> **On your phone?** The same reads are available as Telegram bot
> commands (`/report`, `/weekly`, `/search`, `/last`, …) — MCP and the
> Telegram bot are two independent surfaces over the same vault.
> Full bot reference: [docs/setup/telegram.md §6 Bot commands](telegram.md#6-bot-commands).

---

## 4. Troubleshooting

### A. `monogram: command not found`

**Cause:** binary isn't on PATH. Usually pipx's shim dir isn't in your shell's PATH yet.

**Diagnose:**
```bash
# macOS / Linux
pipx ensurepath
echo $PATH | tr ':' '\n' | grep -E '\.local/bin|pipx'
```
```powershell
# Windows
py -m pipx ensurepath
$env:PATH -split ';' | Select-String 'local\\bin|pipx'
```

**Fix:** open a new shell (`ensurepath` edits `.zshrc`/`.bashrc`/registry but
doesn't affect the current process). If it's still missing, hard-code the
absolute path in your MCP config (`which monogram` / `where.exe monogram`).

### B. No tools showing in the client, or server listed but empty

**Cause (most common):** the client didn't reload, or the config JSON is invalid.

**Diagnose:**
```bash
# Validate the JSON syntax
python3 -m json.tool < "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
# Tail the client's log — macOS Claude Desktop example:
tail -n 80 "$HOME/Library/Logs/Claude/mcp-server-monogram.log"
```

**Fix:**
1. Fully quit the client (Cmd-Q / right-click taskbar → Quit, not just close the window).
2. If the log shows JSON parse errors — fix commas/braces and retry.
3. If the log shows the server dying at startup — run the same command
   manually and watch stderr:
   ```bash
   /Users/<you>/.local/bin/monogram mcp-serve < /dev/null
   ```

### C. `Authentication failed` / tool calls return errors

**Cause:** `.env` wasn't loaded, or tokens are wrong/expired.

**Diagnose:**
```bash
# From a shell, with the same cwd the client spawns from (usually $HOME):
cd ~
monogram mcp-serve < /dev/null &  sleep 1; kill %1
# Then check: does $HOME contain a .env? If not, tool calls can't see it.
ls -la ~/.env
```

**Fix:** easiest is to put the env vars directly in the MCP config under
`"env": { ... }` (see Claude Desktop example with env block above). That
way the client passes them to the subprocess and cwd doesn't matter.

If tokens are expired: rotate `GITHUB_PAT` at
`github.com/settings/tokens`, rotate `GEMINI_API_KEY` at `aistudio.google.com`,
then update both your `.env` and (if used) the `env` block in the MCP config.

---

## 5. Verification checklist

- [ ] `monogram --version` prints `0.8.0.dev0` (or later)
- [ ] `monogram mcp-serve` starts (and you can Ctrl-C out)
- [ ] Client (Claude Desktop / Cursor / OpenClaw) lists `monogram` with **13 tools**
- [ ] A natural-language ask triggers `list_projects` and returns content
- [ ] A natural-language ask triggers `today_brief` and returns a brief
