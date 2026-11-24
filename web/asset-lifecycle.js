import { esc, field, area, select, modal, toast } from "./ui.js";

const pin = (item) => ({ id: item.id, version: item.version });
const name = (item) =>
  item.data.filename ||
  item.data.title ||
  item.data.name ||
  item.data.relative_path ||
  item.id;
const options = (items) => items.map((item) => [item.id, name(item)]);
const none = ["", "不限定"];
const types = [
  ["online", "在线"],
  ["low_priority", "低优先级"],
  ["local_cold", "本机冷藏"],
  ["quarantine", "隔离"],
];
const num = (label, key, value, min, max, step = 1) =>
  field(
    label,
    key,
    value,
    "number",
    `required min="${min}" max="${max}" step="${step}"`,
  );

function multi(label, key, items, required = false) {
  return `<label class="field"><span>${esc(label)}</span><select name="${esc(key)}" multiple size="5" ${required ? "required" : ""}>
    ${items.map((item) => `<option value="${esc(item.id)}">${esc(name(item))}</option>`).join("")}</select></label>`;
}
function dialog(title, html, action, handle) {
  const request_id = crypto.randomUUID();
  return modal(
    title,
    `<form data-form class="form-grid">${html}<div class="dialog-actions"><button type="submit" class="button primary">${esc(action)}</button></div></form>`,
    async (form) => handle(new FormData(form), request_id),
  );
}
async function send(ctx, route, request_id, payload, refresh = true) {
  const result = await ctx.api("/production/assets/" + route, {
    method: "POST",
    body: { project_id: ctx.requireProject(), request_id, ...payload },
  });
  await ctx.reloadData();
  if (refresh) await ctx.refresh();
  return result;
}

export function cooldown(ctx) {
  const assets = ctx.records("AssetVersion");
  const form = dialog(
    "冷却与受众疲劳",
    select("素材版本", "asset", options(assets), "", "required") +
      select("依据", "mode", [
        ["use_count", "时窗内使用次数"],
        ["post_views", "有最终媒体依据的帖子累计播放"],
        ["exclude", "人工禁用素材族"],
        ["retain", "人工保留，免除软降权"],
      ]) +
      select("账号", "account_id", [none, ...options(ctx.records("Account"))]) +
      field("受众范围（可跨账号）", "audience") +
      num("回溯天数", "days", 7, 1, 365) +
      num("压力阈值", "threshold", 3, 1, 100000000) +
      field("规则到期（人工例外必填）", "expires_at", "", "datetime-local") +
      area("原因", "reason", "", "required") +
      '<button type="button" data-mapping class="button secondary">核对帖子最终媒体</button><label><input type="checkbox" name="hard"> 达到阈值时排除该素材族</label>',
    "计算并保存规则",
    async (v, key) => {
      const mode = v.get("mode");
      const result = await send(ctx, "cooldown", key, {
        asset: pin(assets.find((a) => a.id === v.get("asset"))),
        rule: {
          mode,
          account_id: v.get("account_id") || null,
          audience: v.get("audience") || null,
          window_days: Number(v.get("days")),
          threshold: Number(v.get("threshold")),
          expires_at: v.get("expires_at")
            ? new Date(v.get("expires_at")).toISOString()
            : null,
          reason: v.get("reason"),
          hard_limit: v.has("hard"),
        },
      });
      const e = result.policy.data.evaluation;
      modal(
        "冷却依据",
        `<p>活跃预留 ${e.active_reservations} · 窗口内成片使用 ${e.window_rendered} · 映射已核帖子 ${e.confirmed_posts}</p>
        <p>未核实映射的帖子 ${e.unmapped_posts} · 帖子播放代理 ${e.post_views_estimate ?? "暂无"}</p>
        <p>镜头实际曝光及跨账号受众重合未知。这条规则可在探索配额中选用。</p>`,
      );
    },
  );
  form.querySelector("[data-mapping]").onclick = () => {
    const posts = ctx.records("Post");
    dialog(
      "核对帖子最终媒体",
      select("已登记帖子", "post", options(posts), "", "required") +
        select(
          "从该帖子下载并导入的最终媒体",
          "observed",
          options(assets),
          "",
          "required",
        ) +
        field("实际发布时间", "at", "", "datetime-local", "required") +
        field("受众范围", "audience") +
        area("下载来源与发布时间的核对依据", "evidence", "", "required"),
      "核对字节并登记",
      async (v, key) => {
        await send(ctx, "post-mapping", key, {
          post: pin(posts.find((p) => p.id === v.get("post"))),
          observed_file: pin(assets.find((a) => a.id === v.get("observed"))),
          published_at: new Date(v.get("at")).toISOString(),
          audience: v.get("audience") || null,
          evidence: v.get("evidence"),
        });
        toast("已核实下载件与交接成片的字节一致");
      },
    );
  };
}

