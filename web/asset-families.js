import { esc, field, select, area, modal, toast } from "./ui.js";

const pin = (r) => ({ id: r.id, version: r.version });
const label = (r) => r.data.filename || r.data.name || r.id;
const opts = (items) => items.map((x) => [x.id, label(x)]);
function form(title, html, submitLabel, handler) {
  const key = crypto.randomUUID();
  return modal(
    title,
    `<form data-form class="form-grid">${html}<div class="dialog-actions"><button class="button primary" type="submit">${esc(submitLabel)}</button></div></form>`,
    async (el) => handler(new FormData(el), key),
  );
}
function post(ctx, route, key, payload) {
  return ctx.api("/production/asset-families/" + route, {
    method: "POST",
    body: { project_id: ctx.requireProject(), request_id: key, ...payload },
  });
}
function review(ctx, audit) {
  form(
    "复核近似区间",
    field("素材族名称", "name", "近似素材", "text", "required") +
      area("人工对照依据", "reason", "", "required"),
    "确认近似关系",
    async (v, key) => {
      await post(ctx, "group", key, {
        mode: "review_near",
        name: v.get("name"),
        reason: v.get("reason"),
        audit: pin(audit),
      });
      await ctx.reloadData();
      await ctx.refresh();
      toast("已登记近似区间及复核依据");
    },
  );
}
export function compare(ctx) {
  const assets = ctx
    .records("AssetVersion")
    .filter((r) => !["quarantine", "local_cold"].includes(r.data.storage_tier));
  if (assets.length < 2)
    throw new Error("需要至少两个可用的素材版本进行对照。");
  const controls = (side, i) =>
    select(
      i === 0 ? "第一个素材" : "第二个素材",
      side,
      opts(assets),
      assets[i].id,
      "required",
    ) +
    field(
      "起点（秒）",
      side + "_start",
      "0",
      "number",
      'step="any" min="0" required',
    ) +
    field(
      "终点（秒，留空至片尾）",
      side + "_end",
      "",
      "number",
      'step="any" min="0"',
    );
  form(
    "素材区间对照",
    controls("left", 0) +
      controls("right", 1) +
      field(
        "画面相似阈值",
        "visual",
        "0.9",
        "number",
        'min="0.5" max="1" step="0.01" required',
      ) +
      field(
        "声音相似阈值",
        "audio",
        "0.85",
        "number",
        'min="0.5" max="1" step="0.01" required',
      ),
    "对照原片",
    async (v, key) => {
      const side = (s) => ({
        ...pin(assets.find((r) => r.id === v.get(s))),
        start: Number(v.get(s + "_start")),
        end: v.get(s + "_end") ? Number(v.get(s + "_end")) : null,
      });
      const { audit } = await post(ctx, "compare", key, {
        left: side("left"),
        right: side("right"),
        visual_threshold: Number(v.get("visual")),
        audio_threshold: Number(v.get("audio")),
      });
      await ctx.reloadData();
      await ctx.refresh();
      const data = audit.data,
        preview = (s) =>
          `<figure><figcaption>${esc(label(assets.find((r) => r.id === s.asset.id)))} · ${s.range[0].toFixed(2)}–${s.range[1].toFixed(2)} 秒</figcaption><video controls preload="metadata" style="width:100%;max-height:180px" src="/api/v1/media/${encodeURIComponent(s.asset.id)}/file#t=${s.range[0]},${s.range[1]}"></video></figure>`;
      const receipt = modal(
        {
          exact: "完整文件字节相同",
          near_candidate: "疑似近似区间",
          distinct_samples: "采样区间存在差异",
        }[data.classification],
        preview(data.left) +
          preview(data.right) +
          `<p>画面相似度 ${data.measurements.visual} · 声音相似度 ${data.measurements.sound ?? "无可比信号"}</p>
        ${data.classification === "near_candidate" ? '<button data-review class="button primary">人工复核近似关系</button>' : ""}`,
      );
      const btn = receipt.querySelector("[data-review]");
      if (btn) btn.onclick = () => review(ctx, audit);
    },
  );
}
function clipReview(ctx, audit, assets, confirmed = new Set()) {
  const d = audit.data;
  const memberLabel = (id) => esc(label(assets.find((r) => r.id === id)));
  const frameTime = (pts) => (pts / 1000000).toFixed(3) + " 秒";
  const dialog = modal(
    "视觉语义候选族",
    `<p>阈值 ${esc(d.threshold)} · 模型 ${esc(d.space)} · 未归组 ${d.unmatched.length} 项</p>` +
      (d.unmatched.length
        ? `<p>${d.unmatched.map(memberLabel).join("、")}</p>`
        : "") +
      d.groups
        .map(
          (g, i) => `<section><h3>${g.members.map(memberLabel).join("、")}</h3>
      <p>组内最低相似度 ${g.minimum_score.toFixed(3)}</p>
      ${g.members
        .map((id) => {
          const e = d.evidence[id],
            url = "/api/v1/media/" + encodeURIComponent(id) + "/file";
          const r = assets.find((r) => r.id === id),
            still = r.data.probe?.is_still;
          return `<figure><figcaption>${memberLabel(id)} · v${e.asset.version} · 采样于 ${e.frames.map((f) => frameTime(f.pts_us)).join("、")}</figcaption>${still ? `<img src="${url}" alt="${memberLabel(id)}" style="max-width:100%;max-height:180px">` : `<video controls preload="metadata" style="max-width:100%;max-height:180px" src="${url}"></video>`}</figure>`;
        })
        .join("")}
      <details><summary>逐对分数与最相似采样时刻</summary><ul>${g.pairs.map((p) => `<li>${memberLabel(p.left)} · ${frameTime(p.left_pts_us)} / ${memberLabel(p.right)} · ${frameTime(p.right_pts_us)}，双向覆盖分 ${p.score.toFixed(3)}，最佳帧分 ${p.best_frame_cosine.toFixed(3)}</li>`).join("")}</ul></details>
      <button type="button" class="button" data-clip-group="${i}" ${confirmed.has(i) ? "disabled" : ""}>${confirmed.has(i) ? "已确认" : "复核这个族"}</button></section>`,
        )
        .join("") +
      (d.groups.length ? "" : "<p>当前阈值下没有可复核的相似族。</p>"),
    null,
    true,
  );
  for (const button of dialog.querySelectorAll("[data-clip-group]")) {
    const i = Number(button.dataset.clipGroup);
    button.onclick = () =>
      form(
        "确认视觉语义族",
        field("素材族名称", "name", "", "text", "required") +
          area("人工复核依据", "reason", "", "required"),
        "确认分组",
        async (v, key) => {
          await post(ctx, "group", key, {
            mode: "review_clip",
            members: d.groups[i].members.map((id) => pin(d.evidence[id].asset)),
            audit: pin(audit),
            name: v.get("name"),
            reason: v.get("reason"),
          });
          confirmed.add(i);
          await ctx.reloadData();
          await ctx.refresh();
          clipReview(ctx, audit, assets, confirmed);
        },
      );
  }
}

