/* The Archivist — Obsidian plugin. It writes nothing: every action is a request to the local Archivist
   (archivist.py), which captures to the inbox, proposes through the organ, and moves only on the keeper's ratify. */
const { Plugin, ItemView, Notice, Modal, Setting, PluginSettingTab } = require("obsidian");
const VIEW = "archivist-page";
const DEFAULTS = { url: "http://127.0.0.1:8765", polish: true };

class ArchivistView extends ItemView {
  constructor(leaf, plugin) { super(leaf); this.plugin = plugin; }
  getViewType() { return VIEW; }
  getDisplayText() { return "The Archivist"; }
  getIcon() { return "library"; }
  async onOpen() {
    const el = this.containerEl.children[1]; el.empty(); el.style.padding = "0";
    const f = el.createEl("iframe", { attr: { src: this.plugin.settings.url + "/", style: "width:100%;height:100%;border:0;background:#faf8f3" } });
    f.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
  }
}

class AskModal extends Modal {
  constructor(app, plugin, seed) { super(app); this.plugin = plugin; this.seed = seed || ""; }
  onOpen() {
    const { contentEl } = this; contentEl.createEl("h3", { text: "Ask the Archivist" });
    const ta = contentEl.createEl("textarea", { attr: { style: "width:100%;min-height:80px" } }); ta.value = this.seed;
    const out = contentEl.createEl("pre", { attr: { style: "white-space:pre-wrap;max-height:50vh;overflow:auto" } });
    new Setting(contentEl).addButton(b => b.setButtonText("Ask").setCta().onClick(async () => {
      out.setText("retrieving and answering…");
      const r = await this.plugin.api("/api/ask", { q: ta.value });
      out.setText(r.markdown || ("! " + r.error)); this.last = r.markdown || "";
    })).addButton(b => b.setButtonText("Copy the Markdown").onClick(() => { if (this.last) navigator.clipboard.writeText(this.last); new Notice("copied"); }));
  }
  onClose() { this.contentEl.empty(); }
}

module.exports = class ArchivistPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULTS, await this.loadData());
    this.registerView(VIEW, leaf => new ArchivistView(leaf, this));
    this.addRibbonIcon("library", "Open the Archivist", () => this.openPage());
    this.addCommand({ id: "open", name: "Open the Archivist", callback: () => this.openPage() });
    this.addCommand({ id: "send-selection", name: "Send the selection to the Archivist (capture and place)", editorCallback: (ed, view) => {
      const sel = ed.getSelection(); if (!sel.trim()) { new Notice("select some text first"); return; }
      this.capture(`${view.file ? view.file.basename : "Selection"} — selection`, sel, view.file ? view.file.path : "");
    }});
    this.addCommand({ id: "send-note", name: "Send this whole note to the Archivist (capture a copy and place it)", callback: async () => {
      const f = this.app.workspace.getActiveFile(); if (!f) return;
      this.capture(f.basename, await this.app.vault.read(f), f.path);
    }});
    this.addCommand({ id: "ask", name: "Ask the Archivist", editorCallback: (ed) => new AskModal(this.app, this, ed.getSelection()).open() });
    this.addSettingTab(new (class extends PluginSettingTab {
      constructor(app, plugin) { super(app, plugin); this.plugin = plugin; }
      display() { const c = this.containerEl; c.empty();
        new Setting(c).setName("Archivist URL").setDesc("Where archivist.py serves").addText(t => t.setValue(this.plugin.settings.url).onChange(async v => { this.plugin.settings.url = v.trim() || DEFAULTS.url; await this.plugin.saveData(this.plugin.settings); }));
        new Setting(c).setName("Polish before storing").setDesc("Spelling, grammar and structure by the Archivist; your raw words kept verbatim beneath").addToggle(t => t.setValue(this.plugin.settings.polish !== false).onChange(async v => { this.plugin.settings.polish = v; await this.plugin.saveData(this.plugin.settings); })); }
    })(this.app, this));
  }
  async api(path, body) {
    const r = await fetch(this.settings.url + path, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    return r.json();
  }
  async capture(title, text, source) {
    new Notice("capturing to the inbox…");
    const c = await this.api("/api/capture", { title, text, source: source ? `Obsidian: ${source}` : "Obsidian", polish: this.settings.polish !== false });
    if (c.error) { new Notice("! " + c.error); return; }
    new Notice(`captured → ${c.note}. The organ is reading it (a few minutes on the local model) and will place it where both readers agree.`);
    const r = await this.api("/api/place", { note: c.note });
    if (r.placed && r.placed.ok) new Notice(`placed → ${r.placed_into} (verified; undo on the Archivist's page)`, 15000);
    else if (r.queued) new Notice(`agreed → ${r.placed_into}; it moves the moment you quit Obsidian`, 15000);
    else if (r.placed) { const st = (r.placed.steps || []); const undone = st.some(s => s[0] === "undone"); new Notice(`${c.note}: placement ${undone ? "RED — undone, left in the inbox" : "did not run — " + (st.length ? st[st.length - 1].join(": ") : "refused")}`, 20000); }
    else new Notice(`${c.note}: ${r.status}${r.why ? " — " + r.why : ""} — yours to decide on the Archivist's page`, 15000);
    this.openPage();
  }
  async openPage() {
    const leaves = this.app.workspace.getLeavesOfType(VIEW);
    const leaf = leaves.length ? leaves[0] : this.app.workspace.getLeaf("split", "vertical");
    await leaf.setViewState({ type: VIEW, active: true }); this.app.workspace.revealLeaf(leaf);
  }
  onunload() { this.app.workspace.detachLeavesOfType(VIEW); }
};
