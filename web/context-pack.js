import { area, esc, field, modal, select, toast } from "./ui.js";

const nextActions = {
  reconcile_side_effect: "先核对在途执行的实际结果",
  inspect_stop_request: "核对人工停止指令及当前状态",
  observe_worker: "查看正在运行的执行器",
  inspect_final_record: "查看终态记录与产物",
  inspect_blockers: "核对尚未完成的条件",
  inspect_checkpoint: "核对最近检查点",
};

async function showPack(ctx, run) {
  const result = await ctx.api(
    "/execution/" + encodeURIComponent(run.id) + "/context-pack",
  );
  const audit = result.audit.data,
    p = audit.context_packet;
  const facts = (value) => esc(JSON.stringify(value, null, 2));
  const kind = (id) =>
    ctx.state.catalog.entities.find((entity) => entity.id === id)?.label || id;
  const sources = [
    p.source_run,
    ...p.authorities.map((x) => x.source),
    ...(p.bound_inputs || []),
    ...p.evidence_refs,
    ...p.calls,
  ];
  const labels = {
    pending: "待处理",
    blocked: "待补齐",
    running: "执行中",
    unknown: "结果待核对",
    failed: "失败",
  };
  const list = (rows) =>
    rows.length
      ? "<ul>" + rows.map((row) => "<li>" + row + "</li>").join("") + "</ul>"
      : "";
  const warnings = result.source_changes.map(
    (x) =>
      esc(kind(x.current.kind)) +
      " · v" +
      Number(x.pinned.version) +
      " → v" +
      Number(x.current.version) +
      " · " +
      (x.reason === "VERSION_CHANGED" ? "内容已修订" : "有效性需复核"),
  );
  const pending = Object.entries(p.unresolved_nodes).map(
    ([id, x]) =>
      esc(id) +
      " · " +
      esc(labels[x.status] || x.status) +
      " · 尝试 " +
      Number(x.attempts || 0) +
      (x.error?.message ? " · " + esc(x.error.message) : ""),
  );
  const guards = p.authorities.flatMap((a) =>
    [
      "stop_conditions",
      "locks",
      "hard_constraints",
      "authorization_requirements",
    ].flatMap((key) =>
      a.facts[key] == null
        ? []
        : (Array.isArray(a.facts[key]) ? a.facts[key] : [a.facts[key]]).map(
            (value) =>
              esc(typeof value === "string" ? value : JSON.stringify(value)),
          ),
    ),
  );
  const panel = modal(
    "任务恢复证据",
    `<p><strong>${esc(typeof p.goal === "string" ? p.goal : JSON.stringify(p.goal))}</strong></p>
    <p>${esc(nextActions[p.next_read_or_review])} · 恢复依据来自运行 v${Number(p.source_run.version)}</p>
    ${result.run_advanced ? "<p>运行已有新的检查点，请按当前状态复核。</p>" : ""}
    ${p.limits_and_stops.control_request === "RequestCancel" || p.limits_and_stops.control_request === "SuspendTask" ? `<p><strong>${p.limits_and_stops.control_request === "RequestCancel" ? "已请求取消" : "已请求暂停"}</strong> · ${esc(p.limits_and_stops.control_reason || "")}</p>` : ""}
    ${warnings.length ? "<h3>需重新核对的来源</h3>" + list(warnings) : ""}
    <h3>运行进度</h3><p>完成 ${Object.keys(p.completed).length} 个节点 · 未解 ${pending.length} 个节点 · ${p.calls.length} 条调用</p>
    ${list([...pending, ...p.blockers.map((x) => esc(x.message || x.code || x))])}
    ${guards.length ? "<h3>停止条件与人工约束</h3>" + list(guards) : ""}
    <h3>固定来源</h3>${sources.map((x, i) => `<button type="button" class="button secondary" data-pack-source="${i}">${esc(kind(x.kind))} · v${Number(x.version)} · ${esc(x.id.slice(-8))}</button>`).join(" ")}
    ${p.omitted_details.length ? `<p>有 ${p.omitted_details.length} 项明细超出窗口，可打开对应来源查阅。</p>` : ""}
    <details><summary>查看精确事实、节点产物与资源边界</summary><pre>${facts({ 任务边界: p.limits_and_stops, 来源事实: p.authorities, 已完成: p.completed, 产物: p.output_refs, 人工摘录: p.data_excerpts })}</pre></details>
    <p>${Number(audit.payload_bytes)} / ${Number(audit.byte_limit)} 个 UTF-8 字节 · ${esc(audit.reason)}</p>`,
  );
  for (const button of panel.querySelectorAll("[data-pack-source]")) {
    button.onclick = async () => {
      const pin = sources[Number(button.dataset.packSource)];
      try {
        const original = await ctx.api(
          "/records/" + encodeURIComponent(pin.id) + "?version=" + pin.version,
        );
        const view = modal(
          kind(pin.kind) + " · v" + pin.version,
          `<p>${esc(original.data.name || original.data.SKU || original.id)}</p><pre>${facts(original.data)}</pre><button type="button" class="button secondary" data-back>返回任务证据</button>`,
        );
        view.querySelector("[data-back]").onclick = () =>
          showPack(ctx, run).catch((e) => toast(e.message, "error"));
      } catch (e) {
        toast(e.message, "error");
      }
    };
  }
}

