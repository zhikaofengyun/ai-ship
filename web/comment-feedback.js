import { area, esc, field, formData, modal, select, uid } from "./ui.js";

const topics = {
  product: "商品与质量",
  usage: "使用方法",
  price: "价格",
  delivery: "配送",
  content: "视频内容",
  other: "其他",
};
const intents = {
  question: "提问",
  request: "需求",
  complaint: "投诉",
  praise: "认可",
  other: "其他",
};
const sentiments = {
  positive: "正面",
  neutral: "中性",
  negative: "负面",
  uncertain: "未确定",
};
const path = "/production/comment-feedback/";
const objectName = (r) => r.data.name || r.data.platform_comment_id || r.id;
const commentRows = (ctx) =>
  ctx.records("Comment").filter((r) => r.id.startsWith("feedback_"));
function choice(label, name, records, empty) {
  return select(label, name, [
    ["", empty],
    ...records.map((r) => [r.id, objectName(r)]),
  ]);
}
function form(ctx, title, html, submit) {
  const request_id = uid();
  const project_id = ctx.requireProject();
  return modal(
    title,
    `<form data-form class="form-stack">${html}<button class="button primary" type="submit">${esc(title)}</button></form>`,
    async (f) => {
      const [action, data] = submit(formData(f));
      await ctx.api(path + action, {
        method: "POST",
        body: { ...data, project_id, request_id },
      });
      await ctx.reloadData();
      await ctx.refresh();
    },
  );
}
function observe(ctx) {
  const rows = commentRows(ctx);
  const dlg = form(
    ctx,
    "记录评论观察",
    choice("已有评论", "existing", rows, "首次记录") +
      choice("来源账号", "account_id", ctx.records("Account"), "选择账号") +
      field("平台帖子标识", "platform_post_id", "", "text", "required") +
      field("平台评论标识", "platform_comment_id", "", "text", "required") +
      field(
        "采集时间（含时区）",
        "observed_at",
        new Date().toISOString(),
        "text",
        "required",
      ) +
      field("摘录来源说明", "provenance", "", "text", "required") +
      field("保留依据", "retention_basis", "", "text", "required") +
      select("可见状态", "visibility", [
        ["visible", "可用节选"],
        ["unavailable", "不可见"],
        ["deleted", "已删除"],
      ]) +
      "<div data-excerpt>" +
      area("授权保留的去标识节选", "excerpt", "", 'maxlength="4000"') +
      field("保留至（含时区）", "retain_until", "", "text") +
      "</div><div data-unavailable hidden>" +
      field("正文不可得的原因", "unavailable_reason", "") +
      "</div>",
    (v) => {
      const old = rows.find((r) => r.id === v.existing);
      return [
        "observe",
        {
          account_id: old?.data.account_id || v.account_id,
          platform_post_id: old?.data.platform_post_id || v.platform_post_id,
          platform_comment_id:
            old?.data.platform_comment_id || v.platform_comment_id,
          expected_revision: old?.version || 0,
          observed_at: v.observed_at,
          provenance: v.provenance,
          retention_basis: v.retention_basis,
          visibility: v.visibility,
          excerpt: v.visibility === "visible" ? v.excerpt : null,
          retain_until: v.visibility === "visible" ? v.retain_until : null,
          unavailable_reason:
            v.visibility === "visible" ? null : v.unavailable_reason,
        },
      ];
    },
  );
  const f = dlg.querySelector("form[data-form]");
  f.elements.account_id.required = true;
  const sync = () => {
    const old = rows.find((r) => r.id === f.elements.existing.value);
    for (const k of ["account_id", "platform_post_id", "platform_comment_id"]) {
      f.elements[k].disabled = !!old;
      if (old) f.elements[k].value = old.data[k];
    }
    const visible = f.elements.visibility.value === "visible";
    f.querySelector("[data-excerpt]").hidden = !visible;
    f.querySelector("[data-unavailable]").hidden = visible;
    f.elements.excerpt.required = visible;
    f.elements.retain_until.required = visible;
    f.elements.unavailable_reason.required = !visible;
  };
  f.elements.existing.onchange = sync;
  f.elements.visibility.onchange = sync;
  sync();
}
function classify(ctx) {
  const rows = commentRows(ctx).filter(
    (r) => r.data.visibility_state === "visible",
  );
  const dlg = form(
    ctx,
    "保存人工分类",
    choice("评论", "comment", rows, "选择节选") +
      '<p data-preview role="status"></p>' +
      select("主题", "topic", Object.entries(topics)) +
      select("反馈意图", "intent", Object.entries(intents)) +
      select("语气判断", "sentiment", Object.entries(sentiments)),
    (v) => {
      const item = rows.find((r) => r.id === v.comment);
      if (!item) throw new Error("请选择评论。");
      const previous = ctx
        .records("Insight")
        .find((r) => r.id === "feedback_class_" + item.id);
      return [
        "classify",
        {
          comment: { id: item.id, version: item.version },
          topic: v.topic,
          intent: v.intent,
          sentiment: v.sentiment,
          expected_classification_version: previous?.version || 0,
        },
      ];
    },
  );
  const f = dlg.querySelector("form[data-form]"),
    button = f.querySelector("button[type=submit]");
  button.disabled = true;
  f.elements.comment.onchange = async () => {
    const id = f.elements.comment.value;
    const el = f.querySelector("[data-preview]");
    el.textContent = "";
    button.disabled = true;
    if (!id) return;
    try {
      const r = await ctx.api(
        path +
          encodeURIComponent(ctx.requireProject()) +
          "/" +
          encodeURIComponent(id) +
          "/body",
      );
      if (f.elements.comment.value !== id) return;
      el.textContent = r.excerpt || "节选已过期或不可见。";
      button.disabled = !r.available;
    } catch (error) {
      if (f.elements.comment.value === id) el.textContent = error.message;
    }
  };
}
async function summary(ctx) {
  const r = await ctx.api(
    path + encodeURIComponent(ctx.requireProject()) + "/summary",
  );
  const section = (name, counts, labels) =>
    `<h3>${esc(name)}</h3><p>${
      Object.entries(counts)
        .map(([k, v]) => esc(labels[k] ?? k) + " " + v)
        .join(" · ") || "暂无分类"
    }</p>`;
  modal(
    "评论洞察",
    `<p>当前项目 ${r.observations} 条人工记录，可用且已分类 ${r.classified} 条，待分类 ${r.unclassified} 条，不可用 ${r.unavailable} 条。</p>` +
      section("主题", r.topics, topics) +
      section("反馈意图", r.intents, intents) +
      section("人工语气判断", r.sentiments, sentiments),
  );
}
export function feedback(ctx) {
  const d = modal(
    "评论与洞察",
    '<div class="dialog-actions"><button data-observe class="button secondary">录入或更新评论</button><button data-classify class="button secondary">阅读节选并分类</button><button data-summary class="button primary">查看项目洞察</button></div>',
  );
  d.querySelector("[data-observe]").onclick = () => observe(ctx);
  d.querySelector("[data-classify]").onclick = () => classify(ctx);
  d.querySelector("[data-summary]").onclick = () => summary(ctx);
}
