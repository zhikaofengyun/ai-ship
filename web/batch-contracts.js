import { $, esc, field, formData, modal, select, uid } from "./ui.js";

const labels = {
  require_full_decode: "完整解码",
  subtitle_language: "字幕语言",
};
const rules = [
  ["width", "宽度（像素）", 1],
  ["height", "高度（像素）", 1],
  ["fps", "每秒帧数", 1],
  ["duration_seconds", "目标时长（秒）", "any"],
  ["duration_tolerance_seconds", "时长容差（秒）", "any"],
  ["min_distinct_sources", "至少不同来源数", 1],
  ["min_editorial_roles", "至少剪辑用途数", 1],
  ["min_clip_seconds", "镜头最短秒数", "any"],
  ["max_clip_seconds", "镜头最长秒数", "any"],
  ["max_generation_calls", "最多生成调用次数", 1],
];
export async function chooseBatchContract(ctx, pid, current, apply) {
  const path = "/production/batches/contracts",
    data = await ctx.api(path + "?" + new URLSearchParams({ project_id: pid }));
  if (ctx.state.projectId !== pid) return;
  const items = data.contracts;
  const d = modal(
    "批次验收要求",
    `<form data-form class="form-stack">
    ${select("已有验收版本", "contract", [["", "选择验收要求"], ...items.map((r) => [r.id, (r.data.name || r.id) + " · v" + r.version])], current?.id, "required")}
    <button type="submit" class="button primary">应用所选版本</button></form>
    <div class="form-actions"><button type="button" class="button secondary" data-create>新建验收要求</button>${items.length ? '<button type="button" class="button ghost" data-revise>修订所选版本</button>' : ""}</div>`,
    async (f) => {
      const c = items.find((r) => r.id === f.elements.contract.value);
      if (c) apply({ id: c.id, version: c.version, name: c.data.name || c.id });
    },
  );
  function edit(prev) {
    const base = prev?.data.output_requirements || {},
      requestKey = uid();
    modal(
      prev ? "修订验收要求" : "新建验收要求",
      `<form data-form class="form-stack">${field("名称", "name", prev?.data.name || "", "text", "required")}
      <div class="form-grid">${rules.map(([key, label, step]) => field(label, key, base[key] ?? "", "number", 'min="0" step="' + step + '"')).join("")}
      ${field("字幕语言", "subtitle_language", base.subtitle_language || "")}</div>
      <label class="check"><input type="checkbox" name="decode" ${base.require_full_decode ? "checked" : ""}>要求成片完整解码</label>
      <button class="button primary" type="submit">保存并应用到批次</button></form>`,
      async (f) => {
        const v = formData(f),
          requirements = {};
        for (const [key] of rules)
          if (v[key] !== "") requirements[key] = Number(v[key]);
        if (v.subtitle_language?.trim())
          requirements.subtitle_language = v.subtitle_language.trim();
        if (f.elements.decode.checked) requirements.require_full_decode = true;
        const r = await ctx.api(path, {
          method: "POST",
          body: {
            project_id: pid,
            name: v.name.trim(),
            requirements,
            request_id: requestKey,
            ...(prev
              ? { previous: { id: prev.id, version: prev.version } }
              : {}),
          },
        });
        apply({
          id: r.contract.id,
          version: r.contract.version,
          name: r.contract.data.name || r.contract.id,
        });
      },
      true,
    );
  }
  $("[data-create]", d).onclick = () => edit();
  const revise = $("[data-revise]", d);
  if (revise)
    revise.onclick = () => {
      const r = items.find((r) => r.id === $("[name=contract]", d).value);
      if (r) edit(r);
      else ctx.toast("先选择要修订的验收版本。", "error");
    };
}

export function showAcceptance(audit) {
  const statuses = {
    passed: "通过",
    failed: "未通过",
    unknown: "缺少证据",
    deferred: "待编码后验证",
  };
  const names = {
    ...Object.fromEntries(rules.map(([k, v]) => [k, v])),
    ...labels,
  };
  const show = (v) =>
    v == null
      ? "未测得"
      : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);
  modal(
    "作品验收记录",
    `<div class="table-scroll"><table class="data-table"><thead><tr><th>检查项</th><th>要求</th><th>实测</th><th>结果</th></tr></thead><tbody>${audit.data.checks.map((c) => `<tr><td>${esc(names[c.name] || c.name)}</td><td>${esc(show(c.expected))}</td><td>${esc(show(c.observed))}</td><td>${esc(statuses[c.status] || c.status)}</td></tr>`).join("")}</tbody></table></div>`,
    null,
    true,
  );
}
