"""Vault backup — mirror example-org/mono → example-org/mono-backup and
restore-drill verification.

Design:
  - Separate PAT (BACKUP_GITHUB_PAT) scoped ONLY to the backup repo.
    If the primary PAT leaks, backup stays uncompromised. If backup PAT
    leaks, primary is safe.
  - Backup repo = identical commit history to source (not just file
    copy). We use Git refs-level operations: read source branch tip,
    write same commit SHA to backup repo branch. Since GitHub stores
    all commits globally within an org's shared object pool (with
    different permissions gates), we can't directly cross-reference
    commits between two repos — we replay the commit tree into the
    backup repo.
  - For v0.8 simplicity: `monogram backup mirror` reads source HEAD,
    walks files in the current tree, writes them all to backup repo as
    a single atomic commit. Not true git mirroring; close enough for
    restore-drill purposes.

Threats mitigated:
  - Source repo compromise (ransomware on GitHub account): backup repo
    on same account shares this risk. For real isolation, BACKUP_GITHUB_PAT
    should belong to a SECOND GitHub account (user decides — documented
    in SECURITY.md).
  - Backup-to-itself misconfiguration: we refuse to mirror if source and
    backup env vars resolve to the same repo.
  - Silent backup failure: verify command runs a smoke test and returns
    non-zero on failure — CI can alert.

NOT YET IMPLEMENTED (v0.8.1+):
  - Diff-based incremental mirror (full snapshot every run is wasteful
    for large vaults)
  - Retention policy (daily × 30, weekly × 12, monthly forever)
  - Full-history mirror via `git clone --mirror` + `git push --mirror`
    (needs a local git executable, which bumps dependency surface)
"""
from __future__ import annotations

import logging
import os
from typing import NamedTuple

log = logging.getLogger("monogram.backup")


class BackupConfig(NamedTuple):
    """Validated pair of (source, backup) GitHub connections."""
    source_repo: str       # e.g. "example-org/mono"
    source_pat: str
    backup_repo: str       # e.g. "example-org/mono-backup"
    backup_pat: str


class BackupMisconfigured(ValueError):
    """Raised when backup env is missing, invalid, or circular."""


def load_backup_config() -> BackupConfig:
    """Read source + backup config from env and vault_config. Raises
    BackupMisconfigured if anything's wrong."""
    from .config import load_config

    cfg = load_config()
    source_repo = cfg.github_repo
    source_pat = cfg.github_pat
    if not source_repo or not source_pat:
        raise BackupMisconfigured(
            "source repo/PAT missing (GITHUB_REPO, GITHUB_PAT)"
        )

    backup_repo = os.environ.get("BACKUP_GITHUB_REPO", "").strip()
    backup_pat = os.environ.get("BACKUP_GITHUB_PAT", "").strip()
    if not backup_repo:
        raise BackupMisconfigured(
            "BACKUP_GITHUB_REPO env var not set (e.g. 'example-org/mono-backup')"
        )
    if not backup_pat:
        raise BackupMisconfigured(
            "BACKUP_GITHUB_PAT env var not set. Use a SEPARATE fine-grained "
            "PAT scoped to the backup repo only."
        )

    # Guardrail: refuse backup-to-self. Case-insensitive match — GitHub
    # treats example-org/mono and Example-Org/mono as the same repo.
    if backup_repo.lower() == source_repo.lower():
        raise BackupMisconfigured(
            f"backup repo must differ from source ({source_repo}). "
            "Mirroring onto the source would destroy live data."
        )

    # Soft warning: same PAT for both = probably misconfigured
    if backup_pat == source_pat:
        log.warning(
            "BACKUP_GITHUB_PAT equals GITHUB_PAT. Consider using a separate "
            "PAT scoped only to the backup repo — if the source PAT leaks, "
            "the backup would be compromised too."
        )

    return BackupConfig(source_repo, source_pat, backup_repo, backup_pat)


