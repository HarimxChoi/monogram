# Security Policy

## Reporting a vulnerability

If you discover a security vulnerability in Monogram, report it
**privately** so it can be fixed before public disclosure.

**How to report**

- GitHub Security Advisory (preferred):
  <https://github.com/HarimxChoi/monogram/security/advisories/new>

Please include:

- A description of the vulnerability
- Steps to reproduce (proof-of-concept if possible)
- Impact assessment (what an attacker could do)
- Your preferred contact for follow-up

**Please do not**

- Open a public GitHub issue for security reports
- Exploit the vulnerability against any instance you don't own
- Share the vulnerability publicly until a fix is released

## Response timeline

Solo-maintainer project. Honest expectations:

| Severity | First acknowledgment | Fix target |
|----------|---------------------|-----------|
| Critical — credential leak, RCE, data loss | 48 hours | 7 days |
| High — auth bypass, privilege escalation | 5 days | 30 days |
| Medium / Low | 14 days | Best effort |

If you don't hear back within the acknowledgment window, please follow
up — GitHub notifications and email filters fail sometimes.

## Threat surface

Monogram handles sensitive data on three surfaces:

1. **Credential pipeline** — drops classified as `credential` write to
   `life/credentials/` and must never leak into other paths
   (`daily/*/drops.md`, `MEMORY.md`, morning brief, weekly report).
   Any leak across that boundary is Critical.
2. **Prompt injection** — the classifier's output is used to route
   writes. Injection that bypasses the classifier so that a credential
   lands under a benign `target_kind` is Critical.
3. **LLM data exfiltration** — drops often contain private content.
   Any path where the full drop reaches an unintended third-party
   service (e.g. logging, unexpected telemetry) is High.

Bugs that are **not** security issues — please open a normal issue:

- Extraction quality problems (e.g. PDF OCR fails)
- Latency regressions
- UX papercuts
- Documentation errors

## Scope

**In scope**

- The `mono-gram` Python package as published on PyPI
- Code in <https://github.com/HarimxChoi/monogram>
- The Obsidian quick-capture plugin shipped from the same repo

**Out of scope**

- Issues in upstream dependencies (report to them directly)
- User-provided MCP servers and Claude Desktop configuration
- The user's own vault repository hosting

## Disclosure

After a fix lands, the reporter will be credited in `CHANGELOG.md`
unless they ask to stay anonymous. For vulnerabilities rated High or
Critical a CVE will be requested where applicable.

## Supported versions

Only the current minor version receives security fixes. Users on older
versions should upgrade.

| Version | Status |
|---------|--------|
| 0.8.x (current) | ✅ Supported |
| ≤ 0.7.x | ❌ Unsupported — upgrade via `pip install --upgrade mono-gram` |

## Security practices in use

- **PyPI publishing** — Trusted Publishing (OIDC) + Sigstore
  attestations. No long-lived API tokens.
- **Dependency audit** — `pip-audit` in CI, Dependabot weekly updates.
- **Credential handling** — `life/credentials/` is hard-coded into
  `_HARD_NEVER_READ` in `vault_config.py` and enforced by
  `safe_read.py`; `docs/architecture.md §2` describes the guarantee.
- **SSRF hardening** — URL ingestion validates every redirect hop
  (private IPs, CGNAT, cloud metadata endpoints). See
  `src/monogram/ingestion/base.py::is_safe_url`.
- **HWP extraction** — handled by pyhwp's `hwp5txt` in an isolated
  subprocess with a minimal environment, a 20MB input size cap, and a
  60s hard timeout. pyhwp is pure Python with no URL handlers, macro
  engine, or env-var expansion, so the LibreOffice CVE classes
  (CVE-2024-12425 / 12426, CVE-2025-1080, CVE-2018-16858) do not apply.
  HWPX files are not processed — they're surfaced with a clear warning.
- **Atomic writes** — the listener commits through the GitHub Git Tree
  API (`github_store.write_atomic`) so a drop either lands as one
  commit or leaves no trace.
- **Backup isolation** — separate PAT and GitHub repo for the nightly
  mirror; a monthly CI job verifies that the backup is non-empty and
  converges with the primary vault.
- **Telegram session** — `monogram_session.session` is in
  `.gitignore` and must never be committed.
