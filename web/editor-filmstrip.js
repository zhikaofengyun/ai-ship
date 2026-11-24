// Render actual source frames at three positions within the selected range.
// A bounded queue prevents a full timeline from opening all its videos at once.
const strips = new Map();
const queue = [];
let active = 0;

function waitFor(element, name) {
  return new Promise((resolve, reject) => {
    const done = (error) => {
      clearTimeout(timer);
      element.removeEventListener(name, ready);
      element.removeEventListener("error", failed);
      error ? reject(error) : resolve();
    };
    const ready = () => done(),
      failed = () => done(new Error("frame unavailable"));
    const timer = setTimeout(failed, 2800);
    element.addEventListener(name, ready);
    element.addEventListener("error", failed);
  });
}
async function sample(url, start, end) {
  const video = document.createElement("video");
  video.preload = "auto";
  video.muted = true;
  video.playsInline = true;
  // WebKit may report a successful seek on a detached video while exposing a
  // black frame to canvas. Mount the decoder without adding a visible control.
  video.setAttribute("aria-hidden", "true");
  video.tabIndex = -1;
  Object.assign(video.style, {
    position: "fixed",
    top: "0",
    left: "0",
    width: "2px",
    height: "2px",
    opacity: "0",
    pointerEvents: "none",
  });
  document.body.append(video);
  try {
    const loaded = waitFor(video, "loadeddata");
    video.src = url;
    await loaded;
    if (!video.videoWidth || !video.duration) return "";
    const canvas = document.createElement("canvas"),
      ctx = canvas.getContext("2d");
    canvas.width = 252;
    canvas.height = 44;
    for (const [index, fraction] of [0.08, 0.5, 0.92].entries()) {
      const target = Math.max(
        0,
        Math.min(video.duration - 0.025, start + (end - start) * fraction),
      );
      if (Math.abs(video.currentTime - target) > 0.001) {
        const seek = waitFor(video, "seeked");
        video.currentTime = target;
        await seek;
      }
      const scale = Math.max(84 / video.videoWidth, 44 / video.videoHeight);
      const sw = 84 / scale,
        sh = 44 / scale;
      ctx.drawImage(
        video,
        (video.videoWidth - sw) / 2,
        (video.videoHeight - sh) / 2,
        sw,
        sh,
        index * 84,
        0,
        84,
        44,
      );
    }
    return canvas.toDataURL("image/jpeg", 0.74);
  } finally {
    video.remove();
    video.removeAttribute("src");
    video.load();
  }
}
async function waveform(url, start, end) {
  // A small decoded envelope never replaces the stored media or its audio data.
  const response = await fetch(url);
  const bytes = Number(response.headers.get("content-length"));
  if (!response.ok || !bytes || bytes > 16 * 1024 * 1024) {
    await response.body?.cancel();
    return "";
  }
  const decoder = new OfflineAudioContext(1, 1, 22050);
  const audio = await decoder.decodeAudioData(await response.arrayBuffer());
  const canvas = document.createElement("canvas"),
    ctx = canvas.getContext("2d");
  canvas.width = 480;
  canvas.height = 24;
  const left = Math.max(0, Math.floor(start * audio.sampleRate));
  const right = Math.min(audio.length, Math.ceil(end * audio.sampleRate));
  if (right <= left) return "";
  const samples = audio.getChannelData(0);
  ctx.fillStyle = "#9bddbd";
  for (let x = 0; x < canvas.width; x++) {
    const a = left + Math.floor((x * (right - left)) / canvas.width);
    const b = Math.min(
      right,
      left + Math.ceil(((x + 1) * (right - left)) / canvas.width),
    );
    let peak = 0;
    for (let i = a; i < b; i += Math.max(1, Math.floor((b - a) / 128)))
      peak = Math.max(peak, Math.abs(samples[i]));
    const h = Math.min(24, Math.round(24 * Math.sqrt(peak)));
    ctx.fillRect(x, 12 - h / 2, 1, h);
  }
  return canvas.toDataURL("image/png");
}
function drain() {
  while (active < 2 && queue.length) {
    const entry = queue.shift();
    active++;
    (entry.kind === "audio" ? waveform : sample)(
      entry.url,
      entry.start,
      entry.end,
    )
      .then(entry.resolve, () => entry.resolve(""))
      .finally(() => {
        active--;
        drain();
      });
  }
}
export function filmstrip(element, url, start, end, kind, sourceDuration = 0) {
  if (
    !element ||
    (kind === "audio" && (!sourceDuration || sourceDuration > 300))
  )
    return;
  if (kind === "image") {
    element.style.backgroundImage = `url("${url}")`;
    return;
  }
  const key = [url, start, end, kind].join("|");
  if (!strips.has(key)) {
    const promise = new Promise((resolve) =>
      queue.push({ url, start, end, kind, resolve }),
    );
    strips.set(key, promise);
    if (strips.size > 128) strips.delete(strips.keys().next().value);
    drain();
  }
  strips.get(key)?.then((result) => {
    if (result && element.isConnected)
      element.style.backgroundImage = `url("${result}")`;
  });
}
