import { area, esc, field, formData, modal, toast, uid } from "./ui.js";

const signed = (n) => (n > 0 ? "+" : "") + n.toFixed(1);
const summary = (arm) =>
  arm.posts +
  " 篇 · 区间起点 " +
  arm.entrants +
  " 人次 · 终点 " +
  arm.retained +
  " 人次";
const pin = (row) => ({ id: row.source_id, version: row.source_version });

export function cohortFrom(filters) {
  return {
    account_id: filters.account_id,
    traffic: filters.traffic,
    ...Object.fromEntries(
      [
        "start_seconds",
        "end_seconds",
        "cohort_after_hours",
        "cohort_hours",
      ].map((key) => [key, Number(filters[key])]),
    ),
  };
}

export function hypothesisForm(ctx, heading, intro, endpoint, base, onReturn) {
  const request_id = uid();
  modal(
    heading,
    "<p>" +
      esc(intro) +
      '</p><form data-form class="form-stack">' +
      area(
        "推测这个位置表现好的或差的原因",
        "hypothesis",
        "",
        "required maxlength=3000",
      ) +
      area(
        "其他可能解释或反例",
        "counterevidence",
        "",
        "required maxlength=3000",
      ) +
      area(
        "下一版准备如何使用或替换这个素材",
        "next_edit",
        "",
        "required maxlength=3000",
      ) +
      area(
        "什么结果会让你停止沿用这个判断",
        "stop_rule",
        "",
        "required maxlength=3000",
      ) +
      '<button type="submit" class="button primary">固定证据并保存</button></form>',
    async (edit) => {
      const dialog = document.querySelector("#dialog"),
        id = dialog.dataset.modalId;
      const result = await ctx.api("/production/post-reviews/" + endpoint, {
        method: "POST",
        body: { ...base, request_id, ...formData(edit) },
      });
      if (ctx.state.projectId !== base.project_id) return;
      await ctx.reloadData();
      if (
        !dialog.open ||
        dialog.dataset.modalId !== id ||
        ctx.state.projectId !== base.project_id
      )
        return;
      const ready = modal(
        "选材假设已保存",
        "<p>" +
          esc(result.insight.data.name) +
          '。生成下一版时，可在 AI 制作台的迭代依据中选用这个版本。</p><button class="button secondary" data-return>返回帖子复盘</button>',
      );
      ready.querySelector("[data-return]").onclick = onReturn;
    },
  );
}

function receiptDetails(receipts) {
  return receipts
    .map(
      (r) =>
        "<p>" +
        esc(r.source_note) +
        " · 首播总数 " +
        r.cohort_denominator +
        " · " +
        r.entrants +
        " → " +
        r.retained +
        " · 帖子 " +
        esc(r.post) +
        " v" +
        r.post_version +
        " · 回执 " +
        esc(r.receipt) +
        " v" +
        r.receipt_version +
        "</p>",
    )
    .join("");
}

function dayDetail(day) {
  let html =
    "<details><summary>" +
    esc(day.published_day_utc) +
    " · " +
    signed(day.difference_pp) +
    " 个百分点</summary>";
  for (const [label, group] of [
    ["基准", day.baseline],
    ["候选", day.candidate],
  ]) {
    html +=
      "<p>" +
      label +
      " · " +
      summary(group) +
      " · 帖子等权留存 " +
      (100 * group.mean_post_retention).toFixed(1) +
      "%</p>" +
      receiptDetails(group.example_receipts);
  }
  return html + "</details>";
}

