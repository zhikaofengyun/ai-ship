import { $, esc, field, humanBytes, modal } from "./ui.js";

export function acquireCommons(ctx) {
  const project_id = ctx.requireProject();
  const route = "/production/source-media";
  const dialog = modal(
    "从 Wikimedia Commons 获取素材",
    `<form class="form-stack" data-search>
    ${field("搜索公开视频", "q", "", "search", 'maxlength="180" required')}
    <button class="button primary" type="submit">查找素材</button></form>
    <button class="button ghost" type="button" data-switch disabled>读取项目来源状态</button><p data-status role="status"></p><ul class="batch-rows" data-results></ul>`,
  );
  const modalId = dialog.dataset.modalId;
  const active = () =>
    dialog.open &&
    dialog.dataset.modalId === modalId &&
    ctx.state.projectId === project_id;
  const form = $("[data-search]", dialog),
    status = $("[data-status]", dialog),
    results = $("[data-results]", dialog);
  let items = [],
    sequence = 0;
  let settings;
  const toggle = $("[data-switch]", dialog),
    submit = form.querySelector("button");
  const showSettings = (value) => {
    settings = value;
    toggle.textContent = value.enabled
      ? "停用此项目的 Commons 来源"
      : "启用此项目的 Commons 来源";
    toggle.disabled = !["owner", "reviewer"].includes(
      ctx.state.health.actor.role,
    );
    submit.disabled = !value.enabled;
    if (!value.enabled) {
      ++sequence;
      items = [];
      results.replaceChildren();
      status.textContent = "本项目已停用 Commons 来源。";
    }
  };
  async function readSettings() {
    try {
      const r = await ctx.api(
        route + "/settings?" + new URLSearchParams({ project_id }),
      );
      if (active()) showSettings(r);
    } catch (err) {
      if (active()) status.textContent = err.message;
    }
  }
  toggle.onclick = async () => {
    if (!settings) return;
    toggle.disabled = true;
    try {
      const r = await ctx.api(route + "/settings", {
        method: "POST",
        body: {
          project_id,
          enabled: !settings.enabled,
          expected_revision: settings.revision,
        },
      });
      if (active()) {
        status.textContent = "";
        showSettings(r);
      }
    } catch (err) {
      if (active()) {
        await readSettings();
        if (active()) status.textContent = err.message;
      }
    }
  };
  readSettings();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const ticket = ++sequence;
    items = [];
    results.replaceChildren();
    status.textContent = "正在读取公开素材目录…";
    try {
      const data = await ctx.api(
        route +
          "/search?" +
          new URLSearchParams({
            project_id,
            q: form.elements.q.value.trim(),
            limit: 8,
          }),
      );
      if (!active() || sequence !== ticket) return;
      items = data.items;
      status.textContent = items.length
        ? `找到 ${items.length} 项。导入后仍需按许可核对实际用途。`
        : "没有找到符合条件的素材。";
      results.innerHTML = items
        .map((item, i) => {
          let link = "";
          try {
            const url = new URL(item.description_url);
            if (
              url.protocol === "https:" &&
              url.hostname === "commons.wikimedia.org"
            )
              link = `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">来源页</a>`;
          } catch {}
          return `<li><div><strong>${esc(item.title)}</strong><p>${esc(item.author)} · ${esc(item.license)} · ${humanBytes(item.size_bytes)}</p>${link}</div>
          <button type="button" class="button secondary" data-import="${i}">导入素材</button></li>`;
        })
        .join("");
    } catch (err) {
      if (active() && sequence === ticket) status.textContent = err.message;
    }
  };
  results.onclick = async (e) => {
    const btn = e.target.closest("[data-import]");
    if (!btn || btn.disabled) return;
    const row = items[Number(btn.dataset.import)],
      ticket = sequence;
    btn.disabled = true;
    try {
      const r = await ctx.api(route + "/import", {
        method: "POST",
        body: {
          project_id,
          page_id: row.page_id,
          expected_sha1: row.sha1,
          expected_license: row.license,
          expected_author: row.author,
        },
      });
      if (!active() || ticket !== sequence) return;
      btn.textContent = r.duplicate ? "素材已存在" : "已导入";
      status.textContent = row.title + " · " + btn.textContent;
      await ctx.reloadData();
    } catch (err) {
      if (active() && ticket === sequence) {
        status.textContent = err.message;
        btn.disabled = false;
      }
    }
  };
}
