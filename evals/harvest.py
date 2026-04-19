"""Track A — harvest production drops into fixtures, with approval gate.

Flow:
  1. Read log/pipeline.jsonl from vault; filter to high-confidence,
     verifier-approved, non-credential, non-escalated, non-duplicate.
  2. For each row, fetch drop text from daily/<date>/drops.md by drop_id.
  3. Anonymize via anonymizer.scrub(). Skip rows where ResidualPII.
  4. Write dated audit copy to evals/fixtures/harvested-<date>.jsonl.
  5. Run replay eval against existing _accepted.jsonl. Halt on safety
     regression (two-layer halt rule, §6.5).
  6. On pass: write `.monogram/harvest-pending/<token>.json` + push
     approval message to Telegram.

On `/approve_<token>` (handled by bot.py), append to _accepted.jsonl.
On `/deny_<token>`, discard.

Scheduled cron: Sun + Wed 03:00 KST (18:00 UTC Sun + Wed).
Kill-switch: checked at entry; exits 0 if disabled.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from evals.anonymizer import ResidualPII, scrub
from evals.kill_switch import is_eval_enabled

log = logging.getLogger("monogram.evals.harvest")


_PIPELINE_LOG = "log/pipeline.jsonl"
_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_ACCEPTED_PATH = _FIXTURES_DIR / "_accepted.jsonl"
_HARVEST_PENDING_DIR = ".monogram/harvest-pending"
_HARVEST_TTL_SECONDS = 24 * 3600  # 24 hours


@dataclass
class HarvestedFixture:
    id: str
    category: str                  # "harvested" (vs "seed")
    source_harvest_id: str         # e.g. "2026-04-26"
    source_drop_id: str            # 12-char hash from pipeline.jsonl
    input: dict
    expected: dict
    harvested_at: str


# ── Filter criteria ───────────────────────────────────────────────────

def _should_harvest(row: dict) -> bool:
    """Filter pipeline.jsonl rows eligible for harvest."""
    if not row.get("verifier_ok"):
        return False
    if row.get("target_confidence") != "high":
        return False
    if row.get("escalated"):
        return False
    if row.get("target_kind") == "credential":
        return False  # NEVER harvest credentials
    if not row.get("drop_id") or not row.get("target_kind"):
        return False
    return True


def _load_pipeline_log(since_days: int) -> list[dict]:
    """Read log/pipeline.jsonl from the vault; filter by age."""
    from monogram import github_store

    content = github_store.read(_PIPELINE_LOG)
    if not content:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    out: list[dict] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = row.get("ts", "")
        if not ts:
            continue
        try:
            if datetime.fromisoformat(ts) < cutoff:
                continue
        except ValueError:
            continue
        out.append(row)
    return out


def _load_accepted_ids() -> set[str]:
    """IDs of source drops already harvested — for dedup."""
    if not _ACCEPTED_PATH.exists():
        return set()
    out: set[str] = set()
    for line in _ACCEPTED_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        did = d.get("source_drop_id")
        if did:
            out.add(did)
    return out


def _fetch_drop_text(row: dict) -> str | None:
    """Reconstruct the drop text from daily/<date>/drops.md.

    pipeline.jsonl stores drop_id (hash) + ts; the drop text lives in
    daily/<YYYY-MM-DD>/drops.md as an H3 block. We grep for drop_id
    in the metadata comment after each header.
    """
    from monogram import github_store

    ts = row.get("ts", "")
    drop_id = row.get("drop_id", "")
    if not ts or not drop_id:
        return None
    date = ts.split("T", 1)[0]
    path = f"daily/{date}/drops.md"
    content = github_store.read(path)
    if not content:
        return None

    # Blocks are separated by "## HH:MM" headers.  Each block may carry
    # a drop_id comment. Match and return the block body.
    pattern = re.compile(
        rf"(## \d{{2}}:\d{{2}}[^\n]*\n.*?drop_id:\s*{re.escape(drop_id)}.*?)(?=\n## |\Z)",
        re.DOTALL,
    )
    m = pattern.search(content)
    if not m:
        return None
    block = m.group(1)
    # Strip the header + metadata line; keep the body.
    lines = block.splitlines()
    body_lines = []
    skipping_meta = True
    for line in lines[1:]:  # drop the ## header
        if skipping_meta and (line.startswith("<!--") or "drop_id:" in line):
            continue
        skipping_meta = False
        body_lines.append(line)
    return "\n".join(body_lines).strip() or None


def _known_project_slugs() -> list[str]:
    """List of real project slugs from mono/projects/*.md for anonymizer."""
    from monogram import github_store

    try:
        repo = github_store._repo()
        contents = repo.get_contents("projects")
    except Exception as e:
        log.warning("harvest: could not list projects/: %s", e)
        return []
    slugs: list[str] = []
    for f in contents:
        name = getattr(f, "name", "")
        if name.endswith(".md"):
            slugs.append(name[:-3])
    return slugs


def _build_fixture(row: dict, anon_text: str, harvest_id: str) -> HarvestedFixture:
    drop_id = row["drop_id"]
    return HarvestedFixture(
        id=f"harvest-{harvest_id}-{drop_id[:6]}",
        category="harvested",
        source_harvest_id=harvest_id,
        source_drop_id=drop_id,
        input={"text": anon_text},
        expected={
            "target_kind": row.get("target_kind"),
            "slug": row.get("slug"),
            "target_path": row.get("target_path"),
            "drop_type": row.get("drop_type"),
            "should_escalate": False,
        },
        harvested_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Main entry point ──────────────────────────────────────────────────

def run_harvest(
    since_days: int = 7,
    *,
    dry_run: bool = False,
    skip_telegram: bool = False,
) -> dict:
    """Run one harvest cycle. Returns stats dict.

    dry_run=True: anonymize + write audit file only. No replay, no Telegram.
    skip_telegram=True: everything except the bot_notify.push_to_telegram call.
    """
    enabled, reason = is_eval_enabled()
    if not enabled:
        return {"status": "disabled", "reason": reason}

    rows = _load_pipeline_log(since_days)
    eligible = [r for r in rows if _should_harvest(r)]
    seen = _load_accepted_ids()
    fresh = [r for r in eligible if r.get("drop_id") not in seen]
    log.info(
        "harvest: %d rows / %d eligible / %d fresh (after dedup)",
        len(rows), len(eligible), len(fresh),
    )
    if not fresh:
        return {"status": "no_new_fixtures", "scanned": len(rows)}

    known_slugs = _known_project_slugs()
    harvest_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    harvested: list[HarvestedFixture] = []
    skipped_pii = 0
    skipped_no_text = 0

    for row in fresh:
        text = _fetch_drop_text(row)
        if not text:
            skipped_no_text += 1
            continue
        try:
            anon = scrub(text, known_slugs=known_slugs, raise_on_residual=True)
        except ResidualPII as e:
            log.warning("harvest: skipping drop_id=%s due to PII: %s", row["drop_id"], e)
            skipped_pii += 1
            continue
        if anon.similarity < 0.5:
            # Over-scrubbed to the point of unrecognizable; skip
            log.warning(
                "harvest: drop_id=%s over-scrubbed (similarity=%.2f), skipping",
                row["drop_id"], anon.similarity,
            )
            skipped_pii += 1
            continue
        harvested.append(_build_fixture(row, anon.output, harvest_id))

    if not harvested:
        return {
            "status": "all_filtered",
            "skipped_pii": skipped_pii,
            "skipped_no_text": skipped_no_text,
        }

    # Write audit copy
    audit_path = _FIXTURES_DIR / f"harvested-{harvest_id}.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a") as f:
        for h in harvested:
            f.write(json.dumps(asdict(h), ensure_ascii=False) + "\n")
    log.info("harvest: wrote audit file %s (%d fixtures)", audit_path, len(harvested))

    if dry_run:
        return {
            "status": "dry_run",
            "harvested": len(harvested),
            "audit_path": str(audit_path),
        }

    # Run replay suite against the unmodified _accepted.jsonl — the classifier
    # prompt hasn't changed, so cassette must still pass. If it doesn't,
    # something is wrong (production drift or harness bug).
    safety_ok, halt_reason = _run_replay_safety_check()
    if not safety_ok:
        if not skip_telegram:
            _notify_halt(halt_reason)
        return {
            "status": "halted",
            "reason": halt_reason,
            "harvested": len(harvested),
        }

    # Propose via approval gate
    token = secrets.token_urlsafe(12)
    if not skip_telegram:
        _push_proposal(token, harvest_id, harvested)
    _write_pending(token, harvest_id, harvested)

    return {
        "status": "proposed",
        "token": token,
        "harvested": len(harvested),
        "skipped_pii": skipped_pii,
        "skipped_no_text": skipped_no_text,
    }


# ── Safety check ──────────────────────────────────────────────────────

def _run_replay_safety_check() -> tuple[bool, str]:
    """Run pytest in replay mode; return (ok, halt_reason_if_not).

    Two-layer halt rule (§6.5): message differs by cassette freshness.
    """
    import subprocess
    import time

    # Determine cassette age — use newest file under evals/cassettes/
    cassette_dir = Path(__file__).parent / "cassettes"
    ages_days = []
    if cassette_dir.exists():
        for f in cassette_dir.glob("*.json"):
            age = (time.time() - f.stat().st_mtime) / 86400
            ages_days.append(age)
    min_age = min(ages_days) if ages_days else 999
    fresh_cassette = min_age <= 7

    # Subprocess-run pytest to isolate from our own process state.
    result = subprocess.run(
        ["pytest", str(Path(__file__).parent),
         "-m", "safety", "--tb=short", "-q"],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode == 0:
        return (True, "")

    if fresh_cassette:
        return (False, (
            "⚠️ HARVEST HALTED — possible harness-side bug\n"
            f"Cassette age: {min_age:.1f}d (recent). Likely a fixture/test/anonymizer bug.\n"
            f"pytest output:\n{result.stdout[-500:]}"
        ))
    return (False, (
        "🚨 HARVEST HALTED — possible production regression\n"
        f"Cassette age: {min_age:.1f}d (stale). Model behavior may have drifted.\n"
        f"Recommended: `monogram eval drift`.\n"
        f"pytest output:\n{result.stdout[-500:]}"
    ))


def _notify_halt(reason: str) -> None:
    import asyncio
    try:
        from monogram.bot_notify import push_to_telegram
        asyncio.run(push_to_telegram(reason))
    except Exception as e:
        log.error("harvest: halt notification failed: %s", e)


# ── Approval gate ─────────────────────────────────────────────────────

def _write_pending(token: str, harvest_id: str, fixtures: list[HarvestedFixture]) -> None:
    """Store the pending harvest so /approve_<token> can materialize it."""
    from monogram import github_store

    payload = {
        "token": token,
        "kind": "harvest_approval",
        "harvest_id": harvest_id,
        "fixture_ids": [f.id for f in fixtures],
        "expires_at": int(datetime.now(timezone.utc).timestamp()) + _HARVEST_TTL_SECONDS,
    }
    path = f"{_HARVEST_PENDING_DIR}/{token}.json"
    github_store.write(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False),
        f"monogram: harvest pending {token}",
    )


def _push_proposal(token: str, harvest_id: str, fixtures: list[HarvestedFixture]) -> None:
    """Push approval message to Telegram. Onboarding checklist for first 3."""
    import asyncio

    from monogram.bot_notify import push_to_telegram
    from monogram.vault_config import load_vault_config

    cfg = load_vault_config()
    onboarding_needed = not cfg.harvest_onboarding_complete

    ids_list = ", ".join(f.source_drop_id[:6] for f in fixtures[:5])
    if len(fixtures) > 5:
        ids_list += f", …({len(fixtures) - 5} more)"

    msg_parts = [
        f"🌾 Harvest {harvest_id}",
        f"+{len(fixtures)} new fixtures (drops: {ids_list})",
        "Anonymized, replay-suite baselines still pass.",
        "",
        f"Review: evals/fixtures/harvested-{harvest_id}.jsonl",
        "",
    ]

    if onboarding_needed:
        msg_parts.extend([
            "First approvals: verify fixtures contain NONE of:",
            "  [ ] Real names (yours, collaborators, family)",
            "  [ ] Real URLs (only example.com/X or public)",
            "  [ ] Real project slugs (only project-a..z)",
            "  [ ] Identifying financial amounts (round numbers OK)",
            "  [ ] Specific identifying dates",
            "  [ ] Sentences clearly about your real work",
            "  [ ] Email/phone/address",
            "  [ ] API-key-shaped strings",
            "",
            "AND fixtures DO contain:",
            "  [ ] Representative drop patterns (not generic pap)",
            "  [ ] Classifier-deciding structural features",
            "",
        ])

    msg_parts.extend([
        f"/approve_{token} — append to _accepted.jsonl",
        f"/deny_{token} — discard; re-harvest next cycle",
        "(expires 24h)",
    ])
    try:
        asyncio.run(push_to_telegram("\n".join(msg_parts)))
    except Exception as e:
        log.error("harvest: Telegram push failed: %s", e)


def accept_pending(token: str) -> tuple[bool, str]:
    """Called by bot on /approve_<token>. Materializes the harvest.

    1. Load pending file from .monogram/harvest-pending/<token>.json
    2. Append fixture lines from evals/fixtures/harvested-<date>.jsonl to _accepted.jsonl
    3. If Track B enabled, also append to mono/examples/harvested.jsonl
    4. Delete pending file
    5. Increment harvest_onboarding_complete if 3rd approval
    """
    from monogram import github_store

    path = f"{_HARVEST_PENDING_DIR}/{token}.json"
    raw = github_store.read(path)
    if not raw:
        return (False, f"No pending harvest for token {token}")
    try:
        pending = json.loads(raw)
    except json.JSONDecodeError as e:
        return (False, f"Pending file malformed: {e}")

    if pending.get("expires_at", 0) < int(datetime.now(timezone.utc).timestamp()):
        return (False, "Harvest proposal expired (24h)")

    harvest_id = pending["harvest_id"]
    wanted_ids = set(pending["fixture_ids"])

    audit_path = _FIXTURES_DIR / f"harvested-{harvest_id}.jsonl"
    if not audit_path.exists():
        return (False, f"Audit file missing: {audit_path}")

    accepted = []
    with audit_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("id") in wanted_ids:
                accepted.append(d)

    # Append to _accepted.jsonl
    _ACCEPTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _ACCEPTED_PATH.open("a") as f:
        for d in accepted:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # Track B: if enabled, also write to mono/examples/harvested.jsonl
    from monogram.vault_config import load_vault_config
    cfg = load_vault_config()
    if cfg.classifier_few_shot_enabled:
        _write_few_shot_examples(accepted, harvest_id)

    # Delete pending file (best-effort; silent failure is OK — TTL will expire)
    try:
        repo = github_store._repo()
        f = repo.get_contents(path)
        repo.delete_file(path, f"monogram: harvest {token} approved", f.sha)
    except Exception:
        pass

    # Onboarding complete after 3rd approval
    _maybe_complete_onboarding()

    return (True, f"Accepted {len(accepted)} fixtures from harvest {harvest_id}")


def _write_few_shot_examples(fixtures: list[dict], harvest_id: str) -> None:
    """Track B: append approved examples to mono/examples/harvested.jsonl."""
    from monogram import github_store

    path = "examples/harvested.jsonl"
    existing = github_store.read(path) or ""
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=30)).isoformat()

    new_lines = []
    for d in fixtures:
        example = {
            "input_excerpt": (d["input"]["text"])[:200],
            "target_kind": d["expected"]["target_kind"],
            "slug": d["expected"]["slug"],
            "source_harvest_id": harvest_id,
            "approved_at": now.isoformat(),
            "expires_at": expires,
        }
        new_lines.append(json.dumps(example, ensure_ascii=False))

    combined = existing.rstrip()
    if combined:
        combined += "\n"
    combined += "\n".join(new_lines) + "\n"
    github_store.write(path, combined, f"monogram: few-shot from harvest {harvest_id}")


def _maybe_complete_onboarding() -> None:
    """After 3rd accepted harvest, flip harvest_onboarding_complete True."""
    from monogram.vault_config import load_vault_config, set_config_field

    cfg = load_vault_config()
    if cfg.harvest_onboarding_complete:
        return
    # Count accepted harvests by counting unique source_harvest_id values
    if not _ACCEPTED_PATH.exists():
        return
    seen_harvests: set[str] = set()
    for line in _ACCEPTED_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        hid = d.get("source_harvest_id")
        if hid:
            seen_harvests.add(hid)
    if len(seen_harvests) >= 3:
        set_config_field("harvest_onboarding_complete", True)


def deny_pending(token: str) -> tuple[bool, str]:
    """Called by bot on /deny_<token>. Discards the pending harvest."""
    from monogram import github_store

    path = f"{_HARVEST_PENDING_DIR}/{token}.json"
    try:
        repo = github_store._repo()
        f = repo.get_contents(path)
        repo.delete_file(path, f"monogram: harvest {token} denied", f.sha)
    except Exception as e:
        return (False, f"Could not delete pending: {e}")
    return (True, f"Denied harvest proposal {token}")


# ── Rollback ──────────────────────────────────────────────────────────

def rollback_harvest(harvest_id: str) -> dict:
    """Remove all fixtures from a given harvest_id; re-run evals.

    1. Filter _accepted.jsonl to exclude lines where source_harvest_id == harvest_id
    2. If Track B enabled: same for mono/examples/harvested.jsonl
    3. Do NOT delete the dated audit file — retain for git history
    """
    from monogram import github_store
    from monogram.vault_config import load_vault_config

    stats = {"status": "rolled_back", "harvest_id": harvest_id, "removed": 0}

    if _ACCEPTED_PATH.exists():
        kept = []
        removed = 0
        for line in _ACCEPTED_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if d.get("source_harvest_id") == harvest_id:
                removed += 1
            else:
                kept.append(line)
        _ACCEPTED_PATH.write_text("\n".join(kept) + ("\n" if kept else ""))
        stats["removed"] = removed

    # Track B mirror (if enabled)
    cfg = load_vault_config()
    if cfg.classifier_few_shot_enabled:
        path = "examples/harvested.jsonl"
        content = github_store.read(path) or ""
        kept_lines = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(line)
                continue
            if d.get("source_harvest_id") != harvest_id:
                kept_lines.append(line)
        new = "\n".join(kept_lines) + ("\n" if kept_lines else "")
        if new != content:
            github_store.write(path, new, f"monogram: rollback harvest {harvest_id}")
            stats["few_shot_removed"] = True

    return stats
