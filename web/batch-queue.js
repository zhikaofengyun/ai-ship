import { openGenerationReview } from "./generation-review.js";
import { showAcceptance } from "./batch-contracts.js";
import { batchObservability } from "./batch-observability.js";
import { $, esc, select, btn } from "./ui.js";

const route = "/production/batches";
const statusNames = {
  queued: "排队中",
  running: "执行中",
  waiting: "等待生成",
  succeeded: "已完成",
  failed: "失败",
  paused: "已暂停",
  cancelling: "正在取消",
  cancelled: "已取消",
  completed: "全部完成",
  completed_with_errors: "有失败项",
};
const itemStates = [
  "queued",
  "running",
  "waiting",
  "succeeded",
  "failed",
  "cancelled",
];
const label = (s) => statusNames[s] || s;
const terminal = new Set(["cancelled", "completed", "completed_with_errors"]);
const small = (text, action, attrs = "") =>
  btn(text, action, "", "button small ghost", attrs);

function itemMarkup(item) {
  const audit = (a) => {
    const stage = a.stage === "encoded" ? "成片验收" : "时间线验收";
    const status = { passed: "通过", failed: "未通过" }[a.status] || "待核对";
    return small(
      stage + " · " + status,
      "open-acceptance",
      'data-run="' + esc(a.audit_id) + '"',
    );
  };
  return `<li data-ordinal="${esc(item.ordinal)}">
    <span class="batch-item-state"><strong>#${esc(item.ordinal)}</strong> · ${esc(label(item.status))}</span>
    <span class="batch-item-actions">
    ${item.timeline_id ? `<a href="#editor/project-timeline/${encodeURIComponent(item.timeline_id)}">时间线</a>` : ""}
    ${item.workflow_run_id ? small("工作流记录", "open-run", 'data-run="' + esc(item.workflow_run_id) + '"') : ""}
    ${item.artifact_id ? `<a href="/api/v1/media/artifacts/${encodeURIComponent(item.artifact_id)}/file" download>下载成片</a>` : ""}</span>
    ${item.acceptance?.length ? `<div class="batch-controls">${item.acceptance.map(audit).join("")}</div>` : ""}
    ${item.generation_jobs?.length ? `<details><summary>生成镜头 · ${item.generation_jobs.length}</summary>${item.generation_jobs.map((j) => `<p>镜头 ${esc(j.slot)} · ${esc(label(j.status))} ${j.visual_review_available ? small("对照审片", "review-generated-shot", 'data-job="' + esc(j.generation_id) + '"') : ""}</p>`).join("")}</details>` : ""}
    ${item.error ? `<p class="batch-item-error">${esc(item.error.message || item.error.code)}</p>` : ""}</li>`;
}

// Preserve the exact work, link and expanded receipt while live counts change.
function renderRows(rows, items, key, rendered) {
  const html = items.length
    ? items.map(itemMarkup).join("")
    : "<li>当前筛选没有作品。</li>";
  if (rendered.page === key && rendered.items === html) return;
  const same = rendered.page === key;
  const opened = same
    ? [...rows.querySelectorAll("details[open]")].map(
        (el) => el.closest("li").dataset.ordinal,
      )
    : [];
  const focused =
    same && rows.contains(document.activeElement)
      ? document.activeElement
      : null;
  const id = focused?.closest("li")?.dataset.ordinal;
  const index = id
    ? [...focused.closest("li").querySelectorAll("button, a, summary")].indexOf(
        focused,
      )
    : -1;
  const scroll = same ? rows.scrollTop : 0;
  rows.innerHTML = html;
  rendered.page = key;
  rendered.items = html;
  for (const ordinal of opened) {
    const details = rows.querySelector(`li[data-ordinal="${ordinal}"] details`);
    if (details) details.open = true;
  }
  if (id) {
    const targets = rows
      .querySelector(`li[data-ordinal="${id}"]`)
      ?.querySelectorAll("button, a, summary");
    const target = [...(targets || [])].find(
      (node) =>
        node.tagName === focused.tagName &&
        node.getAttribute("href") === focused.getAttribute("href") &&
        node.dataset.run === focused.dataset.run,
    );
    (target || targets?.[index] || rows).focus({ preventScroll: true });
  }
  rows.scrollTop = scroll;
}

