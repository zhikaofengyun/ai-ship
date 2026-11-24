import { icon } from "./ui.js";
const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
const modes = [
  ["fit", "适应窗口"],
  ["fill", "填满窗口"],
  ["0.25", "25%"],
  ["0.5", "50%"],
  ["1", "100%"],
  ["2", "200%"],
  ["4", "400%"],
];
const expandIcon =
  '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M9 4H4v5m11-5h5v5M4 15v5h5m11-5v5h-5"/></svg>';
const minusIcon =
  '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M5 12h14"/></svg>';
export const viewerTools = () =>
  `<div class="room-view-tools"><button type="button" data-view-minus title="缩小画面" aria-label="缩小画面">${minusIcon}</button><select data-view-mode aria-label="预览画面比例">${modes.map(([mode, label]) => `<option value="${mode}">${label}</option>`).join("")}</select><button type="button" data-view-plus title="放大画面" aria-label="放大画面">${icon("plus")}</button><button type="button" data-view-full title="全屏预览（F）" aria-label="全屏预览" aria-pressed="false">${expandIcon}</button></div>`;

export function installViewer(
  room,
  view,
  { format, source, isPlayable, play, refresh },
) {
  const screen = room.querySelector("#viewer-screen"),
    stage = room.querySelector("#preview-stage"),
    monitor = room.querySelector(".room-monitor");
  const selector = room.querySelector("[data-view-mode]"),
    full = room.querySelector("[data-view-full]");
  let geometry,
    fallback = false,
    frame = 0,
    alive = true;
  const isFull = () =>
    document.fullscreenElement === monitor ||
    document.webkitFullscreenElement === monitor ||
    fallback;
  const fit = () => {
    view.mode = "fit";
    view.x = 0;
    view.y = 0;
  };
  const layout = () => {
    if (!alive || !screen.clientWidth || !screen.clientHeight) return;
    const sw = screen.clientWidth,
      sh = screen.clientHeight;
    const media = stage.querySelector(".room-source-media");
    const [w, h] = source()
      ? [
          media?.videoWidth ||
            media?.naturalWidth ||
            source()?.data?.probe?.width ||
            format().width,
          media?.videoHeight ||
            media?.naturalHeight ||
            source()?.data?.probe?.height ||
            format().height,
        ]
      : [format().width, format().height];
    const scale =
      view.mode === "fill"
        ? Math.max(sw / w, sh / h)
        : view.mode === "fit"
          ? Math.min(sw / w, sh / h)
          : clamp(Number(view.mode) || 1, 0.01, 8);
    const vw = w * scale,
      vh = h * scale;
    const maxX = Math.max(0, (vw - sw) / 2),
      maxY = Math.max(0, (vh - sh) / 2);
    view.x = clamp(view.x || 0, -maxX, maxX);
    view.y = clamp(view.y || 0, -maxY, maxY);
    const left = (sw - vw) / 2 + view.x,
      top = (sh - vh) / 2 + view.y;
    Object.assign(stage.style, {
      width: vw + "px",
      height: vh + "px",
      left: left + "px",
      top: top + "px",
    });
    geometry = { scale, sw, sh, vw, vh, top, left, maxX, maxY };
    screen.dataset.pan = String(maxX > 1 || maxY > 1);
    const custom = selector.querySelector("[data-custom]");
    if (modes.some(([mode]) => mode === String(view.mode))) {
      custom?.remove();
      selector.value = String(view.mode);
    } else {
      const opt = custom || document.createElement("option");
      opt.dataset.custom = "";
      opt.value = String(view.mode);
      opt.textContent = +(scale * 100).toFixed(1) + "%";
      if (!custom) selector.append(opt);
      selector.value = opt.value;
    }
    const fullscreen = isFull();
    full.setAttribute("aria-pressed", String(fullscreen));
    full.setAttribute("aria-label", fullscreen ? "退出全屏" : "全屏预览");
    full.title = fullscreen ? "退出全屏（Esc）" : "全屏预览（F）";
    refresh();
  };
  function zoom(next, x, y) {
    if (!geometry) layout();
    if (!geometry) return;
    const { sw, sh, scale } = geometry;
    const factor = clamp(next, 0.01, 8) / scale;
    const cx = (x ?? sw / 2) - sw / 2,
      cy = (y ?? sh / 2) - sh / 2;
    view.x = cx - (cx - view.x) * factor;
    view.y = cy - (cy - view.y) * factor;
    view.mode = String(clamp(next, 0.01, 8));
    layout();
  }
  function exitFallback() {
    if (!fallback) return;
    fallback = false;
    if (monitor.hidePopover && monitor.matches(":popover-open"))
      monitor.hidePopover();
    monitor.removeAttribute("popover");
    monitor.classList.remove("room-view-expanded");
    full.focus({ preventScroll: true });
    layout();
  }
  async function fullscreen() {
    if (fallback) {
      exitFallback();
      return;
    }
    if (isFull()) {
      if (document.exitFullscreen) await document.exitFullscreen();
      else document.webkitExitFullscreen?.();
      return;
    }
    try {
      if (monitor.requestFullscreen) {
        await monitor.requestFullscreen();
        return;
      }
      if (monitor.webkitRequestFullscreen) {
        monitor.webkitRequestFullscreen();
        return;
      }
    } catch {
      /* Restricted and embedded contexts may still use the page-sized viewer. */
    }
    if (!alive) return;
    fallback = true;
    monitor.classList.add("room-view-expanded");
    if (monitor.showPopover) {
      monitor.popover = "manual";
      monitor.showPopover();
    }
    full.focus({ preventScroll: true });
    layout();
  }
  const plus = room.querySelector("[data-view-plus]"),
    minus = room.querySelector("[data-view-minus]");
  plus.onclick = () => zoom((geometry?.scale || 1) * Math.SQRT2);
  minus.onclick = () => zoom((geometry?.scale || 1) / Math.SQRT2);
  selector.onchange = () => {
    view.mode = selector.value;
    view.x = view.y = 0;
    layout();
  };
  full.onclick = () => fullscreen().catch(() => {});
  const pointerMap = new Map();
  let start,
    gesture = false,
    pinch;
  const midpoint = ([a, b]) => ({
    x: (a.x + b.x) / 2,
    y: (a.y + b.y) / 2,
    d: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
  });
  screen.onpointerdown = (ev) => {
    if (
      ev.button !== 0 ||
      ev.target.closest("button,audio,input,select") ||
      !isPlayable()
    )
      return;
    screen.focus({ preventScroll: true });
    pointerMap.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    screen.setPointerCapture(ev.pointerId);
    if (pointerMap.size === 1) {
      start = {
        x: ev.clientX,
        y: ev.clientY,
        px: view.x || 0,
        py: view.y || 0,
      };
      gesture = false;
    }
    if (pointerMap.size === 2) {
      gesture = true;
      pinch = midpoint([...pointerMap.values()]);
    }
  };
  screen.onpointermove = (ev) => {
    if (!pointerMap.has(ev.pointerId)) return;
    pointerMap.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
    if (pointerMap.size === 2 && geometry && pinch) {
      const next = midpoint([...pointerMap.values()]),
        rect = screen.getBoundingClientRect();
      zoom(
        (geometry.scale * next.d) / pinch.d,
        pinch.x - rect.left,
        pinch.y - rect.top,
      );
      view.x += next.x - pinch.x;
      view.y += next.y - pinch.y;
      pinch = next;
      layout();
      return;
    }
    if (!start || pointerMap.size !== 1) return;
    const dx = ev.clientX - start.x,
      dy = ev.clientY - start.y;
    if (Math.hypot(dx, dy) > 4) gesture = true;
    if (gesture) {
      view.x = start.px + dx;
      view.y = start.py + dy;
      layout();
    }
  };
  screen.onpointerup =
    screen.onpointercancel =
    screen.onlostpointercapture =
      (ev) => {
        if (!pointerMap.delete(ev.pointerId)) return;
        if (ev.type === "pointerup" && !gesture && !pointerMap.size) play();
        if (screen.hasPointerCapture(ev.pointerId))
          screen.releasePointerCapture(ev.pointerId);
        const p = [...pointerMap.values()][0];
        start = p ? { ...p, px: view.x || 0, py: view.y || 0 } : null;
        pinch = null;
      };
  const wheel = (ev) => {
    if (!(ev.ctrlKey || ev.metaKey) || !geometry) return;
    ev.preventDefault();
    const r = screen.getBoundingClientRect();
    zoom(
      geometry.scale * Math.exp(-clamp(ev.deltaY, -250, 250) * 0.007),
      ev.clientX - r.left,
      ev.clientY - r.top,
    );
  };
  screen.addEventListener("wheel", wheel, { passive: false });
  const keys = (ev) => {
    if (ev.key === "Escape" && fallback) {
      exitFallback();
      ev.preventDefault();
      ev.stopPropagation();
      return;
    }
    if (
      ["SELECT", "INPUT", "TEXTAREA", "BUTTON", "A"].includes(ev.target.tagName)
    )
      return;
    if (ev.key.toLowerCase() === "f" && !ev.ctrlKey && !ev.metaKey) {
      fullscreen().catch(() => {});
    } else if (ev.code === "Space" || ev.key === "Enter") play();
    else if (ev.key === "+" || ev.key === "=") plus.click();
    else if (ev.key === "-") minus.click();
    else if (ev.key === "0") {
      fit();
      layout();
    } else if (
      ev.key.startsWith("Arrow") &&
      geometry &&
      (geometry.maxX || geometry.maxY)
    ) {
      const amount = ev.shiftKey ? 64 : 16;
      if (ev.key === "ArrowLeft") view.x -= amount;
      else if (ev.key === "ArrowRight") view.x += amount;
      else if (ev.key === "ArrowDown") view.y += amount;
      else view.y -= amount;
      layout();
    } else return;
    ev.preventDefault();
    ev.stopPropagation();
  };
  monitor.addEventListener("keydown", keys);
  const notify = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(layout);
  };
  stage.addEventListener("loadedmetadata", notify, true);
  stage.addEventListener("load", notify, true);
  document.addEventListener("fullscreenchange", notify);
  document.addEventListener("webkitfullscreenchange", notify);
  const ro = new ResizeObserver(notify);
  ro.observe(screen);
  layout();
  return {
    layout,
    destroy() {
      alive = false;
      cancelAnimationFrame(frame);
      ro.disconnect();
      if (fallback) exitFallback();
      screen.removeEventListener("wheel", wheel);
      monitor.removeEventListener("keydown", keys);
      document.removeEventListener("fullscreenchange", notify);
      document.removeEventListener("webkitfullscreenchange", notify);
      stage.removeEventListener("loadedmetadata", notify, true);
      stage.removeEventListener("load", notify, true);
    },
  };
}
