# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in Monogram, please report it
**privately** so we can fix it before public disclosure.

**How to report:**

- GitHub Security Advisory (preferred):
  https://github.com/<your-github-user>/monogram/security/advisories/new
- Email: `security@monogram.example` (you can encrypt with the PGP key
  in `docs/security-pgp.txt` if the issue is sensitive)

Please include:

- A description of the vulnerability
- Steps to reproduce (proof-of-concept if possible)
- Impact assessment (what an attacker could do)
- Your preferred contact for follow-up

**Please do not:**

- Open a public GitHub issue for security reports
- Exploit the vulnerability against any instance you don't own
- Share the vulnerability publicly until a fix is released

## Response timeline

This is a solo-maintainer project. Honest expectations:

| Severity | First acknowledgment | Fix target |
|----------|---------------------|-----------|
| Critical (credential leak, RCE, data loss) | 48 hours | 7 days |
| High (auth bypass, privilege escalation) | 5 days | 30 days |
| Medium/Low | 14 days | Best effort |

If you don't hear back within the acknowledgment window, please follow
up — email filters fail sometimes.

## What counts as a vulnerability

Monogram handles sensitive data on three surfaces:

1. **Credential pipeline** — drops classified as `credential` write to
   `life/credentials/` and must NEVER leak to other paths (drops.md,
   MEMORY.md, brief, weekly report). Any leak is critical.
2. **Prompt injection** — classifier output is used to route writes.
   Injection that bypasses classifier to write credentials under a
   benign `target_kind` is critical.
3. **LLM data exfiltration** — drops may contain private content. A
   path where the full drop is sent to an unintended third-party
   service (e.g., accidental public logging) is high.

Bugs that are **not** security issues (please file as normal issues):

- Extraction quality problems (e.g., PDF OCR fails)
- Latency regressions
- UX papercuts
- Documentation errors

## Scope

In scope:
- The `monogram` Python package as published on PyPI
- Code in the `<your-github-user>/monogram` repository
- The `<your-github-user>/monogram-obsidian` companion plugin

Out of scope:
- Issues in upstream dependencies (report to them directly)
- Issues in user-provided MCP servers or Claude Desktop configuration
- Issues in the user's own vault repository hosting

## Disclosure

After a fix is released, we will credit reporters in the CHANGELOG
unless they request anonymity. A CVE will be requested for any
vulnerability rated High or Critical.

## Supported versions

Only the latest minor version receives security fixes. Users on older
versions should upgrade.

| Version | Status |
|---------|--------|
| 0.7.x | ✅ Supported |
| 0.6.x | ⚠️ Security fixes only until v0.8 ships |
| ≤ 0.5.x | ❌ Unsupported |

## Security practices in use

- **PyPI publishing**: Trusted Publishing (OIDC) + attestations via
  Sigstore. No long-lived API tokens.
- **Dependency audit**: `pip-audit` in CI; Dependabot for weekly
  dependency updates.
- **Secret scanning**: `gitleaks` pre-release audit.
- **Credential handling**: hard-coded `life/credentials/` exclusion
  from LLM context via `safe_read.py` gate.
- **Telegram session**: `monogram_session.session` is in `.gitignore`
  and must never be committed.
