# Contributing to Monogram

Thanks for your interest. This is a solo-maintainer project — honest
expectations below so nobody is surprised.

## Response SLA

- **Issues**: 48h for first acknowledgment. No promise on resolution
  time.
- **Pull requests**: 1 week for first review. Expect changes requested
  before merge.
- **Security issues**: 48h per [SECURITY.md](SECURITY.md).

If you need a faster response, please say so explicitly in the issue.

## What I'll accept

**Priority order:**

1. Security fixes (always priority)
2. Bug fixes for documented behavior
3. Test additions for existing features
4. Documentation improvements
5. Small feature additions that don't change architecture

## What I won't accept (please discuss first)

**Architecture changes** — before opening a PR, open an issue to
discuss. I've made specific non-negotiable choices (see
[docs/eval.md](docs/eval.md) "What NOT to do"):

- **No auto-prompt-rewriting** (DSPy, TextGrad, MIPRO-style)
- **No runtime classifier caching**
- **No self-tuning thresholds**
- **No few-shot in verifier**
- **No multi-user support** (Monogram is single-user by design)
- **No web-based editing** (web UI is read-only)
- **No native mobile app** (Telegram IS the mobile interface)

PRs implementing any of the above will be closed with this link.

## Development setup

```bash
git clone https://github.com/<your-github-user>/monogram
cd monogram
pip install -e '.[dev,eval]'

# Run tests
pytest tests/ -v                   # production tests (~270)
pytest evals/ --force-eval -v      # eval tests (~155)

# Lint
ruff check src/ tests/ evals/
ruff format --check src/ tests/ evals/

# Type check
# (no mypy yet; ruff handles most issues)

# Security
pip-audit
```

## Pull request checklist

Before opening a PR:

- [ ] Tests pass locally (`pytest tests/ evals/ -v`)
- [ ] Ruff passes (`ruff check`, `ruff format --check`)
- [ ] New code has tests (we're at ~425 total, don't regress coverage)
- [ ] Commit messages follow conventional format:
  `v0.X.Y: <type> — <subject>` where type is feat/fix/docs/test/refactor
- [ ] No new dependencies added to core `dependencies` without discussion
  (add to optional-dependencies instead)
- [ ] `evals/cassettes/*.json` NOT committed from a PR (those are recorded
  from main-branch runs only, to avoid contaminating the baseline)
- [ ] Relevant docs updated (CHANGELOG.md entry for user-visible changes)

## Commit message convention

```
v0.X.Y: <type> — <subject>

<optional body>

<optional footer>
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `security`, `chore`.

Examples:
```
v0.7.1: fix — classifier: handle empty payload correctly
v0.8.0: feat — ingestion: YouTube transcript extraction
v0.8.1: security — bot: reject /approve commands from non-owner
```

## Backward compatibility

v0.x.y follows [Semantic Versioning](https://semver.org/):

- **PATCH** (v0.7.0 → v0.7.1): bug fixes, no API change
- **MINOR** (v0.7.x → v0.8.0): new features, backward-compatible
- **MAJOR** (v0.9.x → v1.0.0): breaking changes (rare; announced 1 version ahead)

Vault-side schema (frontmatter fields, file paths) follows the same
rules. Breaking vault-schema changes must include a `monogram migrate`
subcommand that handles the transition.

## Maintenance tasks (for the maintainer's reference)

These are scheduled maintenance items — not contribution asks but
documented here so they don't get lost:

- **Every 6 months**: rotate `MONO_VAULT_PAT` and `BACKUP_GITHUB_PAT`
  (document rotation date in an internal notes doc, not in a public
  commit that would reveal the cadence to attackers)
- **Monthly**: CI runs `monogram backup verify` against
  `<your-github-user>/mono-backup`. Failing = backup corrupt, investigate.
- **Weekly**: review Dependabot PRs. Merge if CI green + no major-version
  bumps. Hold major bumps for the next minor-version release.
- **Per release**: update CHANGELOG.md before tagging.

## Code of Conduct

See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). TL;DR: be kind, assume
good faith, don't harass anyone.

## Questions?

Open a [Discussion](https://github.com/<your-github-user>/monogram/discussions)
for design questions, or an [Issue](https://github.com/<your-github-user>/monogram/issues)
for bug reports and feature requests.
