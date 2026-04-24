"""One-shot GCP provisioning for the encrypted dashboard.

Shells out to `gcloud` rather than pulling in google-cloud-* SDKs so
the dependency surface stays at zero when the user doesn't pick GCS.

Flow (called from `monogram init` when the user picks webui=gcs):

  1. gcloud config set project <PROJECT>
  2. gcloud services enable storage.googleapis.com
  3. gcloud storage buckets create gs://<BUCKET> --location=<REGION>
     (idempotent — reuses existing bucket)
  4. gcloud iam service-accounts create <SA_NAME>
     (idempotent — reuses existing SA)
  5. gcloud iam service-accounts keys create <KEY_PATH>
     — the one non-idempotent step; we key-rotate on re-run only if
     the user opts in.
  6. gcloud storage buckets add-iam-policy-binding gs://<BUCKET>
     --member=serviceAccount:<SA_EMAIL> --role=roles/storage.objectAdmin
  7. gcloud storage buckets add-iam-policy-binding gs://<BUCKET>
     --member=allUsers --role=roles/storage.objectViewer
     (ciphertext only — objects are end-to-end encrypted)

Returns the absolute path of the generated SA key so the wizard can
write GOOGLE_APPLICATION_CREDENTIALS=<path> into .env.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("monogram.cli_provision_gcp")


class ProvisionError(RuntimeError):
    """Raised when a gcloud call fails and no idempotent reuse applies."""


# ── GCS bucket naming ────────────────────────────────────────────────

# GCS naming rules (public docs): 3-63 chars, lowercase letters/digits/
# dashes/underscores/periods, must start and end with alphanumeric, no
# `goog` prefix, no `google` substring. Dots require domain verification
# so we disallow them here to keep the provisioning path simple.
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,61}[a-z0-9]$")


def validate_bucket_name(name: str) -> str | None:
    """Return a human-readable error, or None if the name is valid.

    Caught at prompt time so the user gets an actionable message instead
    of a gcloud 400 several seconds later.
    """
    if not name:
        return "bucket name is empty"
    if len(name) < 3 or len(name) > 63:
        return f"bucket name must be 3-63 chars (got {len(name)})"
    if name != name.lower():
        return "bucket name must be lowercase"
    if not _BUCKET_RE.match(name):
        return (
            "bucket name must start/end with a letter or digit and contain "
            "only lowercase letters, digits, dashes, or underscores"
        )
    if name.startswith("goog") or "google" in name:
        return "bucket name cannot start with 'goog' or contain 'google'"
    if "." in name:
        return (
            "dots in bucket names require domain verification — use dashes "
            "or underscores instead"
        )
    return None


# ── gcloud error diagnostics ─────────────────────────────────────────

_PERMISSION_PATTERNS = (
    "PERMISSION_DENIED",
    "does not have permission",
    "required permission",
    "insufficient permission",
    "access denied",
    "requires .* role",
)
_PERMISSION_RE = re.compile("|".join(_PERMISSION_PATTERNS), re.IGNORECASE)

_IAM_SCOPE_HINT = (
    "Your current gcloud identity lacks the IAM role this step needs.\n"
    "  If running on a GCE VM: the default compute service account "
    "usually cannot create other SAs or bind IAM roles. Fix by either:\n"
    "    (a) authenticating as a project owner — `gcloud auth login` "
    "from a workstation, or\n"
    "    (b) running `monogram init` from Cloud Shell, then copy the "
    "generated key to the VM and re-run init there.\n"
    "  The wizard is idempotent — it will reuse the existing bucket + SA."
)


def _describe_gcloud_error(what: str, stderr: str) -> str:
    """Format gcloud stderr for display. Full text, with guidance on perms."""
    stderr = (stderr or "").strip()
    base = f"{what} failed: {stderr}" if stderr else f"{what} failed"
    if stderr and _PERMISSION_RE.search(stderr):
        return f"{base}\n\n  Hint: {_IAM_SCOPE_HINT}"
    return base


# ── subprocess wrapper ───────────────────────────────────────────────

def _run(cmd: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
    """Run a gcloud command, capture stdout/stderr, never raise.

    Returns (returncode, stdout, stderr) — callers inspect both because
    gcloud sometimes writes a warning to stderr on a returncode-0 path
    (e.g. "API already enabled").
    """
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"timeout after {timeout}s: {' '.join(cmd)}")
    except FileNotFoundError:
        return (127, "", "gcloud not on PATH")
    return (out.returncode, out.stdout, out.stderr)


def _set_project(project: str) -> None:
    rc, _, err = _run(["gcloud", "config", "set", "project", project])
    if rc != 0:
        raise ProvisionError(_describe_gcloud_error("set project", err))


def _enable_storage_api(project: str) -> None:
    """Idempotent — returns 0 even if the API is already enabled."""
    rc, _, err = _run(
        ["gcloud", "services", "enable", "storage.googleapis.com",
         f"--project={project}"],
        timeout=120.0,
    )
    if rc != 0:
        raise ProvisionError(_describe_gcloud_error("enable storage API", err))


def _bucket_exists(bucket: str) -> bool:
    rc, out, _ = _run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{bucket}",
         "--format=value(name)"],
        timeout=20.0,
    )
    return rc == 0 and bool(out.strip())


def _create_bucket(bucket: str, project: str, region: str) -> str:
    """Create bucket if missing; return 'created' or 'exists'."""
    if _bucket_exists(bucket):
        return "exists"
    rc, _, err = _run(
        [
            "gcloud", "storage", "buckets", "create", f"gs://{bucket}",
            f"--project={project}",
            f"--location={region}",
            "--uniform-bucket-level-access",
        ],
        timeout=60.0,
    )
    if rc != 0:
        # Race: another caller may have created it between our check and create.
        if _bucket_exists(bucket):
            return "exists"
        raise ProvisionError(_describe_gcloud_error("bucket create", err))
    return "created"


def _sa_email(sa_name: str, project: str) -> str:
    return f"{sa_name}@{project}.iam.gserviceaccount.com"


def _sa_exists(sa_name: str, project: str) -> bool:
    rc, out, _ = _run(
        [
            "gcloud", "iam", "service-accounts", "describe",
            _sa_email(sa_name, project),
            f"--project={project}",
            "--format=value(email)",
        ],
        timeout=20.0,
    )
    return rc == 0 and bool(out.strip())


def _create_service_account(sa_name: str, project: str) -> str:
    if _sa_exists(sa_name, project):
        return "exists"
    rc, _, err = _run(
        [
            "gcloud", "iam", "service-accounts", "create", sa_name,
            f"--project={project}",
            f"--display-name=Monogram web UI publisher",
        ],
        timeout=30.0,
    )
    if rc != 0:
        if _sa_exists(sa_name, project):
            return "exists"
        raise ProvisionError(_describe_gcloud_error("SA create", err))
    return "created"


def _create_sa_key(sa_name: str, project: str, key_path: Path) -> None:
    key_path.parent.mkdir(parents=True, exist_ok=True)
    rc, _, err = _run(
        [
            "gcloud", "iam", "service-accounts", "keys", "create",
            str(key_path),
            f"--iam-account={_sa_email(sa_name, project)}",
            f"--project={project}",
        ],
        timeout=30.0,
    )
    if rc != 0:
        raise ProvisionError(_describe_gcloud_error("SA key create", err))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass


def _bind_role(bucket: str, member: str, role: str) -> None:
    """Add an IAM policy binding. gcloud treats re-adds as no-ops."""
    rc, _, err = _run(
        [
            "gcloud", "storage", "buckets", "add-iam-policy-binding",
            f"gs://{bucket}",
            f"--member={member}",
            f"--role={role}",
        ],
        timeout=30.0,
    )
    if rc != 0:
        raise ProvisionError(
            _describe_gcloud_error(f"bind {role} to {member}", err)
        )


def provision_gcs_bucket(
    project: str,
    bucket: str,
    region: str = "us-central1",
    sa_name: str = "monogram-webui",
    key_path: Path | None = None,
) -> dict:
    """Run the full provisioning sequence. Returns a summary dict.

    Idempotent end-to-end: re-running against an already-provisioned
    project reuses the bucket + SA and only generates a fresh key if
    `key_path` doesn't already exist. Caller is responsible for
    deciding whether to rotate.

    Default key path is `~/.gcp/<sa_name>-key.json` so the credentials
    live in a stable location regardless of the user's cwd. Previous
    versions dropped keys in `./.gcp/` which orphaned them if the user
    ran init from a tmp dir.
    """
    bucket_err = validate_bucket_name(bucket)
    if bucket_err:
        raise ProvisionError(f"invalid bucket name '{bucket}': {bucket_err}")

    key_path = key_path or (Path.home() / ".gcp" / f"{sa_name}-key.json")
    summary: dict = {
        "project": project,
        "bucket": bucket,
        "region": region,
        "sa_email": _sa_email(sa_name, project),
        "key_path": str(key_path.resolve()),
        "steps": [],
    }

    _set_project(project)
    summary["steps"].append(("set-project", "ok"))

    _enable_storage_api(project)
    summary["steps"].append(("enable-storage-api", "ok"))

    summary["steps"].append(("bucket", _create_bucket(bucket, project, region)))
    summary["steps"].append(
        ("service-account", _create_service_account(sa_name, project))
    )

    if key_path.exists():
        summary["steps"].append(("sa-key", "reused"))
    else:
        _create_sa_key(sa_name, project, key_path)
        summary["steps"].append(("sa-key", "created"))

    sa_member = f"serviceAccount:{_sa_email(sa_name, project)}"
    _bind_role(bucket, sa_member, "roles/storage.objectAdmin")
    summary["steps"].append(("bind-sa-objectAdmin", "ok"))

    _bind_role(bucket, "allUsers", "roles/storage.objectViewer")
    summary["steps"].append(("bind-public-objectViewer", "ok"))

    return summary
