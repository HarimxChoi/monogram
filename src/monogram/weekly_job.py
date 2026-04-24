"""Phase E — Weekly job, runs Sunday 21:00.

Step 1: Generate weekly report from past 7 daily folders (Mon→Sun).
Step 2: Archival sweep — move oldest complete week past 67 days to raw/.

See docs/vault-layout.md.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import github_store
from .llm import complete
from .models import get_model
from .safe_read import is_blocked, safe_read
from .vault_config import load_vault_config

log = logging.getLogger("monogram.weekly_job")

RETENTION_DAYS = 67
_PRO_CALL_TIMEOUT_SECONDS = 120


def _last_monday(reference: datetime) -> datetime:
    """Return the most recent Monday at 00:00 UTC (last week if today is Sunday)."""
    days_since_monday = reference.weekday()
    if days_since_monday == 0 and reference.hour < 22:
        days_since_monday = 7
    return (reference - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _past_7_days(reference: datetime) -> list[str]:
    """Return the past 7 date strings (Mon→Sun of last complete week)."""
    monday = _last_monday(reference)
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def _iso_week_label(monday: datetime) -> str:
    return monday.strftime("%Y-W%W")


async def generate_weekly_report(lint_section: str = "") -> str | None:
    """Reads past 7 daily folders (last complete Mon→Sun), generates report.

    If `lint_section` is provided, appended verbatim after the LLM body
    (authoritative lint findings survive even if the LLM drifts).
    """
    now = datetime.now(timezone.utc)
    days = _past_7_days(now)
    monday = _last_monday(now)
    week_label = _iso_week_label(monday)
    monday_str = days[0]
    sunday_str = days[6]

    daily_content_parts: list[str] = []
    for day_str in days:
        drops = safe_read(f"daily/{day_str}/drops.md")
        commits = safe_read(f"daily/{day_str}/commits.md")
        if drops or commits:
            part = f"### {day_str}\n"
            if drops:
                part += f"Drops:\n{drops[:1500]}\n"
            if commits:
                part += f"Commits:\n{commits[:1500]}\n"
            daily_content_parts.append(part)

    if not daily_content_parts and not lint_section:
        return None

    context = "\n\n".join(daily_content_parts) if daily_content_parts else "(no daily activity)"

    language = (load_vault_config().primary_language or "en").strip()
    try:
        report = await asyncio.wait_for(
            complete(
                f"Generate a weekly report for {week_label} ({monday_str} to {sunday_str}). "
                f"User's primary language (ISO 639-1): {language}. Write every "
                f"narrative section in this language; keep section headers and "
                f"structural strings in their source form. "
                f"Include: Main themes, Top accomplishments, Lessons that compounded, "
                f"Project status deltas, Upcoming. Add Calendar events section ONLY "
                f"if long-horizon deadlines detected — include Google Calendar add-URLs. "
                f"Keep under 600 words.\n\n{context}",
                model=get_model("high"),
            ),
            timeout=_PRO_CALL_TIMEOUT_SECONDS,
        )
    except Exception as e:
        log.warning("weekly report: Pro call failed (%r); using minimal fallback", e)
        report = "(Pro call unavailable — see daily reports in `daily/YYYY-MM-DD/report.md`)"

    body_parts = [
        f"# Weekly Report — {week_label} ({monday_str} to {sunday_str})",
        "",
        report,
    ]
    if lint_section:
        body_parts.append("")
        body_parts.append(lint_section)

    full_body = "\n".join(body_parts)

    report_path = f"reports/weekly/{week_label}.md"
    ok = github_store.write(
        report_path,
        full_body,
        f"monogram: {report_path}",
    )
    return full_body if ok else None


def _list_daily_folders() -> list[str]:
    """List daily/YYYY-MM-DD/ folders from the repo."""
    repo = github_store._repo()
    try:
        contents = repo.get_contents("daily")
        return sorted(
            [f.name for f in contents if f.type == "dir"],
        )
    except Exception:
        return []


async def archival_sweep() -> list[str]:
    """Move daily folders older than RETENTION_DAYS to raw/.

    Moves one complete Monday→Sunday week at a time (calendar-aligned).
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    folders = _list_daily_folders()
    moved: list[str] = []

    for folder_date_str in folders:
        try:
            folder_date = datetime.strptime(folder_date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

        if folder_date >= cutoff:
            break

        # Read all files in this daily folder and move to raw/.
        # Defense in depth: skip anything credential-adjacent even though
        # daily/ shouldn't contain credentials.
        try:
            repo = github_store._repo()
            contents = repo.get_contents(f"daily/{folder_date_str}")
            for f in contents:
                if f.type != "file":
                    continue
                if is_blocked(f.path):
                    log.warning("archival_sweep: skipping blocked path %s", f.path)
                    continue
                raw_path = f"raw/{folder_date_str}/{f.name}"
                content = f.decoded_content.decode()
                github_store.write(raw_path, content, f"monogram: archive {f.path} → {raw_path}")
                repo.delete_file(f.path, f"monogram: archive sweep — moved to raw/", f.sha)
            moved.append(folder_date_str)
        except Exception as e:
            log.warning("archival_sweep error for %s: %s", folder_date_str, e)

    return moved


async def run_weekly_job(push_to_telegram: bool = True, force: bool = False) -> dict:
    """Execute the full Sunday 21:00 job.

    `force=True` runs regardless of weekday (useful for manual catch-up).
    """
    from .runlog import log_run

    now = datetime.now(timezone.utc)
    if not force and now.weekday() != 6:
        log.info("weekly job: skipping, today is %s not Sunday", now.strftime('%A'))
        return {"report_generated": False, "folders_archived": [], "skipped": True}

    with log_run("weekly") as status:
        summary: dict = {
            "report_generated": False,
            "folders_archived": [],
            "report_pushed": False,
            "lint": "",
        }

        # Lint FIRST — so report can include health-check section, and
        # self-healing writes (index regen, confidence decay) are committed
        # before the report references them.
        from .wiki_lint import format_lint_section, run_lint

        lint_report = run_lint()
        summary["lint"] = lint_report.summary()
        if lint_report.writes:
            github_store.write_atomic(
                lint_report.writes,
                "monogram weekly lint: decay + index regeneration",
            )
        lint_section = format_lint_section(lint_report)

        report = await generate_weekly_report(lint_section=lint_section)
        summary["report_generated"] = report is not None

        archived = await archival_sweep()
        summary["folders_archived"] = archived

        if report and push_to_telegram:
            try:
                from .bot import push_text
                monday = _last_monday(now)
                week_label = _iso_week_label(monday)
                await push_text(f"📅 Weekly report — {week_label}\n\n{report}")
                summary["report_pushed"] = True
            except Exception as e:
                summary["push_error"] = f"{type(e).__name__}: {e}"

        for k, v in summary.items():
            status[k] = v
        return summary
