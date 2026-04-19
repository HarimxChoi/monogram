/* Monogram Quick Capture
 *
 * One hotkey → modal → write queue file to daily/<today>/queue-<timestamp>-<rand>.md
 * The Monogram agent's queue poller picks it up within 2 minutes.
 *
 * Zero network calls from this plugin — the vault's sync layer (Obsidian Git,
 * Obsidian Sync, iCloud Drive, etc.) handles propagation to the mono repo.
 */

import {
  App,
  Modal,
  Notice,
  Plugin,
  Setting,
  TFolder,
  normalizePath,
} from "obsidian";

const PLUGIN_NAME = "Monogram Quick Capture";

interface MonogramSettings {
  dailyRootDir: string;
}

const DEFAULT_SETTINGS: MonogramSettings = {
  dailyRootDir: "daily",
};

export default class MonogramQuickCapture extends Plugin {
  settings: MonogramSettings = DEFAULT_SETTINGS;

  async onload() {
    await this.loadSettings();

    this.addCommand({
      id: "monogram-quick-capture",
      name: "Quick capture drop",
      callback: () => this.openCaptureModal(),
    });

    this.addSettingTab(new MonogramSettingsTab(this.app, this));
  }

  async loadSettings() {
    this.settings = Object.assign(
      {},
      DEFAULT_SETTINGS,
      await this.loadData(),
    );
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  openCaptureModal() {
    new CaptureModal(this.app, (text) => this.writeQueueFile(text)).open();
  }

  async writeQueueFile(text: string) {
    const trimmed = (text || "").trim();
    if (!trimmed) {
      new Notice(`${PLUGIN_NAME}: empty capture, nothing written`);
      return;
    }

    const today = _todayIsoDate();
    const dir = normalizePath(`${this.settings.dailyRootDir}/${today}`);

    // Ensure directory exists (vault.createFolder throws if present → swallow)
    const folderOrFile = this.app.vault.getAbstractFileByPath(dir);
    if (folderOrFile == null) {
      try {
        await this.app.vault.createFolder(dir);
      } catch (e) {
        // Race-safe: another path may have created it between checks
      }
    } else if (!(folderOrFile instanceof TFolder)) {
      new Notice(`${PLUGIN_NAME}: ${dir} is a file, not a folder`);
      return;
    }

    const ts = Math.floor(Date.now() / 1000);
    const rand = _random3();
    const filename = `queue-${ts}-${rand}.md`;
    const path = normalizePath(`${dir}/${filename}`);

    const body =
      `---\n` +
      `captured_at: ${_localIso()}\n` +
      `source: obsidian-plugin\n` +
      `version: 1\n` +
      `---\n` +
      trimmed +
      `\n`;

    try {
      await this.app.vault.create(path, body);
      new Notice(`${PLUGIN_NAME}: captured → ${filename}`);
    } catch (e) {
      new Notice(`${PLUGIN_NAME}: write failed — ${(e as Error).message}`);
    }
  }
}

function _todayIsoDate(): string {
  const d = new Date();
  const yyyy = d.getFullYear().toString().padStart(4, "0");
  const mm = (d.getMonth() + 1).toString().padStart(2, "0");
  const dd = d.getDate().toString().padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function _localIso(): string {
  // Local-timezone ISO with offset, e.g. 2026-04-18T14:30:22+09:00
  const d = new Date();
  const pad = (n: number) => n.toString().padStart(2, "0");
  const tzMin = -d.getTimezoneOffset();
  const sign = tzMin >= 0 ? "+" : "-";
  const absMin = Math.abs(tzMin);
  const tzH = pad(Math.floor(absMin / 60));
  const tzM = pad(absMin % 60);
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
    `${sign}${tzH}:${tzM}`
  );
}

function _random3(): string {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < 3; i++) {
    out += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return out;
}

class CaptureModal extends Modal {
  onSubmit: (text: string) => Promise<void> | void;
  text = "";

  constructor(app: App, onSubmit: (text: string) => Promise<void> | void) {
    super(app);
    this.onSubmit = onSubmit;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h3", { text: "Monogram — quick capture" });

    const textarea = contentEl.createEl("textarea");
    textarea.rows = 6;
    textarea.style.width = "100%";
    textarea.placeholder =
      "e.g. need wireless earbuds\nor: mark paper-a phase 0 done";
    textarea.addEventListener("input", (e) => {
      this.text = (e.target as HTMLTextAreaElement).value;
    });
    textarea.addEventListener("keydown", async (e) => {
      // Cmd/Ctrl+Enter to submit
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        await this.submit();
      }
      if (e.key === "Escape") {
        this.close();
      }
    });

    setTimeout(() => textarea.focus(), 0);

    new Setting(contentEl).addButton((btn) =>
      btn
        .setButtonText("Capture")
        .setCta()
        .onClick(async () => {
          await this.submit();
        }),
    );
  }

  async submit() {
    const text = this.text;
    this.close();
    await this.onSubmit(text);
  }

  onClose() {
    this.contentEl.empty();
  }
}

class MonogramSettingsTab extends (window as any).PluginSettingTab {
  plugin: MonogramQuickCapture;

  constructor(app: App, plugin: MonogramQuickCapture) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "Monogram Quick Capture" });

    new Setting(containerEl)
      .setName("Daily root directory")
      .setDesc(
        "Where queue files land under your vault. Default: daily (matches Monogram's daily/YYYY-MM-DD/ layout).",
      )
      .addText((t) =>
        t
          .setPlaceholder("daily")
          .setValue(this.plugin.settings.dailyRootDir)
          .onChange(async (v) => {
            this.plugin.settings.dailyRootDir = v.trim() || "daily";
            await this.plugin.saveSettings();
          }),
      );
  }
}
