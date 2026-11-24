import { postReview } from "./post-reviews.js";
import { compare, families } from "./asset-families.js";
import { openNarration, openReading } from "./narration.js";
import { feedback } from "./comment-feedback.js";
import { cooldown, exploration, tiers } from "./asset-lifecycle.js";
import { scorePolicyActions } from "./score-policies.js";
import { openAssetSearch } from "./asset-search.js";
import { openVoiceStudio } from "./voice-studio.js";
import { openSoundtrack } from "./soundtrack.js";
import {
  esc,
  field,
  area,
  select,
  modal,
  toast,
  formData,
  time,
} from "./ui.js";

const DIMENSIONS = {
  quality: "画面质量",
  relevance: "内容相关性",
  fatigue: "受众疲劳",
  confidence: "证据置信度",
  tag_completeness: "标注完整度",
  performance: "已观察表现",
};
const CATEGORIES = {
  subject: "主体",
  SKU: "商品",
  quantity: "数量",
  action: "动作",
  scene: "场景",
  quality: "画质",
  language: "语言",
  editorial_role: "剪辑用途",
};
const numeric = (label, key, value, attrs = "") =>
  field(label, key, value, "number", `step="any" ${attrs}`);
const required = (label, name, value = "") =>
  field(label, name, value, "text", "required");
function displayName(ctx, r) {
  const d = r.data;
  const parent = ctx.state.records.find(
    (p) => p.id === d.asset_id || p.id === d.asset_version_id,
  );
  const name =
    d.name ||
    d.title ||
    d.filename ||
    d.label ||
    d.SKU ||
    d.metric_name ||
    d.provenance ||
    parent?.data?.filename;
  if (name)
    return (
      String(name).slice(0, 100) +
      (r.kind === "AssetVersion" && d.variant === "derivative"
        ? " · 处理版"
        : "")
    );
  if (r.kind === "Shot")
    return `镜头 ${time(d.source_in || 0)}–${time(d.source_out || 0)}`;
  if (r.kind === "Script") return String(d.text || "脚本").slice(0, 60);
  return `${{ TimeMap: "时码映射", TransformRun: "素材处理", RenderManifest: "渲染清单", Usage: "镜头预留", Source: "来源", Audio: "音轨", Metric: "指标", Tag: "标注", Artifact: "成片" }[r.kind] || "记录"} · ${new Date(r.updated_at || r.created_at).toLocaleString("zh-CN")}`;
}
function pick(ctx, kind, label, key, missing, records = ctx.records(kind)) {
  return select(
    label,
    key,
    [["", missing], ...records.map((r) => [r.id, displayName(ctx, r)])],
    "",
    "required",
  );
}
function form(title, html, label, submit) {
  return modal(
    title,
    `<form data-form class="form-grid">${html}<div class="dialog-actions"><button class="button primary" type="submit">${esc(label)}</button></div></form>`,
    async (f) => submit(formData(f), f),
  );
}
function receipt(title, lines) {
  modal(
    title,
    `<div class="action-receipt" role="status">${lines.map((line) => `<p>${esc(line)}</p>`).join("")}</div>`,
  );
}
const post = (ctx, path, body) =>
  ctx.api("/production/" + path, { method: "POST", body });
