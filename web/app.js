import {
  $,
  $$,
  esc,
  uid,
  arr,
  pretty,
  textValue,
  humanBytes,
  time,
  date,
  icon,
  btn,
  iconBtn,
  badge,
  statusLabel,
  empty,
  pageHead,
  field,
  area,
  select,
  formData,
  toast,
  modal,
  download,
  onAction,
  setBusy,
  parseJSON,
} from "./ui.js";
import { mountProduction } from "./project-production.js";
import { installSidebarResize } from "./workspace-layout.js";
import { mountEditor } from "./editor.js";
import { mountWorkflow } from "./workflow.js";
import {
  renderControls,
  mountControls,
  readControls as readTypedControls,
} from "./controls.js";
import { uploadFile } from "./upload.js";
import { selectionForFeature } from "./selection-actions.js";
import { recordFields, displayRecordFacts } from "./record-fields.js";
import { chooseAudioAnalysis, chooseRecovery } from "./run-inspection.js";
import { actionForFeature } from "./production-actions.js";
import { mountExecutionControls } from "./execution-controls.js";
import {
  mediaLibrary,
  mediaType,
  mediaName,
  mediaVariant,
  mediaDuration,
} from "./media.js";

// The original Atlas GROUPS declaration is the business navigation hierarchy.
const GROUPS = [
  ["Agent 集群", ["agents", "harness"]],
  ["目标与创意", ["project", "trend"]],
  [
    "素材与资产",
    ["acquire", "generate", "ingest", "label", "search", "assetops"],
  ],
  ["声音与叙事", ["voice", "music", "plan"]],
  ["编辑与交付", ["edit", "render"]],
  ["账号与发布", ["account", "publish"]],
  ["数据与进化", ["metrics", "learn"]],
  ["记忆与运行", ["data", "security", "ops", "workspace"]],
];
// Module pages retain one canonical sidebar entry per Atlas module. Dedicated
// workspaces are actions within their corresponding business context.
const workspaceForModule = {
  agents: ["agents", "配置团队"],
  harness: ["agents", "配置模型"],
  acquire: ["assets", "打开素材库"],
  generate: ["production", "打开制作台"],
  ingest: ["assets", "打开素材库"],
  label: ["assets", "打开素材库"],
  search: ["assets", "打开素材库"],
  assetops: ["assets", "打开素材库"],
  plan: ["production", "打开制作台"],
  edit: ["editor", "打开剪辑室"],
  render: ["delivery", "查看交付"],
  publish: ["delivery", "查看交付"],
  account: ["delivery", "账号与排程"],
  metrics: ["insights", "查看数据"],
  learn: ["insights", "查看实验"],
  workspace: ["workflow", "打开工作流"],
  ops: ["audit", "查看活动记录"],
};
const MODULE_ICONS = {
  project: "brief",
  trend: "spark",
  acquire: "download",
  generate: "image",
  ingest: "upload",
  label: "eye",
  search: "search",
  assetops: "layers",
  voice: "volume",
  music: "music",
  plan: "grid",
  edit: "edit",
  render: "film",
  account: "globe",
  publish: "send",
  metrics: "chart",
  learn: "sliders",
  agents: "agents",
  harness: "code",
  data: "box",
  security: "lock",
  ops: "history",
  workspace: "grid",
};
const READ_ONLY_KINDS = new Set([
  "Run",
  "ToolCall",
  "RenderJob",
  "Artifact",
  "Event",
  "Audit",
  "ImportBatch",
  "ImportEntry",
  "TransformRun",
  "BlobLocation",
  "TimeMap",
  "Cost",
  "Post",
  "PlatformEvent",
  "ProductionTrace",
  "RenderManifest",
  "DeliveryPackage",
  "ReplayManifest",
  "Approval",
]);
const prefs = {
  theme: localStorage.getItem("ship-theme") || "system",
  motion: localStorage.getItem("ship-motion") || "system",
  transparency: localStorage.getItem("ship-transparency") || "system",
};
const state = {
  catalog: null,
  health: null,
  projects: [],
  projectId: localStorage.getItem("ship-project") || "",
  records: [],
  events: [],
  runs: [],
  cleanup: [],
  routeGeneration: 0,
  featureId: null,
  assetType: "all",
  assetQuery: "",
  importQueue: [],
  importBusy: false,
  online: true,
};
function applyPrefs() {
  const dark =
    prefs.theme === "dark" ||
    (prefs.theme === "system" &&
      matchMedia("(prefers-color-scheme:dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.motion = prefs.motion;
  document.documentElement.dataset.transparency = prefs.transparency;
  document.querySelector("meta[name=theme-color]").content = dark
    ? "#161617"
    : "#f5f5f7";
}
applyPrefs();
matchMedia("(prefers-color-scheme:dark)").addEventListener(
  "change",
  applyPrefs,
);
async function api(path, options = {}) {
  const headers = { ...options.headers };
  const token = sessionStorage.getItem("ship-token");
  if (token) headers.Authorization = `Bearer ${token}`;
  if (
    options.body &&
    typeof options.body !== "string" &&
    !(options.body instanceof Blob) &&
    !(options.body instanceof ArrayBuffer)
  ) {
    headers["Content-Type"] = "application/json";
    options = { ...options, body: JSON.stringify(options.body) };
  }
  let response;
  try {
    response = await fetch(`/api/v1${path}`, {
      credentials: "same-origin",
      ...options,
      headers,
    });
  } catch {
    state.online = false;
    throw new Error(
      "暂时无法连接服务。当前修改仍保留在页面，请检查服务后重试。",
    );
  }
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`服务返回了无法读取的响应（${response.status}）`);
  }
  if (!response.ok || body.error) {
    const err = body.error || {};
    const msg =
      err.message ||
      (typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail)) ||
      `请求失败（${response.status}）`;
    const extra = Array.isArray(err.details)
      ? err.details
          .slice(0, 3)
          .map((d) =>
            [d.path || d.field || d.key, d.message || d.code || d.expected]
              .filter(Boolean)
              .join(": "),
          )
          .join("；")
      : "";
    const error = new Error(extra ? msg + " " + extra : msg);
    error.details = err.details;
    error.code = err.code;
    error.status = response.status;
    error.traceId = body.trace_id;
    throw error;
  }
  state.online = true;
  return body.data ?? body;
}
let eventSource = null,
  eventProject = null;
function startEvents() {
  if (eventSource && eventProject === state.projectId) return;
  eventSource?.close();
  eventProject = state.projectId;
  eventSource = new EventSource(
    `/api/v1/events/stream${state.projectId ? "?project_id=" + encodeURIComponent(state.projectId) : ""}`,
  );
  eventSource.onopen = () => {
    const el = $("#live-state");
    if (el) el.textContent = "实时连接";
  };
  eventSource.addEventListener("fact", (event) => {
    const el = $("#live-state");
    if (el) el.textContent = "记录已更新";
    let fact;
    try {
      fact = JSON.parse(event.data);
    } catch {
      return;
    }
    if (fact.project_id && fact.project_id !== state.projectId) return;
    state.latestEvent = fact;
  });
  eventSource.onerror = () => {
    const el = $("#live-state");
    if (el) el.textContent = "等待同步";
  };
}
const project = () => state.projects.find((p) => p.id === state.projectId);
const projectName = () => project()?.data?.name || "选择项目";
const contentLabel = (v) =>
  ({
    voiceover: "口播产品介绍",
    music: "音乐卡点",
    product: "纯音乐产品展示",
    mixed: "屏幕演示 / 教程",
    custom: "非模板创意片",
  })[v] || v;
function requireProject() {
  if (!project()) throw new Error("先创建或选择一个项目，再保存或执行。");
  return state.projectId;
}
function bind(handlers, container = $("#main")) {
  state.actionCleanup?.();
  state.actionCleanup = onAction(container, handlers);
}
function nav(route) {
  location.hash = route;
}
async function reloadData() {
  const pid = state.projectId;
  if (!state.projectId) {
    state.records = [];
    state.events = [];
    state.runs = [];
    return;
  }
  const results = await Promise.all([
    api(`/records?project_id=${encodeURIComponent(state.projectId)}&limit=500`),
    api(`/events?project_id=${encodeURIComponent(state.projectId)}`),
    api(`/runs?project_id=${encodeURIComponent(state.projectId)}`),
  ]);
  if (state.projectId !== pid) return;
  state.records = arr(results[0].items || results[0]);
  state.events = arr(results[1].items || results[1]);
  state.runs = arr(results[2].items || results[2]);
}
const records = (kind) => state.records.filter((r) => r.kind === kind);
const mediaRecords = () => mediaLibrary(state.records);
async function saveRecord(
  kind,
  data,
  existing = null,
  refs = [],
  reason = "用户在工作台保存",
) {
  const pid = requireProject();
  const result = existing
    ? await api(`/records/${existing.id}`, {
        method: "PATCH",
        body: { expected_revision: existing.version, data, refs, reason },
      })
    : await api("/records", {
        method: "POST",
        body: { kind, project_id: state.projectId, data, refs, reason },
      });
  if (result.kind === "Project")
    state.projects = state.projects.map((p) =>
      p.id === result.id ? result : p,
    );
  if (state.projectId !== pid) return result;
  state.records = state.records.filter((r) => r.id !== result.id);
  state.records.unshift(result);
  return result;
}
async function command(type, parameters, target = null) {
  requireProject();
  return api("/commands", {
    method: "POST",
    body: {
      command_id: uid(),
      command_type: type,
      project_id: state.projectId,
      actor_id: state.health?.actor?.actor_id || "local-owner",
      actor_role: state.health?.actor?.role || "owner",
      target_id: target?.id || null,
      expected_revision: target?.version || null,
      input_version_refs: target
        ? [{ id: target.id, version: target.version, kind: target.kind }]
        : [],
      parameters,
      policy_version: null,
      idempotency_key: uid(),
      trace_id: uid(),
      reason: "用户在工作台明确发起",
    },
  });
}
function ref(r) {
  return { id: r.id, version: r.version, kind: r.kind };
}
function currentView() {
  return records("View").find((r) => r.data.name === "工作区配置") || null;
}
async function saveFeature(f, routeId, values) {
  const old = currentView();
  return saveRecord(
    "View",
    {
      ...(old?.data || {}),
      name: "工作区配置",
      operation_scope: {
        ...(old?.data?.operation_scope || {}),
        feature_configs: {
          ...(old?.data?.operation_scope?.feature_configs || {}),
          [f.id]: {
            route_id: routeId,
            executions: values.__executions || configFor(f).executions || {},
            parameters: Object.fromEntries(
              Object.entries(values).filter(([k]) => !k.startsWith("__")),
            ),
            input_bindings: values.__input_bindings || {},
            locks: values.__locks || [],
            scope: state.projectId,
          },
        },
      },
    },
    old,
    [],
    `保存 ${f.id} ${f.label} 的路线与参数`,
  );
}
function configFor(f) {
  return currentView()?.data?.operation_scope?.feature_configs?.[f.id] || {};
}
const ctx = {
  state,
  api,
  records,
  project,
  requireProject,
  saveRecord,
  command,
  ref,
  reloadData,
  nav,
  toast,
  bind,
  refresh: () => renderRoute(),
  openFeature: (id) => {
    const f = state.catalog.features.find((f) => f.id === id);
    if (f) {
      state.featureId = id;
      const route = `module/${f.module}/${f.id}`;
      if (location.hash.slice(1) === route) renderRoute();
      else nav(route);
    }
  },
  openRecord,
  assetUrl: (r) => `/api/v1/media/${encodeURIComponent(r.id)}/file`,
  showHistory,
  saveFeature,
  configFor,
  runSummary,
  newProject,
  projectPicker,
  modelList,
  approveArtifact,
  publicationForm,
  preflightPublication,
  receiptForm,
  teamForm,
};

