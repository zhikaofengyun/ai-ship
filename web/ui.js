export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const uid = () => crypto.randomUUID();
export const arr = (v) => (Array.isArray(v) ? v : []);
export const pretty = (v) => JSON.stringify(v ?? {}, null, 2);
export const textValue = (v) =>
  typeof v === "string" ? v : JSON.stringify(v ?? "");
export const humanBytes = (n) =>
  !Number.isFinite(+n)
    ? "—"
    : +n < 1024
      ? `${n} B`
      : +n < 1048576
        ? `${(+n / 1024).toFixed(1)} KB`
        : `${(+n / 1048576).toFixed(1)} MB`;
export const time = (v) => {
  const n = Math.max(0, Number(v) || 0);
  return `${Math.floor(n / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(n % 60)
    .toString()
    .padStart(2, "0")}.${Math.floor((n % 1) * 10)}`;
};
export const date = (v) =>
  v
    ? new Intl.DateTimeFormat("zh-CN", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(v))
    : "—";
const paths = {
  brand: "M3 7 20 3l1 5-17 4ZM5 13h15v8H5ZM8 6l4 4m2-5 4 4",
  sidebar: "M3 5h18v14H3ZM9 5v14",
  home: "M3 11 12 3 21 11M5 10v10h5v-6h4v6h5V10",
  film: "M4 3h16v18H4zM4 8h16M4 16h16M8 3v18M16 3v18",
  box: "m3 7 9-4 9 4v10l-9 4-9-4ZM3 7l9 4 9-4M12 11v10M8 5l9 4",
  edit: "M3 17v4h4L20 8l-4-4ZM14 6l4 4",
  flow: "M5 5h5v5H5zM14 14h5v5h-5zM14 3h5v5h-5zM7 10v6h7M10 7h2V5h2",
  agents:
    "M9 3h6M12 3v3M5 7h14v12H5zM2 11h3M19 11h3M8 19v2M16 19v2M9 11h.01M15 11h.01M9 15h6",
  send: "m3 3 19 9-19 9 4-9-4-9ZM7 12h15",
  chart: "M4 3v17h17M8 15v-5M13 15V6M18 15v-8",
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  settings:
    "M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  plus: "M12 5v14M5 12h14",
  search: "M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  chevron: "m9 5 7 7-7 7",
  down: "m5 9 7 7 7-7",
  close: "m6 6 12 12M6 18 18 6",
  arrow: "M4 12h16m-6-6 6 6-6 6",
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5",
  download: "M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5",
  play: "m8 4 12 8-12 8Z",
  pause: "M8 4v16M16 4v16",
  check: "m5 12 4 4L19 6",
  clock: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  folder: "M3 6h7l2 2h9v12H3ZM3 6V4h7l2 2h7v2",
  image: "M3 3h18v18H3zM3 16l5-5 4 4 3-3 6 6M15 7h.01",
  music:
    "M9 18V5l11-2v13M9 7l11-2M9 18c0 3-6 4-6 1s6-4 6-1ZM20 16c0 3-6 4-6 1s6-4 6-1Z",
  info: "M12 10v6M12 7h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  warning: "m12 3 10 18H2ZM12 9v5M12 17h.01",
  refresh: "M20 10a8 8 0 1 0-1 8M20 4v6h-6",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M9 10v7M15 10v7",
  lock: "M7 10V7a5 5 0 0 1 10 0v3M5 10h14v11H5zM12 14v3",
  unlock: "M7 10V7a5 5 0 0 1 9-3M5 10h14v11H5zM12 14v3",
  sun: "M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0",
  moon: "M21 14a9 9 0 0 1-11-11 9 9 0 1 0 11 11",
  menu: "M4 6h16M4 12h16M4 18h16",
  undo: "M4 11h10a6 6 0 0 1 0 12M4 11l5-5M4 11l5 5",
  redo: "M20 11H10a6 6 0 0 0 0 12M20 11l-5-5M20 11l-5 5",
  scissors:
    "m8 8 12 12M8 16 20 4M9 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0M9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  layers: "m12 3 10 5-10 5L2 8ZM2 12l10 5 10-5M2 16l10 5 10-5",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  link: "m9 15 6-6M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 8l2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0",
  history: "M3 10a9 9 0 1 1 1 8M3 4v6h6M12 7v5l4 2",
  file: "M5 2h9l5 5v15H5ZM14 2v6h5M9 13h6M9 17h6",
  sliders: "M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6",
  checkcircle: "m8 12 3 3 5-6M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  globe:
    "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18",
  spark: "m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z",
  code: "m8 5-7 7 7 7M16 5l7 7-7 7",
  brief: "M8 5V3h8v2M3 6h18v15H3ZM3 12h18M10 10h4v4h-4z",
  keyframe: "m12 3 9 9-9 9-9-9Z",
  stop: "M5 5h14v14H5z",
  volume: "M3 9h4l5-5v16l-5-5H3ZM16 8a5 5 0 0 1 0 8M19 5a9 9 0 0 1 0 14",
  copy: "M8 8h13v13H8zM16 8V3H3v13h5",
  filter: "M3 4h18l-7 8v7l-4 2v-9Z",
  external: "M14 3h7v7M21 3l-11 11M10 3H3v18h18v-7",
  cloud:
    "M6 18a5 5 0 1 1 0-10 7 7 0 0 1 13-1 5 5 0 0 1-1 11ZM12 11v10m-3-3 3 3 3-3",
};
export const icon = (name, cls = "") =>
  `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.box}"/></svg>`;
