import { $, esc, modal, select } from "./ui.js";

export async function chooseBatchWorkflow(
  ctx,
  pid,
  output,
  current,
  requested,
  apply,
  active,
) {
  const [caps, result] = await Promise.all([
    ctx.api("/execution/capabilities"),
    ctx.api(
      "/records?" +
        new URLSearchParams({ project_id: pid, kind: "Workflow", limit: 2000 }),
    ),
  ]);
  if (!active()) return;
  const wanted = output === "render" ? "Artifact" : "Timeline";
  const workflows = (result.items || result).filter(
    (w) => !w.data.feature_operation,
  );
  const ports = (w) =>
    ((w?.data.DAG || w?.data)?.nodes || []).flatMap((node) => {
      const handler = caps.handlers?.[node.route_id];
      return Object.entries(handler?.input_bindings || {})
        .filter(
          ([path, spec]) =>
            spec.kinds.includes(wanted) &&
            !node.input_bindings?.[path]?.node_id,
        )
        .map(([path]) => ({
          node_id: node.id,
          input_path: path,
          label:
            (ctx.state.catalog.features.find((f) => f.id === node.feature_id)
              ?.label || node.feature_id) +
            " · " +
            node.id +
            " / " +
            path,
        }));
    });
  const options = workflows.map((w) => [
    w.id,
    (w.data.name || w.id) + " · v" + w.version,
  ]);
  const dlg = modal(
    "串接已保存的工作流",
    `<form data-form class="form-stack">
    ${select("工作流", "workflow", [["", "选择已保存的流程"], ...options], requested || current?.id, "required")}
    <div data-port></div><p>每条作品将分别把${wanted === "Timeline" ? "时间线" : "成片"}送入所选输入，再运行这份流程。</p>
    <button type="submit" class="button primary">应用到本批</button></form>`,
    async (f) => {
      const w = workflows.find((w) => w.id === f.elements.workflow.value),
        port = ports(w)[Number(f.elements.port.value)];
      if (!w || !port) throw new Error("请先选择可接收批次输出的节点输入。");
      if (active())
        apply({
          id: w.id,
          version: w.version,
          node_id: port.node_id,
          input_path: port.input_path,
          name: w.data.name || w.id,
          label: port.label,
        });
    },
  );
  const workflow = $("[name=workflow]", dlg);
  function show() {
    const list = ports(workflows.find((w) => w.id === workflow.value));
    const i = list.findIndex(
      (p) =>
        current?.node_id === p.node_id &&
        current?.input_path === p.input_path &&
        current.id === workflow.value,
    );
    $("[data-port]", dlg).innerHTML = list.length
      ? select(
          "连接到节点输入",
          "port",
          list.map((v, i) => [i, v.label]),
          Math.max(0, i),
          "required",
        )
      : "<p>当前流程没有可接收此输出的独立输入。请在流程画布中配置对应节点并保存。</p>";
    $("form[data-form] [type=submit]", dlg).disabled = !list.length;
  }
  workflow.onchange = show;
  show();
}
