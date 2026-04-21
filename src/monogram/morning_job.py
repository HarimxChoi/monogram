"""Morning job — runs at 08:00 daily.

Step 1: Per-project update from yesterday's commits (N commits)
Step 2: board.md update-not-regenerate (1 commit)
Step 3: Morning brief (v0.3b: single batched Pro call, board-style, localized)
Step 4: Push brief to Telegram via bot.push_text

See docs/vault-layout.md.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

log = logging.getLogger("monogram.morning_job")

_PRO_CALL_TIMEOUT_SECONDS = 120

from . import github_store
from .calendar_url import build_calendar_url
from .llm import complete, extract as llm_extract
from .models import get_model
from .safe_read import safe_read
from .vault_config import load_vault_config

_STATUS_INACTIVE_DAYS = 10


def _yesterday_str() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _list_project_files() -> list[str]:
    """List projects/*.md files from the repo."""
    repo = github_store._repo()
    try:
        contents = repo.get_contents("projects")
        return [
            f.path
            for f in contents
            if f.path.endswith(".md") and f.type == "file"
        ]
    except Exception:
        return []


def _infer_status(existing_content: str, commits_for_project: str) -> str:
    """Infer active/inactive/done from content + recent commits."""
    meta, _ = github_store.parse_metadata(existing_content)
    current = meta.get("status", "active")
    if current == "done":
        return "done"
    if commits_for_project.strip():
        return "active"
    last_match = re.search(r"last updated (\d{4}-\d{2}-\d{2})", existing_content)
    if last_match:
        try:
            last = datetime.strptime(last_match.group(1), "%Y-%m-%d")
            days = (datetime.now(timezone.utc).replace(tzinfo=None) - last).days
            if days >= _STATUS_INACTIVE_DAYS:
                return "inactive"
        except ValueError:
            pass
    return current


async def update_project_from_commits(
    project_path: str,
    commits_summary: str,
) -> bool:
    """Rewrite the AUTO sections of a project file from commit data."""
    existing = safe_read(project_path)
    if not existing:
        return False

    status = _infer_status(existing, commits_summary)
    today = _today_str()
    lines = existing.split("\n")
    new_lines: list[str] = []
    in_auto_section = False

    for line in lines:
        if line.startswith("## Status"):
            in_auto_section = True
            new_lines.append("## Status")
            new_lines.append(f"{status} — last updated {today} 08:00")
            new_lines.append("")
            continue
        elif line.startswith("## Recent activity"):
            in_auto_section = True
            new_lines.append("## Recent activity")
            if commits_summary.strip():
                new_lines.append(commits_summary)
            else:
                new_lines.append("(no commits yesterday)")
            new_lines.append("")
            continue

        if in_auto_section:
            if line.startswith("## "):
                in_auto_section = False
                new_lines.append(line)
            continue

        new_lines.append(line)

    updated = "\n".join(new_lines)
    if updated.strip() == existing.strip():
        return False

    return github_store.write(
        project_path,
        updated,
        f"monogram: {project_path.split('/')[-1]} — morning update",
    )


def _parse_board_sections(content: str) -> dict[str, list[str]]:
    """Parse board.md into {section_name: [lines]}."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in content.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return sections


def _update_board_line(
    lines: list[str], name: str, path: str, summary: str
) -> list[str]:
    """Update a single project line in a board section, or append."""
    new_line = f"- [{name}]({path}) — {summary}"
    for i, line in enumerate(lines):
        if f"[{name}]" in line:
            lines[i] = new_line
            return lines
    lines.append(new_line)
    return lines


async def update_board(
    projects: list[dict],
) -> bool:
    """Update (not regenerate) board.md.

    Parses existing board, updates lines per project, preserves
    unmentioned content. Follows the MEMORY.md update-not-regenerate pattern.
    """
    existing = safe_read("board.md")

    if not existing:
        # First run: generate from scratch
        lines = [f"# Board — {_today_str()}", ""]
        for section_name in ("Active", "Inactive", "Done"):
            group = [p for p in projects if p["status"] == section_name.lower()]
            if group:
                lines.append(f"## {section_name}")
                for p in group:
                    lines.append(
                        f"- [{p['name']}]({p['path']}) — {p['summary']}"
                    )
                lines.append("")
        content = "\n".join(lines)
    else:
        # Update existing: parse sections, move/update lines
        header_line = existing.split("\n")[0]
        updated_header = re.sub(
            r"\d{4}-\d{2}-\d{2}", _today_str(), header_line
        )

        sections = _parse_board_sections(existing)
        for section in ("Active", "Inactive", "Done"):
            if section not in sections:
                sections[section] = []

        for p in projects:
            target_section = p["status"].capitalize()
            # Remove from all sections first
            for sec_name, sec_lines in sections.items():
                sections[sec_name] = [
                    l for l in sec_lines if f"[{p['name']}]" not in l
                ]
            # Add to correct section
            _update_board_line(
                sections[target_section], p["name"], p["path"], p["summary"]
            )

        lines = [updated_header, ""]
        for sec in ("Active", "Inactive", "Done"):
            clean = [l for l in sections.get(sec, []) if l.strip()]
            if clean:
                lines.append(f"## {sec}")
                lines.extend(clean)
                lines.append("")
        content = "\n".join(lines)

    return github_store.write(
        "board.md",
        content,
        f"monogram: board.md — morning update",
    )


# ── v0.3b: board-style brief via single batched Pro call ────────────────


@dataclass
class ProjectSnapshot:
    slug: str
    path: str
    frontmatter: dict
    body_excerpt: str
    recent_commits: list[str] = field(default_factory=list)


@dataclass
class LifeSnapshot:
    area: str
    entries: list[tuple[str, str]]  # (timestamp_str, title)


@dataclass
class WikiSnapshot:
    slug: str
    summary: str
    tags: list[str]


@dataclass
class MorningContext:
    yesterday: str
    projects: list[ProjectSnapshot] = field(default_factory=list)
    life: list[LifeSnapshot] = field(default_factory=list)
    wiki_new: list[WikiSnapshot] = field(default_factory=list)


_LIFE_ENTRY_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) — (.+)$",
    re.MULTILINE,
)

_WIKI_INDEX_RE = re.compile(
    r"^- \[\[([a-z0-9-]+)\]\] — (.+?) \[(.*?)\] \((\d{4}-\d{2}-\d{2})\)$",
    re.MULTILINE,
)


def _parse_life_entries(content: str, since_iso: str) -> list[tuple[str, str]]:
    """Parse timestamped H3 headers; return entries at-or-after since_iso."""
    results = []
    for m in _LIFE_ENTRY_RE.finditer(content):
        ts_iso = f"{m.group(1)}T{m.group(2)}:00"
        if ts_iso >= since_iso:
            results.append((f"{m.group(1)} {m.group(2)}", m.group(3).strip()))
    return results


def _collect_life_snapshots(yesterday: str) -> list[LifeSnapshot]:
    """Collect yesterday's life entries per category. Credentials are
    UNCONDITIONALLY skipped (defense in depth)."""
    cfg = load_vault_config()
    since_iso = f"{yesterday}T00:00:00"
    snapshots = []
    for area in cfg.life_categories:
        if area == "credentials":
            continue
        content = safe_read(f"life/{area}.md")
        if not content:
            continue
        entries = _parse_life_entries(content, since_iso)
        if entries:
            snapshots.append(LifeSnapshot(area=area, entries=entries))
    return snapshots


def _collect_wiki_snapshots(yesterday: str) -> list[WikiSnapshot]:
    """Scan wiki/index.md for entries dated yesterday (fast, no repo walk)."""
    index_content = safe_read("wiki/index.md")
    if not index_content:
        return []
    out = []
    for m in _WIKI_INDEX_RE.finditer(index_content):
        slug, summary, tags_str, date_str = m.groups()
        if date_str == yesterday:
            tags = [t.lstrip("#") for t in tags_str.split() if t]
            out.append(WikiSnapshot(slug=slug, summary=summary, tags=tags))
    return out


def _collect_project_snapshots(yesterday: str) -> list[ProjectSnapshot]:
    """All project files + yesterday's attributed commits + body/frontmatter."""
    project_files = _list_project_files()
    commits_content = safe_read(f"daily/{yesterday}/commits.md")
    snapshots = []
    for pf in project_files:
        content = safe_read(pf)
        if not content:
            continue
        frontmatter, body = github_store.parse_metadata(content)
        slug = pf.rsplit("/", 1)[-1].replace(".md", "")
        recent = _commits_for_project(pf, commits_content)
        recent_lines = [ln for ln in recent.split("\n") if ln.strip()][:10]
        snapshots.append(ProjectSnapshot(
            slug=slug,
            path=pf,
            frontmatter=frontmatter or {},
            body_excerpt=(body or "")[:2000],
            recent_commits=recent_lines,
        ))
    return snapshots


def _collect_morning_context(yesterday: str) -> MorningContext:
    return MorningContext(
        yesterday=yesterday,
        projects=_collect_project_snapshots(yesterday),
        life=_collect_life_snapshots(yesterday),
        wiki_new=_collect_wiki_snapshots(yesterday),
    )


# ── Structured output schema for the Pro call ──


class ProjectBoardEntry(BaseModel):
    slug: str
    badge: str = Field(
        description='e.g. "[active • D-12]", "[inactive • 14 days]", "[done]"',
    )
    current_state: str = Field(
        description="One sentence on where the project stands. In user's primary language.",
    )
    next_step: str = Field(
        description="One sentence on what is most important now.",
    )
    recent_activity: str = Field(
        description="1-2 sentences summarizing yesterday's commits.",
    )


class LifeBriefEntry(BaseModel):
    area: str
    count: int
    titles: list[str] = Field(default_factory=list)


class CalendarEvent(BaseModel):
    title: str
    when: str = Field(description="Natural-language date/time OR ISO date.")
    iso_start: str | None = Field(
        default=None,
        description="ISO 8601 start (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ) for URL builder.",
    )


class MorningBriefData(BaseModel):
    projects: list[ProjectBoardEntry] = Field(default_factory=list)
    life: list[LifeBriefEntry] = Field(default_factory=list)
    new_knowledge_summary: str = ""
    calendar: list[CalendarEvent] = Field(default_factory=list)


def _build_brief_prompt(ctx: MorningContext) -> str:
    parts = [f"Generate a morning brief for {ctx.yesterday} (previous day).", ""]

    if ctx.projects:
        parts.append("## Projects")
        for p in ctx.projects:
            fm = p.frontmatter
            parts.append(f"### {p.slug}")
            parts.append(f"- status: {fm.get('status', 'active')}")
            if fm.get("deadline"):
                parts.append(f"- deadline: {fm['deadline']}")
            if fm.get("tags"):
                parts.append(f"- tags: {', '.join(fm['tags'])}")
            if p.body_excerpt:
                parts.append(f"\nBody excerpt:\n{p.body_excerpt}\n")
            if p.recent_commits:
                parts.append("Yesterday's commits:")
                parts.extend(f"  {c}" for c in p.recent_commits)
            parts.append("")

    if ctx.life:
        parts.append("## Life entries added yesterday")
        for l in ctx.life:
            parts.append(f"### {l.area}")
            for ts, title in l.entries:
                parts.append(f"- {ts}: {title}")
        parts.append("")

    if ctx.wiki_new:
        parts.append("## New wiki entries yesterday")
        for w in ctx.wiki_new:
            tag_str = " ".join(f"#{t}" for t in w.tags)
            parts.append(f"- {w.slug} — {w.summary} [{tag_str}]")
        parts.append("")

    parts.append("""
Produce a MorningBriefData JSON object. Every narrative field should
read like a careful colleague who actually looked at the commits and
project bodies — not a vague status-meeting summary.

## Order projects before emitting

Sort projects with this priority before adding them to the output:
  1. overdue deadlines (deadline < today)
  2. deadlines within 7 days, nearest first
  3. projects with new activity yesterday, by commit count desc
  4. everything else, alphabetical by slug

## ProjectBoardEntry (one per project above)

- `slug`: exact lowercase kebab-case slug.
- `badge` (English, structural — these strings are parsed downstream):
    "[active • D-N]" when status=active AND deadline exists (N = days
        until; negative = overdue → e.g. "[active • D+3 overdue]").
    "[active]" when status=active AND no deadline.
    "[inactive • N days]" when status=inactive (N = days since last
        activity; use "N+ days" if unknown).
    "[done]" when status=done.
- `current_state` (NARRATIVE, user's primary language, ONE sentence):
    Concrete. Name a specific component, file area, or feature that
    moved — not the project name restated. Bad: "작업 중". Good:
    "인증 리팩터링이 절반 진행됐고, 세션 검증 부분만 남음".
- `next_step` (NARRATIVE, ONE sentence):
    The FIRST concrete action a reader would take if they opened the
    project today. Bad: "테스트 추가". Good: "session.invalidate() 에
    대한 unit test 작성해서 CI 통과시키기".
- `recent_activity` (NARRATIVE, 1–2 sentences):
    Synthesize yesterday's commits into intent, not a restatement.
    Pull concrete nouns (file names, feature names) from the commit
    subjects. Bad: "진전이 있었음". Good: "listener 가 PDF/HWP 첨부까지
    처리하도록 디스패처가 들어왔고, 봇 쪽도 같은 분기로 맞췄음".
    If no commits, write "(어제 커밋 없음)" (or equivalent in the user's
    language).

Never write: "made progress", "진전이 있었음", "작업 중", or any
variant of "continued working on X". If you can't say something
specific, say nothing — empty strings are preferable to filler.

## LifeBriefEntry (one per life category with entries)

- `area`: exact category name (lowercase English).
- `count`: len(entries).
- `titles`: copy each title VERBATIM — they are already in the user's
  language, never paraphrase.

## new_knowledge_summary (NARRATIVE, 1–2 sentences)

What new wiki entries teach, connected when possible. Bad: "새 글이
2개 추가됨". Good: "RTMPose 와 MediaPipe Pose 가 추가 — 둘 다 실시간
포즈 추정이지만 RTMPose 가 V100 기준 ~500 FPS 로 훨씬 빠름". Empty
string if no new wiki entries.

## calendar

For each project with a deadline within 7 days of today, add a
CalendarEvent with title="<slug>: deadline", when=<human-readable>,
iso_start=<ISO 8601 YYYY-MM-DD>. Skip past deadlines.

Return ONLY valid JSON matching MorningBriefData. No preamble, no
trailing prose.
""")
    return "\n".join(parts)


def _render_morning_brief(
    yesterday: str, data: MorningBriefData
) -> str:
    """Render structured brief → markdown. Content already in user's language."""
    lines = [f"# Morning brief — {yesterday}", ""]

    if data.projects:
        lines.append("## Projects")
        lines.append("")
        for p in data.projects:
            lines.append(f"### {p.slug} {p.badge}")
            lines.append(p.current_state)
            lines.append("")
            lines.append(f"**Next:** {p.next_step}")
            lines.append("")
            lines.append(f"_Recent:_ {p.recent_activity}")
            lines.append("")

    if data.life:
        lines.append("## Life updates")
        lines.append("")
        for l in data.life:
            lines.append(f"**{l.area}** ({l.count})")
            for t in l.titles:
                lines.append(f"  • {t}")
            lines.append("")

    if data.new_knowledge_summary:
        lines.append("## New knowledge")
        lines.append("")
        lines.append(data.new_knowledge_summary)
        lines.append("")

    if data.calendar:
        lines.append("## Calendar")
        lines.append("")
        for ev in data.calendar:
            start = ev.iso_start or ev.when
            try:
                url = build_calendar_url(ev.title, start, start)
            except Exception:
                url = ""
            if url:
                lines.append(f"- **{ev.title}** — {ev.when}  [Add to Calendar]({url})")
            else:
                lines.append(f"- **{ev.title}** — {ev.when}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


async def generate_morning_brief(yesterday: str) -> str | None:
    """Generate the board-style morning brief via a single Pro call."""
    ctx = _collect_morning_context(yesterday)

    if not ctx.projects and not ctx.life and not ctx.wiki_new:
        return None

    prompt = _build_brief_prompt(ctx)
    try:
        # Timeout guard — without this a hung Pro call would block the
        # whole morning job indefinitely (cron has no upstream timeout).
        brief_data = await asyncio.wait_for(
            llm_extract(
                prompt=prompt,
                schema=MorningBriefData,
                system="You are Monogram's morning brief generator. Produce structured output.",
                model=get_model("high"),
            ),
            timeout=_PRO_CALL_TIMEOUT_SECONDS,
        )
    except Exception as e:
        # Pro can 429, rate-limit, or time out; fall back to a minimal
        # English brief rather than dropping the day's summary entirely.
        log.warning(
            "morning brief: Pro call failed (%r); falling back to minimal brief", e,
        )
        brief_data = MorningBriefData(
            projects=[
                ProjectBoardEntry(
                    slug=p.slug,
                    badge=f"[{p.frontmatter.get('status', 'active')}]",
                    current_state="(Pro call unavailable — see project file)",
                    next_step="(Pro call unavailable)",
                    recent_activity=" / ".join(p.recent_commits[:3]) or "(no commits)",
                )
                for p in ctx.projects
            ],
            life=[
                LifeBriefEntry(
                    area=l.area,
                    count=len(l.entries),
                    titles=[t for _, t in l.entries],
                )
                for l in ctx.life
            ],
        )

    rendered = _render_morning_brief(yesterday, brief_data)

    # v0.6: append /webui footer when web UI is enabled
    vcfg = load_vault_config()
    if vcfg.webui_mode != "mcp-only":
        rendered = rendered.rstrip() + "\n\n—\nDashboard: /webui\n"

    report_path = f"daily/{yesterday}/report.md"
    ok = github_store.write(
        report_path,
        rendered,
        f"monogram: {report_path} — morning brief",
    )
    return rendered if ok else None


async def run_morning_job(push_to_telegram: bool = True) -> dict:
    """Execute the full morning job, commit outputs, optionally push brief."""
    from .runlog import log_run

    yesterday = _yesterday_str()
    with log_run("morning") as status:
        summary = {
            "projects_updated": 0,
            "board_updated": False,
            "brief_generated": False,
            "brief_pushed": False,
            "yesterday": yesterday,
        }

        # Step 1: per-project updates from yesterday's commits
        project_files = _list_project_files()
        commits_content = safe_read(f"daily/{yesterday}/commits.md")
        project_states: list[dict] = []

        for pf in project_files:
            name = pf.split("/")[-1].replace(".md", "")
            project_commits = _commits_for_project(pf, commits_content)

            updated = await update_project_from_commits(pf, project_commits)
            if updated:
                summary["projects_updated"] += 1

            existing = safe_read(pf)
            proj_status = _infer_status(existing, project_commits)
            project_states.append({
                "name": name,
                "path": pf,
                "status": proj_status,
                "summary": f"{proj_status} — last updated {_today_str()}",
            })

        # Step 2: board update (update-not-regenerate)
        if project_states:
            summary["board_updated"] = await update_board(project_states)

        # Step 3: morning brief
        brief = await generate_morning_brief(yesterday)
        summary["brief_generated"] = brief is not None

        # Step 4: push brief to Telegram (v0.2.4)
        if brief and push_to_telegram:
            try:
                from .bot import push_text
                header = f"🌅 Morning brief — {yesterday}\n\n"
                await push_text(header + brief)
                summary["brief_pushed"] = True
            except Exception as e:
                # Don't fail the whole job if Telegram is unreachable —
                # the brief is already committed to git.
                summary["push_error"] = f"{type(e).__name__}: {e}"

        # Propagate all counters into the runlog for observability
        for k, v in summary.items():
            status[k] = v
        return summary


def _commits_for_project(project_path: str, commits_content: str) -> str:
    """Filter commit digest lines to those relevant to `project_path`.

    v0.2.6: prefer `github_repos:` frontmatter mapping when available,
    fall back to substring match on project slug.
    """
    if not commits_content:
        return ""

    watched_repos = _project_watched_repos(project_path)
    slug = project_path.split("/")[-1].replace(".md", "").lower()

    hits: list[str] = []
    for line in commits_content.split("\n"):
        low = line.lower()
        # Strong match: explicit repo mapping
        if watched_repos and any(r.lower() in low for r in watched_repos):
            hits.append(line)
            continue
        # Weak fallback: project slug in line
        if not watched_repos and slug in low:
            hits.append(line)
    return "\n".join(hits)


def _project_watched_repos(project_path: str) -> list[str]:
    """Extract `github_repos:` from the project's YAML frontmatter."""
    content = safe_read(project_path)
    if not content.startswith("---"):
        return []
    try:
        end = content.index("---", 3)
        frontmatter = content[3:end]
    except ValueError:
        return []
    m = re.search(r"^github_repos:\s*\[([^\]]*)\]", frontmatter, re.MULTILINE)
    if not m:
        return []
    return [r.strip().strip("'\"") for r in m.group(1).split(",") if r.strip()]