def mirror() -> dict:
    """Mirror source → backup. Returns a status dict.

    Strategy:
      1. List all files in source repo's HEAD tree
      2. Read each file's content
      3. Atomically write the full set to backup repo via write_atomic
    """
    from github import Auth, Github

    config = load_backup_config()

    source_client = Github(auth=Auth.Token(config.source_pat))
    backup_client = Github(auth=Auth.Token(config.backup_pat))

    source = source_client.get_repo(config.source_repo)
    backup = backup_client.get_repo(config.backup_repo)

    # Collect all files recursively from source
    files = _collect_repo_files(source)
    log.info("backup.mirror: collected %d files from %s", len(files), config.source_repo)

    if not files:
        return {
            "ok": True,
            "files_mirrored": 0,
            "note": "source repo is empty — no mirror performed",
        }

    # Atomic write to backup via Tree API
    from .github_store import InputGitTreeElement  # local import for stability
    from github import InputGitTreeElement as _GITElement  # noqa

    # Use backup repo's default branch
    backup_ref = backup.get_git_ref(f"heads/{backup.default_branch}")
    backup_parent = backup.get_git_commit(backup_ref.object.sha)
    base_tree = backup_parent.tree

    tree_elements = []
    for path, content in files.items():
        blob = backup.create_git_blob(content, "utf-8")
        tree_elements.append(
            _GITElement(path=path, mode="100644", type="blob", sha=blob.sha)
        )

    new_tree = backup.create_git_tree(tree_elements, base_tree=base_tree)
    source_head_sha = source.get_branch(source.default_branch).commit.sha
    commit_msg = f"backup.mirror: {source_head_sha[:12]} ({len(files)} files)"
    new_commit = backup.create_git_commit(commit_msg, new_tree, [backup_parent])
    backup_ref.edit(new_commit.sha)

    log.info("backup.mirror: wrote commit %s to %s", new_commit.sha[:12], config.backup_repo)
    return {
        "ok": True,
        "files_mirrored": len(files),
        "source_sha": source_head_sha,
        "backup_sha": new_commit.sha,
    }


def verify() -> dict:
    """Smoke-test the backup repo.

    Checks:
      1. Both repos reachable with their PATs
      2. Backup repo has a non-empty tree
      3. A critical file (config.md or README.md) is present and non-empty
      4. File count is within 5% of source count (catches partial mirror)

    Returns a dict with {ok, checks[]}. Callers should exit nonzero on
    failure (CI-friendly).
    """
    from github import Auth, Github

    checks: list[dict] = []

    try:
        config = load_backup_config()
        checks.append({"name": "config", "ok": True})
    except BackupMisconfigured as e:
        return {"ok": False, "checks": [{"name": "config", "ok": False, "err": str(e)}]}

    try:
        source = Github(auth=Auth.Token(config.source_pat)).get_repo(config.source_repo)
        source_files = _collect_repo_files(source, max_files=5000, content=False)
        checks.append({"name": "source_reachable", "ok": True, "files": len(source_files)})
    except Exception as e:
        checks.append({"name": "source_reachable", "ok": False, "err": str(e)[:200]})
        return {"ok": False, "checks": checks}

    try:
        backup = Github(auth=Auth.Token(config.backup_pat)).get_repo(config.backup_repo)
        backup_files = _collect_repo_files(backup, max_files=5000, content=False)
        checks.append({"name": "backup_reachable", "ok": True, "files": len(backup_files)})
    except Exception as e:
        checks.append({"name": "backup_reachable", "ok": False, "err": str(e)[:200]})
        return {"ok": False, "checks": checks}

    # Backup non-empty check
    if not backup_files:
        checks.append({"name": "backup_nonempty", "ok": False, "err": "backup repo is empty"})
        return {"ok": False, "checks": checks}
    checks.append({"name": "backup_nonempty", "ok": True})

    # File-count delta check (±5%)
    source_n = len(source_files)
    backup_n = len(backup_files)
    if source_n > 0:
        delta = abs(backup_n - source_n) / source_n
        if delta > 0.05:
            checks.append({
                "name": "file_count_within_5pct",
                "ok": False,
                "err": f"source={source_n} backup={backup_n} (delta {delta:.1%})",
            })
            return {"ok": False, "checks": checks}
    checks.append({
        "name": "file_count_within_5pct",
        "ok": True,
        "source": source_n,
        "backup": backup_n,
    })

    # Critical-file check: README.md or config.md present and non-empty
    critical_files = ("README.md", "config.md")
    found_critical = False
    for path in critical_files:
        try:
            content = backup.get_contents(path)
            if content.size > 0:
                found_critical = True
                break
        except Exception:
            continue
    if not found_critical:
        checks.append({
            "name": "critical_file_present",
            "ok": False,
            "err": f"neither {' nor '.join(critical_files)} found in backup",
        })
        return {"ok": False, "checks": checks}
    checks.append({"name": "critical_file_present", "ok": True})

    return {"ok": True, "checks": checks}


