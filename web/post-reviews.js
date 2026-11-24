import { area, esc, field, formData, modal, select, uid } from "./ui.js";

export function postReview(ctx) {
  const project_id = ctx.requireProject(),
    path = "/production/post-reviews/";
  const dialog = modal(
    "帖子表现复盘",
    '<div class="dialog-actions"><button data-csv class="button secondary">导入指标 CSV</button><button data-compare class="button primary">比较两个帖子</button></div>',
  );
  dialog.querySelector("[data-csv]").onclick = () => {
    const request_id = uid();
    modal(
      "导入指标 CSV",
      '<form data-form class="form-stack">' +
        field(
          "UTF-8 CSV",
          "file",
          "",
          "file",
          'accept=".csv,text/csv" required',
        ) +
        '<p>列名沿用指标接口。帖子比较需要 account_id、post_record_id、metric_name、value、unit、window_start、window_end、observed_at、source、counting_mode、paid_or_organic、data_mature_after。缺值需填写 missing_reason。</p><button type="submit" class="button primary">导入并核对</button></form>',
      async (f) => {
        const file = f.elements.file.files[0];
        if (file.size > 2000000) throw new Error("单个 CSV 上限为 2 MB。");
        const csv_text = new TextDecoder("utf-8", {
          fatal: true,
          ignoreBOM: true,
        }).decode(await file.arrayBuffer());
        const r = await ctx.api(path + "archive", {
          method: "POST",
          body: { request_id, project_id, filename: file.name, csv_text },
        });
        await ctx.reloadData();
        await ctx.refresh();
        modal(
          "CSV 导入结果",
          `<p>新增 ${r.count} 条指标快照，${r.duplicates.length} 行与已有快照相同。</p>`,
        );
      },
    );
  };
  dialog.querySelector("[data-compare]").onclick = () => {
    const metrics = ctx.records("Metric").filter((r) => r.data.post_record_id);
    const options = [
      ["", "选择一个带帖子绑定的指标"],
      ...metrics.map((r) => [
        r.id,
        [
          ctx.records("Post").find((p) => p.id === r.data.post_record_id)?.data
            .platform_post_id || r.data.post_record_id,
          r.data.metric_name,
          r.data.value_or_null ?? "缺失",
          r.data.observed_at,
        ].join(" · "),
      ]),
    ];
    const request_id = uid();
    modal(
      "比较帖子并登记假设",
      '<form data-form class="form-stack">' +
        select("参照帖子与指标", "baseline", options, "", "required") +
        select("比较帖子与指标", "comparison", options, "", "required") +
        area("可能的解释", "hypothesis", "", "required") +
        area("反证或可能的混杂因素", "counterevidence", "", "required") +
        area("下一版改动", "proposed_change", "", "required") +
        area("验证方法", "experiment_method", "", "required") +
        field("停止条件", "stop_rule", "", "text", "required") +
        '<button type="submit" class="button primary">核对可比性并保存</button></form>',
      async (f) => {
        const body = formData(f);
        for (const k of ["baseline", "comparison"]) {
          const m = metrics.find((r) => r.id === body[k]);
          body[k] = { id: m.id, version: m.version };
        }
        const r = await ctx.api(path + "compare", {
          method: "POST",
          body: { project_id, request_id, ...body },
        });
        await ctx.reloadData();
        await ctx.refresh();
        const d = r.insight.data;
        modal(
          "整帖比较结果",
          `<p>后者减去参照值为 ${esc(d.difference)}。两组分别核对 ${d.observations.map((o) => o.snapshot_count).join("、")} 份同口径快照。</p><p>${esc(d.uncertainty)}</p>`,
        );
      },
    );
  };
}
