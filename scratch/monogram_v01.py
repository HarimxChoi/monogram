"""
Monogram v0.1
Your personal mark on everything you build and learn.

Input:  Telegram Saved Messages (passive drops)
        Bot chat (intentional interaction)
Brain:  Gemini 2.0 Flash (free)
Store:  GitHub repo via PAT (markdown files)
Output: Telegram bot reply

Setup:
    pip install telethon aiogram google-generativeai PyGithub python-dotenv
    cp .env.example .env && fill in values
    python monogram.py
"""

import asyncio
import os
import json
import re
from datetime import datetime
from dotenv import load_dotenv

from telethon import TelegramClient, events
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import google.generativeai as genai
from github import Github

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_API_ID     = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH   = os.environ["TELEGRAM_API_HASH"]
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_USER_ID    = int(os.environ["TELEGRAM_USER_ID"])   # your numeric ID
GEMINI_API_KEY      = os.environ["GEMINI_API_KEY"]
GITHUB_PAT          = os.environ["GITHUB_PAT"]
GITHUB_REPO         = os.environ["GITHUB_REPO"]             # e.g. example-org/mono

genai.configure(api_key=GEMINI_API_KEY)
model  = genai.GenerativeModel("gemini-2.0-flash")
gh     = Github(GITHUB_PAT)
repo   = gh.get_repo(GITHUB_REPO)
bot    = Bot(token=TELEGRAM_BOT_TOKEN)
dp     = Dispatcher()

# ── GitHub helpers ────────────────────────────────────────────────────────────

def gh_read(path: str) -> str:
    """Read file from GitHub repo. Returns empty string if not found."""
    try:
        return repo.get_contents(path).decoded_content.decode()
    except Exception:
        return ""

def gh_write(path: str, content: str, message: str) -> bool:
    """Create or update file in GitHub repo."""
    try:
        try:
            existing = repo.get_contents(path)
            repo.update_file(path, message, content, existing.sha)
        except Exception:
            repo.create_file(path, message, content)
        return True
    except Exception as e:
        print(f"gh_write error: {e}")
        return False

def gh_append(path: str, line: str, commit_msg: str) -> bool:
    """Append a line to an existing file, or create it."""
    current = gh_read(path)
    updated = current + "\n" + line if current else line
    return gh_write(path, updated, commit_msg)

# ── Gemini router ─────────────────────────────────────────────────────────────

ROUTER_PROMPT = """
You are Monogram, a personal agent. Classify the following message and respond ONLY with valid JSON.

Message: {message}

Current date: {date}

Scheduler context (recent):
{context}

Classify into exactly one of:
- "schedule" — task update, deadline, decision, project status
- "wiki"     — link, paper, technical content, something to learn/store
- "query"    — question asking for information from scheduler or wiki
- "log"      — personal note, thought, not clearly schedule or wiki

Response format:
{{
  "type": "schedule|wiki|query|log",
  "summary": "one line summary of what this is",
  "action": "what to do with it",
  "target_file": "which file in the repo to update (e.g. projects/sp-sac.md or wiki/ML-Uncertainty/calibration.md or log/2026-04.md)",
  "content": "the markdown content to append or update"
}}
"""

QUERY_PROMPT = """
You are Monogram. Answer the question based on the scheduler context below.
Be direct and concise. Use markdown for structure if needed.

Question: {question}

Context:
{context}
"""

def route(message: str) -> dict:
    """Classify message and determine action via Gemini."""
    context = gh_read("README.md")[:2000]
    prompt  = ROUTER_PROMPT.format(
        message=message,
        date=datetime.now().strftime("%Y-%m-%d"),
        context=context
    )
    response = model.generate_content(prompt)
    text = response.text.strip()
    # strip markdown fences if present
    text = re.sub(r"^```json\s*|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        return {
            "type": "log",
            "summary": message[:80],
            "action": "log raw message",
            "target_file": f"log/{datetime.now().strftime('%Y-%m')}.md",
            "content": f"- {datetime.now().strftime('%Y-%m-%d %H:%M')} — {message}"
        }

def answer_query(question: str) -> str:
    """Answer a question using scheduler repo context."""
    # pull key files for context
    files = ["README.md", "projects/sp-sac.md", "projects/wivision.md",
             "projects/votinglab.md", "projects/golf-ai.md", "projects/sejong.md"]
    context = ""
    for f in files:
        content = gh_read(f)
        if content:
            context += f"\n\n### {f}\n{content[:800]}"

    prompt = QUERY_PROMPT.format(question=question, context=context)
    return model.generate_content(prompt).text.strip()

