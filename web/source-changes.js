import { area, esc, field, formData, modal, select, uid } from "./ui.js";

const dispositions = {
  applied: "已更新",
  observed_only: "待核实",
  late_ignored: "迟到，已保留记录",
  needs_reconciliation: "同一时刻信号冲突",
  duplicate_observation: "重复观测",
  failed: "提交失败",
};

export function openSourceChanges(ctx) {
  const pid = ctx.requireProject(),
    sources = ctx.records("Source");
  if (!sources.length)
    throw new Error("请先导入素材或登记来源，再核对它的变化。");
  const rows = sources.map((s) => [
    s.id,
    (s.data.name || s.data.canonical_url || s.data.filename || s.id) +
      " · v" +
      s.version,
  ]);
  const row = (i) =>
    "<fieldset><legend>变化记录 " +
    (i + 1) +
    "</legend>" +
    select("来源", "source" + i, rows) +
    select("事件", "change" + i, [
      ["missing", "暂未找到"],
      ["revised", "内容修订"],
      ["origin_deleted", "原出处已删除"],
      ["rights_expired", "权利到期"],
      ["rights_revoked", "权利撤回"],
    ]) +
    field(
      "来源事件编号",
      "event" + i,
      uid(),
      "text",
      'required maxlength="200"',
    ) +
    field(
      "实际发生时间（当地）",
      "when" + i,
      "",
      "datetime-local",
      "required",
    ) +
    field(
      "来源版本或校验值（确认修订时必填）",
      "rev" + i,
      "",
      "text",
      'maxlength="200"',
    ) +
    area("变化依据", "evidence" + i, "", 'required maxlength="3000"') +
    '<label class="choice-row"><input type="checkbox" name="confirmed' +
    i +
    '"> 已核实所选变化</label></fieldset>';
  let count = 1;
  const d = modal(
    "核对来源变化清单",
    '<form class="form-stack" data-form><div data-signals>' +
      row(0) +
      "</div>" +
      '<button type="button" class="button secondary" data-add>增加记录</button><button class="button primary" type="submit">处理清单</button></form>',
    async (f) => {
      const values = formData(f);
      const events = Array.from({ length: count }, (_, i) => {
        const source = sources.find((s) => s.id === values["source" + i]);
        return {
          source: { id: source.id, version: source.version },
          change: values["change" + i],
          event_id: values["event" + i],
          occurred_at: new Date(values["when" + i]).toISOString(),
          origin_revision: values["rev" + i] || null,
          evidence: values["evidence" + i],
          confirmed: Boolean(values["confirmed" + i]),
        };
      });
      const r = await ctx.api("/production/acquisition/changes", {
        method: "POST",
        body: { project_id: pid, events },
      });
      await ctx.reloadData();
      modal(
        "来源清单处理结果",
        r.results
          .map(
            (x) =>
              "<h3>" +
              esc(x.event_id) +
              "</h3><p>" +
              esc(dispositions[x.disposition]) +
              (x.idempotent_replay ? " · 已有回执" : "") +
              "</p>" +
              (x.message ? "<p>" + esc(x.message) + "</p>" : "") +
              (x.impact
                ? "<p>待复核发布 " +
                  x.impact.release_review.length +
                  " 项，在途待对账 " +
                  x.impact.reconcile_in_flight.length +
                  " 项，历史记录 " +
                  x.impact.historical.length +
                  " 项。</p>"
                : ""),
          )
          .join(""),
      );
    },
    true,
  );
  const add = d.querySelector("[data-add]");
  add.onclick = () => {
    d.querySelector("[data-signals]").insertAdjacentHTML(
      "beforeend",
      row(count++),
    );
    add.disabled = count >= 100;
  };
}
