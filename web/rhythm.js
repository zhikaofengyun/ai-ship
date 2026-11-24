import { esc, field, modal, select, uid } from "./ui.js";
import { uploadFile } from "./upload.js";

const names = { beat: "拍点", downbeat: "强拍", section: "段落" };
const pinned = (r) => ({ id: r.id, version: r.version });

export function openRhythm(ctx, reopen = null) {
  const pid = ctx.requireProject();
  const audioList = ctx.records("Audio");
  const annotated = ctx
    .records("Music")
    .filter((r) => r.data.analysis_method === "manual_marks");
  const media = ctx
    .records("AssetVersion")
    .filter((r) => r.data.probe?.has_audio);
  const mediaName = (r) =>
    r.data.name ||
    ctx.records("Asset").find((a) => a.id === r.data.asset_id)?.data.name ||
    r.data.filename ||
    "音轨";
  const record = annotated.find((r) => r.id === reopen);
  let source = record
    ? audioList.find((a) => a.id === record.data.audio_ref?.id)
    : audioList[0];
  let marks = structuredClone(record?.data.markers || []),
    history = [],
    peaks = [],
    dirty = false,
    clockRequest = 0;
  const key = uid();
  const d = modal(
    "音乐打点",
    '<form data-form class="form-stack">' +
      (annotated.length
        ? select(
            "节奏标记",
            "rhythm-version",
            [
              ["", "新建标记"],
              ...annotated.map((r) => [
                r.id,
                r.data.name + " · 第 " + r.version + " 版",
              ]),
            ],
            reopen || "",
          )
        : "") +
      "<div data-track-select>" +
      select(
        "试听音轨",
        "rhythm-audio",
        audioList.map((a) => [a.id, mediaName(a)]),
        source?.id || "",
      ) +
      "</div>" +
      "<details data-rhythm-sources" +
      (!source ? " open" : "") +
      '><summary>添加音轨</summary><div class="form-stack">' +
      (media.length
        ? select(
            "从项目素材提取",
            "rhythm-media",
            media.map((a) => [a.id, mediaName(a)]),
          ) +
          '<button type="button" data-extract class="button secondary">提取原声</button>'
        : "") +
      '<label class="button secondary">导入音频或视频<input type="file" data-rhythm-upload accept="audio/*,video/*" style="position:absolute;width:1px;height:1px;opacity:0"></label>' +
      '<output data-rhythm-status aria-live="polite"></output></div></details>' +
      field(
        "名称",
        "rhythm-name",
        record?.data.name || "节奏标记",
        "text",
        'required maxlength="200"',
      ) +
      '<div data-rhythm-console><canvas data-waveform width="900" height="68" style="width:100%;height:58px;background:#25262b;border-radius:8px;touch-action:manipulation" aria-label="实际音轨波形与时间标记"></canvas>' +
      '<audio controls preload="metadata" data-rhythm-player aria-label="试听音乐" style="width:100%"></audio>' +
      '<div class="form-grid">' +
      field(
        "位置（秒）",
        "rhythm-time",
        "0.000",
        "number",
        'min="0" step="0.001"',
      ) +
      field("段落名（可选）", "rhythm-label", "", "text", 'maxlength="120"') +
      "</div>" +
      '<div class="form-actions">' +
      Object.entries(names)
        .map(
          ([k, v]) =>
            '<button type="button" class="button secondary" data-mark="' +
            k +
            '">' +
            v +
            "</button>",
        )
        .join("") +
      '<button type="button" class="button secondary" data-undo>撤销</button></div></div>' +
      '<div data-markers style="max-height:145px;overflow:auto"></div>' +
      '<label><input name="use" type="checkbox" ' +
      (record?.data.use_markers === false ? "" : "checked") +
      "> 使用人工卡点</label>" +
      '<div class="form-actions"><button type="submit" class="button secondary">保存标记</button><button type="button" data-use-editor class="button primary">在剪辑室使用</button></div></form>',
    async (form) => {
      if (!source) throw new Error("请先选择一条试听音轨。");
      if (
        form.dataset.go === "editor" &&
        (!marks.length || !named("use").checked)
      )
        throw new Error("请先添加并启用节奏标记。");
      const result = await ctx.api("/production/music/markers", {
        method: "POST",
        body: {
          project_id: pid,
          request_id: key,
          audio: pinned(source),
          music: record ? pinned(record) : null,
          name: named("rhythm-name").value,
          use_markers: named("use").checked,
          markers: marks.map(({ type, time, label }) => ({
            type,
            time,
            label,
          })),
        },
      });
      dirty = false;
      await ctx.reloadData();
      ctx.toast("节奏标记已保存");
      if (form.dataset.go === "editor") {
        sessionStorage.setItem(
          "ship-add-beats",
          JSON.stringify({
            project_id: pid,
            music_id: result.music.id,
            music_version: result.music.version,
            audio_id: source.id,
            audio_version: source.version,
          }),
        );
        ctx.nav("editor");
      } else await ctx.refresh();
    },
  );
  const named = (n) => d.querySelector('[name="' + n + '"]'),
    player = d.querySelector("audio"),
    canvas = d.querySelector("canvas"),
    form = d.querySelector("form[data-form]");
  function paint() {
    const width = canvas.width,
      height = canvas.height,
      g = canvas.getContext("2d");
    g.clearRect(0, 0, width, height);
    if (!source) return;
    const duration = source.data.probe?.duration || 0;
    g.fillStyle = "#aab1c4";
    peaks.forEach((p, i) => {
      const h = Math.max(1, p * height);
      g.fillRect(
        (width * i) / peaks.length,
        (height - h) / 2,
        Math.max(1, width / peaks.length - 1),
        h,
      );
    });
    if (duration <= 0) return;
    for (const m of marks) {
      g.fillStyle = {
        beat: "#88aafc",
        downbeat: "#f4b66e",
        section: "#9cd3a4",
      }[m.type];
      g.fillRect((m.time / duration) * width, 0, 3, height);
    }
    g.fillStyle = "#fff";
    g.fillRect((player.currentTime / duration) * width, 0, 2, height);
  }
  function rows() {
    d.querySelector("[data-markers]").innerHTML = marks
      .map(
        (m, i) =>
          '<div class="setting-row"><button type="button" class="button secondary small" data-seek="' +
          i +
          '">' +
          names[m.type] +
          " · " +
          m.time.toFixed(3) +
          " 秒" +
          (m.label ? " · " + esc(m.label) : "") +
          '</button><button type="button" class="button small" data-remove="' +
          i +
          '" aria-label="移除标记">移除</button></div>',
      )
      .join("");
    d.querySelector("[data-undo]").disabled = !history.length;
    d.querySelectorAll("[data-seek]").forEach(
      (b) => (b.onclick = () => seek(marks[+b.dataset.seek].time)),
    );
    d.querySelectorAll("[data-remove]").forEach(
      (b) =>
        (b.onclick = () => {
          snapshot();
          marks.splice(+b.dataset.remove, 1);
          rows();
        }),
    );
    paint();
  }
  function snapshot() {
    history.push(structuredClone(marks));
    if (history.length > 32) history.shift();
    dirty = true;
  }
  function seek(t) {
    if (Number.isFinite(t) && player.readyState >= 1) {
      player.currentTime = t;
      named("rhythm-time").value = t.toFixed(3);
      paint();
    }
  }
  async function refreshSource() {
    const id = ++clockRequest;
    d.querySelector("[data-rhythm-console]").hidden = !source;
    named("rhythm-audio").disabled = !!record;
    if (source) {
      player.src = ctx.assetUrl(source);
      peaks = [];
      paint();
      try {
        const wave = await ctx.api(
          "/media/" + encodeURIComponent(source.id) + "/waveform?buckets=320",
        );
        if (id === clockRequest) {
          peaks = wave.peaks || [];
          paint();
        }
      } catch (err) {
        if (id === clockRequest) ctx.toast(err.message, "error");
      }
    }
    rows();
  }
  player.ontimeupdate = () => {
    if (document.activeElement !== named("rhythm-time"))
      named("rhythm-time").value = player.currentTime.toFixed(3);
    paint();
  };
  named("rhythm-time").oninput = () => {
    if (player.readyState >= 1 && named("rhythm-time").value !== "")
      player.currentTime = Number(named("rhythm-time").value);
  };
  canvas.onclick = (e) => {
    const bounds = canvas.getBoundingClientRect();
    seek(((e.clientX - bounds.left) / bounds.width) * player.duration);
  };
  named("rhythm-audio").onchange = () => {
    if (marks.length) {
      named("rhythm-audio").value = source?.id || "";
      ctx.toast("保存当前标记后再切换音轨。", "error");
      return;
    }
    source = audioList.find((a) => a.id === named("rhythm-audio").value);
    refreshSource();
  };
  if (named("rhythm-version"))
    named("rhythm-version").onchange = () => {
      if (dirty) {
        named("rhythm-version").value = reopen || "";
        ctx.toast("先保存当前修改。", "error");
        return;
      }
      openRhythm(ctx, named("rhythm-version").value);
    };
  for (const b of d.querySelectorAll("[data-mark]"))
    b.onclick = () => {
      if (!source) return;
      const raw = player.paused
        ? named("rhythm-time").value
        : player.currentTime;
      const time = Math.round(Number(raw) * 1000) / 1000,
        type = b.dataset.mark;
      if (
        raw === "" ||
        !Number.isFinite(time) ||
        time < 0 ||
        time >= source.data.probe.duration
      ) {
        ctx.toast("请选择音轨时长内的位置。", "error");
        return;
      }
      if (marks.some((m) => m.type === type && m.time === time)) {
        ctx.toast("此处已有同类标记。", "error");
        return;
      }
      snapshot();
      marks.push({
        type,
        time,
        label: type === "section" ? named("rhythm-label").value.trim() : "",
      });
      marks.sort((a, b) => a.time - b.time);
      rows();
    };
  d.querySelector("[data-undo]").onclick = () => {
    if (history.length) {
      marks = history.pop();
      dirty = true;
      rows();
    }
  };
  form.querySelector("[data-use-editor]").onclick = () => {
    form.dataset.go = "editor";
    form.requestSubmit();
  };
  form.querySelector("[type=submit]").onclick = () => (form.dataset.go = "");
  async function extract(asset) {
    const reply = await ctx.api(
      "/media/" + encodeURIComponent(asset.id) + "/extract-audio",
      {
        method: "POST",
        body: { project_id: pid, expected_revision: asset.version },
      },
    );
    await ctx.reloadData();
    source = reply.audio;
    audioList.push(source);
    named("rhythm-audio").add(new Option(mediaName(asset), source.id));
    named("rhythm-audio").value = source.id;
    d.querySelector("[data-rhythm-sources]").open = false;
    await refreshSource();
  }
  let extracting = false;
  async function add(work) {
    if (record || marks.length) {
      ctx.toast("保存当前标记并新建一份标记后再换音轨。", "error");
      return;
    }
    if (extracting) return;
    extracting = true;
    const out = d.querySelector("[data-rhythm-status]");
    out.textContent = "正在准备音轨…";
    try {
      await work();
      out.textContent = "已添加";
    } catch (e) {
      out.textContent = e.message;
    } finally {
      extracting = false;
    }
  }
  if (d.querySelector("[data-extract]"))
    d.querySelector("[data-extract]").onclick = () =>
      add(() =>
        extract(media.find((m) => m.id === named("rhythm-media").value)),
      );
  d.querySelector("[data-rhythm-upload]").onchange = (e) =>
    add(async () => {
      const file = e.target.files[0];
      if (!file) return;
      const uploaded = await uploadFile(ctx.api, pid, file);
      await extract(uploaded.version);
    });
  named("rhythm-name").oninput = () => (dirty = true);
  named("use").onchange = () => (dirty = true);
  refreshSource();
  return d;
}
