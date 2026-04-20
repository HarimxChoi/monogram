# GCP e2-micro deployment

Deploy Monogram on Google Cloud's always-free `e2-micro` VM in `us-central1-c`.

- **Cost:** $0/month if you stay within free-tier boundaries
- **Setup:** ~30 minutes
- **Good for:** 24/7 `monogram run` + cron-driven morning/weekly/digest

## Free-tier rules (Apr 2026)

- 1 × e2-micro VM: 2 vCPU (shared), 1 GB RAM
- 30 GB standard persistent disk (`pd-standard`, NOT `pd-balanced`)
- 1 GB egress/month (Monogram uses ~100 MB: Telegram + GitHub + Gemini)
- Regions: `us-central1`, `us-east1`, `us-west1` only
  (Seoul / Tokyo are NOT free — add $7/month for regional VMs)
- No backups, no snapshots, no Ops Agent (all cost extra)

## Prerequisites

- GCP account with billing enabled (required even for free tier)
- `gcloud` CLI installed: https://cloud.google.com/sdk/docs/install
- A fresh `<your-github-user>/mono` repo, initialized via `monogram init`

## 1. Create the VM

```bash
gcloud compute instances create monogram-host \
  --project=YOUR_PROJECT_ID \
  --zone=us-central1-c \
  --machine-type=e2-micro \
  --image-family=ubuntu-2404-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30 \
  --boot-disk-type=pd-standard \
  --network-interface=network-tier=STANDARD,stack-type=IPV4_ONLY,subnet=default \
  --maintenance-policy=MIGRATE \
  --shielded-vtpm \
  --shielded-integrity-monitoring
```

The pricing estimate will show ~$7/month — this is pre-credit. Actual bill under free-tier rules: **$0**.

## 2. SSH in and install

```bash
gcloud compute ssh monogram-host --zone=us-central1-c

# On the VM (one time):
sudo apt update && sudo apt install -y python3.12 python3.12-venv python3-pip git

git clone https://github.com/<your-github-user>/monogram.git
cd monogram
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 3. Configure

```bash
# On the VM:
monogram init
# Answer the prompts interactively. Creates ~/monogram/.env
chmod 600 .env
```

If you already have a working `.env` on your laptop, `scp` it over
instead of re-running init:

```bash
# From your laptop:
gcloud compute scp .env monogram-host:~/monogram/.env --zone=us-central1-c
```

## 4. Telegram auth (one-time SMS)

```bash
# On the VM:
monogram auth
# Enter the SMS code Telegram sends you
chmod 600 monogram_session.session
```

## 5. systemd service for `monogram run`

```bash
# Replace YOUR_USER with the VM's username (usually your Google account local-part)
sudo tee /etc/systemd/system/monogram.service >/dev/null <<EOF
[Unit]
Description=Monogram listener + bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/monogram
Environment=PATH=/home/YOUR_USER/monogram/.venv/bin
ExecStart=/home/YOUR_USER/monogram/.venv/bin/monogram run
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable monogram
sudo systemctl start monogram
sudo systemctl status monogram   # verify it's active
```

Logs: `journalctl -u monogram -f`

## 6. Cron for morning / weekly / digest

VM system clock is UTC. Convert your local time:

- KST (UTC+9): 08:00 KST = 23:00 UTC previous day → `0 23 * * *`
- EST (UTC-5): 08:00 EST = 13:00 UTC → `0 13 * * *`
- CET (UTC+1): 08:00 CET = 07:00 UTC → `0 7 * * *`

```bash
crontab -e
```

Add (adjust timezone):

```
# Morning brief — 08:00 KST (23:00 UTC previous day)
0 23 * * * cd /home/YOUR_USER/monogram && ./.venv/bin/monogram digest --hours 6 && ./.venv/bin/monogram morning

# Sunday weekly — 21:00 KST Sunday (12:00 UTC Sunday)
0 12 * * 0 cd /home/YOUR_USER/monogram && ./.venv/bin/monogram weekly

# Digest every 6 hours (separate from morning to reduce failure blast radius)
0 */6 * * * cd /home/YOUR_USER/monogram && ./.venv/bin/monogram digest --hours 6
```

Verify cron picks up the changes:

```bash
crontab -l
```

## 7. Cost alarm

Cloud Console → Billing → Budgets & alerts → create a **$5/month** budget.
If it ever triggers, something escaped free tier. (Most likely: boot disk
grew past 30 GB or you accidentally provisioned a second VM.)

## 8. Smoke test

```bash
# On the VM:
./.venv/bin/monogram digest --hours 24
./.venv/bin/monogram morning --no-push    # commits brief, skips Telegram
./.venv/bin/monogram weekly --force --no-push
```

All three should complete without error and produce commits on `<your-github-user>/mono`.

## Diagnostics

```bash
# Is monogram run alive?
sudo systemctl status monogram

# Last 100 lines of service logs
sudo journalctl -u monogram -n 100 --no-pager

# Disk usage (must stay under 30 GB)
df -h /

# Memory (e2-micro has 1 GB; OOM kills monogram silently)
free -m

# Scheduled job run logs (committed to the vault repo)
# Look at log/runs/YYYY-MM-DD-<job>.md via GitHub or:
git -C ~/monogram-vault log --oneline log/runs/
```

### Memory pressure: add swap

```bash
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Latency note

Korea → us-central1 ~180ms one-way. Monogram is not latency-sensitive:
- Listener is async and the Telegram→commit round-trip is ~2s regardless
- Morning brief runs on cron; extra 0.2s doesn't matter
- Pro call itself takes 2-3s + network

If your use feels laggy on real-time bot queries, pay ~$7/month for
`asia-northeast3-a` (Seoul) to cut latency. Monogram works identically
in any region.

## Uninstall

```bash
gcloud compute instances delete monogram-host --zone=us-central1-c
```

Nothing persists after deletion — the vault lives in GitHub, not on the VM.
