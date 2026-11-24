import { openRhythm } from "./rhythm.js";
import { uploadFile } from "./upload.js";
import { area, esc, field, formData, modal, select, uid } from "./ui.js";

const title = (r) => r.data.name || r.data.title || r.data.filename || "未命名";
const pin = (r) => ({ id: r.id, version: r.version });
const choice = (r) => [r.id, title(r) + " · 第 " + r.version + " 版"];
const button = (label) =>
  '<div class="form-actions"><button type="submit" class="button primary">' +
  esc(label) +
  "</button></div>";

function newBrief(ctx, back) {
  const d = modal(
    "新建候选创意",
    '<form data-form class="form-stack">' +
      field("候选名称", "name", "", "text", 'required maxlength="200"') +
      area("创意内容", "intent", "", 'required maxlength="3000"') +
      button("保存候选") +
      "</form>",
    async (form) => {
      const { name, intent } = formData(form);
      await ctx.saveRecord(
        "Brief",
        {
          name,
          intent,
          status: "candidate",
          project_version: ctx.project().version,
        },
        null,
        [ctx.ref(ctx.project())],
        "保存人工创意候选",
      );
      ctx.toast("候选已保存");
      back();
    },
  );
  return d;
}

function review(ctx) {
  const projectId = ctx.requireProject();
  const briefs = ctx
    .records("Brief")
    .filter((r) => !["approved", "revoked", "deleted"].includes(r.data.status));
  if (!briefs.length) return newBrief(ctx, () => review(ctx));
  const products = ctx.records("Product");
  const requestId = uid();
  const ttl = new Date(Date.now() + 7 * 86400000);
  const day = ttl.toISOString().slice(0, 10);
  const d = modal(
    "选择创意简报",
    '<form data-form class="form-stack">' +
      '<div class="form-actions"><button type="button" class="button secondary" data-add-brief>新建候选</button></div>' +
      select("采用的创意", "chosen", briefs.map(choice)) +
      '<output data-preview class="selection-preview"></output>' +
      "<div data-alternatives></div>" +
      (products.length
        ? "<fieldset><legend>绑定商品事实</legend>" +
          products
            .map(
              (r) =>
                '<label class="choice-row"><input type="checkbox" name="products" value="' +
                esc(r.id) +
                '"> ' +
                esc(title(r)) +
                "</label>",
            )
            .join("") +
          "</fieldset>"
        : "") +
      area("选择依据", "reason", "", 'required maxlength="3000"') +
      '<div class="form-grid">' +
      field(
        "用途",
        "purpose",
        "本项目视频制作",
        "text",
        'required maxlength="200"',
      ) +
      field("有效至（当地日期）", "expiry", day, "date", "required") +
      "</div>" +
      button("批准这个版本") +
      "</form>",
    async (form) => {
      const values = formData(form);
      const chosen = briefs.find((r) => r.id === values.chosen);
      const alternatives = Array.from(
        form.querySelectorAll("[data-compare]:checked"),
      ).map((box) => ({
        ...pin(briefs.find((r) => r.id === box.value)),
        reason: box.closest("[data-option]").querySelector("[data-reason]")
          .value,
      }));
      const facts = Array.from(
        form.querySelectorAll("[name=products]:checked"),
      ).map((box) => pin(products.find((r) => r.id === box.value)));
      await ctx.api("/production/selection/brief", {
        method: "POST",
        body: {
          project_id: projectId,
          request_id: requestId,
          chosen: pin(chosen),
          alternatives,
          products: facts,
          reason: values.reason,
          purpose: values.purpose,
          expires_at: new Date(values.expiry + "T23:59:59").toISOString(),
        },
      });
      await ctx.reloadData();
      ctx.toast("简报已批准");
      await ctx.refresh();
    },
  );
  d.querySelector("[data-add-brief]").onclick = () =>
    newBrief(ctx, () => review(ctx));
  const chosen = d.querySelector("[name=chosen]");
  function display() {
    const current = briefs.find((r) => r.id === chosen.value);
    d.querySelector("[data-preview]").textContent =
      typeof current.data.intent === "string"
        ? current.data.intent
        : current.data.description || "";
    const others = briefs.filter((r) => r.id !== current.id);
    d.querySelector("[data-alternatives]").innerHTML = others.length
      ? "<details><summary>比较其他候选（" +
        others.length +
        "）</summary>" +
        others
          .map(
            (r) =>
              '<div data-option><label class="choice-row"><input type="checkbox" data-compare value="' +
              esc(r.id) +
              '"> ' +
              esc(title(r)) +
              "</label><p>" +
              esc(
                typeof r.data.intent === "string"
                  ? r.data.intent
                  : r.data.description || "",
              ) +
              '</p><label class="field"><input type="text" data-reason hidden maxlength="3000" placeholder="保留为备选的理由" aria-label="保留为备选的理由"></label></div>',
          )
          .join("") +
        "</details>"
      : "";
    d.querySelectorAll("[data-compare]").forEach(
      (box) =>
        (box.onchange = () => {
          const why = box
            .closest("[data-option]")
            .querySelector("[data-reason]");
          why.hidden = !box.checked;
          why.required = box.checked;
        }),
    );
  }
  chosen.onchange = display;
  display();
}

