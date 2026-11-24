import { area, esc, field, formData, modal, select, uid } from "./ui.js";
const pin = (r) => ({ id: r.id, version: r.version });

export function openNarration(ctx, selectedScript = "") {
  const project = ctx.requireProject();
  const scripts = ctx.records("Script");
  const approvals = ctx
    .records("Approval")
    .filter(
      (r) =>
        r.data.operation === "voice_source_review" &&
        r.data.status === "approved",
    );
  const options = (list) => list.map((r) => [r.id, r.data.name || r.id]);
  const d = modal(
    "口播录制与生成",
    '<form data-form class="form-stack">' +
      select(
        "英文稿件",
        "script",
        [["", "录入新稿"], ...options(scripts)],
        selectedScript,
      ) +
      "<div data-new-script>" +
      area("英文口播稿", "text", "", 'maxlength="3000"') +
      "</div>" +
      select(
        "声音来源审核",
        "review",
        [
          ...approvals.map((r) => [
            r.id,
            ctx.records("Source").find((s) => s.id === r.data.subject_id)?.data
              .name +
              " · " +
              r.data.expires_at.slice(0, 10),
          ]),
          ["", "登记声音使用依据"],
        ],
        approvals[0]?.id || "",
      ) +
      "<div data-new-source>" +
      select("本地英文音色", "voice", [
        ["slt", "SLT"],
        ["rms", "RMS"],
        ["awb", "AWB"],
        ["kal", "KAL"],
      ]) +
      area("本项目制作与本地导出所依据的许可或权利人凭据", "evidence", "") +
      field("授权截止（当地日期）", "expires", "", "date") +
      "</div>" +
      field(
        "语速倍率",
        "tempo",
        1,
        "number",
        'min="0.5" max="2" step="0.05" required',
      ) +
      '<div class="form-actions"><button type="button" class="button secondary" data-import-narration>导入已有配音</button><button type="submit" class="button primary">生成并试听</button></div></form>',
    async (form) => {
      const v = formData(form);
      if (!v.script && !v.text.trim()) throw Error("请填写英文稿。");
      if (!v.review && (!v.evidence.trim() || !v.expires))
        throw Error("请填写声音使用依据和有效期。");
      const post = (path, body) =>
        ctx.api("/production/narration/" + path, {
          method: "POST",
          body: { project_id: project, request_id: uid(), ...body },
        });
      const approved = v.review
        ? approvals.find((r) => r.id === v.review)
        : (
            await post("source-reviews", {
              voice: v.voice,
              evidence: v.evidence,
              expires_at: new Date(v.expires + "T23:59:59").toISOString(),
            })
          ).approval;
      const script = v.script
        ? scripts.find((r) => r.id === v.script)
        : await ctx.saveRecord("Script", {
            name: v.text.trim().slice(0, 60),
            text: v.text.trim(),
            language: "en",
            status: "draft",
          });
      const result = await post("synthesize", {
        approval: pin(approved),
        script: pin(script),
        tempo: Number(v.tempo),
      });
      await ctx.reloadData();
      const a = result.audio;
      const receipt = modal(
        "试听配音",
        '<audio controls preload="metadata" src="' +
          esc(ctx.assetUrl(a)) +
          '"></audio><p>' +
          esc(
            a.data.voice +
              " · " +
              a.data.sample_rate +
              " Hz · " +
              a.data.sample_count +
              " 个采样",
          ) +
          '</p><button type="button" class="button secondary" data-voice-map>对齐口播与画面</button>',
      );
      receipt.querySelector("[data-voice-map]").onclick = async () => {
        receipt.close();
        const { openVoiceStudio } = await import("./voice-studio.js");
        openVoiceStudio(ctx, a);
      };
    },
  );
  const sync = () => {
    d.querySelector("[data-new-script]").hidden =
      !!d.querySelector("[name=script]").value;
    d.querySelector("[data-new-source]").hidden =
      !!d.querySelector("[name=review]").value;
  };
  d.querySelector("form[data-form]").addEventListener("change", sync);
  sync();
  d.querySelector("[data-import-narration]").onclick = () => {
    d.close();
    location.hash = "#editor/import";
  };
}

export function openReading(ctx) {
  const project = ctx.requireProject(),
    scripts = ctx.records("Script"),
    products = ctx.records("Product");
  if (!scripts.length || !products.length) {
    ctx.toast("请先录入原稿，并在商品表中导入对应的价格和币种。");
    return;
  }
  const d = modal(
    "英文商品口播读法",
    '<form data-form class="form-stack">' +
      select(
        "原稿",
        "script",
        scripts.map((r) => [
          r.id,
          r.data.name || String(r.data.text || "原稿").slice(0, 80),
        ]),
      ) +
      "<p data-reading-source></p>" +
      select(
        "商品事实",
        "product",
        products.map((r) => [
          r.id,
          [r.data.SKU, r.data.currency, r.data.price].join(" · "),
        ]),
      ) +
      field("原稿中的原样金额", "amount", "", "text", "required") +
      area("人工英文译稿，金额处填写 {price}", "target", "", "required") +
      area(
        "对数量、单位、否定词与价格生效条件的复核依据",
        "note",
        "",
        "required",
      ) +
      '<div class="form-actions"><button type="submit" class="button primary">核对并保存读法</button></div></form>',
    async (form) => {
      const v = formData(form),
        product = products.find((r) => r.id === v.product);
      const result = await ctx.api("/production/narration/readings", {
        method: "POST",
        body: {
          project_id: project,
          request_id: uid(),
          script: pin(scripts.find((r) => r.id === v.script)),
          product: pin(product),
          source_amount: v.amount,
          source_currency: product.data.currency,
          target_template: v.target,
          review_note: v.note,
        },
      });
      await ctx.reloadData();
      const saved = result.script;
      const receipt = modal(
        "英文读法已保存",
        "<p>" +
          esc(saved.data.display_text) +
          "</p><p>" +
          esc(saved.data.text) +
          '</p><button type="button" class="button primary" data-speak-reading>使用此稿配音</button>',
      );
      receipt.querySelector("[data-speak-reading]").onclick = () =>
        openNarration(ctx, saved.id);
    },
  );
  const read = () => {
    d.querySelector("[data-reading-source]").textContent =
      scripts.find((r) => r.id === d.querySelector("[name=script]").value)?.data
        .text || "";
  };
  d.querySelector("[name=script]").onchange = read;
  read();
}
