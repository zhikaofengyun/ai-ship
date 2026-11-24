import { btn, esc, icon, iconBtn, time } from "./ui.js";
import { layoutMenu } from "./editor-layout.js";
import { viewerTools } from "./editor-viewer.js";
import { mediaDuration, mediaName, mediaType, mediaVariant } from "./media.js";

export function frameTime(value, fps) {
  const actual =
    typeof fps === "object" ? fps.num / fps.den : Number(fps) || 30;
  const nominal = Math.max(1, Math.round(actual)),
    frames = Math.max(0, Math.floor(value * actual + 1e-6));
  return [
    Math.floor(frames / nominal / 60),
    Math.floor(frames / nominal) % 60,
    frames % nominal,
  ]
    .map((n) => String(n).padStart(2, "0"))
    .join(":");
}
const typeLabels = { video: "视频", audio: "音频", image: "图片" };
export function libraryItems(assets, state, assetUrl) {
  const query = (state.query || "").trim().toLocaleLowerCase();
  const list = assets.filter(
    (asset) =>
      (!state.filter ||
        state.filter === "all" ||
        mediaType(asset) === state.filter) &&
      (!query || mediaName(asset).toLocaleLowerCase().includes(query)),
  );
  if (!list.length)
    return `<div class="room-bin-empty">${icon(assets.length ? "search" : "folder")}<span>${assets.length ? "没有匹配的素材" : "暂无素材"}</span></div>`;
  return list
    .map((a) => {
      const name = esc(mediaName(a)),
        id = esc(a.id),
        kind = mediaType(a),
        url = esc(assetUrl(a));
      const thumb =
        kind === "image"
          ? `<img src="${url}" alt="" loading="lazy">`
          : kind === "audio"
            ? icon("music")
            : `<video src="${url}#t=0.01" muted preload="metadata" playsinline aria-hidden="true"></video>`;
      return `<div class="room-bin-row ${a.id === state.sourceId ? "is-selected" : ""}" draggable="true" data-media-drag="${id}">
      <button type="button" data-action="audition" data-id="${id}" class="room-bin-source" aria-label="预览 ${name}" aria-pressed="${a.id === state.sourceId}">
        <span class="room-thumb">${thumb}</span><span class="room-media-copy"><strong>${name}</strong><small>${esc(typeLabels[kind] || "媒体")} · ${mediaDuration(a) ? time(mediaDuration(a)) : "静帧"}${a.kind === "Asset" ? "" : " · " + esc(mediaVariant(a))}</small></span>
      </button>${iconBtn("加入时间线 · " + mediaName(a), "add-asset", "plus", `data-id="${id}" ${state.data.locked ? "disabled" : ""}`)}
    </div>`;
    })
    .join("");
}
export function editorMarkup(
  e,
  { assets, assetUrl, total, source, project, artifact },
) {
  const fmt = e.data.format,
    fps =
      typeof fmt.fps === "object"
        ? +(fmt.fps.num / fmt.fps.den).toFixed(2)
        : fmt.fps;
  const emptyViewer =
    !e.data.clips.length && !source && !e.data.captions.length;
  const saveStatus = e.exporting
    ? "正在导出…"
    : e.data.locked
      ? "已锁版"
      : e.dirty
        ? "未保存"
        : e.record
          ? "已保存"
          : "草稿";
  return `<article class="editor-room" data-mobile-panel="${esc(e.panel || "media")}" data-bin-hidden="${!!e.hideBin}" data-inspector-hidden="${!!e.hideInspector}">
    <div class="room-head">
      <div class="room-title"><span class="room-mark">${icon("film")}</span><div><h1>${esc(e.data.name)}</h1><span id="edit-status" class="edit-status" role="status">${saveStatus}</span></div></div>
      <div class="room-head-actions">
        ${layoutMenu()}
        ${iconBtn("剪辑工程", "timeline-list", "folder")}${btn("保存工程", "save-timeline", "check", "button secondary", e.busy || !project ? "disabled" : "")}
        ${btn("导出成片", "render-timeline", "download", "button primary", e.busy || !e.data.clips.length ? "disabled" : "")}
      </div>
    </div>
    <div class="room-body">
      <div class="room-upper">
        <aside data-room-pane="bin" id="room-bin" class="room-bin" aria-label="项目素材"><header class="room-panel-head"><h2 data-dock-handle title="拖动素材标题交换位置">素材</h2><span class="room-count">${assets.length}</span>
          ${iconBtn("导入素材", "import-assets", "upload")}</header>
          <div class="room-bin-filters"><label class="room-search">${icon("search")}<input aria-label="搜索素材" type="search" placeholder="搜索素材" id="room-query" value="${esc(e.query || "")}"></label>
          <select id="room-filter" aria-label="素材类型">${[
            ["all", "全部"],
            ["video", "视频"],
            ["audio", "音频"],
            ["image", "图片"],
          ]
            .map(
              ([v, l]) =>
                `<option value="${v}" ${(e.filter || "all") === v ? "selected" : ""}>${l}</option>`,
            )
            .join("")}</select></div>
          <div id="room-bin-list" class="room-bin-list">${libraryItems(assets, e, assetUrl)}</div>
          <div class="room-import-status" id="room-import-status" aria-live="polite"></div>
        </aside>
        <section data-room-pane="monitor" id="room-monitor" class="room-monitor" aria-label="预览窗口">
          <div class="room-monitor-head"><button data-action="bin-toggle" class="room-pane-switch" type="button" aria-label="显示或隐藏素材" title="素材">${icon("folder")}</button>
            <span data-dock-handle title="拖动预览标题交换位置">${source ? esc(mediaName(source)) : "时间线预览"}</span>
            ${viewerTools()}<button data-action="inspector-toggle" class="room-pane-switch" type="button" aria-label="显示或隐藏检查器" title="检查器">${icon("sliders")}</button></div>
          <div class="room-screen" id="viewer-screen" tabindex="0" role="group" aria-label="预览画面，空格播放或暂停，F 全屏" data-empty="${emptyViewer}"><div id="preview-stage"></div>
            ${emptyViewer ? `<div class="room-screen-empty">${icon("film")}<button type="button" data-action="import-assets">${project ? "导入素材" : "新建项目"}</button></div>` : ""}
          </div>
          ${source ? `<div class="room-source-actions">${mediaType(source) === "video" ? '<button type="button" class="room-source-play" data-source-play aria-label="播放原片">▶</button><input type="range" data-source-scrub aria-label="原片播放位置" min="0" max="1" step="0.001" value="0">' : ""}${btn("返回时间线", "timeline-monitor", "film", "button small secondary")}${btn("加入时间线", "add-asset", "plus", "button small secondary", `data-id="${esc(source.id)}"`)}</div>` : ""}
          <div class="room-transport" ${source ? "hidden" : ""}>
            ${iconBtn("上一帧", "frame-prev", "undo")}${iconBtn("播放或暂停（空格）", "play", "play")}${iconBtn("下一帧", "frame-next", "redo")}
            <span id="playhead-label" class="room-tc">${frameTime(e.playhead, fmt.fps)}</span>
            <input type="range" id="playhead" aria-label="预览时间" min="0" max="${Math.max(total, 1)}" step="0.001" value="${e.playhead}">
            <span class="room-total">${frameTime(total, fmt.fps)}</span>${iconBtn("安全区", "safe", "grid")}
          </div>
        </section>
        <aside data-room-pane="inspector" class="room-inspector inspector" id="clip-inspector" aria-label="片段参数"></aside>
      </div>
      <nav class="room-mobile-tabs" aria-label="剪辑面板">${[
        ["media", "素材", "folder"],
        ["properties", "检查器", "sliders"],
        ["timeline", "时间线", "film"],
      ]
        .map(
          ([v, l, ic]) =>
            `<button type="button" data-action="room-panel" data-panel="${v}" aria-pressed="${(e.panel || "media") === v}">${icon(ic)}${l}</button>`,
        )
        .join("")}</nav>
      <section class="room-timeline" aria-label="时间线">
        <div class="room-timeline-head"><h2>时间线</h2><small>${fmt.width} × ${fmt.height} · ${esc(fps)} fps</small>
          <div class="room-timeline-tools">
            ${iconBtn("撤销（⌘ / Ctrl Z）", "undo", "undo")}${iconBtn("重做", "redo", "redo")}<i></i>
            ${iconBtn("在播放头分割（S）", "split", "scissors")}${iconBtn("复制片段", "duplicate", "copy")}${iconBtn("删除片段", "delete", "trash")}<i></i>
            ${btn("字幕", "caption", "edit", "button small secondary", e.data.locked ? "disabled" : "")}
            ${iconBtn("导入字幕文件", "import-captions", "upload", e.data.locked ? "disabled" : "")}
            ${iconBtn("波纹联动", "ripple", "link", `aria-pressed="${e.ripple}"`)}${iconBtn("吸附", "snap", "grid", `aria-pressed="${e.snap}"`)}
            ${iconBtn("锁定或解锁时间线", "lock-timeline", e.data.locked ? "lock" : "unlock")}
            ${iconBtn("缩小时间线", "zoom-out", "search")}${iconBtn("放大时间线", "zoom-in", "plus")}${btn("适配", "zoom-fit", "", "button small secondary")}
          </div>
        </div>
        <div class="room-track-scroll" id="timeline-scroll"><div class="timeline-ruler" id="timeline-ruler"></div><div id="timeline-tracks"></div></div>
        <footer class="room-timeline-foot"><span>${e.data.clips.length} 个画面 · ${e.data.audio_tracks.length} 段音频 · ${e.data.captions.length} 条字幕</span>
          <div>${artifact ? btn("最近导出", "last-export", "download", "button small ghost") : ""}${btn("画幅与帧率", "format", "sliders", "button small ghost")}</div></footer>
      </section>
    </div>
    <input id="editor-files" type="file" accept="video/*,audio/*,image/*" multiple hidden>
    <input id="editor-captions" type="file" accept=".srt,.vtt,text/vtt,application/x-subrip" hidden>
  </article>`;
}
