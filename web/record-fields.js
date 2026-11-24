import { $, $$, esc, pretty, area, parseJSON } from "./ui.js";

const captions = {
  name: "名称",
  title: "标题",
  description: "说明",
  text: "正文",
  status: "状态",
  provider: "服务商",
  model_id: "模型",
  language: "语言",
  market: "市场",
  region: "地区",
  SKU: "SKU",
  price: "价格",
  quantity: "数量",
  currency: "币种",
  evidence: "证据",
  goals: "目标",
  canonical_url: "来源网址",
  origin_kind: "来源类型",
  provenance: "来源依据",
  expires_at: "有效期",
  observed_at: "观察时间",
  platform: "平台",
  url: "链接",
  identity: "身份",
  prompt: "提示词",
  duration: "时长（秒）",
  width: "宽度",
  height: "高度",
  budget: "预算",
  hypothesis: "假设",
  label: "标签",
  role: "角色",
  goal: "目标",
  version: "版本",
  filename: "文件名",
  label_type: "标注类型",
  reason: "依据",
  source: "来源",
};
const meta = /(?:^id$|(?:^|_)(?:id|ids|ref|refs|hash|hashes|fingerprint)$)/;
const technicalValue =
  /^(?:[a-f\d]{32,}|(?:[a-z]+_)?[a-f\d]{8}-[a-f\d-]{27,}|[a-z]+_[a-f\d]{24,})$/i;
function widget(key, schema, value, required) {
  const title = captions[key] || schema.title || key;
  const attrs = `data-record-key="${esc(key)}" ${required ? "required" : ""}`;
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  const type =
    (typeof value === "object" && value !== null) ||
    !types.some((t) => ["string", "number", "integer", "boolean"].includes(t))
      ? "structured"
      : types.find((t) => t !== "null");
  let element;
  if (schema.enum)
    element = `<select ${attrs}><option value="">未指定</option>${schema.enum.map((x) => `<option value="${esc(x)}" ${value === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>`;
  else if (type === "boolean")
    element = `<select ${attrs} data-type="boolean"><option value="">未指定</option><option value="true" ${value === true ? "selected" : ""}>是</option><option value="false" ${value === false ? "selected" : ""}>否</option></select>`;
  else if (["number", "integer"].includes(type))
    element = `<input ${attrs} data-type="number" type="number" step="${type === "integer" ? 1 : "any"}" value="${esc(value ?? "")}">`;
  else if (type === "structured")
    element = `<textarea rows="3" ${attrs} data-type="json" class="code-input">${esc(value === undefined ? "" : pretty(value))}</textarea>`;
  else if (
    [
      "text",
      "description",
      "goals",
      "evidence",
      "provenance",
      "goal",
      "hypothesis",
    ].includes(key)
  )
    element = `<textarea rows="3" ${attrs}>${esc(value ?? "")}</textarea>`;
  else element = `<input ${attrs} type="text" value="${esc(value ?? "")}">`;
  return `<label class="field"><span>${esc(title)}${required ? " *" : ""}</span>${element}</label>`;
}
export function recordFields(host, schema, initial = {}) {
  const data = structuredClone(initial),
    properties = schema.properties || {},
    required = schema.required || [];
  const basic = [
    ...new Set([
      ...required,
      ...Object.keys(initial).filter(
        (key) =>
          !meta.test(key) &&
          key in captions &&
          !technicalValue.test(String(initial[key])) &&
          (initial[key] === null || typeof initial[key] !== "object"),
      ),
    ]),
  ];
  if (!basic.length && properties.name) basic.push("name");
  const fields = [...basic];
  const draw = () => {
    host.innerHTML = `<div class="form-stack record-standard">${fields.map((key) => widget(key, properties[key] || { type: "string" }, data[key], required.includes(key))).join("")}</div>
      <label class="field add-record-field"><span>添加字段</span><select data-extra-key><option value="">选择字段…</option>${Object.keys(
        properties,
      )
        .filter((key) => !fields.includes(key))
        .map(
          (key) =>
            `<option value="${esc(key)}">${esc(captions[key] || properties[key].title || key)}</option>`,
        )
        .join("")}</select></label>
      <details class="source-details advanced-record"><summary>高级数据</summary>${area("完整字段", "advanced_data", pretty(data), 'class="code-input" rows="8"')}</details>`;
    const read = (el) => {
      const raw = el.value;
      if (raw === "") {
        delete data[el.dataset.recordKey];
        return;
      }
      data[el.dataset.recordKey] =
        el.dataset.type === "number"
          ? Number(raw)
          : el.dataset.type === "boolean"
            ? raw === "true"
            : el.dataset.type === "json"
              ? parseJSON(raw, el.dataset.recordKey)
              : raw;
    };
    const writeAdvanced = () => {
      $("[name=advanced_data]", host).value = pretty(data);
    };
    let usingAdvanced = false;
    const changed = () => {
      $$("[data-record-key]", host).forEach(read);
      writeAdvanced();
    };
    $$(".record-standard [data-record-key]", host).forEach((el) =>
      el.addEventListener("input", () => {
        usingAdvanced = false;
        try {
          read(el);
          writeAdvanced();
        } catch {
          /* Incomplete structured field: validate when committed. */
        }
      }),
    );
    $("[name=advanced_data]", host).oninput = () => {
      usingAdvanced = true;
    };
    host.collect = () => {
      if (usingAdvanced) {
        const parsed = parseJSON($("[name=advanced_data]", host).value, "记录");
        if (!parsed || Array.isArray(parsed) || typeof parsed !== "object")
          throw new Error("记录需要一组字段");
        return parsed;
      }
      changed();
      return structuredClone(data);
    };
    $("[data-extra-key]", host).onchange = (ev) => {
      if (!ev.target.value) return;
      const current = host.collect();
      Object.keys(data).forEach((k) => delete data[k]);
      Object.assign(data, current);
      fields.push(ev.target.value);
      draw();
      $(
        ".record-standard label:last-child input,.record-standard label:last-child textarea,.record-standard label:last-child select",
        host,
      )?.focus();
    };
  };
  draw();
  return () => host.collect();
}
export function displayRecordFacts(data) {
  return Object.entries(data)
    .filter(
      ([k, v]) =>
        k in captions &&
        !meta.test(k) &&
        typeof v !== "object" &&
        v != null &&
        v !== "" &&
        !technicalValue.test(String(v)),
    )
    .slice(0, 12)
    .map(
      ([k, v]) => `<div><dt>${esc(captions[k])}</dt><dd>${esc(v)}</dd></div>`,
    )
    .join("");
}
