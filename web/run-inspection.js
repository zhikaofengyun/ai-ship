import { mountExecutionControls } from "./execution-controls.js";
import { chooseContextPack } from "./context-pack.js";
import {
  $,
  $$,
  arr,
  esc,
  icon,
  modal,
  area,
  btn,
  statusLabel,
  time,
  toast,
  date,
  formData,
  select,
  uid,
} from "./ui.js";

export function chooseAudioAnalysis(ctx) {
  const pid = ctx.requireProject();
  const media = ctx.state.records.filter(
    (r) =>
      r.kind === "Audio" || (r.kind === "Asset" && r.data.probe?.has_audio),
  );
  const d = modal(
    "分析声音起音",
    media.length
      ? `<form data-form class="form-stack"><label class="field"><span>音源</span><select name="source" required>${media.map((r) => `<option value="${esc(r.id)}">${esc(r.data.name || r.data.filename || "音轨")}</option>`).join("")}</select></label><div class="form-actions"><button type="submit" class="button primary">分析</button></div></form>`
      : '<p class="subtle-empty">请先导入带声音的视频或音频。</p>',
    async (form) => {
      const id = formData(form).source,
        result = await ctx.api(
          `/media/${encodeURIComponent(id)}/analyze-audio`,
          { method: "POST" },
        );
      await ctx.reloadData();
      if (ctx.state.projectId !== pid) return;
      const data = result.analysis.data,
        points = arr(data.onset_candidates);
      const summary = points.length
        ? `<div class="onset-list">${points.map((point) => `<button class="button small secondary" type="button" data-moment="${Number(point.time)}">${time(point.time)}</button>`).join("")}</div>`
        : '<p class="subtle-empty">没有检测到起音候选</p>';
      const panel = modal(
        "声音起音候选",
        `<audio controls preload="metadata" src="/api/v1/media/${encodeURIComponent(id)}/file"></audio><h3>${points.length} 个候选位置</h3>${summary}`,
      );
      $$("[data-moment]", panel).forEach(
        (b) =>
          (b.onclick = () => {
            $("audio", panel).currentTime = Number(b.dataset.moment);
          }),
      );
    },
  );
  if (!media.length) {
    d.insertAdjacentHTML(
      "beforeend",
      btn("导入音源", "import-source", "plus", "button primary"),
    );
    $("[data-action=import-source]", d).onclick = () => {
      d.close();
      ctx.nav("editor/import");
    };
  }
}

const states = {
  pending: "待处理",
  running: "执行中",
  unknown: "结果待核对",
  blocked: "待补齐条件",
  completed: "已完成",
  failed: "失败",
  paused: "已暂停",
  awaiting_reconciliation: "待核对",
};
export function chooseRecovery(ctx) {
  const pid = ctx.requireProject();
  const runs = ctx
    .records("Run")
    .filter((r) => r.data.execution_kind === "local_dag");
  const getName = (r) =>
    ctx.records("Workflow").find((w) => w.id === r.data.workflow_version?.id)
      ?.data.name || "工作流";
  const d = modal(
    "运行与恢复",
    `<div>${runs.map((r) => `<button type="button" class="list-row" data-inspect-run="${esc(r.id)}">${icon("flow")}<span class="list-row-body"><strong>${esc(getName(r))}</strong><small>${date(r.created_at)}</small></span><span>${esc(states[r.data.status] || statusLabel(r.data.status))}</span>${icon("chevron")}</button>`).join("") || '<p class="subtle-empty">当前项目没有工作流运行记录</p>'}</div>`,
  );
  $$("[data-inspect-run]", d).forEach(
    (button) =>
      (button.onclick = async () => {
        try {
          await inspectRun(ctx, pid, button.dataset.inspectRun, getName);
        } catch (err) {
          toast(err.message, "error");
        }
      }),
  );
}