export function mountBatchQueue(host, ctx, pid) {
  let selected = "",
    after = 0,
    size = 24,
    filter = "",
    previous = [],
    snapshot = null;
  let before = "",
    nextBefore = null,
    newer = [],
    historyBusy = false;
  let sequence = 0,
    stopped = false,
    working = false,
    timer;
  const api = (id, tail = "") => route + "/" + encodeURIComponent(id) + tail;
  host.innerHTML = `<header class="batch-queue-head"><h2 tabindex="-1">批次队列</h2>
    ${small("返回编排", "back-to-composer", "data-compact-queue")}</header>
    <div class="batch-queue-body"><div class="batch-history"><div class="batch-list" aria-label="批次历史"></div>
    <nav data-history class="batch-controls" aria-label="批次历史翻页" hidden>
      ${small("较新批次", "newer-batches")}${small("更早批次", "older-batches")}</nav>
    <p class="batch-empty" data-empty hidden>当前项目还没有批次。</p></div>
    <section data-batch-monitor hidden><div data-batch-summary></div>
      <div class="batch-browse">
      ${select("查看作品", "batch_filter", [["", "全部状态"], ...itemStates.map((s) => [s, label(s)])])}
      <div data-page-size>${select(
        "每页",
        "batch_size",
        [24, 50, 100].map((n) => [n, n + " 条"]),
        size,
      )}</div></div>
      <form data-jump class="batch-jump" hidden>
        <label>作品序号<input name="ordinal" type="number" min="1" step="1" inputmode="numeric" required></label>
        <button type="submit" class="button small secondary">定位作品</button>
      </form><small data-range></small>
      <ul class="batch-rows" aria-label="本页作品" tabindex="-1"></ul>
      <nav data-pages class="batch-controls" aria-label="作品翻页">${small("上一页", "prev-page")}${small("下一页", "next-page")}</nav></section></div>
    <p role="status" class="batch-queue-notice"></p>
    ${btn("运行监测", "observe-queue", "chart", "button small ghost")}${btn("刷新队列", "refresh-queue", "history", "button small ghost")}`;
  const list = $(".batch-list", host),
    panel = $("[data-batch-monitor]", host);
  const rows = $(".batch-rows", host),
    summary = $("[data-batch-summary]", panel);
  const jump = $("[data-jump]", panel),
    notice = $(".batch-queue-notice", host);
  const button = (action) => host.querySelector(`[data-action="${action}"]`);
  const history = $("[data-history]", host);
  const rendered = {};
  let historyPage,
    historyCursor,
    historyRead = 0;

  function display(batch) {
    const counts = batch.counts,
      finished = counts.succeeded + counts.failed + counts.cancelled;
    const canWrite = ["owner", "editor", "worker"].includes(
      ctx.state.health?.actor?.role,
    );
    const action = (text, cmd, ico) =>
      btn(text, cmd, ico, "button small secondary");
    const controls =
      canWrite &&
      (batch.output !== "render" || ctx.state.health.actor.role === "owner")
        ? [
            ["queued", "running"].includes(batch.status) &&
              action("暂停", "pause", "pause"),
            batch.status === "paused" && action("继续", "resume", "play"),
            counts.failed > 0 &&
              !["cancelled", "cancelling"].includes(batch.status) &&
              action("重试失败项", "retry", "redo"),
            !terminal.has(batch.status) &&
              batch.status !== "cancelling" &&
              action("取消剩余", "cancel", "stop"),
          ]
            .filter(Boolean)
            .join("")
        : "";
    const html = `<h3>${esc(batch.name)}</h3><small>${esc(label(batch.status))} · ${batch.output === "render" ? "导出成片" : "生成时间线"}</small>
      <progress class="batch-progress" value="${finished}" max="${batch.total}" aria-label="已处理作品数"></progress>
      <small>${finished.toLocaleString()} / ${batch.total.toLocaleString()} 条已结束</small>
      <dl class="batch-counts">${Object.entries(counts)
        .map(
          ([k, v]) =>
            `<div><dt>${esc(label(k))}</dt><dd>${v.toLocaleString()}</dd></div>`,
        )
        .join("")}</dl>
      <div class="batch-controls">${controls}</div>`;
    if (html !== rendered.summary) {
      const actionName =
        summary.contains(document.activeElement) &&
        document.activeElement.dataset.action;
      summary.innerHTML = html;
      rendered.summary = html;
      if (actionName) button(actionName)?.focus({ preventScroll: true });
    }
    panel.hidden = false;
    $("[name=batch_filter]", panel).value = filter;
    $("[name=batch_size]", panel).value = size;
    jump.hidden = $("[data-page-size]", panel).hidden = batch.total <= 24;
    jump.elements.ordinal.max = batch.total;
    jump.elements.ordinal.placeholder = "1 – " + batch.total.toLocaleString();
    $("[data-range]", panel).textContent = batch.items.length
      ? `本页 #${batch.items[0].ordinal} – #${batch.items.at(-1).ordinal} · 共 ${(filter ? counts[filter] : batch.total).toLocaleString()} 条`
      : "";
    renderRows(
      rows,
      batch.items,
      JSON.stringify([selected, filter, after, size]),
      rendered,
    );
    button("prev-page").disabled =
      !previous.length && (filter !== "" || after === 0);
    button("next-page").disabled = batch.next_after == null;
    $("[data-pages]", panel).hidden =
      button("prev-page").disabled && button("next-page").disabled;
  }

  function displayHistory(result) {
    nextBefore = result.next_before || null;
    history.hidden = !newer.length && !nextBefore;
    button("newer-batches").disabled = !newer.length;
    button("older-batches").disabled = !nextBefore;
    const html = result.batches
      .map(
        (b) =>
          `<button type="button" class="batch-list-row" data-batch="${esc(b.id)}" aria-current="${b.id === selected}"><span>${esc(b.name)}</span><small>${esc(label(b.status))} · ${esc(b.total)} 条</small></button>`,
      )
      .join("");
    if (rendered.history !== html) {
      const focused =
        list.contains(document.activeElement) &&
        document.activeElement.dataset.batch;
      list.innerHTML = html;
      rendered.history = html;
      if (focused)
        list
          .querySelector('[data-batch="' + CSS.escape(focused) + '"]')
          ?.focus({ preventScroll: true });
    }
    $("[data-empty]", host).hidden = Boolean(selected);
  }
  function poll() {
    clearTimeout(timer);
    if (!stopped && !working && !document.hidden) {
      timer = setTimeout(
        load,
        snapshot && terminal.has(snapshot.status) ? 15000 : 5000,
      );
    }
  }
  async function load(refreshHistory = false) {
    clearTimeout(timer);
    const ticket = ++sequence;
    try {
      if (
        refreshHistory ||
        !historyPage ||
        before !== historyCursor ||
        performance.now() - historyRead >= 30000
      ) {
        const query = new URLSearchParams({ project_id: pid, limit: "20" });
        if (before) query.set("before", before);
        const result = await ctx.api(route + "?" + query);
        if (stopped || ticket !== sequence) return;
        historyPage = result;
        historyCursor = before;
        historyRead = performance.now();
      }
      selected ||= historyPage.batches[0]?.id || "";
      displayHistory(historyPage);
      if (!selected) {
        panel.hidden = true;
        notice.textContent = "";
        return;
      }
      const params = new URLSearchParams({
        after: String(after),
        limit: String(size),
      });
      if (filter) params.set("state", filter);
      const batch = await ctx.api(api(selected) + "?" + params);
      if (stopped || ticket !== sequence) return;
      snapshot = batch;
      const entry = historyPage.batches.find((b) => b.id === selected);
      if (entry && entry.status !== batch.status) {
        entry.status = batch.status;
        displayHistory(historyPage);
      }
      display(batch);
      notice.textContent = "";
    } catch (err) {
      if (!stopped && ticket === sequence) notice.textContent = err.message;
    } finally {
      if (!stopped && ticket === sequence) {
        historyBusy = false;
        poll();
      }
    }
  }
  function resetItems() {
    snapshot = null;
    previous = [];
    after = 0;
    filter = "";
    jump.reset();
    panel.hidden = true;
  }
  function selectBatch(id, latest = true) {
    selected = id;
    if (latest) {
      before = "";
      newer = [];
    }
    resetItems();
    load(latest);
  }
  function changePage() {
    snapshot = null;
    button("prev-page").disabled = button("next-page").disabled = true;
    load();
  }
  host.onchange = (e) => {
    if (e.target.name !== "batch_filter" && e.target.name !== "batch_size")
      return;
    if (e.target.name === "batch_filter") filter = e.target.value;
    else size = Number(e.target.value);
    after = 0;
    previous = [];
    changePage();
  };
  jump.onsubmit = (e) => {
    e.preventDefault();
    const ordinal = jump.elements.ordinal.valueAsNumber;
    if (!jump.reportValidity() || !selected || !Number.isInteger(ordinal))
      return;
    // An ordinal identifies one work independently of its current status.
    filter = "";
    $("[name=batch_filter]", panel).value = "";
    previous = [];
    after = ordinal - 1;
    changePage();
  };
  host.onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b || working) return;
    if (b.dataset.batch) {
      selectBatch(b.dataset.batch, false);
      return;
    }
    const op = b.dataset.action;
    if (op === "newer-batches" || op === "older-batches") {
      if (historyBusy) return;
      if (op === "older-batches" && nextBefore) {
        newer.push(before);
        before = nextBefore;
      } else if (op === "newer-batches" && newer.length) before = newer.pop();
      else return;
      historyBusy = true;
      selectBatch("", false);
      return;
    }
    if (op === "review-generated-shot") {
      await openGenerationReview(ctx, pid, b.dataset.job);
      return;
    }
    if (op === "observe-queue") {
      try {
        await batchObservability(ctx, pid, selected);
      } catch (err) {
        if (!stopped) notice.textContent = err.message;
      }
      return;
    }
    if (op === "open-run" || op === "open-acceptance") {
      try {
        const r = await ctx.api(
          "/records/" + encodeURIComponent(b.dataset.run),
        );
        if (!stopped) {
          if (op === "open-acceptance") showAcceptance(r);
          else ctx.openRecord(r);
        }
      } catch (err) {
        if (!stopped) notice.textContent = err.message;
      }
      return;
    }
    if (op === "refresh-queue") {
      await load(true);
      return;
    }
    if (op === "next-page" && snapshot?.next_after != null) {
      previous.push(after);
      after = snapshot.next_after;
      changePage();
      return;
    }
    if (op === "prev-page" && snapshot) {
      after = previous.length ? previous.pop() : Math.max(0, after - size);
      changePage();
      return;
    }
    if (
      !selected ||
      !snapshot ||
      !["pause", "resume", "cancel", "retry"].includes(op)
    )
      return;
    if (
      op === "cancel" &&
      !window.confirm("取消这个批次中尚未完成的作品？已产生的文件仍可查看。")
    )
      return;
    working = true;
    clearTimeout(timer);
    ++sequence;
    b.disabled = true;
    try {
      await ctx.api(api(selected, "/" + op), { method: "POST" });
      if (!stopped) {
        working = false;
        await load();
      }
    } catch (err) {
      if (!stopped) {
        notice.textContent = err.message;
        b.disabled = false;
      }
    } finally {
      working = false;
      poll();
    }
  };
  const visibility = () => {
    if (document.hidden) clearTimeout(timer);
    else if (!working) load(true);
  };
  document.addEventListener("visibilitychange", visibility);
  load();
  return {
    selectBatch,
    dispose() {
      stopped = true;
      clearTimeout(timer);
      ++sequence;
      document.removeEventListener("visibilitychange", visibility);
    },
  };
}
