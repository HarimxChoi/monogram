"""Weekly job, runs Sunday 21:00."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import github_store
from .llm import complete
from .models import get_model
from .safe_read import is_blocked, safe_read

log = logging.getLogger("monogram.weekly_job")

RETENTION_DAYS = 67


def _last_monday(reference: datetime) -> datetime:
    days_since_monday = reference.weekday()
    if days_since_monday == 0 and reference.hour < 22:
        days_since_monday = 7
    return (reference - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _past_7_days(reference: datetime) -> list[str]:
    monday = _last_monday(reference)
    return [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]


def _iso_week_label(monday: datetime) -> str:
    return monday.strftime("%Y-W%W")


async def generate_weekly_report(lint_section: str = "") -> str | None:
    """lint_section appended verbatim so authoritative findings survive LLM drift."""
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

    try:
        report = await complete(
            f"Generate a weekly report for {week_label} ({monday_str} to {sunday_str}). "
            f"Include: Main themes, Top accomplishments, Lessons that compounded, "
            f"Project status deltas, Upcoming. Add Calendar events section ONLY "
            f"if long-horizon deadlines detected — include Google Calendar add-URLs. "
            f"Keep under 600 words.\n\n{context}",
            model=get_model("high"),
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
    repo = github_store._repo()
    try:
        contents = repo.get_contents("daily")
        return sorted(
            [f.name for f in contents if f.type == "dir"],
        )
    except Exception:
        return []


async def archival_sweep() -> list[str]:
    """Move daily folders older than RETENTION_DAYS to raw/, one full week at a time."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    try:
        folders = _list_daily_folders()
    except Exception as e:
        log.warning("archival_sweep: cannot list daily folders (%s); skipping", e)
        return []
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

        try:
            repo = github_store._repo()
            contents = repo.get_contents(f"daily/{folder_date_str}")
            for f in contents:
                if f.type != "file":
                    continue
                if is_blocked(f.path):
                    log.info("archival_sweep: skipping blocked path %s", f.path)
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
    """`force=True` runs regardless of weekday (manual catch-up)."""
    from .runlog import log_run

    now = datetime.now(timezone.utc)
    if not force and now.weekday() != 6:
        log.info("weekly job: skipping, today is %s not Sunday", now.strftime("%A"))
        return {"report_generated": False, "folders_archived": [], "skipped": True}

    with log_run("weekly") as status:
        summary: dict = {
            "report_generated": False,
            "folders_archived": [],
            "report_pushed": False,
            "lint": "",
        }

        # Lint first so self-healing writes land before the report references them.
        from .wiki_lint import format_lint_section, run_lint

        lint_report = run_lint()
        summary["lint"] = lint_report.summary()
        if lint_report.writes:
            github_store.write_multi(
                lint_report.writes,
                "monogram weekly lint: decay + index regeneration",
            )
        lint_section = format_lint_section(lint_report)

        report = await generate_weekly_report(lint_section=lint_section)
        summary["report_generated"] = report is not None

        archived = await archival_sweep()
        summary["folders_archived"] = archived

        # Skip push (not commit) if this week was already pushed — same double-fire guard as morning.
        marker = "log/last-weekly-push"
        week_label = _iso_week_label(_last_monday(now))
        already_pushed = safe_read(marker).strip() == week_label
        if report and push_to_telegram and not already_pushed:
            try:
                from .bot import push_text
                await push_text(f"📅 Weekly report — {week_label}\n\n{report}")
                summary["report_pushed"] = True
                github_store.write(marker, week_label, f"monogram: weekly push marker {week_label}")
            except Exception as e:
                summary["push_error"] = f"{type(e).__name__}: {e}"

        for k, v in summary.items():
            status[k] = v
        return summary