export const btn = (label, action, ico = "", cls = "button", attrs = "") =>
  `<button type="button" class="${cls}" data-action="${action}" ${attrs}>${ico ? icon(ico) : ""}<span>${esc(label)}</span></button>`;
export const iconBtn = (label, action, ico, attrs = "") =>
  `<button type="button" class="icon-button" aria-label="${esc(label)}" title="${esc(label)}" data-action="${action}" ${attrs}>${icon(ico)}</button>`;
export const badge = (label, type = "") =>
  `<span class="badge ${type}">${esc(label)}</span>`;
export const statusLabel = (v) =>
  ({
    draft: "草稿",
    ready: "就绪",
    running: "运行中",
    blocked: "待补齐条件",
    needs_input: "需要输入",
    succeeded: "已完成",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    unknown: "待核实",
    unavailable: "未接通",
    unprobed: "未探测",
    healthy: "正常",
    technical_passed: "技检通过",
    content_review_required: "待内容审阅",
    pending: "待处理",
    published: "已核实发布",
  })[v] ||
  v ||
  "草稿";
export function empty(title, desc, action = "", label = "", ico = "box") {
  return `<div class="empty-state"><div class="empty-symbol">${icon(ico)}</div><h3>${esc(title)}</h3><p>${esc(desc)}</p>${action ? btn(label, action, "plus", "button primary") : ""}</div>`;
}
export function pageHead(eyebrow, title, subtitle = "", actions = "") {
  return `<header class="page-heading"><h1>${esc(title)}</h1>${actions ? `<div class="heading-actions">${actions}</div>` : ""}</header>`;
}
export function field(label, name, value = "", type = "text", extra = "") {
  return `<label class="field"><span>${esc(label)}</span><input name="${esc(name)}" type="${type}" value="${esc(value)}" ${extra}></label>`;
}
export function area(label, name, value = "", extra = "") {
  return `<label class="field"><span>${esc(label)}</span><textarea name="${esc(name)}" rows="3" ${extra}>${esc(value)}</textarea></label>`;
}
export function select(label, name, options, value = "", extra = "") {
  return `<label class="field"><span>${esc(label)}</span><select name="${esc(name)}" ${extra}>${options
    .map((o) => {
      const [v, l] = Array.isArray(o) ? o : [o, o];
      return `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`;
    })
    .join("")}</select></label>`;
}
export const formData = (f) => Object.fromEntries(new FormData(f));
export function toast(message, type = "success") {
  const host = $("#toasts");
  if (!host) return;
  for (const prior of [...host.children])
    if (prior.dataset.message === message) prior.remove();
  while (host.children.length >= 2) host.firstElementChild.remove();
  const el = document.createElement("div");
  el.dataset.message = message;
  el.className = `toast ${type}`;
  el.innerHTML = `${icon(type === "error" ? "warning" : "checkcircle")}<span>${esc(message)}</span><button type="button" aria-label="关闭通知">${icon("close")}</button>`;
  el.querySelector("button").onclick = () => el.remove();
  host.append(el);
  setTimeout(() => el.remove(), type === "error" ? 6500 : 4200);
}
let previousFocus;
const actionBindings = new WeakMap();
export function modal(title, html, onSubmit = null, wide = false) {
  const d = $("#dialog");
  actionBindings.get(d)?.();
  const modalId = uid();
  d.dataset.modalId = modalId;
  previousFocus = document.activeElement;
  d.className = `dialog ${wide ? "wide" : ""}`;
  d.innerHTML = `<form method="dialog" class="dialog-close-form"><button type="submit" class="icon-button" aria-label="关闭">${icon("close")}</button></form><h2>${esc(title)}</h2>${html}`;
  if (!d.open) d.showModal();
  d.addEventListener(
    "click",
    (e) => {
      if (e.target === d) {
        const r = d.getBoundingClientRect();
        if (
          e.clientX < r.left ||
          e.clientX > r.right ||
          e.clientY < r.top ||
          e.clientY > r.bottom
        )
          d.close();
      }
    },
    { once: true },
  );
  if (onSubmit) {
    const form = $("form[data-form]", d);
    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const submit = $("[type=submit]", form);
      try {
        if (submit) submit.disabled = true;
        await onSubmit(form);
        if (d.dataset.modalId === modalId) d.close();
      } catch (err) {
        toast(err.message, "error");
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  }
  d.onclose = () => {
    if (previousFocus?.isConnected) previousFocus.focus?.();
    else $("#main")?.focus();
  };
  // Focus once while opening. A delayed autofocus can steal focus after a
  // person has already moved to another field and redirect their input.
  $("input:not([type=hidden]),select,textarea", d)?.focus({
    preventScroll: true,
  });
  return d;
}
export async function confirmAction(title, description, label = "确认") {
  return new Promise((resolve) => {
    const d = modal(
      title,
      `<p class="dialog-desc">${esc(description)}</p><div class="dialog-actions">${btn("取消", "cancel", "", "button secondary")}${btn(label, "confirm", "", "button primary")}</div>`,
    );
    d.querySelector("[data-action=cancel]").onclick = () => {
      resolve(false);
      d.close();
    };
    d.querySelector("[data-action=confirm]").onclick = () => {
      resolve(true);
      d.close();
    };
    d.addEventListener("cancel", () => resolve(false), { once: true });
    d.addEventListener("close", () => resolve(false), { once: true });
  });
}
export function download(name, data, type = "application/json") {
  const blob = new Blob([typeof data === "string" ? data : pretty(data)], {
    type,
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
export function onAction(container, handlers) {
  actionBindings.get(container)?.();
  const fn = async (e) => {
    const el = e.target.closest("[data-action]");
    if (!el || !container.contains(el) || el.disabled) return;
    const handler = handlers[el.dataset.action];
    if (handler) {
      try {
        await handler(el, e);
      } catch (err) {
        toast(err.message || String(err), "error");
      }
    }
  };
  container.addEventListener("click", fn);
  const cleanup = () => {
    container.removeEventListener("click", fn);
    if (actionBindings.get(container) === cleanup)
      actionBindings.delete(container);
  };
  actionBindings.set(container, cleanup);
  return cleanup;
}
export function setBusy(button, yes, label = "处理中") {
  if (!button) return;
  if (yes) {
    button.dataset.previous = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>${esc(label)}`;
  } else {
    button.disabled = false;
    button.innerHTML = button.dataset.previous || button.innerHTML;
  }
}
export function parseJSON(input, label = "JSON") {
  try {
    return JSON.parse(input);
  } catch {
    throw new Error(`${label} 格式不正确，请检查引号、逗号与括号`);
  }
}