function shell() {
  const menuLink = ([route, title, symbol]) =>
    `<a class="nav-item" data-route="${route}" href="#${route}" title="${esc(title)}">${icon(symbol)}<span>${esc(title)}</span>${route === "assets" ? '<span class="nav-number" id="asset-count"></span>' : ""}</a>`;
  const collapsed = localStorage.getItem("ship-compact-nav") === "true";
  $("#app").innerHTML = `<div class="shell ${collapsed ? "compact-nav" : ""}">
    <button class="sidebar-scrim" aria-label="关闭导航"></button>
    <aside class="sidebar" aria-label="工作区导航">
      <a class="brand" href="#overview" aria-label="AI Ship 项目"><span class="brand-mark" aria-hidden="true">${icon("brand")}</span><span class="brand-name">AI Ship</span></a>
      <nav class="nav-group" aria-label="工作区">
        ${menuLink(["overview", "项目", "folder"])}
        ${menuLink(["production", "制作台", "spark"])}
      </nav>
      <div class="nav-divider"></div>
      <nav class="atlas-nav" aria-label="业务分区">
        <div class="nav-section-head"><span>分区</span><button class="icon-button" data-global="modules" aria-label="浏览全部模块" title="浏览全部模块">${icon("grid")}</button></div>
        ${GROUPS.map(
          ([name, ids]) =>
            `<details class="nav-cluster" data-cluster="${ids[0]}"><summary><span>${esc(name)}</span>${icon("chevron")}</summary><div>${ids
              .map((id) => {
                const m = state.catalog.modules.find((item) => item.id === id);
                return `<a class="nav-item module-nav-link" data-module-link="${id}" href="#module/${id}">${icon(MODULE_ICONS[id])}<span>${esc(m.label)}</span></a>`;
              })
              .join("")}</div></details>`,
        ).join("")}
      </nav>
      <button class="rail-modules icon-button" data-global="modules" aria-label="浏览业务分区" title="浏览业务分区">${icon("grid")}</button>
      <div class="sidebar-bottom">${menuLink(["audit", "活动记录", "history"])}${menuLink(["settings", "设置", "settings"])}</div>
    </aside>
    <div class="content-column"><div class="workspace-context">
      <button class="icon-button desktop-nav-toggle" aria-label="切换侧栏" data-global="rail" title="切换侧栏" aria-expanded="${!collapsed}">${icon("sidebar")}</button>
      <button class="icon-button mobile-nav-toggle" data-global="menu" aria-label="打开导航" aria-expanded="false">${icon("sidebar")}</button>
      <button class="project-switch" data-global="projects">${icon("folder")}<strong class="context-label">${esc(projectName())}</strong>${icon("down")}</button>
      <a class="production-back" href="#production" hidden>${icon("arrow")}<span>返回制作台</span></a><span class="context-spacer"></span><span id="live-state" class="connection-state sr-only" aria-live="polite"></span>
      <button class="context-button" data-global="search" aria-label="查找功能" title="查找功能"> ${icon("search")}<span>查找功能</span><kbd>⌘ K</kbd></button>
    </div><main id="main" tabindex="-1" aria-label="主工作区"></main></div></div>`;
  installSidebarResize($(".shell"));
  $("#app").onclick = (e) => {
    const g = e.target.closest("[data-global]")?.dataset.global;
    if (g === "menu") setMenu(!$(".shell").classList.contains("sidebar-open"));
    if (g === "rail") {
      const compact = $(".shell").classList.toggle("compact-nav");
      localStorage.setItem("ship-compact-nav", String(compact));
      $(".desktop-nav-toggle").setAttribute("aria-expanded", String(!compact));
    }
    if (g === "modules") modulePicker();
    if (g === "search") search();
    if (g === "projects") projectPicker();
    if (g === "theme") {
      prefs.theme =
        document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem("ship-theme", prefs.theme);
      applyPrefs();
    }
    if (
      e.target.matches(".sidebar-scrim") ||
      e.target.closest(".nav-item[href]")
    )
      setMenu(false);
  };
}
function setMenu(open) {
  $(".shell")?.classList.toggle("sidebar-open", open);
  $(".mobile-nav-toggle")?.setAttribute("aria-expanded", String(open));
  if (open) $(".sidebar [aria-current=page]")?.focus();
}

async function renderRoute() {
  if (state.catalog) startEvents();
  const generation = ++state.routeGeneration;
  state.cleanup.forEach((f) => f?.());
  state.cleanup = [];
  state.actionCleanup?.();
  state.actionCleanup = null;
  const route = (
    location.hash.slice(1) || (project() ? "production" : "overview")
  ).split("/");
  const main = $("#main");
  if (!main) return;
  $$(".nav-item[data-route]").forEach((el) =>
    el.setAttribute(
      "aria-current",
      el.dataset.route === route[0] ? "page" : "false",
    ),
  );
  $(".context-label").textContent = projectName();
  $(".shell").dataset.view = route[0];
  $(".production-back").hidden = route[0] !== "editor" || !project();
  $$(".module-nav-link").forEach((el) => {
    const current = route[0] === "module" && route[1] === el.dataset.moduleLink;
    el.setAttribute("aria-current", current ? "page" : "false");
    if (current) el.closest("details").open = true;
  });
  main.innerHTML =
    '<div class="loading-view" role="status"><span class="spinner"></span>正在读取工作区</div>';
  try {
    await reloadData();
    if (generation !== state.routeGeneration) return;
    const views = {
      overview,
      assets,
      agents,
      delivery,
      insights,
      audit,
      settings,
    };
    if (route[0] === "editor") {
      ctx.editorIntent = route[1];
      ctx.editorTimelineId = route[1] === "project-timeline" ? route[2] : null;
      await mountEditor(main, ctx);
    } else if (route[0] === "production") {
      if (project()) await mountProduction(main, ctx);
      else await overview();
    } else if (route[0] === "workflow") await mountWorkflow(main, ctx);
    else if (route[0] === "module") await modulePage(route[1], route[2]);
    else await (views[route[0]] || overview)();
    main.scrollTop = 0;
  } catch (e) {
    if (generation !== state.routeGeneration) return;
    main.innerHTML = `<div class="page">${pageHead("连接状态", "工作区暂时不可用")}<div class="surface">${empty("未能读取当前数据", e.message, "retry", "重新连接", "cloud")}<div class="form-actions">${btn("连接设置", "settings", "settings", "button ghost")}</div></div></div>`;
    bind({ retry: () => renderRoute(), settings: () => settings() });
  }
}

function newProject(record = null) {
  const editing = record?.kind === "Project" ? record : null;
  const initial = editing?.data || {};
  modal(
    editing ? "项目设置" : "新建项目",
    `<form data-form class="form-stack">${field("项目名称", "name", initial.name || "", "text", 'required maxlength="200" placeholder="例如：秋季产品展示"')}${area("项目目标", "goals", typeof initial.goals === "object" ? pretty(initial.goals) : initial.goals || "", 'placeholder="希望传递什么？哪些事实、镜头或文案必须保留？"')}<div class="form-grid">${select(
      "内容类型",
      "content_mode",
      [
        ["voiceover", "口播产品介绍"],
        ["music", "音乐卡点 / 踩点"],
        ["product", "纯音乐产品展示"],
        ["mixed", "屏幕演示 / 教程"],
        ["custom", "非模板创意片"],
      ],
      initial.content_mode || "product",
    )}${field("目标市场", "market", initial.market || "CN", "text", 'required placeholder="例如：中国大陆 / 美国"')}${field("语言", "language", initial.language || "zh-CN", "text", "required")}${field("目标时长（秒）", "duration", initial.duration || 30, "number", 'required min="1" max="7200"')}${select(
      "画幅",
      "format",
      [
        ["1080,1920", "竖屏 9:16"],
        ["1920,1080", "横屏 16:9"],
        ["1080,1080", "方形 1:1"],
        ...(initial.width &&
        !["1080,1920", "1920,1080", "1080,1080"].includes(
          initial.width + "," + initial.height,
        )
          ? [
              [
                initial.width + "," + initial.height,
                initial.width + " × " + initial.height,
              ],
            ]
          : []),
      ],
      initial.width ? initial.width + "," + initial.height : "1080,1920",
    )}${field("费用上限（USD）", "budget", initial.budget ?? 0, "number", 'min="0" step="0.01"')}</div><div class="form-actions"><button type="submit" class="button primary">${editing ? "保存更改" : "创建项目"} ${icon("arrow")}</button></div></form>`,
    async (f) => {
      const d = formData(f),
        [width, height] = d.format.split(",").map(Number);
      delete d.format;
      if (editing) {
        const fields = {
          ...initial,
          ...d,
          width,
          height,
          duration: +d.duration,
          budget: +d.budget,
        };
        if (typeof initial.goals === "object")
          fields.goals = parseJSON(d.goals, "目标");
        await saveRecord(
          "Project",
          fields,
          editing,
          editing.refs || [],
          "修改项目设置",
        );
        toast("项目设置已保存");
        await renderRoute();
        return;
      }
      const p = await api("/projects", {
        method: "POST",
        body: { ...d, width, height, duration: +d.duration, budget: +d.budget },
      });
      state.projects.unshift(p);
      state.projectId = p.id;
      localStorage.setItem("ship-project", p.id);
      toast("项目已创建");
      if (location.hash === "#production") await renderRoute();
      else nav("production");
    },
  );
}
function projectPicker() {
  const d = modal(
    "选择项目",
    `<div class="project-grid">${state.projects.map((p) => `<button class="project-card" data-project="${esc(p.id)}"><div class="project-card-head">${icon("brief")}${p.id === state.projectId ? badge("当前", "blue") : badge(statusLabel(p.data.status || "draft"))}</div><h3>${esc(p.data.name)}</h3><p>${esc(textValue(p.data.goals))}</p><div class="project-card-footer"><span>${esc(contentLabel(p.data.content_mode) || "未选择内容类型")}</span><span>${date(p.created_at)}</span></div></button>`).join("") || empty("还没有项目", "创建项目，保存目标、素材和制作过程。", "", "", "brief")}</div><div class="dialog-actions">${btn("新建项目", "new", "plus", "button primary")}</div>`,
    null,
    true,
  );
  d.querySelectorAll("[data-project]").forEach(
    (b) =>
      (b.onclick = () => {
        state.projectId = b.dataset.project;
        localStorage.setItem("ship-project", state.projectId);
        d.close();
        if (location.hash === "#production") renderRoute();
        else nav("production");
      }),
  );
  d.querySelector("[data-action=new]").onclick = () => {
    d.close();
    newProject();
  };
}
async function overview() {
  const projects = state.projects
    .slice()
    .sort((a, b) =>
      String(b.updated_at || b.created_at).localeCompare(
        String(a.updated_at || a.created_at),
      ),
    );
  $("#main").innerHTML =
    `<div class="page project-page">${pageHead("", "项目", "", projects.length ? btn("新建项目", "new-project", "plus", "button primary") : "")}
    ${
      projects.length
        ? `<div class="project-list"><div class="project-list-head"><span>名称</span><span>类型</span><span>最近修改</span></div>${projects.map((p) => `<button class="project-entry" data-action="select-project" data-id="${esc(p.id)}"><span class="project-entry-name">${icon("film")}<span><strong>${esc(p.data.name)}</strong><small>${[p.data.width && p.data.height ? p.data.width + " × " + p.data.height : "", p.data.language].filter(Boolean).map(esc).join(" · ")}</small></span></span><span>${esc(contentLabel(p.data.content_mode) || "—")}</span><time>${date(p.updated_at || p.created_at)}</time>${icon("chevron")}</button>`).join("")}</div>`
        : `<div class="empty-project"><span class="project-film-icon" aria-hidden="true">${icon("film")}</span><h2>创建第一个项目</h2>${btn("新建项目", "new-project", "plus", "button primary")}</div>`
    }
  </div>`;
  bind({
    "new-project": newProject,
    "select-project": (b) => {
      state.projectId = b.dataset.id;
      localStorage.setItem("ship-project", state.projectId);
      nav("production");
    },
  });
}

