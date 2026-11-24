import { area, esc, field, formData, modal, select, uid } from "./ui.js";

const pin = (record) => ({ id: record.id, version: record.version });
const name = (record) =>
  record.data.name || record.data.title || record.data.SKU || record.id;
const choices = (records) =>
  records.map((r) => [r.id, name(r) + " · v" + r.version]);
const chosen = (records, id) =>
  id ? pin(records.find((r) => r.id === id)) : null;
const submit = (message) =>
  '<button class="button primary" type="submit">' + esc(message) + "</button>";
const form = (html) => '<form class="form-stack" data-form>' + html + "</form>";
const required = 'required maxlength="200"';
const requiredNote = 'required maxlength="3000"';
const percent = (n) => Math.round(n * 100) + "%";
const dimensionLabels = {
  hook: "开头",
  narrative: "叙事",
  shot_families: "镜头族",
  sound: "声音",
  wording: "用词",
};

function verifyTrend(ctx) {
  const projectId = ctx.requireProject(),
    key = uid();
  const trends = ctx.records("Trend"),
    sources = ctx.records("Source").filter((s) => s.data.canonical_url);
  if (!trends.length || !sources.length)
    throw new Error("请先在「热点来源连接」登记热点及其出处。");
  const row = (i) =>
    "<fieldset><legend>证据 " +
    (i + 1) +
    "</legend>" +
    select(
      "来源记录",
      "source" + i,
      sources.map((r) => [r.id, r.data.canonical_url]),
    ) +
    field(
      "原始发布地址（转载请填原出处）",
      "original" + i,
      "",
      "url",
      'required maxlength="3000"',
    ) +
    field("原始发布方或同一编辑机构", "publisher" + i, "", "text", required) +
    field(
      "原始发布时间（当地时间）",
      "published" + i,
      "",
      "datetime-local",
      "required",
    ) +
    field(
      "看到这条证据的时间（当地时间）",
      "observed" + i,
      "",
      "datetime-local",
      "required",
    ) +
    select("与待核事实的关系", "stance" + i, [
      ["supports", "支持"],
      ["contradicts", "冲突"],
      ["context", "仅供背景"],
    ]) +
    area("核到的原话、转载关系或冲突点", "note" + i, "", requiredNote) +
    "</fieldset>";
  let count = 1;
  const d = modal(
    "核验热点出处与时效",
    form(
      select("待核热点", "trend", choices(trends)) +
        "<div data-observations>" +
        row(0) +
        '</div><button type="button" class="button secondary" data-add>增加证据</button>' +
        submit("核对并保存版本"),
    ),
    async (f) => {
      const v = formData(f);
      const evidence = Array.from({ length: count }, (_, i) => ({
        source: chosen(sources, v["source" + i]),
        original_url: v["original" + i],
        publisher_group: v["publisher" + i],
        published_at: new Date(v["published" + i]).toISOString(),
        observed_at: new Date(v["observed" + i]).toISOString(),
        stance: v["stance" + i],
        note: v["note" + i],
      }));
      const result = await ctx.api("/production/creative/verify-trend", {
        method: "POST",
        body: {
          project_id: projectId,
          request_id: key,
          trend: chosen(trends, v.trend),
          evidence,
        },
      });
      await ctx.reloadData();
      const r = result.data.source_review,
        labels = {
          expired: "过期",
          conflicting: "证据冲突",
          corroborated_as_reviewed: "已交叉核对",
          insufficient_independent_sources: "独立来源不足",
        };
      modal(
        "热点核验结果",
        "<p>" +
          esc(labels[r.outcome]) +
          "</p><p>有支持证据的独立出处 " +
          r.supporting_groups +
          " 组，共核对 " +
          r.observations.length +
          " 条记录。</p>",
      );
    },
    true,
  );
  const add = d.querySelector("[data-add]");
  add.disabled = sources.length <= 1;
  add.onclick = () => {
    d.querySelector("[data-observations]").insertAdjacentHTML(
      "beforeend",
      row(count++),
    );
    add.disabled = count >= Math.min(32, sources.length);
  };
}

function topic(ctx) {
  const projectId = ctx.requireProject(),
    project = ctx.project(),
    key = uid();
  const trends = ctx.records("Trend"),
    products = ctx.records("Product");
  modal(
    "主题与受众匹配",
    form(
      field("创作主题", "topic", "", "text", required) +
        field("目标受众", "audience", "", "text", required) +
        select("热点依据", "trend", [["", "常青主题"], ...choices(trends)]) +
        select("关联商品", "product", [
          ["", "非商品主题"],
          ...choices(products),
        ]) +
        '<label class="choice-row"><input type="checkbox" name="hypothesis"> 来源不足或冲突时，仅按待证假设使用</label>' +
        area("选择依据与待核实的假设", "rationale", "", requiredNote) +
        submit("匹配并保存候选"),
    ),
    async (f) => {
      const v = formData(f);
      const r = await ctx.api("/production/creative/match", {
        method: "POST",
        body: {
          project_id: projectId,
          project: pin(project),
          request_id: key,
          topic: v.topic,
          audience: v.audience,
          rationale: v.rationale,
          allow_hypothesis: Boolean(v.hypothesis),
          trend: chosen(trends, v.trend),
          product: chosen(products, v.product),
        },
      });
      await ctx.reloadData();
      const m = r.data.match_evidence;
      const labels = {
        audience: "受众",
        project_goal: "项目目标",
        product: "商品",
        trend: "热点",
      };
      modal(
        "匹配依据 · " + name(r),
        "<p>有词项匹配的字段占 " +
          percent(m.relevance) +
          "</p><dl>" +
          Object.entries(m.matched_terms)
            .map(
              ([k, words]) =>
                "<dt>" +
                esc(labels[k]) +
                "</dt><dd>" +
                esc(words.join("、") || "无词项命中") +
                "</dd>",
            )
            .join("") +
          "</dl>" +
          m.risks.map((risk) => "<p>" + esc(risk) + "</p>").join(""),
      );
    },
  );
}

