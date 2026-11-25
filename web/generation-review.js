import { $, area, esc, field, modal, select, uid } from "./ui.js";

export const reviewLabels = {
  accept: "通过",
  needs_revision: "退回修改",
  reject: "拒绝",
};

const seconds = (n) =>
  Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
const sha256 = async (blob) => {
  const bytes = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(bytes)]
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
};

export async function openGenerationReview(ctx, pid, jobId, onSaved) {
  const path =
    "/production/generation/" + encodeURIComponent(jobId) + "/visual-review";
  const projectQuery = new URLSearchParams({ project_id: pid });
  const d = modal(
    "参考与生成画面对照",
    '<div data-review-content></div><p data-review-notice role="status">正在读取候选…</p>',
    null,
    true,
  );
  const token = d.dataset.modalId,
    active = () =>
      d.open && d.dataset.modalId === token && ctx.state.projectId === pid;
  const host = $("[data-review-content]", d),
    notice = $("[data-review-notice]", d);
  const urls = new Map();
  let info,
    input,
    saving = false,
    movieLoading = false,
    pending;
  const frames = {
    reference: { ticket: 0 },
    candidate: { ticket: 0 },
  };
  const life = new AbortController();
  function dropUrls() {
    for (const url of urls.values()) URL.revokeObjectURL(url);
    urls.clear();
  }
  const cleanup = () => {
    life.abort();
    for (const f of Object.values(frames)) {
      clearTimeout(f.timer);
      f.controller?.abort();
    }
    dropUrls();
    observer.disconnect();
    d.removeEventListener("close", cleanup);
  };
  const observer = new MutationObserver(() => {
    if (!active()) cleanup();
  });
  observer.observe(d, {
    attributes: true,
    attributeFilter: ["data-modal-id", "open"],
  });
  d.addEventListener("close", cleanup, { once: true });
  function status(error) {
    if (active()) notice.textContent = error.message || String(error);
  }
  async function picture(name, options, signal, isCurrent) {
    const res = await ctx.api(
      path + "/" + name + ".png?" + options,
      { signal, cache: "no-store" },
      true,
    );
    const blob = await res.blob();
    const hash = await sha256(blob);
    if (hash !== res.headers.get("X-Frame-SHA256"))
      throw new Error("画面摘要校验失败，请重新读取。");
    if (
      name === "reference" &&
      info.reference.kind !== "video_segment" &&
      hash !== info.reference.png_sha256
    )
      throw new Error("参考帧版本发生变化，请重新核对。");
    if (!active() || !isCurrent()) return null;
    const image = $("[data-image=" + name + "]", host);
    const before = urls.get(name);
    const url = URL.createObjectURL(blob);
    urls.set(name, url);
    image.src = url;
    if (before) URL.revokeObjectURL(before);
    await image.decode();
    if (!active() || !isCurrent()) return null;
    const value = res.headers.get("X-Frame-PTS");
    const pts = value == null || value === "" ? null : Number(value);
    if (
      (name === "reference"
        ? info.reference.kind === "video_segment"
        : !info.candidate.is_still) &&
      !Number.isFinite(pts)
    ) {
      throw new Error("服务未返回画面时码，请重新读取。");
    }
    const sourcePtsHeader = res.headers.get("X-Source-Frame-PTS");
    const sourcePts = sourcePtsHeader == null ? null : Number(sourcePtsHeader);
    return { hash, pts, sourcePts };
  }
  function updateSave() {
    const submit = $("[data-review-submit]", host);
    if (submit)
      submit.disabled =
        saving ||
        !frames.reference.selected ||
        !frames.candidate.selected ||
        !input.decision.value ||
        !input.reason.value.trim();
  }
  function caption(name, text) {
    $("[data-caption=" + name + "]", host).textContent = text;
  }
  function writeLast() {
    const last = info.last_decision;
    const origin = $("[data-last-frame]", host);
    if (origin) origin.hidden = !last;
    const sourceTime =
      info.reference.kind === "video_segment" && last
        ? "参考 " + seconds(last.reference_time_seconds ?? 0) + " 秒 · "
        : "";
    $("[data-last-decision]", host).textContent = last
      ? `上次审片 · ${reviewLabels[last.decision]} · ${sourceTime}${info.candidate.is_still ? "静帧" : "候选 " + seconds(last.time_seconds) + " 秒"} · ${last.reason}`
      : "此候选尚无人工审片记录。";
  }
  function scrubFields(name, label, duration, time) {
    const last = Math.max(0, Math.ceil(duration * 1000) - 1) / 1000;
    return `<div class="review-scrub">
      <label>浏览${label}画面<input type="range" aria-label="浏览${label}画面" data-${name}-slider min="0" max="${esc(last)}" step="0.001"></label>
      ${field(label + "时码（秒）", name + "_time", time, "number", 'min="0" step="any" required')}
      <small>全长 ${esc(seconds(duration))} 秒</small></div>`;
  }
  function chooseTime(name, value, immediate = false) {
    const f = frames[name];
    clearTimeout(f.timer);
    f.controller?.abort();
    const ticket = ++f.ticket;
    f.selected = null;
    updateSave();
    $("[data-image=" + name + "]", host).removeAttribute("src");
    const time = Number(value);
    if (
      value === "" ||
      !Number.isFinite(time) ||
      time < 0 ||
      (f.slider ? time >= info[name].duration_seconds : time !== 0)
    ) {
      caption(name, "请选择片段内的时码。");
      return;
    }
    if (f.slider) f.slider.value = time;
    caption(name, "正在读取画面…");
    const show = async () => {
      f.controller = new AbortController();
      try {
        const params = new URLSearchParams(projectQuery);
        if (name === "candidate" || f.slider) params.set("time_seconds", time);
        const result = await picture(
          name,
          params,
          f.controller.signal,
          () => ticket === f.ticket,
        );
        if (!result) return;
        f.selected = { ...result, time };
        if (name === "candidate") {
          caption(
            name,
            info.candidate.is_still
              ? "生成静帧"
              : `实际帧时码 ${seconds(result.pts)} 秒`,
          );
        } else if (f.slider) {
          caption(
            name,
            `输入片段 ${seconds(result.pts)} 秒` +
              (Number.isFinite(result.sourcePts)
                ? ` · 原片 ${seconds(result.sourcePts)} 秒`
                : ""),
          );
        } else {
          caption(
            name,
            info.reference.frame_pts_seconds == null
              ? "参考静帧"
              : `源帧时码 ${seconds(info.reference.frame_pts_seconds)} 秒`,
          );
        }
      } catch (err) {
        if (active() && ticket === f.ticket && err.name !== "AbortError") {
          caption(name, "画面读取失败");
          status(err);
        }
      } finally {
        if (active() && ticket === f.ticket) updateSave();
      }
    };
    if (immediate) show();
    else f.timer = setTimeout(show, 140);
  }
  async function loadMovie() {
    const player = $("[data-reference-player]", host);
    if (movieLoading || urls.has("movie") || !player.open) return;
    movieLoading = true;
    const message = $("[data-movie-status]", player);
    message.textContent = "正在校验参考片段…";
    try {
      const response = await ctx.api(
        path + "/reference.mp4?" + projectQuery,
        { signal: life.signal },
        true,
      );
      const blob = await response.blob();
      if ((await sha256(blob)) !== info.reference.segment_sha256)
        throw new Error("输入片段内容已变化，请重新读取。");
      if (!active()) return;
      const url = URL.createObjectURL(blob);
      urls.set("movie", url);
      const video = $("video", player);
      video.onerror = () => {
        message.textContent =
          "此浏览器无法播放输入片段的编码。可调整参考时码，查看模型收到的逐帧画面。";
      };
      video.src = url;
      message.textContent = "";
    } catch (err) {
      if (active()) message.textContent = err.message;
    } finally {
      movieLoading = false;
    }
  }
  async function prepare() {
    try {
      const result = await ctx.api(path + "?" + projectQuery, {
        signal: life.signal,
      });
      if (!active()) return;
      info = result;
      const still = info.candidate.is_still;
      const segment = info.reference.kind === "video_segment";
      const canReview = ["owner", "reviewer"].includes(
        ctx.state.health?.actor?.role,
      );
      const latest = info.last_decision?.time_seconds ?? 0;
      const sourceTime = info.last_decision?.reference_time_seconds ?? 0;
      host.innerHTML = `<div class="generation-compare">
        <figure><h3>参考画面</h3><div><img data-image="reference" alt="${segment ? "输入片段中所选的参考帧" : "生成模型当时收到的参考帧"}"></div><figcaption data-caption="reference"></figcaption></figure>
        <figure><h3>生成候选</h3><div><img data-image="candidate" alt="所选候选时码的实际画面"></div><figcaption data-caption="candidate"></figcaption></figure>
        </div>
        ${
          segment
            ? `<details data-reference-player class="review-movie"><summary>播放模型收到的完整参考片段</summary><video controls playsinline preload="metadata" aria-label="模型收到的完整参考片段"></video><small data-movie-status role="status"></small></details>
          ${scrubFields("reference", "参考", info.reference.duration_seconds, sourceTime)}`
            : ""
        }
        ${still ? "" : scrubFields("candidate", "候选", info.candidate.duration_seconds, latest)}
        <div class="review-last"><p data-last-decision></p>${still && !segment ? "" : '<button type="button" data-last-frame class="button small ghost" hidden>回到上次审片画面</button>'}</div>
        ${
          canReview
            ? `<form class="form-stack" data-review-form>${select("审片结论", "decision", [["", "选择结论"], ...Object.entries(reviewLabels)], "", "required")}
          ${area("判断依据", "reason", "", "required maxlength=3000")}
          <div class="dialog-actions"><button type="submit" class="button primary" data-review-submit disabled>保存审片结果</button></div></form>`
            : ""
        }`;
      input = {
        decision: $("[name=decision]", host),
        reason: $("[name=reason]", host),
      };
      writeLast();
      for (const name of Object.keys(frames)) {
        const f = frames[name];
        f.slider = $("[data-" + name + "-slider]", host);
        f.time = $("[name=" + name + "_time]", host);
        if (f.slider) {
          f.slider.oninput = () => {
            f.time.value = f.slider.value;
            chooseTime(name, f.time.value);
          };
          f.time.oninput = () => chooseTime(name, f.time.value);
        }
      }
      if (segment) $("[data-reference-player]", host).ontoggle = loadMovie;
      const lastFrame = $("[data-last-frame]", host);
      if (lastFrame)
        lastFrame.onclick = () => {
          if (!info.last_decision || saving) return;
          for (const name of Object.keys(frames)) {
            const f = frames[name];
            if (!f.time) continue;
            f.time.value =
              name === "candidate"
                ? info.last_decision.time_seconds
                : (info.last_decision.reference_time_seconds ?? 0);
            chooseTime(name, f.time.value, true);
          }
        };
      if (canReview) {
        input.decision.onchange = input.reason.oninput = updateSave;
        $("[data-review-form]", host).onsubmit = submit;
      }
      notice.textContent = "";
      chooseTime("candidate", still ? 0 : latest, true);
      chooseTime("reference", segment ? sourceTime : 0, true);
    } catch (err) {
      status(err);
    }
  }
  async function submit(event) {
    event.preventDefault();
    const reference = frames.reference.selected,
      candidate = frames.candidate.selected;
    if (
      saving ||
      !reference ||
      !candidate ||
      !input.decision.value ||
      !input.reason.value.trim()
    )
      return;
    const body = {
      project_id: pid,
      expected_job_version: info.job.version,
      expected_review_version: info.review_version,
      time_seconds: candidate.time,
      ...(info.reference.kind === "video_segment" && {
        reference_time_seconds: reference.time,
      }),
      reference_sha256: reference.hash,
      candidate_frame_sha256: candidate.hash,
      decision: input.decision.value,
      reason: input.reason.value.trim(),
    };
    const signature = JSON.stringify(body);
    if (!pending || signature !== pending.signature)
      pending = { signature, id: uid() };
    saving = true;
    for (const control of host.querySelectorAll("input,select,textarea"))
      control.disabled = true;
    updateSave();
    try {
      const saved = await ctx.api(path, {
        method: "POST",
        body: { ...body, request_id: pending.id },
      });
      if (!active()) return;
      pending = null;
      info.review_version = saved.review_version;
      info.last_decision = {
        decision: body.decision,
        reason: body.reason,
        time_seconds: body.time_seconds,
        reference_time_seconds: body.reference_time_seconds,
      };
      writeLast();
      onSaved?.({ version: saved.review_version, ...info.last_decision });
      input.decision.value = input.reason.value = "";
      notice.textContent = "审片结果已保存。";
    } catch (err) {
      status(err);
      if (active() && err.status === 409) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "button small secondary";
        retry.textContent = "重新读取审片记录";
        retry.onclick = () => openGenerationReview(ctx, pid, jobId, onSaved);
        notice.append(" ", retry);
      }
    } finally {
      if (active()) {
        saving = false;
        for (const control of host.querySelectorAll("input,select,textarea"))
          control.disabled = false;
        updateSave();
      }
    }
  }
  await prepare();
}
