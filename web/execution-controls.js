import { $, $$, esc, pretty, icon, modal, toast, parseJSON } from "./ui.js";

// These inputs describe the advertised deterministic handler. They are separate
// from the original Atlas controls, which remain part of the task contract.
export function mountExecutionControls(container, capability, node, ctx) {
  if (!capability) {
    container.innerHTML = `<div class="info-banner amber">${icon("info")}<span>此方案可配置，暂未接通执行。</span></div>`;
    return {
      collect: () => ({
        operation_args: node.operation_args || {},
        input_bindings: node.input_bindings || {},
      }),
    };
  }
  const values = structuredClone(node.operation_args || {}),
    bindings = structuredClone(node.input_bindings || {}),
    fields = [],
    bindingFields = [],
    readers = [];
  const readPath = (path) => path.split(".").reduce((v, k) => v?.[k], values);
  const writePath = (path, value) => {
    const keys = path.split(".");
    if (keys.some((k) => ["__proto__", "constructor", "prototype"].includes(k)))
      throw new Error("参数路径无效");
    let target = values;
    for (const k of keys.slice(0, -1)) {
      target[k] ??= {};
      target = target[k];
    }
    if (value === undefined) delete target[keys.at(-1)];
    else target[keys.at(-1)] = value;
    ctx.onchange?.();
  };
  const recordName = (r) =>
    r.data.name || r.data.title || r.data.filename || r.kind;
  const labels = {
    asset_id: "素材输入",
    timeline_id: "时间线输入",
    artifact_id: "成片输入",
    data: "时间线数据",
    recipe: "实际处理配方",
    profile: "输出规格",
    name: "名称",
    text: "人工脚本",
    language: "语言",
    claims: "事实主张",
    product_bindings: "商品关联",
    origin: "配方来源",
    template: "配方模板",
    skip_reasons: "跳过项目与原因",
  };
  const requiredLabel = (name, required) =>
    `${esc(name)}${required ? '<small class="required-note">执行必需</small>' : ""}`;
  function bindingHTML(path, spec, required) {
    const binding = bindings[path],
      records = ctx.records.filter((r) => spec.kinds.includes(r.kind));
    const choices = records.map((r) => ({
      key: `r:${r.id}`,
      label: `${recordName(r)} · ${r.kind} v${r.version}`,
      binding: { record_id: r.id, version: r.version, value: spec.value },
      record: r,
    }));
    for (const n of ctx.upstream) {
      const c = ctx.capabilities[n.route_id];
      for (const [output, kind] of Object.entries(c?.outputs || {})) {
        if (spec.kinds.includes(kind))
          choices.push({
            key: `n:${n.id}:${output}`,
            label: `上游 ${ctx.feature(n.feature_id)?.label || n.feature_id} · ${output}`,
            binding: { node_id: n.id, output, value: spec.value },
          });
      }
    }
    const same = (choice) =>
      choice.binding.record_id
        ? binding?.record_id === choice.binding.record_id &&
          binding?.version === choice.binding.version
        : binding?.node_id === choice.binding.node_id &&
          binding?.output === choice.binding.output;
    let selected = choices.find(same)?.key || "";
    if (binding && !selected) {
      selected = "pinned";
      choices.unshift({
        key: "pinned",
        label: `已固定版本 · ${binding.version ? "v" + binding.version : binding.output}（需核对当前状态）`,
        binding,
      });
    }
    const index = bindingFields.push({ path, spec, choices }) - 1;
    return `<section class="execution-binding"><label class="field"><span>${requiredLabel(labels[path] || path, required)}</span><select data-execution-binding="${index}"><option value="">暂不绑定</option>${choices.map((c) => `<option value="${esc(c.key)}" ${selected === c.key ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select></label>${path === "timeline_id" && capability.handler === "lock_timeline" ? '<button type="button" data-review-lock class="button small secondary">查看并确认所选时间线版本</button>' : ""}</section>`;
  }
  function fieldHTML(schema, path, required = false) {
    const bind = capability.input_bindings?.[path];
    if (bind) return bindingHTML(path, bind, required);
    const label =
        schema.title ||
        labels[path.split(".").at(-1)] ||
        path.split(".").at(-1),
      value = readPath(path);
    if (schema.type === "object" && schema.properties) {
      const children = Object.entries(schema.properties).filter(
        ([key]) => !["project_id", "expected_revision"].includes(key),
      );
      return `<fieldset class="execution-group"><legend>${requiredLabel(label, required)}</legend><div class="form-stack">${children.map(([key, child]) => fieldHTML(child, `${path}.${key}`, (schema.required || []).includes(key))).join("")}</div></fieldset>`;
    }
    const index = fields.push({ path, schema, label, value }) - 1,
      attr = `data-execution-field="${index}"`;
    if (path === "confirmed_hash")
      return `<input type="hidden" ${attr} value="${esc(value || "")}">`;
    if (schema.enum)
      return `<label class="field"><span>${requiredLabel(label, required)}</span><select ${attr}><option value="">未指定</option>${schema.enum.map((v, i) => `<option value="${i}" ${value === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></label>`;
    if (schema.type === "boolean")
      return `<label class="field"><span>${requiredLabel(label, required)}</span><select ${attr}><option value="">未指定</option><option value="true" ${value === true ? "selected" : ""}>是</option><option value="false" ${value === false ? "selected" : ""}>否</option></select></label>`;
    if (
      schema.type === "number" ||
      schema.type === "integer" ||
      (schema.anyOf?.some((v) => v.type === "number") &&
        typeof value !== "object")
    ) {
      const numeric = schema.anyOf?.find((v) => v.type === "number") || schema;
      return `<label class="field"><span>${requiredLabel(label, required)}</span><input ${attr} data-value-type="number" type="number" value="${esc(value ?? "")}" step="${schema.multipleOf || numeric.type === "integer" ? schema.multipleOf || 1 : "any"}" ${numeric.minimum != null ? `min="${numeric.minimum}"` : ""} ${numeric.maximum != null ? `max="${numeric.maximum}"` : ""} placeholder="未指定"></label>`;
    }
    if (schema.type === "string") {
      return path.endsWith(".text")
        ? `<label class="field"><span>${requiredLabel(label, required)}</span><textarea ${attr} rows="5" placeholder="填写已经确认的脚本内容">${esc(value ?? "")}</textarea></label>`
        : `<label class="field"><span>${requiredLabel(label, required)}</span><input ${attr} type="text" value="${esc(value ?? "")}" placeholder="未指定" ${schema.pattern ? `pattern="${esc(schema.pattern)}"` : ""}></label>`;
    }
    if (ctx.inline)
      return `<label class="field"><span>${requiredLabel(label, required)}</span><textarea data-inline-structure="${index}" rows="4" class="code-input">${esc(value === undefined ? "" : pretty(value))}</textarea></label>`;
    return `<section class="advanced-control"><span><span class="control-label">${requiredLabel(label, required)}</span><small class="control-help" data-json-summary="${index}">${value === undefined ? "未指定" : `已保存 ${Array.isArray(value) ? value.length : Object.keys(value || {}).length} 项`}</small></span><button type="button" class="button small secondary" data-execution-json="${index}">编辑结构化数据</button></section>`;
  }
  const schema = capability.operation_schema,
    props = Object.entries(schema.properties || {}).filter(
      ([key]) => !["project_id", "expected_revision"].includes(key),
    );
  container.innerHTML = `<div class="form-stack">${props.map(([key, s]) => fieldHTML(s, key, (schema.required || []).includes(key))).join("")}</div>`;
  $$("[data-execution-field]", container).forEach((input) => {
    const f = fields[+input.dataset.executionField];
    input.addEventListener("input", () => {
      const raw = input.value;
      let value = raw === "" ? undefined : raw;
      if (value !== undefined) {
        if (f.schema.enum) value = f.schema.enum[+raw];
        else if (f.schema.type === "boolean") value = raw === "true";
        else if (input.dataset.valueType === "number") value = +raw;
      }
      writePath(f.path, value);
    });
    readers.push(() => {
      if (input.value !== "" && !input.checkValidity())
        throw new Error(`${f.label} 不符合执行器的明确范围或格式`);
    });
  });
  $$("[data-inline-structure]", container).forEach((input) => {
    const f = fields[Number(input.dataset.inlineStructure)];
    readers.push(() => {
      const raw = input.value.trim();
      writePath(f.path, raw ? parseJSON(raw, f.label) : undefined);
    });
  });
  $$("[data-execution-binding]", container).forEach((select) => {
    const field = bindingFields[+select.dataset.executionBinding];
    select.onchange = () => {
      const chosen = field.choices.find((x) => x.key === select.value);
      if (chosen) {
        bindings[field.path] = structuredClone(chosen.binding);
        writePath(field.path, undefined);
      } else delete bindings[field.path];
      if (
        field.path === "timeline_id" &&
        capability.handler === "lock_timeline"
      ) {
        writePath("confirmed_hash", undefined);
        const index = fields.findIndex((f) => f.path === "confirmed_hash");
        const hashInput = $(`[data-execution-field="${index}"]`, container);
        if (hashInput) hashInput.value = "";
      }
      ctx.onchange?.();
    };
  });
  $$("[data-execution-json]", container).forEach(
    (button) =>
      (button.onclick = () => {
        const f = fields[+button.dataset.executionJson],
          original = readPath(f.path);
        modal(
          f.label,
          `<p class="dialog-desc">此字段使用执行器公开的数据结构。空白表示暂不指定；保存配置不会执行操作。</p><form data-form class="form-stack"><label class="field"><span>结构化内容</span><textarea name="value" rows="12" class="code-input">${esc(original === undefined ? "" : pretty(original))}</textarea></label><details class="source-details"><summary>字段合同</summary><pre>${esc(pretty(f.schema))}</pre></details><div class="form-actions"><button class="button primary" type="submit">应用到节点</button></div></form>`,
          async (form) => {
            const raw = new FormData(form).get("value").trim();
            writePath(f.path, raw ? parseJSON(raw, f.label) : undefined);
            $(
              `[data-json-summary="${button.dataset.executionJson}"]`,
              container,
            ).textContent = raw ? "已配置，保存工作流后生效" : "未指定";
          },
        );
      }),
  );
  $("[data-review-lock]", container)?.addEventListener("click", () => {
    const binding = bindings.timeline_id,
      record = ctx.records.find(
        (r) => r.id === binding?.record_id && r.version === binding.version,
      );
    if (!record) {
      toast(
        "先绑定已保存且版本仍一致的时间线。动态上游必须生成后实际审阅。",
        "error",
      );
      return;
    }
    if (ctx.inline) {
      const bindingBox = $("[data-review-lock]", container).parentElement;
      bindingBox.querySelector("[data-inline-review]")?.remove();
      const review = document.createElement("details");
      review.dataset.inlineReview = "";
      review.open = true;
      review.innerHTML = `<summary>查看 ${esc(recordName(record))} · v${esc(record.version)}</summary><pre>${esc(pretty(record.data))}</pre><button type="button" class="button secondary">确认此版本</button>`;
      review.querySelector("button").onclick = () => {
        if (
          bindings.timeline_id?.record_id !== record.id ||
          bindings.timeline_id?.version !== record.version
        ) {
          review.remove();
          return;
        }
        writePath("confirmed_hash", record.hash);
        review.remove();
      };
      bindingBox.append(review);
      return;
    }
    modal(
      "确认冻结这个时间线版本",
      `<p class="dialog-desc">${esc(recordName(record))} · v${record.version}。锁版冻结编辑数据，内容与素材使用权仍需分别审阅。</p><div class="detail-facts"><div><dt>片段</dt><dd>${(record.data.clips || []).length}</dd></div><div><dt>字幕</dt><dd>${(record.data.captions || []).length}</dd></div></div><details class="source-details"><summary>查看此版本全部时间线内容</summary><pre>${esc(pretty(record.data))}</pre></details><form data-form class="form-stack"><label class="switch-row"><span>我已查看并确认这个精确版本</span><input type="checkbox" name="confirmed" required></label><div class="form-actions"><button type="submit" class="button primary">确认此版本</button></div></form>`,
      async () => {
        writePath("confirmed_hash", record.hash);
        const index = fields.findIndex((f) => f.path === "confirmed_hash");
        $(`[data-execution-field="${index}"]`, container).value = record.hash;
      },
    );
  });
  return {
    collect: ({ validate = true } = {}) => {
      if (validate) readers.forEach((read) => read());
      return {
        operation_args: structuredClone(values),
        input_bindings: structuredClone(bindings),
      };
    },
  };
}
