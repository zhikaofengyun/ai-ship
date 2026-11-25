import { $, esc, field, modal, select } from "./ui.js";

const value = (n, places = 1) =>
  Number.isFinite(n)
    ? n.toLocaleString("zh-CN", { maximumFractionDigits: places })
    : "未测得";
export async function batchObservability(ctx, pid, selected) {
  const route = "/production/batches";
  const [health, history] = await Promise.all([
    ctx.api(
      route +
        "/observability/health?" +
        new URLSearchParams({ project_id: pid }),
    ),
    ctx.api(route + "?" + new URLSearchParams({ project_id: pid, limit: 100 })),
  ]);
  if (ctx.state.projectId !== pid) return;
  const batches = history.batches,
    options = batches.map((b) => [
      b.id,
      b.name + " · " + b.total.toLocaleString() + " 条",
    ]);
  modal(
    "批次运行监测",
    `<dl class="detail-facts">
    <div><dt>最早等待时间</dt><dd>${value(health.queued_oldest_seconds)} 秒</dd></div>
    <div><dt>过期租约</dt><dd>${value(health.expired_leases)}</dd></div>
    <div><dt>待核对回执</dt><dd>${value(health.waiting_receipts)}</dd></div></dl>
    ${health.failures?.length ? `<ul class="batch-observations">${health.failures.map((f) => `<li>${esc(f.code)} · ${esc(f.count)} 次</li>`).join("")}</ul>` : ""}
    ${
      batches.length >= 2
        ? `<form data-form class="form-grid">${select("参照批次", "left", options, selected || options[0][0], "required")}${select("对照批次", "right", options, batches.find((b) => b.id !== (selected || options[0][0])).id, "required")}
      ${field("抽查作品数", "size", 8, "number", 'min="1" max="32" step="1" required')}<button type="submit" class="button primary">比较两批</button></form>`
        : "<p>项目中有两个批次后可比较运行表现。</p>"
    }`,
    async (form) => {
      const a = form.elements.left.value,
        b = form.elements.right.value;
      if (a === b) throw new Error("请选择两个不同的批次。");
      const r = await ctx.api(
        route +
          "/observability/compare?" +
          new URLSearchParams({
            project_id: pid,
            left: a,
            right: b,
            sample_size: form.elements.size.value,
          }),
      );
      const reasons = {
        output_kind: "交付类型不同",
        frame_format: "画幅或帧率不同",
        duration_not_identical_fixed: "时长不固定或不相同",
        not_both_fully_completed: "两批尚未全部完成",
        throughput_unavailable: "尚无有效吞吐记录",
      };
      const bool = (v) => (v == null ? "待核对" : v ? "一致" : "不同");
      modal(
        "批次运行对照",
        `<table class="data-table"><thead><tr><th>批次</th><th>规格</th><th>每小时完成数</th><th>GPU 费用</th></tr></thead><tbody>${[r.left, r.right].map((row) => `<tr><td>${esc(row.name)}</td><td>${esc(row.format.width)} × ${esc(row.format.height)} · ${esc(row.format.fps)} fps</td><td>${value(row.throughput_per_hour)}</td><td>${value(row.cost.actual_gpu_cost)}</td></tr>`).join("")}</tbody></table>
        <p>${r.comparison.comparable ? "吞吐比 · " + value(r.comparison.throughput_ratio, 3) : "对比条件不同 · " + esc(r.comparison.reasons.map((x) => reasons[x] || x).join("、"))}</p>
        <details><summary>核对 ${r.sample.length} 对作品</summary><table class="data-table"><thead><tr><th>序号</th><th>源镜头顺序</th><th>文件摘要</th></tr></thead><tbody>${r.sample.map((s) => `<tr><td>#${s.ordinal}</td><td>${bool(s.same_clip_sequence)}</td><td>${bool(s.same_file)}</td></tr>`).join("")}</tbody></table></details>`,
      );
    },
  );
}