export function families(ctx) {
  const assets = ctx.records("AssetVersion");
  const groups = ctx
    .records("Cluster")
    .filter(
      (r) =>
        ["semantic", "near"].includes(r.data.cluster_level) &&
        r.data.status !== "retired",
    );
  const dlg = form(
    "素材族管理",
    select("分组依据", "mode", [
      ["provenance", "共同源素材"],
      ["semantic", "人工语义类目"],
      ["clip", "AI 视觉语义聚类"],
      ["retire", "撤销人工分组"],
    ]) +
      `<label data-members class="field"><span>素材成员</span><select name="members" multiple size="5">${assets.map((r) => `<option value="${esc(r.id)}">${esc(label(r))}</option>`).join("")}</select></label>` +
      `<div data-group hidden>${select("待撤销的素材族", "group", opts(groups))}</div>` +
      `<div data-threshold hidden>${field("组内最低双向视觉相似度", "threshold", "0.80", "number", 'min="0.5" max="1" step="0.01"')}</div>` +
      `<div data-manual>${field("分组名称", "name", "", "text", "required")}${area("依据", "reason", "", "required")}</div>`,
    "保存分组",
    async (v, key) => {
      const mode = v.get("mode"),
        payload =
          mode === "retire"
            ? { group: pin(groups.find((g) => g.id === v.get("group"))) }
            : {
                members: v
                  .getAll("members")
                  .map((id) => pin(assets.find((a) => a.id === id))),
              };
      if (mode === "clip") {
        if (payload.members.length < 2 || payload.members.length > 32)
          throw new Error("请选择 2 至 32 个已建画面索引的照片或视频。");
        const { audit } = await post(ctx, "suggest", key, {
          ...payload,
          threshold: Number(v.get("threshold")),
        });
        clipReview(ctx, audit, assets);
        return;
      }
      const { cluster } = await post(ctx, "group", key, {
        mode,
        name: v.get("name"),
        reason: v.get("reason"),
        ...payload,
      });
      await ctx.reloadData();
      await ctx.refresh();
      toast(
        cluster.data.status === "retired"
          ? "已撤销分组，保留历史版本"
          : "已保存素材族",
      );
    },
  );
  const mode = dlg.querySelector("[name=mode]");
  const display = () => {
    const retiring = mode.value === "retire",
      clip = mode.value === "clip";
    for (const option of dlg.querySelector("[name=members]").options) {
      const record = assets.find((r) => r.id === option.value);
      const unavailable =
        clip &&
        (!record.data.probe?.has_video ||
          ["local_cold", "quarantine"].includes(record.data.storage_tier));
      option.disabled = Boolean(unavailable);
      if (unavailable) option.selected = false;
    }
    dlg.querySelector("[data-threshold]").hidden = !clip;
    dlg.querySelector("[name=threshold]").disabled = !clip;
    dlg.querySelector("[data-manual]").hidden = clip;
    for (const el of dlg.querySelectorAll(
      "[data-manual] input, [data-manual] textarea",
    ))
      el.disabled = clip;
    dlg.querySelector("form[data-form] [type=submit]").textContent = clip
      ? "生成待复核候选族"
      : "保存分组";
    dlg.querySelector("[data-members]").hidden = retiring;
    dlg.querySelector("[data-group]").hidden = !retiring;
    const groupsInput = dlg.querySelector("[name=group]");
    groupsInput.required = retiring;
  };
  mode.onchange = display;
  display();
}
