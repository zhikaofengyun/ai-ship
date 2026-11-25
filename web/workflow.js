import {
  $,
  $$,
  esc,
  uid,
  arr,
  pretty,
  icon,
  btn,
  iconBtn,
  badge,
  empty,
  pageHead,
  field,
  area,
  select,
  formData,
  toast,
  modal,
  download,
  setBusy,
  parseJSON,
} from "./ui.js";

import { renderControls, mountControls, readControls } from "./controls.js";
import { installInspectorLayout } from "./workspace-layout.js";
import { mountExecutionControls } from "./execution-controls.js";

const drafts = new Map(),
  clone = (v) => JSON.parse(JSON.stringify(v));
const canonical = (value) =>
  JSON.stringify(value, (_key, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(
          Object.keys(v)
            .sort()
            .map((k) => [k, v[k]]),
        )
      : v,
  );
export async function mountWorkflow(container, ctx) {
  let capabilities = ctx.state.executionCapabilities;
  if (!capabilities) {
    try {
      capabilities = await ctx.api("/execution/capabilities");
      ctx.state.executionCapabilities = capabilities;
    } catch {
      capabilities = { handlers: {} };
    }
  }
  let operationUI = null;
  const pid = ctx.state.projectId || "none";
  let w = drafts.get(pid);
  if (!w) {
    const existing = ctx
        .records("Workflow")
        .find((r) => !r.data.feature_operation),
      view = ctx
        .records("View")
        .find(
          (v) =>
            v.data.name === "流程画布" &&
            v.data.operation_scope?.workflow_id === existing?.id,
        );
    w = {
      record: existing || null,
      data: clone(
        existing?.data || {
          name: "未命名工作流",
          nodes: [],
          edges: [],
          max_steps: 100,
          max_attempts: 3,
          budget: 0,
        },
      ),
      view: view || null,
      positions: clone(view?.data.layout?.positions || {}),
      selected: null,
      connecting: null,
      dirty: false,
      zoom: view?.data.layout?.zoom || 1,
      validation: null,
    };
    drafts.set(pid, w);
  }
  w.data.nodes ??= [];
  w.data.edges ??= [];
  function initializePosition(n, i) {
    if (["__proto__", "prototype", "constructor"].includes(n.id))
      throw new Error("节点标识不能使用保留名称");
    const fallback = {
        x: 70 + (i % 4) * 270,
        y: 100 + Math.floor(i / 4) * 185,
      },
      prior = Object.hasOwn(w.positions, n.id) ? w.positions[n.id] : null;
    w.positions[n.id] = {
      x:
        prior && Number.isFinite(Number(prior.x))
          ? Number(prior.x)
          : fallback.x,
      y:
        prior && Number.isFinite(Number(prior.y))
          ? Number(prior.y)
          : fallback.y,
    };
  }
  w.data.nodes.forEach(initializePosition);
  let dragged = false;
  function ancestors() {
    const ids = new Set(),
      queue = [w.selected];
    while (queue.length) {
      const target = queue.shift();
      for (const edge of w.data.edges) {
        if (edge.target === target && !ids.has(edge.source)) {
          ids.add(edge.source);
          queue.push(edge.source);
        }
      }
    }
    return w.data.nodes.filter((n) => n.id !== w.selected && ids.has(n.id));
  }
  const feature = (id) => ctx.state.catalog.features.find((f) => f.id === id);
  const selected = () => w.data.nodes.find((n) => n.id === w.selected);
  const visualNumber = (value) =>
    Number.isFinite(Number(value)) ? Number(value) : 0;
  const position = (id) => ({
    x: visualNumber(w.positions[id]?.x),
    y: visualNumber(w.positions[id]?.y),
  });
  const touch = () => {
    w.dirty = true;
    w.validation = null;
    const el = $("#workflow-status", container);
    if (el) {
      el.textContent = "有未保存修改";
      el.className = "edit-status dirty";
    }
  };
  let releaseLayout = () => {};
  ctx.state.cleanup.push(() => releaseLayout());
  function draw() {
    releaseLayout();
    container.innerHTML = `<div class="page">${pageHead("WORKFLOW STUDIO", "自由编排工作流", "从任意合格入口开始。选择方式、连接数据，再保存可复用的流程。", `${btn("流程", "workflow-list", "folder", "button secondary")}${btn("添加节点", "add-node", "plus", "button primary")}${btn("保存", "save-workflow", "check", "button secondary")}`)}<div class="toolbar"><span id="workflow-status" class="edit-status ${w.dirty ? "dirty" : ""}">${w.dirty ? "有未保存修改" : w.record ? "已保存 · v" + w.record.version : "空白草稿"}</span><span class="toolbar-spacer"></span><button class="button small ghost" type="button" data-pane-toggle aria-controls="workflow-inspector">收起参数</button><button class="button small ghost" type="button" data-pane-move>移至左侧</button>${btn("校验流程", "validate-workflow", "checkcircle", "button secondary")}${btn("批量运行", "batch-flow", "layers", "button secondary")}${btn("发起运行", "run-workflow", "play", "button secondary", w.running ? "disabled" : "")}</div><div class="workflow-layout adjustable-layout"><div class="workflow-canvas" id="workflow-canvas" aria-label="工作流画布"><div class="workflow-world" id="workflow-world" style="transform:scale(${Number(w.zoom) || 1})"><svg class="workflow-edges" id="workflow-edges" aria-hidden="true"></svg><div id="workflow-nodes"></div></div>${!w.data.nodes.length ? `<div class="canvas-hint"><div class="empty-state" style="align-items:flex-start;text-align:left;padding:32px 14px"><div class="empty-symbol">${icon("flow")}</div><h3>空白流程</h3>${btn("添加第一个节点", "add-node", "plus", "button secondary")}</div></div>` : ""}<div class="canvas-controls">${iconBtn("缩小画布", "zoom-out", "search")}<span id="workflow-zoom">${Math.round(w.zoom * 100)}%</span>${iconBtn("放大画布", "zoom-in", "plus")}${iconBtn("回到画布起点", "canvas-home", "home")}${iconBtn("整理布局", "auto-layout", "grid")}</div></div><aside class="inspector workflow-inspector" id="workflow-inspector" data-inspector aria-label="工作流参数"></aside></div><div id="workflow-validation"></div></div>`;
    releaseLayout = installInspectorLayout(
      $(".workflow-layout", container),
      $(".toolbar", container),
      "ship-workflow-inspector-v1",
      "参数",
    );
    drawNodes();
    drawInspector();
    drawValidation();
    bind();
  }
  function drawNodes() {
    const root = $("#workflow-nodes", container);
    root.innerHTML = w.data.nodes
      .map((n) => {
        const f = feature(n.feature_id),
          p = position(n.id);
        return `<article class="workflow-node ${w.selected === n.id ? "selected" : ""}" style="left:${p.x}px;top:${p.y}px" data-node="${esc(n.id)}" tabindex="0" aria-label="${esc(f?.label || n.feature_id)}节点"><div class="node-eyebrow">${icon("flow")}<span>${n.optional ? "可选步骤" : "必要步骤"}</span></div><strong>${esc(f?.label || n.feature_id)}</strong><p>${esc(f?.routes.find((r) => r.id === n.route_id)?.label || "未选择方案")}</p><button class="node-port input" data-port="input" data-id="${esc(n.id)}" aria-label="连接到 ${esc(f?.label)} 的输入"></button><button class="node-port output ${w.connecting === n.id ? "connecting" : ""}" data-port="output" data-id="${esc(n.id)}" aria-label="从 ${esc(f?.label)} 的输出连接"></button></article>`;
      })
      .join("");
    $$("[data-node]", container).forEach((node) => {
      node.onclick = (ev) => {
        if (ev.target.closest("[data-port]") || dragged) {
          dragged = false;
          return;
        }
        try {
          collectNode();
          w.selected = node.dataset.node;
          drawNodes();
          drawInspector();
        } catch (err) {
          toast(err.message, "error");
        }
      };
      node.onkeydown = (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          try {
            collectNode();
            w.selected = node.dataset.node;
            drawNodes();
            drawInspector();
          } catch (err) {
            toast(err.message, "error");
          }
        }
      };
      node.onpointerdown = (ev) => {
        if (ev.target.closest("button") || ev.button !== 0) return;
        const id = node.dataset.node,
          p = w.positions[id];
        p.x = visualNumber(p.x);
        p.y = visualNumber(p.y);
        const start = { x: ev.clientX, y: ev.clientY, px: p.x, py: p.y };
        node.setPointerCapture(ev.pointerId);
        node.onpointermove = (move) => {
          const dx = (move.clientX - start.x) / w.zoom,
            dy = (move.clientY - start.y) / w.zoom;
          if (Math.abs(dx) + Math.abs(dy) > 3) dragged = true;
          p.x = Math.max(18, Math.min(1540, start.px + dx));
          p.y = Math.max(18, Math.min(1040, start.py + dy));
          node.style.left = p.x + "px";
          node.style.top = p.y + "px";
          drawEdges();
        };
        node.onpointerup = () => {
          node.onpointermove = null;
          if (dragged) {
            w.layoutDirty = true;
            try {
              collectNode();
              w.selected = id;
              drawInspector();
            } catch (err) {
              toast(err.message, "error");
            }
          }
        };
      };
    });
    $$("[data-port]", container).forEach(
      (port) =>
        (port.onclick = () => {
          if (port.dataset.port === "output") {
            w.connecting =
              w.connecting === port.dataset.id ? null : port.dataset.id;
            drawNodes();
            if (w.connecting) toast("选择目标节点左侧输入端口");
            return;
          }
          if (!w.connecting) {
            toast("先选择来源节点右侧输出端口。", "error");
            return;
          }
          if (w.connecting === port.dataset.id) {
            toast("不能把节点直接连接到自身。", "error");
            return;
          }
          connectionForm(w.connecting, port.dataset.id);
        }),
    );
    drawEdges();
  }
  function drawEdges() {
    const svg = $("#workflow-edges", container);
    if (!svg) return;
    svg.innerHTML = w.data.edges
      .map((edge) => {
        const a = position(edge.source),
          b = position(edge.target);
        if (!a || !b) return "";
        const x1 = a.x + 206,
          y1 = a.y + 54,
          x2 = b.x,
          y2 = b.y + 54,
          bend = Math.max(65, Math.abs(x2 - x1) * 0.48);
        return `<path class="workflow-edge" d="M${x1} ${y1} C${x1 + bend} ${y1} ${x2 - bend} ${y2} ${x2} ${y2}"/>`;
      })
      .join("");
  }
  function drawInspector() {
    const n = selected(),
      el = $("#workflow-inspector", container);
    operationUI = null;
    if (!n) {
      el.innerHTML = `<div class="inspector-heading"><h3>流程设置</h3>${icon("sliders")}</div><div class="form-stack">${field("流程名称", "workflow-name", w.data.name, "text", 'id="workflow-name" maxlength="200"')}${field("最大执行步骤", "max_steps", w.data.max_steps || 100, "number", 'min="1" max="10000" id="workflow-steps"')}${field("每步最大尝试次数", "max_attempts", w.data.max_attempts || 3, "number", 'min="1" max="10" id="workflow-attempts"')}${field("总费用上限（USD）", "budget", w.data.budget ?? 0, "number", 'min="0" step="0.01" id="workflow-budget"')}</div><div class="divider"></div>${btn("添加功能节点", "add-node", "plus", "button secondary")}`;
      [
        ["workflow-name", "name"],
        ["workflow-steps", "max_steps"],
        ["workflow-attempts", "max_attempts"],
        ["workflow-budget", "budget"],
      ].forEach(
        ([id, key]) =>
          ($("#" + id, container).onchange = (ev) => {
            w.data[key] =
              key === "name" ? ev.target.value : Number(ev.target.value);
            touch();
          }),
      );
      return;
    }
    const f = feature(n.feature_id);
    el.innerHTML = `<div class="inspector-heading"><h3>${esc(f?.label || n.feature_id)}</h3>${iconBtn("删除节点与相关连线", "delete-node", "trash")}</div><div class="form-stack">${select(
      "处理方案",
      "node-route",
      arr(f?.routes).map((r) => [r.id, r.label]),
      n.route_id,
      'id="node-route"',
    )}<label class="switch-row"><span>可选步骤<small>只有下游条件仍满足时才能跳过。</small></span><span class="switch"><input type="checkbox" id="node-optional" ${n.optional ? "checked" : ""}><i></i></span></label></div><div class="divider"></div><div class="surface-title"><h3>节点参数</h3>${btn("完整控制", "feature-controls", "sliders", "button small secondary")}</div><div id="node-parameters"></div><div class="divider"></div><div class="surface-title"><h3>实际执行与输入</h3></div><div id="node-execution"></div><details class="source-details"><summary>高级参数与输入绑定</summary>${area("参数 JSON", "node-params", pretty(n.parameters || {}), 'class="code-input" id="node-params"')}${area("确定性执行器参数 operation_args（仅受支持节点生效）", "node-operation", pretty(n.operation_args || {}), 'class="code-input" id="node-operation"')}${area("输入绑定 JSON", "node-inputs", pretty(n.input_bindings || {}), 'class="code-input" id="node-inputs"')}${btn("应用高级参数", "apply-node-json", "check", "button small secondary")}</details><div class="divider"></div><div class="surface-title"><h3>数据连线</h3>${btn("添加", "add-edge", "plus", "button small secondary")}</div><div class="connection-list">${
      w.data.edges
        .filter((e) => e.source === n.id || e.target === n.id)
        .map((edge) => {
          const other = w.data.nodes.find(
            (x) => x.id === (edge.source === n.id ? edge.target : edge.source),
          );
          return `<div class="connection-item">${icon(edge.source === n.id ? "arrow" : "link")}<span>${esc(feature(other?.feature_id)?.label || other?.id)}<br><small>${esc(edge.source_port)} → ${esc(edge.target_port)}</small></span>${iconBtn("删除连接", "delete-edge", "close", `data-id="${esc(edge.id)}"`)}</div>`;
        })
        .join("") || '<p class="help-text">尚未连接数据端口。</p>'
    }</div><details class="source-details"><summary>验收与失败边界</summary><ul>${arr(
      f?.checks,
    )
      .map((c) => `<li>${esc(c)}</li>`)
      .join("")}</ul><p>${esc(f?.failure || "")}</p></details>`;
    const controls = arr(f?.operation?.controls).filter(
      (c) => c.key !== "route_choice",
    );
    $("#node-parameters", container).innerHTML = renderControls(
      controls,
      n.parameters || {},
    );
    mountControls($("#node-parameters"), controls, n.parameters || {}, {
      records: ctx.state.records,
    });
    operationUI = mountExecutionControls(
      $("#node-execution"),
      capabilities.handlers[n.route_id],
      n,
      {
        records: ctx.state.records,
        upstream: ancestors(),
        capabilities: capabilities.handlers,
        feature,
        onchange: touch,
      },
    );
    $("#node-parameters").addEventListener("controlchange", touch);
    $("#node-parameters").addEventListener("change", () => {
      try {
        n.parameters = readControls($("#node-parameters"));
        touch();
      } catch (err) {
        toast(err.message, "error");
      }
    });
    $("#node-route", container).onchange = (ev) => {
      try {
        collectNode();
        n.route_id = ev.target.value;
        touch();
        drawNodes();
        drawInspector();
      } catch (err) {
        toast(err.message, "error");
        ev.target.value = n.route_id;
      }
    };
    $("#node-optional", container).onchange = (ev) => {
      n.optional = ev.target.checked;
      touch();
      drawNodes();
    };
  }
  function addPicker() {
    ctx.requireProject();
    collectNode();
    const d = modal(
      "添加功能节点",
      `<div class="search-field" style="max-width:none">${icon("search")}<input id="node-search" placeholder="搜索功能" aria-label="搜索节点功能"></div><div class="search-results" id="node-results"></div>`,
    );
    const render = () => {
      const q = $("#node-search").value.toLowerCase();
      $("#node-results").innerHTML = ctx.state.catalog.features
        .filter((f) =>
          [f.id, f.label, f.goal].join(" ").toLowerCase().includes(q),
        )
        .slice(0, 60)
        .map(
          (f) =>
            `<button class="search-result" data-add="${f.id}">${icon("flow")}<span><strong>${esc(f.label)}</strong><small>${esc(ctx.state.catalog.modules.find((m) => m.id === f.module)?.label || "")}</small></span>${icon("plus")}</button>`,
        )
        .join("");
      d.querySelectorAll("[data-add]").forEach(
        (b) =>
          (b.onclick = () => {
            const f = feature(b.dataset.add),
              n = {
                id: uid(),
                feature_id: f.id,
                route_id: f.routes[0]?.id,
                parameters: clone(ctx.configFor(f).parameters || {}),
                input_bindings: {},
                optional: false,
              };
            w.data.nodes.push(n);
            const i = w.data.nodes.length - 1;
            w.positions[n.id] = {
              x: 70 + (i % 4) * 270,
              y: 100 + Math.floor(i / 4) * 185,
            };
            w.selected = n.id;
            touch();
            d.close();
            draw();
          }),
      );
    };
    $("#node-search").oninput = render;
    render();
  }
  function connectionForm(sourceId, targetId) {
    const source = w.data.nodes.find((n) => n.id === sourceId),
      target = w.data.nodes.find((n) => n.id === targetId),
      sf = feature(source.feature_id),
      tf = feature(target.feature_id),
      outputs = arr(sf.outputs),
      inputs = arr(tf.inputs);
    modal(
      "连接数据端口",
      `<p class="dialog-desc">${esc(sf.label)} → ${esc(tf.label)}。只有匹配的输入/输出类型与真实必需条件通过后，才能运行。</p><form data-form class="form-stack">${select("来源输出", "source_port", outputs.length ? outputs : [["", "没有声明输出"]])}${select("目标输入", "target_port", inputs.length ? inputs : [["", "没有声明输入"]])}<div class="form-actions"><button type="submit" class="button primary">添加连接</button></div></form>`,
      async (f) => {
        const raw = formData(f);
        if (!raw.source_port || !raw.target_port)
          throw new Error("两个节点必须具有明确的数据端口。");
        if (raw.source_port !== raw.target_port)
          throw new Error(
            "端口类型不匹配。请添加合法适配节点或改用合适的输入。",
          );
        if (
          w.data.edges.some(
            (e) =>
              e.source === sourceId &&
              e.target === targetId &&
              e.source_port === raw.source_port &&
              e.target_port === raw.target_port,
          )
        )
          throw new Error("相同的数据连接已经存在。");
        w.data.edges.push({
          id: uid(),
          source: sourceId,
          target: targetId,
          ...raw,
        });
        w.connecting = null;
        touch();
        draw();
      },
    );
  }
  function addEdgeForm() {
    const n = selected();
    if (!n || w.data.nodes.length < 2)
      throw new Error("至少需要两个节点才能建立连接。");
    modal(
      "选择连接目标",
      `<form data-form class="form-stack">${select(
        "来源",
        "source",
        w.data.nodes.map((x) => [
          x.id,
          `${x.feature_id} · ${feature(x.feature_id)?.label}`,
        ]),
        n.id,
      )}${select(
        "目标",
        "target",
        w.data.nodes
          .filter((x) => x.id !== n.id)
          .map((x) => [
            x.id,
            `${x.feature_id} · ${feature(x.feature_id)?.label}`,
          ]),
      )}<div class="form-actions"><button type="submit" class="button primary">选择端口</button></div></form>`,
      async (f) => {
        const raw = formData(f);
        if (raw.source === raw.target) throw new Error("不能连接自身。");
        setTimeout(() => connectionForm(raw.source, raw.target), 20);
      },
    );
  }
  function collectNode() {
    const n = selected(),
      el = $("#node-parameters", container);
    if (n && el) {
      const parameters = readControls(el),
        execution = operationUI?.collect() || {};
      if (
        canonical(parameters) !== canonical(n.parameters || {}) ||
        Object.entries(execution).some(
          ([k, v]) => canonical(v) !== canonical(n[k] || {}),
        )
      ) {
        n.parameters = parameters;
        Object.assign(n, execution);
        touch();
      }
    }
  }
  async function validate() {
    collectNode();
    const payload = {
      nodes: w.data.nodes,
      edges: w.data.edges,
      max_steps: w.data.max_steps,
      max_attempts: w.data.max_attempts,
      budget: w.data.budget,
    };
    w.validation = await ctx.api("/workflows/validate", {
      method: "POST",
      body: payload,
    });
    drawValidation();
    return w.validation;
  }
  function drawValidation() {
    const el = $("#workflow-validation", container);
    if (!el) return;
    if (!w.validation) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = `<section class="surface" style="margin-top:20px"><div class="surface-title"><h2>流程校验</h2>${badge(w.validation.valid ? "结构校验通过" : "需要修正", w.validation.valid ? "green" : "amber")}</div>${
      w.validation.valid
        ? '<p class="help-text">结构校验通过仅说明当前流程定义满足这些检查。运行时还会核对输入、权限、预算和能力。</p>'
        : `<ul class="check-list">${arr(w.validation.errors)
            .map(
              (err) =>
                `<li>${icon("warning")}<span>${esc(typeof err === "string" ? err : err.message || JSON.stringify(err))}</span></li>`,
            )
            .join("")}</ul>`
    }<details class="source-details"><summary>校验原始结果</summary><pre>${esc(pretty(w.validation))}</pre></details></section>`;
  }
  async function save() {
    ctx.requireProject();
    collectNode();
    if (!w.record || w.dirty) {
      const inputRefs = [
        ...new Map(
          w.data.nodes
            .flatMap((n) => Object.values(n.input_bindings || {}))
            .filter((b) => b.record_id && b.version)
            .map((b) => {
              const r = ctx.state.records.find((r) => r.id === b.record_id);
              return [
                `${b.record_id}:${b.version}`,
                {
                  id: b.record_id,
                  version: b.version,
                  ...(r ? { kind: r.kind } : {}),
                },
              ];
            }),
        ).values(),
      ];
      w.record = await ctx.saveRecord(
        "Workflow",
        w.data,
        w.record,
        inputRefs,
        "保存经用户调整的工作流草稿",
      );
    }
    if (!w.view || w.layoutDirty || w.dirty)
      w.view = await ctx.saveRecord(
        "View",
        {
          name: "流程画布",
          operation_scope: { workflow_id: w.record.id },
          layout: { positions: w.positions, zoom: w.zoom },
        },
        w.view,
        [ctx.ref(w.record)],
        "保存独立画布布局，不改变执行数据",
      );
    w.data = clone(w.record.data);
    w.dirty = false;
    w.layoutDirty = false;
    toast("流程与独立画布布局已保存");
    draw();
    return w.record;
  }
  function listFlows() {
    const d = modal(
      "工作流",
      `${
        ctx
          .records("Workflow")
          .filter((r) => !r.data.feature_operation)
          .map(
            (r) =>
              `<button class="list-row" data-workflow="${r.id}">${icon("flow")}<span class="list-row-body"><strong>${esc(r.data.name || "工作流")}</strong><p>${arr(r.data.nodes).length} 个节点 · ${arr(r.data.edges).length} 条连接</p></span><small>v${r.version}</small></button>`,
          )
          .join("") || empty("还没有保存的流程", "", "", "", "flow")
      }<div class="dialog-actions">${btn("导入 JSON", "import-flow", "upload", "button secondary")}${btn("导出 JSON", "export-flow", "download", "button secondary")}${btn("空白流程", "new-flow", "plus", "button primary")}</div><input type="file" id="flow-file" accept=".json,application/json" hidden>`,
    );
    d.querySelectorAll("[data-workflow]").forEach(
      (b) =>
        (b.onclick = () => {
          if (w.dirty) {
            toast("请先保存当前流程，避免丢失修改。", "error");
            return;
          }
          w.record = ctx
            .records("Workflow")
            .find((r) => r.id === b.dataset.workflow);
          w.data = clone(w.record.data);
          w.view = ctx
            .records("View")
            .find(
              (v) =>
                v.data.name === "流程画布" &&
                v.data.operation_scope?.workflow_id === w.record.id,
            );
          w.positions = clone(w.view?.data.layout?.positions || {});
          w.zoom = w.view?.data.layout?.zoom || 1;
          w.data.nodes.forEach(initializePosition);
          w.selected = null;
          w.validation = null;
          d.close();
          draw();
        }),
    );
    $("[data-action=export-flow]", d).onclick = () =>
      download(`${w.data.name || "workflow"}.json`, {
        schema_version: 1,
        workflow: w.data,
        view: { positions: w.positions, zoom: w.zoom },
      });
    $("[data-action=new-flow]", d).onclick = () => {
      if (w.dirty) {
        toast("请先保存当前流程。", "error");
        return;
      }
      w.data = {
        name: "未命名工作流",
        nodes: [],
        edges: [],
        max_steps: 100,
        max_attempts: 3,
        budget: 0,
      };
      w.record = null;
      w.view = null;
      w.positions = {};
      w.selected = null;
      w.validation = null;
      d.close();
      draw();
    };
    $("[data-action=import-flow]", d).onclick = () => $("#flow-file").click();
    $("#flow-file").onchange = async (ev) => {
      try {
        if (w.dirty) throw new Error("请先保存当前流程。");
        const raw = parseJSON(await ev.target.files[0].text()),
          data = raw.workflow || raw;
        if (!Array.isArray(data.nodes) || !Array.isArray(data.edges))
          throw new Error("工作流必须包含 nodes 与 edges");
        if (data.nodes.some((n) => !feature(n.feature_id)))
          throw new Error("导入包含原始架构中不存在的功能节点。");
        if (
          data.nodes.some((n) =>
            ["__proto__", "prototype", "constructor"].includes(n.id),
          )
        )
          throw new Error("节点标识不能使用保留名称");
        w.data = data;
        w.record = null;
        w.view = null;
        w.positions = raw.view?.positions || {};
        data.nodes.forEach(initializePosition);
        w.dirty = true;
        w.selected = null;
        w.validation = null;
        d.close();
        draw();
      } catch (err) {
        toast(err.message, "error");
      }
    };
  }
  function bind() {
    ctx.bind({
      "add-node": addPicker,
      "save-workflow": async (b) => {
        setBusy(b, true, "保存中");
        try {
          await save();
        } finally {
          setBusy(b, false);
        }
      },
      "workflow-list": listFlows,
      "batch-flow": async (b) => {
        setBusy(b, true, "保存流程");
        try {
          const record = await save();
          ctx.nav("production/batches/" + encodeURIComponent(record.id));
        } finally {
          setBusy(b, false);
        }
      },
      "validate-workflow": async (b) => {
        setBusy(b, true, "校验中");
        try {
          const r = await validate();
          toast(
            r.valid ? "流程结构检查通过" : "发现待修正的流程条件",
            r.valid ? "success" : "error",
          );
        } finally {
          setBusy(b, false);
        }
      },
      "run-workflow": async (b) => {
        if (w.running) return;
        w.running = true;
        setBusy(b, true, "检查准入");
        try {
          const result = await validate();
          if (!result.valid) throw new Error("请先修正流程校验问题。");
          const record = await save();
          const versionKey = record.id + ":" + record.version;
          if (w.pendingRunVersion && w.pendingRunVersion !== versionKey)
            throw new Error(
              "上次运行请求的结果尚待核对，请先在来源与审计中检查原运行。",
            );
          w.pendingRunVersion = versionKey;
          w.pendingRunKey ??= uid();
          const run = await ctx.api("/execution/start", {
            method: "POST",
            body: {
              workflow_id: record.id,
              expected_revision: record.version,
              idempotency_key: w.pendingRunKey,
            },
          });
          w.pendingRunVersion = null;
          w.pendingRunKey = null;
          modal(
            "工作流运行请求",
            `${ctx.runSummary(run)}<details class="source-details"><summary>技术详情</summary><pre>${esc(pretty(run))}</pre></details>`,
          );
          await ctx.reloadData();
        } finally {
          w.running = false;
          setBusy(b, false);
          const current = $("[data-action=run-workflow]", container);
          if (current) current.disabled = false;
        }
      },
      "delete-node": () => {
        const n = selected();
        if (!n) return;
        w.data.nodes = w.data.nodes.filter((x) => x.id !== n.id);
        w.data.edges = w.data.edges.filter(
          (x) => x.source !== n.id && x.target !== n.id,
        );
        delete w.positions[n.id];
        w.selected = null;
        touch();
        draw();
      },
      "delete-edge": (b) => {
        collectNode();
        w.data.edges = w.data.edges.filter((e) => e.id !== b.dataset.id);
        touch();
        drawEdges();
        drawInspector();
      },
      "add-edge": addEdgeForm,
      "feature-controls": () => {
        collectNode();
        const n = selected();
        if (!n) return;
        toast(
          "完整功能配置保存在项目工作区；返回画布后可通过高级参数应用到节点。",
        );
        ctx.openFeature(n.feature_id);
      },
      "apply-node-json": () => {
        const n = selected();
        n.parameters = parseJSON($("#node-params").value, "节点参数");
        n.input_bindings = parseJSON($("#node-inputs").value, "输入绑定");
        n.operation_args = parseJSON($("#node-operation").value, "执行器参数");
        touch();
        drawInspector();
        toast("节点参数已应用到草稿");
      },
      "zoom-in": () => zoom(Math.min(1.5, w.zoom + 0.1)),
      "zoom-out": () => zoom(Math.max(0.4, w.zoom - 0.1)),
      "canvas-home": () => {
        $("#workflow-canvas").scrollTo({ left: 0, top: 0, behavior: "smooth" });
      },
      "auto-layout": () => {
        w.data.nodes.forEach(
          (n, i) =>
            (w.positions[n.id] = {
              x: 70 + (i % 4) * 270,
              y: 100 + Math.floor(i / 4) * 185,
            }),
        );
        w.layoutDirty = true;
        drawNodes();
      },
    });
    const world = $("#workflow-world", container);
    world.onclick = (ev) => {
      if (ev.target === world || ev.target.id === "workflow-nodes") {
        try {
          collectNode();
          w.selected = null;
          drawNodes();
          drawInspector();
        } catch (err) {
          toast(err.message, "error");
        }
      }
    };
  }
  function zoom(n) {
    w.zoom = Math.round(n * 100) / 100;
    $("#workflow-world").style.transform = `scale(${w.zoom})`;
    $("#workflow-zoom").textContent = Math.round(w.zoom * 100) + "%";
    w.layoutDirty = true;
  }
  draw();
}
