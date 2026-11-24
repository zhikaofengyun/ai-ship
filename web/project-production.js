import { openNarration } from "./narration.js";
import { exploration } from "./asset-lifecycle.js";
import { openVoiceStudio } from "./voice-studio.js";
import { openSoundtrack } from "./soundtrack.js";
import { createVariantPanel } from "./variant-panel.js";
import { openAssetSearch } from "./asset-search.js";
import {
  $,
  $$,
  arr,
  btn,
  date,
  esc,
  field,
  icon,
  modal,
  select,
  time,
  uid,
} from "./ui.js";
import { mediaDuration, mediaLibrary, mediaName, mediaType } from "./media.js";
import { installProductionResize } from "./workspace-layout.js";
import { directorMonitor, bindDirectorMonitor } from "./director-monitor.js";
import { generationPanel } from "./generation-controls.js";
import { uploadFile } from "./upload.js";

const stages = {
  review: "等待审阅",
  draft: "时间线已就绪",
  rendered: "成片已导出",
  blocked: "需要处理",
  outcome_unknown: "等待核对调用结果",
  planning: "正在编排",
  rendering: "正在渲染",
};
const modes = {
  team: "异构 Agent 小队",
  model: "导演模型",
  local_vision: "本地画面分析",
};
const route = "/production/auto-edits";
const at = (seconds) =>
  Number.isFinite(Number(seconds)) ? Number(seconds).toFixed(3) + " 秒" : "—";
const roleName = { director: "导演", reviewer: "镜头复核", judge: "独立裁决" };
function showDecisions(plan, run) {
  const checks = arr(plan.reviews || run.issue?.details?.verdicts);
  if (!checks.length) return "";
  return `<details class="director-decisions" open><summary>制作复核</summary>${checks.map((r) => `<div><strong>${esc(roleName[r.role] || "制作复核")} · ${r.decision === "accept" ? "接受" : "要求修订"}</strong><p>${esc(r.reason)}</p></div>`).join("")}</details>`;
}
function renderQC(qc) {
  const data = qc?.technical_qc;
  if (!data?.probe || !data?.checks) return "";
  const labels = {
    bytes_present: "文件可读",
    duration_positive: "时长有效",
    duration_within_tolerance: "时长误差",
    dimensions: "画幅一致",
    audio_presence: "音轨一致",
    full_decode: "全片解码",
  };
  const p = data.probe;
  return `<details class="director-decisions"><summary>技术质检</summary><p>${esc(p.width)} × ${esc(p.height)} · ${esc(at(p.duration))}</p>${Object.entries(
    labels,
  )
    .filter(([k]) => typeof data.checks[k] === "boolean")
    .map(
      ([k, label]) => `<p>${label} · ${data.checks[k] ? "通过" : "待处理"}</p>`,
    )
    .join("")}</details>`;
}
function showEvidence(run) {
  const evidence = arr(run.evidence);
  if (!evidence.length) return "";
  return `<details class="director-decisions"><summary>来源证据 · ${evidence.length}</summary>${evidence
    .map((s) => {
      const words = arr(s.ocr?.words)
        .map((w) => (typeof w === "string" ? w : w.text))
        .filter(Boolean);
      return `<div><strong>${esc(s.name)} · v${esc(s.asset_version)}</strong><p>${esc(at(s.duration))} · 画面采样 ${arr(
        s.samples,
      )
        .map((f) => esc(at(f.at)))
        .join(
          "、",
        )}</p>${words.length ? `<p>识别文字 · ${esc(words.join(" "))}</p>` : ""}</div>`;
    })
    .join("")}</details>`;
}
const artifactUrl = (a) =>
  "/api/v1/media/artifacts/" + encodeURIComponent(a.id) + "/file";
const reviewStage = (s) => stages[s] || "处理中";
const link = (label, hash, name) =>
  `<a class="button secondary" href="#${esc(hash)}" data-production-link="${esc(name)}">${icon("scissors")}${esc(label)}</a>`;

function readLocal(key) {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "{}") || {};
  } catch {
    return {};
  }
}
function writeLocal(key, value) {
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* The current session can still work with storage disabled. */
  }
}

function scopedSources(ctx) {
  return mediaLibrary(ctx.state.records).filter(
    (r) =>
      (r.kind === "Asset" || r.kind === "AssetVersion") &&
      !["quarantine", "local_cold"].includes(r.data.storage_tier) &&
      mediaType(r) === "video" &&
      mediaDuration(r) > 0,
  );
}

