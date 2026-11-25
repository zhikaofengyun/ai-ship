import { chooseBatchMarket } from "./batch-market.js";
import { chooseBatchContract } from "./batch-contracts.js";
import { chooseBatchWorkflow } from "./batch-workflow.js";
import {
  $,
  $$,
  area,
  btn,
  esc,
  field,
  icon,
  iconBtn,
  modal,
  select,
  uid,
} from "./ui.js";
import { mediaDuration, mediaLibrary, mediaName, mediaType } from "./media.js";
import { openAssetSearch } from "./asset-search.js";
import { installInspectorLayout } from "./workspace-layout.js";
import { mountBatchQueue } from "./batch-queue.js";

const number = (label, name, value, attrs) =>
  field(label, name, value, "number", attrs);
const slot = () => ({ choices: [] });
const maxCount = 100000;
const optionCount = (slots) =>
  slots.some((s) => !s.generate && !s.choices.length)
    ? 0
    : slots.reduce(
        (a, s) =>
          Math.min(maxCount, a * (s.generate ? maxCount : s.choices.length)),
        1,
      );
const pin = (r) => ({ id: r.id, version: r.version });

export async function mountBatches(root, ctx, requestedWorkflow) {
  const pid = ctx.requireProject(),
    project = ctx.project().data;
  const actor = ctx.state.health.actor;
  const key = [
    "ship-batch-draft-v1",
    actor.tenant_id,
    actor.actor_id,
    pid,
  ].join(":");
  let draft;
  try {
    draft = JSON.parse(sessionStorage.getItem(key));
  } catch {
    /* unavailable */
  }
  draft ||= {
    name: "",
    count: 1,
    width: project.width || 1080,
    height: project.height || 1920,
    fps: 30,
    output: "timeline",
    seed: 0,
    slots: [slot()],
  };
  let disposed = false,
    submitting = false,
    profiles = [],
    timelines = [],
    profileError = "";
  const cache = new Map(ctx.state.records.map((r) => [r.id, r]));
  const persist = () => {
    try {
      sessionStorage.setItem(key, JSON.stringify(draft));
    } catch {
      /* storage quota */
    }
  };
  const lookup = async (id) => {
    if (!cache.has(id))
      cache.set(id, await ctx.api("/records/" + encodeURIComponent(id)));
    return cache.get(id);
  };
  root.innerHTML = `<div class="page batch-page"><header class="batch-head"><h1>批量制作</h1><div class="batch-controls"><a href="#production" class="button small ghost">单条制作</a><a href="#workflow" class="button small ghost">流程画布</a><button type="button" class="button small ghost" data-pane-toggle aria-controls="batch-inspector"></button><button type="button" class="button small ghost" data-pane-move></button></div></header>
    <div class="adjustable-layout"><section class="batch-main" aria-label="创建批次"></section><aside class="batch-inspector" id="batch-inspector" data-inspector aria-label="批次队列"></aside></div></div>`;
  const main = $(".batch-main", root);
  const releaseLayout = installInspectorLayout(
    $(".adjustable-layout", root),
    $(".batch-head", root),
    "ship-batch-inspector-v1",
    "队列",
  );
  const queue = mountBatchQueue($("#batch-inspector", root), ctx, pid);
  ctx.state.cleanup.push(() => {
    disposed = true;
    releaseLayout();
    queue.dispose();
  });
  const notice = (msg) => {
    if (!disposed) $("[data-notice]", main).textContent = msg;
  };
  const profile = (s) => profiles.find((p) => p.id === s.generate?.profile_id);
  function choiceMarkup(c, i) {
    return `<li data-choice="${i}"><div class="batch-source-name"><img src="/api/v1/media/${encodeURIComponent(c.asset_id)}/frame?time=${encodeURIComponent(c.source_in)}&amp;width=144" alt="" width="72" height="42" loading="lazy" decoding="async"><strong>${esc(c.name)} <small>v${esc(c.version)}</small></strong></div>
      ${number("入点（秒）", "source_in", c.source_in, 'min="0" step="any" required')}
      ${number("出点（秒）", "source_out", c.source_out, 'min="0.04" step="any" required')}
      ${iconBtn("移除这个候选", "remove-choice", "close")}</li>`;
  }
  function slotMarkup(s, i) {
    const gen = s.generate;
    const tpl = !!draft.template;
    const imageChoices = mediaLibrary(ctx.state.records).filter(
      (r) => mediaType(r) === "image",
    );
    return `<section class="batch-slot" data-slot="${i}"><header class="batch-slot-head"><strong>镜头 ${i + 1}<small>${gen ? "AI 生成" : "素材剪取"}${tpl ? " · 样片位置" : ""}</small></strong><div class="batch-controls">
      ${i > 0 && !tpl ? iconBtn("将此镜头前移", "up-slot", "up") : ""}
      ${i < draft.slots.length - 1 && !tpl ? iconBtn("将此镜头后移", "down-slot", "down") : ""}
      ${btn(gen ? "用素材" : "用生成", "switch-kind", "", "button small ghost")}
      ${!tpl ? iconBtn("移除此镜头位", "remove-slot", "close") : ""}</div></header>
      ${
        gen
          ? `<div class="form-grid">
        ${select("生成服务", "profile_id", [["", "选择视频模型"], ...profiles.map((p) => [p.id, p.label || p.model])], gen.profile_id, "required")}
        ${number("使用时长（秒）", "duration", gen.duration, 'min="0.04" max="3600" step="any" required')}
        ${area("画面描述", "prompt", gen.prompt, 'maxlength="6000" required')}
        ${profile(s)?.requires_reference ? select("项目参考图", "reference_asset_id", [["", "选择参考图"], ...imageChoices.map((r) => [r.id, mediaName(r)])], gen.reference_asset_id, "required") : ""}
      </div>${!profiles.length ? `<p class="batch-notice">${esc(profileError || "当前未配置视频生成服务。")}</p>` : ""}`
          : `<ul class="batch-segments">${s.choices.map(choiceMarkup).join("")}</ul><div class="batch-controls">${btn("选素材", "choose-assets", "plus", "button small secondary")}${btn("检索全部素材", "search-asset", "search", "button small ghost")}</div>`
      }
    </section>`;
  }
  function draw() {
    if (disposed) return;
    const canWrite = ["owner", "editor", "worker"].includes(actor.role);
    main.innerHTML = `<form class="batch-form"><div class="form-grid">
      ${field("批次名称", "name", draft.name, "text", 'maxlength="150" placeholder="例如：冬季系列开场测试" required')}
      ${select("交付目标", "output", [["timeline", "可编辑时间线"], ...(actor.role === "owner" ? [["render", "批量渲染并导出成片"]] : [])], draft.output)}
      </div><div><h2>镜头组合</h2>
      ${draft.template ? `<p class="batch-meta">沿用 ${esc(draft.template.name)} · v${esc(draft.template.version)} ${btn("解除样片绑定", "unlink-template", "", "button small ghost")}</p>` : `<div class="batch-controls">${btn("从手剪时间线开始", "use-template", "scissors", "button small ghost")}</div>`}
      <div data-slots>${draft.slots.map(slotMarkup).join("")}</div>
      ${!draft.template ? `<div class="batch-controls">${btn("添加素材镜头", "add-slot", "plus", "button secondary")}${btn("添加生成镜头", "add-generation", "spark", "button secondary")}</div>` : ""}
      </div>
      <div class="form-grid">${number("本批作品数", "count", draft.count, 'min="1" max="100000" step="1" required')}
      ${number("组合种子", "seed", draft.seed, 'min="0" max="2147483647" step="1" required')}</div>
      <div data-forecast class="batch-meta"></div>
      ${draft.slots.some((s) => s.generate) ? `<div class="form-grid">${number("最多生成调用次数", "max_generation_calls", draft.max_generation_calls ?? "", 'min="1" step="1" placeholder="默认按本批需求"')}${number("同时在途作品数", "generation_window", draft.generation_window ?? 4, 'min="1" max="16" step="1" required')}</div>` : ""}
      <div class="batch-controls">${btn(draft.tail ? "更换后续流程" : "串接后续工作流", "choose-workflow", "flow", "button small secondary")}${draft.tail ? `<span>${esc(draft.tail.name)} · v${esc(draft.tail.version)} · ${esc(draft.tail.label)}</span>${btn("移除流程", "unlink-workflow", "", "button small ghost")}` : ""}</div>
      <div class="batch-controls">${btn(draft.contract ? "更换验收版本" : "添加验收要求", "choose-contract", "checkcircle", "button small secondary")}${draft.contract ? `<span>${esc(draft.contract.name)} · v${esc(draft.contract.version)}</span>${btn("移除要求", "unlink-contract", "", "button small ghost")}` : ""}</div>
      <div class="batch-controls">${btn(draft.marketBrief ? "更换市场简报" : "添加市场简报", "choose-market", "layers", "button small secondary")}${draft.marketBrief ? `<span>${esc(draft.marketBrief.name)} · v${esc(draft.marketBrief.version)} · ${esc(draft.marketBrief.market)} / ${esc(draft.marketBrief.language)}</span>${btn("移除简报", "unlink-market", "", "button small ghost")}` : ""}</div>
      <details class="batch-options"><summary>输出规格</summary><div class="form-grid">
      ${number("宽度", "width", draft.width, 'min="64" max="4096" step="2" required')}
      ${number("高度", "height", draft.height, 'min="64" max="4096" step="2" required')}
      ${select(
        "帧率",
        "fps",
        [24, 25, 30, 50, 60].map((f) => [f, f + " fps"]),
        draft.fps,
      )}</div></details>
      <div class="batch-output"><button class="button primary" type="submit" ${!canWrite || submitting ? "disabled" : ""}>${icon("layers")}${draft.pending ? "核对并提交原批次" : draft.output === "render" ? "提交批量渲染" : "提交批量剪辑"}</button></div>
      <p role="status" data-notice class="batch-notice"></p></form>`;
    forecast();
  }
  function forecast() {
    const n = optionCount(draft.slots);
    const jobs =
      draft.slots.filter((s) => s.generate).length * Number(draft.count);
    $("[data-forecast]", main).textContent = !n
      ? "先为每个素材镜头选择至少一个候选。"
      : draft.slots.some((s) => s.generate)
        ? "本批将提交 " +
          jobs.toLocaleString() +
          " 次模型生成。每项使用独立种子。"
        : "当前可选组合" +
          (n === maxCount ? "至少 " : " ") +
          n.toLocaleString() +
          " 种";
  }
  function candidate(rec, start = 0, end = null, duration = null) {
    const length = mediaDuration(rec);
    return {
      asset_id: rec.id,
      version: rec.version,
      name: mediaName(rec),
      source_in: start,
      source_out: end ?? Math.min(length, duration ?? 3),
      length,
    };
  }
  function add(s, rec) {
    if (s.choices.length >= 64) throw new Error("每个镜头最多 64 个候选。");
    const c = candidate(rec, 0, null, s.requiredDuration);
    if (!c.length || (s.requiredDuration && c.length < s.requiredDuration))
      throw new Error("素材时长不足。");
    if (
      s.choices.some(
        (v) =>
          v.asset_id === c.asset_id &&
          Number(v.source_in) === 0 &&
          Number(v.source_out) === c.source_out,
      )
    )
      return;
    s.choices.push(c);
  }
  function chooseLocal(s) {
    const all = mediaLibrary(ctx.state.records).filter(
      (r) => mediaType(r) === "video" && mediaDuration(r) > 0,
    );
    const dlg = modal(
      "选择候选素材",
      `<form data-form class="form-stack">${all.length ? `<div class="batch-pins">${all.map((r, i) => `<label><input type="checkbox" name="asset" value="${i}"><span>${esc(mediaName(r))}<small>${mediaDuration(r).toFixed(2)} 秒</small></span></label>`).join("")}</div><button type="submit" class="button primary">加入镜头</button>` : "<p>尚未读取到本项目的视频。可在制作台导入，或检索全部素材。</p>"}</form>`,
      async (form) => {
        const copy = { ...s, choices: [...s.choices] };
        for (const id of new FormData(form).getAll("asset"))
          add(copy, all[Number(id)]);
        if (!disposed) {
          s.choices = copy.choices;
          persist();
          draw();
        }
      },
    );
    return dlg;
  }
  async function useTemplate() {
    const list = await ctx.api(
      "/records?" +
        new URLSearchParams({ kind: "Timeline", project_id: pid, limit: 2000 }),
    );
    if (disposed) return;
    timelines = list.items || list;
    const d = modal(
      "沿用手剪时间线",
      `<form data-form class="form-stack">${select("项目时间线", "timeline", [["", "选择样片"], ...timelines.map((r) => [r.id, (r.data.name || r.id) + " · v" + r.version])], "", "required")}
      <small>样片会替换当前镜头组合。替代素材需匹配原选段时长。</small><button type="submit" class="button primary">使用镜头结构</button></form>`,
      async (f) => {
        const t = timelines.find((v) => v.id === f.elements.timeline.value);
        if (!t?.data.clips?.length) throw new Error("所选时间线没有镜头。");
        if (t.data.clips.length > 64) throw new Error("样片超过 64 个镜头。");
        const slots = await Promise.all(
          t.data.clips.map(async (c) => {
            const r = await lookup(c.asset_version_id || c.asset_id);
            return {
              requiredDuration: c.source_out - c.source_in,
              choices: [candidate(r, c.source_in, c.source_out)],
            };
          }),
        );
        if (!disposed) {
          draft.template = { ...pin(t), name: t.data.name || t.id };
          draft.slots = slots;
          for (const k of ["width", "height", "fps"])
            if (t.data.format?.[k]) draft[k] = t.data.format[k];
          persist();
          draw();
        }
      },
    );
    return d;
  }
  function payload() {
    if (Number(draft.count) > optionCount(draft.slots))
      throw new Error("作品数超过可用组合数，请添加候选或减少数量。");
    if (!draft.slots.length) throw new Error("至少需要一个镜头位。");
    const slots = draft.slots.map((s) => {
      if (s.generate) {
        const g = { ...s.generate, duration: Number(s.generate.duration) };
        if (!profile(s)?.requires_reference) {
          delete g.reference_asset_id;
          delete g.reference_asset_version;
        }
        return { generate: g };
      }
      if (!s.choices.length) throw new Error("每个镜头位至少需要一个候选。");
      return {
        choices: s.choices.map((c) => {
          const start = Number(c.source_in),
            end = Number(c.source_out);
          if (end <= start || end > c.length + 0.001)
            throw new Error("请检查「" + c.name + "」的入点和出点。");
          return {
            asset_id: c.asset_id,
            version: c.version,
            source_in: start,
            source_out: end,
          };
        }),
      };
    });
    return {
      project_id: pid,
      name: draft.name.trim(),
      slots,
      ...(draft.tail
        ? {
            workflow_tail: {
              id: draft.tail.id,
              version: draft.tail.version,
              node_id: draft.tail.node_id,
              input_path: draft.tail.input_path,
            },
          }
        : {}),
      ...(slots.some((s) => s.generate)
        ? {
            generation_window: Number(draft.generation_window || 4),
            ...(draft.max_generation_calls
              ? { max_generation_calls: Number(draft.max_generation_calls) }
              : {}),
          }
        : {}),
      output: draft.output,
      count: Number(draft.count),
      width: Number(draft.width),
      height: Number(draft.height),
      fps: Number(draft.fps),
      seed: Number(draft.seed),
      ...(draft.marketBrief ? { market_brief: pin(draft.marketBrief) } : {}),
      ...(draft.contract ? { acceptance_contract: pin(draft.contract) } : {}),
      ...(draft.template ? { template: pin(draft.template) } : {}),
    };
  }
  main.oninput = (e) => {
    const t = e.target;
    if (!t.name) return;
    const row = t.closest("[data-slot]");
    if (row) {
      const s = draft.slots[Number(row.dataset.slot)],
        l = t.closest("[data-choice]");
      if (l) s.choices[Number(l.dataset.choice)][t.name] = t.value;
      else if (s.generate) {
        s.generate[t.name] = t.value;
        if (t.name === "reference_asset_id")
          s.generate.reference_asset_version = cache.get(t.value)?.version;
      }
    } else draft[t.name] = t.value;
    if (t.name === "count") forecast();
    persist();
  };
  main.onchange = (e) => {
    if (e.target.name === "output") {
      delete draft.tail;
      persist();
    }
    if (["profile_id", "output"].includes(e.target.name)) draw();
  };
  main.onclick = async (e) => {
    const b = e.target.closest("[data-action]");
    if (!b || submitting) return;
    const op = b.dataset.action,
      i = Number(b.closest("[data-slot]")?.dataset.slot),
      s = draft.slots[i];
    try {
      if (op === "choose-market")
        return await chooseBatchMarket(ctx, pid, draft.marketBrief, (brief) => {
          if (!disposed) {
            draft.marketBrief = brief;
            persist();
            draw();
          }
        });
      if (op === "unlink-market") {
        delete draft.marketBrief;
        persist();
        draw();
        return;
      }
      if (op === "choose-contract")
        return await chooseBatchContract(ctx, pid, draft.contract, (c) => {
          if (!disposed) {
            draft.contract = c;
            persist();
            draw();
          }
        });
      if (op === "unlink-contract") {
        delete draft.contract;
        persist();
        draw();
        return;
      }
      if (op === "choose-workflow") return await pickWorkflow();
      if (op === "unlink-workflow") {
        delete draft.tail;
        persist();
        draw();
        return;
      }
      if (op === "choose-assets") return chooseLocal(s);
      if (op === "search-asset")
        return await openAssetSearch(ctx, async (hit) => {
          const rec = await lookup(hit.version_id);
          if (!disposed) {
            add(s, rec);
            persist();
            draw();
          }
        });
      if (op === "use-template") return await useTemplate();
      if (op === "switch-kind") {
        if (s.generate) {
          s.previousGenerator = s.generate;
          delete s.generate;
          s.choices ||= [];
        } else {
          s.generate = s.previousGenerator || {
            profile_id: profiles[0]?.id || "",
            prompt: "",
            duration: s.requiredDuration || 3,
          };
        }
      } else if (op === "unlink-template") {
        delete draft.template;
        draft.slots.forEach((s) => delete s.requiredDuration);
      } else if (op === "remove-choice")
        s.choices.splice(Number(b.closest("[data-choice]").dataset.choice), 1);
      else if (op === "remove-slot") {
        draft.slots.splice(i, 1);
        if (!draft.slots.length) draft.slots.push(slot());
      } else if (op === "up-slot" || op === "down-slot") {
        const j = i + (op === "up-slot" ? -1 : 1);
        [draft.slots[i], draft.slots[j]] = [draft.slots[j], draft.slots[i]];
      } else if (op === "add-slot" || op === "add-generation") {
        if (draft.slots.length >= 64) throw new Error("每批最多 64 个镜头。");
        const next =
          op === "add-slot"
            ? slot()
            : {
                generate: {
                  profile_id: profiles[0]?.id || "",
                  prompt: "",
                  duration: 3,
                },
              };
        if (
          op === "add-generation" &&
          draft.slots.length === 1 &&
          !draft.slots[0].generate &&
          !draft.slots[0].choices?.length
        )
          draft.slots[0] = next;
        else draft.slots.push(next);
      } else return;
      persist();
      draw();
    } catch (err) {
      notice(err.message);
    }
  };
  const pickWorkflow = (id) =>
    chooseBatchWorkflow(
      ctx,
      pid,
      draft.output,
      draft.tail,
      id,
      (tail) => {
        draft.tail = tail;
        persist();
        draw();
      },
      () => !disposed,
    );
  main.onsubmit = async (e) => {
    e.preventDefault();
    if (submitting) return;
    try {
      const body = draft.pending || { ...payload(), idempotency_key: uid() };
      draft.pending = body;
      persist();
      submitting = true;
      draw();
      notice("正在登记批次…");
      const result = await ctx.api("/production/batches", {
        method: "POST",
        body,
      });
      delete draft.pending;
      persist();
      if (!disposed) {
        queue.selectBatch(result.id);
        ctx.toast("已登记 " + result.total + " 条作品。");
      }
    } catch (err) {
      if (
        err.status >= 400 &&
        err.status < 500 &&
        ![408, 429].includes(err.status)
      ) {
        delete draft.pending;
        persist();
      }
      if (!disposed) {
        submitting = false;
        draw();
        notice(err.message);
      }
      return;
    } finally {
      submitting = false;
    }
    draw();
  };
  draw();
  try {
    profiles = (
      await ctx.api("/production/generation/profiles")
    ).profiles.filter((p) => p.output_kind === "video");
  } catch (err) {
    profileError = err.message;
  }
  if (!disposed && draft.slots.some((s) => s.generate)) draw();
  if (requestedWorkflow && !disposed) await pickWorkflow(requestedWorkflow);
}
