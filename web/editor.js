import {
  checkTransitions,
  editTransition,
  showTransition,
  transitionForm,
} from "./clip-transitions.js";
import {
  $,
  $$,
  esc,
  uid,
  arr,
  time,
  icon,
  btn,
  iconBtn,
  badge,
  empty,
  field,
  area,
  select,
  formData,
  toast,
  modal,
  download,
  setBusy,
  confirmAction,
  parseJSON,
} from "./ui.js";
import {
  mediaLibrary,
  mediaType,
  mediaName,
  mediaVariant,
  mediaDuration,
  mediaBinding,
  selectedMedia,
} from "./media.js";

import { loadView, storeView } from "./workspace-layout.js";
import { installEditorLayout } from "./editor-layout.js";
import { installViewer } from "./editor-viewer.js";
import { uploadFile } from "./upload.js";
import { revisionConflict, showVersions } from "./editor-versions.js";
import { filmstrip } from "./editor-filmstrip.js";
import { editorMarkup, libraryItems, frameTime } from "./editor-room.js";
import { openTimelineExchange } from "./editor-exchange.js";

const drafts = new Map();
const clone = (v) => JSON.parse(JSON.stringify(v));
const length = (c) =>
  Math.max(
    0,
    (Number(c.source_out) - Number(c.source_in)) / Number(c.speed || 1),
  );
const duration = (d) =>
  Math.max(
    0,
    ...arr(d.clips).map((c) => Number(c.timeline_start || 0) + length(c)),
    ...arr(d.audio_tracks).map(
      (c) => Number(c.timeline_start || 0) + length(c),
    ),
    ...arr(d.captions).map((c) => Number(c.end || 0)),
  );
const initial = (project) => ({
  name: project?.data?.name || "未命名剪辑",
  locked: false,
  clips: [],
  captions: [],
  audio_tracks: [],
  format: {
    width: project?.data?.width || 1080,
    height: project?.data?.height || 1920,
    fps: 30,
  },
});
const evaluate = (keys, t, fallback) => {
  if (!keys?.length) return fallback;
  const list = [...keys].sort((a, b) => a.time - b.time);
  if (t <= list[0].time) return list[0].value;
  for (let i = 1; i < list.length; i++)
    if (t <= list[i].time) {
      const a = list[i - 1],
        b = list[i];
      return a.value + ((b.value - a.value) * (t - a.time)) / (b.time - a.time);
    }
  return list.at(-1).value;
};
const rangeHTML = (label, key, value, min, max, step, unit = "") =>
  `<div class="range-field"><div class="range-label"><label for="clip-${key}">${label}</label><output id="output-${key}">${Number(value).toFixed(step < 0.1 ? 2 : step < 1 ? 1 : 0)}${unit}</output></div><input id="clip-${key}" data-property="${key}" type="range" value="${esc(value)}" min="${esc(min)}" max="${esc(max)}" step="${esc(step)}" aria-label="${label}"></div>`;