export async function comparePlacements(
  ctx,
  filters,
  left,
  right,
  back,
  onReturn,
) {
  const query = {
    project_id: filters.project_id,
    cohort: cohortFrom(filters),
    role: left.role,
    baseline: pin(left),
    candidate: pin(right),
    published_from: null,
    published_through: null,
  };
  const limit = 15;
  async function show(offset) {
    const host = document.querySelector("#dialog"),
      id = host.dataset.modalId;
    const report = await ctx.api(
      "/production/post-reviews/placement-comparisons?offset=" +
        offset +
        "&limit=" +
        limit,
      { method: "POST", body: query },
    );
    if (
      ctx.state.projectId !== query.project_id ||
      !host.open ||
      host.dataset.modalId !== id
    )
      return;
    const range = report.leave_one_day_out_range_pp,
      diagnostics = report.cohort_diagnostics;
    const result = report.matched_days
      ? " <h3>候选留存减去基准留存为 " +
        signed(report.difference_pp) +
        " 个百分点</h3><p>" +
        report.matched_days +
        " 个共同发布日。先对当天每个素材的帖子等权平均，再对日期等权平均。</p>" +
        (range
          ? "<p>逐日剔除一天重新计算，差值在 " +
            signed(range[0]) +
            " 至 " +
            signed(range[1]) +
            " 个百分点之间。</p>"
          : "<p>仅一个共同发布日，无法观察跨日稳定性。</p>")
      : "<p>没有两组都满足条件的共同发布日，当前无法估计差值。</p>";
    const shared = report.co_occurring_observations;
    const sharedDetails = shared.posts
      ? "<details><summary>查看两种素材共现时的留存</summary><p>" +
        summary(shared) +
        " · 帖子等权留存 " +
        (100 * shared.mean_post_retention).toFixed(1) +
        "%。同一帖子只计一次，两种素材共享这条观测。</p>" +
        receiptDetails(shared.example_receipts) +
        "</details>"
      : "";
    const dialog = modal(
      "同日素材对照",
      "<p>" +
        esc(filters.start_seconds) +
        "–" +
        esc(filters.end_seconds) +
        " 秒 · " +
        esc(left.role) +
        "</p>" +
        result +
        "<p>基准 · " +
        esc(left.name) +
        " v" +
        left.source_version +
        " · " +
        summary(report.matched_baseline) +
        "</p><p>候选 · " +
        esc(right.name) +
        " v" +
        right.source_version +
        " · " +
        summary(report.matched_candidate) +
        "</p>" +
        "<p>同帖共现 " +
        report.co_occurring_posts_excluded +
        " 篇，覆盖不全 " +
        report.partial_interval_posts_excluded +
        " 篇。日期未配对的基准和候选各 " +
        report.unmatched_baseline_posts +
        "、" +
        report.unmatched_candidate_posts +
        " 篇。最新回填冲突 " +
        diagnostics.conflicting_latest_posts +
        " 篇，回执改版待复核 " +
        diagnostics.revised_latest_receipts +
        " 篇。</p>" +
        "<p>受众、投放和其他镜头仍可能影响留存。逐日差值和样本量用于提出下一次剪辑实验。</p>" +
        '<details><summary>限定发布日期（UTC）</summary><form class="form-stack" data-form>' +
        field("从", "published_from", query.published_from || "", "date") +
        field(
          "至",
          "published_through",
          query.published_through || "",
          "date",
        ) +
        '<button type="submit" class="button secondary">应用日期</button></form></details>' +
        sharedDetails +
        report.days.map(dayDetail).join("") +
        '<div class="dialog-actions">' +
        (report.matched_days
          ? '<button class="button primary" data-freeze>沉淀对照假设</button>'
          : "") +
        (offset
          ? '<button class="button secondary" data-prev>上一页日期</button>'
          : "") +
        (offset + limit < report.matched_days
          ? '<button class="button secondary" data-next>下一页日期</button>'
          : "") +
        '<button class="button secondary" data-back>返回素材列表</button></div>',
      async (form) => {
        const dates = formData(form);
        query.published_from = dates.published_from || null;
        query.published_through = dates.published_through || null;
        await show(0);
      },
      true,
    );
    dialog.querySelector("[data-back]").onclick = back;
    const freeze = dialog.querySelector("[data-freeze]");
    if (freeze)
      freeze.onclick = () =>
        hypothesisForm(
          ctx,
          "沉淀同日对照假设",
          right.name +
            " 对比 " +
            left.name +
            "，" +
            report.matched_days +
            " 个共同日期，差值 " +
            signed(report.difference_pp) +
            " 个百分点。",
          "placement-comparison-hypotheses",
          { ...report.scope, evidence_hash: report.evidence_hash },
          onReturn,
        );
    for (const [key, next] of [
      ["prev", offset - limit],
      ["next", offset + limit],
    ]) {
      const button = dialog.querySelector("[data-" + key + "]");
      if (button)
        button.onclick = () =>
          show(next).catch((e) => toast(e.message, "error"));
    }
  }
  await show(0);
}
