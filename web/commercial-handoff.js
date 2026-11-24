import { area, esc, field, modal, select, toast } from "./ui.js";

const pin = (r) => ({ id: r.id, version: r.version });
const display = (r) =>
  r.data.SKU
    ? r.data.SKU + " · " + (r.data.name || "")
    : r.data.title ||
      r.data.name ||
      r.data.provenance ||
      r.data.platform_post_id ||
      r.id;
const choices = (records) => records.map((r) => [r.id, display(r)]);
const choose = (v, k, rows) => pin(rows.find((r) => r.id === v.get(k)));
const kinds = [
  ["own_brand", "自有品牌"],
  ["paid_partnership", "付费合作"],
  ["own_brand_and_paid_partnership", "自有品牌及付费合作"],
];
const form = (title, html, label, callback) => {
  const key = crypto.randomUUID();
  return modal(
    title,
    `<form data-form class="form-stack">${html}<div class="form-actions"><button type="submit" class="button primary">${esc(label)}</button></div></form>`,
    async (f) => callback(new FormData(f), key),
  );
};
const send = async (ctx, route, key, body) => {
  const result = await ctx.api("/production/commerce/" + route, {
    method: "POST",
    body: { request_id: key, project_id: ctx.requireProject(), ...body },
  });
  await ctx.reloadData();
  await ctx.refresh();
  return result;
};
const pendingLabels = {
  select_target_account: "确定目标账号",
  confirm_target_market: "确定市场",
  confirm_creator_eligibility: "核实创作者资格",
  attach_products_in_platform: "在平台草稿内挂商品",
  verify_final_post_and_disclosures: "核实最终帖子商品卡与披露",
  recheck_stock: "复核可售库存",
  resolve_platform_product_ids: "补齐平台商品号",
};
const receiptLabels = { ready: "可下载", receipt_recorded: "已登记收讫" };
function packageCard(ctx, r) {
  const snap = r.data.commercial_metadata;
  const lines = snap.products
    .map(
      (p) =>
        `<p>${esc(p.SKU)} · ${esc(p.price)} ${esc(p.currency)} · 数量 ${esc(p.quantity)} · ${esc(p.market || "市场待核")}</p>`,
    )
    .join("");
  const right = `<a class="button primary" href="/api/v1/production/commerce/packages/${encodeURIComponent(r.id)}/file?version=${r.version}" download>下载商品交接包</a>`;
  const dlg = modal(
    "商品交接包",
    `<p>${esc(receiptLabels[r.data.handoff_state] || r.data.handoff_state)} · ${snap.account ? "已指定账号" : "账号待定"} · ${esc(snap.market || "市场待定")}</p>${lines}<p>披露 · ${esc(snap.disclosure.text)}</p><p>待办 · ${r.data.pending_actions.map((k) => esc(pendingLabels[k] || k)).join("、")}</p>
    <p>SHA-256 <code>${esc(r.data.checksum)}</code></p><div class="dialog-actions">${right}${r.data.handoff_state === "ready" ? '<button type="button" class="button secondary" data-collect>登记收讫</button>' : ""}</div>`,
  );
  const collect = dlg.querySelector("[data-collect]");
  if (collect)
    collect.onclick = () =>
      form(
        "登记交接包收讫",
        field("接收方", "recipient", "", "text", "required") +
          field(
            "接收方核对的文件 SHA-256",
            "checksum",
            "",
            "text",
            'required pattern="[a-f0-9]{64}"',
          ) +
          field(
            "实际收讫时间",
            "collected_at",
            new Date().toISOString(),
            "text",
            "required",
          ) +
          area("接收依据", "evidence", "", "required"),
        "登记收讫",
        async (v, key) => {
          const { package: pkg } = await send(ctx, "receipts", key, {
            package: pin(r),
            ...Object.fromEntries(v),
          });
          packageCard(ctx, pkg);
        },
      );
}
function prepare(ctx, artifact) {
  const media = artifact
      ? [artifact]
      : [
          ...ctx.records("Artifact"),
          ...ctx
            .records("DeliveryPackage")
            .filter((p) => p.data.status === "available"),
        ],
    products = ctx.records("Product"),
    accounts = ctx.records("Account");
  if (!products.length || !media.length)
    throw new Error("先导入有来源的商品事实并生成成片。");
  form(
    "准备待挂商品包",
    select(
      "已审阅成片或已有交付包",
      "artifact",
      media.map((r) => [
        r.id,
        (r.kind === "Artifact" ? "成片 · " : "文件包 · ") + display(r),
      ]),
      "",
      "required",
    ) +
      `<label class="field"><span>商品（可多选）</span><select name="products" multiple required size="4">${choices(
        products,
      )
        .map(
          ([id, title]) => `<option value="${esc(id)}">${esc(title)}</option>`,
        )
        .join("")}</select></label>` +
      select("目标账号", "account", [["", "待定"], ...choices(accounts)]) +
      field("目标市场（可待定）", "market") +
      select("商业披露", "kind", kinds) +
      area("披露文案", "disclosure_text", "", "required"),
    "生成并核验交接包",
    async (v, key) => {
      const { package: pkg } = await send(ctx, "packages", key, {
        [media.find((r) => r.id === v.get("artifact")).kind === "Artifact"
          ? "artifact"
          : "source_package"]: choose(v, "artifact", media),
        account: v.get("account") ? choose(v, "account", accounts) : null,
        products: v
          .getAll("products")
          .map((id) => pin(products.find((r) => r.id === id))),
        market: v.get("market") || null,
        disclosure_kind: v.get("kind"),
        disclosure_text: v.get("disclosure_text"),
      });
      packageCard(ctx, pkg);
    },
  );
}
function bind(ctx, packages, artifact) {
  const pubs = ctx
    .records("Publication")
    .filter(
      (p) =>
        p.data.channel === "manual" &&
        p.data.status === "draft" &&
        p.data.outcome === "not_sent" &&
        (!artifact || p.data.artifact_id === artifact.id),
    );
  const accounts = ctx.records("Account"),
    sources = ctx.records("Source");
  if (!pubs.length || !packages.length)
    throw new Error("需要待挂商品包及同一成片的人工发布草稿。");
  form(
    "平台草稿挂商品交接",
    select("交接包", "pkg", choices(packages)) +
      select("发布意图", "pub", choices(pubs)) +
      field("已核市场", "market", "", "text", "required") +
      select(
        "创作者资格与商品可售的核对来源",
        "source",
        choices(sources),
        "",
        "required",
      ) +
      field("资格核对有效至（含时区）", "until", "", "text", "required") +
      area(
        "账号、市场、商品可售与类目限制的实际核对依据",
        "evidence",
        "",
        "required",
      ),
    "保存待挂商品意图",
    async (v, key) => {
      const pub = pubs.find((p) => p.id === v.get("pub"));
      await send(ctx, "bindings", key, {
        package: choose(v, "pkg", packages),
        publication: pin(pub),
        account: pin(accounts.find((a) => a.id === pub.data.account_id)),
        creator_source: choose(v, "source", sources),
        market: v.get("market"),
        eligibility_evidence: v.get("evidence"),
        eligibility_expires_at: v.get("until"),
      });
      toast("已登记商品绑定意图，平台内挂载仍待核实");
    },
  );
}
function observe(ctx, artifact) {
  const pubs = ctx
    .records("Publication")
    .filter(
      (r) =>
        r.data.commerce_binding &&
        (!artifact || r.data.artifact_id === artifact.id),
    );
  const posts = ctx
      .records("Post")
      .filter((p) => pubs.some((r) => r.id === p.data.publication_id)),
    sources = ctx.records("Source");
  if (!posts.length) throw new Error("请先为商品发布意图登记真实帖子回执。");
  form(
    "核对帖子商品卡",
    select("实际帖子", "post", choices(posts)) +
      area("逐行填写实际商品号（无商品卡留空）", "ids") +
      select("实际披露", "kind", [
        ...kinds,
        ["absent", "未显示"],
        ["unknown", "待核实"],
      ]) +
      area("实际披露文案（未显示可留空）", "observed_text") +
      select("帖子观察来源", "source", choices(sources), "", "required") +
      field(
        "观测时间（含时区）",
        "at",
        new Date().toISOString(),
        "text",
        "required",
      ) +
      area("商品卡及披露核对依据", "evidence", "", "required"),
    "记录人工观测",
    async (v, key) => {
      const p = posts.find((r) => r.id === v.get("post")),
        pub = pubs.find((r) => r.id === p.data.publication_id);
      const { post } = await send(ctx, "observations", key, {
        publication: pin(pub),
        post: pin(p),
        evidence_source: choose(v, "source", sources),
        observed_shop_product_ids: String(v.get("ids") || "")
          .split(/\r?\n/)
          .map((s) => s.trim())
          .filter(Boolean),
        observed_at: v.get("at"),
        observed_disclosure_kind: v.get("kind"),
        observed_disclosure_text: v.get("observed_text") || null,
        evidence: v.get("evidence"),
      });
      toast(
        post.data.commercial_observation.status === "matched_as_observed"
          ? "人工观测的商品与披露吻合"
          : "已记录差异，尚待复核",
      );
    },
  );
}
export function commercialHandoff(ctx, artifact = null) {
  const packages = ctx
    .records("DeliveryPackage")
    .filter(
      (r) =>
        r.data.purpose === "publish_handoff" &&
        (!artifact || r.data.artifact_id === artifact.id),
    );
  const dlg = modal(
    "带商品与商业信息",
    `<div class="dialog-actions"><button class="button primary" data-prepare>准备待挂商品包</button><button class="button secondary" data-bind>平台草稿人工挂商品</button><button class="button secondary" data-observe>核对最终帖子商品卡</button></div>
    ${packages.map((r, i) => `<button type="button" class="list-row" data-package="${i}"><strong>${esc(r.data.commercial_metadata.products.map((p) => p.SKU).join(" / "))}</strong><small>${esc(receiptLabels[r.data.handoff_state] || "待核实")} · ${esc(r.data.commercial_metadata.market || "市场待定")}</small></button>`).join("")}`,
  );
  const attempt = (fn) => () => {
    try {
      fn();
    } catch (e) {
      toast(e.message, "error");
    }
  };
  dlg.querySelector("[data-prepare]").onclick = attempt(() =>
    prepare(ctx, artifact),
  );
  dlg.querySelector("[data-bind]").onclick = attempt(() =>
    bind(ctx, packages, artifact),
  );
  dlg.querySelector("[data-observe]").onclick = attempt(() =>
    observe(ctx, artifact),
  );
  dlg
    .querySelectorAll("[data-package]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          packageCard(ctx, packages[Number(b.dataset.package)])),
    );
}