function modulePicker() {
  const d = modal(
    "业务分区",
    `<div class="cluster-picker">${GROUPS.map(
      ([name, ids]) =>
        `<section><h3>${esc(name)}</h3>${ids
          .map((id) => {
            const m = state.catalog.modules.find((item) => item.id === id);
            return `<button class="module-item" data-module="${id}">${icon(MODULE_ICONS[id])}<span>${esc(m.label)}</span>${icon("chevron")}</button>`;
          })
          .join("")}</section>`,
    ).join("")}</div>`,
    null,
    true,
  );
  $$("[data-module]", d).forEach(
    (button) =>
      (button.onclick = () => {
        d.close();
        setMenu(false);
        nav(`module/${button.dataset.module}`);
      }),
  );
}
function search() {
  const d = modal(
    "查找功能",
    `<div class="search-field" style="max-width:none">${icon("search")}<input id="global-search" placeholder="搜索分区、功能或参数…" aria-label="搜索全部功能"></div><div class="search-results" id="global-results"></div>`,
  );
  const draw = () => {
    const q = $("#global-search").value.trim().toLowerCase();
    const list = state.catalog.features.filter((f) =>
      [
        f.id,
        f.label,
        state.catalog.modules.find((m) => m.id === f.module)?.label,
        GROUPS.find(([, ids]) => ids.includes(f.module))?.[0],
        f.goal,
        ...arr(f.parameters),
      ]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
    $("#global-results").innerHTML =
      list
        .slice(0, q ? state.catalog.features.length : 12)
        .map(
          (f) =>
            `<button class="search-result" data-feature="${f.id}">${icon(MODULE_ICONS[f.module])}<span><strong>${esc(f.label)}</strong><small>${esc(state.catalog.modules.find((m) => m.id === f.module)?.label)}</small></span>${icon("chevron")}</button>`,
        )
        .join("") || '<p class="search-results-empty">没有匹配的功能</p>';
    $("#global-results")
      .querySelectorAll("[data-feature]")
      .forEach(
        (b) =>
          (b.onclick = () => {
            d.close();
            ctx.openFeature(b.dataset.feature);
          }),
      );
  };
  $("#global-search").oninput = draw;
  draw();
}

const featureDrafts = new Map();

function entityName(kind) {
  return (
    state.catalog?.entities.find((e) => e.id === kind)?.label || kind || "记录"
  );
}
function recordLabel(r) {
  if (!r) return "未找到记录";
  return (
    r.data?.name ||
    r.data?.filename ||
    r.data?.title ||
    r.data?.label ||
    entityName(r.kind)
  );
}
function chooseRecord(title, candidates, onSelect, emptyMessage) {
  requireProject();
  const d = modal(
    title,
    candidates.length
      ? `<div>${candidates.map((r) => `<button class="list-row" data-pick="${esc(r.id)}">${icon("file")}<span class="list-row-body"><strong>${esc(recordLabel(r))}</strong><small>${esc(entityName(r.kind))} · v${r.version} · ${date(r.updated_at || r.created_at)}</small></span>${icon("chevron")}</button>`).join("")}</div>`
      : `<p class="subtle-empty">${esc(emptyMessage || "当前项目没有可选记录")}</p>`,
  );
  $$("[data-pick]", d).forEach(
    (b) =>
      (b.onclick = async () => {
        const r = candidates.find((item) => item.id === b.dataset.pick);
        try {
          await onSelect(r);
        } catch (err) {
          toast(err.message, "error");
        }
      }),
  );
}
async function showTrace(r) {
  const t = await api(`/production/trace/${encodeURIComponent(r.id)}`);
  const nodes = new Map(arr(t.nodes).map((n) => [n.key, n]));
  const label = (key) => {
    const n = nodes.get(key);
    return n ? `${n.name || entityName(n.kind)} · v${n.version}` : "记录不可用";
  };
  modal(
    "来源链",
    `<div class="lineage-list">${
      arr(t.edges)
        .map(
          (edge) =>
            `<div class="list-row"><span>${esc(label(edge.source))}</span>${icon("arrow")}<span>${esc(label(edge.target))}</span></div>`,
        )
        .join("") || '<p class="subtle-empty">此版本没有上游引用</p>'
    }</div>${t.truncated ? "<p>仅显示本次查询范围内的记录</p>" : ""}<details class="source-details"><summary>技术详情</summary><pre>${esc(pretty(t))}</pre></details>`,
    null,
    true,
  );
}
async function preflightPublication(p) {
  const r = await api(`/production/publications/${p.id}/preflight`);
  modal(
    "发布前检查",
    `<div class="surface-title"><h3>${r.can_handoff ? "可以人工交接" : "需补齐条件"}</h3></div>${arr(
      r.problems,
    )
      .map(
        (problem) =>
          `<div class="list-row">${icon("warning")}<span>${esc(problem.message || problem.code)}</span></div>`,
      )
      .join(
        "",
      )}<details class="source-details"><summary>技术详情</summary><pre>${esc(pretty(r))}</pre></details>`,
  );
}
function featureAction(id) {
  const production = selectionForFeature(id) || actionForFeature(id);
  if (production) return [production.label, () => production.open(ctx)];
  const pick = (label, kinds, handle = openRecord, emptyMessage) => [
    label,
    () =>
      chooseRecord(
        label,
        state.records.filter((r) => kinds.includes(r.kind)),
        handle,
        emptyMessage,
      ),
  ];
  const edit = (label, intent) => [label, () => nav(`editor/${intent}`)];
  const specific = {
    P01: [project() ? "编辑项目" : "新建项目", () => newProject(project())],
    P03: ["修改市场与语言", () => newProject(project())],
    P04: ["修改制作规格", () => newProject(project())],
    P05: ["编排流程", () => nav("workflow")],
    P06: [
      "查看项目版本",
      () => (project() ? showHistory(project()) : newProject()),
    ],
    A01: ["导入文件", () => startFeatureImport(false)],
    A05: [
      "查看导入批次",
      () => chooseRecord("导入批次", records("ImportBatch"), openRecord),
    ],
    A07: ["导入文件夹", () => startFeatureImport(true)],
    I01: pick("查看媒体信息", ["Asset"], assetDetail),
    I02: pick("查看原始文件", ["Asset"], assetDetail),
    I03: pick("处理素材", ["Asset", "AssetVersion"], transformAsset),
    I04: pick("调整媒体配方", ["Asset", "AssetVersion"], transformAsset),
    I07: pick("编辑处理配方", ["Asset", "AssetVersion"], transformAsset),
    U01: pick("查看使用记录", ["Usage"]),
    V03: edit("导入配音", "import"),
    B01: ["分析声音起音", () => chooseAudioAnalysis(ctx)],
    B05: edit("调整音量", "mix"),
    G01: ["登记补充镜头", () => nav("production/generation")],
    G02: ["生成关键帧候选", () => nav("production/generation")],
    G03: ["生成视频候选", () => nav("production/generation")],
    G04: ["核对生成任务", () => nav("production/generation")],
    G05: ["查看生成素材", () => nav("production/generation")],
    N01: ["编排制作方案", () => nav("production")],
    N03: ["核对镜头区间", () => nav("production")],
    C06: ["审阅与接管任务", () => nav("production")],
    C08: ["设定制作目标", () => nav("production")],
    C09: ["审阅制作决定", () => nav("production")],
    W01: ["输入制作目标", () => nav("production")],
    N05: edit("设置画幅", "format"),
    E01: edit("导入时间线", "timeline-import"),
    E02: edit("剪辑片段", "trim"),
    E03: edit("预览时间线", "inspect"),
    E04: edit("编辑字幕", "captions"),
    E05: edit("调整画面", "grade"),
    E06: edit("查看与锁定片段", "inspect"),
    E07: edit("打开剪辑版本", "history"),
    E08: edit("审阅并锁版", "lock"),
    E09: edit("比较已保存时间线", "compare"),
    Y01: edit("设置输出规格", "format"),
    Y02: pick("查看渲染任务", ["RenderJob"]),
    Y03: edit("渲染成片", "render"),
    Y04: pick("查看渲染结果", ["RenderJob", "RenderManifest"]),
    Y05: pick("审阅成片", ["Artifact"], approveArtifact),
    Y06: edit("导出成片", "export"),
    Y08: pick("查看渲染清单", ["RenderManifest"]),
    Y09: edit("交换剪辑工程", "timeline-export"),
    C01: ["配置团队", deploymentForm],
    C11: ["运行团队", teamForm],
    H02: ["查看模型配置", modelList],
    H03: ["选择模型", modelList],
    H06: pick("查看执行记录", ["Run", "ToolCall"]),
    H12: ["验证模型", modelTryForm],
    J01: ["管理账号", accountList],
    J03: ["新建发布意图", publicationForm],
    J04: ["安排发布", publicationForm],
    J07: pick("核对账号记录", ["Account"]),
    F01: ["选择发布通道", publicationForm],
    F02: pick("预检发布意图", ["Publication"], preflightPublication),
    F06: pick("记录平台可见性", ["Publication"], receiptForm),
    F08: pick("回填发布回执", ["Publication"], receiptForm),
    M01: ["导入指标", metricForm],
    M02: ["导入带口径指标", metricForm],
    M03: ["查看统计", () => nav("insights")],
    M06: ["补充观测窗口", metricForm],
    M07: ["查看数据面板", () => nav("insights")],
    X01: ["查看分组指标", () => nav("insights")],
    X04: ["创建实验", experimentForm],
    D01: ["查看项目记录", () => nav("audit")],
    D03: pick(
      "追踪上游来源",
      ["Asset", "AssetVersion", "Audio", "Timeline", "Artifact"],
      showTrace,
    ),
    D08: pick("核对依赖清单", [
      "ReplayManifest",
      "BlobLocation",
      "RenderManifest",
    ]),
    K01: ["管理工作区连接", () => nav("settings")],
    K04: pick("审阅成片权利", ["Artifact"], (a) =>
      approveArtifact(a, "rights_check"),
    ),
    K05: pick("查看确认记录", ["Approval"]),
    O01: ["查看活动记录", () => nav("audit")],
    O06: ["核对与恢复运行", () => chooseRecovery(ctx)],
    O02: pick("查看费用记录", ["Cost"]),
    O04: ["检查服务连接", () => nav("settings")],
    W02: ["编排工作流", () => nav("workflow")],
    W03: edit("预览与剪辑素材", "inspect"),
    W04: pick("审阅成片", ["Artifact"], approveArtifact),
    W05: ["查看发布与回执", () => nav("delivery")],
    W07: ["打开外观设置", () => nav("settings")],
    W09: ["打开流程画布", () => nav("workflow")],
  };
  return specific[id] || null;
}

function startFeatureImport(folder) {
  requireProject();
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  if (folder) input.setAttribute("webkitdirectory", "");
  else input.accept = "video/*,audio/*,image/*";
  input.onchange = async () => {
    if (input.files.length) await importFiles(input.files);
  };
  input.click();
}

async function modulePage(id, requestedFeature) {
  const m = state.catalog.modules.find((m) => m.id === id);
  if (!m) return overview();
  const features = state.catalog.features.filter((f) => f.module === m.id);
  const f =
    features.find((f) => f.id === (requestedFeature || state.featureId)) ||
    features[0];
  state.featureId = f.id;
  const key = JSON.stringify([state.projectId, f.id]);
  const cfg = structuredClone(featureDrafts.get(key) || configFor(f));
  const route = f.routes.find((r) => r.id === cfg.route_id) || f.routes[0];
  const controls = arr(f.operation?.controls).filter(
    (c) => c.key !== "route_choice",
  );
  const inputs = arr(f.operation?.inputs),
    outputs = arr(f.operation?.outputs);
  const direct = featureAction(f.id);
  const bindings = structuredClone(cfg.input_bindings || {});
  const locks = structuredClone(cfg.locks || []);
  const executions = structuredClone(cfg.executions || {});
  const group = GROUPS.find(([, ids]) => ids.includes(id));
  const workspace = workspaceForModule[id];
  $("#main").innerHTML = `<div class="page module-page">
    <div class="module-path">${esc(group?.[0] || "")}</div>
    ${pageHead("", m.label, "", workspace ? `<a class="button secondary" href="#${workspace[0]}">${icon("external")}${esc(workspace[1])}</a>` : "")}
    <div class="feature-layout">
      <nav class="feature-sidebar" aria-label="模块功能">${features.map((x) => `<button type="button" class="feature-item ${x.id === f.id ? "active" : ""}" data-action="feature" data-id="${x.id}" aria-current="${x.id === f.id ? "page" : "false"}"><span>${esc(x.label)}</span>${icon("chevron")}</button>`).join("")}</nav>
      <section class="feature-detail"><header class="feature-title"><h2>${esc(f.label)}</h2>${direct ? btn(direct[0], "feature-action", "arrow", "button secondary") : ""}</header>
        <form id="feature-form">
          <div class="feature-route-row"><label class="field"><span>处理方案</span><select id="feature-route">${f.routes.map((r) => `<option value="${r.id}" ${r.id === route.id ? "selected" : ""}>${esc(r.label)}</option>`).join("")}</select></label><output id="feature-route-detail" class="sr-only"></output></div>
          <div class="feature-controls">${renderControls(controls, cfg.parameters || {})}</div>
          ${
            inputs.length
              ? `<details class="feature-inputs"><summary>关联输入</summary><div class="form-grid">${inputs
                  .map((input, i) => {
                    const pin = bindings[input.entity],
                      available = state.records.filter(
                        (r) => r.kind === input.entity,
                      );
                    return `<label class="field"><span>${esc(input.label)}</span><select data-atlas-input="${i}"><option value="">暂不绑定</option>${pin ? `<option selected value="pinned">当前绑定 · 版本 ${esc(pin.version || "待核对")}</option>` : ""}${available.map((r) => `<option value="${esc(r.id)}">${esc(recordLabel(r))} · v${r.version}</option>`).join("")}</select></label>`;
                  })
                  .join("")}</div></details>`
              : ""
          }
          <details class="feature-inputs"><summary>人工锁与高级绑定</summary><div class="form-stack">${area("输入绑定", "bindings", pretty(bindings), 'class="code-input"')}${area("人工锁", "locks", pretty(locks), 'class="code-input"')}</div></details>
          <div id="feature-execution" hidden><h3>执行参数</h3><div id="feature-execution-fields"></div></div>
          <div class="feature-bottom"><span id="feature-state" role="status"></span><div class="heading-actions">${btn("保存配置", "save-feature", "check", "button secondary")}${btn("执行", "run-feature", "play", "button primary", "hidden")}</div></div>
        </form>
        <div id="feature-result" aria-live="polite"></div>
        ${outputs.length ? `<section class="feature-output"><div class="surface-title"><h3>项目相关记录</h3>${outputs.some((o) => !READ_ONLY_KINDS.has(o.entity)) ? btn("录入记录", "entity", "plus", "button ghost small") : ""}</div><div id="feature-records"></div></section>` : ""}
      </section>
    </div></div>`;
  const form = $("#feature-form"),
    routeSelect = $("#feature-route");
  let selectedRoute = route.id,
    operationUI;
  const capabilities = state.executionCapabilities?.handlers || {};
  const recordsSection = () => {
    const el = $("#feature-records");
    if (!el) return;
    const related = state.records
      .filter((r) => outputs.some((o) => o.entity === r.kind))
      .slice(0, 8);
    el.innerHTML = related.length
      ? related
          .map(
            (r) =>
              `<button class="list-row" data-action="feature-record" data-id="${esc(r.id)}">${icon("file")}<span class="list-row-body"><strong>${esc(recordLabel(r))}</strong><small>${date(r.updated_at || r.created_at)}</small></span><span>v${r.version}</span>${icon("chevron")}</button>`,
          )
          .join("")
      : '<p class="subtle-empty">暂无对应记录</p>';
  };
  recordsSection();
  mountControls(form, controls, cfg.parameters || {}, {
    records: state.records,
    importFiles: async (files) => {
      const pid = requireProject(),
        refs = [];
      for (const file of files) {
        const result = await uploadFile(api, pid, file);
        refs.push(ref(result.asset));
      }
      await reloadData();
      return refs;
    },
  });
  function drawRoute() {
    const current = f.routes.find((r) => r.id === selectedRoute),
      cap = capabilities[current.id];
    $("#feature-route-detail").textContent = current.label;
    $("#feature-execution").hidden = !cap;
    const run = $("[data-action=run-feature]", form);
    run.hidden = !cap;
    $("#feature-state").textContent = cap
      ? "可在本地执行"
      : direct
        ? "此方案可配置，专项操作见上方"
        : "此方案仅支持配置";
    operationUI = cap
      ? mountExecutionControls(
          $("#feature-execution-fields"),
          cap,
          executions[current.id] || {},
          {
            records: state.records,
            upstream: [],
            capabilities,
            feature: () => f,
          },
        )
      : null;
  }
  const collect = () => {
    if (operationUI) executions[selectedRoute] = operationUI.collect();
    return {
      route_id: selectedRoute,
      parameters: readTypedControls(form),
      input_bindings: parseJSON(form.elements.bindings.value, "输入绑定"),
      locks: parseJSON(form.elements.locks.value, "人工锁"),
      executions,
    };
  };
  const remember = () => {
    try {
      featureDrafts.set(key, collect());
    } catch {
      /* Keep the last valid local values while typing. */
    }
  };
  const save = async () => {
    const data = collect();
    await saveFeature(f, data.route_id, {
      ...data.parameters,
      __input_bindings: data.input_bindings,
      __locks: data.locks,
      __executions: data.executions,
    });
    featureDrafts.delete(key);
    return data;
  };
  drawRoute();
  routeSelect.onchange = () => {
    if (operationUI)
      executions[selectedRoute] = operationUI.collect({ validate: false });
    selectedRoute = routeSelect.value;
    drawRoute();
    remember();
  };
  form.addEventListener("submit", (e) => e.preventDefault());
  form.addEventListener("input", remember);
  form.addEventListener("change", remember);
  form.addEventListener("controlchange", remember);
  $$("[data-atlas-input]", form).forEach(
    (input) =>
      (input.onchange = () => {
        const current = parseJSON(form.elements.bindings.value, "输入绑定"),
          typed = inputs[+input.dataset.atlasInput];
        if (input.value === "pinned")
          current[typed.entity] = bindings[typed.entity];
        else if (input.value)
          current[typed.entity] = ref(
            state.records.find((r) => r.id === input.value),
          );
        else delete current[typed.entity];
        form.elements.bindings.value = pretty(current);
        remember();
      }),
  );
  bind({
    modules: modulePicker,
    feature: (button) => {
      if (button.dataset.id === f.id) return;
      remember();
      history.replaceState(null, "", `#module/${m.id}/${button.dataset.id}`);
      modulePage(m.id, button.dataset.id);
    },
    "feature-action": () => direct?.[1](),
    "feature-record": (b) =>
      openRecord(state.records.find((r) => r.id === b.dataset.id)),
    entity: () =>
      newEntity(outputs.find((o) => !READ_ONLY_KINDS.has(o.entity))?.entity),
    "save-feature": async (b) => {
      setBusy(b, true, "保存中");
      try {
        await save();
        toast("配置已保存");
      } finally {
        setBusy(b, false);
      }
    },
    "run-feature": async (b) => {
      const pid = requireProject();
      setBusy(b, true, "执行中");
      try {
        const config = await save(),
          execution = config.executions[config.route_id];
        if (!execution) throw new Error("请选择已接通的处理方案");
        const spec = {
          name: f.label,
          feature_operation: f.id,
          nodes: [
            {
              id: "operation",
              feature_id: f.id,
              route_id: config.route_id,
              parameters: config.parameters,
              ...execution,
            },
          ],
          edges: [],
          max_steps: 1,
          max_attempts: 1,
          budget: 0,
        };
        const check = await api("/workflows/validate", {
          method: "POST",
          body: spec,
        });
        if (!check.valid)
          throw new Error(
            arr(check.errors)
              .map((e) => e.message || e.label || e.code)
              .join("、") || "参数需要修正",
          );
        const slot = `ship-feature-run:${pid}:${f.id}`;
        let pending = JSON.parse(sessionStorage.getItem(slot) || "null");
        const signature = JSON.stringify(spec);
        if (pending && pending.signature !== signature)
          throw new Error("上次请求尚未核对，恢复原方案后可继续查询");
        if (!pending) {
          const refs = Object.values(execution.input_bindings)
            .filter((x) => x.record_id)
            .map((x) => ({ id: x.record_id, version: x.version }));
          const flow = await saveRecord(
            "Workflow",
            spec,
            null,
            refs,
            "保存单项操作的固定输入与参数",
          );
          pending = {
            signature,
            id: flow.id,
            version: flow.version,
            key: uid(),
          };
          sessionStorage.setItem(slot, JSON.stringify(pending));
        }
        const result = await api("/execution/start", {
          method: "POST",
          body: {
            workflow_id: pending.id,
            expected_revision: pending.version,
            idempotency_key: pending.key,
          },
        });
        sessionStorage.removeItem(slot);
        await reloadData();
        if (!form.isConnected || state.projectId !== pid) return;
        const node = result.run?.data?.checkpoint?.nodes?.operation;
        $("#feature-result").innerHTML = runSummary(result, node);
        recordsSection();
      } finally {
        setBusy(b, false);
      }
    },
  });
}

function runSummary(result, node) {
  const data = result.run?.data || {};
  const status = result.status || data.status || "pending";
  const nodes = node ? [node] : Object.values(data.checkpoint?.nodes || {});
  const problems = nodes
    .map(
      (n) =>
        n.error?.message ||
        n.blocker?.message ||
        n.blockers?.map((b) => b.message || b.code).join("、") ||
        n.error?.code,
    )
    .filter(Boolean);
  const refs = arr(data.output_refs || result.output_refs);
  return `<section class="run-summary"><h3>${esc(statusLabel(status))}</h3>${problems.map((p) => `<p>${esc(p)}</p>`).join("")}${refs.map((item) => `<p>${icon("file")}${esc(recordLabel(state.records.find((r) => r.id === item.id) || { kind: item.kind }))}</p>`).join("")}</section>`;
}

function assetMedia(r, controls = false) {
  const type = mediaType(r),
    url = ctx.assetUrl(r);
  if (type.startsWith("image") || type === "image")
    return `<img src="${url}" alt="${esc(r.data.name || r.data.filename)}" loading="lazy">`;
  if (type.startsWith("audio") || type === "audio")
    return controls
      ? `<audio controls src="${url}" preload="metadata"></audio>`
      : icon("music");
  return `<video src="${url}#t=0.01" ${controls ? "controls" : "muted"} preload="metadata" playsinline ${controls ? "" : 'aria-label="' + esc(r.data.name) + '预览"'}></video>`;
}
const assetDuration = mediaDuration;
function assetBytes(r) {
  const bytes =
    r.data.size_bytes ??
    state.records.find((x) => x.id === r.data.blob_location_id)?.data
      .size_bytes;
  return Number.isFinite(bytes) ? humanBytes(bytes) : "—";
}
async function assets() {
  const count = $("#asset-count");
  if (count) count.textContent = mediaRecords().length || "";
  const list = mediaRecords().filter((r) => {
    const type = mediaType(r);
    return (
      (state.assetType === "all" || type === state.assetType) &&
      [mediaName(r), mediaVariant(r), r.id]
        .join(" ")
        .toLowerCase()
        .includes(state.assetQuery.toLowerCase())
    );
  });
  $("#main").innerHTML =
    `<div class="page">${pageHead("MEDIA LIBRARY", "素材库", "原始文件保持不变。每次处理都会生成独立版本。", `${btn("文件夹", "folder-import", "folder", "button secondary")}${btn("导入素材", "import", "plus", "button primary")}`)}<input type="file" id="media-input" multiple accept="video/*,audio/*,image/*" hidden><input type="file" id="folder-input" multiple webkitdirectory hidden><div class="toolbar"><div class="search-field">${icon("search")}<input id="asset-search" value="${esc(state.assetQuery)}" placeholder="搜索素材名称" aria-label="搜索素材"></div><div class="segmented" role="group" aria-label="素材类型">${[
      ["all", "全部"],
      ["video", "视频"],
      ["image", "图片"],
      ["audio", "音频"],
    ]
      .map(
        ([v, l]) =>
          `<button aria-pressed="${state.assetType === v}" data-action="asset-filter" data-value="${v}">${l}</button>`,
      )
      .join(
        "",
      )}</div><span class="toolbar-spacer"></span><span class="muted small">${list.length} 件素材</span></div><div id="import-progress" class="import-list"></div><div id="asset-list">${list.length ? `<div class="asset-grid">${list.map((r) => `<button class="asset-card" data-action="asset-detail" data-id="${esc(r.id)}"><div class="asset-preview">${assetMedia(r)}<span class="asset-type">${esc({ image: "图片", video: "视频", audio: "音频" }[mediaType(r)] || "文件")} · ${esc(mediaVariant(r))}</span>${assetDuration(r) ? `<span class="asset-duration">${time(assetDuration(r))}</span>` : ""}</div><h3>${esc(mediaName(r))}</h3><p><span>${assetBytes(r)}</span><span>${date(r.created_at)}</span></p></button>`).join("")}</div>` : state.assetQuery || state.assetType !== "all" ? empty("没有匹配的素材", "试着改变搜索词或素材类型。", "", "", "search") : `<div class="dropzone" id="dropzone">${icon("upload")}<h3>拖入素材</h3><p>视频、音频或图片</p>${btn("选择文件", "import", "plus", "button primary")}<p class="small" style="margin:15px 0 0">每个文件最多 256 MiB</p></div>`}</div></div>`;
  const choose = (folder) => {
    if (!project()) {
      newProject();
      return;
    }
    $(folder ? "#folder-input" : "#media-input").click();
  };
  $("#media-input").onchange = (e) => importFiles(e.target.files);
  $("#folder-input").onchange = (e) => importFiles(e.target.files);
  let searchTimer;
  $("#asset-search").oninput = (e) => {
    state.assetQuery = e.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => assets(), 300);
  };
  state.cleanup.push(() => clearTimeout(searchTimer));
  state.assetDragCleanup?.();
  const zone = $("#main");
  const dragover = (e) => {
    if (e.dataTransfer?.types.includes("Files")) {
      e.preventDefault();
      $("#dropzone")?.classList.add("dragover");
    }
  };
  const drop = (e) => {
    if (e.dataTransfer?.files.length) {
      e.preventDefault();
      $("#dropzone")?.classList.remove("dragover");
      importFiles(e.dataTransfer.files);
    }
  };
  zone.addEventListener("dragover", dragover);
  zone.addEventListener("drop", drop);
  state.assetDragCleanup = () => {
    zone.removeEventListener("dragover", dragover);
    zone.removeEventListener("drop", drop);
  };
  state.cleanup.push(state.assetDragCleanup);
  bind({
    import: () => choose(false),
    "folder-import": () => choose(true),
    "asset-filter": (b) => {
      state.assetType = b.dataset.value;
      assets();
    },
    "asset-detail": (b) =>
      assetDetail(mediaRecords().find((r) => r.id === b.dataset.id)),
    module: (b) => nav(`module/${b.dataset.id}`),
  });
  drawImport();
}
function drawImport() {
  const el = $("#import-progress");
  if (!el) return;
  el.innerHTML = state.importQueue
    .map(
      (x, i) =>
        `<div class="import-entry">${["importing", "verifying"].includes(x.status) ? '<span class="spinner"></span>' : icon(x.status === "failed" ? "warning" : x.status === "done" ? "checkcircle" : "file")}<span>${esc(x.name)}</span>${badge(x.status === "importing" ? `上传 ${x.total ? Math.round(((x.offset || 0) / x.total) * 100) : 0}%` : x.status === "verifying" ? "核验入库" : x.status === "done" ? (x.duplicate ? "已识别重复" : "已入库") : x.status === "failed" ? "失败" : x.status === "paused" ? "已暂停" : x.status === "cancelled" ? "已取消" : "等待", x.status === "failed" ? "red" : x.status === "done" ? "green" : "")}<small class="muted">${esc(x.error || "")}</small>${x.status === "importing" ? `<button class="icon-button" type="button" data-import-pause="${i}" aria-label="暂停上传">${icon("pause")}</button>` : ["paused", "failed"].includes(x.status) ? `<button class="icon-button" type="button" data-import-resume="${i}" aria-label="恢复上传">${icon("play")}</button>` : ""}${!["done", "cancelled"].includes(x.status) ? `<button class="icon-button" type="button" data-import-cancel="${i}" aria-label="取消上传">${icon("close")}</button>` : ""}</div>`,
    )
    .join("");
  $$("[data-import-pause]", el).forEach(
    (b) =>
      (b.onclick = () => {
        state.importQueue[+b.dataset.importPause].pauseRequested = true;
        toast("当前分块确认后暂停，已接收字节会保留。");
      }),
  );
  $$("[data-import-resume]", el).forEach(
    (b) =>
      (b.onclick = () => {
        const row = state.importQueue[+b.dataset.importResume];
        row.pauseRequested = false;
        row.status = "waiting";
        runImportQueue();
      }),
  );
  $$("[data-import-cancel]", el).forEach(
    (b) =>
      (b.onclick = async () => {
        const row = state.importQueue[+b.dataset.importCancel];
        row.cancelRequested = true;
        if (!["importing", "verifying"].includes(row.status)) {
          try {
            if (row.entry_id)
              await api(`/media/uploads/${row.entry_id}`, { method: "DELETE" });
            row.status = "cancelled";
            drawImport();
          } catch (err) {
            toast(err.message, "error");
          }
        }
      }),
  );
}
async function importFiles(files) {
  try {
    requireProject();
    if (state.importBusy) {
      toast("导入队列正在处理，请稍候再添加。", "error");
      return;
    }
    state.importQueue = Array.from(files).map((file) => ({
      file,
      name: file.webkitRelativePath || file.name,
      status: "waiting",
      offset: 0,
      total: file.size,
    }));
    await runImportQueue();
  } catch (e) {
    toast(e.message, "error");
  }
}
async function runImportQueue() {
  if (state.importBusy) return;
  state.importBusy = true;
  try {
    for (const row of state.importQueue) {
      if (row.status !== "waiting") continue;
      if (row.cancelRequested) {
        row.status = "cancelled";
        continue;
      }
      row.status = "importing";
      row.error = "";
      drawImport();
      try {
        const r = await uploadFile(api, state.projectId, row.file, {
          onProgress: (progress) => {
            Object.assign(row, progress);
            drawImport();
          },
          shouldPause: () => row.pauseRequested,
          shouldCancel: () => row.cancelRequested,
        });
        if (r.paused) row.status = "paused";
        else if (r.cancelled) row.status = "cancelled";
        else {
          row.status = "done";
          row.duplicate = r.duplicate;
        }
      } catch (error) {
        row.status = "failed";
        row.error = error.message;
      }
      drawImport();
    }
    await reloadData();
    if (location.hash === "#assets") await assets();
    else if (location.hash.startsWith("#module/")) await renderRoute();
    const done = state.importQueue.filter((x) => x.status === "done").length,
      failed = state.importQueue.filter((x) => x.status === "failed").length;
    if (failed)
      toast(`${done} 件入库，${failed} 件失败；可按原记录恢复。`, "error");
    else if (done) toast(`${done} 件素材已入库，原件和来源已保留。`);
  } finally {
    state.importBusy = false;
  }
}
function assetDetail(r) {
  if (!r) return;
  const probe = r.data.probe || {},
    d = modal(
      mediaName(r),
      `<div class="media-detail"><div class="media-detail-preview">${assetMedia(r, true)}</div><div><div class="surface-title"><h3>${esc(mediaVariant(r))}</h3>${badge("已入库", "green")}</div><dl class="detail-facts"><div><dt>时长</dt><dd>${assetDuration(r) ? time(assetDuration(r)) : "静态内容 / 未知"}</dd></div><div><dt>文件大小</dt><dd>${assetBytes(r)}</dd></div><div><dt>画面尺寸</dt><dd>${probe.width && probe.height ? `${esc(probe.width)} × ${esc(probe.height)}` : "—"}</dd></div><div><dt>来源声明</dt><dd>${esc(r.data.rights_status || "待核对")}</dd></div></dl><div class="form-actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:20px">${btn("加入剪辑", "edit", "film", "button primary")}${btn("处理素材", "transform", "sliders", "button secondary")}${btn("提取音频", "extract", "music", "button secondary", probe.has_audio ? "" : "disabled")}${btn("版本与来源", "history", "history", "button ghost")}</div></div></div><details class="source-details"><summary>媒体探测与来源记录</summary><pre>${esc(pretty(r.data))}</pre></details>`,
      null,
      true,
    );
  onAction(d, {
    edit: () => {
      sessionStorage.setItem("ship-add-asset", r.id);
      d.close();
      nav("editor");
    },
    transform: () => transformAsset(r),
    history: () => showHistory(r),
    extract: async (b) => {
      setBusy(b, true);
      try {
        const out = await api(
          `/media/${encodeURIComponent(r.id)}/extract-audio`,
          {
            method: "POST",
            body: { project_id: state.projectId, expected_revision: r.version },
          },
        );
        toast("音频已提取，保存了独立来源记录。");
        await reloadData();
        if (location.hash === "#assets") await assets();
        assetDetail(out.audio);
      } finally {
        setBusy(b, false);
      }
    },
  });
}
function transformAsset(r) {
  const fields = [
    ["trim_start", "入点（秒）", 0, 0, assetDuration(r)],
    ["trim_end", "出点（秒）", assetDuration(r), 0, assetDuration(r)],
    ["scale", "画面缩放", 1.0165, 1, 4],
    ["brightness", "亮度", 0, -1, 1],
    ["contrast", "对比度", 1, 0, 3],
    ["saturation", "饱和度", 1, 0, 3],
    ["sharpness", "锐化", 0, 0, 2],
    ["speed", "速度", 1, 0.25, 4],
    ["volume", "音量倍率", 1, 0, 4],
  ];
  modal(
    "逐素材处理",
    `<form data-form class="form-stack"><div class="form-grid">${fields.map(([k, l, v, min, max]) => field(l, k, v, "number", `step="any" min="${min}" ${max ? 'max="' + max + '"' : ""}`)).join("")}${select(
      "旋转",
      "rotation",
      [
        [0, "不旋转"],
        [90, "顺时针 90°"],
        [180, "180°"],
        [270, "270°"],
      ],
    )}${field("响度目标（LUFS，留空不处理）", "loudness_lufs", "", "number", 'min="-70" max="-5" step="0.1"')}</div><div class="form-actions"><button type="submit" class="button primary">生成处理版本</button></div></form>`,
    async (f) => {
      const input = formData(f),
        recipe = { origin: "atlas_template" };
      for (const [k, v] of Object.entries(input))
        if (v !== "") recipe[k] = Number(v);
      await api(`/media/${encodeURIComponent(r.id)}/transform`, {
        method: "POST",
        body: {
          project_id: state.projectId,
          recipe,
          expected_revision: r.version,
        },
      });
      toast("衍生素材已生成，原件保持不变。");
      await renderRoute();
    },
  );
}

