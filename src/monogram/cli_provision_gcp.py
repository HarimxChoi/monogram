"""GCP provisioning via gcloud subprocess; idempotent on existing bucket/SA."""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("monogram.cli_provision_gcp")


class ProvisionError(RuntimeError):
    pass


_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,61}[a-z0-9]$")


def validate_bucket_name(name: str) -> str | None:
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
        return "dots disallowed (require domain verification)"
    return None


_PERMISSION_RE = re.compile(
    "|".join((
        "PERMISSION_DENIED",
        "does not have permission",
        "required permission",
        "insufficient permission",
        "access denied",
        "requires .* role",
    )),
    re.IGNORECASE,
)

_IAM_SCOPE_HINT = (
    "Current gcloud identity lacks the required IAM role. "
    "GCE default compute SAs cannot create SAs or bind IAM roles. "
    "Authenticate as a project owner from a workstation, or run "
    "`monogram init` from Cloud Shell and copy the key to the VM. "
    "The wizard is idempotent."
)


def _describe_gcloud_error(what: str, stderr: str) -> str:
    stderr = (stderr or "").strip()
    base = f"{what} failed: {stderr}" if stderr else f"{what} failed"
    if stderr and _PERMISSION_RE.search(stderr):
        return f"{base}\n\n  Hint: {_IAM_SCOPE_HINT}"
    return base


def _run(cmd: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
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
        # Race: bucket may have been created between check and create.
        # race: bucket may have been created between check and create
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
        # race: SA may have been created between check and create
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
