import { area, esc, field, formData, modal, select, toast, uid } from "./ui.js";

const weights = {
  quality: "画面质量",
  relevance: "内容相关性",
  fatigue: "受众疲劳",
  confidence: "证据置信度",
  tag_completeness: "标注完整度",
  performance: "已观察表现",
};
const endpoint = "/production/score-policies/";
const managed = (r) => r.id.startsWith("score_release_");
const pinned = (r) => ({ id: r.id, version: r.version });
const itemName = (r) => r.data.name || r.data.filename || r.data.title || r.id;

function choice(ctx, kind, label, name, filter = managed) {
  return select(
    label,
    name,
    [
      ["", "请选择"],
      ...ctx
        .records(kind)
        .filter(filter)
        .map((r) => [r.id, itemName(r) + " · " + r.id.slice(-6)]),
    ],
    "",
    "required",
  );
}
function fact(ctx, kind, id) {
  const r = ctx.records(kind).find((v) => v.id === id);
  if (!r) throw new Error("记录已更新，请刷新后重选。");
  return pinned(r);
}
async function current(ctx) {
  return (await ctx.api(endpoint + encodeURIComponent(ctx.requireProject())))
    .pointer;
}
function operation(ctx, name, title, body, submit, after = null) {
  const project_id = ctx.requireProject();
  const request_id = uid();
  return modal(
    title,
    `<form data-form class="form-stack">${body}<button type="submit" class="button primary">${esc(title)}</button></form>`,
    async (form) => {
      const data = submit(formData(form), form);
      const result = await ctx.api(endpoint + name, {
        method: "POST",
        body: { project_id, request_id, ...data },
      });
      await ctx.reloadData();
      await ctx.refresh();
      if (after) after(result);
      else toast(title + "已保存");
    },
  );
}
function receipt(title, detail) {
  modal(title, `<p role="status">${esc(detail)}</p>`);
}
async function proposal(ctx) {
  const ptr = await current(ctx);
  const active = ctx
    .records("Policy")
    .find((r) => r.id === ptr?.data.active.id);
  const baseline = active?.data.typed_config?.weights;
  const dialog = operation(
    ctx,
    "propose",
    "提出评分策略候选",
    field("候选名称", "name", "", "text", "required") +
      area("预期改善的排序与原因", "hypothesis", "", "required") +
      '<div class="form-grid">' +
      Object.entries(weights)
        .map(([k, label]) =>
          field(
            label,
            k,
            baseline ? baseline[k] || 0 : 1,
            "number",
            'min="0" max="1000000" step="any" required',
          ),
        )
        .join("") +
      '</div><button type="button" data-fit class="button secondary">从评分样本拟合权重</button>',
    (v) => ({
      name: v.name,
      hypothesis: v.hypothesis,
      expected_pointer_version: ptr?.version || 0,
      weights: Object.fromEntries(
        Object.keys(weights).map((k) => [k, Number(v[k])]),
      ),
    }),
  );
  dialog.querySelector("[data-fit]").onclick = () =>
    evaluate(ctx, true).catch((e) => toast(e.message, "error"));
}
async function evaluate(ctx, fit = false) {
  const ptr = await current(ctx);
  const candidates = (r) =>
    managed(r) && r.data.base_pointer_version === ptr?.version;
  const scores = ctx.records("AssetScore").map((r) => {
    const asset = ctx
      .records("AssetVersion")
      .find((a) => a.id === r.data.asset_version_ref);
    return [
      r.id,
      (asset ? itemName(asset) : r.data.asset_version_ref || "评分") +
        " · " +
        r.id.slice(-6),
    ];
  });
  const row = () =>
    `<fieldset data-case>${select("应排在前面的素材", "preferred", [["", "选择评价"], ...scores], "", "required")}${select("与其对照的素材", "comparison", [["", "选择评价"], ...scores], "", "required")}${field("人工排序依据", "reason", "", "text", "required")}<button type="button" data-remove class="button ghost">移除本组</button></fieldset>`;
  const dialog = operation(
    ctx,
    fit ? "optimize" : "evaluate",
    fit ? "拟合候选权重" : "回放素材评分",
    (fit
      ? field("候选名称", "name", "", "text", "required") +
        area("排序改善假设", "hypothesis", "", "required")
      : choice(ctx, "Policy", "当前基线的候选", "candidate", candidates)) +
      "<p>至少两组，素材互不重复。候选须通过每组排序，并纠正至少一个基线结果。所有使用的维度均须有观测。</p>" +
      `<div data-cases>${row()}${row()}</div><button type="button" data-add class="button secondary">增加对照组</button>`,
    (v, f) => ({
      ...(fit
        ? {
            name: v.name,
            hypothesis: v.hypothesis,
            expected_pointer_version: ptr?.version || 0,
          }
        : { candidate: fact(ctx, "Policy", v.candidate) }),
      [fit ? "training_cases" : "cases"]: [
        ...f.querySelectorAll("[data-case]"),
      ].map((r) => ({
        preferred: fact(
          ctx,
          "AssetScore",
          r.querySelector('[name="preferred"]').value,
        ),
        comparison: fact(
          ctx,
          "AssetScore",
          r.querySelector('[name="comparison"]').value,
        ),
        reason: r.querySelector('[name="reason"]').value,
      })),
    }),
    (result) =>
      fit
        ? receipt(
            "已保存候选权重",
            `训练样本排序从 ${result.data.optimizer.baseline_correct} 组正确提升至 ${result.data.optimizer.candidate_correct} 组。请用独立样本回放后审批。`,
          )
        : receipt(
            "素材排序回归",
            (result.data.result.passed ? "通过" : "未通过") +
              " · " +
              result.data.result.cases
                .map(
                  (c, i) =>
                    "第" +
                    (i + 1) +
                    "组 " +
                    (c.candidate.ordered ? "符合" : "未满足"),
                )
                .join("，"),
          ),
  );
  const rows = dialog.querySelector("[data-cases]");
  dialog.querySelector("[data-add]").onclick = () =>
    rows.insertAdjacentHTML("beforeend", row());
  rows.onclick = (e) => {
    if (e.target.closest("[data-remove]") && rows.children.length > 2)
      e.target.closest("[data-case]").remove();
  };
}
async function promotion(ctx) {
  const ptr = await current(ctx);
  const experiments = ctx
    .records("Experiment")
    .filter(
      (r) =>
        managed(r) &&
        r.data.result?.passed &&
        ctx
          .records("Policy")
          .some(
            (p) =>
              p.id === r.data.candidate.id &&
              p.data.base_pointer_version === ptr?.version,
          ),
    );
  const approvals = ctx
    .records("Approval")
    .filter(
      (r) =>
        managed(r) &&
        r.data.pointer_version === ptr?.version &&
        new Date(r.data.expires_at).getTime() > Date.now(),
    );
  const d = modal(
    "评分策略晋升",
    "<p>批准绑定一次回归和候选。生效后，后续使用项目策略的评分将引用新权重。</p>" +
      '<div class="dialog-actions"><button class="button secondary" data-approve>审批回归结果</button><button class="button primary" data-activate>生效已批准候选</button></div>',
  );
  d.querySelector("[data-approve]").onclick = () =>
    operation(
      ctx,
      "approve",
      "批准评分策略",
      select(
        "已通过的候选回归",
        "exp",
        [
          ["", "请选择"],
          ...experiments.map((r) => [
            r.id,
            itemName(
              ctx.records("Policy").find((p) => p.id === r.data.candidate.id),
            ) +
              " · " +
              r.id.slice(-6),
          ]),
        ],
        "",
        "required",
      ) +
        area("适用于此项目的理由", "reason", "", "required") +
        field(
          "批准有效期（含时区）",
          "expiry",
          new Date(Date.now() + 86400000).toISOString(),
          "text",
          "required",
        ),
      (v) => {
        const exp = experiments.find((r) => r.id === v.exp);
        return {
          candidate: pinned(exp.data.candidate),
          evaluation: pinned(exp),
          reason: v.reason,
          expires_at: v.expiry,
        };
      },
    );
  d.querySelector("[data-activate]").onclick = () =>
    operation(
      ctx,
      "activate",
      "生效评分策略",
      select(
        "本项目有效批准",
        "approval",
        [
          ["", "请选择"],
          ...approvals.map((r) => [
            r.id,
            (ctx.records("Policy").find((p) => p.id === r.data.subject_id)?.data
              .name || "候选") +
              " · " +
              r.id.slice(-6),
          ]),
        ],
        "",
        "required",
      ),
      (v) => ({ approval: fact(ctx, "Approval", v.approval) }),
    );
}
async function rollback(ctx) {
  const ptr = await current(ctx);
  if (!ptr) return receipt("尚未建立策略", "当前项目使用默认等权评分。");
  const evidence = [
    ...ctx.records("Experiment"),
    ...ctx.records("AssetScore"),
    ...ctx.records("Insight"),
  ];
  operation(
    ctx,
    "rollback",
    "提交回退决定",
    select("后续评分", "mode", [
      ["restore", "恢复上次可用基线"],
      ["pause", "暂停活动策略"],
    ]) +
      area("受影响范围和原因", "reason", "", "required") +
      select("关联反例或指标", "evidence", [
        ["", "紧急暂停，证据待补"],
        ...evidence.map((r) => [r.id, itemName(r) + " · " + r.id.slice(-6)]),
      ]),
    (v) => ({
      expected_pointer_version: ptr.version,
      reason: v.reason,
      pause_only: v.mode === "pause",
      evidence: v.evidence
        ? pinned(evidence.find((r) => r.id === v.evidence))
        : null,
    }),
    (r) =>
      receipt(
        "项目活动策略",
        r.pointer.data.release_state === "paused"
          ? "已暂停 · " + r.pointer.data.hold_reason
          : "已恢复上次基线",
      ),
  );
}
export const scorePolicyActions = {
  X02: ["提出评分策略候选", proposal],
  X03: ["回放冻结评分样本", (ctx) => evaluate(ctx)],
  X06: ["审批与生效", promotion],
  X07: ["回滚或暂停", rollback],
};