function storyboard(ctx) {
  const projectId = ctx.requireProject();
  const plans = ctx
    .records("Plan")
    .filter(
      (p) =>
        !p.data.locked &&
        !["approved", "frozen", "locked"].includes(p.data.status),
    );
  const assets = ctx
    .records("AssetVersion")
    .filter((r) => r.data.probe?.has_video && !r.data.probe?.is_still);
  const assetName = (a) =>
    a.data.name ||
    ctx.records("Asset").find((x) => x.id === a.data.asset_id)?.data.name ||
    "未命名素材";
  const requestId = uid();
  const d = modal(
    "分镜选片",
    '<form data-form class="form-stack">' +
      select("分镜方案", "plan", [["", "新建方案"], ...plans.map(choice)]) +
      field("方案名称", "plan_name", "", "text", 'required maxlength="200"') +
      field(
        "分镜位置",
        "slot",
        "",
        "text",
        'required list="selection-slots" maxlength="200"',
      ) +
      '<datalist id="selection-slots"></datalist>' +
      field(
        "必须满足的画面条件",
        "constraints",
        "",
        "text",
        'required maxlength="3000"',
      ) +
      select(
        "处理方式",
        "decision",
        [
          ["select", "选用素材"],
          ["gap", "登记缺口"],
        ],
        assets.length ? "select" : "gap",
      ) +
      '<div><label class="button secondary">导入视频<input data-source-file type="file" accept="video/*" style="position:absolute;width:1px;height:1px;opacity:0"></label> <output data-upload-status aria-live="polite"></output></div>' +
      '<div data-source class="form-stack">' +
      select(
        "候选素材",
        "asset",
        assets.map((a) => [a.id, assetName(a)]),
      ) +
      '<video data-shot-preview aria-label="原片预览" controls playsinline preload="metadata" style="width:100%;max-height:195px;background:#171718"></video>' +
      '<div class="form-grid">' +
      field("原片入点（秒）", "start", "0", "number", 'step="0.001" min="0"') +
      field("原片出点（秒）", "end", "", "number", 'step="0.001" min="0.001"') +
      "</div>" +
      '<label class="choice-row"><input type="checkbox" name="lock"> 锁定这个镜头</label></div>' +
      '<label data-unlock hidden><input type="checkbox" name="unlock"> 解锁并替换原选择</label>' +
      area("选用依据或缺口原因", "evidence", "", 'required maxlength="3000"') +
      '<div class="form-actions"><button type="submit" class="button secondary">保存分镜</button><button type="button" data-send-editor class="button primary">保存并加入剪辑</button></div>' +
      "</form>",
    async (form) => {
      const data = formData(form);
      const body = {
        project_id: projectId,
        request_id: requestId,
        plan_name: data.plan_name,
        slot: data.slot,
        constraints: data.constraints,
        decision: data.decision,
        evidence: data.evidence,
        unlock_previous: data.unlock === "on",
      };
      if (data.plan) body.plan = pin(plans.find((p) => p.id === data.plan));
      if (data.decision === "select") {
        const asset = assets.find((r) => r.id === data.asset);
        if (!asset) throw new Error("请先导入视频，或登记缺口。");
        Object.assign(body, {
          asset: pin(asset),
          source_in: Number(data.start),
          source_out: Number(data.end),
          lock: data.lock === "on",
        });
      }
      const result = await ctx.api("/production/selection/shot", {
        method: "POST",
        body,
      });
      await ctx.reloadData();
      if (form.dataset.destination === "editor" && data.decision === "select") {
        const media = assets.find(
          (m) => m.id === result.slot.selected_source.asset.id,
        );
        sessionStorage.setItem(
          "ship-add-shot",
          JSON.stringify({
            project_id: projectId,
            asset_id: media.data.asset_id,
            asset_version_id: media.id,
            asset_revision: result.slot.selected_source.asset.version,
            source_in: result.slot.selected_source.source_in,
            source_out: result.slot.selected_source.source_out,
            shot_id: result.slot.shot_ref.id,
            shot_version: result.slot.shot_ref.version,
          }),
        );
        ctx.nav("editor");
        return;
      }
      ctx.toast(data.decision === "gap" ? "缺口已登记" : "镜头已保存");
      await ctx.refresh();
    },
  );
  const named = (n) => d.querySelector('[name="' + n + '"]');
  const preview = d.querySelector("[data-shot-preview]");
  const form = d.querySelector("[data-form]");
  const send = d.querySelector("[data-send-editor]");
  send.onclick = () => {
    form.dataset.destination = "editor";
    form.requestSubmit();
  };
  form.querySelector("button[type=submit]").onclick = () => {
    form.dataset.destination = "";
  };
  const upload = d.querySelector("[data-source-file]");
  upload.onchange = async () => {
    if (!upload.files.length) return;
    const status = d.querySelector("[data-upload-status]");
    upload.disabled = true;
    status.textContent = "正在导入…";
    try {
      const result = await uploadFile(ctx.api, projectId, upload.files[0]);
      await ctx.reloadData();
      const version = ctx
        .records("AssetVersion")
        .find(
          (v) =>
            v.data.asset_id === result.asset.id &&
            v.data.variant === "original",
        );
      if (!version?.data.probe?.has_video || version.data.probe.is_still)
        throw new Error("请选择有时长的视频素材。");
      if (!assets.some((a) => a.id === version.id)) assets.push(version);
      if (
        !Array.from(named("asset").options).some((o) => o.value === version.id)
      )
        named("asset").add(new Option(assetName(version), version.id));
      named("asset").value = version.id;
      named("decision").value = "select";
      showSource();
      status.textContent = "已导入";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      upload.disabled = false;
      upload.value = "";
    }
  };
  function showSource() {
    const selected = named("decision").value === "select";
    d.querySelector("[data-source]").hidden = !selected;
    send.hidden = !selected;
    for (const n of ["asset", "start", "end"]) named(n).required = selected;
    const a = assets.find((r) => r.id === named("asset").value);
    if (a && selected) {
      preview.src = ctx.assetUrl(a);
      named("end").value = Number(a.data.probe.duration).toFixed(3);
    } else {
      preview.removeAttribute("src");
      preview.load();
    }
  }
  function loadSlot() {
    const plan = plans.find((p) => p.id === named("plan").value);
    const slot = plan?.data.slots?.find(
      (s) =>
        s.name === named("slot").value || s.slot_id === named("slot").value,
    );
    named("constraints").value = slot?.constraints || "";
    named("evidence").value = slot?.evidence || "";
    named("unlock").checked = false;
    d.querySelector("[data-unlock]").hidden = !slot?.locked;
    named("lock").checked = Boolean(slot?.locked);
    if (slot) {
      named("decision").value = slot.status === "gap" ? "gap" : "select";
      if (slot.selected_source)
        named("asset").value = slot.selected_source.asset.id;
    }
    showSource();
    if (slot?.selected_source) {
      named("start").value = slot.selected_source.source_in;
      named("end").value = slot.selected_source.source_out;
    }
  }
  named("plan").onchange = () => {
    const plan = plans.find((p) => p.id === named("plan").value);
    named("plan_name").value = plan ? title(plan) : "";
    d.querySelector("datalist").innerHTML = (plan?.data.slots || [])
      .map(
        (s) =>
          '<option value="' + esc(s.name || s.slot_id || "") + '"></option>',
      )
      .join("");
    named("slot").value = "";
    loadSlot();
  };
  named("slot").onchange = loadSlot;
  named("asset").onchange = showSource;
  named("decision").onchange = showSource;
  named("start").onchange = () => {
    if (preview.readyState >= 1)
      preview.currentTime = Number(named("start").value) || 0;
  };
  showSource();
}

export function selectionForFeature(id) {
  if (id === "B02") return { label: "试听与打点", open: openRhythm };
  if (id === "T06") return { label: "选择并批准简报", open: review };
  if (id === "N02") return { label: "挑选分镜素材", open: storyboard };
  return null;
}