# ── Process a drop (Saved Messages) ──────────────────────────────────────────

async def process_drop(text: str) -> str:
    """Route and store a Saved Messages drop. Returns confirmation message."""
    result = route(text)

    if result["type"] == "query":
        return answer_query(text)

    # build log entry
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## {timestamp}\n{result['content']}\n"

    target = result.get("target_file", f"log/{datetime.now().strftime('%Y-%m')}.md")
    commit = f"monogram: {result['summary'][:60]}"
    ok = gh_append(target, entry, commit)

    if ok:
        emoji = {"schedule": "📅", "wiki": "📚", "log": "📝"}.get(result["type"], "✅")
        return f"{emoji} `{target}`\n_{result['summary']}_"
    else:
        return f"⚠️ Failed to write to `{target}`"

# ── Telethon — watches Saved Messages ────────────────────────────────────────

async def run_listener():
    client = TelegramClient("monogram_session", TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()
    print("✅ Telethon listener started — watching Saved Messages")

    @client.on(events.NewMessage(outgoing=True, func=lambda e: e.is_private and e.out))
    async def saved_message_handler(event):
        # Saved Messages = messages you send to yourself
        me = await client.get_me()
        if event.peer_id.user_id != me.id:
            return

        text = event.raw_text or ""
        if not text:
            return  # TODO: handle media/voice in v0.3

        print(f"📥 Drop: {text[:80]}")
        reply = await process_drop(text)
        await bot.send_message(TELEGRAM_USER_ID, reply, parse_mode="Markdown")

    await client.run_until_disconnected()

# ── aiogram — bot chat (intentional interaction) ──────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Monogram online.\n\n"
        "Drop anything in your *Saved Messages* — links, thoughts, voice notes.\n"
        "Talk to me here for queries and updates.\n\n"
        "Try: `what's due this week?`",
        parse_mode="Markdown"
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    answer = answer_query("What's the current status of all projects? What's due soon?")
    await message.answer(answer, parse_mode="Markdown")

@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    answer = answer_query(
        f"Today is {datetime.now().strftime('%Y-%m-%d %A')}. "
        "What's the one most important thing to focus on today based on deadlines and project state?"
    )
    await message.answer(answer, parse_mode="Markdown")

@dp.message()
async def handle_bot_message(message: types.Message):
    """Handle all direct bot chat messages."""
    if message.from_user.id != TELEGRAM_USER_ID:
        return  # ignore others

    text = message.text or ""
    print(f"💬 Bot chat: {text[:80]}")

    result = route(text)

    if result["type"] == "query":
        reply = answer_query(text)
        await message.answer(reply, parse_mode="Markdown")
        return

    # non-query: process as drop
    reply = await process_drop(text)
    await message.answer(reply, parse_mode="Markdown")

async def run_bot():
    print("✅ Bot started — listening for commands")
    await dp.start_polling(bot)

# ── Proactive cron — morning brief (v0.2 preview) ────────────────────────────

async def morning_brief():
    """Runs daily at 9am. Reads scheduler, sends brief to bot chat."""
    while True:
        now = datetime.now()
        # wait until 9am
        seconds_until_9 = ((9 - now.hour) * 3600 - now.minute * 60 - now.second) % 86400
        await asyncio.sleep(seconds_until_9)

        print("⏰ Generating morning brief")
        brief = answer_query(
            f"Today is {datetime.now().strftime('%Y-%m-%d %A')}. "
            "Generate a morning brief covering: "
            "1) What's critical today (deadlines within 14 days), "
            "2) What's at risk (no recent activity), "
            "3) One recommended focus for today. "
            "Keep it under 200 words. Use emojis for status."
        )
        await bot.send_message(TELEGRAM_USER_ID, f"☀️ *Morning Brief*\n\n{brief}", parse_mode="Markdown")
        await asyncio.sleep(60)  # prevent double-fire

# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    print("🚀 Monogram v0.1 starting...")
    await asyncio.gather(
        run_listener(),
        run_bot(),
        morning_brief(),
    )

if __name__ == "__main__":
    asyncio.run(main())
