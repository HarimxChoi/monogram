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
import subprocess
from pathlib import Path

log = logging.getLogger("monogram.cli_provision_gcp")


class ProvisionError(RuntimeError):
    """Raised when a gcloud call fails and no idempotent reuse applies."""


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
        raise ProvisionError(f"set project failed: {err.strip()[:200]}")


def _enable_storage_api(project: str) -> None:
    """Idempotent — returns 0 even if the API is already enabled."""
    rc, _, err = _run(
        ["gcloud", "services", "enable", "storage.googleapis.com",
         f"--project={project}"],
        timeout=120.0,
    )
    if rc != 0:
        raise ProvisionError(
            f"enable storage API failed: {err.strip()[:200]}"
        )


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
        raise ProvisionError(f"bucket create failed: {err.strip()[:200]}")
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
        raise ProvisionError(f"SA create failed: {err.strip()[:200]}")
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
        raise ProvisionError(f"SA key create failed: {err.strip()[:200]}")
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
            f"bind {role} to {member} failed: {err.strip()[:200]}"
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
    """
    key_path = key_path or (Path.cwd() / ".gcp" / f"{sa_name}-key.json")
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
