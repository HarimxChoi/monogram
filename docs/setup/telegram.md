# Telegram Setup

Monogram needs two Telegram identities and your own numeric user ID.
They serve different jobs:

| What | Uses | Credential |
|---|---|---|
| **API app** | Telethon listener reads Saved Messages | `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` |
| **Bot** | aiogram bot for approvals, `/stats`, morning brief push | `TELEGRAM_BOT_TOKEN` |
| **User ID** | auth gate — only you can invoke bot commands | `TELEGRAM_USER_ID` |

End-to-end setup takes ~5 minutes once.

## 1. API app — `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`

The Telethon listener authenticates as **your personal account** so it
can read your own Saved Messages. This is not a bot — Saved Messages
aren't accessible to bots in Telegram's API.

1. Open <https://my.telegram.org/apps> and sign in with your phone number.
2. Click **Create new application**.
3. Fill in:
   - **App title**: `monogram-personal` (anything sensible)
   - **Short name**: `monogram`
   - **Platform**: `Other`
4. Record the two values Telegram shows:
   - `api_id` — integer, e.g. `1234567`
   - `api_hash` — 32-char hex string

Put both in `.env`:

```env
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
```

## 2. Bot token — `TELEGRAM_BOT_TOKEN`

The bot handles `/approve_<token>` for MCP-gated writes, `/stats`, the
morning brief push, and the `/webui` command.

1. In Telegram, open a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and answer the prompts:
   - **Bot name**: e.g. `Monogram Approve Bot`
   - **Username**: must end with `_bot` or `Bot`, e.g. `yourname_monogram_bot`
3. BotFather replies with a token in the form `1234567890:AAF-abc123...`.
4. Put it in `.env`:

```env
TELEGRAM_BOT_TOKEN=1234567890:AAF-abc123...
```

Then **send any message to your new bot** (e.g. just type `hi`). Until
you do that at least once, Telegram hasn't opened a DM channel with
the bot and the bot can't push morning briefs to you.

## 3. Your numeric user ID — `TELEGRAM_USER_ID`

Every bot command checks `msg.from_user.id == TELEGRAM_USER_ID` before
doing anything. Without it, anyone who finds the bot could issue
commands.