async function inspectRun(ctx, pid, id, getName) {
  const base = `/execution/${encodeURIComponent(id)}`;
  const result = await ctx.api(base + "/recovery"),
    run = result.run;
  const version = run.data.workflow_version;
  const workflow = await ctx.api(
    `/records/${encodeURIComponent(version.id)}?version=${version.version}`,
  );
  if (ctx.state.projectId !== pid) return;
  const nodes = arr((workflow.data.DAG || workflow.data).nodes);
  const steps = run.data.checkpoint?.nodes || {};
  const name = (n) =>
    ctx.state.catalog.features.find((f) => f.id === n.feature_id)?.label ||
    "流程步骤";
  const rows = nodes
    .map((n) => {
      const node = steps[n.id] || {};
      const editable =
        ["queued", "paused", "blocked"].includes(run.data.status) &&
        !run.data.worker_active &&
        !run.data.outcome_unknown &&
        ["pending", "blocked"].includes(node.status) &&
        node.attempts === 0;
      return `<div class="list-row"><span class="list-row-body"><strong>${esc(name(n))}</strong>${node.error?.message ? `<small>${esc(node.error.message)}</small>` : ""}</span><span>${esc(states[node.status] || statusLabel(node.status))}</span>${editable ? `<button type="button" class="button small secondary" data-revise="${esc(n.id)}">修改节点</button>` : ""}</div>`;
    })
    .join("");
  const unknown = nodes.filter(
    (n) =>
      ["unknown", "running"].includes(steps[n.id]?.status) &&
      run.data.outcome_unknown,
  );
  const resumable =
    !run.data.outcome_unknown &&
    !run.data.worker_active &&
    !["completed", "cancelled", "running"].includes(run.data.status);
  const panel = modal(
    getName(run),
    `<div class="surface-title"><h3>执行步骤</h3><span>${esc(states[run.data.status] || statusLabel(run.data.status))}</span></div>${rows}<div class="dialog-actions">${unknown.map((n) => `<button type="button" class="button secondary" data-check-node="${esc(n.id)}">核对${esc(name(n))}</button>`).join("")}${btn("固定恢复证据", "context-pack", "flow", "button secondary")}${resumable ? btn("从检查点继续", "resume", "play", "button primary") : ""}</div>`,
  );
  $$("[data-revise]", panel).forEach(
    (b) =>
      (b.onclick = async () => {
        try {
          const caps = (ctx.state.executionCapabilities ||= await ctx.api(
            "/execution/capabilities",
          )).handlers;
          const node = nodes.find((n) => n.id === b.dataset.revise);
          const f = ctx.state.catalog.features.find(
            (f) => f.id === node.feature_id,
          );
          const choices = f.routes.filter((r) => caps[r.id]);
          if (!choices.length)
            throw new Error("此功能尚没有可本地执行的替代路线。");
          const edges = (workflow.data.DAG || workflow.data).edges || [];
          const ancestors = new Set(),
            work = [node.id];
          while (work.length) {
            const target = work.pop();
            for (const edge of edges)
              if (edge.target === target && !ancestors.has(edge.source)) {
                ancestors.add(edge.source);
                work.push(edge.source);
              }
          }
          let fields,
            prior = "",
            token = "";
          const d = modal(
            "修改待执行节点",
            `<form data-form class="form-stack">${select(
              "执行路线",
              "route",
              choices.map((r) => [r.id, r.label]),
              node.route_id,
              "required",
            )}
        <div data-inputs></div>${area("修改依据", "reason", "", "required")}<button type="submit" class="button primary">保存检查点改动</button></form>`,
            async (form) => {
              const body = {
                project_id: pid,
                expected_revision: run.version,
                base_spec_hash: run.data.spec_hash,
                node_id: node.id,
                route_id: form.elements.route.value,
                reason: form.elements.reason.value,
                ...fields.collect(),
              };
              const intent = JSON.stringify(body);
              if (intent !== prior) {
                prior = intent;
                token = uid();
              }
              await ctx.api(base + "/revise-node", {
                method: "POST",
                body: { ...body, request_id: token },
              });
              await ctx.reloadData();
              await inspectRun(ctx, pid, id, getName);
            },
          );
          const routes = $("[name=route]", d);
          function setup() {
            const data =
              routes.value === node.route_id
                ? node
                : { operation_args: {}, input_bindings: {} };
            fields = mountExecutionControls(
              $("[data-inputs]", d),
              caps[routes.value],
              data,
              {
                records: ctx.state.records,
                upstream: nodes.filter((n) => ancestors.has(n.id)),
                capabilities: caps,
                feature: (id) =>
                  ctx.state.catalog.features.find((f) => f.id === id),
                inline: true,
              },
            );
          }
          routes.onchange = setup;
          setup();
        } catch (err) {
          toast(err.message, "error");
        }
      }),
  );
  $("[data-action=context-pack]", panel).onclick = () =>
    chooseContextPack(ctx, run);
  $("[data-action=resume]", panel)?.addEventListener("click", async (event) => {
    const b = event.currentTarget;
    b.disabled = true;
    try {
      await ctx.api(base + "/resume", {
        method: "POST",
        body: { expected_revision: run.version },
      });
      await ctx.reloadData();
      await inspectRun(ctx, pid, id, getName);
    } catch (err) {
      toast(err.message, "error");
    } finally {
      b.disabled = false;
    }
  });
  $$("[data-check-node]", panel).forEach(
    (b) =>
      (b.onclick = () => {
        const node = nodes.find((n) => n.id === b.dataset.checkNode);
        const contract =
          ctx.state.executionCapabilities?.handlers[node.route_id]?.outputs ||
          {};
        const candidates = arr(result.candidates).filter(
          (r) => r.data.execution_context?.node_id === node.id,
        );
        const fields = Object.entries(contract)
          .map(([key, kind]) => {
            const list = candidates.filter((r) => r.kind === kind);
            const label =
              ctx.state.catalog.entities.find((e) => e.id === kind)?.label ||
              kind;
            return `<label class="field"><span>${esc(label)}</span><select name="${esc(key)}" required ${key === "shots" ? "multiple" : ""}>${key !== "shots" ? '<option value="">选择证据记录</option>' : ""}${list.map((r) => `<option value="${esc(r.id)}">${esc(r.data.name || r.data.filename || label)} · v${r.version} · ${date(r.created_at)}</option>`).join("")}</select></label>`;
          })
          .join("");
        modal(
          "核对此次执行结果",
          `<form data-form class="form-stack">${fields}${area("核对依据", "reason", "", "required")}<div class="form-actions"><button class="button primary" type="submit">提交核对</button></div></form>`,
          async (form) => {
            const fd = new FormData(form),
              outputs = {};
            for (const key of Object.keys(contract)) {
              const refs = fd
                .getAll(key)
                .map((id) => candidates.find((r) => r.id === id))
                .filter(Boolean)
                .map(ctx.ref);
              if (!refs.length) throw new Error("请选择对应的证据记录");
              outputs[key] = key === "shots" ? refs : refs[0];
            }
            await ctx.api(base + "/reconcile", {
              method: "POST",
              body: {
                expected_revision: run.version,
                node_id: node.id,
                outputs,
                reason: fd.get("reason"),
              },
            });
            await ctx.reloadData();
            await inspectRun(ctx, pid, id, getName);
          },
        );
      }),
  );
}
