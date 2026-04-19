# Monogram Quick Capture (Obsidian plugin)

One-hotkey capture for Monogram. Writes a queue file to
`<vault>/daily/<today>/queue-<timestamp>-<rand>.md`; Monogram's queue
poller picks it up within 2 minutes and runs it through the pipeline.

## Manual install (v0.5)

1. Download the `monogram-quick-capture-<version>.zip` from
   [GitHub Releases](https://github.com/HarimxChoi/monogram/releases).
2. Unzip into `<your-vault>/.obsidian/plugins/monogram-quick-capture/`
   (create the directory if missing).
3. In Obsidian: **Settings → Community plugins → Enable** and toggle
   **Monogram Quick Capture** on.
4. **Settings → Hotkeys** → search for "Monogram" → bind a key.

## Usage

- Press your hotkey. A small modal opens.
- Type anything. Cmd/Ctrl+Enter to submit, Esc to cancel.
- A new file lands in `<vault>/daily/<today>/queue-*.md`.
- Your vault sync layer (Obsidian Git, Obsidian Sync, …) pushes it to
  the mono repo.
- Monogram's queue poller reads the file, runs the pipeline, deletes
  the queue file on success.

## Sync layer gotchas

The plugin only writes files. Propagation to the `mono` GitHub repo is
your sync layer's job. Silent exclusion here is the #1 reason the
pipeline "appears broken" after a successful Obsidian capture.

**Check before relying on it:**

1. **`.gitignore` at your vault root.** Some popular Obsidian Git
   templates include `daily/` or `*.md` scratch patterns by default.
   Open `<vault>/.gitignore` and confirm neither of these lines is
   present (or comment them out):
   ```
   daily/
   queue-*.md
   ```
   Also check for broader globs like `**/queue-*` or directory
   exclusions higher up.

2. **Obsidian Git — "Files to ignore" plugin setting.** Obsidian Git
   has its own ignore list (Settings → Obsidian Git → *Files to ignore*).
   It's independent of `.gitignore`. Keep this empty, or make sure no
   pattern matches `daily/` or `queue-*.md`.

3. **Push-on-save or auto-pull interval.** If your sync layer doesn't
   push on every save, queue files may sit locally for minutes before
   Monogram sees them. That's fine for the 2-minute poll interval, but
   longer delays cause duplicate-looking drops. Set auto-push interval
   to ≤ 2 min in Obsidian Git, or enable *Push on save*.

4. **End-to-end test before trusting it.** After install, capture a
   test drop. Wait 3 minutes. Verify:
   - The queue file appears in the `mono` repo on GitHub
   - The queue file is then deleted (poller consumed it)
   - A new commit landed from the pipeline (`daily/<today>/drops.md`
     updated + target file written)
   If any step fails, check the relevant sync layer — the plugin
   itself has no network calls and cannot report "did it ship".

5. **iCloud Drive / Dropbox users.** These sync filesystem only, not
   git. You'll also need Obsidian Git or a cron-driven `git push` to
   reach GitHub. Two sync layers stacked = two places `queue-*.md`
   might be silently excluded.

## Build from source

```bash
cd obsidian-plugin
npm install
npm run build
```

Copies `main.js` + `manifest.json` into `obsidian-plugin/` — zip those
two + `styles.css` (if any) and install per the manual-install steps.

## Not in scope

- No HTTP / network calls — pure vault write.
- No settings beyond `dailyRootDir` (defaults to `daily`).
- Not submitted to Obsidian Community Plugins (manual zip only for v0.5).