const done = async (ctx, text) => {
  await ctx.reloadData();
  await ctx.refresh();
  toast(text);
};
function products(ctx) {
  const pid = ctx.requireProject(),
    sources = ctx.records("Source");
  let newSource = null;
  const d = form(
    "导入商品表",
    select(
      "事实来源",
      "source_id",
      [...sources.map((r) => [r.id, displayName(ctx, r)]), ["", "新增来源"]],
      sources[0]?.id || "",
    ) +
      `<div data-new-source>${required("来源名称或凭据说明", "provenance")}</div>` +
      field("商品 CSV", "file", "", "file", 'accept=".csv,text/csv" required') +
      "<small>必需列为 SKU、price、currency、valid_until。有效期须含时区。</small>",
    "导入商品",
    async (v, f) => {
      const file = f.elements.file.files[0];
      if (file.size > 2_000_000) throw new Error("单批文件上限为 2 MB。");
      const csv_text = await file.text();
      if (
        !v.source_id &&
        (!newSource || newSource.data.provenance !== v.provenance.trim())
      )
        newSource = await ctx.saveRecord("Source", {
          name: v.provenance.trim().slice(0, 200),
          provenance: v.provenance.trim(),
          status: "recorded",
        });
      const r = await post(ctx, "products/import", {
        project_id: pid,
        csv_text,
        source_id: v.source_id || newSource.id,
      });
      await ctx.reloadData();
      await ctx.refresh();
      receipt("商品导入结果", [
        `已导入 ${r.imported} 行，未导入 ${r.rejected} 行`,
        ...r.errors.map((e) => `第 ${e.line} 行 · ${e.reason}`),
      ]);
    },
  );
  function toggle() {
    const input = d.querySelector("[name=provenance]");
    input.disabled = !!d.querySelector("[name=source_id]").value;
    input.closest("[data-new-source]").hidden = input.disabled;
  }
  d.querySelector("[name=source_id]").onchange = toggle;
  toggle();
}
function trends(ctx) {
  const pid = ctx.requireProject();
  form(
    "记录热点来源",
    required("标题", "title") +
      field("原始链接", "canonical_url", "", "url", "required") +
      required("平台", "platform") +
      field("市场", "region") +
      field(
        "观察时间（本地）",
        "observed_at",
        "",
        "datetime-local",
        "required",
      ) +
      field("有效至（本地）", "expires_at", "", "datetime-local", "required") +
      area("观察证据", "evidence", "", "required"),
    "保存观察",
    async (v) => {
      const r = await post(ctx, "trends", {
        ...v,
        project_id: pid,
        region: v.region || "unspecified",
        observed_at: new Date(v.observed_at).toISOString(),
        expires_at: new Date(v.expires_at).toISOString(),
      });
      await done(
        ctx,
        r.data.status === "expired" ? "已登记，观察已过期" : "已记录热点及来源",
      );
    },
  );
}
function tags(ctx) {
  const pid = ctx.requireProject();
  form(
    "标注镜头",
    pick(
      ctx,
      "AssetVersion",
      "素材版本",
      "asset_version_id",
      "选择已导入的素材",
    ) +
      numeric("入点（秒）", "start", 0, 'min="0" required') +
      numeric("出点（秒）", "end", "", 'min="0" required') +
      select("类别", "category", Object.entries(CATEGORIES)) +
      required("标签", "label") +
      area("画面或声音依据", "evidence", "", "required") +
      numeric("置信度（可留空）", "confidence", "", 'min="0" max="1"') +
      '<label><input name="locked" type="checkbox" checked> 锁定人工标注</label>',
    "保存标注",
    async (v, f) => {
      await post(ctx, "tags", {
        ...v,
        project_id: pid,
        start: Number(v.start),
        end: Number(v.end),
        confidence: v.confidence ? Number(v.confidence) : null,
        locked: f.elements.locked.checked,
      });
      await done(ctx, "已保存镜头区间和标签");
    },
  );
}
function search(ctx) {
  const pid = ctx.requireProject();
  form(
    "检索素材",
    field("文件名、标签或关键词", "q") +
      numeric("最短时长（秒）", "min_duration", 0, 'min="0" required') +
      numeric("最长时长（秒）", "max_duration", 86400, 'min="0" required'),
    "搜索",
    async (v) => {
      const r = await ctx.api(
        "/production/search?" + new URLSearchParams({ ...v, project_id: pid }),
      );
      const d = modal(
        `搜索结果 · ${r.count}`,
        r.items
          .map(
            (hit, i) =>
              `<button class="list-row" data-hit="${i}"><strong>${esc(displayName(ctx, hit.asset || hit.version))}</strong><small>${esc((hit.tags || []).map((t) => t.data.label).join(" · "))}</small></button>`,
          )
          .join("") || '<p role="status">没有匹配的素材</p>',
      );
      d.querySelectorAll("[data-hit]").forEach(
        (b) =>
          (b.onclick = () =>
            ctx.openRecord(r.items[Number(b.dataset.hit)].version)),
      );
    },
  );
}
function reserve(ctx) {
  const pid = ctx.requireProject();
  const d = form(
    "预留镜头",
    pick(ctx, "Shot", "镜头", "shot_id", "选择已标注的镜头") +
      pick(ctx, "Timeline", "使用方", "owner_ref", "选择项目或时间线", [
        ctx.project(),
        ...ctx.records("Timeline"),
      ]) +
      numeric(
        "保留时长（秒）",
        "ttl_seconds",
        900,
        'min="10" max="86400" required',
      ) +
      numeric(
        "同时使用上限",
        "max_concurrent",
        1,
        'min="1" max="100" required',
      ),
    "预留",
    async (v) => {
      await post(ctx, "usage/reserve", {
        ...v,
        project_id: pid,
        ttl_seconds: Number(v.ttl_seconds),
        max_concurrent: Number(v.max_concurrent),
      });
      await done(ctx, "已预留镜头");
    },
  );
  const active = ctx
    .records("Usage")
    .filter(
      (r) =>
        r.data.reservation_status === "reserved" &&
        new Date(r.data.expires_at) > new Date(),
    );
  if (!active.length) return;
  const block = document.createElement("section");
  block.innerHTML =
    "<h3>当前预留</h3>" +
    active
      .map(
        (r, i) =>
          `<div class="list-row"><span>${esc(displayName(ctx, ctx.state.records.find((row) => row.id === r.data.shot_id) || r))}</span><button class="button secondary" data-release="${i}">释放</button></div>`,
      )
      .join("");
  d.append(block);
  block.querySelectorAll("[data-release]").forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        const r = active[Number(b.dataset.release)];
        try {
          await post(ctx, "usage/" + encodeURIComponent(r.id) + "/release", {
            expected_revision: r.version,
          });
          await done(ctx, "已释放预留");
          d.close();
        } catch (err) {
          toast(err.message, "error");
          b.disabled = false;
        }
      }),
  );
}
function score(ctx) {
  const pid = ctx.requireProject();
  const dialog = form(
    "评价素材",
    pick(ctx, "AssetVersion", "素材", "asset_version_id", "选择素材版本") +
      select("评分权重", "weight_mode", [
        ["policy", "使用项目活动策略"],
        ["manual", "使用本次自定义权重"],
      ]) +
      Object.entries(DIMENSIONS)
        .map(
          ([k, v]) =>
            `<div class="form-grid two">${numeric(v + "（0–1）", k, "", 'min="0" max="1"')}${numeric("权重", "weight_" + k, 1, 'min="0" required')}</div>`,
        )
        .join("") +
      select("关联证据", "evidence", [
        ["", "暂未选择"],
        ...ctx.records("Tag").map((r) => [r.id, displayName(ctx, r)]),
      ]),
    "保存评价",
    async (v) => {
      const keys = Object.keys(DIMENSIONS),
        r = await post(ctx, "assets/score", {
          project_id: pid,
          asset_version_id: v.asset_version_id,
          dimensions: Object.fromEntries(
            keys.map((k) => [k, v[k] === "" ? null : Number(v[k])]),
          ),
          weights:
            v.weight_mode === "manual"
              ? Object.fromEntries(
                  keys.map((k) => [k, Number(v["weight_" + k])]),
                )
              : null,
          evidence_refs: v.evidence ? [v.evidence] : [],
        });
      await done(
        ctx,
        r.data.observed_score == null
          ? "已保存，暂无已观测评分"
          : "已保存素材评价",
      );
    },
  );
  const mode = dialog.querySelector('[name="weight_mode"]');
  function syncWeightInputs() {
    for (const input of dialog.querySelectorAll('[name^="weight_"]')) {
      if (input === mode) continue;
      input.disabled = mode.value !== "manual";
      input.closest("label").hidden = input.disabled;
    }
  }
  mode.onchange = syncWeightInputs;
  syncWeightInputs();
}
function align(ctx) {
  const pid = ctx.requireProject();
  const d = form(
    "对齐脚本与音轨",
    pick(ctx, "Script", "脚本", "script_id", "选择已有脚本") +
      pick(ctx, "Audio", "音轨", "audio_id", "选择已提取的音轨") +
      '<div data-anchors></div><button type="button" class="button secondary" data-add-anchor>添加词句</button>',
    "保存时间标记",
    async (v, f) => {
      const anchors = [...f.querySelectorAll("[data-anchor]")].map((row) => ({
        token: row.querySelector("[name=token]").value,
        start: Number(row.querySelector("[name=start]").value),
        end: Number(row.querySelector("[name=end]").value),
        timing_status: "aligned",
      }));
      await post(ctx, "voice/align", {
        project_id: pid,
        script_id: v.script_id,
        audio_id: v.audio_id,
        anchors,
      });
      await done(ctx, "已保存词句时间标记");
    },
  );
  function add() {
    const row = document.createElement("fieldset");
    row.dataset.anchor = "";
    row.innerHTML =
      required("脚本原句", "token") +
      numeric("开始（秒）", "start", "", 'min="0" required') +
      numeric("结束（秒）", "end", "", 'min="0" required') +
      '<button class="button secondary" type="button">移除</button>';
    row.querySelector("button").onclick = () => row.remove();
    d.querySelector("[data-anchors]").append(row);
  }
  d.querySelector("[data-add-anchor]").onclick = add;
  add();
}
function trace(ctx) {
  ctx.requireProject();
  form(
    "查看时码来源",
    pick(ctx, "TimeMap", "时间映射", "time_map_id", "选择已生成的时码映射") +
      numeric("成片时间（秒）", "seconds", 0, 'min="0" required'),
    "查看来源",
    async (v) => {
      const r = await post(ctx, "time-map/trace", {
        time_map_id: v.time_map_id,
        output_seconds: Number(v.seconds),
      });
      receipt(
        "片段来源",
        r.contributors.length
          ? r.contributors.map((c) => {
              const id = c.source_ref?.id || c.source_ref;
              const rec = ctx.state.records.find((row) => row.id === id);
              return (
                (rec ? displayName(ctx, rec) : "素材") +
                " · 源时间 " +
                Number(c.source_seconds).toFixed(3) +
                " 秒"
              );
            })
          : ["当前时间没有已知来源"],
      );
    },
  );
}
function evaluate(ctx) {
  ctx.requireProject();
  form(
    "核对试验结果",
    pick(ctx, "Experiment", "试验", "experiment", "选择已冻结的试验") +
      `<fieldset><legend>观测指标</legend>${
        ctx
          .records("Metric")
          .map(
            (r) =>
              `<label class="list-row"><input name="metric" type="checkbox" value="${esc(r.id)}"> ${esc(displayName(ctx, r))}</label>`,
          )
          .join("") || "<p>尚未导入指标</p>"
      }</fieldset>`,
    "计算结果",
    async (v, f) => {
      const r = ctx.records("Experiment").find((e) => e.id === v.experiment),
        ids = new FormData(f).getAll("metric");
      if (!ids.length) throw new Error("至少选择一项观测指标。");
      const result = await post(
        ctx,
        "experiments/" + encodeURIComponent(r.id) + "/evaluate",
        { expected_revision: r.version, metric_ids: ids },
      );
      await ctx.reloadData();
      await ctx.refresh();
      const diff = result.data.result.difference;
      receipt("试验结果", [
        diff == null ? "样本不足，暂时无法比较" : "已观察组间差异 " + diff,
        ...result.data.result.limitations,
      ]);
    },
  );
}
function replay(ctx) {
  const pid = ctx.requireProject();
  form(
    "检查重放依赖",
    pick(ctx, "TransformRun", "起始记录", "root", "选择素材处理或成片记录", [
      ...ctx.records("TransformRun"),
      ...ctx.records("RenderManifest"),
    ]),
    "检查依赖",
    async (v) => {
      const r = await post(ctx, "replay/prepare", {
        project_id: pid,
        root_ids: [v.root],
        mode: "inspect",
      });
      await ctx.reloadData();
      await ctx.refresh();
      receipt("依赖检查结果", [
        "已核对 " + r.data.dependency_refs.length + " 项依赖",
        "缺失 " + r.data.missing_dependencies.length + " 项",
      ]);
    },
  );
}
const actions = {
  ...scorePolicyActions,
  M04: ["评论与洞察", feedback],
  M09: ["比较帖子与登记改版假设", postReview],
  P02: ["导入商品 CSV", products],
  T01: ["记录热点来源", trends],
  L02: ["标注镜头主体", tags],
  L03: ["标注场景与动作", tags],
  L05: ["标注剪辑用途", tags],
  L06: ["标注镜头", tags],
  S01: ["检索素材", search],
  S03: ["对照素材区间", compare],
  S04: ["管理素材族", families],
  S02: ["检索素材与镜头标注", openAssetSearch],
  S05: ["构建或回退索引", openAssetSearch],
  D05: ["核对素材索引", openAssetSearch],
  V02: ["校正英文商品读法", openReading],
  V03: ["生成或导入口播", openNarration],
  V06: ["对齐口播与画面", openVoiceStudio],
  V07: ["调整声画剪点", openVoiceStudio],
  V08: ["复核失效区间", openVoiceStudio],
  V09: ["核对声画证据", openVoiceStudio],
  B03: ["核对配乐许可", (ctx) => openSoundtrack(ctx, "B03")],
  B04: ["剪配音乐", (ctx) => openSoundtrack(ctx, "B04")],
  B05: ["调整混音", (ctx) => openSoundtrack(ctx, "B05")],
  B06: ["检查响度", (ctx) => openSoundtrack(ctx, "B06")],
  N04: [
    "比较制作方案",
    () => {
      location.hash = "#production/variants";
    },
  ],
  U02: ["预留或释放镜头", reserve],
  U03: ["评价素材", score],
  U04: ["核对使用压力", cooldown],
  U05: ["编排探索配额", exploration],
  U06: ["归档与复苏", tiers],
  U07: ["评价素材", score],
  V04: ["人工标记词句时码", align],
  V05: ["编辑词句锚点", align],
  M08: ["查看时码来源", trace],
  X05: ["核对试验结果", evaluate],
  H10: ["检查重放依赖", replay],
};
export function actionForFeature(id) {
  const entry = actions[id];
  return entry ? { label: entry[0], open: entry[1] } : null;
}