async function agents() {
  const profiles = records("ModelProfile"),
    roles = records("AgentRole"),
    catalogRoles = arr(state.catalog.agentSystem?.roles);
  $("#main").innerHTML =
    `<div class="page">${pageHead("AGENT WORKSPACE", "Agent 团队", "按职责组合模型与工具。配置、能力验证和实际运行各自保留状态。", `${btn("模型配置", "model-list", "sliders", "button secondary")}${btn("添加模型", "new-model", "plus", "button secondary")}${btn("运行团队", "run-team", "play", "button primary")}`)}<div class="section-heading"><div><h2>团队角色</h2></div>${btn("团队部署", "deployment", "agents", "button secondary")}</div><div class="role-grid">${catalogRoles
      .map((r, i) => {
        const configured = roles.find((x) => x.data.catalog_role_id === r.id),
          profile = profiles.find(
            (p) => p.id === configured?.data.default_model_profile_ref,
          );
        return `<section class="role-card"><div class="role-top"><div class="role-symbol">${icon(["spark", "box", "volume", "music", "film", "layers", "send", "chart", "eye", "settings", "history"][i] || "agents")}</div>${badge(configured ? "已配置" : "待配置", configured ? "blue" : "")}</div><h3>${esc(r.label)}</h3><div class="role-bottom"><span>${profile ? esc(profile.data.model_id) : "尚未绑定模型"}</span>${btn("配置", "role", "sliders", "button small secondary", `data-id="${r.id}"`)}</div></section>`;
      })
      .join("")}</div></div>`;
  bind({
    "new-model": () => modelForm(),
    "model-list": modelList,
    role: (b) => roleForm(catalogRoles.find((r) => r.id === b.dataset.id)),
    deployment: deploymentForm,
    "run-team": teamForm,
    module: (b) => nav(`module/${b.dataset.id}`),
  });
}
function modelForm(existing, preset = null) {
  const pid = requireProject();
  const d = existing?.data || preset || {};
  modal(
    existing ? "编辑模型配置" : "添加模型配置",
    `<form data-form class="form-stack"><div class="form-grid">${field("配置名称", "name", d.name || "", "", "required")}${field("供应商", "provider", d.provider || "", "", 'required placeholder="兼容接口 / 本地服务"')}${field("模型标识", "model_id", d.model_id || "", "", 'required placeholder="以供应商实际标识为准"')}${field("服务端地址引用", "endpoint_ref", d.endpoint_ref || "", "", 'required placeholder="配置中的 endpoint 引用"')}${field("服务端凭据引用", "credential_ref", d.credential_ref || "", "", 'placeholder="只填写引用名称，不填写密钥"')}${field("上下文上限", "context_limit", d.context_limit || 32768, "number", 'min="1"')}${field("请求超时（毫秒）", "timeout_ms", d.timeout_ms || 60000, "number", 'min="1000" max="3600000"')}${field("最大尝试次数", "max_attempts", d.retry_policy?.max_attempts || 2, "number", 'min="1" max="10"')}</div>${area("采样参数（仅填写该模型支持的参数）", "sampling_parameters", pretty(d.sampling_parameters || {}), 'class="code-input"')}${area("模态列表（JSON）", "modalities", pretty(d.modalities || ["text"]), 'class="code-input"')}<p class="help-text"></p><div class="form-actions"><button class="button primary" type="submit">保存配置版本</button></div></form>`,
    async (f) => {
      const raw = formData(f),
        data = {
          ...d,
          ...raw,
          credential_ref: raw.credential_ref || null,
          context_limit: +raw.context_limit,
          timeout_ms: +raw.timeout_ms,
          sampling_parameters: parseJSON(raw.sampling_parameters),
          modalities: parseJSON(raw.modalities),
          retry_policy: { max_attempts: +raw.max_attempts },
          health_status: "unprobed",
          status: "draft",
        };
      delete data.max_attempts;
      if (state.projectId !== pid)
        throw new Error("项目已切换，请重新选择模型。");
      if (
        preset &&
        records("ModelProfile").some(
          (p) =>
            (p.data.endpoint_ref || p.data.provider) === data.endpoint_ref &&
            p.data.model_id === data.model_id,
        )
      )
        throw new Error("这个模型已经绑定到项目，请编辑已有的配置。");
      await saveRecord("ModelProfile", data, existing);
      toast("模型配置已保存，状态为未探测。");
      await renderRoute();
    },
  );
}
async function modelList() {
  const pid = requireProject();
  const profiles = records("ModelProfile");
  const d = modal(
    "模型配置",
    `<div>${
      records("ModelProfile")
        .map(
          (p) =>
            `<button class="list-row" data-model="${p.id}">${icon("agents")}<span class="list-row-body"><strong>${esc(p.data.name || p.data.model_id)}</strong><p>${esc(p.data.provider)} · ${esc(p.data.model_id)}</p></span>${badge(statusLabel(p.data.health_status || "unprobed"))}${icon("chevron")}</button>`,
        )
        .join("") || '<p class="help-text">此项目尚未绑定模型。</p>'
    }</div><section data-available-models class="form-stack" aria-label="服务器已登记模型"><p role="status" class="help-text">正在读取可绑定模型…</p></section><div class="dialog-actions">${btn("验证模型调用", "try-model", "play", "button secondary")}${btn("手动添加配置", "add", "plus", "button secondary")}</div>`,
  );
  d.querySelectorAll("[data-model]").forEach(
    (b) =>
      (b.onclick = () =>
        modelForm(
          records("ModelProfile").find((p) => p.id === b.dataset.model),
        )),
  );
  d.querySelector("[data-action=add]").onclick = () => modelForm();
  const test = d.querySelector("[data-action=try-model]");
  test.disabled = !profiles.length;
  test.onclick = modelTryForm;
  const holder = d.querySelector("[data-available-models]");
  try {
    const result = await api("/providers/availability");
    if (state.projectId !== pid || !d.open || !holder.isConnected) return;
    const available = [];
    const seen = new Set(
      profiles.map((p) =>
        JSON.stringify([
          p.data.endpoint_ref || p.data.provider,
          p.data.model_id,
        ]),
      ),
    );
    for (const provider of arr(result.providers)) {
      if (!provider.configured) continue;
      for (const model of arr(provider.models)) {
        const key = JSON.stringify([provider.id, model]);
        if (typeof model !== "string" || !model || seen.has(key)) continue;
        seen.add(key);
        available.push({
          provider: provider.id,
          model,
          modalities: provider.modalities,
        });
      }
    }
    if (!available.length) {
      holder.innerHTML =
        '<p class="help-text">暂无可新增绑定的服务端模型。</p>';
      return;
    }
    holder.innerHTML =
      select(
        "可绑定的服务端模型",
        "available_model",
        available.map((item, i) => [
          String(i),
          item.provider + " · " + item.model,
        ]),
        "0",
      ) +
      btn("绑定到当前项目", "bind-available-model", "plus", "button primary");
    holder.querySelector("button").onclick = () => {
      const item = available[Number(holder.querySelector("select").value)];
      if (!item || state.projectId !== pid) return;
      modelForm(null, {
        name: item.model,
        provider: item.provider,
        endpoint_ref: item.provider,
        model_id: item.model,
        modalities: item.modalities,
      });
    };
  } catch (err) {
    if (d.open && holder.isConnected)
      holder.textContent = "读取可绑定模型失败。" + err.message;
  }
}
function roleForm(role) {
  const old = records("AgentRole").find(
      (r) => r.data.catalog_role_id === role.id,
    ),
    d = old?.data || {};
  modal(
    role.label,
    `<p class="dialog-desc">${esc(role.mission)}</p><form data-form class="form-stack">${select("默认模型配置", "default_model_profile_ref", [["", "未绑定 / 人工作业"], ...records("ModelProfile").map((p) => [p.id, `${p.data.name || p.data.model_id} · ${p.data.provider}`])], d.default_model_profile_ref || "")}${select(
      "职责类型",
      "role_kind",
      [
        ["agent", "Agent"],
        ["rule", "确定性规则"],
        ["human", "人工"],
        ["hybrid", "混合"],
      ],
      d.role_kind || "agent",
    )}${field("独立性分组", "independence_group", d.independence_group || role.id)}${area("允许动作（JSON）", "allowed_actions", pretty(d.allowed_actions || role.tools || []), 'class="code-input"')}${area("费用与轮次上限（JSON）", "budget_policy", pretty(d.budget_policy || { max_rounds: 3, max_cost: 0, max_delegation_depth: 2 }), 'class="code-input"')}<div class="info-banner">${icon("lock")}<span>${esc(role.authority)}</span></div><div class="form-actions"><button class="button primary" type="submit">保存职责</button></div></form>`,
    async (f) => {
      const raw = formData(f),
        profile = records("ModelProfile").find(
          (p) => p.id === raw.default_model_profile_ref,
        );
      await saveRecord(
        "AgentRole",
        {
          ...d,
          ...raw,
          catalog_role_id: role.id,
          label: role.label,
          objective: role.mission,
          allowed_actions: parseJSON(raw.allowed_actions),
          budget_policy: parseJSON(raw.budget_policy),
          status: "draft",
        },
        old,
        profile ? [ref(profile)] : [],
      );
      toast("职责配置已保存");
      await renderRoute();
    },
  );
}

