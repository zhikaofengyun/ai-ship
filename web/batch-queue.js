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
const label = (s) => statusNames[s] || s;
const terminal = new Set(["cancelled", "completed", "completed_with_errors"]);

export function mountBatchQueue(host, ctx, pid) {
  let selected = "",
    after = 0,
    filter = "",
    previous = [],
    snapshot = null;
  let sequence = 0,
    stopped = false,
    working = false,
    timer,
    lastMarkup = "";
  const query = new URLSearchParams({ project_id: pid, limit: "20" });
  host.innerHTML = `<h2>批次队列</h2><div class="batch-list" aria-label="最近批次"></div>
    <div data-batch-monitor></div><p role="status" class="batch-queue-notice"></p>
    ${btn("运行监测", "observe-queue", "chart", "button small ghost")}${btn("刷新队列", "refresh-queue", "history", "button small ghost")}`;
  const list = $(".batch-list", host),
    panel = $("[data-batch-monitor]", host),
    notice = $(".batch-queue-notice", host);
  const api = (id, tail = "") => route + "/" + encodeURIComponent(id) + tail;

  function display(batch) {
    const counts = batch.counts,
      finished = counts.succeeded + counts.failed + counts.cancelled;
    const rows = batch.items
      .map(
        (
          item,
        ) => `<li><span>#${esc(item.ordinal)} · ${esc(label(item.status))}</span><span>
      ${item.timeline_id ? `<a href="#editor/project-timeline/${encodeURIComponent(item.timeline_id)}">时间线</a>` : ""}
      ${item.workflow_run_id ? btn("工作流记录", "open-run", "", "button small ghost", 'data-run="' + esc(item.workflow_run_id) + '"') : ""}
      ${item.artifact_id ? `<a href="/api/v1/media/artifacts/${encodeURIComponent(item.artifact_id)}/file" download>下载成片</a>` : ""}
      </span>${item.acceptance?.length ? `<div class="batch-controls">${item.acceptance.map((a) => btn((a.stage === "encoded" ? "成片验收 · " : "时间线验收 · ") + (a.status === "passed" ? "通过" : a.status === "failed" ? "未通过" : "待核对"), "open-acceptance", "", "button small ghost", 'data-run="' + esc(a.audit_id) + '"')).join("")}</div>` : ""}${item.generation_jobs?.length ? `<details><summary>生成镜头 · ${item.generation_jobs.length}</summary>${item.generation_jobs.map((j) => `<p>镜头 ${esc(j.slot)} · ${esc(label(j.status))}</p>`).join("")}</details>` : ""}${item.error ? `<p>${esc(item.error.message || item.error.code)}</p>` : ""}</li>`,
      )
      .join("");
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
    const markup = `<h3>${esc(batch.name)}</h3><small>${esc(label(batch.status))} · ${batch.output === "render" ? "导出成片" : "生成时间线"}</small>
      <progress class="batch-progress" value="${finished}" max="${batch.total}" aria-label="已处理作品数"></progress>
      <small>${finished.toLocaleString()} / ${batch.total.toLocaleString()} 条已结束</small>
      <dl class="batch-counts">${Object.entries(counts)
        .map(
          ([k, v]) => `<div><dt>${esc(label(k))}</dt><dd>${esc(v)}</dd></div>`,
        )
        .join("")}</dl>
      <div class="batch-controls">${controls}</div>
      ${select("查看作品", "batch_filter", [["", "全部状态"], ...Object.keys(counts).map((k) => [k, label(k)])], filter)}
      <ul class="batch-rows">${rows || "<li>当前筛选没有作品。</li>"}</ul>
      <div class="batch-controls">${btn("上一页", "prev-page", "", "button small ghost", previous.length ? "" : "disabled")}${btn("下一页", "next-page", "", "button small ghost", batch.next_after != null ? "" : "disabled")}</div>`;
    if (markup !== lastMarkup) {
      const focused = document.activeElement,
        name = focused?.name,
        actionName = focused?.dataset.action;
      const panelFocused = panel.contains(focused),
        scroll = $(".batch-rows", panel)?.scrollTop || 0;
      panel.innerHTML = markup;
      lastMarkup = markup;
      $(".batch-rows", panel).scrollTop = scroll;
      if (panelFocused) {
        const match = name
          ? panel.querySelector("[name=" + CSS.escape(name) + "]")
          : actionName &&
            panel.querySelector("[data-action=" + CSS.escape(actionName) + "]");
        match?.focus({ preventScroll: true });
      }
    }
  }
  async function load() {
    clearTimeout(timer);
    const ticket = ++sequence;
    try {
      const result = await ctx.api(route + "?" + query);
      if (stopped || ticket !== sequence) return;
      selected ||= result.batches[0]?.id || "";
      const listHtml = result.batches
        .map(
          (b) =>
            `<button type="button" class="batch-list-row" data-batch="${esc(b.id)}" aria-current="${b.id === selected}"><span>${esc(b.name)}</span><small>${esc(label(b.status))} · ${esc(b.total)} 条</small></button>`,
        )
        .join("");
      if (list.dataset.html !== listHtml) {
        const focused =
          list.contains(document.activeElement) &&
          document.activeElement.dataset.batch;
        list.innerHTML = listHtml;
        list.dataset.html = listHtml;
        if (focused)
          list
            .querySelector('[data-batch="' + CSS.escape(focused) + '"]')
            ?.focus({ preventScroll: true });
      }
      if (!selected) {
        panel.innerHTML = '<p class="batch-empty">当前项目还没有批次。</p>';
        notice.textContent = "";
        return;
      }
      const params = new URLSearchParams({ after: String(after), limit: "24" });
      if (filter) params.set("state", filter);
      const batch = await ctx.api(api(selected) + "?" + params);
      if (stopped || ticket !== sequence) return;
      snapshot = batch;
      display(batch);
      notice.textContent = "";
    } catch (err) {
      if (!stopped && ticket === sequence) notice.textContent = err.message;
    } finally {
      if (
        !stopped &&
        ticket === sequence &&
        !working &&
        (!snapshot || !terminal.has(snapshot.status))
      ) {
        timer = setTimeout(() => {
          if (!document.hidden) load();
          else scheduleOnShow();
        }, 5000);
      }
    }
  }
  function scheduleOnShow() {
    if (!stopped && !document.hidden) load();
  }
  function selectBatch(id) {
    selected = id;
    snapshot = null;
    previous = [];
    after = 0;
    filter = "";
    load();
  }
  host.onchange = (e) => {
    if (e.target.name === "batch_filter") {
      filter = e.target.value;
      after = 0;
      previous = [];
      load();
    }
  };
  host.onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b || working) return;
    if (b.dataset.batch) {
      selectBatch(b.dataset.batch);
      return;
    }
    const op = b.dataset.action;
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
      await load();
      return;
    }
    if (op === "next-page" && snapshot?.next_after != null) {
      previous.push(after);
      after = snapshot.next_after;
      await load();
      return;
    }
    if (op === "prev-page" && previous.length) {
      after = previous.pop();
      await load();
      return;
    }
    if (!selected || !["pause", "resume", "cancel", "retry"].includes(op))
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
      if (stopped) return;
      working = false;
      await load();
    } catch (err) {
      if (!stopped) {
        notice.textContent = err.message;
        b.disabled = false;
      }
    } finally {
      working = false;
    }
  };
  document.addEventListener("visibilitychange", scheduleOnShow);
  load();
  return {
    selectBatch,
    dispose() {
      stopped = true;
      clearTimeout(timer);
      ++sequence;
      document.removeEventListener("visibilitychange", scheduleOnShow);
    },
  };
}
