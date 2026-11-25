import { $, area, esc, field, formData, modal, select, uid } from "./ui.js";

const labels = {
  review: "待核对",
  approve: "批准用途",
  restrict: "限制用途",
  reject: "拒绝使用",
  quarantine: "隔离来源",
};
const purposes = { inspect: "查看", process: "加工", export: "导出" };
export async function sourceAdmission(ctx, initial = "decision") {
  const project_id = ctx.requireProject(),
    path = "/production/assets/";
  const data = await ctx.api(
    "/records?" +
      new URLSearchParams({ project_id, kind: "Source", limit: 2000 }),
  );
  if (ctx.state.projectId !== project_id) return;
  const sources = data.items || data;
  const dlg = modal(
    "来源用途与保留期限",
    `<form data-form class="form-stack">
    ${select("项目来源", "source", [["", "选择来源"], ...sources.map((r) => [r.id, (r.data.name || r.data.provenance || r.id) + " · v" + r.version])], "", "required")}
    <p role="status" data-status></p>${select(
      "处理方式",
      "mode",
      [
        ["decision", "更新来源判定"],
        ["reconcile", "执行隔离核销"],
      ],
      initial,
    )}
    <div data-edit class="form-stack"></div>${area("审核依据", "reason", "", "required")}
    <button type="submit" class="button primary" disabled>保存判定</button></form>`,
    async (f) => {
      const v = formData(f),
        record = sources.find((r) => r.id === v.source);
      if (!info || !record) throw new Error("请先读取来源的当前版本。");
      const body = {
        project_id,
        source: { id: info.source.id, version: info.source.version },
        expected_revision: info.revision,
        reason: v.reason,
      };
      if (v.mode === "decision") {
        body.action = v.action;
        body.allowed_purposes = ["approve", "restrict"].includes(v.action)
          ? new FormData(f).getAll("purpose")
          : [];
        if (v.retain_until)
          body.retain_until = new Date(v.retain_until).toISOString();
      }
      const intent = JSON.stringify([v.mode, body]);
      if (intent !== prior) {
        prior = intent;
        key = uid();
      }
      const r = await ctx.api(
        path + (v.mode === "decision" ? "admission" : "retention-reconcile"),
        { method: "POST", body: { ...body, request_id: key } },
      );
      await ctx.reloadData();
      const summary = r.summary;
      modal(
        summary ? "核销结果" : "来源判定已保存",
        summary
          ? `<ul class="batch-observations">
        <li>逻辑隔离 ${esc(summary.logical_quarantine_count)}</li><li>移除的文件对象 ${esc(summary.physical_objects_removed)}</li>
        <li>因共享关系保留 ${esc(summary.shared_objects_retained)}</li><li>外部核验待处理 ${esc(summary.external_verification_pending)}</li></ul>`
          : `<p>${esc(labels[v.action])} · v${esc(r.revision)}</p>`,
      );
    },
  );
  const form = $("form[data-form]", dlg),
    box = $("[data-edit]", dlg),
    status = $("[data-status]", dlg),
    submit = $("[type=submit]", form);
  let serial = 0,
    info = null,
    key = "",
    prior = "";
  const modalId = dlg.dataset.modalId,
    active = () =>
      dlg.open &&
      dlg.dataset.modalId === modalId &&
      ctx.state.projectId === project_id;
  function draw() {
    const purge = form.elements.mode.value === "reconcile";
    submit.textContent = purge ? "执行核销" : "保存判定";
    box.innerHTML = purge
      ? '<label><input type="checkbox" required> 我已核对该来源的保留期限与共享引用，确认执行核销。</label>'
      : select(
          "判定",
          "action",
          Object.entries(labels),
          info?.decision?.data?.action || "review",
        ) +
        "<fieldset data-purposes><legend>允许用途</legend>" +
        Object.entries(purposes)
          .map(
            ([k, l]) =>
              `<label><input type="checkbox" name="purpose" value="${k}" ${info?.decision?.data?.allowed_purposes?.includes(k) ? "checked" : ""}> ${l}</label>`,
          )
          .join("") +
        "</fieldset>" +
        field(
          "保留至（可选，本地时间）",
          "retain_until",
          "",
          "datetime-local",
          'step="1"',
        );
    for (const el of box.querySelectorAll("input,select,textarea"))
      el.disabled = !info;
    const action = form.elements.action;
    if (action) {
      const show = () => {
        form.querySelector("[data-purposes]").hidden = ![
          "approve",
          "restrict",
        ].includes(action.value);
      };
      action.onchange = show;
      show();
    }
    submit.disabled =
      !info || !["owner", "reviewer"].includes(ctx.state.health.actor.role);
  }
  async function read() {
    const ticket = ++serial;
    info = null;
    draw();
    status.textContent = "";
    const id = form.elements.source.value;
    if (!id) return;
    try {
      const r = await ctx.api(
        path +
          "admission?" +
          new URLSearchParams({ project_id, source_id: id }),
      );
      if (!active() || ticket !== serial) return;
      info = r;
      status.textContent =
        "当前来源判定 · " +
        (labels[r.scope_status] ||
          {
            legacy_unreviewed: "尚未审核",
            expired: "已到保留期限",
            reconciled: "已核销",
          }[r.scope_status] ||
          r.scope_status) +
        " · v" +
        r.revision;
      draw();
    } catch (err) {
      if (active() && ticket === serial) status.textContent = err.message;
    }
  }
  form.elements.source.onchange = read;
  form.elements.mode.onchange = draw;
  draw();
}
