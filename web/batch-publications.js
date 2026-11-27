import { $, area, btn, esc, field, formData, modal, select } from "./ui.js";

export async function preparePublications(ctx, batch) {
  const pid = batch.project_id;
  const result = await ctx.api(
    "/records?" +
      new URLSearchParams({ kind: "Account", project_id: pid, limit: 2000 }),
  );
  if (ctx.state.projectId !== pid) return;
  const accounts = result.items || result;
  if (!accounts.length) {
    const d = modal(
      "准备批量发布",
      "<p>为这个项目登记一个交付账号，即可将成片转入发布审阅。</p>" +
        btn("登记账号", "new-account", "plus", "button primary"),
    );
    $("[data-action=new-account]", d).onclick = () => ctx.accountForm();
    return;
  }
  const d = modal(
    "准备批量发布",
    `<form class="form-stack" data-prepare>
    ${select(
      "交付账号",
      "account_id",
      accounts.map((a) => [
        a.id,
        a.data.name || a.data.platform_account_id || "项目账号",
      ]),
    )}
    ${select("交付方式", "channel", [
      ["manual", "人工交接"],
      ["tiktok_inbox", "TikTok 收件箱"],
    ])}
    ${area("交付备注（{n} 为作品序号）", "title", batch.name + " · {n}", 'maxlength="2200" required')}
    <p class="muted small">收件箱交付后，由创作者在 TikTok 确认文案、可见性和发表。</p><div class="form-grid">${field("从作品序号", "start", 1, "number", `min="1" max="${batch.total}" step="1" required`)}${field("到作品序号", "end", batch.total, "number", `min="1" max="${batch.total}" step="1" required`)}</div>
    <button class="button primary" type="submit">创建发布草稿并预检</button></form>
    <p role="status" data-totals></p><div data-results></div><div class="batch-controls" data-pager hidden><button type="button" class="button small ghost" data-prev>上一页</button><span data-range></span><button type="button" class="button small ghost" data-next>下一页</button></div><button type="button" data-send hidden class="button primary">上传选中的作品至 TikTok 收件箱</button>`,
    null,
    true,
  );
  const token = d.dataset.modalId,
    active = () =>
      d.open && d.dataset.modalId === token && ctx.state.projectId === pid;
  const form = $("[data-prepare]", d);
  const send = $("[data-send]", d);
  const panel = $("[data-results]", d);
  const selections = new Map(),
    pager = $("[data-pager]", d);
  let range = null,
    offset = 0,
    loading = false,
    sending = false;
  function updateSelection() {
    send.textContent = selections.size
      ? "上传选中的 " + selections.size + " 条作品至 TikTok 收件箱"
      : "上传选中的作品至 TikTok 收件箱";
    send.hidden = !selections.size && !panel.querySelector("[data-upload]");
    send.disabled = sending || !selections.size;
  }
  function show(page) {
    panel.innerHTML = page.items
      .map(
        (row) =>
          `<div class="list-row">${row.can_send && ctx.state.health.actor.role === "owner" ? `<label><input type="checkbox" data-upload="${esc(row.publication_id)}" data-version="${esc(row.version)}">#${esc(row.ordinal)}</label>` : `<strong>#${esc(row.ordinal)}</strong>`}<span data-state>${esc(row.message || (row.status === "skipped" ? "尚无可交付成片" : row.problems?.map((p) => p.message).join(" · ") || "预检通过"))}</span>${row.publication_id ? btn("查看交付", "inspect-intent", "", "button small ghost", `data-pub="${esc(row.publication_id)}"`) : ""}</div>`,
      )
      .join("");
    for (const input of panel.querySelectorAll("[data-upload]"))
      input.checked = selections.has(input.dataset.upload);
    updateSelection();

    pager.hidden = !range || range.end - range.start < 25;
    if (range) {
      $("[data-prev]", pager).disabled = offset <= range.start - 1;
      $("[data-next]", pager).disabled = offset + 25 >= range.end;
      $("[data-range]", pager).textContent =
        "#" + (offset + 1) + " – #" + Math.min(offset + 25, range.end);
    }
  }
  panel.onchange = (e) => {
    const input = e.target.closest("[data-upload]");
    if (!input) return;
    if (input.checked)
      selections.set(input.dataset.upload, Number(input.dataset.version));
    else selections.delete(input.dataset.upload);
    updateSelection();
  };
  pager.onclick = async (e) => {
    const button = e.target.closest("button");
    if (!button || !range || loading || sending) return;
    const nextOffset = offset + (button.hasAttribute("data-prev") ? -25 : 25);
    const scope = range;
    loading = true;
    try {
      const params = new URLSearchParams({
        account_id: scope.account_id,
        channel: scope.channel,
        after: nextOffset,
        limit: Math.min(25, scope.end - nextOffset),
      });
      const page = await ctx.api(
        "/production/batches/" +
          encodeURIComponent(batch.id) +
          "/publications?" +
          params,
      );
      if (active() && range === scope) {
        offset = nextOffset;
        show(page);
      }
    } catch (err) {
      ctx.toast(err.message, "error");
    } finally {
      loading = false;
    }
  };
  form.oninput = () => {
    range = null;
    selections.clear();
    pager.hidden = true;
    send.hidden = true;
    panel.replaceChildren();
  };
  send.onclick = async () => {
    const checked = [...selections.entries()];
    if (!checked.length) {
      ctx.toast("先选择要交付的作品。", "error");
      return;
    }
    sending = true;
    updateSelection();
    const controls = [
      ...form.elements,
      ...pager.querySelectorAll("button"),
      ...panel.querySelectorAll("input,button"),
    ];
    const restore = controls.filter((control) => !control.disabled);
    restore.forEach((control) => {
      control.disabled = true;
    });
    const completed = new Set();
    const notice = $("[data-totals]", d);
    let uploaded = 0;
    try {
      for (const [id, version] of checked) {
        if (!active()) break;
        notice.textContent =
          "正在交付第 " +
          (uploaded + 1) +
          " 条，共 " +
          checked.length +
          " 条。";
        const pub = await ctx.api(
          "/production/tiktok/inbox/" + encodeURIComponent(id) + "/send",
          {
            method: "POST",
            body: { expected_revision: version },
          },
        );
        if (!active()) break;
        selections.delete(id);
        const input = panel.querySelector(
          '[data-upload="' + CSS.escape(id) + '"]',
        );
        if (input) {
          completed.add(input);
          input.checked = false;
        }
        const out = pub.data.inbox_stage;
        if (input)
          input.closest(".list-row").querySelector("[data-state]").textContent =
            out === "uploaded"
              ? "已上传，等待平台回执"
              : out === "init_refused"
                ? "平台拒绝本次交付"
                : "交付结果待核对";
        if (out !== "uploaded") {
          notice.textContent =
            "已上传 " + uploaded + " 条。后续交付已停止，请核对本条平台回执。";
          break;
        }
        uploaded++;
        notice.textContent =
          "已上传 " + uploaded + " / " + checked.length + " 条，等待平台回执。";
      }
    } catch (err) {
      ctx.toast(err.message, "error");
    } finally {
      if (active()) {
        sending = false;
        restore.forEach((control) => {
          control.disabled = completed.has(control);
        });
        updateSelection();
      }
    }
  };
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (sending) return;
    const values = formData(form),
      start = Number(values.start),
      end = Number(values.end);
    if (end < start) {
      ctx.toast("结束序号须大于或等于开始序号。", "error");
      return;
    }
    const fields = [...form.elements];
    fields.forEach((el) => (el.disabled = true));
    const tally = { created: 0, existing: 0, skipped: 0, blocked: 0 };
    const panel = $("[data-results]", d),
      status = $("[data-totals]", d);
    send.hidden = true;
    panel.replaceChildren();
    let cursor = start - 1,
      firstPage = null;
    range = null;
    pager.hidden = true;
    selections.clear();
    try {
      while (active() && cursor < end) {
        status.textContent = `正在核对作品 #${cursor + 1} 至 #${end}…`;
        const page = await ctx.api(
          "/production/batches/" +
            encodeURIComponent(batch.id) +
            "/publications/prepare",
          {
            method: "POST",
            body: {
              account_id: values.account_id,
              title: values.title,
              channel: values.channel,
              after: cursor,
              limit: Math.min(25, end - cursor),
            },
          },
        );
        if (!active()) return;
        for (const row of page.items) tally[row.status]++;
        if (!firstPage) firstPage = page;
        cursor = page.next_after;
        if (cursor == null) break;
      }
      if (active() && firstPage) {
        range = {
          start,
          end,
          account_id: values.account_id,
          channel: values.channel,
        };
        offset = start - 1;
        show(firstPage);
      }
      if (active())
        status.textContent = `新建 ${tally.created} · 已登记 ${tally.existing} · 未出片 ${tally.skipped} · 受阻 ${tally.blocked}。完整记录可在「交付与发布」查看。`;
    } catch (err) {
      if (active()) status.textContent = err.message;
    } finally {
      if (active()) fields.forEach((el) => (el.disabled = false));
    }
  };
  $("[data-results]", d).onclick = async (e) => {
    const id = e.target.closest("[data-pub]")?.dataset.pub;
    if (id)
      try {
        const pub = await ctx.api("/records/" + encodeURIComponent(id));
        if (active()) await ctx.preflightPublication(pub);
      } catch (err) {
        ctx.toast(err.message, "error");
      }
  };
}
