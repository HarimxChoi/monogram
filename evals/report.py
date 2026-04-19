"""Reporting helpers for eval output.

- render_last_report: markdown summary of last pytest run + cassette stats
- save_baseline: snapshot current state as a dated baseline
- run_drift_comparison: re-record cassettes side-by-side and diff
- run_ablation_diff: compare current branch's cassettes vs another branch
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from evals.cassette import Cassette, diff_structured

_EVAL_ROOT = Path(__file__).parent
_CASSETTE_DIR = _EVAL_ROOT / "cassettes"
_BASELINES_DIR = _EVAL_ROOT / "baselines"
_REPORTS_DIR = _EVAL_ROOT / "reports"


def render_last_report() -> Path:
    """Render a markdown report from the current cassettes + last pytest run.

    Not all metrics are derivable from the cassette alone (pass/fail counts
    need pytest's .last_failed file). Keep this honest — numbers shown are
    what's actually in the files.
    """
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cassette = Cassette(_CASSETTE_DIR, mode="replay")
    # Load every per-agent file to populate stats
    for p in _CASSETTE_DIR.glob("*.json"):
        cassette._load(p.stem)

    per_agent = cassette.per_agent_counts()
    models = cassette.tier_usage()
    total_tokens = cassette.total_tokens()
    latencies = cassette.avg_latency_ms()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    path = _REPORTS_DIR / f"report-{now}.md"

    lines = [
        f"# Eval Report — {now}",
        "",
        "## Cassette coverage",
        "",
        "| Agent | Entries |",
        "|---|---|",
    ]
    for agent in sorted(per_agent):
        lines.append(f"| {agent} | {per_agent[agent]} |")
    lines += [
        "",
        "## Model usage",
        "",
        "| Model | Calls |",
        "|---|---|",
    ]
    for model in sorted(models):
        lines.append(f"| {model} | {models[model]} |")
    lines += [
        "",
        f"**Total cassette-recorded tokens:** {total_tokens:,}",
        "",
        "## Recorded latency (per agent)",
        "",
        "| Agent | Avg ms |",
        "|---|---|",
    ]
    for agent in sorted(latencies):
        lines.append(f"| {agent} | {latencies[agent]:.0f} |")

    path.write_text("\n".join(lines) + "\n")
    return path


def save_baseline() -> Path:
    """Snapshot current cassettes + a report into baselines/<date>/."""
    _BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest = _BASELINES_DIR / date
    dest.mkdir(parents=True, exist_ok=True)
    for p in _CASSETTE_DIR.glob("*.json"):
        shutil.copy2(p, dest / p.name)
    # Also copy the latest report if any
    report = render_last_report()
    shutil.copy2(report, dest / "report.md")
    return dest


def run_drift_comparison() -> dict:
    """Record cassettes to a parallel directory, diff against committed.

    This burns LLM quota — use sparingly. Result is a JSON dict of changes
    per agent; empty diff means no drift detected for that agent.
    """
    parallel = _EVAL_ROOT / "cassettes-drift"
    if parallel.exists():
        shutil.rmtree(parallel)
    parallel.mkdir()

    # Run the record pass using --record but pointing to the parallel dir.
    # Easiest way: temporarily move committed cassettes, record, compare, restore.
    backup = _EVAL_ROOT / "cassettes-backup"
    if backup.exists():
        shutil.rmtree(backup)
    _CASSETTE_DIR.rename(backup)
    _CASSETTE_DIR.mkdir()
    try:
        subprocess.run(
            ["pytest", str(_EVAL_ROOT), "--record", "-q", "--tb=no"],
            check=False,
        )
        # Compare per agent
        out: dict[str, list] = {}
        for new_file in _CASSETTE_DIR.glob("*.json"):
            agent = new_file.stem
            old_file = backup / new_file.name
            if not old_file.exists():
                out[agent] = [{"note": "new cassette with no baseline"}]
                continue
            new_entries = json.loads(new_file.read_text())
            old_entries = json.loads(old_file.read_text())
            agent_diffs = []
            for key, new_e in new_entries.items():
                old_e = old_entries.get(key)
                if old_e is None:
                    agent_diffs.append({"key": key, "kind": "new_entry"})
                    continue
                d = diff_structured(
                    old_e.get("response_content", ""),
                    new_e.get("response_content", ""),
                )
                if (d.get("kind") == "json" and d.get("diff")) or (
                    d.get("kind") == "text" and d.get("changed")
                ):
                    agent_diffs.append({"key": key, "diff": d})
            if agent_diffs:
                out[agent] = agent_diffs
    finally:
        # Restore committed cassettes
        if _CASSETTE_DIR.exists():
            parallel_final = _EVAL_ROOT / "cassettes-drift-result"
            if parallel_final.exists():
                shutil.rmtree(parallel_final)
            _CASSETTE_DIR.rename(parallel_final)
        backup.rename(_CASSETTE_DIR)
    return out


def run_ablation_diff(against: str = "main") -> dict:
    """Compare current-branch cassettes vs another branch's cassettes.

    Used for the orchestrator ablation study (§6.4 of plan):
      git checkout main && pytest --record
      git checkout exp/no-orchestrator && pytest --record
      monogram eval ablate-diff --against main

    Compares `primary_path` and `target_kind` fields across response JSONs
    in classifier.json between the two branches.
    """
    # Get list of current-branch classifier cassette entries
    current = _CASSETTE_DIR / "classifier.json"
    if not current.exists():
        return {"error": f"{current} not found — run --record first"}

    # Use git show to load the baseline branch's version
    try:
        result = subprocess.run(
            ["git", "show", f"{against}:evals/cassettes/classifier.json"],
            capture_output=True, text=True, check=True,
        )
        baseline = json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        return {"error": f"could not load baseline from {against}: {e}"}

    current_entries = json.loads(current.read_text())

    diverged = []
    same = 0
    for key, cur_e in current_entries.items():
        base_e = baseline.get(key)
        if base_e is None:
            continue  # different fixture set — ignore
        try:
            cur_j = json.loads(cur_e["response_content"])
            base_j = json.loads(base_e["response_content"])
        except (json.JSONDecodeError, KeyError):
            continue
        keys_of_interest = ("target_kind", "slug", "target_path")
        if any(cur_j.get(k) != base_j.get(k) for k in keys_of_interest):
            diverged.append({
                "key": key,
                "prompt_sample": cur_e.get("prompt_sample", "")[:80],
                "baseline": {k: base_j.get(k) for k in keys_of_interest},
                "current": {k: cur_j.get(k) for k in keys_of_interest},
            })
        else:
            same += 1

    total = same + len(diverged)
    pct = (100.0 * len(diverged) / total) if total else 0
    return {
        "against": against,
        "total_compared": total,
        "same": same,
        "diverged": len(diverged),
        "percent_diverged": round(pct, 2),
        "recommendation": (
            "DELETE orchestrator (< 2% divergence)" if pct < 2
            else "KEEP orchestrator (>= 2% divergence)"
        ),
        "diverged_samples": diverged[:10],
    }
