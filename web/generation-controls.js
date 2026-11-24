import { uploadFile } from "./upload.js";
import { mediaType } from "./media.js";
import { $, $$, btn, esc, field, icon, select, uid } from "./ui.js";

const endpoint = "/production/generation";
const statusName = {
  submitting: "正在提交",
  queued: "排队中",
  running: "正在生成",
  outcome_unknown: "供应方状态待核对",
  failed: "失败",
  cancel_requested: "已请求取消",
  cancelled: "已取消",
  ready: "文件已到库",
};
const needsCheck = new Set([
  "submitting",
  "queued",
  "running",
  "outcome_unknown",
  "cancel_requested",
]);

export function generationPanel(ctx, projectId, draft, persist, onAsset) {
  let profiles = [],
    jobs = [],
    loaded = false,
    open = false,
    busy = "",
    error = "",
    serial = 0,
    cancelId = "";
  const prefs = (draft.generation ||= { seed: 0, prompt: "", slot: "" });
  let host = null;
  let currentRun = "";
  const active = () => host?.isConnected && ctx.state.projectId === projectId;
  const remember = () => {
    draft.generation = prefs;
    persist();
  };
  const jobById = (id) => jobs.find((j) => j.id === id);
  const recentStills = new Map();
  const pin = (a) => JSON.stringify([a.id, a.version]);
  const stills = () => {
    const unique = new Map(recentStills);
    for (const a of ctx.records("Asset"))
      if (a.project_id === projectId && mediaType(a) === "image")
        unique.set(a.id, a);
    return [...unique.values()];
  };
  const profile = () =>
    profiles.find((p) => p.id === prefs.profile_id) || profiles[0];
  const imageByPin = (value) => stills().find((a) => pin(a) === value);
  function imageFields() {
    if (!profile()?.requires_reference) return "";
    const items = stills(),
      a = imageByPin(prefs.reference_pin);
    return `<div class="generation-reference">${select("项目参考图", "reference_pin", [["", "选择参考图"], ...items.map((r) => [pin(r), (r.data.name || r.data.filename || "项目图片") + " · v" + r.version])], a ? pin(a) : "", "required")}${btn("导入参考图", "gen-import-reference", "upload", "button small ghost")}<input type="file" data-reference-file accept="image/png,image/jpeg,image/webp" hidden>${a ? `<img class="generation-still" alt="选中的项目参考图" src="${esc(ctx.assetUrl(a))}">` : ""}</div>`;
  }
  function jobMarkup(j) {
    const d = j.data,
      stage = d.stage;
    const isCurrentReference =
      profile()?.requires_reference &&
      d.asset_ref &&
      pin(d.asset_ref) === prefs.reference_pin;
    return `<li data-generation-job="${esc(j.id)}"><strong>${esc(d.slot || d.prompt || "生成镜头")}</strong><div class="generation-count"><span>${esc(statusName[stage] || "等待核对")}</span><small>${esc(d.model || "")}</small></div>
    ${d.issue ? `<p>${esc(d.issue.message || d.issue.code)}</p>` : ""}<div class="generation-row">
    ${needsCheck.has(stage) ? btn("核对状态", "gen-refresh", "history", "button small secondary", `data-job="${esc(j.id)}"`) : ""}
    ${["queued", "running", "outcome_unknown"].includes(stage) ? btn(cancelId === j.id ? "确认取消" : "请求取消", "gen-cancel", "stop", "button small ghost", `data-job="${esc(j.id)}"`) : ""}
    ${stage === "ready" && d.asset_ref && d.output_kind !== "image" ? btn("用作制作素材", "gen-use", "plus", "button small secondary", `data-job="${esc(j.id)}"`) : ""}</div>
    ${
      stage === "ready" && d.output_kind === "image" && d.asset_ref
        ? `${isCurrentReference ? "" : `<img class="generation-still" src="/api/v1/media/${encodeURIComponent(d.asset_ref.id)}/file" loading="lazy" alt="生成的关键帧候选">`}<form data-hold-form="${esc(j.id)}" class="generation-hold">${field("定格时长（秒）", "duration", d.hold_params?.duration ?? 3, "number", 'step="any" min="1" max="60" required')}${select(
            "帧率",
            "fps",
            [
              [24, "24 fps"],
              [25, "25 fps"],
              [30, "30 fps"],
              [50, "50 fps"],
              [60, "60 fps"],
            ],
            d.hold_params?.fps || 30,
          )}<button class="button small secondary" type="submit">定格并加入制作</button></form>`
        : ""
    }</li>`;
  }
  function repaint() {
    if (!active()) return;
    host.innerHTML = `<details class="director-generation" ${open ? "open" : ""}><summary>${icon("spark")}生成补充镜头</summary><div class="generation-body">
    ${
      loaded
        ? profiles.length
          ? `<form data-generation-form class="generation-form">${select(
              "生成模型",
              "profile_id",
              profiles.map((p) => [
                p.id,
                (p.label || p.model) +
                  (p.output_kind === "image" ? " · 图片" : " · 视频"),
              ]),
              prefs.profile_id || profiles[0]?.id,
              "required",
            )}
    ${imageFields()}${field("分镜位置（可选）", "slot", prefs.slot || "", "text", 'maxlength="160" placeholder="如：开场产品特写"')}<label class="field"><span>画面描述</span><textarea name="prompt" required maxlength="6000" rows="3">${esc(prefs.prompt)}</textarea></label>
    ${field("随机种子", "seed", prefs.seed ?? 0, "number", 'step="1" min="0" max="2147483647" required')}
    <button class="button primary" type="submit">${icon("spark")}生成候选</button></form>`
          : '<p class="generation-empty">此工作区尚未配置生成服务。已有视频可直接导入。</p>'
        : ""
    }
    ${prefs.pending ? btn("核对上次提交", "gen-resume", "history", "button small secondary") : ""}
    ${jobs.length ? `<ul class="generation-list">${jobs.map(jobMarkup).join("")}</ul>` : ""}
    ${!loaded && !busy ? btn("读取生成服务", "gen-reload", "history", "button small secondary") : ""}
    <p role="status" class="generation-feedback" ${busy || error ? "" : "hidden"}>${esc(busy || error)}</p></div></details>`;
    const details = $("details", host);
    details.ontoggle = () => {
      const opening = details.open && !open;
      open = details.open;
      if (opening && !loaded && !busy) run("正在读取生成服务…", load);
    };
    const form = $("[data-generation-form]", host);
    if (form) {
      form.oninput = (e) => {
        if (e.target.name) prefs[e.target.name] = e.target.value;
        remember();
      };
      const profileSelect = form.elements.profile_id;
      profileSelect.onchange = () => {
        prefs.profile_id = profileSelect.value;
        remember();
        repaint();
        host.querySelector("[name=profile_id]")?.focus({ preventScroll: true });
      };
      const reference = form.elements.reference_pin;
      if (reference)
        reference.onchange = () => {
          prefs.reference_pin = reference.value;
          remember();
          repaint();
          host
            .querySelector("[name=reference_pin]")
            ?.focus({ preventScroll: true });
        };
      const fileInput = $("[data-reference-file]", form);
      if (fileInput)
        fileInput.onchange = () => {
          const file = fileInput.files[0];
          if (!file) return;
          run("正在导入项目参考图…", async () => {
            const result = await uploadFile(ctx.api, projectId, file);
            if (!active()) return;
            if (!result.asset || mediaType(result.asset) !== "image")
              throw new Error("请选择可解码的图片作为参考。");
            recentStills.set(result.asset.id, result.asset);
            prefs.reference_pin = pin(result.asset);
            remember();
            await ctx.reloadData();
          });
        };
      form.onsubmit = (e) => {
        e.preventDefault();
        if (busy) return;
        const values = Object.fromEntries(new FormData(form));
        const data = {
          project_id: projectId,
          profile_id: values.profile_id,
          prompt: values.prompt.trim(),
          seed: Number(values.seed),
        };
        if (profile()?.requires_reference) {
          const image = imageByPin(values.reference_pin);
          if (!image) {
            form.elements.reference_pin.focus();
            return;
          }
          data.reference_asset_id = image.id;
          data.reference_asset_version = image.version;
        }
        if (values.slot.trim()) data.slot = values.slot.trim();
        if (prefs.source_run_id && prefs.source_run_id === currentRun)
          data.source_run_id = prefs.source_run_id;
        if (!data.prompt) {
          form.elements.prompt.focus();
          return;
        }
        const body = JSON.stringify(data);
        if (prefs.pending && prefs.pending.body !== body) {
          error = "上次提交的结果待核对。";
          repaint();
          return;
        }
        prefs.pending ||= { body, key: uid() };
        remember();
        run("正在提交生成请求…", submit);
      };
    }
    for (const hold of $$("[data-hold-form]", host)) {
      hold.onsubmit = (e) => {
        e.preventDefault();
        if (busy) return;
        const d = Object.fromEntries(new FormData(hold));
        const id = hold.dataset.holdForm;
        run("正在定格并校验视频…", async () =>
          apply(
            await ctx.api(endpoint + "/" + encodeURIComponent(id) + "/hold", {
              method: "POST",
              body: { duration: Number(d.duration), fps: Number(d.fps) },
            }),
          ),
        );
      };
    }
    host.onclick = async (e) => {
      const b = e.target.closest("[data-action]");
      if (!b || busy || b.disabled) return;
      const j = jobById(b.dataset.job);
      if (b.dataset.action === "gen-import-reference")
        $("[data-reference-file]", host)?.click();
      if (b.dataset.action === "gen-resume")
        await run("正在核对原请求…", submit);
      if (b.dataset.action === "gen-reload")
        await run("正在读取生成服务…", load);
      if (b.dataset.action === "gen-refresh" && j)
        await run("正在核对供应方任务…", async () =>
          apply(
            await ctx.api(
              endpoint + "/" + encodeURIComponent(j.id) + "/refresh",
              { method: "POST" },
            ),
          ),
        );
      if (b.dataset.action === "gen-cancel" && j) {
        if (cancelId !== j.id) {
          cancelId = j.id;
          repaint();
          return;
        }
        await run("正在请求取消…", async () =>
          apply(
            await ctx.api(
              endpoint + "/" + encodeURIComponent(j.id) + "/cancel",
              { method: "POST" },
            ),
          ),
        );
      }
      if (b.dataset.action === "gen-use" && j?.data.asset_ref)
        await run("正在读取生成素材…", async () =>
          onAsset(j.data.asset_ref.id),
        );
    };
    if (busy)
      $$("button,textarea,input,select", host).forEach(
        (el) => (el.disabled = true),
      );
  }
  async function apply(value) {
    if (!value.job) throw new Error("未收到本次生成任务记录。");
    jobs = [value.job, ...jobs.filter((j) => j.id !== value.job.id)];
    if (
      value.asset?.kind === "Asset" &&
      mediaType(value.asset) === "image" &&
      value.asset.project_id === projectId
    )
      recentStills.set(value.asset.id, value.asset);
    if (
      value.job.data.stage === "ready" &&
      value.asset &&
      mediaType(value.asset) === "video"
    )
      await onAsset(value.asset.id);
    cancelId = "";
  }
  async function submit() {
    if (!prefs.pending) return;
    try {
      const value = await ctx.api(endpoint, {
        method: "POST",
        body: {
          ...JSON.parse(prefs.pending.body),
          idempotency_key: prefs.pending.key,
        },
      });
      prefs.pending = null;
      remember();
      await apply(value);
    } catch (e) {
      if (e.status >= 400 && e.status < 500 && ![409, 429].includes(e.status)) {
        prefs.pending = null;
        remember();
      }
      throw e;
    }
  }
  async function load() {
    const [p, j] = await Promise.all([
      ctx.api(endpoint + "/profiles"),
      ctx.api(endpoint + "?project_id=" + encodeURIComponent(projectId)),
    ]);
    profiles = p.profiles;
    jobs = j.jobs;
    const loadedIds = new Set(stills().map((a) => a.id));
    const refs = new Set(
      jobs
        .filter(
          (j) => j.data.stage === "ready" && j.data.output_kind === "image",
        )
        .map((j) => j.data.asset_ref?.id)
        .filter((id) => id && !loadedIds.has(id)),
    );
    await Promise.all(
      [...refs].map(async (id) => {
        try {
          const a = await ctx.api("/records/" + encodeURIComponent(id));
          if (a.project_id === projectId && mediaType(a) === "image")
            recentStills.set(a.id, a);
        } catch (err) {
          if (![403, 404].includes(err.status)) throw err;
        }
      }),
    );
    loaded = true;
  }
  async function run(label, fn) {
    if (busy) return;
    busy = label;
    error = "";
    const request = ++serial;
    repaint();
    try {
      await fn();
    } catch (e) {
      error = e.message || String(e);
    } finally {
      if (request === serial) {
        busy = "";
        repaint();
      }
    }
  }
  return {
    prefill(slot, prompt, sourceRun) {
      if (prefs.pending) throw new Error("请先核对上次生成请求。");
      prefs.slot = slot.slice(0, 160);
      prefs.prompt = String(prompt || "").slice(0, 6000);
      prefs.source_run_id = sourceRun;
      remember();
      open = true;
      repaint();
      if (!loaded && !busy) run("正在读取生成服务…", load);
      host?.querySelector("summary")?.focus();
      host?.scrollIntoView({ block: "nearest" });
    },
    mount(target, initial = false, runId = "") {
      host = target;
      currentRun = runId;
      if (
        !prefs.pending &&
        prefs.source_run_id &&
        prefs.source_run_id !== runId
      ) {
        prefs.source_run_id = null;
        prefs.slot = "";
        remember();
      }

      if (initial && !open) {
        open = true;
      }
      repaint();
      if (open && !loaded && !busy) run("正在读取生成服务…", load);
    },
  };
}
