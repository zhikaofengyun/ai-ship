import { $, esc, field, formData, modal, select, uid } from "./ui.js";

export async function harnessRuntime(ctx) {
  const project_id = ctx.requireProject(),
    path = "/harness/";
  const [runtime, items, registeredSkills] = await Promise.all([
    ctx.api(path + "runtime"),
    ctx.api(
      "/records?" +
        new URLSearchParams({ project_id, kind: "Workflow", limit: 2000 }),
    ),
    ctx.api(
      "/records?" +
        new URLSearchParams({ project_id, kind: "SkillVersion", limit: 2000 }),
    ),
  ]);
  if (ctx.state.projectId !== project_id) return;
  const workflows = (items.items || items).filter(
    (r) => !r.data.feature_operation,
  );
  let request_id = "",
    prior = "";
  const runtimeDetails = `<details><summary>执行环境与已登记路线 · ${runtime.routes.length} 条</summary><dl>${Object.entries(
    runtime.versions,
  )
    .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`)
    .join(
      "",
    )}</dl><ul>${runtime.routes.map((r) => `<li>${esc(r.route_id)} · ${esc(r.operation)}</li>`).join("")}</ul><small>运行环境指纹 · ${esc(runtime.fingerprint)}</small></details>`;
  const d = modal(
    "运行环境与配方",
    runtimeDetails +
      `<form data-form class="form-stack">${select("待核验的工作流", "workflow", [["", "选择已保存流程"], ...workflows.map((r) => [r.id, (r.data.name || r.id) + " · v" + r.version])], "", "required")}
    ${field("配方名称", "name", "", "text", 'maxlength="150" required')}<button type="submit" class="button primary">实测并登记配方</button></form>`,
    async (f) => {
      const v = formData(f),
        w = workflows.find((r) => r.id === v.workflow),
        body = {
          project_id,
          workflow: { id: w.id, version: w.version },
          name: v.name,
        };
      const intent = JSON.stringify(body);
      if (intent !== prior) {
        prior = intent;
        request_id = uid();
      }
      const r = await ctx.api(path + "qualify", {
        method: "POST",
        body: { ...body, request_id },
      });
      const d = modal(
        r.passed ? "配方实测通过" : "配方需要处理",
        `<ul class="batch-rows">${r.checks.map((c) => `<li><strong>${esc(c.name)} · ${c.passed ? "通过" : "未通过"}</strong><small>${esc(typeof c.detail === "object" ? JSON.stringify(c.detail) : c.detail)}</small></li>`).join("")}</ul>${r.passed && r.skill ? '<button type="button" data-dispatch class="button primary">运行已核验版本</button>' : ""}`,
      );
      if (r.passed && r.skill) bindDispatch(d, r.skill);
    },
  );
  const registered = (registeredSkills.items || registeredSkills).filter(
    (r) => r.kind === "SkillVersion" && r.data.workflow_pin,
  );
  if (registered.length) {
    const pick = document.createElement("div");
    pick.innerHTML =
      select("已登记配方", "skill", [
        ["", "选择版本"],
        ...registered.map((r, i) => [
          i,
          (r.data.name || r.id) + " · v" + r.version,
        ]),
      ]) +
      '<button type="button" data-dispatch class="button secondary" disabled>运行已核验版本</button>';
    d.append(pick);
    $("select", pick).onchange = () => {
      const v = $("select", pick).value;
      $("button", pick).disabled = v === "";
      if (v !== "") bindDispatch(pick, registered[Number(v)]);
    };
  }
  function bindDispatch(panel, skill) {
    const button = $("[data-dispatch]", panel),
      key = uid();
    button.onclick = async () => {
      button.disabled = true;
      try {
        const r = await ctx.api(path + "dispatch", {
          method: "POST",
          body: {
            project_id,
            skill: { id: skill.id, version: skill.version },
            request_id: key,
          },
        });
        if (ctx.state.projectId === project_id)
          modal("配方运行记录", ctx.runSummary(r));
      } catch (err) {
        ctx.toast(err.message, "error");
        button.disabled = false;
      }
    };
  }
}
