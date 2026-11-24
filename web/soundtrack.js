import { esc, field, modal, select, uid } from "./ui.js";
import { uploadFile } from "./upload.js";

const pinned = ({ id, version }) => ({ id, version });
const uses = [
  ["commercial", "商业发布"],
  ["editorial", "编辑用途"],
  ["private", "内部使用"],
];
const roles = [
  ["music", "背景音乐"],
  ["voice", "口播"],
  ["ambience", "环境声"],
  ["effect", "音效"],
];
const button = (label, action, primary = false) =>
  `<button type="button" class="button ${primary ? "primary" : "secondary"}" data-sound="${action}">${label}</button>`;
const num = (label, name, value, attributes = "") =>
  field(label, name, value, "number", `step="any" required ${attributes}`);
const named = (label, id) => [id, label];

export function openSoundtrack(ctx, initial = "rights") {
  const project = ctx.project(),
    pid = ctx.requireProject();
  const market = project?.data.market || "";
  const root = modal(
    "声音与混音",
    `<div class="form-actions" data-sound-tabs></div><div class="form-stack" data-sound-page></div><output data-sound-status aria-live="polite"></output>`,
    null,
    true,
  );
  const host = root.querySelector("[data-sound-page]"),
    tabs = root.querySelector("[data-sound-tabs]"),
    status = root.querySelector("[data-sound-status]");
  let tab =
    { B03: "rights", B04: "mix", B05: "mix", B06: "output" }[initial] ||
    initial;
  let licenseAudio = "",
    mode = "local_stock",
    use = "commercial",
    mixId = "",
    timelineId = "",
    artifactId = "",
    audition = null;
  const requestKeys = new Map(),
    cuts = [];
  const names = { rights: "音乐与许可", mix: "混音", output: "成片检查" };
  const records = (kind) =>
    ctx.records(kind).filter((r) => r.project_id === pid);
  const find = (kind, id) => records(kind).find((r) => r.id === id);
  const pick = (name) => host.querySelector('[name="' + name + '"]');
  const label = (r) => r?.data?.name || r?.data?.filename || r?.kind || "音轨";
  const audioName = (r) =>
    records("Asset").find((a) => a.id === r.data.asset_id)?.data?.name ||
    label(r);
  const withVersion = (r) => label(r) + " · 第 " + r.version + " 版";
  const options = (rows, name = label) => rows.map((r) => named(name(r), r.id));
  const music = () =>
    records("Music").filter(
      (r) =>
        r.data.soundtrack_mode === "local_stock" && r.data.scope?.use === use,
    );
  const mixes = () =>
    records("Mix").filter((r) => r.data.mix_protocol === "soundtrack_pcm_v1");
  const play = (row, video = false) =>
    row
      ? `<${video ? "video" : "audio"} controls preload="metadata" data-sound-player aria-label="${video ? "试听成片" : "试听声音"}" style="width:100%;max-height:280px" src="${esc(video ? "/api/v1/media/artifacts/" + encodeURIComponent(row.id) + "/file" : ctx.assetUrl(row))}"></${video ? "video" : "audio"}>`
      : "";
  async function post(route, body) {
    const signature = route + JSON.stringify(body);
    if (!requestKeys.has(signature)) requestKeys.set(signature, uid());
    return ctx.api("/production/soundtrack/" + route, {
      method: "POST",
      body: {
        project_id: pid,
        request_id: requestKeys.get(signature),
        ...body,
      },
    });
  }
  const action = (el, event, work) => {
    el.addEventListener(event, async (e) => {
      if (event === "submit") e.preventDefault();
      if (root.dataset.soundBusy === "1") return;
      root.dataset.soundBusy = "1";
      const trigger = e.submitter || el;
      trigger.disabled = true;
      status.textContent = "正在处理…";
      tabs.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        const msg = await work(e);
        status.textContent = msg || "";
      } catch (error) {
        status.textContent = error.message;
      } finally {
        trigger.disabled = false;
        delete root.dataset.soundBusy;
        tabs.querySelectorAll("button").forEach((b) => (b.disabled = false));
      }
    });
  };
  const wire = (id, work) => {
    const el = host.querySelector('[data-sound="' + id + '"]');
    if (el) action(el, "click", work);
  };
  const number = (fd, key) => Number(fd.get(key));
  function drawTabs() {
    tabs.innerHTML = Object.entries(names)
      .map(
        ([id, text]) =>
          `<button type="button" class="button ${tab === id ? "primary" : "secondary"}" aria-pressed="${tab === id}" data-tab="${id}">${text}</button>`,
      )
      .join("");
    tabs.querySelectorAll("button").forEach(
      (b) =>
        (b.onclick = () => {
          if (root.dataset.soundBusy === "1") return;
          tab = b.dataset.tab;
          status.textContent = "";
          draw();
        }),
    );
  }
  function drawRights() {
    const audio = records("Audio").filter((a) => a.data.asset_id),
      sources = records("AssetVersion").filter((a) => a.data.probe?.has_audio);
    const current = audio.find((a) => a.id === licenseAudio) || audio[0];
    const audioFields =
      mode === "local_stock"
        ? `<div class="form-grid">${select("实际音轨", "stock-audio", options(audio, audioName), current?.id)}${field("曲目名称", "stock-name", current ? audioName(current) : "", "text", "required maxlength=200")}</div>
    ${play(current)}<details ${audio.length ? "" : "open"}><summary>添加可用音轨</summary><div class="form-stack">${
      sources.length
        ? select(
            "项目素材",
            "stock-source",
            options(sources, (r) => r.data.filename || label(r)),
          ) + button("提取音轨", "extract")
        : ""
    }
    <label class="field"><span>从文件导入</span><input type="file" data-sound-file accept="audio/*,video/*"/></label></div></details>`
        : "";
    const platform = mode === "platform_sound";
    host.innerHTML = `<form data-rights class="form-stack">${select(
      "音乐加入方式",
      "mode",
      [
        ["local_stock", "已授权音乐 · 本地混入"],
        ["no_music", "无背景音乐"],
        ["platform_sound", "平台附曲"],
      ],
      mode,
    )}
      ${audioFields}${platform ? select("平台账号", "account", options(records("Account"))) + field("平台曲目 ID", "sound-id", "", "text", "required") : ""}
      <div class="form-grid">${select("作品用途", "purpose", uses, use)}${field("项目市场", "market", market, "text", "required")}</div>
      ${
        mode === "local_stock"
          ? `<div class="form-grid">${field("许可覆盖市场（逗号分隔）", "markets", "", "text", "required")}${select("许可覆盖用途", "allowed-use", uses, use)}</div>
      <label class="field"><span>许可截止日期与时间</span><input type="datetime-local" name="expiry"></label>
      <label><input type="checkbox" name="perpetual"> 许可明确长期有效</label>`
          : ""
      }
      ${field(platform ? "平台曲库依据" : mode === "no_music" ? "采用依据" : "许可文件编号、地址或原创声明", "proof", "", "text", "required maxlength=3000")}
      <button type="submit" class="button primary">${platform ? "登记待核实附曲" : mode === "no_music" ? "采用无背景音乐" : "保存许可核对"}</button></form>`;
    const noChoices = records("Music").filter(
      (r) => r.data.soundtrack_mode === "no_music",
    );
    const openTimelines = records("Timeline").filter(
      (r) => !r.data.locked && r.data.clips?.length,
    );
    if (mode === "no_music" && noChoices.length && openTimelines.length) {
      host.insertAdjacentHTML(
        "beforeend",
        '<div class="form-stack">' +
          select(
            "无背景音乐决定",
            "no-choice",
            options(noChoices, withVersion),
          ) +
          select(
            "去除配乐的时间线",
            "no-timeline",
            options(openTimelines, withVersion),
          ) +
          button("去除已标注音乐，保留其他音轨", "remove-score") +
          "</div>",
      );
      wire("remove-score", async () => {
        const r = await post("remove-music", {
          music: pinned(find("Music", pick("no-choice").value)),
          timeline: pinned(find("Timeline", pick("no-timeline").value)),
        });
        await ctx.reloadData();
        drawRights();
        return (
          (r.removed_managed_music ? "已去除配乐" : "当前没有已标注的配乐") +
          "，原片内的声音可继续试听"
        );
      });
    }
    pick("mode").onchange = () => {
      mode = pick("mode").value;
      drawRights();
    };
    pick("purpose").onchange = () => {
      use = pick("purpose").value;
    };
    if (pick("stock-audio"))
      pick("stock-audio").onchange = () => {
        licenseAudio = pick("stock-audio").value;
        const a = find("Audio", licenseAudio);
        const player = host.querySelector("audio");
        if (player) player.src = ctx.assetUrl(a);
        pick("stock-name").value = audioName(a);
      };
    async function extract(asset) {
      const result = await ctx.api(
        "/media/" + encodeURIComponent(asset.id) + "/extract-audio",
        {
          method: "POST",
          body: { project_id: pid, expected_revision: asset.version },
        },
      );
      await ctx.reloadData();
      licenseAudio = result.audio.id;
      drawRights();
      return "音轨已就绪";
    }
    wire("extract", () =>
      extract(find("AssetVersion", pick("stock-source").value)),
    );
    const uploader = host.querySelector("[data-sound-file]");
    if (uploader)
      action(uploader, "change", async () => {
        const file = uploader.files[0];
        if (!file) return "";
        const result = await uploadFile(ctx.api, pid, file);
        return extract(result.version);
      });
    action(host.querySelector("form"), "submit", async () => {
      const data = new FormData(host.querySelector("form"));
      const payload = {
        mode,
        name:
          mode === "local_stock"
            ? data.get("stock-name")
            : platform
              ? "平台附曲"
              : "无背景音乐",
        market: data.get("market"),
        use: data.get("purpose"),
        evidence: data.get("proof"),
      };
      if (mode === "local_stock") {
        const a = find("Audio", data.get("stock-audio"));
        if (!a) throw Error("请先导入或提取一条音轨。");
        Object.assign(payload, {
          audio: pinned(a),
          allowed_markets: String(data.get("markets"))
            .split(/[,，]/)
            .map((x) => x.trim())
            .filter(Boolean),
          allowed_uses: [data.get("allowed-use")],
          perpetual: data.has("perpetual"),
          expires_at: data.get("expiry")
            ? new Date(data.get("expiry")).toISOString()
            : null,
        });
      } else if (platform) {
        const a = find("Account", data.get("account"));
        if (!a) throw Error("请选择项目内的平台账号。");
        Object.assign(payload, {
          account: pinned(a),
          sound_id: data.get("sound-id"),
        });
      }
      const result = await post("rights", payload);
      await ctx.reloadData();
      if (mode === "local_stock") {
        tab = "mix";
        draw();
        const track = host.querySelector('[name="role"]');
        if (track) {
          track.value = "music";
          setSource(result.music.id);
        }
      }
      if (mode === "no_music") drawRights();
      return platform
        ? "已保留平台曲目，等待通道核实"
        : mode === "no_music"
          ? "已登记无背景音乐"
          : "音乐与许可已绑定";
    });
  }
  const silenceList = (reading) =>
    reading?.silences?.length
      ? '<div class="form-actions">' +
        reading.silences
          .map(
            (s) =>
              `<button type="button" class="button secondary small" data-seek="${s.start}">静音 ${s.start.toFixed(2)}–${s.end.toFixed(2)} 秒</button>`,
          )
          .join("") +
        "</div>"
      : "";
  const reading = (r) => {
    if (!r) return "";
    const value = (v) => (v == null ? "低于检测范围" : v.toFixed(2));
    const drift = r.stream_clock || {};
    return `<p>综合响度 ${value(r.integrated_lufs)} LUFS · 真峰值 ${value(r.true_peak_dbtp)} dBTP</p>${drift.status === "measured_stream_boundaries" ? `<p>音视频起点差 ${(drift.start_delta_seconds * 1000).toFixed(1)} ms · 终点差 ${(drift.end_delta_seconds * 1000).toFixed(1)} ms</p>` : drift.status === "clock_unavailable" ? "<p>文件未提供完整流时钟</p>" : ""}${silenceList(r)}`;
  };
  function wireSeeks() {
    const player = host.querySelector("[data-sound-player]");
    host.querySelectorAll("[data-seek]").forEach(
      (b) =>
        (b.onclick = () => {
          if (player && player.readyState >= 1) {
            player.currentTime = Number(b.dataset.seek);
            player.focus();
          }
        }),
    );
  }
  function setSource(selected = "") {
    const role = pick("role").value;
    const optionsRows = role === "music" ? music() : records("Audio");
    const parent = host.querySelector("[data-cut-source]");
    parent.innerHTML = select(
      role === "music" ? "许可曲目" : "实际音轨",
      "track-source",
      options(optionsRows, role === "music" ? label : audioName),
      selected,
    );
    const update = () => {
      const r =
        role === "music"
          ? find(
              "Audio",
              find("Music", pick("track-source").value)?.data.audio_ref?.id,
            )
          : find("Audio", pick("track-source").value);
      pick("end").value = r?.data?.probe?.duration || "";
    };
    pick("track-source").onchange = update;
    update();
  }
  function drawMix() {
    const recent = mixes(),
      selected = recent.find((r) => r.id === mixId) || recent[0];
    if (selected) mixId = selected.id;
    const tls = records("Timeline").filter(
      (r) =>
        !r.data.locked && r.data.status !== "locked" && r.data.clips?.length,
    );
    const aud = selected && find("Audio", selected.data.rendered_audio.id);
    host.innerHTML = `<form data-cue class="form-stack">${select("音轨用途", "role", roles)}<div data-cut-source></div>
      <div class="form-grid">${num("源入点（秒）", "begin", 0, "min=0")}${num("源出点（秒）", "end", "", "min=0.000001")}${num("输出位置（秒）", "start", 0, "min=0")}${num("增益（dB）", "gain", 0, "min=-72 max=18")}${num("淡入（秒）", "fade-in", 0, "min=0 max=60")}${num("淡出（秒）", "fade-out", 0, "min=0 max=60")}</div>
      <button type="submit" class="button secondary">加入混音</button></form>
      <div data-cues></div><form data-mixer class="form-stack">
      <div class="form-grid">${field("混音名称", "mix-name", "项目混音", "text", "required maxlength=200")}${select("作品用途", "mix-use", uses, use)}${field("项目市场", "mix-market", market, "text", "required")}</div>
      <label><input name="duck" type="checkbox"> 口播出现时自动压低音乐</label>
      <details><summary>声音处理</summary><div class="form-grid">${num("启动门槛（dB）", "threshold", -32, "min=-48 max=-6")}${num("压缩比", "ratio", 6, "min=1 max=20")}${num("起效（毫秒）", "attack", 15, "min=1 max=200")}${num("释放（毫秒）", "release", 180, "min=20 max=2000")}${field("目标响度（可选 LUFS）", "target", "", "number", "step=0.1 min=-36 max=-8")}</div></details>
      <button type="submit" class="button primary">生成并实测混音</button></form>
      ${
        selected
          ? `<section class="form-stack">${select("试听混音", "mix", options(recent, withVersion), selected.id)}${play(aud)}${reading(selected.data.measurement)}
      ${
        tls.length
          ? select(
              "采用到时间线",
              "timeline",
              options(tls, withVersion),
              timelineId,
            ) +
            select("画面片段的原声", "original", [
              ["keep", "保留"],
              ["mute", "静音"],
            ]) +
            button("采用已试听混音", "adopt", true)
          : "<p>采用镜头提案后，可将这版混音加入时间线。</p>"
      }</section>`
          : ""
      }`;
    function cueList() {
      const list = host.querySelector("[data-cues]");
      list.innerHTML = cuts
        .map(
          (c, i) =>
            `<div class="setting-row"><span>${esc(c.name)} · ${esc(Object.fromEntries(roles)[c.track.role])} · ${c.track.source_in}–${c.track.source_out} 秒 → ${c.track.start} 秒</span><button type="button" class="button secondary small" data-remove="${i}">移除</button></div>`,
        )
        .join("");
      list.querySelectorAll("[data-remove]").forEach(
        (b) =>
          (b.onclick = () => {
            cuts.splice(Number(b.dataset.remove), 1);
            cueList();
          }),
      );
    }
    setSource();
    cueList();
    pick("role").onchange = () => setSource();
    pick("mix-use").onchange = () => {
      use = pick("mix-use").value;
      setSource();
    };
    action(host.querySelector("[data-cue]"), "submit", async () => {
      const data = new FormData(host.querySelector("[data-cue]")),
        role = data.get("role"),
        id = data.get("track-source");
      const m = role === "music" ? find("Music", id) : null,
        a = find("Audio", m?.data?.audio_ref?.id || id);
      if (!a)
        throw Error(
          role === "music"
            ? "先在音乐与许可中核对本次用途。"
            : "请先提取音轨。",
        );
      const track = {
        role,
        audio: pinned(a),
        music: m ? pinned(m) : null,
        source_in: number(data, "begin"),
        source_out: number(data, "end"),
        start: number(data, "start"),
        gain_db: number(data, "gain"),
        fade_in: number(data, "fade-in"),
        fade_out: number(data, "fade-out"),
      };
      if (
        cuts.length >= 16 ||
        track.source_out <= track.source_in ||
        track.source_out > a.data.probe.duration + 0.000001 ||
        track.fade_in + track.fade_out >
          track.source_out - track.source_in + 0.000001
      )
        throw Error("请核对声音区间、淡入出和轨道数量。");
      cuts.push({ name: m ? label(m) : audioName(a), track });
      cueList();
      return "已加入";
    });
    action(host.querySelector("[data-mixer]"), "submit", async () => {
      if (!cuts.length) throw Error("先加入至少一段声音。");
      const d = new FormData(host.querySelector("[data-mixer]"));
      const r = await post("mixes", {
        name: d.get("mix-name"),
        tracks: cuts.map((c) => c.track),
        use: d.get("mix-use"),
        market: d.get("mix-market"),
        ducking: d.has("duck"),
        duck_threshold_db: number(d, "threshold"),
        duck_ratio: number(d, "ratio"),
        attack_ms: number(d, "attack"),
        release_ms: number(d, "release"),
        target_lufs: d.get("target") ? number(d, "target") : null,
      });
      await ctx.reloadData();
      mixId = r.mix.id;
      drawMix();
      return "混音已生成，可试听后采用";
    });
    const ms = pick("mix");
    if (ms)
      ms.onchange = () => {
        mixId = ms.value;
        drawMix();
      };
    wireSeeks();
    wire("adopt", async () => {
      const t = find("Timeline", pick("timeline").value),
        m = find("Mix", mixId);
      const r = await post("adopt", {
        mix: pinned(m),
        timeline: pinned(t),
        original_audio: pick("original").value,
      });
      timelineId = r.timeline.id;
      await ctx.reloadData();
      drawMix();
      return "已采用到 " + label(t) + " · 第 " + r.timeline.version + " 版";
    });
  }
  function drawOutput() {
    const outputs = records("Artifact").filter((a) => a.data.blob_location_id),
      current = outputs.find((a) => a.id === artifactId) || outputs[0];
    if (!current) {
      host.innerHTML = "<p>项目导出成片后，可在这里检测与定位声音。</p>";
      return;
    }
    artifactId = current.id;
    const meter =
      audition?.id === current.id
        ? audition.meter
        : records("Audit").find(
            (a) =>
              a.data.operation === "encoded_audio_meter" &&
              a.data.subject_hash === current.hash,
          );
    const reviews = records("Approval").filter(
      (r) =>
        r.data.operation === "timecoded_audio_listen" &&
        r.data.subject_hash === current.hash,
    );
    host.innerHTML =
      select(
        "已导出成片",
        "artifact",
        options(outputs, withVersion),
        current.id,
      ) +
      play(current, true) +
      button("检测这份成片", "inspect", true) +
      reading(meter?.data.audio_measurement);
    if (meter) {
      host.insertAdjacentHTML(
        "beforeend",
        `<form data-listen class="form-stack">
      <div class="form-grid">${num("试听起点（秒）", "listen-start", 0, "min=0")}${num("试听终点（秒）", "listen-end", meter.data.audio_measurement.duration, "min=0.00001")}</div>
      <div class="form-actions">${button("用当前播放位置作起点", "listen-in")}${button("用当前播放位置作终点", "listen-out")}</div>
      ${select("核对项目", "issue", [
        ["intelligibility", "口播可辨识"],
        ["sync", "音画同步"],
        ["silence", "静音区间"],
        ["peak", "峰值"],
        ["seam", "接缝"],
        ["other", "其他"],
      ])}
      ${field("实际听感", "observation", "", "text", "required maxlength=3000")}
      ${select("试听结论", "decision", [
        ["rework", "定位返工"],
        ["accepted", "此区间可接受"],
      ])}
      <button type="submit" class="button primary">保存区间试听结论</button></form>`,
      );
      for (const [buttonId, inputName] of [
        ["listen-in", "listen-start"],
        ["listen-out", "listen-end"],
      ]) {
        wire(buttonId, async () => {
          pick(inputName).value = host
            .querySelector("video")
            .currentTime.toFixed(3);
          return "";
        });
      }
      action(host.querySelector("[data-listen]"), "submit", async () => {
        await post("listen-review", {
          artifact: pinned(current),
          meter: pinned(meter),
          decision: pick("decision").value,
          findings: [
            {
              start: Number(pick("listen-start").value),
              end: Number(pick("listen-end").value),
              category: pick("issue").value,
              observation: pick("observation").value,
            },
          ],
        });
        await ctx.reloadData();
        drawOutput();
        return "试听结论已绑定当前成片与区间";
      });
    }
    if (reviews.length)
      host.insertAdjacentHTML(
        "beforeend",
        '<div class="form-stack">' +
          reviews
            .flatMap((r) =>
              r.data.findings.map(
                (f) =>
                  `<button type="button" class="button secondary" data-seek="${Number(f.start)}">${Number(f.start).toFixed(2)}–${Number(f.end).toFixed(2)} 秒 · ${r.data.decision === "rework" ? "返工" : "已听审"} · ${esc(f.observation)}</button>`,
              ),
            )
            .join("") +
          "</div>",
      );
    pick("artifact").onchange = () => {
      artifactId = pick("artifact").value;
      drawOutput();
    };
    wireSeeks();
    wire("inspect", async () => {
      const r = await post("inspect-output", { artifact: pinned(current) });
      audition = { id: current.id, meter: r.audit };
      await ctx.reloadData();
      drawOutput();
      return "已读取成片音轨与流时钟";
    });
  }
  function draw() {
    drawTabs();
    if (tab === "rights") drawRights();
    else if (tab === "output") drawOutput();
    else drawMix();
  }
  draw();
  return root;
}
