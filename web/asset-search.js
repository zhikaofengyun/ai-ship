import { $, esc, field, modal, select, toast, uid } from "./ui.js";

export async function openAssetSearch(ctx, choose) {
  const project = ctx.requireProject();
  const via = "/production/asset-index";
  const post = (route, data) =>
    ctx.api(via + route, {
      method: "POST",
      body: { project_id: project, ...data },
    });
  const dlg = modal(
    "检索素材",
    `<form class="director-index-query form-grid">
    ${field("名称或镜头标注", "text", "", "search", 'maxlength="300" autocomplete="off"')}
    <div class="director-numbers">${select(
      "媒体",
      "media_type",
      [
        ["any", "全部"],
        ["video", "视频"],
        ["image", "图片"],
        ["audio", "音频"],
      ],
      typeof choose === "function" ? "video" : "any",
    )}
    ${select(
      "检索方式",
      "channel",
      [
        ["indexed", "项目索引"],
        ["source", "直接查素材"],
        ["semantic", "语义召回"],
      ],
      "indexed",
    )}</div>
    <button class="button primary" type="submit">查找</button></form>
    <p role="status" data-index-status></p><ul class="director-index-hits" data-hits></ul><div data-result-pages class="form-actions"></div>
    <details class="director-format"><summary>索引版本</summary><div data-index-history></div></details>`,
    null,
    true,
  );
  const anchor = dlg.dataset.modalId,
    valid = () =>
      dlg.open &&
      dlg.dataset.modalId === anchor &&
      ctx.state.projectId === project;
  let history,
    serial = 0,
    last = null,
    busy = false;
  const status = $("[data-index-status]", dlg),
    form = $(".director-index-query", dlg);
  async function loadHistory() {
    const v = await ctx.api(
      via + "/history?project_id=" + encodeURIComponent(project),
    );
    if (!valid()) return;
    history = v;
    const canWrite = !["viewer", "reviewer"].includes(
      ctx.state.health?.actor?.role,
    );
    $("[data-index-history]", dlg).innerHTML =
      `<div class="director-actions">${canWrite ? `<button type="button" class="button small" data-build="shadow">构建新索引</button><button type="button" class="button small" data-embed>构建语义索引</button>${v.head.generation ? '<button type="button" class="button small" data-build="incremental">同步变化</button>' : ""}` : ""}</div>${v.snapshots.map((r, i) => `<div class="director-index-version"><span>${esc(new Date(r.stamp).toLocaleString())} · ${r.checked} 份素材${r.reused ? " · 复用 " + r.reused : ""}</span>${r.id === v.head.generation ? "<strong>正在使用</strong>" : canWrite ? `<button type="button" class="button ghost small" data-switch="${i}">${r.previously_active ? "回退到此版本" : "启用此版本"}</button>` : ""}</div>`).join("")}`;
  }
  async function search(offset = 0) {
    const ticket = ++serial;
    const fields = Object.fromEntries(new FormData(form));
    status.textContent = "正在查找…";
    let v;
    try {
      v = await post("/query", {
        ...fields,
        offset,
        expected_view: offset ? last?.view : null,
      });
    } catch (error) {
      if (
        offset &&
        error.code === "RESULT_SET_CHANGED" &&
        valid() &&
        ticket === serial
      )
        return search();
      throw error;
    }
    if (!valid() || ticket !== serial) return;
    last = v;
    status.textContent =
      (v.fallback ? v.fallback + " " : "") +
      v.count +
      " 份匹配素材" +
      (v.count > v.items.length
        ? " · 当前 " + (v.offset + 1) + "–" + (v.offset + v.items.length)
        : "");
    $("[data-result-pages]", dlg).innerHTML =
      (offset
        ? '<button type="button" data-prev class="button small">上一页</button>'
        : "") +
      (v.next_offset != null
        ? '<button type="button" data-next class="button small">下一页</button>'
        : "");
    $("[data-hits]", dlg).innerHTML = v.items
      .map(
        (r, i) =>
          `<li><div><strong>${esc(r.name)}</strong><small>${r.seconds != null ? Number(r.seconds).toFixed(2) + " 秒 · " : ""}${esc(r.tags.map((t) => t.label).join("、") || "原片")}${Number.isFinite(r.semantic_score) && v.vector_available ? " · 语义相似度 " + r.semantic_score.toFixed(3) : ""}</small></div>${typeof choose === "function" && r.media_type === "video" ? `<button type="button" class="button secondary small" data-choose="${i}">加入制作</button>` : ""}</li>`,
      )
      .join("");
  }
  form.onsubmit = (e) => {
    e.preventDefault();
    search().catch((e) => {
      if (valid()) status.textContent = e.message;
    });
  };
  dlg.addEventListener("click", handle);
  dlg.addEventListener(
    "close",
    () => dlg.removeEventListener("click", handle),
    { once: true },
  );
  async function handle(event) {
    const b = event.target.closest("button");
    if (!b || busy || !valid()) return;
    try {
      if (b.hasAttribute("data-next") || b.hasAttribute("data-prev")) {
        if (last)
          await search(
            b.hasAttribute("data-next")
              ? last.next_offset
              : Math.max(0, last.offset - 30),
          );
        return;
      }
      if (b.hasAttribute("data-choose")) {
        const item = last?.items[Number(b.dataset.choose)];
        if (!item) return;
        await choose(item);
        if (valid()) dlg.close();
        return;
      }
      if (b.hasAttribute("data-embed")) {
        busy = true;
        b.disabled = true;
        const result = await post("/embed", { limit: 128 });
        if (valid())
          status.textContent = `本次编码 ${result.processed} 条，剩余 ${result.remaining} 条 · ${result.space}`;
        return;
      }
      if (!b.hasAttribute("data-build") && !b.hasAttribute("data-switch"))
        return;
      busy = true;
      b.disabled = true;
      if (b.dataset.build) {
        await post("/build", {
          mode: b.dataset.build,
          expected_revision: history.head.revision,
          request_key: uid(),
        });
      } else {
        const target = history.snapshots[Number(b.dataset.switch)];
        await post("/switch", {
          snapshot: target.id,
          expected_revision: history.head.revision,
          action: target.previously_active ? "rollback" : "activate",
        });
      }
      if (valid()) {
        await loadHistory();
        await search();
      }
    } catch (err) {
      if (valid()) {
        status.textContent = err.message;
        toast(err.message, "error");
        await loadHistory().catch(() => {});
      }
    } finally {
      busy = false;
      b.disabled = false;
    }
  }
  try {
    await loadHistory();
    if (valid()) await search();
  } catch (err) {
    if (valid()) status.textContent = err.message;
  }
}
