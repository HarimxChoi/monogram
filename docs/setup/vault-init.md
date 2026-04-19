# Vault init — starting a fresh `mono` repo (v0.3a)

v0.3a is a clean-slate reset: a new empty GitHub repo, new folder
structure, new 5-kind taxonomy. No content migrates from a v0.2
`scheduler` repo. This doc is the step-by-step for first-time setup.

## Preconditions

1. Create an **empty private repo** on GitHub: `<your-github-user>/mono`
   (or whatever `<user>/mono` — substitute accordingly)
2. Update your fine-grained PAT to include the new repo:
   - **Repository access**: "Only select repositories" → add `mono`
   - **Permissions**: Contents `Read and write`, Metadata `Read-only`
3. Edit `.env` in your local clone of this code repo:
   ```
   GITHUB_REPO=<your-github-user>/mono
   ```

The old `<your-github-user>/mono` repo can stay in GitHub as an archive.
Monogram no longer reads or writes it.

## Initialize the vault skeleton

```powershell
# Preview what will be written (dry run)
python scripts/migrate_v0_3.py

# Actually create the skeleton — one commit with ~15 files
python scripts/migrate_v0_3.py --apply
```

The script is **idempotent** — re-running `--apply` on an already-seeded
repo is safe (files exist, write_multi overwrites with same content).

You now have:

```
mono/
├── config.md
├── README.md
├── MEMORY.md
├── board.md
├── projects/archive/.gitkeep
├── life/{shopping,places,career,read-watch,meeting-notes,health,finance}.md
├── life/credentials/credentials.md
├── wiki/index.md
├── log/decisions.md
├── log/runs/.gitkeep
├── reports/weekly/.gitkeep
└── daily/.gitkeep
```

## Customize `config.md`

Open `mono/config.md` in Obsidian (or any text editor) and edit the
YAML frontmatter:

```yaml
primary_language: en        # ISO 639-1: en, ko, ja, zh, es, fr, de, ...
life_categories:
  - shopping
  - places
  - credentials
  - career
  - read-watch
  - meeting-notes
  - health
  - finance
never_read_paths:
  - life/credentials/       # hard-coded; listed here for documentation
```

**Restart `monogram run` for config changes to take effect.**
`VaultConfig` is cached at startup; live reload arrives in v0.4+.

## First-run checklist

After skeleton init:

- [ ] `monogram auth` (first-run Telegram SMS auth)
- [ ] `monogram run` (listener + bot)
- [ ] Drop a test message in Saved Messages:
  - `need wireless earbuds` → should create/append `life/shopping.md`
  - `mark paper-a phase 0 done` → `projects/paper-a.md`
  - `RTMPose does 500 FPS` → `wiki/rtmpose.md` + `wiki/index.md` line
  - `feeling stuck today` → daily_only (drops.md only)
- [ ] Check `<your-github-user>/mono` commits — you should see 4 commits within
  ~10 seconds, one per drop
- [ ] **Credential test:** drop `openai api key sk-TEST123` → verify
  `life/credentials/openai-api-key.md` was created; verify
  `daily/*/drops.md` shows `(redacted)`; verify `MEMORY.md` has NO
  mention of the credential

## Upgrading from v0.2

v0.3 is a clean reset — no automatic data migration from v0.2's
`scheduler` repo. If you want specific v0.2 files in the new vault,
manually copy them:

```powershell
# from your old local clone of scheduler/
copy scheduler/projects/paper-a.md   mono/projects/paper-a.md
# Adjust any wiki/<category>/ files by dropping the subfolder:
copy scheduler/wiki/tech/rtmpose.md   mono/wiki/rtmpose.md
```

The old scheduler repo stays in GitHub as a historical archive.

## Troubleshooting

**`monogram run` errors with Bad credentials:**
PAT doesn't have access to the new `mono` repo. Recheck fine-grained
PAT's "Repository access" list.

**Morning brief is empty every day:**
No `MONOGRAM_WATCH_REPOS` set (digest has nothing to pull from).
Either set that env var or ignore — brief still works, just without a
commits section.

**Classifier routes everything to `daily_only`:**
`life_area` list in config.md is empty or contains only unknown values.
Check the YAML frontmatter parses: `primary_language: en` on its own
line, `life_categories:` followed by indented `- item` lines.
