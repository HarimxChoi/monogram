# GCP Cloud Storage web UI (stable URL)

Deliver the Monogram dashboard via GCP Cloud Storage. Stable URL you can
bookmark / add to your home screen. Content is encrypted client-side —
the bucket only ever holds ciphertext.

- **Cost:** ~$0.01/month at personal scale (30 regenerations/day)
- **Setup:** ~5 minutes on any OS
- **URL format:** `https://storage.googleapis.com/<bucket>/<path_slug>/index.html`

## Prerequisites

- GCP account with billing enabled (required even for near-zero cost)
- `gcloud` CLI ([cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install))
- A password ≥ 16 chars (use your password manager's generator)

## 1. Create project + bucket

Windows / PowerShell:

```powershell
# Auth (opens browser once)
gcloud auth login

$PROJECT_ID = "monogram-yourusername-2026"        # must be globally unique
$BUCKET     = "yourusername-monogram-webui"       # also globally unique

gcloud projects create $PROJECT_ID --name="Monogram"
gcloud config set project $PROJECT_ID

# Link billing (required for API access; $0 at personal scale)
gcloud beta billing accounts list
# copy the ACCOUNT_ID and then:
gcloud beta billing projects link $PROJECT_ID --billing-account=ACCOUNT_ID

# Enable storage API
gcloud services enable storage.googleapis.com

# Create bucket in a free-tier region
gcloud storage buckets create gs://$BUCKET `
  --location=us-central1 `
  --uniform-bucket-level-access
```

macOS / Linux: same commands, replace `$VAR` → `${VAR}`.

## 2. Make the bucket publicly readable

The ciphertext is encrypted client-side, so `allUsers:objectViewer` is safe.

```powershell
gcloud storage buckets update gs://$BUCKET --no-public-access-prevention
gcloud storage buckets add-iam-policy-binding gs://$BUCKET `
  --member=allUsers --role=roles/storage.objectViewer
```

## 3. Service account with per-bucket write access

Least privilege — the key can write *only* to this bucket.

```powershell
$SA_NAME  = "monogram-webui"
$SA_EMAIL = "$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud iam service-accounts create $SA_NAME --display-name="Monogram Web UI"

gcloud storage buckets add-iam-policy-binding gs://$BUCKET `
  --member="serviceAccount:$SA_EMAIL" `
  --role=roles/storage.objectAdmin

# Download JSON key
mkdir -p "$env:USERPROFILE\.config\monogram"
gcloud iam service-accounts keys create `
  "$env:USERPROFILE\.config\monogram\gcp-sa.json" `
  --iam-account=$SA_EMAIL
```

## 4. Optional: lifecycle rule

Delete stale objects after 7 days (Monogram regenerates on every
`/webui`, so old uploads are safe to collect). Keeps the bucket tidy,
reduces egress.

```powershell
@"
{
  "lifecycle": {
    "rule": [
      { "action": { "type": "Delete" }, "condition": { "age": 7 } }
    ]
  }
}
"@ | Out-File -Encoding utf8 "$env:TEMP\lifecycle.json"

gcloud storage buckets update gs://$BUCKET `
  --lifecycle-file="$env:TEMP\lifecycle.json"
```

## 5. Wire into Monogram

Add to `.env`:

```
GOOGLE_APPLICATION_CREDENTIALS=C:\Users\yourusername\.config\monogram\gcp-sa.json
MONOGRAM_WEBUI_PASSWORD=<your 16+ char password>
```

Edit `mono/config.md` frontmatter (or use bot commands):

```yaml
webui_mode: gcs
webui_gcs:
  bucket: yourusername-monogram-webui
  path_slug: main
```

Restart `monogram run` to pick up the new config.

## 6. Generate the dashboard

In Telegram Saved Messages:

```
/webui
```

Bot responds with the URL. Open on any device, enter the password.

## Rotation & recovery

**Rotate password:**
```
monogram webui rotate-password
```

After rotation, run `/webui` once — new ciphertext overwrites the old
one in the bucket. Old password immediately useless.

**Lost password:** there is no recovery. AES-GCM with 600k PBKDF2
iterations means offline brute force is infeasible. Rotate the password
as above + regenerate: you get a clean slate, and no old version is
decryptable with any known key.

## Diagnostics

| Symptom | Check |
|---|---|
| `/webui` says 403 | IAM binding on bucket (step 3) |
| URL returns 404 | `webui_gcs.path_slug` matches config; dashboard published at least once |
| "GOOGLE_APPLICATION_CREDENTIALS not set" | `.env` path exists and points at the JSON |
| Page loads, password always fails | Out-of-sync: rotate + re-publish |

## Cost expectation (April 2026 pricing)

Storage class: Standard, us-central1.

- Storage: ~1 MB peak per day (encrypted shell is ~300 KB; lifecycle keeps ≤ 7 days)
- Class A ops (writes): 30-60 per day
- Class B ops (reads): 5-50 per day (only when you view)
- Egress: ~20-50 MB/month

Monthly total: **well under $0.05**. Always-free tier covers the class
ops and most egress. If you see a bill over $1/month, something escaped
(a misconfigured bucket lifecycle, an accidental second bucket, etc.).
Set a **$5/month budget alert** to catch regressions early.

## Security notes

- The bucket is **public**. The ciphertext is safe to serve publicly
  because decryption happens in the browser with a password only you
  know. This is the whole design.
- `MONOGRAM_WEBUI_PASSWORD` in `.env` is plaintext — treat `.env` like a
  private key. On unix `monogram init` sets `0600` perms. Keep the file
  off git (it's in `.gitignore`).
- The Google Cloud service account JSON can write to this bucket only.
  Don't reuse it for other purposes.
- PBKDF2 iterations: 600,000. AES-256-GCM. 16-char minimum password
  gives ~80-100 bits of practical entropy (with a password manager).
  Offline brute force is beyond feasible for any adversary within a
  personal tool's threat model.