function deploymentForm() {
  requireProject();
  const roles = records("AgentRole");
  if (!roles.length) throw new Error("请先配置至少一个真实职责。");
  modal(
    "配置团队部署",
    `<form data-form class="form-stack">${field("部署名称", "name", "", "text", "required")}<div>${roles.map((r) => `<label class="switch-row"><span>${esc(r.data.label)}<small>${esc(records("ModelProfile").find((p) => p.id === r.data.default_model_profile_ref)?.data.model_id || "人工作业 / 尚未绑定模型")}</small></span><span class="switch"><input type="checkbox" name="role_ids" value="${r.id}" aria-label="纳入 ${esc(r.data.label)}"><i></i></span></label>`).join("")}</div><div class="form-grid">${select(
      "协作模式",
      "team_mode",
      [
        ["heterogeneous_parallel", "异构并行"],
        ["heterogeneous_serial", "异构串行"],
        ["heterogeneous_human_assisted", "异构与人工协作"],
      ],
    )}${field("最多协作轮次", "max_rounds", 3, "number", 'required min="1" max="20"')}${field("最多委派层数", "max_delegation_depth", 2, "number", 'required min="0" max="5"')}${field("并发上限", "concurrency", 2, "number", 'required min="1" max="20"')}${field("Token 上限", "token_budget", 12000, "number", 'required min="1"')}${field("总费用上限（USD）", "cost_budget", 0, "number", 'required min="0" step="0.01"')}</div>${area("停止条件与未决项处理", "stop_conditions", "", 'required placeholder="预算耗尽、权限失效、无进展、目标已满足时分别如何收束"')}<div class="form-actions"><button class="button primary" type="submit">保存部署版本</button></div></form>`,
    async (f) => {
      const raw = formData(f),
        ids = new FormData(f).getAll("role_ids"),
        chosen = roles.filter((r) => ids.includes(r.id));
      if (!chosen.length) throw new Error("至少选择一个职责");
      delete raw.role_ids;
      const profiles = chosen
        .map((r) =>
          records("ModelProfile").find(
            (p) => p.id === r.data.default_model_profile_ref,
          ),
        )
        .filter(Boolean);
      await saveRecord(
        "AgentDeployment",
        {
          ...raw,
          max_rounds: +raw.max_rounds,
          max_delegation_depth: +raw.max_delegation_depth,
          concurrency: +raw.concurrency,
          token_budget: +raw.token_budget,
          cost_budget: +raw.cost_budget,
          role_bindings: chosen.map((r) => ({
            role_ref: r.id,
            model_profile_ref: r.data.default_model_profile_ref || null,
            tool_refs: [],
            fallback_refs: [],
          })),
          model_profile_refs: profiles.map(ref),
          independence_groups: chosen.map((r) => ({
            role_ids: [r.id],
            group: r.data.independence_group,
            evidence_provenance_policy: "explicit_source_refs",
            blind_phase: true,
          })),
          status: "draft",
        },
        null,
        [...chosen, ...profiles].map(ref),
      );
      toast("部署已保存，实际执行仍需能力验证。");
      await renderRoute();
    },
  );
}
function modelTryForm() {
  requireProject();
  if (!records("ModelProfile").length) throw new Error("先保存模型配置。");
  const key = uid();
  modal(
    "验证真实模型调用",
    `<form data-form class="form-stack">${select(
      "模型配置",
      "model_profile_id",
      records("ModelProfile").map((p) => [
        p.id,
        `${p.data.name || p.data.model_id} · ${p.data.provider}`,
      ]),
    )}${area("验证任务", "prompt", "", 'required placeholder="使用你要验证的具体任务检查模型输出"')}<div class="form-grid">${field("输出 Token 上限", "max_tokens", 512, "number", 'required min="16" max="32768"')}${field("费用上限（USD）", "max_cost", "", "number", 'required min="0.000001" step="any"')}</div><p class="help-text">只有服务端已配置凭据、模型白名单与价格时才会真正调用。请求和返回标识、Token 与未知结果会保留。</p><div class="form-actions"><button class="button primary" type="submit">发起真实调用</button></div></form>`,
    async (f) => {
      const raw = formData(f),
        r = await api("/providers/chat", {
          method: "POST",
          body: {
            project_id: state.projectId,
            model_profile_id: raw.model_profile_id,
            messages: [{ role: "user", content: raw.prompt }],
            max_tokens: +raw.max_tokens,
            max_cost: +raw.max_cost,
            idempotency_key: key,
          },
        });
      await reloadData();
      setTimeout(
        () =>
          modal(
            "模型实际响应",
            `<div class="info-banner">${icon("info")}<span>${esc(statusLabel(r.status))}</span></div>${r.result?.content ? `<p class="model-response">${esc(r.result.content)}</p>` : ""}<details class="source-details"><summary>技术详情</summary><pre>${esc(pretty(r))}</pre></details>`,
            null,
            true,
          ),
        20,
      );
    },
  );
}
function teamForm() {
  requireProject();
  const profiles = records("ModelProfile");
  if (profiles.length < 2)
    throw new Error("异构团队至少需要两个实际模型配置；请先添加。");
  const key = uid();
  modal(
    "运行有界协作团队",
    `<form data-form class="form-stack">${area("本次目标", "goal", "", 'required placeholder="清晰说明任务目标、输出要求、不能改动的事实"')}<div class="form-grid">${[
      ["planner", "规划角色"],
      ["creator", "创作角色"],
      ["judge", "独立裁决"],
    ]
      .map(
        ([r, l]) =>
          `<div>${select(l, r, [["", "选择模型"], ...profiles.map((p) => [p.id, `${p.data.model_id} · ${p.data.provider}`])], "", "required")}${field("独立证据组", r + "_group", r === "judge" ? "review" : "creation", "text", "required")}</div>`,
      )
      .join(
        "",
      )}</div><label class="field"><span>本次证据记录（可多选）</span><select name="evidence_refs" multiple required size="5">${state.records
      .filter((r) => !["View", "Run", "Cost"].includes(r.kind))
      .map(
        (r) =>
          `<option value="${r.id}">${esc(r.data.name || r.data.title || r.data.label || r.kind)} · ${esc(r.kind)}</option>`,
      )
      .join(
        "",
      )}</select></label><div class="form-grid">${field("每个角色最大输出 Token", "max_tokens_per_role", 1024, "number", 'min="16" max="8192" required')}${field("总费用上限（USD）", "max_cost", "", "number", 'min="0.000001" step="any" required')}</div><p class="help-text">先独立生成候选，再以真实证据裁决。当前执行器为一次有界协作；不会自动发布、扩权或假定模型换名就足够独立。</p><div class="form-actions"><button type="submit" class="button primary">运行团队</button></div></form>`,
    async (f) => {
      const raw = formData(f),
        result = await api("/providers/team", {
          method: "POST",
          body: {
            project_id: state.projectId,
            goal: raw.goal,
            roles: ["planner", "creator", "judge"].map((role) => ({
              role,
              model_profile_id: raw[role],
              independence_group: raw[role + "_group"],
              instructions: "",
            })),
            evidence_refs: new FormData(f).getAll("evidence_refs"),
            idempotency_key: key,
            max_tokens_per_role: +raw.max_tokens_per_role,
            max_cost: +raw.max_cost,
          },
        });
      await reloadData();
      setTimeout(
        () =>
          modal(
            "团队运行结果",
            `${runSummary(result)}${arr(result.outputs)
              .map(
                (o) =>
                  `<div class="list-row"><span>${esc(o.role || "团队成员")}</span><span>${esc(statusLabel(o.status))}</span></div>`,
              )
              .join(
                "",
              )}<details class="source-details"><summary>技术详情</summary><pre>${esc(pretty(result))}</pre></details>`,
            null,
            true,
          ),
        20,
      );
    },
    true,
  );
}