export async function mountProduction(root, ctx) {
  const pid = ctx.requireProject(),
    project = ctx.project(),
    generation = ctx.state.routeGeneration;
  const active = () =>
    ctx.state.projectId === pid && ctx.state.routeGeneration === generation;
  const actor = ctx.state.health?.actor || {};
  const key = [
    "ship-production",
    actor.tenant_id || "local",
    actor.actor_id || "owner",
    pid,
  ].join(":");
  const draft = readLocal(key);
  const persist = () => writeLocal(key, draft);
  const profiles = () => ctx.records("ModelProfile");
  let sessions = [],
    detail = null,
    busy = "",
    issue = "",
    highlighted = "",
    shotIndex = -1;
  let shotView = "current";
  let requestSerial = 0;
  let releasePane = () => {};
  let releaseMonitor = () => {};
  const monitorView = { mode: "fit", x: 0, y: 0 };
  let previousScene;
  ctx.state.cleanup.push(() => {
    releasePane();
    releaseMonitor();
  });
  const refreshSessions = async () => {
    const value = await ctx.api(
      route + "?project_id=" + encodeURIComponent(pid),
    );
    sessions = arr(value.sessions);
  };
  const loadDetail = async (id) => {
    const serial = ++requestSerial;
    const value = await ctx.api(route + "/" + encodeURIComponent(id));
    if (!active() || serial !== requestSerial) return;
    await hydrateReferencedSources(value);
    if (!active() || serial !== requestSerial) return;
    detail = value;
    shotView = "current";
    draft.selectedSession = id;
    draft.newTask = false;
    shotIndex = 0;
    highlighted = "";
    persist();
  };
  const sources = () => scopedSources(ctx);
  const generated = generationPanel(ctx, pid, draft, persist, async (id) => {
    const existing = selected();
    await ctx.reloadData();
    if (!active()) return;
    if (!sources().some((s) => s.id === id))
      throw new Error("生成的文件暂时无法用于当前来源选择。");
    delete draft.explorationPlan;
    draft.assetIds = [...new Set([...existing, id])];
    draft.lastGenerated = id;
    persist();
    draw();
    ctx.toast(
      detail
        ? "镜头已加入素材，新建制作任务时可以选用。"
        : "镜头已加入本次制作素材。",
    );
  });
  const variants = createVariantPanel(ctx, async (value) => {
    if (!active()) return;
    await applySession(value);
    if (active()) draw();
  });
  const pinnedSources = new Map();
  const sourceById = (id) =>
    ctx.state.records.find((r) => r.id === id) || pinnedSources.get(id);
  async function hydrateReferencedSources(value) {
    const ids = [
      ...arr(value.session?.data.evidence).map((e) => e.asset_id),
      ...arr(value.timeline?.data.clips).map(
        (c) => c.asset_version_id || c.asset_id,
      ),
    ];
    const missing = [...new Set(ids)].filter((id) => id && !sourceById(id));
    await Promise.all(
      missing.map(async (id) => {
        try {
          const r = await ctx.api("/records/" + encodeURIComponent(id));
          if (r.project_id === pid) pinnedSources.set(id, r);
        } catch (error) {
          if (![403, 404].includes(error.status)) throw error;
        }
      }),
    );
  }
  const selected = () => {
    const available = new Set(sources().map((r) => r.id));
    return Array.isArray(draft.assetIds)
      ? draft.assetIds.filter((id) => available.has(id))
      : [...available];
  };
  const options = () => [
    ["", "选择模型配置"],
    ...profiles().map((p) => [p.id, p.data.name || p.data.model_id]),
  ];
  const initialGoals =
    typeof project.data.goals === "string" ? project.data.goals : "";
  const mode = () =>
    draft.mode ||
    (profiles().length > 1
      ? "team"
      : profiles().length
        ? "model"
        : "local_vision");
  const viewingTimeline = () =>
    Boolean(detail?.timeline) && shotView === "current";
  const cuts = () => {
    if (!viewingTimeline()) return arr(detail?.plan?.data?.shots);
    return arr(detail.timeline.data.clips)
      .slice()
      .sort(
        (a, b) =>
          a.timeline_start - b.timeline_start ||
          (a.track || 0) - (b.track || 0),
      )
      .map((c) => {
        const asset = c.asset_version_id || c.asset_id;
        return {
          ...c,
          asset_id: asset,
          name: c.name || mediaName(sourceById(asset)),
          reason:
            "画面轨 " +
            ((c.track || 0) + 1) +
            " · " +
            Number(c.speed ?? 1).toFixed(2) +
            "× 速度 · " +
            Math.round(Number(c.volume ?? 1) * 100) +
            "% 原声",
        };
      });
  };
  function renderPreview() {
    const box = $("[data-director-monitor]", root);
    if (!box) return;
    releaseMonitor();
    releaseMonitor = () => {};
    const row = cuts()[shotIndex];
    const artifact =
      detail?.artifact && !highlighted && shotIndex === -1
        ? detail.artifact
        : null;
    const source =
      artifact ||
      (highlighted
        ? sourceById(highlighted)
        : row
          ? sourceById(row.asset_id)
          : sourceById(selected()[0]));
    if (
      row &&
      !highlighted &&
      !artifact &&
      !viewingTimeline() &&
      source?.version !== row.asset_version
    ) {
      box.innerHTML = `<header>镜头来源</header><div class="director-screen"><div class="director-monitor-empty">${icon("history")}来源版本已变化，请重新核对方案。</div></div>`;
      return;
    }
    const title = artifact
      ? "导出的成片"
      : source
        ? mediaName(source)
        : "素材监视器";
    if (!source) {
      box.innerHTML = `<header><span>${icon("eye")} ${esc(title)}</span></header><div class="director-screen"><div class="director-monitor-empty">${icon("film")}<span>预览画面</span></div></div>`;
      return;
    }
    const start = row && !highlighted && !artifact ? Number(row.source_in) : 0;
    const end = row && !highlighted && !artifact ? Number(row.source_out) : 0;
    const address = artifact ? artifactUrl(source) : ctx.assetUrl(source);
    box.innerHTML = directorMonitor({ title, address, start, end, artifact });
    releaseMonitor = bindDirectorMonitor(box, monitorView, source, start, end);
  }
  function settingsFields() {
    const m = mode(),
      list = profiles();
    const idFor = (p) =>
      JSON.stringify([
        p?.data.endpoint_ref || p?.data.provider,
        p?.data.model_id,
      ]);
    const director =
      list.find((p) => p.id === draft.model_profile_id) || list[0];
    const different = list.filter((p) => idFor(p) !== idFor(director));
    const third = different.some((p) => idFor(p) !== idFor(different[0]));
    const reviewer =
      list.find((p) => p.id === draft.reviewer_profile_id) ||
      (third ? different[0] : director);
    const judge =
      list.find((p) => p.id === draft.judge_profile_id) ||
      different.find((p) => idFor(p) !== idFor(reviewer));
    return `<div class="director-config">${select("制作方式", "mode", Object.entries(modes), m)}
      ${
        m !== "local_vision"
          ? `<div class="director-model-settings">${select("导演模型", "model_profile_id", options(), director?.id || "", "required")}
      ${m === "team" ? select("镜头复核模型", "reviewer_profile_id", options(), reviewer?.id || "", "required") + select("独立裁决模型", "judge_profile_id", options(), judge?.id || "", "required") : ""}
      ${field("本次费用上限（USD）", "max_cost", draft.max_cost ?? "", "number", 'min="0.000001" max="10000" step="any" required placeholder="输入本次上限"')}</div>`
          : ""
      }
    ${btn("模型配置", "models", "sliders", "button small ghost director-model-button")}<div class="director-numbers">${field("目标时长（秒）", "target_seconds", draft.target_seconds ?? Math.min(120, Math.max(1, Number(project.data.duration) || 30)), "number", 'min="1" max="120" step="any" required')}${select(
      "帧率",
      "fps",
      [
        [24, "24 fps"],
        [25, "25 fps"],
        [30, "30 fps"],
        [50, "50 fps"],
        [60, "60 fps"],
      ],
      draft.fps || "30",
    )}</div>
    <details class="director-format"><summary>输出画幅</summary><div class="director-numbers">${field("宽（像素）", "width", draft.width ?? project.data.width ?? 1080, "number", 'min="64" max="4096" step="2" required')}${field("高（像素）", "height", draft.height ?? project.data.height ?? 1920, "number", 'min="64" max="4096" step="2" required')}</div></details></div>`;
  }
  function sourcesMarkup() {
    const assets = sources(),
      checked = selected();
    return `<section class="director-sources"><header><h2>制作素材 <small>${checked.length} / ${assets.length}</small></h2><div>${btn("探索配额", "explore-assets", "film", "button small ghost")}${btn("检索素材", "search-production-assets", "search", "button small ghost")}${btn("导入视频", "upload", "upload", "button small ghost")}</div></header>
      <input type="file" multiple accept="video/*,.mkv,.mov,.mp4,.webm" data-production-files hidden>
      ${assets.length ? `<div class="director-sources-list">${assets.map((a) => `<div class="director-source"><label><input type="checkbox" data-source="${esc(a.id)}" ${checked.includes(a.id) ? "checked" : ""}><span>${esc(mediaName(a))}</span></label><small>${time(mediaDuration(a))}</small><button type="button" class="icon-button" aria-label="预览 ${esc(mediaName(a))}" data-action="preview-source" data-id="${esc(a.id)}">${icon("play")}</button></div>`).join("")}</div>` : `<button type="button" class="director-drop" data-action="upload">${icon("upload")}拖入视频或选择文件</button>`}
    </section>`;
  }
  function composeMarkup() {
    return `<form class="director-compose" id="production-form"><label for="production-goal">制作目标</label><textarea id="production-goal" name="goal" required maxlength="3000" rows="3" placeholder="要为谁制作什么视频？哪些画面需要保留？">${esc(draft.goal ?? initialGoals)}</textarea>
      <details class="director-format" ${draft.opening_text ? "open" : ""}><summary>首镜文字要求</summary>${field("首镜原片必须可见的文字", "opening_text", draft.opening_text || "", "text", 'maxlength="120" autocomplete="off" placeholder="可选"')}</details>
      ${sourcesMarkup()}${settingsFields()}
      ${draft.pending ? `<div class="director-warning" role="status">上次提交尚待核对。${btn("核对原请求", "reconcile", "history", "button small secondary")}</div>` : ""}
      <div class="director-submit"><button type="submit" class="button primary" ${busy ? "disabled" : ""}>${icon("spark")}生成剪辑方案</button></div></form>`;
  }
  function openingReview(check, output = false) {
    if (!check?.expected_text) return "";
    const labels = {
      matched: "已核实",
      conflict: "与要求不符",
      unverified: "待核验",
    };
    const observation =
      (output ? check.encoded_first_frame : check.observation) || {};
    const words = arr(observation.words)
      .map((w) => w.text)
      .filter(Boolean)
      .join(" ");
    const options =
      !output && check.status !== "matched" ? arr(check.alternatives) : [];
    return `<details class="director-decisions" ${output ? "data-export-opening" : "data-opening-check"}="${esc(check.status)}" ${check.status === "matched" ? "" : "open"}><summary>${output ? "成片首帧" : "方案首帧"} · ${esc(labels[check.status] || "待核验")}</summary>
      <p>要求 · ${esc(check.expected_text)}</p>
      ${words ? `<p>画面文字 · ${esc(words)}</p>` : ""}
      ${Number.isFinite(observation.at) ? `<p>${output ? "成片位置" : "原片位置"} · ${esc(at(observation.at))}</p>` : ""}
      ${options
        .map((option, i) => {
          const file = sourceById(option.asset_id);
          const name = file?.data?.name || file?.data?.filename || "已核实片段";
          return btn(
            "选用 " + name + " · " + at(option.source_in),
            "replace-opening",
            "layers",
            "button small secondary",
            `data-alternative="${i}"`,
          );
        })
        .join("")}</details>`;
  }
  function handoffMarkup(artifact) {
    const approvals = ctx
      .records("Approval")
      .filter(
        (r) =>
          r.data.subject_id === artifact.id &&
          r.data.subject_version === artifact.version,
      );
    const intents = ctx
      .records("Publication")
      .filter(
        (r) =>
          r.data.artifact_id === artifact.id &&
          r.data.artifact_version === artifact.version,
      );
    return `<details class="director-decisions director-delivery"><summary>内容审阅与发布交接</summary><div class="director-actions">${btn("内容审阅", "review-content", "eye", "button small secondary")}${btn("权利审阅", "review-rights", "lock", "button small secondary")}${btn("建立发布意图", "publication", "send", "button small secondary")}</div>
      ${approvals
        .slice(0, 6)
        .map(
          (r) =>
            `<p>${r.data.kind === "rights_check" ? "权利审阅" : "内容审阅"} · ${r.data.decision === "approved" ? "通过" : "需要修改"} · ${esc(date(r.created_at))}</p>`,
        )
        .join("")}
      ${intents.map((r) => `<div><strong>${esc(r.data.title || "发布意图")}</strong><div class="director-actions">${btn("核对发布条件", "preflight", "checkcircle", "button small ghost", `data-id="${esc(r.id)}"`)}${btn("登记实际回执", "receipt", "edit", "button small ghost", `data-id="${esc(r.id)}"`)}</div></div>`).join("")}</details>`;
  }
  function reviewMarkup() {
    const s = detail.session,
      stage = s.data.stage,
      plan = detail.plan?.data || {},
      shots = cuts(),
      qc = detail.qc?.data || {},
      missing = arr(plan.missing);
    const timeline = detail.timeline;
    const heading = plan.title || s.data.goal || "制作任务";
    const timelineName = timeline?.data.name;
    const editedAfterRender =
      stage === "rendered" &&
      timeline &&
      timeline.version !== s.data.timeline_ref?.version;
    return `<section class="director-review"><div class="director-stage"><span class="director-status" data-stage="${esc(stage)}">${esc(reviewStage(stage))}</span><small>${esc(date(s.updated_at || s.created_at))}</small></div>
      <h2 tabindex="-1">${esc(heading)}</h2>
      ${plan.approach ? `<p class="director-approach">${esc(plan.approach)}</p>` : ""}
      ${s.data.issue ? `<p class="director-warning" role="alert">${esc(s.data.issue.message || s.data.issue.code)}</p>` : ""}
      ${viewingTimeline() ? "" : openingReview(plan.opening_check)}
      ${timeline ? `<div class="director-views" role="group" aria-label="核对剪辑版本"><button type="button" data-action="shot-view" data-view="current" aria-pressed="${viewingTimeline()}">当前时间线 · v${esc(timeline.version)}</button><button type="button" data-action="shot-view" data-view="proposal" aria-pressed="${!viewingTimeline()}">原始方案</button></div>` : ""}
      ${shots.length ? `<ol class="director-shots" aria-label="${viewingTimeline() ? "当前时间线镜头" : "镜头方案"}">${shots.map((shot, i) => `<li><button type="button" data-action="preview-shot" data-shot="${i}" aria-pressed="${i === shotIndex && !highlighted}"><span class="director-shot-index">${String(i + 1).padStart(2, "0")}</span><span><strong>${esc(shot.name || mediaName(sourceById(shot.asset_id)))}</strong><small>源 ${at(shot.source_in)} – ${at(shot.source_out)} · 时间线 ${at(shot.timeline_start)}</small>${shot.reason ? `<span class="director-reason">${esc(shot.reason)}</span>` : ""}</span>${icon("play")}</button></li>`).join("")}</ol>` : ""}
      ${missing.length ? `<details class="director-gaps" open><summary>待核对的内容 · ${missing.length}</summary><ul>${missing.map((m, i) => `<li>${esc(m)} ${btn("拟定补镜", "generate-gap", "spark", "button ghost small", `data-gap="${i}"`)}</li>`).join("")}</ul></details>` : ""}
      ${shots.length && !viewingTimeline() ? `<div class="director-shot-actions">${btn("替换选中镜头", "replace-shot", "layers", "button ghost small")}${btn("为选段生成候选", "generate-shot", "spark", "button ghost small")}</div>` : ""}
      ${showDecisions(plan, s.data)}
      ${stage === "review" ? `<div class="director-actions">${btn("采用方案并建立时间线", "approve", "check", "button primary")}</div>` : ""}
      ${timeline ? `<div class="director-timeline"><span>${icon("film")}${esc(timelineName && timelineName !== heading && timelineName !== s.data.goal ? timelineName : "时间线")} <small>v${esc(timeline.version)}</small></span><div>${link("接管这条时间线", "editor/project-timeline/" + timeline.id, "takeover")}${stage === "draft" || editedAfterRender ? btn(editedAfterRender ? "渲染最新修改" : "锁版并渲染", "render", "play", "button primary") : ""}</div></div>` : ""}
      ${detail.artifact ? `<div class="director-output"><h3>导出结果</h3><span>${qc.publish_ready === true ? "已通过当前发布检查" : "已生成成片 · 发布资格需单独审阅"}</span><div>${btn("播放成片", "watch", "play", "button secondary")}<a class="button primary" href="${artifactUrl(detail.artifact)}?download=true" download>${icon("download")}下载 MP4</a></div>${openingReview(detail.artifact.data?.qc?.opening_check, true)}${renderQC(qc)}${handoffMarkup(detail.artifact)}</div>` : ""}
      <div class="director-review-foot">${btn("刷新任务", "reload", "history", "button small ghost")}${stage === "planning" && draft.pending ? btn("核对原请求", "reconcile", "history", "button small secondary") : ""}${stage === "rendering" && s.data.render_request ? btn("核对本次渲染", "recover-render", "history", "button small secondary") : ""}${stage === "blocked" ? btn("调整后重新编排", "new-task", "edit", "button small secondary") : ""}</div>
      </section>`;
  }
  function focusedControl() {
    const el = document.activeElement;
    if (!el || !root.contains(el)) return null;
    const attrs = [
      "id",
      "name",
      "data-action",
      "data-view",
      "data-shot",
      "data-source",
      "data-production-link",
      "type",
    ];
    const parts = attrs
      .filter((a) => el.hasAttribute(a))
      .map((a) => "[" + a + '="' + CSS.escape(el.getAttribute(a)) + '"]');
    return parts.length ? el.localName + parts.join("") : null;
  }
  function draw(returnFocus) {
    if (!active()) return;
    const selector = returnFocus || focusedControl();
    const scene = JSON.stringify([
      detail?.session.id,
      detail?.session.data.stage,
      shotView,
    ]);
    const changed = previousScene !== scene;
    const first = previousScene === undefined;
    previousScene = scene;
    const list = sessions.map((s) => [
      s.id,
      (s.data.goal || s.data.name || "制作任务").slice(0, 60) +
        " · " +
        reviewStage(s.data.stage),
    ]);
    root.innerHTML = `<div class="page director-page" ${changed ? "data-scene-enter" : ""}><div class="director-titlebar"><div><h1>制作台</h1></div><div>${btn("项目设置", "project-settings", "sliders", "button ghost")}${!detail?.timeline ? link("手动剪辑", "editor", "manual") : ""}${detail ? btn("新建任务", "new-task", "plus", "button secondary") : ""}</div></div>
    ${list.length ? `<details class="director-task-picker"><summary>${icon("history")} 制作记录 · ${list.length}</summary><div class="director-history">${select("制作记录", "history", [["", "选择制作记录"], ...list], detail?.session.id || "")}</div></details>` : ""}
    <div class="director-work"><div class="director-primary">${detail ? reviewMarkup() : composeMarkup()}<p data-production-status role="status" class="director-live" ${busy || issue ? "" : "hidden"}>${esc(busy || issue)}</p></div>
    <aside class="director-evidence"><div data-director-monitor></div>${detail ? `<div class="director-run-meta"><h3>任务依据</h3>${detail.session.data.goal && detail.session.data.goal !== detail.plan?.data.title ? `<p>${esc(detail.session.data.goal)}</p>` : ""}<small>${esc(modes[detail.session.data.mode] || "")}</small>${showEvidence(detail.session.data)}</div>` : ""}<div data-variants></div><div data-generation-panel></div><details class="director-format director-audio-tools"><summary>声音与叙事</summary><div class="director-actions">${btn("生成或导入口播", "narration", "audio", "button small secondary")}${btn("口播与画面对齐", "voice-studio", "sliders", "button small secondary")}${btn("配乐与混音", "soundtrack", "sliders", "button small secondary")}</div></details></aside></div></div>`;
    releasePane();
    releasePane = installProductionResize($(".director-work", root));
    renderPreview();
    variants.mount($("[data-variants]", root), detail);
    generated.mount(
      $("[data-generation-panel]", root),
      location.hash === "#production/generation",
      detail?.session.id || "",
    );
    wire();
    const target = selector && $(selector, root);
    if (target && !target.closest("details:not([open])"))
      target.focus({ preventScroll: true });
    else if (changed && !first && !document.querySelector("dialog[open]")) {
      (detail
        ? $(".director-review h2", root)
        : $("#production-goal", root)
      )?.focus({ preventScroll: true });
    }
  }
  async function work(message, fn) {
    if (busy || !active()) return;
    busy = message;
    issue = "";
    const returnFocus = focusedControl();
    const notice = $("[data-production-status]", root);
    notice.textContent = message;
    notice.hidden = false;
    root.querySelectorAll("button,input,textarea,select").forEach((el) => {
      if (!el.closest(".director-monitor")) el.disabled = true;
    });
    try {
      await fn();
    } catch (e) {
      if (active()) issue = e.message || String(e);
    } finally {
      busy = "";
      draw(returnFocus);
    }
  }
  async function applySession(value) {
    detail = value;
    shotView = "current";
    draft.selectedSession = value.session.id;
    draft.newTask = false;
    if (value.session.data.stage !== "planning") draft.pending = null;
    shotIndex = value.artifact ? -1 : 0;
    highlighted = "";
    persist();
    await Promise.all([refreshSessions(), ctx.reloadData()]);
    await hydrateReferencedSources(value);
  }
  async function submit(event) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget,
      data = Object.fromEntries(new FormData(form));
    const ids = selected();
    if (ids.length > 16) {
      issue = "当前每个制作任务最多选择 16 段视频。";
      draw();
      return;
    }
    if (!ids.length) {
      issue = "请先选择至少一段视频。";
      draw();
      return;
    }
    const request = {
      project_id: pid,
      goal: String(data.goal).trim(),
      mode: data.mode,
      asset_ids: ids,
      ...(draft.explorationPlan
        ? { exploration_plan: draft.explorationPlan }
        : {}),
      target_seconds: Number(data.target_seconds),
      width: Number(data.width),
      height: Number(data.height),
      fps: Number(data.fps),
    };
    const openingText = String(data.opening_text || "").trim();
    if (openingText) request.opening_text = openingText;
    if (!request.goal) {
      form.elements.goal.focus();
      return;
    }
    if (request.mode !== "local_vision") {
      request.model_profile_id = data.model_profile_id;
      request.max_cost = Number(data.max_cost);
      if (request.mode === "team") {
        request.reviewer_profile_id = data.reviewer_profile_id;
        request.judge_profile_id = data.judge_profile_id;
      }
    }
    const fingerprint = JSON.stringify(request);
    if (draft.pending && draft.pending.body !== fingerprint) {
      issue = "上次请求结果待核对。请先刷新制作记录，或恢复原请求后重试。";
      draw();
      return;
    }
    draft.pending ||= { body: fingerprint, key: uid() };
    persist();
    await work("正在分析来源、编排镜头…", () => sendRequest(request));
  }
  async function sendRequest(request) {
    try {
      const response = await ctx.api(route, {
        method: "POST",
        body: { ...request, idempotency_key: draft.pending.key },
      });
      await applySession(response);
    } catch (err) {
      if (
        err.status >= 400 &&
        err.status < 500 &&
        err.status !== 409 &&
        err.status !== 429
      ) {
        draft.pending = null;
        persist();
      }
      throw err;
    }
  }
  async function advance(action) {
    const id = detail.session.id,
      revision = detail.session.version;
    const body = { expected_revision: revision };
    if (action === "render") body.timeline_revision = detail.timeline.version;
    await work(
      action === "approve" ? "正在建立时间线…" : "正在锁版、渲染和校验文件…",
      async () => {
        try {
          await applySession(
            await ctx.api(route + "/" + encodeURIComponent(id) + "/" + action, {
              method: "POST",
              body,
            }),
          );
        } catch (e) {
          await loadDetail(id);
          throw e;
        }
      },
    );
  }
  const reportProgress = (s) => {
    if (active()) {
      const el = $("[data-production-status]", root);
      if (el) {
        el.hidden = false;
        el.textContent = s;
      }
    }
  };
  async function importFiles(files) {
    if (!files.length || busy) return;
    await work("正在导入视频…", async () => {
      const ids = selected();
      const errors = [];
      for (const f of files) {
        try {
          const result = await uploadFile(ctx.api, pid, f, {
            onProgress: ({ offset, total }) =>
              reportProgress(
                f.name +
                  " · " +
                  Math.round((offset / Math.max(1, total)) * 100) +
                  "%",
              ),
          });
          if (result.asset) ids.push(result.asset.id);
        } catch (e) {
          errors.push(f.name + " · " + e.message);
        }
      }
      await ctx.reloadData();
      delete draft.explorationPlan;
      draft.assetIds = [...new Set(ids)];
      persist();
      if (errors.length) throw new Error(errors.join(" / "));
    });
  }
  function replaceShot(index = shotIndex, verified = null) {
    const shot = cuts()[index];
    if (!shot) throw new Error("先点选要替换的镜头。");
    const candidates = sources();
    if (!candidates.length) throw new Error("没有可用的视频来源。");
    const original = detail.session;
    const duration = shot.source_out - shot.source_in;
    const preferred = verified
      ? candidates.find((r) => r.id === verified.asset_id)
      : candidates.find((r) => r.id === draft.lastGenerated) ||
        candidates.find((r) => r.id === shot.asset_id) ||
        candidates[0];
    if (
      !preferred ||
      (verified && preferred.version !== verified.asset_version)
    )
      throw new Error("候选来源已更新，请刷新制作任务后重新选取。");
    const old = preferred.id === shot.asset_id;
    const d = modal(
      "替换第 " + (index + 1) + " 个镜头",
      `<form data-form class="form-stack">${select(
        "使用素材",
        "asset_id",
        candidates.map((a) => [a.id, mediaName(a)]),
        preferred.id,
        "required",
      )}
      <div class="form-grid">${field("源入点（秒）", "source_in", verified?.source_in ?? (old ? shot.source_in : 0), "number", 'step="any" min="0" required')}${field("源出点（秒）", "source_out", verified?.source_out ?? (old ? shot.source_out : Math.min(duration, mediaDuration(preferred))), "number", 'step="any" min="0.04" required')}</div>
      ${field("替换理由", "reason", verified ? "按首镜文字要求替换开场" : "", "text", 'required maxlength="1000"')}<div class="form-actions"><button type="submit" class="button primary">保留原案并创建候选</button></div></form>`,
      async (form) => {
        if (!active()) throw new Error("项目已切换，请重新打开镜头。");
        const v = Object.fromEntries(new FormData(form));
        const value = await ctx.api(
          route + "/" + encodeURIComponent(original.id) + "/replace-shot",
          {
            method: "POST",
            body: {
              expected_revision: original.version,
              shot_index: index,
              asset_id: v.asset_id,
              ...(verified?.asset_id === v.asset_id
                ? { expected_asset_version: verified.asset_version }
                : {}),
              source_in: Number(v.source_in),
              source_out: Number(v.source_out),
              reason: v.reason.trim(),
            },
          },
        );
        await applySession(value);
        draw();
      },
    );
    const input = $("select", d);
    input.onchange = () => {
      const a = candidates.find((r) => r.id === input.value);
      $("[name=source_in]", d).value = 0;
      $("[name=source_out]", d).value = Math.min(
        duration,
        mediaDuration(a),
      ).toFixed(3);
    };
  }
  function wire() {
    const form = $("#production-form", root);
    const startNew = () => {
      detail = null;
      draft.newTask = true;
      draft.selectedSession = "";
      highlighted = "";
      shotIndex = -1;
      issue = "";
      persist();
      draw();
    };
    if (form) {
      form.onsubmit = submit;
      form.oninput = (e) => {
        const t = e.target;
        if (t.dataset.source) {
          delete draft.explorationPlan;
          draft.assetIds = $$("[data-source]:checked", form).map(
            (c) => c.dataset.source,
          );
          $("h2 small", form).textContent =
            draft.assetIds.length + " / " + sources().length;
        } else if (t.name) {
          draft[t.name] = t.value;
        }
        persist();
      };
      form.elements.mode.onchange = (e) => {
        draft.mode = e.target.value;
        persist();
        draw();
      };
      const drop = $(".director-sources", form);
      drop.ondragover = (e) => {
        e.preventDefault();
        drop.classList.add("drag-over");
      };
      drop.ondragleave = () => drop.classList.remove("drag-over");
      drop.ondrop = (e) => {
        e.preventDefault();
        drop.classList.remove("drag-over");
        importFiles([...e.dataTransfer.files]);
      };
      $("[data-production-files]", form).onchange = (e) =>
        importFiles([...e.target.files]);
    }
    const history = $("[name=history]", root);
    if (history)
      history.onchange = async () => {
        const id = history.value;
        if (!id) {
          startNew();
          return;
        }
        await work("正在读取任务…", () => loadDetail(id));
      };
    ctx.bind(
      {
        upload: () => $("[data-production-files]", root)?.click(),
        "project-settings": () => ctx.newProject(project),
        "shot-view": (b) => {
          shotView = b.dataset.view;
          shotIndex = 0;
          highlighted = "";
          draw();
        },
        narration: () => openNarration(ctx),
        "voice-studio": () => openVoiceStudio(ctx),
        soundtrack: () => openSoundtrack(ctx),
        "explore-assets": () =>
          exploration(ctx, (candidates, plan) => {
            const available = new Set(sources().map((r) => r.id));
            const chosen = candidates.map((c) =>
              available.has(c.asset.id) ? c.asset.id : c.asset_id,
            );
            if (chosen.some((id) => !available.has(id)))
              throw new Error("素材已经变化，请重新核对。");
            draft.assetIds = [...new Set(chosen)];
            draft.explorationPlan = { id: plan.id, version: plan.version };
            persist();
            draw();
            ctx.toast("已采用此方案的候选素材。");
          }),
        "search-production-assets": () =>
          openAssetSearch(ctx, async (hit) => {
            if (!active()) return;
            const ids = new Set(sources().map((r) => r.id));
            const chosen = ids.has(hit.version_id)
              ? hit.version_id
              : hit.asset_id;
            if (!ids.has(chosen))
              throw new Error("此版本已经不可用，请重新检索。");
            delete draft.explorationPlan;
            draft.assetIds = [...new Set([...selected(), chosen])];
            persist();
            draw();
            ctx.toast("已加入本次制作素材。");
          }),
        "replace-shot": () => replaceShot(),
        "replace-opening": (b) => {
          const option =
            detail?.plan?.data?.opening_check?.alternatives?.[
              Number(b.dataset.alternative)
            ];
          if (option) replaceShot(0, option);
        },
        "review-content": () =>
          ctx.approveArtifact(detail.artifact, "content_check"),
        "review-rights": () =>
          ctx.approveArtifact(detail.artifact, "rights_check"),
        publication: () => ctx.publicationForm(detail.artifact),
        preflight: (b) =>
          ctx.preflightPublication(
            ctx.records("Publication").find((r) => r.id === b.dataset.id),
          ),
        receipt: (b) =>
          ctx.receiptForm(
            ctx.records("Publication").find((r) => r.id === b.dataset.id),
          ),
        "generate-shot": () => {
          const shot = cuts()[shotIndex];
          if (!shot) throw new Error("先点选需要新候选的镜头。");
          generated.prefill(
            "镜头 " + (shotIndex + 1) + " · " + shot.name,
            detail.session.data.goal,
            detail.session.id,
          );
        },
        "generate-gap": (b) => {
          const gap = arr(detail.plan?.data.missing)[Number(b.dataset.gap)];
          if (gap)
            generated.prefill(
              gap,
              detail.session.data.goal + "\n" + gap,
              detail.session.id,
            );
        },
        models: () => ctx.modelList(),
        "new-task": startNew,
        "preview-source": (b) => {
          highlighted = b.dataset.id;
          renderPreview();
        },
        "preview-shot": (b) => {
          shotIndex = Number(b.dataset.shot);
          highlighted = "";
          $$("[data-shot]", root).forEach((el) =>
            el.setAttribute("aria-pressed", String(el === b)),
          );
          renderPreview();
        },
        approve: () => advance("approve"),
        render: () => advance("render"),
        "recover-render": () =>
          work("正在核对本次渲染…", async () => {
            const session = detail.session;
            await applySession(
              await ctx.api(
                route + "/" + encodeURIComponent(session.id) + "/render",
                { method: "POST", body: session.data.render_request },
              ),
            );
          }),
        reconcile: () =>
          work("正在核对原请求…", async () => {
            if (!draft.pending) return;
            await sendRequest(JSON.parse(draft.pending.body));
          }),
        reload: () =>
          work("正在核对任务状态…", async () => {
            await loadDetail(detail.session.id);
            await refreshSessions();
          }),
        watch: () => {
          shotIndex = -1;
          highlighted = "";
          renderPreview();
          $(".director-player", root)
            ?.play()
            .catch(() => {});
        },
      },
      root,
    );
  }
  try {
    await refreshSessions();
  } catch (err) {
    issue = "制作记录暂时无法读取。" + err.message;
  }
  if (!active()) return;
  const current =
    sessions.find((s) => s.id === draft.selectedSession) ||
    (!draft.newTask && sessions[0]);
  if (current) {
    await loadDetail(current.id);
    if (detail?.artifact) shotIndex = -1;
  }
  draw();
}