def _collect_repo_files(
    repo, max_files: int = 5000, content: bool = True
) -> dict[str, str]:
    """Walk the repo's default-branch tree and return {path: content}.

    If content=False, values are empty strings (just for counting/
    existence checks, cheaper — uses recursive tree API which is 1 call).
    """
    from github.GithubException import GithubException

    try:
        branch = repo.get_branch(repo.default_branch)
        tree = repo.get_git_tree(branch.commit.sha, recursive=True)
    except GithubException as e:
        log.error("backup: tree fetch failed: %s", e)
        return {}

    files: dict[str, str] = {}
    for element in tree.tree:
        if element.type != "blob":
            continue
        if len(files) >= max_files:
            log.warning("backup: hit max_files cap %d", max_files)
            break
        if content:
            try:
                raw = repo.get_contents(element.path)
                files[element.path] = raw.decoded_content.decode(errors="replace")
            except Exception as e:
                log.warning("backup: read failed for %s: %s", element.path, e)
                continue
        else:
            files[element.path] = ""

    return files


# ── CLI wiring ────────────────────────────────────────────────────────────

import click


@click.group(name="backup")
def backup_group():
    """Vault backup commands (v0.8)."""


@backup_group.command("mirror")
@click.option("--dry-run", is_flag=True, help="Show plan without writing.")
def backup_mirror_cmd(dry_run: bool):
    """Mirror source vault → backup vault. Use for nightly snapshots
    (set up cron with this command) or manual redundancy."""
    try:
        config = load_backup_config()
    except BackupMisconfigured as e:
        click.echo(f"✗ {e}", err=True)
        raise click.Abort()

    click.echo(f"Source: {config.source_repo}")
    click.echo(f"Backup: {config.backup_repo}")

    if dry_run:
        click.echo("(dry-run — no changes)")
        return

    if not click.confirm("Proceed with mirror?", default=True):
        raise click.Abort()

    result = mirror()
    if result["ok"]:
        click.echo(f"✓ Mirrored {result['files_mirrored']} files")
        if "source_sha" in result:
            click.echo(f"  Source SHA: {result['source_sha'][:12]}")
            click.echo(f"  Backup SHA: {result['backup_sha'][:12]}")
    else:
        click.echo("✗ Mirror failed", err=True)
        raise click.Abort()


@backup_group.command("verify")
@click.option("--json", "as_json", is_flag=True, help="Output JSON (CI-friendly).")
def backup_verify_cmd(as_json: bool):
    """Restore-drill the backup vault. Fails nonzero on any check failure
    — suitable for monthly CI-scheduled runs."""
    result = verify()

    if as_json:
        import json
        click.echo(json.dumps(result, indent=2))
    else:
        for check in result["checks"]:
            mark = "✓" if check["ok"] else "✗"
            line = f"  {mark} {check['name']}"
            extras = {k: v for k, v in check.items() if k not in ("name", "ok")}
            if extras:
                line += f" — {extras}"
            click.echo(line)
        click.echo()
        if result["ok"]:
            click.echo("✓ Backup verified.")
        else:
            click.echo("✗ Backup verification failed.", err=True)

    if not result["ok"]:
        raise click.Abort()