async function delivery() {
  const artifacts = records("Artifact"),
    pubs = records("Publication"),
    accounts = records("Account");
  $("#main").innerHTML =
    `<div class="page">${pageHead("DELIVERY & PUBLISHING", "交付与发布", "成片、审阅、交接和平台回执分别确认。", `${btn("管理账号", "accounts", "globe", "button secondary")}${btn("新建发布意图", "new-publication", "plus", "button primary")}`)}<div class="section-heading" style="margin-top:0"><h2>交付产物</h2>${btn("进入剪辑室", "editor", "film", "button ghost")}</div><div class="surface">${artifacts.length ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>交付文件</th><th>规格</th><th>状态</th><th>操作</th></tr></thead><tbody>${artifacts.map((a) => `<tr><td>${esc(a.data.name || a.data.filename || "渲染产物")}<small>${date(a.created_at)}</small></td><td>${esc(a.data.probe?.width ?? a.data.width ?? a.data.profile?.width ?? "—")} × ${esc(a.data.probe?.height ?? a.data.height ?? a.data.profile?.height ?? "—")}<small>${mediaDuration(a) ? time(mediaDuration(a)) : ""}</small></td><td>${badge(statusLabel(a.data.status || "content_review_required"), "amber")}</td><td><div class="heading-actions"><a class="button small secondary" href="/api/v1/media/artifacts/${encodeURIComponent(a.id)}/file" download>${icon("download")}下载</a>${btn("审阅", "approve-artifact", "eye", "button small secondary", `data-id="${a.id}"`)}${iconBtn("查看产物来源", "record", "history", `data-id="${a.id}"`)}</div></td></tr>`).join("")}</tbody></table></div>` : empty("还没有交付文件", "", "editor", "打开剪辑室", "film")}</div><div class="section-heading"><div><h2>发布意图</h2></div>${badge(`${pubs.length} 项`)}</div><div class="surface">${pubs.length ? pubs.map((p) => `<div class="list-row">${icon("send")}<div class="list-row-body"><strong>${esc(p.data.title || p.data.metadata?.title || "未命名发布")}</strong><p>${esc(accounts.find((a) => a.id === p.data.account_id)?.data.name || "账号待核对")} · ${esc(p.data.channel || "manual")}${p.data.scheduled_at ? " · " + date(p.data.scheduled_at) : ""}</p></div>${badge(statusLabel(p.data.status || "draft"), p.data.status === "receipt_recorded" ? "blue" : "")}<div class="heading-actions">${btn("预检", "preflight", "checkcircle", "button small secondary", `data-id="${p.id}"`)}${btn("回填回执", "receipt", "link", "button small secondary", `data-id="${p.id}"`)}</div></div>`).join("") : empty("暂无发布意图", "", "", "", "send")}</div></div>`;
  bind({
    editor: () => nav("editor"),
    accounts: accountList,
    "new-publication": publicationForm,
    preflight: (b) =>
      preflightPublication(pubs.find((p) => p.id === b.dataset.id)),
    receipt: (b) => receiptForm(pubs.find((p) => p.id === b.dataset.id)),
    record: (b) => openRecord(artifacts.find((p) => p.id === b.dataset.id)),
    "approve-artifact": (b) =>
      approveArtifact(artifacts.find((p) => p.id === b.dataset.id)),
    module: (b) => nav(`module/${b.dataset.id}`),
  });
}
function accountList() {
  const d = modal(
    "发布账号",
    `${
      records("Account")
        .map(
          (a) =>
            `<button class="list-row" data-account="${a.id}">${icon("globe")}<span class="list-row-body"><strong>${esc(a.data.name || "账号")}</strong><p>${esc(a.data.provider || a.data.platform || "manual")} · ${esc(a.data.identity || "未验证身份")}</p></span>${badge(statusLabel(a.data.status || "draft"))}</button>`,
        )
        .join("") ||
      empty(
        "还没有账号记录",
        "登记真实账号后，可用于发布意图和指标导入。",
        "",
        "",
        "globe",
      )
    }<div class="dialog-actions">${btn("登记账号", "add", "plus", "button primary")}</div>`,
  );
  d.querySelectorAll("[data-account]").forEach(
    (b) =>
      (b.onclick = () =>
        openRecord(records("Account").find((r) => r.id === b.dataset.account))),
  );
  d.querySelector("[data-action=add]").onclick = () =>
    modal(
      "登记账号",
      `<form data-form class="form-stack">${field("账号名称", "name", "", "text", "required")}${select("平台", "provider", ["tiktok", "youtube", "manual"])}${field("平台账号 ID", "platform_account_id", "", "text", "required")}${field("身份或主页链接", "identity", "", "text", "required")}<p class="help-text">登记账号不是平台授权。API 发布还需要有效授权、账号能力和内容检查。</p><div class="form-actions"><button class="button primary" type="submit">保存账号</button></div></form>`,
      async (f) => {
        await saveRecord("Account", {
          ...formData(f),
          status: "draft",
          authorization_status: "unconnected",
        });
        toast("账号已登记");
        await renderRoute();
      },
    );
}
function publicationForm(selectedArtifact = null) {
  requireProject();
  const candidates =
    selectedArtifact?.kind === "Artifact"
      ? [selectedArtifact]
      : records("Artifact");
  if (!candidates.length) throw new Error("请先渲染实际交付文件。");
  if (!records("Account").length) {
    accountList();
    return;
  }
  modal(
    "新建发布意图",
    `<form data-form class="form-stack">${select(
      "成片",
      "artifact_id",
      candidates.map((r) => [
        r.id,
        r.data.name || r.data.filename || "未命名文件",
      ]),
    )}${select(
      "账号",
      "account_id",
      records("Account").map((r) => [r.id, recordLabel(r)]),
    )}${area("发布标题与文案", "title", "", 'required maxlength="2200"')}${select(
      "发布通道",
      "channel",
      [
        ["manual", "人工交接"],
        ["tiktok_direct", "TikTok 直接发布"],
        ["tiktok_inbox", "TikTok 收件箱草稿"],
      ],
    )}<div class="form-grid">${select("可见性", "privacy", [
      ["", "尚未确认"],
      ["public", "公开"],
      ["private", "私密"],
    ])}${field("时区", "timezone", "Asia/Shanghai")}${field("排期（可留空）", "local_datetime", "", "datetime-local")}</div>${area("商业 / AI 披露（JSON）", "disclosures", "{}", 'class="code-input"')}<div class="form-actions"><button class="button primary" type="submit">保存意图并预检</button></div></form>`,
    async (f) => {
      const raw = formData(f);
      await api("/production/publications", {
        method: "POST",
        body: {
          ...raw,
          project_id: state.projectId,
          privacy: raw.privacy || null,
          local_datetime: raw.local_datetime || null,
          disclosures: parseJSON(raw.disclosures),
        },
      });
      toast("发布意图已保存，尚未发送到平台。");
      await renderRoute();
    },
  );
}
function receiptForm(p) {
  modal(
    "回填真实发布回执",
    `<p class="dialog-desc">仅填写已经发生的发布事实。人工记录会保留为人工证据，不会伪装成平台独立验证。</p><form data-form class="form-stack">${field("平台帖子 ID", "platform_post_id", "", "text", "required")}${field("实际帖子链接", "post_url", "", "url", "required")}${field("观察时间（含时区）", "observed_at", new Date().toISOString(), "text", "required")}${select(
      "已观察到的可见性",
      "visibility",
      [
        ["unknown", "尚未核实"],
        ["private", "私密"],
        ["public", "公开"],
        ["removed", "已移除"],
      ],
    )}${area("可核对的证据与备注", "evidence", "", "required")}<div class="form-actions"><button class="button primary" type="submit">保存回执</button></div></form>`,
    async (f) => {
      await api(`/production/publications/${p.id}/receipt`, {
        method: "POST",
        body: { ...formData(f), expected_revision: p.version },
      });
      toast("人工回执已保存");
      await renderRoute();
    },
  );
}
function approveArtifact(a, preferredKind = "content_check") {
  modal(
    "成片内容审阅",
    `<div class="media-detail-preview"><video controls playsinline preload="metadata" src="/api/v1/media/artifacts/${a.id}/file"></video></div><form data-form class="form-stack" style="margin-top:20px">${area("审阅范围与依据", "reason", "", 'required placeholder="已检查的商品事实、画面、字幕、音轨、权利及未决项"')}${field("审批有效期（含时区）", "expires_at", new Date(Date.now() + 86400000).toISOString(), "text", "required")}${select(
      "审阅范围",
      "kind",
      [
        ["content_check", "内容与商品事实"],
        ["rights_check", "素材与音乐使用权"],
      ],
      preferredKind,
    )}${select("结论", "decision", [
      ["approved", "当前版本通过"],
      ["rejected", "需要修改"],
    ])}<p class="help-text">此审批只针对当前文件版本与内容范围。实际发布仍需单独授权。</p><div class="form-actions"><button class="button primary" type="submit">保存审阅结论</button></div></form>`,
    async (f) => {
      const raw = formData(f);
      await api("/production/approvals", {
        method: "POST",
        body: {
          subject_id: a.id,
          subject_version: a.version,
          decision: raw.decision,
          kind: raw.kind,
          expires_at: raw.expires_at,
          evidence: raw.reason,
        },
      });
      toast("审阅结论已保存");
      await renderRoute();
    },
  );
}

async function insights() {
  const analytics = project()
      ? await api(
          `/production/analytics?project_id=${encodeURIComponent(state.projectId)}`,
        )
      : { groups: [], snapshot_count: 0, deduplicated_observations: 0 },
    experiments = records("Experiment");
  $("#main").innerHTML =
    `<div class="page">${pageHead("EVIDENCE & LEARNING", "数据与实验", "来自真实数据的描述、比较与假设。缺失值始终保留为缺失。", `${btn("导入指标", "import-metrics", "upload", "button primary")}${btn("新建实验", "experiment", "plus", "button secondary")}`)}<div class="metric-strip"><div class="metric"><span>指标快照</span><strong>${analytics.snapshot_count || 0}</strong></div><div class="metric"><span>去重后观察</span><strong>${analytics.deduplicated_observations || 0}</strong></div><div class="metric"><span>可比较的分组</span><strong>${arr(analytics.groups).length}</strong></div><div class="metric"><span>实验</span><strong>${experiments.length}</strong></div></div><div class="surface">${arr(analytics.groups).length ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>指标与来源</th><th>观察数量</th><th>均值</th><th>范围</th><th>数据状态</th></tr></thead><tbody>${analytics.groups.map((g) => `<tr><td>${esc(g.metric_name)}<small>${esc(g.source)} · ${esc(g.unit)}</small></td><td>${g.observations}<small>${g.missing} 项缺失</small></td><td>${g.mean == null ? "—" : Number(g.mean).toLocaleString("zh-CN", { maximumFractionDigits: 3 })}</td><td>${g.min == null ? "—" : `${g.min} – ${g.max}`}</td><td>${badge(g.mature ? "已过成熟期" : "未成熟", g.mature ? "green" : "amber")}</td></tr>`).join("")}</tbody></table></div><p class="help-text">按来源、单位及观察窗口分组</p>` : empty("还没有可分析的数据", "", "import-metrics", "导入第一组指标", "chart")}</div><div class="section-heading"><h2>实验与护栏</h2>${btn("分析与实验模块", "learn", "sliders", "button ghost")}</div><div class="surface">${experiments.length ? experiments.map((r) => `<div class="list-row">${icon("sliders")}<div class="list-row-body"><strong>${esc(r.data.name || "实验")}</strong><p>${esc(r.data.hypothesis || "")}</p></div>${badge(statusLabel(r.data.status || "draft"))}${btn("查看", "record", "chevron", "button small secondary", `data-id="${r.id}"`)}</div>`).join("") : empty("暂无实验", "", "", "", "sliders")}</div></div>`;
  bind({
    "import-metrics": metricForm,
    experiment: experimentForm,
    learn: () => nav("module/learn"),
    record: (b) => openRecord(experiments.find((r) => r.id === b.dataset.id)),
  });
}
function metricForm() {
  requireProject();
  if (!records("Account").length) {
    accountList();
    return;
  }
  modal(
    "导入真实指标",
    `<form data-form class="form-stack">${select(
      "账号",
      "account_id",
      records("Account").map((r) => [r.id, recordLabel(r)]),
    )}<div class="form-grid">${field("指标名称", "metric_name", "", "text", 'required placeholder="views / likes / watch_time"')}${field("数值（缺失时留空）", "value", "", "number", 'step="any"')}${field("单位", "unit", "count", "text", "required")}${field("缺失原因", "missing_reason", "", "text", 'placeholder="没有数据时必须填写"')}${field("窗口开始（含时区）", "window_start", "", "text", 'required placeholder="2026-11-20T00:00:00+08:00"')}${field("窗口结束（含时区）", "window_end", "", "text", 'required placeholder="2026-11-21T00:00:00+08:00"')}${field("观察时间", "observed_at", new Date().toISOString(), "text", "required")}${select(
      "流量口径",
      "paid_or_organic",
      [
        ["unknown", "未知"],
        ["organic", "自然"],
        ["paid", "付费"],
        ["mixed", "混合"],
      ],
    )}</div>${field("来源 / 官方导出文件说明", "source", "", "text", "required")}${field("币种（仅金额指标填写）", "currency")}<details class="source-details"><summary>批量导入 JSON</summary>${area("指标数组（填写后优先使用，每项带完整口径）", "rows", "", 'class="code-input" placeholder="[]"')}</details><div class="form-actions"><button class="button primary" type="submit">验证并导入</button></div></form>`,
    async (f) => {
      const raw = formData(f),
        rows = raw.rows.trim()
          ? parseJSON(raw.rows)
          : [
              {
                ...raw,
                value: raw.value === "" ? null : +raw.value,
                missing_reason: raw.missing_reason || null,
                currency: raw.currency || null,
              },
            ];
      rows.forEach((r) => delete r.rows);
      await api("/production/metrics/import", {
        method: "POST",
        body: { project_id: state.projectId, rows },
      });
      toast("指标已导入，来源与时间窗已保留。");
      await renderRoute();
    },
  );
}
function experimentForm() {
  requireProject();
  modal(
    "建立可检验的实验",
    `<form data-form class="form-stack">${field("实验名称", "name", "", "text", "required")}${area("假设", "hypothesis", "", "required")}${area("真实实验单位 ID（每行一个，至少四个）", "unit_ids", "", "required")}${field("目标指标", "metric_name", "", "text", "required")}<div class="form-grid">${field("分组种子", "seed", uid(), "text", "required")}${field("每组最少样本", "min_units_per_arm", 2, "number", 'min="2" max="500"')}${field("开始（含时区）", "window_start", new Date().toISOString(), "text", "required")}${field("结束（含时区）", "window_end", new Date(Date.now() + 604800000).toISOString(), "text", "required")}</div>${area("停止规则与负向护栏", "stop_rule", "", "required")}<div class="form-actions"><button class="button primary" type="submit">保存实验</button></div></form>`,
    async (f) => {
      const raw = formData(f);
      await api("/production/experiments", {
        method: "POST",
        body: {
          ...raw,
          project_id: state.projectId,
          unit_ids: raw.unit_ids
            .split("\n")
            .map((x) => x.trim())
            .filter(Boolean),
          min_units_per_arm: +raw.min_units_per_arm,
        },
      });
      toast("实验已保存");
      await renderRoute();
    },
  );
}

