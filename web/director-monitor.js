import { $, esc, icon, time } from "./ui.js";
import { installViewer, viewerTools } from "./editor-viewer.js";

export function directorMonitor({ title, address, start, end, artifact }) {
  return `<div class="room-monitor director-monitor">
    <div class="room-monitor-head"><span title="${esc(title)}">${icon(artifact ? "film" : "eye")} ${esc(title)}</span>${viewerTools()}</div>
    <div id="viewer-screen" class="room-screen director-screen" data-empty="false" tabindex="0" role="group" aria-label="预览画面，空格播放或暂停">
      <div id="preview-stage"><video playsinline preload="auto" class="director-player room-source-media" data-source-in="${esc(start)}" data-source-out="${esc(end)}" aria-label="${esc(title)}" src="${esc(address)}"></video></div>
    </div>
    <div class="director-transport"><button type="button" data-monitor-play aria-label="播放">${icon("play")}</button>
      <input type="range" data-monitor-seek aria-label="预览播放位置" min="${esc(start)}" max="${esc(end || start)}" step="0.01" value="${esc(start)}">
      <output data-monitor-clock></output>
      <button type="button" data-monitor-mute aria-label="静音" aria-pressed="false">${icon("volume")}</button>
      <input type="range" data-monitor-volume aria-label="预览音量" min="0" max="1" step="0.05" value="1">
    </div>
    <p class="director-monitor-issue" role="status" hidden></p>
  </div>`;
}

// Both monitors use the same image geometry, pointer gestures, keyboard shortcuts,
// browser fullscreen and fallback so switching between workspaces keeps their meaning.
export function bindDirectorMonitor(root, state, source, start, end) {
  const video = $(".director-player", root),
    seek = $("[data-monitor-seek]", root),
    toggle = $("[data-monitor-play]", root),
    mute = $("[data-monitor-mute]", root),
    volume = $("[data-monitor-volume]", root),
    clock = $("[data-monitor-clock]", root),
    issue = $(".director-monitor-issue", root);
  const boundary = () =>
    Math.min(
      end || Infinity,
      Number.isFinite(video.duration) ? video.duration : end || start,
    );
  const sync = () => {
    const stop = boundary();
    seek.max = Math.max(start, stop);
    seek.value = Math.max(start, Math.min(stop, video.currentTime));
    const t = time(Number(seek.value));
    clock.textContent = t + " / " + time(stop);
    seek.setAttribute("aria-valuetext", clock.textContent);
    const playing = !video.paused && !video.ended;
    toggle.setAttribute("aria-label", playing ? "暂停" : "播放");
    if (toggle.dataset.playing !== String(playing)) {
      toggle.dataset.playing = String(playing);
      toggle.innerHTML = icon(playing ? "pause" : "play");
    }
    mute.setAttribute("aria-label", video.muted ? "打开声音" : "静音");
    mute.setAttribute("aria-pressed", String(video.muted));
    volume.value = video.volume;
  };
  const failure = (text) => {
    issue.textContent = text;
    issue.hidden = false;
  };
  const play = () => {
    if (!video.paused) {
      video.pause();
      return;
    }
    if (video.currentTime >= boundary() - 0.025 || video.currentTime < start)
      video.currentTime = start;
    video.play().catch((err) => {
      if (err.name !== "AbortError")
        failure("无法播放这段视频，请检查文件或浏览器解码支持。");
    });
  };
  const intervalStart = () => {
    video.currentTime = start;
    sync();
  };
  if (video.readyState >= 1) intervalStart();
  else video.addEventListener("loadedmetadata", intervalStart, { once: true });
  video.onplay = () => {
    if (video.currentTime < start || (end && video.currentTime >= end - 0.025))
      video.currentTime = start;
    sync();
  };
  video.ontimeupdate = () => {
    if (end && !video.paused && video.currentTime >= end) {
      video.pause();
      video.currentTime = end;
    }
    sync();
  };
  video.onpause = video.onended = video.onseeked = video.onvolumechange = sync;
  video.onerror = () => failure("无法读取预览，请检查文件或浏览器解码支持。");
  seek.oninput = () => {
    video.currentTime = Number(seek.value);
    sync();
  };
  toggle.onclick = play;
  video.muted = Boolean(state.muted);
  video.volume = state.volume ?? 1;
  mute.onclick = () => {
    state.muted = video.muted = !video.muted;
    sync();
  };
  volume.oninput = () => {
    state.volume = video.volume = Number(volume.value);
    state.muted = video.muted = state.volume === 0;
    sync();
  };
  const viewer = installViewer(root, state, {
    format: () => ({
      width: video.videoWidth || source.data.probe?.width || 1280,
      height: video.videoHeight || source.data.probe?.height || 720,
    }),
    source: () => source,
    isPlayable: () => video.readyState >= 1,
    play,
    refresh: () => {},
  });
  sync();
  return () => {
    viewer.destroy();
    video.pause();
  };
}
