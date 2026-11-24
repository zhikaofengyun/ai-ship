import { esc, icon } from "./ui.js";
import { mediaDuration, mediaLibrary, mediaName, mediaType } from "./media.js";

const routes = {
  "N04-R1": "分镜组合",
  "N04-R2": "分层轮换",
  "N04-R3": "人工改写",
};
const dimensions = {
  picture: "画面",
  text: "画内文字",
  sound: "声音",
  structure: "来源结构",
};
const reasons = {
  same_sequence: "与原方案相同",
  reuse_quota: "超过复用配额",
  product_evidence: "商品画面证据不足",
  duplicate: "实质差异不足",
  duplicates_sibling: "与其他候选重复",
  duplicates_history: "与历史方案重复",
  opening_requirement: "不满足首镜条件",
  internal_repetition: "引入片内重复",
};
const checks = (name, values, checked) =>
  values
    .map(
      ([v, s]) =>
        `<label class="check"><input type="checkbox" name="${name}" value="${esc(v)}" ${checked.has(String(v)) ? "checked" : ""}>${esc(s)}</label>`,
    )
    .join("");
const options = (values, selected) =>
  values
    .map(
      ([k, v]) =>
        `<option value="${esc(k)}" ${k === selected ? "selected" : ""}>${esc(v)}</option>`,
    )
    .join("");
const percent = (value) =>
  value == null ? "未测得" : `${Math.round(value * 100)}%`;
const key = () =>
  globalThis.crypto?.randomUUID?.() ||
  "variant-" + Date.now() + Math.random().toString(36).slice(2);