async function audit() {
  const kinds = [...new Set(state.records.map((r) => r.kind))];
  $("#main").innerHTML =
    `<div class="page">${pageHead("PROVENANCE & HISTORY", "活动记录", "从当前作品回到输入、版本、操作与实际回执。", `${btn("导出项目记录", "export-records", "download", "button secondary")}${btn("录入事实", "entity", "plus", "button primary")}`)}<div class="split-two"><section class="surface"><div class="surface-title"><h2>事实与版本</h2>${badge(`${state.records.length} 条当前记录`)}</div><div class="toolbar"><div class="search-field">${icon("search")}<input id="record-query" placeholder="搜索名称或类型" aria-label="搜索事实记录"></div><label class="field"><select id="record-kind" aria-label="按记录类型筛选"><option value="">全部类型</option>${kinds.map((k) => `<option value="${esc(k)}">${esc(entityName(k))}</option>`).join("")}</select></label></div><div id="record-list"></div></section><section class="surface"><div class="surface-title"><h2>活动</h2>${badge(`${state.events.length}`)}</div>${
      state.events.length
        ? `<div class="audit-list">${state.events
            .slice()
            .reverse()
            .slice(0, 40)
            .map(
              (e) =>
                `<div class="audit-entry"><time>${date(e.created_at || e.occurred_at || e.time)}</time><h4>${esc(e.event_type || e.type || "记录事件")}</h4><p>${esc(e.reason || e.payload?.reason || "")}</p></div>`,
            )
            .join("")}</div>`
        : empty("暂无活动", "", "", "", "history")
    }</section></div></div>`;
  const draw = () => {
    const q = $("#record-query").value.toLowerCase(),
      k = $("#record-kind").value;
    const list = state.records.filter(
      (r) =>
        (!k || r.kind === k) &&
        [r.id, r.kind, r.data.name, r.data.title, r.data.label]
          .join(" ")
          .toLowerCase()
          .includes(q),
    );
    $("#record-list").innerHTML = list.length
      ? list
          .slice(0, 100)
          .map(
            (r) =>
              `<button class="list-row" data-action="record" data-id="${r.id}">${icon("file")}<span class="list-row-body"><strong>${esc(recordLabel(r))}</strong><p>${esc(entityName(r.kind))} · ${date(r.updated_at || r.created_at)}</p></span><small>v${r.version}</small>${icon("chevron")}</button>`,
          )
          .join("")
      : empty("没有匹配记录", "", "", "", "history");
  };
  $("#record-query").oninput = draw;
  $("#record-kind").onchange = draw;
  draw();
  bind({
    entity: () => newEntity(),
    record: (b) => openRecord(state.records.find((r) => r.id === b.dataset.id)),
    "export-records": () =>
      download(`ai-ship-${state.projectId || "workspace"}-records.json`, {
        project: project(),
        records: state.records,
        events: state.events,
        exported_at: new Date().toISOString(),
      }),
  });
}
async function newEntity(defaultKind) {
  const pid = requireProject();
  const entities = state.catalog.entities.filter(
    (e) => !READ_ONLY_KINDS.has(e.id),
  );
  const known = entities.some((e) => e.id === defaultKind)
    ? defaultKind
    : "Brief";
  let collect,
    loading = 0;
  const d = modal(
    "录入记录",
    `<form data-form class="form-stack">${select(
      "记录类型",
      "kind",
      entities.map((e) => [e.id, e.label]),
      known,
    )}<div id="record-fields"></div>${field("录入依据", "reason", "", "text", "required")}<details class="source-details"><summary>上游版本引用</summary>${area("引用列表", "refs", "[]", 'class="code-input"')}</details><div class="form-actions"><button type="submit" class="button primary" disabled>保存记录</button></div></form>`,
    async (form) => {
      if (!collect || pid !== state.projectId)
        throw new Error("请重新选择项目");
      const raw = formData(form);
      await saveRecord(
        raw.kind,
        collect(),
        null,
        parseJSON(raw.refs),
        raw.reason,
      );
      toast("记录已保存");
      await renderRoute();
    },
    true,
  );
  const host = $("#record-fields", d),
    submit = $("form[data-form] button[type=submit]", d);
  const draw = async () => {
    const revision = ++loading,
      kind = $("[name=kind]", d).value;
    submit.disabled = true;
    collect = null;
    host.innerHTML =
      '<span class="spinner" role="status" aria-label="读取字段"></span>';
    try {
      const schema = await entitySchema(kind);
      if (revision !== loading || !host.isConnected) return;
      collect = recordFields(host, schema);
      submit.disabled = false;
    } catch (err) {
      if (revision === loading && host.isConnected)
        host.textContent = err.message;
    }
  };
  $("[name=kind]", d).onchange = draw;
  await draw();
}
const schemaCache = new Map();
async function entitySchema(kind) {
  if (!schemaCache.has(kind))
    schemaCache.set(
      kind,
      await api(`/contracts/entities/${encodeURIComponent(kind)}`),
    );
  return schemaCache.get(kind);
}
async function openRecord(r) {
  if (!r) return;
  const readOnly = READ_ONLY_KINDS.has(r.kind);
  const schema = readOnly ? null : await entitySchema(r.kind);
  if (r.project_id && r.project_id !== state.projectId) return;
  const facts = `<dl class="detail-facts"><div><dt>类型</dt><dd>${esc(entityName(r.kind))}</dd></div><div><dt>版本</dt><dd>v${r.version}</dd></div>${readOnly ? displayRecordFacts(r.data) : ""}</dl>`;
  const actions = `${btn("版本历史", "history", "history", "button secondary")}${btn("来源链", "trace", "link", "button secondary")}`;
  let collect;
  const content = readOnly
    ? `${facts}<details class="source-details"><summary>技术详情</summary><pre>${esc(pretty(r.data))}</pre></details><div class="dialog-actions">${actions}</div>`
    : `${facts}<form data-form class="form-stack"><div id="record-fields"></div>${field("变更依据", "reason", "", "text", "required")}<div class="form-actions">${actions}<button class="button primary" type="submit">保存新版本</button></div></form>`;
  const d = modal(
    recordLabel(r),
    content,
    readOnly
      ? null
      : async (form) => {
          const raw = formData(form);
          await saveRecord(r.kind, collect(), r, r.refs || [], raw.reason);
          toast("新版本已保存");
          await renderRoute();
        },
    true,
  );
  if (!readOnly) collect = recordFields($("#record-fields", d), schema, r.data);
  onAction(d, { history: () => showHistory(r), trace: () => showTrace(r) });
}

