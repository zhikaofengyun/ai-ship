import { $, area, esc, field, formData, modal, select, uid } from "./ui.js";

const topics = {
  tone: "语气",
  pace: "节奏",
  opening: "开场",
  framing: "构图",
  captions: "字幕",
  music: "配乐",
};
const scopes = { all: "全部模型模式", model: "单导演", team: "Agent 小队" };
const instant = (label, key) =>
  field(label, key, "", "datetime-local", 'step="1"');
export async function editPreferences(ctx) {
  const project_id = ctx.requireProject(),
    path = "/execution/edit-preferences";
  const d = modal(
    "项目剪辑偏好",
    `<form data-lookup class="form-grid">${select("查看模式", "mode", [
      ["model", "单导演"],
      ["team", "Agent 小队"],
    ])}
    ${instant("有效时刻（默认现在）", "valid_at")}${instant("当时已知的信息（默认现在）", "known_at")}
    <button class="button secondary">查看偏好</button></form>
    <ul data-prefs class="batch-observations"></ul><p data-notice role="status"></p>
    ${
      ["owner", "reviewer"].includes(ctx.state.health.actor.role)
        ? `<details><summary>设置或撤回偏好</summary><form data-write class="form-stack">
      ${select("剪辑属性", "key", Object.entries(topics))}${select("适用模式", "mode", Object.entries(scopes))}
      ${select("操作", "action", [
        ["set", "设置"],
        ["revoke", "撤回"],
      ])}${area("偏好内容", "value", "", "maxlength=400 required")}${field("变更依据", "reason", "", "text", "required")}
      <div class="form-grid">${instant("生效时间（默认现在）", "valid_from")}${instant("失效时间（可选）", "valid_until")}</div>
      <button class="button primary" type="submit" disabled>保存变更</button></form></details>`
        : ""
    }`,
  );
  const modalId = d.dataset.modalId,
    active = () =>
      d.open &&
      d.dataset.modalId === modalId &&
      ctx.state.projectId === project_id;
  const lookup = $("[data-lookup]", d),
    form = $("[data-write]", d),
    status = $("[data-notice]", d);
  let heads = [],
    serial = 0,
    lastIntent = "",
    lastKey = "";
  async function read() {
    const ticket = ++serial,
      v = formData(lookup),
      q = new URLSearchParams({ project_id, mode: v.mode });
    for (const k of ["valid_at", "known_at"])
      if (v[k]) q.set(k, new Date(v[k]).toISOString());
    if (form) form.querySelector("[type=submit]").disabled = true;
    try {
      const result = await ctx.api(path + "?" + q);
      if (!active() || serial !== ticket) return;
      heads = result.current_heads;
      $("[data-prefs]", d).innerHTML = result.preferences
        .map(
          (p) =>
            `<li><strong>${esc(topics[p.key])}</strong> · ${esc(p.value)} <small>${esc(scopes[p.mode])} · v${esc(p.profile.version)}</small></li>`,
        )
        .join("");
      status.textContent = result.preferences.length
        ? ""
        : "这个时刻没有有效的项目偏好。";
      if (form) form.querySelector("[type=submit]").disabled = false;
    } catch (err) {
      if (active() && serial === ticket) status.textContent = err.message;
    }
  }
  lookup.onsubmit = (e) => {
    e.preventDefault();
    read();
  };
  if (form) {
    form.elements.action.onchange = () => {
      const revoke = form.elements.action.value === "revoke";
      for (const k of ["value", "valid_until"]) {
        form.elements[k].disabled = revoke;
        form.elements[k].closest("label").hidden = revoke;
      }
    };
    form.onsubmit = async (e) => {
      e.preventDefault();
      const v = formData(form);
      const body = {
        project_id,
        key: v.key,
        mode: v.mode,
        action: v.action,
        reason: v.reason,
        expected_version:
          heads.find((h) => h.key === v.key && h.mode === v.mode)
            ?.latest_version || 0,
      };
      if (v.action === "set") body.value = v.value;
      for (const k of ["valid_from", "valid_until"])
        if (v[k]) body[k] = new Date(v[k]).toISOString();
      const intent = JSON.stringify(body);
      if (intent !== lastIntent) {
        lastKey = uid();
        lastIntent = intent;
      }
      const submit = form.querySelector("[type=submit]");
      submit.disabled = true;
      try {
        await ctx.api(path, {
          method: "POST",
          body: { ...body, request_id: lastKey },
        });
        if (!active()) return;
        lastIntent = "";
        form.reset();
        form.elements.action.onchange();
        await read();
      } catch (err) {
        if (active()) {
          if (err.status === 409) await read();
          if (active()) {
            submit.disabled = false;
            status.textContent = err.message;
          }
        }
      }
    };
  }
  await read();
}