1. In Telegram, open a chat with [@userinfobot](https://t.me/userinfobot)
   and send `/start`.
2. It replies with your numeric user ID (e.g. `123456789`).
3. Put it in `.env`:

```env
TELEGRAM_USER_ID=123456789
```

## 4. First-run authentication

With all four env vars in place, run once:

```bash
monogram auth
```

Telethon will send an SMS or Telegram code to your phone. Enter it, and
Monogram writes `monogram_session.session` in the working directory.
That file proves you authorised this machine; keep it safe
(`.gitignore` already excludes `*.session`).

If you move the session file to a new machine, you don't need to
re-auth — but treat it like a password.

## 5. Verify

Start the full service:

```bash
monogram run
```

Then from Telegram:

- **Saved Messages** — drop anything (link, note). Within seconds a
  commit appears on `<your-github-user>/mono`. The bot replies with a
  one-line confirmation.
- **Bot DM** — type `/stats`. You should get a pipeline-health report.
- **Bot DM** — type `/webui` (only if you've enabled
  `webui_mode: gcs` in `mono/config.md`). You should get a URL.

If Saved Messages drops don't commit within ~30s, check
`journalctl -u monogram -f` on the VM for auth errors or `monogram
auth` / `monogram run` stderr.

## 6. Bot commands

The bot exposes three kinds of command. Every command is gated on
`TELEGRAM_USER_ID`; non-matching senders are silently ignored.

### Reports & queries (on-demand from your phone)

| Command | What it does | Default arg | Cooldown | LLM? |
|---|---|---|---|---|
| `/report [YYYY-MM-DD]` | Return `daily/<date>/report.md`. Generates on cache-miss for **yesterday** only. | yesterday | 10 min | ✓ Pro tier if generating |
| `/weekly [YYYY-Www]` | Return `reports/weekly/<label>.md`. Generates on cache-miss for the **most recent completed week**. | last Mon–Sun | 30 min | ✓ Pro tier if generating |
| `/digest [Nh\|Nd\|Nw]` | Fresh commit digest over the last window, then dump today's `commits.md`. | `24h` | 1 min | — |
| `/search <query>` | Fixed-string grep over the markdown cache (top 20 hits). `life/credentials/` is unconditionally filtered out of bot results regardless of `never_read_paths`. | — | 5 sec | — |
| `/last [N]` | N most-recent drop headers across `daily/*/drops.md` (default 10, max 50, scans last 14 days). | `10` | 5 sec | — |

All responses are sent as plain text (`parse_mode=None`) and auto-chunk
at 3800 chars — your GitHub-flavored Markdown is preserved but
Telegram won't try to parse `**bold**` or fenced code and crash on it.

### Drop confirmations & approvals

- Any message that isn't a recognised command is treated as a drop
  (same as sharing to Saved Messages). The bot replies with the
  committed path + confidence + atomic-commit size.
- `/approve_<token>` / `/deny_<token>` — one-time MCP / harvest write
  approvals. Tokens are URL-safe 22-char base64 issued by the MCP
  server or the eval harvest loop. TTL ~24h.

### Admin / config

- `/stats` — pipeline health: p50/p95/p99 latency, error rate, top
  target kinds over the last 7 days. Reads `log/pipeline.jsonl`.
- `/start` — one-liner intro.
- `/status` — dumps the markdown README.
- `/done <slug>` / `/revive <slug>` — atomic rename between
  `projects/` and `projects/archive/` plus frontmatter status flip.
- `/config_llm_*` — read/propose LLM config changes; writes are
  approval-gated via `/approve_<token>`. See `src/monogram/bot_config_cmds.py`.
- `/webui` / `/config_webui_*` — same pattern for the web UI mode +
  credentials. See [docs/webui.md](../webui.md).
- `/eval_status` / `/eval_enable` / `/eval_disable` — toggle the
  3-layer eval kill-switch. See [docs/eval.md](../eval.md).

### How this relates to MCP

The bot and the MCP server are two **independent surfaces over the
same markdown**. Both gate on `TELEGRAM_USER_ID` (MCP via the approval
bot). The overlap looks like this:

| What you want | Bot command | MCP tool |
|---|---|---|
| Yesterday's morning brief | `/report` | `get_morning_brief` |
| Current project board | `/status` | `get_board` |
| Specific project state | (n/a — use `/search <slug>`) | `current_project_state` |
| Recent activity | `/last` | `recent_activity` |
| Grep the wiki | `/search <q>` | `search_wiki` |
| Set LLM config | `/config_llm_set` (gated) | `set_llm_config` (gated) |

Use the bot when you're on your phone; use MCP from Claude Desktop /
Cursor when you want conversational follow-ups on the results. Neither
surface can exfiltrate `life/credentials/` — the path is blocked at
both the code level (in `src/monogram/safe_read.py`) and the bot
surface layer (in `src/monogram/bot_report_cmds.py`).

Full MCP tool list + client setup: [docs/setup/mcp-clients.md](mcp-clients.md)
and [docs/mcp.md](../mcp.md).

## Security notes

- The bot token alone cannot read your Saved Messages — that's what
  `TELEGRAM_API_HASH` is for. Protect both equally; either one on its
  own still lets an attacker act in your name.
- The session file (`monogram_session.session`) is the actual auth
  material after `monogram auth`. Anyone with that file can read your
  Telegram account. Permissions default to `0600` on the VM; audit
  them if you copied the file anywhere.
- Only the numeric user ID matching `TELEGRAM_USER_ID` can issue
  commands. If you want to let a second account in (not recommended),
  you'd need to extend the auth gate in `src/monogram/bot.py` — it's
  a single-user design.

## Rotation

If your bot token leaks:

1. In `@BotFather`, `/revoke` → choose the bot → new token issued.
2. Update `TELEGRAM_BOT_TOKEN` in `.env` on every host running Monogram.
3. Restart `monogram run` or the `monogram.service` unit.

The API app (id + hash) doesn't rotate unless you delete and recreate
it at my.telegram.org — not necessary unless it's compromised.