export function grantProcessing(ctx) {
  const sources = ctx.records("Source");
  dialog(
    "核对本地制作用途",
    select("来源", "source", options(sources), "", "required") +
      area("权利依据及允许的本地用途", "evidence", "", "required"),
    "记录本地制作用途",
    async (v, key) => {
      await send(ctx, "processing-evidence", key, {
        source: pin(sources.find((s) => s.id === v.get("source"))),
        evidence: v.get("evidence"),
      });
      toast("已记录本地制作依据");
    },
  );
}

export function exploration(ctx, onSelected) {
  const assets = ctx.records("AssetVersion"),
    policies = ctx.records("Policy").filter((p) => p.data.feature_id === "U04");
  const form = dialog(
    "素材探索与沉淀",
    field("方案名", "name", "新旧素材配额", "text", "required") +
      multi("候选素材版本", "assets", assets, true) +
      multi("本次采用的冷却规则", "policies", policies) +
      select("目标账号", "account_id", [
        none,
        ...options(ctx.records("Account")),
      ]) +
      field("目标受众", "audience") +
      num("候选名额", "slots", 4, 1, 16) +
      num("未使用素材份额", "share", 0.5, 0, 1, 0.01) +
      num("最低画面质量评分", "floor", 0.6, 0, 1, 0.01) +
      '<button type="button" data-rights class="button secondary">核对来源的本地制作用途</button>',
    "计算选材方案",
    async (v, key) => {
      const refs = (items, k) =>
        v.getAll(k).map((id) => pin(items.find((i) => i.id === id)));
      const { plan } = await send(
        ctx,
        "exploration",
        key,
        {
          name: v.get("name"),
          assets: refs(assets, "assets"),
          policies: refs(policies, "policies"),
          slots: Number(v.get("slots")),
          exploration_share: Number(v.get("share")),
          quality_floor: Number(v.get("floor")),
          account_id: v.get("account_id") || null,
          audience: v.get("audience") || null,
        },
        !onSelected,
      );
      const data = plan.data,
        title = (id) => esc(name(assets.find((a) => a.id === id)));
      const receipt = modal(
        "选材结果",
        `<p>已选 ${data.candidates.length} 段 · 缺额 ${data.unfilled} 段</p>
        ${data.candidates.map((c) => `<p>${title(c.asset.id)} · ${c.pool === "exploration" ? "探索" : "保留"} · 质量 ${c.quality}</p>`).join("")}
        ${data.omissions.map((c) => `<p>${title(c.asset.id)} · ${esc(c.reason)}</p>`).join("")}
        ${onSelected && data.candidates.length ? '<button class="button primary" data-use>用于本次制作</button>' : ""}`,
      );
      const use = receipt.querySelector("[data-use]");
      if (use)
        use.onclick = () => {
          onSelected(data.candidates, plan);
          receipt.close();
        };
    },
  );
  form.querySelector("[data-rights]").onclick = () => grantProcessing(ctx);
}

export function tiers(ctx) {
  const assets = ctx.records("AssetVersion");
  const form = dialog(
    "归档与复苏",
    select(
      "素材版本",
      "asset",
      assets.map((a) => [
        a.id,
        `${name(a)} · ${types.find((t) => t[0] === (a.data.storage_tier || "online"))?.[1] || "未知"}`,
      ]),
      "",
      "required",
    ) +
      select("目标层级", "tier", types, "low_priority") +
      area("变更原因", "reason", "", "required") +
      '<button type="button" data-rights class="button secondary">核对来源的本地制作用途</button>',
    "变更层级",
    async (v, key) => {
      const { event } = await send(ctx, "tier", key, {
        asset: pin(assets.find((a) => a.id === v.get("asset"))),
        tier: v.get("tier"),
        reason: v.get("reason"),
      });
      modal(
        "分层记录",
        `<p>${esc(event.data.reason)}</p><p>已保留历史引用与原始字节。${event.data.restore_bytes != null ? `恢复时需校验 ${event.data.restore_bytes.toLocaleString()} 字节。` : ""}</p>`,
      );
    },
  );
  form.querySelector("[data-rights]").onclick = () => grantProcessing(ctx);
}
