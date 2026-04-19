# Deploying Monogram

End-to-end walkthrough from empty Google Cloud account to a Monogram
instance processing real drops on a 24/7 GCP `e2-micro` free-tier VM.

This guide is for the single-person personal-use case — the system's
intended deployment.

---

## 0 · What you'll have at the end

A running Monogram instance on a GCP always-free VM that:

1. Listens to your Telegram Saved Messages
2. Processes drops through the 5-stage pipeline
3. Commits structured markdown to `<your-github-user>/mono`
4. Backs up nightly to `<your-github-user>/mono-backup`
5. Sends you a morning brief at 08:00 local time
6. Sends you a weekly rollup Sunday evening
7. Exposes `/stats`, `/approve_<token>`, `/deny_<token>` via Telegram
8. Runs the eval harness on a cassette-replay CI loop

Total cost on GCP free tier: **$0/month**. Gemini free tier: **$0** for
~50 drops/day.

---

## 1 · Prerequisites you create yourself

Before any code, create these accounts and have them ready:

- **GitHub** account with ability to create private repos
- **Google Cloud Platform** account with billing enabled (free tier
  requires a card on file but is genuinely free for this workload)
- **Telegram** account
- **Gemini API** access via [Google AI Studio](https://aistudio.google.com)
  (free tier is fine; paid tier optional)
- **Local dev machine** with git, Python 3.10+, and SSH

Estimated setup time: **60–90 minutes end-to-end** if you've never done
any of this before. 30 minutes if you have.

---

## 2 · Telegram credentials

### 2.1 API credentials (for Telethon listener)

Go to <https://my.telegram.org/apps>, sign in with your phone number,
and create a new application.

- **App title:** `monogram-personal` (or anything)
- **Short name:** `monogram`
- **Platform:** Other

Record the two values you'll be shown:

- `TELEGRAM_API_ID` (integer)
- `TELEGRAM_API_HASH` (32-char hex)

These live in `.env` on the deployment host.

### 2.2 Bot token (for the approval bot)

In Telegram, message `@BotFather`:

```
/newbot
<bot name>     e.g. monogram-approve-bot
<username>     e.g. yourname_monogram_bot
```

Record the token `@BotFather` returns — format `1234567890:AAF...`.
This is `TELEGRAM_BOT_TOKEN`.

### 2.3 Your user ID

Message `@userinfobot` in Telegram. It returns your numeric user ID.
This is `TELEGRAM_USER_ID`.

> **Important:** Monogram uses this as an auth gate. Every bot command
> (`/approve_<token>`, `/deny_<token>`, `/stats`, `/eval_*`) checks the
> sender's ID against this value. No one else can command your bot.

### 2.4 Bot → Saved Messages permission

The bot needs to be able to reply to you. Message your bot once (say
`hi`) — that establishes the DM channel.

---

## 3 · GitHub vault + backup repos

### 3.1 Create the vault repo

```bash
gh repo create <your-github-user>/mono \
  --private \
  --description "Personal knowledge vault — managed by monogram" \
  --clone=false
```

Or via the web UI at <https://github.com/new> — same thing.

Keep it **empty for now**. `monogram init` will populate the initial
structure on first run.

### 3.2 Create the backup repo

```bash
gh repo create <your-github-user>/mono-backup \
  --private \
  --description "Nightly mirror of mono — for disaster recovery" \
  --clone=false
```

> **Security rationale:** A separate repo with its own PAT means a
> compromise of either PAT doesn't take out both copies. For maximum
> isolation, use a second GitHub account for the backup — see
> [SECURITY.md](SECURITY.md).

### 3.3 Fine-grained PAT for vault writes

Go to <https://github.com/settings/tokens?type=beta>. Create a
fine-grained personal access token.

- **Token name:** `monogram-vault-write`
- **Expiration:** 1 year (rotate annually — calendar it now)
- **Repository access:** *Only select repositories* → `<your-github-user>/mono`
- **Permissions:**
  - Contents: **Read and write**
  - Metadata: **Read-only** (auto-selected)

Record the token. This is `GITHUB_PAT`. Format: `github_pat_...`.

### 3.4 Fine-grained PAT for backups

Repeat the process for the backup repo:

- **Token name:** `monogram-backup-write`
- **Repository access:** *Only select repositories* → `<your-github-user>/mono-backup`
- **Permissions:** Contents: Read and write; Metadata: Read-only

Record as `BACKUP_GITHUB_PAT`. This is the isolation we just discussed
— the vault PAT cannot write to the backup and vice versa.

---

## 4 · LLM provider — Gemini free tier

Go to <https://aistudio.google.com/app/apikey> and create a new API
key. Record it — this is `GEMINI_API_KEY`.

Free-tier limits (verify in AI Studio, they change periodically):

| Model | Monogram use | Daily limit |
|---|---|---|
| `gemini-2.5-flash-lite` | Classifier, Extractor, Verifier | ~1000 RPD |
| `gemini-2.5-flash` | Morning brief, weekly rollup | ~250 RPD |
| `gemini-2.5-pro` | Occasional escalation | ~100 RPD |

A typical 20-drop day uses ~60 Flash-Lite calls + 1 Flash call. You're
not going to hit the ceiling on personal use.

**If you want provider diversity** (recommended for the verifier to
reduce evaluator-evaluatee correlation), also set up one of:
Anthropic API, OpenAI API, or a local Ollama server. Configurable via
`cheap`/`mid`/`pro` tier overrides in `config.md`. See
[docs/setup/llm-providers.md](docs/setup/llm-providers.md).

---

## 5 · GCP `e2-micro` VM (the always-free tier)

### 5.1 Project + billing

```bash
# Install gcloud CLI once — see https://cloud.google.com/sdk/docs/install
gcloud auth login
gcloud projects create monogram-personal-$(date +%s) \
  --set-as-default
```

Link billing: <https://console.cloud.google.com/billing>. You must have
billing enabled even for the free tier. You will not be charged if you
stay within the free-tier limits for `e2-micro`.

### 5.2 Create the VM

```bash
gcloud compute instances create monogram \
  --zone=us-west1-a \
  --machine-type=e2-micro \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=monogram
```

Why these choices:

- **`us-west1-a`** (Oregon), `us-central1-a` (Iowa), or `us-east1-b`
  (S. Carolina) are the only free-tier regions for `e2-micro` — pick
  whichever is closest geographically.
- **`pd-standard` 30 GB** is the free-tier disk cap. Don't exceed this.
- **`e2-micro`** is the free-tier VM size: 2 vCPUs burst, 1 GB RAM.
  Monogram's steady-state footprint is ~200 MB.

### 5.3 SSH in

```bash
gcloud compute ssh monogram --zone=us-west1-a
```

### 5.4 System-level setup on the VM

```bash
sudo apt update && sudo apt upgrade -y

# Python + build tools
sudo apt install -y python3.12 python3.12-venv python3-pip git ripgrep

# For HWP (Korean) extraction — skip if you don't need this
sudo apt install -y libreoffice

# For PDF extraction fallback — skip if you don't need this
sudo apt install -y poppler-utils
```

> **HWP hardening:** if you install LibreOffice, confirm version
> `≥25.2.1` to get the CVE-2024-12425/12426 and CVE-2025-1080 fixes.
> Run `libreoffice --version` to check. Older Ubuntu releases ship
> older LibreOffice — upgrade via the LibreOffice PPA if needed.

---

## 6 · Install Monogram on the VM

```bash
# Still SSH'd into the VM
python3.12 -m venv ~/monogram-env
source ~/monogram-env/bin/activate

# Once v1.0 is on PyPI:
pip install 'monogram[ingestion-all]'

# Until then, install from git:
pip install -e 'git+https://github.com/<your-github-user>/monogram.git#egg=monogram[ingestion-all]'
```

Confirm the install:

```bash
monogram --version
# Expected: 0.8.0.dev0 (or later)
```

### 6.1 Environment file

Create `~/.config/monogram/.env`:

```bash
mkdir -p ~/.config/monogram
cat > ~/.config/monogram/.env <<'EOF'
# Telegram
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_BOT_TOKEN=1234567890:AAF...
TELEGRAM_USER_ID=123456789

# GitHub — vault
GITHUB_PAT=github_pat_11A...
GITHUB_REPO=<your-github-user>/mono

# GitHub — backup (separate PAT!)
BACKUP_GITHUB_PAT=github_pat_11B...
BACKUP_GITHUB_REPO=<your-github-user>/mono-backup

# LLM
GEMINI_API_KEY=AIza...

# Optional — if using additional providers
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-proj-...
EOF

chmod 600 ~/.config/monogram/.env
```

Monogram reads `.env` from the current working directory by default;
export `MONOGRAM_DOTENV=~/.config/monogram/.env` if you start the
service from elsewhere, or just `cd ~/.config/monogram` in the
systemd unit (shown below).

### 6.2 Interactive initialization

```bash
cd ~/.config/monogram
monogram init
```

This wizard:

- Validates every credential (Telegram, GitHub, LLM provider reachable)
- Creates the initial vault structure in `<your-github-user>/mono`
  (`identity/`, `daily/`, `wiki/`, `scheduler/`, `config.md`, etc.)
- Writes a sample `config.md` with sensible defaults
- Commits the scaffolding

Expected duration: ~90 seconds.

### 6.3 Telegram first-run auth

```bash
monogram auth
```

This triggers the Telethon first-time flow: you'll get an SMS or
Telegram code, enter it, and Monogram writes a session file. Keep
that file safe (it's `.gitignore`-protected by default).

### 6.4 Smoke test

Run the pipeline once against a fake drop:

```bash
monogram digest "Testing monogram on new VM. This should classify as life/misc."
```

Expected output: pipeline-stage log, classification decision, draft
write. No commit to the vault yet (digest is dry-run).

Now the real smoke test — run the listener briefly:

```bash
monogram run
# In Telegram Saved Messages, drop: "Test drop from new deployment"
# Wait a few seconds.
# You should see a new commit on <your-github-user>/mono.
# Ctrl-C to stop.
```

If the commit appears, the system is working end-to-end.

---

## 7 · Run as a service

A `systemd` unit so Monogram restarts on reboot and survives SSH
disconnects.

```bash
sudo tee /etc/systemd/system/monogram.service > /dev/null <<EOF
[Unit]
Description=Monogram personal pipeline
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/$USER/.config/monogram
Environment="PATH=/home/$USER/monogram-env/bin:/usr/bin:/bin"
EnvironmentFile=/home/$USER/.config/monogram/.env
ExecStart=/home/$USER/monogram-env/bin/monogram run
Restart=always
RestartSec=10

# Light hardening
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/$USER/.config/monogram

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable monogram
sudo systemctl start monogram
```

Check status:

```bash
sudo systemctl status monogram
journalctl -u monogram -f   # follow live logs
```

### 7.1 Scheduled jobs (morning + weekly + backup)

The listener runs continuously; the scheduler tasks run once daily.
Use cron:

```bash
crontab -e
```

Append:

```cron
# Every morning at 08:00 — briefing
0 8 * * * /home/$USER/monogram-env/bin/monogram morning >> /home/$USER/.config/monogram/morning.log 2>&1

# Sundays at 19:00 — weekly rollup
0 19 * * 0 /home/$USER/monogram-env/bin/monogram weekly >> /home/$USER/.config/monogram/weekly.log 2>&1

# Nightly at 02:00 — backup mirror
0 2 * * * /home/$USER/monogram-env/bin/monogram backup mirror >> /home/$USER/.config/monogram/backup.log 2>&1
```

Adjust times to your timezone. The `e2-micro` is in UTC by default —
run `sudo timedatectl set-timezone America/Los_Angeles` (or your zone)
first if you want local-time crons.

---

## 8 · Verify

After 24 hours of the listener + scheduler running, confirm:

- [ ] `<your-github-user>/mono` has commits from real drops
- [ ] Morning brief arrived in Telegram at 08:00
- [ ] `monogram stats` shows non-zero pipeline runs
- [ ] `monogram backup verify` reports "✓ Backup verified"
- [ ] `journalctl -u monogram --since "24 hours ago"` has no errors

If any of these fail, the logs tell you why. The pipeline's
observability layer (`log/pipeline.jsonl`) shows every stage transition
for every drop.

---

## 9 · GitHub Actions CI (optional but recommended)

If you fork Monogram, enable the CI workflows on your fork.

### 9.1 Repository secrets

Settings → Secrets and variables → Actions → New repository secret.

Add all four:

- `GEMINI_API_KEY` — same as your `.env`
- `MONO_VAULT_PAT` — the vault PAT
- `BACKUP_VAULT_PAT` — the backup PAT
- `BACKUP_VAULT_REPO` — value `<your-github-user>/mono-backup`

### 9.2 Workflows

Three pre-configured workflows run automatically on push / PR /
schedule:

- `tests.yml` — production test suite on Python 3.10 + 3.12
- `eval.yml` — cassette replay + scheduled harvest (Sun + Wed 18:00 UTC)
- `backup-verify.yml` — monthly restore drill on the 1st @ 04:00 UTC

First-run notes:

- Eval cassettes must be recorded before the replay suite goes green.
  Run locally once: `pytest evals/ --record -q` and commit the
  cassettes. Gemini free tier's 20-requests-per-day quota means
  full-record takes multiple days — use
  `MONOGRAM_EVAL_MISS_SKIP=1` to backfill incrementally.

---

## 10 · PyPI release (maintainers only)

Only relevant if you're shipping Monogram to PyPI yourself — not needed
for personal use.

### 10.1 Trusted Publisher registration

Go to <https://pypi.org/manage/account/publishing/>. Add a *pending
publisher*:

- **Project name:** `monogram` (or your fork's name)
- **Owner:** `<your-github-user>`
- **Repository:** `monogram`
- **Workflow name:** `publish.yml`
- **Environment name:** `pypi`

No token is stored — OIDC handles authentication at release time.

### 10.2 TestPyPI dry run

```bash
git tag v1.0.0-rc1
git push origin v1.0.0-rc1
```

The `publish.yml` workflow fires on any `v*.*.*-*` tag and publishes
to **TestPyPI only**. Verify in a clean venv:

```bash
python3.12 -m venv /tmp/test-monogram
source /tmp/test-monogram/bin/activate
pip install -i https://test.pypi.org/simple/ monogram==1.0.0rc1
monogram --version
```

### 10.3 Real release

```bash
# Bump pyproject.toml version to 1.0.0 first
git commit -am "v1.0.0"
git tag -a v1.0.0 -m "v1.0.0"
git push origin main v1.0.0
```

The workflow's tag-version match step will abort if `pyproject.toml`
disagrees with the tag. On success: real PyPI release, sigstore-signed
attestations, GitHub release auto-drafted.

---

## 11 · Troubleshooting

### "Can't connect to Telegram"

Test auth separately:

```bash
source ~/monogram-env/bin/activate
cd ~/.config/monogram
monogram auth
```

If prompted for a code and none arrives, the issue is Telegram's own
rate limiting for new API IDs. Wait a few minutes and retry.

### "GitHub push failed: 403"

PAT expired or lost permission. Regenerate it following §3.3.

### "Morning brief didn't arrive"

Check crontab: `crontab -l`. Check log: `tail -50
~/.config/monogram/morning.log`. If the job ran but Telegram didn't
receive, likely bot token expired (rare) or network issue.

### "Pipeline taking >30 seconds per drop"

Check `monogram stats` — which stage dominates? If classifier: Gemini
quota may be saturated. If extractor: the drop may have URLs hitting
slow extraction paths. Log tail for specifics.

### Backup-verify failing with "source count X backup count Y (delta
> 5%)"

Either the nightly mirror job stopped running, or you made a very
large batch of drops between mirror and verify. Re-run `monogram
backup mirror` manually and re-check.

### VM ran out of disk

GCP free-tier cap is 30 GB. Check:

```bash
df -h
du -sh ~/monogram-env ~/.config/monogram
```

Most likely culprit: `~/monogram-env` or accumulated log files.
Rotate logs:

```bash
sudo apt install logrotate
# or manually:
truncate -s 0 ~/.config/monogram/*.log
```

---

## 12 · Monthly maintenance (10 min)

- [ ] `pip install --upgrade monogram` (after v1.0.0 is on PyPI)
- [ ] Check `monogram stats --window 30` — any latency regression?
- [ ] Check PAT expiration dates (both vault + backup)
- [ ] Confirm backup-verify CI ran and was green
- [ ] Skim `CHANGELOG.md` for anything that might affect your config

Annually: rotate PATs (both vault + backup) and Gemini API key.

---

## 13 · What's next

Once you have a week of drops in the vault:

- `monogram stats --save` — commit a baseline for drift detection
- Try `monogram search` for quick grep-style lookup
- Connect Monogram to Claude Desktop via MCP
  ([docs/setup/mcp-clients.md](docs/setup/mcp-clients.md))
- Install the Obsidian plugin for one-click capture from the desktop

Once you have a month of drops:

- Review classifier accuracy via `monogram eval run`
- Consider enabling the harvest loop to grow the fixture base
  (Telegram-approval-gated; read [docs/eval.md](docs/eval.md))
- Tune `wiki/` vs `scheduler/` routing by editing `config.md`

Once you have three months of drops, the vault starts functioning as
the second-brain it was designed to be. That's the point.

---

## Appendix A · File locations summary

| File | Location | Purpose |
|---|---|---|
| `.env` | `~/.config/monogram/.env` | Credentials (chmod 600) |
| `session` | `~/.config/monogram/*.session` | Telethon session (don't commit) |
| Systemd unit | `/etc/systemd/system/monogram.service` | Service definition |
| Cron | `crontab -l` | Morning / weekly / backup schedules |
| Logs | `journalctl -u monogram`, `~/.config/monogram/*.log` | Runtime + scheduled-job logs |
| Vault | `<your-github-user>/mono` | Your content |
| Backup | `<your-github-user>/mono-backup` | Nightly mirror |

## Appendix B · Minimum-permissions reference

Fine-grained PATs (both vault and backup):

| Permission | Level | Why |
|---|---|---|
| Contents | Read and write | Commit drops / mirror |
| Metadata | Read-only | List repo files |

Nothing else. Not Issues, not Actions, not Packages. If a PAT has
more than these two, regenerate it with less.

## Appendix C · Cost sanity check

Steady-state monthly cost for a single-person deployment:

| Item | Cost |
|---|---|
| GCP `e2-micro` + 30 GB standard disk | $0 (free tier) |
| GCP egress (20 drops/day, ~3 MB/day) | $0 (under 1 GB/month free) |
| Gemini API (20 drops/day × ~60 calls) | $0 (under free-tier cap) |
| GitHub private repos | $0 (unlimited on free tier) |
| PyPI publishing | $0 |
| **Total** | **$0 / month** |

This remains true up to ~30 drops/day. Above that, Gemini free tier may
throttle — upgrade the tier routing (Flash-Lite → paid tier) or the
provider.
