import { $, area, esc, field, formData, modal, select, uid } from "./ui.js";

const path = "/production/batches/market-briefs";
const kinds = {
  account: "Account",
  source: "Source",
  original_script: "Script",
  localized_script: "Script",
  product: "Product",
};
const names = {
  account: "目标账号",
  source: "市场规则来源",
  original_script: "原文脚本",
  localized_script: "本地化脚本",
  product: "商品凭据（可选）",
};
const label = (r) =>
  String(
    r.data.name ||
      r.data.title ||
      r.data.SKU ||
      r.data.handle ||
      r.data.text ||
      r.data.canonical_url ||
      r.id,
  ).slice(0, 90) +
  " · v" +
  r.version;
const pin = (r) => ({ id: r.id, version: r.version });
const same = (a, b) =>
  String(a || "").toLowerCase() === String(b || "").toLowerCase();

export async function chooseBatchMarket(ctx, pid, current, apply) {
  const project = ctx.project().data;
  const query = new URLSearchParams({ project_id: pid, limit: 500 });
  const { briefs } = await ctx.api(path + "?" + query);
  if (ctx.state.projectId !== pid) return;
  const visible = briefs.filter((r) => {
    const b = r.data.market_rules;
    return (
      r.data.status === "approved" &&
      Date.parse(b.valid_until) > Date.now() &&
      same(b.market, project.market) &&
      same(b.language, project.language)
    );
  });
  const canReview = ["owner", "reviewer"].includes(ctx.state.health.actor.role);
  const applyBrief = (r) => {
    const b = r.data.market_rules;
    apply({
      ...pin(r),
      name: r.data.name || r.id,
      market: b.market,
      language: b.language,
    });
  };
  const d = modal(
    "市场本地化简报",
    `<p class="dialog-desc">批次将逐条核对样片字幕、脚本与简报有效期。</p>${visible.length ? `<form class="form-stack" data-form>${select("已审核的市场简报", "brief", [["", "选择简报版本"], ...visible.map((r) => [r.id, label(r)])], current?.id, "required")}<p data-preview class="help-text"></p><button type="submit" class="button primary">应用简报版本</button></form>` : '<p class="batch-notice">当前项目没有在有效期内的匹配简报。</p>'}
      ${canReview ? '<div class="form-actions"><button type="button" class="button secondary" data-market-create>新建市场简报</button></div>' : ""}`,
    (f) => {
      const r = visible.find((r) => r.id === f.elements.brief.value);
      if (r) applyBrief(r);
    },
  );
  const chooser = $("[name=brief]", d);
  if (chooser) {
    chooser.onchange = () => {
      const r = visible.find((r) => r.id === chooser.value);
      const b = r?.data.market_rules;
      $("[data-preview]", d).textContent = b
        ? [
            b.market,
            b.language,
            "审阅人 " + r.data.reviewer,
            "有效至 " + new Date(b.valid_until).toLocaleString(),
            b.audience_hypothesis,
          ].join(" · ")
        : "";
    };
    chooser.onchange();
  }
  const newButton = $("[data-market-create]", d);
  if (newButton)
    newButton.onclick = async () => {
      newButton.disabled = true;
      try {
        if (!project.market || !project.language)
          throw new Error("请先在项目设置中填写目标市场和语言。");
        const read = (kind, q = "") =>
          ctx.api(
            "/records?" +
              new URLSearchParams({
                kind,
                project_id: pid,
                limit: 100,
                ...(q ? { q } : {}),
              }),
          );
        const lists = Object.fromEntries(
          await Promise.all(
            [...new Set(Object.values(kinds))].map(async (kind) => [
              kind,
              await read(kind),
            ]),
          ),
        );
        if (!d.open || !newButton.isConnected || ctx.state.projectId !== pid)
          return;
        const records = {};
        const allowed = (k, r) => {
          if (k === "account")
            return same(
              r.data.identity_market || r.data.region,
              project.market,
            );
          if (k === "source")
            return [
              "provenance",
              "canonical_url",
              "evidence",
              "original_url",
            ].some((f) => r.data[f]);
          if (k === "original_script") return !!r.data.text;
          if (k === "localized_script")
            return !!r.data.text && same(r.data.language, project.language);
          return true;
        };
        const options = (k, rows) => {
          records[k] = rows.filter((r) => allowed(k, r));
          return [
            ["", k === "product" ? "不绑定商品" : "请选择"],
            ...records[k].map((r) => [r.id, label(r)]),
          ];
        };
        const requestKey = uid();
        const editor = modal(
          "新建市场简报",
          `<form class="form-stack" data-form>
        <p class="dialog-desc">目标市场 ${esc(project.market)} · 字幕语言 ${esc(project.language)}。保存后将以你的身份登记审阅。</p>
        ${field("简报名称", "name", "", "text", 'required maxlength="160"')}
        <div class="form-grid">${Object.entries(kinds)
          .map(([k, kind]) => {
            const rows = lists[kind];
            return `<div>${select(names[k], k, options(k, rows), "", k === "product" ? "" : "required")}
          ${rows.length === 100 ? `<label class="field"><span>检索更多${esc(names[k])}</span><input type="search" data-query="${k}" placeholder="输入名称或记录编号"></label><button type="button" class="button small ghost" data-search="${k}">查找</button>` : ""}</div>`;
          })
          .join("")}
        ${field("简报有效至（当地时间）", "valid_until", "", "datetime-local", "required")}</div>
        ${area("受众假设与依据", "audience_hypothesis", "", "required maxlength=800")}${area("禁止表达，每行一条", "prohibited_phrases")}
        <button type="submit" class="button primary">审核并应用简报</button></form>`,
          async (f) => {
            const v = formData(f);
            const evidence = {};
            for (const k of Object.keys(kinds)) {
              const r = records[k].find((r) => r.id === v[k]);
              if (r) evidence[k] = pin(r);
              else if (k !== "product")
                throw new Error("请选择" + names[k] + "。");
            }
            const valid = new Date(v.valid_until);
            if (
              !Number.isFinite(valid.getTime()) ||
              valid.getTime() <= Date.now()
            )
              throw new Error("有效期须晚于当前时间。");
            const { brief } = await ctx.api(path, {
              method: "POST",
              body: {
                project_id: pid,
                request_id: requestKey,
                name: v.name.trim(),
                market: project.market,
                language: project.language,
                ...evidence,
                audience_hypothesis: v.audience_hypothesis.trim(),
                prohibited_phrases: [
                  ...new Set(
                    v.prohibited_phrases
                      .split(/\r?\n/)
                      .map((s) => s.trim())
                      .filter(Boolean),
                  ),
                ],
                valid_until: valid.toISOString(),
              },
            });
            if (ctx.state.projectId === pid) applyBrief(brief);
          },
          true,
        );
        for (const b of editor.querySelectorAll("[data-search]"))
          b.onclick = async () => {
            const k = b.dataset.search;
            b.disabled = true;
            try {
              const rows = await read(
                kinds[k],
                $(`[data-query="${k}"]`, editor).value.trim(),
              );
              if (!b.isConnected) return;
              const menu = $(`[name="${k}"]`, editor),
                prev = menu.value;
              menu.replaceChildren(
                ...options(k, rows).map(
                  ([id, text]) => new Option(text, id, false, id === prev),
                ),
              );
              if (!records[k].length) ctx.toast("没有匹配的项目记录。");
            } catch (e) {
              ctx.toast(e.message, "error");
            } finally {
              b.disabled = false;
            }
          };
      } catch (e) {
        ctx.toast(e.message, "error");
      } finally {
        newButton.disabled = false;
      }
    };
}