async function showHistory(r) {
  const versions = await api(`/records/${r.id}/history`);
  const list = arr(versions.items || versions);
  const d = modal(
    "版本历史",
    `<div>${list.map((v) => `<details class="source-details"><summary>v${v.version} · ${date(v.created_at)}</summary><pre>${esc(pretty(v.data))}</pre>${READ_ONLY_KINDS.has(r.kind) ? '<p class="help-text">执行事实仅供追溯，不支持人工覆盖恢复。</p>' : btn("以此内容创建新版本", "restore", "history", "button secondary", `data-version="${v.version}"`)}</details>`).join("")}</div>`,
    null,
    true,
  );
  onAction(d, {
    restore: async (b) => {
      const v = list.find((v) => v.version === +b.dataset.version),
        latest = await api(`/records/${r.id}`);
      await saveRecord(
        r.kind,
        v.data,
        latest,
        v.refs || [],
        `用户恢复版本 ${v.version} 的内容`,
      );
      toast("已恢复为新版本");
      d.close();
      await renderRoute();
    },
  });
}
async function settings() {
  const h = state.health || {};
  $("#main").innerHTML =
    `<div class="page settings-page">${pageHead("PREFERENCES", "工作区设置", "让工作区适应你的设备与操作习惯。")}<section class="surface setting-section"><h2>外观与交互</h2><div class="setting-row"><div><strong>外观</strong></div><div class="segmented">${[
      ["system", "自动"],
      ["light", "浅色"],
      ["dark", "深色"],
    ]
      .map(
        ([v, l]) =>
          `<button data-action="theme" data-value="${v}" aria-pressed="${prefs.theme === v}" class="${prefs.theme === v ? "active" : ""}">${l}</button>`,
      )
      .join(
        "",
      )}</div></div><div class="setting-row"><div><strong>减少动态效果</strong></div><label class="switch"><input id="reduce-motion" type="checkbox" ${prefs.motion === "reduced" ? "checked" : ""} aria-label="减少动态效果"><i></i></label></div><div class="setting-row"><div><strong>减少透明度</strong></div><label class="switch"><input id="reduce-transparency" type="checkbox" ${prefs.transparency === "reduced" ? "checked" : ""} aria-label="减少透明度"><i></i></label></div></section><section class="surface setting-section"><h2>连接与权限</h2><form id="connection-form" class="form-stack">${field("访问令牌（仅保留在当前浏览器会话）", "token", sessionStorage.getItem("ship-token") || "", "password", 'autocomplete="off" placeholder="本地模式可留空"')}<div class="form-actions"><button class="button secondary" type="submit">保存并验证连接</button></div></form><div class="divider"></div><div class="detail-facts"><div><dt>服务状态</dt><dd>${state.online ? "已连接" : "未连接"}</dd></div><div><dt>运行模式</dt><dd>${esc({ local: "本地", team: "团队" }[h.mode] || h.mode || "待确认")}</dd></div></div></section></div>`;
  $("#reduce-motion").onchange = (e) => {
    prefs.motion = e.target.checked ? "reduced" : "system";
    localStorage.setItem("ship-motion", prefs.motion);
    applyPrefs();
  };
  $("#reduce-transparency").onchange = (e) => {
    prefs.transparency = e.target.checked ? "reduced" : "system";
    localStorage.setItem("ship-transparency", prefs.transparency);
    applyPrefs();
  };
  $("#connection-form").onsubmit = async (e) => {
    e.preventDefault();
    const previous = sessionStorage.getItem("ship-token"),
      token = formData(e.target).token.trim();
    if (token) sessionStorage.setItem("ship-token", token);
    else sessionStorage.removeItem("ship-token");
    try {
      const health = await api("/health");
      if (health.mode === "team" && token)
        await api("/session", { method: "POST" });
      eventSource?.close();
      eventSource = null;
      eventProject = null;
      state.records = [];
      state.events = [];
      state.runs = [];
      await start();
      toast("连接验证成功，已按当前身份重新加载工作区");
    } catch (err) {
      if (previous) sessionStorage.setItem("ship-token", previous);
      else sessionStorage.removeItem("ship-token");
      toast(err.message + "；原连接设置已保留。", "error");
    }
  };
  bind({
    theme: (b) => {
      prefs.theme = b.dataset.value;
      localStorage.setItem("ship-theme", prefs.theme);
      applyPrefs();
      settings();
    },
    modules: modulePicker,
    extensions: () => nav("module/workspace"),
  });
}

window.addEventListener("hashchange", renderRoute);
window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (state.catalog) search();
  }
  if (e.key === "Escape") setMenu(false);
});
window.addEventListener("offline", () => {
  state.online = false;
  toast("网络已断开。已保存的版本保持不变。", "error");
});
window.addEventListener("online", () => {
  state.online = true;
  toast("网络已恢复，可以重试未完成的操作。");
});
async function start() {
  try {
    const [health, catalog, projects, executionCapabilities] =
      await Promise.all([
        api("/health"),
        api("/catalog/workspace"),
        api("/projects"),
        api("/execution/capabilities").catch(() => ({ handlers: {} })),
      ]);
    state.executionCapabilities = executionCapabilities;
    state.health = health;
    if (health.mode === "team" && sessionStorage.getItem("ship-token"))
      await api("/session", { method: "POST" });
    state.catalog = catalog;
    state.projects = arr(projects.items || projects);
    if (!state.projects.some((p) => p.id === state.projectId))
      state.projectId = state.projects[0]?.id || "";
    shell();
    await renderRoute();
  } catch (e) {
    $("#app").innerHTML =
      `<div class="boot"><div class="brand-mark">S</div><h1 style="font-size:24px;color:var(--text)">打开 AI Ship</h1><p style="max-width:420px;text-align:center;line-height:1.8">${esc(e.message)}</p><form id="startup-form" class="form-stack" style="width:min(370px,80vw)">${field("访问令牌（本地模式可留空）", "token", "", "password", 'autocomplete="off"')}<button type="submit" class="button primary">连接工作区</button></form></div>`;
    $("#startup-form").onsubmit = (ev) => {
      ev.preventDefault();
      sessionStorage.setItem("ship-token", formData(ev.target).token);
      start();
    };
  }
}
start();