function fields(i) {
  return (
    "<fieldset><legend>导演方案 " +
    (i + 1) +
    "</legend>" +
    field("路线名称", "name" + i, "", "text", required) +
    field("开头钩子", "hook" + i, "", "text", required) +
    area("叙事段落（每行一个）", "narrative" + i, "", requiredNote) +
    area("镜头族（每行一个）", "shot_families" + i, "", requiredNote) +
    field("声音结构", "sound" + i, "", "text", required) +
    area("候选脚本", "script" + i, "", requiredNote) +
    area("希望验证的假设", "hypothesis" + i, "", requiredNote) +
    area("素材与实现条件", "feasibility" + i, "", requiredNote) +
    "</fieldset>"
  );
}
function directions(ctx) {
  const projectId = ctx.requireProject(),
    key = uid(),
    briefs = ctx.records("Brief");
  if (!briefs.length) return topic(ctx);
  let count = 2;
  const d = modal(
    "制作导演候选",
    form(
      select("基础简报", "brief", choices(briefs)) +
        "<div data-proposals>" +
        fields(0) +
        fields(1) +
        '</div><button class="button secondary" type="button" data-add>增加路线</button>' +
        submit("保存候选与脚本"),
    ),
    async (f) => {
      const v = formData(f),
        proposals = [];
      for (let i = 0; i < count; i++) {
        const proposal = {};
        for (const k of [
          "name",
          "hook",
          "narrative",
          "shot_families",
          "sound",
          "script",
          "hypothesis",
          "feasibility",
        ]) {
          proposal[k] = ["narrative", "shot_families"].includes(k)
            ? v[k + i]
                .split(/\n/)
                .map((s) => s.trim())
                .filter(Boolean)
            : v[k + i];
        }
        proposals.push(proposal);
      }
      const r = await ctx.api("/production/creative/directions", {
        method: "POST",
        body: {
          project_id: projectId,
          request_id: key,
          brief: chosen(briefs, v.brief),
          proposals,
        },
      });
      await ctx.reloadData();
      ctx.toast("已保存 " + r.directions.length + " 组候选与脚本");
      await ctx.refresh();
    },
    true,
  );
  const add = d.querySelector("[data-add]");
  add.onclick = () => {
    d.querySelector("[data-proposals]").insertAdjacentHTML(
      "beforeend",
      fields(count++),
    );
    add.disabled = count >= 6;
  };
}
function preflight(ctx) {
  const projectId = ctx.requireProject(),
    key = uid(),
    briefs = ctx.records("Brief");
  if (!briefs.length) return topic(ctx);
  const d = modal(
    "创意重复预检",
    form(
      select("待检候选", "brief", choices(briefs)) +
        "<fieldset><legend>比较范围</legend><div data-history></div></fieldset>" +
        field(
          "复核阈值",
          "threshold",
          "0.7",
          "number",
          'min="0.01" max="1" step="0.01" required',
        ) +
        submit("检查结构与用词"),
    ),
    async (f) => {
      const v = formData(f);
      const r = await ctx.api("/production/creative/preflight", {
        method: "POST",
        body: {
          project_id: projectId,
          request_id: key,
          brief: chosen(briefs, v.brief),
          threshold: Number(v.threshold),
          comparisons: [...f.querySelectorAll("[name=history]:checked")].map(
            (el) => chosen(briefs, el.value),
          ),
        },
      });
      await ctx.reloadData();
      const data = r.data;
      modal(
        "预检结果",
        (data.coverage === "insufficient_history"
          ? "<p>参照不足，部分结构无法比较。</p>"
          : "") +
          data.evidence
            .map(
              (item) =>
                "<h3>" +
                esc(name(briefs.find((b) => b.id === item.brief.id))) +
                "</h3><dl>" +
                Object.entries(item.scores)
                  .map(
                    ([k, v]) =>
                      "<dt>" +
                      esc(dimensionLabels[k]) +
                      "</dt><dd>" +
                      percent(v) +
                      (item.review_dimensions.includes(k) ? " · 需复核" : "") +
                      "</dd>",
                  )
                  .join("") +
                "</dl>" +
                item.repeated_locations
                  .map(
                    (hit) =>
                      "<p>" +
                      esc(dimensionLabels[hit.dimension]) +
                      " " +
                      (hit.index + 1) +
                      " · " +
                      esc(hit.value) +
                      "</p>",
                  )
                  .join(""),
            )
            .join("") +
          "<p>" +
          esc(data.uncertainty) +
          "</p>",
      );
    },
  );
  const source = d.querySelector("[name=brief]");
  const history = d.querySelector("[data-history]");
  const show = () => {
    history.innerHTML =
      briefs
        .filter((b) => b.id !== source.value)
        .map(
          (b) =>
            '<label class="choice-row"><input name="history" type="checkbox" value="' +
            esc(b.id) +
            '"> ' +
            esc(name(b)) +
            "</label>",
        )
        .join("") || "暂无其他候选";
  };
  source.onchange = show;
  show();
}

export const creativeActions = {
  T02: { label: "核验热点出处", open: verifyTrend },
  T03: { label: "匹配主题与受众", open: topic },
  T04: { label: "编排导演路线", open: directions },
  T05: { label: "检查创意重复", open: preflight },
};