export function chooseContextPack(ctx, fixedRun) {
  const project = ctx.requireProject();
  const runs = fixedRun
    ? [fixedRun]
    : ctx
        .records("Run")
        .filter(
          (r) =>
            r.data.execution_kind === "local_dag" || r.data.task_contract_ref,
        );
  if (!runs.length) {
    modal("固定任务证据", "<p>请先运行本地工作流或保存功能任务合同。</p>");
    return;
  }
  const evidence = [
    ...ctx.records("Product"),
    ...ctx.records("DecisionRecord"),
  ];
  const key = crypto.randomUUID();
  const panel = modal(
    "固定任务证据",
    `<form data-form class="form-stack">
    ${select(
      "任务运行",
      "run",
      runs.map((r) => [
        r.id,
        (r.data.name || r.data.feature_id || "工作流") +
          " · " +
          r.id.slice(-8) +
          " · v" +
          r.version,
      ]),
    )}
    <label class="field"><span>补充商品或决定（可选）</span><select name="evidence" multiple size="4">${evidence.map((r) => `<option value="${esc(r.id)}">${esc(r.data.name || r.data.SKU || r.id)} · v${Number(r.version)}</option>`).join("")}</select></label>
    ${field("证据包大小上限（UTF-8 字节）", "limit", 16384, "number", 'min="512" max="131072" required')}
    ${area("固定依据", "reason", "", "required")}
    <div class="dialog-actions"><button class="button secondary" type="button" data-read-pack>读取已固定版本</button><button class="button primary" type="submit">保存证据包</button></div></form>`,
    async (form) => {
      const values = new FormData(form),
        run = runs.find((r) => r.id === values.get("run"));
      const pins = (items) => items.map(({ id, version }) => ({ id, version }));
      await ctx.api("/execution/context-packs", {
        method: "POST",
        body: {
          project_id: project,
          run: pins([run])[0],
          request_id: key,
          reason: values.get("reason"),
          byte_limit: Number(values.get("limit")),
          evidence: pins(
            evidence.filter((r) => values.getAll("evidence").includes(r.id)),
          ),
        },
      });
      await ctx.reloadData();
      if (ctx.state.projectId === project) await showPack(ctx, run);
    },
  );
  const selector = panel.querySelector('[name="run"]'),
    reader = panel.querySelector("[data-read-pack]");
  const update = () => {
    reader.disabled = !runs.find((r) => r.id === selector.value)?.data
      .context_ref;
  };
  selector.onchange = update;
  update();
  reader.onclick = () =>
    showPack(
      ctx,
      runs.find((r) => r.id === selector.value),
    ).catch((e) => toast(e.message, "error"));
}