export async function mountEditor(container, ctx) {
  const pid = ctx.state.projectId || "none";
  const actor = ctx.state.health?.actor;
  const requestedId = ctx.editorTimelineId;
  const pageGeneration = ctx.state.routeGeneration;
  const currentPage = () =>
    pageGeneration === ctx.state.routeGeneration &&
    (ctx.state.projectId || "none") === pid;
  let requested =
    requestedId && ctx.records("Timeline").find((r) => r.id === requestedId);
  if (requestedId && !requested) {
    requested = await ctx.api("/records/" + encodeURIComponent(requestedId));
    if (!currentPage()) return;
    if (requested.kind !== "Timeline" || requested.project_id !== pid)
      throw new Error("此剪辑工程不在当前项目。");
  }
  const localDraft =
    ctx.editorIntent === "local-draft" ? location.hash.split("/")[2] : null;
  const baseDraftKey = [
    "ship-editor-draft",
    actor?.tenant_id || "local",
    actor?.actor_id || "owner",
    pid,
  ].join(":");
  const draftKey = requested
    ? baseDraftKey + ":timeline:" + requested.id
    : localDraft
      ? baseDraftKey + ":draft:" + localDraft
      : baseDraftKey;
  function openLocalDraft(data) {
    const id = uid(),
      key = baseDraftKey + ":draft:" + id;
    // Seed the new editing route before leaving this one. Its recovery record
    // has no relation to the AI-selected immutable timeline.
    sessionStorage.setItem(key, JSON.stringify({ data, record: null }));
    location.hash = "editor/local-draft/" + id;
  }
  let e = drafts.get(draftKey);
  if (
    requested &&
    !e?.dirty &&
    (e?.record?.id !== requested.id || e?.record?.version !== requested.version)
  )
    e = null;
  if (!e) {
    let latest = localDraft ? null : requested || ctx.records("Timeline")[0];
    let recovered;
    try {
      const pending = JSON.parse(sessionStorage.getItem(draftKey) || "null");
      if (
        pending &&
        Array.isArray(pending.data?.clips) &&
        pending.data?.format
      ) {
        // Keep the original version for optimistic concurrency on a resumed edit.
        latest = pending.record || null;
        recovered = pending.data;
      }
    } catch {
      /* A browser storage quota or user reset must not prevent editing. */
    }
    e = {
      data: clone(recovered || latest?.data || initial(ctx.project?.())),
      record: latest || null,
      selected: null,
      dirty: !!recovered,
      undo: [],
      redo: [],
      playhead: 0,
      playing: false,
      ripple: false,
      snap: true,
      zoom: 55,
      safe: false,
    };
    drafts.set(draftKey, e);
  }
  e.data.clips ??= [];
  e.data.audio_tracks ??= [];
  e.data.captions ??= [];
  e.data.format ??= { width: 1080, height: 1920, fps: 30 };
  const layoutKey = [
    "ship-editor-layout-v1",
    actor?.tenant_id || "local",
    actor?.actor_id || "owner",
  ].join(":");
  const paneKey = layoutKey + ":visibility",
    savedPanes = loadView(paneKey);
  e.hideBin ??= savedPanes.hideBin === true;
  e.hideInspector ??= savedPanes.hideInspector === true;
  e.viewer ??= { mode: "fit", x: 0, y: 0 };
  let releaseLayout, viewerControls;
  let mounted = true;
  const active = () => mounted && (ctx.state.projectId || "none") === pid;
  // A batch timeline can outlive the recent-record window. Resolve only the
  // distinct sources needed for this timeline and keep them across list refreshes.
  const pinned = new Map();
  const sourceIds = new Set(
    [...e.data.clips, ...e.data.audio_tracks]
      .flatMap((c) => [c.asset_version_id, c.asset_id])
      .filter(Boolean),
  );
  let linkedSource,
    initialSourceSeconds = 0;
  if (ctx.editorSource) {
    const { id, version } = ctx.editorSource;
    if (!id || !Number.isSafeInteger(version) || version < 1)
      throw new Error("素材版本无效。");
    const source = await ctx.api("/records/" + encodeURIComponent(id));
    if (!currentPage()) return;
    if (
      source?.project_id !== pid ||
      source.kind !== "AssetVersion" ||
      source.version !== version
    )
      throw new Error("此素材版本已变更，请重新检索。");
    linkedSource = source;
    initialSourceSeconds = Number.isFinite(ctx.editorSource.seconds)
      ? Math.max(0, ctx.editorSource.seconds)
      : 0;
    e.sourceId = id;
  }
  const currentIds = new Map(ctx.state.records.map((r) => [r.id, r]));
  const missing = [];
  for (const id of sourceIds) {
    if (currentIds.has(id)) pinned.set(id, currentIds.get(id));
    else missing.push(id);
  }
  for (let n = 0; n < missing.length; n += 8) {
    await Promise.all(
      missing.slice(n, n + 8).map(async (id) => {
        try {
          const r = await ctx.api("/records/" + encodeURIComponent(id));
          if (r?.project_id === pid) pinned.set(id, r);
        } catch (err) {
          if (![403, 404].includes(err.status)) throw err;
        }
      }),
    );
    if (!currentPage()) return;
  }
  let baseRecords = null,
    combined = [];
  const allRecords = () => {
    if (baseRecords !== ctx.state.records) {
      baseRecords = ctx.state.records;
      const known = new Set(baseRecords.map((r) => r.id));
      combined = [
        ...baseRecords,
        ...Array.from(pinned.values()).filter((r) => !known.has(r.id)),
      ];
    }
    return combined;
  };
  const assets = () => mediaLibrary(allRecords());
  const findAsset = (id) => allRecords().find((r) => r.id === id);
  const clipAsset = (c) => selectedMedia(c, allRecords());
  const currentSource = () =>
    e.sourceId &&
    (linkedSource?.id === e.sourceId ? linkedSource : findAsset(e.sourceId));
  const lastExport = () =>
    ctx
      .records("Artifact")
      .find((a) => e.record && a.data?.timeline_id === e.record.id);
  const assetDuration = (a) =>
    mediaDuration(a) || (mediaType(a) === "image" ? 5 : 0);
  const allItems = () => [
    ...e.data.clips.map((c) => ({ ...c, _kind: "video" })),
    ...e.data.audio_tracks.map((c) => ({ ...c, _kind: "audio" })),
  ];
  const current = () =>
    e.data.clips.find((c) => c.id === e.selected) ||
    e.data.audio_tracks.find((c) => c.id === e.selected);
  let frame = 0,
    lastFrame = 0,
    drag = null;
  const guard = () => {
    if (e.saving) throw new Error("正在保存，请稍候。");
    if (e.exporting) throw new Error("导出中，请稍候。");
    if (e.data.locked) throw new Error("请先解锁时间线。");
  };
  const persistDraft = () => {
    if (pid === "none") return;
    try {
      if (e.dirty)
        sessionStorage.setItem(
          draftKey,
          JSON.stringify({ data: e.data, record: e.record }),
        );
      else sessionStorage.removeItem(draftKey);
    } catch {
      /* The in-memory draft still remains available. */
    }
  };
  const snapshot = () => {
    e.undo.push(clone(e.data));
    if (e.undo.length > 60) e.undo.shift();
    e.redo = [];
  };
  const dirty = () => {
    e.dirty = true;
    const status = $("#edit-status", container);
    if (status) {
      status.textContent = "有未保存修改";
      status.className = "edit-status dirty";
    }
    const save = $("[data-action=save-timeline]", container);
    if (save) save.disabled = false;
  };
  function mutate(fn) {
    guard();
    snapshot();
    try {
      fn();
      e.data.duration = duration(e.data);
      dirty();
    } catch (err) {
      e.data = e.undo.pop();
      throw err;
    }
  }
  function rippleAfter(c, delta, previousEnd) {
    if (!e.ripple || Math.abs(delta) < 1e-8) return;
    const targets = e.data.clips.filter(
      (x) =>
        x.id !== c.id &&
        x.track === c.track &&
        x.timeline_start >= previousEnd - 1e-5,
    );
    if (targets.some((x) => x.locked))
      throw new Error("联动会改变人工锁定片段，操作已停止。");
    targets.forEach(
      (x) => (x.timeline_start = Math.max(0, x.timeline_start + delta)),
    );
  }
  function addAsset(a, position = null, shot = null) {
    ctx.requireProject();
    guard();
    if (!a?.data?.blob_location_id)
      throw new Error("所选素材没有当前可消费的实际文件");
    const binding = mediaBinding(a, allRecords()),
      selected = selectedMedia(binding, allRecords());
    if (!selected || assetDuration(selected) <= 0)
      throw new Error("所选版本缺少有效媒体时长，请核对探测结果。");
    mutate(() => {
      const isAudio = mediaType(selected) === "audio",
        list = isAudio ? e.data.audio_tracks : e.data.clips,
        track = isAudio ? 2 : 0,
        start =
          position ??
          Math.max(
            0,
            ...list
              .filter((c) => c.track === track)
              .map((c) => c.timeline_start + length(c)),
          );
      const c = {
        id: uid(),
        ...binding,
        source_in: shot ? shot.data.source_in : 0,
        source_out: shot ? shot.data.source_out : assetDuration(selected),
        ...(shot ? { shot_ref: ctx.ref(shot) } : {}),
        timeline_start: start,
        track,
        speed: 1,
        volume: 1,
        opacity: 1,
        x: 0,
        y: 0,
        fit: "contain",
        locked: false,
      };
      list.push(c);
      e.selected = c.id;
      e.sourceId = null;
      e.playhead = start;
    });
    draw();
  }
  const beatsKey = draftKey + ":music-overlay";
  const beatRequest = sessionStorage.getItem("ship-add-beats");
  function beatSource() {
    const req = e.beatOverlay;
    if (!req || req.project_id !== pid) return null;
    const music = findAsset(req.music_id),
      audio = findAsset(req.audio_id);
    return music?.kind === "Music" &&
      music.project_id === pid &&
      music.version === req.music_version &&
      music.data.analysis_method === "manual_marks" &&
      music.data.use_markers !== false &&
      audio?.kind === "Audio" &&
      audio.project_id === pid &&
      audio.version === req.audio_version &&
      music.data.audio_ref?.id === audio.id &&
      music.data.audio_ref.version === audio.version
      ? { music, audio }
      : null;
  }
  try {
    const request = beatRequest && JSON.parse(beatRequest);
    if (request?.project_id === pid) {
      e.beatOverlay = request;
      if (!beatSource()) {
        e.beatOverlay = null;
        throw new Error("拍点或音轨版本已变更，请重新选择。");
      }
      sessionStorage.setItem(beatsKey, beatRequest);
      sessionStorage.removeItem("ship-add-beats");
    } else if (!e.beatOverlay) {
      e.beatOverlay = JSON.parse(sessionStorage.getItem(beatsKey) || "null");
    }
  } catch (err) {
    toast(err.message, "error");
  }
  const shotRequest = sessionStorage.getItem("ship-add-shot");
  if (shotRequest) {
    try {
      const req = JSON.parse(shotRequest);
      if (req.project_id === pid) {
        const shot = findAsset(req.shot_id),
          version = findAsset(req.asset_version_id);
        if (
          shot?.kind !== "Shot" ||
          shot.version !== req.shot_version ||
          shot.project_id !== pid ||
          version?.kind !== "AssetVersion" ||
          version.version !== req.asset_revision ||
          version.project_id !== pid ||
          version.data.asset_id !== req.asset_id ||
          shot.data.asset_version_id !== version.id ||
          req.source_in !== shot.data.source_in ||
          req.source_out !== shot.data.source_out ||
          mediaType(version) !== "video" ||
          !(
            0 <= req.source_in &&
            req.source_in < req.source_out &&
            req.source_out <= assetDuration(version) + 0.000001
          )
        ) {
          throw new Error("分镜或素材版本已变化。请重新选择该分镜。");
        }
        if (e.data.locked) throw new Error("请解锁时间线，再从分镜加入。");
        addAsset(version, null, shot);
        sessionStorage.removeItem("ship-add-shot");
      }
    } catch (error) {
      toast(error.message, "error");
    }
  }
  const queued = sessionStorage.getItem("ship-add-asset");
  if (queued) {
    sessionStorage.removeItem("ship-add-asset");
    try {
      if (e.data.locked) {
        e.data = { ...clone(e.data), locked: false, status: "draft" };
        e.dirty = true;
      }
      addAsset(findAsset(queued));
    } catch (err) {
      toast(err.message, "error");
    }
  }
  function draw() {
    stopPlayback();
    if (!active()) return;
    persistDraft();
    releaseLayout?.();
    viewerControls?.destroy();
    const scroll = $("#timeline-scroll", container)?.scrollLeft || 0;
    const inspectorScroll = $("#clip-inspector", container)?.scrollTop || 0;
    container.innerHTML = editorMarkup(e, {
      assets: assets(),
      assetUrl: ctx.assetUrl,
      total: duration(e.data),
      source: currentSource(),
      project: ctx.state.projectId,
      artifact: lastExport(),
    });
    drawTimeline();
    drawInspector();
    buildPreview();
    bindControls();
    const room = $(".editor-room", container);
    viewerControls = installViewer(room, e.viewer, {
      format: () => e.data.format,
      source: currentSource,
      isPlayable: () => !!currentSource() || duration(e.data) > 0,
      play: playViewer,
      refresh: updatePreview,
    });
    releaseLayout = installEditorLayout(room, layoutKey, () =>
      viewerControls?.layout(),
    );
    updateSelectionControls();
    updatePreview();
    $("#timeline-scroll", container).scrollLeft = scroll;
    $("#clip-inspector", container).scrollTop = inspectorScroll;
    setImportStatus();
  }
  function drawTimeline() {
    const total = duration(e.data) || 10;
    const scroll = $("#timeline-scroll", container);
    const laneLabel =
      parseFloat(getComputedStyle(scroll).getPropertyValue("--lane-label")) ||
      75;
    const visibleWidth = Math.max(120, scroll.clientWidth - laneLabel - 2);
    if (!e.manualZoom)
      e.zoom = Math.max(0.035, Math.min(4500, visibleWidth / (total * 1.1)));
    const width = Math.max(
      visibleWidth,
      Math.ceil(total * e.zoom + Math.min(80, visibleWidth * 0.09)),
    );
    const minimumStep = Math.max(68 / e.zoom, total / 200);
    const unit = 10 ** Math.floor(Math.log10(minimumStep));
    const step =
      ([1, 2, 5, 10].find((n) => n * unit >= minimumStep) || 10) * unit;
    const ruler = $("#timeline-ruler", container);
    ruler.style.width = width + "px";
    ruler.innerHTML =
      Array.from(
        { length: Math.floor(width / e.zoom / step) + 1 },
        (_, i) =>
          `<span style="left:${i * step * e.zoom}px">${time(i * step).replace(/\.0$/, "")}</span>`,
      ).join("") +
      `<div class="room-ruler-head timeline-playhead" style="left:${e.playhead * e.zoom}px"></div>`;
    const tracks = [
      ...new Set([
        0,
        1,
        2,
        ...e.data.clips.map((c) => Number(c.track) || 0),
        ...e.data.audio_tracks.map((c) => Number(c.track) || 2),
      ]),
    ].sort((a, b) => a - b);
    const items = allItems();
    const clipHTML = (c, track) => {
      const asset = clipAsset(c);
      return `<button class="timeline-clip room-clip ${e.selected === c.id ? "selected" : ""} ${c.locked ? "locked" : ""}" data-clip="${esc(c.id)}" data-track="${track}"
        style="left:${c.timeline_start * e.zoom}px;width:${Math.max(9, length(c) * e.zoom)}px" aria-label="${esc(mediaName(asset))}，${time(c.timeline_start)} 开始，${time(length(c))} 长">
        <span class="room-strip" aria-hidden="true"></span><strong>${esc(mediaName(asset))}</strong>
        <span class="room-trim" data-trim="in" title="拖动入点"></span><span class="room-trim" data-trim="out" title="拖动出点"></span>
      </button>`;
    };
    const trackHTML = (type, label, iconName, contents, track) =>
      `<div class="timeline-track room-lane-row" data-role="${type}"><span class="track-label">${icon(iconName)}${label}</span>
        <div class="track-lane" ${track != null ? `data-track-lane="${track}"` : ""} style="width:${width}px">${contents}
        <div class="timeline-playhead" style="left:${e.playhead * e.zoom}px"></div></div></div>`;
    $("#timeline-tracks", container).innerHTML =
      tracks
        .map((track) =>
          trackHTML(
            track === 2 ? "audio" : "video",
            track === 2
              ? "声音"
              : track === 0
                ? "主画面"
                : track === 1
                  ? "叠加"
                  : "画面 " + (track + 1),
            track === 2 ? "music" : "film",
            items
              .filter((c) => Number(c.track || 0) === track)
              .map((c) => clipHTML(c, track))
              .join(""),
            track,
          ),
        )
        .join("") +
      trackHTML(
        "caption",
        "字幕",
        "edit",
        e.data.captions
          .map(
            (c, i) =>
              `<button class="timeline-clip timeline-caption" data-caption="${i}" style="left:${c.start * e.zoom}px;width:${Math.max(9, (c.end - c.start) * e.zoom)}px"><strong>${esc(c.text)}</strong></button>`,
          )
          .join(""),
      );
    const source = beatSource();
    if (source) {
      const bound = e.data.audio_tracks.filter(
        (c) => c.asset_id === source.audio.id,
      );
      const markerLabels = { beat: "拍点", downbeat: "强拍", section: "乐段" };
      const markers = bound
        .flatMap((c) =>
          source.music.data.markers
            .filter((m) => m.time >= c.source_in && m.time < c.source_out)
            .map((m) => ({
              ...m,
              position:
                c.timeline_start + (m.time - c.source_in) / (c.speed || 1),
            })),
        )
        .sort((a, b) => a.position - b.position);
      if (markers.length) {
        const html = markers
          .map(
            (m) =>
              `<button type="button" class="room-beat" data-beat-time="${Number(m.position)}" data-kind="${esc(m.type)}" style="left:${m.position * e.zoom}px" aria-label="${esc(m.label || markerLabels[m.type] || "标记")} ${time(m.position)}" title="${esc(m.label || markerLabels[m.type] || "标记")} ${time(m.position)}"></button>`,
          )
          .join("");
        $("#timeline-tracks", container).insertAdjacentHTML(
          "beforeend",
          trackHTML("beat", "拍点", "music", html),
        );
        $$("[data-beat-time]", container).forEach(
          (b) =>
            (b.onclick = () => {
              e.playhead = Number(b.dataset.beatTime);
              e.sourceId = null;
              draw();
            }),
        );
      }
      const footer = $(".room-timeline-foot>div", container);
      footer.querySelector("[data-beat-tools]")?.remove();
      footer.insertAdjacentHTML(
        "afterbegin",
        `<span data-beat-tools>${!bound.length ? btn("加入拍点音轨", "add-beat-track", "music", "button small secondary", e.data.locked ? "disabled" : "") : ""}${btn("隐藏拍点", "dismiss-beats", "", "button small ghost")}</span>`,
      );
    }
    for (const b of $$("[data-clip]", container)) {
      const item = items.find((c) => c.id === b.dataset.clip),
        asset = clipAsset(item);
      if (asset)
        filmstrip(
          $(".room-strip", b),
          ctx.assetUrl(asset),
          item.source_in,
          item.source_out,
          mediaType(asset),
          assetDuration(asset),
        );
      b.onclick = () => {
        e.selected = b.dataset.clip;
        if (e.sourceId) {
          e.sourceId = null;
          draw();
          return;
        }
        drawInspector();
        updateSelectionControls();
        $$("[data-clip]", container).forEach((x) =>
          x.classList.toggle("selected", x.dataset.clip === e.selected),
        );
      };
      b.onpointerdown = startDrag;
    }
    $$("[data-caption]", container).forEach(
      (b) =>
        (b.onclick = () => {
          try {
            captionForm(+b.dataset.caption);
          } catch (error) {
            toast(error.message, "error");
          }
        }),
    );
    const seek = (ev, element) => {
      if (e.sourceId) {
        e.sourceId = null;
        draw();
      }
      stopPlayback();
      e.playhead = Math.max(
        0,
        Math.min(
          duration(e.data),
          (ev.clientX - element.getBoundingClientRect().left) / e.zoom,
        ),
      );
      updatePreview();
    };
    ruler.onpointerdown = (ev) => {
      if (ev.button !== 0) return;
      if (e.sourceId) {
        e.sourceId = null;
        draw();
        return;
      }
      ruler.setPointerCapture(ev.pointerId);
      seek(ev, ruler);
      ruler.onpointermove = (move) => {
        if (ruler.hasPointerCapture(move.pointerId)) seek(move, ruler);
      };
    };
    $$(".track-lane", container).forEach(
      (lane) =>
        (lane.onclick = (ev) => {
          if (!ev.target.closest("button")) seek(ev, lane);
        }),
    );
  }
  function updateSelectionControls() {
    const c = current();
    for (const action of ["split", "duplicate", "delete"]) {
      const b = $(`[data-action=${action}]`, container);
      if (b)
        b.disabled =
          !c || e.data.locked || (action !== "duplicate" && c.locked);
    }
    const undo = $("[data-action=undo]", container),
      redo = $("[data-action=redo]", container);
    if (undo) undo.disabled = e.data.locked || !e.undo.length;
    if (redo) redo.disabled = e.data.locked || !e.redo.length;
  }
  function drawInspector() {
    const c = current(),
      el = $("#clip-inspector", container);
    if (!c) {
      el.innerHTML = `<div class="inspector-heading"><h3 data-dock-handle title="拖动检查器标题交换位置">工程设置</h3>${icon("sliders")}</div><div>${field("工程名称", "timeline-name", e.data.name, "text", 'id="timeline-name" maxlength="200"')}<div class="divider"></div><dl class="detail-facts"><div><dt>画幅</dt><dd>${esc(e.data.format.width)} × ${esc(e.data.format.height)}</dd></div><div><dt>帧率</dt><dd>${esc(typeof e.data.format.fps === "object" ? `${e.data.format.fps.num}/${e.data.format.fps.den}` : e.data.format.fps)} fps</dd></div><div><dt>画面片段</dt><dd>${e.data.clips.length}</dd></div><div><dt>独立音轨</dt><dd>${e.data.audio_tracks.length}</dd></div></dl></div>`;
      $("#timeline-name").onchange = (ev) => {
        try {
          mutate(() => (e.data.name = ev.target.value.trim() || "未命名剪辑"));
        } catch (err) {
          toast(err.message, "error");
          ev.target.value = e.data.name;
        }
      };
      return;
    }
    const a = clipAsset(c),
      audio = e.data.audio_tracks.some((x) => x.id === c.id);
    el.innerHTML = `<div class="inspector-heading"><h3 data-dock-handle title="拖动检查器标题交换位置">${audio ? "音轨" : "片段"}参数</h3>${iconBtn(c.locked ? "解除片段人工锁" : "锁定片段", "lock-clip", c.locked ? "lock" : "unlock")}</div><div><div class="eyebrow" style="overflow-wrap:anywhere;margin-bottom:16px">${esc(mediaName(a))}</div><div class="form-grid">${field("源入点（秒）", "source_in", c.source_in, "number", 'min="0" step="0.01" data-property="source_in"')}${field("源出点（秒）", "source_out", c.source_out, "number", `min="0" max="${assetDuration(a)}" step="0.01" data-property="source_out"`)}${field("时间线起点", "timeline_start", c.timeline_start, "number", 'min="0" step="0.01" data-property="timeline_start"')}${select(
      "轨道",
      "track",
      audio
        ? [[2, "独立音轨"]]
        : [
            [0, "画面 1"],
            [1, "画面 2"],
            [3, "画面 4"],
          ],
      c.track,
      'data-property="track"',
    )}</div>${rangeHTML("速度", "speed", c.speed || 1, 0.25, 4, 0.05, "×")}${rangeHTML("音量", "volume", c.volume ?? 1, 0, 4, 0.05, "×")}</div>${
      !audio
        ? `<details class="inspector-section room-disclosure" ${e.openComposition ? "open" : ""}><summary>构图与关键帧</summary><div class="form-grid">${field("水平位置（px）", "x", c.x || 0, "number", 'step="1" data-property="x"')}${field("垂直位置（px）", "y", c.y || 0, "number", 'step="1" data-property="y"')}${field("宽度（px，留空随画布）", "width", c.width || "", "number", 'min="2" step="2" data-property="width"')}${field("高度（px，留空随画布）", "height", c.height || "", "number", 'min="2" step="2" data-property="height"')}</div>${rangeHTML("不透明度", "opacity", c.opacity ?? 1, 0, 1, 0.01)}${select(
            "画面适配",
            "fit",
            [
              ["contain", "完整显示"],
              ["cover", "填满画幅"],
            ],
            c.fit || "contain",
            'data-property="fit"',
          )}<div class="divider"></div><div class="surface-title"><h3>关键帧</h3>${btn("添加", "keyframe", "plus", "button small secondary")}</div><div class="keyframe-list">${
            Object.entries(c.keyframes || {})
              .flatMap(([property, keys]) =>
                keys.map(
                  (k, i) =>
                    `<div class="keyframe-row">${icon("keyframe")}<span>${esc(property)} · ${time(k.time)} → ${esc(k.value)}</span>${iconBtn("删除关键帧", "remove-keyframe", "close", `data-property="${esc(property)}" data-index="${i}"`)}</div>`,
                ),
              )
              .join("") || '<span class="help-text">未设置关键帧</span>'
          }</div></details>`
        : ""
    }`;
    const composition = $("details.room-disclosure", el);
    if (composition)
      composition.ontoggle = () => {
        e.openComposition = composition.open;
      };
    const actions = [
      btn("向前排列", "move-prev", "undo", "button small secondary"),
      btn("向后排列", "move-next", "redo", "button small secondary"),
      btn("替换素材", "replace-source", "layers", "button small secondary"),
    ];
    if (!audio)
      actions.push(
        btn(
          c.mask ? "编辑画面遮罩" : "添加画面遮罩",
          "clip-mask",
          "layers",
          "button small secondary",
        ),
      );
    if (
      !audio &&
      (c.transition_in ||
        e.data.clips.some(
          (other) =>
            other !== c &&
            other.track === c.track &&
            other.timeline_start < c.timeline_start,
        ))
    )
      actions.push(
        btn(
          c.transition_in ? "调整转场" : "镜头转场",
          "clip-transition",
          "layers",
          "button small secondary",
        ),
      );
    if (!audio && a?.data?.probe?.has_audio)
      actions.push(
        btn("分离音频", "extract-track", "music", "button small secondary"),
      );
    if (!audio && mediaType(a) === "video")
      actions.push(
        btn("调色", "grade-clip", "sliders", "button small secondary"),
      );
    el.insertAdjacentHTML(
      "beforeend",
      `<div class="room-clip-actions">${actions.join("")}</div>`,
    );
    $$("[data-property]", el).forEach((input) => {
      if (input.dataset.action) return;
      input.addEventListener(
        input.type === "range" ? "input" : "change",
        (ev) => changeProperty(input, ev),
      );
      if (input.type === "range") {
        input.onpointerdown = () => {
          if (!e.data.locked && !c.locked) {
            snapshot();
            input.dataset.snapshotted = "1";
          }
        };
        input.onpointerup = () => delete input.dataset.snapshotted;
      }
    });
  }
  function changeProperty(input, event) {
    const c = current(),
      beforeData = clone(e.data);
    let addedSnapshot = false;
    try {
      guard();
      if (c.locked) throw new Error("当前片段有人工锁，先显式解锁。");
      const property = input.dataset.property,
        previousEnd = c.timeline_start + length(c),
        before = length(c);
      let value = input.value;
      if (property !== "fit") value = value === "" ? null : Number(value);
      if (
        value !== null &&
        typeof value === "number" &&
        !Number.isFinite(value)
      )
        throw new Error("请输入有限数值。");
      if (property === "source_in" && (value < 0 || value >= c.source_out))
        throw new Error("源入点必须小于出点。");
      if (
        property === "source_out" &&
        (value <= c.source_in || value > assetDuration(clipAsset(c)) + 0.01)
      )
        throw new Error("源出点必须在原素材区间内。");
      if (property === "speed" && (value < 0.25 || value > 4))
        throw new Error("支持 0.25 到 4 倍常速。");
      if (property === "timeline_start" && value < 0)
        throw new Error("时间线起点不能为负数。");
      if (!input.dataset.snapshotted) {
        snapshot();
        addedSnapshot = true;
      }
      if (value == null) delete c[property];
      else c[property] = value;
      rippleAfter(c, length(c) - before, previousEnd);
      e.data.duration = duration(e.data);
      dirty();
      const output = $(`#output-${property}`, container);
      if (output)
        output.textContent = Number(value).toFixed(
          property === "opacity" ? 2 : 2,
        );
      drawTimeline();
      if (
        property === "fit" ||
        property === "width" ||
        property === "height" ||
        property === "track"
      )
        buildPreview();
      updatePreview();
    } catch (err) {
      toast(err.message, "error");
      e.data = beforeData;
      if (addedSnapshot) e.undo.pop();
      drawInspector();
      drawTimeline();
    }
  }
  function buildPreview() {
    const stage = $("#preview-stage", container);
    if (!stage) return;
    const source = currentSource();
    if (source) {
      const url = esc(ctx.assetUrl(source)),
        kind = mediaType(source);
      stage.innerHTML =
        kind === "image"
          ? `<img class="room-source-media" src="${url}" alt="${esc(mediaName(source))}">`
          : kind === "audio"
            ? `<div class="room-audio-preview">${icon("music")}<audio controls src="${url}" preload="metadata"></audio></div>`
            : `<video class="room-source-media" src="${url}" playsinline preload="auto"></video>`;
      const media = stage.querySelector("video"),
        scrub = $("[data-source-scrub]", container),
        button = $("[data-source-play]", container);
      if (media && source.id === linkedSource?.id && initialSourceSeconds > 0) {
        const at = initialSourceSeconds;
        media.addEventListener(
          "loadedmetadata",
          () => {
            if (!media.isConnected || !Number.isFinite(media.duration)) return;
            media.currentTime = Math.min(at, media.duration);
            initialSourceSeconds = 0;
          },
          { once: true },
        );
      }
      if (media && button && scrub) {
        const update = () => {
          if (Number.isFinite(media.duration)) scrub.max = media.duration;
          scrub.value = media.currentTime;
          button.innerHTML = icon(media.paused ? "play" : "pause");
          button.setAttribute(
            "aria-label",
            media.paused ? "播放原片" : "暂停原片",
          );
        };
        media.onloadedmetadata =
          media.ontimeupdate =
          media.onplay =
          media.onpause =
            update;
        scrub.oninput = () => {
          if (Number.isFinite(media.duration))
            media.currentTime = Number(scrub.value);
        };
        button.onclick = playViewer;
        update();
      }
      return;
    }
    stage.innerHTML =
      e.data.clips
        .map((c) => {
          const a = clipAsset(c);
          if (!a) return "";
          const tag = mediaType(a).includes("image") ? "img" : "video";
          return `<${tag} data-preview="${esc(c.id)}" src="${ctx.assetUrl(a)}" ${tag === "video" ? 'muted preload="metadata" playsinline' : ""} alt="${tag === "img" ? esc(a.data.name) : ""}" style="position:absolute;display:none;object-fit:${c.fit === "cover" ? "cover" : "contain"};pointer-events:none">${tag === "video" ? "</video>" : ""}`;
        })
        .join("") +
      '<div class="viewer-caption" id="viewer-caption"></div>' +
      (e.safe ? '<div class="viewer-safe"></div>' : "") +
      e.data.audio_tracks
        .map(
          (c) =>
            `<audio data-preview-audio="${esc(c.id)}" src="${ctx.assetUrl(clipAsset(c) || { id: c.asset_version_id || c.asset_id })}" preload="metadata"></audio>`,
        )
        .join("");
  }
  function updatePreview() {
    const total = duration(e.data);
    e.playhead = Math.max(0, Math.min(total, e.playhead));
    const input = $("#playhead", container);
    if (input) {
      input.max = Math.max(total, 1);
      input.value = e.playhead;
    }
    const label = $("#playhead-label", container);
    if (label) label.textContent = frameTime(e.playhead, e.data.format.fps);
    $$(".timeline-playhead", container).forEach(
      (p) => (p.style.left = e.playhead * e.zoom + "px"),
    );
    const stage = $("#preview-stage", container);
    if (!stage || currentSource()) return;
    const box = stage.getBoundingClientRect(),
      fmt = e.data.format;
    const scale = Math.min(box.width / fmt.width, box.height / fmt.height),
      cw = fmt.width * scale,
      ch = fmt.height * scale,
      left = (box.width - cw) / 2,
      top = (box.height - ch) / 2;
    const rate =
      typeof fmt.fps === "object" ? fmt.fps.num / fmt.fps.den : fmt.fps;
    const at =
      !e.playing && total > 0 && e.playhead >= total
        ? Math.max(0, total - 1 / (rate || 30))
        : e.playhead;
    e.data.clips.forEach((c) => {
      const el = $(`[data-preview="${CSS.escape(String(c.id))}"]`, container);
      if (!el) return;
      const local = at - c.timeline_start,
        active = local >= 0 && local < length(c);
      el.style.display = active ? "block" : "none";
      if (active) {
        el.style.width = (c.width || fmt.width) * scale + "px";
        el.style.height = (c.height || fmt.height) * scale + "px";
        el.style.left =
          left + evaluate(c.keyframes?.x, local, c.x || 0) * scale + "px";
        el.style.top =
          top + evaluate(c.keyframes?.y, local, c.y || 0) * scale + "px";
        el.style.opacity = evaluate(
          c.keyframes?.opacity,
          local,
          c.opacity ?? 1,
        );
        const gain = showTransition(
          el,
          c,
          e.data.clips,
          at,
          Number(el.style.opacity),
        );
        if (el.tagName === "VIDEO") {
          const target = c.source_in + local * (c.speed || 1);
          if (Math.abs(el.currentTime - target) > (e.playing ? 0.18 : 0.001))
            try {
              el.currentTime = target;
            } catch {}
          el.playbackRate = c.speed || 1;
          el.volume = Math.min(1, (c.volume ?? 1) * gain);
          el.muted = (c.volume ?? 1) === 0;
          if (e.playing && el.paused) el.play().catch(() => {});
          if (!e.playing && !el.paused) el.pause();
        }
      } else if (el.tagName === "VIDEO") el.pause();
    });
    e.data.audio_tracks.forEach((c) => {
      const el = $(
        `[data-preview-audio="${CSS.escape(String(c.id))}"]`,
        container,
      );
      if (!el) return;
      const local = at - c.timeline_start,
        active = local >= 0 && local < length(c);
      if (active) {
        const target = c.source_in + local * (c.speed || 1);
        if (Math.abs(el.currentTime - target) > (e.playing ? 0.18 : 0.001))
          try {
            el.currentTime = target;
          } catch {}
        el.playbackRate = c.speed || 1;
        el.volume = Math.min(1, c.volume ?? 1);
        if (e.playing && el.paused) el.play().catch(() => {});
        else if (!e.playing) el.pause();
      } else el.pause();
    });
    const caption = e.data.captions.filter((c) => at >= c.start && at < c.end);
    const labelCaption = $("#viewer-caption", container);
    if (labelCaption) {
      labelCaption.textContent = caption.map((c) => c.text).join("\n");
      labelCaption.style.zIndex = 300;
      labelCaption.style.fontSize =
        (caption[0]?.font_size || 32) * Math.max(scale, 0.35) + "px";
      labelCaption.style.color = caption[0]?.color || "#ffffff";
    }
  }
  function stopPlayback() {
    cancelAnimationFrame(frame);
    e.playing = false;
    $$("video[data-preview],audio[data-preview-audio]", container).forEach(
      (v) => v.pause(),
    );
    const button = $("[data-action=play]", container);
    if (button) button.innerHTML = icon("play");
  }
  function playViewer() {
    const source = currentSource();
    if (source) {
      const player = $("#preview-stage video,#preview-stage audio", container);
      if (player) {
        if (player.paused)
          player.play().catch((err) => toast(err.message, "error"));
        else player.pause();
      }
      return;
    }
    play();
  }
  function play() {
    if (!duration(e.data)) {
      toast("先添加片段或文字。", "error");
      return;
    }
    if (e.playing) {
      stopPlayback();
      return;
    }
    if (e.playhead >= duration(e.data)) e.playhead = 0;
    e.playing = true;
    lastFrame = performance.now();
    $("[data-action=play]", container).innerHTML = icon("pause");
    function tick(now) {
      if (!e.playing) return;
      e.playhead += (now - lastFrame) / 1000;
      lastFrame = now;
      if (e.playhead >= duration(e.data)) {
        e.playhead = duration(e.data);
        stopPlayback();
        updatePreview();
        return;
      }
      updatePreview();
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
  }
  function startDrag(ev) {
    if (ev.button !== 0) return;
    const c =
      e.data.clips.find((x) => x.id === ev.currentTarget.dataset.clip) ||
      e.data.audio_tracks.find((x) => x.id === ev.currentTarget.dataset.clip);
    e.selected = c.id;
    drawInspector();
    updateSelectionControls();
    if (c.locked || e.data.locked) return;
    const edge = ev.target.closest("[data-trim]")?.dataset.trim;
    const box = ev.currentTarget,
      sourceMax = assetDuration(clipAsset(c));
    const previousEnd = c.timeline_start + length(c),
      previousLength = length(c);
    drag = {
      id: c.id,
      startX: ev.clientX,
      origin: c.timeline_start,
      sourceIn: c.source_in,
      sourceOut: c.source_out,
      changed: false,
    };
    box.setPointerCapture(ev.pointerId);
    box.onpointermove = (move) => {
      if (!drag) return;
      const pixels = move.clientX - drag.startX;
      if (!drag.changed && Math.abs(pixels) < 4) return;
      if (!drag.changed) {
        snapshot();
        drag.changed = true;
      }
      const fps = e.data.format.fps,
        rate = typeof fps === "object" ? fps.num / fps.den : fps || 30;
      const delta = e.snap
        ? Math.round((pixels / e.zoom) * rate) / rate
        : pixels / e.zoom;
      if (edge === "in") {
        c.source_in = Math.max(
          0,
          Math.min(
            drag.sourceOut - 1 / rate,
            drag.sourceIn + delta * (c.speed || 1),
          ),
        );
        c.timeline_start = drag.origin;
      } else if (edge === "out") {
        c.source_out = Math.max(
          drag.sourceIn + 1 / rate,
          Math.min(sourceMax, drag.sourceOut + delta * (c.speed || 1)),
        );
      } else {
        c.timeline_start = Math.max(0, drag.origin + delta);
      }
      box.style.left = c.timeline_start * e.zoom + "px";
      box.style.width = Math.max(9, length(c) * e.zoom) + "px";
      e.playhead =
        edge === "out"
          ? c.timeline_start + length(c) - 1 / rate
          : c.timeline_start;
      updatePreview();
    };
    const finish = (cancel) => {
      if (!drag) return;
      if (drag.changed) {
        try {
          if (cancel) {
            e.data = e.undo.pop();
          } else {
            if (edge) rippleAfter(c, length(c) - previousLength, previousEnd);
            else if (e.ripple) {
              const items = (
                e.data.clips.includes(c) ? e.data.clips : e.data.audio_tracks
              )
                .filter((x) => x.track === c.track)
                .sort((a, b) => a.timeline_start - b.timeline_start);
              let next = 0;
              for (const x of items) {
                if (x.locked && Math.abs(next - x.timeline_start) > 0.001)
                  throw new Error("重新排序会移动锁定片段。");
                x.timeline_start = next;
                next += length(x);
              }
            }
            dirty();
            e.data.duration = duration(e.data);
          }
        } catch (err) {
          e.data = e.undo.pop();
          toast(err.message, "error");
        }
      }
      drag = null;
      box.onpointermove = null;
      box.onpointerup = null;
      box.onpointercancel = null;
      drawTimeline();
      drawInspector();
      updateSelectionControls();
      updatePreview();
    };
    box.onpointerup = () => finish(false);
    box.onpointercancel = () => finish(true);
  }
  function split() {
    const c = current();
    if (!c) throw new Error("先选择要分割的片段。");
    if (c.locked) throw new Error("片段已锁定。");
    const local = e.playhead - c.timeline_start;
    if (local <= 0.04 || local >= length(c) - 0.04)
      throw new Error("播放头必须位于所选片段内部。");
    mutate(() => {
      const list = e.data.clips.includes(c)
          ? e.data.clips
          : e.data.audio_tracks,
        cut = c.source_in + local * (c.speed || 1),
        second = {
          ...clone(c),
          id: uid(),
          source_in: cut,
          timeline_start: e.playhead,
        },
        keys = c.keyframes || {};
      c.source_out = cut;
      c.keyframes = {};
      second.keyframes = {};
      for (const [property, points] of Object.entries(keys)) {
        const value = evaluate(points, local, c[property] ?? 0);
        c.keyframes[property] = [
          ...points.filter((p) => p.time < local),
          { time: local, value },
        ];
        second.keyframes[property] = [
          { time: 0, value },
          ...points
            .filter((p) => p.time > local)
            .map((p) => ({ ...p, time: p.time - local })),
        ];
      }
      list.splice(list.indexOf(c) + 1, 0, second);
      e.selected = second.id;
    });
    draw();
  }
  function captionForm(index) {
    guard();
    const c =
      index == null
        ? {
            text: "",
            start: Math.min(e.playhead, duration(e.data)),
            end:
              duration(e.data) > e.playhead
                ? Math.min(e.playhead + 3, duration(e.data))
                : e.playhead + 3,
            font_size: 32,
            color: "#ffffff",
          }
        : e.data.captions[index];
    modal(
      index == null ? "添加文字" : "编辑文字",
      `<form data-form class="form-stack">${area("文字内容", "text", c.text, 'required maxlength="2000"')}<div class="form-grid">${field("开始（秒）", "start", c.start, "number", 'min="0" step="0.001" required')}${field("结束（秒）", "end", c.end, "number", 'min="0" step="0.001" required')}${field("字号", "font_size", c.font_size || 32, "number", 'min="8" max="160"')}${field("颜色", "color", c.color || "#ffffff", "color")}</div><div class="form-actions">${index == null ? "" : btn("删除文字", "delete-caption", "trash", "button danger")}<button class="button primary" type="submit">保存文字</button></div></form>`,
      async (f) => {
        const d = formData(f);
        if (+d.end <= +d.start) throw new Error("文字结束时间必须晚于开始时间");
        mutate(() => {
          const data = {
            ...(index == null ? {} : e.data.captions[index]),
            text: d.text,
            start: +d.start,
            end: +d.end,
            font_size: +d.font_size,
            color: d.color,
          };
          if (index == null) e.data.captions.push(data);
          else e.data.captions[index] = data;
        });
        draw();
      },
    );
    if (index != null)
      $("[data-action=delete-caption]", $("#dialog")).onclick = () => {
        mutate(() => e.data.captions.splice(index, 1));
        $("#dialog").close();
        draw();
      };
  }
  function keyframeForm() {
    const c = current();
    if (!c) return;
    if (c.locked) throw new Error("片段已锁定。");
    guard();
    modal(
      "添加关键帧",
      `<form data-form class="form-stack">${select("动画属性", "property", [
        ["opacity", "不透明度"],
        ["x", "水平位置（px）"],
        ["y", "垂直位置（px）"],
      ])}${field("片段局部时间（秒）", "time", Math.max(0, Math.min(length(c), e.playhead - c.timeline_start)).toFixed(2), "number", `min="0" max="${length(c)}" step="0.01" required`)}${field("值", "value", c.opacity ?? 1, "number", 'step="any" required')}<div class="form-actions"><button class="button primary" type="submit">添加关键帧</button></div></form>`,
      async (f) => {
        const d = formData(f);
        if (d.property === "opacity" && (+d.value < 0 || +d.value > 1))
          throw new Error("不透明度必须在 0 到 1 之间");
        if (
          (c.keyframes?.[d.property] || []).some(
            (p) => Math.abs(p.time - +d.time) < 0.0001,
          )
        )
          throw new Error("同一时间已有关键帧，请先删除或选择其他时间。");
        mutate(() => {
          c.keyframes ??= {};
          c.keyframes[d.property] ??= [];
          c.keyframes[d.property].push({ time: +d.time, value: +d.value });
          c.keyframes[d.property].sort((a, b) => a.time - b.time);
        });
        drawInspector();
        updatePreview();
      },
    );
  }
  async function save() {
    ctx.requireProject();
    if (e.record && !e.dirty) return e.record;
    const fps = e.data.format.fps;
    checkTransitions(
      e.data.clips,
      typeof fps === "object" ? fps.num / fps.den : fps,
    );
    for (const item of allItems())
      if (!clipAsset(item))
        throw new Error("时间线引用的版本不在当前工作区，先核对来源后再保存。");
    const refs = [
      ...new Set(
        allItems()
          .flatMap((c) => [c.asset_id, c.asset_version_id])
          .filter(Boolean),
      ),
    ]
      .map(findAsset)
      .filter(Boolean)
      .map(ctx.ref);
    for (const clip of allItems()) {
      if (
        clip.shot_ref &&
        !refs.some(
          (r) =>
            r.id === clip.shot_ref.id && r.version === clip.shot_ref.version,
        )
      )
        refs.push(clip.shot_ref);
    }
    e.data.duration = duration(e.data);
    e.data.locked = false;
    e.data.status = "draft";
    delete e.data.locked_at;
    delete e.data.locked_by;
    if (e.saving) throw new Error("正在保存，请稍候。");
    e.saving = true;
    try {
      e.record = await ctx.saveRecord(
        "Timeline",
        clone(e.data),
        e.record,
        refs,
        "保存用户编辑的统一时间线草稿",
      );
    } catch (error) {
      if (error.code === "REVISION_CONFLICT" && e.record) {
        const latest = await ctx.api(
          "/records/" + encodeURIComponent(e.record.id),
        );
        revisionConflict(latest, e.data, allRecords(), async () => {
          const saved = e.record,
            name = e.data.name;
          e.record = null;
          e.data.name = name + " · 副本";
          try {
            await save();
            if (requested)
              location.hash = "editor/project-timeline/" + e.record.id;
          } catch (err) {
            e.record = saved;
            e.data.name = name;
            toast(err.message, "error");
            throw err;
          }
        });
      }
      throw error;
    } finally {
      e.saving = false;
    }
    e.data = clone(e.record.data);
    e.dirty = false;
    toast("工程已保存");
    draw();
    return e.record;
  }
  async function lockCurrent(label = "确认锁定版本") {
    ctx.requireProject();
    if (e.data.locked && e.record && !e.dirty) return e.record;
    if (!e.data.clips.length)
      throw new Error("需要至少一个实际画面片段才能锁版。");
    const record = await save();
    const accepted = await confirmAction(
      "锁定已保存的时间线？",
      `${record.data.name} · ${record.data.clips.length} 个画面片段 · ${time(duration(record.data))}。锁定此版本后可继续创建草稿。`,
      label,
    );
    if (!accepted) return null;
    const response = await ctx.command(
      "LockTimelineRevision",
      { confirmed_hash: record.hash },
      record,
    );
    const locked = response.record || (await ctx.api(`/records/${record.id}`));
    if (!locked.data.locked)
      throw new Error("服务端未返回已锁定版本，请核对命令回执。");
    e.record = locked;
    e.data = clone(locked.data);
    e.dirty = false;
    e.undo = [];
    e.redo = [];
    await ctx.reloadData();
    draw();
    return locked;
  }
  async function toggleLock() {
    if (e.busy) return;
    e.busy = true;
    try {
      if (e.data.locked) {
        if (
          !(await confirmAction(
            "创建可编辑的草稿版本？",
            "已锁定的旧版本与交付引用保留。新的编辑版本需要再次审阅和锁版。",
            "创建草稿",
          ))
        )
          return;
        e.data.locked = false;
        e.data.status = "draft";
        e.dirty = true;
        await save();
      } else {
        await lockCurrent();
      }
    } finally {
      e.busy = false;
      draw();
    }
  }
  function versionHistory() {
    return showVersions(ctx, e, (version) => {
      if (e.busy || e.saving) return;
      snapshot();
      e.data = clone(version.data);
      e.data.locked = false;
      e.data.status = "draft";
      delete e.data.locked_at;
      delete e.data.locked_by;
      e.selected = null;
      e.sourceId = null;
      e.playhead = 0;
      dirty();
      draw();
    });
  }
  function timelineList() {
    const list = ctx.records("Timeline");
    const d = modal(
      "剪辑工程",
      `${list.map((r) => `<button class="list-row" data-timeline="${r.id}">${icon("film")}<span class="list-row-body"><strong>${esc(r.data.name || "剪辑工程")}</strong><p>${arr(r.data.clips).length} 个片段 · ${time(duration(r.data))}</p></span>${badge(r.data.locked ? "已锁版" : "可编辑", r.data.locked ? "blue" : "")}<small>v${r.version}</small></button>`).join("") || empty("还没有保存的工程", "完成编辑后保存，就可以在这里切换。", "", "", "film")}<div class="dialog-actions">${btn("导入工程 JSON", "import-timeline", "upload", "button secondary")}${btn("导出工程 JSON", "export-timeline", "download", "button secondary")}${btn("空白工程", "new-timeline", "plus", "button primary")}</div><div class="room-exchange">${btn("比较版本", "compare-versions", "history", "button small secondary", e.record ? "" : "disabled")}${btn("导入 OTIO", "import-otio", "upload", "button small secondary")}${btn("导出 OTIO", "export-otio", "download", "button small secondary")}${btn("导出 SRT", "export-srt", "download", "button small secondary", e.data.captions.length ? "" : "disabled")}${btn("导出 WebVTT", "export-vtt", "download", "button small secondary", e.data.captions.length ? "" : "disabled")}</div><input type="file" id="timeline-file" accept="application/json,.json" hidden>`,
    );
    $("[data-action=compare-versions]", d).onclick = () =>
      versionHistory().catch((error) => toast(error.message, "error"));
    for (const format of ["srt", "vtt"]) {
      $(`[data-action=export-${format}]`, d).onclick = () =>
        exportCaptions(format).catch((error) => toast(error.message, "error"));
    }
    for (const action of ["import-otio", "export-otio"]) {
      $(`[data-action=${action}]`, d).onclick = async () => {
        try {
          if (action === "import-otio" && e.dirty) await save();
          await openTimelineExchange(action, {
            ctx,
            timelineId: e.record?.id,
            save,
            onImported: async (record) => {
              await ctx.reloadData();
              if (!active()) return;
              if (requested || localDraft) {
                location.hash = "editor/project-timeline/" + record.id;
                return;
              }
              e.record = record;
              e.data = clone(record.data);
              e.dirty = false;
              e.selected = null;
              e.sourceId = null;
              e.undo = [];
              e.redo = [];
              e.playhead = 0;
              draw();
            },
          });
        } catch (error) {
          toast(error.message, "error");
        }
      };
    }
    d.querySelectorAll("[data-timeline]").forEach(
      (b) =>
        (b.onclick = () => {
          d.close();
          if (e.record?.id === b.dataset.timeline) return;
          persistDraft();
          location.hash = "editor/project-timeline/" + b.dataset.timeline;
        }),
    );
    $("[data-action=new-timeline]", d).onclick = () => {
      if (e.dirty) {
        toast("请先保存当前工程，再创建空白工程。", "error");
        return;
      }
      if (requested || localDraft) {
        d.close();
        openLocalDraft(initial(ctx.project?.()));
        return;
      }
      e.data = initial(ctx.project?.());
      e.record = null;
      e.selected = null;
      e.playhead = 0;
      d.close();
      draw();
    };
    $("[data-action=export-timeline]", d).onclick = () =>
      download(`${e.data.name || "timeline"}.json`, {
        schema_version: 1,
        timeline: e.data,
        source_record: e.record?.id || null,
      });
    $("[data-action=import-timeline]", d).onclick = () =>
      $("#timeline-file").click();
    $("#timeline-file").onchange = async (ev) => {
      try {
        const json = parseJSON(await ev.target.files[0].text()),
          data = json.timeline || json;
        if (!Array.isArray(data.clips) || !data.format)
          throw new Error("工程必须包含 clips 和 format");
        if (
          [...data.clips, ...arr(data.audio_tracks)].some((c) => {
            const source = clipAsset(c);
            return (
              !source?.data?.blob_location_id ||
              (c.asset_version_id && source.data.asset_id !== c.asset_id)
            );
          })
        )
          throw new Error(
            "工程引用的素材或指定版本不在当前项目，或版本归属不一致。先导入素材并明确映射。",
          );
        if (e.dirty) throw new Error("请先保存当前工程。");
        if (requested || localDraft) {
          d.close();
          openLocalDraft({ ...clone(data), locked: false });
          return;
        }
        e.data = clone(data);
        e.data.locked = false;
        e.record = null;
        e.selected = null;
        e.dirty = true;
        e.undo = [];
        e.redo = [];
        d.close();
        draw();
      } catch (err) {
        toast(err.message, "error");
      }
    };
  }
  function formatForm() {
    guard();
    modal(
      "工程画幅",
      `<form data-form class="form-stack"><div class="form-grid">${field("宽度（px）", "width", e.data.format.width, "number", 'min="64" max="4096" step="2" required')}${field("高度（px）", "height", e.data.format.height, "number", 'min="64" max="4096" step="2" required')}${select(
        "帧率",
        "fps",
        [
          [24, "24 fps"],
          [25, "25 fps"],
          [30, "30 fps"],
          [50, "50 fps"],
          [60, "60 fps"],
        ],
        e.data.format.fps,
      )}</div><div class="form-actions"><button class="button primary" type="submit">应用画幅</button></div></form>`,
      async (f) => {
        const raw = formData(f);
        mutate(
          () =>
            (e.data.format = {
              width: +raw.width,
              height: +raw.height,
              fps: +raw.fps,
            }),
        );
        draw();
      },
    );
  }
  function stepFrame(direction) {
    stopPlayback();
    const fps = e.data.format.fps;
    e.playhead +=
      direction / (typeof fps === "object" ? fps.num / fps.den : fps || 30);
    updatePreview();
  }

  function setImportStatus() {
    const el = $("#room-import-status", container);
    if (el) el.textContent = e.importStatus || "";
  }

  async function importMedia(files, toTimeline) {
    ctx.requireProject();
    if (e.importing) throw new Error("请等待本次导入完成。");
    if (toTimeline) guard();
    e.importing = true;
    const failures = [];
    let completed = 0;
    try {
      for (const file of files) {
        const status = (text) => {
          e.importStatus = text;
          if (active()) setImportStatus();
        };
        status(`导入 ${file.name}`);
        try {
          const result = await uploadFile(ctx.api, pid, file, {
            onProgress: (x) =>
              status(
                `${file.name} · ${x.status === "verifying" ? "正在校验" : Math.round((100 * x.offset) / x.total) + "%"}`,
              ),
          });
          completed++;
          if (!active()) return;
          await ctx.reloadData();
          if (!active()) return;
          if (toTimeline && result.asset) addAsset(findAsset(result.asset.id));
        } catch (err) {
          failures.push(file.name + " · " + err.message);
        }
        if (!active()) return;
      }
    } finally {
      e.importing = false;
      e.importStatus = failures.length
        ? `已导入 ${completed} 项，失败 ${failures.length} 项`
        : completed
          ? `已导入 ${completed} 项素材`
          : "";
      if (active()) {
        draw();
        if (failures.length)
          modal(
            "导入结果",
            `<ul class="room-errors">${failures.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`,
          );
      }
    }
  }

  async function importCaptions(file) {
    guard();
    if (!file) return;
    if (file.size > 1024 * 1024)
      throw new Error("请选择小于 1 MiB 的字幕文件。");
    const timeline = e.data,
      recordId = e.record?.id;
    const result = await ctx.api("/media/captions/parse", {
      method: "POST",
      body: { filename: file.name, content: await file.text() },
    });
    if (!active() || recordId !== e.record?.id || timeline !== e.data) return;
    if (e.data.captions.length + result.captions.length > 400)
      throw new Error("每条时间线最多容纳 400 条字幕。");
    mutate(() => e.data.captions.push(...result.captions));
    e.sourceId = null;
    draw();
    toast(`已加入 ${result.captions.length} 条字幕`);
  }

  async function exportCaptions(format) {
    if (!e.data.captions.length) throw new Error("时间线中还没有字幕。");
    const out = await ctx.api("/media/captions/export", {
      method: "POST",
      body: { captions: e.data.captions, format },
    });
    download(`${e.data.name}.${format}`, out.content, out.mime_type);
  }

  function bindRoomFiles() {
    const fail = (action) => (event) =>
      Promise.resolve()
        .then(() => action(event))
        .catch((error) => toast(error.message, "error"));
    $("#editor-files", container).onchange = fail((event) =>
      importMedia([...event.target.files], false),
    );
    $("#editor-captions", container).onchange = fail((event) =>
      importCaptions(event.target.files[0]),
    );
    const bin = $(".room-bin", container),
      timeline = $(".room-timeline", container),
      viewer = $("#viewer-screen", container);
    for (const target of [bin, timeline, viewer]) {
      const accepts = (ev) =>
        ev.dataTransfer.types.includes("Files") ||
        (target !== bin &&
          ev.dataTransfer.types.includes("application/x-ai-ship-media"));
      target.ondragover = (ev) => {
        if (!accepts(ev)) return;
        ev.preventDefault();
        target.classList.add("room-drag-over");
        ev.dataTransfer.dropEffect = "copy";
      };
      target.ondragleave = (ev) => {
        if (!target.contains(ev.relatedTarget))
          target.classList.remove("room-drag-over");
      };
      target.ondrop = fail((ev) => {
        ev.preventDefault();
        target.classList.remove("room-drag-over");
        if (!ctx.state.projectId) {
          ctx.newProject?.();
          return;
        }
        const input = [...ev.dataTransfer.files];
        if (input.length) return importMedia(input, target !== bin);
        const id = ev.dataTransfer.getData("application/x-ai-ship-media");
        if (id && target !== bin) {
          const lane = ev.target.closest("[data-track-lane]");
          const start = lane
            ? Math.max(
                0,
                (ev.clientX - lane.getBoundingClientRect().left) / e.zoom,
              )
            : null;
          addAsset(findAsset(id), start);
        }
      });
    }
    const renderBin = () => {
      $("#room-bin-list", container).innerHTML = libraryItems(
        assets(),
        e,
        ctx.assetUrl,
      );
    };
    $("#room-query", container).oninput = (ev) => {
      e.query = ev.target.value;
      renderBin();
    };
    $("#room-filter", container).onchange = (ev) => {
      e.filter = ev.target.value;
      renderBin();
    };
    bin.ondragstart = (ev) => {
      const media = ev.target.closest("[data-media-drag]");
      if (media) {
        ev.dataTransfer.setData(
          "application/x-ai-ship-media",
          media.dataset.mediaDrag,
        );
        ev.dataTransfer.effectAllowed = "copy";
      }
    };
  }

  function moveSelected(direction) {
    const c = current();
    if (!c) return;
    const list = (e.data.clips.includes(c) ? e.data.clips : e.data.audio_tracks)
      .filter((item) => item.track === c.track)
      .sort((a, b) => a.timeline_start - b.timeline_start);
    const neighbor = list[list.indexOf(c) + direction];
    if (!neighbor)
      throw new Error(
        direction < 0 ? "已经是轨道第一段。" : "已经是轨道最后一段。",
      );
    if (c.locked || neighbor.locked) throw new Error("先解锁相邻片段。");
    mutate(() => {
      const first = direction < 0 ? neighbor : c,
        second = direction < 0 ? c : neighbor;
      const gap = Math.max(
        0,
        second.timeline_start - first.timeline_start - length(first),
      );
      second.timeline_start = first.timeline_start;
      first.timeline_start = second.timeline_start + length(second) + gap;
    });
    e.playhead = c.timeline_start;
    draw();
  }

  function replaceSource() {
    guard();
    const c = current();
    if (!c || c.locked) throw new Error("请先选择未锁定的片段。");
    const audio = e.data.audio_tracks.includes(c);
    const options = assets().filter(
      (a) =>
        (mediaType(a) === "audio") === audio &&
        assetDuration(a) >= c.source_out - 0.001,
    );
    if (!options.length)
      throw new Error("请先导入能容纳当前源区间的同类素材。");
    modal(
      "替换片段素材",
      `<form data-form class="form-stack">${select(
        "素材",
        "asset",
        options.map((a) => [a.id, mediaName(a)]),
      )}<div class="form-actions"><button class="button primary" type="submit">替换</button></div></form>`,
      async (f) => {
        const a = findAsset(formData(f).asset);
        mutate(() => {
          delete c.asset_version_id;
          Object.assign(c, mediaBinding(a, allRecords()));
        });
        draw();
      },
    );
  }

  async function separateAudio(button) {
    guard();
    const c = current();
    if (!c || c.locked) throw new Error("请先选择未锁定的画面片段。");
    if (
      c.volume === 0 &&
      e.data.audio_tracks.some((a) => {
        const audio = clipAsset(a);
        return (
          audio?.data?.asset_version_id === c.asset_version_id &&
          a.timeline_start === c.timeline_start &&
          a.source_in === c.source_in
        );
      })
    )
      throw new Error("此片段已有对应的独立音轨。");
    const source = clipAsset(c),
      timeline = e.data;
    setBusy(button, true, "分离中");
    try {
      const result = await ctx.api(
        `/media/${encodeURIComponent(source.id)}/extract-audio`,
        {
          method: "POST",
          body: { project_id: pid, expected_revision: source.version },
        },
      );
      if (!active()) return;
      await ctx.reloadData();
      if (
        !active() ||
        e.data !== timeline ||
        !e.data.clips.includes(c) ||
        c.locked
      )
        return;
      mutate(() => {
        const audio = {
          id: uid(),
          asset_id: result.audio.id,
          source_in: c.source_in,
          source_out: c.source_out,
          timeline_start: c.timeline_start,
          track: 2,
          speed: c.speed || 1,
          volume: c.volume ?? 1,
          locked: false,
        };
        e.data.audio_tracks.push(audio);
        c.volume = 0;
      });
      draw();
    } finally {
      setBusy(button, false);
    }
  }

  function gradeClip() {
    guard();
    const c = current();
    if (!c || c.locked) throw new Error("请先选择未锁定的片段。");
    const source = clipAsset(c),
      timeline = e.data;
    modal(
      "片段调色",
      `<form data-form class="form-stack">${field("亮度", "brightness", 0, "number", 'min="-1" max="1" step="0.01" required')}${field("对比度", "contrast", 1, "number", 'min="0" max="3" step="0.01" required')}${field("饱和度", "saturation", 1, "number", 'min="0" max="3" step="0.01" required')}<div class="form-actions"><button class="button primary" type="submit">应用到片段</button></div></form>`,
      async (f) => {
        const recipe = Object.fromEntries(
          Object.entries(formData(f)).map(([k, v]) => [k, Number(v)]),
        );
        const result = await ctx.api(
          `/media/${encodeURIComponent(source.id)}/transform`,
          {
            method: "POST",
            body: {
              project_id: pid,
              expected_revision: source.version,
              recipe,
            },
          },
        );
        if (!active()) return;
        await ctx.reloadData();
        if (
          !active() ||
          e.data !== timeline ||
          !e.data.clips.includes(c) ||
          c.locked
        )
          return;
        mutate(() =>
          Object.assign(c, mediaBinding(result.version, allRecords())),
        );
        draw();
      },
    );
  }

  function showExport(artifact) {
    if (!artifact) return;
    const base = "/api/v1/media/artifacts/" + encodeURIComponent(artifact.id),
      probe = artifact.data.probe || {};
    modal(
      "成片已生成",
      `<video src="${base}/file" controls playsinline preload="metadata" class="room-export-preview"></video>
      <div class="room-export-facts"><strong>${esc(e.data.name)}</strong><span>${esc(probe.width)} × ${esc(probe.height)} · ${time(probe.duration)} · MP4</span></div>
      <div class="dialog-actions"><a class="button secondary" href="${base}/package" download>下载交付包</a><a class="button primary" href="${base}/file?download=true" download>${icon("download")}下载成片</a></div>`,
    );
  }

  function updatePanels() {
    const room = $(".editor-room", container);
    room.dataset.binHidden = String(!!e.hideBin);
    room.dataset.inspectorHidden = String(!!e.hideInspector);
    room.dataset.mobilePanel = e.panel || "media";
    room
      .querySelectorAll("[data-action=room-panel]")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.panel === e.panel)),
      );
    room.dispatchEvent(new Event("roompanelschange"));
    storeView(paneKey, {
      hideBin: !!e.hideBin,
      hideInspector: !!e.hideInspector,
    });
  }

  function bindControls() {
    ctx.bind({
      "clip-transition": () => {
        guard();
        const c = current(),
          rate = e.data.format.fps,
          fps = typeof rate === "object" ? rate.num / rate.den : rate;
        if (!c || !e.data.clips.includes(c)) return;
        transitionForm(
          e.data.clips,
          c,
          fps,
          (kind, seconds) => {
            mutate(() => editTransition(e.data.clips, c, kind, seconds, fps));
            e.playhead =
              c.timeline_start + (c.transition_in?.duration || 0) / 2;
            draw();
          },
          (clip) => mediaName(clipAsset(clip) || { data: { name: "镜头" } }),
        );
      },
      "import-assets": () => {
        if (!ctx.state.projectId) {
          ctx.newProject?.();
          return;
        }
        $("#editor-files", container).click();
      },
      "import-captions": () => {
        if (!ctx.state.projectId) {
          ctx.newProject?.();
          return;
        }
        guard();
        $("#editor-captions", container).click();
      },
      audition: (b) => {
        e.sourceId = b.dataset.id;
        draw();
      },
      "timeline-monitor": () => {
        e.sourceId = null;
        draw();
      },
      "bin-toggle": () => {
        e.hideBin = !e.hideBin;
        e.panel = e.panel === "media" ? "timeline" : "media";
        updatePanels();
      },
      "inspector-toggle": () => {
        e.hideInspector = !e.hideInspector;
        e.panel = e.panel === "properties" ? "timeline" : "properties";
        updatePanels();
      },
      "room-panel": (b) => {
        e.panel = b.dataset.panel;
        updatePanels();
      },
      "add-beat-track": () => {
        const source = beatSource();
        if (source) addAsset(source.audio);
      },
      "dismiss-beats": () => {
        e.beatOverlay = null;
        sessionStorage.removeItem(beatsKey);
        draw();
      },
      "frame-prev": () => stepFrame(-1),
      "frame-next": () => stepFrame(1),
      "last-export": () => showExport(lastExport()),
      "extract-track": separateAudio,
      "grade-clip": gradeClip,
      "move-prev": () => moveSelected(-1),
      "move-next": () => moveSelected(1),
      "replace-source": replaceSource,
      "add-asset": (b) => addAsset(findAsset(b.dataset.id)),
      play,
      safe: () => {
        e.safe = !e.safe;
        draw();
      },
      format: formatForm,
      "save-timeline": async (b) => {
        setBusy(b, true, "保存中");
        try {
          await save();
        } finally {
          setBusy(b, false);
        }
      },
      "timeline-list": timelineList,
      "compare-versions": versionHistory,
      undo: () => {
        if (!e.undo.length) return;
        guard();
        e.redo.push(clone(e.data));
        e.data = e.undo.pop();
        e.dirty = true;
        draw();
      },
      redo: () => {
        if (!e.redo.length) return;
        guard();
        e.undo.push(clone(e.data));
        e.data = e.redo.pop();
        e.dirty = true;
        draw();
      },
      split,
      duplicate: () => {
        const c = current();
        if (!c) return;
        mutate(() => {
          const list = e.data.clips.includes(c)
              ? e.data.clips
              : e.data.audio_tracks,
            d = {
              ...clone(c),
              id: uid(),
              timeline_start: c.timeline_start + length(c),
              locked: false,
            };
          list.push(d);
          e.selected = d.id;
        });
        draw();
      },
      delete: () => {
        const c = current();
        if (!c) return;
        if (c.locked) throw new Error("片段已锁定。");
        mutate(() => {
          const list = e.data.clips.includes(c)
              ? e.data.clips
              : e.data.audio_tracks,
            prevEnd = c.timeline_start + length(c);
          rippleAfter(c, -length(c), prevEnd);
          list.splice(list.indexOf(c), 1);
          e.selected = null;
        });
        draw();
      },
      caption: () => captionForm(),
      ripple: () => {
        e.ripple = !e.ripple;
        draw();
      },
      snap: () => {
        e.snap = !e.snap;
        draw();
      },
      "zoom-fit": () => {
        e.manualZoom = false;
        drawTimeline();
        $("#timeline-scroll", container).scrollLeft = 0;
      },
      "zoom-in": () => {
        e.manualZoom = true;
        e.zoom = Math.min(5000, e.zoom * 1.5);
        drawTimeline();
      },
      "zoom-out": () => {
        e.manualZoom = true;
        e.zoom = Math.max(0.035, e.zoom / 1.5);
        drawTimeline();
      },
      "lock-timeline": toggleLock,
      "lock-clip": () => {
        guard();
        snapshot();
        current().locked = !current().locked;
        dirty();
        drawInspector();
        drawTimeline();
      },
      "clip-mask": () => {
        const c = current();
        if (!c || !e.data.clips.includes(c)) return;
        if (c.locked) throw new Error("片段已锁定。");
        guard();
        const rect = c.mask?.rect || [0, 0, 1, 1];
        const fields = ["左边界", "上边界", "右边界", "下边界"]
          .map((label, i) =>
            field(
              label + "（%）",
              "edge" + i,
              Number((100 * rect[i]).toFixed(5)),
              "number",
              'min="0" max="100" step="any" required',
            ),
          )
          .join("");
        const dialog = modal(
          "画面遮罩",
          '<form data-form class="form-grid"><p>边界相对片段完成旋转与适配后的画面。</p>' +
            select(
              "形状",
              "shape",
              [
                ["rectangle", "矩形"],
                ["ellipse", "椭圆"],
              ],
              c.mask?.shape || "rectangle",
            ) +
            fields +
            '<div class="form-actions"><button type="button" class="button secondary" data-clear-mask>清除遮罩</button><button type="submit" class="button primary">应用遮罩</button></div></form>',
          (form) => {
            const values = formData(form);
            const bounds = rect.map((_, i) => Number(values["edge" + i]) / 100);
            if (bounds[0] >= bounds[2] || bounds[1] >= bounds[3])
              throw Error("右边界须在左边界右侧，下边界须在上边界下方。");
            mutate(() => {
              c.mask = {
                space: "post_fit_clip",
                rect: bounds,
                ...(values.shape === "ellipse" ? { shape: "ellipse" } : {}),
              };
            });
            draw();
          },
        );
        dialog.querySelector("[data-clear-mask]").onclick = () => {
          mutate(() => {
            delete c.mask;
          });
          dialog.close();
          draw();
        };
      },
      keyframe: keyframeForm,
      "remove-keyframe": (b) => {
        const c = current();
        if (c.locked) throw new Error("片段已锁定");
        mutate(() =>
          c.keyframes[b.dataset.property].splice(+b.dataset.index, 1),
        );
        drawInspector();
        updatePreview();
      },
      "render-timeline": async (b) => {
        ctx.requireProject();
        if (e.busy) return;
        if (!e.data.clips.length) throw new Error("请先添加实际画面片段。");
        e.busy = true;
        setBusy(b, true, "正在检查版本");
        try {
          const locked = await lockCurrent("锁定并渲染");
          if (!locked) return;
          e.exporting = true;
          draw();
          const result = await ctx.api("/media/render", {
            method: "POST",
            body: {
              project_id: ctx.state.projectId,
              timeline_id: locked.id,
              expected_revision: locked.version,
              profile: locked.data.format,
            },
          });
          await ctx.reloadData();
          const artifact = result.artifact || result;
          e.lastArtifactId = artifact.id;
          if (active()) showExport(artifact);
        } finally {
          e.busy = false;
          e.exporting = false;
          setBusy(b, false);
          draw();
        }
      },
    });
    bindRoomFiles();
    $("#playhead", container).oninput = (ev) => {
      stopPlayback();
      e.playhead = Number(ev.target.value);
      updatePreview();
    };
    const ripple = $("[data-action=ripple]", container);
    ripple.classList.toggle("active", e.ripple);
    $("[data-action=snap]", container).classList.toggle("active", e.snap);
  }
  const keydown = (ev) => {
    if (
      ev.defaultPrevented ||
      ev.target.closest('[role="separator"]') ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(ev.target.tagName) ||
      $("#dialog").open
    )
      return;
    try {
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "s") {
        ev.preventDefault();
        $("[data-action=save-timeline]", container)?.click();
        return;
      }
      if (["ArrowLeft", "ArrowRight"].includes(ev.key) && !e.sourceId) {
        ev.preventDefault();
        stepFrame((ev.key === "ArrowLeft" ? -1 : 1) * (ev.shiftKey ? 10 : 1));
        return;
      }
      if (
        ev.code === "Space" &&
        !ev.target.closest("button,summary,a,video,audio")
      ) {
        ev.preventDefault();
        playViewer();
      }
      if (ev.key.toLowerCase() === "s" && !ev.ctrlKey && !ev.metaKey) {
        ev.preventDefault();
        split();
      }
      if (ev.key === "Delete" || ev.key === "Backspace") {
        ev.preventDefault();
        $("[data-action=delete]", container)?.click();
      }
      if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === "z") {
        ev.preventDefault();
        $(`[data-action=${ev.shiftKey ? "redo" : "undo"}]`, container)?.click();
      }
    } catch (err) {
      toast(err.message, "error");
    }
  };
  window.addEventListener("keydown", keydown);
  window.addEventListener("pagehide", persistDraft);
  let measuredWidth = 0;
  const ro = new ResizeObserver(() => {
    const scroll = $("#timeline-scroll", container);
    if (scroll && scroll.clientWidth !== measuredWidth && !drag) {
      measuredWidth = scroll.clientWidth;
      drawTimeline();
    }
    updatePreview();
  });
  ro.observe(container);
  ctx.state.cleanup.push(() => {
    mounted = false;
    releaseLayout?.();
    viewerControls?.destroy();
    persistDraft();
    window.removeEventListener("pagehide", persistDraft);
    stopPlayback();
    window.removeEventListener("keydown", keydown);
    ro.disconnect();
  });
  const intent = ctx.editorIntent;
  if (intent === "keyframe") e.openComposition = true;
  if (["inspect", "trim", "mix", "grade", "keyframe"].includes(intent)) {
    const item =
      (intent === "mix" ? e.data.audio_tracks[0] : null) ||
      current() ||
      e.data.clips[0];
    e.selected = item?.id || null;
    e.panel = "properties";
    e.hideInspector = false;
  }
  if (intent === "audio") {
    e.filter = "audio";
    e.panel = "media";
    e.hideBin = false;
  }
  if (intent === "import") {
    e.filter = "all";
    e.panel = "media";
    e.hideBin = false;
  }
  draw();
  const simple = {
    captions: "caption",
    format: "format",
    history: "timeline-list",
    compare: "timeline-list",
    render: "render-timeline",
    export: "render-timeline",
    lock: "lock-timeline",
    grade: "grade-clip",
    keyframe: "keyframe",
  };
  if (ctx.state.projectId && intent) {
    if (intent === "timeline-import" || intent === "timeline-export") {
      timelineList();
      $(
        "#dialog [data-action=" +
          (intent === "timeline-import" ? "import-otio" : "export-otio") +
          "]",
      )?.click();
    } else if (["history", "compare"].includes(intent) && e.record) {
      await versionHistory();
    } else if (simple[intent])
      $(`[data-action="${simple[intent]}"]`, container)?.click();
    else if (intent === "import")
      $(".room-bin [data-action=import-assets]", container)?.focus();
  }
}