export function createVariantPanel(ctx, onSelect) {
  let bound = "",
    host = null,
    detail = null,
    opened = false,
    pending = false,
    loaded = false,
    families = [],
    message = "";
  let formState = null,
    lastIntent = "",
    requestId = "",
    loadingSerial = 0;
  const baseRoute = () => "/production/variants/" + encodeURIComponent(bound);
  const library = () =>
    mediaLibrary(ctx.state.records).filter(
      (s) =>
        (s.kind === "Asset" || s.kind === "AssetVersion") &&
        mediaType(s) === "video" &&
        mediaDuration(s) > 0,
    );
  function state() {
    const shots = detail.plan.data.shots || [];
    if (!formState)
      formState = {
        route: "N04-R1",
        count: "3",
        seed: "1",
        history: "30",
        quota: "",
        dims: Object.keys(dimensions),
        locks: (detail.plan.data.locked_slots || []).map(String),
        productIds: (detail.plan.data.variant_dependency_refs || [])
          .filter((r) => r.kind === "Product")
          .map((r) => r.id),
        pool: [],
        manual: shots.map((s) => s.asset_id),
        starts: shots.map((s) => String(s.source_in)),
        note: "",
        policy: "",
        reviews: {},
      };
    return formState;
  }
  function inputs() {
    const d = state(),
      shots = detail.plan.data.shots || [],
      sources = library();
    const named = sources.map((r) => [r.id, mediaName(r)]);
    const ps = (ctx.records?.("Product") || []).filter(
      (p) => p.project_id === detail.plan.project_id,
    );
    const policies = (ctx.records?.("Policy") || []).filter(
      (p) =>
        p.project_id === detail.plan.project_id &&
        ["approved", "active"].includes(p.data.release_state || p.data.status),
    );
    return `<form data-variant-form class="form-grid">
      <label>变体路线<select name="variant_route">${options(Object.entries(routes), d.route)}</select></label>
      ${d.route !== "N04-R3" ? `<label>候选数量<input name="variant_count" type="number" value="${esc(d.count)}" min="1" max="8" required></label>` : ""}
      ${
        d.route === "N04-R3"
          ? `<div class="span2">${shots.map((s, i) => `<div class="form-grid"><label>镜头 ${i + 1}<select name="variant_manual" ${d.locks.includes(String(i)) ? "disabled" : ""}>${options(named, d.manual[i])}</select></label><label>源入点（秒）<input name="variant_start" type="number" step="0.001" min="0" value="${esc(d.starts[i])}" ${d.locks.includes(String(i)) ? "disabled" : ""}></label></div>`).join("")}</div><label class="span2">创意改动<textarea name="variant_note" required maxlength="500">${esc(d.note)}</textarea></label>`
          : `<details class="span2"><summary>可替换素材</summary>${checks("variant_pool", named, new Set(d.pool))}</details>`
      }
      <details class="span2"><summary>锁定镜头与比较条件</summary>
      <fieldset><legend>保留原位</legend>${checks(
        "variant_lock",
        shots.map((s, i) => [String(i), `镜头 ${i + 1} · ${s.name}`]),
        new Set(d.locks),
      )}</fieldset>
      <fieldset><legend>比较维度</legend>${checks("variant_dim", Object.entries(dimensions), new Set(d.dims))}</fieldset>
      <div class="form-grid"><label>历史窗口（天）<input name="variant_history" type="number" min="0" max="365" value="${esc(d.history)}" required></label>
      <label>每项素材复用上限<input name="variant_quota" type="number" min="1" max="1000" value="${esc(d.quota)}" placeholder="不限"></label>
      <label>组合种子<input type="number" name="variant_seed" min="0" max="2147483647" value="${esc(d.seed)}" required></label></div>
      ${policies.length ? `<label>已批准的轮换策略<select name="variant_policy">${options([["", "手动设置"], ...policies.map((x) => [x.id, x.data.name || "轮换策略"])], d.policy)}</select></label>` : ""}
      ${
        ps.length
          ? `<fieldset><legend>必须匹配的商品</legend>${checks(
              "variant_product",
              ps.map((x) => [x.id, x.data.SKU || x.data.name]),
              new Set(d.productIds),
            )}</fieldset>`
          : ""
      }</details>
      <button class="button secondary" type="submit" ${pending ? "disabled" : ""}>${icon("layers")} 比较镜头方案</button></form>`;
  }
  function results() {
    const d = state();
    return families
      .map(
        (
          f,
        ) => `<section aria-label="候选结果"><h4>${esc(routes[f.route])} · ${f.actual}/${f.requested} 个候选</h4>
      ${
        Object.keys(f.rejected || {}).length && f.actual < f.requested
          ? `<p>${Object.entries(f.rejected)
              .map(([k, v]) => esc((reasons[k] || "条件未满足") + " " + v))
              .join(" · ")}</p>`
          : ""
      }
      ${(f.candidates || [])
        .map(
          (
            c,
            i,
          ) => `<details><summary>变体 ${i + 1} · ${esc(c.shots.map((s) => s.name).join(" → "))}</summary>
      <dl>${Object.entries(dimensions)
        .map(
          ([k, n]) =>
            `<div><dt>${n}差异</dt><dd>${percent(c.vs_base[k])}</dd></div>`,
        )
        .join("")}</dl>
      ${c.vs_history?.length ? `<p>另已对照 ${c.vs_history.length} 个历史方案</p>` : ""}
      <ol>${c.shots.map((s) => `<li>${esc(s.name)} · ${Number(s.source_in).toFixed(2)}–${Number(s.source_out).toFixed(2)} 秒</li>`).join("")}</ol>
      <form data-family="${esc(f.id)}" data-plan="${esc(c.plan.id)}"><label>复核意见<input name="review" maxlength="800" value="${esc(d.reviews[c.plan.id] || "")}" required placeholder="商品、选段与节奏"></label><button class="button secondary" ${pending ? "disabled" : ""}>审阅此版本</button></form></details>`,
        )
        .join("")}
      </section>`,
      )
      .join("");
  }
  function collect(form) {
    const d = state(),
      fd = new FormData(form);
    for (const [field, prop] of Object.entries({
      variant_route: "route",
      variant_count: "count",
      variant_history: "history",
      variant_quota: "quota",
      variant_seed: "seed",
      variant_note: "note",
      variant_policy: "policy",
    }))
      if (fd.has(field)) d[prop] = String(fd.get(field));
    for (const [field, prop] of Object.entries({
      variant_dim: "dims",
      variant_lock: "locks",
      variant_product: "productIds",
      variant_pool: "pool",
    }))
      if (form.querySelector('[name="' + field + '"]'))
        d[prop] = fd.getAll(field).map(String);
    const manual = Array.from(form.querySelectorAll('[name="variant_manual"]'));
    if (manual.length) {
      d.manual = manual.map((el) => el.value);
      d.starts = Array.from(
        form.querySelectorAll('[name="variant_start"]'),
        (el) => el.value,
      );
    }
  }
  function draw() {
    if (
      !host ||
      !detail?.plan ||
      !["review", "draft", "rendered"].includes(detail.session.data.stage)
    ) {
      if (host) host.replaceChildren();
      return;
    }
    host.innerHTML = `<details class="director-gaps" data-variant-group ${opened ? "open" : ""}><summary>${icon("layers")} 镜头变体</summary><div>${inputs()}<p role="status" ${message || pending ? "" : "hidden"}>${esc(message || "正在比较镜头…")}</p>${results()}</div></details>`;
    const group = host.querySelector("[data-variant-group]");
    group.ontoggle = () => {
      if (!group.isConnected) return;
      opened = group.open;
      if (opened && !loaded && !pending) load();
    };
    const form = host.querySelector("[data-variant-form]");
    form.oninput = () => collect(form);
    form.onchange = (evt) => {
      collect(form);
      if (["variant_route", "variant_lock"].includes(evt.target.name)) draw();
    };
    form.onsubmit = async (e) => {
      e.preventDefault();
      collect(form);
      if (pending) return;
      const d = state(),
        src = library(),
        map = new Map(src.map((s) => [s.id, s]));
      const pin = (id) => ({ id, version: map.get(id)?.version || 1 });
      const projectRecords = ctx.state.records,
        ppin = (id) => ({
          id,
          version: projectRecords.find((s) => s.id === id)?.version || 1,
        });
      const b = {
        expected_revision: detail.session.version,
        route: d.route,
        count: d.route === "N04-R3" ? 1 : Number(d.count),
        seed: Number(d.seed),
        dimensions: d.dims,
        locked_slots: d.locks.map(Number),
        history_days: Number(d.history),
        max_uses: d.quota ? Number(d.quota) : null,
        products: d.productIds.map(ppin),
        policy: d.policy ? ppin(d.policy) : null,
      };
      if (d.route === "N04-R3") {
        b.manual_shots = d.manual.map((id, i) => ({
          ...pin(id),
          source_in: Number(d.starts[i]),
        }));
        b.manual_reason = d.note;
      } else b.pool = d.pool.map(pin);
      const intent = JSON.stringify(b);
      if (intent !== lastIntent) {
        lastIntent = intent;
        requestId = key();
      }
      const issued = bound;
      pending = true;
      message = "正在解码选段并比较…";
      draw();
      try {
        const f = await ctx.api(baseRoute(), {
          method: "POST",
          body: { ...b, request_id: requestId },
        });
        if (issued !== bound) return;
        families = [f, ...families.filter((x) => x.id !== f.id)];
        loaded = true;
        message = f.actual ? "" : "现有素材没有满足条件的变体。";
      } catch (err) {
        if (issued === bound) message = err.message;
      } finally {
        if (issued === bound) {
          pending = false;
          draw();
        }
      }
    };
    for (const choice of host.querySelectorAll("form[data-plan]")) {
      const id = choice.dataset.plan;
      choice.oninput = () =>
        (state().reviews[id] = choice.elements.review.value);
      choice.onsubmit = async (e) => {
        e.preventDefault();
        if (pending) return;
        const f = families.find((v) => v.id === choice.dataset.family),
          plan = f.candidates.find((c) => c.plan.id === id).plan;
        const issued = bound,
          note = choice.elements.review.value.trim();
        pending = true;
        message = "正在打开选定版本…";
        draw();
        try {
          const v = await ctx.api(
            baseRoute() + "/" + encodeURIComponent(f.id) + "/select",
            {
              method: "POST",
              body: {
                expected_revision: f.version,
                plan: { id: plan.id, version: plan.version },
                review_note: note,
              },
            },
          );
          if (issued === bound && host?.isConnected) await onSelect(v);
        } catch (err) {
          if (issued === bound) message = err.message;
        } finally {
          if (issued === bound) {
            pending = false;
            draw();
          }
        }
      };
    }
  }
  async function load() {
    loaded = true;
    const serial = ++loadingSerial,
      issued = bound;
    try {
      const v = await ctx.api(baseRoute());
      if (issued === bound && serial === loadingSerial) {
        families = v.families;
        draw();
      }
    } catch (e) {
      if (issued === bound && serial === loadingSerial) {
        message = e.message;
        draw();
      }
    }
  }
  return {
    mount(el, next) {
      host = el;
      detail = next;
      if (bound !== (next?.session.id || "")) {
        bound = next?.session.id || "";
        loaded = false;
        opened = location.hash === "#production/variants";
        pending = false;
        families = [];
        formState = null;
        requestId = "";
        lastIntent = "";
        message = "";
        loadingSerial++;
      }
      draw();
    },
  };
}
