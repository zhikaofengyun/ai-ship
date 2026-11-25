import { esc, field, select, modal } from "./ui.js";
import { mediaDuration, mediaName } from "./media.js";

export function referenceSource(mode) {
  return select(
    "参考来源",
    "reference_mode",
    [
      ["image", "项目图片"],
      ["video", "从实拍视频取帧"],
    ],
    mode || "image",
  );
}
export function framePreview(project, asset, time) {
  if (!asset) return "";
  const duration = mediaDuration(asset);
  const query = new URLSearchParams({
    project_id: project,
    asset_id: asset.id,
    version: asset.version,
    time_seconds: time,
  });
  const valid =
    time !== "" &&
    Number.isFinite(Number(time)) &&
    Number(time) >= 0 &&
    Number(time) < duration;
  return valid
    ? `<figure><img class="generation-still" alt="选定源时码的实际参考帧" src="/api/v1/production/generation/reference/frame?${esc(query.toString())}"><figcaption>源时码 ${esc(time)} 秒 · 全长 ${esc(duration)} 秒</figcaption></figure>`
    : "";
}

export function frameFields(project, asset, time = 0) {
  if (!asset) return "";
  const duration = mediaDuration(asset);
  return `${field("源视频时码（秒）", "reference_time_seconds", time, "number", 'required min="0" step="any" max="' + esc(Math.max(0, duration - 0.000001)) + '"')}<div data-reference-preview>${framePreview(project, asset, time)}</div>`;
}

export function segmentEnd(asset, profile, start = 0) {
  const fps = profile.reference_fps;
  const frames = Math.min(
    4,
    profile.max_reference_frames,
    Math.floor((mediaDuration(asset) - Number(start)) * fps),
  );
  return Number(start) + Math.max(0, frames) / fps;
}

export function segmentFields(asset, profile, start = 0, end) {
  if (!asset) return "";
  const finish = end ?? segmentEnd(asset, profile, start);
  const constraints =
    'required min="0" step="any" max="' + esc(mediaDuration(asset)) + '"';
  return `<div class="generation-reference-segment">${field("连续参考入点（秒）", "reference_time_seconds", start, "number", constraints)}${field("连续参考出点（秒）", "reference_end_seconds", finish, "number", constraints)}<small>按 ${esc(profile.reference_fps)} fps 取样，区间须包含 2 至 ${esc(profile.max_reference_frames)} 帧。</small><video data-segment-source controls playsinline preload="metadata" class="generation-still" aria-label="实拍参考区间" src="/api/v1/media/${encodeURIComponent(asset.id)}/file#t=${esc(start)},${esc(finish)}"></video></div>`;
}

export function updateSegmentPreview(container) {
  const player = container.querySelector("[data-segment-source]");
  if (!player) return;
  const start = Number(
    container.querySelector("[name=reference_time_seconds]").value,
  );
  const end = Number(
    container.querySelector("[name=reference_end_seconds]").value,
  );
  if (Number.isFinite(start) && end > start)
    player.src = player.src.split("#")[0] + "#t=" + start + "," + end;
}

function referencePage(ctx, project, mode, query = "", after = "") {
  return ctx.api(
    "/production/generation/reference/assets?" +
      new URLSearchParams({
        project_id: project,
        media_type: mode || "image",
        q: query,
        after,
        limit: 50,
      }),
  );
}

export async function recentReferences(ctx, project) {
  const ids = new Set(ctx.state.records.map((r) => r.id));
  const pages = await Promise.all(
    ["image", "video"].map((mode) => referencePage(ctx, project, mode)),
  );
  return pages.flatMap((page) => page.items.filter((r) => ids.has(r.id)));
}

export async function pinnedReference(ctx, project, mode, id, version) {
  const page = await referencePage(ctx, project, mode, id);
  return page.items.find((r) => r.id === id && r.version === version);
}

export async function chooseReference(ctx, project, mode, apply) {
  const box = modal(
    "检索全部参考素材",
    `<form data-reference-search class="form-stack">${field("名称或素材编号", "query", "", "search", 'maxlength="300" autocomplete="off"')}<button class="button primary" type="submit">查找</button></form><p role="status" data-reference-status></p><ul class="batch-segments" data-reference-results></ul><div class="form-actions"><button class="button secondary" type="button" data-reference-prev hidden>上一页</button><button class="button secondary" type="button" data-reference-next hidden>下一页</button></div>`,
  );
  const id = box.dataset.modalId,
    active = () =>
      box.open && box.dataset.modalId === id && ctx.state.projectId === project;
  const form = box.querySelector("[data-reference-search]"),
    list = box.querySelector("[data-reference-results]"),
    message = box.querySelector("[data-reference-status]"),
    prev = box.querySelector("[data-reference-prev]"),
    next = box.querySelector("[data-reference-next]");
  let page = 0,
    pages = [""],
    query = "",
    newer = 0;
  async function read() {
    const request = ++newer;
    list.replaceChildren();
    message.textContent = "正在读取项目原件…";
    next.hidden = true;
    prev.hidden = page === 0;
    try {
      const result = await referencePage(
        ctx,
        project,
        mode,
        query,
        pages[page],
      );
      if (!active() || request !== newer) return;
      message.textContent = result.items.length
        ? `第 ${page + 1} 页 · ${result.items.length} 件`
        : result.next_after
          ? "本页没有可用原件，可继续下一页。"
          : "没有匹配的可用项目原件。";
      list.innerHTML = result.items
        .map(
          (r, i) =>
            `<li><span>${esc(mediaName(r))} · v${esc(r.version)}</span><button class="button small secondary" type="button" data-pick="${i}">选用</button></li>`,
        )
        .join("");
      list.querySelectorAll("[data-pick]").forEach(
        (b) =>
          (b.onclick = () => {
            if (active()) {
              apply(result.items[Number(b.dataset.pick)]);
              box.close();
            }
          }),
      );
      next.hidden = !result.next_after;
      if (result.next_after) pages[page + 1] = result.next_after;
    } catch (e) {
      if (active() && request === newer) message.textContent = e.message;
    }
  }
  form.onsubmit = (e) => {
    e.preventDefault();
    query = form.elements.query.value.trim();
    page = 0;
    pages = [""];
    read();
  };
  prev.onclick = () => {
    if (page > 0) {
      page--;
      read();
    }
  };
  next.onclick = () => {
    if (pages[page + 1]) {
      page++;
      read();
    }
  };
  await read();
}
