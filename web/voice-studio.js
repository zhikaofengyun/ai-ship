import { $, $$, esc, field, area, select, modal, uid, time } from "./ui.js";

const endpoint = "/production/voice-studio/";
const pin = (r) => ({ id: r.id, version: r.version });
const names = {
  subject: "主体",
  product: "SKU",
  quantity: "数量",
  unit: "单位",
  action: "动作",
  duration: "长度",
};
const numberField = (label, name, value = "", attrs = "") =>
  field(label, name, value, "number", 'min="0" step="0.001" ' + attrs);
const action = (id, label, primary = false) =>
  '<button type="button" data-v-action="' +
  id +
  '" class="button ' +
  (primary ? "primary" : "secondary") +
  '">' +
  esc(label) +
  "</button>";

export function openVoiceStudio(ctx, initialAudio = null) {
  const project = ctx.requireProject();
  const records = (kind) =>
    ctx.records(kind).filter((r) => r.project_id === project);
  const stamp = (r) =>
    (r.data.name || r.data.filename || r.kind) + " · v" + r.version;
  let maps = records("VoiceMap").filter((r) => r.data.changed_anchors),
    observations = records("Tag").filter(
      (r) => r.data.method === "human_range_review",
    );
  let plans = records("Plan").filter((r) =>
    r.data.slots?.some((s) => s.anchor && s.candidates),
  );
  let plan = initialAudio ? null : plans[0],
    map = initialAudio
      ? null
      : maps.find((r) => r.id === plan?.data.voice_map_version?.id) || maps[0];
  let audio = initialAudio?.id || map?.data.audio_version?.id || "",
    script =
      initialAudio?.data.script_version?.id ||
      map?.data.script_version?.id ||
      "",
    title = map?.data.name || "口播分镜";
  let media = records("AssetVersion").filter(
    (r) => r.data.probe?.has_video && !r.data.probe?.is_still,
  );
  let timeline = null,
    artifact = null,
    marked = null,
    step = plan ? "plan" : "cues",
    busy = false;
  let tagMedia = media[0]?.id || "";
  const ids = new Set(observations.map((r) => r.id)),
    locks = new Map(),
    unlock = new Set();
  const cueDraft = (m) =>
    (m?.data.anchors || []).map((a) => ({
      key: a.anchor_id,
      words: a.token,
      meaning: {
        subject: a.semantic.subject,
        quantity: a.semantic.quantity,
        unit: a.semantic.unit,
        action: a.semantic.action,
        product: a.semantic.product ? pin(a.semantic.product) : null,
      },
      start: a.sample_range
        ? a.sample_range[0] / m.data.clock.sample_rate
        : null,
      end: a.sample_range ? a.sample_range[1] / m.data.clock.sample_rate : null,
      heard: a.timing_status === "human_verified",
      price_caption: !!a.price_caption,
    }));
  let cues = cueDraft(map);
  const d = modal(
    "口播与画面对齐",
    '<div data-v-nav class="director-actions"></div><div data-v-body></div><p role="status" data-v-status></p>',
    null,
    true,
  );
  const box = $("[data-v-body]", d);
  const status = (x) => {
    $("[data-v-status]", d).textContent = x;
  };
  const active = () =>
    d.open && d.contains(box) && ctx.state.projectId === project;
  const getName = (name) =>
    box.querySelector('[name="' + name + '"]')?.value || "";
  const find = (kind, id) => records(kind).find((r) => r.id === id);
  const options = (items, empty) => [
    ["", empty],
    ...items.map((r) => [r.id, stamp(r)]),
  ];
  const currentAudio = () => find("Audio", audio);
  const meaningFields = (prefix) =>
    '<div class="form-grid">' +
    field("画面主体", prefix + "subject", "", "text", 'maxlength="200"') +
    select(
      "商品事实",
      prefix + "product",
      options(records("Product"), "一般实体"),
    ) +
    field("数量", prefix + "quantity", "", "number", 'min="1" step="1"') +
    field("单位", prefix + "unit", "") +
    field("动作", prefix + "action", "") +
    "</div>";
  function readMeaning(prefix) {
    const subject = getName(prefix + "subject").trim(),
      quantity = getName(prefix + "quantity"),
      unit = getName(prefix + "unit").trim();
    if (!subject) throw Error("请填写需要核对的画面主体。");
    if (Boolean(quantity) !== Boolean(unit))
      throw Error("请同时填写数量和单位。");
    const p = find("Product", getName(prefix + "product"));
    return {
      subject,
      product: p ? pin(p) : null,
      quantity: quantity ? Number(quantity) : null,
      unit: unit || null,
      action: getName(prefix + "action").trim() || null,
    };
  }
  function facts(a) {
    return [
      a.subject,
      a.product ? find("Product", a.product.id)?.data.SKU : null,
      a.quantity ? String(a.quantity) + a.unit : "",
      a.action,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  const player = (r, tag) =>
    r
      ? "<" +
        tag +
        ' data-v-player controls preload="metadata" src="' +
        esc(ctx.assetUrl(r)) +
        '" style="width:100%;max-height:14rem"></' +
        tag +
        ">"
      : "";
  function readNav() {
    const nav = $("[data-v-nav]", d);
    nav.innerHTML = [
      ["cues", "口播锚点"],
      ["visual", "画面证据"],
      ["plan", "匹配与交付"],
    ]
      .map(
        ([k, label]) =>
          '<button type="button" data-step="' +
          k +
          '" aria-pressed="' +
          (k === step) +
          '" class="button ' +
          (k === step ? "primary" : "ghost") +
          '">' +
          label +
          "</button>",
      )
      .join("");
  }
  function rhythmControls(slots) {
    if (slots.length < 2 || plan?.data.status !== "ready_for_review") return "";
    const previous = plan.data.rhythm;
    const music = records("Music").filter(
      (r) => r.data.use_markers && r.data.analysis_method === "manual_marks",
    );
    const types = { beat: "拍点", downbeat: "强拍", section: "段落边界" };
    const controls =
      select(
        "剪点策略",
        "cut-mode",
        [
          ["semantic", "语义优先"],
          ["beat", "贴合音乐标记"],
          ["manual", "手动剪点"],
        ],
        previous?.policy || "semantic",
      ) +
      field(
        "允许偏移（毫秒）",
        "tolerance",
        previous
          ? Math.round((previous.tolerance_samples * 1000) / previous.rate)
          : 120,
        "number",
        'min="0" max="2000" step="1" required',
      ) +
      select(
        "已打点音轨（拍点策略使用）",
        "rhythm-music",
        options(music, "选择音轨"),
      ) +
      Object.entries(types)
        .map(
          ([t, l]) =>
            '<label><input type="checkbox" name="marker-type" value="' +
            t +
            '" checked> ' +
            l +
            "</label>",
        )
        .join("") +
      slots
        .slice(0, -1)
        .map((s, i) =>
          numberField(s.token + " 之后的手动剪点（秒）", "cut-" + i),
        )
        .join("");
    const boundaries = (previous?.boundaries || [])
      .map((b) => {
        const value =
          b.cut_sample == null
            ? "保留原剪点"
            : (b.cut_sample / previous.rate).toFixed(3) +
              " 秒，偏移 " +
              ((b.offset_samples * 1000) / previous.rate).toFixed(1) +
              " 毫秒";
        const markers = b.eligible_markers.map((m) => types[m.type]).join("、");
        return (
          "<p>" +
          esc(
            b.after +
              " → " +
              b.before +
              " · " +
              value +
              (markers ? " · " + markers : ""),
          ) +
          "</p>"
        );
      })
      .join("");
    return (
      "<details data-rhythm><summary>口播剪点与节奏</summary>" +
      controls +
      action("rhythm", "计算并保存剪点") +
      boundaries +
      "</details>"
    );
  }
  function draw() {
    readNav();
    if (step === "cues") {
      box.innerHTML =
        '<div class="form-stack">' +
        select("口播版本", "map", options(maps, "新建口播"), map?.id || "") +
        field("名称", "title", title) +
        select(
          "脚本",
          "script",
          options(records("Script"), "从下方词句建立脚本"),
          script,
        ) +
        select(
          "试听音轨",
          "audio",
          options(records("Audio"), "无时码分镜草稿"),
          audio,
        ) +
        player(currentAudio(), "audio") +
        "<details><summary>从原片取出口播</summary>" +
        select(
          "含声音的素材",
          "audio-source",
          options(
            records("AssetVersion").filter((r) => r.data.probe?.has_audio),
            "选择素材",
          ),
        ) +
        action("extract", "提取音轨") +
        "</details>" +
        "<div data-v-cues>" +
        cues
          .map(
            (c, i) =>
              '<div class="director-run-meta"><strong>' +
              esc(c.words) +
              "</strong><p>" +
              esc(facts(c.meaning)) +
              " · " +
              (c.start == null
                ? "未定时"
                : esc(c.start + "–" + c.end + " 秒")) +
              '</p><button type="button" class="button ghost" data-edit="' +
              i +
              '">编辑</button><button type="button" class="button ghost" data-remove="' +
              i +
              '">移除</button></div>',
          )
          .join("") +
        "</div>" +
        "<details data-v-edit " +
        (!cues.length ? "open" : "") +
        "><summary>编辑词句</summary>" +
        field("实际词句", "words", "") +
        meaningFields("c") +
        '<div class="form-grid">' +
        numberField("音频起点（秒）", "start") +
        numberField("音频终点（秒）", "end") +
        '</div><div class="director-actions">' +
        action("start", "取当前起点") +
        action("end", "取当前终点") +
        '</div><label><input type="checkbox" name="heard"> 已试听并核对这段发音</label><label><input type="checkbox" name="price"> 显示商品事实中的价格</label>' +
        action("add", "加入词句") +
        "</details>" +
        '<div class="director-actions">' +
        action("save", "保存口播锚点", true) +
        "</div></div>";
      if (marked !== null && cues[marked]) {
        const c = cues[marked];
        $("[data-v-edit]", box).open = true;
        for (const [k, v] of Object.entries({
          words: c.words,
          csubject: c.meaning.subject,
          cproduct: c.meaning.product?.id,
          cquantity: c.meaning.quantity,
          cunit: c.meaning.unit,
          caction: c.meaning.action,
          start: c.start,
          end: c.end,
        }))
          box.querySelector('[name="' + k + '"]').value = v ?? "";
        box.querySelector('[name="heard"]').checked = c.heard;
        box.querySelector('[name="price"]').checked = c.price_caption;
      }
    } else if (step === "visual") {
      box.innerHTML =
        '<div class="form-stack">' +
        select("原片", "media", options(media, "选择定时视频"), tagMedia) +
        player(
          media.find((m) => m.id === tagMedia),
          "video",
        ) +
        '<div class="form-grid">' +
        numberField("可用起点（秒）", "start", "0") +
        numberField("可用终点（秒）", "end") +
        numberField("证据画面（秒）", "frame", "0") +
        '</div><div class="director-actions">' +
        action("start", "标记起点") +
        action("end", "标记终点") +
        action("frame", "标记证据帧") +
        "</div>" +
        meaningFields("v") +
        area("该区间实际看到的内容", "note", "") +
        action("observe", "保存画面观察", true) +
        observations
          .map(
            (t) =>
              "<p>" +
              esc(facts(t.data.visual_facts)) +
              " · " +
              esc(t.data.source_range.join("–")) +
              " 秒</p>",
          )
          .join("") +
        "</div>";
    } else {
      const slots = plan?.data.slots || [];
      box.innerHTML =
        '<div class="form-stack">' +
        select(
          "口播版本",
          "match-map",
          options(maps, "选择口播"),
          map?.id || "",
        ) +
        (plans.length
          ? select(
              "已有方案",
              "match-plan",
              options(plans, "新方案"),
              plan?.id || "",
            )
          : "") +
        "<fieldset><legend>参与匹配的画面</legend>" +
        observations
          .map(
            (t) =>
              '<label><input type="checkbox" data-evidence="' +
              esc(t.id) +
              '" ' +
              (ids.has(t.id) ? "checked" : "") +
              "> " +
              esc(facts(t.data.visual_facts)) +
              " · " +
              esc(t.data.review_note) +
              "</label>",
          )
          .join("") +
        "</fieldset>" +
        action("match", "匹配镜头", true) +
        slots
          .map((s, i) => {
            const chosen = observations.find((t) => t.id === s.selected?.id),
              good = s.candidates.filter((c) => !c.differences.length);
            return (
              '<section class="director-run-meta"><h3>' +
              esc(s.token) +
              "</h3><p>" +
              esc(
                (s.time_ok ? "发音已核对" : "发音待核对") +
                  " · " +
                  (chosen ? chosen.data.review_note : "缺少合格镜头"),
              ) +
              "</p>" +
              (good.length
                ? select(
                    "锁定镜头",
                    "lock-" + i,
                    [
                      ["", "自动选材"],
                      ...good.map((c) => [
                        c.tag.id,
                        observations.find((t) => t.id === c.tag.id)?.data
                          .review_note || "已审画面",
                      ]),
                    ],
                    locks.get(s.anchor)?.id ||
                      (s.locked && !unlock.has(s.anchor) ? s.selected.id : ""),
                  )
                : "") +
              (chosen
                ? '<video controls preload="none" style="max-height:10rem;width:100%" src="' +
                  esc(ctx.assetUrl({ id: chosen.data.media.id })) +
                  "#t=" +
                  chosen.data.source_range.join(",") +
                  '"></video>'
                : "") +
              s.candidates
                .filter((c) => c.differences.length)
                .map(
                  (c) =>
                    "<p>排除 · " +
                    esc(
                      observations.find((t) => t.id === c.tag.id)?.data
                        .review_note || "镜头",
                    ) +
                    " · " +
                    esc(c.differences.map((k) => names[k]).join("、")) +
                    "</p>",
                )
                .join("") +
              (!chosen
                ? area("补镜要求或替换台词", "repair-" + i, "") +
                  '<div class="director-actions">' +
                  action("repair-gen-" + i, "交给镜头生成") +
                  action("repair-script-" + i, "另存改稿") +
                  "</div>"
                : "") +
              "</section>"
            );
          })
          .join("") +
        rhythmControls(slots) +
        (plan?.data.status === "ready_for_review"
          ? action("adopt", "采用声画方案", true)
          : "") +
        (timeline
          ? '<div class="director-actions">' +
            action("export", "锁版并导出") +
            '<a class="button secondary" href="#editor/project-timeline/' +
            esc(timeline.id) +
            '" data-open-editor>手动精剪</a></div>'
          : "") +
        (artifact
          ? '<video controls aria-label="导出成片" style="width:100%" src="/api/v1/media/artifacts/' +
            esc(artifact.id) +
            '/file"></video><a class="button primary" href="/api/v1/media/artifacts/' +
            esc(artifact.id) +
            '/file" download>下载成片</a>'
          : "") +
        "</div>";
    }
  }
  const api = (path, body) =>
    ctx.api(endpoint + path, {
      method: "POST",
      body: { project_id: project, request_id: uid(), ...body },
    });
  const remember = () => {
    if (step === "cues") {
      title = getName("title");
      audio = getName("audio");
      script = getName("script");
    }
  };
  const bindMap = (m) => {
    map = m;
    cues = cueDraft(m);
    audio = m?.data.audio_version?.id || "";
    script = m?.data.script_version?.id || "";
    title = m?.data.name || "口播分镜";
    marked = null;
    locks.clear();
    unlock.clear();
    plan = plans.find((p) => p.data.voice_map_version?.id === m?.id);
    timeline = artifact = null;
  };
  const update = (list, r) => [r, ...list.filter((v) => v.id !== r.id)];
  async function handle(id) {
    remember();
    if (id === "start" || id === "end" || id === "frame") {
      const el = $("[data-v-player]", box);
      if (!el || !el.readyState) throw Error("请先播放实际素材。");
      box.querySelector('[name="' + id + '"]').value =
        el.currentTime.toFixed(3);
      return;
    }
    if (id === "extract") {
      const m = find("AssetVersion", getName("audio-source"));
      if (!m) throw Error("请选择含声音的素材。");
      const result = await ctx.api(
        "/media/" + encodeURIComponent(m.id) + "/extract-audio",
        {
          method: "POST",
          body: { project_id: project, expected_revision: m.version },
        },
      );
      await ctx.reloadData();
      audio = result.audio.id;
      for (const c of cues) c.heard = false;
    } else if (id === "add") {
      const words = getName("words").trim();
      if (!words) throw Error("请填写实际词句。");
      const c = {
        key: marked !== null ? cues[marked].key : uid(),
        words,
        meaning: readMeaning("c"),
        start: getName("start") ? Number(getName("start")) : null,
        end: getName("end") ? Number(getName("end")) : null,
        heard: box.querySelector('[name="heard"]').checked,
        price_caption: box.querySelector('[name="price"]').checked,
      };
      if (marked === null) cues.push(c);
      else cues[marked] = c;
      marked = null;
    } else if (id === "save") {
      if (!cues.length) throw Error("请先加入至少一条词句。");
      const input = {
        name: title,
        script: script ? pin(find("Script", script)) : null,
        audio: audio ? pin(find("Audio", audio)) : null,
        cues,
        previous: map ? pin(map) : null,
        reviewed_audio: audio ? pin(find("Audio", audio)) : null,
      };
      const r = await api("maps", input);
      maps = update(maps, r.map);
      await ctx.reloadData();
      bindMap(r.map);
      step = "plan";
    } else if (id === "observe") {
      const m = media.find((r) => r.id === tagMedia);
      if (!m) throw Error("请先选择带画面的素材。");
      if (!getName("end") || !getName("note").trim())
        throw Error("请填写可用终点和画面观察。");
      const r = await api("observations", {
        media: pin(m),
        meaning: readMeaning("v"),
        start: getName("start"),
        end: getName("end"),
        seen_at: getName("frame"),
        visual_note: getName("note"),
      });
      observations = update(observations, r.observation);
      ids.add(r.observation.id);
      step = "plan";
    } else if (id === "match") {
      if (!map) throw Error("请先保存口播锚点。");
      const r = await api("plans", {
        voice_map: pin(map),
        observations: observations.filter((o) => ids.has(o.id)).map(pin),
        previous: plan ? pin(plan) : null,
        locked: Object.fromEntries(locks),
        unlock: [...unlock],
      });
      plan = r.plan;
      plans = update(plans, plan);
      locks.clear();
      unlock.clear();
      timeline = artifact = null;
    } else if (id === "rhythm") {
      if (locks.size || unlock.size)
        throw Error("请先匹配镜头以保存锁定范围。");
      const mode = getName("cut-mode"),
        tolerance = getName("tolerance");
      const music = find("Music", getName("rhythm-music"));
      if (!tolerance || !Number.isInteger(Number(tolerance)))
        throw Error("容差须为整毫秒。");
      const manual = {};
      if (mode === "manual")
        plan.data.slots.slice(0, -1).forEach((s, i) => {
          const value = getName("cut-" + i);
          if (value !== "") manual[s.anchor] = value;
        });
      const r = await api("rhythm", {
        plan: pin(plan),
        mode,
        music: mode === "beat" && music ? pin(music) : null,
        tolerance_ms: Number(tolerance),
        manual_cuts: manual,
        marker_types: Array.from(
          box.querySelectorAll('[name="marker-type"]:checked'),
          (e) => e.value,
        ),
      });
      plan = r.plan;
      plans = update(plans, plan);
      timeline = artifact = null;
    } else if (id === "adopt") {
      if (locks.size || unlock.size)
        throw Error("请先匹配镜头以保存刚才的锁定选择。");
      const r = await api("adopt", { plan: pin(plan) });
      timeline = r.timeline;
      await ctx.reloadData();
    } else if (id === "export") {
      if (!timeline.data.locked) {
        const r = await ctx.command(
          "LockTimelineRevision",
          { confirmed_hash: timeline.hash },
          timeline,
        );
        timeline =
          r.record ||
          (await ctx.api("/records/" + encodeURIComponent(timeline.id)));
      }
      const r = await ctx.api("/media/render", {
        method: "POST",
        body: {
          project_id: project,
          timeline_id: timeline.id,
          expected_revision: timeline.version,
        },
      });
      artifact = r.artifact;
    } else if (id.startsWith("repair-")) {
      const [, kind, raw] = id.split("-"),
        i = Number(raw),
        instruction = getName("repair-" + i);
      if (!instruction.trim()) throw Error("请填写补镜要求或替换台词。");
      const r = await api("repair", {
        plan: pin(plan),
        anchor: plan.data.slots[i].anchor,
        action: kind === "gen" ? "generation" : "rewrite",
        instruction,
      });
      await ctx.reloadData();
      if (kind === "script") {
        bindMap(null);
        script = r.script.id;
        title = r.script.data.name;
        step = "cues";
        draw();
        status("改稿已保存。请核对新台词的音轨与词句。");
        return;
      }
      const pr = await ctx.api("/production/generation/profiles");
      const profiles = pr.profiles.filter(
        (p) => p.output_kind === "video" && !p.requires_reference,
      );
      if (!active()) return;
      if (!profiles.length) {
        status("补镜任务已保存，当前没有已配置的文生视频模型。");
        return;
      }
      const section = document.createElement("section");
      section.className = "form-stack";
      section.innerHTML =
        select(
          "视频模型",
          "model",
          profiles.map((p) => [p.id, p.label]),
        ) + action("queue", "提交生成任务");
      box.append(section);
      section.querySelector("button").onclick = async (e) => {
        e.stopPropagation();
        const b = e.currentTarget;
        b.disabled = true;
        try {
          await ctx.api("/production/generation", {
            method: "POST",
            body: {
              ...r.generation_request,
              profile_id: section.querySelector("select").value,
              seed: 0,
            },
          });
          status("已提交生成任务。素材入库后可回到画面证据核对。");
        } catch (err) {
          b.disabled = false;
          status(err.message);
        }
      };
      return;
    }
    if (active()) {
      draw();
      status(id === "add" ? "词句已加入，保存后生效。" : "已更新");
    }
  }
  d.addEventListener(
    "click",
    (e) => {
      if (!d.contains(box) || busy) return;
      if (e.target.closest("[data-open-editor]")) {
        d.close();
        return;
      }
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.step) {
        remember();
        step = b.dataset.step;
        draw();
        status("");
        return;
      }
      if (b.dataset.edit != null) {
        remember();
        marked = Number(b.dataset.edit);
        draw();
        return;
      }
      if (b.dataset.remove != null) {
        remember();
        cues.splice(Number(b.dataset.remove), 1);
        marked = null;
        draw();
        return;
      }
      const id = b.dataset.vAction;
      if (!id || id === "queue") return;
      busy = true;
      b.disabled = true;
      Promise.resolve(handle(id))
        .catch((err) => {
          if (active()) status(err.message);
        })
        .finally(() => {
          busy = false;
          b.disabled = false;
        });
    },
    { signal: modalSignal(d) },
  );
  box.onchange = (e) => {
    const t = e.target,
      n = t.name;
    if (n === "map") {
      bindMap(maps.find((r) => r.id === t.value));
      draw();
    } else if (n === "audio") {
      remember();
      for (const c of cues) c.heard = false;
      draw();
    } else if (n === "media") {
      tagMedia = t.value;
      draw();
    } else if (n === "match-map") {
      bindMap(maps.find((r) => r.id === t.value));
      draw();
    } else if (n === "match-plan") {
      plan = plans.find((r) => r.id === t.value);
      map = maps.find((m) => m.id === plan?.data.voice_map_version?.id) || map;
      locks.clear();
      unlock.clear();
      timeline = artifact = null;
      draw();
    } else if (t.dataset.evidence) {
      if (t.checked) ids.add(t.dataset.evidence);
      else ids.delete(t.dataset.evidence);
    } else if (n?.startsWith("lock-")) {
      const slot = plan.data.slots[Number(n.split("-")[1])],
        tag = observations.find((r) => r.id === t.value);
      if (slot.locked) unlock.add(slot.anchor);
      if (tag) locks.set(slot.anchor, pin(tag));
      else locks.delete(slot.anchor);
    }
  };
  draw();
}

function modalSignal(dialog) {
  const controller = new AbortController();
  dialog.addEventListener("close", () => controller.abort(), { once: true });
  return controller.signal;
}
